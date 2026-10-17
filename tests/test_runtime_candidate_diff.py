from __future__ import annotations

import copy
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from factory_core.manifest import digest_bytes, digest_obj
from factory_runtime.candidate_diff import (
    CandidateDiffError,
    build_candidate_review_context,
    embedded_baseline_files,
    verify_candidate_review_context,
)
from factory_runtime.snapshot import tree_digest


def _git(*arguments: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _fixture(tmp_path: Path) -> tuple[dict[str, object], Path]:
    source = tmp_path / "source"
    source.mkdir()
    _git("init", "-q", cwd=source)
    (source / "README.md").write_text("old\n", encoding="utf-8")
    (source / "deleted.txt").write_text("delete me\n", encoding="utf-8")
    (source / "unchanged.txt").write_text("same\n", encoding="utf-8")
    script = source / "script.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o644)
    _git("add", ".", cwd=source)
    _git(
        "-c",
        "user.name=Factory Test",
        "-c",
        "user.email=factory@example.test",
        "commit",
        "-qm",
        "baseline",
        cwd=source,
    )
    commit = _git("rev-parse", "HEAD", cwd=source)
    tree = _git("rev-parse", "HEAD^{tree}", cwd=source)
    object_store = tmp_path / "objects.git"
    _git("clone", "-q", "--bare", str(source), str(object_store))

    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "README.md").write_text("new\n", encoding="utf-8")
    changed_script = candidate / "script.sh"
    changed_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    changed_script.chmod(0o755)
    (candidate / "added.txt").write_text("new file\n", encoding="utf-8")
    (candidate / "unchanged.txt").write_text("same\n", encoding="utf-8")
    state: dict[str, object] = {
        "object_store": str(object_store),
        "resolved_commit": commit,
        "resolved_tree": tree,
        "subpath": "",
    }
    return state, candidate


def _rehash_baseline_and_change_set(
    baseline: dict[str, object],
    changes: dict[str, object],
) -> None:
    snapshot_body = {key: value for key, value in baseline.items() if key != "snapshot_digest"}
    baseline["snapshot_digest"] = digest_obj(snapshot_body)
    changes["baseline_snapshot_digest"] = baseline["snapshot_digest"]
    change_body = {key: value for key, value in changes.items() if key != "change_set_digest"}
    changes["change_set_digest"] = digest_obj(change_body)


def _rehash_change_set(changes: dict[str, object]) -> None:
    change_body = {key: value for key, value in changes.items() if key != "change_set_digest"}
    changes["change_set_digest"] = digest_obj(change_body)


def test_candidate_review_context_is_complete_deterministic_and_self_verifying(
    tmp_path: Path,
) -> None:
    state, candidate = _fixture(tmp_path)
    digest = tree_digest(candidate)

    first = build_candidate_review_context(
        target_state=state,
        candidate_root=candidate,
        candidate_digest=digest,
        construction_mode="regenerate",
    )
    second = build_candidate_review_context(
        target_state=state,
        candidate_root=candidate,
        candidate_digest=digest,
        construction_mode="regenerate",
    )

    assert first == second
    baseline, changes = first
    assert baseline["resolved_tree"] == state["resolved_tree"]
    assert [(row["path"], row["kind"]) for row in changes["changes"]] == [
        ("README.md", "modified"),
        ("added.txt", "added"),
        ("deleted.txt", "deleted"),
        ("script.sh", "mode-changed"),
    ]
    verify_candidate_review_context(baseline, changes)


