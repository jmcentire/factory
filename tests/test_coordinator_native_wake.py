"""Coordinator consumes native prompt observations, never terminal write attempts.

Synthetic interactive executable only: real wrapper, PTY, hooks, owned process
receipts and inbox wake run without paid model calls or another user's terminal.
"""

import importlib
import json
import subprocess
from pathlib import Path

import pytest

from harness import run_record
from tests.test_persistent_lane import claude_terminal as _claude_terminal
from tests.test_persistent_lane import eventually

claude_terminal = _claude_terminal

pytestmark = pytest.mark.parametrize("claude_terminal", ["validator"], indirect=True)


def consumed(root):
    return json.loads((root / "inbox-wake/state.json").read_text())["recipients"]["validator"][
        "consumed"
    ]


def test_native_observation_consumes_without_claiming_completion_or_waiting_for_idle(
    claude_terminal,
):
    lane = claude_terminal
    wake = importlib.import_module("harness.inbox_wake")
    run_record.message(lane.root, "validator", "coder", "WAIT_REPORT NO_STOP first complete")
    wake.tick(lane.root)
    eventually(lambda: len(lane.received()) == 2)
    assert not consumed(lane.root)
    (lane.folder / "allow-report").touch()
    eventually(lambda: (wake.tick(lane.root), consumed(lane.root))[1])
    assert lane.raw()["phase"] == "prompt-observed"
    before = lane.raw()
    run_record.message(lane.root, "validator", "tester", "NO_STOP second complete")
    eventually(lambda: (wake.tick(lane.root), len(consumed(lane.root)))[1] == 2)
    assert len(lane.received()) == 3
    assert lane.raw()["worker"] == before["worker"]
    assert lane.raw()["session_id"] == before["session_id"]
    assert lane.raw()["phase"] == "prompt-observed"


def test_mismatched_retained_process_cannot_consume_or_receive_message(claude_terminal):
    lane = claude_terminal
    wake = importlib.import_module("harness.inbox_wake")
    run_record.message(lane.root, "validator", "host", "must not reach foreign process")
    path = lane.folder / "validator-process.json"
    receipt = json.loads(path.read_text())
    changed = {**receipt, "worker": {**receipt["worker"], "started": "wrong-process-start"}}
    lane.driver.store(path, changed)
    before = lane.received()
    try:
        wake.tick(lane.root)
        assert not consumed(lane.root)
        assert lane.received() == before
    finally:
        lane.driver.store(path, receipt)
    eventually(lambda: (wake.tick(lane.root), consumed(lane.root))[1])
    assert len(lane.received()) == len(before) + 1


def test_daemon_ends_when_owned_native_recipient_exits(claude_terminal, monkeypatch):
    lane = claude_terminal
    wake = importlib.import_module("harness.inbox_wake")
    lane.child.terminate()
    lane.child.wait(timeout=5)
    real_run = subprocess.run

    def read_processes_only(argv, **kwargs):
        assert Path(argv[0]).name == "ps", "dead recipient must not trigger another session"
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", read_processes_only)
    wake.run(lane.root)
    assert wake.status(lane.root)["status"] == "recipients-unavailable"
