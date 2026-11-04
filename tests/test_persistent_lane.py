"""Bootstrap driver regressions using owned native-protocol fixtures, never tmux seats."""

from __future__ import annotations

import importlib
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

THREAD = "12345678-1234-4234-8234-123456789abc"
FAKE_NATIVE = r"""
import json, pathlib, sys, time
agent, folder, thread = sys.argv[1:]
folder = pathlib.Path(folder)
def emit(row):
    print(json.dumps(row), flush=True)
if agent == "agy":
    emit({"event": "init", "conversation_id": thread})
else:
    emit({"type": "system", "subtype": "init", "session_id": thread,
          "capabilities": ["interrupt_receipt_v1", "interrupt_cancel_queued_v1"]})
active = None
step_index = -1
for line in sys.stdin:
    step_index += 1
    row = json.loads(line)
    if row.get("type") == "control_request":
        with (folder / "controls.jsonl").open("a") as stream:
            stream.write(json.dumps(row) + "\n")
        if active["message"]["content"] == "ACTIVE_COMPLETED_FIRST":
            emit({"type": "result", "session_id": thread, "is_error": False,
                  "user_message_uuid": active["uuid"]})
            while not (folder / "allow-control-ack").exists():
                time.sleep(.01)
        emit({"type": "control_response", "response": {"subtype": "success",
              "request_id": row["request_id"], "response": {"still_queued": [], "cancelled": []}}})
        if active["message"]["content"] == "ACTIVE_COMPLETED_FIRST":
            continue
        if active["message"]["content"] == "ACTIVE_ACK_ONLY":
            while not (folder / "allow-result").exists():
                time.sleep(.01)
        emit({"type": "result", "session_id": thread, "is_error": True,
              "terminal_reason": "quota" if active["message"]["content"] == "ACTIVE_ERROR"
                                 else "aborted_tools",
              "user_message_uuid": "stale" if active["message"]["content"] == "ACTIVE_STALE"
                                   else active["uuid"]})
        continue
    text = row["message"]["content"]
    with (folder / "received.jsonl").open("a") as stream:
        stream.write(json.dumps({"text": text, "pid": __import__("os").getpid()}) + "\n")
    launch = folder / "coder-launch.jsonl"
    if launch.exists():
        reopened = [r.get("message_id") for r in map(json.loads, launch.read_text().splitlines())
                    if r.get("schema_version") == "factory-tmux-lane-reopen/1"]
        with (folder / "reopen-at-input.jsonl").open("a") as stream:
            stream.write(json.dumps({"text": text, "reopened": reopened}) + "\n")
    if text == "EXIT":
        break
    if text == "REPLAY_ACK":
        emit({"event": "step_update", "step_update": {"conversation_id": thread,
              "step_type": "user_input", "state": "DONE", "step_index": step_index - 1}})
        (folder / "replayed-ack").touch()
    if text in {"WAIT_ACK", "REPLAY_ACK"}:
        while not (folder / "allow-ack").exists():
            time.sleep(.01)
    session = "99999999-9999-4999-8999-999999999999" if text == "BAD_SESSION" else thread
    if text != "NO_ACK":
        if agent == "claude":
            emit({"type": "user", "uuid": row["uuid"], "session_id": session,
                  "message": {"role": "user", "content": text}, "parent_tool_use_id": None})
        else:
            emit({"event": "step_update", "step_update": {"conversation_id": session,
                  "step_type": "user_input", "state": "DONE", "step_index": step_index}})
    if text.startswith("ACTIVE"):
        active = row
        continue
    if agent == "claude":
        emit({"type": "result", "session_id": session, "result": "received literally"})
    else:
        emit({"event": "result", "result": {"conversation_id": session, "status": "SUCCESS"}})
"""


