#!/usr/bin/env python3
"""Run one Codex lane turn, retain its event stream, and bind its thread id."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import stat
import subprocess
import sys

_HARNESS_ROOT = str(pathlib.Path(__file__).resolve().parent)
if _HARNESS_ROOT not in sys.path:
    sys.path.insert(0, _HARNESS_ROOT)

import codex_hook_admission  # noqa: E402
import validator_boundary  # noqa: E402
from lane_dialogue import (  # noqa: E402
    MAX_TEXT_BYTES,
    LaneDialogueError,
    record_question,
)

_THREAD = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_MAX_PROMPT = 4 * 1024 * 1024
_FACTORY_QUESTION = re.compile(r"[ \t]*FACTORY_QUESTION:[ \t]*(.*)")
# A question longer than the dialogue's text ceiling is recorded truncated, with this visible
# marker, rather than refused: a refusal there would kill the lane mid-turn over prose length.
_TRUNCATED = "\n[truncated by codex_lane_session: question exceeded the dialogue size limit]"


class SessionError(RuntimeError):
    """The lane session wrapper could not retain a trustworthy session handle."""


def _read_regular(path: pathlib.Path) -> bytes:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= _MAX_PROMPT:
            raise SessionError("prompt is not a bounded non-empty regular file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise SessionError("prompt changed while read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _thread_id(value: object) -> str | None:
    """Accept only the event envelope's session handle, never model-emitted nested JSON."""

    if not isinstance(value, dict):
        return None
    for key in ("thread_id", "session_id"):
        candidate = value.get(key)
        if isinstance(candidate, str) and _THREAD.fullmatch(candidate):
            return candidate
    return None


def _agent_message(value: object) -> str | None:
    """Return only a completed assistant message, never command or tool output."""

    if not isinstance(value, dict) or value.get("type") != "item.completed":
        return None
    item = value.get("item")
    if not isinstance(item, dict) or item.get("type") != "agent_message":
        return None
    text = item.get("text")
    return text if isinstance(text, str) else None


def _questions(message: str) -> list[str]:
    """Decide the typed questions in one assistant message.

    Each ``FACTORY_QUESTION:`` line opens one question. Its body continues on the following
    lines until the next marker line or a blank line, so a multi-line question survives and
    two questions stay two records (answering one never closes the other).
    """

    questions: list[str] = []
    body: list[str] | None = None
    for line in message.splitlines():
        marker = _FACTORY_QUESTION.fullmatch(line)
        if marker is not None:
            if body is not None:
                questions.append("\n".join(body).strip())
            body = [marker.group(1)]
        elif body is not None and line.strip():
            body.append(line)
        elif body is not None:
            questions.append("\n".join(body).strip())
            body = None
    if body is not None:
        questions.append("\n".join(body).strip())
    return [_bounded(question) for question in questions if question]


def _bounded(question: str) -> str:
    """Fit a question inside the dialogue ceiling, marking any truncation visibly."""

    raw = question.encode("utf-8")
    if len(raw) <= MAX_TEXT_BYTES:
        return question
    room = MAX_TEXT_BYTES - len(_TRUNCATED.encode("utf-8"))
    return raw[:room].decode("utf-8", errors="ignore").rstrip() + _TRUNCATED


def _retain_questions(root: pathlib.Path, role: str, message: str) -> None:
    for question in _questions(message):
        record_question(root, role, question)


def _read_thread(path: pathlib.Path) -> str:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
    except OSError as exc:
        raise SessionError("retained thread file cannot be opened safely") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 128:
            raise SessionError("retained thread file is not a bounded regular file")
        raw = os.read(descriptor, 129)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise SessionError("retained thread file changed while read")
    finally:
        os.close(descriptor)
    try:
        value = raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise SessionError("retained thread file is not UTF-8") from exc
    if not _THREAD.fullmatch(value):
        raise SessionError("retained thread id is malformed")
    return value


def _write_thread(path: pathlib.Path, thread_id: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = (thread_id + "\n").encode("utf-8")
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError as exc:
        if _read_thread(path) != thread_id:
            raise SessionError("Codex event stream changed the retained thread id") from exc
        return
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def run_turn(
    prompt_path: pathlib.Path,
    thread_path: pathlib.Path,
    event_path: pathlib.Path,
    command: list[str],
    *,
    root: pathlib.Path | None = None,
    role: str | None = None,
    boundary_record: object = None,
) -> int:
    if (root is None) != (role is None):
        raise SessionError("run root and role must be supplied together")
    lane_id = r"(?:coder|tester)(?:-[a-z0-9][a-z0-9-]{0,15})?"
    if role is not None and not re.fullmatch(lane_id, role):
        raise SessionError("lane must be coder or tester[-instance]")
    prompt = _read_regular(prompt_path)
    if not command or command[0] == "--":
        command = command[1:]
    if not command:
        raise SessionError("Codex command is empty")
    try:
        command = codex_hook_admission.preserve_trust_with_scoped_mcp(command, pathlib.Path.cwd())
        codex_hook_admission.check_command(command, pathlib.Path.cwd())
    except codex_hook_admission.HookAdmissionError as exc:
        raise SessionError(str(exc)) from exc
    command = validator_boundary.model_command(
        command, boundary_record, (role or "").split("-", 1)[0], pathlib.Path.cwd())
    event_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    event_descriptor = os.open(
        event_path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(event_descriptor).st_mode):
            raise SessionError("Codex event sink is not a regular file")
        os.fchmod(event_descriptor, 0o600)
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(prompt)
        process.stdin.close()
        for raw in process.stdout:
            offset = 0
            while offset < len(raw):
                count = os.write(event_descriptor, raw[offset:])
                if count < 1:
                    process.kill()
                    raise SessionError("Codex event append made no progress")
                offset += count
            sys.stdout.buffer.write(raw)
            sys.stdout.buffer.flush()
            try:
                value = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if thread_id := _thread_id(value):
                _write_thread(thread_path, thread_id)
            if root is not None and role is not None and (message := _agent_message(value)):
                try:
                    _retain_questions(root, role, message)
                except (LaneDialogueError, OSError) as exc:
                    process.kill()
                    raise SessionError(f"typed lane question could not be retained: {exc}") from exc
        return_code = process.wait()
        os.fsync(event_descriptor)
    finally:
        os.close(event_descriptor)
    try:
        _read_thread(thread_path)
    except SessionError as exc:
        if not thread_path.exists() and not thread_path.is_symlink():
            raise SessionError("Codex emitted no retained thread id") from exc
        raise
    return return_code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", type=pathlib.Path, required=True)
    parser.add_argument("--thread-file", type=pathlib.Path, required=True)
    parser.add_argument("--events", type=pathlib.Path, required=True)
    parser.add_argument("--root", type=pathlib.Path, required=True)
    # The lane identity (tester-b for an instance), which its questions are recorded under.
    parser.add_argument("--role", required=True)
    parser.add_argument("--boundary-record", type=json.loads, default=None)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    try:
        return run_turn(
            arguments.prompt,
            arguments.thread_file,
            arguments.events,
            arguments.command,
            root=arguments.root,
            role=arguments.role,
            boundary_record=arguments.boundary_record,
        )
    except (OSError, SessionError, validator_boundary.BoundaryError) as exc:
        print(f"codex-lane-session refused: {exc}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
