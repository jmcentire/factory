"""Target preparation proves local construction without authorizing target contact."""

from __future__ import annotations

import json
import math
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from factory_core.build_plan import PatternCatalog
from factory_core.target import load_target_manifest
from factory_runtime.cli import main

TARGET_URL = "https://example.invalid/acme/widget.git"
OUTPUT_FILES = (
    "target-manifest.toml",
    "pattern-catalog.json",
    "construction-artifact.json",
    "qualification-report.json",
)


def _arguments(output: Path, *extra: str) -> list[str]:
    return [
        "prepare-target",
        "--target-id",
        "widget",
        "--repository-url",
        TARGET_URL,
        "--ref",
        "main",
        "--output-dir",
        str(output),
        "--compliance-rules-path",
        "compliance/rules.json",
        *extra,
    ]


def _invoke(arguments: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    try:
        code = main(arguments)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _prepared(output: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> dict[str, Any]:
    code, stdout, stderr = _invoke(_arguments(output, *extra), capsys)
    assert code == 0, stderr or stdout
    result = json.loads(stdout)
    assert result["status"] == "prepared"
    assert Path(result["manifest_path"]) == output / "target-manifest.toml"
    assert Path(result["catalog_path"]) == output / "pattern-catalog.json"
    assert Path(result["qualification_report_path"]) == output / "qualification-report.json"
    assert all((output / name).is_file() for name in OUTPUT_FILES)
    return result


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def test_prepare_target_creates_loadable_target_bound_to_real_catalog(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "prepared"
    result = _prepared(output, capsys)
    target = load_target_manifest(output / "target-manifest.toml")
    catalog = PatternCatalog.from_dict(json.loads((output / "pattern-catalog.json").read_text()))
    report = json.loads((output / "qualification-report.json").read_text())
    artifact = json.loads((output / "construction-artifact.json").read_text())

    assert target.schema_version == "factory-target-manifest/2"
    assert target.target_id == "widget"
    assert target.repo["url"] == TARGET_URL
    assert target.repo["ref"] == "main"
    assert target.compliance["rules_path"] == "compliance/rules.json"
    assert target.build["construction_modes"] == ["regenerate"]
    assert target.build["review_baseline"] == "git-reference"
    assert result["catalog_digest"] == catalog.content_digest
    assert target.build["pattern_catalog_digest"] == catalog.content_digest
    assert catalog.patterns
    assert all(pattern.pattern_id and pattern.version for pattern in catalog.patterns)
    assert all(pattern.mechanism for pattern in catalog.patterns)
    assert all(
        pattern.qualification_evidence_digest == result["qualification_report_digest"]
        for pattern in catalog.patterns
    )
    assert artifact and report
    signal = target.build["signal"]
    assert 1 <= signal["signal_pass_warn"] <= signal["signal_pass_deadline"]
    assert signal["signal_pass_deadline"] <= target.build["max_attempts"]
    assert 0 < signal["signal_wall_clock_cap_hours"] <= 8760
    assert math.isfinite(signal["signal_wall_clock_cap_hours"])


def test_preparation_keeps_supplied_ref_subpath_and_attempt_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "prepared"
    arguments = _arguments(output, "--subpath", "services/widget", "--max-attempts", "1")
    arguments[arguments.index("--ref") + 1] = "release/v2"
    code, stdout, stderr = _invoke(arguments, capsys)
    assert code == 0, stderr or stdout
    target = load_target_manifest(output / "target-manifest.toml")
    assert target.repo["ref"] == "release/v2"
    assert target.repo["subpath"] == "services/widget"
    assert target.build["max_attempts"] == 1
    assert target.build["signal"]["signal_pass_deadline"] == 1


def test_preparation_qualifies_locally_without_target_contact_or_run_authority(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    original_popen = subprocess.Popen
    git_calls: list[list[str]] = []

    def guarded_popen(*args: Any, **kwargs: Any) -> Any:
        command = args[0] if args else kwargs.get("args")
        if isinstance(command, (tuple, list)):
            words = [os.fsdecode(word) for word in command]
            assert TARGET_URL not in words, "preparation contacted the supplied target"
            if words and Path(words[0]).name == "git":
                assert not {"clone", "fetch", "ls-remote", "push"}.intersection(words)
                git_calls.append(words)
        elif isinstance(command, str):
            assert TARGET_URL not in command, "preparation contacted the supplied target"
        return original_popen(*args, **kwargs)

    def no_network(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("preparation attempted network contact")

    monkeypatch.setattr(subprocess, "Popen", guarded_popen)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    output = tmp_path / "prepared"
    _prepared(output, capsys)
    assert any("init" in words for words in git_calls), "no real local Git fixture executed"
    assert any("commit" in words for words in git_calls), "no committed local baseline executed"
    assert any("ls-tree" in words for words in git_calls), "baseline tree was never inspected"
    assert not (tmp_path / ".factory").exists()
    assert not any(path.name in {"runs", "receipts", "keys"} for path in output.rglob("*"))


def test_exact_repeat_validates_and_reuses_retained_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "prepared"
    first = _prepared(output, capsys)
    retained = _snapshot(output)
    second = _prepared(output, capsys)
    assert second["catalog_digest"] == first["catalog_digest"]
    assert second["qualification_report_digest"] == first["qualification_report_digest"]
    assert _snapshot(output) == retained


@pytest.mark.parametrize("filename", OUTPUT_FILES)
def test_modified_preparation_output_is_refused_without_overwriting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], filename: str
) -> None:
    output = tmp_path / "prepared"
    _prepared(output, capsys)
    changed = output / filename
    changed.write_bytes(changed.read_bytes() + b"\nTAMPERED\n")
    retained = _snapshot(output)
    code, _, _ = _invoke(_arguments(output), capsys)
    assert code != 0
    assert _snapshot(output) == retained


def test_foreign_output_is_preserved_and_not_adopted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "prepared"
    output.mkdir()
    (output / "foreign.txt").write_text("keep this\n")
    before = _snapshot(output)
    code, _, _ = _invoke(_arguments(output), capsys)
    assert code != 0
    assert _snapshot(output) == before


@pytest.mark.parametrize("link_parent", [False, True])
def test_symlink_output_and_parent_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], link_parent: bool
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_text("untouched")
    link = tmp_path / "linked"
    link.symlink_to(outside, target_is_directory=True)
    output = link / "prepared" if link_parent else link
    before = _snapshot(outside)
    code, _, _ = _invoke(_arguments(output), capsys)
    assert code != 0
    assert _snapshot(outside) == before


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@example.invalid/acme/widget.git",
        "https://secret@example.invalid/acme/widget.git",
        "https://example.invalid/acme/widget.git?access_token=secret",
        "file:///tmp/target",
        "ext::sh -c malicious",
        "http://example.invalid/acme/widget.git",
    ],
)
def test_unsafe_or_credential_bearing_repository_url_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], url: str
) -> None:
    output = tmp_path / "prepared"
    arguments = _arguments(output)
    arguments[arguments.index("--repository-url") + 1] = url
    code, _, _ = _invoke(arguments, capsys)
    assert code != 0
    assert not (output / "target-manifest.toml").exists()
    assert not (output / "pattern-catalog.json").exists()


@pytest.mark.parametrize("path", ["../escape", "/absolute/path", "safe/../../escape"])
@pytest.mark.parametrize("option", ["--subpath", "--compliance-rules-path"])
def test_traversal_or_absolute_target_paths_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], option: str, path: str
) -> None:
    output = tmp_path / "prepared"
    code, _, _ = _invoke(_arguments(output, option, path), capsys)
    assert code != 0
    assert not (output / "target-manifest.toml").exists()
    assert not (output / "pattern-catalog.json").exists()


def test_failed_local_qualification_publishes_no_prepared_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    original_run = subprocess.run

    injected_failures: list[list[str]] = []

    def fail_local_git(command: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(command, (tuple, list)):
            words = [os.fsdecode(word) for word in command]
            if words and Path(words[0]).name == "git" and "init" in words:
                injected_failures.append(words)
                result = subprocess.CompletedProcess(command, 1, b"", b"injected Git failure")
                if kwargs.get("check"):
                    raise subprocess.CalledProcessError(1, command, result.stdout, result.stderr)
                return result
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fail_local_git)
    output = tmp_path / "prepared"
    code, _, _ = _invoke(_arguments(output), capsys)
    assert injected_failures, "local construction qualification never reached Git"
    assert code != 0
    assert not (output / "target-manifest.toml").exists()
    assert not (output / "pattern-catalog.json").exists()


def test_missing_required_compliance_rules_path_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "prepared"
    arguments = _arguments(output)
    position = arguments.index("--compliance-rules-path")
    del arguments[position : position + 2]
    code, _, _ = _invoke(arguments, capsys)
    assert code != 0
    assert not output.exists()


@pytest.mark.parametrize("filename", OUTPUT_FILES)
def test_missing_preparation_evidence_is_not_silently_replaced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], filename: str
) -> None:
    output = tmp_path / "prepared"
    _prepared(output, capsys)
    (output / filename).unlink()
    retained = _snapshot(output)
    code, _, _ = _invoke(_arguments(output), capsys)
    assert code != 0
    assert _snapshot(output) == retained


