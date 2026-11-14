"""A native interactive lane never silently becomes exec on recovery.

Synthetic rollout metadata belongs to this test, never an operator provider store.
The public message CLI must reject obsolete rollout-file handoff, not parse private
provider state to invent a noninteractive replacement controller.
"""
import hashlib
import json
from pathlib import Path

import pytest

from tests.test_harness_scripts import HARNESS, ordinary_message_fixture, run

SESSION = "12345678-1234-4234-8234-123456789abc"

def fixture(root: Path, repository: Path):
    lanes = root / "tmux-lanes"
    lanes.mkdir(exist_ok=True)
    prompt = "The exact retained task $(touch MUST_NOT_EXECUTE)"
    launch = {"status": "active", "run_id": "r1", "role": "coder",
              "agent": "codex-interactive", "model": "assigned-astra",
              "repository": str(repository), "sandbox": "off",
              "ts": "2026-11-04T20:00:00+00:00",
              "prompt_digest": "sha256:" + hashlib.sha256(prompt.encode()).hexdigest()}
    rows = [
        {"type": "session_meta", "payload": {
            "id": SESSION, "originator": "codex-tui", "cwd": str(repository),
            "timestamp": "2026-11-04T20:00:01Z"}},
        {"type": "turn_context", "payload": {
            "cwd": str(repository), "model": "assigned-astra", "effort": "high"}},
        {"type": "response_item", "payload": {"role": "user", "content": [
            {"type": "input_text", "text": prompt}]}}]
    launch_file = lanes / "coder-launch.jsonl"
    launch_file.write_text(json.dumps(launch) + "\n")
    rollout = root / "native.jsonl"
    rollout.write_text("\n".join(map(json.dumps, rows)) + "\n")
    turn = lanes / "coder-turn.json"
    turn.write_text(json.dumps({"schema_version": "factory-lane-turn/1", "agent": "codex",
                               "model": "assigned-astra", "effort": "high", "extra": []}))
    return launch_file, rollout, lanes / "coder-thread-id", turn, rows

@pytest.mark.parametrize("pane_dead", ["1", "0"])
@pytest.mark.parametrize(
    "change", ["none", "directory", "model", "prompt", "old", "guard", "symlink"]
)
def test_legacy_native_file_handoff_refuses_without_exec_or_identity_mutation(
    tmp_path, pane_dead, change
):
    operator, root, env, tmux_log = ordinary_message_fixture(tmp_path)
    lane = tmp_path / "coder"
    lane.mkdir()
    launch, rollout, thread, turn, rows = fixture(root, lane)
    if change == "directory":
        rows[0]["payload"]["cwd"] = str(tmp_path / "tester")
    elif change == "model":
        rows[1]["payload"]["model"] = "other-model"
    elif change == "prompt":
        rows[2]["payload"]["content"][0]["text"] = "different task"
    elif change == "old":
        rows[0]["payload"]["timestamp"] = "2026-11-03T20:00:00Z"
    elif change == "guard":
        turn.unlink()
    rollout.write_text("\n".join(map(json.dumps, rows)) + "\n")
    if change == "symlink":
        link = root / "rollout-link"
        link.symlink_to(rollout)
        rollout = link
    before = {p: p.read_bytes() for p in (launch, rollout, turn) if p.exists()}
    stub = Path(env["PATH"].split(":")[0]) / "tmux"
    stub.write_text(stub.read_text().replace('then printf "1\\n"',
                                           f'then printf "{pane_dead}\\n"'))
    result = run(["bash", str(HARNESS / "tmux_lane_message.sh"), "r1", "validator",
                  "coder", "status", "--runs", str(root.parent),
                  "--native-session-file", str(rollout)], operator, env)
    command = tmux_log.read_text() if tmux_log.exists() else ""
    assert result.returncode != 0, result.stdout + result.stderr
    assert "exec --json resume" not in command
    assert "respawn-pane" not in command and "new-window" not in command
    assert "send-keys" not in command
    assert not thread.exists()
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert not (operator / "MUST_NOT_EXECUTE").exists()
    assert result.stderr.strip(), "refusal must explain that no message was delivered"


def test_ended_native_lane_without_legacy_flag_never_becomes_exec(tmp_path):
    operator, root, env, tmux_log = ordinary_message_fixture(tmp_path)
    lane = tmp_path / "coder"
    lane.mkdir()
    _, _, thread, _, _ = fixture(root, lane)
    thread.write_text(SESSION + "\n")
    result = run(["bash", str(HARNESS / "tmux_lane_message.sh"), "r1", "validator",
                  "coder", "status", "--runs", str(root.parent)], operator, env)
    command = tmux_log.read_text() if tmux_log.exists() else ""
    assert "exec --json resume" not in command
    assert "codex exec" not in command
    assert thread.read_text().strip() == SESSION
    if result.returncode:
        assert "respawn-pane" not in command and "new-window" not in command
        assert result.stderr.strip()
    else:
        # A future supported native resume is permitted, but must target the same
        # conversation and never claim success through a bare launch/new session.
        assert "resume" in command and SESSION in command
        assert "--json" not in command and "--print" not in command
