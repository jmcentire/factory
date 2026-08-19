"""Local-POSIX durability primitives shared by runtime evidence publishers.

An fsynced file is not enough when one of its containing directories was just created.  These
helpers commit directory entries from the evidence leaf back through a caller-declared durable
root without resolving symlinks or silently widening the filesystem boundary.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


class DurabilityError(OSError):
    """The runtime could not prove local evidence-directory durability."""


def _absolute_lexical(path: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def fsync_directory(path: str | Path) -> None:
    """Fsync one exact real directory and reject path replacement during the operation."""

    directory = _absolute_lexical(path)
    descriptor = -1
    try:
        descriptor = os.open(
            directory,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            raise DurabilityError(f"durability path is not a directory: {directory}")
        os.fsync(descriptor)
        installed = os.lstat(directory)
        if stat.S_ISLNK(installed.st_mode) or (
            installed.st_dev,
            installed.st_ino,
        ) != (opened.st_dev, opened.st_ino):
            raise DurabilityError(f"durability directory changed during fsync: {directory}")
    except OSError as exc:
        if isinstance(exc, DurabilityError):
            raise
        raise DurabilityError(f"could not fsync durability directory {directory}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def fsync_directory_chain(start: str | Path, *, through: str | Path) -> None:
    """Fsync ``start`` and every parent through an already durable boundary."""

    leaf = _absolute_lexical(start)
    boundary = _absolute_lexical(through)
    try:
        within_boundary = os.path.commonpath((leaf, boundary)) == os.fspath(boundary)
    except ValueError as exc:
        raise DurabilityError("durability directory chain crosses filesystem roots") from exc
    if not within_boundary:
        raise DurabilityError(f"durability directory {leaf} is outside declared root {boundary}")
    current = leaf
    while True:
        fsync_directory(current)
        if current == boundary:
            return
        parent = current.parent
        if parent == current:
            raise DurabilityError(
                f"durability directory chain did not reach declared root {boundary}"
            )
        current = parent
