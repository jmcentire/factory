"""The task must reach the native CLI as a prompt, not an allowed directory."""

import argparse
from pathlib import Path

import pytest

from harness.run_record import resident_command


@pytest.mark.parametrize("model,effort", [("", ""), ("opus", "high")])
@pytest.mark.parametrize("prompt", ["Read the task and begin", "--task-looking-like-an-option"])
def test_claude_prompt_survives_variadic_directory_argument(tmp_path, monkeypatch, model, effort, prompt):
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
