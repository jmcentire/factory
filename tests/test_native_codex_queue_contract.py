"""Real wrapper/hooks/provider-queue seam with a synthetic Codex executable.

No model runs. Oracle: the hosted interactive contract and public Codex hook
payloads. A queue reply is not receipt; unknown may have enqueued already.
"""

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_persistent_lane import eventually

MAIN = "12345678-1234-4234-8234-123456789abc"
BACKGROUND = "99999999-9999-4999-8999-999999999999"

CODEX = r"""
import json, os, pathlib, select, subprocess, sys, time, tty
folder = pathlib.Path(__file__).parent
args = sys.argv[1:]
if args and args[0] == 'queue':
    item = dict(thread=args[args.index('--thread') + 1], message=args[args.index('--message') + 1])
    state = json.loads((folder / 'coder-persistent.json').read_text())
    assert any(row['state'] == 'submitted' and row.get('via') == 'provider-queue'
               and '[factory-message:' + row['token'] + ']' in item['message']
               for row in state['deliveries'].values())
    with (folder / 'queues.jsonl').open('a') as out: out.write(json.dumps(item) + '\n')
    if (folder / 'timeout-queue').exists(): time.sleep(30)
    print('Queued message for session', item['thread'], flush=True)
    raise SystemExit(0)
assert os.isatty(0) and os.isatty(1)
assert 'exec' not in args and '--no-daemon' in args and '--no-alt-screen' in args
prompt = args[args.index('--') + 1]
json.loads((folder / 'coder-launch-intent.json').read_text())
settings = json.loads((pathlib.Path.cwd() / '.codex/hooks.json').read_text())
main = '12345678-1234-4234-8234-123456789abc'
background = '99999999-9999-4999-8999-999999999999'
def event_entries(node, event):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == event: yield value
            else: yield from event_entries(value, event)
    elif isinstance(node, list):
        for item in node: yield from event_entries(item, event)
def commands(node):
    if isinstance(node, dict):
        if node.get('type') == 'command': yield node['command']
        else:
            for value in node.values(): yield from commands(value)
    elif isinstance(node, list):
        for item in node: yield from commands(item)
def report(event, **fields):
    payload = dict(hook_event_name=event, session_id=main, model='synthetic-model',
                   permission_mode='default')
    payload.update(fields)
    executable_commands = [cmd for node in event_entries(settings, event) for cmd in commands(node)]
    assert executable_commands, event
    for cmd in executable_commands:
        subprocess.run(cmd, shell=True, input=json.dumps(payload), text=True, check=True, timeout=5)
tty.setraw(0)
report('SessionStart', session_id=background, source='startup')
report('SessionStart', source='startup')
report('UserPromptSubmit', turn_id='turn-initial', prompt=prompt)
report('Stop', turn_id='turn-initial', stop_hook_active=False,
       last_assistant_message='initial reply')
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
report('SessionEnd', reason='fixture-exit')
"""

WRAPPER = r"""
import pathlib, subprocess, sys
sys.path.insert(0, sys.argv[1])
import lane_agent
root = pathlib.Path(sys.argv[2])
folder = root / 'tmux-lanes'
run = subprocess.run
def bounded_queue(argv, *args, **kwargs):
    # Fault seam: the real queue subprocess accepts the message then exceeds its
    # caller's deadline. No response/receipt is manufactured by this wrapper.
    if len(argv) > 1 and argv[1] == 'queue' and (folder / 'timeout-queue').exists():
        kwargs['timeout'] = .15
    return run(argv, *args, **kwargs)
subprocess.run = bounded_queue
raise SystemExit(lane_agent.run_lane(
    [sys.argv[3], '--no-daemon', '--no-alt-screen'], slot='coder',
    log=folder/'coder-log', exits=folder/'coder-exits.jsonl',
    prompt=root/'prompt', session_agent='codex-interactive',
    session_file=folder/'coder-thread-id', persistent=True, run_root=root))
"""


