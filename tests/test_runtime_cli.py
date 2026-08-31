from __future__ import annotations

import json
import os
import stat
from argparse import Namespace
from pathlib import Path

import pytest
from pytest import CaptureFixture

import factory_runtime.cli as runtime_cli
from factory_core.manifest import digest_bytes, digest_obj
from factory_core.target import load_target_manifest
from factory_runtime.cli import (
    _require_semantic_json_digest,
    _retain_state_admission_refusal,
    main,
)
from factory_runtime.resources import ResourceLedger, ResourceLedgerError
from factory_runtime.runner import RunnerError
from factory_runtime.schema import DocumentValidationError, validate_document
from factory_runtime.state import RunStore
from factory_runtime.state_admission import StateAdmissionError
from tests.conftest import create_intake_run

DIGEST = "sha256:" + ("a" * 64)


def _create_run(root: Path, run_id: str = "run-1") -> None:
    create_intake_run(
        RunStore(root),
        run_id=run_id,
        target_digest=digest_obj({"target": run_id}),
        source_digest=digest_obj({"source": run_id}),
    )


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


def test_replay_cli_store_requires_both_external_authority_anchors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unanchored = Namespace(
        runs=str(tmp_path),
        genesis="",
        root_public_key="",
        tessera_bin="tessera",
    )
    store = runtime_cli._load_replay_store(unanchored)
    assert isinstance(store, RunStore)
    assert store._preview_evidence_verifier is None

    with pytest.raises(ValueError, match="--root-public-key is required"):
        runtime_cli._load_replay_store(
            Namespace(
                runs=str(tmp_path),
                genesis=str(tmp_path / "genesis.tessera.json"),
                root_public_key="",
                tessera_bin="tessera",
            )
        )

    authenticated = object()
    monkeypatch.setattr(
        runtime_cli,
        "_load_workflow",
        lambda _arguments: Namespace(store=authenticated),
    )
    assert (
        runtime_cli._load_replay_store(
            Namespace(
                runs=str(tmp_path),
                genesis=str(tmp_path / "genesis.tessera.json"),
                root_public_key="f" * 64,
                tessera_bin="tessera",
            )
        )
        is authenticated
    )


