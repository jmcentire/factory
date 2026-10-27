#!/usr/bin/env python3
"""Kernel path boundary for subscription CLI seats, independent of tool spelling.

The host supplies the protected roots. Normal tools and IP networking remain available;
protected reads, control-record writes and out-of-process execution routes do not. This is
the process boundary, not a substitute for Signet, role admission or the custody broker.
No profile is installed globally and an unavailable backend never falls back to plain exec.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import stat
import sys
from dataclasses import dataclass
from typing import Any

SCHEMA = "factory-seat-boundary/1"
ROLES = {"validator", "orchestrator", "coder", "tester"}
_SPAWNERS = ("/bin/launchctl", "/usr/bin/osascript", "/usr/bin/open")
# macOS resolves external DNS through this root-owned service. Keeping IP networking
# useful requires this exact endpoint, not access to arbitrary host Unix sockets.
_DNS_SOCKET = pathlib.Path("/private/var/run/mDNSResponder")
# Preserve terminal/plan-login locations and scoped Factory/Kindex wiring, not the operator's
# arbitrary environment. A caller must not source a login profile again inside the seat.
ENVIRONMENT_KEYS = frozenset({
    "HOME", "USER", "LOGNAME", "PATH", "SHELL", "TERM", "LANG", "LC_ALL", "TMPDIR",
    "CLAUDE_CONFIG_DIR", "CODEX_HOME", "CURSOR_CONFIG_DIR", "SIGNET_SESSION",
    "KIN_PROJECT_PATH", "FACTORY_HOME", "FACTORY_CLI", "FACTORY_PYTHON",
    "FACTORY_TESSERA_BIN", "FACTORY_RUNS_DIR", "HARNESS_RUN_ROOT",
    "FACTORY_REQUIRE_SIGNET", "FACTORY_SIGNET_CONTRACT", "FACTORY_SIGNET_DIGEST",
    "BASH_FUNC_cl%%",
})


def seat_environment(environment: dict[str, str]) -> dict[str, str]:
    """Select permitted names without inspecting/logging credential values."""
    return {name: value for name, value in environment.items() if name in ENVIRONMENT_KEYS}


class BoundaryError(ValueError):
    """The host's boundary is absent, ambiguous or cannot be enforced."""


def _path(value: Any) -> pathlib.Path:
    if not isinstance(value, str) or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise BoundaryError("boundary paths must be nonempty strings without control characters")
    path = pathlib.Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise BoundaryError("boundary paths must be absolute and canonical")
    resolved = path.resolve()
    if resolved == resolved.parent:
        raise BoundaryError("a filesystem root is not a scoped boundary")
    return resolved


def _quoted(value: pathlib.Path) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _within(path: pathlib.Path, root: pathlib.Path) -> bool:
    return path == root or path.is_relative_to(root)


def _alias_links(path: pathlib.Path) -> set[pathlib.Path]:
    """Collect every intermediate link, including directory aliases, using metadata only."""
    pending = list(path.parts[1:])
    cursor = pathlib.Path("/")
    links: set[pathlib.Path] = set()
    traversals = 0
    while pending:
        cursor /= pending.pop(0)
        if cursor.is_symlink():
            traversals += 1
            if traversals > 40:
                raise BoundaryError("protected alias chain is cyclic or too long")
            links.add(cursor)
            target = cursor.readlink()
            if not target.is_absolute():
                target = cursor.parent / target
            pending = list(pathlib.Path(os.path.normpath(target)).parts[1:]) + pending
            cursor = pathlib.Path("/")
    return links


