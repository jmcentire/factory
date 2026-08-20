from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

import factory_runtime.orchestrator as orchestrator_module
from factory_core.build_plan import (
    BuildPlan,
    BuildStep,
    OracleLink,
    PatternCatalog,
    PatternDefinition,
)
from factory_core.correction import LANE_CAPABILITY
from factory_core.evidence import EvidenceIntegrity
from factory_core.independence import (
    INDEPENDENCE_STRONGER,
    ROLE_CODER,
    ROLE_TESTER,
    ROLE_VALIDATOR,
    STRUCTURAL_MODE_ISOLATED,
    AgentIdentity,
    IndependenceRecord,
    StructuralModeRecord,
)
from factory_core.manifest import digest_bytes, digest_obj
from factory_core.monitors import (
    MONITOR_AUTHORSHIP_HUMAN,
    MONITOR_DERIVATION_SPECIFICATION,
    Monitor,
)
from factory_core.provenance import PhaseArtifact
from factory_core.target import load_target_manifest
from factory_runtime.acceptance_obligations import (
    AcceptanceObligationCatalog,
    validator_execution_digests,
)
from factory_runtime.adversarial_review import canonical_document_bytes
from factory_runtime.authority import AuthorityPolicy, Principal, load_genesis
from factory_runtime.evidence_plane import (
    DeterminismRecord,
    EvidencePlaneError,
    SurfaceEvidence,
    TesseraEvidenceEnvelopeVerifier,
)
from factory_runtime.generation import build_input_document, verify_prepared_generation
from factory_runtime.orchestrator import BuildOutcome, FactoryOrchestrator, OrchestrationError
from factory_runtime.repair import RepairBrief, RepairPlan, RepairPolicy, RepairSupervisor
from factory_runtime.resources import ResourceLedger
from factory_runtime.resume import derive_resume_checkpoint
from factory_runtime.snapshot import tree_digest, verify_frozen_tree
from factory_runtime.state import RunState, RunStateError, RunStore
from factory_runtime.target_state import normalize_repository_url
from factory_runtime.tessera import TesseraCli, TesseraVerificationError
from factory_runtime.workflow import FactoryWorkflow, WorkflowError
from tests.conftest import (
    acceptance_catalog_artifacts,
    build_payload,
    create_intake_run,
    fixture_phase_artifact_digests,
    preview_artifacts,
    ratification_receipts,
    retained_generation_artifacts,
    synthetic_candidate_digest,
    validation_artifacts,
)

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
    assert (
        cli.verify_json(
            envelope_path,
            trusted_public_keys=(verified.public_key,),
            expected_kind="factory-integration-proof",
            expected_payload_digest=verified.payload_digest,
        )
        == verified
    )

    tampered_path = tmp_path / "tampered.tessera.json"
    tampered = json.loads(envelope_path.read_text(encoding="utf-8"))
    tampered["state"]["kind"] = "forged-kind"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(TesseraVerificationError, match="Tessera refused"):
        cli.verify_json(tampered_path)


