"""Real process crashes and I/O faults must not corrupt later durable handoffs."""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from harness import run_record


def rows(root):
    return (root / "validator/inbox.jsonl").read_bytes().splitlines()


@pytest.mark.parametrize("point", ["before-write", "partial-write", "after-sync"])
def test_killed_writer_releases_lock_and_preserves_subsequent_handoffs(tmp_path, point):
    run_record.message(tmp_path, "validator", "coder", "prior decision")
    prior = rows(tmp_path)[0]
    script = '''
import os, signal, sys
from pathlib import Path
from harness import run_record
point = sys.argv[2]
write, sync = os.write, os.fsync
def kill():
    os.kill(os.getpid(), signal.SIGKILL)
def interrupted_write(fd, data):
    if point == 'before-write': kill()
    if point == 'partial-write':
        write(fd, data[:11])
        kill()
    return write(fd, data)
def interrupted_sync(fd):
    sync(fd)
    if point == 'after-sync': kill()
os.write, os.fsync = interrupted_write, interrupted_sync
run_record.message(Path(sys.argv[1]), 'validator', 'coder', 'interrupted handoff')
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), point],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10,
    )
    assert result.returncode == -signal.SIGKILL, result.stderr
    run_record.message(tmp_path, "validator", "tester", "next handoff")
    retained = rows(tmp_path)
    assert retained[0] == prior
    assert json.loads(retained[-1])["text"] == "next handoff"
    if point == "partial-write":
        # Keep the crash fragment as evidence; never concatenate the next record to it.
        assert retained[1] == b'{"sender": '
    elif point == "after-sync":
        assert json.loads(retained[1])["text"] == "interrupted handoff"
    else:
        assert len(retained) == 2


@pytest.mark.parametrize("fault", ["write", "sync"])
def test_failed_append_rolls_back_only_its_own_bytes_then_retry_succeeds(
    tmp_path, monkeypatch, fault,
):
    run_record.message(tmp_path, "validator", "coder", "prior")
    inbox = tmp_path / "validator/inbox.jsonl"
    prior = inbox.read_bytes()
    write, sync = os.write, os.fsync
    calls = 0

    def failing_write(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return write(fd, data[:7])
        raise OSError("fixture disk failure")

    def failing_sync(fd):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("fixture disk failure")
        return sync(fd)

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "write" if fault == "write" else "fsync",
                       failing_write if fault == "write" else failing_sync)
        with pytest.raises(OSError, match="fixture disk failure"):
            run_record.message(tmp_path, "validator", "coder", "retry me")
    assert inbox.read_bytes() == prior
    run_record.message(tmp_path, "validator", "coder", "retry me")
    assert [json.loads(row)["text"] for row in rows(tmp_path)] == ["prior", "retry me"]


@pytest.mark.parametrize("sink", ["directory", "symlink", "fifo"])
def test_optional_log_failure_does_not_report_a_durable_delivery_as_failed(
    tmp_path, capsys, sink,
):
    log = tmp_path / "log"
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    if sink == "directory":
        log.mkdir()
    elif sink == "symlink":
        log.symlink_to(outside)
    else:
        os.mkfifo(log)
    run_record.message(tmp_path, "validator", "coder", "already delivered", log=str(log))
    assert [json.loads(row)["text"] for row in rows(tmp_path)] == ["already delivered"]
    warning = capsys.readouterr().err
    assert "queued" in warning and "optional log" in warning
    assert outside.read_text() == "untouched"
