"""Alert triage that cannot quiet the monitor, and notification that has to be earned.

The published pattern routes a firing monitor to an agent that assesses scope: real issue, push
a fix; noise, tune or delete the monitor. **The second branch is the writer controlling the
judge, relocated to the observability layer.** The cheapest available path to a quiet channel is
deletion, and nothing in the triage step distinguishes *this threshold is badly calibrated* from
*this is correctly detecting something expensive to fix.* Both present as noise to the party who
would have to do the work.

So silencing is treated as what it is — a change to the oracle:

* an agent that evaluates an alert may **not** delete, weaken, or silence the monitor that
  produced it; those are proposals raised as specification defects against the signed phase-3
  monitor set and ratified by a human;
* the ratifying human is not the party that evaluated the alert, for the same reason the
  implementer is not the approver anywhere else in this system; and
* the coordination pattern is state **on the monitor** — a proposed fix reference is appended to
  the monitor so a subsequent trigger finds it and stands down. That is a convenience, never a
  substitute for the ratification rule.

Notification is separate and follows refutation-before-reporting: detection is cheap and should
be exhaustive, notification is expensive and should be earned. A signal reaching a human carries
a human-actionable conclusion and has survived an attempt to refute it; everything else is
recorded. A monitor that fires without an actionable conclusion is the alert wall, and a team
that learns to ignore noisy monitors learns to ignore noisy agents at the same rate.

Pure and stdlib-only: no clock, no disk, no delivery. The caller supplies the trusted human
roster and the evaluation time.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from factory_core.criticality import normalize_label
from factory_core.evidence import EvidenceIntegrity
from factory_core.manifest import SegregationPolicy

TRIAGE_ACTION_INVESTIGATE = "investigate"
TRIAGE_ACTION_PROPOSE_FIX = "propose-fix"
TRIAGE_ACTION_DELETE_MONITOR = "delete-monitor"
TRIAGE_ACTION_WEAKEN_THRESHOLD = "weaken-threshold"
TRIAGE_ACTION_SILENCE_MONITOR = "silence-monitor"

TRIAGE_ACTIONS: tuple[str, ...] = (
    TRIAGE_ACTION_INVESTIGATE,
    TRIAGE_ACTION_PROPOSE_FIX,
    TRIAGE_ACTION_DELETE_MONITOR,
    TRIAGE_ACTION_WEAKEN_THRESHOLD,
    TRIAGE_ACTION_SILENCE_MONITOR,
)

# The three shapes of "make the channel quieter". They are one class, not three cases.
SILENCING_ACTIONS: frozenset[str] = frozenset(
    {
        TRIAGE_ACTION_DELETE_MONITOR,
        TRIAGE_ACTION_WEAKEN_THRESHOLD,
        TRIAGE_ACTION_SILENCE_MONITOR,
    }
)

TRIAGE_DISPOSITION_ALLOWED = "allowed"
TRIAGE_DISPOSITION_SPECIFICATION_DEFECT_REQUIRED = "specification-defect-required"
TRIAGE_DISPOSITION_DENIED = "denied"

NOTIFICATION_NOTIFY_HUMAN = "notify-human"
NOTIFICATION_RECORD_ONLY = "record-only"


@dataclass(frozen=True)
class MonitorChangeRatification:
    """A human's ratification of a monitor change through the specification-defect path."""

    defect_id: str
    monitor_id: str
    action: str
    ratified_by: str
    expires_at: int = 0
    evidence: EvidenceIntegrity | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "monitor_id", normalize_label(self.monitor_id))
        object.__setattr__(self, "action", normalize_label(self.action))

    def authority_body(self) -> dict[str, Any]:
        """The exact fields the ratification evidence must bind."""

        return {
            "defect_id": self.defect_id,
            "monitor_id": self.monitor_id,
            "action": self.action,
            "ratified_by": self.ratified_by,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "defect_id": self.defect_id,
            "monitor_id": self.monitor_id,
            "action": self.action,
            "ratified_by": self.ratified_by,
            "expires_at": self.expires_at,
            "evidence": self.evidence.to_dict() if self.evidence is not None else None,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> MonitorChangeRatification:
        evidence_raw = raw.get("evidence")
        expires_raw = raw.get("expires_at", 0)
        try:
            expires_at = int(expires_raw)
        except (TypeError, ValueError):
            expires_at = 0
        return cls(
            defect_id=str(raw.get("defect_id", "")),
            monitor_id=str(raw.get("monitor_id", "")),
            action=str(raw.get("action", "")),
            ratified_by=str(raw.get("ratified_by", "")),
            expires_at=expires_at,
            evidence=EvidenceIntegrity.from_dict(
                evidence_raw if isinstance(evidence_raw, Mapping) else None
            ),
        )


@dataclass(frozen=True)
class TriageRequest:
    """One triage step against one firing monitor."""

    alert_id: str
    monitor_id: str
    actor: str
    action: str
    fix_reference: str = ""
    ratification: MonitorChangeRatification | None = None
    evaluated_at: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "monitor_id", normalize_label(self.monitor_id))
        object.__setattr__(self, "action", normalize_label(self.action))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> TriageRequest:
        ratification_raw = raw.get("ratification")
        evaluated_raw = raw.get("evaluated_at", 0)
        try:
            evaluated_at = int(evaluated_raw)
        except (TypeError, ValueError):
            evaluated_at = 0
        return cls(
            alert_id=str(raw.get("alert_id", "")),
            monitor_id=str(raw.get("monitor_id", "")),
            actor=str(raw.get("actor", "")),
            action=str(raw.get("action", "")),
            fix_reference=str(raw.get("fix_reference", "")),
            ratification=(
                MonitorChangeRatification.from_dict(ratification_raw)
                if isinstance(ratification_raw, Mapping)
                else None
            ),
            evaluated_at=evaluated_at,
        )


