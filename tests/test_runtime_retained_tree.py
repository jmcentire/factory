from __future__ import annotations

from pathlib import Path

import pytest

from factory_runtime.retained_tree import RetainedTreeError, retained_regular_files


def test_retained_tree_rejects_an_escaping_file_symlink(tmp_path: Path) -> None:
    root = tmp_path / "retained"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("not retained", encoding="utf-8")
    (root / "escape").symlink_to(outside)

    with pytest.raises(RetainedTreeError, match="symlink escaping"):
        retained_regular_files(root, permit_contained_symlinks=True)


def test_retained_tree_rejects_an_escaping_directory_symlink(tmp_path: Path) -> None:
    root = tmp_path / "retained"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("not retained", encoding="utf-8")
    (root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RetainedTreeError, match="symlink escaping"):
        retained_regular_files(root, permit_contained_symlinks=True)


def test_retained_tree_rejects_an_unresolvable_symlink(tmp_path: Path) -> None:
    root = tmp_path / "retained"
    root.mkdir()
    (root / "missing").symlink_to(root / "not-present")

    with pytest.raises(RetainedTreeError, match="unresolvable symlink"):
        retained_regular_files(root, permit_contained_symlinks=True)


def test_retained_tree_admits_in_tree_file_and_directory_links(tmp_path: Path) -> None:
    root = tmp_path / "retained"
    root.mkdir()
    target = root / "state" / "current.txt"
    target.parent.mkdir()
    target.write_text("covered", encoding="utf-8")
    (root / "current-link").symlink_to(target)
    (root / "state-link").symlink_to(target.parent, target_is_directory=True)

    assert retained_regular_files(root, permit_contained_symlinks=True) == (target,)


def test_snapshot_policy_still_forbids_even_an_in_tree_symlink(tmp_path: Path) -> None:
    root = tmp_path / "retained"
    root.mkdir()
    target = root / "covered.txt"
    target.write_text("covered", encoding="utf-8")
    (root / "pointer").symlink_to(target)

    with pytest.raises(RetainedTreeError, match="forbidden symlink"):
        retained_regular_files(root)
