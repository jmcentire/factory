"""validator_input — what the Validator is owed, delivered once into its live session's inbox.

The Validator's inbox (``<run>/validator/inbox.jsonl``) is how anything addressed to it reaches
its live session: a lane's turn end or status, a worker's question, a test result, the response
to an escalation. This module owns that delivery and knows nothing about lanes or turns.

``post`` records a notice as owed in its own file, ``<run>/validator/notices.jsonl`` (appended
under its lock, owner-only, never through a link); posting the same id again records nothing.
``deliver`` writes every owed notice to the inbox through ``run_record.message``, each exactly
once across retries, crashes and two deliverers at once: before a notice's first attempt the
inbox's end, read under the inbox's lock, is recorded (the first record wins), and every
attempt writes the notice only if the inbox does not hold it from there, decided under the same
lock. A notice still owed when ``deliver`` returns is retried by the next one, and is recorded
once in ``events.jsonl`` as ``validator-input-undelivered``, which wakes the operator.

Delivery is not acceptance. A notice is ``owed`` or ``delivered``, never accepted or approved:
that is only ever the Validator's own verdict, and every notice's text ends by saying so.
"""

from __future__ import annotations

import datetime
import fcntl
import json
import os
import pathlib
import re
import stat
import sys
import time
from collections.abc import Callable
from typing import Any

_HARNESS = str(pathlib.Path(__file__).resolve().parent)
if _HARNESS not in sys.path:
    sys.path.insert(0, _HARNESS)

import run_record  # noqa: E402 - adjacent harness module
from attention_gate import AttentionGateError, _append_jsonl  # noqa: E402

KINDS = frozenset({"lane-turn-ended", "lane-status", "worker-question", "test-result",
                   "escalation-response"})
CLOSING = ("Delivered for the Validator's decision. Delivery is not acceptance or approval, and "
           "gives no one a decision they did not already have.")
LEDGER = "notices.jsonl"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def _parse(raw: bytes) -> list[dict[str, Any]]:
    rows = []
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            rows.append(row)
    return rows


