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
import tempfile

_THREAD = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_MAX_PROMPT = 4 * 1024 * 1024


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


def _write_thread(path: pathlib.Path, thread_id: str) -> None:
    if path.exists():
        current = path.read_text(encoding="utf-8").strip()
        if current != thread_id:
            raise SessionError("Codex event stream changed the retained thread id")
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    )
    try:
        temporary.write(thread_id + "\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary.close()
        os.chmod(temporary.name, 0o600)
        os.link(temporary.name, path)
        os.unlink(temporary.name)
    except BaseException:
        temporary.close()
        if os.path.exists(temporary.name):
            os.unlink(temporary.name)
        raise


def run_turn(
    prompt_path: pathlib.Path,
    thread_path: pathlib.Path,
    event_path: pathlib.Path,
    command: list[str],
) -> int:
    prompt = _read_regular(prompt_path)
    if not command or command[0] == "--":
        command = command[1:]
    if not command:
        raise SessionError("Codex command is empty")
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
        return_code = process.wait()
        os.fsync(event_descriptor)
    finally:
        os.close(event_descriptor)
    if not thread_path.exists():
        raise SessionError("Codex emitted no retained thread id")
    return return_code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", type=pathlib.Path, required=True)
    parser.add_argument("--thread-file", type=pathlib.Path, required=True)
    parser.add_argument("--events", type=pathlib.Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    try:
        return run_turn(
            arguments.prompt,
            arguments.thread_file,
            arguments.events,
            arguments.command,
        )
    except (OSError, SessionError) as exc:
        print(f"codex-lane-session refused: {exc}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
