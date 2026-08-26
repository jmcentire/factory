"""Test configuration — make the repo root importable without requiring an install.

Inserting the repo root on ``sys.path`` lets ``import factory_core`` (and importing the
``scripts`` guard) work whether or not the package has been pip-installed, so ``make test``
runs from a bare checkout.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SYNTHETIC_TARGET = FIXTURES / "synthetic_target" / "target.toml"
EMPTY_GIT_TREE_SHA1 = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
SYNTHETIC_CANDIDATE_BYTES = b"fixture candidate\n"
SYNTHETIC_TEST_BYTES = b"def test_fixture_acceptance():\n    assert True\n"
_FIXTURE_EVIDENCE_KEY = b"factory-state-fixture-evidence-verifier-v1"


def _fixture_signer_public_key(identity: str) -> str:
    return hashlib.sha256(f"fixture-evidence-signer:{identity}".encode()).hexdigest()


class FixtureEvidenceEnvelopeVerifier:
    """Explicit authenticated unit seam; real Tessera is exercised by integration tests."""

    def verify(
        self,
        envelope_path: Path,
        *,
        expected_kind: str,
        expected_payload_digest: str,
        expected_envelope_digest: str,
        expected_signer_identity: str,
        expected_authority_genesis_digest: str,
    ) -> Any:
        from factory_core.manifest import digest_bytes, digest_obj
        from factory_runtime.evidence_plane import (
            EvidencePlaneError,
            EvidenceVerificationReceipt,
            VerifiedEvidenceEnvelope,
        )

        try:
            raw = envelope_path.read_bytes()
            document = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise EvidencePlaneError(f"fixture evidence envelope is unreadable: {exc}") from exc
        if digest_bytes(raw) != expected_envelope_digest:
            raise EvidencePlaneError("fixture evidence envelope differs from its address")
        if not isinstance(document, dict):
            raise EvidencePlaneError("fixture evidence envelope must be an object")
        signature = str(document.pop("fixture_mac", ""))
        authenticated = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        expected_mac = hmac.new(
            _FIXTURE_EVIDENCE_KEY,
            authenticated,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_mac):
            raise EvidencePlaneError("fixture evidence MAC is invalid")
        if document.get("kind") != expected_kind:
            raise EvidencePlaneError("fixture evidence kind differs")
        if document.get("signer_identity") != expected_signer_identity:
            raise EvidencePlaneError("fixture evidence signer identity differs")
        if document.get("authority_genesis_digest") != expected_authority_genesis_digest:
            raise EvidencePlaneError("fixture evidence authority genesis differs")
        payload = document.get("payload")
        if not isinstance(payload, dict) or digest_obj(payload) != expected_payload_digest:
            raise EvidencePlaneError("fixture evidence payload differs")
        signer_public_key = _fixture_signer_public_key(expected_signer_identity)
        if document.get("signer_public_key") != signer_public_key:
            raise EvidencePlaneError("fixture evidence signer key differs")
        return VerifiedEvidenceEnvelope(
            payload=payload,
            receipt=EvidenceVerificationReceipt(
                schema_version="factory-evidence-verification-receipt/1",
                verifier_id="factory-test-evidence-verifier/1",
                authority_genesis_digest=expected_authority_genesis_digest,
                signer_identity=expected_signer_identity,
                signer_public_key=signer_public_key,
                envelope_digest=expected_envelope_digest,
                payload_digest=expected_payload_digest,
            ),
        )


def fixture_preview_evidence_verifier() -> FixtureEvidenceEnvelopeVerifier:
    return FixtureEvidenceEnvelopeVerifier()


def synthetic_candidate_digest() -> str:
    """Address the one-file candidate used by state-machine review fixtures."""

    from factory_core.manifest import digest_bytes, digest_obj

    return digest_obj(
        {
            "files": [
                {
                    "path": "artifact.py",
                    "mode": 0o444,
                    "digest": digest_bytes(SYNTHETIC_CANDIDATE_BYTES),
                }
            ]
        }
    )


def ratification_receipts(phase: str) -> dict[str, str]:
    """The two receipt digests a `*-ratified` transition must name, for a test driving the store.

    `RunStore.transition` refuses a ratification that does not name a human receipt and a distinct
    Validator receipt (`factory_runtime.state._require_ratification_receipts`), so a test that walks
    the states directly has to supply them. Derived from the phase name so every value is distinct
    from every other and from any artifact digest — the store checks distinctness, and a helper that
    handed back one constant would defeat the check it exists to satisfy.

    These are stand-in digests, NOT verified receipts. Only `WorkflowEngine.ratify_phase` verifies a
    receipt; see `tests/test_runtime_workflow.py` and the real-Tessera integration test for that.
    """
    return {
        f"{phase}:{role}-receipt": "sha256:"
        + hashlib.sha256(f"{phase}:{role}-receipt".encode()).hexdigest()
        for role in ("human", "validator")
    }


def fixture_phase_artifacts() -> dict[str, dict[str, Any]]:
    """Canonical phase authority used by store-level end-to-end fixtures."""

    from factory_core.manifest import digest_obj

    statements = {
        "product-specification": "The fixture product exposes its requested behavior.",
        "architecture": "The fixture uses one focused implementation boundary.",
        "operational-maturity": "A retained acceptance oracle verifies the fixture behavior.",
    }
    return {
        phase: {
            "artifact_id": f"fixture-{phase}",
            "phase": phase,
            "version": "1",
            "source_digest": digest_obj({"fixture-phase-source": phase}),
            "human_ratifier": "human-approver",
            "validator_ratifier": "validator",
            "items": [
                {
                    "item_id": f"{phase}:fixture",
                    "canonical_statement": statement,
                    "supersedes": [],
                }
            ],
        }
        for phase, statement in statements.items()
    }


def fixture_phase_artifact_digests() -> dict[str, str]:
    from factory_core.manifest import digest_obj

    return {phase: digest_obj(document) for phase, document in fixture_phase_artifacts().items()}


def retain_fixture_execution_request(
    store: Any,
    *,
    run_id: str,
    target_digest: str,
) -> str:
    """Retain one canonical Stage-E request and return its semantic address."""

    from factory_core.manifest import digest_bytes, digest_obj

    projection = store.load(run_id)
    verbatim_request = "Build the exact ratified fixture behavior and prove it."
    request = {
        "schema_version": "factory-execution-request/1",
        "request_id": f"{run_id}-fixture-request",
        "run_id": run_id,
        "repository_id": str(projection.target_state["repository_id"]),
        "generation": projection.generation,
        "target_manifest_digest": target_digest,
        "target_state_digest": projection.target_state_digest,
        "resolved_commit": str(projection.target_state["resolved_commit"]),
        "proposed_by": "human-approver",
        "verbatim_request": verbatim_request,
        "verbatim_request_digest": digest_bytes(verbatim_request.encode("utf-8")),
        "requested_outcome": "The fixture behavior is implemented and acceptance-tested.",
        "surfaces": [
            {
                "surface_id": "fixture",
                "proposed_criticality": "critical",
                "reason": "It is the sole requested fixture behavior.",
            }
        ],
        "created_at": 1,
    }
    request_bytes = (json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n").encode()
    destination = store.root / run_id / "evidence" / "intake" / "execution-request.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(request_bytes)
    return digest_obj(request)


def standin_test_change_authorization_artifacts(seed: str = "default") -> dict[str, str]:
    """Stand-in artifact and dual receipts for RunStore admissibility tests."""

    subject = "test-change-authorization"
    return {
        key: "sha256:" + hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()
        for key in (
            subject,
            f"{subject}:human-receipt",
            f"{subject}:validator-receipt",
        )
    }


def generation_artifacts(
    seed: str = "default",
    *,
    include_acceptance_catalog: bool = True,
) -> dict[str, str]:
    """Complete stand-in generation tuple for state-machine unit tests.

    These values exercise ledger admissibility only. Runtime generation tests create and verify
    real retained target/catalog/plan/input bytes before using the same transition.
    """

    from factory_runtime.state import GENERATION_ARTIFACT_KEYS

    artifacts = {
        key: "sha256:" + hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()
        for key in GENERATION_ARTIFACT_KEYS
    }
    artifacts["resume-checkpoint"] = (
        "sha256:" + hashlib.sha256(f"{seed}:resume-checkpoint".encode()).hexdigest()
    )
    if include_acceptance_catalog:
        artifacts.update(
            {
                key: "sha256:" + hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()
                for key in (
                    "acceptance-obligation-catalog",
                    "acceptance-obligation-catalog:human-receipt",
                    "acceptance-obligation-catalog:validator-receipt",
                )
            }
        )
    return artifacts


def retained_generation_artifacts(
    store: Any,
    seed: str = "default",
    *,
    run_id: str = "run-1",
    include_acceptance_catalog: bool = True,
) -> dict[str, str]:
    """Retain the exact generation blobs later cited by a Validator review fixture."""

    from factory_core.manifest import digest_obj
    from factory_runtime.snapshot import freeze_blob

    root = store.root / run_id / "evidence" / "generation"

    def retain(label: str, document: object) -> tuple[str, str]:
        data = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        blob = freeze_blob(
            root,
            label=label,
            data=data,
            durable_through=store.root / run_id,
        )
        return digest_obj(document), blob.digest

    pattern_digest, pattern_source = retain(
        "pattern-catalog",
        {"schema_version": "fixture-pattern-catalog/1", "seed": seed},
    )
    plan_digest, plan_source = retain(
        "build-plan",
        {"schema_version": "fixture-build-plan/1", "seed": seed},
    )
    projection = store.load(run_id)
    phase_documents = fixture_phase_artifacts()
    phase_digests = fixture_phase_artifact_digests()
    if dict(projection.phase_artifact_digests) != phase_digests:
        raise AssertionError("store fixture ratification differs from canonical phase authority")
    _, build_input = retain(
        "build-input",
        {
            "schema_version": "factory-build-input/1",
            "run_id": run_id,
            "target_digest": projection.target_digest,
            "phase_artifacts": [
                phase_documents[phase]
                for phase in (
                    "product-specification",
                    "architecture",
                    "operational-maturity",
                )
            ],
        },
    )
    _, target_source = retain(
        "target-manifest-source",
        {"schema_version": "fixture-target-source/1", "seed": seed},
    )
    _, readiness = retain(
        "generation-readiness",
        {"schema_version": "fixture-generation-readiness/1", "seed": seed},
    )
    artifacts = {
        "target-manifest-source": target_source,
        "pattern-catalog": pattern_digest,
        "pattern-catalog-source": pattern_source,
        "build-plan": plan_digest,
        "build-plan-source": plan_source,
        "build-input": build_input,
        "generation-readiness": readiness,
        "resume-checkpoint": "sha256:"
        + hashlib.sha256(f"{seed}:resume-checkpoint".encode()).hexdigest(),
    }
    if include_acceptance_catalog:
        artifacts.update(acceptance_catalog_artifacts(store, run_id=run_id))
    return artifacts


def build_payload(
    *,
    attempt_number: int = 1,
    attempt_limit: int = 1,
    seed: str = "default",
    activate_catalog: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    """First-build payload including the two stand-in catalog-ratification nonces."""

    payload: dict[str, Any] = {
        "attempt_id": f"{seed}-attempt-{attempt_number}",
        "attempt_number": attempt_number,
        "attempt_limit": attempt_limit,
        "resume_checkpoint_id": f"{seed}-checkpoint",
        "anchored_run_ledger_head": "sha256:"
        + hashlib.sha256(f"{seed}:anchored-run-ledger".encode()).hexdigest(),
        "anchored_run_ledger_length": 1,
        **extra,
    }
    authority_nonces: list[str] = []
    if activate_catalog:
        authority_nonces.extend(
            [
                f"{seed}-acceptance-catalog-human-nonce",
                f"{seed}-acceptance-catalog-validator-nonce",
            ]
        )
    if extra.get("changed_existing_tests"):
        authority_nonces.extend(
            [
                f"{seed}-test-change-human-nonce",
                f"{seed}-test-change-validator-nonce",
            ]
        )
    if authority_nonces:
        payload["authority_receipt_nonces"] = authority_nonces
    return payload


def acceptance_catalog_artifacts(
    store: Any,
    *,
    run_id: str = "run-1",
    command: tuple[str, ...] | None = None,
) -> dict[str, str]:
    """Retain a real minimal catalog for state-machine tests that later enter preview.

    Workflow and Tessera integration tests prove signatures and provenance.  State tests need
    actual content-addressed bytes because ledger re-derivation now reopens the catalog/report;
    the receipt values remain explicit stand-ins at this lower layer.
    """

    from factory_core.manifest import digest_obj
    from factory_runtime.acceptance_obligations import (
        AcceptanceObligationCatalog,
        validator_execution_digests,
    )

    projection = store.load(run_id)
    selected_command = command or (sys.executable,)
    command_digest, configuration_digest, environment_digest = validator_execution_digests(
        selected_command
    )
    assertion_digest = digest_obj(
        {"test_id": "fixture-acceptance", "expectation": "fixture passes"}
    )
    document = {
        "schema_version": "factory-acceptance-obligation-catalog/1",
        "catalog_id": "fixture-acceptance-catalog",
        "version": "1",
        "run_id": run_id,
        "generation": projection.generation,
        "target_state_digest": projection.target_state_digest,
        "phase_artifact_digests": dict(projection.phase_artifact_digests),
        "human_ratifier": "human-approver",
        "validator_ratifier": "validator",
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
                        "obligation_id": "fixture-acceptance",
                        "criterion": "The exact fixture acceptance test passes.",
                        "verifier_id": "validator-test-execution-v1",
                        "intent_backreferences": [
                            {
                                "artifact_id": "fixture-product-specification",
                                "artifact_digest": projection.phase_artifact_digests[
                                    "product-specification"
                                ],
                                "item_id": "fixture-criterion",
                                "intent_digest": digest_obj(
                                    {"canonical_statement": "fixture passes"}
                                ),
                            }
                        ],
                        "required_evidence_ids": [
                            "candidate",
                            "acceptance-tests",
                            "coder-output-snapshot",
                            "tester-output-snapshot",
                        ],
                        "test_assertions": [
                            {
                                "test_id": "fixture-acceptance",
                                "assertion_digest": assertion_digest,
                            }
                        ],
                    }
                ],
            }
        ],
    }
    catalog = AcceptanceObligationCatalog.from_dict(document)
    directory = (
        store.root
        / run_id
        / "evidence"
        / "acceptance-obligation-catalogs"
        / catalog.content_digest.removeprefix("sha256:")
    )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "catalog.json").write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return {
        "acceptance-obligation-catalog": catalog.content_digest,
        "acceptance-obligation-catalog:human-receipt": "sha256:"
        + hashlib.sha256(f"{catalog.content_digest}:human".encode()).hexdigest(),
        "acceptance-obligation-catalog:validator-receipt": "sha256:"
        + hashlib.sha256(f"{catalog.content_digest}:validator".encode()).hexdigest(),
    }


def validator_execution_artifacts(
    store: Any,
    *,
    run_id: str = "run-1",
) -> dict[str, str]:
    """Retain the exact Validator executable behind the active build attempt."""

    from factory_runtime.acceptance_obligations import capture_validator_execution
    from factory_runtime.snapshot import freeze_blob, freeze_tree

    entries = store.verified_ledger_entries(run_id)
    building = [entry for entry in entries if entry.get("to_state") == "building"]
    if not building:
        raise AssertionError("validation fixture requires a BUILDING attempt")
    payload = building[-1].get("payload")
    if not isinstance(payload, dict):
        raise AssertionError("BUILDING fixture has no payload")
    attempt_id = str(payload["attempt_id"])
    capture = capture_validator_execution((sys.executable,))
    execution_root = (
        store.root / run_id / "evidence" / "build-attempts" / attempt_id / "validator-execution"
    )
    manifest = freeze_blob(
        execution_root / "manifests",
        label="validator-execution-manifest",
        data=json.dumps(capture.document, sort_keys=True, separators=(",", ":")).encode(),
        durable_through=store.root / run_id,
    )
    with tempfile.TemporaryDirectory(prefix="factory-state-validator-") as temporary:
        source = Path(temporary)
        for item in capture.files:
            path = source / item.snapshot_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(item.content)
            os.chmod(path, item.mode)
        validator_snapshot = freeze_tree(
            source,
            execution_root / "trees",
            durable_through=store.root / run_id,
        )
    if manifest.digest != capture.command_digest:
        raise AssertionError("Validator fixture manifest did not retain at its command address")
    if validator_snapshot.digest != capture.document["snapshot_tree_digest"]:
        raise AssertionError("Validator fixture tree differs from the captured execution bytes")
    command_digest, configuration_digest, environment_digest = capture.digests
    return {
        "validator-execution-manifest": command_digest,
        "validator-execution-configuration": configuration_digest,
        "validator-execution-environment": environment_digest,
        "validator-execution-snapshot": validator_snapshot.digest,
    }


def validation_artifacts(
    store: Any,
    seed: str = "default",
    *,
    run_id: str = "run-1",
    candidate: str | None = None,
) -> dict[str, str]:
    """Retain exact author trees and the exact Validator execution identity."""

    del seed
    from factory_runtime.snapshot import freeze_tree, tree_digest

    review_root = store.root / run_id / "evidence" / "review-snapshots"
    with tempfile.TemporaryDirectory(prefix="factory-state-authors-") as temporary:
        source = Path(temporary)
        coder = source / "coder" / "artifact"
        tester = source / "tester" / "tests"
        coder.mkdir(parents=True)
        tester.mkdir(parents=True)
        (coder / "artifact.py").write_bytes(SYNTHETIC_CANDIDATE_BYTES)
        (tester / "acceptance_test.py").write_bytes(SYNTHETIC_TEST_BYTES)
        os.chmod(coder / "artifact.py", 0o444)
        os.chmod(tester / "acceptance_test.py", 0o444)
        coder_snapshot = freeze_tree(
            source / "coder",
            review_root,
            durable_through=store.root / run_id,
        )
        tester_snapshot = freeze_tree(
            source / "tester",
            review_root,
            durable_through=store.root / run_id,
        )

    candidate_digest = tree_digest(coder_snapshot.files_directory / "artifact")
    acceptance_tests_digest = tree_digest(tester_snapshot.files_directory / "tests")
    if candidate is not None and candidate != candidate_digest:
        raise AssertionError("validation fixture candidate differs from retained Coder bytes")
    return {
        "candidate": candidate_digest,
        "acceptance-tests": acceptance_tests_digest,
        "coder-output-snapshot": coder_snapshot.digest,
        "tester-output-snapshot": tester_snapshot.digest,
        **validator_execution_artifacts(store, run_id=run_id),
    }


def preview_artifacts(
    store: Any,
    seed: str = "default",
    *,
    run_id: str = "run-1",
    candidate: str | None = None,
    reviewer_identity: str = "validator",
) -> dict[str, str]:
    """Retain freshly re-derived acceptance and adversarial-review evidence for preview."""

    from factory_core.manifest import digest_bytes, digest_obj
    from factory_runtime.acceptance_obligations import (
        AcceptanceObligationCatalog,
        derive_acceptance_obligation_report,
        retain_acceptance_obligation_report,
    )
    from factory_runtime.adversarial_review import (
        REQUIRED_COMPLETENESS_CHECKS,
        REQUIRED_REVIEW_DIMENSIONS,
        build_review_authority_context,
        build_validator_review_subject,
        canonical_document_bytes,
        retain_validator_adversarial_review,
        verify_validator_adversarial_review,
    )
    from factory_runtime.evidence_plane import build_preview_admission
    from factory_runtime.snapshot import verify_frozen_blob, verify_frozen_tree

    projection = store.load(run_id)
    catalog_path = (
        store.root
        / run_id
        / "evidence"
        / "acceptance-obligation-catalogs"
        / projection.acceptance_obligation_catalog_digest.removeprefix("sha256:")
        / "catalog.json"
    )
    catalog = AcceptanceObligationCatalog.from_dict(json.loads(catalog_path.read_text()))
    trigger = catalog.select("validating", "preview")
    obligation = trigger["obligations"][0]
    tests = list(obligation["test_assertions"])
    trusted = {
        key: str(store.current_artifact_digests(run_id)[key])
        for key in (
            "candidate",
            "acceptance-tests",
            "coder-output-snapshot",
            "tester-output-snapshot",
        )
    }
    execution = {
        key: str(store.current_artifact_digests(run_id)[key])
        for key in (
            "validator-execution-manifest",
            "validator-execution-configuration",
            "validator-execution-environment",
            "validator-execution-snapshot",
        )
    }
    test_results = [
        {
            **test,
            "exit_status": 0,
            "output_digest": digest_obj(
                {
                    **test,
                    "exit_status": 0,
                    "candidate_digest": trusted["candidate"],
                    "acceptance_tests_digest": trusted["acceptance-tests"],
                    "command_digest": trigger["command_digest"],
                }
            ),
        }
        for test in tests
    ]
    effect_body = {
        "obligation_id": obligation["obligation_id"],
        "verifier_id": obligation["verifier_id"],
        "candidate_digest": trusted["candidate"],
        "acceptance_tests_digest": trusted["acceptance-tests"],
        "command_digest": trigger["command_digest"],
        "configuration_digest": trigger["configuration_digest"],
        "environment_digest": trigger["environment_digest"],
        "started_at": 100,
        "finished_at": 101,
        "evidence_digests": trusted,
        "test_results": test_results,
    }
    observations = {
        "schema_version": "factory-acceptance-obligation-observations/1",
        "run_id": run_id,
        "generation": projection.generation,
        "catalog_digest": catalog.content_digest,
        "trigger_id": trigger["trigger_id"],
        "candidate_digest": trusted["candidate"],
        "acceptance_tests_digest": trusted["acceptance-tests"],
        "command_digest": trigger["command_digest"],
        "configuration_digest": trigger["configuration_digest"],
        "environment_digest": trigger["environment_digest"],
        "started_at": 100,
        "finished_at": 101,
        "results": [
            {
                "obligation_id": obligation["obligation_id"],
                "verifier_id": obligation["verifier_id"],
                "passed": True,
                "evidence_digests": trusted,
                "test_results": test_results,
                "effect_digest": digest_obj(effect_body),
            }
        ],
    }
    report = derive_acceptance_obligation_report(
        catalog,
        observations=observations,
        run_id=run_id,
        generation=projection.generation,
        source="validating",
        destination="preview",
        target_state_digest=projection.target_state_digest,
        resolved_commit=str(projection.target_state["resolved_commit"]),
        resolved_tree=str(projection.target_state["resolved_tree"]),
        phase_artifact_digests=projection.phase_artifact_digests,
        candidate_digest=trusted["candidate"],
        acceptance_tests_digest=trusted["acceptance-tests"],
        command_digest=trigger["command_digest"],
        configuration_digest=trigger["configuration_digest"],
        environment_digest=trigger["environment_digest"],
        trusted_evidence_digests=trusted,
    )
    report_digest = retain_acceptance_obligation_report(store.root, run_id, report)
    observations_bytes = canonical_document_bytes(observations)
    observations_input = (
        store.root
        / run_id
        / "evidence"
        / "fixture-review-inputs"
        / f"{digest_bytes(observations_bytes).removeprefix('sha256:')}.json"
    )
    observations_input.parent.mkdir(parents=True, exist_ok=True)
    observations_input.write_bytes(observations_bytes)
    catalog_source_digest = digest_bytes(catalog_path.read_bytes())
    snapshot_body = {
        "schema_version": "factory-base-source-snapshot/1",
        "resolved_commit": str(projection.target_state["resolved_commit"]),
        "resolved_tree": str(projection.target_state["resolved_tree"]),
        "hash_algorithm": "sha1",
        "subpath": "",
        "files": [],
    }
    base_source_snapshot = {
        **snapshot_body,
        "snapshot_digest": digest_obj(snapshot_body),
    }
    change_body = {
        "schema_version": "factory-candidate-change-set/1",
        "resolved_commit": str(projection.target_state["resolved_commit"]),
        "resolved_tree": str(projection.target_state["resolved_tree"]),
        "subpath": "",
        "construction_mode": "regenerate",
        "baseline_snapshot_digest": base_source_snapshot["snapshot_digest"],
        "candidate_digest": trusted["candidate"],
        "changed_path_digest": digest_obj(["artifact.py"]),
        "changes": [
            {
                "path": "artifact.py",
                "kind": "added",
                "old_type": None,
                "new_type": "file",
                "old_mode": None,
                "new_mode": 0o444,
                "old_digest": None,
                "new_digest": digest_bytes(SYNTHETIC_CANDIDATE_BYTES),
            }
        ],
    }
    candidate_change_set = {
        **change_body,
        "change_set_digest": digest_obj(change_body),
    }
    execution_request_path = store.root / run_id / "evidence" / "intake" / "execution-request.json"
    execution_request_bytes = execution_request_path.read_bytes()
    execution_request = json.loads(execution_request_bytes)
    execution_request_digest = str(store.execution_authority_digests(run_id)["execution-request"])
    if digest_obj(execution_request) != execution_request_digest:
        raise AssertionError("fixture Stage-E request differs from the run authority")
    generation_root = store.root / run_id / "evidence" / "generation"

    def generation_blob(label: str, digest: str) -> Path:
        return verify_frozen_blob(
            generation_root / label / digest.removeprefix("sha256:"),
            expected_digest=digest,
            label=label,
        ).payload_path

    build_input_path = generation_blob(
        "build-input", str(projection.generation_artifact_digests["build-input"])
    )
    build_input = json.loads(build_input_path.read_bytes())
    checkpoint = {
        "run_id": run_id,
        "seed": seed,
        "execution_request_digest": execution_request_digest,
    }
    checkpoint_bytes = (
        json.dumps(checkpoint, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    configuration_bytes = b'{"fixture":true}\n'
    authority_context = build_review_authority_context(
        resume_checkpoint_digest=digest_obj(checkpoint),
        resume_checkpoint_source_digest=digest_bytes(checkpoint_bytes),
        resume_checkpoint_bytes=checkpoint_bytes,
        configuration_sources={"state-fixture": configuration_bytes},
        expected_configuration_digests={"state-fixture": digest_bytes(configuration_bytes)},
        changed_existing_tests=(),
        test_change_artifacts={},
        test_change_sources={},
    )
    subject = build_validator_review_subject(
        run_id=run_id,
        generation=projection.generation,
        target_digest=projection.target_digest,
        target_state_digest=projection.target_state_digest,
        resolved_commit=str(projection.target_state["resolved_commit"]),
        resolved_tree=str(projection.target_state["resolved_tree"]),
        reviewer_identity=reviewer_identity,
        base_source_snapshot=base_source_snapshot,
        candidate_change_set=candidate_change_set,
        authority_context=authority_context,
        execution_request_bytes=execution_request_bytes,
        build_input=build_input,
        build_input_digest=str(projection.generation_artifact_digests["build-input"]),
        pattern_catalog_digest=str(projection.generation_artifact_digests["pattern-catalog"]),
        pattern_catalog_source_digest=str(
            projection.generation_artifact_digests["pattern-catalog-source"]
        ),
        build_plan_digest=str(projection.generation_artifact_digests["build-plan"]),
        build_plan_source_digest=str(projection.generation_artifact_digests["build-plan-source"]),
        phase_artifact_digests=projection.phase_artifact_digests,
        acceptance_obligation_catalog_digest=catalog.content_digest,
        acceptance_obligation_catalog_source_digest=catalog_source_digest,
        candidate_digest=trusted["candidate"],
        acceptance_tests_digest=trusted["acceptance-tests"],
        coder_output_snapshot_digest=trusted["coder-output-snapshot"],
        tester_output_snapshot_digest=trusted["tester-output-snapshot"],
        command_digest=str(report["command_digest"]),
        configuration_digest=str(report["configuration_digest"]),
        environment_digest=str(report["environment_digest"]),
    )

    review_root = store.root / run_id / "evidence" / "review-snapshots"
    coder_snapshot = verify_frozen_tree(
        review_root / trusted["coder-output-snapshot"].removeprefix("sha256:"),
        expected_digest=trusted["coder-output-snapshot"],
    )
    tester_snapshot = verify_frozen_tree(
        review_root / trusted["tester-output-snapshot"].removeprefix("sha256:"),
        expected_digest=trusted["tester-output-snapshot"],
    )
    pattern_catalog_path = generation_blob(
        "pattern-catalog",
        str(projection.generation_artifact_digests["pattern-catalog-source"]),
    )
    build_plan_path = generation_blob(
        "build-plan", str(projection.generation_artifact_digests["build-plan-source"])
    )
    evidence_bytes = {
        ("implementation", "artifact.py"): SYNTHETIC_CANDIDATE_BYTES,
        ("acceptance-tests", "acceptance_test.py"): SYNTHETIC_TEST_BYTES,
        ("build-input", "build-input.json"): build_input_path.read_bytes(),
        ("pattern-catalog", "pattern-catalog.json"): pattern_catalog_path.read_bytes(),
        ("build-plan", "build-plan.json"): build_plan_path.read_bytes(),
        (
            "acceptance-obligation-catalog",
            "acceptance-obligation-catalog.json",
        ): catalog_path.read_bytes(),
        (
            "acceptance-observations",
            "acceptance-obligation-observations.json",
        ): observations_bytes,
        (
            "candidate-change-set",
            "candidate-change-set.json",
        ): canonical_document_bytes(candidate_change_set),
        (
            "review-authority-context",
            "review-authority-context.json",
        ): canonical_document_bytes(authority_context),
        ("operator-intent", "execution-request.json"): execution_request_bytes,
    }

    def review_reference(source: str, path: str) -> dict[str, object]:
        data = evidence_bytes[(source, path)]
        first_line = data.splitlines(keepends=True)[0]
        return {
            "source": source,
            "path": path,
            "start_line": 1,
            "end_line": 1,
            "excerpt_digest": digest_bytes(first_line),
        }

    references = [
        review_reference("implementation", "artifact.py"),
        review_reference("acceptance-tests", "acceptance_test.py"),
        review_reference("build-input", "build-input.json"),
        review_reference("pattern-catalog", "pattern-catalog.json"),
        review_reference("build-plan", "build-plan.json"),
        review_reference("acceptance-obligation-catalog", "acceptance-obligation-catalog.json"),
        review_reference("acceptance-observations", "acceptance-obligation-observations.json"),
        review_reference("candidate-change-set", "candidate-change-set.json"),
        review_reference("review-authority-context", "review-authority-context.json"),
        review_reference("operator-intent", "execution-request.json"),
    ]
    requirement_dispositions = [
        {
            **target,
            "disposition": "CONFORMS",
            "summary": "Fixture implementation satisfies the ratified Product item.",
            "evidence": [references[2], references[0]],
            "finding_ids": [],
        }
        for target in subject["review_targets"]["requirements"]
    ]
    architecture_dispositions = [
        {
            **target,
            "disposition": "CONFORMS",
            "summary": "Fixture change set satisfies the ratified Architecture item.",
            "evidence": [references[2], references[7]],
            "finding_ids": [],
        }
        for target in subject["review_targets"]["architecture_items"]
    ]
    operational_maturity_dispositions = [
        {
            **target,
            "disposition": "CONFORMS",
            "summary": "Fixture tests and observations satisfy Operational Maturity.",
            "evidence": [references[2], references[1], references[6]],
            "finding_ids": [],
        }
        for target in subject["review_targets"]["operational_maturity_items"]
    ]
    observed_effect = observations["results"][0]
    observed_test = observed_effect["test_results"][0]
    probe_body = {
        "obligation_id": observed_effect["obligation_id"],
        "verifier_id": observed_effect["verifier_id"],
        "effect_digest": observed_effect["effect_digest"],
        "test_result": {
            "test_id": observed_test["test_id"],
            "assertion_digest": observed_test["assertion_digest"],
            "output_digest": observed_test["output_digest"],
        },
        "probe_method": "inspect-observed-test-result/1",
        "failure_mode": "The fixture candidate could fail its ratified acceptance assertion.",
        "attempt": "Run the exact retained acceptance assertion against the candidate.",
        "expected_result": "The retained assertion completes successfully.",
        "observed_result": "The bound Validator observation records a passing execution.",
        "outcome": "PASSED",
        "evidence": [references[1], references[6]],
        "finding_ids": [],
    }
    challenge_body = {
        "challenge_method": "compare-exact-evidence/1",
        "authority_evidence_index": 0,
        "produced_evidence_index": 1,
        "hypothesis": "The candidate omits the exact operator-requested behavior.",
        "attempt": "Compare the Stage-E request with the complete candidate artifact.",
        "observed_result": "The candidate artifact implements the requested fixture behavior.",
        "outcome": "REFUTED",
        "evidence": [references[9], references[0]],
        "finding_ids": [],
    }
    review_report = {
        "schema_version": "factory-validator-adversarial-review/1",
        "authority": "review-evidence-only",
        "subject_digest": digest_obj(subject),
        "reviewer_identity": reviewer_identity,
        "acceptance_observations_digest": str(report["observations_digest"]),
        "dimensions": [
            {
                "dimension_id": dimension,
                "state": "COMPLETED",
                "summary": f"Fixture reviewed exact evidence for {dimension}.",
                "evidence": (
                    [references[9], references[2], references[0]]
                    if dimension == "intent-conformance"
                    else [references[0]]
                ),
            }
            for dimension in REQUIRED_REVIEW_DIMENSIONS
        ],
        "requirement_dispositions": requirement_dispositions,
        "architecture_dispositions": architecture_dispositions,
        "operational_maturity_dispositions": operational_maturity_dispositions,
        "failure_mode_probes": [{"probe_id": digest_obj(probe_body), **probe_body}],
        "clean_claim_challenges": [{"challenge_id": digest_obj(challenge_body), **challenge_body}],
        "findings": [],
        "completeness": {
            "state": "COMPLETED",
            "summary": "Fixture completed the independent clean-claim challenge.",
            "checks": [
                {
                    "check_id": check_id,
                    "state": "COMPLETED",
                    "summary": f"Fixture completed exact evidence checks for {check_id}.",
                    "evidence": [references[2]],
                }
                for check_id in REQUIRED_COMPLETENESS_CHECKS
            ],
            "evidence": references,
        },
        "verdict": "CLEAN_QUALIFIED",
    }
    verified_review = verify_validator_adversarial_review(
        review_report,
        subject=subject,
        reviewer_identity=reviewer_identity,
        acceptance_observations=observations,
        implementation_root=coder_snapshot.files_directory / "artifact",
        tests_root=tester_snapshot.files_directory / "tests",
        build_input_path=build_input_path,
        pattern_catalog_path=pattern_catalog_path,
        build_plan_path=build_plan_path,
        acceptance_catalog_path=catalog_path,
        acceptance_observations_path=observations_input,
    )
    review_artifacts = retain_validator_adversarial_review(
        store.root,
        run_id,
        verified_review,
    )
    entries = store.verified_ledger_entries(run_id)
    genesis = entries[0]["artifact_digests"]
    validating_entry = entries[-1]
    validating_payload = validating_entry["payload"]
    assert isinstance(genesis, dict)
    assert isinstance(validating_payload, dict)
    preview_admission = build_preview_admission(
        run_schema_version=projection.schema_version,
        run_id=run_id,
        generation=projection.generation,
        validating_ledger_head=projection.ledger_head,
        authority_genesis_digest=str(genesis["authority-genesis"]),
        implementer_identity=str(validating_entry["implementer_identity"]),
        tester_identity=str(validating_payload["tester_identity"]),
        verifier_identity=str(validating_entry["verifier_identity"]),
        artifact_digests={
            "candidate": trusted["candidate"],
            "acceptance-tests": trusted["acceptance-tests"],
            "coder-output-snapshot": trusted["coder-output-snapshot"],
            "tester-output-snapshot": trusted["tester-output-snapshot"],
            "acceptance-obligation-report": report_digest,
            **dict(review_artifacts),
            **execution,
        },
    )
    phase_artifacts = list(fixture_phase_artifacts().values())
    bundle = {
        "schema_version": "factory-evidence-bundle/3",
        "run_id": run_id,
        "target_digest": projection.target_digest,
        "source_digest": projection.source_digest,
        "candidate_digest": trusted["candidate"],
        "acceptance_tests_digest": trusted["acceptance-tests"],
        "generation_artifacts": dict(projection.generation_artifact_digests),
        "review_snapshots": {
            "coder-output": trusted["coder-output-snapshot"],
            "tester-output": trusted["tester-output-snapshot"],
        },
        "build_attempt": {
            "number": projection.build_attempt_count,
            "limit": projection.build_attempt_limit,
        },
        "ledger_head": projection.ledger_head,
        "phase_artifacts": phase_artifacts,
        "trusted_artifact_digests": dict(projection.phase_artifact_digests),
        "preview_admission": preview_admission,
        "claims": [],
        "checklist_results": [
            {
                "id": "fixture-acceptance",
                "passed": True,
                "detail": "Exact fixture acceptance evidence passed.",
                "recorded_at": 1,
                "evidence": {
                    "body": {"candidate_digest": trusted["candidate"]},
                    "claimed_digest": digest_obj({"candidate_digest": trusted["candidate"]}),
                },
            }
        ],
        "surface_evidence": [
            {
                "surface_id": "fixture",
                "criticality": "critical",
                "oracle_adequate": True,
                "required_evidence_ids": ["fixture-acceptance"],
                "evidence_digests": {"fixture-acceptance": report_digest},
            }
        ],
        "determinism_records": [
            {
                "surface_id": "fixture",
                "criticality": "critical",
                "deterministic": True,
                "flake_count": 0,
                "automatic_retry_count": 0,
            }
        ],
        "lane": "capability",
        "independence": {
            "agents": [
                {
                    "role": role,
                    "model_family": "fixture-family",
                    "model_version": "fixture-version",
                    "directive_version": f"{role}-fixture",
                }
                for role in ("coder", "tester", "validator")
            ],
            "shared_context": False,
            "channel_open": False,
            "mechanism_ids": [],
            "claimed_tier": "stronger",
            "derived_tier": "stronger",
            "structural_mode": {
                "mode": "isolated",
                "contract_backreference": None,
                "mutation_evidence": None,
                "decision_package_note": "State fixture only.",
            },
        },
        "monitors": [],
        "monitor_declared_unit_count": 0,
    }
    bundle_digest = digest_obj(bundle)
    envelope = {
        "kind": "factory-evidence-bundle",
        "payload": bundle,
        "payload_digest": bundle_digest,
        "signer_identity": reviewer_identity,
        "signer_public_key": _fixture_signer_public_key(reviewer_identity),
        "authority_genesis_digest": str(genesis["authority-genesis"]),
    }
    authenticated = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    envelope["fixture_mac"] = hmac.new(
        _FIXTURE_EVIDENCE_KEY,
        authenticated,
        hashlib.sha256,
    ).hexdigest()
    envelope_bytes = canonical_document_bytes(envelope)
    building = [
        entry
        for entry in store.verified_ledger_entries(run_id)
        if entry.get("to_state") == "building"
    ]
    attempt_payload = building[-1]["payload"]
    assert isinstance(attempt_payload, dict)
    envelope_path = (
        store.root
        / run_id
        / "evidence"
        / "build-attempts"
        / str(attempt_payload["attempt_id"])
        / "evidence-bundle.tessera.json"
    )
    envelope_path.parent.mkdir(parents=True, exist_ok=True)
    envelope_path.write_bytes(envelope_bytes)
    return {
        "candidate": trusted["candidate"],
        "acceptance-tests": trusted["acceptance-tests"],
        "acceptance-obligation-report": report_digest,
        **dict(review_artifacts),
        **execution,
        "evidence-bundle": bundle_digest,
        "evidence-envelope": digest_bytes(envelope_bytes),
    }


def ci_artifacts(seed: str = "default") -> dict[str, str]:
    """Content-addressed CI result required after human candidate approval."""

    return {"ci-evidence": "sha256:" + hashlib.sha256(f"{seed}:ci-evidence".encode()).hexdigest()}


def create_intake_run(
    store: Any,
    *,
    run_id: str,
    target_digest: str,
    source_digest: str,
) -> Any:
    """Drive a RunStore through the v4 Stage-R/target-state/Stage-E intake boundary.

    Store-level tests do not exercise Git or Tessera; this fixture supplies canonical stand-in
    digests and a schema-valid target-state so those tests still begin at intake without adding a
    production bypass around the two-stage authority model.
    """

    def address(label: str) -> str:
        return "sha256:" + hashlib.sha256(f"{run_id}:{label}".encode()).hexdigest()

    resource_head = address("resource-ledger")
    run_dir = (store.root / run_id).resolve()
    source_root = run_dir / "target" / "source"
    commit = hashlib.sha256(f"{run_id}:commit".encode()).hexdigest()[:40]
    store.create(
        run_id,
        target_digest=target_digest,
        actor="validator",
        artifact_digests={
            "target-resolution-request": address("target-resolution-request"),
            "target-resolution-receipt": address("target-resolution-receipt"),
            "authority-genesis": address("authority-genesis"),
        },
        payload={"authority_receipt_nonces": [f"{run_id}-resolution-nonce"]},
    )
    target_state = {
        "schema_version": "factory-target-state/1",
        "run_id": run_id,
        "repository_id": "fixture",
        "generation": 1,
        "target_id": "fixture",
        "target_manifest_digest": target_digest,
        "requested_url": "https://example.test/repository.git",
        "canonical_url": "https://example.test/repository.git",
        "requested_ref": "refs/heads/main",
        "observed_ref_object": commit,
        "peeled_object": commit,
        "resolved_commit": commit,
        "resolved_tree": EMPTY_GIT_TREE_SHA1,
        "control_root": str(run_dir),
        "object_store": str(run_dir / "target" / "objects.git"),
        "source_root": str(source_root),
        "subpath": "",
        "workdir": str(source_root),
        "checkout_id": address("checkout"),
        "observation_method": "remote",
        "remote_freshness": "PROVED",
        "contact_ledger_head": address("contact-ledger"),
        "resource_ledger_head": resource_head,
        "created_at": 1,
    }
    store.record_target_state(
        run_id,
        target_state=target_state,
        actor="target-resolver",
        artifact_digests={"resource-ledger": resource_head},
    )
    execution_request_digest = retain_fixture_execution_request(
        store,
        run_id=run_id,
        target_digest=target_digest,
    )
    return store.authorize_intake(
        run_id,
        source_digest=source_digest,
        actor="validator",
        artifact_digests={
            "execution-request": execution_request_digest,
            "execution-receipt": address("execution-receipt"),
            "authority-genesis": address("authority-genesis"),
        },
        payload={"authority_receipt_nonces": [f"{run_id}-execution-nonce"]},
        approver_identity="human-approver",
    )


def terminalize_run_resources(store: Any, *, run_id: str) -> str:
    """Give a state-machine unit run one explicitly retained run-owned resource.

    The production resolver creates several real resources.  Store-level tests intentionally do
    not invoke Git, but a successful ``PROMOTED`` transition must still exercise the same
    resource-close precondition instead of acquiring a test-only bypass.  The transition itself
    installs the terminal seal; this helper stops at a closeable ledger head.
    """

    from factory_runtime.resources import ResourceLedger

    ledger = ResourceLedger(store.root / run_id, run_id, clock=lambda: 100)
    identifier = str((store.root / run_id / "fixture-retained-resource").resolve())
    common = {
        "generation": 1,
        "resource_id": "fixture-retained-resource",
        "resource_type": "source-worktree",
        "identifier": identifier,
        "creator_action": "state-machine-test-fixture",
        "ownership": "run-owned",
        "baseline": {"absent_at_plan": True},
        "evidence_digests": {},
        "actor": "fixture",
    }
    ledger.append(**common, disposition={}, status="planned")
    ledger.append(**common, disposition={}, status="active")
    ledger.append(
        **common,
        disposition={"reason": "retained state-machine fixture", "residue": True},
        status="retained",
    )
    return ledger.head()


def _freeze(obj: object) -> object:
    """Serialize a dataclass request/policy/profile to the dict shape ``from_dict`` reads.

    Shared by the Gate L translator tests and the promote.sh end-to-end tests so both build the
    same promoting fixture from the proven core helpers. Sets/frozensets -> sorted lists, tuples
    -> lists, Mappings (incl. mappingproxy) -> dicts, dataclasses -> their field dict.
    ``EvidenceIntegrity`` (body + claimed_digest) freezes to exactly its ``to_dict`` wire shape.
    """
    import dataclasses
    from collections.abc import Mapping

    if isinstance(obj, (set, frozenset)):
        return sorted(str(x) for x in obj)
    if isinstance(obj, Mapping):
        return {str(k): _freeze(v) for k, v in obj.items()}
    if isinstance(obj, tuple):
        return [_freeze(x) for x in obj]
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _freeze(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    return obj


def promoting_promotion_inputs() -> dict[str, object]:
    """A promotion_inputs.json body that ``decide_promotion`` PROMOTES (allowed=True).

    Built by serializing the core test helpers' ``_request()`` (proven to promote in
    ``test_promotion_gate.py``) plus the roster policy and profile. This is the contract a real
    evidence-production pipeline would gather; reusing the proven request means a wiring bug that
    dropped a field turns the promote into a block here, where it is visible.
    """
    from tests.test_promotion_gate import _profile, _request, _roster

    return {
        "request": _freeze(_request()),
        "policy": _freeze(_roster()),
        "profile": _profile().to_dict(),
    }


def _chain_entry(**fields: Any) -> dict[str, Any]:
    """A tamper-evident chain entry: the producer's body plus its bare-hex content address.

    The hash is ``sha256(json.dumps(body, sort_keys=True, separators=(",",":")))`` with no
    ``"sha256:"`` prefix — the same canonical encoding the seam's ``_load_chain`` re-derives, so
    an honest entry re-derives and a tampered one (body changed, hash not) does not.
    """
    body = dict(fields)
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {**body, "hash": digest}


def promoting_chain_entries() -> list[dict[str, Any]]:
    """Receipt chain entries that ground the cited envelopes in ``promoting_promotion_inputs``.

    The fixture cites M-default (oracle) and F-default (flake); the chain entries carry
    the real producer fields the seam projection reads (oracle: ``oracle_adequate``; flake:
    ``deterministic`` + ``flake_count`` + ``automatic_retry_count``). The R-default build
    entry stays because receipt.sh still writes build receipts to the chain — but since
    1.1c nothing cites or projects it (the disturbed-surface set is host-derived inside
    decide_promotion, not attested by envelope). The flake producer writes
    ``automatic_retry_count``; the envelope reads ``retry_count`` — the projection renames it, so
    the chain carries the producer name.

    The entries are hash-CHAINED the way the real producers (``receipt.sh``/``mutate.sh``/
    ``flake.sh``) chain them: each entry's ``prev_hash`` is the prior entry's content address,
    genesis ``prev_hash`` = 64 zeros. The seam's ``_load_chain`` verifies this linkage (Opus
    R2), so a fixture that left every ``prev_hash`` empty would fail-closed at load — the chain
    is built sequentially so each entry links to the one before it.
    """
    entries: list[dict[str, Any]] = []
    prev = "0" * 64
    r = _chain_entry(
        id="R-default",
        kind="build",
        ts=1,
        exit=0,
        disturbed_surface_ids=["standard-surface"],
        changed_paths_digest="sha256:abcd",
        prev_hash=prev,
    )
    entries.append(r)
    prev = r["hash"]
    m = _chain_entry(id="M-default", kind="oracle", ts=2, oracle_adequate=True, prev_hash=prev)
    entries.append(m)
    prev = m["hash"]
    f = _chain_entry(
        id="F-default",
        kind="flake",
        ts=3,
        name="suite",
        runs=3,
        deterministic=True,
        flake_count=0,
        automatic_retry_count=0,
        prev_hash=prev,
    )
    entries.append(f)
    return entries


def write_promoting_chain(run_root: Path) -> Path:
    """Write the receipt chain grounding ``promoting_promotion_inputs`` for a run at ``run_root``.

    Mirrors the real harness layout: run_root = ``<H>/runs/<run>``, chain at
    ``<H>/receipts/chain.jsonl`` (the seam's ``_chain_path`` derives this as
    ``run_root.parent.parent / receipts / chain.jsonl``). Returns the chain path.
    """
    chain_path = run_root.parent.parent / "receipts" / "chain.jsonl"
    chain_path.parent.mkdir(parents=True, exist_ok=True)
    entries = promoting_chain_entries()
    chain_path.write_text(
        "\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n",
        encoding="utf-8",
    )
    return chain_path
SYNTHETIC_CATALOG = FIXTURES / "synthetic_target" / "pattern-catalog.json"
