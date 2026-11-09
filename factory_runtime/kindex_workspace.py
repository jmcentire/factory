"""Keep Factory scratch trees out of Kindex's durable background indexing.

Kindex inherits this presence marker through ancestor directories. It does not
disable private queries, captures or knowledge exports. Only Factory-owned
workspace creation calls this helper; canonical repositories are never marked.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

NO_INDEX_MARKER = ".kindex-no-index"


def mark_no_index(root: Path) -> bool:
    """Create runtime metadata without following links or replacing existing bytes."""
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            descriptor = os.open(
                NO_INDEX_MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600, dir_fd=directory,
            )
        except FileExistsError:
            info = os.stat(NO_INDEX_MARKER, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"Kindex exclusion marker is not a regular file: {root}") from None
            return False
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(directory)
        return True
    finally:
        os.close(directory)


def _paths(source: Path, destination: Path) -> tuple[Path, Path]:
    """Refuse aliases and overlap before changing any workspace bytes."""
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ValueError(f"workspace source must be a directory: {source}")
    if destination.is_symlink():
        raise ValueError(f"workspace destination must not be a symlink: {destination}")
    destination = destination.resolve()

    def identity(path: Path) -> tuple[int, int] | None:
        try:
            info = path.stat()
        except FileNotFoundError:
            return None
        return info.st_dev, info.st_ino

    # resolve() does not normalize case aliases on case-insensitive filesystems.
    # Compare physical ancestry too, including existing parents of a new target.
    source_id = identity(source)
    destination_id = identity(destination)
    source_ancestors = {identity(path) for path in (source, *source.parents)}
    destination_ancestors = {identity(path) for path in (destination, *destination.parents)}
    if (source_id in destination_ancestors
            or destination_id is not None and destination_id in source_ancestors):
        raise ValueError("workspace source and destination must not overlap")
    return source, destination


def _marker_identity(root: Path) -> tuple[int, int]:
    info = (root / NO_INDEX_MARKER).lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Kindex exclusion marker is not a regular file: {root}")
    return info.st_dev, info.st_ino


def _sync_workspace(source: Path, destination: Path, *, delete: bool = False,
                    exclude: tuple[str, ...] = ()) -> Path:
    """Synchronize a caller-designated transient tree without deleting its exclusion.

    Fixed filters hide sender markers and protect receiver markers, including
    during --delete. Caller patterns are explicit exclusions, not legacy rules
    that can become includes or clear the filter list. Arbitrary rsync options
    are not accepted.
    """
    if any(not pattern or pattern.strip() == "!" or any(c in pattern for c in "\0\r\n")
           for pattern in exclude):
        raise ValueError("workspace exclusions must be patterns, not empty or clear-list rules")
    source, destination = _paths(source, destination)
    destination.mkdir(parents=True, exist_ok=True)
    mark_no_index(destination)
    marker = _marker_identity(destination)
    command = ["rsync", "-a", f"--filter=P {NO_INDEX_MARKER}",
               f"--filter=H {NO_INDEX_MARKER}",
               *(f"--filter=- {pattern}" for pattern in exclude)]
    if delete:
        command.append("--delete")
    command.extend(["--", str(source) + "/", str(destination) + "/"])
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if _marker_identity(destination) != marker:
        raise ValueError("workspace exclusion marker changed during synchronization")
    if completed.returncode:
        raise ValueError(f"workspace rsync failed ({completed.returncode}): "
                         f"{completed.stderr.strip()[-2000:]}")
    return destination


def _prepare_scratch(source: Path, tests: Path, destination: Path) -> Path:
    """Reset only the designated scratch contents, leaving its marker in place.

    Removing the root and recreating its marker exposes an indexable interval.
    Keep the root and marker across resets, and refuse source overlap before any
    deletion. This is for exclusive mutation/flake scratch, never author stores.
    """
    source, destination = _paths(source, destination)
    tests, _ = _paths(tests, destination)
    suite = tests / "tests"
    _paths(suite, destination)
    git_metadata = destination / ".git"
    if git_metadata.exists() or git_metadata.is_symlink():
        raise ValueError("scratch reset refuses Git repositories; use transient sync instead")
    destination.mkdir(parents=True, exist_ok=True)
    mark_no_index(destination)
    marker = _marker_identity(destination)
    for entry in destination.iterdir():
        if entry.name == NO_INDEX_MARKER:
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    if _marker_identity(destination) != marker:
        raise ValueError("workspace exclusion marker changed during reset")
    _sync_workspace(source, destination, exclude=(".git", ".factory"))
    _sync_workspace(suite, destination / "tests", delete=True)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Maintain excluded transient Factory workspaces")
    commands = parser.add_subparsers(dest="action", required=True)
    sync = commands.add_parser("sync", help="sync a transient destination, preserving exclusion")
    sync.add_argument("--source", type=Path, required=True)
    sync.add_argument("--destination", type=Path, required=True)
    sync.add_argument("--delete", action="store_true")
    sync.add_argument("--exclude", action="append", default=[])
    scratch = commands.add_parser("prepare-scratch", help="reset exclusive mutation/flake scratch")
    scratch.add_argument("--source", type=Path, required=True)
    scratch.add_argument("--tests", type=Path, required=True)
    scratch.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "sync":
            destination = _sync_workspace(args.source, args.destination, delete=args.delete,
                                          exclude=tuple(args.exclude))
        else:
            destination = _prepare_scratch(args.source, args.tests, args.destination)
        print(json.dumps({"destination": str(destination), "excluded_from_indexing": True}))
        return 0
    except (OSError, ValueError) as error:
        print(f"workspace sync: {error}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
