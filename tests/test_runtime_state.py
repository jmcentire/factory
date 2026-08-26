from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from factory_core.manifest import (
    LedgerEntry,
    SegregationError,
    SegregationPolicy,
    digest_bytes,
    digest_obj,
)
from factory_runtime.adversarial_review import canonical_document_bytes
from factory_runtime.state import RunState, RunStateError, RunStore
from tests import conftest as fixture_support
from tests.conftest import (
    EMPTY_GIT_TREE_SHA1,
    acceptance_catalog_artifacts,
    build_payload,
    ci_artifacts,
    fixture_phase_artifact_digests,
    fixture_preview_evidence_verifier,
    generation_artifacts,
    preview_artifacts,
    ratification_receipts,
    retain_fixture_execution_request,
    retained_generation_artifacts,
    synthetic_candidate_digest,
    terminalize_run_resources,
    validation_artifacts,
)

TARGET = "sha256:" + ("1" * 64)
SOURCE = "sha256:" + ("2" * 64)
TARGET_SOURCE = "sha256:" + ("6" * 64)
RESOURCE_HEAD = "sha256:" + ("7" * 64)
_PHASE_DIGESTS = fixture_phase_artifact_digests()
PRODUCT = _PHASE_DIGESTS["product-specification"]
ARCHITECTURE = _PHASE_DIGESTS["architecture"]
OPERATIONS = _PHASE_DIGESTS["operational-maturity"]
CANDIDATE = synthetic_candidate_digest()


class _Clock:
    def __init__(self) -> None:
        self.value = 100

    def __call__(self) -> int:
        self.value += 1
        return self.value


def _store(tmp_path: Path) -> RunStore:
    return RunStore(
        tmp_path,
        clock=_Clock(),
        preview_evidence_verifier=fixture_preview_evidence_verifier(),
    )


def _target_state(store: RunStore) -> dict[str, object]:
    run_dir = (store.root / "run-1").resolve()
    source_root = run_dir / "target" / "source"
    return {
        "schema_version": "factory-target-state/1",
        "run_id": "run-1",
        "repository_id": "factory",
        "generation": 1,
        "target_id": "fixture",
        "target_manifest_digest": TARGET,
        "target_manifest_source_digest": TARGET_SOURCE,
        "requested_url": "https://example.test/repository.git",
        "canonical_url": "https://example.test/repository.git",
        "requested_ref": "refs/heads/main",
        "observed_ref_object": "b" * 40,
        "peeled_object": "b" * 40,
        "resolved_commit": "b" * 40,
        "resolved_tree": EMPTY_GIT_TREE_SHA1,
        "control_root": str(run_dir),
        "object_store": str(run_dir / "target" / "objects.git"),
        "source_root": str(source_root),
        "subpath": "",
        "workdir": str(source_root),
        "checkout_id": "sha256:" + ("8" * 64),
        "observation_method": "remote",
        "remote_freshness": "PROVED",
        "contact_ledger_head": "sha256:" + ("9" * 64),
        "resource_ledger_head": RESOURCE_HEAD,
        "created_at": 100,
    }


def _create_resolution(store: RunStore) -> None:
    store.create(
        "run-1",
        target_digest=TARGET,
        actor="validator",
        artifact_digests={
            "target-manifest-source": TARGET_SOURCE,
            "target-resolution-request": "sha256:" + ("a" * 64),
            "target-resolution-receipt": "sha256:" + ("b" * 64),
            "authority-genesis": "sha256:" + ("c" * 64),
        },
        payload={"authority_receipt_nonces": ["resolution-nonce"]},
    )


def _create_intake(store: RunStore) -> None:
    _create_resolution(store)
    store.record_target_state(
        "run-1",
        target_state=_target_state(store),
        actor="target-resolver",
        artifact_digests={"resource-ledger": RESOURCE_HEAD},
    )
    execution_request_digest = retain_fixture_execution_request(
        store,
        run_id="run-1",
        target_digest=TARGET,
    )
    store.authorize_intake(
        "run-1",
        source_digest=SOURCE,
        actor="validator",
        artifact_digests={
            "execution-request": execution_request_digest,
            "execution-receipt": "sha256:" + ("e" * 64),
            "authority-genesis": "sha256:" + ("c" * 64),
        },
        payload={"authority_receipt_nonces": ["execution-nonce"]},
        approver_identity="human-approver",
    )


def _ratify_all(store: RunStore) -> None:
    store.transition(
        "run-1",
        RunState.PRODUCT_SPECIFICATION_RATIFIED,
        actor="validator",
        artifact_digests={
            "product-specification": PRODUCT,
            **ratification_receipts("product-specification"),
        },
    )
    store.transition(
        "run-1",
        RunState.ARCHITECTURE_RATIFIED,
        actor="validator",
        artifact_digests={
            "architecture": ARCHITECTURE,
            **ratification_receipts("architecture"),
        },
    )
    store.transition(
        "run-1",
        RunState.OPERATIONAL_MATURITY_RATIFIED,
        actor="validator",
        artifact_digests={
            "operational-maturity": OPERATIONS,
            **ratification_receipts("operational-maturity"),
        },
    )


