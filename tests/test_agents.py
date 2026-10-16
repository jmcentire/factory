"""Any harness in any seat: the registry, the lane wrapper's credential file, and preflight."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness import agents, lane_agent

REPO = Path(__file__).resolve().parents[1]


def test_registry_lists_every_harness_the_lanes_accept() -> None:
    for name in ("claude", "codex", "agy", "prime-agent", "cursor-agent"):
        assert name in agents.HARNESSES
    for name in ("claude", "agy", "prime-agent"):
        assert lane_agent.agent(name).auth == "registry"


def test_turn_cli_prints_nul_separated_argv(tmp_path: Path) -> None:
    out = subprocess.run(
        [sys.executable, str(REPO / "harness" / "agents.py"), "turn", "--agent", "claude",
         "--model", "claude-haiku-5-5", "--cwd", str(tmp_path)],
        capture_output=True, check=True,
    ).stdout.decode().split("\0")
    assert out[0] == "stdin" and out[1] == "claude" and "claude-haiku-5-5" in out


def test_credentials_reach_the_child_only_through_an_owner_only_file(tmp_path: Path) -> None:
    creds = tmp_path / "creds.env"
    creds.write_text("ANTHROPIC_API_KEY=sk-test-value\n", encoding="utf-8")
    creds.chmod(0o600)
    probe = tmp_path / "probe.out"
    rc = lane_agent.run_lane(
        [sys.executable, "-c",
         f"import os; open({str(probe)!r}, 'w').write(os.environ.get('ANTHROPIC_API_KEY', ''))"],
        slot="coder", log=tmp_path / "lane.log", exits=tmp_path / "exits.jsonl", env_file=creds,
    )
    assert rc == 0 and probe.read_text() == "sk-test-value"
    assert "sk-test-value" not in (tmp_path / "lane.log").read_text()
    creds.chmod(0o644)
    with pytest.raises(ValueError):
        lane_agent.run_lane([sys.executable, "-c", "pass"], slot="coder",
                            log=tmp_path / "lane.log", exits=tmp_path / "exits.jsonl",
                            env_file=creds)


def test_registry_preflight_proves_the_cli_runs(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "1.0.0", "")

    assert lane_agent.preflight("agy", environ=dict(os.environ), runner=runner) == \
        "registry-cli-runs"
    assert calls == [["agy", "--version"]]
