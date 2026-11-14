"""The public candidate validator must preserve unchanged target-owned config."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)


@pytest.mark.parametrize("name", ["hooks.json", "mcp.json", "cli.json"])
def test_unchanged_target_cursor_config_is_not_a_lane_artifact(
    tmp_path: Path, name: str,
) -> None:
    repo = tmp_path / "target"
    repo.mkdir()
    run("git", "init", "-q", str(repo))
    config = repo / ".cursor" / name
    config.parent.mkdir()
    original = '{"operator-owned": "lane-kindex", "note": "keep this unchanged"}\n'
    config.write_text(original)
    run("git", "add", ".", cwd=repo)
    run("git", "-c", "user.name=Acceptance", "-c", "user.email=test@example.invalid",
        "commit", "-qm", "target-owned config", cwd=repo)
    commit = run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    task = tmp_path / "task.txt"
    task.write_text("Validate unchanged target configuration only.")
    runs = tmp_path / "runs"
    run(sys.executable, str(ROOT / "harness/run_record.py"), "create", "--runs", str(runs),
        "--run", "preserve", "--repo", str(repo), "--task-file", str(task))
    marker = tmp_path / "project-check-ran"
    result = subprocess.run(
        [sys.executable, str(ROOT / "harness/validate_candidate.py"), "preserve", commit,
         "--runs", str(runs), "--repo", str(repo), "--", sys.executable, "-c",
         f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.read_text() == "ran"
    assert config.read_text() == original
    assert run("git", "status", "--porcelain", cwd=repo).stdout == ""
