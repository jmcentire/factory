"""Trusted host supervision of a sealed candidate over an exact loopback range.

macOS forbids a nested Seatbelt sandbox inside a lane, so the candidate cannot be a
narrower-profiled child of the validator lane. Instead the host TCB (this module, run by the
orchestrator outside every lane) starts the sealed candidate as a *sibling* under its own
narrow ``loopback-bind`` profile — candidate artifact and runtime only, no Tester tree or
acceptance catalog, bind/listen only inside a freshly allocated contiguous loopback block,
and no outbound connection. The validator lane runs separately under ``loopback-connect`` and
drives the candidate over that same block. The supervisor owns readiness, the candidate's
process group, its logs, teardown, range revocation, and the no-surviving-listener assertion.

Concurrent attempts never share a block: allocation is serialized through a lock-file registry
of live ranges, and a held reservation socket on the block's first port closes the
allocate-then-race window before the candidate binds.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from factory_runtime.isolation import MacOSSandbox, NetworkPolicy

_REGISTRY_DIR = Path(
    os.environ.get(
        "FACTORY_LOOPBACK_REGISTRY",
        str(Path(tempfile.gettempdir()) / f"factory-loopback-{os.getuid()}"),
    )
)
_REGISTRY_FILE = _REGISTRY_DIR / "active-ranges.json"
_RANGE_FLOOR = 49152  # IANA dynamic/ephemeral range floor
_RANGE_CEILING = 65500
_READINESS_TIMEOUT_SECONDS = 30.0
_READINESS_POLL_SECONDS = 0.2


class LoopbackSupervisionError(RuntimeError):
    """The sealed candidate could not be supervised over an exact loopback range."""


@dataclass(frozen=True)
class CandidateEndpoint:
    """The exact per-attempt endpoint contract handed to the validator lane.

    One freshly allocated contiguous block is partitioned into three disjoint parts: the TCP
    ``signaling_port`` the candidate serves ``/offer`` on, the ``candidate_udp_ports`` block
    the candidate gathers its ICE host candidates in, and the ``validator_udp_ports`` block the
    validator's acceptance oracle gathers in. The two peers send only to each other's block.
    """

    host: str
    signaling_port: int
    candidate_udp_ports: tuple[int, ...]
    validator_udp_ports: tuple[int, ...]
    base_url: str

    @property
    def port_low(self) -> int:
        return self.signaling_port

    @property
    def port_high(self) -> int:
        return self.validator_udp_ports[-1]

    @property
    def block(self) -> tuple[int, int]:
        return (self.signaling_port, self.validator_udp_ports[-1])


def _load_active(handle: os.PathLike[str] | int) -> list[list[int]]:
    try:
        raw = Path(_REGISTRY_FILE).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    try:
        blocks = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [list(block) for block in blocks if isinstance(block, list) and len(block) == 2]


def _overlaps(lo: int, hi: int, active: list[list[int]]) -> bool:
    return any(not (hi < a_lo or lo > a_hi) for a_lo, a_hi in active)


@contextlib.contextmanager
def _registry_lock() -> Iterator[int]:
    _REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = _REGISTRY_DIR / ".registry.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _register(lo: int, hi: int) -> None:
    with _registry_lock():
        active = _load_active(0)
        active.append([lo, hi])
        _REGISTRY_FILE.write_text(json.dumps(active), encoding="utf-8")


def _revoke(lo: int, hi: int) -> None:
    with _registry_lock():
        active = [b for b in _load_active(0) if b != [lo, hi]]
        _REGISTRY_FILE.write_text(json.dumps(active), encoding="utf-8")


def _allocate_block(size: int) -> tuple[int, int, socket.socket]:
    """Reserve a fresh contiguous block; hold a socket on its first port to close the race."""

    if not (1 <= size <= 64):
        raise LoopbackSupervisionError("loopback block size must be between 1 and 64 ports")
    with _registry_lock():
        active = _load_active(0)
        base = _RANGE_FLOOR
        while base + size - 1 <= _RANGE_CEILING:
            lo, hi = base, base + size - 1
            if not _overlaps(lo, hi, active):
                reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                reservation.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    reservation.bind(("127.0.0.1", lo))
                    reservation.listen(1)
                except OSError:
                    reservation.close()
                    base += size
                    continue
                active.append([lo, hi])
                _REGISTRY_FILE.write_text(json.dumps(active), encoding="utf-8")
                return lo, hi, reservation
            base += size
    raise LoopbackSupervisionError("no free loopback block available for this attempt")


def _listeners_in_range(lo: int, hi: int) -> list[int]:
    live: list[int] = []
    for port in range(lo, hi + 1):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(0.5)
        try:
            probe.connect(("127.0.0.1", port))
            live.append(port)
        except OSError:
            pass
        finally:
            probe.close()
    return live


def _udp_ports_still_held(ports: Sequence[int]) -> list[int]:
    """Return UDP ports that cannot be bound — i.e. a leaked socket is still holding them."""

    held: list[int] = []
    for port in ports:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            held.append(port)
        finally:
            probe.close()
    return held


_PIN_SITECUSTOMIZE = (
    "# Materialised by the Factory loopback supervisor: pin peer-local WebRTC ICE to the\n"
    "# per-attempt UDP block before the sealed candidate imports aiortc. Keeps the candidate\n"
    "# source untouched; the pinning is a host-owned isolation concern.\n"
    "try:\n"
    "    import factory_webrtc_pin\n"
    "    factory_webrtc_pin.apply()\n"
    "except Exception as exc:  # fail loud in the candidate log, never silently unpinned\n"
    "    import sys\n"
    "    sys.stderr.write('factory ICE pin failed: %r\\n' % (exc,))\n"
    "    raise\n"
)


def _materialize_pin_shim(work_root: Path) -> Path:
    """Write a standalone copy of the pin module + a sitecustomize that applies it.

    The candidate sandbox reads only its own source, runtime, and this work root, so the pin
    logic is copied here as a self-contained module rather than imported from ``factory_runtime``
    (which the candidate cannot see). ``sitecustomize`` is auto-imported by the ``site`` module
    at interpreter start, before the candidate's ``import aiortc``.
    """

    from factory_runtime import webrtc_pin

    shim_dir = work_root / "pin"
    shim_dir.mkdir(parents=True, exist_ok=True)
    source = Path(webrtc_pin.__file__).read_text(encoding="utf-8")
    (shim_dir / "factory_webrtc_pin.py").write_text(source, encoding="utf-8")
    (shim_dir / "sitecustomize.py").write_text(_PIN_SITECUSTOMIZE, encoding="utf-8")
    return shim_dir


@contextlib.contextmanager
def supervised_candidate(
    *,
    candidate_launch: Sequence[str],
    candidate_source: Path,
    candidate_runtime: Path | None,
    work_root: Path,
    udp_block_size: int = 4,
    sandbox: MacOSSandbox | None = None,
    readiness_timeout: float = _READINESS_TIMEOUT_SECONDS,
) -> Iterator[CandidateEndpoint]:
    """Start the sealed candidate as a supervised WebRTC-capable sibling; tear it down cleanly.

    One freshly allocated contiguous block is partitioned into a TCP signaling port, the
    candidate's own UDP ICE block, and the validator's UDP ICE block. The candidate runs under
    a ``candidate-webrtc`` profile — it may bind the signaling port and its own UDP block and
    send only to the validator's UDP block; it reads only its source, runtime, and this work
    root (never the Tester tree). Its ICE binds are pinned to its UDP block through a
    materialised ``sitecustomize`` shim, so the sealed source is untouched. Readiness is a live
    TCP listener on the signaling port. On exit the whole process group is killed, the signaling
    listener must be gone, and the candidate UDP block must be fully bindable (no leaked socket)
    before the range is revoked.
    """

    sandbox = sandbox or MacOSSandbox()
    if not candidate_launch or not Path(candidate_launch[0]).is_absolute():
        raise LoopbackSupervisionError("candidate launch argv[0] must be an absolute path")
    if not (1 <= udp_block_size <= 24):
        raise LoopbackSupervisionError("candidate UDP block size must be between 1 and 24")
    work_root = Path(work_root)
    work_root.mkdir(parents=True, exist_ok=True)
    block_size = 1 + 2 * udp_block_size
    lo, hi, reservation = _allocate_block(block_size)
    signaling_port = lo
    candidate_udp = tuple(range(lo + 1, lo + 1 + udp_block_size))
    validator_udp = tuple(range(lo + 1 + udp_block_size, lo + 1 + 2 * udp_block_size))
    policy = NetworkPolicy(
        mode="candidate-webrtc",
        signaling_port=signaling_port,
        own_udp_ports=candidate_udp,
        peer_udp_ports=validator_udp,
    )
    shim_dir = _materialize_pin_shim(work_root)
    readable = [Path(candidate_source).resolve(), shim_dir.resolve()]
    if candidate_runtime is not None:
        readable.append(Path(candidate_runtime).resolve())
    profile = sandbox._profile(tuple(readable), (work_root.resolve(),), policy)
    profile_path = work_root / ".candidate.sb"
    profile_path.write_text(profile, encoding="utf-8")
    log_path = work_root / "candidate.log"
    environment = {
        "HOME": str(work_root),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(shim_dir),
        "TMPDIR": str(work_root),
        "HOST": "127.0.0.1",
        "PORT": str(signaling_port),
        "FACTORY_ICE_HOST": "127.0.0.1",
        "FACTORY_ICE_UDP_PORTS": ",".join(str(p) for p in candidate_udp),
        "FACTORY_CANDIDATE_SIGNALING_PORT": str(signaling_port),
    }
    invocation = [
        str(sandbox.executable),
        "-f",
        str(profile_path),
        *[str(part) for part in candidate_launch],
    ]
    process: subprocess.Popen[bytes] | None = None
    log = open(log_path, "wb")
    try:
        reservation.close()  # hand the signaling port to the candidate; block stays registered
        process = subprocess.Popen(
            invocation,
            cwd=str(Path(candidate_source).resolve()),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        deadline = time.monotonic() + readiness_timeout
        ready = False
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise LoopbackSupervisionError(
                    "sealed candidate exited before it became ready: "
                    + log_path.read_text(errors="replace")[-1200:]
                )
            if signaling_port in _listeners_in_range(signaling_port, signaling_port):
                ready = True
                break
            time.sleep(_READINESS_POLL_SECONDS)
        if not ready:
            raise LoopbackSupervisionError(
                "sealed candidate never listened on its signaling port within the timeout: "
                + log_path.read_text(errors="replace")[-1200:]
            )
        yield CandidateEndpoint(
            host="127.0.0.1",
            signaling_port=signaling_port,
            candidate_udp_ports=candidate_udp,
            validator_udp_ports=validator_udp,
            base_url=f"http://127.0.0.1:{signaling_port}",
        )
    finally:
        if process is not None:
            _terminate_group(process)
        log.close()
        survivors = _listeners_in_range(signaling_port, signaling_port)
        leaked_udp = _udp_ports_still_held(candidate_udp)
        _revoke(lo, hi)
        if survivors:
            raise LoopbackSupervisionError(
                f"signaling port {signaling_port} still had a live listener after teardown"
            )
        if leaked_udp:
            raise LoopbackSupervisionError(
                f"candidate UDP ports leaked after teardown: {leaked_udp}"
            )


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    try:
        pgid = os.getpgid(process.pid)
    except ProcessLookupError:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=1.0 if sig == signal.SIGTERM else 3.0)
            return
        except subprocess.TimeoutExpired:
            continue
