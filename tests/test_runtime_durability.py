from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

import factory_runtime.durability as durability_module
from factory_runtime.durability import DurabilityError, fsync_directory_chain


def test_fsync_directory_chain_commits_every_directory_through_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = tmp_path / "runs"
    leaf = boundary / "run-1" / "evidence" / "subject"
    leaf.mkdir(parents=True)
    real_fsync = os.fsync
    synced: list[int] = []

    def track_fsync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        assert stat.S_ISDIR(metadata.st_mode)
        synced.append(metadata.st_ino)
        real_fsync(descriptor)

    monkeypatch.setattr(durability_module.os, "fsync", track_fsync)

    fsync_directory_chain(leaf, through=boundary)

    expected = [
        leaf.stat().st_ino,
        leaf.parent.stat().st_ino,
        leaf.parent.parent.stat().st_ino,
        boundary.stat().st_ino,
    ]
    assert synced == expected


def test_fsync_directory_chain_rejects_a_leaf_outside_the_boundary(tmp_path: Path) -> None:
    boundary = tmp_path / "runs"
    boundary.mkdir()
    outside = tmp_path / "other"
    outside.mkdir()

    with pytest.raises(DurabilityError, match="outside declared root"):
        fsync_directory_chain(outside, through=boundary)


def test_fsync_directory_chain_rejects_a_symlinked_directory(tmp_path: Path) -> None:
    boundary = tmp_path / "runs"
    real_leaf = boundary / "real"
    real_leaf.mkdir(parents=True)
    linked_leaf = boundary / "linked"
    linked_leaf.symlink_to(real_leaf, target_is_directory=True)

    with pytest.raises(DurabilityError, match="could not fsync durability directory"):
        fsync_directory_chain(linked_leaf, through=boundary)
