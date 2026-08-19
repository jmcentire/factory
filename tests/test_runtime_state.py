from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from factory_core.manifest import LedgerEntry, SegregationError, SegregationPolicy, digest_obj
from factory_runtime.state import RunState, RunStateError, RunStore
from tests.conftest import (
    acceptance_catalog_artifacts,
    build_payload,
    ci_artifacts,
    generation_artifacts,
    preview_artifacts,
    ratification_receipts,
    terminalize_run_resources,
    validation_artifacts,
)

TARGET = "sha256:" + ("1" * 64)
SOURCE = "sha256:" + ("2" * 64)
TARGET_SOURCE = "sha256:" + ("6" * 64)
RESOURCE_HEAD = "sha256:" + ("7" * 64)
PRODUCT = "sha256:" + ("3" * 64)
ARCHITECTURE = "sha256:" + ("4" * 64)
OPERATIONS = "sha256:" + ("5" * 64)
CANDIDATE = "sha256:" + ("a" * 64)


class _Clock:
    def __init__(self) -> None:
        self.value = 100

    def __call__(self) -> int:
        self.value += 1
        return self.value


def _store(tmp_path: Path) -> RunStore:
    return RunStore(tmp_path, clock=_Clock())


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
        "resolved_tree": "c" * 40,
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
    store.authorize_intake(
        "run-1",
        source_digest=SOURCE,
        actor="validator",
        artifact_digests={
            "execution-request": "sha256:" + ("d" * 64),
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
    artifacts = generation_artifacts(seed, include_acceptance_catalog=False)
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
        artifact_digests=validation_artifacts(candidate=CANDIDATE),
        payload={"tester_identity": "tester"},
        implementer_identity="coder",
    )
    store.transition(
        "run-1",
        RunState.PREVIEW,
        actor="validator",
        artifact_digests=preview_artifacts(store, candidate=CANDIDATE),
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

    loaded = RunStore(tmp_path, clock=_Clock()).load("run-1")
    assert loaded.state == RunState.PROMOTED
    assert loaded.approved_candidate_digest == CANDIDATE
    assert loaded.phase_artifact_digests == {
        "product-specification": PRODUCT,
        "architecture": ARCHITECTURE,
        "operational-maturity": OPERATIONS,
    }


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
        artifact_digests=validation_artifacts(candidate=CANDIDATE),
        payload={"tester_identity": "tester"},
        implementer_identity="coder",
    )
    store.transition(
        "run-1",
        RunState.PREVIEW,
        actor="validator",
        artifact_digests=preview_artifacts(store, candidate=CANDIDATE),
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
        artifact_digests=validation_artifacts("stale", candidate=CANDIDATE),
        payload={"tester_identity": "tester"},
        implementer_identity="coder",
    )
    stale = store.load("run-1")
    RunStore(tmp_path, clock=_Clock()).transition(
        "run-1",
        RunState.PREVIEW,
        actor="other-validator",
        artifact_digests=preview_artifacts(store, "stale", candidate=CANDIDATE),
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

    assert RunStore(tmp_path, clock=_Clock()).load("run-1").state == RunState.PREVIEW


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