@dataclass(frozen=True)
class Boundary:
    role: str
    denied_reads: tuple[pathlib.Path, ...]
    protected_writes: tuple[pathlib.Path, ...]
    allowed_sockets: tuple[pathlib.Path, ...] = ()

    @classmethod
    def parse(cls, value: Any) -> Boundary:
        fields = {"schema", "role", "denied_reads", "protected_writes", "allowed_sockets"}
        if not isinstance(value, dict) or set(value) != fields or value["schema"] != SCHEMA:
            raise BoundaryError("invalid boundary contract shape")
        if not isinstance(value["role"], str) or value["role"] not in ROLES:
            raise BoundaryError("unknown boundary role")
        paths = {}
        for field in fields - {"schema", "role"}:
            raw = value[field]
            if not isinstance(raw, list) or len(raw) > 256:
                raise BoundaryError("boundary path lists must be bounded arrays")
            paths[field] = tuple(dict.fromkeys(_path(item) for item in raw))
        if not paths["denied_reads"] or not paths["protected_writes"]:
            raise BoundaryError("read and control-write boundaries must both be explicit")
        for sock in paths["allowed_sockets"]:
            if any(_within(sock, root) for root in paths["denied_reads"]):
                raise BoundaryError("socket exception overlaps a denied root")
            if not any(_within(sock, root) for root in paths["protected_writes"]):
                raise BoundaryError("socket exception must be owned by the protected control plane")
        return cls(role=value["role"], **paths)

    def document(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "role": self.role,
                "denied_reads": [str(p) for p in self.denied_reads],
                "protected_writes": [str(p) for p in self.protected_writes],
                "allowed_sockets": [str(p) for p in self.allowed_sockets]}

    def profile(self) -> str:
        aliases = self.check_topology()
        reads = "\n    ".join(f"(subpath {_quoted(p)})" for p in self.denied_reads)
        writes = "\n    ".join(f"(subpath {_quoted(p)})" for p in
                             (*self.denied_reads, *self.protected_writes, *aliases))
        # Deny Unix-domain host services, including other tmux servers. Only host-approved
        # exact sockets (the closed custody broker, or a qualified private client IPC) pass.
        exceptions = " ".join(f"(remote unix-socket (path-literal {_quoted(p)}))"
                              for p in (_DNS_SOCKET, *self.allowed_sockets))
        sockets = f"(require-all (remote unix-socket) (require-not (require-any {exceptions})))"
        executables = "\n    ".join(f"(literal {_quoted(pathlib.Path(p).resolve())})"
                                    for p in _SPAWNERS)
        ancestors: set[pathlib.Path] = set()
        for root in (*self.denied_reads, *self.protected_writes, *aliases):
            ancestors.update(root.parents)
        anchors = "\n    ".join(f"(literal {_quoted(p)})" for p in sorted(ancestors))
        return f'''(version 1)
(allow default)
(deny file-read* file-map-executable
    {reads})
(deny file-write*
    {writes})
(deny file-write-unlink
    {anchors})
(deny process-exec
    {executables})
(deny appleevent-send)
(deny signal)
(allow signal (target self) (target children))
(deny process-info*)
(allow process-info* (target self))
(deny network-outbound {sockets})
'''

    @property
    def digest(self) -> str:
        payload = {"contract": self.document(), "profile": self.profile(),
                   "environment_keys": sorted(ENVIRONMENT_KEYS)}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def invocation(self, command: list[str], *, cwd: pathlib.Path) -> list[str]:
        if platform.system() != "Darwin" or not os.access("/usr/bin/sandbox-exec", os.X_OK):
            raise BoundaryError("the required macOS boundary backend is unavailable")
        if not command or any(not isinstance(part, str) or not part for part in command):
            raise BoundaryError("boundary command must be nonempty argv")
        if any(_within(cwd.resolve(), p) for p in self.denied_reads):
            raise BoundaryError("seat working directory is inside a denied root")
        dns = _DNS_SOCKET.lstat()
        if dns.st_uid != 0 or not stat.S_ISSOCK(dns.st_mode):
            raise BoundaryError("system DNS endpoint is not the root-owned socket")
        self.check_topology()
        return ["/usr/bin/sandbox-exec", "-p", self.profile(), *command]

    def check_topology(self) -> tuple[pathlib.Path, ...]:
        """Path rules cannot cover pre-existing inode aliases outside a protected tree.

        Inspect metadata only, never file contents. The trusted host must
        keep this invariant when it later creates protected workspaces. The bounded seat
        cannot create new aliases of these objects because file-write* denies link creation.
        """
        pending = list((*self.denied_reads, *self.protected_writes))
        seen = set()
        aliases: set[pathlib.Path] = set()
        while pending:
            path = pending.pop()
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            key = (metadata.st_dev, metadata.st_ino)
            if stat.S_ISLNK(metadata.st_mode):
                if any(_within(path, root) for root in self.denied_reads):
                    raise BoundaryError("denied-read trees must not contain symlinks")
                try:
                    target = path.resolve(strict=True)
                except (OSError, RuntimeError) as error:
                    raise BoundaryError("protected alias target cannot be resolved") from error
                if not any(_within(target, root) for root in
                           (*self.protected_writes, *self.denied_reads)):
                    raise BoundaryError("protected alias target is outside the boundary")
                # Its target is covered by a separately declared root. Every intermediate
                # link must also be immutable, including a package-manager directory alias.
                aliases.update(_alias_links(path))
                continue
            if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
                raise BoundaryError("protected file has an unbounded hard-link alias")
            if stat.S_ISDIR(metadata.st_mode) and key not in seen:
                seen.add(key)
                pending.extend(path.iterdir())
        return tuple(sorted(aliases))


