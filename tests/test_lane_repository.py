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
    read_freeze_excludes,
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


def test_plain_export_leaves_out_the_lanes_kindex_runtime_state(tmp_path: Path) -> None:
    """The lane's Kindex store and its Kinbase evidence copy are runtime state, not work: they
    stay in the retained repository for the Validator and never enter the judged snapshot."""

    repo = standalone_repo(tmp_path)
    (repo / "answer.txt").write_text("agent output\n", encoding="utf-8")
    (repo / ".kin" / "local" / "kindex").mkdir(parents=True)
    (repo / ".kin" / "local" / "kindex" / "kindex.db").write_bytes(b"sqlite")
    (repo / ".kin" / "events" / "00").mkdir(parents=True)
    (repo / ".kin" / "events" / "00" / "e.json").write_text("{}", encoding="utf-8")
    (repo / ".kin" / "knowledge.jsonl").write_text('{"title":"tracked"}\n', encoding="utf-8")

    export = freeze_lane_repository(repo, tmp_path / "run" / "snapshots", durable_through=tmp_path)

    manifest = json.loads(export.frozen_tree.manifest_path.read_text(encoding="utf-8"))
    paths = {row["path"] for row in manifest["files"]}
    assert paths == {"answer.txt", ".kin/knowledge.jsonl"}
    assert set(export.excluded_entries) == {".git", ".kin/local", ".kin/events"}
    assert (repo / ".kin" / "local" / "kindex" / "kindex.db").exists()


def _declared(tmp_path: Path, *lines: str) -> Path:
    conf = tmp_path / "projection.conf"
    conf.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return conf


def _node_modules_repo(tmp_path: Path) -> Path:
    repo = standalone_repo(tmp_path)
    (repo / "app.ts").write_text("x", encoding="utf-8")
    (repo / "node_modules").mkdir()
    os.symlink(repo / "app.ts", repo / "node_modules" / "link")
    return repo


def test_declared_freeze_exclude_skips_a_symlink_tree_unread(tmp_path: Path) -> None:
    repo = _node_modules_repo(tmp_path)
    exclude = read_freeze_excludes(_declared(tmp_path, "freeze-exclude: node_modules"))

    export = freeze_lane_repository(
        repo, tmp_path / "store", durable_through=tmp_path, exclude=exclude
    )

    manifest = json.loads(export.frozen_tree.manifest_path.read_text(encoding="utf-8"))
    assert {row["path"] for row in manifest["files"]} == {"app.ts"}
    assert export.excluded_entries == (".git", "node_modules")


def test_undeclared_symlink_is_refused_and_lane_gitignore_has_no_effect(tmp_path: Path) -> None:
    repo = _node_modules_repo(tmp_path)
    (repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    with pytest.raises(LaneRepositoryError, match="symbolic links"):
        freeze_lane_repository(repo, tmp_path / "store-a", durable_through=tmp_path)

    # a declared exclude covers only its own top-level path, not other symlinks
    os.symlink(repo / "app.ts", repo / "other-link")
    exclude = read_freeze_excludes(_declared(tmp_path, "freeze-exclude: node_modules"))
    with pytest.raises(LaneRepositoryError, match="symbolic links"):
        freeze_lane_repository(
            repo, tmp_path / "store-b", durable_through=tmp_path, exclude=exclude
        )


def test_freeze_exclude_rejects_unsafe_values(tmp_path: Path) -> None:
    for bad in ("a/b", "..", ".git", ""):
        with pytest.raises(LaneRepositoryError):
            read_freeze_excludes(_declared(tmp_path, f"freeze-exclude: {bad}"))
