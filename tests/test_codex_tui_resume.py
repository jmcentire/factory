"""Native TUI recovery retains the original conversation and assigned exec settings."""
import hashlib
import json
from pathlib import Path

import pytest

from harness.codex_lane_session import SessionError, bind_interactive_session
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


@pytest.mark.parametrize("change",
                         ["none", "directory", "model", "prompt", "old", "guard", "symlink"])
def test_native_identity_is_bound_to_exact_launch(tmp_path, change):
    launch, rollout, thread, turn, rows = fixture(tmp_path, tmp_path / "coder")
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
        link = tmp_path / "link"
        link.symlink_to(rollout)
        rollout = link
    if change == "none":
        before = turn.read_bytes()
        assert bind_interactive_session(launch, rollout, thread, turn) == SESSION
        assert thread.read_text().strip() == SESSION
        assert turn.read_bytes() == before
    else:
        with pytest.raises((OSError, SessionError)):
            bind_interactive_session(launch, rollout, thread, turn)
        assert not thread.exists()


def test_tui_handoff_uses_existing_typed_resume(tmp_path):
    operator, root, env, tmux_log = ordinary_message_fixture(tmp_path)
    lane = tmp_path / "coder"
    lane.mkdir()
    _, rollout, thread, _, _ = fixture(root, lane)
    result = run(["bash", str(HARNESS / "tmux_lane_message.sh"), "r1", "validator",
                  "coder", "status", "--runs", str(root.parent),
                  "--native-session-file", str(rollout)], operator, env)
    assert result.returncode == 0, result.stdout + result.stderr
    command = tmux_log.read_text()
    assert "exec --json resume" in command and SESSION in command
    assert "-m assigned-astra" in command and "model_reasoning_effort=high" in command
    assert "--dangerously-bypass-approvals-and-sandbox" in command
    assert "--ask-for-approval" not in command and "send-keys" not in command
    assert thread.read_text().strip() == SESSION
    assert not (operator / "MUST_NOT_EXECUTE").exists()


def test_tui_handoff_never_starts_a_second_controller(tmp_path):
    operator, root, env, tmux_log = ordinary_message_fixture(tmp_path)
    _, rollout, thread, _, _ = fixture(root, tmp_path / "coder")
    stub = Path(env["PATH"].split(":")[0]) / "tmux"
    stub.write_text(stub.read_text().replace('then printf "1\\n"', 'then printf "0\\n"'))
    result = run(["bash", str(HARNESS / "tmux_lane_message.sh"), "r1", "validator",
                  "coder", "status", "--runs", str(root.parent),
                  "--native-session-file", str(rollout)], operator, env)
    assert result.returncode == 75, result.stdout + result.stderr
    assert "still live" in result.stderr
    assert not thread.exists()
    assert not tmux_log.exists()
