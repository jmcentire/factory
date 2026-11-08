"""Durable inbox consumption across missed notifications and uncertain delivery.

The injected transport models delivery outcomes, not process authentication. Real native
identity/busy-boundary coverage lives in test_persistent_lane; this suite also exercises
production deliver's refusal when that identity check fails.
"""

import importlib
import json

import pytest

from harness import run_record


def wake():
    return importlib.import_module("harness.inbox_wake")


@pytest.fixture
def root(tmp_path):
    (tmp_path / "harness.json").write_text(json.dumps({"run_id": "r", "tmux_socket": "test-only"}))
    return tmp_path


def batches(root, recipient):
    return [
        json.loads(path.read_text())
        for path in (root / "inbox-wake/batches").glob(f"{recipient}-*.json")
    ]


def state(root, recipient):
    return json.loads((root / "inbox-wake/state.json").read_text())["recipients"][recipient]


class Transport:
    def __init__(self, result="acknowledged"):
        self.result = result
        self.calls = []

    def __call__(self, root, recipient, identifier):
        self.calls.append((recipient, identifier))
        return {"state": self.result}


def test_startup_drains_existing_completion_without_new_append(root):
    folder = root / "tmux-lanes"
    folder.mkdir()
    (folder / "coder-exits.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "factory-lane-exit/1",
                "slot": "coder",
                "rc": 0,
                "ts": "2026-11-08T17:55:38+00:00",
            }
        )
        + "\n"
    )
    transport = Transport()
    wake().tick(root, transport)
    assert {"validator", "orchestrator"} <= {recipient for recipient, _ in transport.calls}
    for role in ("validator", "orchestrator"):
        assert state(root, role)["consumed"]
        assert any("coder" in item["text"] for item in batches(root, role))
    transport.calls.clear()
    wake().tick(root, transport)
    assert transport.calls == []


@pytest.mark.parametrize("outcome", ["busy", "pending", "dead", "unsupported"])
def test_uncertain_or_unavailable_delivery_never_consumes_and_restart_retries_same_id(
    root, outcome
):
    run_record.message(root, "validator", "coder", "Completed the requested change")
    transport = Transport(outcome)
    wake().tick(root, transport)
    first = [call for call in transport.calls if call[0] == "validator"]
    assert len(first) == 1
    assert not state(root, "validator")["consumed"]
    # A fresh transport and module invocation must recover pending state from disk.
    retry = Transport()
    wake().tick(root, retry)
    assert [call for call in retry.calls if call[0] == "validator"] == first
    assert state(root, "validator")["consumed"]
    assert not state(root, "validator")["pending"]
    retry.calls.clear()
    wake().tick(root, retry)
    assert retry.calls == []


def test_new_messages_do_not_mutate_inflight_batch_or_replay_consumed_events(root):
    run_record.message(root, "validator", "coder", "first")
    transport = Transport("pending")
    wake().tick(root, transport)
    first_id = transport.calls[0][1]
    first_events = set(batches(root, "validator")[0]["event_ids"])
    run_record.message(root, "validator", "tester", "second")
    transport.result = "acknowledged"
    wake().tick(root, transport)
    wake().tick(root, transport)
    retained = batches(root, "validator")
    assert len(retained) == 2
    assert any(
        b["message_id"] == first_id and set(b["event_ids"]) == first_events for b in retained
    )
    assert set(retained[0]["event_ids"]).isdisjoint(retained[1]["event_ids"])
    assert set(state(root, "validator")["consumed"]) == set().union(
        *(b["event_ids"] for b in retained)
    )


def test_transport_failure_preserves_message_for_retry(root):
    run_record.message(root, "validator", "host", "keep this")

    def broken(*args):
        raise OSError("connection interrupted before receipt")

    try:
        wake().tick(root, broken)
    except OSError:
        pass
    assert not state(root, "validator")["consumed"]
    transport = Transport()
    wake().tick(root, transport)
    assert any(role == "validator" for role, _ in transport.calls)
    assert state(root, "validator")["consumed"]


def test_recipient_ack_does_not_consume_another_recipients_inbox(root):
    run_record.message(root, "validator", "coder", "implementation ready")
    run_record.message(root, "orchestrator", "tester", "review concern")

    def selective(root, recipient, identifier):
        return {"state": "acknowledged" if recipient == "validator" else "busy"}

    wake().tick(root, selective)
    assert state(root, "validator")["consumed"]
    assert not state(root, "orchestrator")["consumed"]
    retry = Transport()
    wake().tick(root, retry)
    assert {role for role, _ in retry.calls} == {"orchestrator"}


def test_explicit_stop_survives_restart_and_prevents_delivery(root):
    run_record.message(root, "validator", "host", "pending at closure")
    wake().stop(root)
    transport = Transport()
    wake().tick(root, transport)
    wake().tick(root, transport)
    assert transport.calls == []


def test_inbox_routing_does_not_open_author_cross_talk(root):
    for recipient in ("coder", "tester"):
        with pytest.raises(ValueError):
            run_record.message(root, recipient, "orchestrator", "bypass independent inputs")
    assert not (root / "coder/inbox.jsonl").exists()
    assert not (root / "tester/inbox.jsonl").exists()


def test_another_cycle_owner_cannot_deliver_concurrently(root):
    import fcntl

    run_record.message(root, "validator", "coder", "single owner")
    folder = root / "inbox-wake"
    folder.mkdir()
    transport = Transport()
    with (folder / "owner.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert wake().tick(root, transport)["status"] == "owned"
        assert transport.calls == []
    wake().tick(root, transport)
    assert transport.calls


def test_unsupported_existing_terminal_is_never_used_as_fallback(root, monkeypatch):
    import subprocess

    run_record.message(root, "validator", "coder", "read results")
    calls = []

    def forbid_terminal(*args, **kwargs):
        calls.append(args)
        raise AssertionError("delivery must not fall back to shell or tmux input")

    monkeypatch.setattr(subprocess, "run", forbid_terminal)
    monkeypatch.setattr(subprocess, "Popen", forbid_terminal)
    wake().tick(root)
    assert not state(root, "validator")["consumed"]
    assert calls == []
