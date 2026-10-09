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


DIGEST = "sha256:" + "ab" * 32


def test_ruling_notice_delivers_without_a_question_and_carries_the_digest(tmp_path: Path) -> None:
    root = run_root(tmp_path)
    notice = plan_message(
        root,
        sender="validator",
        lane="coder",
        message_kind="ruling-notice",
        text="RUL-6 binds discovery: list endpoints paginate.",
        basis="ignored: derived from the citation",
        authority="ratified-spec",
        ruling_id="RUL-6",
        ruling_sha256=DIGEST,
    )
    assert notice["question_id"] is None
    assert notice["basis"] == f"ruling=RUL-6 {DIGEST}"
    record_delivery(root, message_id=str(notice["message_id"]), thread_id=THREAD, transport="queue")
    assert pending_questions(root) == []


def test_ruling_notice_is_refused_without_a_ruling_id_or_from_the_orchestrator(
    tmp_path: Path,
) -> None:
    root = run_root(tmp_path)
    kwargs = dict(
        lane="coder",
        message_kind="ruling-notice",
        text="dependencies are now cached",
        basis="x",
        authority="runtime-protocol",
        ruling_sha256=DIGEST,
    )
    with pytest.raises(LaneDialogueError, match="ruling id"):
        plan_message(root, sender="validator", **kwargs)
    with pytest.raises(LaneDialogueError, match="only the Validator"):
        plan_message(root, sender="orchestrator", ruling_id="RUL-1", **kwargs)
