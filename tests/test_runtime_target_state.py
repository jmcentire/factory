from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from factory_core.target import TargetManifest, load_target_manifest
from factory_runtime.resources import ResourceLedger
from factory_runtime.target_state import (
    TargetResolutionError,
    TargetResolver,
    normalize_repository_url,
    normalize_subpath,
    verify_target_state,
)
from tests.conftest import SYNTHETIC_TARGET


def _git(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _source(tmp_path: Path, *, remote: str = "https://example.invalid/acme/widget.git") -> Path:
    source = tmp_path / "operator-source"
    _git("init", "-b", "main", str(source))
    _git("-C", str(source), "config", "user.email", "factory@example.test")
    _git("-C", str(source), "config", "user.name", "Factory Test")
    (source / "README.md").write_text("fixture\n", encoding="utf-8")
    _git("-C", str(source), "add", "README.md")
    _git("-C", str(source), "commit", "-m", "fixture")
    _git("-C", str(source), "remote", "add", "origin", remote)
    return source


def _request(manifest: TargetManifest, **overrides: Any) -> dict[str, Any]:
    request: dict[str, Any] = {
        "schema_version": "factory-target-resolution-request/1",
        "request_id": "resolution-1",
        "run_id": "run-1",
        "repository_id": "factory",
        "generation": 1,
        "target_manifest_digest": manifest.source_digest,
        "normalized_url": normalize_repository_url(str(manifest.repo["url"])),
        "requested_ref": str(manifest.repo["ref"]),
        "subpath": normalize_subpath(str(manifest.repo.get("subpath", ""))),
        "allowed_contact_operations": ["git-local-object-read"],
        "lane_execution": False,
        "nonce": "resolution-nonce-001",
        "created_at": 100,
        "expires_at": 200,
    }
    request.update(overrides)
    return request


def _resolver(
    tmp_path: Path,
    *,
    runner: Any = subprocess.run,
) -> TargetResolver:
    return TargetResolver(
        tmp_path / "runs" / "run-1",
        "run-1",
        repository_id="factory",
        generation=1,
        clock=lambda: 150,
        runner=runner,
    )


def test_local_object_source_resolves_exact_commit_without_operator_checkout_reads(
    tmp_path: Path,
) -> None:
    manifest = load_target_manifest(SYNTHETIC_TARGET)
    source = _source(tmp_path)
    (source / "operator-dirt.txt").write_text("unrelated\n", encoding="utf-8")
    commands: list[tuple[str, ...]] = []
    run_dir = tmp_path / "runs" / "run-1"

    def runner(command: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if not commands:
            contact = ResourceLedger(run_dir, "run-1").latest()["target-contact"]
            assert contact["status"] == "planned"
        commands.append(tuple(command))
        return subprocess.run(command, **kwargs)

    target_state = _resolver(tmp_path, runner=runner).resolve(
        manifest=manifest,
        request=_request(manifest),
        object_source=source,
    )

    expected = _git("-C", str(source), "rev-parse", "refs/heads/main^{commit}")
    assert target_state["resolved_commit"] == expected
    assert target_state["source_root"] != str(source)
    assert target_state["remote_freshness"] == "UNPROVED"
    verify_target_state(target_state)

    operator_commands = [command for command in commands if str(source) in " ".join(command)]
    rendered = "\n".join(" ".join(command) for command in operator_commands)
    assert " status " not in f" {rendered} "
    assert " HEAD" not in rendered
    assert "diff" not in rendered
    assert "checkout" not in rendered
    assert "clone" not in rendered


def test_request_mismatch_causes_zero_contact_and_zero_checkout_creation(tmp_path: Path) -> None:
    manifest = load_target_manifest(SYNTHETIC_TARGET)
    calls: list[Sequence[str]] = []

    def runner(command: Sequence[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        raise AssertionError("invalid Stage-R subject must not invoke Git")

    with pytest.raises(TargetResolutionError, match="request-url-mismatch"):
        _resolver(tmp_path, runner=runner).resolve(
            manifest=manifest,
            request=_request(manifest, normalized_url="https://example.invalid/other.git"),
            object_source=tmp_path,
        )
    assert calls == []
    assert not (tmp_path / "runs" / "run-1" / "resources.jsonl").exists()
    assert not (tmp_path / "runs" / "run-1" / "target").exists()


def test_missing_ref_fails_named_and_never_falls_back_to_head(tmp_path: Path) -> None:
    source = _source(tmp_path)
    manifest = load_target_manifest(SYNTHETIC_TARGET)
    manifest.repo["ref"] = "missing"
    commands: list[tuple[str, ...]] = []

    def runner(command: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(tuple(command))
        return subprocess.run(command, **kwargs)

    with pytest.raises(TargetResolutionError) as raised:
        _resolver(tmp_path, runner=runner).resolve(
            manifest=manifest,
            request=_request(manifest),
            object_source=source,
        )
    assert raised.value.code == "ref-not-found"
    assert all("HEAD" not in command for command in commands)
    assert not (tmp_path / "runs" / "run-1" / "target").exists()


def test_non_commit_tag_is_refused_instead_of_guessed(tmp_path: Path) -> None:
    source = _source(tmp_path)
    blob = _git("-C", str(source), "hash-object", "README.md")
    _git("-C", str(source), "tag", "blobtag", blob)
    manifest = load_target_manifest(SYNTHETIC_TARGET)
    manifest.repo["ref"] = "blobtag"

    with pytest.raises(TargetResolutionError) as raised:
        _resolver(tmp_path).resolve(
            manifest=manifest,
            request=_request(manifest),
            object_source=source,
        )
    assert raised.value.code == "ref-not-a-commit"


def test_ref_movement_across_the_copy_window_is_refused(tmp_path: Path) -> None:
    source = _source(tmp_path)
    manifest = load_target_manifest(SYNTHETIC_TARGET)
    moved = False

    def runner(command: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal moved
        result = subprocess.run(command, **kwargs)
        if not moved and "fetch" in command:
            moved = True
            (source / "moved.txt").write_text("new commit\n", encoding="utf-8")
            _git("-C", str(source), "add", "moved.txt")
            _git("-C", str(source), "commit", "-m", "move ref")
        return result

    with pytest.raises(TargetResolutionError) as raised:
        _resolver(tmp_path, runner=runner).resolve(
            manifest=manifest,
            request=_request(manifest),
            object_source=source,
        )
    assert moved is True
    assert raised.value.code == "ref-moved"


def test_existing_checkout_path_refuses_before_repository_contact(tmp_path: Path) -> None:
    manifest = load_target_manifest(SYNTHETIC_TARGET)
    target = tmp_path / "runs" / "run-1" / "target"
    target.mkdir(parents=True)
    calls: list[Sequence[str]] = []

    def runner(command: Sequence[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        raise AssertionError("checkout reuse must deny before Git contact")

    with pytest.raises(TargetResolutionError) as raised:
        _resolver(tmp_path, runner=runner).resolve(
            manifest=manifest,
            request=_request(manifest),
            object_source=tmp_path,
        )

    assert raised.value.code == "checkout-path-reuse"
    assert calls == []
    assert not (tmp_path / "runs" / "run-1" / "resources.jsonl").exists()


def test_symlinked_subpath_is_refused_after_exact_checkout(tmp_path: Path) -> None:
    source = _source(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (source / "linked").symlink_to(outside, target_is_directory=True)
    _git("-C", str(source), "add", "linked")
    _git("-C", str(source), "commit", "-m", "symlinked subpath")
    manifest = load_target_manifest(SYNTHETIC_TARGET)
    manifest.repo["subpath"] = "linked"

    with pytest.raises(TargetResolutionError) as raised:
        _resolver(tmp_path).resolve(
            manifest=manifest,
            request=_request(manifest),
            object_source=source,
        )

    assert raised.value.code == "subpath-symlink"


def test_target_state_verification_detects_untracked_baseline_divergence(tmp_path: Path) -> None:
    source = _source(tmp_path)
    manifest = load_target_manifest(SYNTHETIC_TARGET)
    target_state = _resolver(tmp_path).resolve(
        manifest=manifest,
        request=_request(manifest),
        object_source=source,
    )
    (Path(str(target_state["source_root"])) / "unexpected.txt").write_text(
        "diverged\n", encoding="utf-8"
    )

    with pytest.raises(TargetResolutionError) as raised:
        verify_target_state(target_state)
    assert raised.value.code == "target-state-diverged"


@pytest.mark.parametrize(
    "value",
    ("../escape", "/absolute", "nested/../escape", "nested\\escape"),
)
def test_subpath_escape_forms_are_refused(value: str) -> None:
    with pytest.raises(TargetResolutionError):
        normalize_subpath(value)


@pytest.mark.parametrize(
    "value",
    (
        "https://token@example.test/repository.git",
        "https://example.test/repository.git?token=secret",
        "ssh://root@example.test/repository.git",
        "http://example.test/repository.git",
    ),
)
def test_repository_url_credentials_and_ambiguous_forms_are_refused(value: str) -> None:
    with pytest.raises(TargetResolutionError):
        normalize_repository_url(value)
