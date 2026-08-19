#!/usr/bin/env python3
"""Serialize advisory-event production, disposition, and lane admission.

The attention channel has one ordering question: did a blocking event become durable before or
after a lane dispatch was admitted?  Every in-tree producer and consumer takes the same run-local
lock.  Dispatch checks both applicable blocking files and acquires its crash-released role mutex
while holding that lock, making lock acquisition the ordering point rather than a comment about one.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
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
_MAX_BLOCKING_BYTES = 1_048_576
_MAX_LEDGER_LINE_BYTES = 65_536


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
    row = (json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor = os.open(
        path,
        os.O_RDWR
        | os.O_CREAT
        | os.O_APPEND
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise AttentionGateError(f"attention event sink is not regular: {path}")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        # The file lock, rather than the broader attention lock, is the common
        # ordering primitive with legacy dispatcher writers.  Snapshot the rollback
        # boundary only after acquiring it so a failed append can never erase bytes
        # another supported producer made durable while we were waiting.
        start = os.fstat(descriptor).st_size
        # events.jsonl predates this closed writer and may end in an interrupted legacy fragment.
        # Preserve those bytes as their own (invalid, ignored) row rather than concatenating the
        # new closed receipt onto them.
        payload = (b"\n" if start and os.pread(descriptor, 1, start - 1) != b"\n" else b"") + row
        written = 0
        try:
            while written < len(payload):
                count = os.write(descriptor, payload[written:])
                if count < 1:
                    raise OSError("attention event append made no progress")
                written += count
            os.fsync(descriptor)
        except OSError:
            # The shared attention lock excludes every supported writer/consumer while this
            # canonical inode is repaired. A retry can therefore inspect either the prior exact
            # prefix or the complete new row, never an abandoned partial JSON record.
            os.ftruncate(descriptor, start)
            os.fsync(descriptor)
            raise
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _repair_incomplete_blocking_tail(path: pathlib.Path) -> None:
    """Discard only an unterminated final blocker row left by a killed writer.

    Blocking files contain only closed, newline-terminated producer events.  The
    run-wide attention lock is already held by the caller; the per-file lock also
    excludes legacy readers/writers while the exact last complete-row boundary is
    restored.  Complete malformed rows remain fail-closed evidence.
    """

    try:
        descriptor = os.open(
            path,
            os.O_RDWR
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except FileNotFoundError:
        return
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise AttentionGateError(f"blocking source is not regular: {path}")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        current = os.fstat(descriptor)
        if current.st_size > _MAX_BLOCKING_BYTES:
            raise AttentionGateError(f"blocking source exceeds its byte ceiling: {path}")
        if not current.st_size or os.pread(descriptor, 1, current.st_size - 1) == b"\n":
            # A previous producer may have been killed after the complete row reached
            # the page cache but before _append_jsonl fsynced it.  Exact retry is the
            # recovery boundary: make the already-published blocker durable before a
            # blocking_written receipt can be admitted for it.
            os.fsync(descriptor)
            return
        raw = os.pread(descriptor, current.st_size, 0)
        boundary = raw.rfind(b"\n") + 1
        os.ftruncate(descriptor, boundary)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _event_digest(event: Mapping[str, object]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(event).encode("utf-8")).hexdigest()


def _read_jsonl(path: pathlib.Path, *, maximum_bytes: int | None = None) -> list[dict[str, object]]:
    """Stable-read one regular JSONL sink while cooperating appenders are locked out."""

    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except FileNotFoundError:
        return []
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise AttentionGateError(f"attention event sink is not regular: {path}")
        if maximum_bytes is not None and opened.st_size > maximum_bytes:
            raise AttentionGateError(f"attention event sink exceeds its byte ceiling: {path}")
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            fcntl.flock(stream, fcntl.LOCK_SH)
            rows: list[dict[str, object]] = []
            for number, line in enumerate(stream, 1):
                if len(line.encode("utf-8")) > _MAX_LEDGER_LINE_BYTES:
                    raise AttentionGateError(
                        f"attention event row {number} exceeds its byte ceiling: {path}"
                    )
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise AttentionGateError(
                        f"attention event row {number} is not JSON: {path}"
                    ) from exc
                if not isinstance(row, dict):
                    raise AttentionGateError(
                        f"attention event row {number} is not an object: {path}"
                    )
                rows.append(row)
            installed = os.lstat(path)
            if stat.S_ISLNK(installed.st_mode) or (
                installed.st_dev,
                installed.st_ino,
            ) != (opened.st_dev, opened.st_ino):
                raise AttentionGateError(f"attention event sink changed while read: {path}")
            return rows
    except UnicodeDecodeError as exc:
        raise AttentionGateError(f"attention event sink is not UTF-8: {path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _event_state(
    root: pathlib.Path,
    lane: str,
    admitted: Mapping[str, object],
) -> tuple[int, int, int]:
    """Return exact blocker, write-receipt, and consume-receipt counts."""

    canonical = _canonical(admitted)
    blocker_count = 0
    for row in _read_jsonl(
        root / "lanes" / f"{lane}.blocking",
        maximum_bytes=_MAX_BLOCKING_BYTES,
    ):
        validated = validate_blocking_event(row)
        if _canonical(validated) == canonical:
            blocker_count += 1
    digest = _event_digest(admitted)
    written_count, consumed_count = _receipt_counts(
        root / "events.jsonl",
        lane=lane,
        canonical_event=canonical,
        event_digest=digest,
    )
    if max(blocker_count, written_count, consumed_count) > 1:
        raise AttentionGateError("blocking event identity is duplicated")
    if consumed_count and not written_count:
        raise AttentionGateError("blocking event was consumed without a write receipt")
    if written_count and not blocker_count and not consumed_count:
        raise AttentionGateError("blocking event write receipt has no pending or consumed subject")
    return blocker_count, written_count, consumed_count


def _receipt_counts(
    path: pathlib.Path,
    *,
    lane: str,
    canonical_event: str,
    event_digest: str,
) -> tuple[int, int]:
    """Count only closed attention receipts in the heterogeneous legacy event stream.

    This shared ledger intentionally admits unrelated historical event shapes and bounded-tail
    readers tolerate an interrupted final legacy fragment.  Those rows cannot grant idempotence:
    only a complete small object with the exact lane and event identity is counted.
    """

    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except FileNotFoundError:
        return 0, 0
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise AttentionGateError(f"attention event sink is not regular: {path}")
        written = 0
        consumed = 0
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            fcntl.flock(stream, fcntl.LOCK_SH)
            for raw in stream:
                if len(raw) > _MAX_LEDGER_LINE_BYTES:
                    continue
                try:
                    candidate = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(candidate, dict) or candidate.get("lane") != lane:
                    continue
                if (
                    candidate.get("kind") == "blocking_written"
                    and _canonical(candidate.get("event")) == canonical_event
                ):
                    written += 1
                if (
                    candidate.get("kind") == "blocking_consumed"
                    and candidate.get("event_digest") == event_digest
                ):
                    consumed += 1
            installed = os.lstat(path)
            if stat.S_ISLNK(installed.st_mode) or (
                installed.st_dev,
                installed.st_ino,
            ) != (opened.st_dev, opened.st_ino):
                raise AttentionGateError(f"attention event sink changed while read: {path}")
        return written, consumed
    finally:
        if descriptor >= 0:
            os.close(descriptor)


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
        _repair_incomplete_blocking_tail(root / "lanes" / f"{lane}.blocking")
        blocker_count, written_count, consumed_count = _event_state(root, lane, admitted)
        if consumed_count:
            return
        if not blocker_count:
            _append_jsonl(root / "lanes" / f"{lane}.blocking", admitted)
        if not written_count:
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


def check_lane_admission(root: pathlib.Path, lane: str) -> None:
    """Linearize a legacy lane start against the same producer/consumer lock."""

    root = pathlib.Path(root)
    if lane not in _LANES:
        raise AttentionGateError("invalid lane admission identity")
    lock_fd = acquire_attention_lock(root)
    try:
        candidates = [root / "lanes" / f"{lane}.blocking"]
        if lane in _DISPATCH_ROLES:
            candidates.insert(0, root / "lanes" / "validator.blocking")
        pending = tuple(path for path in dict.fromkeys(candidates) if _file_has_bytes(path))
        if pending:
            raise BlockingEventPending(pending)
    finally:
        os.close(lock_fd)


def _open_dispatch_lock(root: pathlib.Path, role: str) -> int:
    lock_path = root / "lanes" / f".dispatch-{role}.lock"
    descriptor = os.open(
        lock_path,
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise AttentionGateError("role dispatch lock is not a regular file")
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        _fsync_directory(lock_path.parent)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AttentionGateError("role dispatch is already active") from exc
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def acquire_dispatch_lock(root: pathlib.Path, role: str) -> int:
    """Check blockers and acquire a crash-released role mutex at one ordering point."""

    root = pathlib.Path(root)
    if role not in _DISPATCH_ROLES:
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
        return _open_dispatch_lock(root, role)
    finally:
        os.close(lock_fd)


def verify_dispatch_lock(root: pathlib.Path, role: str, descriptor: int) -> None:
    """Verify the role mutex and repeat admission at this process boundary.

    An environment variable and caller-opened descriptor are not authority.  The
    recursive process therefore rechecks both blockers under the shared attention
    lock even when the descriptor names and locks the canonical inode.
    """

    if role not in _DISPATCH_ROLES or descriptor < 3:
        raise AttentionGateError("invalid inherited dispatch lock")
    lock_path = pathlib.Path(root) / "lanes" / f".dispatch-{role}.lock"
    opened = os.fstat(descriptor)
    installed = os.lstat(lock_path)
    if (
        not stat.S_ISREG(opened.st_mode)
        or stat.S_ISLNK(installed.st_mode)
        or (opened.st_dev, opened.st_ino) != (installed.st_dev, installed.st_ino)
    ):
        raise AttentionGateError("inherited dispatch lock differs from its canonical inode")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise AttentionGateError("inherited dispatch lock is not owned by this invocation") from exc
    attention_fd = acquire_attention_lock(pathlib.Path(root))
    try:
        pending = tuple(
            path
            for path in (
                pathlib.Path(root) / "lanes" / "validator.blocking",
                pathlib.Path(root) / "lanes" / f"{role}.blocking",
            )
            if _file_has_bytes(path)
        )
        if pending:
            raise BlockingEventPending(pending)
    finally:
        os.close(attention_fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    hold = subparsers.add_parser("hold")
    hold.add_argument("--root", type=pathlib.Path, required=True)
    hold.add_argument("--role", choices=sorted(_DISPATCH_ROLES), required=True)
    hold.add_argument("command_args", nargs=argparse.REMAINDER)
    verify = subparsers.add_parser("verify-held")
    verify.add_argument("--root", type=pathlib.Path, required=True)
    verify.add_argument("--role", choices=sorted(_DISPATCH_ROLES), required=True)
    verify.add_argument("--fd", type=int, required=True)
    check = subparsers.add_parser("check")
    check.add_argument("--root", type=pathlib.Path, required=True)
    check.add_argument("--lane", choices=sorted(_LANES), required=True)
    arguments = parser.parse_args()
    try:
        if arguments.command == "check":
            check_lane_admission(arguments.root, arguments.lane)
        elif arguments.command == "verify-held":
            verify_dispatch_lock(arguments.root, arguments.role, arguments.fd)
        else:
            command = list(arguments.command_args)
            if command[:1] == ["--"]:
                command = command[1:]
            if not command:
                raise AttentionGateError("dispatch lock holder has no command")
            dispatch_fd = acquire_dispatch_lock(arguments.root, arguments.role)
            os.set_inheritable(dispatch_fd, True)
            environment = dict(os.environ)
            environment["FACTORY_DISPATCH_LOCK_FD"] = str(dispatch_fd)
            os.execvpe(command[0], command, environment)
    except BlockingEventPending as exc:
        print("blocking event pending — disposition before dispatching:", file=sys.stderr)
        for path in exc.paths:
            print(f"  {path}", file=sys.stderr)
        return 81
    except (AttentionGateError, OSError) as exc:
        print(f"attention admission refused: {exc}", file=sys.stderr)
        return 70
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
