from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from factory_core.manifest import digest_bytes, digest_obj
from factory_core.provenance import PhaseArtifact
from factory_runtime.authority import (
    AuthorityPolicy,
    AuthorityVerificationError,
    Principal,
)
from factory_runtime.state import RunState
from factory_runtime.tessera import TesseraVerificationError, VerifiedEnvelope
from factory_runtime.workflow import FactoryWorkflow, WorkflowError

ROOT_KEY = "a" * 64
VALIDATOR_KEY = "b" * 64
TARGET = "sha256:" + ("c" * 64)
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
        envelope = self.envelopes[str(envelope_path)]
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
        bootstrap_scope=frozenset({"authorize-change", "activate-policy"}),
        genesis_digest="sha256:" + ("d" * 64),
    )


def _request(tmp_path: Path, *, verbatim_digest: str | None = None) -> tuple[Path, dict[str, Any]]:
    request = {
        "schema_version": "factory-authorization-request/1",
        "request_id": "request-1",
        "run_id": "run-1",
        "repository_id": "factory",
        "target_digest": TARGET,
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
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    return path, request


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


def _authorize(
    tmp_path: Path,
    tessera: _Tessera,
) -> FactoryWorkflow:
    request_path, request = _request(tmp_path)
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
    workflow = FactoryWorkflow(
        tmp_path / "runs",
        authority_policy=_policy(),
        tessera=tessera,  # type: ignore[arg-type]
        clock=lambda: 150,
    )
    projection = workflow.authorize_change(
        "run-1",
        target_digest=TARGET,
        request_path=request_path,
        receipt_path=receipt_path,
    )
    assert projection.state == RunState.INTAKE
    return workflow


def test_authorized_change_intake_persists_request_receipt_and_nonce(tmp_path: Path) -> None:
    tessera = _Tessera()
    workflow = _authorize(tmp_path, tessera)

    evidence = tmp_path / "runs" / "run-1" / "evidence" / "intake"
    assert (evidence / "authorization-request.json").is_file()
    assert (evidence / "authorization-receipt.tessera.json").is_file()
    assert workflow.store.consumed_authority_nonces("run-1") == frozenset(
        {"authorize-nonce-001"}
    )


def test_intake_rejects_a_request_whose_verbatim_digest_does_not_rederive(
    tmp_path: Path,
) -> None:
    tessera = _Tessera()
    request_path, request = _request(
        tmp_path,
        verbatim_digest="sha256:" + ("f" * 64),
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
    workflow = FactoryWorkflow(
        tmp_path / "runs",
        authority_policy=_policy(),
        tessera=tessera,  # type: ignore[arg-type]
        clock=lambda: 150,
    )

    with pytest.raises(WorkflowError, match="verbatim digest"):
        workflow.authorize_change(
            "run-1",
            target_digest=TARGET,
            request_path=request_path,
            receipt_path=receipt_path,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("run_id", "run-2", "different Factory run"),
        ("target_digest", "sha256:" + ("f" * 64), "different target digest"),
    ),
)
def test_intake_authority_binds_the_run_and_target(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    tessera = _Tessera()
    request_path, request = _request(tmp_path)
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
    workflow = FactoryWorkflow(
        tmp_path / "runs",
        authority_policy=_policy(),
        tessera=tessera,  # type: ignore[arg-type]
        clock=lambda: 150,
    )

    with pytest.raises(WorkflowError, match=message):
        workflow.authorize_change(
            "run-1",
            target_digest=TARGET,
            request_path=request_path,
            receipt_path=receipt_path,
        )


def test_intake_refuses_a_preexisting_symlink_evidence_path(tmp_path: Path) -> None:
    tessera = _Tessera()
    request_path, request = _request(tmp_path)
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
    (evidence / "authorization-request.json").symlink_to(outside)
    workflow = FactoryWorkflow(
        tmp_path / "runs",
        authority_policy=_policy(),
        tessera=tessera,  # type: ignore[arg-type]
        clock=lambda: 150,
    )

    with pytest.raises(WorkflowError, match="symlink evidence"):
        workflow.authorize_change(
            "run-1",
            target_digest=TARGET,
            request_path=request_path,
            receipt_path=receipt_path,
        )
    assert outside.read_text(encoding="utf-8") == "do not replace"
    assert not (tmp_path / "runs" / "run-1" / "ledger.jsonl").exists()


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
    assert len(workflow.store.consumed_authority_nonces("run-1")) == 7


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