@dataclass(frozen=True)
class TriageDecision:
    """What the triage step may do, and what the monitor should carry afterwards."""

    alert_id: str
    monitor_id: str
    action: str
    disposition: str
    reasons: tuple[str, ...] = ()
    appended_fix_reference: str = ""

    @property
    def allowed(self) -> bool:
        return self.disposition == TRIAGE_DISPOSITION_ALLOWED

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "monitor_id": self.monitor_id,
            "action": self.action,
            "disposition": self.disposition,
            "allowed": self.allowed,
            "reasons": list(self.reasons),
            "appended_fix_reference": self.appended_fix_reference,
        }


def decide_triage(request: TriageRequest, policy: SegregationPolicy) -> TriageDecision:
    """Decide one triage action against the monitor that produced the alert.

    Investigating is always available. Proposing a fix is available and appends its reference to
    the monitor. Deleting, weakening, or silencing is never available to the evaluating party:
    it requires a human ratification through the specification-defect path, bound to this exact
    monitor and action, and ratified by someone other than the evaluator.
    """

    reasons: list[str] = []
    if not request.monitor_id:
        reasons.append("triage-monitor-id-missing")
    if not request.alert_id.strip():
        reasons.append("triage-alert-id-missing")
    if not request.actor.strip():
        reasons.append("triage-actor-missing")
    if not request.action:
        reasons.append("triage-action-missing")
    elif request.action not in TRIAGE_ACTIONS:
        reasons.append(f"triage-action-unknown:{request.action}")

    if reasons:
        return TriageDecision(
            alert_id=request.alert_id,
            monitor_id=request.monitor_id,
            action=request.action,
            disposition=TRIAGE_DISPOSITION_DENIED,
            reasons=tuple(dict.fromkeys(reasons)),
        )

    if request.action == TRIAGE_ACTION_INVESTIGATE:
        return TriageDecision(
            alert_id=request.alert_id,
            monitor_id=request.monitor_id,
            action=request.action,
            disposition=TRIAGE_DISPOSITION_ALLOWED,
        )

    if request.action == TRIAGE_ACTION_PROPOSE_FIX:
        if not request.fix_reference.strip():
            return TriageDecision(
                alert_id=request.alert_id,
                monitor_id=request.monitor_id,
                action=request.action,
                disposition=TRIAGE_DISPOSITION_DENIED,
                reasons=("triage-fix-reference-missing",),
            )
        return TriageDecision(
            alert_id=request.alert_id,
            monitor_id=request.monitor_id,
            action=request.action,
            disposition=TRIAGE_DISPOSITION_ALLOWED,
            appended_fix_reference=request.fix_reference.strip(),
        )

    return _decide_silencing(request, policy)


