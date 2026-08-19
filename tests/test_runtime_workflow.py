from __future__ import annotations

import json
import os
import stat
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from factory_core.manifest import digest_bytes, digest_obj
from factory_core.provenance import PhaseArtifact
from factory_core.target import load_target_manifest
from factory_runtime import workflow as workflow_module
from factory_runtime.authority import (
    AuthorityPolicy,
    AuthorityVerificationError,
    Principal,
)
from factory_runtime.state import RunState
from factory_runtime.tessera import TesseraVerificationError, VerifiedEnvelope
from factory_runtime.workflow import FactoryWorkflow, WorkflowError
from tests.conftest import SYNTHETIC_TARGET

ROOT_KEY = "a" * 64
VALIDATOR_KEY = "b" * 64
VERBATIM = "Build the executable three-role Factory runtime."
SOURCE = digest_bytes(VERBATIM.encode())


class _Tessera:
    def __init__(self) -> None:
        self.envelopes: dict[str, VerifiedEnvelope] = {}

    def add(
        self,
        path: Path,
        payload: dict[str, Any],
        *,
        key: str,
        kind: str,
    ) -> Path:
        path.write_text(json.dumps({"fixture": payload}), encoding="utf-8")
        self.envelopes[str(path)] = VerifiedEnvelope(
            kind=kind,
            payload=payload,
            payload_digest=digest_obj(payload),
            public_key=key,
            envelope_digest=digest_bytes(path.read_bytes()),
            path=path,
        )
        return path

    def verify_json(
        self,
        envelope_path: str | Path,
        *,
        trusted_public_keys: tuple[str, ...] = (),
        expected_kind: str | None = None,
        expected_payload_digest: str | None = None,
    ) -> VerifiedEnvelope:
        envelope = self.envelopes.get(str(envelope_path))
        if envelope is None:
            retained_path = Path(envelope_path)
            retained = json.loads(retained_path.read_text(encoding="utf-8"))
            payload = retained.get("fixture")
            envelope = next(
                (
                    replace(candidate, path=retained_path)
                    for candidate in self.envelopes.values()
                    if candidate.payload == payload
                    and candidate.envelope_digest == digest_bytes(retained_path.read_bytes())
                ),
                None,
            )
        if envelope is None:
            raise TesseraVerificationError("unknown fixture envelope")
        if trusted_public_keys and envelope.public_key not in trusted_public_keys:
            raise TesseraVerificationError("untrusted fixture signer")
        if expected_kind is not None and envelope.kind != expected_kind:
            raise TesseraVerificationError("wrong fixture kind")
        if (
            expected_payload_digest is not None
            and envelope.payload_digest != expected_payload_digest
        ):
            raise TesseraVerificationError("wrong fixture payload")
        return envelope


def _policy() -> AuthorityPolicy:
    principals = {
        "human:founder": Principal(
            identity="human:founder",
            kind="human",
            public_key=ROOT_KEY,
            capabilities=frozenset(
                {
                    "factory:authorize-target-resolution",
                    "factory:authorize-change",
                    "factory:ratify-product-specification",
                    "factory:ratify-architecture",
                    "factory:ratify-operational-maturity",
                    "factory:approve-promotion",
                }
            ),
        ),
        "agent:validator": Principal(
            identity="agent:validator",
            kind="agent",
            public_key=VALIDATOR_KEY,
            capabilities=frozenset(
                {
                    "factory:ratify-product-specification",
                    "factory:ratify-architecture",
                    "factory:ratify-operational-maturity",
                }
            ),
        ),
    }
    return AuthorityPolicy(
        repository_id="factory",
        policy_id="factory-authority/1",
        root_public_key=ROOT_KEY,
        principals=principals,
        bootstrap_enabled=True,
        bootstrap_scope=frozenset(
            {"authorize-target-resolution", "authorize-change", "activate-policy"}
        ),
        genesis_digest="sha256:" + ("d" * 64),
    )