def test_candidate_review_context_refuses_baseline_and_candidate_substitution(
    tmp_path: Path,
) -> None:
    state, candidate = _fixture(tmp_path)
    baseline, changes = build_candidate_review_context(
        target_state=state,
        candidate_root=candidate,
        candidate_digest=tree_digest(candidate),
        construction_mode="regenerate",
    )
    tampered_baseline = copy.deepcopy(baseline)
    tampered_baseline["files"][0]["content_base64"] = "eA=="
    with pytest.raises(CandidateDiffError, match="digest|blob|content"):
        verify_candidate_review_context(tampered_baseline, changes)

    tampered_changes = copy.deepcopy(changes)
    tampered_changes["candidate_digest"] = "sha256:" + ("0" * 64)
    with pytest.raises(CandidateDiffError, match="digest"):
        verify_candidate_review_context(baseline, tampered_changes)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("entry_type", "symlink", "entry type"),
        ("mode", 0o755, "mode"),
        ("content_utf8", "different\n", "UTF-8 display"),
    ],
)
def test_candidate_review_context_rejects_rehashed_redundant_baseline_fields(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    state, candidate = _fixture(tmp_path)
    baseline, changes = build_candidate_review_context(
        target_state=state,
        candidate_root=candidate,
        candidate_digest=tree_digest(candidate),
        construction_mode="regenerate",
    )
    tampered_baseline = copy.deepcopy(baseline)
    tampered_changes = copy.deepcopy(changes)
    unchanged = next(
        row for row in tampered_baseline["files"] if row["path"] == "unchanged.txt"
    )
    unchanged[field] = value
    _rehash_baseline_and_change_set(tampered_baseline, tampered_changes)

    with pytest.raises(CandidateDiffError, match=message):
        verify_candidate_review_context(tampered_baseline, tampered_changes)


def test_candidate_review_context_rejects_a_rehashed_inconsistent_change_kind(
    tmp_path: Path,
) -> None:
    state, candidate = _fixture(tmp_path)
    baseline, changes = build_candidate_review_context(
        target_state=state,
        candidate_root=candidate,
        candidate_digest=tree_digest(candidate),
        construction_mode="regenerate",
    )
    tampered_changes = copy.deepcopy(changes)
    changed_readme = next(
        row for row in tampered_changes["changes"] if row["path"] == "README.md"
    )
    changed_readme["kind"] = "mode-changed"
    _rehash_change_set(tampered_changes)

    with pytest.raises(CandidateDiffError, match="kind must re-derive as modified"):
        verify_candidate_review_context(baseline, tampered_changes)


def test_brownfield_review_refuses_without_a_signed_partial_output_contract(
    tmp_path: Path,
) -> None:
    state, candidate = _fixture(tmp_path)

    with pytest.raises(CandidateDiffError, match="brownfield review is INCOMPLETE"):
        build_candidate_review_context(
            target_state=state,
            candidate_root=candidate,
            candidate_digest=tree_digest(candidate),
            construction_mode="brownfield",
        )


def _reference_context(
    state: dict[str, object], candidate: Path
) -> tuple[dict[str, object], dict[str, object]]:
    return build_candidate_review_context(
        target_state=state,
        candidate_root=candidate,
        candidate_digest=tree_digest(candidate),
        construction_mode="regenerate",
        baseline_storage="git-reference",
    )


def test_git_reference_baseline_embeds_only_the_files_the_change_replaces(
    tmp_path: Path,
) -> None:
    state, candidate = _fixture(tmp_path)
    embedded_baseline, embedded_changes = build_candidate_review_context(
        target_state=state,
        candidate_root=candidate,
        candidate_digest=tree_digest(candidate),
        construction_mode="regenerate",
    )

    baseline, changes = _reference_context(state, candidate)

    assert baseline["schema_version"] == "factory-base-source-snapshot/2"
    assert "files" not in baseline
    assert baseline["entry_count"] == len(embedded_baseline["files"])
    assert [entry["path"] for entry in baseline["changed_files"]] == [
        "README.md",
        "deleted.txt",
        "script.sh",
    ]
    assert embedded_baseline_files(baseline) == baseline["changed_files"]
    assert changes["changes"] == embedded_changes["changes"]
    assert changes["baseline_snapshot_digest"] == baseline["snapshot_digest"]
    verify_candidate_review_context(baseline, changes, object_store=state["object_store"])


def test_git_reference_baseline_refuses_verification_without_its_object_store(
    tmp_path: Path,
) -> None:
    state, candidate = _fixture(tmp_path)
    baseline, changes = _reference_context(state, candidate)

    with pytest.raises(CandidateDiffError, match="without its run-owned object store"):
        verify_candidate_review_context(baseline, changes)


def test_git_reference_baseline_refuses_a_store_that_lacks_its_commit(
    tmp_path: Path,
) -> None:
    state, candidate = _fixture(tmp_path)
    baseline, changes = _reference_context(state, candidate)
    stranger = tmp_path / "stranger.git"
    _git("init", "-q", "--bare", str(stranger))

    with pytest.raises(CandidateDiffError, match="projection failed"):
        verify_candidate_review_context(baseline, changes, object_store=stranger)


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        (lambda base: base.update(listing_digest="sha256:" + "0" * 64), "listing digest"),
        (lambda base: base.update(entry_count=1), "entry count"),
        (lambda base: base["changed_files"].pop(), "exactly the paths"),
        (
            lambda base: base["changed_files"][0].update(
                content_base64="eA==", content_utf8="x", content_digest=digest_bytes(b"x")
            ),
            "blob changed",
        ),
    ],
)
def test_git_reference_baseline_rejects_rehashed_substitution(
    tmp_path: Path,
    tamper: Callable[[dict[str, Any]], object],
    message: str,
) -> None:
    state, candidate = _fixture(tmp_path)
    baseline, changes = _reference_context(state, candidate)
    tampered_baseline = copy.deepcopy(baseline)
    tampered_changes = copy.deepcopy(changes)
    tamper(tampered_baseline)
    _rehash_baseline_and_change_set(tampered_baseline, tampered_changes)

    with pytest.raises(CandidateDiffError, match=message):
        verify_candidate_review_context(
            tampered_baseline, tampered_changes, object_store=state["object_store"]
        )


