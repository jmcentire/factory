"""The task must reach the native CLI as a prompt, not an allowed directory."""

import argparse
import json
from pathlib import Path

import pytest

from harness.run_record import resident_command


@pytest.mark.parametrize("model,effort", [("", ""), ("opus", "high")])
@pytest.mark.parametrize("prompt", ["Read the task and begin", "--task-looking-like-an-option"])
def test_claude_prompt_survives_variadic_directory_argument(
    tmp_path, monkeypatch, model, effort, prompt
):
    monkeypatch.setenv("FACTORY_LANE_SANDBOX", "on")
    argv = resident_command("claude", model, effort, prompt, tmp_path)
    # Claude's documented --add-dir <directories...> greedily accepts positional strings.
    # Exercise argument consumption rather than requiring a particular delimiter spelling.
    parser = argparse.ArgumentParser()
    parser.add_argument("--add-dir", nargs="+")
    parser.add_argument("--model")
    parser.add_argument("--effort")
    parser.add_argument("prompt")
    parsed = parser.parse_args(argv[1:])
    assert parsed.prompt == prompt
    assert list(map(Path, parsed.add_dir)) == [tmp_path]


def test_agy_seat_settings_allow_only_the_seat_harness_and_read_only_kindex(tmp_path):
    # Field runs: an Antigravity Orchestrator asked a person for every dispatcher call and
    # every Kindex read, so its polling loop (and so its inbox) stalled with nobody at the pane.
    from harness.run_record import AGY_SEAT_KINDEX_TOOLS, agy_seat_settings

    tools = tmp_path / "tools"
    settings = json.loads(agy_seat_settings("/repo", tmp_path / "run", tools, "/py"))
    assert settings["trustedWorkspaces"] == ["/repo", str(tmp_path / "run")]
    allow = settings["permissions"]["allow"]
    assert f"command(/py {tools / 'dispatcher.py'})" in allow
    assert f"command(bash {tools / 'tmux_lane_message.sh'})" in allow
    assert sorted(rule for rule in allow if rule.startswith("mcp(")) == sorted(
        f"mcp(kindex/{tool})" for tool in AGY_SEAT_KINDEX_TOOLS)
    assert not any("*" in rule or rule.startswith(("write_file(", "command(bash)")) for rule in allow)
    assert all(rule.startswith(("mcp(kindex/", f"command(/py {tools}/", f"command(bash {tools}/",
                                f"command({tools}/")) for rule in allow)


def test_only_an_agy_seat_takes_seat_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_LANE_SANDBOX", "on")
    agy = resident_command("agy", "m", "", "the task", tmp_path, settings='{"a": 1}')
    assert agy[agy.index("--settings") + 1] == '{"a": 1}'
    assert agy[-2:] == ["--prompt-interactive", "the task"]
    assert "--settings" not in resident_command("agy", "m", "", "the task", tmp_path)
    assert "--settings" not in resident_command("claude", "m", "", "task", tmp_path,
                                                settings='{"a": 1}')
