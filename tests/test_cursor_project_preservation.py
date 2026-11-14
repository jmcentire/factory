"""Public Cursor admission must preserve existing project configuration."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def check(workspace: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "harness/native_session.py"),
         "check-project", "--workspace", str(workspace)],
        capture_output=True, text=True, check=False,
    )


def test_empty_or_unrelated_cursor_project_is_preserved(tmp_path: Path) -> None:
    assert check(tmp_path).returncode == 0
    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    custom = rules / "operator.mdc"
    custom.write_text("Keep my existing project rules.\n")
    assert check(tmp_path).returncode == 0
    assert custom.read_text() == "Keep my existing project rules.\n"


@pytest.mark.parametrize("name", ["hooks.json", "mcp.json", "cli.json"])
@pytest.mark.parametrize("is_link", [False, True])
def test_existing_cursor_configuration_refuses_without_mutation(
    tmp_path: Path, name: str, is_link: bool,
) -> None:
    directory = tmp_path / ".cursor"
    directory.mkdir()
    occupied = directory / name
    if is_link:
        occupied.symlink_to(tmp_path / "absent-target")
    else:
        occupied.write_bytes(b'{"operator-owned":"do not replace"}\n')
    result = check(tmp_path)
    assert result.returncode == 70, result.stdout + result.stderr
    assert name in result.stdout + result.stderr
    if is_link:
        assert occupied.is_symlink()
        assert occupied.readlink() == tmp_path / "absent-target"
    else:
        assert occupied.read_bytes() == b'{"operator-owned":"do not replace"}\n'
    assert sorted(p.name for p in directory.iterdir()) == [name]


def test_symlinked_cursor_directory_is_never_followed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "operator-config"
    external.mkdir()
    sentinel = external / "keep"
    sentinel.write_text("unchanged")
    (workspace / ".cursor").symlink_to(external, target_is_directory=True)
    result = check(workspace)
    assert result.returncode == 70, result.stdout + result.stderr
    assert (workspace / ".cursor").is_symlink()
    assert sentinel.read_text() == "unchanged"
    assert list(external.iterdir()) == [sentinel]
