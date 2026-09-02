from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

import factory_runtime.lane_repository as lane_repository
from factory_runtime.lane_repository import (
    LaneRepositoryError,
    freeze_lane_repository,
    validate_standalone_repository,
)


def standalone_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "lane"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    return repo


def test_standalone_lane_requires_local_git_and_common_directories(tmp_path: Path) -> None:
    repo = standalone_repo(tmp_path)

    validated = validate_standalone_repository(repo)

    assert validated.root == repo.resolve()
    assert validated.git_directory == (repo / ".git").resolve()
    assert validated.common_directory == (repo / ".git").resolve()


def test_linked_worktree_style_git_file_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "lane"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /outside/shared/worktrees/lane\n", encoding="utf-8")

    with pytest.raises(LaneRepositoryError, match="gitdir files are refused"):
        validate_standalone_repository(repo)


def test_plain_export_never_invokes_git_and_excludes_agent_owned_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = standalone_repo(tmp_path)
    executable = repo / "run.sh"
    executable.write_text("#!/bin/sh\necho safe\n", encoding="utf-8")
    executable.chmod(0o755)
    (repo / "answer.txt").write_text("agent output\n", encoding="utf-8")

    def forbid_git(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("host Git was invoked after lane handoff")

    monkeypatch.setattr(lane_repository.subprocess, "run", forbid_git)
    export = freeze_lane_repository(
        repo,
        tmp_path / "run" / "snapshots",
        durable_through=tmp_path,
    )

    manifest = json.loads(export.frozen_tree.manifest_path.read_text(encoding="utf-8"))
    paths = {row["path"] for row in manifest["files"]}
    assert export.excluded_entries == (".git",)
    assert paths == {"answer.txt", "run.sh"}
    assert not any(part.casefold() == ".git" for path in paths for part in Path(path).parts)
    frozen_mode = stat.S_IMODE((export.frozen_tree.files_directory / "run.sh").stat().st_mode)
    assert frozen_mode & stat.S_IXUSR
    assert not frozen_mode & stat.S_IWUSR


def test_plain_export_rejects_links_nested_git_and_portable_name_collisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = standalone_repo(tmp_path)
    (repo / "payload").write_text("bytes", encoding="utf-8")
    os.symlink(repo / "payload", repo / "linked")
    with pytest.raises(LaneRepositoryError, match="linked"):
        freeze_lane_repository(repo, tmp_path / "store-a", durable_through=tmp_path)

    (repo / "linked").unlink()
    nested = repo / "vendor" / ".git"
    nested.mkdir(parents=True)
    with pytest.raises(LaneRepositoryError, match="nested Git metadata"):
        freeze_lane_repository(repo, tmp_path / "store-b", durable_through=tmp_path)

    nested.rmdir()
    nested.parent.rmdir()
    real_listdir = lane_repository.os.listdir
    first = True

    def colliding_listdir(path: object) -> list[str]:
        nonlocal first
        if first:
            first = False
            return [".git", "payload", "Readme", "README"]
        return list(real_listdir(path))

    monkeypatch.setattr(lane_repository.os, "listdir", colliding_listdir)
    with pytest.raises(LaneRepositoryError, match="portable-name collision"):
        freeze_lane_repository(repo, tmp_path / "store-c", durable_through=tmp_path)
