"""Build a bounded, path-free data projection for the networked model runner."""

from __future__ import annotations

import base64
import hashlib
import os
import stat
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from factory_core.manifest import digest_bytes, digest_obj
from factory_runtime.durability import CHAIN_ROOT_KEY_FILENAME
from factory_runtime.schema import DocumentValidationError, validate_document

_MAX_FILES = 4_096
_MAX_FILE_BYTES = 524_288
_MAX_TOTAL_BYTES = 1_200_000


class ProjectionBundleError(ValueError):
    """A lane tree could not be reduced to a safe bounded data projection."""


def _canonical_relative(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    pure = PurePosixPath(relative)
    if (
        not relative
        or pure.is_absolute()
        or "\\" in relative
        or any(part in {"", ".", ".."} for part in pure.parts)
        or pure.as_posix() != relative
    ):
        raise ProjectionBundleError(f"projection contains a non-canonical path: {relative!r}")
    return relative


def bundle_runner_projection(
    root: str | Path,
    *,
    projection_receipt: Mapping[str, Any],
    run_id: str,
    generation: int,
    role: str,
    target_state_digest: str,
    resolved_commit: str,
    resolved_tree: str,
) -> dict[str, Any]:
    """Re-derive a projection receipt and encode only bounded file data, never host paths."""

    source = Path(root)
    if source.is_symlink() or not source.is_dir():
        raise ProjectionBundleError(
            "runner projection root is missing, not a directory, or symlinked"
        )
    source = source.resolve()
    expected_receipt = {
        "role": role,
        "sha": resolved_commit,
        "tree": resolved_tree,
    }
    for field, expected in expected_receipt.items():
        if projection_receipt.get(field) != expected:
            raise ProjectionBundleError(f"projection receipt has wrong {field}")
    files: list[dict[str, Any]] = []
    projection_hash = hashlib.sha256()
    total_bytes = 0
    for base, directories, names in os.walk(source, followlinks=False):
        directories[:] = sorted(name for name in directories if name != ".git")
        for name in sorted(names):
            path = Path(base) / name
            relative = _canonical_relative(path, source)
            if name == CHAIN_ROOT_KEY_FILENAME:
                # 2.2 negative space: a lane's closed environment never receives
                # chain-key material — a projection root containing it is a staging
                # error to surface, never to silently sanitize.
                raise ProjectionBundleError(
                    f"chain-key material may never enter a lane projection: {relative}"
                )
            mode = path.lstat().st_mode
            if path.is_symlink() or not stat.S_ISREG(mode):
                raise ProjectionBundleError(
                    f"runner projection permits regular files only: {relative}"
                )
            raw = path.read_bytes()
            if len(raw) > _MAX_FILE_BYTES:
                raise ProjectionBundleError(f"runner projection file is too large: {relative}")
            total_bytes += len(raw)
            if total_bytes > _MAX_TOTAL_BYTES:
                raise ProjectionBundleError("runner projection exceeds its total byte ceiling")
            permissions = stat.S_IMODE(mode)
            projection_hash.update(
                relative.encode("utf-8") + b"\0" + oct(permissions).encode("ascii") + b"\0"
            )
            projection_hash.update(b"file\0" + raw)
            files.append(
                {
                    "relative_path": relative,
                    "mode": permissions,
                    "content_digest": digest_bytes(raw),
                    "content_base64": base64.b64encode(raw).decode("ascii"),
                }
            )
            if len(files) > _MAX_FILES:
                raise ProjectionBundleError("runner projection exceeds its file-count ceiling")
    manifest_digest = "sha256:" + projection_hash.hexdigest()
    if projection_receipt.get("manifest_digest") != manifest_digest:
        raise ProjectionBundleError("projection bytes differ from the retained projection receipt")
    document = {
        "schema_version": "factory-runner-projection/1",
        "run_id": run_id,
        "generation": generation,
        "role": role,
        "target_state_digest": target_state_digest,
        "resolved_commit": resolved_commit,
        "resolved_tree": resolved_tree,
        "projection_manifest_digest": manifest_digest,
        "projection_receipt_digest": digest_obj(dict(projection_receipt)),
        "files": files,
    }
    try:
        validate_document("runner-projection", document)
    except DocumentValidationError as exc:
        raise ProjectionBundleError(str(exc)) from exc
    return document


__all__ = ["ProjectionBundleError", "bundle_runner_projection"]
