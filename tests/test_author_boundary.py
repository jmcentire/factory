"""Author model confinement preserves host-owned session and completion evidence."""
from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path

import pytest

from harness.codex_lane_session import run_turn
from harness.lane_agent import run_lane
from harness.validator_boundary import (
    Boundary,
    BoundaryError,
    select_lane_record,
    validate_lane_record,
)
from tests.test_harness_scripts import (
    HARNESS,
    _ignite_and_launcher,
    execution_truth_fixture,
    factory_ignition_env,
    run,
)


def binding(tmp_path, role="coder"):
    control = tmp_path / "control"
    control.mkdir(exist_ok=True)
    private = tmp_path / "peer"
    private.mkdir(exist_ok=True)
    (private / "implementation").write_text("synthetic peer work")
    boundary = Boundary.parse({"schema": "factory-seat-boundary/1", "role": role,
        "denied_reads": [str(private)], "protected_writes": [str(control)],
        "allowed_sockets": []})
    contract = control / "contract.json"
    contract.write_text(json.dumps(boundary.document()))
    return {"schema": "factory-lane-boundary/1", "role": role,
            "contract": str(contract), "digest": boundary.digest}


@pytest.mark.parametrize("environment", [
    {"FACTORY_REQUIRE_BOUNDARY": "1"},
    {"FACTORY_BOUNDARY_CONTRACT": "/missing"},
    {"FACTORY_BOUNDARY_DIGEST": "0" * 64},
])
def test_missing_boundary_never_satisfies_required_admission(tmp_path, environment):
    with pytest.raises(BoundaryError, match="missing"):
        select_lane_record(environment, "coder", tmp_path)


@pytest.mark.parametrize("mutation", ["wrong-role", "unknown-role", "drift"])
def test_role_mismatch_and_configuration_drift_refuse_before_execution(tmp_path, mutation):
    record = binding(tmp_path)
    if mutation == "drift":
        record["digest"] = "0" * 64
    else:
        record["role"] = "tester" if mutation == "wrong-role" else "unknown"
    with pytest.raises(BoundaryError):
        validate_lane_record(record, "coder", tmp_path)


@pytest.mark.skipif(platform.system() != "Darwin", reason="actual model-child kernel boundary")
@pytest.mark.parametrize("driver", ["codex", "registry"])
def test_model_cannot_read_peer_or_forge_trusted_output_but_host_records_completion(
    tmp_path, monkeypatch, driver,
):
    monkeypatch.chdir(tmp_path)
    record = binding(tmp_path)
    script = '''import json,pathlib,sys
root=pathlib.Path(sys.argv[1])
try: (root/'peer/implementation').read_text(); sys.exit(41)
except PermissionError: pass
try: (root/'control/forged-exit.json').write_text('forged'); sys.exit(42)
except PermissionError: pass
print(json.dumps({'type':'thread.started','thread_id':'12345678-1234-4234-8234-123456789abc'}))
print(json.dumps({'type':'item.completed',
                  'item':{'type':'agent_message','text':'finished fixture'}}))
'''
    command = [sys.executable, "-c", script, str(tmp_path)]
    if driver == "codex":
        prompt = tmp_path / "prompt"
        prompt.write_text("synthetic test only")
        result = run_turn(prompt, tmp_path / "control/thread", tmp_path / "control/events",
                          command, root=tmp_path, role="coder", boundary_record=record)
        assert (tmp_path / "control/thread").read_text().strip().endswith("123456789abc")
        assert "thread.started" in (tmp_path / "control/events").read_text()
    else:
        result = run_lane(command, slot="coder", log=tmp_path / "control/model.log",
                          exits=tmp_path / "control/exits.jsonl", boundary_record=record,
                          boundary_role="coder")
        assert "finished fixture" in (tmp_path / "control/model.log").read_text()
        exits = [json.loads(line) for line in
                 (tmp_path / "control/exits.jsonl").read_text().splitlines()]
        assert exits[-1]["rc"] == 0
    assert result == 0
    assert not (tmp_path / "control/forged-exit.json").exists()


