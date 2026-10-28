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

from factory_core.engagement import (  # noqa: E402 - core decision, imported by path below
    INTERACTIVE,
    EngagementError,
    normalize,
)


class LaneDialogueError(RuntimeError):
    """A dialogue operation could not prove its closed contract."""


_SCHEMA = "factory-lane-dialogue/1"
# A lane is a role with an optional parallel instance (tester-b), as tmux_lane.sh names its
# window; questions and answers bind to that full identity (dogfood #39).
_LANE = re.compile(r"^(?:coder|tester)(?:-[a-z0-9][a-z0-9-]{0,15})?$")


def _is_lane(lane: object) -> bool:
    return isinstance(lane, str) and _LANE.fullmatch(lane) is not None


def _lane_argument(value: str) -> str:
    if not _is_lane(value):
        raise argparse.ArgumentTypeError(f"invalid lane {value!r}: coder or tester[-instance]")
    return value

# The watchdog is a sender only of the generated wrap-up notice (lane_watchdog.py), never of
# prose, an answer or a status probe.
_SENDERS = frozenset({"validator", "orchestrator", "watchdog"})
_AUTHORITIES = frozenset({"human-answer", "ratified-spec", "runtime-protocol"})
_THREAD = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_MAX_JOURNAL_BYTES = 8 * 1024 * 1024
MAX_TEXT_BYTES = 16 * 1024  # the ceiling on one question, answer or status text


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text(value: object, field: str, maximum: int = MAX_TEXT_BYTES) -> str:
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
    corrected: set[str] = set()
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
            if set(row) != expected or not _is_lane(row.get("lane")):
                raise LaneDialogueError("dialogue question row is malformed")
            value = _text(row.get("text"), "question")
            identifier = row.get("question_id")
            if not isinstance(identifier, str) or identifier in questions:
                raise LaneDialogueError("dialogue question id is invalid or repeated")
            if row.get("text_sha256") != _digest(value):
                raise LaneDialogueError("dialogue question digest is invalid")
            questions[identifier] = row
        elif record_type == "question-corrected":
            expected = {"schema_version", "sequence", "ts", "record_type", "question_id",
                        "question_digest", "reason", "source", "source_digest", "event_line",
                        "event_digest", "assessment_digest"}
            identifier = str(row.get("question_id"))
            question = questions.get(identifier)
            if (set(row) != expected or question is None or identifier in corrected
                    or identifier in planned_answers
                    or row.get("reason") != "quoted-tool-placeholder"
                    or row.get("question_digest") != question["text_sha256"]
                    or not _placeholder_echo(str(question["text"]))
                    or not _source_name(row.get("source"), str(question["lane"]))
                    or type(row.get("event_line")) is not int or int(str(row["event_line"])) < 1
                    or any(not isinstance(row.get(k), str)
                           or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(row[k]))
                           for k in ("source_digest", "event_digest", "assessment_digest"))):
                raise LaneDialogueError("invalid question correction receipt")
            corrected.add(identifier)
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
            if sender not in _SENDERS or not _is_lane(lane) or authority not in _AUTHORITIES:
                raise LaneDialogueError("dialogue planned-message principal is invalid")
            if kind == "status-probe":
                if (row.get("question_id") is not None or authority != "runtime-protocol"
                        or sender == "watchdog"):
                    raise LaneDialogueError("status probe carries invalid authority")
            elif kind == "progress-review":
                if (row.get("question_id") is not None or sender != "validator"
                        or authority != "ratified-spec"):
                    raise LaneDialogueError("only the Validator may plan a progress review")
            elif kind == "wrap-up":
                if (row.get("question_id") is not None or sender != "watchdog"
                        or authority != "runtime-protocol"):
                    raise LaneDialogueError("only the watchdog may plan a wrap-up notice")
            elif kind == "spec-answer":
                question_id = row.get("question_id")
                if sender != "validator" or authority not in {"human-answer", "ratified-spec"}:
                    raise LaneDialogueError("only the Validator may plan a specification answer")
                if (question_id not in questions or str(question_id) in corrected
                        or questions[str(question_id)]["lane"] != lane):
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


def _corrected_question_ids(rows: Sequence[Mapping[str, object]]) -> set[str]:
    return {str(row["question_id"]) for row in rows
            if row.get("record_type") == "question-corrected"}


def _placeholder_echo(text: str) -> bool:
    # Deliberately narrow: ordinary questions, even questions mentioning a placeholder,
    # cannot be administratively dismissed with this operation.
    return text.startswith(("<question>` instead of guessing.",
                            "<one concrete question>` instead of guessing."))


