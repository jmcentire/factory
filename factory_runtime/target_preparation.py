"""Prepare exact target data from an actually exercised complete-tree construction mechanism.

Preparation neither contacts a target nor grants authority. The catalog retains the production
source closure and actual deterministic probe observations; independent acceptance, Stage R/E,
and runner admission remain separate. Existing output is revalidated, never refreshed in place.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from factory_core.build_plan import PatternCatalog, PatternDefinition
from factory_core.manifest import digest_bytes, digest_obj
from factory_core.target import load_target_manifest_bytes
from factory_runtime.candidate_diff import (
    CandidateDiffError,
    build_candidate_review_context,
    verify_candidate_review_context,
)
from factory_runtime.snapshot import SnapshotError, freeze_tree, tree_digest, verify_frozen_tree

BUILTIN_PATTERN_ID = "complete-tree/1"
_PROBES = (
    "complete-delta-and-unchanged-file",
    "snapshot-roundtrip",
    "candidate-digest-refusal",
    "rehashed-baseline-tamper-refusal",
    "candidate-symlink-refusal",
    "brownfield-refusal",
)
_FILES = frozenset(
    {
        "target-manifest.toml",
        "pattern-catalog.json",
        "construction-artifact.json",
        "qualification-report.json",
    }
)
_MAX_BUNDLE_FILE = 16 * 1024 * 1024


def _json(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _relative(value: str, *, optional: bool = False) -> str:
    path = PurePosixPath(value)
    if optional and value == "":
        return value
    if (
        not value
        or path.is_absolute()
        or path.as_posix() != value
        or "\\" in value
        or any(c in value for c in "\x00\n\r\t")
        or any(part in {".", "..", ".git"} for part in path.parts)
    ):
        raise ValueError("target path must be a canonical safe relative path")
    return value


def _coordinates(url: str, ref: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"https", "ssh"}
        or not parsed.hostname
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path
        or any(c.isspace() or ord(c) < 32 for c in url)
    ):
        raise ValueError("repository URL must be credential-free HTTPS or SSH without userinfo")
    _ = parsed.port
    if (
        not ref
        or ref.startswith(("-", "/"))
        or ref.endswith(("/", ".", ".lock"))
        or ".." in ref
        or "@{" in ref
        or "//" in ref
        or ref == "@"
        or any(c.isspace() or ord(c) < 32 or c in "~^:?*[\\" for c in ref)
        or any(part.startswith(".") or part.endswith(".lock") for part in ref.split("/"))
    ):
        raise ValueError("repository ref must be an explicit safe Git ref or object id")


def _source_artifact() -> dict[str, Any]:
    root = Path(__file__).resolve().parent.parent
    files = []
    # A conservative superset of the transitive production closure: changes anywhere in the
    # installed packages require fresh qualification, rather than guessing dynamic imports.
    for package in ("factory_core", "factory_runtime"):
        for path in sorted((root / package).rglob("*")):
            if path.suffix not in {".py", ".json"} or "__pycache__" in path.parts:
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError("construction source closure contains a non-regular file")
            data = path.read_bytes()
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "digest": digest_bytes(data),
                    "content_base64": base64.b64encode(data).decode("ascii"),
                }
            )
    if not files:
        raise ValueError("construction implementation source closure is empty")
    return {
        "schema_version": "factory-complete-tree-construction/1",
        "mechanism_id": BUILTIN_PATTERN_ID,
        "files": files,
        "contract": {
            "construction_mode": "regenerate",
            "output": "complete-target-subtree",
            "omitted_baseline_paths": "deleted",
            "sparse_overlay": "unsupported",
        },
    }


def _git_env() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_DATE": "2020-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2020-01-01T00:00:00Z",
    }


def _git(*arguments: str, cwd: Path) -> str:
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgSign=false",
                "-c",
                "user.name=Factory qualification",
                "-c",
                "user.email=qualification@invalid",
                *arguments,
            ],
            cwd=cwd,
            env=_git_env(),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("construction qualification could not execute local Git") from exc
    if result.returncode:
        raise ValueError("construction qualification local Git command failed")
    return result.stdout.strip()


def _identity() -> dict[str, str]:
    executable = Path(sys.executable).resolve()
    git = shutil.which("git")
    if git is None:
        raise ValueError("construction qualification requires local Git")
    git_path = Path(git).resolve()
    return {
        "python_version": sys.version,
        "python_implementation": platform.python_implementation(),
        "python_executable_digest": digest_bytes(executable.read_bytes()),
        "git_version": _git("--version", cwd=Path.cwd()),
        "git_executable_digest": digest_bytes(git_path.read_bytes()),
        "platform": platform.platform(),
    }


def _qualify(artifact: Mapping[str, Any], identity: Mapping[str, str]) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="factory-complete-tree-") as temporary:
        root = Path(temporary)
        repo, candidate = root / "baseline", root / "candidate"
        repo.mkdir()
        candidate.mkdir()
        initial = {"keep.txt": b"unchanged\n", "edit.txt": b"before\n", "delete.txt": b"remove\n"}
        for name, data in initial.items():
            (repo / name).write_bytes(data)
        _git("init", "--quiet", cwd=repo)
        _git("add", "--all", cwd=repo)
        _git("commit", "--quiet", "-m", "qualification baseline", cwd=repo)
        state = {
            "object_store": str(repo / ".git"),
            "resolved_commit": _git("rev-parse", "HEAD", cwd=repo),
            "resolved_tree": _git("rev-parse", "HEAD^{tree}", cwd=repo),
            "subpath": "",
        }
        for name, data in {
            "keep.txt": initial["keep.txt"],
            "edit.txt": b"after\n",
            "add.txt": b"new\n",
        }.items():
            (candidate / name).write_bytes(data)
            (candidate / name).chmod(0o644)
        candidate_digest = tree_digest(candidate)
        snapshot, changes = build_candidate_review_context(
            target_state=state,
            candidate_root=candidate,
            candidate_digest=candidate_digest,
            construction_mode="regenerate",
            baseline_storage="git-reference",
        )
        verify_candidate_review_context(snapshot, changes, object_store=state["object_store"])
        actual = [(row["path"], row["kind"]) for row in changes["changes"]]
        expected = [("add.txt", "added"), ("delete.txt", "deleted"), ("edit.txt", "modified")]
        if actual != expected or (candidate / "keep.txt").read_bytes() != initial["keep.txt"]:
            raise ValueError("complete-tree positive qualification failed")
        observations.append(
            {
                "probe_id": _PROBES[0],
                "passed": True,
                "observed_changes": actual,
                "candidate_digest": candidate_digest,
                "unchanged_file_digest": digest_bytes(initial["keep.txt"]),
            }
        )
        frozen = freeze_tree(candidate, root / "snapshots", durable_through=root)
        verify_frozen_tree(frozen.directory, expected_digest=frozen.digest)
        if tree_digest(candidate) != candidate_digest:
            raise ValueError("snapshot qualification changed source bytes")
        observations.append(
            {"probe_id": _PROBES[1], "passed": True, "snapshot_digest": frozen.digest}
        )

        def refusal(probe_id: str, operation: Any) -> None:
            try:
                operation()
            except (CandidateDiffError, SnapshotError) as exc:
                observations.append(
                    {
                        "probe_id": probe_id,
                        "passed": True,
                        "observed_exception": type(exc).__name__,
                        "observed_reason": str(exc).replace(str(root), "<fixture>"),
                    }
                )
            else:
                raise ValueError(f"construction qualification did not refuse {probe_id}")

        refusal(
            _PROBES[2],
            lambda: build_candidate_review_context(
                target_state=state,
                candidate_root=candidate,
                candidate_digest="sha256:" + "0" * 64,
                construction_mode="regenerate",
                baseline_storage="git-reference",
            ),
        )
        tampered = copy.deepcopy(snapshot)
        tampered["listing_digest"] = "sha256:" + "0" * 64
        tampered["snapshot_digest"] = digest_obj(
            {key: value for key, value in tampered.items() if key != "snapshot_digest"}
        )
        tampered_changes = copy.deepcopy(changes)
        tampered_changes["baseline_snapshot_digest"] = tampered["snapshot_digest"]
        tampered_changes["change_set_digest"] = digest_obj(
            {key: value for key, value in tampered_changes.items() if key != "change_set_digest"}
        )
        refusal(
            _PROBES[3],
            lambda: verify_candidate_review_context(
                tampered, tampered_changes, object_store=state["object_store"]
            ),
        )
        (candidate / "link.txt").symlink_to("keep.txt")
        refusal(_PROBES[4], lambda: tree_digest(candidate))
        (candidate / "link.txt").unlink()
        refusal(
            _PROBES[5],
            lambda: build_candidate_review_context(
                target_state=state,
                candidate_root=candidate,
                candidate_digest=candidate_digest,
                construction_mode="brownfield",
                baseline_storage="git-reference",
            ),
        )
    return {
        "schema_version": "factory-complete-tree-qualification/1",
        "artifact_digest": digest_obj(dict(artifact)),
        "runtime": dict(identity),
        "scope": "deterministic complete-tree host mechanism; not model or target qualification",
        "observations": observations,
        "passed": True,
    }


def verify_builtin_catalog(catalog: Mapping[str, Any]) -> None:
    """Verify retained built-in payloads without broadening trust for external catalogs."""
    for pattern in catalog.get("patterns", []):
        if pattern.get("pattern_id") != BUILTIN_PATTERN_ID:
            continue
        mechanism = pattern.get("mechanism", {})
        artifact, report = mechanism.get("artifact"), mechanism.get("qualification_evidence")
        if not isinstance(artifact, dict) or not isinstance(report, dict):
            raise ValueError("built-in construction artifact or qualification evidence is missing")
        if (
            digest_obj(artifact) != pattern.get("artifact_digest")
            or digest_obj(report) != pattern.get("qualification_evidence_digest")
            or report.get("artifact_digest") != digest_obj(artifact)
        ):
            raise ValueError("built-in construction qualification payload digest mismatch")
        identity = _identity()
        if artifact != _source_artifact() or report.get("runtime") != identity:
            raise ValueError(
                "built-in construction qualification has stale source or runtime identity"
            )
        observations = report.get("observations", [])
        if (
            report.get("schema_version") != "factory-complete-tree-qualification/1"
            or report.get("passed") is not True
            or not isinstance(observations, list)
            or any(not isinstance(row, dict) for row in observations)
            or [row.get("probe_id") for row in observations] != list(_PROBES)
            or any(row.get("passed") is not True for row in observations)
        ):
            raise ValueError(
                "built-in construction qualification inventory is incomplete or failed"
            )
        # A rehashed report can still invent observed results. Execute the mechanism again
        # and compare the whole report, not just its shape or its claimed success flags.
        actual_report = _qualify(artifact, identity)
        if artifact != _source_artifact() or identity != _identity():
            raise ValueError("construction source or runtime changed during verification")
        if _json(report) != _json(actual_report):
            raise ValueError("built-in qualification observations do not rederive from execution")


def _safe_parent(destination: Path) -> None:
    for path in (*reversed(destination.parents), destination):
        if path.is_symlink():
            raise ValueError("preparation output path contains a symlink")
        if path.exists() and not path.is_dir():
            raise ValueError("preparation output path contains a non-directory")
    if not destination.parent.is_dir():
        raise ValueError("preparation output parent must already exist")


def _open_parent(destination: Path) -> int:
    descriptor = os.open(destination.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in destination.parent.parts[1:]:
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _verify_payloads(descriptor: int, payloads: Mapping[str, bytes]) -> None:
    """Verify every published name against its exact intended regular-file bytes."""
    if set(os.listdir(descriptor)) != _FILES:
        raise ValueError("preparation output has foreign or missing files; refusing overwrite")
    for name, expected in payloads.items():
        entry = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        try:
            metadata = os.fstat(entry)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size > _MAX_BUNDLE_FILE
            ):
                raise ValueError("preparation output contains a foreign file type")
            chunks = []
            while chunk := os.read(entry, 65536):
                chunks.append(chunk)
            after = os.fstat(entry)
            if (metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise ValueError("preparation output changed while being verified")
            named = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if (
                not stat.S_ISREG(named.st_mode)
                or named.st_nlink != 1
                or (named.st_dev, named.st_ino) != (after.st_dev, after.st_ino)
            ):
                raise ValueError("preparation output name changed while being verified")
            if b"".join(chunks) != expected:
                raise ValueError("preparation output is stale or modified; refusing overwrite")
        finally:
            os.close(entry)
    if set(os.listdir(descriptor)) != _FILES:
        raise ValueError("preparation output inventory changed during verification")


def _publish(destination: Path, payloads: Mapping[str, bytes]) -> bool:
    _safe_parent(destination)
    parent_fd = _open_parent(destination)
    descriptor = -1
    try:
        try:
            os.mkdir(destination.name, mode=0o700, dir_fd=parent_fd)
            reused = False
        except FileExistsError:
            reused = True
        descriptor = os.open(
            destination.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
        )
        if not reused:
            # Evidence names must be durable before the manifest can name their catalog.
            publication_order = [name for name in payloads if name != "target-manifest.toml"]
            publication_order.append("target-manifest.toml")
            for name in publication_order:
                if name == "target-manifest.toml":
                    os.fsync(descriptor)
                data = payloads[name]
                entry = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=descriptor,
                )
                with os.fdopen(entry, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            os.fsync(descriptor)
            os.fsync(parent_fd)
        # Fresh output is mutable too: neither successful writes nor fsync attest its
        # current bytes. Revalidate new publication and reuse through the same reader.
        _verify_payloads(descriptor, payloads)
        _safe_parent(destination)
        if set(os.listdir(descriptor)) != _FILES:
            raise ValueError("preparation output inventory changed during publication")
        installed = os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(installed.st_mode) or (installed.st_dev, installed.st_ino) != (
            opened.st_dev,
            opened.st_ino,
        ):
            raise ValueError("preparation output directory changed during publication")
        return reused
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)


def prepare_target(
    *,
    target_id: str,
    repository_url: str,
    ref: str,
    output_dir: str | Path,
    compliance_rules_path: str,
    subpath: str = "",
    max_attempts: int = 4,
    max_files: int | None = None,
    max_file_bytes: int | None = None,
    max_total_bytes: int | None = None,
) -> dict[str, Any]:
    """Produce local target data only after real mechanism probes pass; never authorize a run."""
    if not re.fullmatch(r"[a-z][a-z0-9-]*", target_id):
        raise ValueError("target id must be lowercase letters, digits and hyphens")
    _coordinates(repository_url, ref)
    _relative(subpath, optional=True)
    _relative(compliance_rules_path)
    if max_attempts < 1 or any(
        value is not None and value < 1 for value in (max_files, max_file_bytes, max_total_bytes)
    ):
        raise ValueError("build attempts and projection limits must be positive")
    destination = Path(os.path.abspath(output_dir))
    _safe_parent(destination)
    artifact, identity = _source_artifact(), _identity()
    report = _qualify(artifact, identity)
    if artifact != _source_artifact() or identity != _identity():
        raise ValueError("construction source or runtime changed during qualification")
    pattern = PatternDefinition(
        pattern_id=BUILTIN_PATTERN_ID,
        version="1",
        artifact_digest=digest_obj(artifact),
        qualification_evidence_digest=digest_obj(report),
        mechanism={
            "kind": "complete-tree",
            "required_configuration": ["artifact_directory"],
            "artifact": artifact,
            "qualification_evidence": report,
        },
    )
    catalog = PatternCatalog(
        catalog_id="factory-builtin-complete-tree", version="1", patterns=(pattern,)
    )
    catalog_document = catalog.body()
    verify_builtin_catalog(catalog_document)
    q = json.dumps
    lines = [
        'schema_version = "factory-target-manifest/2"',
        f"target_id = {q(target_id)}",
        "[repo]",
        f"url = {q(repository_url)}",
        f"ref = {q(ref)}",
        f"subpath = {q(subpath)}",
        'scope_tier = "write"',
        "[adapters]",
        'repo = "readonly_git"',
        'knowledge = "kin_reader"',
        'compliance = "rules_json"',
        'idp = "oidc"',
        'artifact_sink = "local_fs"',
        "[compliance]",
        f"rules_path = {q(compliance_rules_path)}",
        'format = "json"',
        "[build]",
        f"pattern_catalog_digest = {q(catalog.content_digest)}",
        f"max_attempts = {max_attempts}",
        'construction_modes = ["regenerate"]',
        'review_baseline = "git-reference"',
        "[build.signal]",
        f"signal_pass_deadline = {max_attempts}",
        f"signal_pass_warn = {max(1, max_attempts - 1)}",
        "signal_wall_clock_cap_hours = 24",
    ]
    limits = {
        "max_files": max_files,
        "max_file_bytes": max_file_bytes,
        "max_total_bytes": max_total_bytes,
    }
    if any(value is not None for value in limits.values()):
        lines.append("[build.projection_limits]")
        lines.extend(f"{key} = {value}" for key, value in limits.items() if value is not None)
    manifest_bytes = ("\n".join(lines) + "\n").encode()
    manifest = load_target_manifest_bytes(manifest_bytes)
    payloads = {
        "target-manifest.toml": manifest_bytes,
        "pattern-catalog.json": _json(catalog_document),
        "construction-artifact.json": _json(artifact),
        "qualification-report.json": _json(report),
    }
    reused = _publish(destination, payloads)
    return {
        "status": "prepared",
        "reused": reused,
        "manifest_path": str(destination / "target-manifest.toml"),
        "manifest_digest": manifest.source_digest,
        "catalog_path": str(destination / "pattern-catalog.json"),
        "catalog_digest": catalog.content_digest,
        "qualification_report_path": str(destination / "qualification-report.json"),
        "qualification_report_digest": digest_obj(report),
        "construction_artifact_path": str(destination / "construction-artifact.json"),
        "construction_artifact_digest": digest_obj(artifact),
    }
