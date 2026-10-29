"""Fixed-operation custody: synthetic identities plus real disposable socket clients."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import platform
import subprocess
import sys
import tempfile
import threading
import time

import pytest

from harness import lane_custody as custody

HOME = pathlib.Path(__file__).resolve().parents[1]


def plan_document(tmp_path, identity=None):
    script = HOME / "harness/lane_agent.py"
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    return {"schema": custody.SCHEMA,
            "seat": identity or {"pid": 999999, "started": "synthetic-start"},
            "factory_home": str(HOME), "python": sys.executable, "cwd": str(HOME),
            "operations": {"info": {"entrypoint": "agent-info",
                                    "args": ["describe", "--agent", "claude"],
                                    "timeout": 10,
                                    "source_sha256": digest}}}


def request(plan, identifier="request-1"):
    return {"request_id": identifier, "operation": "info", "plan_digest": plan.digest}


@pytest.mark.parametrize("change", [
    {"schema": "unknown"}, {"seat": {"pid": 1, "started": "x"}},
    {"operations": {}}, {"factory_home": "relative"}, {"unexpected": "override"},
])
def test_bad_host_plan_refused(tmp_path, change):
    with pytest.raises(custody.CustodyError):
        custody.Plan.parse({**plan_document(tmp_path), **change})


@pytest.mark.parametrize("change", [
    {"entrypoint": "raw-shell"}, {"entrypoint": []}, {"timeout": 0},
    {"timeout": 301}, {"args": "a shell string"}, {"source_sha256": "changed"},
])
def test_unknown_or_changed_operation_refused(tmp_path, change):
    document = plan_document(tmp_path)
    document["operations"]["info"].update(change)
    with pytest.raises(custody.CustodyError):
        custody.Plan.parse(document)


def test_host_will_not_execute_from_a_seat_writable_working_directory(tmp_path):
    with pytest.raises(custody.CustodyError, match="protected Factory checkout"):
        custody.Plan.parse({**plan_document(tmp_path), "cwd": str(tmp_path)})


def test_factory_module_is_loaded_with_isolated_python_from_pinned_root(tmp_path, monkeypatch):
    document = plan_document(tmp_path)
    source = HOME / "factory_runtime/cli.py"
    document["operations"]["help"] = {"entrypoint": "factory-cli", "args": ["--help"],
        "timeout": 10, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    plan = custody.Plan.parse(document)
    poison = tmp_path / "poison"
    poison.mkdir()
    (poison / "sitecustomize.py").write_text("raise RuntimeError('untrusted search path')")
    monkeypatch.setenv("PYTHONPATH", str(poison))
    ran = subprocess.run(plan.command("help"), cwd=tmp_path, capture_output=True, text=True,
                         timeout=10)
    assert ran.returncode == 0, ran.stderr
    assert "untrusted search path" not in ran.stderr


@pytest.mark.parametrize("change", [
    {"argv": ["/bin/sh", "-c", "echo injected"]}, {"cwd": "/tmp"},
    {"operation": "unregistered"}, {"plan_digest": "stale"},
    {"request_id": "../escaped"}, {"request_id": ""},
])
def test_client_cannot_supply_execution_parameters(tmp_path, change):
    plan = custody.Plan.parse(plan_document(tmp_path))
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    with pytest.raises(custody.CustodyError):
        custody.execute(plan, {**request(plan), **change}, receipts)
    assert list(receipts.iterdir()) == []


def test_uncertain_request_is_not_executed_again(tmp_path):
    plan = custody.Plan.parse(plan_document(tmp_path))
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / "request-1.started.json").write_text("{}")
    with pytest.raises(custody.CustodyError, match="uncertain"):
        custody.execute(plan, request(plan), receipts)
    assert not (receipts / "request-1.log").exists()


def test_new_request_id_does_not_evade_an_uncertain_operation(tmp_path):
    plan = custody.Plan.parse(plan_document(tmp_path))
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / "old.started.json").write_text(json.dumps({"request": request(plan, "old")}))
    with pytest.raises(custody.CustodyError, match="uncertain"):
        custody.execute(plan, request(plan, "new"), receipts)
    assert not (receipts / "new.started.json").exists()


def test_timeout_receipt_does_not_authorize_retry_of_possible_external_effects(tmp_path):
    plan = custody.Plan.parse(plan_document(tmp_path))
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    previous = request(plan, "old")
    (receipts / "old.started.json").write_text(json.dumps({"request": previous}))
    (receipts / "old.terminal.json").write_text(json.dumps({
        "request": previous, "status": "timed-out", "returncode": -15}))
    with pytest.raises(custody.CustodyError, match="uncertain"):
        custody.execute(plan, request(plan, "new"), receipts)
    assert not (receipts / "new.started.json").exists()


@pytest.mark.parametrize("suffix", ["terminal", "started"])
def test_receipt_symlink_prevents_execution(tmp_path, suffix):
    plan = custody.Plan.parse(plan_document(tmp_path))
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / f"request-1.{suffix}.json").symlink_to(tmp_path / "absent")
    with pytest.raises((OSError, custody.CustodyError)):
        custody.execute(plan, request(plan), receipts)
    assert not (receipts / "request-1.log").exists()
    assert not (tmp_path / "absent").exists()


def test_completed_request_replays_result_without_reexecution(tmp_path):
    plan = custody.Plan.parse(plan_document(tmp_path))
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    first = custody.execute(plan, request(plan), receipts)
    assert first["status"] == "completed" and first["returncode"] == 0
    assert (receipts / "request-1.log").read_bytes().startswith(b"claude\0")
    timestamp = (receipts / "request-1.log").stat().st_mtime_ns
    again = custody.execute(plan, request(plan), receipts)
    assert again["replayed"] is True
    assert (receipts / "request-1.log").stat().st_mtime_ns == timestamp


@pytest.mark.parametrize("condition", ["pid-reused", "paused", "outside", "nested-server"])
def test_peer_identity_and_service_ancestry_are_required(tmp_path, monkeypatch, condition):
    plan = custody.Plan.parse(plan_document(tmp_path, {"pid": 100, "started": "old"}))
    table = {100: {"pid": 100, "ppid": 1, "started": "old", "state": "S"},
             101: {"pid": 101, "ppid": 100, "started": "child", "state": "S"},
             200: {"pid": 200, "ppid": 1, "started": "other", "state": "S"}}
    if condition == "pid-reused":
        table[100]["started"] = "replacement"
    if condition == "paused":
        table[100]["state"] = "T"
    monkeypatch.setattr(custody.lane_watchdog, "process_snapshot", lambda: table)
    with pytest.raises(custody.CustodyError):
        custody.authorized_peer(plan, 200 if condition == "outside" else 101,
                                101 if condition == "nested-server" else 300)


def test_peer_descendant_is_accepted_without_trusting_claimed_pid(tmp_path, monkeypatch):
    plan = custody.Plan.parse(plan_document(tmp_path, {"pid": 100, "started": "root"}))
    table = {100: {"pid": 100, "ppid": 1, "started": "root", "state": "S"},
             101: {"pid": 101, "ppid": 100, "started": "child", "state": "S"}}
    monkeypatch.setattr(custody.lane_watchdog, "process_snapshot", lambda: table)
    custody.authorized_peer(plan, 101, 300)


@pytest.mark.parametrize("tamper", [False, True])
def test_real_kernel_peer_and_fixed_operation_round_trip(tmp_path, tamper):
    child_code = '''import json,socket,sys
message=json.loads(sys.stdin.readline())
with socket.socket(socket.AF_UNIX) as sock:
 sock.connect(message['socket'])
 sock.sendall(json.dumps(message['request']).encode()+b'\\n')
 print(sock.recv(65536).decode(),end='')
'''
    child = subprocess.Popen([sys.executable, "-c", child_code], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    errors = []
    try:
        table = custody.lane_watchdog.process_snapshot()
        assert table is not None and child.pid in table
        identity = {"pid": child.pid, "started": table[child.pid]["started"]}
        plan = custody.Plan.parse(plan_document(tmp_path, identity))
        with tempfile.TemporaryDirectory(prefix="f-custody-", dir="/tmp") as directory:
            address = pathlib.Path(directory) / "custody.sock"
            receipts = tmp_path / "receipts"

            def server():
                try:
                    custody.serve(plan, address, receipts, once=True)
                except Exception as error:
                    errors.append(error)

            worker = threading.Thread(target=server, daemon=True)
            worker.start()
            deadline = time.monotonic() + 3
            while not address.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            assert address.exists()
            body = request(plan)
            if tamper:
                body["argv"] = ["/bin/echo", "not-admitted"]
            output, stderr = child.communicate(json.dumps({"socket": str(address),
                                                          "request": body}) + "\n", timeout=15)
            worker.join(timeout=3)
            assert child.returncode == 0, stderr
            assert not worker.is_alive() and not errors
            result = json.loads(output)
            assert result["status"] == ("refused" if tamper else "completed")
            if not tamper:
                assert result["returncode"] == 0
            assert not address.exists()
            assert (receipts / "request-1.terminal.json").exists() is not tamper
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=3)


def test_peer_pid_is_kernel_supplied():
    import socket
    left, right = socket.socketpair()
    with left, right:
        assert custody.peer_pid(left) == os.getpid()


@pytest.mark.skipif(platform.system() != "Darwin", reason="real macOS boundary plus custody")
def test_bounded_seat_cannot_read_host_file_but_can_request_fixed_operation(tmp_path):
    from harness.validator_boundary import Boundary
    private = tmp_path / "private"
    private.mkdir()
    (private / "synthetic").write_text("host-only fixture")
    with tempfile.TemporaryDirectory(prefix="f-custody-bound-", dir="/tmp") as directory:
        control = pathlib.Path(directory).resolve()
        address = control / "custody.sock"
        boundary = Boundary.parse({"schema": "factory-seat-boundary/1", "role": "validator",
            "denied_reads": [str(private)], "protected_writes": [str(control)],
            "allowed_sockets": [str(address)]})
        code = '''import json,pathlib,socket,sys
message=json.loads(sys.stdin.readline())
try: pathlib.Path(sys.argv[1]).read_text(); sys.exit(42)
except PermissionError: pass
with socket.socket(socket.AF_UNIX) as sock:
 sock.connect(message['socket'])
 sock.sendall(json.dumps(message['request']).encode()+b'\\n')
 print(sock.recv(65536).decode(),end='')
'''
        child = subprocess.Popen(boundary.invocation(
            [sys.executable, "-c", code, str(private / "synthetic")], cwd=tmp_path),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            table = custody.lane_watchdog.process_snapshot()
            assert table is not None and child.pid in table
            identity = {"pid": child.pid, "started": table[child.pid]["started"]}
            plan = custody.Plan.parse(plan_document(tmp_path, identity))
            worker = threading.Thread(target=custody.serve,
                args=(plan, address, control / "receipts"), kwargs={"once": True}, daemon=True)
            worker.start()
            deadline = time.monotonic() + 3
            while not address.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            assert address.exists()
            output, stderr = child.communicate(json.dumps({"socket": str(address),
                                                          "request": request(plan)}) + "\n",
                                                timeout=15)
            worker.join(timeout=3)
            assert child.returncode == 0, stderr
            assert not worker.is_alive()
            assert json.loads(output)["returncode"] == 0
            receipt = control / "receipts" / "request-1.terminal.json"
            assert json.loads(receipt.read_text())["status"] == "completed"
        finally:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=3)
