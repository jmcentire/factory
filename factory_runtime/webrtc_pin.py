"""Trusted bounded ICE port handoff for peer-local WebRTC under an exact-port sandbox.

An exact-port Seatbelt UDP profile denies ephemeral ``bind(0)`` (proven on host), so aioice's
host-candidate gathering — which binds ``local_addr=(address, 0)`` — cannot rely on kernel
ephemeral selection. Applied before any ``RTCPeerConnection`` is built, this shim (1) restricts
host candidates to the single pinned loopback host and (2) redirects each ephemeral datagram
bind onto a port drawn from the per-attempt UDP block the host supervisor assigned this
process. No wildcard bind, no external egress, no STUN/TURN: pure peer-local loopback.

This is an isolation-layer concern owned by the host TCB, not application logic. It is applied
to the candidate through a ``sitecustomize`` shim materialised into the candidate's sandbox
(keeping the sealed candidate *source* untouched) and to the Validator's acceptance oracle by
the deterministic validator runner before it drives the peer connection.
"""

from __future__ import annotations

import asyncio.base_events as _base
import os

_APPLIED = False


def parse_ports(spec: str) -> list[int]:
    """Parse a block spec: ``"49301-49304"`` or ``"49301,49302,49303,49304"``."""

    spec = spec.strip()
    if not spec:
        return []
    if "-" in spec:
        low, high = spec.split("-", 1)
        return list(range(int(low), int(high) + 1))
    return [int(part) for part in spec.split(",") if part.strip()]


def apply(host: str | None = None, ports: list[int] | None = None) -> dict[str, object]:
    """Pin aioice host gathering to ``host`` and the assigned UDP ``ports``. Idempotent."""

    global _APPLIED
    host = host or os.environ.get("FACTORY_ICE_HOST", "127.0.0.1")
    if ports is None:
        ports = parse_ports(os.environ.get("FACTORY_ICE_UDP_PORTS", ""))
    if not ports:
        raise RuntimeError("webrtc_pin.apply requires a non-empty per-attempt UDP port block")
    if _APPLIED:
        return {"host": host, "ports": ports, "already": True}

    # A candidate without aioice cannot be a WebRTC peer, so it has no ICE binds to pin. There is
    # nothing to weaken: the kernel sandbox still denies any ephemeral bind(0) it might attempt.
    try:
        import aioice.ice as _ice  # type: ignore[import-not-found]
    except ImportError:
        return {"host": host, "ports": ports, "pinned": False, "reason": "aioice-absent"}

    # (1) Only the pinned loopback host is offered as a host candidate. No LAN, no IPv6.
    _ice.get_host_addresses = lambda use_ipv4, use_ipv6: [host]

    # (2) Redirect every ephemeral datagram bind onto the assigned block, on EVERY event-loop
    #     implementation in play. Patching only asyncio.base_events.BaseEventLoop misses uvloop,
    #     whose Loop.create_datagram_endpoint is a distinct method — and uvicorn (the candidate's
    #     server) defaults to uvloop, so the candidate's real loop would otherwise be unpinned.
    def _make_pinned(original):
        async def pinned(self, protocol_factory, *args, local_addr=None, **kwargs):
            if local_addr is not None and local_addr[0] == host and local_addr[1] == 0:
                last: OSError | None = None
                for port in ports:
                    try:
                        return await original(
                            self, protocol_factory, *args, local_addr=(host, port), **kwargs
                        )
                    except OSError as exc:  # in use or sandbox-denied: try the next in-block port
                        last = exc
                        continue
                raise last if last is not None else OSError("no pinned ICE port available")
            return await original(self, protocol_factory, *args, local_addr=local_addr, **kwargs)

        return pinned

    loop_classes = [_base.BaseEventLoop]
    try:
        import uvloop  # type: ignore[import-not-found]

        loop_classes.append(uvloop.Loop)
    except ImportError:
        pass
    patched = []
    for loop_cls in loop_classes:
        loop_cls.create_datagram_endpoint = _make_pinned(  # type: ignore[method-assign]
            loop_cls.create_datagram_endpoint
        )
        patched.append(f"{loop_cls.__module__}.{loop_cls.__name__}")

    _APPLIED = True
    return {"host": host, "ports": ports, "patched_loops": patched, "already": False}