def _start_build(store: RunStore, *, attempt: int = 1, limit: int = 2) -> None:
    current = store.load("run-1")
    first_activation = not current.acceptance_obligation_catalog_digest
    seed = f"attempt-{attempt}"
    artifacts = retained_generation_artifacts(store, seed, include_acceptance_catalog=False)
    if first_activation:
        artifacts.update(acceptance_catalog_artifacts(store))
    if current.state == RunState.BLOCKED:
        retained = store.current_artifact_digests("run-1")
        artifacts.update(
            {
                "repair-brief": retained["repair-brief"],
                "repair-brief-envelope": retained["repair-brief-envelope"],
            }
        )
    store.transition(
        "run-1",
        RunState.BUILDING,
        actor="validator",
        artifact_digests=artifacts,
        payload=build_payload(
            attempt_number=attempt,
            attempt_limit=limit,
            seed=seed,
            activate_catalog=first_activation,
        ),
        implementer_identity="coder",
        verifier_identity="validator",
    )


def _enter_preview(store: RunStore) -> dict[str, str]:
    """Enter PREVIEW using only exact retained validation and review bytes."""

    validation = validation_artifacts(store, candidate=CANDIDATE)
    store.transition(
        "run-1",
        RunState.VALIDATING,
        actor="validator",
        artifact_digests=validation,
        payload={"tester_identity": "tester"},
        implementer_identity="coder",
        verifier_identity="validator",
    )
    artifacts = preview_artifacts(store, candidate=CANDIDATE)
    store.transition(
        "run-1",
        RunState.PREVIEW,
        actor="validator",
        artifact_digests=artifacts,
        payload={"tester_identity": "tester"},
        implementer_identity="coder",
        verifier_identity="validator",
    )
    preview_entry = store.verified_ledger_entries("run-1")[-1]
    preview_payload = preview_entry["payload"]
    assert isinstance(preview_payload, dict)
    assert preview_payload["evidence_verification_receipt"]["schema_version"] == (
        "factory-evidence-verification-receipt/1"
    )
    return {**validation, **artifacts}


def _block_attempt(store: RunStore) -> None:
    store.transition(
        "run-1",
        RunState.BLOCKED,
        actor="validator",
        payload={"reason": "attempt-failed", "tester_identity": "tester"},
        implementer_identity="coder",
        verifier_identity="validator",
    )


def _authorize_repair(
    store: RunStore,
    *,
    attempt: int,
    validator_identity: str = "validator",
) -> None:
    current = store.load("run-1")
    attempt_id = f"attempt-{attempt}-attempt-{attempt}"
    brief = "sha256:" + hashlib.sha256(f"brief:{attempt}".encode()).hexdigest()
    envelope = "sha256:" + hashlib.sha256(f"envelope:{attempt}".encode()).hexdigest()
    store.transition(
        "run-1",
        RunState.BLOCKED,
        actor="repair-supervisor",
        artifact_digests={"repair-brief": brief, "repair-brief-envelope": envelope},
        payload={
            "reason": "repair-brief-recorded",
            "predecessor_ledger_head": current.ledger_head,
            "repair_brief_digest": brief,
            "repair_brief_envelope_digest": envelope,
            "repair_signal": "retry",
            "authorized_attempt_id": attempt_id,
            "failure_signature": f"failure-{attempt - 1}",
            "authority_receipt_nonces": [],
        },
        verifier_identity=validator_identity,
    )


