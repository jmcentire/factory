"""Content-addressed retained bytes for generated inputs and reviewed outputs.

A digest-only manifest is not a reproducible freeze. These helpers copy exact regular-file bytes
under their address, reject links and special files, remove write bits, and re-derive every
address before consumption. This is application-level immutability with tamper detection, not a
claim of hardware WORM; an administrator can alter the filesystem, and verification will refuse.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory_core.manifest import digest_bytes, digest_obj

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
        os.fsync(handle.fileno())
    path.chmod(mode)


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


def freeze_blob(store_root: str | Path, *, label: str, data: bytes) -> FrozenBlob:
    """Persist one exact byte string once under its SHA-256 address."""

    if not _LABEL.fullmatch(label):
        raise SnapshotError(f"invalid snapshot label: {label!r}")
    digest = digest_bytes(data)
    root = Path(store_root) / label
    destination = root / digest.removeprefix("sha256:")
    if destination.exists():
        return verify_frozen_blob(destination, expected_digest=digest, label=label)
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
        try:
            os.rename(temporary, destination)
        except FileExistsError:
            temporary.chmod(0o755)
            shutil.rmtree(temporary)
        else:
            # Hosted macOS refuses to rename a directory after its owner-write bit is
            # removed. Publish the hidden staging directory first, then immediately seal
            # its final address. Payloads are already read-only, and the verifier below
            # refuses any concurrent disturbance or a directory that remained writable.
            destination.chmod(0o555)
        return verify_frozen_blob(destination, expected_digest=digest, label=label)
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
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise SnapshotError(f"blob snapshot directory is missing or linked: {root}")
    _require_readonly_directory(root)
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


def freeze_tree(
    source: str | Path,
    store_root: str | Path,
    *,
    allow_empty: bool = False,
) -> FrozenTree:
    """Persist exact tree bytes in a content-addressed read-only directory."""

    rows, content = _capture_tree(Path(source), allow_empty=allow_empty)
    digest = _digest_rows(rows)
    root = Path(store_root)
    destination = root / digest.removeprefix("sha256:")
    if destination.exists():
        return verify_frozen_tree(destination, expected_digest=digest)
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
        try:
            os.rename(temporary, destination)
        except FileExistsError:
            _make_tree_writable(temporary)
            shutil.rmtree(temporary)
        else:
            # See freeze_blob: some macOS filesystems refuse to rename the sealed staging
            # root. All descendants are already read-only; seal the published root and
            # re-derive the complete snapshot before returning it.
            destination.chmod(0o555)
        return verify_frozen_tree(destination, expected_digest=digest)
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

    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise SnapshotError(f"tree snapshot directory is missing or linked: {root}")
    _require_readonly_directory(root)
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