def test_replay_and_resource_commands_use_the_explicitly_anchored_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    run_id = "run-1"
    retained_target = {"target": "retained"}
    target_digest = digest_obj(retained_target)
    source_text = "Build the exact authorized behavior."
    source_digest = digest_bytes(source_text.encode("utf-8"))
    execution_request = {
        "run_id": run_id,
        "generation": 1,
        "target_manifest_digest": DIGEST,
        "target_state_digest": target_digest,
        "resolved_commit": "1" * 40,
        "verbatim_request": source_text,
        "verbatim_request_digest": source_digest,
    }
    request_digest = digest_obj(execution_request)
    run_root = tmp_path / run_id / "evidence"
    target_root = run_root / "target-resolution"
    intake_root = run_root / "intake"
    target_root.mkdir(parents=True)
    intake_root.mkdir(parents=True)
    (target_root / "target-state.json").write_text(
        json.dumps(retained_target),
        encoding="utf-8",
    )
    (intake_root / "execution-request.json").write_text(
        json.dumps(execution_request),
        encoding="utf-8",
    )

    projection = Namespace(
        target_state_digest=target_digest,
        target_state={"resolved_commit": "1" * 40},
        target_digest=DIGEST,
        source_digest=source_digest,
        generation=1,
    )

    class _ReplayStore:
        def load(self, actual_run_id: str) -> Namespace:
            assert actual_run_id == run_id
            return projection

        def execution_authority_digests(self, actual_run_id: str) -> dict[str, str]:
            assert actual_run_id == run_id
            return {
                "execution-request": request_digest,
                "execution-receipt": "sha256:" + ("2" * 64),
                "authority-genesis": "sha256:" + ("3" * 64),
            }

    observed: list[Namespace] = []
    resource_appends: list[dict[str, object]] = []

    def load_replay_store(arguments: Namespace) -> _ReplayStore:
        observed.append(arguments)
        return _ReplayStore()

    class _ResourceLedger:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def latest(self) -> dict[str, dict[str, object]]:
            return {
                "proof-resource": {
                    "generation": 1,
                    "resource_type": "proof",
                    "identifier": "/tmp/proof-resource",
                    "creator_action": "test",
                    "ownership": "run-owned",
                    "baseline": {"absent_at_plan": True},
                }
            }

        def append(self, **kwargs: object) -> str:
            resource_appends.append(kwargs)
            return DIGEST

    monkeypatch.setattr(runtime_cli, "_load_replay_store", load_replay_store)
    monkeypatch.setattr(runtime_cli, "verify_target_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime_cli, "validate_document", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime_cli, "ResourceLedger", _ResourceLedger)

    common = [
        "--runs",
        str(tmp_path),
        "--run-id",
        run_id,
        "--genesis",
        str(tmp_path / "genesis.tessera.json"),
        "--root-public-key",
        "f" * 64,
        "--tessera-bin",
        "/opt/tessera",
    ]
    assert main(["verify-target-state", *common]) == 0
    capsys.readouterr()
    assert main(["verify-execution-request", *common]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "record-resource",
                *common,
                "--resource-id",
                "proof-resource",
                "--resource-type",
                "proof",
                "--identifier",
                "/tmp/proof-resource",
                "--creator-action",
                "test",
                "--ownership",
                "run-owned",
                "--status",
                "planned",
                "--actor",
                "test",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "disposition-resource",
                *common,
                "--resource-id",
                "proof-resource",
                "--status",
                "retained",
                "--reason",
                "retain the exact proof",
                "--residue",
                "true",
                "--actor",
                "test",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert [arguments.command for arguments in observed] == [
        "verify-target-state",
        "verify-execution-request",
        "record-resource",
        "disposition-resource",
    ]
    for arguments in observed:
        assert arguments.genesis == str(tmp_path / "genesis.tessera.json")
        assert arguments.root_public_key == "f" * 64
        assert arguments.tessera_bin == "/opt/tessera"
    assert [entry["status"] for entry in resource_appends] == ["planned", "retained"]


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
    assert inspected["source_digest"] == target.source_digest
    assert inspected["source_digest"].startswith("sha256:")
    assert inspected["build"] == dict(target.build)


def test_pre_model_state_refusal_is_retained_without_paths_or_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_run(tmp_path)
    synced_modes: list[int] = []
    real_fsync = os.fsync

    def observed_fsync(descriptor: int) -> None:
        synced_modes.append(os.fstat(descriptor).st_mode)
        real_fsync(descriptor)

    monkeypatch.setattr(runtime_cli.os, "fsync", observed_fsync)
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
    assert document["generation"] == 1
    assert document["run_ledger_head"] == RunStore(tmp_path).load("run-1").ledger_head
    assert document["model_attempts"] == 0
    assert document["broker_effects"] == 0
    assert str(tmp_path) not in path.read_text()
    assert any(stat.S_ISREG(mode) for mode in synced_modes)
    assert any(stat.S_ISDIR(mode) for mode in synced_modes)

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


def test_historical_state_admission_refusal_v1_schema_remains_addressable() -> None:
    document = {
        "schema_version": "factory-state-admission-refusal/1",
        "receipt_id": "runner-legacy",
        "run_id": "run-legacy",
        "role": "coder",
        "purpose": "lane-dispatch",
        "refusal_code": "LEGACY_RUNNER_MANIFEST",
        "dependency_id": "runner-manifest",
        "state_profile_digest": digest_obj({"profile": "legacy"}),
        "model_attempts": 0,
        "broker_effects": 0,
        "created_at": 1,
    }

    validate_document("state-admission-refusal-v1", document)
    with pytest.raises(DocumentValidationError):
        validate_document("state-admission-refusal", document)


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
    _create_run(tmp_path)
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
    _create_run(tmp_path)

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
    _create_run(tmp_path)

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


def test_execute_retains_run_bound_refusal_before_releasing_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_run(tmp_path)

    def refuse(_arguments: Namespace) -> None:
        guard = tmp_path / "run-1" / "run-transition.guard"
        assert guard.is_file()
        raise StateAdmissionError("MISSING_DEPENDENCY", "missing", dependency_id="role-primer")

    monkeypatch.setattr(runtime_cli, "_execute_unleased", refuse)
    arguments = Namespace(
        command="run-model",
        runs=str(tmp_path),
        run_id="run-1",
        receipt_id="runner-1",
        role="coder",
    )

    with pytest.raises(StateAdmissionError) as error:
        runtime_cli._execute(arguments)

    assert error.value.receipt_retained is True
    receipt = json.loads(
        (
            tmp_path
            / "run-1"
            / "evidence"
            / "state-admission"
            / "refusals"
            / "runner-1.json"
        ).read_text()
    )
    projection = RunStore(tmp_path).load("run-1")
    assert receipt["schema_version"] == "factory-state-admission-refusal/2"
    assert receipt["generation"] == projection.generation
    assert receipt["run_ledger_head"] == projection.ledger_head
    assert not (tmp_path / "run-1" / "run-transition.guard").exists()


def test_refusal_retention_failure_does_not_replace_primary_admission_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_run(tmp_path)

    def refuse(_arguments: Namespace) -> None:
        raise StateAdmissionError("POISONED_STATE", "primary admission refusal")

    real_write = runtime_cli._write_json_once

    def uncertain_write(path: str | Path, document: dict[str, object]) -> None:
        real_write(path, document)
        raise OSError("directory fsync failed")

    monkeypatch.setattr(runtime_cli, "_execute_unleased", refuse)
    monkeypatch.setattr(runtime_cli, "_write_json_once", uncertain_write)
    arguments = Namespace(
        command="run-model",
        runs=str(tmp_path),
        run_id="run-1",
        receipt_id="runner-uncertain",
        role="coder",
    )

    with pytest.raises(StateAdmissionError) as error:
        runtime_cli._execute(arguments)

    assert error.value.code == "POISONED_STATE"
    assert error.value.receipt_attempted is True
    assert error.value.receipt_retained is False
    assert error.value.receipt_retention_error == "directory fsync failed"


def test_post_attempt_runner_failure_is_not_laundered_as_zero_attempt_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_run(tmp_path)

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
    _create_run(tmp_path)

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


def test_long_action_refuses_unknown_run_without_creating_a_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def should_not_run(_arguments: Namespace) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(runtime_cli, "_execute_unleased", should_not_run)
    arguments = Namespace(command="run-model", runs=str(tmp_path), run_id="future-run")

    with pytest.raises(ValueError, match="existing run"):
        runtime_cli._execute(arguments)

    assert called is False
    assert not (tmp_path / "future-run").exists()


def test_forbidden_runner_roots_derivation_covers_the_control_root(tmp_path) -> None:
    """Round-7 mutation B closed: the WPX red_now's CLI arm. The field tuple is
    pinned (a one-token deletion reds HERE, not silently in every dispatch), the
    derivation puts every root — control_root first — in the forbidden set, and
    the fail-closed branch refuses a target-state missing any field."""
    from factory_runtime.cli import _FORBIDDEN_ROOT_FIELDS, _derive_forbidden_runner_roots

    assert _FORBIDDEN_ROOT_FIELDS == (
        "control_root",
        "source_root",
        "workdir",
        "object_store",
    )

    roots = {}
    for name in _FORBIDDEN_ROOT_FIELDS:
        directory = tmp_path / name
        directory.mkdir()
        roots[name] = str(directory)
    derived = _derive_forbidden_runner_roots(roots)
    assert (tmp_path / "control_root").resolve() in derived
    assert len(derived) == 4

    incomplete = dict(roots)
    del incomplete["control_root"]
    with pytest.raises(ValueError, match="no forbidden runner root control_root"):
        _derive_forbidden_runner_roots(incomplete)
    with pytest.raises(ValueError, match="control_root"):
        _derive_forbidden_runner_roots({**roots, "control_root": ""})