def _notices(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each posted notice, in posting order, with its first ``offset``, ``delivered`` and
    ``undelivered`` records."""
    notices: dict[str, dict[str, Any]] = {}
    for row in rows:
        op, notice = row.get("op"), notices.get(row["id"])
        if op == "post" and notice is None:
            notices[row["id"]] = dict(row)
        elif notice is not None and op in ("offset", "delivered", "undelivered"):
            notice.setdefault(op, row.get("inbox_from") if op == "offset" else row.get("ts"))
    return notices


def _holds(ident: str, op: str) -> Callable[[bytes], bool]:
    """Whether rows hold notice ``ident``'s ``post``, or its ``offset``."""
    def holds(rows: bytes) -> bool:
        notice = _notices(_parse(rows)).get(ident)
        return notice is not None and (op == "post" or op in notice)
    return holds


def _read(root: pathlib.Path, wait_s: float | None = None) -> dict[str, dict[str, Any]]:
    """The notices on record, read under the record's lock (shared): an append another writer
    has not committed, or may still roll back, is never seen. ``wait_s`` bounds that wait."""
    try:
        directory = run_record._inbox_directory(root, "validator")
    except FileNotFoundError:
        return {}
    try:
        fd = os.open(LEDGER, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return {}
    finally:
        os.close(directory)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"notice record is not a regular file: {root / 'validator' / LEDGER}")
        run_record._flock(fd, fcntl.LOCK_SH, wait_s, "notice record", root / "validator" / LEDGER)
        raw = stream.read()
    return _notices(_parse(raw.rpartition(b"\n")[0]))


def _append(root: pathlib.Path, row: dict[str, Any], wait_s: float | None,
            once: Callable[[bytes], bool] | None = None) -> bool:
    if root.is_symlink():
        raise ValueError("run directory is a symlink")
    directory = root / "validator"
    if directory.is_symlink():
        raise ValueError("inbox directory is a symlink")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:  # an existing record is made private too, not only a new one; links are not followed
        fd = os.open(directory / LEDGER, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        pass
    else:
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
    line = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return run_record.append_record(directory / LEDGER, line, wait_s=wait_s, once=once,
                                    since=None if once is None else 0)


def post(root: pathlib.Path, notice_id: str, kind: str, sender: str, text: str, *,
         wait_s: float | None = None) -> bool:
    """Record a notice as owed to the Validator. Returns False when ``notice_id`` was already
    posted: nothing is recorded again. ``wait_s`` bounds the wait on the record's lock."""
    if kind not in KINDS:
        raise ValueError(f"unknown notice kind: {kind}")
    if sender not in run_record.SENDERS:
        raise ValueError("unknown sender")
    if not _ID.fullmatch(notice_id):
        raise ValueError("invalid notice id")
    body = text.strip()
    if not body:
        raise ValueError("empty notice")
    body += "" if body.endswith((".", "!", "?")) else "."
    row = {"op": "post", "id": notice_id, "kind": kind, "sender": sender, "ts": _now(),
           "text": f"FACTORY_{kind.upper().replace('-', '_')} (from {sender}) {body} {CLOSING}"}
    return _append(pathlib.Path(root), row, wait_s, once=_holds(notice_id, "post"))


def status(root: pathlib.Path, notice_id: str) -> str:
    """``owed`` or ``delivered``. A notice never posted raises KeyError."""
    return "delivered" if "delivered" in _read(pathlib.Path(root))[notice_id] else "owed"


class LedgerUnreadable(OSError):
    """The notice record could not be read: what is owed is unknown, never "nothing"."""


def deliver(root: pathlib.Path, *, wait_s: float) -> list[str]:
    """Write every owed notice to the Validator's inbox, oldest first, and return the ids still
    owed. Every lock is waited on for what is left of ``wait_s`` in all, and no notice is
    started once it has run out, even when the locks are free. A locked or unwritable inbox
    raises nothing: its notice, and every later one, stays owed for the next call. An unreadable
    notice record raises ``LedgerUnreadable``: what is owed is then unknown, not empty."""
    root = pathlib.Path(root)
    deadline = time.monotonic() + max(0.0, wait_s)

    def left() -> float:
        return max(0.0, deadline - time.monotonic())

    try:
        owed = [notice for notice in _read(root, left()).values() if "delivered" not in notice]
    except OSError as failure:
        raise LedgerUnreadable(f"the notice record is unreadable: {failure}") from failure
    error = ""
    for index, notice in enumerate(owed):
        ident = notice["id"]
        if index and left() <= 0:
            error = f"the delivery's {wait_s:g} s ran out"
            break
        try:
            if "offset" not in notice:
                end = run_record.inbox_end(root, "validator", wait_s=left())
                if _append(root, {"op": "offset", "id": ident, "inbox_from": end}, left(),
                           once=_holds(ident, "offset")):
                    notice["offset"] = end
                else:
                    notice["offset"] = _read(root, left())[ident]["offset"]
            run_record.message(root, "validator", notice["sender"], notice["text"],
                               wait_s=left(), once_since=int(notice["offset"]))
            stamp = _now()
            _append(root, {"op": "delivered", "id": ident, "ts": stamp}, left())
            notice["delivered"] = stamp
        except (OSError, ValueError, KeyError) as failure:
            error = str(failure) or type(failure).__name__
            break
    still = [notice for notice in owed if "delivered" not in notice]
    for notice in still:
        if "undelivered" in notice:
            continue
        try:
            # Decided under the record's lock, before the wake: two deliverers, or a retry after
            # a crash, wake the operator at most once (a crash in between loses that one wake;
            # the notice itself stays owed and is retried).
            if not _append(root, {"op": "undelivered", "id": notice["id"], "ts": _now()},
                           left(), once=_holds(notice["id"], "undelivered")):
                continue
            _append_jsonl(root / "events.jsonl", {
                "ts": _now(), "kind": "validator-input-undelivered", "class": "validator-input",
                "source": "validator_input.py", "wake": True,
                "detail": (f"{notice['id']} ({notice['kind']} from {notice['sender']}) is still "
                           f"owed to the Validator: {error or 'a notice before it is owed'}; "
                           "the next delivery retries it")[:500],
            }, wait_s=left())
        except (OSError, ValueError, AttentionGateError):
            break
    return [notice["id"] for notice in still]