def eventually(function, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = function()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("owned native fixture did not reach expected state")


@pytest.fixture
def native_lane(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "harness"))
    driver = importlib.import_module("persistent_lane")
    watchdog = importlib.import_module("lane_watchdog")
    dialogue = importlib.import_module("lane_dialogue")
    children = []

    class Lane:
        def __init__(self, agent, *, name=None, receipt_changes=None, expect_error=False):
            self.root = tmp_path / (name or agent)
            self.folder = self.root / "tmux-lanes"
            self.folder.mkdir(parents=True)
            self.driver = driver
            self.child = subprocess.Popen(
                [sys.executable, "-u", "-c", FAKE_NATIVE, agent, str(self.folder), THREAD],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            table = watchdog.process_snapshot()
            if table is None:
                self.child.stdin.close()
                self.child.wait(timeout=5)
                self.child.stdout.close()
                self.child.stderr.close()
            assert table is not None, "real PID/start validation needs permitted ps"
            worker, wrapper = table[self.child.pid], table[os.getpid()]
            self.receipt = {
                "schema": "factory-worker-process/1",
                "slot": "coder",
                "status": "running",
                "worker": {k: worker[k] for k in ("pid", "started")},
                "wrapper": {k: wrapper[k] for k in ("pid", "started")},
            }
            children.append(self)
            driver.store(
                self.folder / "coder-process.json", {**self.receipt, **(receipt_changes or {})}
            )
            self.errors, self.output = [], []

            def run():
                try:
                    driver.run(
                        self.child,
                        root=self.root,
                        slot="coder",
                        agent=agent,
                        prompt="initial",
                        session_file=self.folder / "coder-thread-id",
                        emit=self.output.append,
                    )
                except Exception as error:  # retained for assertions, never a silent thread failure
                    self.errors.append(error)

            self.thread = threading.Thread(target=run, daemon=True)
            self.thread.start()
            if expect_error:
                eventually(lambda: self.errors)
            else:
                eventually(lambda: self.raw().get("phase") == "idle")

        def raw(self):
            path = self.folder / "coder-persistent.json"
            return driver.read(path) if path.exists() else {}

        def plan(self, text, kind="status-probe"):
            return dialogue.plan_message(
                self.root,
                sender="validator",
                lane="coder",
                message_kind=kind,
                text=text,
                basis="native protocol regression",
                authority="ratified-spec" if kind == "progress-review" else "runtime-protocol",
            )["message_id"]

        def send(self, identifier, thread=THREAD, *, defer=False):
            return driver.request(self.root, "coder", identifier, thread, defer=defer)

        def received(self):
            return [
                json.loads(line)
                for line in (self.folder / "received.jsonl").read_text().splitlines()
            ]

        def delivered(self, identifier):
            return [
                r
                for r in dialogue._read(self.root / "dialogue" / "journal.jsonl")
                if r.get("record_type") == "message-delivered" and r.get("message_id") == identifier
            ]

    yield Lane
    for lane in children:
        # Cleanup addresses only this Popen-owned child, never a tmux server or another run.
        (lane.folder / "allow-ack").touch()
        (lane.folder / "allow-result").touch()
        (lane.folder / "allow-control-ack").touch()
        if lane.child.poll() is None:
            table = watchdog.process_snapshot()
            actual = (table or {}).get(lane.child.pid, {})
            expected = lane.receipt["worker"]
            assert actual.get("started") == expected["started"], "cleanup identity mismatch"
            if "T" in actual.get("state", ""):
                os.kill(lane.child.pid, signal.SIGCONT)
            lane.child.stdin.close()
        lane.child.wait(timeout=5)
        lane.thread.join(timeout=5)
        assert not lane.thread.is_alive()
        lane.child.stdout.close()
        lane.child.stderr.close()


@pytest.mark.parametrize("agent", ["claude", "agy"])
def test_same_native_process_and_context_accept_literal_multiple_turns(
    native_lane,
    agent,
    tmp_path,
):
    lane = native_lane(agent)
    before = lane.driver.inspect(lane.root, "coder")
    marker = tmp_path / "must-not-execute"
    text = f"literal $(touch {marker}); `touch {marker}`\nquotes ' \" $HOME"
    identifier = lane.plan(text)
    assert lane.send(identifier)["state"] == "pending"
    eventually(lambda: lane.raw().get("turns") == 2)
    assert lane.send(identifier)["state"] == "acknowledged"
    after = lane.driver.inspect(lane.root, "coder")
    assert before["worker"] == after["worker"] and after["session_id"] == THREAD
    assert after["completed_work_s"] > before["completed_work_s"] > 0
    assert lane.received() == [
        {"text": "initial", "pid": lane.child.pid},
        {"text": text, "pid": lane.child.pid},
    ]
    assert not marker.exists() and not lane.errors
    assert lane.child.poll() is None


@pytest.mark.parametrize("agent", ["claude", "agy"])
def test_enqueue_and_success_result_never_fabricate_native_ack(native_lane, agent):
    lane = native_lane(agent)
    identifier = lane.plan("WAIT_ACK")
    assert lane.send(identifier)["state"] == "pending"
    assert lane.send(identifier)["state"] == "pending"
    another = lane.plan("must wait for the current turn")
    assert lane.send(another)["state"] == "busy"
    assert lane.raw()["turns"] == 1
    (lane.folder / "allow-ack").touch()
    eventually(lambda: lane.raw()["turns"] == 2)
    assert lane.send(identifier)["state"] == "acknowledged"
    no_ack = lane.plan("NO_ACK")
    assert lane.send(no_ack)["state"] == "pending"
    eventually(lambda: lane.raw()["turns"] == 3)
    assert lane.send(no_ack)["state"] == "pending"
    assert [r["text"] for r in lane.received()].count("NO_ACK") == 1


def test_wrong_session_and_stale_receipt_refuse_native_input(native_lane):
    lane = native_lane("claude")
    identifier = lane.plan("must remain undelivered")
    with pytest.raises(ValueError, match="conversation mismatch"):
        lane.send(identifier, "wrong-session")
    altered = {**lane.receipt, "worker": {**lane.receipt["worker"], "started": "stale"}}
    lane.driver.store(lane.folder / "coder-process.json", altered)
    with pytest.raises(ValueError, match="receipt changed"):
        lane.send(identifier)
    previous = lane.raw()
    path = lane.folder / "coder-persistent.json"
    lane.driver.store(path, {**previous, "worker": altered["worker"]})
    with pytest.raises(ValueError, match="exited|stale"):
        lane.send(identifier)
    lane.driver.store(path, previous)
    lane.driver.store(lane.folder / "coder-process.json", lane.receipt)
    assert len(lane.received()) == 1


def test_stopped_owned_native_child_is_busy_and_never_receives_input(native_lane):
    lane = native_lane("agy")
    identifier = lane.plan("no input while held")
    # This PID is our unreaped Popen child; no terminal or unrelated process is addressed.
    os.kill(lane.child.pid, signal.SIGSTOP)
    eventually(lambda: lane.driver.inspect(lane.root, "coder")["process_state"] == "stopped")
    assert lane.send(identifier)["state"] == "busy"
    assert len(lane.received()) == 1


def test_exited_native_child_cannot_receive_another_message(native_lane):
    lane = native_lane("claude")
    identifier = lane.plan("EXIT")
    assert lane.send(identifier)["state"] == "pending"
    lane.child.wait(timeout=5)
    another = lane.plan("never send to a shell")
    with pytest.raises(ValueError, match="exited|stale"):
        lane.send(another)
    assert [row["text"] for row in lane.received()] == ["initial", "EXIT"]


@pytest.mark.parametrize("agent", ["claude", "agy"])
def test_native_session_change_is_failure_without_delivery_ack(native_lane, agent):
    lane = native_lane(agent)
    identifier = lane.plan("BAD_SESSION")
    assert lane.send(identifier)["state"] == "pending"
    eventually(lambda: lane.errors)
    assert "conversation changed" in str(lane.errors[0])
    assert lane.raw()["phase"] == "failed"
    assert lane.raw()["deliveries"][identifier]["state"] == "pending"


@pytest.mark.parametrize("agent", ["claude", "agy"])
def test_deferred_on_track_review_waits_for_boundary_and_reopens_before_input(native_lane, agent):
    lane = native_lane(agent)
    launch = {"status": "active", "run_id": "fixture-run"}
    (lane.folder / "coder-launch.jsonl").write_text(json.dumps(launch) + "\n")
    working = lane.plan("WAIT_ACK")
    assert lane.send(working)["state"] == "pending"
    text = "FACTORY_REVIEW verdict=ON_TRACK\nContinue with the ratified task."
    review = lane.plan(text, "progress-review")
    response = lane.send(review, defer=True)
    assert response["state"] == "pending" and response["boundary"] == "next-native-turn"
    assert not lane.delivered(review)
    assert text not in [r["text"] for r in lane.received()]
    (lane.folder / "allow-ack").touch()
    eventually(lambda: lane.raw()["turns"] == 3)
    assert lane.send(review)["state"] == "acknowledged"
    [receipt] = lane.delivered(review)
    assert receipt["thread_id"] == THREAD and receipt["transport"] == "persistent"
    rows = [
        json.loads(line)
        for line in (lane.folder / "reopen-at-input.jsonl").read_text().splitlines()
    ]
    assert next(row for row in rows if row["text"] == text)["reopened"][-1] == review
    assert lane.child.poll() is None and not lane.errors


@pytest.mark.parametrize("verdict", ["REDIRECT", "REPAIR"])
def test_working_redirect_or_repair_cannot_be_deferred(native_lane, verdict):
    lane = native_lane("claude")
    assert lane.send(lane.plan("WAIT_ACK"))["state"] == "pending"
    text = f"FACTORY_REVIEW verdict={verdict}\nChange course."
    review = lane.plan(text, "progress-review")
    assert lane.send(review, defer=True)["state"] == "busy"
    assert not lane.delivered(review) and review not in lane.raw()["deliveries"]
    assert text not in [r["text"] for r in lane.received()]


def test_review_backlog_cannot_hold_a_live_native_session(native_lane):
    lane = native_lane("agy")
    assert lane.send(lane.plan("WAIT_ACK"))["state"] == "pending"
    text = "FACTORY_REVIEW verdict=ON_TRACK\nContinue after the boundary."
    review = lane.plan(text, "progress-review")
    assert lane.send(review, defer=True)["state"] == "pending"
    supervisor = importlib.import_module("seat_supervisor")
    supervisor.save_state(lane.root, {"coder": ["stop:review-lag"]})
    (lane.folder / "allow-ack").touch()
    eventually(lambda: lane.raw()["turns"] == 3)
    assert lane.send(review)["state"] == "acknowledged"
    assert text in [r["text"] for r in lane.received()]


def test_replayed_agy_user_input_step_cannot_acknowledge_later_message(native_lane):
    lane = native_lane("agy")
    identifier = lane.plan("REPLAY_ACK")
    assert lane.send(identifier)["state"] == "pending"
    eventually(lambda: (lane.folder / "replayed-ack").exists())
    # Two driver turns through the socket ensure the earlier native event was serviced.
    assert lane.send(identifier)["state"] == "pending"
    assert lane.send(identifier)["state"] == "pending"
    assert not lane.delivered(identifier)
    (lane.folder / "allow-ack").touch()
    eventually(lambda: lane.raw()["turns"] == 2)
    assert lane.send(identifier)["state"] == "acknowledged"
    assert len(lane.delivered(identifier)) == 1


def test_wrapper_domain_error_cleans_owned_child_and_records_real_exit(
    native_lane,
    tmp_path,
):
    # A native protocol error must terminate only the owned child and retain its exit.
    root = tmp_path / "wrapper-error"
    folder = root / "tmux-lanes"
    folder.mkdir(parents=True)
    prompt = root / "prompt"
    prompt.write_text("BAD_SESSION")
    harness = Path(__file__).resolve().parents[1] / "harness"
    wrapper = """
import pathlib, sys
sys.path.insert(0, sys.argv[1])
import lane_agent
root = pathlib.Path(sys.argv[2])
folder = root / "tmux-lanes"
raise SystemExit(lane_agent.run_lane(
    [sys.executable, "-u", "-c", sys.argv[3], "claude", str(folder), sys.argv[4]],
    slot="coder", log=folder / "coder-log", exits=folder / "coder-exits.jsonl",
    prompt=root / "prompt", session_agent="claude", session_file=folder / "coder-thread-id",
    persistent=True, run_root=root))
"""
    watchdog = importlib.import_module("lane_watchdog")
    try:
        result = subprocess.run(
            [sys.executable, "-c", wrapper, str(harness), str(root), FAKE_NATIVE, THREAD],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 127, result.stdout + result.stderr
        [exit_row] = [
            json.loads(line) for line in (folder / "coder-exits.jsonl").read_text().splitlines()
        ]
        assert exit_row["rc"] == 127 and exit_row["slot"] == "coder"
        receipt = json.loads((folder / "coder-process.json").read_text())
        actual = (watchdog.process_snapshot() or {}).get(receipt["worker"]["pid"], {})
        assert actual.get("started") != receipt["worker"]["started"]
        assert "__LANE_EXIT__ coder rc=127" in result.stdout
    finally:
        # If the regression fails, only clean up this exact disposable child's recorded identity.
        receipt_path = folder / "coder-process.json"
        if receipt_path.exists():
            worker = json.loads(receipt_path.read_text())["worker"]
            actual = (watchdog.process_snapshot() or {}).get(worker["pid"], {})
            if actual.get("started") == worker["started"]:
                os.kill(worker["pid"], signal.SIGTERM)


def test_foreign_live_worker_receipt_cannot_bind_a_different_native_child(native_lane):
    first = native_lane("claude")
    second = native_lane(
        "claude", name="other-run", receipt_changes=first.receipt, expect_error=True
    )
    assert second.errors and not second.output
    assert first.child.poll() is None and first.raw()["phase"] == "idle"
    assert first.received() == [{"text": "initial", "pid": first.child.pid}]


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": "unrecognized-receipt"},
        {"slot": "tester"},
        {"wrapper": {"pid": 1, "started": "not the wrapper"}},
    ],
)
def test_driver_refuses_mismatched_process_receipt_before_initial_input(native_lane, changes):
    lane = native_lane("agy", receipt_changes=changes, expect_error=True)
    assert lane.errors and not lane.output
    assert not (lane.folder / "received.jsonl").exists()


