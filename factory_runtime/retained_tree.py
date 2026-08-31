"""Fail-closed traversal for retained filesystem trees.

Callers that retain or scan a tree must not silently skip symlink entries: an
escaping link is a live pointer outside the retained proof, while a dangling
link has no target that can be proved.  This module validates both file and
directory links before returning the regular files covered by the traversal.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


class RetainedTreeError(ValueError):
    """A retained tree cannot be safely traversed or proved."""


def retained_regular_files(
    root: str | Path,
    *,
    permit_contained_symlinks: bool = False,
) -> tuple[Path, ...]:
    """Return regular files only after proving the retained tree is safe.

    A symlink is either forbidden by the caller's retention policy or, when
    admitted, must resolve strictly within ``root`` to a regular file or
    directory visited by this traversal.  The returned files are the real
    in-tree files, so a caller scanning them covers every admitted link target.
    """

    root_path = Path(root).absolute()
    if root_path.is_symlink() or not root_path.is_dir():
        raise RetainedTreeError(
            f"retained tree root is missing, linked, or not a directory: {root_path}"
        )
    try:
        resolved_root = root_path.resolve(strict=True)
    except OSError as exc:
        raise RetainedTreeError(
            f"retained tree root cannot be resolved: {root_path}: {exc}"
        ) from exc

    regular_files: list[Path] = []
    resolved_files: set[Path] = set()
    resolved_directories: set[Path] = {resolved_root}
    symlinks: list[Path] = []

    def walk_error(error: OSError) -> None:
        raise RetainedTreeError(f"retained tree cannot be enumerated: {root_path}: {error}")

    try:
        for base, directories, names in os.walk(
            root_path,
            followlinks=False,
            onerror=walk_error,
        ):
            base_path = Path(base)
            directories.sort()
            names.sort()
            for name in directories:
                path = base_path / name
                if path.is_symlink():
                    symlinks.append(path)
                else:
                    resolved_directories.add(path.resolve(strict=True))
            for name in names:
                path = base_path / name
                if path.is_symlink():
                    symlinks.append(path)
                    continue
                mode = path.lstat().st_mode
                if not stat.S_ISREG(mode):
                    relative = path.relative_to(root_path).as_posix()
                    raise RetainedTreeError(
                        f"retained tree contains a non-regular entry: {relative}"
                    )
                regular_files.append(path)
                resolved_files.add(path.resolve(strict=True))
    except OSError as exc:
        raise RetainedTreeError(f"retained tree cannot be enumerated: {root_path}: {exc}") from exc

    for path in symlinks:
        relative = path.relative_to(root_path).as_posix()
        if not permit_contained_symlinks:
            raise RetainedTreeError(f"retained tree contains a forbidden symlink: {relative}")
        try:
            target = path.resolve(strict=True)
        except OSError as exc:
            raise RetainedTreeError(
                f"retained tree contains an unresolvable symlink: {relative}: {exc}"
            ) from exc
        if not target.is_relative_to(resolved_root):
            raise RetainedTreeError(
                f"retained tree contains a symlink escaping its root: {relative}"
            )
        if target in resolved_files or target in resolved_directories:
            continue
        raise RetainedTreeError(
            f"retained tree symlink target is not covered by the traversal: {relative}"
        )

    return tuple(sorted(regular_files, key=lambda path: path.relative_to(root_path).as_posix()))