def _decide_silencing(request: TriageRequest, policy: SegregationPolicy) -> TriageDecision:
    reasons: list[str] = []
    ratification = request.ratification
    if ratification is None:
        reasons.append("silencing-requires-human-ratified-specification-defect")
    else:
        if not ratification.defect_id.strip():
            reasons.append("silencing-specification-defect-id-missing")
        if ratification.monitor_id != request.monitor_id:
            reasons.append("silencing-ratification-monitor-mismatch")
        if ratification.action != request.action:
            reasons.append("silencing-ratification-action-mismatch")
        ratifier = policy.resolve_human(ratification.ratified_by)
        if ratifier is None:
            reasons.append("silencing-ratifier-not-enrolled-human")
        elif ratifier == policy.canonical(request.actor):
            # The evaluating party does not get to ratify its own quiet channel.
            reasons.append("silencing-ratifier-equals-evaluator")
        if request.evaluated_at <= 0 or ratification.expires_at <= request.evaluated_at:
            reasons.append("silencing-ratification-expired")
        evidence = ratification.evidence
        if evidence is None or not evidence.present:
            reasons.append("silencing-ratification-evidence-missing")
        elif not evidence.verify():
            reasons.append("silencing-ratification-evidence-digest-mismatch")
        elif not evidence.verifies_binding(ratification.authority_body()):
            reasons.append("silencing-ratification-evidence-subject-mismatch")

    if reasons:
        return TriageDecision(
            alert_id=request.alert_id,
            monitor_id=request.monitor_id,
            action=request.action,
            disposition=TRIAGE_DISPOSITION_SPECIFICATION_DEFECT_REQUIRED,
            reasons=tuple(dict.fromkeys(reasons)),
        )
    return TriageDecision(
        alert_id=request.alert_id,
        monitor_id=request.monitor_id,
        action=request.action,
        disposition=TRIAGE_DISPOSITION_ALLOWED,
    )


@dataclass(frozen=True)
class AlertAssessment:
    """One assessed firing, ready for the notify-or-record decision."""

    alert_id: str
    monitor_id: str
    actionable_conclusion: str = ""
    survived_refutation: bool = False
    monitor_stands_down: bool = False

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> AlertAssessment:
        return cls(
            alert_id=str(raw.get("alert_id", "")),
            monitor_id=str(raw.get("monitor_id", "")),
            actionable_conclusion=str(raw.get("actionable_conclusion", "")),
            survived_refutation=bool(raw.get("survived_refutation", False)),
            monitor_stands_down=bool(raw.get("monitor_stands_down", False)),
        )


@dataclass(frozen=True)
class NotificationDecision:
    """Whether this firing reaches a human, and why."""

    alert_id: str
    monitor_id: str
    disposition: str
    reasons: tuple[str, ...] = ()

    @property
    def notifies_human(self) -> bool:
        return self.disposition == NOTIFICATION_NOTIFY_HUMAN

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "monitor_id": self.monitor_id,
            "disposition": self.disposition,
            "notifies_human": self.notifies_human,
            "reasons": list(self.reasons),
        }


def decide_notification(assessment: AlertAssessment) -> NotificationDecision:
    """Decide whether a firing is notified or only recorded.

    Detection is exhaustive; every firing is recorded either way. Reaching a human requires a
    human-actionable conclusion and a finding that survived an attempt to refute it. A monitor
    already carrying a proposed fix stands down: the human has the fix in flight, and a second
    page adds noise rather than information.
    """

    reasons: list[str] = []
    if not assessment.actionable_conclusion.strip():
        reasons.append("no-human-actionable-conclusion")
    if not assessment.survived_refutation:
        reasons.append("finding-not-refuted-before-reporting")
    if assessment.monitor_stands_down:
        reasons.append("monitor-carries-proposed-fix")

    if reasons:
        return NotificationDecision(
            alert_id=assessment.alert_id,
            monitor_id=assessment.monitor_id,
            disposition=NOTIFICATION_RECORD_ONLY,
            reasons=tuple(dict.fromkeys(reasons)),
        )
    return NotificationDecision(
        alert_id=assessment.alert_id,
        monitor_id=assessment.monitor_id,
        disposition=NOTIFICATION_NOTIFY_HUMAN,
    )
