"""Content-addressed retained bytes for generated inputs and reviewed outputs.

A digest-only manifest is not a reproducible freeze. These helpers copy exact regular-file bytes
under their address, reject links and special files, remove write bits, and re-derive every
address before consumption. This is application-level immutability with tamper detection, not a
claim of hardware WORM; an administrator can alter the filesystem, and verification will refuse.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory_core.manifest import digest_bytes, digest_obj
from factory_runtime.durability import (
    DurabilityError,
    fsync_directory,
    fsync_directory_chain,
)

_LABEL = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class SnapshotError(ValueError):
    """A snapshot could not be created or verified without trusting mutable state."""


@dataclass(frozen=True)
class FrozenBlob:
    digest: str
    directory: Path
    payload_path: Path


@dataclass(frozen=True)
class FrozenTree:
    digest: str
    directory: Path
    files_directory: Path
    manifest_path: Path
    file_count: int


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_once(path: Path, data: bytes, mode: int = 0o444) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fchmod(handle.fileno(), mode)
        os.fsync(handle.fileno())


def _durability_boundary(
    store_root: str | Path,
    *,
    durable_through: str | Path,
) -> tuple[Path, Path]:
    """Validate a caller-owned durability boundary before creating snapshot paths."""

    root = Path(os.path.abspath(os.fspath(store_root)))
    boundary = Path(os.path.abspath(os.fspath(durable_through)))
    if boundary.parent == boundary:
        raise SnapshotError("snapshot durability boundary may not be a filesystem root")
    if boundary.is_symlink() or not boundary.is_dir():
        raise SnapshotError(
            f"snapshot durability boundary is not an existing real directory: {boundary}"
        )
    try:
        within_boundary = os.path.commonpath((root, boundary)) == os.fspath(boundary)
    except ValueError as exc:
        raise SnapshotError(
            "snapshot store and durability boundary cross filesystem roots"
        ) from exc
    if not within_boundary:
        raise SnapshotError(f"snapshot store {root} is outside declared durability root {boundary}")
    current = boundary
    for component in root.relative_to(boundary).parts:
        current /= component
        if current.is_symlink():
            raise SnapshotError(f"snapshot store contains a forbidden symlink: {current}")
        if current.exists() and not current.is_dir():
            raise SnapshotError(f"snapshot store component is not a directory: {current}")
    return root, boundary


def _sync_snapshot_publication(
    directory: Path,
    *,
    durable_through: Path,
    internal_directories: tuple[Path, ...] = (),
) -> None:
    """Commit retained directory entries before a ledger may cite the snapshot.

    Exact file bytes and modes are fsynced before publication. Tree snapshots additionally
    contain newly created directory entries below the published address, so those directories
    are committed deepest-first before the public address and its ancestor chain.
    """

    try:
        for internal in sorted(
            internal_directories,
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            fsync_directory(internal)
        fsync_directory_chain(directory, through=durable_through)
    except DurabilityError as exc:
        raise SnapshotError(str(exc)) from exc


def _sync_snapshot_staging(
    directory: Path,
    *,
    internal_directories: tuple[Path, ...] = (),
) -> None:
    """Commit a complete hidden snapshot before exposing its content address.

    The staging root deliberately remains owner-writable through ``rename`` because hosted
    macOS Python 3.12 refuses to rename a sealed directory. Exact files are already fsynced and
    read-only. Committing every internal directory here means a crash-visible writable root can
    be re-derived and sealed on retry without trusting incomplete directory entries.
    """

    try:
        for internal in sorted(
            internal_directories,
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            fsync_directory(internal)
        fsync_directory(directory)
    except DurabilityError as exc:
        raise SnapshotError(str(exc)) from exc


def _read_regular(path: Path) -> tuple[bytes, int]:
    if path.is_symlink():
        raise SnapshotError(f"snapshot contains a forbidden symlink: {path}")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise SnapshotError(f"snapshot file cannot be opened safely: {path}: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise SnapshotError(f"snapshot entry is not a regular file: {path}")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks), stat.S_IMODE(metadata.st_mode)
    finally:
        os.close(descriptor)


def _require_readonly_directory(path: Path) -> None:
    if path.is_symlink():
        raise SnapshotError(f"snapshot contains a forbidden directory symlink: {path}")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise SnapshotError(f"snapshot directory cannot be inspected: {path}: {exc}") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise SnapshotError(f"snapshot entry is not a directory: {path}")
    if stat.S_IMODE(metadata.st_mode) & 0o222:
        raise SnapshotError(f"snapshot directory is writable: {path}")


def _require_snapshot_root(path: Path, *, allow_recoverable_writable: bool) -> None:
    """Require either a sealed root or the exact private mode used before publication."""

    if path.is_symlink():
        raise SnapshotError(f"snapshot contains a forbidden directory symlink: {path}")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise SnapshotError(f"snapshot directory cannot be inspected: {path}: {exc}") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise SnapshotError(f"snapshot entry is not a directory: {path}")
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & 0o222 and not (allow_recoverable_writable and mode == 0o700):
        raise SnapshotError(f"snapshot directory is writable: {path}")


def _directory_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _assert_path_names_directory(path: Path, descriptor: int, *, context: str) -> None:
    opened = os.fstat(descriptor)
    try:
        installed = os.lstat(path)
    except OSError as exc:
        raise SnapshotError(f"{context} pathname became unreadable: {path}: {exc}") from exc
    if (
        not stat.S_ISDIR(opened.st_mode)
        or stat.S_ISLNK(installed.st_mode)
        or (opened.st_dev, opened.st_ino) != (installed.st_dev, installed.st_ino)
    ):
        raise SnapshotError(f"{context} pathname changed: {path}")


def _open_publication_directory(path: Path) -> int:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        _assert_path_names_directory(path, descriptor, context="snapshot publication")
        return descriptor
    except SnapshotError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise SnapshotError(f"snapshot publication cannot be opened safely: {path}: {exc}") from exc


@contextmanager
def _publication_lock(path: Path) -> Iterator[int]:
    """Serialize one content address on its exact, crash-releasing directory inode."""

    descriptor = _open_publication_directory(path)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        _assert_path_names_directory(path, descriptor, context="snapshot publication lock")
        yield descriptor
        _assert_path_names_directory(path, descriptor, context="snapshot publication lock")
    except OSError as exc:
        raise SnapshotError(f"snapshot publication lock failed for {path}: {exc}") from exc
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _publish_staging_directory(temporary: Path, destination: Path) -> bool:
    """Atomically publish staging, recognizing POSIX directory-collision variants."""

    try:
        os.rename(temporary, destination)
        return True
    except OSError as exc:
        # Linux commonly reports EEXIST while Darwin reports ENOTEMPTY when another
        # publisher won the same non-empty content address. Every other rename failure
        # remains fatal, and the winner is independently re-derived below.
        if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
            raise SnapshotError(
                f"snapshot staging publication failed for {destination}: {exc}"
            ) from exc
        return False


def _read_regular_at(parent_descriptor: int, name: str, *, context: str) -> tuple[bytes, int]:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_descriptor,
        )
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SnapshotError(f"{context} is not a regular file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        installed = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if _directory_identity(before) != _directory_identity(after) or (
            stat.S_ISLNK(installed.st_mode)
            or (installed.st_dev, installed.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise SnapshotError(f"{context} changed during recovery preflight")
        return b"".join(chunks), stat.S_IMODE(after.st_mode)
    except OSError as exc:
        if isinstance(exc, SnapshotError):
            raise
        raise SnapshotError(f"{context} cannot be read safely: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _capture_tree_at(
    descriptor: int,
    *,
    prefix: str = "",
) -> dict[str, tuple[bytes, int]]:
    before = os.fstat(descriptor)
    if not stat.S_ISDIR(before.st_mode) or stat.S_IMODE(before.st_mode) & 0o222:
        raise SnapshotError("recoverable tree contains a writable or non-directory component")
    names = sorted(os.listdir(descriptor))
    captured: dict[str, tuple[bytes, int]] = {}
    for name in names:
        if not name or name in {".", ".."} or "/" in name:
            raise SnapshotError("recoverable tree contains an unsafe directory entry")
        relative = f"{prefix}/{name}" if prefix else name
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
            metadata = os.fstat(child)
            installed = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISLNK(installed.st_mode) or (
                installed.st_dev,
                installed.st_ino,
            ) != (metadata.st_dev, metadata.st_ino):
                raise SnapshotError(f"recoverable tree entry changed: {relative}")
            if stat.S_ISDIR(metadata.st_mode):
                captured.update(_capture_tree_at(child, prefix=relative))
            elif stat.S_ISREG(metadata.st_mode):
                chunks: list[bytes] = []
                first = metadata
                while chunk := os.read(child, 1024 * 1024):
                    chunks.append(chunk)
                last = os.fstat(child)
                if _directory_identity(first) != _directory_identity(last):
                    raise SnapshotError(f"recoverable tree file changed: {relative}")
                captured[relative] = (b"".join(chunks), stat.S_IMODE(last.st_mode))
            else:
                raise SnapshotError(f"recoverable tree contains a special entry: {relative}")
        finally:
            if child >= 0:
                os.close(child)
    after = os.fstat(descriptor)
    if _directory_identity(before) != _directory_identity(after) or names != sorted(
        os.listdir(descriptor)
    ):
        raise SnapshotError("recoverable tree directory changed during preflight")
    return captured


def _canonical_object_bytes(raw: bytes, *, context: str) -> dict[str, Any]:
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"{context} is unreadable: {exc}") from exc
    if not isinstance(decoded, dict) or raw != _canonical_json(decoded):
        raise SnapshotError(f"{context} is not a canonical JSON object")
    return decoded


def _preflight_recoverable_blob(
    descriptor: int,
    *,
    expected_digest: str,
    label: str,
) -> None:
    before = os.fstat(descriptor)
    names = sorted(os.listdir(descriptor))
    if names != ["manifest.json", "payload"]:
        raise SnapshotError("recoverable blob has unexpected contents")
    manifest_bytes, manifest_mode = _read_regular_at(
        descriptor,
        "manifest.json",
        context="recoverable blob manifest",
    )
    payload, payload_mode = _read_regular_at(
        descriptor,
        "payload",
        context="recoverable blob payload",
    )
    manifest = _canonical_object_bytes(manifest_bytes, context="recoverable blob manifest")
    if manifest != {
        "schema_version": "factory-blob-snapshot/1",
        "label": label,
        "digest": expected_digest,
        "size": len(payload),
    }:
        raise SnapshotError("recoverable blob manifest mismatch")
    if digest_bytes(payload) != expected_digest or manifest_mode & 0o222 or payload_mode & 0o222:
        raise SnapshotError("recoverable blob bytes or modes mismatch")
    after = os.fstat(descriptor)
    if _directory_identity(before) != _directory_identity(after) or names != sorted(
        os.listdir(descriptor)
    ):
        raise SnapshotError("recoverable blob changed during preflight")


def _preflight_recoverable_tree(descriptor: int, *, expected_digest: str) -> None:
    before = os.fstat(descriptor)
    names = sorted(os.listdir(descriptor))
    if names != ["files", "manifest.json"]:
        raise SnapshotError("recoverable tree has unexpected contents")
    manifest_bytes, manifest_mode = _read_regular_at(
        descriptor,
        "manifest.json",
        context="recoverable tree manifest",
    )
    if manifest_mode & 0o222:
        raise SnapshotError("recoverable tree manifest is writable")
    manifest = _canonical_object_bytes(manifest_bytes, context="recoverable tree manifest")
    files_descriptor = -1
    try:
        files_descriptor = os.open(
            "files",
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=descriptor,
        )
        captured = _capture_tree_at(files_descriptor)
        installed = os.stat("files", dir_fd=descriptor, follow_symlinks=False)
        opened = os.fstat(files_descriptor)
        if stat.S_ISLNK(installed.st_mode) or (
            installed.st_dev,
            installed.st_ino,
        ) != (opened.st_dev, opened.st_ino):
            raise SnapshotError("recoverable tree files directory changed")
    except OSError as exc:
        if isinstance(exc, SnapshotError):
            raise
        raise SnapshotError(f"recoverable tree files cannot be opened safely: {exc}") from exc
    finally:
        if files_descriptor >= 0:
            os.close(files_descriptor)
    rows = manifest.get("files")
    if (
        manifest.get("schema_version") != "factory-tree-snapshot/1"
        or manifest.get("tree_digest") != expected_digest
        or not isinstance(rows, list)
    ):
        raise SnapshotError("recoverable tree manifest address mismatch")
    verified_rows: list[dict[str, Any]] = []
    expected_paths: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "path",
            "mode",
            "frozen_mode",
            "digest",
        }:
            raise SnapshotError("recoverable tree manifest has a malformed row")
        relative = row.get("path")
        mode = row.get("mode")
        frozen_mode = row.get("frozen_mode")
        digest = row.get("digest")
        if (
            not isinstance(relative, str)
            or not relative
            or relative.startswith("/")
            or ".." in Path(relative).parts
            or relative in expected_paths
            or isinstance(mode, bool)
            or not isinstance(mode, int)
            or not 0 <= mode <= 0o7777
            or isinstance(frozen_mode, bool)
            or not isinstance(frozen_mode, int)
            or frozen_mode != mode & ~0o222
            or not isinstance(digest, str)
            or not _DIGEST.fullmatch(digest)
        ):
            raise SnapshotError("recoverable tree manifest has invalid path, mode, or digest data")
        expected_paths.add(relative)
        retained = captured.get(relative)
        if retained is None or retained[1] != frozen_mode or digest_bytes(retained[0]) != digest:
            raise SnapshotError(f"recoverable tree retained bytes mismatch: {relative}")
        verified_rows.append({"path": relative, "mode": mode, "digest": digest})
    if set(captured) != expected_paths or _digest_rows(verified_rows) != expected_digest:
        raise SnapshotError("recoverable tree retained set or address mismatch")
    after = os.fstat(descriptor)
    if _directory_identity(before) != _directory_identity(after) or names != sorted(
        os.listdir(descriptor)
    ):
        raise SnapshotError("recoverable tree changed during preflight")


def _seal_snapshot_root(path: Path, descriptor: int) -> None:
    """Seal and sync one exact published directory inode without re-resolving it."""

    try:
        opened = os.fstat(descriptor)
        _assert_path_names_directory(path, descriptor, context="snapshot publication")
        mode = stat.S_IMODE(opened.st_mode)
        if mode & 0o222:
            if mode != 0o700:
                raise SnapshotError(f"snapshot publication has an unsafe writable mode: {path}")
            os.fchmod(descriptor, 0o555)
        os.fsync(descriptor)
        sealed = os.fstat(descriptor)
        _assert_path_names_directory(path, descriptor, context="snapshot publication")
        if stat.S_IMODE(sealed.st_mode) & 0o222:
            raise SnapshotError(f"snapshot publication changed while sealing: {path}")
    except OSError as exc:
        if isinstance(exc, SnapshotError):
            raise
        raise SnapshotError(f"snapshot publication could not be sealed: {path}: {exc}") from exc


def _read_manifest(path: Path, *, label: str) -> tuple[dict[str, Any], int]:
    data, mode = _read_regular(path)
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"{label} is unreadable: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SnapshotError(f"{label} must be a JSON object: {path}")
    return raw, mode


def _capture_tree(
    root: Path,
    *,
    allow_empty: bool,
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    if root.is_symlink() or not root.is_dir():
        raise SnapshotError(f"snapshot source is not a regular directory: {root}")
    rows: list[dict[str, Any]] = []
    content: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise SnapshotError(f"snapshot source contains a forbidden symlink: {relative}")
        if path.is_dir():
            continue
        data, mode = _read_regular(path)
        rows.append(
            {
                "path": relative,
                "mode": mode,
                "frozen_mode": mode & ~0o222,
                "digest": digest_bytes(data),
            }
        )
        content[relative] = data
    if not rows and not allow_empty:
        raise SnapshotError(f"snapshot source tree is empty: {root}")
    return rows, content


def _digest_rows(rows: list[dict[str, Any]]) -> str:
    return digest_obj(
        {
            "files": [
                {"path": row["path"], "mode": row["mode"], "digest": row["digest"]} for row in rows
            ]
        }
    )


def tree_digest(root: str | Path, *, allow_empty: bool = False) -> str:
    """Address a regular tree by relative paths, original modes, and exact bytes."""

    rows, _ = _capture_tree(Path(root), allow_empty=allow_empty)
    return _digest_rows(rows)


def freeze_blob(
    store_root: str | Path,
    *,
    durable_through: str | Path,
    label: str,
    data: bytes,
) -> FrozenBlob:
    """Persist one exact byte string durably under its SHA-256 address.

    ``durable_through`` is an already-existing run/attempt boundary owned by the caller. The
    function never infers or widens that boundary, and an identical retry re-syncs the retained
    publication before returning.
    """

    if not _LABEL.fullmatch(label):
        raise SnapshotError(f"invalid snapshot label: {label!r}")
    digest = digest_bytes(data)
    store, boundary = _durability_boundary(
        store_root,
        durable_through=durable_through,
    )
    root = store / label
    if root.is_symlink():
        raise SnapshotError(f"snapshot store contains a forbidden symlink: {root}")
    if root.exists() and not root.is_dir():
        raise SnapshotError(f"snapshot store component is not a directory: {root}")
    destination = root / digest.removeprefix("sha256:")
    if destination.exists() or destination.is_symlink():
        return _admit_blob_publication(
            destination,
            expected_digest=digest,
            label=label,
            durable_through=boundary,
        )
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".blob-", dir=root))
    try:
        _write_once(temporary / "payload", data)
        _write_once(
            temporary / "manifest.json",
            _canonical_json(
                {
                    "schema_version": "factory-blob-snapshot/1",
                    "label": label,
                    "digest": digest,
                    "size": len(data),
                }
            ),
        )
        _sync_snapshot_staging(temporary)
        if not _publish_staging_directory(temporary, destination):
            temporary.chmod(0o755)
            shutil.rmtree(temporary)
        return _admit_blob_publication(
            destination,
            expected_digest=digest,
            label=label,
            durable_through=boundary,
        )
    finally:
        if temporary.exists():
            temporary.chmod(0o755)
            shutil.rmtree(temporary)


def verify_frozen_blob(
    directory: str | Path,
    *,
    expected_digest: str,
    label: str,
) -> FrozenBlob:
    return _verify_frozen_blob(
        directory,
        expected_digest=expected_digest,
        label=label,
        allow_recoverable_writable=False,
    )


def _verify_frozen_blob(
    directory: str | Path,
    *,
    expected_digest: str,
    label: str,
    allow_recoverable_writable: bool,
) -> FrozenBlob:
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise SnapshotError(f"blob snapshot directory is missing or linked: {root}")
    _require_snapshot_root(
        root,
        allow_recoverable_writable=allow_recoverable_writable,
    )
    if {path.name for path in root.iterdir()} != {"manifest.json", "payload"}:
        raise SnapshotError(f"blob snapshot has unexpected contents: {root}")
    manifest_path = root / "manifest.json"
    payload_path = root / "payload"
    manifest, manifest_mode = _read_manifest(
        manifest_path,
        label="blob snapshot manifest",
    )
    data, mode = _read_regular(payload_path)
    expected_manifest = {
        "schema_version": "factory-blob-snapshot/1",
        "label": label,
        "digest": expected_digest,
        "size": len(data),
    }
    if manifest != expected_manifest:
        raise SnapshotError(f"blob snapshot manifest mismatch: {root}")
    if digest_bytes(data) != expected_digest or root.name != expected_digest.removeprefix(
        "sha256:"
    ):
        raise SnapshotError(f"blob snapshot content address mismatch: {root}")
    if mode & 0o222 or manifest_mode & 0o222:
        raise SnapshotError(f"blob snapshot files are writable: {root}")
    return FrozenBlob(expected_digest, root, payload_path)


def _admit_blob_publication(
    directory: Path,
    *,
    expected_digest: str,
    label: str,
    durable_through: Path,
) -> FrozenBlob:
    """Re-derive, seal, and re-derive a new or crash-recovered blob publication."""

    with _publication_lock(directory) as descriptor:
        try:
            candidate = _verify_frozen_blob(
                directory,
                expected_digest=expected_digest,
                label=label,
                allow_recoverable_writable=True,
            )
            _assert_path_names_directory(
                directory,
                descriptor,
                context="blob snapshot publication",
            )
            if stat.S_IMODE(os.fstat(descriptor).st_mode) & 0o222:
                _preflight_recoverable_blob(
                    descriptor,
                    expected_digest=expected_digest,
                    label=label,
                )
            _seal_snapshot_root(candidate.directory, descriptor)
            frozen = verify_frozen_blob(
                directory,
                expected_digest=expected_digest,
                label=label,
            )
            _preflight_recoverable_blob(
                descriptor,
                expected_digest=expected_digest,
                label=label,
            )
            _assert_path_names_directory(
                directory,
                descriptor,
                context="blob snapshot publication",
            )
            _sync_snapshot_publication(frozen.directory, durable_through=durable_through)
            frozen = verify_frozen_blob(
                directory,
                expected_digest=expected_digest,
                label=label,
            )
            _preflight_recoverable_blob(
                descriptor,
                expected_digest=expected_digest,
                label=label,
            )
            _assert_path_names_directory(
                directory,
                descriptor,
                context="blob snapshot publication",
            )
            return frozen
        finally:
            _assert_path_names_directory(
                directory,
                descriptor,
                context="blob snapshot publication cleanup",
            )


def freeze_tree(
    source: str | Path,
    store_root: str | Path,
    *,
    durable_through: str | Path,
    allow_empty: bool = False,
) -> FrozenTree:
    """Persist exact tree bytes in a durably published content-addressed directory."""

    rows, content = _capture_tree(Path(source), allow_empty=allow_empty)
    digest = _digest_rows(rows)
    root, boundary = _durability_boundary(
        store_root,
        durable_through=durable_through,
    )
    destination = root / digest.removeprefix("sha256:")
    if destination.exists() or destination.is_symlink():
        return _admit_tree_publication(
            destination,
            expected_digest=digest,
            durable_through=boundary,
        )
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".tree-", dir=root))
    try:
        files = temporary / "files"
        files.mkdir()
        for row in rows:
            relative = str(row["path"])
            target = files / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_once(target, content[relative], int(row["frozen_mode"]))
        _write_once(
            temporary / "manifest.json",
            _canonical_json(
                {
                    "schema_version": "factory-tree-snapshot/1",
                    "tree_digest": digest,
                    "files": rows,
                }
            ),
        )
        directories = [path for path in files.rglob("*") if path.is_dir()]
        for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
            directory.chmod(0o555)
        files.chmod(0o555)
        _sync_snapshot_staging(
            temporary,
            internal_directories=(*directories, files),
        )
        if not _publish_staging_directory(temporary, destination):
            _make_tree_writable(temporary)
            shutil.rmtree(temporary)
        return _admit_tree_publication(
            destination,
            expected_digest=digest,
            durable_through=boundary,
        )
    finally:
        if temporary.exists():
            _make_tree_writable(temporary)
            shutil.rmtree(temporary)


def _make_tree_writable(root: Path) -> None:
    root.chmod(0o755)
    for path in root.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)


def verify_frozen_tree(directory: str | Path, *, expected_digest: str) -> FrozenTree:
    """Re-derive a retained tree from bytes and its frozen original modes."""

    return _verify_frozen_tree(
        directory,
        expected_digest=expected_digest,
        allow_recoverable_writable=False,
    )


def _verify_frozen_tree(
    directory: str | Path,
    *,
    expected_digest: str,
    allow_recoverable_writable: bool,
) -> FrozenTree:
    """Re-derive a tree, optionally admitting only Factory's private staging-root mode."""

    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise SnapshotError(f"tree snapshot directory is missing or linked: {root}")
    _require_snapshot_root(
        root,
        allow_recoverable_writable=allow_recoverable_writable,
    )
    if {path.name for path in root.iterdir()} != {"files", "manifest.json"}:
        raise SnapshotError(f"tree snapshot has unexpected contents: {root}")
    manifest_path = root / "manifest.json"
    files = root / "files"
    _require_readonly_directory(files)
    manifest, manifest_mode = _read_manifest(
        manifest_path,
        label="tree snapshot manifest",
    )
    if manifest_mode & 0o222:
        raise SnapshotError(f"tree snapshot manifest is writable: {root}")
    rows = manifest.get("files")
    if not isinstance(rows, list):
        raise SnapshotError(f"tree snapshot manifest has no files array: {root}")
    expected_paths: set[str] = set()
    verified_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "path",
            "mode",
            "frozen_mode",
            "digest",
        }:
            raise SnapshotError(f"tree snapshot manifest has a malformed row: {root}")
        if (
            isinstance(row["mode"], bool)
            or not isinstance(row["mode"], int)
            or not 0 <= row["mode"] <= 0o7777
            or isinstance(row["frozen_mode"], bool)
            or not isinstance(row["frozen_mode"], int)
            or row["frozen_mode"] != row["mode"] & ~0o222
            or not isinstance(row["digest"], str)
            or not _DIGEST.fullmatch(row["digest"])
        ):
            raise SnapshotError(f"tree snapshot manifest has invalid mode or digest data: {root}")
        relative = str(row["path"])
        if not relative or relative.startswith("/") or ".." in Path(relative).parts:
            raise SnapshotError(f"tree snapshot manifest has an unsafe path: {relative!r}")
        if relative in expected_paths:
            raise SnapshotError(f"tree snapshot manifest repeats a path: {relative}")
        expected_paths.add(relative)
        data, actual_mode = _read_regular(files / relative)
        if digest_bytes(data) != row["digest"]:
            raise SnapshotError(f"tree snapshot payload digest mismatch: {relative}")
        if actual_mode != row["frozen_mode"] or actual_mode & 0o222:
            raise SnapshotError(f"tree snapshot payload mode mismatch: {relative}")
        verified_rows.append({"path": relative, "mode": row["mode"], "digest": row["digest"]})
    actual_paths: set[str] = set()
    for path in files.rglob("*"):
        if path.is_symlink():
            raise SnapshotError(f"tree snapshot contains a forbidden symlink: {path}")
        if path.is_dir():
            _require_readonly_directory(path)
        elif path.is_file():
            actual_paths.add(path.relative_to(files).as_posix())
        else:
            raise SnapshotError(f"tree snapshot contains a special file: {path}")
    if actual_paths != expected_paths:
        raise SnapshotError(f"tree snapshot retained bytes do not match its manifest: {root}")
    actual_digest = digest_obj({"files": verified_rows})
    if manifest.get("schema_version") != "factory-tree-snapshot/1":
        raise SnapshotError(f"tree snapshot schema version mismatch: {root}")
    if manifest.get("tree_digest") != expected_digest:
        raise SnapshotError(f"tree snapshot manifest address mismatch: {root}")
    if actual_digest != expected_digest or root.name != expected_digest.removeprefix("sha256:"):
        raise SnapshotError(f"tree snapshot content address mismatch: {root}")
    return FrozenTree(expected_digest, root, files, manifest_path, len(rows))


