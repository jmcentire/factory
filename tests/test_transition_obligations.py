from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory_core.manifest import LedgerEntry, digest_obj
from factory_runtime.state import ALLOWED_TRANSITIONS, RunState, RunStateError, RunStore
from factory_runtime.transition_obligations import (
    REPORT_KEY,
    SET_KEY,
    TransitionObligationError,
    assert_catalog_covers,
    derive_transition_obligations,
)
from tests.conftest import (
    build_payload,
    create_intake_run,
    generation_artifacts,
    ratification_receipts,
    standin_test_change_authorization_artifacts,
    validation_artifacts,
)

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


def _operations_ready(tmp_path: Path) -> RunStore:
    store = RunStore(tmp_path, clock=_Clock())
    create_intake_run(
        store,
        run_id="run-1",
        target_digest=TARGET,
        source_digest=SOURCE,
    )
    for state, key, value in (
        (RunState.PRODUCT_SPECIFICATION_RATIFIED, "product-specification", PRODUCT),
        (RunState.ARCHITECTURE_RATIFIED, "architecture", ARCHITECTURE),
        (RunState.OPERATIONAL_MATURITY_RATIFIED, "operational-maturity", OPERATIONS),
    ):
        store.transition(
            "run-1",
            state,
            actor="validator",
            artifact_digests={key: value, **ratification_receipts(key)},
        )
    return store


def _build(store: RunStore, *, payload: dict[str, object] | None = None) -> None:
    artifacts = generation_artifacts()
    if payload and payload.get("changed_existing_tests"):
        artifacts.update(standin_test_change_authorization_artifacts())
    store.transition(
        "run-1",
        RunState.BUILDING,
        actor="validator",
        artifact_digests=artifacts,
        payload=build_payload(**(payload or {})),
    )


def _last_record(store: RunStore) -> dict[str, object]:
    return dict(store._ledger("run-1").verified_entries()[-1])


def test_catalog_exactly_covers_the_live_state_transition_table() -> None:
    assert_catalog_covers(
        {
            str(source): tuple(str(destination) for destination in destinations)
            for source, destinations in ALLOWED_TRANSITIONS.items()
        }
    )

    with pytest.raises(TransitionObligationError, match="catalog drift"):
        assert_catalog_covers({"intake": ("product-specification-ratified",)})


def test_unknown_trigger_is_denied_instead_of_selecting_a_default() -> None:
    with pytest.raises(TransitionObligationError, match="unknown state-triggered"):
        derive_transition_obligations(
            run_id="run-1",
            generation=1,
            source="building",
            destination="promoted",
            prior_ledger_head="sha256:" + ("1" * 64),
            target_state_digest="sha256:" + ("2" * 64),
            target_state={"resolved_commit": "a" * 40, "resolved_tree": "b" * 40},
            phase_artifact_digests={},
            supplied_artifact_digests={},
            payload={},
            approved_candidate_digest="",
            recorded_at=100,
        )


def test_transition_persists_set_and_report_and_rederives_on_every_load(tmp_path: Path) -> None:
    store = _operations_ready(tmp_path)
    _build(store)

    record = _last_record(store)
    digests = record["artifact_digests"]
    assert isinstance(digests, dict)
    set_digest = str(digests[SET_KEY])
    report_digest = str(digests[REPORT_KEY])
    evidence_root = (
        tmp_path
        / "run-1"
        / "evidence"
        / "transition-obligations"
        / set_digest.removeprefix("sha256:")
    )
    set_document = json.loads((evidence_root / "set.json").read_text())
    report_document = json.loads(
        (evidence_root / f"{report_digest.removeprefix('sha256:')}.report.json").read_text()
    )

    assert set_document["selector_id"] == "operational-maturity-ratified--building"
    assert report_document["obligation_set_digest"] == set_digest
    assert report_document["resolved_commit"]
    assert report_document["resolved_tree"]
    assert report_document["recorded_at"] > 0
    assert report_document["idempotency_key"].startswith("sha256:")
    assert {result["obligation_id"] for result in report_document["results"]} >= {
        "ledger-anchor",
        "target-subject",
        "external-resume-anchor",
        "generation-readiness",
        "existing-test-expectations",
    }
    assert all(result["passed"] is True for result in report_document["results"])
    assert store.load("run-1").state == RunState.BUILDING


def test_tampered_retained_obligation_report_breaks_authoritative_load(tmp_path: Path) -> None:
    store = _operations_ready(tmp_path)
    _build(store)
    record = _last_record(store)
    digests = record["artifact_digests"]
    assert isinstance(digests, dict)
    set_digest = str(digests[SET_KEY])
    report_digest = str(digests[REPORT_KEY])
    report_path = (
        tmp_path
        / "run-1"
        / "evidence"
        / "transition-obligations"
        / set_digest.removeprefix("sha256:")
        / f"{report_digest.removeprefix('sha256:')}.report.json"
    )
    report = json.loads(report_path.read_text())
    report["results"][0]["criterion"] = "weakened after the fact"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(RunStateError, match="retained obligation report differs"):
        store.load("run-1")


