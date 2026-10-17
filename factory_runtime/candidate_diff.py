"""Immutable Git baseline and candidate change-set derivation.

The Validator cannot review architecture, redundancy, or scope from generated files alone.  This
module projects the exact resolved Git tree into a canonical document, proves that it reconstructs
the retained Git tree object, and derives the complete resulting candidate change set.  The current
candidate ABI is complete-tree only; brownfield partial-output semantics remain unsupported and
therefore fail closed instead of manufacturing a scope claim.

A baseline takes one of two storage forms, chosen by the target (``build.review_baseline``):

``embedded`` (``factory-base-source-snapshot/1``, the default)
    Every blob's bytes ride inside the document, so it verifies with nothing else in hand.  That
    bounds it to a small repository: 4,096 files, 512 KiB per file, about 1.2 MB in all.

``git-reference`` (``factory-base-source-snapshot/2``)
    The document names the commit and tree and binds the complete listing (path, Git mode, object
    id and SHA-256 content digest of every entry) by one ``listing_digest``.  It embeds bytes only
    for the baseline files the change set replaces or deletes, under the same per-file and total
    ceilings, so the Validator can still cite exactly what changed.  Everything else re-derives from
    the run-owned object store on every verification, and a verification without that store
    refuses.  The SHA-256 listing digest keeps unchanged-file identity off the repository's own
    object hash.
"""

from __future__ import annotations

import base64
import hashlib
import os
import stat
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from factory_core.manifest import digest_bytes, digest_obj
from factory_runtime.snapshot import SnapshotError, tree_digest

_MAX_FILES = 4_096
_MAX_FILE_BYTES = 524_288
_MAX_TOTAL_BYTES = 1_200_000
_GIT_MODES = {"100644", "100755", "120000", "160000"}
_STREAM_CHUNK = 1 << 20

BASELINE_EMBEDDED = "embedded"
BASELINE_GIT_REFERENCE = "git-reference"
BASELINE_STORAGES = (BASELINE_EMBEDDED, BASELINE_GIT_REFERENCE)
_SNAPSHOT_SCHEMAS = {
    BASELINE_EMBEDDED: "factory-base-source-snapshot/1",
    BASELINE_GIT_REFERENCE: "factory-base-source-snapshot/2",
}


class CandidateDiffError(ValueError):
    """The immutable baseline or resulting candidate could not be proved completely."""