def test_create_and_rederive_run_from_authoritative_ledger(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    created = store.load("run-1")

    assert created.state == RunState.INTAKE
    assert store.load("run-1") == created
    assert (tmp_path / "run-1" / "ledger.jsonl").is_file()


def test_stage_r_alone_has_no_source_or_checkout_and_cannot_skip_resolution(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _create_resolution(store)
    projection = store.load("run-1")
    assert projection.state == RunState.TARGET_RESOLUTION_AUTHORIZED
    assert projection.source_digest == ""
    assert projection.target_state_digest == ""
    assert projection.target_state == {}

    with pytest.raises(RunStateError, match="transition refused"):
        store.authorize_intake(
            "run-1",
            source_digest=SOURCE,
            actor="validator",
            artifact_digests={
                "execution-request": "sha256:" + ("d" * 64),
                "execution-receipt": "sha256:" + ("e" * 64),
            },
            payload={"authority_receipt_nonces": ["execution-nonce"]},
            approver_identity="human-approver",
        )


def test_stage_e_cannot_switch_the_stage_r_authority_genesis(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_resolution(store)
    store.record_target_state(
        "run-1",
        target_state=_target_state(store),
        actor="target-resolver",
        artifact_digests={"resource-ledger": RESOURCE_HEAD},
    )

    with pytest.raises(RunStateError, match="authority genesis differs"):
        store.authorize_intake(
            "run-1",
            source_digest=SOURCE,
            actor="validator",
            artifact_digests={
                "execution-request": "sha256:" + ("d" * 64),
                "execution-receipt": "sha256:" + ("e" * 64),
                "authority-genesis": "sha256:" + ("f" * 64),
            },
            payload={"authority_receipt_nonces": ["execution-nonce"]},
            approver_identity="human-approver",
        )


@pytest.mark.parametrize("schema_version", ("factory-run/1", "factory-run/2"))
def test_legacy_runs_verify_and_rebuild_but_cannot_advance(
    tmp_path: Path,
    schema_version: str,
) -> None:
    store = _store(tmp_path)
    artifact_digests: dict[str, object] = {
        "target": TARGET,
        "source": SOURCE,
        "phase_artifacts": {},
    }
    if schema_version == "factory-run/2":
        artifact_digests["generation_artifacts"] = {}
    store._ledger("run-1").append(
        LedgerEntry(
            capability_id="run-1",
            from_state="",
            to_state=RunState.INTAKE,
            artifact_digests=artifact_digests,
            payload={"run_schema_version": schema_version},
            actor="validator",
            created_at="100",
        )
    )
    projection = store.rebuild_projection("run-1")
    assert store.load("run-1") == projection
    assert projection.schema_version == schema_version
    with pytest.raises(RunStateError, match="legacy run schema cannot advance"):
        store.transition(
            "run-1",
            RunState.PRODUCT_SPECIFICATION_RATIFIED,
            actor="validator",
            artifact_digests={
                "product-specification": PRODUCT,
                **ratification_receipts("product-specification"),
            },
        )


def test_full_happy_path_is_explicit_and_resumable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    _ratify_all(store)

    _start_build(store)
    store.transition(
        "run-1",
        RunState.VALIDATING,
        actor="validator",
        artifact_digests=validation_artifacts(store, candidate=CANDIDATE),
        payload={"tester_identity": "tester"},
        implementer_identity="coder",
        verifier_identity="validator",
    )
    store.transition(
        "run-1",
        RunState.PREVIEW,
        actor="validator",
        artifact_digests=preview_artifacts(store, candidate=CANDIDATE),
        payload={"tester_identity": "tester"},
        implementer_identity="coder",
        verifier_identity="validator",
    )

    # The two anchor states carry authority. This previously walked them with nothing but
    # actor="validator" — a validator human-approving its own run — which is exactly the hole
    # the anchor controls close. See tests/test_runtime_anchor_states.py for the refusals.
    store.transition(
        "run-1",
        RunState.HUMAN_APPROVED,
        actor="validator",
        artifact_digests={"candidate": CANDIDATE},
        implementer_identity="coder",
        approver_identity="human-approver",
    )
    store.transition("run-1", RunState.CI, actor="validator", artifact_digests=ci_artifacts())
    terminalize_run_resources(store, run_id="run-1")
    store.transition(
        "run-1",
        RunState.PROMOTED,
        actor="validator",
        artifact_digests={"promoted-artifact": CANDIDATE},
    )

    with pytest.raises(RunStateError, match="explicit cryptographic evidence verifier"):
        RunStore(tmp_path, clock=_Clock()).load("run-1")
    loaded = RunStore(
        tmp_path,
        clock=_Clock(),
        preview_evidence_verifier=fixture_preview_evidence_verifier(),
    ).load("run-1")
    assert loaded.state == RunState.PROMOTED
    assert loaded.approved_candidate_digest == CANDIDATE
    assert loaded.phase_artifact_digests == {
        "product-specification": PRODUCT,
        "architecture": ARCHITECTURE,
        "operational-maturity": OPERATIONS,
    }


def test_preview_admission_requires_an_explicit_cryptographic_verifier(
    tmp_path: Path,
) -> None:
    configured = _store(tmp_path)
    _create_intake(configured)
    _ratify_all(configured)
    _start_build(configured)
    configured.transition(
        "run-1",
        RunState.VALIDATING,
        actor="validator",
        artifact_digests=validation_artifacts(configured, candidate=CANDIDATE),
        payload={"tester_identity": "tester"},
        implementer_identity="coder",
        verifier_identity="validator",
    )
    artifacts = preview_artifacts(configured, candidate=CANDIDATE)

    with pytest.raises(RunStateError, match="explicit cryptographic evidence verifier"):
        RunStore(tmp_path, clock=_Clock()).transition(
            "run-1",
            RunState.PREVIEW,
            actor="validator",
            artifact_digests=artifacts,
            payload={"tester_identity": "tester"},
            implementer_identity="coder",
            verifier_identity="validator",
        )

    assert configured.load("run-1").state == RunState.VALIDATING


def test_preview_rejects_signature_shaped_but_unauthenticated_evidence(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    _ratify_all(store)
    _start_build(store)
    store.transition(
        "run-1",
        RunState.VALIDATING,
        actor="validator",
        artifact_digests=validation_artifacts(store, candidate=CANDIDATE),
        payload={"tester_identity": "tester"},
        implementer_identity="coder",
        verifier_identity="validator",
    )
    artifacts = preview_artifacts(store, candidate=CANDIDATE)
    attempt = [
        entry
        for entry in store.verified_ledger_entries("run-1")
        if entry.get("to_state") == RunState.BUILDING
    ][-1]
    attempt_payload = attempt["payload"]
    assert isinstance(attempt_payload, dict)
    envelope_path = (
        tmp_path
        / "run-1"
        / "evidence"
        / "build-attempts"
        / str(attempt_payload["attempt_id"])
        / "evidence-bundle.tessera.json"
    )
    shaped = {
        "pubkey": "1" * 64,
        "signature": "2" * 128,
        "state": {
            "kind": "factory-evidence-bundle",
            "payload": "{}",
            "payload_digest": artifacts["evidence-bundle"],
        },
    }
    shaped_bytes = json.dumps(shaped, sort_keys=True, separators=(",", ":")).encode()
    envelope_path.write_bytes(shaped_bytes)
    artifacts["evidence-envelope"] = "sha256:" + hashlib.sha256(shaped_bytes).hexdigest()

    with pytest.raises(RunStateError, match="fixture evidence MAC is invalid"):
        store.transition(
            "run-1",
            RunState.PREVIEW,
            actor="validator",
            artifact_digests=artifacts,
            payload={"tester_identity": "tester"},
            implementer_identity="coder",
            verifier_identity="validator",
        )


@pytest.mark.parametrize(
    "field",
    [
        "run_schema_version",
        "run_id",
        "generation",
        "source",
        "destination",
        "validating_ledger_head",
        "authority_genesis_digest",
        "identity:implementer",
        "identity:tester",
        "identity:verifier",
        *(
            f"artifact:{name}"
            for name in (
                "candidate",
                "acceptance-tests",
                "coder-output-snapshot",
                "tester-output-snapshot",
                "acceptance-obligation-report",
                "validator-review-subject",
                "validator-adversarial-review",
                "base-source-snapshot",
                "candidate-change-set",
                "validator-review-authority-context",
                "validator-review-observations-source",
                "validator-execution-manifest",
                "validator-execution-configuration",
                "validator-execution-environment",
                "validator-execution-snapshot",
            )
        ),
    ],
)
def test_preview_rejects_each_substituted_authenticated_admission_field(
    tmp_path: Path,
    field: str,
) -> None:
    """A valid signature over a different PREVIEW subject is not authority for this run."""

    store = _store(tmp_path)
    _create_intake(store)
    _ratify_all(store)
    _start_build(store)
    store.transition(
        "run-1",
        RunState.VALIDATING,
        actor="validator",
        artifact_digests=validation_artifacts(store, candidate=CANDIDATE),
        payload={"tester_identity": "tester"},
        implementer_identity="coder",
        verifier_identity="validator",
    )
    artifacts = preview_artifacts(store, candidate=CANDIDATE)
    building = [
        entry
        for entry in store.verified_ledger_entries("run-1")
        if entry.get("to_state") == RunState.BUILDING
    ][-1]
    attempt_payload = building["payload"]
    assert isinstance(attempt_payload, dict)
    envelope_path = (
        tmp_path
        / "run-1"
        / "evidence"
        / "build-attempts"
        / str(attempt_payload["attempt_id"])
        / "evidence-bundle.tessera.json"
    )
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    payload = envelope["payload"]
    preview = payload["preview_admission"]
    replacement_digest = "sha256:" + ("f" * 64)
    if field == "run_schema_version":
        preview[field] = "factory-run/6"
    elif field == "run_id":
        preview[field] = "other-run"
    elif field == "generation":
        preview[field] = int(preview[field]) + 1
    elif field == "source":
        preview[field] = "building"
    elif field == "destination":
        preview[field] = "ci"
    elif field in {"validating_ledger_head", "authority_genesis_digest"}:
        preview[field] = replacement_digest
    elif field.startswith("identity:"):
        identity = field.partition(":")[2]
        preview["identities"][identity] = f"other-{identity}"
    else:
        artifact = field.partition(":")[2]
        preview["artifact_digests"][artifact] = replacement_digest

    envelope["payload_digest"] = digest_obj(payload)
    envelope.pop("fixture_mac")
    authenticated = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    envelope["fixture_mac"] = hmac.new(
        fixture_support._FIXTURE_EVIDENCE_KEY,
        authenticated,
        hashlib.sha256,
    ).hexdigest()
    envelope_bytes = canonical_document_bytes(envelope)
    envelope_path.write_bytes(envelope_bytes)
    artifacts["evidence-bundle"] = digest_obj(payload)
    artifacts["evidence-envelope"] = digest_bytes(envelope_bytes)

    with pytest.raises(RunStateError, match="signed evidence bundle is invalid"):
        store.transition(
            "run-1",
            RunState.PREVIEW,
            actor="validator",
            artifact_digests=artifacts,
            payload={"tester_identity": "tester"},
            implementer_identity="coder",
            verifier_identity="validator",
        )

    assert store.load("run-1").state == RunState.VALIDATING


def test_preview_rejects_a_validator_swap_after_validation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    _ratify_all(store)
    _start_build(store)
    store.transition(
        "run-1",
        RunState.VALIDATING,
        actor="validator-a",
        artifact_digests=validation_artifacts(store, candidate=CANDIDATE),
        payload={"tester_identity": "tester"},
        implementer_identity="coder",
        verifier_identity="validator-a",
    )
    artifacts = preview_artifacts(
        store,
        candidate=CANDIDATE,
        reviewer_identity="validator-b",
    )

    with pytest.raises(RunStateError, match="Validator differs from causal VALIDATING identity"):
        store.transition(
            "run-1",
            RunState.PREVIEW,
            actor="validator-b",
            artifact_digests=artifacts,
            payload={"tester_identity": "tester"},
            implementer_identity="coder",
            verifier_identity="validator-b",
        )

    assert store.load("run-1").state == RunState.VALIDATING


def test_replay_rejects_a_rehashed_validator_swap_after_validation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    _ratify_all(store)
    _start_build(store)
    _enter_preview(store)
    ledger_path = tmp_path / "run-1" / "ledger.jsonl"
    rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    rows[-1]["verifier_identity"] = "other-validator"
    body = {key: value for key, value in rows[-1].items() if key != "entry_hash"}
    rows[-1]["entry_hash"] = digest_obj(body)
    ledger_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RunStateError, match="Validator differs from causal VALIDATING identity"):
        store.rebuild_projection("run-1")


def test_replay_rejects_a_rehashed_fabricated_evidence_verification_receipt(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    _ratify_all(store)
    _start_build(store)
    _enter_preview(store)
    ledger_path = tmp_path / "run-1" / "ledger.jsonl"
    rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    receipt = rows[-1]["payload"]["evidence_verification_receipt"]
    receipt["signer_public_key"] = "f" * 64
    body = {key: value for key, value in rows[-1].items() if key != "entry_hash"}
    rows[-1]["entry_hash"] = digest_obj(body)
    ledger_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RunStateError, match="receipt does not reproduce"):
        store.rebuild_projection("run-1")


def test_preview_refuses_missing_retained_adversarial_review(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    _ratify_all(store)
    _start_build(store)
    store.transition(
        "run-1",
        RunState.VALIDATING,
        actor="validator",
        artifact_digests=validation_artifacts(store, candidate=CANDIDATE),
        payload={"tester_identity": "tester"},
        implementer_identity="coder",
        verifier_identity="validator",
    )
    artifacts = preview_artifacts(store, candidate=CANDIDATE)
    retained_report = (
        tmp_path
        / "run-1"
        / "evidence"
        / "validator-adversarial-reviews"
        / artifacts["validator-review-subject"].removeprefix("sha256:")
        / f"{artifacts['validator-adversarial-review'].removeprefix('sha256:')}.json"
    )
    retained_report.unlink()

    with pytest.raises(RunStateError, match="adversarial review is invalid"):
        store.transition(
            "run-1",
            RunState.PREVIEW,
            actor="validator",
            artifact_digests=artifacts,
            payload={"tester_identity": "tester"},
            implementer_identity="coder",
            verifier_identity="validator",
        )

    assert store.load("run-1").state == RunState.VALIDATING


def test_replay_refuses_tampered_retained_adversarial_review(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    _ratify_all(store)
    _start_build(store)
    store.transition(
        "run-1",
        RunState.VALIDATING,
        actor="validator",
        artifact_digests=validation_artifacts(store, candidate=CANDIDATE),
        payload={"tester_identity": "tester"},
        implementer_identity="coder",
        verifier_identity="validator",
    )
    artifacts = preview_artifacts(store, candidate=CANDIDATE)
    store.transition(
        "run-1",
        RunState.PREVIEW,
        actor="validator",
        artifact_digests=artifacts,
        payload={"tester_identity": "tester"},
        implementer_identity="coder",
        verifier_identity="validator",
    )
    retained_subject = (
        tmp_path
        / "run-1"
        / "evidence"
        / "validator-adversarial-reviews"
        / artifacts["validator-review-subject"].removeprefix("sha256:")
        / "subject.json"
    )
    retained_subject.write_bytes(b"{}\n")

    with pytest.raises(RunStateError, match="adversarial review is invalid"):
        RunStore(
            tmp_path,
            clock=_Clock(),
            preview_evidence_verifier=fixture_preview_evidence_verifier(),
        ).load("run-1")


@pytest.mark.parametrize("missing", ["validator-execution", "evidence-envelope"])
def test_replay_refuses_missing_preview_dependency_bytes(
    tmp_path: Path,
    missing: str,
) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    _ratify_all(store)
    _start_build(store)
    artifacts = _enter_preview(store)
    if missing == "validator-execution":
        attempt = [
            entry
            for entry in store.verified_ledger_entries("run-1")
            if entry.get("to_state") == RunState.BUILDING
        ][-1]
        payload = attempt["payload"]
        assert isinstance(payload, dict)
        tree = (
            tmp_path
            / "run-1"
            / "evidence"
            / "build-attempts"
            / str(payload["attempt_id"])
            / "validator-execution"
            / "trees"
            / artifacts["validator-execution-snapshot"].removeprefix("sha256:")
        )
        tree.chmod(0o755)
        (tree / "manifest.json").unlink()
        expected = "validating Validator execution is invalid"
    else:
        attempt = [
            entry
            for entry in store.verified_ledger_entries("run-1")
            if entry.get("to_state") == RunState.BUILDING
        ][-1]
        payload = attempt["payload"]
        assert isinstance(payload, dict)
        (
            tmp_path
            / "run-1"
            / "evidence"
            / "build-attempts"
            / str(payload["attempt_id"])
            / "evidence-bundle.tessera.json"
        ).unlink()
        expected = "signed evidence bundle is invalid"

    with pytest.raises(RunStateError, match=expected):
        RunStore(
            tmp_path,
            clock=_Clock(),
            preview_evidence_verifier=fixture_preview_evidence_verifier(),
        ).load("run-1")


@pytest.mark.parametrize("mutated", ["cited-implementation", "review-observations"])
def test_replay_refuses_mutated_preview_dependency_bytes(
    tmp_path: Path,
    mutated: str,
) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    _ratify_all(store)
    _start_build(store)
    artifacts = _enter_preview(store)
    if mutated == "cited-implementation":
        path = (
            tmp_path
            / "run-1"
            / "evidence"
            / "review-snapshots"
            / artifacts["coder-output-snapshot"].removeprefix("sha256:")
            / "files"
            / "artifact"
            / "artifact.py"
        )
    else:
        path = (
            tmp_path
            / "run-1"
            / "evidence"
            / "validator-adversarial-reviews"
            / artifacts["validator-review-subject"].removeprefix("sha256:")
            / "acceptance-obligation-observations.json"
        )
    path.chmod(0o644)
    path.write_bytes(b"mutated after preview\n")

    with pytest.raises(RunStateError, match="adversarial review is invalid"):
        RunStore(
            tmp_path,
            clock=_Clock(),
            preview_evidence_verifier=fixture_preview_evidence_verifier(),
        ).load("run-1")


def test_promoting_with_an_unresolved_resource_is_refused(tmp_path: Path) -> None:
    from factory_runtime.resources import ResourceLedger

    store = _store(tmp_path)
    _create_intake(store)
    _ratify_all(store)
    _start_build(store)
    store.transition(
        "run-1",
        RunState.VALIDATING,
        actor="validator",
        artifact_digests=validation_artifacts(store, candidate=CANDIDATE),
        payload={"tester_identity": "tester"},
        implementer_identity="coder",
        verifier_identity="validator",
    )
    store.transition(
        "run-1",
        RunState.PREVIEW,
        actor="validator",
        artifact_digests=preview_artifacts(store, candidate=CANDIDATE),
        payload={"tester_identity": "tester"},
        implementer_identity="coder",
        verifier_identity="validator",
    )
    store.transition(
        "run-1",
        RunState.HUMAN_APPROVED,
        actor="validator",
        artifact_digests={"candidate": CANDIDATE},
        implementer_identity="coder",
        approver_identity="human-approver",
    )
    store.transition("run-1", RunState.CI, actor="validator", artifact_digests=ci_artifacts())
    ResourceLedger(tmp_path / "run-1", "run-1", clock=lambda: 100).append(
        generation=1,
        resource_id="unfinished-workspace",
        resource_type="lane-workspace",
        identifier=str(tmp_path / "run-1" / "workspaces" / "coder"),
        creator_action="test",
        ownership="run-owned",
        baseline={"absent_at_plan": True},
        disposition={},
        status="planned",
        evidence_digests={},
        actor="test",
    )

    with pytest.raises(RunStateError, match="unfinished-workspace"):
        store.transition(
            "run-1",
            RunState.PROMOTED,
            actor="validator",
            artifact_digests={"promoted-artifact": CANDIDATE},
        )

    assert store.load("run-1").state == RunState.CI
    assert not (tmp_path / "run-1" / "resources.seal.json").exists()


def test_transition_refuses_a_stale_lifecycle_head_without_appending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    _ratify_all(store)
    _start_build(store)
    store.transition(
        "run-1",
        RunState.VALIDATING,
        actor="validator",
        artifact_digests=validation_artifacts(store, "stale", candidate=CANDIDATE),
        payload={"tester_identity": "tester"},
        implementer_identity="coder",
        verifier_identity="validator",
    )
    stale = store.load("run-1")
    RunStore(
        tmp_path,
        clock=_Clock(),
        preview_evidence_verifier=fixture_preview_evidence_verifier(),
    ).transition(
        "run-1",
        RunState.PREVIEW,
        actor="other-validator",
        artifact_digests=preview_artifacts(store, "stale", candidate=CANDIDATE),
        payload={"tester_identity": "tester"},
        implementer_identity="coder",
        verifier_identity="validator",
    )
    monkeypatch.setattr(store, "load", lambda _run_id: stale)

    with pytest.raises(RunStateError, match="run changed"):
        store.transition(
            "run-1",
            RunState.BUILDING,
            actor="stale-validator",
            artifact_digests=generation_artifacts("stale-retry", include_acceptance_catalog=False),
            payload=build_payload(
                attempt_number=2,
                attempt_limit=2,
                seed="stale-retry",
                activate_catalog=False,
            ),
        )

    assert (
        RunStore(
            tmp_path,
            clock=_Clock(),
            preview_evidence_verifier=fixture_preview_evidence_verifier(),
        )
        .load("run-1")
        .state
        == RunState.PREVIEW
    )


def test_skipping_a_phase_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_intake(store)

    with pytest.raises(RunStateError, match="transition refused"):
        store.transition("run-1", RunState.BUILDING, actor="validator")


def test_building_requires_the_complete_generation_readiness_tuple(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    _ratify_all(store)
    incomplete = generation_artifacts()
    incomplete.pop("build-plan-source")

    with pytest.raises(RunStateError, match="build-plan-source"):
        store.transition(
            "run-1",
            RunState.BUILDING,
            actor="validator",
            artifact_digests=incomplete,
            payload=build_payload(attempt_limit=2),
        )


def test_build_attempt_limit_is_monotone_and_mechanically_exhausted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    _ratify_all(store)
    _start_build(store, attempt=1, limit=2)
    _block_attempt(store)
    first = store.load("run-1")
    assert first.build_attempt_count == 1
    assert first.build_attempt_limit == 2

    _authorize_repair(store, attempt=2)
    with pytest.raises(RunStateError, match="cannot raise the attempt limit"):
        _start_build(store, attempt=2, limit=3)

    _start_build(store, attempt=2, limit=2)
    _block_attempt(store)
    second = store.load("run-1")
    assert second.build_attempt_count == 2
    assert second.build_attempt_limit == 2

    _authorize_repair(store, attempt=3)
    with pytest.raises(RunStateError, match="exceeds the authorized build attempt limit"):
        _start_build(store, attempt=3, limit=2)


def test_blocked_retry_requires_the_immediately_preceding_repair_bytes(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    _ratify_all(store)
    _start_build(store, attempt=1, limit=2)
    _block_attempt(store)
    _authorize_repair(store, attempt=2)
    artifacts = generation_artifacts("attempt-2", include_acceptance_catalog=False)
    retained = store.current_artifact_digests("run-1")
    artifacts.update(
        {
            "repair-brief": "sha256:" + ("9" * 64),
            "repair-brief-envelope": retained["repair-brief-envelope"],
        }
    )

    with pytest.raises(RunStateError, match="repair-brief differs"):
        store.transition(
            "run-1",
            RunState.BUILDING,
            actor="validator",
            artifact_digests=artifacts,
            payload=build_payload(
                attempt_number=2,
                attempt_limit=2,
                seed="attempt-2",
                activate_catalog=False,
            ),
            implementer_identity="coder",
            verifier_identity="validator",
        )


def test_one_failed_attempt_cannot_authorize_multiple_repair_briefs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    _ratify_all(store)
    _start_build(store, attempt=1, limit=2)
    _block_attempt(store)
    _authorize_repair(store, attempt=2)

    with pytest.raises(RunStateError, match="only one repair brief"):
        _authorize_repair(store, attempt=3)


def test_repair_brief_must_use_the_causal_failed_attempt_validator(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    _ratify_all(store)
    _start_build(store, attempt=1, limit=2)
    _block_attempt(store)

    with pytest.raises(RunStateError, match="Validator of the causal failed attempt"):
        _authorize_repair(store, attempt=2, validator_identity="coder")


def test_blocked_retry_cannot_swap_the_validator_after_signed_authorization(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    _ratify_all(store)
    _start_build(store, attempt=1, limit=2)
    _block_attempt(store)
    _authorize_repair(store, attempt=2)
    retained = store.current_artifact_digests("run-1")
    artifacts = generation_artifacts("attempt-2", include_acceptance_catalog=False)
    artifacts.update(
        {
            "repair-brief": retained["repair-brief"],
            "repair-brief-envelope": retained["repair-brief-envelope"],
        }
    )

    with pytest.raises(RunStateError, match="differs from the signed repair authorization"):
        store.transition(
            "run-1",
            RunState.BUILDING,
            actor="validator",
            artifact_digests=artifacts,
            payload=build_payload(
                attempt_number=2,
                attempt_limit=2,
                seed="attempt-2",
                activate_catalog=False,
            ),
            implementer_identity="coder",
            verifier_identity="another-validator",
        )


def test_replay_rejects_a_rehashed_repair_event_with_a_swapped_validator(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    _ratify_all(store)
    _start_build(store, attempt=1, limit=2)
    _block_attempt(store)
    _authorize_repair(store, attempt=2)
    ledger_path = tmp_path / "run-1" / "ledger.jsonl"
    records = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    repair_event = records[-1]
    repair_event["verifier_identity"] = "coder"
    repair_event["entry_hash"] = digest_obj(
        {key: value for key, value in repair_event.items() if key != "entry_hash"}
    )
    ledger_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )

    with pytest.raises(RunStateError, match="Validator of the causal failed attempt"):
        store.rebuild_projection("run-1")


def test_ratified_state_requires_the_corresponding_artifact(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_intake(store)

    with pytest.raises(RunStateError, match="requires artifact digest"):
        store.transition(
            "run-1",
            RunState.PRODUCT_SPECIFICATION_RATIFIED,
            actor="validator",
        )


def test_tampered_projection_is_not_authority(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    path = tmp_path / "run-1" / "run.json"
    projection = json.loads(path.read_text())
    projection["state"] = RunState.PROMOTED
    path.write_text(json.dumps(projection))

    with pytest.raises(RunStateError, match="stale or tampered"):
        store.load("run-1")

    assert store.rebuild_projection("run-1").state == RunState.INTAKE


def test_tampered_ledger_blocks_even_if_projection_is_green(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    path = tmp_path / "run-1" / "ledger.jsonl"
    lines = path.read_text().splitlines()
    record = json.loads(lines[-1])
    record["to_state"] = RunState.PROMOTED
    lines[-1] = json.dumps(record)
    path.write_text("\n".join(lines) + "\n")

    with pytest.raises(RunStateError, match="ledger verification failed"):
        store.load("run-1")


def test_specification_defect_can_only_resume_through_a_ratified_phase(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    _ratify_all(store)
    _start_build(store)
    projection = store.transition(
        "run-1",
        RunState.SPECIFICATION_DEFECT,
        actor="validator",
        payload={"phase": "architecture"},
    )
    assert projection.phase_artifact_digests == {
        "product-specification": PRODUCT,
    }

    with pytest.raises(RunStateError, match="transition refused"):
        store.transition("run-1", RunState.BUILDING, actor="validator")

    amended = "sha256:" + ("6" * 64)
    projection = store.transition(
        "run-1",
        RunState.ARCHITECTURE_RATIFIED,
        actor="validator",
        artifact_digests={
            "architecture": amended,
            # A receipt binds to one subject digest, so the amended version needs its own pair;
            # the store refuses the receipts recorded for the version the defect invalidated.
            "architecture:human-receipt": "sha256:" + ("7" * 64),
            "architecture:validator-receipt": "sha256:" + ("8" * 64),
        },
    )
    assert projection.phase_artifact_digests["architecture"] == amended
    assert "operational-maturity" not in projection.phase_artifact_digests


def test_specification_defect_requires_affected_phase(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    _ratify_all(store)
    _start_build(store)

    with pytest.raises(RunStateError, match="payload.phase"):
        store.transition("run-1", RunState.SPECIFICATION_DEFECT, actor="validator")


def test_sod_overlap_is_refused_at_transition_write(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_intake(store)
    policy = SegregationPolicy(
        human_ids=frozenset({"human-1"}),
        excluded_service_identities=frozenset({"agent-*"}),
    )

    with pytest.raises(SegregationError):
        store.transition(
            "run-1",
            RunState.PRODUCT_SPECIFICATION_RATIFIED,
            actor="validator",
            artifact_digests={
                "product-specification": PRODUCT,
                **ratification_receipts("product-specification"),
            },
            implementer_identity="agent-validator",
            verifier_identity="agent-validator",
            approver_identity="human-1",
            policy=policy,
        )


def test_run_store_refuses_symlinked_run_or_ledger_paths(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "run-1").symlink_to(outside, target_is_directory=True)
    store = _store(tmp_path)

    with pytest.raises(RunStateError, match="run directory cannot be a symlink"):
        _create_resolution(store)

    (tmp_path / "run-1").unlink()
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    (run_dir / "ledger.jsonl").symlink_to(tmp_path / "missing-ledger")
    with pytest.raises(RunStateError, match="run ledger cannot be a symlink"):
        _create_resolution(store)


def test_phase_derived_catalog_activation_requires_exact_om_receipt_citation() -> None:
    """Reuse is admissible only as exact re-citation of the run's own om receipts."""

    from factory_runtime.state import (
        RunStateError,
        _require_phase_derived_catalog_receipts,
    )

    om = "sha256:" + "1" * 64
    human = "sha256:" + "2" * 64
    validator = "sha256:" + "3" * 64
    catalog = "sha256:" + "4" * 64
    entries = [
        {
            "artifact_digests": {
                "operational-maturity": om,
                "operational-maturity:human-receipt": human,
                "operational-maturity:validator-receipt": validator,
            }
        }
    ]

    def supplied(human_digest: str, validator_digest: str) -> dict[str, str]:
        return {
            "acceptance-obligation-catalog": catalog,
            "acceptance-obligation-catalog:human-receipt": human_digest,
            "acceptance-obligation-catalog:validator-receipt": validator_digest,
        }

    _require_phase_derived_catalog_receipts(
        supplied(human, validator), entries=entries, catalog_digest=catalog
    )

    with pytest.raises(RunStateError, match="exactly the recorded"):
        _require_phase_derived_catalog_receipts(
            supplied("sha256:" + "9" * 64, validator),
            entries=entries,
            catalog_digest=catalog,
        )
    with pytest.raises(RunStateError, match="recorded operational-maturity"):
        _require_phase_derived_catalog_receipts(
            supplied(human, validator), entries=[], catalog_digest=catalog
        )
    with pytest.raises(RunStateError, match="distinct"):
        _require_phase_derived_catalog_receipts(
            supplied(human, validator), entries=entries, catalog_digest=human
        )
