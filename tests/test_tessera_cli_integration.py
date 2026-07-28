from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import pytest

from factory_core.manifest import digest_bytes, digest_obj
from factory_core.provenance import PhaseArtifact
from factory_runtime.authority import load_genesis
from factory_runtime.evidence_plane import DeterminismRecord, SurfaceEvidence
from factory_runtime.orchestrator import FactoryOrchestrator, OrchestrationError
from factory_runtime.state import RunState
from factory_runtime.tessera import TesseraCli, TesseraVerificationError
from factory_runtime.workflow import FactoryWorkflow

RUNTIME_FIXTURES = Path(__file__).parent / "fixtures" / "runtime_agents"


def _binary() -> Path:
    configured = os.environ.get("FACTORY_TESSERA_BIN")
    if not configured:
        pytest.skip("FACTORY_TESSERA_BIN is required for the real Tessera integration proof")
    binary = Path(configured)
    if not binary.is_file():
        pytest.fail(f"configured Tessera binary does not exist: {binary}")
    return binary


def _keypair(binary: Path, path: Path) -> str:
    subprocess.run(
        [str(binary), "keygen", "--output", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    encoded = path.read_text(encoding="utf-8").strip()
    assert len(encoded) == 128
    return encoded[64:]


@pytest.mark.tessera_integration
def test_real_tessera_signs_validates_and_detects_tampering(tmp_path: Path) -> None:
    binary = _binary()
    version = subprocess.run(
        [str(binary), "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert version.stdout.startswith("tessera ")

    key = tmp_path / "signing-key.hex"
    _keypair(binary, key)
    cli = TesseraCli((str(binary),))
    envelope_path = tmp_path / "evidence.tessera.json"
    payload = {
        "schema_version": "factory-tessera-integration/1",
        "subject_digest": "sha256:" + ("a" * 64),
    }
    verified = cli.wrap_json(
        payload,
        kind="factory-integration-proof",
        key_path=key,
        output_path=envelope_path,
    )

    assert verified.payload == payload
    assert len(verified.public_key) == 64
    assert cli.verify_json(
        envelope_path,
        trusted_public_keys=(verified.public_key,),
        expected_kind="factory-integration-proof",
        expected_payload_digest=verified.payload_digest,
    ) == verified

    tampered_path = tmp_path / "tampered.tessera.json"
    tampered = json.loads(envelope_path.read_text(encoding="utf-8"))
    tampered["state"]["kind"] = "forged-kind"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(TesseraVerificationError, match="Tessera refused"):
        cli.verify_json(tampered_path)


@pytest.mark.tessera_integration
@pytest.mark.skipif(
    platform.system() != "Darwin",
    reason="full runtime E2E requires macOS Seatbelt",
)
def test_real_runtime_reaches_preview_through_authority_isolation_tests_and_evidence(
    tmp_path: Path,
) -> None:
    binary = _binary()
    cli = TesseraCli((str(binary),))
    now = int(time.time())
    root_key = tmp_path / "root.hex"
    validator_key = tmp_path / "validator.hex"
    coder_key = tmp_path / "coder.hex"
    tester_key = tmp_path / "tester.hex"
    root_public_key = _keypair(binary, root_key)
    validator_public_key = _keypair(binary, validator_key)
    coder_public_key = _keypair(binary, coder_key)
    tester_public_key = _keypair(binary, tester_key)
    genesis_payload = {
        "schema_version": "factory-genesis/1",
        "repository_id": "synthetic-factory-target",
        "policy_id": "synthetic-authority/1",
        "root_public_key": root_public_key,
        "principals": [
            {
                "identity": "human:founder",
                "kind": "human",
                "public_key": root_public_key,
                "capabilities": [
                    "factory:authorize-change",
                    "factory:ratify-product-specification",
                    "factory:ratify-architecture",
                    "factory:ratify-operational-maturity",
                    "factory:approve-promotion",
                    "factory:activate-policy",
                ],
            },
            {
                "identity": "agent:validator",
                "kind": "agent",
                "public_key": validator_public_key,
                "capabilities": [
                    "factory:ratify-product-specification",
                    "factory:ratify-architecture",
                    "factory:ratify-operational-maturity",
                ],
            },
            {
                "identity": "agent:coder",
                "kind": "agent",
                "public_key": coder_public_key,
                "capabilities": [],
            },
            {
                "identity": "agent:tester",
                "kind": "agent",
                "public_key": tester_public_key,
                "capabilities": [],
            },
        ],
        "bootstrap": {
            "enabled": True,
            "scope": ["authorize-change", "activate-policy"],
            "deactivates_when": "the first replacement policy activation is consumed",
        },
        "issued_at": now,
    }
    genesis_path = tmp_path / "genesis.tessera.json"
    cli.wrap_json(
        genesis_payload,
        kind="factory-genesis",
        key_path=root_key,
        output_path=genesis_path,
    )
    policy = load_genesis(
        genesis_path,
        trusted_root_public_key=root_public_key,
        tessera=cli,
    )
    workflow = FactoryWorkflow(
        tmp_path / "runs",
        authority_policy=policy,
        tessera=cli,
    )

    verbatim = "Build the synthetic authorized Factory change."
    source_digest = digest_bytes(verbatim.encode())
    target_digest = "sha256:" + ("9" * 64)
    request = {
        "schema_version": "factory-authorization-request/1",
        "request_id": "synthetic-request",
        "run_id": "synthetic-run",
        "repository_id": "synthetic-factory-target",
        "target_digest": target_digest,
        "proposed_by": "human:founder",
        "verbatim_request": verbatim,
        "verbatim_request_digest": source_digest,
        "requested_outcome": "Prove the executable authorization and ratification path.",
        "surfaces": [
            {
                "surface_id": "synthetic-control-plane",
                "proposed_criticality": "critical",
                "reason": "This test exercises authorization.",
            }
        ],
        "created_at": now,
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    authorize_receipt = {
        "schema_version": "factory-authority-receipt/1",
        "receipt_id": "authorize-synthetic",
        "run_id": "synthetic-run",
        "repository_id": "synthetic-factory-target",
        "action": "authorize-change",
        "subject_digest": digest_obj(request),
        "signer_identity": "human:founder",
        "capabilities": ["factory:authorize-change"],
        "issued_at": now,
        "expires_at": now + 600,
        "nonce": "authorize-synthetic-nonce",
    }
    authorize_receipt_path = tmp_path / "authorize.tessera.json"
    cli.wrap_json(
        authorize_receipt,
        kind="factory-authority-receipt",
        key_path=root_key,
        output_path=authorize_receipt_path,
    )
    workflow.authorize_change(
        "synthetic-run",
        target_digest=target_digest,
        request_path=request_path,
        receipt_path=authorize_receipt_path,
    )

    phases = (
        ("product-specification", "ratify-product-specification"),
        ("architecture", "ratify-architecture"),
        ("operational-maturity", "ratify-operational-maturity"),
    )
    phase_artifacts: dict[str, PhaseArtifact] = {}
    for index, (phase, action) in enumerate(phases, start=1):
        phase_document = {
            "artifact_id": f"synthetic-{phase}",
            "phase": phase,
            "version": "1",
            "source_digest": source_digest,
            "human_ratifier": "human:founder",
            "validator_ratifier": "agent:validator",
            "items": [
                {
                    "item_id": f"{phase}:1",
                    "canonical_statement": f"The synthetic {phase} artifact is authoritative.",
                    "supersedes": [],
                }
            ],
        }
        phase_artifact = PhaseArtifact.from_dict(phase_document)
        phase_artifacts[phase] = phase_artifact
        phase_path = tmp_path / f"{phase}.json"
        phase_path.write_text(json.dumps(phase_document), encoding="utf-8")

        receipt_base = {
            "schema_version": "factory-authority-receipt/1",
            "run_id": "synthetic-run",
            "repository_id": "synthetic-factory-target",
            "action": action,
            "subject_digest": phase_artifact.content_digest,
            "capabilities": [f"factory:{action}"],
            "issued_at": now,
            "expires_at": now + 600,
        }
        human_receipt_path = tmp_path / f"{phase}.human.tessera.json"
        cli.wrap_json(
            {
                **receipt_base,
                "receipt_id": f"{phase}-human",
                "signer_identity": "human:founder",
                "nonce": f"{phase}-human-nonce-{index}",
            },
            kind="factory-authority-receipt",
            key_path=root_key,
            output_path=human_receipt_path,
        )
        validator_receipt_path = tmp_path / f"{phase}.validator.tessera.json"
        cli.wrap_json(
            {
                **receipt_base,
                "receipt_id": f"{phase}-validator",
                "signer_identity": "agent:validator",
                "nonce": f"{phase}-validator-nonce-{index}",
            },
            kind="factory-authority-receipt",
            key_path=validator_key,
            output_path=validator_receipt_path,
        )
        workflow.ratify_phase(
            "synthetic-run",
            artifact_path=phase_path,
            human_receipt_path=human_receipt_path,
            validator_receipt_path=validator_receipt_path,
        )

    ratified = workflow.store.load("synthetic-run")
    assert ratified.state == RunState.OPERATIONAL_MATURITY_RATIFIED
    assert set(ratified.phase_artifact_digests) == {
        "product-specification",
        "architecture",
        "operational-maturity",
    }
    assert len(workflow.store.consumed_authority_nonces("synthetic-run")) == 7

    product = phase_artifacts["product-specification"]
    backreference = product.backreference(product.items[0]).to_dict()
    build_spec = {
        "schema_version": "factory-synthetic-build-spec/1",
        "interface": {
            "module": "calculator.py",
            "function": "add",
            "operation": "integer-addition",
        },
        "acceptance": [
            {
                "criterion_id": "AC-1",
                "left": 2,
                "right": 3,
                "expected": 5,
                "backreference": backreference,
            },
            {
                "criterion_id": "AC-2",
                "left": -7,
                "right": 4,
                "expected": -3,
                "backreference": backreference,
            },
        ],
    }
    build_spec_path = tmp_path / "combined-spec.json"
    build_spec_path.write_text(json.dumps(build_spec), encoding="utf-8")
    surface_evidence = (
        SurfaceEvidence(
            surface_id="synthetic-control-plane",
            criticality="critical",
            oracle_adequate=True,
            required_evidence_ids=("acceptance-tests",),
            evidence_digests={},
        ),
    )
    determinism_records = (
        DeterminismRecord(
            surface_id="synthetic-control-plane",
            criticality="critical",
            deterministic=True,
            flake_count=0,
            automatic_retry_count=0,
        ),
    )
    orchestrator = FactoryOrchestrator(workflow)
    common_arguments = {
        "spec_path": build_spec_path,
        "tester_command": (sys.executable, str(RUNTIME_FIXTURES / "tester.py")),
        "validator_command": (sys.executable, str(RUNTIME_FIXTURES / "validator.py")),
        "coder_trusted_paths": (RUNTIME_FIXTURES / "coder.py",),
        "tester_trusted_paths": (RUNTIME_FIXTURES / "tester.py",),
        "validator_trusted_paths": (RUNTIME_FIXTURES / "validator.py",),
        "implementer_identity": "agent:coder",
        "tester_identity": "agent:tester",
        "verifier_identity": "agent:validator",
        "verifier_key_path": validator_key,
        "surface_evidence": surface_evidence,
        "determinism_records": determinism_records,
    }
    with pytest.raises(OrchestrationError, match="attempt_id"):
        orchestrator.build_and_validate(
            "synthetic-run",
            attempt_id="../escape",
            coder_command=(sys.executable, str(RUNTIME_FIXTURES / "coder.py")),
            **common_arguments,
        )
    assert workflow.store.load("synthetic-run").state == RunState.OPERATIONAL_MATURITY_RATIFIED

    failed = orchestrator.build_and_validate(
        "synthetic-run",
        attempt_id="attempt-failed",
        coder_command=(sys.executable, "-c", "raise SystemExit(1)"),
        **common_arguments,
    )
    assert failed.repair_signal == "fail"
    assert failed.projection.state == RunState.BLOCKED

    outcome = orchestrator.build_and_validate(
        "synthetic-run",
        attempt_id="attempt-1",
        coder_command=(sys.executable, str(RUNTIME_FIXTURES / "coder.py")),
        **common_arguments,
    )
    assert outcome.passed is True
    assert outcome.repair_signal == "pass"
    assert outcome.projection.state == RunState.PREVIEW
    assert outcome.evidence_report is not None
    assert outcome.evidence_report.provenance.satisfied is True
    assert outcome.evidence_report.checklist.satisfied is True
    assert outcome.evidence_envelope is not None
    assert outcome.evidence_envelope.public_key == validator_public_key
    cli.verify_json(
        outcome.evidence_envelope.path,
        trusted_public_keys=(validator_public_key,),
        expected_kind="factory-evidence-bundle",
        expected_payload_digest=outcome.evidence_envelope.payload_digest,
    )
