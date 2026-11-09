"""Retain existing dialogue history without reintroducing ratification for new messages.

The legacy fixture was emitted by record_question/plan_message on 5ac29cc; it is persisted
input compatibility evidence, not an oracle for current authority. The contract requires its
question still be deliverable without changing history, while new writes use current task
authority. Rejecting that history or accepting a new retired-authority write turns this red.
"""

from pathlib import Path

import pytest

from harness.lane_dialogue import (
    LaneDialogueError,
    pending_questions,
    plan_message,
    record_delivery,
)


def test_legacy_dialogue_replays_but_new_messages_cannot_revive_ratification(
    tmp_path: Path,
) -> None:
    root = tmp_path / "run"
    journal = root / "dialogue/journal.jsonl"
    journal.parent.mkdir(parents=True)
    fixture = Path(__file__).with_name("fixtures") / "lane-dialogue-pre-cleanup.jsonl"
    original = fixture.read_bytes()
    journal.write_bytes(original)

    assert [row["question_id"] for row in pending_questions(root)] == ["Q-0001-3b4e600f1745"]
    record_delivery(
        root, message_id="M-0002-98d8c49f4194",
        thread_id="12345678-1234-4234-8234-123456789abc", transport="queue",
    )
    assert pending_questions(root) == []
    assert journal.read_bytes().startswith(original)
    delivered = journal.read_bytes()

    with pytest.raises(LaneDialogueError):
        plan_message(
            root, sender="validator", lane="coder", message_kind="progress-review",
            text="The implementation preserves cancellation idempotence.",
            basis="task requirement R-4", authority="ratified-spec",
        )
    assert journal.read_bytes() == delivered
