"""Platform-enforced process isolation for Factory lanes.

Separate directories are organization, not isolation. On macOS this backend uses the kernel
sandbox through ``sandbox-exec`` with deny-by-default file and network access. It deliberately
fails closed on unsupported platforms or an absent executable. The profile is qualified by
real denial probes before a lane is trusted.
"""

from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


class IsolationError(RuntimeError):
    """A lane could not be run under a demonstrated isolation boundary."""


# Executed inside the sandbox. A network refusal counts as denial ONLY when the kernel
# sandbox rejects the operation with EPERM/EACCES; a timeout or ECONNREFUSED means the
# rule did not deny (it reached the network or a closed port) and fails qualification.
_QUALIFICATION_PROBE = r"""
import errno
import json
import os
import socket

_SANDBOX_DENIED = {errno.EPERM, errno.EACCES}


def _denied(target, connect):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3)
    try:
        if connect:
            sock.connect(target)
        else:
            sock.bind(target)
        return False
    except OSError as exc:
        return exc.errno in _SANDBOX_DENIED
    finally:
        sock.close()


def _allowed_bind(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _allowed_connect(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3)
    try:
        sock.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


result = {}
try:
    open(os.environ["FACTORY_FORBIDDEN_READ"], encoding="utf-8").read()
    result["read_denied"] = False
except OSError:
    result["read_denied"] = True
try:
    open(os.environ["FACTORY_FORBIDDEN_WRITE"], "w", encoding="utf-8").write("bad")
    result["write_denied"] = False
except OSError:
    result["write_denied"] = True

mode = os.environ["FACTORY_NETWORK_MODE"]
external_denied = _denied(("192.0.2.1", 9), connect=True)
unrelated_denied = _denied(("127.0.0.1", int(os.environ["FACTORY_UNRELATED_PORT"])), connect=True)
result["external_denied"] = external_denied
result["unrelated_listener_denied"] = unrelated_denied
if mode == "deny-all":
    bind_denied = _denied(("127.0.0.1", 0), connect=False)
    result["bind_denied"] = bind_denied
    result["connect_denied"] = unrelated_denied
    result["network_denied"] = bind_denied and unrelated_denied and external_denied
    result["loopback_in_range_ok"] = False
    result["loopback_out_of_range_denied"] = True
else:
    result["network_denied"] = False
    result["loopback_in_range_ok"] = _allowed_connect(
        int(os.environ["FACTORY_IN_RANGE_CONNECT_PORT"])
    ) and _allowed_bind(int(os.environ["FACTORY_IN_RANGE_BIND_PORT"]))
    result["loopback_out_of_range_denied"] = _denied(
        ("127.0.0.1", int(os.environ["FACTORY_OUT_OF_RANGE_BIND_PORT"])), connect=False
    )
print(json.dumps(result, sort_keys=True))
"""


@dataclass(frozen=True)
class IsolatedProcessResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


_MAX_LOOPBACK_RANGE_PORTS = 64


@dataclass(frozen=True)
class NetworkPolicy:
    """A lane's exact, enforceable network boundary.

    ``deny-all`` is the default and the only policy the author (Coder/Tester) and broker
    lanes may ever run under: no bind, no connect, not even loopback. ``loopback-range``
    grants a validator lane an exact, per-attempt, contiguous loopback TCP port block —
    enumerated as one Seatbelt rule per port because SBPL supports no range literal — so the
    validator may connect to, and its launched candidate child may bind, only ports inside
    the block. Every other endpoint, loopback or external, stays denied.
    """

    mode: str = "deny-all"
    ports: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in {"deny-all", "loopback-range"}:
            raise IsolationError(f"unsupported network policy mode: {self.mode}")
        if self.mode == "deny-all":
            if self.ports:
                raise IsolationError("deny-all network policy cannot enumerate ports")
            return
        if not self.ports:
            raise IsolationError("loopback-range network policy requires at least one port")
        if len(self.ports) > _MAX_LOOPBACK_RANGE_PORTS:
            raise IsolationError("loopback-range exceeds the per-attempt port ceiling")
        if list(self.ports) != sorted(set(self.ports)):
            raise IsolationError("loopback-range ports must be unique and ascending")
        if self.ports[-1] - self.ports[0] + 1 != len(self.ports):
            raise IsolationError("loopback-range ports must be a contiguous block")
        for port in self.ports:
            if not (1 <= port <= 65535):
                raise IsolationError(f"loopback-range port out of range: {port}")

    @property
    def identity(self) -> dict[str, object]:
        """Stable, content-addressable description recorded in qualification evidence."""

        return {"mode": self.mode, "ports": list(self.ports)}

    def _network_clauses(self) -> str:
        if self.mode == "deny-all":
            return ""
        lines: list[str] = []
        for port in self.ports:
            endpoint = _scheme_string(f"localhost:{port}")
            lines.append(f"(allow network-outbound (remote tcp {endpoint}))")
            lines.append(f"(allow network-bind (local tcp {endpoint}))")
            lines.append(f"(allow network-inbound (local tcp {endpoint}))")
        return "\n".join(lines)


DENY_ALL_NETWORK = NetworkPolicy()