def _canonical_path(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CandidateDiffError("Git baseline contains a non-UTF-8 path") from exc
    path = PurePosixPath(text)
    if (
        not text
        or "\\" in text
        or path.is_absolute()
        or path.as_posix() != text
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise CandidateDiffError(f"Git baseline contains a non-canonical path: {text!r}")
    return text


def _hash(algorithm: str, data: bytes) -> str:
    if algorithm == "sha1":
        return hashlib.sha1(data, usedforsecurity=False).hexdigest()
    if algorithm == "sha256":
        return hashlib.sha256(data).hexdigest()
    raise CandidateDiffError(f"unsupported Git object hash algorithm: {algorithm}")


def _git_object_id(algorithm: str, kind: str, data: bytes) -> str:
    return _hash(algorithm, f"{kind} {len(data)}\0".encode("ascii") + data)


def _algorithm_for_oid(value: str) -> str:
    if len(value) == 40:
        return "sha1"
    if len(value) == 64:
        return "sha256"
    raise CandidateDiffError("Git object id has an unsupported length")


def _display_text(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _entry_attributes(path: str, git_mode: str) -> tuple[str, int]:
    """Derive the review-facing type and mode from the authoritative Git mode."""

    if git_mode in {"100644", "100755"}:
        return "file", 0o755 if git_mode == "100755" else 0o644
    if git_mode == "120000":
        return "symlink", 0o777
    if git_mode == "160000":
        return "gitlink", 0
    raise CandidateDiffError(f"unsupported Git mode for {path}: {git_mode}")


def _entry(
    *,
    path: str,
    git_mode: str,
    object_id: str,
    data: bytes,
) -> dict[str, Any]:
    entry_type, mode = _entry_attributes(path, git_mode)
    return {
        "path": path,
        "entry_type": entry_type,
        "git_mode": git_mode,
        "mode": mode,
        "object_id": object_id,
        "content_digest": digest_bytes(data),
        "content_base64": base64.b64encode(data).decode("ascii"),
        "content_utf8": _display_text(data),
    }


def _git_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "LC_ALL": "C",
    }


def _git_argv(object_store: Path, arguments: Sequence[str]) -> list[str]:
    return ["git", "-c", "credential.helper=", "--git-dir", str(object_store), *arguments]


def _run_git(
    object_store: Path,
    arguments: Sequence[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> bytes:
    result = runner(
        _git_argv(object_store, arguments),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=120,
        env=_git_environment(),
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace")[:1000]
        raise CandidateDiffError(f"Git baseline projection failed: {detail}")
    return result.stdout


def _tree_listing(
    object_store: Path,
    commit: str,
    algorithm: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> list[tuple[str, str, str]]:
    """List ``(path, git_mode, object_id)`` for every entry of the commit's tree, validated."""

    listing = _run_git(
        object_store,
        ("ls-tree", "-rz", "--full-tree", commit),
        runner=runner,
    )
    rows: list[tuple[str, str, str]] = []
    for record in listing.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            raw_mode, raw_type, raw_oid = metadata.split(b" ", 2)
            git_mode = raw_mode.decode("ascii")
            object_type = raw_type.decode("ascii")
            object_id = raw_oid.decode("ascii")
        except (ValueError, UnicodeDecodeError) as exc:
            raise CandidateDiffError("Git baseline listing is malformed") from exc
        if git_mode not in _GIT_MODES:
            raise CandidateDiffError(f"Git baseline contains unsupported mode {git_mode}")
        if _algorithm_for_oid(object_id) != algorithm:
            raise CandidateDiffError("Git baseline mixes object hash algorithms")
        path = _canonical_path(raw_path)
        if git_mode == "160000" and object_type != "commit":
            raise CandidateDiffError("Gitlink baseline entry has the wrong object type")
        if git_mode != "160000" and object_type != "blob":
            raise CandidateDiffError("non-gitlink baseline entry is not a blob")
        rows.append((path, git_mode, object_id))
    return rows


def _baseline_entries(
    target_state: Mapping[str, Any],
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> list[dict[str, Any]]:
    object_store = Path(str(target_state["object_store"]))
    commit = str(target_state["resolved_commit"])
    algorithm = _algorithm_for_oid(str(target_state["resolved_tree"]))
    entries: list[dict[str, Any]] = []
    total = 0
    for path, git_mode, object_id in _tree_listing(
        object_store, commit, algorithm, runner=runner
    ):
        if git_mode == "160000":
            data = object_id.encode("ascii")
        else:
            data = _run_git(object_store, ("cat-file", "blob", object_id), runner=runner)
            if len(data) > _MAX_FILE_BYTES:
                raise CandidateDiffError(f"Git baseline file exceeds its byte ceiling: {path}")
            if _git_object_id(algorithm, "blob", data) != object_id:
                raise CandidateDiffError(f"Git baseline blob address does not re-derive: {path}")
        total += len(data)
        if total > _MAX_TOTAL_BYTES:
            raise CandidateDiffError("Git baseline exceeds its total byte ceiling")
        entries.append(
            _entry(path=path, git_mode=git_mode, object_id=object_id, data=data)
        )
        if len(entries) > _MAX_FILES:
            raise CandidateDiffError("Git baseline exceeds its file-count ceiling")
    return entries


def _reference_entries(
    object_store: Path,
    commit: str,
    algorithm: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> list[dict[str, Any]]:
    """Digest every baseline blob straight from the object store, holding none of them.

    One ``git cat-file --batch`` streams the blobs in listing order.  Each one is hashed in chunks
    for both its SHA-256 content digest and its Git object id, and an object id that does not
    re-derive refuses the baseline exactly as the embedded form does.
    """

    listing = _tree_listing(object_store, commit, algorithm, runner=runner)
    blob_ids = [object_id for _path, git_mode, object_id in listing if git_mode != "160000"]
    process = subprocess.Popen(
        _git_argv(object_store, ("cat-file", "--batch")),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_git_environment(),
    )
    assert process.stdin is not None and process.stdout is not None

    def feed() -> None:
        assert process.stdin is not None
        try:
            for object_id in blob_ids:
                process.stdin.write(object_id.encode("ascii") + b"\n")
            process.stdin.close()
        except BrokenPipeError:
            pass

    writer = threading.Thread(target=feed, daemon=True)
    writer.start()
    entries: list[dict[str, Any]] = []
    try:
        for path, git_mode, object_id in listing:
            if git_mode == "160000":
                data = object_id.encode("ascii")
                entries.append(
                    _reference_entry(
                        path=path,
                        git_mode=git_mode,
                        object_id=object_id,
                        content_digest=digest_bytes(data),
                    )
                )
                continue
            header = process.stdout.readline().split()
            if len(header) != 3 or header[0].decode("ascii", "replace") != object_id:
                raise CandidateDiffError(f"Git baseline blob is missing from its store: {path}")
            if header[1] != b"blob":
                raise CandidateDiffError("non-gitlink baseline entry is not a blob")
            size = int(header[2])
            content = hashlib.sha256()
            address = hashlib.new(algorithm, usedforsecurity=False)
            address.update(f"blob {size}\0".encode("ascii"))
            remaining = size
            while remaining:
                chunk = process.stdout.read(min(remaining, _STREAM_CHUNK))
                if not chunk:
                    raise CandidateDiffError(f"Git baseline blob stream ended early: {path}")
                content.update(chunk)
                address.update(chunk)
                remaining -= len(chunk)
            if process.stdout.read(1) != b"\n":
                raise CandidateDiffError("Git baseline blob stream is malformed")
            if address.hexdigest() != object_id:
                raise CandidateDiffError(f"Git baseline blob address does not re-derive: {path}")
            entries.append(
                _reference_entry(
                    path=path,
                    git_mode=git_mode,
                    object_id=object_id,
                    content_digest=f"sha256:{content.hexdigest()}",
                )
            )
    finally:
        process.stdout.close()
        writer.join()
        stderr = process.stderr.read() if process.stderr is not None else b""
        returncode = process.wait()
    if returncode != 0:
        detail = stderr.decode("utf-8", errors="replace")[:1000]
        raise CandidateDiffError(f"Git baseline projection failed: {detail}")
    return entries


def _reference_entry(
    *,
    path: str,
    git_mode: str,
    object_id: str,
    content_digest: str,
) -> dict[str, Any]:
    entry_type, mode = _entry_attributes(path, git_mode)
    return {
        "path": path,
        "entry_type": entry_type,
        "git_mode": git_mode,
        "mode": mode,
        "object_id": object_id,
        "content_digest": content_digest,
    }


def _tree_object_id(entries: Sequence[Mapping[str, Any]], algorithm: str) -> str:
    tree: dict[str, Any] = {}
    for entry in entries:
        parts = PurePosixPath(str(entry["path"])).parts
        node = tree
        for part in parts[:-1]:
            existing = node.setdefault(part, {})
            if not isinstance(existing, dict):
                raise CandidateDiffError("Git baseline path collides with a file")
            node = existing
        if parts[-1] in node:
            raise CandidateDiffError("Git baseline repeats a path")
        node[parts[-1]] = dict(entry)

    def address(node: Mapping[str, Any]) -> str:
        encoded: list[tuple[bytes, bytes]] = []
        for name, value in node.items():
            name_bytes = name.encode("utf-8")
            if isinstance(value, Mapping) and "path" not in value:
                child = address(value)
                sort_key = name_bytes + b"/"
                body = b"40000 " + name_bytes + b"\0" + bytes.fromhex(child)
            else:
                assert isinstance(value, Mapping)
                sort_key = name_bytes
                body = (
                    str(value["git_mode"]).encode("ascii")
                    + b" "
                    + name_bytes
                    + b"\0"
                    + bytes.fromhex(str(value["object_id"]))
                )
            encoded.append((sort_key, body))
        payload = b"".join(body for _key, body in sorted(encoded, key=lambda item: item[0]))
        return _git_object_id(algorithm, "tree", payload)

    return address(tree)


def _candidate_entries(candidate_root: Path) -> list[dict[str, Any]]:
    if candidate_root.is_symlink() or not candidate_root.is_dir():
        raise CandidateDiffError("candidate root is missing, linked, or not a directory")
    entries: list[dict[str, Any]] = []
    for base, directories, names in os.walk(candidate_root, followlinks=False):
        directories.sort()
        names.sort()
        for directory in directories:
            if (Path(base) / directory).is_symlink():
                raise CandidateDiffError("candidate tree contains a directory symlink")
        for name in names:
            path = Path(base) / name
            relative = _canonical_path(path.relative_to(candidate_root).as_posix().encode())
            metadata = path.lstat()
            if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                raise CandidateDiffError(f"candidate entry is not a regular file: {relative}")
            data = path.read_bytes()
            entries.append(
                {
                    "path": relative,
                    "entry_type": "file",
                    "mode": stat.S_IMODE(metadata.st_mode),
                    "content_digest": digest_bytes(data),
                }
            )
    return entries


def _baseline_for_subpath(
    entries: Sequence[Mapping[str, Any]], subpath: str
) -> dict[str, dict[str, Any]]:
    prefix = f"{subpath}/" if subpath else ""
    selected: dict[str, dict[str, Any]] = {}
    for entry in entries:
        path = str(entry["path"])
        if prefix and not path.startswith(prefix):
            continue
        relative = path[len(prefix) :] if prefix else path
        selected[relative] = {
            "path": relative,
            "entry_type": str(entry["entry_type"]),
            "mode": int(entry["mode"]),
            "content_digest": str(entry["content_digest"]),
        }
    return selected


def _derive_changes(
    baseline: Mapping[str, Mapping[str, Any]],
    candidate: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for path in sorted(set(baseline) | set(candidate)):
        old = baseline.get(path)
        new = candidate.get(path)
        kind = _derive_change_kind(old, new)
        if kind is None:
            continue
        changes.append(
            {
                "path": path,
                "kind": kind,
                "old_type": None if old is None else old["entry_type"],
                "new_type": None if new is None else new["entry_type"],
                "old_mode": None if old is None else old["mode"],
                "new_mode": None if new is None else new["mode"],
                "old_digest": None if old is None else old["content_digest"],
                "new_digest": None if new is None else new["content_digest"],
            }
        )
    return changes


def _derive_change_kind(
    old: Mapping[str, Any] | None,
    new: Mapping[str, Any] | None,
) -> str | None:
    """Classify one row solely from its old/new semantic tuple."""

    if old is None:
        return "added" if new is not None else None
    if new is None:
        return "deleted"
    if old["entry_type"] != new["entry_type"]:
        return "type-changed"
    if old["content_digest"] != new["content_digest"]:
        return "modified"
    if old["mode"] != new["mode"]:
        return "mode-changed"
    return None


def _change_side(
    row: Mapping[str, Any],
    side: str,
    *,
    path: str,
) -> dict[str, Any] | None:
    """Normalize an old/new row side and reject partially populated tuples."""

    values = (
        row.get(f"{side}_type"),
        row.get(f"{side}_mode"),
        row.get(f"{side}_digest"),
    )
    present = tuple(value is not None for value in values)
    if any(present) and not all(present):
        raise CandidateDiffError(f"candidate change has a partial {side} tuple: {path}")
    if not any(present):
        return None
    return {
        "entry_type": values[0],
        "mode": values[1],
        "content_digest": values[2],
    }


def _old_side_paths(changes: Sequence[Mapping[str, Any]], subpath: str) -> list[str]:
    """Full baseline paths whose bytes a change replaces or deletes, in canonical order."""

    prefix = f"{subpath}/" if subpath else ""
    return sorted(f"{prefix}{row['path']}" for row in changes if row.get("old_digest") is not None)


def _embed_changed_files(
    object_store: Path,
    listing: Sequence[Mapping[str, Any]],
    paths: Sequence[str],
    algorithm: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> list[dict[str, Any]]:
    by_path = {str(entry["path"]): entry for entry in listing}
    embedded: list[dict[str, Any]] = []
    total = 0
    for path in paths:
        entry = by_path[path]
        object_id = str(entry["object_id"])
        git_mode = str(entry["git_mode"])
        if git_mode == "160000":
            data = object_id.encode("ascii")
        else:
            data = _run_git(object_store, ("cat-file", "blob", object_id), runner=runner)
            if len(data) > _MAX_FILE_BYTES:
                raise CandidateDiffError(
                    f"changed Git baseline file exceeds its byte ceiling: {path}"
                )
            if _git_object_id(algorithm, "blob", data) != object_id:
                raise CandidateDiffError(f"Git baseline blob address does not re-derive: {path}")
        total += len(data)
        if total > _MAX_TOTAL_BYTES:
            raise CandidateDiffError("changed Git baseline files exceed their total byte ceiling")
        embedded.append(_entry(path=path, git_mode=git_mode, object_id=object_id, data=data))
    return embedded


def build_candidate_review_context(
    *,
    target_state: Mapping[str, Any],
    candidate_root: str | Path,
    candidate_digest: str,
    construction_mode: str,
    baseline_storage: str = BASELINE_EMBEDDED,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a self-verifying baseline snapshot and complete candidate change set."""

    if construction_mode != "regenerate":
        raise CandidateDiffError(
            "brownfield review is INCOMPLETE until a signed changed-path ceiling and complete "
            "partial-output application contract exist"
        )
    if baseline_storage not in BASELINE_STORAGES:
        raise CandidateDiffError(f"unknown review baseline storage: {baseline_storage!r}")
    root = Path(candidate_root)
    try:
        actual_candidate_digest = tree_digest(root)
    except SnapshotError as exc:
        raise CandidateDiffError(str(exc)) from exc
    if actual_candidate_digest != candidate_digest:
        raise CandidateDiffError("candidate tree differs from its immutable address")
    object_store = Path(str(target_state["object_store"]))
    commit = str(target_state["resolved_commit"])
    resolved_tree = str(target_state["resolved_tree"])
    subpath = str(target_state.get("subpath", ""))
    algorithm = _algorithm_for_oid(resolved_tree)
    if baseline_storage == BASELINE_EMBEDDED:
        listing = _baseline_entries(target_state, runner=runner)
    else:
        listing = _reference_entries(object_store, commit, algorithm, runner=runner)
    if _tree_object_id(listing, algorithm) != resolved_tree:
        raise CandidateDiffError("embedded Git baseline does not reconstruct the resolved tree")
    baseline = _baseline_for_subpath(listing, subpath)
    candidate_rows = _candidate_entries(root)
    candidate = {str(row["path"]): row for row in candidate_rows}
    changes = _derive_changes(baseline, candidate)
    snapshot_body: dict[str, Any] = {
        "schema_version": _SNAPSHOT_SCHEMAS[baseline_storage],
        "resolved_commit": commit,
        "resolved_tree": resolved_tree,
        "hash_algorithm": algorithm,
        "subpath": subpath,
    }
    if baseline_storage == BASELINE_EMBEDDED:
        snapshot_body["files"] = listing
    else:
        snapshot_body["entry_count"] = len(listing)
        snapshot_body["listing_digest"] = digest_obj(listing)
        snapshot_body["changed_files"] = _embed_changed_files(
            object_store,
            listing,
            _old_side_paths(changes, subpath),
            algorithm,
            runner=runner,
        )
    base_snapshot = {
        **snapshot_body,
        "snapshot_digest": digest_obj(snapshot_body),
    }
    change_body = {
        "schema_version": "factory-candidate-change-set/1",
        "resolved_commit": commit,
        "resolved_tree": resolved_tree,
        "subpath": subpath,
        "construction_mode": construction_mode,
        "baseline_snapshot_digest": base_snapshot["snapshot_digest"],
        "candidate_digest": candidate_digest,
        "changed_path_digest": digest_obj([row["path"] for row in changes]),
        "changes": changes,
    }
    change_set = {**change_body, "change_set_digest": digest_obj(change_body)}
    if baseline_storage == BASELINE_EMBEDDED:
        verify_candidate_review_context(base_snapshot, change_set)
    else:
        _verify_reference_snapshot(base_snapshot, change_set, listing)
    return base_snapshot, change_set


def embedded_baseline_files(base_snapshot: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The baseline entries whose bytes the document itself carries, and so may be cited."""

    if base_snapshot.get("schema_version") == _SNAPSHOT_SCHEMAS[BASELINE_GIT_REFERENCE]:
        return list(base_snapshot.get("changed_files", []))
    return list(base_snapshot.get("files", []))


def _verify_embedded_files(
    files: Any,
    algorithm: str,
    *,
    label: str,
) -> list[dict[str, Any]]:
    """Re-derive every embedded entry from its own bytes, under the embedded byte ceilings."""

    if not isinstance(files, list) or len(files) > _MAX_FILES:
        raise CandidateDiffError(f"{label} has an invalid file set")
    seen: set[str] = set()
    total = 0
    normalized: list[dict[str, Any]] = []
    for raw in files:
        if not isinstance(raw, Mapping):
            raise CandidateDiffError(f"{label} file is not an object")
        entry = dict(raw)
        path = _canonical_path(str(entry.get("path", "")).encode())
        if path in seen:
            raise CandidateDiffError(f"{label} repeats a path")
        seen.add(path)
        try:
            data = base64.b64decode(str(entry["content_base64"]), validate=True)
        except (KeyError, ValueError) as exc:
            raise CandidateDiffError(f"{label} content is not canonical base64") from exc
        total += len(data)
        if len(data) > _MAX_FILE_BYTES or total > _MAX_TOTAL_BYTES:
            raise CandidateDiffError(f"{label} exceeds its byte ceiling")
        if digest_bytes(data) != entry.get("content_digest"):
            raise CandidateDiffError(f"{label} content changed: {path}")
        git_mode = str(entry.get("git_mode", ""))
        expected_entry_type, expected_mode = _entry_attributes(path, git_mode)
        if entry.get("entry_type") != expected_entry_type:
            raise CandidateDiffError(f"{label} entry type disagrees with Git mode: {path}")
        if type(entry.get("mode")) is not int or entry["mode"] != expected_mode:
            raise CandidateDiffError(f"{label} mode disagrees with Git mode: {path}")
        if "content_utf8" not in entry or entry["content_utf8"] != _display_text(data):
            raise CandidateDiffError(f"{label} UTF-8 display disagrees with content: {path}")
        object_id = str(entry.get("object_id", ""))
        if git_mode != "160000" and _git_object_id(algorithm, "blob", data) != object_id:
            raise CandidateDiffError(f"{label} blob changed: {path}")
        if git_mode == "160000" and data != object_id.encode("ascii"):
            raise CandidateDiffError(f"{label} gitlink changed: {path}")
        normalized.append(entry)
    return normalized


def verify_candidate_review_context(
    base_snapshot: Mapping[str, Any],
    change_set: Mapping[str, Any],
    *,
    object_store: str | Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> None:
    """Re-derive the baseline tree, candidate address, and change-set addresses.

    An embedded baseline verifies from its own bytes and ignores ``object_store``.  A Git-reference
    baseline re-reads its listing from ``object_store`` (the run-owned store that resolved the
    target) and refuses without it.
    """

    schema_version = base_snapshot.get("schema_version")
    if schema_version == _SNAPSHOT_SCHEMAS[BASELINE_GIT_REFERENCE]:
        if object_store is None:
            raise CandidateDiffError(
                "Git-reference baseline cannot be verified without its run-owned object store"
            )
        algorithm = str(base_snapshot.get("hash_algorithm", ""))
        if algorithm not in {"sha1", "sha256"}:
            raise CandidateDiffError(f"unsupported Git object hash algorithm: {algorithm}")
        listing = _reference_entries(
            Path(object_store),
            str(base_snapshot.get("resolved_commit", "")),
            algorithm,
            runner=runner,
        )
        _verify_reference_snapshot(base_snapshot, change_set, listing)
        return
    snapshot = dict(base_snapshot)
    snapshot_digest = str(snapshot.pop("snapshot_digest", ""))
    if schema_version != _SNAPSHOT_SCHEMAS[BASELINE_EMBEDDED]:
        raise CandidateDiffError("base source snapshot has the wrong schema")
    if digest_obj(snapshot) != snapshot_digest:
        raise CandidateDiffError("base source snapshot digest does not re-derive")
    algorithm = str(snapshot.get("hash_algorithm", ""))
    normalized = _verify_embedded_files(
        snapshot.get("files"), algorithm, label="base source snapshot"
    )
    if _tree_object_id(normalized, algorithm) != snapshot.get("resolved_tree"):
        raise CandidateDiffError("base source snapshot does not reconstruct its Git tree")
    _verify_change_set(snapshot, snapshot_digest, change_set, normalized)


def _verify_reference_snapshot(
    base_snapshot: Mapping[str, Any],
    change_set: Mapping[str, Any],
    listing: Sequence[Mapping[str, Any]],
) -> None:
    """Check a Git-reference baseline against the listing its object store re-derived."""

    snapshot = dict(base_snapshot)
    snapshot_digest = str(snapshot.pop("snapshot_digest", ""))
    if snapshot.get("schema_version") != _SNAPSHOT_SCHEMAS[BASELINE_GIT_REFERENCE]:
        raise CandidateDiffError("base source snapshot has the wrong schema")
    if digest_obj(snapshot) != snapshot_digest:
        raise CandidateDiffError("base source snapshot digest does not re-derive")
    algorithm = str(snapshot.get("hash_algorithm", ""))
    if _tree_object_id(listing, algorithm) != snapshot.get("resolved_tree"):
        raise CandidateDiffError("base source snapshot does not reconstruct its Git tree")
    if snapshot.get("entry_count") != len(listing):
        raise CandidateDiffError("base source snapshot entry count does not re-derive")
    if snapshot.get("listing_digest") != digest_obj(list(listing)):
        raise CandidateDiffError("base source snapshot listing digest does not re-derive")
    embedded = _verify_embedded_files(
        snapshot.get("changed_files"), algorithm, label="changed baseline files"
    )
    by_path = {str(entry["path"]): entry for entry in listing}
    for entry in embedded:
        listed = by_path.get(str(entry["path"]))
        if listed is None or any(
            listed[field] != entry[field]
            for field in ("git_mode", "object_id", "content_digest")
        ):
            raise CandidateDiffError(
                f"changed baseline file differs from the baseline listing: {entry['path']}"
            )
    _verify_change_set(snapshot, snapshot_digest, change_set, listing)
    expected_paths = _old_side_paths(
        [row for row in change_set["changes"] if isinstance(row, Mapping)],
        str(snapshot.get("subpath", "")),
    )
    if [str(entry["path"]) for entry in embedded] != expected_paths:
        raise CandidateDiffError(
            "changed baseline files are not exactly the paths the change set replaces or deletes"
        )


def _verify_change_set(
    snapshot: Mapping[str, Any],
    snapshot_digest: str,
    change_set: Mapping[str, Any],
    baseline_entries: Sequence[Mapping[str, Any]],
) -> None:
    change = dict(change_set)
    change_digest = str(change.pop("change_set_digest", ""))
    if change.get("schema_version") != "factory-candidate-change-set/1":
        raise CandidateDiffError("candidate change set has the wrong schema")
    if digest_obj(change) != change_digest:
        raise CandidateDiffError("candidate change-set digest does not re-derive")
    if (
        change.get("resolved_commit") != snapshot.get("resolved_commit")
        or change.get("resolved_tree") != snapshot.get("resolved_tree")
        or change.get("subpath") != snapshot.get("subpath")
        or change.get("baseline_snapshot_digest") != snapshot_digest
        or change.get("construction_mode") != "regenerate"
    ):
        raise CandidateDiffError("candidate change set belongs to another baseline")
    changes = change.get("changes")
    if not isinstance(changes, list):
        raise CandidateDiffError("candidate change set has no change rows")
    paths = [str(row.get("path", "")) for row in changes if isinstance(row, Mapping)]
    if len(paths) != len(changes) or paths != sorted(paths) or len(paths) != len(set(paths)):
        raise CandidateDiffError("candidate change-set paths are not canonical and unique")
    if digest_obj(paths) != change.get("changed_path_digest"):
        raise CandidateDiffError("candidate changed-path digest does not re-derive")
    baseline = _baseline_for_subpath(baseline_entries, str(snapshot.get("subpath", "")))
    resulting = dict(baseline)
    for raw in changes:
        assert isinstance(raw, Mapping)
        path = _canonical_path(str(raw["path"]).encode())
        old = resulting.get(path)
        row_old = _change_side(raw, "old", path=path)
        row_new = _change_side(raw, "new", path=path)
        expected_kind = _derive_change_kind(row_old, row_new)
        if expected_kind is None:
            raise CandidateDiffError(f"candidate change row has no semantic change: {path}")
        if raw.get("kind") != expected_kind:
            raise CandidateDiffError(
                f"candidate change kind must re-derive as {expected_kind}: {path}"
            )
        if (None if old is None else old["entry_type"]) != raw.get("old_type"):
            raise CandidateDiffError(f"candidate change has the wrong old type: {path}")
        if (None if old is None else old["mode"]) != raw.get("old_mode"):
            raise CandidateDiffError(f"candidate change has the wrong old mode: {path}")
        if (None if old is None else old["content_digest"]) != raw.get("old_digest"):
            raise CandidateDiffError(f"candidate change has the wrong old digest: {path}")
        if raw.get("new_type") is None:
            resulting.pop(path, None)
        else:
            resulting[path] = {
                "path": path,
                "entry_type": str(raw["new_type"]),
                "mode": int(raw["new_mode"]),
                "content_digest": str(raw["new_digest"]),
            }
    if any(row["entry_type"] != "file" for row in resulting.values()):
        raise CandidateDiffError("resulting candidate contains an unmaterialized non-file entry")
    reconstructed_candidate = digest_obj(
        {
            "files": [
                {
                    "path": path,
                    "mode": int(resulting[path]["mode"]),
                    "digest": str(resulting[path]["content_digest"]),
                }
                for path in sorted(resulting)
            ]
        }
    )
    if reconstructed_candidate != change.get("candidate_digest"):
        raise CandidateDiffError("candidate change set does not reconstruct the candidate tree")


__all__ = [
    "BASELINE_EMBEDDED",
    "BASELINE_GIT_REFERENCE",
    "BASELINE_STORAGES",
    "CandidateDiffError",
    "build_candidate_review_context",
    "embedded_baseline_files",
    "verify_candidate_review_context",
]