@pytest.mark.parametrize("agent", ["claude", "agy"])
def test_client_disconnect_before_request_keeps_native_session_usable(native_lane, agent):
    lane = native_lane(agent)
    before = lane.driver.inspect(lane.root, "coder")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(lane.raw()["socket"])
        client.shutdown(socket.SHUT_RDWR)
    # Servicing a later request proves the driver survived its error response to the closed peer.
    identifier = lane.plan("after client disconnected without a request")
    assert lane.send(identifier)["state"] == "pending"
    eventually(lambda: lane.raw()["turns"] == 2)
    assert lane.send(identifier)["state"] == "acknowledged"
    after = lane.driver.inspect(lane.root, "coder")
    assert after["worker"] == before["worker"] and after["session_id"] == before["session_id"]
    assert lane.child.poll() is None and lane.thread.is_alive() and not lane.errors
    assert len(lane.delivered(identifier)) == 1


@pytest.mark.parametrize("agent", ["claude", "agy"])
def test_disconnect_after_request_preserves_pending_input_without_replay(native_lane, agent):
    lane = native_lane(agent)
    before = lane.driver.inspect(lane.root, "coder")
    identifier = lane.plan("WAIT_ACK")
    state = lane.raw()
    request = {"token": state["token"], "message_id": identifier, "thread": THREAD}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(state["socket"])
        # Close the receive half BEFORE sending, so even an immediate response has no reader.
        client.shutdown(socket.SHUT_RD)
        client.sendall(json.dumps(request).encode() + b"\n")
        client.shutdown(socket.SHUT_WR)
    eventually(lambda: any(row["text"] == "WAIT_ACK" for row in lane.received()))
    # The request reached the native process but has no native acknowledgment yet. A retry
    # must report that uncertainty without restarting the process or sending a second input.
    assert lane.send(identifier)["state"] == "pending"
    assert lane.send(identifier)["state"] == "pending"
    assert not lane.delivered(identifier)
    assert [row["text"] for row in lane.received()].count("WAIT_ACK") == 1
    (lane.folder / "allow-ack").touch()
    eventually(lambda: lane.raw()["turns"] == 2)
    assert lane.send(identifier)["state"] == "acknowledged"
    assert len(lane.delivered(identifier)) == 1
    later = lane.plan("new message after lost response")
    assert lane.send(later)["state"] == "pending"
    eventually(lambda: lane.raw()["turns"] == 3)
    assert lane.send(later)["state"] == "acknowledged"
    after = lane.driver.inspect(lane.root, "coder")
    assert after["worker"] == before["worker"] and after["session_id"] == before["session_id"]
    assert [row["text"] for row in lane.received()].count("WAIT_ACK") == 1
    assert lane.child.poll() is None and lane.thread.is_alive() and not lane.errors