def _admit_tree_publication(
    directory: Path,
    *,
    expected_digest: str,
    durable_through: Path,
) -> FrozenTree:
    """Re-derive, seal, and re-derive a new or crash-recovered tree publication."""

    with _publication_lock(directory) as descriptor:
        try:
            candidate = _verify_frozen_tree(
                directory,
                expected_digest=expected_digest,
                allow_recoverable_writable=True,
            )
            _assert_path_names_directory(
                directory,
                descriptor,
                context="tree snapshot publication",
            )
            if stat.S_IMODE(os.fstat(descriptor).st_mode) & 0o222:
                _preflight_recoverable_tree(descriptor, expected_digest=expected_digest)
            _seal_snapshot_root(candidate.directory, descriptor)
            frozen = verify_frozen_tree(directory, expected_digest=expected_digest)
            _preflight_recoverable_tree(descriptor, expected_digest=expected_digest)
            _assert_path_names_directory(
                directory,
                descriptor,
                context="tree snapshot publication",
            )
            internal = tuple(path for path in frozen.files_directory.rglob("*") if path.is_dir())
            _sync_snapshot_publication(
                frozen.directory,
                durable_through=durable_through,
                internal_directories=(*internal, frozen.files_directory),
            )
            frozen = verify_frozen_tree(directory, expected_digest=expected_digest)
            _preflight_recoverable_tree(descriptor, expected_digest=expected_digest)
            _assert_path_names_directory(
                directory,
                descriptor,
                context="tree snapshot publication",
            )
            return frozen
        finally:
            _assert_path_names_directory(
                directory,
                descriptor,
                context="tree snapshot publication cleanup",
            )