def _source_name(value: object, lane: str) -> bool:
    return isinstance(value, str) and re.fullmatch(
        re.escape(lane) + r"\.[A-Za-z0-9][A-Za-z0-9_-]{0,39}-codex-events\.jsonl", value
    ) is not None


def _tool_echo_evidence(root: pathlib.Path, source: str, question: Mapping[str, Any]) -> dict:
    """Verify the exact legacy pane match came from a completed tool event, not an author.

    Read only the named run-local stream, never a provider's private session store. Keep
    hashes/locations in the receipt, not arbitrary tool output or command payloads.
    """
    if not _source_name(source, str(question["lane"])):
        raise LaneDialogueError("source must name this lane's retained event stream")
    directory = root / "tmux-lanes"
    if directory.is_symlink():
        raise LaneDialogueError("event directory may not be a symlink")
    path = directory / source
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 0 < before.st_size <= 16 * 1024 * 1024):
            raise LaneDialogueError("event stream must be a bounded regular file")
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            body = stream.read(16 * 1024 * 1024 + 1)
            after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise LaneDialogueError("event stream changed during correction review")
    finally:
        if fd >= 0:
            os.close(fd)
    if not body.endswith(b"\n"):
        raise LaneDialogueError("event stream is incomplete")
    legacy = re.compile(r"\bFACTORY_QUESTION:\s*(\S.*)$")
    matches = []
    try:
        lines = body.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise LaneDialogueError("event stream is not UTF-8") from error
    for number, raw in enumerate(lines, 1):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError as error:
            raise LaneDialogueError("event stream contains invalid JSON") from error
        if not isinstance(event, dict):
            raise LaneDialogueError("event stream contains a non-object")
        item = event.get("item", {})
        if not isinstance(item, dict):
            raise LaneDialogueError("event item is malformed")
        if item.get("type") == "agent_message" and "FACTORY_QUESTION:" in str(item.get("text")):
            # An actual author asking about the same placeholder makes attribution
            # ambiguous. Retain the question; this is not a general dismissal command.
            if any(token in str(item.get("text"))
                   for token in ("<question>", "<one concrete question>")):
                raise LaneDialogueError("author message makes placeholder attribution ambiguous")
        match = legacy.search(raw)
        if match and match.group(1).strip() == question["text"]:
            output = item.get("aggregated_output")
            if (event.get("type") != "item.completed"
                    or item.get("type") != "command_execution"
                    or not isinstance(output, str)
                    or not any("FACTORY_QUESTION: " + token in output
                               for token in ("<question>", "<one concrete question>"))):
                raise LaneDialogueError("matching event is not a quoted tool placeholder")
            matches.append((number, _digest(raw)))
    if len(matches) != 1:
        raise LaneDialogueError("correction requires one exact retained tool event")
    return {"source": source, "source_digest": "sha256:" + hashlib.sha256(body).hexdigest(),
            "event_line": matches[0][0], "event_digest": matches[0][1]}


def correct_tool_echo(root: pathlib.Path, question_id: str, source: str,
                      assessment_digest: str) -> dict[str, Any]:
    """Host-only append-only classification repair; never answer, deliver, resume or grant.

    The current accepted AG review must have observed this pending question. A later
    independent assessment and all normal process/attention gates still govern rebind.
    """
    import orchestrator_channel

    root = pathlib.Path(root)
    orchestrator_channel.require_current(root)
    reports = orchestrator_channel._read_jsonl(root / "orchestrator/reports.jsonl")
    review = reports[-1] if reports else {}
    if review.get("assessment_digest") != assessment_digest:
        raise LaneDialogueError("correction requires the current accepted assessment")
    with _locked(root) as directory:
        path = directory / "journal.jsonl"
        rows = _read(path)
        _validate(rows)
        question = next((r for r in rows if r.get("record_type") == "question"
                         and r.get("question_id") == question_id), None)
        if question is None or not _placeholder_echo(str(question["text"])):
            raise LaneDialogueError("question is not an eligible quoted placeholder")
        if any(r.get("record_type") == "message-planned" and r.get("question_id") == question_id
               for r in rows):
            raise LaneDialogueError("a planned answer prevents classification correction")
        evidence = _tool_echo_evidence(root, source, question)
        existing = next((r for r in rows if r.get("record_type") == "question-corrected"
                         and r.get("question_id") == question_id), None)
        if existing:
            if any(existing[k] != value for k, value in evidence.items()):
                raise LaneDialogueError("retained correction evidence changed")
            return existing
        if question["text"] not in review.get("assessment", {}).get("specification_questions", []):
            raise LaneDialogueError("assessment has not reviewed this pending question")
        row = {"schema_version": _SCHEMA, "sequence": len(rows) + 1, "ts": _now(),
               "record_type": "question-corrected", "question_id": question_id,
               "question_digest": question["text_sha256"], "reason": "quoted-tool-placeholder",
               **evidence, "assessment_digest": assessment_digest}
        _validate([*rows, row])
        _append(path, row)
        return row


