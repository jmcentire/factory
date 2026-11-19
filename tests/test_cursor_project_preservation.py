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


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repository), "-c", "user.name=t", "-c",
                           "user.email=t@example.invalid", *args],
                          capture_output=True, text=True, check=True).stdout


def test_cursor_lane_commits_drop_cursors_coauthor_trailer(tmp_path: Path) -> None:
    # Seen in a Factory run: every Cursor lane commit carried `Co-authored-by: Cursor`, which
    # Cursor adds with `git commit --trailer` (cursor-agent 2026.11.19).
    sys.path.insert(0, str(ROOT / "harness"))
    import native_session
    _git(tmp_path, "init", "-q")
    native_session.install_cursor_commit_hook(tmp_path)
    (tmp_path / "a.txt").write_text("a\n")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "--trailer", native_session.CURSOR_COAUTHOR,
         "--trailer", "FactoryRun: r1", "-m", "add a")
    assert _git(tmp_path, "log", "-1", "--format=%B").strip() == "add a\n\nFactoryRun: r1"


def test_a_repositorys_own_commit_hook_is_never_replaced(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "harness"))
    import native_session
    _git(tmp_path, "init", "-q")
    hook = tmp_path / ".git" / "hooks" / "commit-msg"
    hook.write_text("#!/bin/sh\nexit 0\n")
    native_session.install_cursor_commit_hook(tmp_path)
    assert hook.read_text() == "#!/bin/sh\nexit 0\n"


def test_lane_kindex_alone_is_approved_from_the_lane_workspace(tmp_path: Path) -> None:
    # Seen in a Factory run: the Cursor Tester's pane waited on "Approve all servers".
    sys.path.insert(0, str(ROOT / "harness"))
    import native_session
    calls = tmp_path / "calls"
    cursor = tmp_path / "agent"
    cursor.write_text(f'#!/bin/sh\necho "$PWD $*" >> {calls}\n')
    cursor.chmod(0o755)
    workspace = tmp_path / "lane"
    workspace.mkdir()
    native_session.approve_lane_kindex(str(cursor), workspace)
    assert calls.read_text() == f"{workspace.resolve()} mcp enable lane-kindex\n"


@pytest.mark.parametrize("script", ["#!/bin/sh\nexit 3\n", None])
def test_a_failed_or_missing_approval_never_stops_the_lane(tmp_path: Path, script) -> None:
    sys.path.insert(0, str(ROOT / "harness"))
    import native_session
    cursor = tmp_path / "agent"
    if script:
        cursor.write_text(script)
        cursor.chmod(0o755)
    native_session.approve_lane_kindex(str(cursor), tmp_path)


def test_a_hanging_approval_is_abandoned(tmp_path: Path, monkeypatch) -> None:
    sys.path.insert(0, str(ROOT / "harness"))
    import native_session

    def hang(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("agent", 20)
    monkeypatch.setattr(native_session.subprocess, "run", hang)
    native_session.approve_lane_kindex("agent", tmp_path)


@pytest.mark.parametrize("has_kindex", [True, False])
def test_cursor_plan_approves_only_when_the_lane_has_its_kindex(
        tmp_path: Path, monkeypatch, has_kindex: bool) -> None:
    sys.path.insert(0, str(ROOT / "harness"))
    import native_session
    approved = []
    monkeypatch.setattr(native_session, "approve_lane_kindex",
                        lambda executable, workspace: approved.append((executable, workspace)))
    directory, workspace = tmp_path / "lane-state", tmp_path / "lane"
    directory.mkdir()
    workspace.mkdir()
    if has_kindex:
        (directory / "cursor-mcp.json").write_text('{"mcpServers": {"lane-kindex": {}}}')
    native_session.cursor_plan([], executable="/x/agent", directory=directory, slot="tester",
                               workspace=workspace)
    assert approved == ([("/x/agent", workspace)] if has_kindex else [])
