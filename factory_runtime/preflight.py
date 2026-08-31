"""Feasibility preflight — the early NO (remediation plan §1.1/§1.1b).

The founder's criterion: every reason a run would die must surface at intake,
as cheaply as possible — a NO at hour 0 is the point. This module is a PURE
function over ratified, signed inputs: no prompt, role instruction, or
attestation anywhere in the loop; the verdict is derived from the same tables
the late gates use, so an operator who ignores a NO meets the identical reason
code at the existing gate.

Three-way output discipline (check_wiring's tri-state precedent):
- ``hard_no``      — PASS or ``__DONE__`` is PROVABLY unreachable from
                     ratification facts alone (surface-set-independent).
- ``disclosures``  — surface-scoped: "NO if any Critical surface is disturbed"
                     phrased at T=0 (e.g. the one-human/Critical approver
                     collision), never a silent late death.
- ``not_applicable`` — an input group was absent, so its checks DID NOT RUN.
                     "Could not check" is loud and distinct from "passed".

The report is UNSIGNED and never auto-appended to any ledger — it enters the
record only as an explicitly human-signed artifact (provenance demotion, not a
claimed structural impossibility). The ceiling disposition states the best
verdict this run can possibly reach given what is ratified today.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from factory_core.criticality import CriticalityProfile
from factory_core.manifest import SegregationPolicy
from factory_core.verdict import (
    TERRITORY_COVERED,
    TERRITORY_KIND_SCENARIO,
    TERRITORY_UNCOVERED,
    VERDICT_BLOCK,
    VERDICT_PASS,
    VERDICT_PASS_ON_COVERED,
    CoverageMap,
    normalize_label,
)
from factory_runtime.generation import _signal_knob_issues


@dataclass(frozen=True)
class PreflightFinding:
    code: str
    subject: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "subject": self.subject, "detail": self.detail}


@dataclass(frozen=True)
class PreflightReport:
    go: bool
    ceiling: str
    hard_no: tuple[PreflightFinding, ...] = ()
    disclosures: tuple[PreflightFinding, ...] = ()
    notes: tuple[PreflightFinding, ...] = ()
    not_applicable: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "factory-preflight-report/1",
            "go": self.go,
            "ceiling": self.ceiling,
            "hard_no": [finding.to_dict() for finding in self.hard_no],
            "disclosures": [finding.to_dict() for finding in self.disclosures],
            "notes": [finding.to_dict() for finding in self.notes],
            "not_applicable": list(self.not_applicable),
        }


def run_preflight(
    *,
    coverage: CoverageMap | None = None,
    profile: CriticalityProfile | None = None,
    policy: SegregationPolicy | None = None,
    target_build: Mapping[str, Any] | None = None,
    plan_max_build_attempts: int | None = None,
) -> PreflightReport:
    """Compute the intake verdict from ratification-time facts only."""

    hard_no: list[PreflightFinding] = []
    disclosures: list[PreflightFinding] = []
    notes: list[PreflightFinding] = []
    not_applicable: list[str] = []

    # --- verdict reachability (§1.1b, the two-islands bridge) --------------------
    ceiling = VERDICT_PASS
    if coverage is None:
        not_applicable.append("coverage-map")
        ceiling = "unknown"
        disclosures.append(
            PreflightFinding(
                "preflight-reachability-unverified",
                "coverage-map",
                "no ratified coverage map was supplied: PASS/__DONE__ "
                "reachability was NOT verified — a GO here clears nothing "
                "about the verdict layer",
            )
        )
    else:
        criteria_ids = {normalize_label(c.territory_id) for c in coverage.adequacy}
        uncovered = [
            territory
            for territory in coverage.territories
            if territory.status == TERRITORY_UNCOVERED
        ]
        for territory in uncovered:
            if normalize_label(territory.territory_id) not in criteria_ids:
                hard_no.append(
                    PreflightFinding(
                        "preflight-pass-unreachable",
                        normalize_label(territory.territory_id),
                        "uncovered territory has no ratified adequacy criterion: "
                        "no receipt can ever clear it, so PASS is provably "
                        "unreachable (the 127-hour case, caught at hour 0)",
                    )
                )
        if uncovered:
            ceiling = VERDICT_PASS_ON_COVERED
        if not any(
            territory.kind == TERRITORY_KIND_SCENARIO
            and territory.status == TERRITORY_COVERED
            for territory in coverage.territories
        ):
            # The post-run auditor's pure predicate (audit.py), lifted to a
            # pre-construction call site; the auditor itself stays non-gating.
            hard_no.append(
                PreflightFinding(
                    "preflight-purpose-untested",
                    "scenario-coverage",
                    "no covered scenario territory: the run's one-sentence "
                    "purpose has no planned test, so the forced first line can "
                    "never be YES",
                )
            )
        if not coverage.verb_ids:
            hard_no.append(
                PreflightFinding(
                    "preflight-done-unreachable",
                    "verb-ids",
                    "ratified verb set is empty: compose_done refuses an empty "
                    "verb union, so __DONE__ is provably unreachable",
                )
            )

    # --- attempt-ceiling and signal-knob consistency ------------------------------
    if target_build is None:
        not_applicable.append("attempt-ceilings")
    else:
        target_limit = target_build.get("max_attempts")
        if (
            isinstance(target_limit, int)
            and not isinstance(target_limit, bool)
            and plan_max_build_attempts is not None
            and plan_max_build_attempts > target_limit
        ):
            hard_no.append(
                PreflightFinding(
                    "preflight-attempt-ceiling-inconsistent",
                    "build-plan",
                    f"plan max_build_attempts {plan_max_build_attempts} exceeds "
                    f"the target ABI ceiling {target_limit}",
                )
            )
        for issue in _signal_knob_issues(target_build, None):
            hard_no.append(
                PreflightFinding(
                    "preflight-signal-knobs",
                    issue,
                    "signal-deadline knobs are invalid or undeclared at intake",
                )
            )

    # --- critical approver capacity (§1.1 class split, kindex 956b08784b09) -------
    if profile is None or policy is None:
        not_applicable.append("critical-roster")
    else:
        required = profile.required_critical_approvers
        declared = profile.critical_ratification_delegates
        resolved_humans = {
            resolved
            for delegate in declared
            if (resolved := policy.resolve_human(delegate))
        }
        if not declared:
            disclosures.append(
                PreflightFinding(
                    "preflight-critical-roster-undeclared",
                    "critical-ratification-delegates",
                    "no delegate roster declared: NO if any Critical surface is "
                    "disturbed (unclassified surfaces default to Critical)",
                )
            )
        elif not resolved_humans:
            disclosures.append(
                PreflightFinding(
                    "preflight-critical-roster-unresolvable",
                    "critical-ratification-delegates",
                    "no declared delegate resolves to an enrolled human: NO if "
                    "any Critical surface is disturbed",
                )
            )
        # Computed DIRECTLY from roster size — an emitted insufficient-approvers
        # from a late null probe cannot distinguish expected-empty from
        # impossible; this can (the n=1/I2 collision surfaced at T=0).
        capacity = len({policy.canonical(human) for human in policy.human_ids})
        if capacity < required:
            disclosures.append(
                PreflightFinding(
                    "preflight-critical-approver-capacity",
                    "enrolled-humans",
                    f"{capacity} enrolled human(s) < required Critical approver "
                    f"floor {required}: a Critical surface is permanently "
                    f"unpromotable under this roster — NO if any Critical "
                    f"surface is disturbed",
                )
            )
        else:
            notes.append(
                PreflightFinding(
                    "preflight-critical-approver-capacity-ok",
                    "enrolled-humans",
                    f"{capacity} enrolled human(s) >= floor {required}",
                )
            )

    if hard_no:
        ceiling = VERDICT_BLOCK
    return PreflightReport(
        go=not hard_no,
        ceiling=ceiling,
        hard_no=tuple(hard_no),
        disclosures=tuple(disclosures),
        notes=tuple(notes),
        not_applicable=tuple(not_applicable),
    )