def test_interrupt_needs_native_terminal_result_not_review_paperwork(native_lane):
    lane = native_lane("claude")
    identifier = lane.plan("ACTIVE_ACK_ONLY")
    lane.send(identifier)
    eventually(lambda: lane.raw()["deliveries"][identifier]["state"] == "acknowledged")
    before = lane.raw()
    receipt = lane.driver.interrupt(lane.root, "coder")
    assert receipt["state"] == "requested"
    eventually(lambda: lane.raw()["interruption"]["state"] == "acknowledged")
    next_input = lane.plan("Continue with retained context")
    assert lane.send(next_input)["state"] == "busy"
    assert lane.driver.interrupt(lane.root, "coder")["request_id"] == receipt["request_id"]
    assert len((lane.folder / "controls.jsonl").read_text().splitlines()) == 1
    (lane.folder / "allow-result").touch()
    eventually(lambda: lane.raw()["phase"] == "idle")
    assert lane.raw()["last_turn_outcome"] == "interrupted"
    assert lane.raw()["worker"] == before["worker"] and lane.raw()["session_id"] == THREAD
    assert lane.send(next_input)["state"] == "pending"
    eventually(lambda: lane.raw()["turns"] == 3)
    assert lane.child.poll() is None and not lane.errors


def test_acknowledged_interrupt_does_not_launder_native_error(native_lane):
    lane = native_lane("claude")
    identifier = lane.plan("ACTIVE_ERROR")
    lane.send(identifier)
    eventually(lambda: lane.raw()["deliveries"][identifier]["state"] == "acknowledged")
    lane.driver.interrupt(lane.root, "coder")
    eventually(lambda: lane.raw()["phase"] == "failed")
    assert lane.raw()["last_turn_outcome"] == "failed"
    assert lane.raw()["interruption"]["state"] == "failed"
    assert lane.child.poll() is None and not lane.errors