def _large_fixture(tmp_path: Path, *, modify_large_file: bool) -> tuple[dict[str, object], Path]:
    """A baseline past every embedded ceiling: 4,200 files and one 600 KiB file."""

    source = tmp_path / "large-source"
    (source / "many").mkdir(parents=True)
    _git("init", "-q", cwd=source)
    for index in range(4_200):
        (source / "many" / f"{index:04d}.txt").write_text(f"{index}\n", encoding="utf-8")
    large = b"0123456789abcdef" * (600 * 1024 // 16)
    (source / "large.bin").write_bytes(large)
    (source / "README.md").write_text("old\n", encoding="utf-8")
    _git("add", ".", cwd=source)
    _git(
        "-c",
        "user.name=Factory Test",
        "-c",
        "user.email=factory@example.test",
        "commit",
        "-qm",
        "baseline",
        cwd=source,
    )
    object_store = tmp_path / "large-objects.git"
    _git("clone", "-q", "--bare", "--no-local", str(source), str(object_store))
    candidate = tmp_path / "large-candidate"
    shutil.copytree(source, candidate, ignore=shutil.ignore_patterns(".git"))
    (candidate / "README.md").write_text("new\n", encoding="utf-8")
    if modify_large_file:
        (candidate / "large.bin").write_bytes(large[::-1])
    state: dict[str, object] = {
        "object_store": str(object_store),
        "resolved_commit": _git("rev-parse", "HEAD", cwd=source),
        "resolved_tree": _git("rev-parse", "HEAD^{tree}", cwd=source),
        "subpath": "",
    }
    return state, candidate


def test_git_reference_baseline_reviews_a_repository_past_the_embedded_ceilings(
    tmp_path: Path,
) -> None:
    state, candidate = _large_fixture(tmp_path, modify_large_file=False)
    with pytest.raises(CandidateDiffError, match="ceiling"):
        build_candidate_review_context(
            target_state=state,
            candidate_root=candidate,
            candidate_digest=tree_digest(candidate),
            construction_mode="regenerate",
        )

    baseline, changes = _reference_context(state, candidate)

    assert baseline["entry_count"] == 4_202
    assert [entry["path"] for entry in baseline["changed_files"]] == ["README.md"]
    assert [(row["path"], row["kind"]) for row in changes["changes"]] == [
        ("README.md", "modified")
    ]
    verify_candidate_review_context(baseline, changes, object_store=state["object_store"])


def test_git_reference_baseline_still_bounds_the_bytes_it_embeds(tmp_path: Path) -> None:
    state, candidate = _large_fixture(tmp_path, modify_large_file=True)

    with pytest.raises(CandidateDiffError, match="changed Git baseline file exceeds"):
        _reference_context(state, candidate)


def test_unknown_review_baseline_storage_refuses(tmp_path: Path) -> None:
    state, candidate = _fixture(tmp_path)

    with pytest.raises(CandidateDiffError, match="unknown review baseline storage"):
        build_candidate_review_context(
            target_state=state,
            candidate_root=candidate,
            candidate_digest=tree_digest(candidate),
            construction_mode="regenerate",
            baseline_storage="lazy",
        )