@pytest.mark.tessera_integration
def test_real_tessera_authenticates_preview_admission_and_replay(tmp_path: Path) -> None:
    binary = _binary()
    validator_key = tmp_path / "validator.hex"
    validator_public_key = _keypair(binary, validator_key)
    cli = TesseraCli((str(binary),))
    runs = tmp_path / "runs"
    staging_store = RunStore(runs, clock=lambda: 100)
    target_digest = "sha256:" + ("1" * 64)
    source_digest = "sha256:" + ("2" * 64)
    create_intake_run(
        staging_store,
        run_id="run-1",
        target_digest=target_digest,
        source_digest=source_digest,
    )
    phase_digests = fixture_phase_artifact_digests()
    for state, phase in (
        (RunState.PRODUCT_SPECIFICATION_RATIFIED, "product-specification"),
        (RunState.ARCHITECTURE_RATIFIED, "architecture"),
        (RunState.OPERATIONAL_MATURITY_RATIFIED, "operational-maturity"),
    ):
        staging_store.transition(
            "run-1",
            state,
            actor="agent:validator",
            artifact_digests={
                phase: phase_digests[phase],
                **ratification_receipts(phase),
            },
        )
    staging_store.transition(
        "run-1",
        RunState.BUILDING,
        actor="agent:validator",
        artifact_digests={
            **retained_generation_artifacts(
                staging_store,
                include_acceptance_catalog=False,
            ),
            **acceptance_catalog_artifacts(staging_store),
        },
        payload=build_payload(),
    )
    candidate_digest = synthetic_candidate_digest()
    staging_store.transition(
        "run-1",
        RunState.VALIDATING,
        actor="agent:validator",
        artifact_digests=validation_artifacts(
            staging_store,
            candidate=candidate_digest,
        ),
        payload={"tester_identity": "agent:tester"},
        implementer_identity="agent:coder",
        verifier_identity="agent:validator",
    )
    artifacts = preview_artifacts(
        staging_store,
        candidate=candidate_digest,
        reviewer_identity="agent:validator",
    )
    building = [
        entry
        for entry in staging_store.verified_ledger_entries("run-1")
        if entry.get("to_state") == RunState.BUILDING
    ][-1]
    building_payload = building["payload"]
    assert isinstance(building_payload, Mapping)
    envelope_path = (
        runs
        / "run-1"
        / "evidence"
        / "build-attempts"
        / str(building_payload["attempt_id"])
        / "evidence-bundle.tessera.json"
    )
    fixture_document = json.loads(envelope_path.read_text(encoding="utf-8"))
    payload = fixture_document["payload"]
    assert isinstance(payload, dict)
    envelope_path.unlink()
    real_envelope = cli.wrap_json(
        payload,
        kind="factory-evidence-bundle",
        key_path=validator_key,
        output_path=envelope_path,
    )
    artifacts["evidence-envelope"] = real_envelope.envelope_digest
    genesis_artifacts = staging_store.verified_ledger_entries("run-1")[0]["artifact_digests"]
    assert isinstance(genesis_artifacts, Mapping)
    policy = AuthorityPolicy(
        repository_id="fixture",
        policy_id="fixture-policy/1",
        root_public_key="f" * 64,
        principals={
            "agent:validator": Principal(
                identity="agent:validator",
                kind="agent",
                public_key=validator_public_key,
                capabilities=frozenset(),
            )
        },
        bootstrap_enabled=False,
        bootstrap_scope=frozenset(),
        genesis_digest=str(genesis_artifacts["authority-genesis"]),
    )
    authenticated_store = RunStore(
        runs,
        clock=lambda: 100,
        preview_evidence_verifier=TesseraEvidenceEnvelopeVerifier(
            tessera=cli,
            authority_policy=policy,
        ),
    )

    review_root = (
        runs
        / "run-1"
        / "evidence"
        / "validator-adversarial-reviews"
        / artifacts["validator-review-subject"].removeprefix("sha256:")
    )
    original_report_path = review_root / (
        f"{artifacts['validator-adversarial-review'].removeprefix('sha256:')}.json"
    )
    substituted_report = json.loads(original_report_path.read_text(encoding="utf-8"))
    substituted_report["dimensions"][0]["summary"] = (
        "A different but independently schema-valid review of the same subject."
    )
    substituted_report_digest = digest_obj(substituted_report)
    (review_root / f"{substituted_report_digest.removeprefix('sha256:')}.json").write_bytes(
        canonical_document_bytes(substituted_report)
    )
    substituted_artifacts = {
        **artifacts,
        "validator-adversarial-review": substituted_report_digest,
    }

    with pytest.raises(
        RunStateError,
        match="signed evidence bundle is invalid: retained evidence bundle has stale or "
        "substituted preview_admission",
    ):
        authenticated_store.transition(
            "run-1",
            RunState.PREVIEW,
            actor="agent:validator",
            artifact_digests=substituted_artifacts,
            payload={"tester_identity": "agent:tester"},
            implementer_identity="agent:coder",
            verifier_identity="agent:validator",
        )

    assert authenticated_store.load("run-1").state == RunState.VALIDATING

    projection = authenticated_store.transition(
        "run-1",
        RunState.PREVIEW,
        actor="agent:validator",
        artifact_digests=artifacts,
        payload={"tester_identity": "agent:tester"},
        implementer_identity="agent:coder",
        verifier_identity="agent:validator",
    )

    assert projection.state == RunState.PREVIEW
    receipt = authenticated_store.verified_ledger_entries("run-1")[-1]["payload"]
    assert isinstance(receipt, Mapping)
    verification = receipt["evidence_verification_receipt"]
    assert isinstance(verification, Mapping)
    assert verification["verifier_id"] == "factory-tessera-evidence-verifier/1"
    assert verification["signer_identity"] == "agent:validator"
    assert verification["signer_public_key"] == validator_public_key
    assert authenticated_store.rebuild_projection("run-1") == projection
    with pytest.raises(RunStateError, match="explicit cryptographic evidence verifier"):
        RunStore(runs, clock=lambda: 100).load("run-1")

    ledger_path = runs / "run-1" / "ledger.jsonl"
    rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    rows[-1]["artifact_digests"]["validator-adversarial-review"] = substituted_report_digest
    body = {key: value for key, value in rows[-1].items() if key != "entry_hash"}
    rows[-1]["entry_hash"] = digest_obj(body)
    ledger_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        RunStateError,
        match="preview signed evidence bundle is invalid: retained evidence bundle has stale "
        "or substituted preview_admission",
    ):
        authenticated_store.rebuild_projection("run-1")


