"""Bootstrap driver regressions using owned native-protocol fixtures, never tmux seats."""

from __future__ import annotations

import importlib
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest


def eventually(function, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = function()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("owned native fixture did not reach expected state")


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
