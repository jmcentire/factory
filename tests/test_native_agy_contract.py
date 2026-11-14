"""Synthetic AGY executable exercises real terminal/hooks/custody, never a model.

Oracle: docs/HARNESS.md and the independently observed native hook schema. Prompt
reports are observations, Stop is not readiness, and launch intent forbids replay.
"""

import importlib
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_persistent_lane import eventually

AGY = r"""
import json, os, pathlib, select, subprocess, sys, tty
folder = pathlib.Path(__file__).parent
args = sys.argv[1:]
assert os.isatty(0) and os.isatty(1)
assert not {'--print', '--input-format', '--output-format'}.intersection(args)
prompt = args[args.index('--prompt-interactive') + 1]
# The launch argument is already handed over: intent must predate this process.
json.loads((folder / 'coder-launch-intent.json').read_text())
with (folder / 'starts.jsonl').open('a') as out:
    out.write(json.dumps(dict(argv=args, pid=os.getpid(), prompt=prompt)) + '\n')
hooks = json.loads((pathlib.Path.cwd() / '.agents/plugins/lane/hooks.json').read_text())['lane']
session = '12345678-1234-4234-8234-123456789abc'
def report(event, **fields):
    payload = dict(hookEventName=event, conversationId=session, **fields)
    for hook in hooks[event]:
        subprocess.run(hook['command'], shell=True, input=json.dumps(payload),
                       text=True, check=True, timeout=5)
tty.setraw(0)
report('SessionStart')
if not (folder / 'withhold-prompt').exists():
    # PreInvocation can repeat the latest user text before another model call.
    for execution in (1, 2):
        report('PreInvocation', lastUserInput=prompt, executionNum=execution)
report('Stop', fullyIdle=True, continuationActive=False,
       terminationReason='NO_TOOL_CALL', executionNum=2)
processed = 0
while not (folder / 'exit').exists():
    if select.select([0], [], [], .02)[0]:
        data = os.read(0, 4096)
        if not data: break
        with (folder / 'typed.bin').open('ab') as out: out.write(data)
    requests = folder / 'reports.jsonl'
    rows = requests.read_text().splitlines() if requests.exists() else []
    for row in rows[processed:]:
        payload = json.loads(row)
        event = payload.pop('event')
        report(event, **payload)
    processed = len(rows)
"""


@pytest.fixture
def agy_native(tmp_path, monkeypatch, request):
    harness = Path(__file__).resolve().parents[1] / "harness"
    monkeypatch.syspath_prepend(str(harness))
    driver = importlib.import_module("persistent_lane")
    dialogue = importlib.import_module("lane_dialogue")
    root = tmp_path / "run"
    folder = root / "tmux-lanes"
    folder.mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    executable = folder / "agy"
    executable.write_text(f"#!{sys.executable}\n" + AGY)
    executable.chmod(0o755)
    prompt = root / "prompt"
    marker = tmp_path / "must-not-execute"
    initial = f"literal $(touch {marker}); 'quotes' and newline\nretained context"
    prompt.write_text(initial)
    if getattr(request, "param", "") == "withhold-prompt":
        (folder / "withhold-prompt").touch()
    command = [
        sys.executable,
        str(harness / "lane_agent.py"),
        "run",
        "--slot",
        "coder",
        "--log",
        str(folder / "coder-log"),
        "--exits",
        str(folder / "coder-exits.jsonl"),
        "--prompt-arg",
        str(prompt),
        "--session-agent",
        "agy",
        "--session-file",
        str(folder / "coder-thread-id"),
        "--persistent",
        "--run-root",
        str(root),
        "--",
        str(executable),
    ]
    output = (root / "wrapper-output").open("w")
    child = subprocess.Popen(command, cwd=project, stdout=output, stderr=subprocess.STDOUT)

    class Lane:
        def __init__(self):
            self.root, self.folder, self.driver = root, folder, driver
            self.child, self.command, self.project = child, command, project
            self.initial, self.marker = initial, marker

        def raw(self):
            path = folder / "coder-persistent.json"
            return json.loads(path.read_text()) if path.exists() else {}

        def plan(self, text, kind="status-probe"):
            return dialogue.plan_message(
                root,
                sender="validator",
                lane="coder",
                message_kind=kind,
                text=text,
                basis="native AGY custody regression",
                authority="runtime-protocol" if kind == "status-probe" else "task-specification",
            )["message_id"]

        def send(self, message, thread=None):
            return driver.request(root, "coder", message, thread or self.raw()["session_id"])

        def reports(self, *rows):
            with (folder / "reports.jsonl").open("a") as stream:
                for row in rows:
                    stream.write(json.dumps(row) + "\n")

    lane = Lane()
    try:
        eventually(lambda: lane.raw().get("phase") == "stop-reported")
        yield lane
    finally:
        (folder / "exit").touch()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.terminate()
            child.wait(timeout=5)
        output.close()