@pytest.fixture
def codex_native(tmp_path, monkeypatch):
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
    executable = folder / "codex"
    executable.write_text(f"#!{sys.executable}\n" + CODEX)
    executable.chmod(0o755)
    (root / "prompt").write_text("initial literal native message")
    home = tmp_path / "operator"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "CODEX_HOME": str(home / ".codex"),
        "PATH": str(folder) + os.pathsep + os.environ["PATH"],
    }
    output = (root / "wrapper-output").open("w")
    child = subprocess.Popen(
        [sys.executable, "-c", WRAPPER, str(harness), str(root), str(executable)],
        cwd=project,
        env=env,
        stdout=output,
        stderr=subprocess.STDOUT,
    )

    class Lane:
        def __init__(self):
            self.root, self.folder, self.driver = root, folder, driver

        def raw(self):
            path = folder / "coder-persistent.json"
            return json.loads(path.read_text()) if path.exists() else {}

        def plan(self, text):
            return dialogue.plan_message(
                root,
                sender="validator",
                lane="coder",
                message_kind="status-probe",
                text=text,
                basis="provider queue regression",
                authority="runtime-protocol",
            )["message_id"]

        def send(self, message):
            return driver.request(root, "coder", message, self.raw()["session_id"])

        def queues(self):
            path = folder / "queues.jsonl"
            return (
                [json.loads(row) for row in path.read_text().splitlines()] if path.exists() else []
            )

        def reports(self, *rows):
            with (folder / "reports.jsonl").open("a") as out:
                for row in rows:
                    out.write(json.dumps(row) + "\n")

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


def test_codex_background_start_does_not_steal_binding_or_disable_provider_queue(codex_native):
    lane = codex_native
    before = lane.raw()
    assert before["session_id"] == MAIN
    assert before["deliveries"]["initial"]["state"] == "observed"
    lane.reports({"event": "SessionStart", "session_id": BACKGROUND, "source": "background"})
    # Wait for ingestion, without a primary Stop that could mask the bug by
    # making the primary session ready again.
    eventually(
        lambda: (
            lane.raw().get("unbound_events", 0) > before.get("unbound_events", 0)
            or (lane.raw()["last_native_event"]["event"] == "SessionStart"
                and lane.raw()["last_native_event"]["at"] > before["last_native_event"]["at"])
        )
    )
    message = lane.plan("later message must reach this retained session")
    lane.send(message)
    eventually(lambda: lane.queues())
    eventually(lambda: lane.send(message)["state"] == "submitted")
    [queued] = lane.queues()
    assert queued["thread"] == MAIN
    assert lane.raw()["session_id"] == MAIN and lane.raw()["worker"] == before["worker"]
    assert not (lane.folder / "typed.bin").exists()
    # The queue's successful answer is not a prompt report.
    assert lane.send(message)["state"] == "submitted"
    lane.reports(
        {"event": "UserPromptSubmit", "turn_id": "turn-later", "prompt": queued["message"]}
    )
    eventually(lambda: lane.send(message)["state"] == "observed")
    assert len(lane.queues()) == 1


def test_codex_timeout_can_reconcile_only_exact_late_prompt_without_requeue(codex_native):
    lane = codex_native
    (lane.folder / "timeout-queue").touch()
    message = lane.plan("accepted by provider before its queue client timed out")
    lane.send(message)
    eventually(lambda: lane.queues())
    eventually(lambda: lane.send(message)["state"] == "unknown")
    [queued] = lane.queues()
    assert queued["thread"] == MAIN
    assert lane.send(message)["state"] == "unknown"
    mark = queued["message"][queued["message"].index("[factory-message:") :]
    stops = lane.raw()["stop_reports"]
    lane.reports(
        {"event": "UserPromptSubmit", "turn_id": "turn-wrong", "prompt": "wrong body " + mark},
        {
            "event": "Stop",
            "turn_id": "turn-wrong",
            "stop_hook_active": False,
            "last_assistant_message": "not the original queued message",
        },
    )
    eventually(lambda: lane.raw()["stop_reports"] == stops + 1)
    assert lane.send(message)["state"] == "unknown"
    lane.reports({"event": "UserPromptSubmit", "turn_id": "turn-late", "prompt": queued["message"]})
    eventually(lambda: lane.send(message)["state"] == "observed")
    assert len(lane.queues()) == 1
    assert not (lane.folder / "typed.bin").exists()


def test_codex_successful_queue_is_not_receipt_and_never_types_or_requeues(codex_native):
    lane = codex_native
    marker = lane.root / "must-not-execute"
    message = lane.plan(f"literal $(touch {marker}) belongs to provider queue")
    lane.send(message)
    eventually(lambda: lane.send(message)["state"] == "submitted")
    [queued] = lane.queues()
    assert queued["thread"] == MAIN
    assert lane.send(message)["state"] == "submitted"
    assert len(lane.queues()) == 1
    lane.reports(
        {"event": "UserPromptSubmit", "turn_id": "turn-positive", "prompt": queued["message"]}
    )
    eventually(lambda: lane.send(message)["state"] == "observed")
    assert lane.send(message)["state"] == "observed"
    assert len(lane.queues()) == 1 and not (lane.folder / "typed.bin").exists()
    assert not marker.exists()
