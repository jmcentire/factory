import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness import run_record
from tests.test_host_git import committed_repository, git

LINK = ".claude/skills/example"
TARGET = "../../.agents/skills/example"
SKILL = ".agents/skills/example/SKILL.md"
CONTENT = "# Committed example skill\n"
ENDGAME = Path(__file__).resolve().parents[1] / "harness/endgame.sh"


@pytest.mark.parametrize("target_kind", ["directory", "escaping", "chained"])
def test_endgame_runs_checks_only_for_a_contained_directory_link(tmp_path, target_kind):
    repository, _ = committed_repository(tmp_path, {SKILL: CONTENT})
    link = repository / LINK
    link.parent.mkdir(parents=True)
    target = TARGET
    if target_kind == "escaping":
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "SKILL.md").write_text(CONTENT)
        target = "../../../outside"
    elif target_kind == "chained":
        (repository / ".agents/skills/alias").symlink_to("example", target_is_directory=True)
        target = "../../.agents/skills/alias"
    link.symlink_to(target, target_is_directory=True)
    git(repository, "add", "-A")
    git(repository, "commit", "-qm", "Add skill directory link")
    commit = git(repository, "rev-parse", "HEAD")
    runs = tmp_path / "runs"
    root = run_record.create(runs, "task", repository, b"Validate the committed skill link")
    (repository / SKILL).write_text("Uncommitted replacement\n")
    marker = tmp_path / "checks-ran"
    command = [
        sys.executable,
        "-c",
        "from pathlib import Path; "
        f"Path({str(marker)!r}).touch(); "
        f"link = Path({LINK!r}); "
        "assert link.is_symlink(); "
        f"assert str(link.readlink()) == {TARGET!r}; "
        f"assert (link / 'SKILL.md').read_text() == {CONTENT!r}; "
        "print((link / 'SKILL.md').read_text(), end='')",
    ]
    process = subprocess.run(
        [
            "bash", str(ENDGAME), "task", commit,
            "--repo", str(repository), "--runs", str(runs), "--", *command,
        ],
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path),
             "FACTORY_PYTHON": sys.executable},
        capture_output=True,
        text=True,
        timeout=30,
    )
    result_path, = (root / "validation").glob("*/result.json")
    result = json.loads(result_path.read_text())
    if target_kind == "directory":
        assert process.returncode == 0, (process.stdout, process.stderr, result)
        assert result["status"] == "tests-passed"
        assert result["candidate"]["commit"] == commit
        assert marker.exists()
        assert (result_path.parent / "output.log").read_text() == CONTENT
    else:
        assert process.returncode == 1, (process.stdout, process.stderr, result)
        assert result["status"] == "validation-failed"
        assert result["exit_code"] == 70
        assert result["error"]
        assert not marker.exists()