def test_replaying_a_prior_set_digest_for_a_new_transition_is_rejected(tmp_path: Path) -> None:
    store = _operations_ready(tmp_path)
    _build(store)
    prior = _last_record(store)
    prior_digests = prior["artifact_digests"]
    assert isinstance(prior_digests, dict)
    store.transition(
        "run-1",
        RunState.VALIDATING,
        actor="validator",
        artifact_digests=validation_artifacts(candidate=CANDIDATE),
        payload={"tester_identity": "tester"},
        implementer_identity="coder",
    )
    ledger_path = tmp_path / "run-1" / "ledger.jsonl"
    rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    last = rows[-1]
    last["artifact_digests"][SET_KEY] = prior_digests[SET_KEY]
    body = {key: value for key, value in last.items() if key != "entry_hash"}
    last["entry_hash"] = digest_obj(body)
    ledger_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RunStateError, match="obligation-set digest does not re-derive"):
        store.rebuild_projection("run-1")


def test_direct_ledger_append_without_obligation_receipts_is_inadmissible(tmp_path: Path) -> None:
    store = RunStore(tmp_path, clock=_Clock())
    create_intake_run(
        store,
        run_id="run-1",
        target_digest=TARGET,
        source_digest=SOURCE,
    )
    current = store.load("run-1")
    receipts = ratification_receipts("product-specification")
    store._ledger("run-1").append(
        LedgerEntry(
            capability_id="run-1",
            from_state=RunState.INTAKE,
            to_state=RunState.PRODUCT_SPECIFICATION_RATIFIED,
            artifact_digests={
                "product-specification": PRODUCT,
                **receipts,
                "target": TARGET,
                "target-state": current.target_state_digest,
                "source": SOURCE,
                "phase_artifacts": {"product-specification": PRODUCT},
                "generation_artifacts": {},
            },
            payload={},
            actor="validator",
            created_at="500",
        ),
        expected_head=current.ledger_head,
    )

    with pytest.raises(RunStateError, match="state-triggered obligations are invalid"):
        store.rebuild_projection("run-1")


def test_build_requires_external_resume_anchor(tmp_path: Path) -> None:
    store = _operations_ready(tmp_path)
    artifacts = generation_artifacts()
    del artifacts["resume-checkpoint"]

    with pytest.raises(RunStateError, match="resume-checkpoint"):
        store.transition(
            "run-1",
            RunState.BUILDING,
            actor="validator",
            artifact_digests=artifacts,
            payload=build_payload(),
        )


def test_validation_requires_both_immutable_lane_snapshots(tmp_path: Path) -> None:
    store = _operations_ready(tmp_path)
    _build(store)
    artifacts = validation_artifacts(candidate=CANDIDATE)
    del artifacts["tester-output-snapshot"]

    with pytest.raises(RunStateError, match="tester-output-snapshot"):
        store.transition(
            "run-1",
            RunState.VALIDATING,
            actor="validator",
            artifact_digests=artifacts,
            payload={"tester_identity": "tester"},
            implementer_identity="coder",
        )


def test_changed_existing_tests_need_exact_authorization_and_unique_ids(tmp_path: Path) -> None:
    store = _operations_ready(tmp_path)
    artifacts = generation_artifacts()

    with pytest.raises(RunStateError, match="test-change-authorization"):
        store.transition(
            "run-1",
            RunState.BUILDING,
            actor="validator",
            artifact_digests=artifacts,
            payload=build_payload(
                changed_existing_tests=["tests/test_contract.py::test_old_expectation"]
            ),
        )

    incomplete = {
        **generation_artifacts(),
        **standin_test_change_authorization_artifacts(),
    }
    del incomplete["test-change-authorization:validator-receipt"]
    with pytest.raises(RunStateError, match="validator-receipt"):
        store.transition(
            "run-1",
            RunState.BUILDING,
            actor="validator",
            artifact_digests=incomplete,
            payload=build_payload(
                changed_existing_tests=["tests/test_contract.py::test_old_expectation"]
            ),
        )

    with pytest.raises(RunStateError, match="duplicates"):
        _build(
            store,
            payload={
                "changed_existing_tests": ["test-family-a", "test-family-a"],
            },
        )


def test_authorized_test_change_ids_are_bound_into_report(tmp_path: Path) -> None:
    store = _operations_ready(tmp_path)
    _build(
        store,
        payload={
            "changed_existing_tests": ["tests/test_contract.py::test_old_expectation"],
        },
    )
    record = _last_record(store)
    digests = record["artifact_digests"]
    assert isinstance(digests, dict)
    set_digest = str(digests[SET_KEY])
    report_digest = str(digests[REPORT_KEY])
    report = json.loads(
        (
            tmp_path
            / "run-1"
            / "evidence"
            / "transition-obligations"
            / set_digest.removeprefix("sha256:")
            / f"{report_digest.removeprefix('sha256:')}.report.json"
        ).read_text()
    )
    result = next(
        item for item in report["results"] if item["obligation_id"] == "existing-test-expectations"
    )
    assert result["observations"]["changed_existing_tests"] == [
        "tests/test_contract.py::test_old_expectation"
    ]
    assert (
        result["observations"]["artifact_digests"]["test-change-authorization"]
        == (standin_test_change_authorization_artifacts()["test-change-authorization"])
    )
