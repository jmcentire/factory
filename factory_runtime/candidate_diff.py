"""Self-contained immutable Git baseline and candidate change-set derivation.

The Validator cannot review architecture, redundancy, or scope from generated files alone.  This
module projects the exact resolved Git tree into a bounded canonical document, proves that its
embedded blob bytes reconstruct the retained Git tree object, and derives the complete resulting
candidate change set.  The current candidate ABI is complete-tree only; brownfield partial-output
semantics remain unsupported and therefore fail closed instead of manufacturing a scope claim.
"""

from __future__ import annotations

import base64
import hashlib
import os
import stat
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from factory_core.manifest import digest_bytes, digest_obj
from factory_runtime.snapshot import SnapshotError, tree_digest

_MAX_FILES = 4_096
_MAX_FILE_BYTES = 524_288
_MAX_TOTAL_BYTES = 1_200_000
_GIT_MODES = {"100644", "100755", "120000", "160000"}


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


def _entry(
    *,
    path: str,
    git_mode: str,
    object_id: str,
    data: bytes,
) -> dict[str, Any]:
    if git_mode in {"100644", "100755"}:
        entry_type = "file"
        mode = 0o755 if git_mode == "100755" else 0o644
    elif git_mode == "120000":
        entry_type = "symlink"
        mode = 0o777
    elif git_mode == "160000":
        entry_type = "gitlink"
        mode = 0
    else:
        raise CandidateDiffError(f"unsupported Git mode for {path}: {git_mode}")
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