def _write_json(path: Path, document: dict[str, Any]) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _object_source(tmp_path: Path) -> Path:
    source = tmp_path / "operator-source"
    subprocess.run(["git", "init", "-b", "main", str(source)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(source), "config", "user.email", "factory@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(source), "config", "user.name", "Factory Test"],
        check=True,
    )
    (source / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-m", "fixture"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "remote",
            "add",
            "origin",
            "https://example.invalid/acme/widget.git",
        ],
        check=True,
    )
    return source


def test_write_once_fsyncs_identical_evidence_through_durable_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    durable_root = tmp_path / "runs"
    evidence = durable_root / "run-1" / "evidence" / "repair-briefs" / "repair.tessera.json"
    evidence.parent.mkdir(parents=True)
    content = b'{"signed":"brief"}\n'
    evidence.write_bytes(content)
    real_fsync = os.fsync
    synced: list[tuple[str, int]] = []

    def track_fsync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        synced.append(("file" if stat.S_ISREG(metadata.st_mode) else "directory", metadata.st_ino))
        real_fsync(descriptor)

    monkeypatch.setattr(workflow_module.os, "fsync", track_fsync)

    workflow_module._write_once(evidence, content, durable_root=durable_root)

    assert evidence.read_bytes() == content
    assert ("file", evidence.stat().st_ino) in synced
    for directory in (
        evidence.parent,
        evidence.parent.parent,
        evidence.parent.parent.parent,
        durable_root,
    ):
        assert ("directory", directory.stat().st_ino) in synced


