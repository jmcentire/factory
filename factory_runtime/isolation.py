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


def _udp_allowed_bind(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _udp_allowed_send(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2)
    try:
        sock.sendto(b"factory-probe", (host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _udp_denied_bind(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", port))
        return False
    except OSError as exc:
        return exc.errno in _SANDBOX_DENIED
    finally:
        sock.close()


def _udp_denied_send(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2)
    try:
        sock.sendto(b"factory-probe", (host, port))
        return False
    except OSError as exc:
        return exc.errno in _SANDBOX_DENIED
    finally:
        sock.close()


def _allow_probe(entry):
    direction, protocol, port = entry["dir"], entry["proto"], int(entry["port"])
    if protocol == "tcp" and direction == "bind":
        return _allowed_bind(port)
    if protocol == "tcp" and direction == "connect":
        return _allowed_connect(port)
    if protocol == "udp" and direction == "bind":
        return _udp_allowed_bind(port)
    if protocol == "udp" and direction == "connect":
        return _udp_allowed_send("127.0.0.1", port)
    raise SystemExit("unknown allow probe: " + json.dumps(entry))


def _deny_probe(entry):
    direction, protocol = entry["dir"], entry["proto"]
    host = entry.get("host", "127.0.0.1")
    port = int(entry["port"])
    if protocol == "tcp" and direction == "bind":
        return _denied((host, port), connect=False)
    if protocol == "tcp" and direction == "connect":
        return _denied((host, port), connect=True)
    if protocol == "udp" and direction == "bind":
        return _udp_denied_bind(port)
    if protocol == "udp" and direction == "connect":
        return _udp_denied_send(host, port)
    raise SystemExit("unknown deny probe: " + json.dumps(entry))


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
    result["network_denied"] = bind_denied and unrelated_denied and external_denied
    result["permitted_use_ok"] = False
    result["wrong_direction_denied"] = True
    result["out_of_range_denied"] = True
elif mode in ("candidate-webrtc", "validator-webrtc"):
    # Composite peer-local WebRTC lane. The exact permitted/denied endpoints are computed
    # by the parent from the policy rules and passed as a categorized spec; the child proves
    # every permitted use succeeds and every denial is an EPERM/EACCES sandbox refusal.
    spec = json.loads(os.environ["FACTORY_PROBE_SPEC"])
    result["network_denied"] = False
    result["permitted_use_ok"] = all(_allow_probe(entry) for entry in spec["allow"])
    by_category = {}
    for entry in spec["deny"]:
        by_category.setdefault(entry["category"], []).append(_deny_probe(entry))
    result["wrong_direction_denied"] = all(by_category.get("wrong_direction", [True]))
    result["out_of_range_denied"] = all(by_category.get("out_of_range", [True]))
    result["external_denied"] = all(by_category.get("external", [True]))
    result["unrelated_listener_denied"] = all(by_category.get("unrelated", [True]))
elif mode == "loopback-connect":
    # Validator: connect in-range works; binding (any port) and out-of-range connect denied.
    result["network_denied"] = False
    result["permitted_use_ok"] = _allowed_connect(int(os.environ["FACTORY_IN_RANGE_CONNECT_PORT"]))
    result["wrong_direction_denied"] = _denied(("127.0.0.1", 0), connect=False)
    result["out_of_range_denied"] = _denied(
        ("127.0.0.1", int(os.environ["FACTORY_OUT_OF_RANGE_PORT"])), connect=True
    )
else:  # loopback-bind
    # Candidate: bind in-range works; any outbound connect and out-of-range bind denied.
    result["network_denied"] = False
    result["permitted_use_ok"] = _allowed_bind(int(os.environ["FACTORY_IN_RANGE_BIND_PORT"]))
    result["wrong_direction_denied"] = _denied(
        ("127.0.0.1", int(os.environ["FACTORY_IN_RANGE_CONNECT_PORT"])), connect=True
    )
    result["out_of_range_denied"] = _denied(
        ("127.0.0.1", int(os.environ["FACTORY_OUT_OF_RANGE_PORT"])), connect=False
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
    """A lane's exact, enforceable, directional network boundary.

    Three modes, each scoped to an exact per-attempt contiguous loopback TCP block that is
    enumerated as one Seatbelt rule per port (SBPL has no range literal):

    - ``deny-all`` (default): the only policy author (Coder/Tester) and broker lanes ever run
      under. No bind, no connect, not even loopback.
    - ``loopback-bind``: the sealed candidate's own sandbox. It may bind/listen only inside
      the block and may make no outbound connection at all — it is a pure server.
    - ``loopback-connect``: the validator's sandbox. It may connect only to endpoints inside
      the block and may not bind — it drives the candidate but cannot itself stand up a peer.

    Every endpoint outside the block, loopback or external, stays denied in every mode.
    """

    mode: str = "deny-all"
    ports: tuple[int, ...] = ()
    signaling_port: int | None = None
    own_udp_ports: tuple[int, ...] = ()
    peer_udp_ports: tuple[int, ...] = ()

    _MODES = (
        "deny-all",
        "loopback-bind",
        "loopback-connect",
        "candidate-webrtc",
        "validator-webrtc",
    )
    _WEBRTC_MODES = ("candidate-webrtc", "validator-webrtc")

    def __post_init__(self) -> None:
        if self.mode not in self._MODES:
            raise IsolationError(f"unsupported network policy mode: {self.mode}")
        if self.mode in self._WEBRTC_MODES:
            self._validate_webrtc()
            return
        if self.signaling_port is not None or self.own_udp_ports or self.peer_udp_ports:
            raise IsolationError(f"{self.mode} network policy cannot carry WebRTC endpoints")
        if self.mode == "deny-all":
            if self.ports:
                raise IsolationError("deny-all network policy cannot enumerate ports")
            return
        self._validate_block(self.ports)

    @staticmethod
    def _validate_block(ports: tuple[int, ...]) -> None:
        if not ports:
            raise IsolationError("loopback network policy requires at least one port")
        if len(ports) > _MAX_LOOPBACK_RANGE_PORTS:
            raise IsolationError("loopback range exceeds the per-attempt port ceiling")
        if list(ports) != sorted(set(ports)):
            raise IsolationError("loopback range ports must be unique and ascending")
        if ports[-1] - ports[0] + 1 != len(ports):
            raise IsolationError("loopback range ports must be a contiguous block")
        for port in ports:
            if not (1 <= port <= 65535):
                raise IsolationError(f"loopback range port out of range: {port}")

    def _validate_webrtc(self) -> None:
        if self.ports:
            raise IsolationError("a WebRTC policy expresses ports via its typed endpoints")
        if self.signaling_port is None or not (1 <= self.signaling_port <= 65535):
            raise IsolationError("a WebRTC policy requires a valid signaling TCP port")
        self._validate_block(self.own_udp_ports)
        self._validate_block(self.peer_udp_ports)
        own = set(self.own_udp_ports)
        peer = set(self.peer_udp_ports)
        if own & peer:
            raise IsolationError("candidate and validator UDP blocks must be disjoint")
        if self.signaling_port in own or self.signaling_port in peer:
            raise IsolationError("the signaling TCP port must lie outside both UDP blocks")

    @property
    def allows_bind(self) -> bool:
        return self.mode in ("loopback-bind", "candidate-webrtc", "validator-webrtc")

    @property
    def allows_connect(self) -> bool:
        return self.mode in ("loopback-connect", "candidate-webrtc", "validator-webrtc")

    @property
    def identity(self) -> dict[str, object]:
        """Stable, content-addressable description recorded in qualification evidence."""

        if self.mode in self._WEBRTC_MODES:
            return {
                "mode": self.mode,
                "signaling_port": self.signaling_port,
                "own_udp_ports": list(self.own_udp_ports),
                "peer_udp_ports": list(self.peer_udp_ports),
            }
        return {"mode": self.mode, "ports": list(self.ports)}

    def _network_clauses(self) -> str:
        if self.mode == "deny-all":
            return ""
        if self.mode in self._WEBRTC_MODES:
            return self._webrtc_clauses()
        lines: list[str] = []
        for port in self.ports:
            endpoint = _scheme_string(f"localhost:{port}")
            if self.mode == "loopback-bind":
                lines.append(f"(allow network-bind (local tcp {endpoint}))")
                lines.append(f"(allow network-inbound (local tcp {endpoint}))")
            else:  # loopback-connect
                lines.append(f"(allow network-outbound (remote tcp {endpoint}))")
        return "\n".join(lines)

    def _webrtc_clauses(self) -> str:
        """Exact, enumerated, directional TCP-signaling + symmetric-UDP peer-local rules.

        Both peers gather host ICE candidates in their OWN UDP block (bind + inbound) and
        send connectivity checks / media to the PEER block (outbound) — WebRTC peering is
        symmetric, unlike the pure TCP directional lanes. The candidate additionally binds the
        TCP signaling port it serves ``/offer`` on; the validator connects out to it. No
        wildcard, no external address, no protocol-wide grant.
        """

        lines: list[str] = []
        signaling = _scheme_string(f"localhost:{self.signaling_port}")
        if self.mode == "candidate-webrtc":
            lines.append(f"(allow network-bind (local tcp {signaling}))")
            lines.append(f"(allow network-inbound (local tcp {signaling}))")
        else:  # validator-webrtc
            lines.append(f"(allow network-outbound (remote tcp {signaling}))")
        for port in self.own_udp_ports:
            endpoint = _scheme_string(f"localhost:{port}")
            lines.append(f"(allow network-bind (local udp {endpoint}))")
            lines.append(f"(allow network-inbound (local udp {endpoint}))")
        for port in self.peer_udp_ports:
            endpoint = _scheme_string(f"localhost:{port}")
            lines.append(f"(allow network-outbound (remote udp {endpoint}))")
        return "\n".join(lines)


DENY_ALL_NETWORK = NetworkPolicy()


@dataclass(frozen=True)
class IsolationQualification:
    backend: str
    read_denied: bool
    write_denied: bool
    network_denied: bool
    network_policy: str = "deny-all"
    permitted_use_ok: bool = False
    wrong_direction_denied: bool = True
    out_of_range_denied: bool = True
    external_denied: bool = True
    unrelated_listener_denied: bool = True

    @property
    def satisfied(self) -> bool:
        if self.network_policy == "deny-all":
            return self.read_denied and self.write_denied and self.network_denied
        # A directional loopback lane deliberately succeeds at its permitted use inside the
        # block; the guarantee is that filesystem denial holds, the permitted direction works,
        # and everything else is refused: the wrong direction (bind for a connect-only lane,
        # or any outbound for a bind-only lane), any out-of-range endpoint, a live unrelated
        # loopback listener, and every external address. ``network_denied`` is neither required
        # nor expected here.
        return (
            self.read_denied
            and self.write_denied
            and self.permitted_use_ok
            and self.wrong_direction_denied
            and self.out_of_range_denied
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
        webrtc_listeners: list[socket.socket] = []
        environment = {
            "FACTORY_FORBIDDEN_READ": str(secret),
            "FACTORY_FORBIDDEN_WRITE": str(outside_write),
            "FACTORY_UNRELATED_PORT": str(deny_port),
            "FACTORY_NETWORK_MODE": network_policy.mode,
        }
        if network_policy.mode in NetworkPolicy._WEBRTC_MODES:
            spec, webrtc_listeners = self._webrtc_probe_spec(network_policy, deny_port)
            environment["FACTORY_PROBE_SPEC"] = json.dumps(spec)
        elif network_policy.mode != "deny-all":
            # In-range endpoints for the permitted-use probe, plus one port just past the
            # block to prove the block is exact. A connect-only lane needs a live in-range
            # listener; a bind-only lane needs a free in-range port to bind and an in-range
            # port to prove outbound is refused.
            in_range_connect = network_policy.ports[0]
            in_range_bind = network_policy.ports[-1]
            out_of_range = network_policy.ports[-1] + 1
            if deny_port in (in_range_connect, in_range_bind, out_of_range):
                raise IsolationError("qualification port allocation collided; retry attempt")
            if network_policy.mode == "loopback-connect":
                in_range_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                in_range_listener.bind(("127.0.0.1", in_range_connect))
                in_range_listener.listen(1)
            environment.update(
                {
                    "FACTORY_IN_RANGE_CONNECT_PORT": str(in_range_connect),
                    "FACTORY_IN_RANGE_BIND_PORT": str(in_range_bind),
                    "FACTORY_OUT_OF_RANGE_PORT": str(out_of_range),
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
            for listener in webrtc_listeners:
                listener.close()
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
            permitted_use_ok=result.get("permitted_use_ok") is True,
            wrong_direction_denied=result.get("wrong_direction_denied") is True,
            out_of_range_denied=result.get("out_of_range_denied") is True,
            external_denied=result.get("external_denied") is True,
            unrelated_listener_denied=result.get("unrelated_listener_denied") is True,
        )
        if not qualification.satisfied:
            raise IsolationError(f"sandbox denial qualification failed: {qualification}")
        if outside_write.exists():
            raise IsolationError("sandbox write-denial probe created the forbidden file")
        return qualification

    def _webrtc_probe_spec(
        self, policy: NetworkPolicy, deny_port: int
    ) -> tuple[dict[str, object], list[socket.socket]]:
        """Compute the exact permitted/denied endpoints a WebRTC policy claims.

        Both peers must: gather (bind + receive) inside their own UDP block, send only to the
        peer block, and signal over exactly one TCP port (bound by the candidate, connected to
        by the validator). Everything else — the wrong direction, the other peer's UDP block,
        the port just past a block, an unrelated live loopback listener, and every external
        address — must be an EPERM/EACCES refusal.
        """

        sig = int(policy.signaling_port or 0)
        own = policy.own_udp_ports
        peer = policy.peer_udp_ports
        out_of_block = max(sig, own[-1], peer[-1]) + 1
        if deny_port in (sig, out_of_block) or deny_port in own or deny_port in peer:
            raise IsolationError("qualification port allocation collided; retry attempt")

        listeners: list[socket.socket] = []
        allow: list[dict[str, object]] = [
            {"dir": "bind", "proto": "udp", "port": own[-1]},
            {"dir": "connect", "proto": "udp", "port": peer[0]},
        ]
        external = {"host": "192.0.2.1", "port": 9}
        deny: list[dict[str, object]] = [
            {"category": "external", "dir": "connect", "proto": "tcp", **external},
            {"category": "external", "dir": "connect", "proto": "udp", **external},
            {"category": "unrelated", "dir": "connect", "proto": "tcp", "port": deny_port},
            {"category": "out_of_range", "dir": "bind", "proto": "udp", "port": out_of_block},
            {"category": "out_of_range", "dir": "connect", "proto": "udp", "port": out_of_block},
            # The other peer's UDP block: may be sent to, never bound.
            {"category": "wrong_direction", "dir": "bind", "proto": "udp", "port": peer[0]},
            # Own UDP block is receive-only; outbound to it is not granted.
            {"category": "wrong_direction", "dir": "connect", "proto": "udp", "port": own[0]},
        ]
        if policy.mode == "candidate-webrtc":
            allow.append({"dir": "bind", "proto": "tcp", "port": sig})
            deny.append(
                {"category": "wrong_direction", "dir": "connect", "proto": "tcp", "port": sig}
            )
            deny.append(
                {"category": "out_of_range", "dir": "bind", "proto": "tcp", "port": out_of_block}
            )
        else:  # validator-webrtc — the candidate's signaling port is live for the connect probe
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", sig))
            listener.listen(1)
            listeners.append(listener)
            allow.append({"dir": "connect", "proto": "tcp", "port": sig})
            deny.append(
                {"category": "wrong_direction", "dir": "bind", "proto": "tcp", "port": sig}
            )
            deny.append(
                {"category": "out_of_range", "dir": "connect", "proto": "tcp", "port": out_of_block}
            )
        return {"allow": allow, "deny": deny}, listeners

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
