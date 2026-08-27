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
    """The exact endpoint contract handed to the validator lane."""

    host: str
    port_low: int
    port_high: int
    base_url: str

    @property
    def ports(self) -> tuple[int, ...]:
        return tuple(range(self.port_low, self.port_high + 1))


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


@contextlib.contextmanager
def supervised_candidate(
    *,
    candidate_launch: Sequence[str],
    candidate_source: Path,
    candidate_runtime: Path | None,
    work_root: Path,
    block_size: int = 8,
    sandbox: MacOSSandbox | None = None,
    readiness_timeout: float = _READINESS_TIMEOUT_SECONDS,
) -> Iterator[CandidateEndpoint]:
    """Start the sealed candidate as a supervised loopback-bind sibling; tear it down cleanly.

    The candidate reads only its source and runtime, binds only inside the allocated block,
    and cannot connect anywhere. Readiness is a live listener inside the block. On exit the
    whole process group is killed and the range is asserted clear before revocation.
    """

    sandbox = sandbox or MacOSSandbox()
    if not candidate_launch or not Path(candidate_launch[0]).is_absolute():
        raise LoopbackSupervisionError("candidate launch argv[0] must be an absolute path")
    work_root = Path(work_root)
    work_root.mkdir(parents=True, exist_ok=True)
    lo, hi, reservation = _allocate_block(block_size)
    policy = NetworkPolicy(mode="loopback-bind", ports=tuple(range(lo, hi + 1)))
    readable = [Path(candidate_source).resolve()]
    if candidate_runtime is not None:
        readable.append(Path(candidate_runtime).resolve())
    profile = sandbox._profile(
        tuple(readable), (work_root.resolve(),), policy
    )
    profile_path = work_root / ".candidate.sb"
    profile_path.write_text(profile, encoding="utf-8")
    log_path = work_root / "candidate.log"
    environment = {
        "HOME": str(work_root),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(work_root),
        "HOST": "127.0.0.1",
        "PORT": str(lo),
        "FACTORY_CANDIDATE_PORT_LOW": str(lo),
        "FACTORY_CANDIDATE_PORT_HIGH": str(hi),
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
        reservation.close()  # hand the first port to the candidate; block stays registered
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
        ready_port: int | None = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise LoopbackSupervisionError(
                    "sealed candidate exited before it became ready: "
                    + log_path.read_text(errors="replace")[-800:]
                )
            live = _listeners_in_range(lo, hi)
            if live:
                ready_port = live[0]
                break
            time.sleep(_READINESS_POLL_SECONDS)
        if ready_port is None:
            raise LoopbackSupervisionError(
                "sealed candidate never listened inside its loopback block within the timeout"
            )
        yield CandidateEndpoint(
            host="127.0.0.1",
            port_low=lo,
            port_high=hi,
            base_url=f"http://127.0.0.1:{ready_port}",
        )
    finally:
        if process is not None:
            _terminate_group(process)
        log.close()
        survivors = _listeners_in_range(lo, hi)
        _revoke(lo, hi)
        if survivors:
            raise LoopbackSupervisionError(
                f"loopback block {lo}-{hi} still had live listeners after teardown: {survivors}"
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
