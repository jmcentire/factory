"""Dispatcher entrypoint observes records; it cannot restart the retired control loop.

The ceremony-removal contract requires --observe before reading or mutating run state.
A legacy invocation creating a Validator block, running an external tool, or interpreting
invalid run metadata turns the rejection controls red. Actual retained-output observation
is the positive control, without any simulated role work or authorization artifacts.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_validation_failure_paths import task as task

DISPATCHER = Path(__file__).resolve().parents[1] / "harness/dispatcher.py"


@pytest.mark.parametrize("root_exists", [False, True])
def test_dispatcher_without_observe_rejects_before_touching_run(
    tmp_path: Path, root_exists: bool,
) -> None:
    root = tmp_path / "run"
    before = {}
    if root_exists:
        root.mkdir()
        # Validating these bytes would itself be a regression: the mode must reject first.
        files = {"harness.json": b"not JSON\x00", "TASK.md": b"keep exact task"}
        for name, content in files.items():
            (root / name).write_bytes(content)
        before = {path.name: path.read_bytes() for path in root.iterdir()}
    binary = tmp_path / "bin"
    binary.mkdir()
    marker = tmp_path / "external-command"
    for name in ("factory", "tmux", "codex", "agy", "claude", "launchctl", "crontab"):
        executable = binary / name
        executable.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 97\n")
        executable.chmod(0o755)

    result = subprocess.run(
        [sys.executable, str(DISPATCHER), "--run", "ordinary", "--root", str(root)],
        env={"PATH": f"{binary}:/usr/bin:/bin", "FACTORY_PYTHON": sys.executable},
        capture_output=True, text=True, timeout=10,
    )

    assert result.returncode == 2
    assert "--observe" in result.stderr
    assert result.stdout == ""
    assert not marker.exists()
    assert root.exists() is root_exists
    if root_exists:
        assert {path.name: path.read_bytes() for path in root.iterdir()} == before


def test_dispatcher_observe_reports_retained_output_without_mutation(task) -> None:
    _, root, _, _ = task
    log = root / "validator-lane.log"
    log.write_bytes(b"retained progress, not a verdict\n")
    before = {path.relative_to(root): path.read_bytes()
              for path in root.rglob("*") if path.is_file()}
    result = subprocess.run(
        [sys.executable, str(DISPATCHER), "--run", "task", "--root", str(root), "--observe"],
        env={"PATH": "/usr/bin:/bin", "FACTORY_PYTHON": sys.executable},
        capture_output=True, text=True, timeout=10,
    )

    assert result.returncode == 0, result.stderr
    observation = json.loads(result.stdout)
    assert len(observation["entries"]) == 1
    assert observation["entries"][0]["source"] == log.name
    assert observation["entries"][0]["text"] == before[Path(log.name)].decode()
    assert {path.relative_to(root): path.read_bytes()
            for path in root.rglob("*") if path.is_file()} == before