@pytest.mark.tessera_integration
def test_real_tessera_preview_verifier_rejects_another_signing_identity(
    tmp_path: Path,
) -> None:
    binary = _binary()
    expected_key = tmp_path / "expected.hex"
    wrong_key = tmp_path / "wrong.hex"
    expected_public_key = _keypair(binary, expected_key)
    _keypair(binary, wrong_key)
    cli = TesseraCli((str(binary),))
    payload = {"schema_version": "fixture/1", "subject": "wrong-signer"}
    envelope_path = tmp_path / "wrong-signer.tessera.json"
    envelope = cli.wrap_json(
        payload,
        kind="factory-evidence-bundle",
        key_path=wrong_key,
        output_path=envelope_path,
    )
    genesis_digest = "sha256:" + ("a" * 64)
    verifier = TesseraEvidenceEnvelopeVerifier(
        tessera=cli,
        authority_policy=AuthorityPolicy(
            repository_id="fixture",
            policy_id="fixture-policy/1",
            root_public_key="f" * 64,
            principals={
                "agent:validator": Principal(
                    identity="agent:validator",
                    kind="agent",
                    public_key=expected_public_key,
                    capabilities=frozenset(),
                )
            },
            bootstrap_enabled=False,
            bootstrap_scope=frozenset(),
            genesis_digest=genesis_digest,
        ),
    )

    with pytest.raises(EvidencePlaneError, match="trusted key set"):
        verifier.verify(
            envelope_path,
            expected_kind="factory-evidence-bundle",
            expected_payload_digest=envelope.payload_digest,
            expected_envelope_digest=envelope.envelope_digest,
            expected_signer_identity="agent:validator",
            expected_authority_genesis_digest=genesis_digest,
        )


