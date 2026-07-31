"""The correction flow's two controls, its red-guard rule, and its reproduction requirement.

In a correction the Tester authors against the one oracle this flow trusts — the pre-defect
behavior of main — and two controls bound the spec from both sides:

* the **negative** control, operationally **red-now**: new tests must fail against current broken
  main, at least one failing on the defect. Otherwise the spec is too weak and is rejected.
* the **positive** control, operationally **green-now**: new tests must pass against main on
  behavior unrelated to the defect. Otherwise the spec is too strong.

The operational names lead because they say what the test does today against main, which is what
the author has to get right. Both names are kept.

This module exists for the failing leg the controls left unnamed. **A green guard that comes back
red is not a forcing test.** The two are indistinguishable in the run — same red signal, opposite
meaning — and the natural move, reclassifying the guard as forcing and driving an implementation
to satisfy it, silently encodes a change to previously-correct behavior with a green suite
defending it. So a guard that fails against main on unrelated behavior is a **suspected
over-constraint** that stops and routes to a human, and this classifier structurally cannot
return a forcing role for a test declared as a guard: the declared role is an input, never an
inference from the result.

The mirror case is cheap and useful: a test declared red-now that is **already green** against
main is the negative control failing early — the defect is misunderstood or already fixed, and
that is worth knowing before any implementation effort is spent.

It also carries the requirement the flow was missing. **A defect is reproduced in a disposable
environment before any repair is written, and the reproduction is recorded.** The reproduction
failing is the negative control for a production defect: it establishes that the fault is real,
that it is understood well enough to trigger deliberately, and that the eventual fix has
something to be verified against. A repair written against a defect nobody reproduced is a repair
against a hypothesis.

Pure and stdlib-only: no clock, no disk, no test execution. Every observation is caller-supplied.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from factory_core.criticality import normalize_label
from factory_core.evidence import EvidenceIntegrity

LANE_CAPABILITY = "capability"
LANE_CORRECTION = "correction"

LANES: tuple[str, ...] = (LANE_CAPABILITY, LANE_CORRECTION)

CONTROL_RED_NOW = "red-now"
CONTROL_GREEN_NOW = "green-now"

CONTROL_ROLES: tuple[str, ...] = (CONTROL_RED_NOW, CONTROL_GREEN_NOW)

# The logical names remain valid vocabulary; they normalize onto the operational ones so a
# manifest written either way classifies identically.
CONTROL_ROLE_ALIASES: Mapping[str, str] = {
    "negative": CONTROL_RED_NOW,
    "positive": CONTROL_GREEN_NOW,
}

BASELINE_RESULT_FAILED = "failed"
BASELINE_RESULT_PASSED = "passed"

BASELINE_RESULTS: tuple[str, ...] = (BASELINE_RESULT_FAILED, BASELINE_RESULT_PASSED)

FAILURE_RELATION_DEFECT = "defect"
FAILURE_RELATION_UNRELATED = "unrelated"

FAILURE_RELATIONS: tuple[str, ...] = (FAILURE_RELATION_DEFECT, FAILURE_RELATION_UNRELATED)

CONTROL_SATISFIED = "satisfied"
CONTROL_SUSPECTED_OVER_CONSTRAINT = "suspected-over-constraint"
CONTROL_RECOGNITION_CHECK = "recognition-check"
CONTROL_ROUTE_HUMAN = "route-human"

REPRODUCTION_REPRODUCED = "reproduced"
REPRODUCTION_NOT_REPRODUCED = "not-reproduced"
REPRODUCTION_IMPOSSIBLE = "impossible"

REPRODUCTION_RESULTS: tuple[str, ...] = (
    REPRODUCTION_REPRODUCED,
    REPRODUCTION_NOT_REPRODUCED,
    REPRODUCTION_IMPOSSIBLE,
)


def normalize_control_role(value: str) -> str:
    """Normalize a declared control role, accepting the logical aliases."""

    label = normalize_label(value)
    return CONTROL_ROLE_ALIASES.get(label, label)


@dataclass(frozen=True)
class ControlObservation:
    """One new test, the role it was declared as, and what it did against the baseline.

    ``declared_role`` is recorded before the run. That ordering is the control: a role inferred
    after seeing the result is not a classification, it is a rationalization.
    """

    test_id: str
    declared_role: str = ""
    baseline_result: str = ""
    failure_relation: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "test_id", normalize_label(self.test_id))
        object.__setattr__(self, "declared_role", normalize_control_role(self.declared_role))
        object.__setattr__(self, "baseline_result", normalize_label(self.baseline_result))
        object.__setattr__(self, "failure_relation", normalize_label(self.failure_relation))

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "declared_role": self.declared_role,
            "baseline_result": self.baseline_result,
            "failure_relation": self.failure_relation,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ControlObservation:
        return cls(
            test_id=str(raw.get("test_id", "")),
            declared_role=str(raw.get("declared_role", "")),
            baseline_result=str(raw.get("baseline_result", "")),
            failure_relation=str(raw.get("failure_relation", "")),
        )


@dataclass(frozen=True)
class ControlClassification:
    """The disposition of one control observation. The declared role is never rewritten."""

    test_id: str
    declared_role: str
    disposition: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "declared_role": self.declared_role,
            "disposition": self.disposition,
            "reason": self.reason,
        }


def classify_control(observation: ControlObservation) -> ControlClassification:
    """Classify one test's behavior against the baseline without reclassifying its role.

    Every disposition other than ``satisfied`` stops for a human. In particular a red guard is
    raised, not repurposed: there is no input to this function that turns a declared green-now
    test into a red-now forcing test.
    """

    def classified(disposition: str, reason: str) -> ControlClassification:
        return ControlClassification(
            test_id=observation.test_id,
            declared_role=observation.declared_role,
            disposition=disposition,
            reason=reason,
        )

    if not observation.test_id:
        return classified(CONTROL_ROUTE_HUMAN, "control-test-id-missing")
    if not observation.declared_role:
        return classified(CONTROL_ROUTE_HUMAN, "control-role-undeclared")
    if observation.declared_role not in CONTROL_ROLES:
        return classified(CONTROL_ROUTE_HUMAN, f"control-role-unknown:{observation.declared_role}")
    if observation.baseline_result not in BASELINE_RESULTS:
        return classified(CONTROL_ROUTE_HUMAN, "control-baseline-result-missing")

    if observation.declared_role == CONTROL_RED_NOW:
        if observation.baseline_result == BASELINE_RESULT_PASSED:
            # The negative control failing early, before implementation effort is spent.
            return classified(
                CONTROL_RECOGNITION_CHECK,
                "red-now-test-already-green-against-baseline",
            )
        if observation.failure_relation == FAILURE_RELATION_DEFECT:
            return classified(CONTROL_SATISFIED, "red-now-fails-on-defect")
        if observation.failure_relation == FAILURE_RELATION_UNRELATED:
            return classified(CONTROL_ROUTE_HUMAN, "red-now-fails-away-from-defect")
        return classified(CONTROL_ROUTE_HUMAN, "red-now-failure-relation-unrecorded")

    if observation.baseline_result == BASELINE_RESULT_PASSED:
        return classified(CONTROL_SATISFIED, "green-now-passes-against-baseline")
    if observation.failure_relation == FAILURE_RELATION_UNRELATED:
        # The case the controls previously left unnamed. It stops here.
        return classified(
            CONTROL_SUSPECTED_OVER_CONSTRAINT,
            "green-now-guard-failed-on-unrelated-behavior",
        )
    if observation.failure_relation == FAILURE_RELATION_DEFECT:
        return classified(CONTROL_ROUTE_HUMAN, "green-now-guard-failed-on-defect-behavior")
    return classified(CONTROL_ROUTE_HUMAN, "green-now-failure-relation-unrecorded")


@dataclass(frozen=True)
class ReproductionRecord:
    """The recorded attempt to trigger the defect deliberately, before any repair."""

    defect_id: str
    result: str = ""
    environment_id: str = ""
    disposable_environment: bool = False
    recorded_before_repair: bool = False
    impossibility_condition: str = ""
    evidence: EvidenceIntegrity | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "result", normalize_label(self.result))

    def authority_body(self) -> dict[str, Any]:
        """The exact fields a reproduction's evidence must bind."""

        return {
            "defect_id": self.defect_id,
            "result": self.result,
            "environment_id": self.environment_id,
            "disposable_environment": self.disposable_environment,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "defect_id": self.defect_id,
            "result": self.result,
            "environment_id": self.environment_id,
            "disposable_environment": self.disposable_environment,
            "recorded_before_repair": self.recorded_before_repair,
            "impossibility_condition": self.impossibility_condition,
            "evidence": self.evidence.to_dict() if self.evidence is not None else None,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ReproductionRecord:
        evidence_raw = raw.get("evidence")
        return cls(
            defect_id=str(raw.get("defect_id", "")),
            result=str(raw.get("result", "")),
            environment_id=str(raw.get("environment_id", "")),
            disposable_environment=bool(raw.get("disposable_environment", False)),
            recorded_before_repair=bool(raw.get("recorded_before_repair", False)),
            impossibility_condition=str(raw.get("impossibility_condition", "")),
            evidence=EvidenceIntegrity.from_dict(
                evidence_raw if isinstance(evidence_raw, Mapping) else None
            ),
        )


@dataclass(frozen=True)
class CorrectionRecord:
    """Everything the correction lane must show for one candidate."""

    defect_id: str = ""
    baseline_available: bool = False
    controls: tuple[ControlObservation, ...] = ()
    reproduction: ReproductionRecord | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "defect_id": self.defect_id,
            "baseline_available": self.baseline_available,
            "controls": [observation.to_dict() for observation in self.controls],
            "reproduction": (
                self.reproduction.to_dict() if self.reproduction is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CorrectionRecord:
        raw_controls = raw.get("controls")
        reproduction_raw = raw.get("reproduction")
        return cls(
            defect_id=str(raw.get("defect_id", "")),
            baseline_available=bool(raw.get("baseline_available", False)),
            controls=tuple(
                ControlObservation.from_dict(item)
                for item in (raw_controls if isinstance(raw_controls, Sequence) else ())
                if isinstance(item, Mapping)
            ),
            reproduction=(
                ReproductionRecord.from_dict(reproduction_raw)
                if isinstance(reproduction_raw, Mapping)
                else None
            ),
        )


@dataclass(frozen=True)
class CorrectionReport:
    """Independently inspectable correction-lane result.

    ``gaps`` are absences a caller's criticality policy disposes. ``failures`` are observed
    negative evidence — a rejected spec. ``integrity_issues`` are malformed or duplicated
    records. ``gate_reasons`` are the outcomes doctrine routes to a human regardless of class:
    a suspected over-constraint, an ambiguous classification, a defect that would not reproduce,
    and a greenfield repair with no baseline to bound either control.
    """

    classifications: tuple[ControlClassification, ...]
    gaps: tuple[str, ...]
    failures: tuple[str, ...]
    integrity_issues: tuple[str, ...]
    gate_reasons: tuple[str, ...]
    reports: tuple[str, ...]

    @property
    def satisfied(self) -> bool:
        return not (self.gaps or self.failures or self.integrity_issues or self.gate_reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "classifications": [item.to_dict() for item in self.classifications],
            "gaps": list(self.gaps),
            "failures": list(self.failures),
            "integrity_issues": list(self.integrity_issues),
            "gate_reasons": list(self.gate_reasons),
            "reports": list(self.reports),
        }


def verify_correction(record: CorrectionRecord | None) -> CorrectionReport:
    """Verify both controls and the reproduction requirement for one correction candidate.

    A greenfield repair — no trusted baseline for either control — falls back to a sensitivity
    measure that proves the tests can detect faults but not that they test the right thing. That
    is categorically weaker evidence, so the lane gates regardless of hazard class rather than
    being disposed of as an ordinary absence.
    """

    if record is None:
        return CorrectionReport(
            classifications=(),
            gaps=("correction-record-missing",),
            failures=(),
            integrity_issues=(),
            gate_reasons=(),
            reports=(),
        )

    gaps: list[str] = []
    failures: list[str] = []
    integrity: list[str] = []
    gate_reasons: list[str] = []
    reports: list[str] = []

    if not record.defect_id.strip():
        gaps.append("correction-defect-id-missing")

    classifications: list[ControlClassification] = []
    seen: set[str] = set()
    for observation in record.controls:
        if observation.test_id and observation.test_id in seen:
            integrity.append(f"control-observation-duplicate:{observation.test_id}")
            continue
        if observation.test_id:
            seen.add(observation.test_id)
        classifications.append(classify_control(observation))

    if not record.baseline_available:
        # Categorically weaker than a bounded correction; it gates instead of blocking so the
        # human decides with the sensitivity evidence in front of them.
        gate_reasons.append("greenfield-repair-without-baseline")
    elif not classifications:
        gaps.append("correction-controls-missing")
    else:
        satisfied_red_now = any(
            item.disposition == CONTROL_SATISFIED and item.declared_role == CONTROL_RED_NOW
            for item in classifications
        )
        if not satisfied_red_now:
            # The spec did not catch the bug. Rejected, not waived.
            failures.append("negative-control-unsatisfied")
        if not any(item.declared_role == CONTROL_GREEN_NOW for item in classifications):
            gaps.append("positive-control-unobserved")

    for item in classifications:
        if item.disposition == CONTROL_SUSPECTED_OVER_CONSTRAINT:
            gate_reasons.append(f"suspected-over-constraint:{item.test_id}")
        elif item.disposition == CONTROL_RECOGNITION_CHECK:
            gate_reasons.append(f"recognition-check:{item.test_id}")
        elif item.disposition == CONTROL_ROUTE_HUMAN:
            gate_reasons.append(f"control-classification-ambiguous:{item.test_id}:{item.reason}")
        else:
            reports.append(f"control-satisfied:{item.declared_role}:{item.test_id}")

    _verify_reproduction(record, gaps, failures, integrity, gate_reasons, reports)

    return CorrectionReport(
        classifications=tuple(classifications),
        gaps=tuple(dict.fromkeys(gaps)),
        failures=tuple(dict.fromkeys(failures)),
        integrity_issues=tuple(dict.fromkeys(integrity)),
        gate_reasons=tuple(dict.fromkeys(gate_reasons)),
        reports=tuple(dict.fromkeys(reports)),
    )


def _verify_reproduction(
    record: CorrectionRecord,
    gaps: list[str],
    failures: list[str],
    integrity: list[str],
    gate_reasons: list[str],
    reports: list[str],
) -> None:
    reproduction = record.reproduction
    if reproduction is None:
        gaps.append("reproduction-missing")
        return
    if not reproduction.result:
        gaps.append("reproduction-result-unrecorded")
        return
    if reproduction.result not in REPRODUCTION_RESULTS:
        integrity.append(f"reproduction-result-unknown:{reproduction.result}")
        return
    if record.defect_id.strip() and reproduction.defect_id.strip() != record.defect_id.strip():
        integrity.append("reproduction-defect-mismatch")

    if reproduction.result == REPRODUCTION_IMPOSSIBLE:
        if not reproduction.impossibility_condition.strip():
            gaps.append("reproduction-impossibility-condition-missing")
        else:
            # A stated condition of the lane, not a step quietly skipped.
            gate_reasons.append("reproduction-impossible-condition-stated")
        return

    if reproduction.result == REPRODUCTION_NOT_REPRODUCED:
        # The diagnosis, the environment, or the report is wrong. It is not a clean bill of
        # health and it does not authorize a repair against the original hypothesis.
        gate_reasons.append("reproduction-did-not-reproduce")
        return

    if not reproduction.disposable_environment:
        failures.append("reproduction-environment-not-disposable")
    if not reproduction.environment_id.strip():
        gaps.append("reproduction-environment-unrecorded")
    if not reproduction.recorded_before_repair:
        # A reproduction recorded after the repair cannot be the negative control for it.
        failures.append("reproduction-not-recorded-before-repair")

    evidence = reproduction.evidence
    if evidence is None or not evidence.present:
        gaps.append("reproduction-evidence-missing")
    elif not evidence.verify():
        integrity.append("reproduction-evidence-digest-mismatch")
    elif not evidence.verifies_binding(reproduction.authority_body()):
        integrity.append("reproduction-evidence-subject-mismatch")
    else:
        reports.append("reproduction-recorded")
