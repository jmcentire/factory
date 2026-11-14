"""Public author delivery after a native process exits retains the same conversation."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_native_claude_contract import (
    FACTORY,
    execute,
    launch,
    read_events,
    wait_for,
)

pytest_plugins = ["tests.test_native_claude_contract"]


@pytest.mark.parametrize("hold", [None, "stopping", "paused", "wrapping"])
def test_ended_claude_author_followup_resumes_native_identity(native_case, tmp_path, hold):
    case = native_case
    # Keep only fixture startup inputs, so failure rendering cannot expose
    # unrelated credentials inherited by the host test process.
    allowed = {
        "HOME",
        "PATH",
        "SHELL",
        "TMPDIR",
        "TERM",
        "LANG",
        "CLAUDE_CONFIG_DIR",
        "FACTORY_VALIDATOR_AGENT",
        "FACTORY_ORCHESTRATOR_AGENT",
        "FACTORY_PYTHON",
        "FACTORY_CONFIG",
        "FACTORY_KINDEX_LITE_BIN",
    }
    case["env"] = {key: value for key, value in case["env"].items() if key in allowed}
    launch(case)
    exit_flag = tmp_path / "end-owned-turn"
    for name in ("cl", "claude"):
        executable = case["bin_dir"] / name
        text = executable.read_text()
        # Synthetic native provider exits after its own Stop hook. Existing
        # coordinator processes already loaded their earlier recorder version.
        line = next(line for line in text.splitlines() if "native_event('Stop')" in line)
        indent = line[: len(line) - len(line.lstrip())]
        text = text.replace(
            line,
            line
            + "\n"
            + indent
            + f"if os.path.exists({str(exit_flag)!r}):\n"
            + indent
            + "    native_event('SessionEnd')\n"
            + indent
            + "    raise SystemExit(0)",
        )
        executable.write_text(text)
    lane = tmp_path / "author-repository"
    prepared = execute(
        [
            sys.executable,
            str(FACTORY / "harness/role_inputs.py"),
            "prepare",
            "--runs",
            str(case["runs"]),
            "--run",
            "native-acceptance",
            "--role",
            "coder",
            "--repo",
            str(lane),
        ],
        case["env"],
    )
    assert prepared.returncode == 0, prepared.stderr
    lane = Path(json.loads(prepared.stdout)["repository"])
    brief = tmp_path / "author.txt"
    brief.write_text("INITIAL_ONLY_ONCE_6729")
    exit_flag.touch()
    env = {**case["env"], "FACTORY_LANE_SANDBOX": "off"}
    launched = execute(
        [
            "bash",
            str(FACTORY / "harness/tmux_lane.sh"),
            "native-acceptance",
            "coder",
            "launch",
            "--runs",
            str(case["runs"]),
            "--repo",
            str(lane),
            "--prompt",
            str(brief),
            "--agent",
            "claude",
            "--model",
            "assigned-fixture",
            "--effort",
            "high",
        ],
        env,
    )
    assert launched.returncode == 0, launched.stdout + launched.stderr
    root = case["runs"] / "native-acceptance"
    state_path = root / "tmux-lanes/coder-persistent.json"

    def state():
        return json.loads(state_path.read_text()) if state_path.exists() else {}

    before = wait_for(lambda: s if (s := state()).get("phase") == "exited" else None)
    session = before["session_id"]
    assert before["deliveries"]["initial"]["state"] == "observed"
    initial = next(
        e
        for e in read_events(case["log"])
        if session in e.get("argv", []) and "--session-id" in e["argv"]
    )
    initial_input = next(
        e["input"]
        for e in read_events(case["log"])
        if e.get("pid") == initial["pid"] and "input" in e
    )
    exit_flag.unlink()
    case["suppress_ack"].touch()
    if hold:
        (root / "tmux-lanes/coder-watch.json").write_text(json.dumps({"status": hold}))
    message = execute(
        [
            "bash",
            str(FACTORY / "harness/tmux_lane_message.sh"),
            "native-acceptance",
            "validator",
            "coder",
            "status",
            "--runs",
            str(case["runs"]),
        ],
        env,
    )
    if hold:
        assert message.returncode == 70, message.stdout + message.stderr
        assert "unverified" in message.stderr
        assert not any(
            "--resume" in e.get("argv", []) and session in e["argv"]
            for e in read_events(case["log"])
        )
        return
    assert message.returncode in (0, 75), message.stdout + message.stderr
    resumed = wait_for(
        lambda: next(
            (
                e
                for e in read_events(case["log"])
                if "--resume" in e.get("argv", []) and session in e["argv"]
            ),
            None,
        )
    )
    assert resumed["stdin_tty"] and resumed["stdout_tty"]
    assert resumed["profile"] == initial["profile"] and resumed["home"] == initial["home"]
    assert not {"-p", "--print"} & set(resumed["argv"])
    for flag in ("--model", "--effort"):
        assert (
            resumed["argv"][resumed["argv"].index(flag) + 1]
            == initial["argv"][initial["argv"].index(flag) + 1]
        )
    assert ("--dangerously-skip-permissions" in resumed["argv"]) == (
        "--dangerously-skip-permissions" in initial["argv"]
    )
    assert "--settings" in resumed["argv"] and "--mcp-config" in resumed["argv"]
    inputs = read_events(case["log"])
    assert sum(e.get("input") == initial_input for e in inputs) == 1
    after = state()
    assert after["session_id"] == session
    assert all(
        row["state"] != "observed" for key, row in after["deliveries"].items() if key != "initial"
    )

    followup = wait_for(
        lambda: next(
            (
                e["input"]
                for e in read_events(case["log"])
                if e.get("pid") == resumed["pid"] and "input" in e
            ),
            None,
        )
    )
    settings = json.loads(
        Path(resumed["argv"][resumed["argv"].index("--settings") + 1]).read_text()
    )
    # The provider fixture now emits the delayed exact native receipt, not a
    # Factory success report. This is the same hook interface it uses normally.
    for kind in ("UserPromptSubmit", "Stop"):
        payload = {"hook_event_name": kind, "session_id": session}
        if kind == "UserPromptSubmit":
            payload["prompt"] = followup
        commands = [
            hook["command"]
            for group in settings["hooks"].get(kind, [])
            for hook in group.get("hooks", [])
        ]
        assert commands
        for command in commands:
            sent = subprocess.run(
                command,
                shell=True,
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                env=env,
                timeout=5,
            )
            assert sent.returncode == 0, sent.stderr
    wait_for(
        lambda: any(
            row["state"] == "observed"
            for key, row in state()["deliveries"].items()
            if key != "initial"
        )
    )
    assert sum(e.get("input") == initial_input for e in read_events(case["log"])) == 1
