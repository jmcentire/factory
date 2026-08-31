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


# --------------------------------------------------------------------------- #
# Ledger chain-key resolution (plan 2.2)
# --------------------------------------------------------------------------- #

CHAIN_ROOT_KEY_FILENAME = ".chain-root.key"
_CHAIN_KEY_WALK_CAP = 8


def load_chain_key(ledger_path: str | Path) -> bytes | None:
    """Resolve the per-ledger HMAC chain key from the durability seam.

    Root key material lives in ``.chain-root.key`` at (an ancestor of) the runs root —
    an asset retained under the founder-root signature with the authority genesis, so
    key-FILE loss degrades to re-derivation from that retained asset, never to a
    permanently unverifiable ledger. The per-ledger key is
    HMAC-SHA256(root, relative-ledger-path), binding each ledger file's identity: the
    run ledger, resource ledger, and evidence ledger of one run all key differently,
    and the derivation is recoverable from (root material, path) alone.

    Absent root material returns ``None`` — the deprecated migration-only unkeyed mode
    (the core is loud about it). FAIL-CLOSED CONSEQUENCE, DOCUMENTED: if the root
    asset is ever unrecoverable, every keyed ledger under it refuses verification
    permanently; recover the root from the founder-root retention, never regenerate it.
    """
    import hashlib
    import hmac as _hmac

    located = load_chain_root_material(ledger_path)
    if located is None:
        return None
    material, ancestor = located
    relative = _absolute_lexical(ledger_path).relative_to(ancestor).as_posix()
    return _hmac.new(material, relative.encode("utf-8"), hashlib.sha256).digest()


def load_chain_root_material(ledger_path: str | Path) -> tuple[bytes, Path] | None:
    """Locate the chain root material governing ``ledger_path``.

    Returns ``(material, ancestor_dir)`` from the nearest ``.chain-root.key`` ancestor,
    or ``None`` when no root material governs the path (migration-only unkeyed mode).
    Exposed separately so the genesis commitment check can bind the MATERIAL digest
    without ever deriving (or holding) a per-ledger key it does not need.
    """
    path = _absolute_lexical(ledger_path)
    ancestor = path.parent
    for _ in range(_CHAIN_KEY_WALK_CAP):
        root_file = ancestor / CHAIN_ROOT_KEY_FILENAME
        if root_file.is_file() and not root_file.is_symlink():
            material = root_file.read_bytes().strip()
            if not material:
                raise DurabilityError(f"chain root key file is empty: {root_file}")
            return material, ancestor
        if ancestor.parent == ancestor:
            break
        ancestor = ancestor.parent
    return None
