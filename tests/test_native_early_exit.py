"""A rejected native CLI must retain its own exit and diagnostic before SessionStart."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("code", [64, 78])
def test_native_rejection_before_session_start_is_not_reclassified_as_wrapper_failure(
    tmp_path, code
):
    harness = Path(__file__).resolve().parents[1] / "harness"
    (tmp_path / "prompt").write_text("initial")
    marker = tmp_path / "invocations"
    native = (
        "import pathlib,sys\n"
        f'with pathlib.Path({str(marker)!r}).open("a") as out:out.write("called\\n")\n'
        'print("native configuration rejected",file=sys.stderr,flush=True)\n'
        f"raise SystemExit({code})\n"
    )
    executable = tmp_path / "fake-claude"
    executable.write_text(f"#!{sys.executable}\n" + native)
    executable.chmod(0o755)
    wrapper = r"""
import pathlib, subprocess, sys
sys.path.insert(0, sys.argv[1])
import lane_agent
root = pathlib.Path(sys.argv[2])
raise SystemExit(lane_agent.run_lane(
    [sys.argv[3]], slot='validator',
    log=root/'validator-lane.log', exits=root/'validator-exits.jsonl',
    prompt=root/'prompt', session_agent='claude', session_file=root/'session',
    persistent=True, run_root=root))
"""
    result = subprocess.run(
        [sys.executable, "-c", wrapper, str(harness), str(tmp_path), str(executable)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == code, result.stdout + result.stderr
    assert "native configuration rejected" in result.stdout + result.stderr
    assert "native configuration rejected" in (tmp_path / "validator-lane.log").read_text()
    rows = [
        json.loads(row) for row in (tmp_path / "validator-exits.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 1 and rows[0]["rc"] == code
    assert marker.read_text().splitlines() == ["called"]


def test_unread_wrapper_output_cannot_block_native_exit_or_durable_diagnostics(tmp_path):
    """A slow observer must not stall native custody or lose the session log.

    Oracle: the reviewer's liveness finding and the native diagnostic-retention
    contract. The owned synthetic CLI emits more than an ordinary pipe can hold.
    No stdout reader runs until the wrapper's bounded exit check has finished.
    This catches synchronous forwarding that stops terminal/event servicing.
    """
    harness = Path(__file__).resolve().parents[1] / "harness"
    prompt = tmp_path / "prompt"
    prompt.write_text("initial")
    executable = tmp_path / "fake-claude"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "for fd in (1, 2):\n"
        "    for _ in range(256):\n"
        "        os.write(fd, b'native-burst-' + b'x' * 4082 + b'\\n')\n"
        "    os.write(fd, f'native-stream-{fd}-finished\\n'.encode())\n"
        "raise SystemExit(78)\n"
    )
    executable.chmod(0o755)
    child = subprocess.Popen(
        [
            sys.executable,
            str(harness / "lane_agent.py"),
            "run",
            "--slot",
            "validator",
            "--log",
            str(tmp_path / "validator-lane.log"),
            "--exits",
            str(tmp_path / "validator-exits.jsonl"),
            "--prompt-arg",
            str(prompt),
            "--session-agent",
            "claude",
            "--session-file",
            str(tmp_path / "session"),
            "--persistent",
            "--run-root",
            str(tmp_path),
            "--",
            str(executable),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    blocked = False
    try:
        try:
            child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            blocked = True
    finally:
        # Always drain after the bounded assertion window. Even the broken
        # synchronous relay can then finish and reap its native child normally.
        try:
            child.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            child.terminate()
            try:
                child.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate(timeout=5)
    assert not blocked, "unread observer pipe stalled the owned native session's exit"
    assert child.returncode == 78
    log = (tmp_path / "validator-lane.log").read_text()
    assert log.count("native-burst-") == 512
    assert "native-stream-1-finished" in log and "native-stream-2-finished" in log
    rows = [
        json.loads(row) for row in (tmp_path / "validator-exits.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 1 and rows[0]["rc"] == 78
