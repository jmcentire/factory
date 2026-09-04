"""Platform-enforced process isolation for Factory lanes.

Separate directories are organization, not isolation. On macOS this backend uses the kernel
sandbox through ``sandbox-exec`` with deny-by-default file and network access. It deliberately
fails closed on unsupported platforms or an absent executable. The profile is qualified by
real denial probes before a lane is trusted.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO


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


def _bind_errno(port, udp):
    family = socket.SOCK_DGRAM if udp else socket.SOCK_STREAM
    sock = socket.socket(socket.AF_INET, family)
    try:
        sock.bind(("127.0.0.1", port))
        return None
    except OSError as exc:
        return exc.errno
    finally:
        sock.close()


def _connect_errno(host, port, udp):
    family = socket.SOCK_DGRAM if udp else socket.SOCK_STREAM
    sock = socket.socket(socket.AF_INET, family)
    sock.settimeout(3)
    try:
        if udp:
            sock.sendto(b"factory-probe", (host, port))
        else:
            sock.connect((host, port))
        return None
    except OSError as exc:
        return exc.errno
    finally:
        sock.close()


def _permitted(entry):
    # A granted bind must actually succeed. A granted connect need only pass the sandbox: a
    # TCP RST / ECONNREFUSED still proves the kernel let the syscall through, so no live peer
    # is required to qualify a connect grant.
    udp = entry["proto"] == "udp"
    host = entry.get("host", "127.0.0.1")
    if entry["dir"] == "bind":
        return _bind_errno(int(entry["port"]), udp) is None
    return _connect_errno(host, int(entry["port"]), udp) not in _SANDBOX_DENIED


def _denied(entry):
    udp = entry["proto"] == "udp"
    host = entry.get("host", "127.0.0.1")
    if entry["dir"] == "bind":
        return _bind_errno(int(entry["port"]), udp) in _SANDBOX_DENIED
    return _connect_errno(host, int(entry["port"]), udp) in _SANDBOX_DENIED


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

spec = json.loads(os.environ["FACTORY_PROBE_SPEC"])
result["permitted_use_ok"] = all(_permitted(entry) for entry in spec["allow"])
result["denied_ok"] = all(_denied(entry) for entry in spec["deny"])
print(json.dumps(result, sort_keys=True))
"""


@dataclass(frozen=True)
class IsolatedProcessResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass
class SandboxProcessHandle:
    """A non-blocking handle to a supervised sandboxed process (e.g. a long-running candidate).

    The process runs in its own session/group under Seatbelt; ``reap`` signals the whole group,
    closes the captured stream files, and removes the persisted profile. Stream output is written
    to the caller's files rather than pipes, so a long-running server cannot deadlock on a full
    pipe buffer while the Validator does other work.
    """

    command: tuple[str, ...]
    popen: subprocess.Popen[bytes]
    pgid: int
    profile_path: str
    stdout_path: Path
    stderr_path: Path
    _streams: tuple[IO[bytes], ...] = ()

    def poll(self) -> int | None:
        return self.popen.poll()

    @property
    def returncode(self) -> int | None:
        return self.popen.returncode

    def reap(self) -> None:
        """Tear down the supervised process group deterministically.

        Unlike the blocking path, the session leader (``sandbox-exec``) has never been waited on
        while the candidate ran, so it can be a zombie whose group ``killpg(.., 0)`` probe returns
        EPERM. SIGKILL the whole group, wait on the leader to clear that zombie, then poll the group
        until it drains — tolerating a transient EPERM by re-signalling. The loopback reservation's
        own no-leak assertion remains the authoritative guard that the port was freed.
        """

        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self.pgid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            self.popen.wait(timeout=5)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                os.killpg(self.pgid, 0)
            except ProcessLookupError:
                break
            except PermissionError:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(self.pgid, signal.SIGKILL)
            time.sleep(0.05)
        for stream in self._streams:
            with contextlib.suppress(Exception):
                stream.close()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.profile_path)


