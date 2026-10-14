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


def test_codex_lane_session_retains_a_multi_line_question_in_full(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("do the work\n", encoding="utf-8")
    script = (
        "import json; "
        "print(json.dumps({'type':'thread.started',"
        "'thread_id':'12345678-1234-4234-8234-123456789abc'})); "
        "print(json.dumps({'type':'item.completed','item':{"
        "'type':'agent_message','text':"
        "'Blocked.\\nFACTORY_QUESTION: Please resolve these together:\\n1. A?\\n2. B?'}}))"
    )

    run_turn(
        prompt,
        tmp_path / "thread-id",
        tmp_path / "events.jsonl",
        [sys.executable, "-c", script],
        root=root,
        role="tester",
    )

    assert [row["text"] for row in pending_questions(root, "tester")] == [
        "Please resolve these together:\n1. A?\n2. B?"
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


def _run_agent_message(tmp_path: Path, text: str) -> tuple[int, Path]:
    """Drive one real Codex-shaped turn whose assistant message is ``text``."""

    root = tmp_path / "run"
    root.mkdir()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("do the work\n", encoding="utf-8")
    message = tmp_path / "message.txt"
    message.write_text(text, encoding="utf-8")
    script = (
        "import json,sys; "
        "print(json.dumps({'type':'thread.started',"
        "'thread_id':'12345678-1234-4234-8234-123456789abc'})); "
        "print(json.dumps({'type':'item.completed','item':{"
        "'type':'agent_message','text':open(sys.argv[1],encoding='utf-8').read()}})); "
        "print(json.dumps({'type':'turn.completed'}))"
    )
    result = run_turn(
        prompt,
        tmp_path / "thread-id",
        tmp_path / "events.jsonl",
        [sys.executable, "-c", script, str(message)],
        root=root,
        role="tester",
    )
    return result, root


def test_codex_lane_session_records_each_question_marker_separately(tmp_path: Path) -> None:
    # Answering one question must never close another: two markers are two records.
    result, root = _run_agent_message(
        tmp_path,
        "Two things block me.\n"
        "FACTORY_QUESTION: Which retry owner does the specification select?\n"
        "FACTORY_QUESTION: Is the refund window seven days or ten?\n",
    )

    assert result == 0
    assert [row["text"] for row in pending_questions(root, "tester")] == [
        "Which retry owner does the specification select?",
        "Is the refund window seven days or ten?",
    ]


def test_codex_lane_session_question_body_ends_at_a_blank_line(tmp_path: Path) -> None:
    result, root = _run_agent_message(
        tmp_path,
        "FACTORY_QUESTION: Which retry owner applies:\n- the gateway?\n- the worker?\n\n"
        "Notes: I read the retry section twice and it names neither.\n",
    )

    assert result == 0
    assert [row["text"] for row in pending_questions(root, "tester")] == [
        "Which retry owner applies:\n- the gateway?\n- the worker?"
    ]


def test_codex_lane_session_survives_a_question_followed_by_long_notes(tmp_path: Path) -> None:
    # A blank line ends the question, so 20 KiB of trailing notes are not part of it.
    notes = "\n".join("note line about the work in progress " * 4 for _ in range(150))
    assert len(notes.encode()) > 16 * 1024
    result, root = _run_agent_message(
        tmp_path, "FACTORY_QUESTION: Which retry owner applies?\n\n" + notes
    )

    assert result == 0
    assert [row["text"] for row in pending_questions(root, "tester")] == [
        "Which retry owner applies?"
    ]


def test_codex_lane_session_truncates_an_oversized_question_visibly(tmp_path: Path) -> None:
    # A question body over the dialogue ceiling is recorded truncated and marked, never fatal.
    body = "\n".join("context the question depends on " * 4 for _ in range(200))
    assert len(body.encode()) > 16 * 1024
    result, root = _run_agent_message(
        tmp_path, "FACTORY_QUESTION: Which retry owner applies, given:\n" + body
    )

    assert result == 0
    [question] = pending_questions(root, "tester")
    assert question["text"].startswith("Which retry owner applies, given:\ncontext")
    assert question["text"].endswith("question exceeded the dialogue size limit]")
    assert len(question["text"].encode()) <= 16 * 1024
