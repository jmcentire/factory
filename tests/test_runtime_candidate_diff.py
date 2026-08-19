from __future__ import annotations

import copy
import subprocess
from pathlib import Path

import pytest

from factory_runtime.candidate_diff import (
    CandidateDiffError,
    build_candidate_review_context,
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
    state: dict[str, object] = {
        "object_store": str(object_store),
        "resolved_commit": commit,
        "resolved_tree": tree,
        "subpath": "",
    }
    return state, candidate


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