def _resolution_request(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    manifest = load_target_manifest(SYNTHETIC_TARGET)
    request = {
        "schema_version": "factory-target-resolution-request/1",
        "request_id": "resolution-request-1",
        "run_id": "run-1",
        "repository_id": "factory",
        "generation": 1,
        "target_manifest_digest": manifest.content_digest,
        "target_manifest_source_digest": manifest.source_digest,
        "normalized_url": "https://example.invalid/acme/widget.git",
        "requested_ref": "main",
        "subpath": "",
        "allowed_contact_operations": ["git-local-object-read"],
        "lane_execution": False,
        "nonce": "resolution-nonce-001",
        "created_at": 100,
        "expires_at": 200,
    }
    return _write_json(tmp_path / "resolution-request.json", request), request


def _execution_request(
    tmp_path: Path,
    workflow: FactoryWorkflow,
    *,
    verbatim_digest: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    projection = workflow.store.load("run-1")
    request = {
        "schema_version": "factory-execution-request/1",
        "request_id": "execution-request-1",
        "run_id": "run-1",
        "repository_id": "factory",
        "generation": projection.generation,
        "target_manifest_digest": projection.target_digest,
        "target_state_digest": projection.target_state_digest,
        "resolved_commit": projection.target_state["resolved_commit"],
        "proposed_by": "human:founder",
        "verbatim_request": VERBATIM,
        "verbatim_request_digest": verbatim_digest or SOURCE,
        "requested_outcome": "A running, evidence-producing orchestration path.",
        "surfaces": [
            {
                "surface_id": "factory-control-plane",
                "proposed_criticality": "critical",
                "reason": "It controls authorization and promotion.",
            }
        ],
        "created_at": 100,
    }
    return _write_json(tmp_path / "execution-request.json", request), request


def _receipt(
    *,
    receipt_id: str,
    action: str,
    subject_digest: str,
    signer: str,
    nonce: str,
    run_id: str = "run-1",
) -> dict[str, Any]:
    capability = {
        "authorize-target-resolution": "factory:authorize-target-resolution",
        "authorize-change": "factory:authorize-change",
        "ratify-product-specification": "factory:ratify-product-specification",
        "ratify-architecture": "factory:ratify-architecture",
        "ratify-operational-maturity": "factory:ratify-operational-maturity",
    }[action]
    return {
        "schema_version": "factory-authority-receipt/1",
        "receipt_id": receipt_id,
        "run_id": run_id,
        "repository_id": "factory",
        "action": action,
        "subject_digest": subject_digest,
        "signer_identity": signer,
        "capabilities": [capability],
        "issued_at": 100,
        "expires_at": 200,
        "nonce": nonce,
    }


def _phase(phase: str, sequence: int) -> dict[str, Any]:
    return {
        "artifact_id": f"{phase}-{sequence}",
        "phase": phase,
        "version": str(sequence),
        "source_digest": SOURCE,
        "human_ratifier": "human:founder",
        "validator_ratifier": "agent:validator",
        "items": [
            {
                "item_id": f"{phase}:1",
                "canonical_statement": f"{phase} is settled.",
                "supersedes": [],
            }
        ],
    }


def _resolve(
    tmp_path: Path,
    tessera: _Tessera,
) -> FactoryWorkflow:
    request_path, request = _resolution_request(tmp_path)
    receipt_path = tessera.add(
        tmp_path / "resolution.tessera.json",
        _receipt(
            receipt_id="resolution-1",
            action="authorize-target-resolution",
            subject_digest=digest_obj(request),
            signer="human:founder",
            nonce="resolution-nonce-001",
        ),
        key=ROOT_KEY,
        kind="factory-authority-receipt",
    )
    workflow = FactoryWorkflow(
        tmp_path / "runs",
        authority_policy=_policy(),
        tessera=tessera,  # type: ignore[arg-type]
        clock=lambda: 150,
    )
    projection = workflow.authorize_target_resolution(
        "run-1",
        manifest_path=SYNTHETIC_TARGET,
        request_path=request_path,
        receipt_path=receipt_path,
    )
    assert projection.state == RunState.TARGET_RESOLUTION_AUTHORIZED
    projection = workflow.resolve_target("run-1", object_source=_object_source(tmp_path))
    assert projection.state == RunState.TARGET_RESOLVED
    return workflow


def _authorize(
    tmp_path: Path,
    tessera: _Tessera,
) -> FactoryWorkflow:
    workflow = _resolve(tmp_path, tessera)
    request_path, request = _execution_request(tmp_path, workflow)
    receipt_path = tessera.add(
        tmp_path / "authorize.tessera.json",
        _receipt(
            receipt_id="authorize-1",
            action="authorize-change",
            subject_digest=digest_obj(request),
            signer="human:founder",
            nonce="authorize-nonce-001",
        ),
        key=ROOT_KEY,
        kind="factory-authority-receipt",
    )
    projection = workflow.authorize_change(
        "run-1",
        request_path=request_path,
        receipt_path=receipt_path,
    )
    assert projection.state == RunState.INTAKE
    return workflow


def test_stage_r_persists_authority_but_performs_no_contact_or_source_creation(
    tmp_path: Path,
) -> None:
    tessera = _Tessera()
    request_path, request = _resolution_request(tmp_path)
    receipt_path = tessera.add(
        tmp_path / "resolution.tessera.json",
        _receipt(
            receipt_id="resolution-1",
            action="authorize-target-resolution",
            subject_digest=digest_obj(request),
            signer="human:founder",
            nonce="resolution-nonce-001",
        ),
        key=ROOT_KEY,
        kind="factory-authority-receipt",
    )
    workflow = FactoryWorkflow(
        tmp_path / "runs",
        authority_policy=_policy(),
        tessera=tessera,  # type: ignore[arg-type]
        clock=lambda: 150,
    )
    projection = workflow.authorize_target_resolution(
        "run-1",
        manifest_path=SYNTHETIC_TARGET,
        request_path=request_path,
        receipt_path=receipt_path,
    )

    evidence = tmp_path / "runs" / "run-1" / "evidence" / "target-resolution"
    assert projection.state == RunState.TARGET_RESOLUTION_AUTHORIZED
    assert (evidence / "target-manifest.toml").is_file()
    assert (evidence / "target-resolution-request.json").is_file()
    assert (evidence / "target-resolution-receipt.tessera.json").is_file()
    assert not (tmp_path / "runs" / "run-1" / "resources.jsonl").exists()
    assert not (tmp_path / "runs" / "run-1" / "target").exists()


@pytest.mark.parametrize(
    ("receipt_nonce", "receipt_expiry", "message"),
    (
        ("different-nonce-01", 200, "nonce differs"),
        ("resolution-nonce-001", 201, "expiry differs"),
    ),
)
def test_stage_r_receipt_nonce_and_expiry_must_match_the_signed_request(
    tmp_path: Path,
    receipt_nonce: str,
    receipt_expiry: int,
    message: str,
) -> None:
    tessera = _Tessera()
    request_path, request = _resolution_request(tmp_path)
    receipt = _receipt(
        receipt_id="resolution-1",
        action="authorize-target-resolution",
        subject_digest=digest_obj(request),
        signer="human:founder",
        nonce=receipt_nonce,
    )
    receipt["expires_at"] = receipt_expiry
    receipt_path = tessera.add(
        tmp_path / "resolution.tessera.json",
        receipt,
        key=ROOT_KEY,
        kind="factory-authority-receipt",
    )
    workflow = FactoryWorkflow(
        tmp_path / "runs",
        authority_policy=_policy(),
        tessera=tessera,  # type: ignore[arg-type]
        clock=lambda: 150,
    )

    with pytest.raises(AuthorityVerificationError, match=message):
        workflow.authorize_target_resolution(
            "run-1",
            manifest_path=SYNTHETIC_TARGET,
            request_path=request_path,
            receipt_path=receipt_path,
        )
    assert not (tmp_path / "runs" / "run-1" / "ledger.jsonl").exists()
    assert not (tmp_path / "runs" / "run-1" / "resources.jsonl").exists()


def test_expired_stage_r_authority_causes_zero_repository_contact(tmp_path: Path) -> None:
    tessera = _Tessera()
    request_path, request = _resolution_request(tmp_path)
    receipt_path = tessera.add(
        tmp_path / "resolution.tessera.json",
        _receipt(
            receipt_id="resolution-1",
            action="authorize-target-resolution",
            subject_digest=digest_obj(request),
            signer="human:founder",
            nonce="resolution-nonce-001",
        ),
        key=ROOT_KEY,
        kind="factory-authority-receipt",
    )
    now = [150]
    workflow = FactoryWorkflow(
        tmp_path / "runs",
        authority_policy=_policy(),
        tessera=tessera,  # type: ignore[arg-type]
        clock=lambda: now[0],
    )
    workflow.authorize_target_resolution(
        "run-1",
        manifest_path=SYNTHETIC_TARGET,
        request_path=request_path,
        receipt_path=receipt_path,
    )
    now[0] = 200
    with pytest.raises(WorkflowError, match="expired before repository contact"):
        workflow.resolve_target("run-1", object_source=tmp_path / "unused")
    assert not (tmp_path / "runs" / "run-1" / "resources.jsonl").exists()


def test_retained_stage_r_signature_is_reverified_before_repository_contact(
    tmp_path: Path,
) -> None:
    tessera = _Tessera()
    request_path, request = _resolution_request(tmp_path)
    receipt_path = tessera.add(
        tmp_path / "resolution.tessera.json",
        _receipt(
            receipt_id="resolution-1",
            action="authorize-target-resolution",
            subject_digest=digest_obj(request),
            signer="human:founder",
            nonce="resolution-nonce-001",
        ),
        key=ROOT_KEY,
        kind="factory-authority-receipt",
    )
    workflow = FactoryWorkflow(
        tmp_path / "runs",
        authority_policy=_policy(),
        tessera=tessera,  # type: ignore[arg-type]
        clock=lambda: 150,
    )
    workflow.authorize_target_resolution(
        "run-1",
        manifest_path=SYNTHETIC_TARGET,
        request_path=request_path,
        receipt_path=receipt_path,
    )
    retained = (
        tmp_path
        / "runs"
        / "run-1"
        / "evidence"
        / "target-resolution"
        / "target-resolution-receipt.tessera.json"
    )
    retained.write_text('{"fixture":{"forged":true}}', encoding="utf-8")

    with pytest.raises(WorkflowError, match="retained target-resolution authority is invalid"):
        workflow.resolve_target("run-1", object_source=tmp_path / "must-not-be-contacted")
    assert not (tmp_path / "runs" / "run-1" / "resources.jsonl").exists()


def test_authorized_change_intake_persists_request_receipt_and_nonce(tmp_path: Path) -> None:
    tessera = _Tessera()
    workflow = _authorize(tmp_path, tessera)

    evidence = tmp_path / "runs" / "run-1" / "evidence" / "intake"
    assert (evidence / "execution-request.json").is_file()
    assert (evidence / "execution-receipt.tessera.json").is_file()
    assert workflow.store.consumed_authority_nonces("run-1") == frozenset(
        {"resolution-nonce-001", "authorize-nonce-001"}
    )


def test_intake_rejects_a_request_whose_verbatim_digest_does_not_rederive(
    tmp_path: Path,
) -> None:
    tessera = _Tessera()
    workflow = _resolve(tmp_path, tessera)
    request_path, request = _execution_request(
        tmp_path, workflow, verbatim_digest="sha256:" + ("f" * 64)
    )
    receipt_path = tessera.add(
        tmp_path / "authorize.tessera.json",
        _receipt(
            receipt_id="authorize-1",
            action="authorize-change",
            subject_digest=digest_obj(request),
            signer="human:founder",
            nonce="authorize-nonce-001",
        ),
        key=ROOT_KEY,
        kind="factory-authority-receipt",
    )
    with pytest.raises(WorkflowError, match="verbatim digest"):
        workflow.authorize_change(
            "run-1",
            request_path=request_path,
            receipt_path=receipt_path,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("run_id", "run-2", "different Factory run"),
        (
            "target_state_digest",
            "sha256:" + ("f" * 64),
            "different target-state",
        ),
    ),
)
def test_intake_authority_binds_the_run_and_target(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    tessera = _Tessera()
    workflow = _resolve(tmp_path, tessera)
    request_path, request = _execution_request(tmp_path, workflow)
    request[field] = value
    request_path.write_text(json.dumps(request), encoding="utf-8")
    receipt_path = tessera.add(
        tmp_path / "authorize.tessera.json",
        _receipt(
            receipt_id="authorize-1",
            action="authorize-change",
            subject_digest=digest_obj(request),
            signer="human:founder",
            nonce="authorize-nonce-001",
        ),
        key=ROOT_KEY,
        kind="factory-authority-receipt",
    )
    with pytest.raises(WorkflowError, match=message):
        workflow.authorize_change(
            "run-1",
            request_path=request_path,
            receipt_path=receipt_path,
        )


def test_intake_refuses_a_preexisting_symlink_evidence_path(tmp_path: Path) -> None:
    tessera = _Tessera()
    workflow = _resolve(tmp_path, tessera)
    request_path, request = _execution_request(tmp_path, workflow)
    receipt_path = tessera.add(
        tmp_path / "authorize.tessera.json",
        _receipt(
            receipt_id="authorize-1",
            action="authorize-change",
            subject_digest=digest_obj(request),
            signer="human:founder",
            nonce="authorize-nonce-001",
        ),
        key=ROOT_KEY,
        kind="factory-authority-receipt",
    )
    evidence = tmp_path / "runs" / "run-1" / "evidence" / "intake"
    evidence.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text("do not replace", encoding="utf-8")
    (evidence / "execution-request.json").symlink_to(outside)

    with pytest.raises(WorkflowError, match="symlink evidence"):
        workflow.authorize_change(
            "run-1",
            request_path=request_path,
            receipt_path=receipt_path,
        )
    assert outside.read_text(encoding="utf-8") == "do not replace"
    assert workflow.store.load("run-1").state == RunState.TARGET_RESOLVED


def test_three_phases_require_human_and_validator_receipts_and_reach_build_ready(
    tmp_path: Path,
) -> None:
    tessera = _Tessera()
    workflow = _authorize(tmp_path, tessera)

    for sequence, (phase, action) in enumerate(
        (
            ("product-specification", "ratify-product-specification"),
            ("architecture", "ratify-architecture"),
            ("operational-maturity", "ratify-operational-maturity"),
        ),
        start=1,
    ):
        document = _phase(phase, sequence)
        artifact = PhaseArtifact.from_dict(document)
        artifact_path = tmp_path / f"{phase}.json"
        artifact_path.write_text(json.dumps(document), encoding="utf-8")
        human_receipt = tessera.add(
            tmp_path / f"{phase}.human.tessera.json",
            _receipt(
                receipt_id=f"{phase}-human",
                action=action,
                subject_digest=artifact.content_digest,
                signer="human:founder",
                nonce=f"{phase}-human-nonce",
            ),
            key=ROOT_KEY,
            kind="factory-authority-receipt",
        )
        validator_receipt = tessera.add(
            tmp_path / f"{phase}.validator.tessera.json",
            _receipt(
                receipt_id=f"{phase}-validator",
                action=action,
                subject_digest=artifact.content_digest,
                signer="agent:validator",
                nonce=f"{phase}-validator-nonce",
            ),
            key=VALIDATOR_KEY,
            kind="factory-authority-receipt",
        )

        result = workflow.ratify_phase(
            "run-1",
            artifact_path=artifact_path,
            human_receipt_path=human_receipt,
            validator_receipt_path=validator_receipt,
        )
        assert (result.directory / "artifact.json").is_file()

    assert workflow.store.load("run-1").state == RunState.OPERATIONAL_MATURITY_RATIFIED
    assert len(workflow.store.consumed_authority_nonces("run-1")) == 8


def test_phase_artifact_must_bind_the_authorized_verbatim_source(tmp_path: Path) -> None:
    tessera = _Tessera()
    workflow = _authorize(tmp_path, tessera)
    document = _phase("product-specification", 1)
    document["source_digest"] = "sha256:" + ("f" * 64)
    artifact_path = tmp_path / "product.json"
    artifact_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(WorkflowError, match="authorized verbatim source"):
        workflow.ratify_phase(
            "run-1",
            artifact_path=artifact_path,
            human_receipt_path=tmp_path / "unused-human",
            validator_receipt_path=tmp_path / "unused-validator",
        )


def test_phase_receipt_nonce_cannot_be_replayed(tmp_path: Path) -> None:
    tessera = _Tessera()
    workflow = _authorize(tmp_path, tessera)
    document = _phase("product-specification", 1)
    artifact = PhaseArtifact.from_dict(document)
    artifact_path = tmp_path / "product.json"
    artifact_path.write_text(json.dumps(document), encoding="utf-8")
    human_receipt = tessera.add(
        tmp_path / "human.tessera.json",
        _receipt(
            receipt_id="human-1",
            action="ratify-product-specification",
            subject_digest=artifact.content_digest,
            signer="human:founder",
            nonce="authorize-nonce-001",
        ),
        key=ROOT_KEY,
        kind="factory-authority-receipt",
    )
    validator_receipt = tessera.add(
        tmp_path / "validator.tessera.json",
        _receipt(
            receipt_id="validator-1",
            action="ratify-product-specification",
            subject_digest=artifact.content_digest,
            signer="agent:validator",
            nonce="validator-nonce-001",
        ),
        key=VALIDATOR_KEY,
        kind="factory-authority-receipt",
    )

    with pytest.raises(AuthorityVerificationError, match="already been consumed"):
        workflow.ratify_phase(
            "run-1",
            artifact_path=artifact_path,
            human_receipt_path=human_receipt,
            validator_receipt_path=validator_receipt,
        )
