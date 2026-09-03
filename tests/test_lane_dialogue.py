from __future__ import annotations

from pathlib import Path

import pytest

from harness.lane_dialogue import (
    LaneDialogueError,
    pending_questions,
    plan_message,
    record_delivery,
    record_question,
)

THREAD = "12345678-1234-4234-8234-123456789abc"


def run_root(tmp_path: Path) -> Path:
    root = tmp_path / "run"
    root.mkdir()
    return root


def test_question_blocks_until_a_bound_answer_is_delivered(tmp_path: Path) -> None:
    root = run_root(tmp_path)
    question, created = record_question(
        root,
        "tester",
        "Should an unknown reservation state be rejected or preserved?",
    )
    repeated, repeated_created = record_question(
        root,
        "tester",
        "Should an unknown reservation state be rejected or preserved?",
    )

    assert created is True
    assert repeated_created is False
    assert repeated["question_id"] == question["question_id"]
    assert [row["question_id"] for row in pending_questions(root)] == [question["question_id"]]

    answer = plan_message(
        root,
        sender="validator",
        lane="tester",
        message_kind="spec-answer",
        text="Reject it with the contract's typed unknown-state error.",
        basis="founder answered the retained question",
        authority="human-answer",
        question_id=str(question["question_id"]),
    )
    assert pending_questions(root)
    record_delivery(
        root,
        message_id=str(answer["message_id"]),
        thread_id=THREAD,
        transport="resume",
    )
    assert pending_questions(root) == []

    next_occurrence, next_created = record_question(
        root,
        "tester",
        "Should an unknown reservation state be rejected or preserved?",
    )
    assert next_created is True
    assert next_occurrence["question_id"] != question["question_id"]


def test_orchestrator_can_probe_but_cannot_answer_a_lane(tmp_path: Path) -> None:
    root = run_root(tmp_path)
    question, _ = record_question(root, "coder", "Which public method owns retry state?")

    probe = plan_message(
        root,
        sender="orchestrator",
        lane="coder",
        message_kind="status-probe",
        text="FACTORY_STATUS_PROBE: report explicit state.",
        basis="silence is not a liveness classification",
        authority="runtime-protocol",
    )
    assert probe["message_kind"] == "status-probe"

    with pytest.raises(LaneDialogueError, match="only the Validator"):
        plan_message(
            root,
            sender="orchestrator",
            lane="coder",
            message_kind="spec-answer",
            text="Put retry state on the client.",
            basis="orchestrator guessed",
            authority="human-answer",
            question_id=str(question["question_id"]),
        )


def test_answer_cannot_cross_to_the_other_lane_or_be_reanswered(tmp_path: Path) -> None:
    root = run_root(tmp_path)
    question, _ = record_question(root, "tester", "Is cancellation idempotent?")

    with pytest.raises(LaneDialogueError, match="existing question for this lane"):
        plan_message(
            root,
            sender="validator",
            lane="coder",
            message_kind="spec-answer",
            text="Yes.",
            basis="ratified requirement R-4",
            authority="ratified-spec",
            question_id=str(question["question_id"]),
        )

    answer = plan_message(
        root,
        sender="validator",
        lane="tester",
        message_kind="spec-answer",
        text="Yes.",
        basis="ratified requirement R-4",
        authority="ratified-spec",
        question_id=str(question["question_id"]),
    )
    with pytest.raises(LaneDialogueError, match="only one planned specification answer"):
        plan_message(
            root,
            sender="validator",
            lane="tester",
            message_kind="spec-answer",
            text="No.",
            basis="a conflicting answer planned before delivery",
            authority="ratified-spec",
            question_id=str(question["question_id"]),
        )
    record_delivery(
        root,
        message_id=str(answer["message_id"]),
        thread_id=THREAD,
        transport="queue",
    )
    with pytest.raises(LaneDialogueError, match="already has a delivered answer"):
        plan_message(
            root,
            sender="validator",
            lane="tester",
            message_kind="spec-answer",
            text="No.",
            basis="a conflicting guess",
            authority="ratified-spec",
            question_id=str(question["question_id"]),
        )