def test_unsupported_and_stale_interrupt_never_reaches_native_stdin(native_lane):
    lane = native_lane("agy")
    identifier = lane.plan("ACTIVE")
    lane.send(identifier)
    eventually(lambda: lane.raw()["deliveries"][identifier]["state"] == "acknowledged")
    with pytest.raises(ValueError, match="interruptible"):
        lane.driver.interrupt(lane.root, "coder")
    with pytest.raises(ValueError, match="different native input"):
        lane.driver.request(lane.root, "coder", "", THREAD, action="interrupt", input_id="stale")
    assert not (lane.folder / "controls.jsonl").exists()
    assert lane.raw()["phase"] == "working" and not lane.errors


def test_natural_completion_race_waits_for_interrupt_receipt(native_lane):
    lane = native_lane("claude")
    message = lane.plan("ACTIVE_COMPLETED_FIRST")
    lane.send(message)
    eventually(lambda: lane.raw()["deliveries"][message]["state"] == "acknowledged")
    lane.driver.interrupt(lane.root, "coder")
    eventually(lambda: lane.raw()["phase"] == "idle")
    assert lane.raw()["last_turn_outcome"] == "completed"
    assert lane.raw()["interruption"]["state"] == "requested"
    (lane.folder / "allow-control-ack").touch()
    eventually(lambda: lane.raw()["interruption"]["state"] == "completed")
    assert lane.child.poll() is None and not lane.errors


