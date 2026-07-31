"""Red-now / green-now controls, the red-guard rule, and the reproduction requirement."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from factory_core.correction import (
    BASELINE_RESULT_FAILED,
    BASELINE_RESULT_PASSED,
    CONTROL_GREEN_NOW,
    CONTROL_RECOGNITION_CHECK,
    CONTROL_RED_NOW,
    CONTROL_ROUTE_HUMAN,
    CONTROL_SATISFIED,
    CONTROL_SUSPECTED_OVER_CONSTRAINT,
    FAILURE_RELATION_DEFECT,
    FAILURE_RELATION_UNRELATED,
    REPRODUCTION_IMPOSSIBLE,
    REPRODUCTION_NOT_REPRODUCED,
    REPRODUCTION_REPRODUCED,
    ControlObservation,
    CorrectionRecord,
    ReproductionRecord,
    classify_control,
    normalize_control_role,
    verify_correction,
)
from factory_core.evidence import EvidenceIntegrity
from factory_core.manifest import digest_obj


def _evidence(body: dict[str, Any]) -> EvidenceIntegrity:
    return EvidenceIntegrity(body=body, claimed_digest=digest_obj(body))


def _reproduction(**overrides: Any) -> ReproductionRecord:
    values: dict[str, Any] = {
        "defect_id": "defect-1",
        "result": REPRODUCTION_REPRODUCED,
        "environment_id": "ephemeral-1",
        "disposable_environment": True,
        "recorded_before_repair": True,
    }
    values.update(overrides)
    record = ReproductionRecord(**values)
    if "evidence" in overrides:
        return record
    return replace(record, evidence=_evidence(record.authority_body()))


def _forcing() -> ControlObservation:
    return ControlObservation(
        test_id="forces-the-defect",
        declared_role=CONTROL_RED_NOW,
        baseline_result=BASELINE_RESULT_FAILED,
        failure_relation=FAILURE_RELATION_DEFECT,
    )


def _guard() -> ControlObservation:
    return ControlObservation(
        test_id="guards-unrelated-behavior",
        declared_role=CONTROL_GREEN_NOW,
        baseline_result=BASELINE_RESULT_PASSED,
    )


def _record(**overrides: Any) -> CorrectionRecord:
    values: dict[str, Any] = {
        "defect_id": "defect-1",
        "baseline_available": True,
        "controls": (_forcing(), _guard()),
        "reproduction": _reproduction(),
    }
    values.update(overrides)
    return CorrectionRecord(**values)


def test_the_logical_control_names_normalize_onto_the_operational_ones() -> None:
    assert normalize_control_role("Negative") == CONTROL_RED_NOW
    assert normalize_control_role("positive") == CONTROL_GREEN_NOW
    assert normalize_control_role(" red-now ") == CONTROL_RED_NOW


def test_both_controls_are_satisfied_by_their_expected_behavior() -> None:
    assert classify_control(_forcing()).disposition == CONTROL_SATISFIED
    assert classify_control(_guard()).disposition == CONTROL_SATISFIED


def test_a_red_guard_is_a_suspected_over_constraint_and_keeps_its_declared_role() -> None:
    red_guard = replace(
        _guard(),
        baseline_result=BASELINE_RESULT_FAILED,
        failure_relation=FAILURE_RELATION_UNRELATED,
    )

    classification = classify_control(red_guard)

    assert classification.disposition == CONTROL_SUSPECTED_OVER_CONSTRAINT
    assert classification.reason == "green-now-guard-failed-on-unrelated-behavior"
    # It is raised, not repurposed: the declared role survives the classification.
    assert classification.declared_role == CONTROL_GREEN_NOW


def test_no_result_can_turn_a_declared_guard_into_a_forcing_test() -> None:
    for baseline in (BASELINE_RESULT_FAILED, BASELINE_RESULT_PASSED):
        for relation in ("", FAILURE_RELATION_DEFECT, FAILURE_RELATION_UNRELATED):
            classification = classify_control(
                replace(_guard(), baseline_result=baseline, failure_relation=relation)
            )

            assert classification.declared_role == CONTROL_GREEN_NOW, (baseline, relation)
            assert classification.disposition != CONTROL_SATISFIED or baseline == (
                BASELINE_RESULT_PASSED
            )


def test_a_guard_failing_on_defect_behavior_routes_to_the_human() -> None:
    classification = classify_control(
        replace(
            _guard(),
            baseline_result=BASELINE_RESULT_FAILED,
            failure_relation=FAILURE_RELATION_DEFECT,
        )
    )

    assert classification.disposition == CONTROL_ROUTE_HUMAN
    assert classification.reason == "green-now-guard-failed-on-defect-behavior"


def test_an_already_green_forcing_test_is_the_recognition_check() -> None:
    classification = classify_control(
        replace(_forcing(), baseline_result=BASELINE_RESULT_PASSED, failure_relation="")
    )

    assert classification.disposition == CONTROL_RECOGNITION_CHECK
    assert classification.reason == "red-now-test-already-green-against-baseline"


def test_undeclared_unknown_or_unobserved_roles_route_to_the_human() -> None:
    undeclared = classify_control(replace(_forcing(), declared_role=""))
    unknown = classify_control(replace(_forcing(), declared_role="probably-red"))
    unobserved = classify_control(replace(_forcing(), baseline_result=""))
    unaddressed = classify_control(replace(_forcing(), test_id=""))
    unrelated_forcing = classify_control(
        replace(_forcing(), failure_relation=FAILURE_RELATION_UNRELATED)
    )

    assert undeclared.reason == "control-role-undeclared"
    assert unknown.reason == "control-role-unknown:probably-red"
    assert unobserved.reason == "control-baseline-result-missing"
    assert unaddressed.reason == "control-test-id-missing"
    assert unrelated_forcing.reason == "red-now-fails-away-from-defect"
    for classification in (undeclared, unknown, unobserved, unaddressed, unrelated_forcing):
        assert classification.disposition == CONTROL_ROUTE_HUMAN


def test_a_complete_correction_record_is_satisfied() -> None:
    report = verify_correction(_record())

    assert report.satisfied is True
    assert "reproduction-recorded" in report.reports


def test_a_suspected_over_constraint_gates_the_promotion_for_a_human() -> None:
    red_guard = replace(
        _guard(),
        baseline_result=BASELINE_RESULT_FAILED,
        failure_relation=FAILURE_RELATION_UNRELATED,
    )

    report = verify_correction(_record(controls=(_forcing(), red_guard)))

    assert "suspected-over-constraint:guards-unrelated-behavior" in report.gate_reasons
    assert report.failures == ()


def test_an_unsatisfied_negative_control_rejects_rather_than_gaps() -> None:
    report = verify_correction(_record(controls=(_guard(),)))

    assert "negative-control-unsatisfied" in report.failures


def test_an_unobserved_positive_control_is_a_gap() -> None:
    report = verify_correction(_record(controls=(_forcing(),)))

    assert "positive-control-unobserved" in report.gaps


def test_a_greenfield_repair_gates_regardless_of_class() -> None:
    report = verify_correction(_record(baseline_available=False, controls=()))

    assert "greenfield-repair-without-baseline" in report.gate_reasons
    # No baseline means no controls to be missing; the weakness is the lane, not an absence.
    assert "correction-controls-missing" not in report.gaps


def test_absent_records_and_duplicate_observations_are_distinguished() -> None:
    absent = verify_correction(None)
    no_controls = verify_correction(_record(controls=()))
    duplicated = verify_correction(_record(controls=(_forcing(), _forcing(), _guard())))
    unaddressed = verify_correction(_record(defect_id=""))

    assert absent.gaps == ("correction-record-missing",)
    assert "correction-controls-missing" in no_controls.gaps
    assert "control-observation-duplicate:forces-the-defect" in duplicated.integrity_issues
    assert "correction-defect-id-missing" in unaddressed.gaps


def test_a_missing_reproduction_is_a_gap_disposed_by_class() -> None:
    report = verify_correction(_record(reproduction=None))

    assert "reproduction-missing" in report.gaps
    assert report.failures == ()


def test_a_reproduction_recorded_after_the_repair_is_not_a_negative_control() -> None:
    report = verify_correction(
        _record(reproduction=_reproduction(recorded_before_repair=False)),
    )

    assert "reproduction-not-recorded-before-repair" in report.failures


def test_a_reproduction_outside_a_disposable_environment_is_rejected() -> None:
    report = verify_correction(_record(reproduction=_reproduction(disposable_environment=False)))

    assert "reproduction-environment-not-disposable" in report.failures


def test_a_defect_that_did_not_reproduce_routes_to_the_human() -> None:
    report = verify_correction(
        _record(reproduction=_reproduction(result=REPRODUCTION_NOT_REPRODUCED)),
    )

    assert "reproduction-did-not-reproduce" in report.gate_reasons
    assert report.failures == ()


def test_reproduction_impossible_gates_only_with_a_stated_condition() -> None:
    stated = verify_correction(
        _record(
            reproduction=_reproduction(
                result=REPRODUCTION_IMPOSSIBLE,
                impossibility_condition="The race does not reproduce outside production load.",
            )
        )
    )
    unstated = verify_correction(
        _record(reproduction=_reproduction(result=REPRODUCTION_IMPOSSIBLE)),
    )

    assert "reproduction-impossible-condition-stated" in stated.gate_reasons
    assert "reproduction-impossibility-condition-missing" in unstated.gaps


def test_reproduction_evidence_must_be_present_intact_and_subject_bound() -> None:
    unsigned = _reproduction(evidence=None)
    missing = verify_correction(_record(reproduction=unsigned))
    tampered = verify_correction(
        _record(
            reproduction=replace(
                unsigned,
                evidence=EvidenceIntegrity(
                    body=unsigned.authority_body(),
                    claimed_digest=digest_obj({"defect_id": "other"}),
                ),
            )
        )
    )
    wrong_subject = verify_correction(
        _record(
            reproduction=replace(
                unsigned,
                evidence=_evidence({"defect_id": "defect-1", "result": REPRODUCTION_REPRODUCED}),
            )
        )
    )
    mismatched = verify_correction(_record(reproduction=_reproduction(defect_id="defect-2")))
    unknown_result = verify_correction(_record(reproduction=_reproduction(result="maybe")))
    unrecorded_result = verify_correction(_record(reproduction=_reproduction(result="")))

    assert "reproduction-evidence-missing" in missing.gaps
    assert "reproduction-evidence-digest-mismatch" in tampered.integrity_issues
    assert "reproduction-evidence-subject-mismatch" in wrong_subject.integrity_issues
    assert "reproduction-defect-mismatch" in mismatched.integrity_issues
    assert "reproduction-result-unknown:maybe" in unknown_result.integrity_issues
    assert "reproduction-result-unrecorded" in unrecorded_result.gaps


def test_record_round_trips_through_dicts() -> None:
    record = _record()

    restored = CorrectionRecord.from_dict(json.loads(json.dumps(record.to_dict())))

    assert restored == record
    assert verify_correction(restored).satisfied is True