@dataclass(frozen=True)
class IsolationQualification:
    backend: str
    read_denied: bool
    write_denied: bool
    network_denied: bool
    network_policy: str = "deny-all"
    loopback_in_range_ok: bool = False
    loopback_out_of_range_denied: bool = True
    external_denied: bool = True
    unrelated_listener_denied: bool = True

    @property
    def satisfied(self) -> bool:
        if self.network_policy == "deny-all":
            return self.read_denied and self.write_denied and self.network_denied
        # A loopback-range lane deliberately succeeds inside its block; the guarantee is
        # that filesystem denial still holds, the permitted use works, and every endpoint
        # outside the block — out-of-range loopback, a live unrelated listener, and any
        # external address — is refused. network_denied is not required or expected here.
        return (
            self.read_denied
            and self.write_denied
            and self.loopback_in_range_ok
            and self.loopback_out_of_range_denied
            and self.unrelated_listener_denied
            and self.external_denied
        )


def _scheme_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _path_filter(path: Path) -> str:
    resolved = path.resolve()
    operator = "subpath" if resolved.is_dir() else "literal"
    return f"({operator} {_scheme_string(str(resolved))})"


def _interpreter_read_paths() -> tuple[Path, ...]:
    """Return the paths the running interpreter must read to start.

    Factory currently runs every lane command through ``sys.executable``. Under a virtualenv,
    CPython needs both the environment at ``sys.prefix`` and the installation at
    ``sys.base_prefix``. Deriving those locations keeps the deny-default profile portable across
    Homebrew, python.org, virtualenv, and hosted-runner layouts without granting the filesystem
    root. These read-only locations are part of the host's trusted computing base: an actor that
    can replace the interpreter already controls Factory before Seatbelt starts. They are
    resolved by the parent process, before any sandboxed lane runs.
    """

    candidates = (
        Path(sys.prefix),
        Path(sys.base_prefix),
        Path(sys.executable).resolve().parent,
    )
    seen: dict[Path, None] = {}
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.parent != resolved and resolved.is_dir():
            seen.setdefault(resolved, None)
    return tuple(seen)