def record_question(root: pathlib.Path, lane: str, question: str) -> tuple[dict[str, Any], bool]:
    if not _is_lane(lane):
        raise LaneDialogueError("question lane must be coder or tester[-instance]")
    question = _text(question, "question")
    with _locked(pathlib.Path(root)) as directory:
        path = directory / "journal.jsonl"
        rows = _read(path)
        _validate(rows)
        answered = _answered_question_ids(rows) | _corrected_question_ids(rows)
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
        if sender not in _SENDERS or not _is_lane(lane):
            raise LaneDialogueError("message sender or lane is invalid")
        if message_kind == "status-probe":
            if question_id is not None or authority != "runtime-protocol" or sender == "watchdog":
                raise LaneDialogueError("status probes use only runtime-protocol authority")
        elif message_kind == "progress-review":
            # The Validator's review of a paused or finished lane, judged against the ratified
            # specification; it binds no question and passes the coder leak guard upstream.
            if question_id is not None or sender != "validator" or authority != "ratified-spec":
                raise LaneDialogueError("only the Validator may send a progress review")
        elif message_kind == "wrap-up":
            if question_id is not None or sender != "watchdog" or authority != "runtime-protocol":
                raise LaneDialogueError("only the watchdog may send a wrap-up notice")
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
            if str(question_id) in _corrected_question_ids(rows):
                raise LaneDialogueError("question was corrected as a tool echo")
        else:
            raise LaneDialogueError(
                "message kind must be status-probe, spec-answer, progress-review or wrap-up"
            )
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


def run_engagement(root: pathlib.Path) -> str:
    """The engagement this run was ignited with.

    A run record without one is not configured, so the strictest level applies:
    an unconfigured run behaves interactively rather than quietly running dark.
    """

    try:
        metadata = json.loads((pathlib.Path(root) / "harness.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return INTERACTIVE
    declared = metadata.get("engagement") if isinstance(metadata, dict) else None
    if not isinstance(declared, str):
        return INTERACTIVE
    try:
        return normalize(declared)
    except EngagementError:
        return INTERACTIVE


def pending_questions(root: pathlib.Path, lane: str | None = None) -> list[dict[str, Any]]:
    if lane is not None and not _is_lane(lane):
        raise LaneDialogueError("pending-question lane must be coder or tester[-instance]")
    with _locked(pathlib.Path(root)) as directory:
        rows = _read(directory / "journal.jsonl")
        _validate(rows)
    answered = _answered_question_ids(rows) | _corrected_question_ids(rows)
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
    if not raw or len(raw) > MAX_TEXT_BYTES:
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
    question.add_argument("--lane", type=_lane_argument, required=True)
    question.add_argument("--text", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--root", type=pathlib.Path, required=True)
    plan.add_argument("--sender", choices=sorted(_SENDERS), required=True)
    plan.add_argument("--lane", type=_lane_argument, required=True)
    plan.add_argument(
        "--kind", choices=("status-probe", "spec-answer", "progress-review", "wrap-up"),
        required=True,
    )
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
    pending.add_argument("--lane", type=_lane_argument)
    clear = commands.add_parser("require-clear")
    clear.add_argument("--root", type=pathlib.Path, required=True)
    clear.add_argument("--lane", type=_lane_argument)
    correction = commands.add_parser("correct-tool-echo",
                                    help="host-only audited placeholder repair")
    correction.add_argument("--root", type=pathlib.Path, required=True)
    correction.add_argument("--question-id", required=True)
    correction.add_argument("--source", required=True, help="retained lane event filename")
    correction.add_argument("--assessment-digest", required=True)
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
        elif arguments.command == "correct-tool-echo":
            print(json.dumps(correct_tool_echo(arguments.root, arguments.question_id,
                                              arguments.source, arguments.assessment_digest)))
        else:
            outstanding = pending_questions(arguments.root, arguments.lane)
            if outstanding:
                # A question never stops the run (the impasse rule): it is carried
                # into the record the human reads afterwards, and named here so the
                # transition is not silently unaware of it.
                identifiers = ",".join(str(row["question_id"]) for row in outstanding)
                print(
                    "lane-dialogue: carrying unanswered lane questions into the record "
                    f"({identifiers}); a question never blocks",
                    file=sys.stderr,
                )
            print(
                json.dumps(
                    {"clear": True, "carried": [row["question_id"] for row in outstanding]},
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
    except (LaneDialogueError, OSError) as exc:
        print(f"lane-dialogue refused: {exc}", file=sys.stderr)
        return 70
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
