from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory_core.manifest import SegregationError, SegregationPolicy
from factory_runtime.state import RunState, RunStateError, RunStore
from tests.conftest import ratification_receipts

TARGET = "sha256:" + ("1" * 64)
SOURCE = "sha256:" + ("2" * 64)
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


def test_create_and_rederive_run_from_authoritative_ledger(tmp_path: Path) -> None:
    store = _store(tmp_path)
    created = store.create(
        "run-1",
        target_digest=TARGET,
        source_digest=SOURCE,
        actor="validator",
    )

    assert created.state == RunState.INTAKE
    assert store.load("run-1") == created
    assert (tmp_path / "run-1" / "ledger.jsonl").is_file()


def test_full_happy_path_is_explicit_and_resumable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create("run-1", target_digest=TARGET, source_digest=SOURCE, actor="validator")
    _ratify_all(store)

    for state in (RunState.BUILDING, RunState.VALIDATING, RunState.PREVIEW):
        store.transition("run-1", state, actor="validator")

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
    store.transition("run-1", RunState.CI, actor="validator")
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


def test_skipping_a_phase_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create("run-1", target_digest=TARGET, source_digest=SOURCE, actor="validator")

    with pytest.raises(RunStateError, match="transition refused"):
        store.transition("run-1", RunState.BUILDING, actor="validator")


def test_ratified_state_requires_the_corresponding_artifact(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create("run-1", target_digest=TARGET, source_digest=SOURCE, actor="validator")

    with pytest.raises(RunStateError, match="requires artifact digest"):
        store.transition(
            "run-1",
            RunState.PRODUCT_SPECIFICATION_RATIFIED,
            actor="validator",
        )


def test_tampered_projection_is_not_authority(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create("run-1", target_digest=TARGET, source_digest=SOURCE, actor="validator")
    path = tmp_path / "run-1" / "run.json"
    projection = json.loads(path.read_text())
    projection["state"] = RunState.PROMOTED
    path.write_text(json.dumps(projection))

    with pytest.raises(RunStateError, match="stale or tampered"):
        store.load("run-1")

    assert store.rebuild_projection("run-1").state == RunState.INTAKE


def test_tampered_ledger_blocks_even_if_projection_is_green(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create("run-1", target_digest=TARGET, source_digest=SOURCE, actor="validator")
    path = tmp_path / "run-1" / "ledger.jsonl"
    record = json.loads(path.read_text())
    record["to_state"] = RunState.PROMOTED
    path.write_text(json.dumps(record) + "\n")

    with pytest.raises(RunStateError, match="ledger verification failed"):
        store.load("run-1")


def test_specification_defect_can_only_resume_through_a_ratified_phase(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create("run-1", target_digest=TARGET, source_digest=SOURCE, actor="validator")
    _ratify_all(store)
    store.transition("run-1", RunState.BUILDING, actor="validator")
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
    store.create("run-1", target_digest=TARGET, source_digest=SOURCE, actor="validator")
    _ratify_all(store)
    store.transition("run-1", RunState.BUILDING, actor="validator")

    with pytest.raises(RunStateError, match="payload.phase"):
        store.transition("run-1", RunState.SPECIFICATION_DEFECT, actor="validator")


def test_sod_overlap_is_refused_at_transition_write(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create("run-1", target_digest=TARGET, source_digest=SOURCE, actor="validator")
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
        store.create(
            "run-1",
            target_digest=TARGET,
            source_digest=SOURCE,
            actor="validator",
        )

    (tmp_path / "run-1").unlink()
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    (run_dir / "ledger.jsonl").symlink_to(tmp_path / "missing-ledger")
    with pytest.raises(RunStateError, match="run ledger cannot be a symlink"):
        store.create(
            "run-1",
            target_digest=TARGET,
            source_digest=SOURCE,
            actor="validator",
        )
