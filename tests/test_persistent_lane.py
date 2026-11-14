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
emit({"event": "init", "conversation_id": thread})
active = None
step_index = -1
for line in sys.stdin:
    step_index += 1
    row = json.loads(line)
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
        emit({"event": "step_update", "step_update": {"conversation_id": session,
              "step_type": "user_input", "state": "DONE", "step_index": step_index}})
    if text.startswith("ACTIVE"):
        active = row
        continue
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
                authority="task-specification" if kind == "progress-review" else "runtime-protocol",
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


@pytest.mark.parametrize("agent", ["agy"])
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


@pytest.mark.parametrize("agent", ["agy"])
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
    lane = native_lane("agy")
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
    lane = native_lane("agy")
    identifier = lane.plan("EXIT")
    assert lane.send(identifier)["state"] == "pending"
    lane.child.wait(timeout=5)
    another = lane.plan("never send to a shell")
    with pytest.raises(ValueError, match="exited|stale"):
        lane.send(another)
    assert [row["text"] for row in lane.received()] == ["initial", "EXIT"]


@pytest.mark.parametrize("agent", ["agy"])
def test_native_session_change_is_failure_without_delivery_ack(native_lane, agent):
    lane = native_lane(agent)
    identifier = lane.plan("BAD_SESSION")
    assert lane.send(identifier)["state"] == "pending"
    eventually(lambda: lane.errors)
    assert "conversation changed" in str(lane.errors[0])
    assert lane.raw()["phase"] == "failed"
    assert lane.raw()["deliveries"][identifier]["state"] == "pending"


@pytest.mark.parametrize("agent", ["agy"])
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
    lane = native_lane("agy")
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


def test_foreign_live_worker_receipt_cannot_bind_a_different_native_child(native_lane):
    first = native_lane("agy")
    second = native_lane("agy", name="other-run", receipt_changes=first.receipt, expect_error=True)
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


@pytest.mark.parametrize("agent", ["agy"])
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


@pytest.mark.parametrize("agent", ["agy"])
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


@pytest.mark.parametrize("damage", ["partial-runner-evidence", "mismatched-lane"])
def test_retry_refuses_partial_or_mismatched_binding_without_input(native_lane, damage):
    # Python protocol fixture only: no CLI model, account, or tmux server is used.
    lane = native_lane("agy")
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


# Oracle: docs/HARNESS.md interactive Claude contract. These tests run the real
# wrapper, terminal, hook commands, receipt store and message transport. The fake
# executable only supplies native lifecycle reports, not Factory acknowledgments.
CLAUDE_TERMINAL = r"""
import json, os, pathlib, subprocess, sys, tty
args = sys.argv[1:]
folder = pathlib.Path(__file__).parent
settings = json.loads(pathlib.Path(args[args.index('--settings') + 1]).read_text())
flag = '--resume' if '--resume' in args else '--session-id'
session = args[args.index(flag) + 1]
assert os.isatty(0) and os.isatty(1)
assert '-p' not in args and '--print' not in args
assert '--input-format' not in args and '--output-format' not in args
(folder / 'argv.json').write_text(json.dumps(args))
def hook(event, **fields):
    payload = dict(hook_event_name=event, session_id=session, cwd=os.getcwd(), **fields)
    for group in settings['hooks'][event]:
        for entry in group['hooks']:
            subprocess.run(entry['command'], shell=True, input=json.dumps(payload),
                           text=True, check=True, timeout=5)
tty.setraw(0)
hook('SessionStart')
buffer = b''
while True:
    byte = os.read(0, 1)
    if not byte: break
    buffer += byte
    if not buffer.endswith(b'\r'): continue
    text = buffer[:-1].decode().replace('\x1b[200~', '').replace('\x1b[201~', '')
    buffer = b''
    with (folder / 'received.jsonl').open('a') as out:
        out.write(json.dumps(dict(text=text, pid=os.getpid())) + '\n')
    if 'EXIT' in text: break
    if 'WAIT_REPORT' in text:
        import time
        while not (folder / 'allow-report').exists(): time.sleep(.01)
    hook('UserPromptSubmit', prompt=text)
    if 'NO_STOP' not in text: hook('Stop', stop_hook_active=False)
hook('SessionEnd')
"""


