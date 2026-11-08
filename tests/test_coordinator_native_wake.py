"""Owned protocol process proves coordinator receipt and unsafe-target refusal.

This is a native-protocol fixture, not a paid Claude/Agy integration claim. No tmux
or unrelated process is touched; actual PID/start receipts and sockets are exercised.
"""

import importlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from harness import run_record

THREAD = "12345678-1234-4234-8234-123456789abc"
NATIVE = r"""
import json, pathlib, sys, time
folder=pathlib.Path(sys.argv[1]); thread=sys.argv[2]
def emit(row): print(json.dumps(row),flush=True)
emit({'type':'system','subtype':'init','session_id':thread})
for line in sys.stdin:
    row=json.loads(line)
    with (folder/'received.jsonl').open('a') as out:out.write(json.dumps(row)+'\n')
    if row['message']['content']!='initial':
        while not (folder/'allow-ack').exists():time.sleep(.01)
    emit({'type':'user','uuid':row['uuid'],'session_id':thread,
          'message':row['message'],'parent_tool_use_id':None})
    if row['message']['content']!='initial':
        while not (folder/'allow-result').exists():time.sleep(.01)
    emit({'type':'result','session_id':thread,'is_error':False,'result':'done'})
"""


def eventually(function, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = function()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("owned protocol fixture did not reach expected state")


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "harness"))
    driver = importlib.import_module("persistent_lane")
    watchdog = importlib.import_module("lane_watchdog")
    wake = importlib.import_module("harness.inbox_wake")
    (tmp_path / "harness.json").write_text(json.dumps({"run_id": "r", "tmux_socket": "fixture"}))
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", NATIVE, str(tmp_path), THREAD],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    table = watchdog.process_snapshot()
    assert table is not None
    receipt = {
        "schema": "factory-worker-process/1",
        "slot": "validator",
        "status": "running",
        "worker": {k: table[child.pid][k] for k in ("pid", "started")},
        "wrapper": {k: table[os.getpid()][k] for k in ("pid", "started")},
    }
    driver.store(tmp_path / "validator-process.json", receipt)
    errors = []

    def serve():
        try:
            driver.run(
                child,
                root=tmp_path,
                slot="validator",
                agent="claude",
                prompt="initial",
                session_file=tmp_path / "validator-thread-id",
                emit=lambda _: None,
            )
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()

    def phase():
        path = tmp_path / "validator-persistent.json"
        return driver.read(path).get("phase") if path.exists() else None

    try:
        eventually(lambda: errors or phase() == "idle")
        assert not errors
        yield tmp_path, wake, driver, receipt, phase
    finally:
        (tmp_path / "allow-ack").touch()
        (tmp_path / "allow-result").touch()
        if child.poll() is None:
            child.stdin.close()
        child.wait(timeout=5)
        worker.join(timeout=5)
        assert not worker.is_alive()
        child.stdout.close()
        child.stderr.close()


def consumed(root):
    return json.loads((root / "inbox-wake/state.json").read_text())["recipients"]["validator"][
        "consumed"
    ]


def test_native_receipt_not_write_attempt_consumes_and_busy_session_defers_next_batch(native):
    root, wake, driver, receipt, phase = native
    run_record.message(root, "validator", "coder", "first complete")
    wake.tick(root)
    assert not consumed(root)
    (root / "allow-ack").touch()
    eventually(lambda: (wake.tick(root), consumed(root))[1])
    assert phase() != "idle"  # receipt is not completion of the agent's work
    run_record.message(root, "validator", "tester", "second complete")
    before = len((root / "received.jsonl").read_text().splitlines())
    wake.tick(root)
    assert len((root / "received.jsonl").read_text().splitlines()) == before
    (root / "allow-result").touch()
    eventually(lambda: phase() == "idle")
    eventually(lambda: (wake.tick(root), len(consumed(root)))[1] == 2)
    assert len((root / "received.jsonl").read_text().splitlines()) == before + 1


def test_mismatched_retained_process_cannot_receive_message(native):
    root, wake, driver, receipt, phase = native
    run_record.message(root, "validator", "host", "must not reach foreign process")
    changed = {**receipt, "worker": {**receipt["worker"], "started": "wrong-process-start"}}
    driver.store(root / "validator-process.json", changed)
    before = (root / "received.jsonl").read_bytes()
    try:
        wake.tick(root)
        assert not consumed(root)
        assert (root / "received.jsonl").read_bytes() == before
    finally:
        driver.store(root / "validator-process.json", receipt)
