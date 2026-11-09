"""The public CLI cannot reopen the removed authorization workflow.

The 2026-11-09 removal request makes these commands unavailable, including their help
entrypoints. Each assertion would turn red if its obsolete command were accepted by
the parser again. Run in an empty private directory; no run, key or machine job is used.
"""

from pathlib import Path

import pytest

from factory_runtime.cli import main

RETIRED_COMMANDS = (
    "init", "record-statement", "record-ruling", "verify-genesis",
    "authorize-target-resolution", "resolve-target", "authorize-change", "ratify-phase",
    "derive-resume-checkpoint", "verify-resume-checkpoint", "prepare-lane-dispatch",
    "run-model", "execute-broker-handoff", "tessera-wrap", "promote", "build-and-validate",
    "chain-repair", "ledger-unlock",
    "status", "pass-count", "ci-retain", "readiness-issues", "signal-knobs",
    "rebuild-projection", "verify-target-state", "verify-execution-request", "verify-resources",
    "qualify-state", "bundle-runner-projection", "bundle-orchestrator-projection",
    "record-resource", "disposition-resource", "assemble-promotion-inputs",
)


@pytest.mark.parametrize("command", RETIRED_COMMANDS)
def test_retired_workflow_command_is_not_a_public_entrypoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    # Help used to accept these commands without needing a real signed run. Its rejection
    # now establishes that no legacy command parser or action is reachable by this name.
    with pytest.raises(SystemExit) as stopped:
        main([command, "--help"])
    assert stopped.value.code == 2
    assert f"invalid choice: '{command}'" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []
