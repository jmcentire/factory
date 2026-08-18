from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest
from pytest import CaptureFixture

import factory_runtime.cli as runtime_cli
from factory_core.manifest import digest_obj
from factory_core.target import load_target_manifest
from factory_runtime.cli import (
    _require_semantic_json_digest,
    _retain_state_admission_refusal,
    main,
)
from factory_runtime.resources import ResourceLedger, ResourceLedgerError
from factory_runtime.runner import RunnerError
from factory_runtime.state_admission import StateAdmissionError

DIGEST = "sha256:" + ("a" * 64)


def _artifact(path: Path) -> dict[str, object]:
    document: dict[str, object] = {
        "artifact_id": "product",
        "phase": "product-specification",
        "version": "1",
        "source_digest": DIGEST,
        "human_ratifier": "human:founder",
        "validator_ratifier": "agent:validator",
        "items": [
            {
                "item_id": "product:1",
                "canonical_statement": "The signed behavior is authoritative.",
                "supersedes": [],
            }
        ],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return document


def test_cli_validates_and_content_addresses_runtime_documents(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    path = tmp_path / "product.json"
    document = _artifact(path)

    assert (
        main(
            [
                "validate-document",
                "--schema",
                "phase-artifact",
                "--input",
                str(path),
            ]
        )
        == 0
    )
    validation = json.loads(capsys.readouterr().out)
    assert validation["valid"] is True
    assert validation["digest"] == digest_obj(document)

    assert main(["digest-json", "--input", str(path)]) == 0
    addressed = json.loads(capsys.readouterr().out)
    assert addressed == {"digest": digest_obj(document)}


def test_cli_refuses_a_document_outside_the_closed_schema(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    path = tmp_path / "product.json"
    document = _artifact(path)
    document["ticket_authority"] = "mutable-input"
    path.write_text(json.dumps(document), encoding="utf-8")

    assert (
        main(
            [
                "validate-document",
                "--schema",
                "phase-artifact",
                "--input",
                str(path),
            ]
        )
        == 2
    )
    assert "factory: refused:" in capsys.readouterr().err


def test_cli_inspects_the_target_operational_abi(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    source = Path(__file__).parent / "fixtures" / "synthetic_target" / "target.toml"
    manifest_path = tmp_path / "target.toml"
    manifest_path.write_bytes(source.read_bytes())

    assert main(["inspect-target", "--manifest", str(manifest_path)]) == 0
    inspected = json.loads(capsys.readouterr().out)
    target = load_target_manifest(manifest_path)

    assert inspected["target_id"] == target.target_id
    assert inspected["content_digest"] == target.content_digest
    assert inspected["source_digest"].startswith("sha256:")
    assert inspected["build"] == dict(target.build)


def test_pre_model_state_refusal_is_retained_without_paths_or_content(
    tmp_path: Path,
) -> None:
    arguments = Namespace(
        command="run-model",
        runs=str(tmp_path),
        run_id="run-1",
        receipt_id="runner-1",
        role="coder",
    )
    error = StateAdmissionError(
        "MISSING_DEPENDENCY",
        f"sensitive path {tmp_path}/primer was missing",
        dependency_id="role-primer",
    )

    _retain_state_admission_refusal(arguments, error)

    path = (
        tmp_path
        / "run-1"
        / "evidence"
        / "state-admission"
        / "refusals"
        / "runner-1.json"
    )
    document = json.loads(path.read_text())
    assert document["refusal_code"] == "MISSING_DEPENDENCY"
    assert document["dependency_id"] == "role-primer"
    assert document["model_attempts"] == 0
    assert document["broker_effects"] == 0
    assert str(tmp_path) not in path.read_text()

    with pytest.raises(ValueError, match="single-use"):
        _retain_state_admission_refusal(arguments, error)


def test_cli_materializes_structural_state_qualification_report(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    configuration_digest = digest_obj({"runner": "fixture"})
    observations_output = tmp_path / "observations.json"
    output = tmp_path / "qualification.json"

    assert (
        main(
            [
                "qualify-state",
                "--runner-configuration-digest",
                configuration_digest,
                "--qualification-id",
                "qualification-1",
                "--observations-output",
                str(observations_output),
                "--output",
                str(output),
            ]
        )
        == 0
    )

    rendered = json.loads(capsys.readouterr().out)
    retained = json.loads(output.read_text())
    observations = json.loads(observations_output.read_text())
    assert rendered["qualified"] is True
    assert retained == rendered
    assert retained["observations_digest"] == digest_obj(observations)


def test_checkpoint_recheck_uses_canonical_json_address_not_serialization() -> None:
    checkpoint = {"checkpoint_id": "checkpoint-1", "nested": {"value": 1}}
    expected = digest_obj(checkpoint)
    pretty_with_newline = (json.dumps(checkpoint, indent=2) + "\n").encode()

    assert _require_semantic_json_digest(
        pretty_with_newline,
        expected_digest=expected,
        label="resume checkpoint",
    ) == checkpoint


def test_checkpoint_recheck_refuses_semantic_mutation() -> None:
    checkpoint = {"checkpoint_id": "checkpoint-1", "nested": {"value": 1}}
    expected = digest_obj(checkpoint)
    checkpoint["nested"]["value"] = 2

    with pytest.raises(ValueError, match="changed after external verification"):
        _require_semantic_json_digest(
            json.dumps(checkpoint).encode(),
            expected_digest=expected,
            label="resume checkpoint",
        )


@pytest.mark.parametrize(
    "command",
    [
        "bundle-orchestrator-projection",
        "run-model",
        "execute-broker-handoff",
    ],
)
def test_state_assembly_model_and_broker_commands_hold_execution_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    run_dir = tmp_path / "run-1"
    observed = False

    def probe(_arguments: Namespace) -> None:
        nonlocal observed
        ledger = ResourceLedger(run_dir, "run-1")
        with pytest.raises(ResourceLedgerError, match="run transition guard already exists"):
            ledger.append(
                generation=1,
                resource_id="probe",
                resource_type="probe",
                identifier="/probe",
                creator_action="test",
                ownership="run-owned",
                baseline={"absent_at_plan": True},
                disposition={},
                status="planned",
                actor="test",
            )
        observed = True

    monkeypatch.setattr(runtime_cli, "_execute_unleased", probe)
    runtime_cli._execute(Namespace(command=command, runs=str(tmp_path), run_id="run-1"))

    assert observed is True
    assert not (run_dir / "run-transition.guard").exists()


def test_zero_attempt_runner_failure_is_typed_as_state_admission_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(_arguments: Namespace) -> None:
        raise RunnerError("runner executable is unavailable")

    monkeypatch.setattr(runtime_cli, "_execute_unleased", refuse)
    arguments = Namespace(command="run-model", runs=str(tmp_path), run_id="run-1")

    with pytest.raises(StateAdmissionError) as error:
        runtime_cli._execute(arguments)

    assert error.value.code == "PRE_MODEL_REFUSAL"
    assert not (tmp_path / "run-1" / "run-transition.guard").exists()


def test_specific_zero_attempt_runner_refusal_code_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(_arguments: Namespace) -> None:
        raise RunnerError(
            "legacy runner manifest cannot dispatch",
            refusal_code="LEGACY_RUNNER_MANIFEST",
        )

    monkeypatch.setattr(runtime_cli, "_execute_unleased", refuse)
    arguments = Namespace(command="run-model", runs=str(tmp_path), run_id="run-1")

    with pytest.raises(StateAdmissionError) as error:
        runtime_cli._execute(arguments)

    assert error.value.code == "LEGACY_RUNNER_MANIFEST"


def test_post_attempt_runner_failure_is_not_laundered_as_zero_attempt_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(_arguments: Namespace) -> None:
        raise RunnerError("model call failed", model_attempts=1)

    monkeypatch.setattr(runtime_cli, "_execute_unleased", refuse)
    arguments = Namespace(command="run-model", runs=str(tmp_path), run_id="run-1")

    with pytest.raises(RunnerError) as error:
        runtime_cli._execute(arguments)

    assert error.value.model_attempts == 1
    assert not (tmp_path / "run-1" / "run-transition.guard").exists()


def test_broker_runner_error_is_not_laundered_as_model_state_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(_arguments: Namespace) -> None:
        raise RunnerError("broker handoff failed")

    monkeypatch.setattr(runtime_cli, "_execute_unleased", refuse)
    arguments = Namespace(
        command="execute-broker-handoff",
        runs=str(tmp_path),
        run_id="run-1",
    )

    with pytest.raises(RunnerError, match="broker handoff failed"):
        runtime_cli._execute(arguments)