def test_missing_required_boundary_refuses_author_window(tmp_path):
    _, _, tmux_log, launch = _ignite_and_launcher(
        tmp_path, "Required author boundary.", {"FACTORY_REQUIRE_BOUNDARY": "1"})
    result = launch("coder")
    assert result.returncode == 77, result.stdout + result.stderr
    assert "boundary" in result.stderr
    assert not any("-n coder" in row for row in tmux_log.read_text().splitlines())


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS boundary admission")
def test_launch_retains_model_child_binding(tmp_path):
    record = binding(tmp_path)
    root, _, tmux_log, launch = _ignite_and_launcher(
        tmp_path, "Retain the admitted author boundary.", {
            "FACTORY_REQUIRE_BOUNDARY": "1",
            "FACTORY_BOUNDARY_CONTRACT": record["contract"],
            "FACTORY_BOUNDARY_DIGEST": record["digest"],
        })
    result = launch("coder")
    assert result.returncode == 0, result.stdout + result.stderr
    rows = [json.loads(row) for row in
            (root / "tmux-lanes/coder-launch.jsonl").read_text().splitlines()]
    assert rows[-1]["kernel_boundary"] == record
    command = next(row for row in tmux_log.read_text().splitlines() if "-n coder" in row)
    assert command.count("--boundary-record") == 1
    assert command.index("codex_lane_session.py") < command.index("--boundary-record")
    assert "--boundary-role" not in command  # outer trusted writer is not the model child


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS boundary admission")
def test_resume_uses_retained_binding_and_refuses_drift_before_respawn(tmp_path):
    operator, root, _ = execution_truth_fixture(
        tmp_path, task="Resume under original boundary.", harness_status="open")
    env, tmux_log = factory_ignition_env(tmp_path, root)
    lane = tmp_path / "standalone-coder"
    subprocess.run(["git", "init", "-q", "-b", "main", str(lane)], check=True)
    record = binding(tmp_path)
    launch_dir = root / "tmux-lanes"
    launch_dir.mkdir()
    (launch_dir / "coder-thread-id").write_text("12345678-1234-4234-8234-123456789abc\n")
    row = {"schema_version": "factory-tmux-lane-launch/1", "status": "active", "run_id": "r1",
           "role": "coder", "agent": "codex", "model": "gpt-coder", "repository": str(lane),
           "kernel_boundary": record}
    journal = launch_dir / "coder-launch.jsonl"
    journal.write_text(json.dumps(row) + "\n")
    args = ["bash", str(HARNESS / "tmux_lane_message.sh"), "r1", "validator", "coder", "status",
            "--runs", str(root.parent)]
    # Ambient replacement cannot substitute for the retained host binding.
    env.update(FACTORY_REQUIRE_BOUNDARY="1", FACTORY_BOUNDARY_CONTRACT="/replacement-not-used",
               FACTORY_BOUNDARY_DIGEST="0" * 64)
    resumed = run(args, operator, env)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    command = next(row for row in tmux_log.read_text().splitlines() if "respawn-pane" in row)
    assert record["digest"] in command and "replacement-not-used" not in command
    assert command.count("--boundary-record") == 1
    tmux_log.unlink()
    document = json.loads(Path(record["contract"]).read_text())
    document["role"] = "tester"
    Path(record["contract"]).write_text(json.dumps(document))
    refused = run(args, operator, env)
    assert refused.returncode == 77, refused.stdout + refused.stderr
    assert "identity or configuration changed" in refused.stderr
    assert not tmux_log.exists() or "respawn-pane" not in tmux_log.read_text()
    row["kernel_boundary"] = None
    journal.write_text(json.dumps(row) + "\n")
    missing = run(args, operator, env)
    assert missing.returncode == 77 and "required retained author boundary" in missing.stderr
