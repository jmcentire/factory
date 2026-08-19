#!/usr/bin/env python3
"""Serialize advisory-event production, disposition, and lane admission.

The attention channel has one ordering question: did a blocking event become durable before or
after a lane dispatch was admitted?  Every in-tree producer and consumer takes the same run-local
lock.  Dispatch checks both applicable blocking files and publishes its no-replace guard while
holding that lock, making the guard publication the ordering point rather than a comment about one.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import pathlib
import re
import stat
import sys
from collections.abc import Mapping

_LANES = frozenset({"validator", "coder", "tester"})
_DISPATCH_ROLES = frozenset({"coder", "tester"})
_DISPATCHER_CLASSES = frozenset(
    {
        "stall",
        "target_state_diverged",
        "target_state_verifier_unavailable",
        "invalid_legacy_abandonment",
        "legacy_harness",
    }
)
_CLASS = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_EVENT_BYTES = 16_384


class AttentionGateError(RuntimeError):
    """The attention channel could not establish a safe ordering."""


class BlockingEventPending(AttentionGateError):
    """A pre-admission blocking event must be dispositioned first."""

    def __init__(self, paths: tuple[pathlib.Path, ...]) -> None:
        super().__init__("blocking event pending")
        self.paths = paths


def _open_directory(path: pathlib.Path) -> int:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise AttentionGateError(f"attention path is not a directory: {path}")
    return descriptor


def _fsync_directory(path: pathlib.Path) -> None:
    descriptor = _open_directory(path)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_lanes(root: pathlib.Path) -> pathlib.Path:
    lanes = root / "lanes"
    root_fd = _open_directory(root)
    try:
        try:
            os.mkdir(lanes, 0o700)
        except FileExistsError:
            pass
        lanes_fd = _open_directory(lanes)
        try:
            os.fsync(lanes_fd)
            os.fsync(root_fd)
        finally:
            os.close(lanes_fd)
    finally:
        os.close(root_fd)
    return lanes


def acquire_attention_lock(root: pathlib.Path) -> int:
    """Return a locked descriptor; closing it releases the run-local attention lock."""

    root = pathlib.Path(root)
    lanes = _ensure_lanes(root)
    lock_path = lanes / ".attention.lock"
    descriptor = os.open(
        lock_path,
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise AttentionGateError("attention lock is not a regular file")
    os.fchmod(descriptor, 0o600)
    os.fsync(descriptor)
    _fsync_directory(lanes)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    return descriptor


def _bounded_text(value: object, *, field: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > maximum:
        raise AttentionGateError(f"blocking event has invalid {field}")
    return value


def _validate_timestamp(value: object) -> None:
    raw = _bounded_text(value, field="ts", maximum=64)
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError as exc:
        raise AttentionGateError("blocking event has invalid ts") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        raise AttentionGateError("blocking event ts must be UTC")


def validate_blocking_event(event: object) -> dict[str, object]:
    """Validate the exact event shapes emitted by the two in-tree producers."""

    if not isinstance(event, dict):
        raise AttentionGateError("blocking event is not an object")
    cls = event.get("class")
    if not isinstance(cls, str) or not _CLASS.fullmatch(cls):
        raise AttentionGateError("blocking event has invalid class")
    if cls == "orchestrator_response":
        expected = {"ts", "class", "response", "wake", "trust_class", "effect_route"}
        if set(event) != expected:
            raise AttentionGateError("orchestrator response has unknown or missing fields")
        _bounded_text(event["response"], field="response")
        _bounded_text(event["wake"], field="wake")
    elif cls == "orchestrator_dead":
        expected = {
            "ts",
            "class",
            "wake",
            "evidence",
            "excerpt",
            "trust_class",
            "effect_route",
        }
        if set(event) != expected:
            raise AttentionGateError("orchestrator failure has unknown or missing fields")
        _bounded_text(event["wake"], field="wake")
        _bounded_text(event["evidence"], field="evidence")
        _bounded_text(event["excerpt"], field="excerpt", maximum=1024)
    else:
        if cls not in _DISPATCHER_CLASSES or set(event) != {"ts", "class", "evidence"}:
            raise AttentionGateError("blocking event is not an admitted producer event")
        _bounded_text(event["evidence"], field="evidence")
    _validate_timestamp(event["ts"])
    if cls.startswith("orchestrator_") and (
        event["trust_class"] != "untrusted-advisory"
        or event["effect_route"] != "validator-blocking-only"
    ):
        raise AttentionGateError("orchestrator blocking event overclaims trust or effect")
    raw = json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(raw) > _MAX_EVENT_BYTES:
        raise AttentionGateError("blocking event exceeds its byte ceiling")
    return dict(event)


def _append_jsonl(path: pathlib.Path, body: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_APPEND
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise AttentionGateError(f"attention event sink is not regular: {path}")
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    _fsync_directory(path.parent)


def append_blocking_event(
    root: pathlib.Path, lane: str, event: Mapping[str, object]
) -> None:
    """Durably append one validated event and its write receipt under the shared lock."""

    root = pathlib.Path(root)
    if lane not in _LANES:
        raise AttentionGateError("invalid blocking lane")
    admitted = validate_blocking_event(dict(event))
    lock_fd = acquire_attention_lock(root)
    try:
        _append_jsonl(root / "lanes" / f"{lane}.blocking", admitted)
        _append_jsonl(
            root / "events.jsonl",
            {
                "ts": admitted["ts"],
                "kind": "blocking_written",
                "lane": lane,
                "event": admitted,
            },
        )
    finally:
        os.close(lock_fd)


def _file_has_bytes(path: pathlib.Path) -> bool:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except FileNotFoundError:
        return False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise AttentionGateError(f"blocking source is not regular: {path}")
        return bool(os.read(descriptor, 1))
    finally:
        os.close(descriptor)


def _publish_guard(root: pathlib.Path, role: str, pid: int) -> pathlib.Path:
    guard = root / f"dispatch-{role}.guard"
    descriptor = os.open(
        guard,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(f"pid={pid}\n".encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(root)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return guard


def admit_dispatch(root: pathlib.Path, role: str, *, pid: int) -> pathlib.Path:
    """Check blockers and publish the role guard at one serialized ordering point."""

    root = pathlib.Path(root)
    if role not in _DISPATCH_ROLES or pid < 1:
        raise AttentionGateError("invalid dispatch admission identity")
    lock_fd = acquire_attention_lock(root)
    try:
        pending = tuple(
            path
            for path in (
                root / "lanes" / "validator.blocking",
                root / "lanes" / f"{role}.blocking",
            )
            if _file_has_bytes(path)
        )
        if pending:
            raise BlockingEventPending(pending)
        try:
            return _publish_guard(root, role, pid)
        except FileExistsError as exc:
            raise AttentionGateError("role dispatch is concurrent or interrupted") from exc
    finally:
        os.close(lock_fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    admit = subparsers.add_parser("admit")
    admit.add_argument("--root", type=pathlib.Path, required=True)
    admit.add_argument("--role", choices=sorted(_DISPATCH_ROLES), required=True)
    admit.add_argument("--pid", type=int, required=True)
    arguments = parser.parse_args()
    try:
        guard = admit_dispatch(arguments.root, arguments.role, pid=arguments.pid)
    except BlockingEventPending as exc:
        print("blocking event pending — disposition before dispatching:", file=sys.stderr)
        for path in exc.paths:
            print(f"  {path}", file=sys.stderr)
        return 81
    except (AttentionGateError, OSError) as exc:
        print(f"attention admission refused: {exc}", file=sys.stderr)
        return 70
    print(guard)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
