"""Synthetic Muse/AGY manual receipt contract, never a model.

Oracle: public held-message promise in docs/HARNESS.md and owner-approved
exact-session receipt reconciliation. A receipt is not acceptance or completion.
"""

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_persistent_lane import eventually

NATIVE = r"""
import json, os, pathlib, select, subprocess, sys, tty
folder = pathlib.Path(__file__).parent
args = sys.argv[1:]
assert os.isatty(0) and os.isatty(1)
assert not {'--print', '--input-format', '--output-format'}.intersection(args)
is_agy = '--prompt-interactive' in args
prompt = args[args.index('--prompt-interactive') + 1] if is_agy else args[args.index('--') + 1]
# The launch argument is already handed over: intent must predate this process.
json.loads((folder / 'coder-launch-intent.json').read_text())
with (folder / 'starts.jsonl').open('a') as out:
    out.write(json.dumps(dict(argv=args, pid=os.getpid(), prompt=prompt)) + '\n')
hook_path = '.agents/plugins/lane/hooks.json' if is_agy else '.muse/hooks.json'
hooks = json.loads((pathlib.Path.cwd() / hook_path).read_text())
if is_agy: hooks = hooks['lane']
def commands(node):
    if isinstance(node, dict):
        if 'command' in node: yield node['command']
        else:
            for value in node.values(): yield from commands(value)
    elif isinstance(node, list):
        for item in node: yield from commands(item)
def event_nodes(node, event):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == event: yield value
            else: yield from event_nodes(value, event)
    elif isinstance(node, list):
        for item in node: yield from event_nodes(item, event)
session = '12345678-1234-4234-8234-123456789abc'
def report(event, **fields):
    target = fields.pop('target_session', session)
    if is_agy:
        payload = dict(hookEventName=event, conversationId=target, **fields)
    else:
        if event == 'PreInvocation':
            event = 'UserPromptSubmit'
            fields = dict(prompt=fields['lastUserInput'], turn_id=str(fields['executionNum']))
        payload = dict(hook_event_name=event, session_id=target, **fields)
    for node in event_nodes(hooks, event):
        for command in commands(node):
            subprocess.run(command, shell=True, input=json.dumps(payload),
                           text=True, check=True, timeout=5)
tty.setraw(0)
report('SessionStart')
if not (folder / 'withhold-prompt').exists():
    # PreInvocation can repeat the latest user text before another model call.
    for execution in ((1, 2) if is_agy else (1,)):
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


@pytest.fixture(params=["agy", "muse"])
def held_native(tmp_path, monkeypatch, request):
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
    executable = folder / request.param
    executable.write_text(f"#!{sys.executable}\n" + NATIVE)
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
        request.param,
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
                basis="native held-message receipt regression",
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



def test_exact_manual_receipt_releases_only_same_session_held_message(held_native):
    lane = held_native
    initial = lane.raw()
    text = "retained followup literal, not a new permission"
    message = lane.plan(text)
    held = lane.send(message)
    assert held["state"] == "queued" and held.get("held")
    marked = Path(held["enter_file"]).read_text()
    assert text in marked and marked != text
    # Raw words are ambiguous; only this retained item's marked entry binds.
    lane.reports({"event": "PreInvocation", "lastUserInput": text, "executionNum": 9})
    eventually(lambda: lane.raw().get("foreign_inputs", 0) > initial.get("foreign_inputs", 0))
    assert lane.send(message)["state"] == "queued"
    before_wrong = lane.raw().get("foreign_inputs", 0)
    lane.reports({"event": "PreInvocation", "lastUserInput": "wrong " + marked,
                  "executionNum": 10})
    eventually(lambda: lane.raw().get("foreign_inputs", 0) > before_wrong)
    assert lane.send(message)["state"] == "queued"
    lane.reports({"event": "PreInvocation", "lastUserInput": marked,
                  "executionNum": 11, "target_session": "99999999-9999-4999-8999-999999999999"})
    eventually(lambda: lane.raw().get("unbound_events", 0) > initial.get("unbound_events", 0))
    assert lane.send(message)["state"] == "queued"
    lane.reports({"event": "PreInvocation", "lastUserInput": marked, "executionNum": 12})
    eventually(lambda: lane.send(message)["state"] == "observed")
    assert lane.send(message)["state"] == "observed"
    second = lane.plan("second followup still requires native manual receipt")
    next_held = lane.send(second)
    assert next_held["state"] == "queued" and next_held.get("held")
    stops = lane.raw()["stop_reports"]
    lane.reports({"event": "PreInvocation", "lastUserInput": marked, "executionNum": 13},
                 {"event": "Stop", "fullyIdle": True, "continuationActive": False,
                  "terminationReason": "NO_TOOL_CALL", "executionNum": 13})
    eventually(lambda stops=stops: lane.raw()["stop_reports"] > stops)
    assert lane.send(second)["state"] == "queued"
    assert lane.raw()["session_id"] == initial["session_id"]
    assert lane.raw()["worker"] == initial["worker"]
    assert not (lane.folder / "typed.bin").exists()


def test_old_receipt_cannot_settle_new_identical_body_message(held_native):
    lane = held_native
    text = "identical body in distinct retained messages"
    first = lane.plan(text)
    first_held = lane.send(first)
    assert first_held["state"] == "queued"
    entry_a = Path(first_held["enter_file"]).read_text()
    lane.reports({"event": "PreInvocation", "lastUserInput": entry_a, "executionNum": 20})
    eventually(lambda: lane.send(first)["state"] == "observed")
    second = lane.plan(text, "progress-review")
    assert second != first
    second_held = lane.send(second)
    assert second_held["state"] == "queued"
    entry_b = Path(second_held["enter_file"]).read_text()
    assert entry_b != entry_a and text in entry_b
    # Duplicate native event and a later invocation carrying old marked text
    # cannot settle the new retained item, even though the underlying words match.
    for execution in (20, 21):
        stops = lane.raw()["stop_reports"]
        lane.reports({"event": "PreInvocation", "lastUserInput": entry_a,
                      "executionNum": execution},
                     {"event": "Stop", "fullyIdle": True, "continuationActive": False,
                      "terminationReason": "NO_TOOL_CALL", "executionNum": execution})
        eventually(lambda stops=stops: lane.raw()["stop_reports"] > stops)
        assert lane.send(second)["state"] == "queued"
    lane.reports({"event": "PreInvocation", "lastUserInput": entry_b, "executionNum": 22})
    eventually(lambda: lane.send(second)["state"] == "observed")
    assert lane.send(first)["state"] == "observed"
    assert not (lane.folder / "typed.bin").exists()
