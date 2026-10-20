"""The kickoff Validator's window: its console goes out, and what is typed in reaches its inbox."""

from __future__ import annotations

import importlib
import io
import json
import os
from pathlib import Path

import harness.orchestrator_channel  # noqa: F401 - establish adjacent harness imports

mirror = importlib.import_module("validator_mirror")


def rows(path: Path, *entries: object) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")


def said(kind: str, content: object) -> dict[str, object]:
    return {"type": kind, "message": {"role": kind, "content": content}}


def test_the_console_shows_what_was_said_and_done_not_reasoning() -> None:
    assert mirror.render(said("user", "Ship it.")) == ["founder: Ship it."]
    assert mirror.render(
        said(
            "assistant",
            [
                {"type": "thinking", "thinking": "private"},
                {"type": "text", "text": "Merging #59."},
                {"type": "tool_use", "name": "Bash", "input": {"command": "gh pr merge 59"}},
            ],
        )
    ) == ["validator: Merging #59.", "tool Bash: gh pr merge 59"]
    assert mirror.render(
        said("user", [{"type": "tool_result", "content": [{"type": "text", "text": "merged"}]}])
    ) == ["result: merged"]
    assert mirror.render({"type": "summary", "summary": "x"}) == []
    assert len(mirror.render(said("user", "y" * 2000))[0]) <= len("founder: ") + 500


def test_typed_lines_reach_the_inbox_and_the_console_follows_the_transcript(
    tmp_path: Path,
) -> None:
    transcript, inbox = tmp_path / "session.jsonl", tmp_path / "validator" / "inbox.jsonl"
    rows(transcript, said("user", "first"), said("assistant", [{"type": "text", "text": "ok"}]))
    read_end, write_end = os.pipe()
    correction = "FACTORY_CORRECTION from the Orchestrator. Rule: one tester. Correction: resume."
    os.write(write_end, f"1\n{correction}\n".encode())
    os.close(write_end)
    out = io.StringIO()
    with os.fdopen(read_end) as stdin:
        passes = iter(range(4))

        def counted():
            for index in passes:
                if index == 1:
                    rows(transcript, said("assistant", [{"type": "text", "text": "later"}]))
                yield index

        mirror.mirror(
            mirror.Transcript(transcript),
            inbox,
            stdin=stdin,
            stdout=out,
            poll_seconds=0,
            backlog_rows=1,
            passes=counted(),
        )
    shown = out.getvalue()
    assert "founder: first" not in shown  # only the backlog asked for
    assert "validator: ok" in shown and "validator: later" in shown
    queued = [json.loads(line)["text"] for line in inbox.read_text().splitlines()]
    assert queued == [correction]  # a dialog key is not a message
    assert inbox.stat().st_mode & 0o077 == 0


def test_a_symlinked_transcript_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real.jsonl"
    real.write_text("")
    link = tmp_path / "link.jsonl"
    link.symlink_to(real)
    assert mirror.main(["--transcript", str(link), "--inbox", str(tmp_path / "inbox")]) == 64