def test_stale_terminal_result_does_not_create_review_boundary(native_lane):
    lane = native_lane("claude")
    message = lane.plan("ACTIVE_STALE")
    lane.send(message)
    eventually(lambda: lane.raw()["deliveries"][message]["state"] == "acknowledged")
    lane.driver.interrupt(lane.root, "coder")
    eventually(lambda: lane.errors)
    assert "does not bind" in str(lane.errors[0])
    assert lane.raw()["phase"] == "failed"
    assert lane.raw()["interruption"]["state"] != "interrupted"


def test_natural_completion_without_interrupt_ack_cannot_accept_next_input(native_lane):
    lane = native_lane("claude")
    message = lane.plan("ACTIVE_COMPLETED_FIRST")
    lane.send(message)
    eventually(lambda: lane.raw()["deliveries"][message]["state"] == "acknowledged")
    receipt = lane.driver.interrupt(lane.root, "coder")
    eventually(lambda: lane.raw()["phase"] == "idle")
    assert lane.raw()["interruption"]["state"] == "requested"
    following = lane.plan("Continue only after acknowledgment")
    with pytest.raises(ValueError, match="interruption boundary remains unverified"):
        lane.send(following)
    (lane.folder / "allow-control-ack").touch()
    eventually(lambda: lane.raw()["interruption"]["state"] == "completed")
    assert lane.driver.interrupt(lane.root, "coder")["request_id"] == receipt["request_id"]
    assert len((lane.folder / "controls.jsonl").read_text().splitlines()) == 1
    assert lane.send(following)["state"] == "pending"
    eventually(lambda: lane.raw()["turns"] == 3)


