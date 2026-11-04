"""Historical entry points cannot reactivate removed authority or touch old runs."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_validation_failure_paths import task as task

HARNESS = Path(__file__).resolve().parents[1] / "harness"


@pytest.mark.parametrize("command", [
    "dispatch_lane.sh", "promote.sh", "record_no.sh", "abandon_legacy.sh",
])
@pytest.mark.parametrize("status", ["open", "closed", "no", "unknown"])
def test_retired_commands_preserve_all_evidence_and_launch_nothing(tmp_path, command, status):
    root = tmp_path / "runs/r1"
    root.mkdir(parents=True)
    for name, text in {
        "harness.json": json.dumps({"status": status, "schema_version": "factory-harness/2"}),
        "verdict.json": '{"verdict":"PASS"}',
        "promotion_inputs.json": '{"allow":true}',
        "TASK.md": "unrelated user work",
    }.items():
        (root / name).write_text(text)
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    # Empty PATH: there is no available CLI, Python, tmux or agent to invoke.
    result = subprocess.run(
        ["/bin/bash", str(HARNESS / command), "r1", "--runs", str(root.parent)],
        cwd=tmp_path, env={"PATH": str(tmp_path / "absent")},
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 64
    assert "retired" in result.stderr
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    assert list(tmp_path.iterdir()) == [root.parent]


@pytest.mark.parametrize("kind", ["pre_dispatch", "pre_verdict", "user_imperative"])
def test_checkpoint_queues_exact_detail_without_waiting_or_changing_task(task, kind):
    runs, root, _, _ = task
    before = (root / "harness.json").read_bytes()
    # Deliberately stale/blocked historical records are observations, not prerequisites.
    (root / "orchestrator").mkdir()
    (root / "orchestrator/activity.jsonl").write_text('{"cursor":999,"kind":"unread"}\n')
    (root / "orchestrator/seat-control.json").write_text('{"effect":"block"}')
    detail = "$(touch MUST_NOT_EXIST)\n`echo not-code`; precise task decision"
    result = subprocess.run(
        ["/bin/bash", str(HARNESS / "orchestrator_checkpoint.sh"), "task", kind,
         detail, "--runs", str(runs)],
        env={"PATH": "/usr/bin:/bin", "FACTORY_PYTHON": sys.executable,
             "FACTORY_ORCHESTRATOR_CHECKPOINT_TIMEOUT": "9999"},
        cwd=root, capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert "queued" in result.stdout and "not an acknowledgment" in result.stdout
    row = json.loads((root / "orchestrator/inbox.jsonl").read_text())
    assert row["sender"] == "validator" and row["text"] == f"Checkpoint: {kind}\n{detail}"
    assert (root / "harness.json").read_bytes() == before
    assert not (root / "MUST_NOT_EXIST").exists()
    assert not (root / "orchestrator/assessments.jsonl").exists()
    assert not (root / "events.jsonl").exists()


def test_checkpoint_does_not_claim_notification_when_inbox_is_unwritable(task):
    runs, root, _, _ = task
    (root / "orchestrator").mkdir()
    os.mkfifo(root / "orchestrator/inbox.jsonl")
    result = subprocess.run(
        ["/bin/bash", str(HARNESS / "orchestrator_checkpoint.sh"), "task", "pre_commit",
         "decision", "--runs", str(runs)],
        env={"PATH": "/usr/bin:/bin", "FACTORY_PYTHON": sys.executable},
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 70 and "queued" not in result.stdout
    assert "regular file" in result.stderr