_MAX_LOOPBACK_PORTS = 64
_PROTOCOLS = ("tcp", "udp")
_OPERATIONS = ("bind", "connect")


@dataclass(frozen=True)
class LoopbackGrant:
    """One exact, enumerated permission on a contiguous loopback port block.

    Fully generic and transport-agnostic: it names a protocol (``tcp``/``udp``), an operation
    (``bind`` — the lane may listen; or ``connect`` — the lane may reach out), and the exact
    ports it applies to. SBPL has no port-range literal, so the block is emitted as one rule
    per port. The Factory never interprets what a grant is *for*; a target declares the shape
    it needs and the orchestrator allocates the concrete ports.
    """

    protocol: str
    operation: str
    ports: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.protocol not in _PROTOCOLS:
            raise IsolationError(f"unsupported loopback protocol: {self.protocol}")
        if self.operation not in _OPERATIONS:
            raise IsolationError(f"unsupported loopback operation: {self.operation}")
        ports = self.ports
        if not ports:
            raise IsolationError("a loopback grant requires at least one port")
        if len(ports) > _MAX_LOOPBACK_PORTS:
            raise IsolationError("loopback grant exceeds the per-attempt port ceiling")
        if list(ports) != sorted(set(ports)):
            raise IsolationError("loopback grant ports must be unique and ascending")
        if ports[-1] - ports[0] + 1 != len(ports):
            raise IsolationError("loopback grant ports must be a contiguous block")
        for port in ports:
            if not (1 <= port <= 65535):
                raise IsolationError(f"loopback grant port out of range: {port}")

    def identity(self) -> dict[str, object]:
        return {"protocol": self.protocol, "operation": self.operation, "ports": list(self.ports)}


@dataclass(frozen=True)
class NetworkPolicy:
    """A lane's exact, enforceable network boundary as a set of loopback grants.

    ``deny-all`` (no grants) is the default and the only policy Coder, Tester-authoring, and
    broker lanes ever run under: no bind, no connect, not even loopback. A grant-bearing policy
    is used only by the Validator attempt, scoped to exactly the loopback endpoints a target
    declared and the orchestrator allocated for that attempt. Every endpoint outside the granted
    ports — loopback or external, TCP or UDP — stays denied.
    """

    label: str = "deny-all"
    grants: tuple[LoopbackGrant, ...] = ()

    def __post_init__(self) -> None:
        if not self.label:
            raise IsolationError("network policy requires a non-empty label")
        total = sum(len(grant.ports) for grant in self.grants)
        if total > 4 * _MAX_LOOPBACK_PORTS:
            raise IsolationError("network policy exceeds the total per-attempt port ceiling")

    @classmethod
    def declared_loopback(cls, grants: Sequence[LoopbackGrant]) -> NetworkPolicy:
        grants = tuple(grants)
        if not grants:
            raise IsolationError("a declared-loopback policy requires at least one grant")
        return cls(label="declared-loopback", grants=grants)

    @property
    def loopback_ports(self) -> tuple[int, ...]:
        """Every port this policy touches, ascending — the allocation/cleanup surface."""

        return tuple(sorted({port for grant in self.grants for port in grant.ports}))

    @property
    def identity(self) -> dict[str, object]:
        """Stable, content-addressable description recorded in qualification evidence."""

        return {"label": self.label, "grants": [grant.identity() for grant in self.grants]}

    def _network_clauses(self) -> str:
        lines: list[str] = []
        for grant in self.grants:
            for port in grant.ports:
                endpoint = _scheme_string(f"localhost:{port}")
                if grant.operation == "bind":
                    lines.append(f"(allow network-bind (local {grant.protocol} {endpoint}))")
                    lines.append(f"(allow network-inbound (local {grant.protocol} {endpoint}))")
                else:  # connect
                    lines.append(f"(allow network-outbound (remote {grant.protocol} {endpoint}))")
        return "\n".join(lines)

    def clauses(self) -> str:
        """Public rendering for typed-admission policy verification."""
        return self._network_clauses()