def test_lost_interrupt_reply_reconciles_without_replay_or_supervisor(native_lane):
    lane = native_lane("claude")
    message = lane.plan("ACTIVE")
    lane.send(message)
    eventually(lambda: lane.raw()["deliveries"][message]["state"] == "acknowledged")
    before = lane.raw()
    current = lane.driver.inspect(lane.root, "coder")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(current["socket"])
        client.sendall(
            json.dumps(
                {
                    "token": current["token"],
                    "thread": THREAD,
                    "action": "interrupt",
                    "input_id": current["current_input_id"],
                }
            ).encode()
            + b"\n"
        )
    eventually(lambda: lane.raw()["phase"] == "idle")
    receipt = lane.driver.interrupt(lane.root, "coder")
    assert receipt["state"] == "interrupted"
    assert len((lane.folder / "controls.jsonl").read_text().splitlines()) == 1
    assert lane.raw()["worker"] == before["worker"] and lane.raw()["session_id"] == THREAD
    following = lane.plan("Continue in the same conversation")
    assert lane.send(following)["state"] == "pending"
    eventually(lambda: lane.raw()["turns"] == 3)


@pytest.mark.parametrize("damage", ["partial-runner-evidence", "mismatched-lane"])
def test_retry_refuses_partial_or_mismatched_binding_without_input(native_lane, damage):
    # Python protocol fixture only: no CLI model, account, or tmux server is used.
    lane = native_lane("claude")
    identifier = lane.plan("retry once with original context")
    before = lane.raw()
    receipt = lane.folder / "coder-process.json"
    state = lane.folder / "coder-persistent.json"
    if damage == "partial-runner-evidence":
        receipt.unlink()
    else:
        lane.driver.store(state, {**before, "slot": "tester"})
    try:
        with pytest.raises((ValueError, FileNotFoundError)):
            lane.send(identifier)
        assert lane.received() == [{"text": "initial", "pid": lane.child.pid}]
        assert lane.child.poll() is None and not lane.delivered(identifier)
    finally:
        lane.driver.store(receipt, lane.receipt)
        lane.driver.store(state, before)
    assert lane.send(identifier)["state"] == "pending"
    eventually(lambda: lane.raw().get("turns") == 2)
    assert lane.send(identifier)["state"] == "acknowledged"
    assert [row["text"] for row in lane.received()].count("retry once with original context") == 1
    assert lane.raw()["worker"] == before["worker"]
