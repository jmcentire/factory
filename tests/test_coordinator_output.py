"""Actual terminal output reaches oversight without becoming terminal input."""

import io
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from harness import run_record
from harness.dispatcher import observe


def test_capture_preserves_raw_output_and_never_follows_a_sink_link(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"raw\xff\n")))
    run_record.capture_output(tmp_path, "validator")
    assert (tmp_path / "validator-lane.log").read_bytes() == b"raw\xff\n"
    target = tmp_path / "untouched"
    target.write_bytes(b"keep")
    (tmp_path / "orchestrator-lane.log").symlink_to(target)
    with pytest.raises(OSError):
        run_record.capture_output(tmp_path, "orchestrator")
    assert target.read_bytes() == b"keep"


def test_capture_refuses_a_fifo_without_waiting_for_a_reader(tmp_path):
    os.mkfifo(tmp_path / "validator-lane.log")
    with pytest.raises(OSError):
        run_record.capture_output(tmp_path, "validator")


def test_author_capture_uses_the_slot_log_and_rejects_path_traversal(tmp_path, monkeypatch):
    from tests.test_harness_scripts import projection_fixture

    repo = projection_fixture(tmp_path)
    runs = tmp_path / "runs"
    root = run_record.create(runs, "r1", repo, b"Capture author output")
    (root / "tmux-lanes").mkdir()
    args = [sys.executable, run_record.__file__, "capture", "--runs", str(runs),
            "--run", "r1", "--slot"]
    result = subprocess.run([*args, "coder.r3"], input=b"literal $(touch NO)\n",
                            capture_output=True)
    assert result.returncode == 0, result.stderr
    assert (root / "tmux-lanes/coder.r3-lane.log").read_bytes() == b"literal $(touch NO)\n"
    rejected = subprocess.run([*args, "../validator"], input=b"bad", capture_output=True)
    assert rejected.returncode != 0
    assert not (root / "validator-lane.log").exists()


def test_resident_output_is_retained_from_start_on_private_tmux(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c",
                    "user.email=test@example.invalid", "commit", "--allow-empty",
                    "-qm", "initial"], check=True)
    binary = tmp_path / "bin"
    binary.mkdir()
    stub = binary / "codex"
    stub.write_text(f"#!{sys.executable}\n" + '''import pathlib, time
print("FIRST $(touch SHOULD_NOT_EXIST)", flush=True)
print("x" * 20000, flush=True)
while not pathlib.Path("release").exists():
    time.sleep(0.05)
print("LAST", flush=True)
''')
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary) + ":/opt/homebrew/bin:/usr/bin:/bin")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("FACTORY_CONFIG", raising=False)
    monkeypatch.setenv("FACTORY_SEAT_BILLING", "plan")
    for role in ("VALIDATOR", "ORCHESTRATOR", "CODER", "TESTER"):
        monkeypatch.setenv(f"FACTORY_{role}_AGENT", "codex")
        monkeypatch.setenv(f"FACTORY_{role}_MODEL", "fixture")
        monkeypatch.setenv(f"FACTORY_{role}_EFFORT", "")
    runs = tmp_path / "runs"
    root = run_record.create(runs, "output", repo, b"Disposable terminal probe")
    doc = json.loads((root / "harness.json").read_text())
    tmux = ["tmux", "-L", doc["tmux_socket"]]
    server = None
    try:
        run_record.start(runs, "output")
        server = subprocess.check_output([*tmux, "display-message", "-p", "#{pid}"])
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            log = root / "validator-lane.log"
            if log.exists() and b"x" * 100 in log.read_bytes():
                break
            time.sleep(0.05)
        assert log.exists(), "interactive coordinator output was not retained"
        cursor, output = {}, ""
        while True:
            result = observe(root, cursor)
            cursor = result["cursors"]
            output += "".join(row["text"] for row in result["entries"]
                              if row["source"] == "validator-lane.log")
            if not result["entries"]:
                break
        assert output.startswith("FIRST $(touch SHOULD_NOT_EXIST)")
        assert output.count("x") >= 20000
        assert not (repo / "SHOULD_NOT_EXIST").exists()
        assert (root / "orchestrator-lane.log").exists()
        assert not any(row["source"] == "orchestrator-lane.log"
                       for row in observe(root, {})["entries"])
        assert os.stat(log).st_mode & 0o777 == 0o600
    finally:
        (repo / "release").touch()
        if server is not None:
            current = subprocess.run([*tmux, "display-message", "-p", "#{pid}"],
                                     capture_output=True)
            if current.returncode == 0 and current.stdout == server:
                subprocess.run([*tmux, "kill-server"], check=True)


def test_a_seat_that_cannot_start_fails_a_real_start(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c",
                    "user.email=test@example.invalid", "commit", "--allow-empty",
                    "-qm", "initial"], check=True)
    binary = tmp_path / "bin"
    binary.mkdir()
    stub = binary / "codex"
    # Found on PATH, so the launcher opens its window, but it cannot be executed.
    stub.write_text("#!/nonexistent/interpreter\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary) + ":/opt/homebrew/bin:/usr/bin:/bin")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("FACTORY_CONFIG", raising=False)
    monkeypatch.setenv("FACTORY_SEAT_BILLING", "plan")
    for role in ("VALIDATOR", "ORCHESTRATOR", "CODER", "TESTER"):
        monkeypatch.setenv(f"FACTORY_{role}_AGENT", "codex")
        monkeypatch.setenv(f"FACTORY_{role}_MODEL", "fixture")
        monkeypatch.setenv(f"FACTORY_{role}_EFFORT", "")
    runs = tmp_path / "runs"
    root = run_record.create(runs, "broken", repo, b"Disposable startup probe")
    doc = json.loads((root / "harness.json").read_text())
    tmux = ["tmux", "-L", doc["tmux_socket"]]
    try:
        with pytest.raises(ValueError, match="the validator stopped during startup; "
                           "lane-agent: could not start codex"):
            run_record.start(runs, "broken")
        assert subprocess.run([*tmux, "has-session", "-t", "=broken"],
                              capture_output=True).returncode != 0
        assert "retry with:" in json.loads((root / "validator/inbox.jsonl").read_text())["text"]
    finally:
        subprocess.run([*tmux, "kill-server"], capture_output=True)
