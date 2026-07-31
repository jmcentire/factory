"""Triage that cannot silence the monitor it evaluated, and notification that is earned."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from factory_core.evidence import EvidenceIntegrity
from factory_core.manifest import SegregationPolicy, digest_obj
from factory_core.triage import (
    NOTIFICATION_NOTIFY_HUMAN,
    NOTIFICATION_RECORD_ONLY,
    SILENCING_ACTIONS,
    TRIAGE_ACTION_DELETE_MONITOR,
    TRIAGE_ACTION_INVESTIGATE,
    TRIAGE_ACTION_PROPOSE_FIX,
    TRIAGE_ACTION_SILENCE_MONITOR,
    TRIAGE_ACTION_WEAKEN_THRESHOLD,
    TRIAGE_DISPOSITION_ALLOWED,
    TRIAGE_DISPOSITION_DENIED,
    TRIAGE_DISPOSITION_SPECIFICATION_DEFECT_REQUIRED,
    AlertAssessment,
    MonitorChangeRatification,
    TriageRequest,
    decide_notification,
    decide_triage,
)


def _roster() -> SegregationPolicy:
    return SegregationPolicy(
        human_ids=frozenset({"alice", "carol"}),
        human_aliases={"alice": "alice", "alice@example.com": "alice", "carol": "carol"},
        excluded_service_identities=frozenset({"*-bot", "factory-agent", "claude*"}),
    )


def _evidence(body: dict[str, Any]) -> EvidenceIntegrity:
    return EvidenceIntegrity(body=body, claimed_digest=digest_obj(body))


def _ratification(
    *,
    action: str = TRIAGE_ACTION_DELETE_MONITOR,
    monitor_id: str = "monitor-1",
    ratified_by: str = "carol",
    expires_at: int = 200,
) -> MonitorChangeRatification:
    record = MonitorChangeRatification(
        defect_id="spec-defect-9",
        monitor_id=monitor_id,
        action=action,
        ratified_by=ratified_by,
        expires_at=expires_at,
    )
    return replace(record, evidence=_evidence(record.authority_body()))


def _request(**overrides: Any) -> TriageRequest:
    values: dict[str, Any] = {
        "alert_id": "alert-1",
        "monitor_id": "monitor-1",
        "actor": "factory-agent",
        "action": TRIAGE_ACTION_INVESTIGATE,
        "evaluated_at": 100,
    }
    values.update(overrides)
    return TriageRequest(**values)


def test_investigating_is_always_available() -> None:
    decision = decide_triage(_request(), _roster())

    assert decision.allowed is True
    assert decision.disposition == TRIAGE_DISPOSITION_ALLOWED


def test_proposing_a_fix_appends_state_to_the_monitor() -> None:
    decision = decide_triage(
        _request(action=TRIAGE_ACTION_PROPOSE_FIX, fix_reference="change-42"),
        _roster(),
    )
    without_reference = decide_triage(_request(action=TRIAGE_ACTION_PROPOSE_FIX), _roster())

    assert decision.allowed is True
    assert decision.appended_fix_reference == "change-42"
    assert without_reference.disposition == TRIAGE_DISPOSITION_DENIED
    assert "triage-fix-reference-missing" in without_reference.reasons


def test_no_silencing_action_is_available_to_the_evaluating_agent() -> None:
    for action in sorted(SILENCING_ACTIONS):
        decision = decide_triage(_request(action=action), _roster())

        assert decision.allowed is False, action
        assert decision.disposition == TRIAGE_DISPOSITION_SPECIFICATION_DEFECT_REQUIRED, action
        assert "silencing-requires-human-ratified-specification-defect" in decision.reasons


def test_a_human_ratified_specification_defect_authorizes_the_change() -> None:
    decision = decide_triage(
        _request(action=TRIAGE_ACTION_DELETE_MONITOR, ratification=_ratification()),
        _roster(),
    )

    assert decision.allowed is True


def test_the_evaluator_cannot_ratify_its_own_quiet_channel() -> None:
    decision = decide_triage(
        _request(
            actor="alice@example.com",
            action=TRIAGE_ACTION_WEAKEN_THRESHOLD,
            ratification=_ratification(action=TRIAGE_ACTION_WEAKEN_THRESHOLD, ratified_by="alice"),
        ),
        _roster(),
    )

    # An alias of the evaluator is still the evaluator.
    assert "silencing-ratifier-equals-evaluator" in decision.reasons
    assert decision.allowed is False


def test_a_ratification_binds_this_monitor_this_action_and_an_enrolled_human() -> None:
    wrong_monitor = decide_triage(
        _request(
            action=TRIAGE_ACTION_DELETE_MONITOR,
            ratification=_ratification(monitor_id="monitor-2"),
        ),
        _roster(),
    )
    wrong_action = decide_triage(
        _request(
            action=TRIAGE_ACTION_DELETE_MONITOR,
            ratification=_ratification(action=TRIAGE_ACTION_SILENCE_MONITOR),
        ),
        _roster(),
    )
    agent_ratifier = decide_triage(
        _request(
            action=TRIAGE_ACTION_DELETE_MONITOR,
            ratification=_ratification(ratified_by="triage-bot"),
        ),
        _roster(),
    )
    expired = decide_triage(
        _request(
            action=TRIAGE_ACTION_DELETE_MONITOR,
            ratification=_ratification(expires_at=50),
        ),
        _roster(),
    )

    assert "silencing-ratification-monitor-mismatch" in wrong_monitor.reasons
    assert "silencing-ratification-action-mismatch" in wrong_action.reasons
    assert "silencing-ratifier-not-enrolled-human" in agent_ratifier.reasons
    assert "silencing-ratification-expired" in expired.reasons


def test_ratification_evidence_must_be_present_intact_and_subject_bound() -> None:
    unsigned = MonitorChangeRatification(
        defect_id="spec-defect-9",
        monitor_id="monitor-1",
        action=TRIAGE_ACTION_DELETE_MONITOR,
        ratified_by="carol",
        expires_at=200,
    )
    missing = decide_triage(
        _request(action=TRIAGE_ACTION_DELETE_MONITOR, ratification=unsigned),
        _roster(),
    )
    tampered = decide_triage(
        _request(
            action=TRIAGE_ACTION_DELETE_MONITOR,
            ratification=replace(
                unsigned,
                evidence=EvidenceIntegrity(
                    body=unsigned.authority_body(),
                    claimed_digest=digest_obj({"defect_id": "other"}),
                ),
            ),
        ),
        _roster(),
    )
    wrong_subject = decide_triage(
        _request(
            action=TRIAGE_ACTION_DELETE_MONITOR,
            ratification=replace(
                unsigned,
                evidence=_evidence({"defect_id": "spec-defect-9", "monitor_id": "monitor-1"}),
            ),
        ),
        _roster(),
    )

    assert "silencing-ratification-evidence-missing" in missing.reasons
    assert "silencing-ratification-evidence-digest-mismatch" in tampered.reasons
    assert "silencing-ratification-evidence-subject-mismatch" in wrong_subject.reasons


def test_malformed_or_unknown_triage_requests_deny() -> None:
    unknown = decide_triage(_request(action="mute-for-now"), _roster())
    unaddressed = decide_triage(_request(monitor_id="", actor="", action=""), _roster())

    assert unknown.disposition == TRIAGE_DISPOSITION_DENIED
    assert "triage-action-unknown:mute-for-now" in unknown.reasons
    assert unaddressed.disposition == TRIAGE_DISPOSITION_DENIED
    assert "triage-monitor-id-missing" in unaddressed.reasons
    assert "triage-actor-missing" in unaddressed.reasons
    assert "triage-action-missing" in unaddressed.reasons


def test_notification_is_earned_by_an_actionable_refuted_conclusion() -> None:
    notified = decide_notification(
        AlertAssessment(
            alert_id="alert-1",
            monitor_id="monitor-1",
            actionable_conclusion="Roll back revision 41; the criterion is unmet.",
            survived_refutation=True,
        )
    )
    unactionable = decide_notification(
        AlertAssessment(alert_id="alert-1", monitor_id="monitor-1", survived_refutation=True)
    )
    unrefuted = decide_notification(
        AlertAssessment(
            alert_id="alert-1",
            monitor_id="monitor-1",
            actionable_conclusion="Something looks off.",
        )
    )

    assert notified.disposition == NOTIFICATION_NOTIFY_HUMAN
    assert unactionable.disposition == NOTIFICATION_RECORD_ONLY
    assert "no-human-actionable-conclusion" in unactionable.reasons
    assert unrefuted.disposition == NOTIFICATION_RECORD_ONLY
    assert "finding-not-refuted-before-reporting" in unrefuted.reasons


def test_a_monitor_carrying_a_proposed_fix_stands_down() -> None:
    decision = decide_notification(
        AlertAssessment(
            alert_id="alert-2",
            monitor_id="monitor-1",
            actionable_conclusion="Roll back revision 41.",
            survived_refutation=True,
            monitor_stands_down=True,
        )
    )

    assert decision.disposition == NOTIFICATION_RECORD_ONLY
    assert "monitor-carries-proposed-fix" in decision.reasons


def test_requests_and_decisions_round_trip_for_the_record() -> None:
    request = TriageRequest.from_dict(
        {
            "alert_id": "alert-1",
            "monitor_id": "Monitor-1",
            "actor": "factory-agent",
            "action": "Delete-Monitor",
            "evaluated_at": "100",
            "ratification": _ratification().to_dict(),
        }
    )

    decision = decide_triage(request, _roster())

    assert request.monitor_id == "monitor-1"
    assert request.action == TRIAGE_ACTION_DELETE_MONITOR
    assert request.evaluated_at == 100
    assert decision.to_dict()["allowed"] is True