def validate_lane_record(value: Any, role: str, cwd: pathlib.Path) -> dict[str, str] | None:
    """Validate a host-retained author binding before launch and every continuation."""
    if value is None:
        return None
    if (not isinstance(value, dict)
            or set(value) != {"schema", "role", "contract", "digest"}
            or value["schema"] != "factory-lane-boundary/1"
            or role not in {"coder", "tester"} or value["role"] != role):
        raise BoundaryError("lane boundary role or record is invalid")
    path = _path(value["contract"])
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)) as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 1024 * 1024:
            raise BoundaryError("lane boundary contract is not a bounded regular file")
        boundary = Boundary.parse(json.load(stream))
    if boundary.role != role or boundary.digest != value["digest"]:
        raise BoundaryError("lane boundary identity or configuration changed")
    if not any(_within(path, root) for root in boundary.protected_writes):
        raise BoundaryError("lane boundary contract is not host write-protected")
    boundary.invocation(["/usr/bin/true"], cwd=cwd)  # validate backend/topology; no execution
    return dict(value)


def select_lane_record(environment: dict[str, str], role: str,
                       cwd: pathlib.Path) -> dict[str, str] | None:
    path = environment.get("FACTORY_BOUNDARY_CONTRACT", "")
    digest = environment.get("FACTORY_BOUNDARY_DIGEST", "")
    if not path and not digest and environment.get("FACTORY_REQUIRE_BOUNDARY") != "1":
        return None  # explicitly unqualified legacy route; never an enforcement receipt
    if not path or not digest:
        raise BoundaryError("required author boundary contract and digest are missing")
    return validate_lane_record({"schema": "factory-lane-boundary/1", "role": role,
                                 "contract": path, "digest": digest}, role, cwd)


def model_command(command: list[str], record: Any, role: str,
                  cwd: pathlib.Path) -> list[str]:
    """Bound only the model child; trusted session/exit writers stay outside its tree."""
    checked = validate_lane_record(record, role, cwd)
    if checked is None:
        return command
    return [sys.executable, str(pathlib.Path(__file__).resolve()),
            "--contract", checked["contract"], "--digest", checked["digest"],
            "--expected-role", role, "--", *command]


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["select-lane"]:
        select = argparse.ArgumentParser()
        select.add_argument("--role", required=True, choices=("coder", "tester"))
        select.add_argument("--cwd", required=True, type=pathlib.Path)
        args = select.parse_args(argv[1:])
        try:
            print(json.dumps(select_lane_record(dict(os.environ), args.role, args.cwd)))
            return 0
        except (OSError, ValueError, TypeError) as error:
            print(f"lane-boundary: {error}", file=sys.stderr)
            return 77
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=pathlib.Path, required=True)
    parser.add_argument("--digest", required=True, help="host-pinned boundary SHA256")
    parser.add_argument("--expected-role", choices=sorted(ROLES))
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        boundary = Boundary.parse(json.loads(args.contract.read_text()))
        if args.expected_role and boundary.role != args.expected_role:
            raise BoundaryError("boundary role differs from the admitted lane")
        if boundary.digest != args.digest:
            raise BoundaryError("boundary contract changed after host admission")
        contract = args.contract.resolve()
        if not any(_within(contract, p) for p in boundary.protected_writes):
            raise BoundaryError("boundary contract itself is not write-protected")
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        invocation = boundary.invocation(command, cwd=pathlib.Path.cwd())
        # Do not carry pre-opened host files or service sockets across the kernel boundary.
        # macOS owns guarded runtime FDs which must not be forcibly closed. Non-inheritable
        # FDs close at exec; close only explicitly inherited descriptors above standard IO.
        for entry in pathlib.Path("/dev/fd").iterdir():
            descriptor = int(entry.name)
            if descriptor <= 2:
                continue
            try:
                if os.get_inheritable(descriptor):
                    os.close(descriptor)
            except OSError:
                pass  # /dev/fd iteration itself opens a transient, already-closed descriptor
        os.execve(invocation[0], invocation, seat_environment(dict(os.environ)))
    except (BoundaryError, OSError, ValueError, TypeError) as error:
        print(f"validator-boundary: {error}", file=sys.stderr)
        return 77
    return 77


if __name__ == "__main__":
    raise SystemExit(main())