@pytest.mark.tessera_integration
@pytest.mark.skipif(
    platform.system() != "Darwin",
    reason="full runtime E2E requires macOS Seatbelt",
)
def test_real_runtime_reaches_preview_through_authority_isolation_tests_and_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = _binary()
    cli = TesseraCli((str(binary),))
    real_sync_chain = orchestrator_module.fsync_directory_chain
    preview_syncs: list[tuple[Path, Path]] = []

    def track_sync_chain(start: str | Path, *, through: str | Path) -> None:
        preview_syncs.append((Path(start), Path(through)))
        real_sync_chain(start, through=through)

    monkeypatch.setattr(orchestrator_module, "fsync_directory_chain", track_sync_chain)
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
                    "factory:authorize-target-resolution",
                    "factory:authorize-change",
                    "factory:ratify-product-specification",
                    "factory:ratify-architecture",
                    "factory:ratify-operational-maturity",
                    "factory:ratify-acceptance-obligation-catalog",
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
                    "factory:ratify-acceptance-obligation-catalog",
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
            "scope": [
                "authorize-target-resolution",
                "authorize-change",
                "activate-policy",
            ],
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

    pattern = PatternDefinition(
        pattern_id="python-function",
        version="1",
        artifact_digest=digest_obj({"artifact": "python-function-v1"}),
        qualification_evidence_digest=digest_obj({"qualification": "python-function-v1"}),
        mechanism={
            "kind": "source-generator",
            "required_configuration": ["module", "function", "operation"],
        },
    )
    catalog = PatternCatalog(
        catalog_id="synthetic-qualified-patterns",
        version="1",
        patterns=(pattern,),
    )
    catalog_path = tmp_path / "pattern-catalog.json"
    catalog_path.write_text(json.dumps(catalog.body()), encoding="utf-8")
    target_path = tmp_path / "target.toml"
    target_path.write_text(
        "\n".join(
            (
                'schema_version = "factory-target-manifest/1"',
                'target_id = "synthetic-runtime"',
                "[repo]",
                'url = "https://example.invalid/synthetic.git"',
                'ref = "main"',
                "[adapters]",
                'repo = "readonly_git"',
                'knowledge = "kin_reader"',
                'compliance = "rules_json"',
                'idp = "oidc"',
                'artifact_sink = "local_fs"',
                "[compliance]",
                'rules_path = "compliance/rules.json"',
                "[build]",
                f'pattern_catalog_digest = "{catalog.content_digest}"',
                "max_attempts = 2",
                'construction_modes = ["regenerate", "brownfield"]',
                "",
            )
        ),
        encoding="utf-8",
    )
    manifest = load_target_manifest(target_path)
    target_digest = manifest.content_digest
    verbatim = "Build the synthetic authorized Factory change."
    source_digest = digest_bytes(verbatim.encode())

    operator_source = tmp_path / "operator-source"
    subprocess.run(
        ["git", "init", "-b", "main", str(operator_source)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(operator_source), "config", "user.email", "factory@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(operator_source), "config", "user.name", "Factory Test"],
        check=True,
    )
    (operator_source / "README.md").write_text("synthetic target\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(operator_source), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(operator_source), "commit", "-m", "synthetic target"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(operator_source),
            "remote",
            "add",
            "origin",
            str(manifest.repo["url"]),
        ],
        check=True,
    )

    resolution_request = {
        "schema_version": "factory-target-resolution-request/1",
        "request_id": "synthetic-resolution",
        "run_id": "synthetic-run",
        "repository_id": "synthetic-factory-target",
        "generation": 1,
        "target_manifest_digest": target_digest,
        "target_manifest_source_digest": manifest.source_digest,
        "normalized_url": normalize_repository_url(str(manifest.repo["url"])),
        "requested_ref": str(manifest.repo["ref"]),
        "subpath": "",
        "allowed_contact_operations": ["git-local-object-read"],
        "lane_execution": False,
        "nonce": "synthetic-resolution-nonce",
        "created_at": now,
        "expires_at": now + 600,
    }
    resolution_request_path = tmp_path / "resolution-request.json"
    resolution_request_path.write_text(json.dumps(resolution_request), encoding="utf-8")
    resolution_receipt = {
        "schema_version": "factory-authority-receipt/1",
        "receipt_id": "resolve-synthetic",
        "run_id": "synthetic-run",
        "repository_id": "synthetic-factory-target",
        "action": "authorize-target-resolution",
        "subject_digest": digest_obj(resolution_request),
        "signer_identity": "human:founder",
        "capabilities": ["factory:authorize-target-resolution"],
        "issued_at": now,
        "expires_at": now + 600,
        "nonce": "synthetic-resolution-nonce",
    }
    resolution_receipt_path = tmp_path / "resolution.tessera.json"
    cli.wrap_json(
        resolution_receipt,
        kind="factory-authority-receipt",
        key_path=root_key,
        output_path=resolution_receipt_path,
    )
    workflow.authorize_target_resolution(
        "synthetic-run",
        manifest_path=target_path,
        request_path=resolution_request_path,
        receipt_path=resolution_receipt_path,
    )
    resolved = workflow.resolve_target("synthetic-run", object_source=operator_source)

    request = {
        "schema_version": "factory-execution-request/1",
        "request_id": "synthetic-request",
        "run_id": "synthetic-run",
        "repository_id": "synthetic-factory-target",
        "generation": resolved.generation,
        "target_manifest_digest": target_digest,
        "target_state_digest": resolved.target_state_digest,
        "resolved_commit": resolved.target_state["resolved_commit"],
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
        request_path=request_path,
        receipt_path=authorize_receipt_path,
    )

    phases = (
        ("product-specification", "ratify-product-specification"),
        ("architecture", "ratify-architecture"),
        ("operational-maturity", "ratify-operational-maturity"),
    )
    phase_items = {
        "product-specification": (
            "product:addition",
            "The product adds integers and returns their mathematical sum.",
        ),
        "architecture": (
            "architecture:interface",
            "The public Python interface is calculator.py:add(left: int, right: int).",
        ),
        "operational-maturity": (
            "test:addition",
            "Acceptance examples cover positive and negative integer addition.",
        ),
    }
    phase_artifacts: dict[str, PhaseArtifact] = {}
    for index, (phase, action) in enumerate(phases, start=1):
        item_id, statement = phase_items[phase]
        phase_document = {
            "artifact_id": f"synthetic-{phase}",
            "phase": phase,
            "version": "1",
            "source_digest": source_digest,
            "human_ratifier": "human:founder",
            "validator_ratifier": "agent:validator",
            "items": [
                {
                    "item_id": item_id,
                    "canonical_statement": statement,
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
    assert len(workflow.store.consumed_authority_nonces("synthetic-run")) == 8

    product = phase_artifacts["product-specification"]
    architecture = phase_artifacts["architecture"]
    operations = phase_artifacts["operational-maturity"]
    product_reference = product.backreference(product.items[0])
    architecture_reference = architecture.backreference(architecture.items[0])
    oracle_reference = operations.backreference(operations.items[0])
    authority = tuple(phase_artifacts[phase] for phase, _ in phases)
    build_input = build_input_document("synthetic-run", target_digest, authority)
    plan = BuildPlan(
        plan_id="synthetic-plan",
        version="1",
        run_id="synthetic-run",
        target_digest=target_digest,
        construction_mode="regenerate",
        max_build_attempts=2,
        build_input_digest=digest_obj(build_input),
        pattern_catalog_digest=catalog.content_digest,
        phase_artifact_digests={artifact.phase: artifact.content_digest for artifact in authority},
        steps=(
            BuildStep(
                step_id="generate-calculator",
                pattern_id=pattern.pattern_id,
                pattern_digest=pattern.content_digest,
                configuration={
                    "module": "calculator.py",
                    "function": "add",
                    "operation": "integer-addition",
                },
                intent_backreferences=(product_reference, architecture_reference),
            ),
        ),
        oracle_links=(
            OracleLink(product_reference, oracle_reference),
            OracleLink(architecture_reference, oracle_reference),
        ),
    )
    plan_path = tmp_path / "build-plan.json"
    plan_path.write_text(json.dumps(plan.body()), encoding="utf-8")
    tester_command = (sys.executable, str(RUNTIME_FIXTURES / "tester.py"))
    validator_command = (sys.executable, str(RUNTIME_FIXTURES / "validator.py"))
    command_digest, configuration_digest, environment_digest = validator_execution_digests(
        validator_command,
        trusted_paths=(RUNTIME_FIXTURES / "validator.py",),
    )
    examples = (
        ("AC-1", 2, 3, 5),
        ("AC-2", -7, 4, -3),
    )
    acceptance_catalog_document = {
        "schema_version": "factory-acceptance-obligation-catalog/1",
        "catalog_id": "synthetic-acceptance",
        "version": "1",
        "run_id": "synthetic-run",
        "generation": ratified.generation,
        "target_state_digest": ratified.target_state_digest,
        "phase_artifact_digests": dict(ratified.phase_artifact_digests),
        "human_ratifier": "human:founder",
        "validator_ratifier": "agent:validator",
        "max_review_rounds": 2,
        "triggers": [
            {
                "trigger_id": "validating-to-preview",
                "from_state": "validating",
                "to_state": "preview",
                "command_digest": command_digest,
                "configuration_digest": configuration_digest,
                "environment_digest": environment_digest,
                "obligations": [
                    {
                        "obligation_id": "integer-addition-examples",
                        "criterion": (
                            "Every ratified integer-addition example passes against the exact "
                            "candidate and independently authored test snapshot."
                        ),
                        "verifier_id": "validator-test-execution-v1",
                        "intent_backreferences": [
                            product.backreference(product.items[0]).to_dict(),
                            operations.backreference(operations.items[0]).to_dict(),
                        ],
                        "required_evidence_ids": [
                            "candidate",
                            "acceptance-tests",
                            "coder-output-snapshot",
                            "tester-output-snapshot",
                        ],
                        "test_assertions": [
                            {
                                "test_id": test_id,
                                "assertion_digest": digest_obj(
                                    {
                                        "test_id": test_id,
                                        "left": left,
                                        "right": right,
                                        "expected": expected,
                                    }
                                ),
                            }
                            for test_id, left, right, expected in examples
                        ],
                    }
                ],
            }
        ],
    }
    acceptance_catalog = AcceptanceObligationCatalog.from_dict(acceptance_catalog_document)
    resume_config = tmp_path / "resume-config.json"
    resume_config.write_text('{"runner":"synthetic"}\n', encoding="utf-8")
    resume_checkpoint_document = derive_resume_checkpoint(
        workflow.root,
        "synthetic-run",
        checkpoint_id="synthetic-checkpoint-1",
        previous_checkpoint_digest="",
        genesis_path=genesis_path,
        trusted_root_public_key=root_public_key,
        tessera=cli,
        configuration_sources={"runner": resume_config},
        acceptance_obligation_catalog_digest=acceptance_catalog.content_digest,
        retention={
            "policy_id": "synthetic-retention-1",
            "mode": "retain-indefinitely",
            "retain_until": 0,
            "metadata_classes": [
                "authority-envelopes",
                "lifecycle-ledger",
                "resource-ledger",
            ],
            "erasure_authority": "human:founder",
        },
        clock=lambda: now,
    )
    resume_checkpoint = tmp_path / "resume-checkpoint.json"
    resume_checkpoint.write_text(
        json.dumps(resume_checkpoint_document),
        encoding="utf-8",
    )
    acceptance_catalog_path = tmp_path / "acceptance-obligation-catalog.json"
    acceptance_catalog_path.write_text(
        json.dumps(acceptance_catalog_document),
        encoding="utf-8",
    )
    acceptance_receipt_base = {
        "schema_version": "factory-authority-receipt/1",
        "run_id": "synthetic-run",
        "repository_id": "synthetic-factory-target",
        "action": "ratify-acceptance-obligation-catalog",
        "subject_digest": acceptance_catalog.content_digest,
        "capabilities": ["factory:ratify-acceptance-obligation-catalog"],
        "issued_at": now,
        "expires_at": now + 600,
    }
    acceptance_human_receipt_path = tmp_path / "acceptance.human.tessera.json"
    cli.wrap_json(
        {
            **acceptance_receipt_base,
            "receipt_id": "acceptance-human",
            "signer_identity": "human:founder",
            "nonce": "acceptance-human-nonce",
        },
        kind="factory-authority-receipt",
        key_path=root_key,
        output_path=acceptance_human_receipt_path,
    )
    acceptance_validator_receipt_path = tmp_path / "acceptance.validator.tessera.json"
    cli.wrap_json(
        {
            **acceptance_receipt_base,
            "receipt_id": "acceptance-validator",
            "signer_identity": "agent:validator",
            "nonce": "acceptance-validator-nonce",
        },
        kind="factory-authority-receipt",
        key_path=validator_key,
        output_path=acceptance_validator_receipt_path,
    )
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
    structural_mode = StructuralModeRecord(
        mode=STRUCTURAL_MODE_ISOLATED,
        decision_package_note=(
            "The synthetic lanes ran fully isolated, so branch-level depth was not purchased."
        ),
    )
    independence = IndependenceRecord(
        agents=(
            AgentIdentity(
                role=ROLE_CODER,
                model_family="synthetic-a",
                model_version="fixture-1",
                directive_version="coder-fixture-1",
            ),
            AgentIdentity(
                role=ROLE_TESTER,
                model_family="synthetic-b",
                model_version="fixture-1",
                directive_version="tester-fixture-1",
            ),
            AgentIdentity(
                role=ROLE_VALIDATOR,
                model_family="synthetic-c",
                model_version="fixture-1",
                directive_version="validator-fixture-1",
            ),
        ),
        shared_context=False,
        channel_open=False,
        claimed_tier=INDEPENDENCE_STRONGER,
        structural_mode=replace(
            structural_mode,
            mutation_evidence=EvidenceIntegrity(
                body=structural_mode.authority_body(),
                claimed_digest=digest_obj(structural_mode.authority_body()),
            ),
        ),
    )
    monitors = (
        Monitor(
            monitor_id="monitor-synthetic-control-plane",
            surface_id="synthetic-control-plane",
            derivation=MONITOR_DERIVATION_SPECIFICATION,
            authorship=MONITOR_AUTHORSHIP_HUMAN,
            author_identity="human:founder",
            backreference=product.backreference(product.items[0]),
            actionable_conclusion="Page the control-plane owner with the unmet criterion.",
            notifies_human=True,
        ),
    )
    orchestrator = FactoryOrchestrator(workflow)
    common_arguments = {
        "target_manifest_path": target_path,
        "pattern_catalog_path": catalog_path,
        "build_plan_path": plan_path,
        "acceptance_catalog_path": acceptance_catalog_path,
        "acceptance_catalog_human_receipt_path": acceptance_human_receipt_path,
        "acceptance_catalog_validator_receipt_path": acceptance_validator_receipt_path,
        "tester_command": tester_command,
        "validator_command": validator_command,
        "coder_trusted_paths": (RUNTIME_FIXTURES / "coder.py",),
        "tester_trusted_paths": (RUNTIME_FIXTURES / "tester.py",),
        "validator_trusted_paths": (RUNTIME_FIXTURES / "validator.py",),
        "resume_checkpoint_path": resume_checkpoint,
        "expected_resume_checkpoint_digest": digest_obj(resume_checkpoint_document),
        "genesis_path": genesis_path,
        "resume_configuration_sources": {"runner": resume_config},
        "implementer_identity": "agent:coder",
        "tester_identity": "agent:tester",
        "verifier_identity": "agent:validator",
        "verifier_key_path": validator_key,
        "surface_evidence": surface_evidence,
        "determinism_records": determinism_records,
        "lane": LANE_CAPABILITY,
        "independence": independence,
        "monitors": monitors,
        "monitor_declared_unit_count": 75,
    }
    with pytest.raises(OrchestrationError, match="attempt_id"):
        orchestrator.build_and_validate(
            "synthetic-run",
            attempt_id="../escape",
            coder_command=(sys.executable, str(RUNTIME_FIXTURES / "coder.py")),
            **common_arguments,
        )
    assert workflow.store.load("synthetic-run").state == RunState.OPERATIONAL_MATURITY_RATIFIED

    repair_supervisor = RepairSupervisor(
        workflow,
        validator_identity="agent:validator",
        validator_key_path=validator_key,
        policy=RepairPolicy(max_attempts=2, max_elapsed_seconds=120),
    )
    attempted: list[tuple[str, Path | None]] = []
    attempt_outcomes: list[BuildOutcome] = []

    def run_attempt(attempt_id: str, repair_brief_path: Path | None) -> BuildOutcome:
        attempted.append((attempt_id, repair_brief_path))
        coder_command = (
            (sys.executable, str(RUNTIME_FIXTURES / "coder.py"), "--broken")
            if repair_brief_path is None
            else (sys.executable, str(RUNTIME_FIXTURES / "coder.py"))
        )
        attempt_outcome = orchestrator.build_and_validate(
            "synthetic-run",
            attempt_id=attempt_id,
            coder_command=coder_command,
            repair_brief_path=repair_brief_path,
            **common_arguments,
        )
        attempt_outcomes.append(attempt_outcome)
        return attempt_outcome

    def diagnose_failed_attempt(
        failed_outcome: BuildOutcome,
        *,
        predecessor_ledger_head: str,
        phase_artifact_digests: Mapping[str, str],
    ) -> RepairPlan:
        plan = RepairPlan(
            summary="Implement the ratified integer-addition behavior.",
            actions=("Return the sum required by the product specification.",),
            intent_backreferences=(product_reference, architecture_reference),
            failure_signature="integer-addition-behavior-mismatch",
        )
        malicious = RepairBrief(
            run_id="synthetic-run",
            failed_attempt_id="attempt-failed",
            authorized_attempt_id="attempt-malicious",
            predecessor_ledger_head=predecessor_ledger_head,
            phase_artifact_digests=phase_artifact_digests,
            candidate_digest=failed_outcome.candidate_digest,
            oracle_digest=failed_outcome.tests_digest,
            plan=plan,
        )
        malicious_envelope = cli.wrap_json(
            malicious.document(),
            kind="factory-repair-brief",
            key_path=coder_key,
            output_path=tmp_path / "coder-self-diagnosis.tessera.json",
        )
        with pytest.raises(WorkflowError, match="Validator of the causal failed attempt"):
            workflow.record_repair_brief(
                "synthetic-run",
                expected_ledger_head=predecessor_ledger_head,
                brief_digest=malicious.digest,
                envelope=malicious_envelope,
                validator_identity="agent:coder",
            )
        return plan

    record_repair_brief = workflow.record_repair_brief

    def crash_after_envelope_publication(*args: object, **kwargs: object):
        if kwargs.get("validator_identity") == "agent:validator":
            raise WorkflowError("injected crash after repair envelope publication")
        return record_repair_brief(*args, **kwargs)

    monkeypatch.setattr(workflow, "record_repair_brief", crash_after_envelope_publication)
    with pytest.raises(WorkflowError, match="injected crash"):
        repair_supervisor.run(
            "synthetic-run",
            initial_attempt_id="attempt-failed",
            next_attempt_id=lambda _index: "attempt-1",
            attempt_runner=run_attempt,
            validator_diagnose=diagnose_failed_attempt,
        )

    orphaned_envelopes = tuple(
        (workflow.root / "synthetic-run" / "evidence" / "repair-briefs").glob("*.tessera.json")
    )
    assert len(orphaned_envelopes) == 1
    assert workflow.store.load("synthetic-run").state == RunState.BLOCKED

    monkeypatch.setattr(workflow, "record_repair_brief", record_repair_brief)
    recovery_supervisor = RepairSupervisor(
        workflow,
        validator_identity="agent:validator",
        validator_key_path=validator_key,
        policy=RepairPolicy(max_attempts=1, max_elapsed_seconds=120),
    )
    result = recovery_supervisor.run(
        "synthetic-run",
        initial_attempt_id="attempt-1",
        next_attempt_id=lambda _index: "unused-attempt",
        attempt_runner=run_attempt,
        validator_diagnose=lambda *_args, **_kwargs: pytest.fail(
            "a recovered brief must make the retry pass without another diagnosis"
        ),
        initial_repair_brief_path=orphaned_envelopes[0],
    )
    assert attempted[0] == ("attempt-failed", None)
    assert attempted[1][0] == "attempt-1"
    assert attempted[1][1] == result.repair_brief_paths[0]
    assert result.attempts_run == 1
    assert result.terminal_reason == "preview"
    assert result.projection.state == RunState.PREVIEW
    outcome = attempt_outcomes[-1]
    assert outcome.passed is True
    assert outcome.repair_signal == "pass"
    assert outcome.projection == result.projection
    assert outcome.evidence_report is not None
    assert outcome.evidence_report.provenance.satisfied is True
    assert outcome.evidence_report.checklist.satisfied is True
    final_attempt_root = (
        workflow.root / "synthetic-run" / "evidence" / "build-attempts" / "attempt-1"
    )
    assert (final_attempt_root, workflow.root) in preview_syncs
    coder_evidence = json.loads(
        (final_attempt_root / "coder" / "output" / "evidence" / "lane-evidence.json").read_text(
            encoding="utf-8"
        )
    )
    assert coder_evidence["repair_brief_present"] is True
    preview = workflow.store.load("synthetic-run")
    assert preview == result.projection
    evidence = outcome.evidence_report.document
    assert evidence["schema_version"] == "factory-evidence-bundle/3"
    assert (
        evidence["preview_admission"]["artifact_digests"]["validator-adversarial-review"]
        == outcome.adversarial_review_digest
    )
    assert evidence["build_attempt"] == {"number": 2, "limit": 2}
    assert set(evidence["generation_artifacts"]) == {
        "target-manifest-source",
        "pattern-catalog",
        "pattern-catalog-source",
        "build-plan",
        "build-plan-source",
        "build-input",
        "generation-readiness",
    }
    assert set(evidence["review_snapshots"]) == {
        "coder-output",
        "tester-output",
    }
    assert outcome.evidence_envelope is not None
    assert outcome.evidence_envelope.public_key == validator_public_key
    cli.verify_json(
        outcome.evidence_envelope.path,
        trusted_public_keys=(validator_public_key,),
        expected_kind="factory-evidence-bundle",
        expected_payload_digest=outcome.evidence_envelope.payload_digest,
    )

    # The source workspaces may change after review. Evidence consumption continues from the
    # retained bytes, and the exact Coder tree the Validator reviewed remains reproducible.
    plan_path.write_text("{}", encoding="utf-8")
    candidate_path = outcome.execution.coder.output_directory / "artifact" / "calculator.py"
    candidate_path.write_text("raise RuntimeError('later mutation')\n", encoding="utf-8")
    verify_prepared_generation(tmp_path / "runs", outcome.projection)
    assert outcome.execution.coder_snapshot is not None
    coder_snapshot = verify_frozen_tree(
        outcome.execution.coder_snapshot.directory,
        expected_digest=outcome.execution.coder_snapshot.digest,
    )
    assert tree_digest(coder_snapshot.files_directory / "artifact") == outcome.candidate_digest

    # The shared shell loader must reopen a PREVIEW run through the same externally pinned
    # genesis/root/Tessera tuple used by the resume gate.  A structural RunStore can replay
    # intake, but it must fail closed at PREVIEW because the retained evidence envelope requires
    # authenticated signer verification.
    resume_config_manifest = tmp_path / "resume-config.manifest"
    resume_config_manifest.write_text(
        f"runner={resume_config.resolve()}\n",
        encoding="utf-8",
    )
    repository_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.update(
        {
            "FACTORY_CLI": f"{sys.executable} -m factory_runtime.cli",
            "FACTORY_RESUME_CHECKPOINT": str(resume_checkpoint),
            "FACTORY_RESUME_CHECKPOINT_DIGEST": digest_obj(resume_checkpoint_document),
            "FACTORY_GENESIS": str(genesis_path),
            "FACTORY_ROOT_PUBLIC_KEY": root_public_key,
            "FACTORY_TESSERA_BIN": str(binary.resolve(strict=True)),
            "FACTORY_RESUME_CONFIG_MANIFEST": str(resume_config_manifest),
            "PYTHONPATH": os.pathsep.join(
                filter(
                    None,
                    (str(repository_root), environment.get("PYTHONPATH", "")),
                )
            ),
        }
    )
    post_preview_resource = tmp_path / "post-preview-resource"
    post_preview_resource.mkdir()
    harness_replay = subprocess.run(
        [
            "/bin/bash",
            "-c",
            'set -euo pipefail; source "$1"; factory_load_context "$2" "$3"; '
            'factory_record_resource --runs "$3" --run-id "$2" '
            "--resource-id post-preview-proof --resource-type object-store "
            '--identifier "$4" --creator-action integration-test --ownership run-owned '
            "--baseline-json '{\"absent_at_plan\":true}' --status planned "
            "--actor integration-test >/dev/null; "
            'factory_disposition_resource --runs "$3" --run-id "$2" '
            "--resource-id post-preview-proof --status failed "
            '--reason "injected disposition for authenticated replay proof" --residue true '
            "--actor integration-test >/dev/null; "
            'factory_disposition_resource --runs "$3" --run-id "$2" '
            "--resource-id post-preview-proof --status retained "
            '--reason "retained authenticated replay proof" --residue true '
            "--actor integration-test >/dev/null; "
            'printf "%s\\n" "$FACTORY_RUN_STATE"',
            "factory-preview-replay",
            str(repository_root / "harness" / "run_context.sh"),
            "synthetic-run",
            str(workflow.root),
            str(post_preview_resource),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert harness_replay.returncode == 0, harness_replay.stderr
    assert harness_replay.stdout.strip() == "preview"
    retained_resource = ResourceLedger(
        workflow.root / "synthetic-run",
        "synthetic-run",
    ).latest()["post-preview-proof"]
    assert retained_resource["status"] == "retained"
    assert retained_resource["identifier"] == str(post_preview_resource)
