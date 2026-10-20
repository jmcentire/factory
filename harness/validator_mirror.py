#!/usr/bin/env python3
"""The validator window when the session that kicked off the run is the Validator.

The founder's default is that the session they started the run in is the Validator: no second
Validator, no relay between them. That session lives outside the run's tmux, so this window
stands in for its terminal, keeping the Orchestrator's view of it and its hold over it:

- Out: it prints the kickoff session's transcript as it is written, so the dispatcher's pane
  capture, its Validator failure-mode checks and the minutes read the Validator's real console.
- In: every line typed into this window (a dispatcher notice through inject.sh, an Orchestrator
  correction through seat_supervisor.py) is appended to the Validator's inbox, which the kickoff
  session watches. Nothing typed here is lost because no agent reads this terminal.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import select
import sys
from collections.abc import Iterable, Iterator
from typing import Any, TextIO

# A short line (a dialog key the dispatcher sends to a stuck pane, a stray Enter) is not a
# message to the Validator.
MIN_MESSAGE_CHARS = 8
MAX_LINE_CHARS = 500


def _clip(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= MAX_LINE_CHARS else flat[: MAX_LINE_CHARS - 3] + "..."


def _summarize(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("command", "description", "file_path", "pattern", "query", "url", "prompt"):
            if isinstance(value.get(key), str):
                return value[key]
    return json.dumps(value, sort_keys=True)


def render(entry: Any) -> list[str]:
    """One transcript row as console lines: what was said, which tools ran, what came back.

    Reads the Claude Code session JSONL shape; a row it does not recognize renders as nothing.
    Reasoning blocks are not shown, as a terminal does not show them.
    """
    if not isinstance(entry, dict) or entry.get("type") not in ("user", "assistant"):
        return []
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    speaker = "founder" if entry["type"] == "user" else "validator"
    content = message.get("content")
    if isinstance(content, str):
        return [f"{speaker}: {_clip(content)}"] if content.strip() else []
    lines: list[str] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text" and str(block.get("text", "")).strip():
            lines.append(f"{speaker}: {_clip(block['text'])}")
        elif kind == "tool_use":
            lines.append(f"tool {block.get('name', '?')}: {_clip(_summarize(block.get('input')))}")
        elif kind == "tool_result":
            result = block.get("content")
            if isinstance(result, list):
                result = " ".join(
                    str(part.get("text", "")) for part in result if isinstance(part, dict)
                )
            lines.append(f"result: {_clip(str(result or ''))}")
    return lines


class Transcript:
    "Incremental reader of a JSONL transcript; a partial last line waits for its newline."

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.offset = 0

    def new_rows(self) -> Iterator[Any]:
        try:
            with self.path.open("rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read()
        except FileNotFoundError:
            return
        end = chunk.rfind(b"\n")
        if end < 0:
            return
        self.offset += end + 1
        for line in chunk[: end + 1].splitlines():
            try:
                yield json.loads(line)
            except ValueError:
                continue


def deliver_to_inbox(inbox: pathlib.Path, text: str) -> bool:
    "Queue one typed line for the kickoff Validator. Returns False when it is not a message."
    text = text.strip()
    if len(text) < MIN_MESSAGE_CHARS:
        return False
    row = {
        "at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "text": text,
    }
    inbox.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(inbox, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, (json.dumps(row, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    return True


def mirror(
    transcript: Transcript,
    inbox: pathlib.Path,
    *,
    stdin: TextIO,
    stdout: TextIO,
    poll_seconds: float,
    backlog_rows: int,
    passes: Iterable[int] | None = None,
) -> None:
    rows = list(transcript.new_rows())
    for row in rows[-backlog_rows:]:
        for line in render(row):
            print(line, file=stdout)
    stdout.flush()
    open_input = True
    for _ in passes if passes is not None else iter(int, 1):
        if open_input:
            ready, _, _ = select.select([stdin], [], [], poll_seconds)
            if ready:
                line = stdin.readline()
                if line == "":
                    open_input = False
                elif deliver_to_inbox(inbox, line):
                    print(f"[mirror] queued for the Validator: {_clip(line)[:120]}", file=stdout)
        for row in transcript.new_rows():
            for line in render(row):
                print(line, file=stdout)
        stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--transcript", required=True, type=pathlib.Path)
    parser.add_argument("--inbox", required=True, type=pathlib.Path)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--backlog-rows", type=int, default=40)
    args = parser.parse_args(argv)
    if args.transcript.is_symlink() or not args.transcript.is_file():
        print(f"validator-mirror: {args.transcript} is not a regular file", file=sys.stderr)
        return 64
    mirror(
        Transcript(args.transcript),
        args.inbox,
        stdin=sys.stdin,
        stdout=sys.stdout,
        poll_seconds=args.poll_seconds,
        backlog_rows=args.backlog_rows,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
