from __future__ import annotations

import sys
from pathlib import Path

import pytest

from harness.codex_lane_session import SessionError, run_turn


def test_codex_lane_session_retains_the_real_thread_id_and_stream(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("do the work\n", encoding="utf-8")
    thread = tmp_path / "thread-id"
    events = tmp_path / "events.jsonl"
    script = (
        "import json,sys; "
        "assert sys.stdin.read() == 'do the work\\n'; "
        "print(json.dumps({'type':'item.completed','item':{"
        "'session_id':'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'}})); "
        "print(json.dumps({'type':'thread.started',"
        "'thread_id':'12345678-1234-4234-8234-123456789abc'})); "
        "print(json.dumps({'type':'item.completed','text':'done'}))"
    )

    result = run_turn(prompt, thread, events, [sys.executable, "-c", script])

    assert result == 0
    assert thread.read_text().strip() == "12345678-1234-4234-8234-123456789abc"
    assert "thread.started" in events.read_text()
    assert "item.completed" in events.read_text()


def test_codex_lane_session_refuses_output_without_a_thread_id(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("do the work\n", encoding="utf-8")

    with pytest.raises(SessionError, match="no retained thread id"):
        run_turn(
            prompt,
            tmp_path / "thread-id",
            tmp_path / "events.jsonl",
            [sys.executable, "-c", "print('{}')"],
        )
