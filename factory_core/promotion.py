"""Fail-closed promotion over invariant authority, tool policy, checklists, and criticality.

The core answers one question: may this exact built artifact be promoted? Every target-specific
surface, component, gate id, evidence id, and dependency edge arrives as data. The core fixes
only the doctrine:

* verification depth is not scaled by diff size or criticality;
* every explicitly disturbed surface inherits the highest class reachable through declared
  side effects;
* every requirement resolves to one exact version of the three invariant documents;
* a signed tool policy is required and every rule resolves to phase-2/3 authority;
* gate items count only when individually cited by subject-bound evidence;
* missing oracle/evidence links are gaps disposed by class;
* malformed, mismatched, or negative evidence blocks every class;
* Critical gaps block without waiver and Critical evidence must be deterministic, flake-free,
  retry-free, live, specialist-reviewed, and approved by at least two distinct enrolled humans;
* Standard gaps require a candidate-bound, expiring risk acceptance from an enrolled human;
* Cosmetic gaps are reported and promoted past; and
* Standard/Cosmetic changes with adequate oracles need no discretionary human click.

The declared side-effect closure is not a proof that the topology is complete. Phase-2
enumeration and parity controls own that upstream obligation. This module guarantees only that
the supplied topology cannot be bypassed during the decision.

Posture: stdlib only, pure, no clock, no disk, no target contact. The caller supplies the
evaluation time and externally trusted identity roster.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from factory_core.checklist import (
    ChecklistItemResult,
    ChecklistReport,
    verify_checklist,
)
from factory_core.criticality import (
    BASE_REQUIRED_EVIDENCE_IDS,
    CRITICALITY_COSMETIC,
    CRITICALITY_CRITICAL,
    CRITICALITY_STANDARD,
    CriticalityProfile,
    CriticalityResolution,
    ResolvedSurface,
    normalize_label,
    resolve_criticality,
)
from factory_core.evidence import EvidenceIntegrity
from factory_core.manifest import SegregationPolicy, digest_obj
from factory_core.provenance import ProvenanceBundle, provenance_issue_is_gap
from factory_core.tool_policy import (
    ToolPolicyBundle,
    tool_policy_issue_is_gap,
    verify_tool_policy,
)

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_LIVE_RESULTS = frozenset({"missing", "passed", "failed"})

DISPOSITION_PROMOTE = "promote"
DISPOSITION_REPORT_AND_PROMOTE = "report-and-promote"
DISPOSITION_RISK_ACCEPTED = "risk-accepted"
DISPOSITION_GATE = "gate"
DISPOSITION_BLOCK = "block"

# Compatibility name: promotion gates are the generic evidence-backed checklist primitive.
GateOutcome = ChecklistItemResult


class PromotionError(ValueError):
    """Raised when a promotion input cannot be parsed without guessing."""


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(str(item) for item in value)
    return (str(value),)


def _as_int(value: Any, *, field_name: str, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise PromotionError(f"{field_name!r} must be an integer, got {value!r}") from exc


def _is_sha256(value: str) -> bool:
    return bool(_SHA256_RE.fullmatch(value))


def _mapping_sequence(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _phase_artifact_versions(provenance: ProvenanceBundle) -> tuple[tuple[str, str], ...]:
    """Return the exact phase-to-content-address mapping carried by a bundle."""

    return tuple(
        sorted((artifact.phase, artifact.content_digest) for artifact in provenance.artifacts)
    )


@dataclass(frozen=True)
class NamedEvidence:
    """One additional class-required evidence artifact for a surface."""

    evidence_id: str
    integrity: EvidenceIntegrity | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> NamedEvidence:
        return cls(
            evidence_id=str(raw.get("evidence_id", "")),
            integrity=EvidenceIntegrity.from_dict(
                raw.get("integrity") if isinstance(raw.get("integrity"), Mapping) else None
            ),
        )


@dataclass(frozen=True)
class Quarantine:
    """Time-bounded Standard flake debt."""

    owner: str
    expires_at: int
    rationale: str
    evidence: EvidenceIntegrity | None = None

    def authority_body(self, surface_id: str) -> dict[str, Any]:
        return {
            "surface_id": normalize_label(surface_id),
            "owner": self.owner,
            "expires_at": self.expires_at,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Quarantine:
        return cls(
            owner=str(raw.get("owner", "")),
            expires_at=_as_int(raw.get("expires_at"), field_name="expires_at"),
            rationale=str(raw.get("rationale", "")),
            evidence=EvidenceIntegrity.from_dict(
                raw.get("evidence") if isinstance(raw.get("evidence"), Mapping) else None
            ),
        )


@dataclass(frozen=True)
class SurfaceObservation:
    """Oracle, live, determinism, and additional evidence for one disturbed surface."""

    surface_id: str
    oracle_adequate: bool = False
    live_result: str = "missing"
    live_evidence: EvidenceIntegrity | None = None
    evidence: tuple[NamedEvidence, ...] = ()
    deterministic: bool = False
    flake_count: int = 0
    automatic_retry_count: int = 0
    quarantine: Quarantine | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> SurfaceObservation:
        quarantine_raw = raw.get("quarantine")
        return cls(
            surface_id=str(raw.get("surface_id", "")),
            oracle_adequate=bool(raw.get("oracle_adequate", False)),
            live_result=str(raw.get("live_result", "missing")),
            live_evidence=EvidenceIntegrity.from_dict(
                raw.get("live_evidence") if isinstance(raw.get("live_evidence"), Mapping) else None
            ),
            evidence=tuple(
                NamedEvidence.from_dict(item) for item in _mapping_sequence(raw.get("evidence"))
            ),
            deterministic=bool(raw.get("deterministic", False)),
            flake_count=_as_int(raw.get("flake_count"), field_name="flake_count"),
            automatic_retry_count=_as_int(
                raw.get("automatic_retry_count"),
                field_name="automatic_retry_count",
            ),
            quarantine=(
                Quarantine.from_dict(quarantine_raw)
                if isinstance(quarantine_raw, Mapping)
                else None
            ),
        )


@dataclass(frozen=True)
class SpecialistReview:
    """Candidate-bound human specialist verdict for one surface."""

    surface_id: str
    reviewer: str
    candidate_digest: str
    criticality_profile_digest: str
    passed: bool
    evidence: EvidenceIntegrity | None = None

    def authority_body(self) -> dict[str, Any]:
        return {
            "surface_id": normalize_label(self.surface_id),
            "reviewer": self.reviewer,
            "candidate_digest": self.candidate_digest,
            "criticality_profile_digest": self.criticality_profile_digest,
            "passed": self.passed,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> SpecialistReview:
        return cls(
            surface_id=str(raw.get("surface_id", "")),
            reviewer=str(raw.get("reviewer", "")),
            candidate_digest=str(raw.get("candidate_digest", "")),
            criticality_profile_digest=str(raw.get("criticality_profile_digest", "")),
            passed=bool(raw.get("passed", False)),
            evidence=EvidenceIntegrity.from_dict(
                raw.get("evidence") if isinstance(raw.get("evidence"), Mapping) else None
            ),
        )


@dataclass(frozen=True)
class RiskAcceptance:
    """Candidate-bound, expiring Standard-gap acceptance owned by a named human."""

    owner: str
    surface_ids: tuple[str, ...]
    candidate_digest: str
    criticality_profile_digest: str
    expires_at: int
    rationale: str
    evidence: EvidenceIntegrity | None = None

    def authority_body(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "surface_ids": sorted(
                {
                    normalize_label(surface_id)
                    for surface_id in self.surface_ids
                    if normalize_label(surface_id)
                }
            ),
            "candidate_digest": self.candidate_digest,
            "criticality_profile_digest": self.criticality_profile_digest,
            "expires_at": self.expires_at,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> RiskAcceptance:
        return cls(
            owner=str(raw.get("owner", "")),
            surface_ids=_as_str_tuple(raw.get("surface_ids")),
            candidate_digest=str(raw.get("candidate_digest", "")),
            criticality_profile_digest=str(raw.get("criticality_profile_digest", "")),
            expires_at=_as_int(raw.get("expires_at"), field_name="expires_at"),
            rationale=str(raw.get("rationale", "")),
            evidence=EvidenceIntegrity.from_dict(
                raw.get("evidence") if isinstance(raw.get("evidence"), Mapping) else None
            ),
        )


@dataclass(frozen=True)
class PromotionRequest:
    """All evidence and authority records for one exact candidate."""

    candidate_digest: str = ""
    disturbed_surface_ids: tuple[str, ...] = ()
    observations: tuple[SurfaceObservation, ...] = ()
    gates: tuple[GateOutcome, ...] = ()
    implementer: str = ""
    verifier: str = ""
    approvers: tuple[str, ...] = ()
    specialist_reviews: tuple[SpecialistReview, ...] = ()
    risk_acceptance: RiskAcceptance | None = None
    evaluated_at: int = 0
    evidence: EvidenceIntegrity | None = None
    provenance: ProvenanceBundle | None = None
    tool_policy: ToolPolicyBundle | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> PromotionRequest:
        risk_raw = raw.get("risk_acceptance")
        provenance_raw = raw.get("provenance")
        tool_policy_raw = raw.get("tool_policy")
        return cls(
            candidate_digest=str(raw.get("candidate_digest", "")),
            disturbed_surface_ids=_as_str_tuple(raw.get("disturbed_surface_ids")),
            observations=tuple(
                SurfaceObservation.from_dict(item)
                for item in _mapping_sequence(raw.get("observations"))
            ),
            gates=tuple(
                GateOutcome.from_dict(item) for item in _mapping_sequence(raw.get("gates"))
            ),
            implementer=str(raw.get("implementer", "")),
            verifier=str(raw.get("verifier", "")),
            approvers=_as_str_tuple(raw.get("approvers")),
            specialist_reviews=tuple(
                SpecialistReview.from_dict(item)
                for item in _mapping_sequence(raw.get("specialist_reviews"))
            ),
            risk_acceptance=(
                RiskAcceptance.from_dict(risk_raw) if isinstance(risk_raw, Mapping) else None
            ),
            evaluated_at=_as_int(raw.get("evaluated_at"), field_name="evaluated_at"),
            evidence=EvidenceIntegrity.from_dict(
                raw.get("evidence") if isinstance(raw.get("evidence"), Mapping) else None
            ),
            provenance=(
                ProvenanceBundle.from_dict(provenance_raw)
                if isinstance(provenance_raw, Mapping)
                else None
            ),
            tool_policy=(
                ToolPolicyBundle.from_dict(tool_policy_raw)
                if isinstance(tool_policy_raw, Mapping)
                else None
            ),
        )


def _integrity_digest(evidence: EvidenceIntegrity | None) -> str:
    """Return the claimed address so the manifest binds the exact cited artifact."""

    return evidence.claimed_digest if evidence is not None else ""


def promotion_attestation_subject(
    request: PromotionRequest,
    profile: CriticalityProfile,
) -> dict[str, Any]:
    """Canonical decision inputs the change-evidence attestation must bind.

    The attestation is the content-addressed envelope for the facts used by the promotion
    decision. Individual live, specialist, quarantine, risk-acceptance, and named evidence
    artifacts are still verified independently; this subject binds their claimed addresses
    and the decision inputs so none can be swapped, erased, or rewritten after attestation.

    This function defines content, not authority. The core still relies on an external trust
    system to establish who was allowed to attest it.
    """

    gates = sorted(
        (
            {
                "id": normalize_label(gate.id),
                "passed": gate.passed,
                "detail": gate.detail,
                "recorded_at": gate.recorded_at,
                "evidence_digest": _integrity_digest(gate.evidence),
            }
            for gate in request.gates
        ),
        key=lambda item: (item["id"], str(item["passed"]), item["detail"]),
    )
    observations = sorted(
        (
            {
                "surface_id": normalize_label(observation.surface_id),
                "oracle_adequate": observation.oracle_adequate,
                "live_result": normalize_label(observation.live_result) or "missing",
                "live_evidence_digest": _integrity_digest(observation.live_evidence),
                "evidence": sorted(
                    (
                        {
                            "evidence_id": normalize_label(record.evidence_id),
                            "claimed_digest": _integrity_digest(record.integrity),
                        }
                        for record in observation.evidence
                    ),
                    key=lambda item: (item["evidence_id"], item["claimed_digest"]),
                ),
                "deterministic": observation.deterministic,
                "flake_count": observation.flake_count,
                "automatic_retry_count": observation.automatic_retry_count,
                "quarantine": (
                    {
                        **observation.quarantine.authority_body(observation.surface_id),
                        "evidence_digest": _integrity_digest(observation.quarantine.evidence),
                    }
                    if observation.quarantine is not None
                    else None
                ),
            }
            for observation in request.observations
        ),
        key=lambda item: str(item["surface_id"]),
    )
    specialist_reviews = sorted(
        (
            {
                **review.authority_body(),
                "evidence_digest": _integrity_digest(review.evidence),
            }
            for review in request.specialist_reviews
        ),
        key=lambda item: (item["surface_id"], item["reviewer"]),
    )
    risk_acceptance = (
        {
            **request.risk_acceptance.authority_body(),
            "evidence_digest": _integrity_digest(request.risk_acceptance.evidence),
        }
        if request.risk_acceptance is not None
        else None
    )
    return {
        "candidate_digest": request.candidate_digest,
        "criticality_profile_digest": profile.content_digest,
        "disturbed_surface_ids": sorted(
            normalize_label(surface_id)
            for surface_id in request.disturbed_surface_ids
            if normalize_label(surface_id)
        ),
        "gates": gates,
        "implementer": normalize_label(request.implementer),
        "verifier": normalize_label(request.verifier),
        "approvers": sorted(normalize_label(approver) for approver in request.approvers),
        "observations": observations,
        "specialist_reviews": specialist_reviews,
        "risk_acceptance": risk_acceptance,
        "evaluated_at": request.evaluated_at,
        "provenance_bundle_digest": (
            digest_obj(request.provenance.to_dict()) if request.provenance is not None else ""
        ),
        "tool_policy_bundle_digest": (
            digest_obj(request.tool_policy.to_dict()) if request.tool_policy is not None else ""
        ),
    }


@dataclass(frozen=True)
class SurfaceDecision:
    """Class, evidence requirement, and findings for one disturbed surface."""

    surface_id: str
    criticality: str
    required_evidence_ids: tuple[str, ...]
    gaps: tuple[str, ...]
    negative_findings: tuple[str, ...]
    reports: tuple[str, ...]
    deterministic: bool | None
    flake_count: int | None
    automatic_retry_count: int | None
    live_result: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "criticality": self.criticality,
            "required_evidence_ids": list(self.required_evidence_ids),
            "gaps": list(self.gaps),
            "negative_findings": list(self.negative_findings),
            "reports": list(self.reports),
            "deterministic": self.deterministic,
            "flake_count": self.flake_count,
            "automatic_retry_count": self.automatic_retry_count,
            "live_result": self.live_result,
        }


@dataclass(frozen=True)
class PromotionDecision:
    """The independently inspectable gate verdict."""

    allowed: bool
    disposition: str
    reasons: tuple[str, ...]
    reports: tuple[str, ...]
    highest_criticality: str
    required_approvers: int
    approver_count: int
    provenance_issues: tuple[str, ...]
    tool_policy_issues: tuple[str, ...]
    tool_policy_digest: str
    checklist: ChecklistReport
    criticality: CriticalityResolution
    surfaces: tuple[SurfaceDecision, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "disposition": self.disposition,
            "reasons": list(self.reasons),
            "reports": list(self.reports),
            "highest_criticality": self.highest_criticality,
            "required_approvers": self.required_approvers,
            "approver_count": self.approver_count,
            "provenance_issues": list(self.provenance_issues),
            "tool_policy_issues": list(self.tool_policy_issues),
            "tool_policy_digest": self.tool_policy_digest,
            "checklist": self.checklist.to_dict(),
            "criticality": self.criticality.to_dict(),
            "surfaces": [surface.to_dict() for surface in self.surfaces],
        }


def _record_human(
    identity: str,
    *,
    policy: SegregationPolicy,
    implementer: str,
    verifier: str,
) -> tuple[str | None, str | None]:
    human = policy.resolve_human(identity)
    if human is None:
        code = (
            "authority-is-agent"
            if policy.is_excluded(identity) or policy.is_excluded(policy.canonical(identity))
            else "authority-not-enrolled"
        )
        return None, f"{code}:{normalize_label(identity)}"
    if implementer and human == policy.canonical(implementer):
        return None, f"authority-equals-implementer:{normalize_label(identity)}"
    if verifier and human == policy.canonical(verifier):
        return None, f"authority-equals-verifier:{normalize_label(identity)}"
    return human, None


def _validate_authority_evidence(
    evidence: EvidenceIntegrity | None,
    expected_body: Mapping[str, Any],
    *,
    missing_code: str,
    invalid_code: str,
) -> tuple[bool, str | None, bool]:
    """Return valid, issue, is_integrity_failure."""

    if evidence is None or not evidence.present:
        return False, missing_code, False
    if not evidence.verifies_binding(expected_body):
        return False, invalid_code, True
    return True, None, False


def decide_promotion(
    request: PromotionRequest,
    policy: SegregationPolicy,
    profile: CriticalityProfile,
) -> PromotionDecision:
    """Pure promotion decision. It never performs the promotion.

    Missing evidence is accumulated per surface before disposition. Negative evidence and
    evidence-integrity failures are accumulated separately because neither can be converted
    into a risk acceptance.
    """

    resolution = resolve_criticality(profile, request.disturbed_surface_ids, policy)
    surface_by_id = {surface.surface_id: surface for surface in resolution.surfaces}
    gaps: dict[str, list[str]] = {surface_id: [] for surface_id in surface_by_id}
    negatives: dict[str, list[str]] = {surface_id: [] for surface_id in surface_by_id}
    local_reports: dict[str, list[str]] = {surface_id: [] for surface_id in surface_by_id}
    hard_reasons: list[str] = [
        f"criticality-profile-invalid:{issue}" for issue in resolution.blocking_issues
    ]
    reports: list[str] = list(resolution.reports)
    gate_reasons: list[str] = []

    def gap_all(code: str) -> None:
        for surface_gaps in gaps.values():
            surface_gaps.append(code)

    def negative(surface_id: str, code: str) -> None:
        negatives[surface_id].append(code)

    candidate_valid = _is_sha256(request.candidate_digest)
    if not request.candidate_digest:
        hard_reasons.append("candidate-digest-missing")
    elif not candidate_valid:
        hard_reasons.append("candidate-digest-invalid")

    # The content-addressed manifest is the attestation-chain anchor. Absence is a class-scoped
    # gap; tamper or subject mismatch is an integrity failure for every class.
    if request.evidence is None or not request.evidence.present:
        gap_all("attestation-missing")
    elif not request.evidence.verify():
        hard_reasons.append("attestation-digest-mismatch")
    elif not request.evidence.verifies_binding(promotion_attestation_subject(request, profile)):
        hard_reasons.append("attestation-subject-mismatch")

    tool_policy_issues: tuple[str, ...]
    tool_policy_digest = ""
    if request.tool_policy is None:
        # Unlike a missing observation, an absent run policy means no authority exists for the
        # tools that produced the candidate or its evidence. This is a control-plane defect,
        # not an evidence item a lower class may promote past.
        tool_policy_issues = ("tool-policy-missing",)
        hard_reasons.append("tool-policy-missing")
    else:
        tool_report = verify_tool_policy(request.tool_policy, policy, request.evaluated_at)
        tool_policy_issues = tool_report.issues
        tool_policy_digest = tool_report.policy_digest
        for issue in tool_policy_issues:
            if tool_policy_issue_is_gap(issue):
                gap_all(f"tool-policy-gap:{issue}")
            else:
                hard_reasons.append(f"tool-policy-invalid:{issue}")

    provenance_issues: tuple[str, ...]
    if request.provenance is None:
        provenance_issues = ("provenance-missing",)
        gap_all("provenance-missing")
    else:
        provenance_report = request.provenance.verify()
        provenance_issues = provenance_report.issues
        for issue in provenance_issues:
            if provenance_issue_is_gap(issue):
                gap_all(f"provenance-gap:{issue}")
            else:
                hard_reasons.append(f"provenance-integrity:{issue}")

    if request.provenance is not None and request.tool_policy is not None:
        if _phase_artifact_versions(
            request.provenance
        ) != _phase_artifact_versions(request.tool_policy.provenance):
            hard_reasons.append("tool-policy-phase-artifacts-mismatch")

    # A gate is the checklist definition plus individually cited item observations. Absence is
    # visible silence; failed items are negative evidence; tampered or wrong-subject citations
    # are integrity failures for every class.
    checklist_report = verify_checklist(
        profile.required_gate_ids,
        request.gates,
        request.candidate_digest,
    )
    for gap in checklist_report.gaps:
        gap_all(gap)
    hard_reasons.extend(
        f"checklist-integrity:{issue}" for issue in checklist_report.integrity_issues
    )
    for failure in checklist_report.failures:
        for surface_id in negatives:
            negative(surface_id, failure)
    reports.extend(checklist_report.reports)

    observations: dict[str, SurfaceObservation] = {}
    for captured_observation in request.observations:
        surface_id = normalize_label(captured_observation.surface_id)
        if not surface_id:
            hard_reasons.append("observation-surface-id-missing")
            continue
        if surface_id in observations:
            hard_reasons.append(f"surface-observation-duplicate:{surface_id}")
            continue
        observations[surface_id] = captured_observation
        if surface_id not in surface_by_id:
            reports.append(f"observation-outside-disturbance:{surface_id}")

    for surface_id, surface in surface_by_id.items():
        current_observation = observations.get(surface_id)
        if current_observation is None:
            gaps[surface_id].append("surface-observation-missing")
            continue

        if not current_observation.oracle_adequate:
            gaps[surface_id].append("oracle-silent")

        live_result = normalize_label(current_observation.live_result) or "missing"
        if live_result not in _LIVE_RESULTS:
            hard_reasons.append(f"live-result-invalid:{surface_id}:{live_result}")
        elif live_result == "missing":
            gaps[surface_id].append("live-verification-missing")
        elif live_result == "failed":
            negative(surface_id, "live-verification-failed")
        else:
            live = current_observation.live_evidence
            if live is None or not live.present:
                gaps[surface_id].append("live-verification-artifact-missing")
            elif not live.verify():
                hard_reasons.append(f"live-evidence-digest-mismatch:{surface_id}")
            elif not live.verifies_binding(
                {
                    "surface_id": surface_id,
                    "candidate_digest": request.candidate_digest,
                    "result": "passed",
                }
            ):
                hard_reasons.append(f"live-evidence-subject-mismatch:{surface_id}")

        if current_observation.flake_count < 0:
            hard_reasons.append(f"flake-count-invalid:{surface_id}")
        if current_observation.automatic_retry_count < 0:
            hard_reasons.append(f"automatic-retry-count-invalid:{surface_id}")

        named: dict[str, NamedEvidence] = {}
        for captured_record in current_observation.evidence:
            evidence_id = normalize_label(captured_record.evidence_id)
            if not evidence_id:
                hard_reasons.append(f"evidence-id-missing:{surface_id}")
                continue
            if evidence_id in named:
                hard_reasons.append(f"evidence-duplicate:{surface_id}:{evidence_id}")
                continue
            named[evidence_id] = captured_record

        additional_required = surface.required_evidence_ids - BASE_REQUIRED_EVIDENCE_IDS
        for evidence_id in sorted(additional_required):
            required_record = named.get(evidence_id)
            if (
                required_record is None
                or required_record.integrity is None
                or not required_record.integrity.present
            ):
                gaps[surface_id].append(f"evidence-missing:{evidence_id}")
            elif not required_record.integrity.verify():
                hard_reasons.append(f"evidence-digest-mismatch:{surface_id}:{evidence_id}")
            elif not required_record.integrity.verifies_binding(
                {
                    "surface_id": surface_id,
                    "candidate_digest": request.candidate_digest,
                    "evidence_id": evidence_id,
                }
            ):
                hard_reasons.append(f"evidence-subject-mismatch:{surface_id}:{evidence_id}")

        if surface.effective_criticality == CRITICALITY_CRITICAL:
            if not current_observation.deterministic:
                negative(surface_id, "critical-evidence-nondeterministic")
            if current_observation.flake_count > 0:
                negative(
                    surface_id,
                    f"critical-test-flaked:{current_observation.flake_count}",
                )
            if current_observation.automatic_retry_count > 0:
                negative(
                    surface_id,
                    f"critical-automatic-retry:{current_observation.automatic_retry_count}",
                )
        elif surface.effective_criticality == CRITICALITY_STANDARD:
            if current_observation.flake_count > 0:
                quarantine_ok, quarantine_issue, quarantine_integrity = _validate_quarantine(
                    current_observation.quarantine,
                    surface,
                    request,
                    policy,
                )
                if current_observation.flake_count > surface.standard_flake_budget:
                    gaps[surface_id].append(
                        "standard-flake-budget-exceeded:"
                        f"{current_observation.flake_count}/{surface.standard_flake_budget}"
                    )
                elif not quarantine_ok and quarantine_issue:
                    if quarantine_integrity:
                        hard_reasons.append(f"{quarantine_issue}:{surface_id}")
                    else:
                        gaps[surface_id].append(quarantine_issue)
                else:
                    local_reports[surface_id].append(
                        f"standard-flake-quarantined:{current_observation.flake_count}"
                    )
            if current_observation.automatic_retry_count > 0:
                local_reports[surface_id].append(
                    f"standard-automatic-retry:{current_observation.automatic_retry_count}"
                )
        else:
            if current_observation.flake_count > 0:
                local_reports[surface_id].append(
                    f"cosmetic-flake:{current_observation.flake_count}"
                )
            if current_observation.automatic_retry_count > 0:
                local_reports[surface_id].append(
                    f"cosmetic-automatic-retry:{current_observation.automatic_retry_count}"
                )

    # Validate every supplied specialist record; cited bad evidence cannot be ignored merely
    # because a review was unnecessary on that class.
    reviews: dict[str, SpecialistReview] = {}
    for review in request.specialist_reviews:
        surface_id = normalize_label(review.surface_id)
        if not surface_id:
            hard_reasons.append("specialist-review-surface-id-missing")
            continue
        if surface_id in reviews:
            hard_reasons.append(f"specialist-review-duplicate:{surface_id}")
            continue
        reviews[surface_id] = review
        _validate_specialist_review(
            review,
            surface_id,
            request,
            policy,
            profile,
            hard_reasons,
            gaps.get(surface_id),
        )
        if not review.passed:
            hard_reasons.append(f"specialist-review-failed:{surface_id}")

    for surface_id, surface in surface_by_id.items():
        if surface.effective_criticality == CRITICALITY_CRITICAL and surface_id not in reviews:
            gaps[surface_id].append("specialist-review-missing")

    impl_c = policy.canonical(request.implementer)
    verifier_c = policy.canonical(request.verifier)
    if not normalize_label(request.implementer):
        hard_reasons.append("implementer-missing")
    if not normalize_label(request.verifier):
        hard_reasons.append("verifier-missing")
    if request.implementer and request.verifier and impl_c == verifier_c:
        hard_reasons.append("verifier-equals-implementer")

    distinct_approvers: set[str] = set()
    for approver in request.approvers:
        human, authority_issue = _record_human(
            approver,
            policy=policy,
            implementer=request.implementer,
            verifier=request.verifier,
        )
        if authority_issue:
            hard_reasons.append(authority_issue.replace("authority-", "approver-", 1))
        elif human:
            distinct_approvers.add(human)

    required_approvers = (
        profile.required_critical_approvers
        if resolution.highest_criticality == CRITICALITY_CRITICAL
        else 0
    )
    approver_count = len(distinct_approvers)
    if approver_count < required_approvers:
        hard_reasons.append(f"insufficient-approvers:{approver_count}/{required_approvers}")

    for surface_id, findings in negatives.items():
        hard_reasons.extend(f"negative-evidence:{surface_id}:{finding}" for finding in findings)

    critical_gap_ids = {
        surface_id
        for surface_id, surface in surface_by_id.items()
        if surface.effective_criticality == CRITICALITY_CRITICAL and gaps[surface_id]
    }
    critical_surface_ids = {
        surface_id
        for surface_id, surface in surface_by_id.items()
        if surface.effective_criticality == CRITICALITY_CRITICAL
    }
    standard_gap_ids = {
        surface_id
        for surface_id, surface in surface_by_id.items()
        if surface.effective_criticality == CRITICALITY_STANDARD and gaps[surface_id]
    }
    cosmetic_gap_ids = {
        surface_id
        for surface_id, surface in surface_by_id.items()
        if surface.effective_criticality == CRITICALITY_COSMETIC and gaps[surface_id]
    }

    hard_reasons.extend(
        f"critical-gap:{surface_id}:{gap}"
        for surface_id in sorted(critical_gap_ids)
        for gap in gaps[surface_id]
    )

    risk_valid = False
    if request.risk_acceptance is not None:
        risk_valid = _validate_risk_acceptance(
            request.risk_acceptance,
            request,
            policy,
            profile,
            critical_surface_ids,
            standard_gap_ids,
            hard_reasons,
            gate_reasons,
            reports,
        )
    if standard_gap_ids and not risk_valid:
        gate_reasons.extend(
            f"standard-gap-requires-risk-acceptance:{surface_id}"
            for surface_id in sorted(standard_gap_ids)
        )
    elif standard_gap_ids:
        reports.extend(
            f"standard-gap-risk-accepted:{surface_id}:{gap}"
            for surface_id in sorted(standard_gap_ids)
            for gap in gaps[surface_id]
        )

    reports.extend(
        f"cosmetic-gap:{surface_id}:{gap}"
        for surface_id in sorted(cosmetic_gap_ids)
        for gap in gaps[surface_id]
    )
    for surface_id, surface_reports in local_reports.items():
        reports.extend(f"{surface_id}:{report}" for report in surface_reports)

    hard = tuple(dict.fromkeys(hard_reasons))
    gated = tuple(dict.fromkeys(gate_reasons))
    reasons = hard + tuple(reason for reason in gated if reason not in hard)
    if hard:
        disposition = DISPOSITION_BLOCK
    elif gated:
        disposition = DISPOSITION_GATE
    elif standard_gap_ids:
        disposition = DISPOSITION_RISK_ACCEPTED
    elif cosmetic_gap_ids:
        disposition = DISPOSITION_REPORT_AND_PROMOTE
    else:
        disposition = DISPOSITION_PROMOTE

    surface_decisions = tuple(
        SurfaceDecision(
            surface_id=surface_id,
            criticality=surface.effective_criticality,
            required_evidence_ids=tuple(sorted(surface.required_evidence_ids)),
            gaps=tuple(dict.fromkeys(gaps[surface_id])),
            negative_findings=tuple(dict.fromkeys(negatives[surface_id])),
            reports=tuple(dict.fromkeys(local_reports[surface_id])),
            deterministic=(
                observations[surface_id].deterministic if surface_id in observations else None
            ),
            flake_count=(
                observations[surface_id].flake_count if surface_id in observations else None
            ),
            automatic_retry_count=(
                observations[surface_id].automatic_retry_count
                if surface_id in observations
                else None
            ),
            live_result=(
                normalize_label(observations[surface_id].live_result)
                if surface_id in observations
                else "missing"
            ),
        )
        for surface_id, surface in sorted(surface_by_id.items())
    )
    return PromotionDecision(
        allowed=disposition
        in {
            DISPOSITION_PROMOTE,
            DISPOSITION_REPORT_AND_PROMOTE,
            DISPOSITION_RISK_ACCEPTED,
        },
        disposition=disposition,
        reasons=reasons,
        reports=tuple(dict.fromkeys(reports)),
        highest_criticality=resolution.highest_criticality,
        required_approvers=required_approvers,
        approver_count=approver_count,
        provenance_issues=provenance_issues,
        tool_policy_issues=tool_policy_issues,
        tool_policy_digest=tool_policy_digest,
        checklist=checklist_report,
        criticality=resolution,
        surfaces=surface_decisions,
    )


def _validate_quarantine(
    quarantine: Quarantine | None,
    surface: ResolvedSurface,
    request: PromotionRequest,
    policy: SegregationPolicy,
) -> tuple[bool, str | None, bool]:
    if quarantine is None:
        return False, "standard-flake-quarantine-missing", False
    _, authority_issue = _record_human(
        quarantine.owner,
        policy=policy,
        implementer=request.implementer,
        verifier=request.verifier,
    )
    if authority_issue:
        return False, f"standard-flake-quarantine-{authority_issue}", False
    if request.evaluated_at <= 0 or quarantine.expires_at <= request.evaluated_at:
        return False, "standard-flake-quarantine-expired", False
    if not quarantine.rationale.strip():
        return False, "standard-flake-quarantine-rationale-missing", False
    valid, issue, integrity = _validate_authority_evidence(
        quarantine.evidence,
        quarantine.authority_body(surface.surface_id),
        missing_code="standard-flake-quarantine-evidence-missing",
        invalid_code="standard-flake-quarantine-evidence-invalid",
    )
    return valid, issue, integrity


def _validate_specialist_review(
    review: SpecialistReview,
    surface_id: str,
    request: PromotionRequest,
    policy: SegregationPolicy,
    profile: CriticalityProfile,
    hard_reasons: list[str],
    surface_gaps: list[str] | None,
) -> None:
    _, authority_issue = _record_human(
        review.reviewer,
        policy=policy,
        implementer=request.implementer,
        verifier=request.verifier,
    )
    if authority_issue:
        hard_reasons.append(f"specialist-review-{authority_issue}:{surface_id}")
    if review.candidate_digest != request.candidate_digest:
        hard_reasons.append(f"specialist-review-candidate-mismatch:{surface_id}")
    if review.criticality_profile_digest != profile.content_digest:
        hard_reasons.append(f"specialist-review-profile-mismatch:{surface_id}")
    valid, issue, integrity = _validate_authority_evidence(
        review.evidence,
        review.authority_body(),
        missing_code="specialist-review-evidence-missing",
        invalid_code="specialist-review-evidence-invalid",
    )
    if not valid and issue:
        if integrity or surface_gaps is None:
            hard_reasons.append(f"{issue}:{surface_id}")
        else:
            surface_gaps.append(issue)


def _validate_risk_acceptance(
    risk: RiskAcceptance,
    request: PromotionRequest,
    policy: SegregationPolicy,
    profile: CriticalityProfile,
    critical_surface_ids: set[str],
    standard_gap_ids: set[str],
    hard_reasons: list[str],
    gate_reasons: list[str],
    reports: list[str],
) -> bool:
    covered = {
        normalize_label(surface_id)
        for surface_id in risk.surface_ids
        if normalize_label(surface_id)
    }
    prohibited = covered & critical_surface_ids
    if prohibited:
        hard_reasons.extend(
            f"critical-risk-acceptance-prohibited:{surface_id}" for surface_id in sorted(prohibited)
        )
    if risk.candidate_digest != request.candidate_digest:
        hard_reasons.append("risk-acceptance-candidate-mismatch")
    if risk.criticality_profile_digest != profile.content_digest:
        hard_reasons.append("risk-acceptance-profile-mismatch")
    _, authority_issue = _record_human(
        risk.owner,
        policy=policy,
        implementer=request.implementer,
        verifier=request.verifier,
    )
    if authority_issue:
        gate_reasons.append(f"risk-acceptance-{authority_issue}")
    if request.evaluated_at <= 0 or risk.expires_at <= request.evaluated_at:
        gate_reasons.append("risk-acceptance-expired")
    if not risk.rationale.strip():
        gate_reasons.append("risk-acceptance-rationale-missing")
    uncovered = standard_gap_ids - covered
    gate_reasons.extend(
        f"risk-acceptance-surface-missing:{surface_id}" for surface_id in sorted(uncovered)
    )
    valid_evidence, evidence_issue, integrity = _validate_authority_evidence(
        risk.evidence,
        risk.authority_body(),
        missing_code="risk-acceptance-evidence-missing",
        invalid_code="risk-acceptance-evidence-invalid",
    )
    if not valid_evidence and evidence_issue:
        if integrity:
            hard_reasons.append(evidence_issue)
        else:
            gate_reasons.append(evidence_issue)
    if covered - standard_gap_ids - critical_surface_ids:
        reports.append("risk-acceptance-covers-surface-without-gap")

    return not (
        prohibited
        or risk.candidate_digest != request.candidate_digest
        or risk.criticality_profile_digest != profile.content_digest
        or authority_issue
        or request.evaluated_at <= 0
        or risk.expires_at <= request.evaluated_at
        or not risk.rationale.strip()
        or uncovered
        or not valid_evidence
    )
