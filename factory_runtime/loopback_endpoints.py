"""Per-attempt loopback endpoint reservation and leak-proof cleanup.

The Factory's only networking capability is a Validator-only *declared loopback endpoint*
grant. A target declares the shape it needs (how many TCP/UDP ports, which operations); this
module allocates the concrete per-attempt ports, hands them to the Validator lane as a trusted
input, and — after the lane and everything it launched in-lane has exited — proves that no
listener or socket leaked on those ports before releasing them. Concurrent attempts never share
a port: allocation is serialized through a lock-file registry of live blocks.

There is no candidate sandbox, no port pinning, and no transport knowledge here: the candidate
is launched in-lane by the target's own oracle/runner using the ports exported to it, and the
ports are just integers. Nothing in this module names or imports a transport library.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import socket
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

_REGISTRY_DIR = Path(
    os.environ.get(
        "FACTORY_LOOPBACK_REGISTRY",
        str(Path(tempfile.gettempdir()) / f"factory-loopback-{os.getuid()}"),
    )
)
_REGISTRY_FILE = _REGISTRY_DIR / "active-ranges.json"
_RANGE_FLOOR = 49152  # IANA dynamic/ephemeral range floor
_RANGE_CEILING = 65500
_MAX_BLOCK_PORTS = 64


class LoopbackEndpointError(RuntimeError):
    """A per-attempt loopback endpoint block could not be reserved or cleanly released."""


@dataclass(frozen=True)
class EndpointSpec:
    """A target's declared need for loopback ports of one protocol.

    ``operations`` is any non-empty subset of ``{"bind", "connect"}``; ``count`` is how many
    contiguous ports the target needs for this protocol. The Factory does not interpret what
    the ports are for.
    """

    protocol: str
    operations: tuple[str, ...]
    count: int

    def __post_init__(self) -> None:
        if self.protocol not in ("tcp", "udp"):
            raise LoopbackEndpointError(f"unsupported endpoint protocol: {self.protocol}")
        if not self.operations or any(op not in ("bind", "connect") for op in self.operations):
            raise LoopbackEndpointError("endpoint operations must be a subset of {bind, connect}")
        if list(self.operations) != sorted(set(self.operations)):
            raise LoopbackEndpointError("endpoint operations must be unique and ordered")
        if not (1 <= self.count <= _MAX_BLOCK_PORTS):
            raise LoopbackEndpointError("endpoint count must be between 1 and 64")

    @classmethod
    def from_dict(cls, data: object) -> EndpointSpec:
        if not isinstance(data, dict):
            raise LoopbackEndpointError("endpoint spec must be an object")
        return cls(
            protocol=str(data.get("protocol", "")),
            operations=tuple(str(op) for op in data.get("operations", ())),
            count=int(data.get("count", 0)),
        )


@dataclass(frozen=True)
class LoopbackReservation:
    """The concrete per-attempt ports assigned to each declared protocol."""

    tcp_ports: tuple[int, ...]
    udp_ports: tuple[int, ...]

    @property
    def all_ports(self) -> tuple[int, ...]:
        return tuple(sorted({*self.tcp_ports, *self.udp_ports}))


def _load_active() -> list[list[int]]:
    try:
        raw = _REGISTRY_FILE.read_text(encoding="utf-8")
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


def _allocate_block(size: int) -> tuple[int, int, socket.socket]:
    """Reserve a fresh contiguous block; hold a socket on its first port to close the race."""

    if not (1 <= size <= _MAX_BLOCK_PORTS):
        raise LoopbackEndpointError("loopback block size must be between 1 and 64 ports")
    with _registry_lock():
        active = _load_active()
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
    raise LoopbackEndpointError("no free loopback block available for this attempt")


def _revoke(lo: int, hi: int) -> None:
    with _registry_lock():
        active = [block for block in _load_active() if block != [lo, hi]]
        _REGISTRY_FILE.write_text(json.dumps(active), encoding="utf-8")


def _tcp_listeners(ports: Sequence[int]) -> list[int]:
    live: list[int] = []
    for port in ports:
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
    """Return UDP ports that cannot be rebound — i.e. a leaked socket is still holding them."""

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


@contextlib.contextmanager
def reserve_loopback_endpoints(specs: Sequence[EndpointSpec]) -> Iterator[LoopbackReservation]:
    """Allocate a fresh per-attempt block for the declared specs; prove no leak on release.

    Ports are assigned per protocol from one contiguous block (TCP first, then UDP), so a
    single registry entry covers the attempt and concurrent attempts stay disjoint. On exit —
    after the caller has run and reaped everything that used these ports — no TCP listener and
    no held UDP socket may remain, or the attempt fails closed.
    """

    tcp_count = sum(spec.count for spec in specs if spec.protocol == "tcp")
    udp_count = sum(spec.count for spec in specs if spec.protocol == "udp")
    total = tcp_count + udp_count
    if total == 0:
        raise LoopbackEndpointError("no loopback endpoints were declared")
    lo, hi, holder = _allocate_block(total)
    tcp_ports = tuple(range(lo, lo + tcp_count))
    udp_ports = tuple(range(lo + tcp_count, lo + tcp_count + udp_count))
    holder.close()  # release the race-holder before the lane binds inside the block
    try:
        yield LoopbackReservation(tcp_ports=tcp_ports, udp_ports=udp_ports)
    finally:
        survivors = _tcp_listeners(tcp_ports)
        leaked_udp = _udp_ports_still_held(udp_ports)
        _revoke(lo, hi)
        if survivors:
            raise LoopbackEndpointError(
                f"loopback TCP ports still had live listeners after the attempt: {survivors}"
            )
        if leaked_udp:
            raise LoopbackEndpointError(
                f"loopback UDP ports leaked after the attempt: {leaked_udp}"
            )