@pytest.fixture
def claude_terminal(tmp_path, monkeypatch, request):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "harness"))
    driver = importlib.import_module("persistent_lane")
    dialogue = importlib.import_module("lane_dialogue")
    slot = getattr(request, "param", "coder")
    root = tmp_path / "native"
    folder = root / "tmux-lanes" if slot == "coder" else root
    folder.mkdir(parents=True)
    (root / "harness.json").write_text(json.dumps({"run_id": "r", "tmux_socket": "fixture"}))
    executable = folder / "cl"
    executable.write_text(f"#!{sys.executable}\n" + CLAUDE_TERMINAL)
    executable.chmod(0o755)
    prompt = root / "prompt"
    prompt.write_text("initial")
    harness = Path(__file__).resolve().parents[1] / "harness"
    output = (root / "wrapper-output").open("w")
    child = subprocess.Popen(
        [
            sys.executable,
            str(harness / "lane_agent.py"),
            "run",
            "--slot",
            slot,
            "--log",
            str(folder / f"{slot}-log"),
            "--exits",
            str(folder / f"{slot}-exits.jsonl"),
            "--prompt-arg",
            str(prompt),
            "--session-agent",
            "claude",
            "--session-file",
            str(folder / f"{slot}-thread-id"),
            "--persistent",
            "--run-root",
            str(root),
            "--",
            str(executable),
        ],
        stdout=output,
        stderr=subprocess.STDOUT,
    )

    class Terminal:
        def __init__(self):
            self.root, self.folder, self.driver, self.child = root, folder, driver, child

        def raw(self):
            path = folder / f"{slot}-persistent.json"
            return json.loads(path.read_text()) if path.exists() else {}

        def received(self):
            path = folder / "received.jsonl"
            return (
                [json.loads(row) for row in path.read_text().splitlines()] if path.exists() else []
            )

        def plan(self, text, kind="status-probe"):
            return dialogue.plan_message(
                root,
                sender="validator",
                lane="coder",
                message_kind=kind,
                text=text,
                basis="interactive contract regression",
                authority="task-specification" if kind == "progress-review" else "runtime-protocol",
            )["message_id"]

        def send(self, message, thread=None, **kwargs):
            return driver.request(root, slot, message, thread or self.raw()["session_id"], **kwargs)

    lane = Terminal()
    try:
        eventually(lambda: lane.raw().get("phase") == "stop-reported")
        yield lane
    finally:
        (folder / "allow-report").touch()
        if child.poll() is None:
            # Signal only our owned wrapper, which owns terminal child cleanup.
            child.terminate()
        child.wait(timeout=5)
        output.close()


def test_claude_prompt_observation_is_not_completion_and_native_queue_remains_available(
    claude_terminal,
):
    lane = claude_terminal
    before = lane.raw()
    first = lane.plan("NO_STOP work remains active")
    lane.send(first)
    eventually(lambda: lane.send(first)["state"] == "observed")
    assert lane.raw()["phase"] == "prompt-observed"
    second = lane.plan("NO_STOP FACTORY_REVIEW verdict=REDIRECT\nChange course.", "progress-review")
    lane.send(second)
    eventually(lambda: lane.send(second)["state"] == "observed")
    assert lane.raw()["phase"] == "prompt-observed"
    assert lane.raw()["worker"] == before["worker"]
    assert lane.raw()["session_id"] == before["session_id"]
    assert len(lane.received()) == 3
    with pytest.raises(ValueError):
        lane.driver.interrupt(lane.root, "coder")


def test_claude_retry_never_retypes_unobserved_or_observed_input(claude_terminal):
    lane = claude_terminal
    marker = lane.root / "must-not-execute"
    message = lane.plan(f"WAIT_REPORT literal $(touch {marker})")
    state = lane.raw()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(state["socket"])
        client.shutdown(socket.SHUT_RD)
        client.sendall(
            json.dumps(
                {"token": state["token"], "message_id": message, "thread": state["session_id"]}
            ).encode()
            + b"\n"
        )
        client.shutdown(socket.SHUT_WR)
    eventually(lambda: len(lane.received()) == 2)
    for _ in range(2):
        assert lane.send(message)["state"] == "submitted"
    assert lane.send(lane.plan("cannot overtake unresolved custody"))["state"] == "busy"
    assert len(lane.received()) == 2
    (lane.folder / "allow-report").touch()
    eventually(lambda: lane.send(message)["state"] == "observed")
    assert lane.send(message)["state"] == "observed"
    assert len(lane.received()) == 2
    assert not marker.exists()


def test_claude_wrong_session_and_stale_worker_refuse_input(claude_terminal):
    lane = claude_terminal
    message = lane.plan("not for a foreign session")
    before = lane.received()
    with pytest.raises(ValueError):
        lane.send(message, thread="wrong-conversation")
    path = lane.folder / "coder-process.json"
    receipt = json.loads(path.read_text())
    altered = {**receipt, "worker": {**receipt["worker"], "started": "stale"}}
    lane.driver.store(path, altered)
    try:
        with pytest.raises(ValueError):
            lane.send(message)
        assert lane.received() == before
    finally:
        lane.driver.store(path, receipt)
    lane.send(message)
    eventually(lambda: lane.send(message)["state"] == "observed")