def test_existing_output_cannot_be_rebound_to_another_target_ref(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "prepared"
    _prepared(output, capsys)
    retained = _snapshot(output)
    arguments = _arguments(output)
    arguments[arguments.index("--ref") + 1] = "another-branch"
    code, _, _ = _invoke(arguments, capsys)
    assert code != 0
    assert _snapshot(output) == retained


def test_preparation_rejects_qualification_after_construction_source_changes(
    tmp_path: Path,
) -> None:
    import shutil

    repository = Path(__file__).resolve().parents[1]
    isolated = tmp_path / "isolated-factory"
    isolated.mkdir()
    for package in ("factory_core", "factory_runtime"):
        shutil.copytree(
            repository / package,
            isolated / package,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    output = tmp_path / "prepared"
    command = [sys.executable, "-m", "factory_runtime.cli", *_arguments(output)]
    environment = {**os.environ, "PYTHONPATH": str(isolated), "PYTHONDONTWRITEBYTECODE": "1"}
    first = subprocess.run(command, cwd=isolated, env=environment, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr or first.stdout
    retained = _snapshot(output)

    construction_source = isolated / "factory_runtime" / "candidate_diff.py"
    construction_source.write_bytes(construction_source.read_bytes() + b"\n# source changed\n")
    second = subprocess.run(command, cwd=isolated, env=environment, capture_output=True, text=True)
    assert second.returncode != 0
    assert _snapshot(output) == retained


def test_builtin_verification_accepts_real_fresh_preparation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from factory_runtime.target_preparation import verify_builtin_catalog

    output = tmp_path / "prepared"
    _prepared(output, capsys)
    catalog = json.loads((output / "pattern-catalog.json").read_text())
    verify_builtin_catalog(catalog)


@pytest.mark.parametrize(
    "falsification",
    [
        "overall-failure",
        "probe-failure",
        "false-positive-changes",
        "negative-without-exception",
        "negative-with-false-reason",
        "extra-report-field",
        "extra-probe-field",
        "missing-probe",
        "duplicate-probe",
    ],
)
def test_builtin_verification_refuses_rehashed_false_qualification_observations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], falsification: str
) -> None:
    from factory_core.manifest import digest_obj
    from factory_runtime.target_preparation import verify_builtin_catalog

    output = tmp_path / "prepared"
    _prepared(output, capsys)
    catalog = json.loads((output / "pattern-catalog.json").read_text())
    real_report = json.loads((output / "qualification-report.json").read_text())
    pattern = next(
        item
        for item in catalog["patterns"]
        if item["mechanism"].get("qualification_evidence") == real_report
    )
    report = pattern["mechanism"]["qualification_evidence"]
    observations = report["observations"]
    assert observations and report["passed"] is True
    assert all(item["passed"] is True for item in observations)

    if falsification == "overall-failure":
        report["passed"] = False
    elif falsification == "probe-failure":
        observations[0]["passed"] = False
    elif falsification == "false-positive-changes":
        positive = next(item for item in observations if "observed_changes" in item)
        positive["observed_changes"] = [["never-produced.txt", "added"]]
    elif falsification == "negative-without-exception":
        negative = next(item for item in observations if "observed_exception" in item)
        negative["observed_exception"] = "NoException"
    elif falsification == "negative-with-false-reason":
        negative = next(item for item in observations if "observed_reason" in item)
        negative["observed_reason"] = "The prohibited operation succeeded."
    elif falsification == "extra-report-field":
        report["unexpected_attestation"] = "trust this unexecuted assertion"
    elif falsification == "extra-probe-field":
        observations[0]["unexpected_observation"] = "not emitted by the probe"
    elif falsification == "missing-probe":
        observations.pop()
    elif falsification == "duplicate-probe":
        observations.append(dict(observations[0]))
    else:
        raise AssertionError(f"Unhandled test case: {falsification}")

    assert report != real_report
    pattern["qualification_evidence_digest"] = digest_obj(report)
    assert pattern["qualification_evidence_digest"] != digest_obj(real_report)
    with pytest.raises(ValueError):
        verify_builtin_catalog(catalog)


def test_preparation_refuses_evidence_changed_during_publication(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import stat

    output = tmp_path / "prepared"
    original_fsync = os.fsync
    interference: list[tuple[Path, bytes]] = []

    def interfere_after_file_sync(descriptor: int) -> None:
        original_fsync(descriptor)
        if interference or (output / "target-manifest.toml").exists():
            return
        synchronized = os.fstat(descriptor)
        if not stat.S_ISREG(synchronized.st_mode):
            return
        for name in OUTPUT_FILES:
            if name == "target-manifest.toml":
                continue
            payload = output / name
            try:
                current = payload.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            if (current.st_dev, current.st_ino) == (synchronized.st_dev, synchronized.st_ino):
                changed_bytes = payload.read_bytes() + b"\nPUBLICATION_INTERFERENCE\n"
                payload.write_bytes(changed_bytes)
                interference.append((payload, changed_bytes))
                break

    monkeypatch.setattr(os, "fsync", interfere_after_file_sync)
    code, stdout, _ = _invoke(_arguments(output), capsys)
    assert interference, "publication never reached the injected evidence-file boundary"
    assert code != 0, "preparation reported success over evidence changed by another writer"
    if stdout.strip():
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError:
            pass
        else:
            assert result.get("status") != "prepared"
    payload, changed_bytes = interference[0]
    assert payload.read_bytes() == changed_bytes, "the other writer's bytes were overwritten"