def test_agy_native_launch_observation_deduplicates_prompt_without_claiming_completion(agy_native):
    lane = agy_native
    state = lane.raw()
    [start] = [json.loads(row) for row in (lane.folder / "starts.jsonl").read_text().splitlines()]
    assert start["prompt"].split("[factory-message:", 1)[0].rstrip() == lane.initial
    assert state["deliveries"]["initial"]["state"] == "observed"
    assert state["prompt_reports"] == 1 and state["foreign_inputs"] == 0
    assert "[factory-message:" in start["prompt"]
    assert state["worker"]["pid"] == start["pid"]
    assert not lane.marker.exists()
    assert not (lane.folder / "typed.bin").exists()
    assert (lane.folder / "coder-launch-intent.json").exists()


def test_agy_stop_cannot_release_followup_or_convert_it_to_observed(agy_native):
    lane = agy_native
    before = lane.raw()
    message = lane.plan("FACTORY_REVIEW verdict=REDIRECT\nChange course.", "progress-review")
    first = lane.send(message)
    assert first["state"] == "queued" and first.get("held")
    assert lane.send(message)["state"] == "queued"
    lane.reports(
        {
            "event": "Stop",
            "fullyIdle": True,
            "continuationActive": True,
            "terminationReason": "CUSTOM_HOOK",
            "executionNum": 3,
        }
    )
    eventually(lambda: lane.raw()["stop_reports"] == before["stop_reports"] + 1)
    assert lane.send(message)["state"] == "queued"
    assert lane.raw()["worker"] == before["worker"]
    assert lane.raw()["session_id"] == before["session_id"]
    assert not (lane.folder / "typed.bin").exists()
    with pytest.raises(ValueError):
        lane.driver.interrupt(lane.root, "coder")


@pytest.mark.parametrize("damage", ["stale", "missing", "wrong-slot"])
def test_agy_lost_reply_and_wrong_identity_never_type_or_replay(agy_native, damage):
    lane = agy_native
    message = lane.plan("retained follow-up, not a permission response")
    state = lane.raw()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(state["socket"])
        client.shutdown(socket.SHUT_RD)
        client.sendall(
            json.dumps(
                dict(token=state["token"], thread=state["session_id"], message_id=message)
            ).encode()
            + b"\n"
        )
        client.shutdown(socket.SHUT_WR)
    eventually(lambda: lane.send(message)["state"] == "queued")
    with pytest.raises(ValueError):
        lane.send(message, thread="wrong-session")
    receipt_path = lane.folder / "coder-process.json"
    receipt = json.loads(receipt_path.read_text())
    if damage == "missing":
        receipt_path.unlink()
    elif damage == "wrong-slot":
        lane.driver.store(receipt_path, {**receipt, "slot": "tester"})
    else:
        lane.driver.store(
            receipt_path, {**receipt, "worker": {**receipt["worker"], "started": "stale"}}
        )
    try:
        with pytest.raises((ValueError, FileNotFoundError)):
            lane.send(message)
    finally:
        lane.driver.store(receipt_path, receipt)
    assert lane.send(message)["state"] == "queued"
    assert not (lane.folder / "typed.bin").exists()


@pytest.mark.parametrize("agy_native", ["", "withhold-prompt"], indirect=True)
def test_agy_exited_native_child_and_restart_cannot_replay_launch(agy_native):
    lane = agy_native
    expected = "submitted" if (lane.folder / "withhold-prompt").exists() else "observed"
    assert lane.raw()["deliveries"]["initial"]["state"] == expected
    (lane.folder / "exit").touch()
    lane.child.wait(timeout=5)
    with pytest.raises(ValueError):
        lane.send(lane.plan("must not reach an ended process"))
    result = subprocess.run(
        lane.command, cwd=lane.project, capture_output=True, text=True, timeout=8
    )
    assert result.returncode != 0
    assert len((lane.folder / "starts.jsonl").read_text().splitlines()) == 1
    assert (lane.folder / "coder-launch-intent.json").exists()