def _run_git(
    object_store: Path,
    arguments: Sequence[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> bytes:
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "LC_ALL": "C",
    }
    result = runner(
        [
            "git",
            "-c",
            "credential.helper=",
            "--git-dir",
            str(object_store),
            *arguments,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=120,
        env=environment,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace")[:1000]
        raise CandidateDiffError(f"Git baseline projection failed: {detail}")
    return result.stdout


def _baseline_entries(
    target_state: Mapping[str, Any],
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> list[dict[str, Any]]:
    object_store = Path(str(target_state["object_store"]))
    commit = str(target_state["resolved_commit"])
    listing = _run_git(
        object_store,
        ("ls-tree", "-rz", "--full-tree", commit),
        runner=runner,
    )
    algorithm = _algorithm_for_oid(str(target_state["resolved_tree"]))
    entries: list[dict[str, Any]] = []
    total = 0
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
        if git_mode == "160000":
            if object_type != "commit":
                raise CandidateDiffError("Gitlink baseline entry has the wrong object type")
            data = object_id.encode("ascii")
        else:
            if object_type != "blob":
                raise CandidateDiffError("non-gitlink baseline entry is not a blob")
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
        if old is None:
            kind = "added"
        elif new is None:
            kind = "deleted"
        elif old["entry_type"] != new["entry_type"]:
            kind = "type-changed"
        elif old["content_digest"] != new["content_digest"]:
            kind = "modified"
        elif old["mode"] != new["mode"]:
            kind = "mode-changed"
        else:
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


def build_candidate_review_context(
    *,
    target_state: Mapping[str, Any],
    candidate_root: str | Path,
    candidate_digest: str,
    construction_mode: str,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a self-verifying baseline snapshot and complete candidate change set."""

    if construction_mode != "regenerate":
        raise CandidateDiffError(
            "brownfield review is INCOMPLETE until a signed changed-path ceiling and complete "
            "partial-output application contract exist"
        )
    root = Path(candidate_root)
    try:
        actual_candidate_digest = tree_digest(root)
    except SnapshotError as exc:
        raise CandidateDiffError(str(exc)) from exc
    if actual_candidate_digest != candidate_digest:
        raise CandidateDiffError("candidate tree differs from its immutable address")
    baseline_entries = _baseline_entries(target_state, runner=runner)
    algorithm = _algorithm_for_oid(str(target_state["resolved_tree"]))
    reconstructed_tree = _tree_object_id(baseline_entries, algorithm)
    if reconstructed_tree != str(target_state["resolved_tree"]):
        raise CandidateDiffError("embedded Git baseline does not reconstruct the resolved tree")
    snapshot_body = {
        "schema_version": "factory-base-source-snapshot/1",
        "resolved_commit": str(target_state["resolved_commit"]),
        "resolved_tree": str(target_state["resolved_tree"]),
        "hash_algorithm": algorithm,
        "subpath": str(target_state.get("subpath", "")),
        "files": baseline_entries,
    }
    base_snapshot = {
        **snapshot_body,
        "snapshot_digest": digest_obj(snapshot_body),
    }
    baseline = _baseline_for_subpath(baseline_entries, str(target_state.get("subpath", "")))
    candidate_rows = _candidate_entries(root)
    candidate = {str(row["path"]): row for row in candidate_rows}
    changes = _derive_changes(baseline, candidate)
    change_body = {
        "schema_version": "factory-candidate-change-set/1",
        "resolved_commit": str(target_state["resolved_commit"]),
        "resolved_tree": str(target_state["resolved_tree"]),
        "subpath": str(target_state.get("subpath", "")),
        "construction_mode": construction_mode,
        "baseline_snapshot_digest": base_snapshot["snapshot_digest"],
        "candidate_digest": candidate_digest,
        "changed_path_digest": digest_obj([row["path"] for row in changes]),
        "changes": changes,
    }
    change_set = {**change_body, "change_set_digest": digest_obj(change_body)}
    verify_candidate_review_context(base_snapshot, change_set)
    return base_snapshot, change_set


def verify_candidate_review_context(
    base_snapshot: Mapping[str, Any],
    change_set: Mapping[str, Any],
) -> None:
    """Re-derive the embedded baseline tree, candidate address, and change-set addresses."""

    snapshot = dict(base_snapshot)
    snapshot_digest = str(snapshot.pop("snapshot_digest", ""))
    if snapshot.get("schema_version") != "factory-base-source-snapshot/1":
        raise CandidateDiffError("base source snapshot has the wrong schema")
    if digest_obj(snapshot) != snapshot_digest:
        raise CandidateDiffError("base source snapshot digest does not re-derive")
    files = snapshot.get("files")
    if not isinstance(files, list) or len(files) > _MAX_FILES:
        raise CandidateDiffError("base source snapshot has an invalid file set")
    algorithm = str(snapshot.get("hash_algorithm", ""))
    seen: set[str] = set()
    total = 0
    normalized: list[dict[str, Any]] = []
    for raw in files:
        if not isinstance(raw, Mapping):
            raise CandidateDiffError("base source snapshot file is not an object")
        entry = dict(raw)
        path = _canonical_path(str(entry.get("path", "")).encode())
        if path in seen:
            raise CandidateDiffError("base source snapshot repeats a path")
        seen.add(path)
        try:
            data = base64.b64decode(str(entry["content_base64"]), validate=True)
        except (KeyError, ValueError) as exc:
            raise CandidateDiffError(
                "base source snapshot content is not canonical base64"
            ) from exc
        total += len(data)
        if len(data) > _MAX_FILE_BYTES or total > _MAX_TOTAL_BYTES:
            raise CandidateDiffError("base source snapshot exceeds its byte ceiling")
        if digest_bytes(data) != entry.get("content_digest"):
            raise CandidateDiffError(f"base source snapshot content changed: {path}")
        git_mode = str(entry.get("git_mode", ""))
        object_id = str(entry.get("object_id", ""))
        if git_mode != "160000" and _git_object_id(algorithm, "blob", data) != object_id:
            raise CandidateDiffError(f"base source snapshot blob changed: {path}")
        if git_mode == "160000" and data != object_id.encode("ascii"):
            raise CandidateDiffError(f"base source snapshot gitlink changed: {path}")
        normalized.append(entry)
    if _tree_object_id(normalized, algorithm) != snapshot.get("resolved_tree"):
        raise CandidateDiffError("base source snapshot does not reconstruct its Git tree")

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
    baseline = _baseline_for_subpath(normalized, str(snapshot.get("subpath", "")))
    resulting = dict(baseline)
    for raw in changes:
        assert isinstance(raw, Mapping)
        path = _canonical_path(str(raw["path"]).encode())
        old = resulting.get(path)
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
    "CandidateDiffError",
    "build_candidate_review_context",
    "verify_candidate_review_context",
]
