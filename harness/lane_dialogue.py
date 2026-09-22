#!/usr/bin/env python3
"""Typed, append-only Q&A and status channel for tmux Codex lanes.

The channel preserves Coder/Tester separation: a lane can publish only its own
question, the Orchestrator can send only a generated status probe, and only the
Validator can bind a specification answer to one pending question.  Delivery is
recorded separately from intent so a failed queue/resume is never called sent.
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
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any


class LaneDialogueError(RuntimeError):
    """A dialogue operation could not prove its closed contract."""


_SCHEMA = "factory-lane-dialogue/1"
_LANES = frozenset({"coder", "tester"})
_SENDERS = frozenset({"validator", "orchestrator"})
_AUTHORITIES = frozenset({"human-answer", "ratified-spec", "runtime-protocol"})
_THREAD = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_MAX_JOURNAL_BYTES = 8 * 1024 * 1024
_MAX_TEXT_BYTES = 16 * 1024


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text(value: object, field: str, maximum: int = _MAX_TEXT_BYTES) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LaneDialogueError(f"{field} must be non-empty text")
    if len(value.encode("utf-8")) > maximum:
        raise LaneDialogueError(f"{field} exceeds its byte ceiling")
    return value


def _open_directory(path: pathlib.Path) -> int:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise LaneDialogueError(f"not a real directory: {path}")
    return descriptor


@contextmanager
def _locked(root: pathlib.Path) -> Iterator[pathlib.Path]:
    if root.is_symlink() or not root.is_dir():
        raise LaneDialogueError("run root is not a real directory")
    directory = root / "dialogue"
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    directory_fd = _open_directory(directory)
    try:
        os.fchmod(directory_fd, 0o700)
    finally:
        os.close(directory_fd)
    lock = os.open(
        directory / ".lock",
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(lock).st_mode):
            raise LaneDialogueError("dialogue lock is not a regular file")
        os.fchmod(lock, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield directory
    finally:
        os.close(lock)


def _read(path: pathlib.Path) -> list[dict[str, Any]]:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
    except FileNotFoundError:
        return []
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_JOURNAL_BYTES:
            raise LaneDialogueError("dialogue journal is not a bounded regular file")
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            descriptor = -1
            rows = []
            for number, line in enumerate(stream, 1):
                if not line.endswith("\n"):
                    raise LaneDialogueError(f"dialogue journal row {number} is incomplete")
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise LaneDialogueError(f"dialogue journal row {number} is not JSON") from exc
                if not isinstance(value, dict):
                    raise LaneDialogueError(f"dialogue journal row {number} is not an object")
                rows.append(value)
        installed = os.lstat(path)
        if stat.S_ISLNK(installed.st_mode) or (
            installed.st_dev,
            installed.st_ino,
        ) != (before.st_dev, before.st_ino):
            raise LaneDialogueError("dialogue journal changed while read")
        return rows
    except UnicodeDecodeError as exc:
        raise LaneDialogueError("dialogue journal is not UTF-8") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _append(path: pathlib.Path, row: Mapping[str, object]) -> None:
    payload = (json.dumps(dict(row), sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise LaneDialogueError("dialogue journal is not a regular file")
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(payload):
            count = os.write(descriptor, payload[offset:])
            if count < 1:
                raise LaneDialogueError("dialogue append made no progress")
            offset += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    parent = _open_directory(path.parent)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def _validate(rows: Sequence[Mapping[str, object]]) -> None:
    questions: dict[str, Mapping[str, object]] = {}
    planned: dict[str, Mapping[str, object]] = {}
    planned_answers: dict[str, str] = {}
    delivered: set[str] = set()
    for sequence, row in enumerate(rows, 1):
        if row.get("schema_version") != _SCHEMA or row.get("sequence") != sequence:
            raise LaneDialogueError("dialogue journal sequence or schema is invalid")
        record_type = row.get("record_type")
        if record_type == "question":
            expected = {
                "schema_version",
                "sequence",
                "ts",
                "record_type",
                "question_id",
                "lane",
                "text",
                "text_sha256",
            }
            if set(row) != expected or row.get("lane") not in _LANES:
                raise LaneDialogueError("dialogue question row is malformed")
            value = _text(row.get("text"), "question")
            identifier = row.get("question_id")
            if not isinstance(identifier, str) or identifier in questions:
                raise LaneDialogueError("dialogue question id is invalid or repeated")
            if row.get("text_sha256") != _digest(value):
                raise LaneDialogueError("dialogue question digest is invalid")
            questions[identifier] = row
        elif record_type == "message-planned":
            expected = {
                "schema_version",
                "sequence",
                "ts",
                "record_type",
                "message_id",
                "sender",
                "lane",
                "message_kind",
                "question_id",
                "authority",
                "basis",
                "text",
                "text_sha256",
            }
            if set(row) != expected:
                raise LaneDialogueError("dialogue planned-message row is malformed")
            sender = row.get("sender")
            lane = row.get("lane")
            kind = row.get("message_kind")
            authority = row.get("authority")
            if sender not in _SENDERS or lane not in _LANES or authority not in _AUTHORITIES:
                raise LaneDialogueError("dialogue planned-message principal is invalid")
            if kind == "status-probe":
                if row.get("question_id") is not None or authority != "runtime-protocol":
                    raise LaneDialogueError("status probe carries invalid authority")
            elif kind == "spec-answer":
                question_id = row.get("question_id")
                if sender != "validator" or authority not in {"human-answer", "ratified-spec"}:
                    raise LaneDialogueError("only the Validator may plan a specification answer")
                if question_id not in questions or questions[str(question_id)]["lane"] != lane:
                    raise LaneDialogueError("specification answer does not bind a lane question")
                if str(question_id) in planned_answers:
                    raise LaneDialogueError(
                        "a lane question may have only one planned specification answer"
                    )
            else:
                raise LaneDialogueError("dialogue message kind is invalid")
            value = _text(row.get("text"), "message")
            _text(row.get("basis"), "basis", maximum=4096)
            identifier = row.get("message_id")
            if not isinstance(identifier, str) or identifier in planned:
                raise LaneDialogueError("dialogue message id is invalid or repeated")
            if row.get("text_sha256") != _digest(value):
                raise LaneDialogueError("dialogue message digest is invalid")
            planned[identifier] = row
            if kind == "spec-answer":
                planned_answers[str(row["question_id"])] = str(identifier)
        elif record_type == "message-delivered":
            expected = {
                "schema_version",
                "sequence",
                "ts",
                "record_type",
                "message_id",
                "thread_id",
                "transport",
            }
            identifier = row.get("message_id")
            if (
                set(row) != expected
                or identifier not in planned
                or identifier in delivered
                or row.get("transport") not in {"queue", "resume"}
                or not isinstance(row.get("thread_id"), str)
                or not _THREAD.fullmatch(str(row["thread_id"]))
            ):
                raise LaneDialogueError("dialogue delivery row is malformed")
            delivered.add(str(identifier))
        else:
            raise LaneDialogueError("dialogue record type is invalid")


def _answered_question_ids(rows: Sequence[Mapping[str, object]]) -> set[str]:
    delivered = {
        str(row["message_id"]) for row in rows if row.get("record_type") == "message-delivered"
    }
    return {
        str(row["question_id"])
        for row in rows
        if row.get("record_type") == "message-planned"
        and row.get("message_kind") == "spec-answer"
        and row.get("message_id") in delivered
    }


def record_question(root: pathlib.Path, lane: str, question: str) -> tuple[dict[str, Any], bool]:
    if lane not in _LANES:
        raise LaneDialogueError("question lane must be coder or tester")
    question = _text(question, "question")
    with _locked(pathlib.Path(root)) as directory:
        path = directory / "journal.jsonl"
        rows = _read(path)
        _validate(rows)
        answered = _answered_question_ids(rows)
        for row in reversed(rows):
            if (
                row.get("record_type") == "question"
                and row.get("lane") == lane
                and row.get("text") == question
                and row.get("question_id") not in answered
            ):
                return dict(row), False
        sequence = len(rows) + 1
        identifier = f"Q-{sequence:04d}-{_digest(question)[7:19]}"
        row = {
            "schema_version": _SCHEMA,
            "sequence": sequence,
            "ts": _now(),
            "record_type": "question",
            "question_id": identifier,
            "lane": lane,
            "text": question,
            "text_sha256": _digest(question),
        }
        _append(path, row)
        return row, True


def plan_message(
    root: pathlib.Path,
    *,
    sender: str,
    lane: str,
    message_kind: str,
    text: str,
    basis: str,
    authority: str,
    question_id: str | None = None,
) -> dict[str, Any]:
    text = _text(text, "message")
    basis = _text(basis, "basis", maximum=4096)
    with _locked(pathlib.Path(root)) as directory:
        path = directory / "journal.jsonl"
        rows = _read(path)
        _validate(rows)
        if sender not in _SENDERS or lane not in _LANES:
            raise LaneDialogueError("message sender or lane is invalid")
        if message_kind == "status-probe":
            if question_id is not None or authority != "runtime-protocol":
                raise LaneDialogueError("status probes use only runtime-protocol authority")
        elif message_kind == "spec-answer":
            if sender != "validator" or authority not in {"human-answer", "ratified-spec"}:
                raise LaneDialogueError("only the Validator may answer a specification question")
            questions = {
                str(row["question_id"]): row for row in rows if row.get("record_type") == "question"
            }
            if question_id not in questions or questions[str(question_id)]["lane"] != lane:
                raise LaneDialogueError("answer does not bind an existing question for this lane")
            if str(question_id) in _answered_question_ids(rows):
                raise LaneDialogueError("question already has a delivered answer")
        else:
            raise LaneDialogueError("message kind must be status-probe or spec-answer")
        delivered = {
            str(row["message_id"]) for row in rows if row.get("record_type") == "message-delivered"
        }
        for row in reversed(rows):
            if (
                row.get("record_type") == "message-planned"
                and row.get("sender") == sender
                and row.get("lane") == lane
                and row.get("message_kind") == message_kind
                and row.get("question_id") == question_id
                and row.get("text") == text
                and row.get("basis") == basis
                and row.get("authority") == authority
                and row.get("message_id") not in delivered
            ):
                return dict(row)
        sequence = len(rows) + 1
        seed = "\0".join((sender, lane, message_kind, question_id or "", text, basis))
        identifier = f"M-{sequence:04d}-{_digest(seed)[7:19]}"
        row = {
            "schema_version": _SCHEMA,
            "sequence": sequence,
            "ts": _now(),
            "record_type": "message-planned",
            "message_id": identifier,
            "sender": sender,
            "lane": lane,
            "message_kind": message_kind,
            "question_id": question_id,
            "authority": authority,
            "basis": basis,
            "text": text,
            "text_sha256": _digest(text),
        }
        candidate = [*rows, row]
        _validate(candidate)
        _append(path, row)
        return row


def record_delivery(
    root: pathlib.Path,
    *,
    message_id: str,
    thread_id: str,
    transport: str,
) -> dict[str, Any]:
    with _locked(pathlib.Path(root)) as directory:
        path = directory / "journal.jsonl"
        rows = _read(path)
        _validate(rows)
        existing = [
            row
            for row in rows
            if row.get("record_type") == "message-delivered" and row.get("message_id") == message_id
        ]
        if existing:
            row = dict(existing[0])
            if row.get("thread_id") != thread_id or row.get("transport") != transport:
                raise LaneDialogueError("message already has a conflicting delivery")
            return row
        row = {
            "schema_version": _SCHEMA,
            "sequence": len(rows) + 1,
            "ts": _now(),
            "record_type": "message-delivered",
            "message_id": message_id,
            "thread_id": thread_id,
            "transport": transport,
        }
        _validate([*rows, row])
        _append(path, row)
        return row


def pending_questions(root: pathlib.Path, lane: str | None = None) -> list[dict[str, Any]]:
    if lane is not None and lane not in _LANES:
        raise LaneDialogueError("pending-question lane must be coder or tester")
    with _locked(pathlib.Path(root)) as directory:
        rows = _read(directory / "journal.jsonl")
        _validate(rows)
    answered = _answered_question_ids(rows)
    return [
        dict(row)
        for row in rows
        if row.get("record_type") == "question"
        and row.get("question_id") not in answered
        and (lane is None or row.get("lane") == lane)
    ]


def _read_message_file(path: pathlib.Path) -> str:
    if path.is_symlink():
        raise LaneDialogueError("message input may not be a symlink")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise LaneDialogueError(f"message input cannot be read: {exc}") from exc
    if not raw or len(raw) > _MAX_TEXT_BYTES:
        raise LaneDialogueError("message input is empty or oversized")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LaneDialogueError("message input is not UTF-8") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    question = commands.add_parser("question")
    question.add_argument("--root", type=pathlib.Path, required=True)
    question.add_argument("--lane", choices=sorted(_LANES), required=True)
    question.add_argument("--text", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--root", type=pathlib.Path, required=True)
    plan.add_argument("--sender", choices=sorted(_SENDERS), required=True)
    plan.add_argument("--lane", choices=sorted(_LANES), required=True)
    plan.add_argument("--kind", choices=("status-probe", "spec-answer"), required=True)
    plan.add_argument("--message-file", type=pathlib.Path, required=True)
    plan.add_argument("--basis", required=True)
    plan.add_argument("--authority", choices=sorted(_AUTHORITIES), required=True)
    plan.add_argument("--question-id")
    delivered = commands.add_parser("delivered")
    delivered.add_argument("--root", type=pathlib.Path, required=True)
    delivered.add_argument("--message-id", required=True)
    delivered.add_argument("--thread-id", required=True)
    delivered.add_argument("--transport", choices=("queue", "resume"), required=True)
    pending = commands.add_parser("pending")
    pending.add_argument("--root", type=pathlib.Path, required=True)
    pending.add_argument("--lane", choices=sorted(_LANES))
    clear = commands.add_parser("require-clear")
    clear.add_argument("--root", type=pathlib.Path, required=True)
    clear.add_argument("--lane", choices=sorted(_LANES))
    arguments = parser.parse_args()
    try:
        if arguments.command == "question":
            row, created = record_question(arguments.root, arguments.lane, arguments.text)
            print(json.dumps({"created": created, **row}, sort_keys=True, separators=(",", ":")))
        elif arguments.command == "plan":
            row = plan_message(
                arguments.root,
                sender=arguments.sender,
                lane=arguments.lane,
                message_kind=arguments.kind,
                text=_read_message_file(arguments.message_file),
                basis=arguments.basis,
                authority=arguments.authority,
                question_id=arguments.question_id,
            )
            print(json.dumps(row, sort_keys=True, separators=(",", ":")))
        elif arguments.command == "delivered":
            row = record_delivery(
                arguments.root,
                message_id=arguments.message_id,
                thread_id=arguments.thread_id,
                transport=arguments.transport,
            )
            print(json.dumps(row, sort_keys=True, separators=(",", ":")))
        elif arguments.command == "pending":
            print(
                json.dumps(
                    pending_questions(arguments.root, arguments.lane),
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        else:
            outstanding = pending_questions(arguments.root, arguments.lane)
            if outstanding:
                identifiers = ",".join(str(row["question_id"]) for row in outstanding)
                raise LaneDialogueError(
                    f"unanswered lane questions block this transition: {identifiers}"
                )
            print(json.dumps({"clear": True}, sort_keys=True, separators=(",", ":")))
    except (LaneDialogueError, OSError) as exc:
        print(f"lane-dialogue refused: {exc}", file=sys.stderr)
        return 70
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
