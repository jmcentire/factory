from __future__ import annotations

import sys
from pathlib import Path

import pytest

from harness.codex_lane_session import SessionError, run_turn
from harness.lane_dialogue import pending_questions


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


def test_codex_lane_session_retains_typed_question_from_agent_message(
    tmp_path: Path,
) -> None:
    root = tmp_path / "run"
    root.mkdir()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("do the work\n", encoding="utf-8")
    script = (
        "import json; "
        "print(json.dumps({'type':'thread.started',"
        "'thread_id':'12345678-1234-4234-8234-123456789abc'})); "
        "print(json.dumps({'type':'item.completed','item':{"
        "'type':'command_execution','aggregated_output':"
        "'FACTORY_QUESTION: tool output is not a lane question'}})); "
        "print(json.dumps({'type':'item.completed','item':{"
        "'type':'agent_message','text':"
        "'FACTORY_QUESTION: Which retry owner does the specification select?'}}))"
    )

    result = run_turn(
        prompt,
        tmp_path / "thread-id",
        tmp_path / "events.jsonl",
        [sys.executable, "-c", script],
        root=root,
        role="tester",
    )

    assert result == 0
    assert [row["text"] for row in pending_questions(root, "tester")] == [
        "Which retry owner does the specification select?"
    ]


def test_codex_lane_session_refuses_a_symlinked_thread_identity(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("do the work\n", encoding="utf-8")
    target = tmp_path / "outside-thread-id"
    target.write_text("12345678-1234-4234-8234-123456789abc\n", encoding="utf-8")
    thread = tmp_path / "thread-id"
    thread.symlink_to(target)
    script = (
        "import json; print(json.dumps({'type':'thread.started',"
        "'thread_id':'12345678-1234-4234-8234-123456789abc'}))"
    )

    with pytest.raises(SessionError, match="cannot be opened safely"):
        run_turn(
            prompt,
            thread,
            tmp_path / "events.jsonl",
            [sys.executable, "-c", script],
        )