class MacOSSandbox:
    """Deny-by-default macOS Seatbelt wrapper with explicit filesystem grants."""

    def __init__(
        self,
        executable: str | Path = "/usr/bin/sandbox-exec",
        *,
        timeout_seconds: int = 120,
    ) -> None:
        self.executable = Path(executable)
        self.timeout_seconds = timeout_seconds
        if platform.system() != "Darwin":
            raise IsolationError("the macOS sandbox backend is unavailable on this platform")
        if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
            raise IsolationError(f"sandbox executable is unavailable: {self.executable}")
        self.interpreter_read_paths = _interpreter_read_paths()
        if not self.interpreter_read_paths:
            raise IsolationError("the trusted interpreter has no safe readable path grant")

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path,
        readable_paths: Sequence[str | Path] = (),
        writable_paths: Sequence[str | Path] = (),
        environment: Mapping[str, str] | None = None,
        stdin_bytes: bytes | None = None,
        network_policy: NetworkPolicy = DENY_ALL_NETWORK,
    ) -> IsolatedProcessResult:
        """Run with no ambient environment or user-file access, under the given network policy."""

        if not command or not all(str(part) for part in command):
            raise IsolationError("isolated command cannot be empty")
        working_directory = Path(cwd).resolve()
        if not working_directory.is_dir():
            raise IsolationError(f"isolated working directory does not exist: {working_directory}")
        readable = tuple(Path(path).resolve() for path in readable_paths)
        writable = tuple(Path(path).resolve() for path in writable_paths)
        if not any(
            working_directory == path or working_directory.is_relative_to(path)
            for path in writable
        ):
            raise IsolationError("isolated working directory must be inside a writable grant")
        for path in (*readable, *writable):
            if not path.exists():
                raise IsolationError(f"isolated path grant does not exist: {path}")

        profile = self._profile(readable, writable, network_policy)
        fd, profile_path = tempfile.mkstemp(
            prefix=".factory-sandbox-",
            suffix=".sb",
            dir=working_directory,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(profile)
                handle.flush()
                os.fsync(handle.fileno())
            lane_environment = {
                "HOME": str(working_directory),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                "PYTHONDONTWRITEBYTECODE": "1",
                "TMPDIR": str(working_directory),
                **dict(environment or {}),
            }
            invocation = [
                str(self.executable),
                "-f",
                profile_path,
                *[str(part) for part in command],
            ]
            if stdin_bytes is None:
                completed_text = subprocess.run(
                    invocation,
                    cwd=working_directory,
                    env=lane_environment,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
                returncode = completed_text.returncode
                stdout = completed_text.stdout
                stderr = completed_text.stderr
            else:
                completed_bytes = subprocess.run(
                    invocation,
                    cwd=working_directory,
                    env=lane_environment,
                    check=False,
                    capture_output=True,
                    input=stdin_bytes,
                    timeout=self.timeout_seconds,
                )
                returncode = completed_bytes.returncode
                stdout = completed_bytes.stdout.decode("utf-8", errors="replace")
                stderr = completed_bytes.stderr.decode("utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise IsolationError(f"isolated process execution failed: {exc}") from exc
        finally:
            if os.path.exists(profile_path):
                os.unlink(profile_path)
        return IsolatedProcessResult(
            command=tuple(str(part) for part in command),
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def qualify(
        self,
        root: str | Path,
        network_policy: NetworkPolicy = DENY_ALL_NETWORK,
    ) -> IsolationQualification:
        """Prove the exact boundary this policy claims with real allow/deny probes.

        For deny-all the probe requires read, write, bind, and loopback-connect all refused.
        For loopback-range it additionally requires the permitted in-range use to succeed
        while every out-of-range endpoint — a live unrelated loopback listener, an
        out-of-range bind, and an external address — is refused with an EPERM/EACCES sandbox
        denial (a timeout or connection-refused is treated as qualification failure, not
        denial).
        """

        qualification_root = Path(root).resolve()
        qualification_root.mkdir(parents=True, exist_ok=True)
        allowed = qualification_root / "allowed"
        forbidden = qualification_root / "forbidden"
        allowed.mkdir()
        forbidden.mkdir()
        secret = forbidden / "secret.txt"
        secret.write_text("must-not-be-readable", encoding="utf-8")
        outside_write = forbidden / "must-not-be-created.txt"

        deny_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        deny_listener.bind(("127.0.0.1", 0))
        deny_listener.listen(1)
        deny_port = deny_listener.getsockname()[1]

        in_range_listener: socket.socket | None = None
        environment = {
            "FACTORY_FORBIDDEN_READ": str(secret),
            "FACTORY_FORBIDDEN_WRITE": str(outside_write),
            "FACTORY_UNRELATED_PORT": str(deny_port),
            "FACTORY_NETWORK_MODE": network_policy.mode,
        }
        if network_policy.mode == "loopback-range":
            # A host listener on the first in-range port proves permitted connect; the
            # candidate would bind these ports, so the probe also binds the last in-range
            # port (distinct from the listener) to prove permitted bind, and one port just
            # past the block to prove the block is exact.
            in_range_connect = network_policy.ports[0]
            in_range_bind = network_policy.ports[-1]
            out_of_range_bind = network_policy.ports[-1] + 1
            if out_of_range_bind == deny_port or in_range_bind == deny_port:
                raise IsolationError("qualification port allocation collided; retry attempt")
            in_range_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            in_range_listener.bind(("127.0.0.1", in_range_connect))
            in_range_listener.listen(1)
            environment.update(
                {
                    "FACTORY_IN_RANGE_CONNECT_PORT": str(in_range_connect),
                    "FACTORY_IN_RANGE_BIND_PORT": str(in_range_bind),
                    "FACTORY_OUT_OF_RANGE_BIND_PORT": str(out_of_range_bind),
                }
            )

        try:
            process = self.run(
                (sys.executable, "-c", _QUALIFICATION_PROBE),
                cwd=allowed,
                writable_paths=(allowed,),
                environment=environment,
                network_policy=network_policy,
            )
        finally:
            deny_listener.close()
            if in_range_listener is not None:
                in_range_listener.close()
        if process.returncode != 0:
            raise IsolationError(
                "sandbox qualification process failed: "
                + (process.stderr.strip() or process.stdout.strip() or "no diagnostic")
            )
        try:
            result = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise IsolationError("sandbox qualification returned invalid evidence") from exc
        qualification = IsolationQualification(
            backend="macos-seatbelt",
            read_denied=result.get("read_denied") is True,
            write_denied=result.get("write_denied") is True,
            network_denied=result.get("network_denied") is True,
            network_policy=network_policy.mode,
            loopback_in_range_ok=result.get("loopback_in_range_ok") is True,
            loopback_out_of_range_denied=result.get("loopback_out_of_range_denied") is True,
            external_denied=result.get("external_denied") is True,
            unrelated_listener_denied=result.get("unrelated_listener_denied") is True,
        )
        if not qualification.satisfied:
            raise IsolationError(f"sandbox denial qualification failed: {qualification}")
        if outside_write.exists():
            raise IsolationError("sandbox write-denial probe created the forbidden file")
        return qualification

    def _profile(
        self,
        readable_paths: Sequence[Path],
        writable_paths: Sequence[Path],
        network_policy: NetworkPolicy = DENY_ALL_NETWORK,
    ) -> str:
        read_filters = "\n    ".join(
            _path_filter(path)
            for path in (*self.interpreter_read_paths, *readable_paths, *writable_paths)
        )
        write_filters = "\n    ".join(_path_filter(path) for path in writable_paths)
        network_clauses = network_policy._network_clauses()
        network_section = f"\n{network_clauses}\n" if network_clauses else "\n"
        return f"""(version 1)
(deny default)
(import "system.sb")
(allow process*)
(allow file-read-metadata)
(allow file-read* file-map-executable
    (subpath "/opt/homebrew")
    (subpath "/usr/local")
    (subpath "/bin")
    (subpath "/private/etc")
    {read_filters})
(allow file-write*
    {write_filters}){network_section}"""