DENY_ALL_NETWORK = NetworkPolicy()


@dataclass(frozen=True)
class IsolationQualification:
    """Proof that a lane's exact boundary holds: filesystem denied, granted ops permitted,
    and every undeclared loopback / external / wrong-operation endpoint refused with EPERM."""

    backend: str
    read_denied: bool
    write_denied: bool
    permitted_use_ok: bool
    denied_ok: bool
    policy_label: str = "deny-all"

    @property
    def satisfied(self) -> bool:
        return (
            self.read_denied
            and self.write_denied
            and self.permitted_use_ok
            and self.denied_ok
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
        reap_process_group: bool = False,
    ) -> IsolatedProcessResult:
        """Run with no ambient environment or user-file access, under the given network policy.

        When ``reap_process_group`` is set the lane runs in its own session and the whole group
        is signalled after it returns, so any process the lane launched in-lane (e.g. a target
        candidate) cannot survive the lane. This is generic process hygiene, not lifecycle
        supervision: it guarantees no lane descendant outlives the lane.
        """

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
            if reap_process_group:
                returncode, stdout, stderr = self._run_reaped(
                    invocation, working_directory, lane_environment, stdin_bytes
                )
            elif stdin_bytes is None:
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

    def _run_reaped(
        self,
        invocation: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
        stdin_bytes: bytes | None,
    ) -> tuple[int, str, str]:
        """Run the lane in its own session and reap the whole group when it returns."""

        process = subprocess.Popen(
            list(invocation),
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        # start_new_session makes the child its own session/group leader, so its pgid == pid.
        # Capture it now: communicate() reaps the leader, after which its pgid is unrecoverable
        # while children (e.g. a launched candidate) may still hold the group — and the port.
        pgid = process.pid
        try:
            out, err = process.communicate(input=stdin_bytes, timeout=self.timeout_seconds)
            returncode = process.returncode
        except subprocess.TimeoutExpired:
            self._reap_group(pgid)
            process.communicate()
            raise
        finally:
            self._reap_group(pgid)
        return (
            returncode,
            out.decode("utf-8", errors="replace"),
            err.decode("utf-8", errors="replace"),
        )

    def spawn(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path,
        stdout_path: str | Path,
        stderr_path: str | Path,
        readable_paths: Sequence[str | Path] = (),
        writable_paths: Sequence[str | Path] = (),
        environment: Mapping[str, str] | None = None,
        network_policy: NetworkPolicy = DENY_ALL_NETWORK,
    ) -> SandboxProcessHandle:
        """Start a supervised sandboxed process non-blocking and return a handle.

        Used for a long-running candidate that must run concurrently with a separate test profile.
        The process is its own session leader (pgid == pid), captured before any wait, so the whole
        group can be reaped later even if the leader exits while children hold a port. Streams are
        written to files, never pipes, so the caller need not drain them.
        """

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
            prefix=".factory-sandbox-", suffix=".sb", dir=working_directory
        )
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
        invocation = [str(self.executable), "-f", profile_path, *[str(part) for part in command]]
        out_handle = open(stdout_path, "wb")
        err_handle = open(stderr_path, "wb")
        try:
            process = subprocess.Popen(
                invocation,
                cwd=working_directory,
                env=lane_environment,
                stdin=subprocess.DEVNULL,
                stdout=out_handle,
                stderr=err_handle,
                start_new_session=True,
            )
        except OSError as exc:
            out_handle.close()
            err_handle.close()
            with contextlib.suppress(FileNotFoundError):
                os.unlink(profile_path)
            raise IsolationError(f"isolated process spawn failed: {exc}") from exc
        return SandboxProcessHandle(
            command=tuple(str(part) for part in command),
            popen=process,
            pgid=process.pid,
            profile_path=profile_path,
            stdout_path=Path(stdout_path),
            stderr_path=Path(stderr_path),
            _streams=(out_handle, err_handle),
        )

    @staticmethod
    def _reap_group(pgid: int) -> None:
        """Reap the lane's whole process group, deterministically, by its captured group id.

        Signalling only the session leader is not enough: a graceful child (e.g. a server doing
        a SIGTERM shutdown) can outlive the leader and keep a port bound. So SIGTERM the group,
        then SIGKILL it, and poll ``killpg(pgid, 0)`` until the group has no members — only then
        is the no-leak assertion race-free. The pgid is captured before the leader is reaped.
        """

        deadline_term = time.monotonic() + 2.0
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGTERM)
        while time.monotonic() < deadline_term:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGKILL)
        deadline_kill = time.monotonic() + 3.0
        while time.monotonic() < deadline_kill:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)

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

        environment = {
            "FACTORY_FORBIDDEN_READ": str(secret),
            "FACTORY_FORBIDDEN_WRITE": str(outside_write),
            "FACTORY_PROBE_SPEC": json.dumps(self._loopback_probe_spec(network_policy, deny_port)),
        }

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
            permitted_use_ok=result.get("permitted_use_ok") is True,
            denied_ok=result.get("denied_ok") is True,
            policy_label=network_policy.label,
        )
        if not qualification.satisfied:
            raise IsolationError(f"sandbox denial qualification failed: {qualification}")
        if outside_write.exists():
            raise IsolationError("sandbox write-denial probe created the forbidden file")
        return qualification

    def _loopback_probe_spec(
        self, policy: NetworkPolicy, deny_port: int
    ) -> dict[str, object]:
        """Derive the exact permitted/denied endpoints any policy claims, generically.

        For each granted (protocol, operation) the child proves the operation is permitted on a
        granted port. Every denial the boundary must uphold is proved too: external TCP and UDP,
        a live unrelated loopback listener, both operations on the port just past every grant
        (undeclared loopback), and any ungranted operation on a protocol that does have grants
        (an exact-scope check). ``deny-all`` yields no allow probes and full denial. No live peer
        is needed — a granted connect qualifies as long as the sandbox does not refuse it.
        """

        granted: dict[tuple[str, str], list[int]] = {}
        for grant in policy.grants:
            granted.setdefault((grant.protocol, grant.operation), []).extend(grant.ports)
        all_ports = policy.loopback_ports
        out_of_grant = (all_ports[-1] + 1) if all_ports else 49500
        if out_of_grant > 65535:
            out_of_grant = all_ports[0] - 1
        if deny_port in all_ports or deny_port == out_of_grant:
            raise IsolationError("qualification port allocation collided; retry attempt")

        allow: list[dict[str, object]] = [
            {"proto": protocol, "dir": operation, "port": max(ports)}
            for (protocol, operation), ports in sorted(granted.items())
        ]
        external = {"host": "192.0.2.1", "port": 9}
        deny: list[dict[str, object]] = [
            {"proto": "tcp", "dir": "connect", **external},
            {"proto": "udp", "dir": "connect", **external},
            {"proto": "tcp", "dir": "connect", "port": deny_port},  # unrelated live listener
        ]
        for protocol in _PROTOCOLS:
            for operation in _OPERATIONS:
                # The port just past every grant — no protocol/operation may reach it.
                deny.append({"proto": protocol, "dir": operation, "port": out_of_grant})
            protocol_ports = sorted(
                {
                    port
                    for grant in policy.grants
                    if grant.protocol == protocol
                    for port in grant.ports
                }
            )
            if not protocol_ports:
                continue
            for operation in _OPERATIONS:
                if (protocol, operation) not in granted:
                    # An ungranted operation on a granted port proves the grant is exact.
                    deny.append({"proto": protocol, "dir": operation, "port": protocol_ports[0]})
        return {"allow": allow, "deny": deny}

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
