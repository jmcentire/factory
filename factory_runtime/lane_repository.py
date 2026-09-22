"""Standalone Git lanes that become agent-owned, then cross as plain frozen trees.

Git is intentionally useful inside an author lane: the agent may stage, inspect,
and checkpoint its own work.  Once the agent starts, however, host code treats
the whole repository (including Git metadata, refs, hooks, and config) as
untrusted.  Export walks regular files without invoking Git, excludes the root
``.git`` entry, and publishes only a content-addressed snapshot.

This is an audited coordination workflow, not OS isolation.  A separate uid,
container, or qualified runner is required to contain a malicious same-user
process.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass

from factory_runtime.snapshot import FrozenTree, SnapshotError, freeze_tree


class LaneRepositoryError(RuntimeError):
    """A lane repository or its plain-tree crossing was unsafe."""


@dataclass(frozen=True)
class StandaloneRepository:
    root: pathlib.Path
    git_directory: pathlib.Path
    common_directory: pathlib.Path


@dataclass(frozen=True)
class LaneExport:
    frozen_tree: FrozenTree
    excluded_entries: tuple[str, ...]
    source_file_count: int
    source_bytes: int


def _run_git(root: pathlib.Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LaneRepositoryError(f"Git preflight could not run: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Git preflight failed").strip()
        raise LaneRepositoryError(detail)
    return result.stdout.strip()


def validate_standalone_repository(source: str | pathlib.Path) -> StandaloneRepository:
    """Use Git only before handoff to prove metadata is local to one lane."""

    requested = pathlib.Path(source)
    if requested.is_symlink():
        raise LaneRepositoryError("lane repository root may not be a symlink")
    try:
        root = requested.resolve(strict=True)
    except OSError as exc:
        raise LaneRepositoryError(f"lane repository is missing: {requested}") from exc
    if not root.is_dir():
        raise LaneRepositoryError("lane repository root is not a directory")
    git_entry = root / ".git"
    try:
        git_metadata = os.lstat(git_entry)
    except OSError as exc:
        raise LaneRepositoryError("lane must have a local .git directory") from exc
    if stat.S_ISLNK(git_metadata.st_mode) or not stat.S_ISDIR(git_metadata.st_mode):
        raise LaneRepositoryError("linked worktrees and gitdir files are refused")

    top = pathlib.Path(_run_git(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    git_directory = pathlib.Path(
        _run_git(root, "rev-parse", "--path-format=absolute", "--absolute-git-dir")
    ).resolve(strict=True)
    common_directory = pathlib.Path(
        _run_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    ).resolve(strict=True)
    expected_git = git_entry.resolve(strict=True)
    if top != root:
        raise LaneRepositoryError("lane path is not the repository top level")
    if git_directory != expected_git or common_directory != expected_git:
        raise LaneRepositoryError("Git directory or common directory escapes the standalone lane")
    return StandaloneRepository(root, git_directory, common_directory)


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _portable_name(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold().rstrip(" .")


def _capture_plain_tree(
    root: pathlib.Path,
    *,
    max_files: int,
    max_file_bytes: int,
    max_total_bytes: int,
    max_depth: int,
) -> tuple[dict[str, tuple[bytes, int]], tuple[str, ...], int]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        root_fd = os.open(root, flags)
    except OSError as exc:
        raise LaneRepositoryError(f"lane export root cannot be opened safely: {exc}") from exc
    captured: dict[str, tuple[bytes, int]] = {}
    excluded: list[str] = []
    total_bytes = 0
    root_device = os.fstat(root_fd).st_dev

    def walk(descriptor: int, prefix: str, depth: int) -> None:
        nonlocal total_bytes
        if depth > max_depth:
            raise LaneRepositoryError("lane export exceeds its depth ceiling")
        before = os.fstat(descriptor)
        if not stat.S_ISDIR(before.st_mode) or before.st_dev != root_device:
            raise LaneRepositoryError("lane export crossed a device or non-directory boundary")
        names = sorted(os.listdir(descriptor))
        normalized: dict[str, str] = {}
        for name in names:
            if not name or name in {".", ".."} or "/" in name or "\x00" in name:
                raise LaneRepositoryError("lane export contains an unsafe entry name")
            portable = _portable_name(name)
            if not portable:
                raise LaneRepositoryError("lane export contains a non-portable empty name")
            prior = normalized.get(portable)
            if prior is not None:
                raise LaneRepositoryError(
                    f"lane export contains a portable-name collision: {prior!r}, {name!r}"
                )
            normalized[portable] = name
        for name in names:
            relative = f"{prefix}/{name}" if prefix else name
            if _portable_name(name) == ".git":
                if prefix:
                    raise LaneRepositoryError(
                        f"nested Git metadata is refused rather than silently skipped: {relative}"
                    )
                excluded.append(relative)
                continue
            child = -1
            try:
                child = os.open(
                    name,
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=descriptor,
                )
                opened = os.fstat(child)
                installed = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISLNK(installed.st_mode) or (
                    installed.st_dev,
                    installed.st_ino,
                ) != (opened.st_dev, opened.st_ino):
                    raise LaneRepositoryError(f"lane export entry changed or is linked: {relative}")
                if opened.st_dev != root_device:
                    raise LaneRepositoryError(f"lane export crosses a device boundary: {relative}")
                mode = stat.S_IMODE(opened.st_mode)
                if mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                    raise LaneRepositoryError(
                        f"lane export contains privileged mode bits: {relative}"
                    )
                if stat.S_ISDIR(opened.st_mode):
                    walk(child, relative, depth + 1)
                elif stat.S_ISREG(opened.st_mode):
                    if len(captured) >= max_files:
                        raise LaneRepositoryError("lane export exceeds its file-count ceiling")
                    if opened.st_size > max_file_bytes:
                        raise LaneRepositoryError(
                            f"lane export file exceeds its byte ceiling: {relative}"
                        )
                    chunks: list[bytes] = []
                    read_bytes = 0
                    while chunk := os.read(child, min(1024 * 1024, max_file_bytes + 1)):
                        chunks.append(chunk)
                        read_bytes += len(chunk)
                        if read_bytes > max_file_bytes:
                            raise LaneRepositoryError(
                                f"lane export file exceeds its byte ceiling: {relative}"
                            )
                    after = os.fstat(child)
                    if _identity(opened) != _identity(after):
                        raise LaneRepositoryError(
                            f"lane export file changed while read: {relative}"
                        )
                    total_bytes += read_bytes
                    if total_bytes > max_total_bytes:
                        raise LaneRepositoryError("lane export exceeds its total-byte ceiling")
                    captured[relative] = (b"".join(chunks), mode)
                else:
                    raise LaneRepositoryError(f"lane export contains a special entry: {relative}")
            except OSError as exc:
                raise LaneRepositoryError(f"lane export cannot read {relative}: {exc}") from exc
            finally:
                if child >= 0:
                    os.close(child)
        after = os.fstat(descriptor)
        if _identity(before) != _identity(after) or names != sorted(os.listdir(descriptor)):
            raise LaneRepositoryError("lane export directory changed during capture")

    try:
        walk(root_fd, "", 0)
    finally:
        os.close(root_fd)
    if excluded != [".git"]:
        raise LaneRepositoryError("lane export did not find exactly one root .git entry")
    return captured, tuple(excluded), total_bytes


def freeze_lane_repository(
    source: str | pathlib.Path,
    store_root: str | pathlib.Path,
    *,
    durable_through: str | pathlib.Path,
    max_files: int = 10_000,
    max_file_bytes: int = 16 * 1024 * 1024,
    max_total_bytes: int = 256 * 1024 * 1024,
    max_depth: int = 64,
) -> LaneExport:
    """Freeze only regular working-tree bytes, never consulting agent-owned Git state."""

    source_path = pathlib.Path(source)
    if source_path.is_symlink() or not source_path.is_dir():
        raise LaneRepositoryError("lane export source is not a real directory")
    captured, excluded, total_bytes = _capture_plain_tree(
        source_path,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
        max_depth=max_depth,
    )
    store = pathlib.Path(store_root)
    boundary = pathlib.Path(durable_through)
    staging_parent = store.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = pathlib.Path(tempfile.mkdtemp(prefix=".lane-export-", dir=staging_parent))
    try:
        for relative, (content, mode) in captured.items():
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(mode & 0o777)
        try:
            frozen = freeze_tree(
                staging,
                store,
                durable_through=boundary,
                allow_empty=True,
            )
        except SnapshotError as exc:
            raise LaneRepositoryError(str(exc)) from exc
    finally:
        shutil.rmtree(staging)
    return LaneExport(frozen, excluded, len(captured), total_bytes)
