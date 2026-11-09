"""Ordinary shell startup needs the task and repository, not machine-wide approval.

Contract: the owner's 2026-11-09 request removes authorization and scheduler ceremonies
while retaining task/repository/HEAD/tree records and ordinary execution. This drives the
real shell entry and Git/filesystem behavior. Only the native session boundary is a recorder:
it proves startup issues coordinator launches, not that a model performs useful work.
Falsifiers: requiring a receipt/registry, reading or modifying OS jobs, or recording a different
task/commit/tree. Scheduler commands return unrelated desktop jobs if mistakenly invoked;
the test must fail even if a future audit ignores those jobs or swallows a tool failure.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

FACTORY = Path(__file__).resolve().parents[1] / "harness/factory.sh"


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True, text=True, capture_output=True,
    ).stdout.strip()


@pytest.mark.parametrize("task_from_file", [False, True])
def test_shell_startup_needs_no_authorization_or_os_schedule_registry(
    tmp_path: Path, task_from_file: bool,
) -> None:
    repository = tmp_path / "ordinary source"
    repository.mkdir()
    _git(repository, "init", "-q")
    source = repository / "application.txt"
    source.write_text("committed source\n")
    _git(repository, "add", "application.txt")
    _git(repository, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "-qm", "initial source")
    commit = _git(repository, "rev-parse", "HEAD")
    tree = _git(repository, "rev-parse", "HEAD^{tree}")
    source.write_text("unrelated work in progress\n")
    untracked = repository / "untracked.txt"
    untracked.write_text("retain unrelated notes\n")
    status = _git(repository, "status", "--porcelain=v1")

    home = tmp_path / "home"
    home.mkdir()
    binary = tmp_path / "bin"
    binary.mkdir()
    calls = tmp_path / "session-calls.jsonl"
    tmux = binary / "tmux"
    tmux.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"with open({str(calls)!r}, 'a') as log:\n"
        "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "raise SystemExit(1 if 'has-session' in sys.argv else 0)\n"
    )
    tmux.chmod(0o755)
    # Never invoke the user's agents: startup hands commands to the session recorder.
    for agent in ("codex", "agy", "claude", "ollama", "agent", "muse", "prime-agent"):
        executable = binary / agent
        executable.write_text("#!/bin/sh\necho unexpected-native-agent >&2\nexit 97\n")
        executable.chmod(0o755)
    scheduler_calls = tmp_path / "scheduler-calls.jsonl"
    for scheduler in ("launchctl", "crontab", "atq", "systemctl"):
        executable = binary / scheduler
        executable.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\n"
            f"with open({str(scheduler_calls)!r}, 'a') as log:\n"
            "    log.write(json.dumps(sys.argv) + '\\n')\n"
            "print('PID Status Label\\n120 0 com.example.desktop-helper')\n"
        )
        executable.chmod(0o755)

    task = "Keep these exact words.\nSecond line: $HOME; `literal`\n"
    task_argument = task
    if task_from_file:
        task_file = tmp_path / "requested task.txt"
        task_file.write_text(task)
        task_argument = str(task_file)
    runs = tmp_path / "records"
    environment = {
        "HOME": str(home), "PATH": f"{binary}:/usr/bin:/bin",
        "FACTORY_PYTHON": sys.executable, "FACTORY_VALIDATOR_AGENT": "codex",
        "FACTORY_ORCHESTRATOR_AGENT": "agy", "FACTORY_SEAT_BILLING": "plan",
        "FACTORY_LANE_SANDBOX": "on", "TERM": "xterm-256color",
    }
    result = subprocess.run(
        ["/bin/bash", str(FACTORY), "ordinary", task_argument,
         "--repo", str(repository), "--runs", str(runs)],
        cwd=tmp_path, env=environment, text=True, capture_output=True, timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not scheduler_calls.exists(), "startup contacted the machine's job scheduler"
    root = runs / "ordinary"
    assert (root / "TASK.md").read_bytes() == task.encode()
    record = json.loads((root / "harness.json").read_text())
    assert record["repository"] == str(repository.resolve())
    assert record["resolved_commit"] == commit
    assert record["resolved_tree"] == tree
    launches = [json.loads(line) for line in calls.read_text().splitlines()]
    coordinator_names = {
        call[call.index("-n") + 1] for call in launches if "-n" in call
    }
    assert {"validator", "orchestrator"} <= coordinator_names
    assert _git(repository, "rev-parse", "HEAD") == commit
    assert _git(repository, "status", "--porcelain=v1") == status
    assert source.read_text() == "unrelated work in progress\n"
    assert untracked.read_text() == "retain unrelated notes\n"
    assert not list(tmp_path.rglob("schedule.registry"))
    assert not (tmp_path / "DIRECTIVES").exists()
