"""A rejected native CLI must retain its own exit and diagnostic before registration."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("code", [64, 78])
def test_native_rejection_before_registration_is_not_reclassified_as_wrapper_failure(
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
    wrapper = r"""
import pathlib, subprocess, sys
sys.path.insert(0, sys.argv[1])
import lane_agent
root = pathlib.Path(sys.argv[2])
original = subprocess.Popen
def already_finished(*args, **kwargs):
    subprocess.Popen = original
    child = original(*args, **kwargs)
    child.wait(timeout=5)
    return child
# Force the actual child to have exited before process identity registration. This
# reproduces the short-lived CLI race deterministically, without faking its exit.
subprocess.Popen = already_finished
raise SystemExit(lane_agent.run_lane(
    [sys.executable, '-u', '-c', sys.argv[3]], slot='validator',
    log=root/'validator-lane.log', exits=root/'validator-exits.jsonl',
    prompt=root/'prompt', session_agent='claude', session_file=root/'session',
    persistent=True, run_root=root))
"""
    result = subprocess.run(
        [sys.executable, "-c", wrapper, str(harness), str(tmp_path), native],
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
