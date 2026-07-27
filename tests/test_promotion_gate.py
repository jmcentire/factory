"""Oracle-adequacy × surface-criticality promotion policy tests."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from factory_core.criticality import (
    CRITICAL_APPROVER_FLOOR,
    CRITICALITY_COSMETIC,
    CRITICALITY_CRITICAL,
    CRITICALITY_STANDARD,
    CriticalityProfile,
    SurfaceControl,
)
from factory_core.manifest import SegregationPolicy, digest_obj
from factory_core.promotion import (
    DISPOSITION_BLOCK,
    DISPOSITION_GATE,
    DISPOSITION_PROMOTE,
    DISPOSITION_REPORT_AND_PROMOTE,
    DISPOSITION_RISK_ACCEPTED,
    EvidenceIntegrity,
    GateOutcome,
    NamedEvidence,
    PromotionRequest,
    Quarantine,
    RiskAcceptance,
    SpecialistReview,
    SurfaceObservation,
    decide_promotion,
    promotion_attestation_subject,
)
from factory_core.provenance import (
    CLAIM_REQUIREMENT,
    PHASE_ARCHITECTURE,
    PHASE_OPERATIONAL_MATURITY,
    PHASE_PRODUCT_SPECIFICATION,
    IntentBackreference,
    IntentItem,
    PhaseArtifact,
    ProvenanceBundle,
    ProvenanceClaim,
)
from factory_core.tool_policy import (
    TOOL_TIER_ALLOWED,
    TOOL_TIER_VERBOTEN,
    DenialProbe,
    ToolPolicy,
    ToolPolicyBundle,
    ToolRule,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "factory_core" / "promotion.py"
DENYLIST_TOKENS = tuple(
    json.loads((REPO_ROOT / "core_purity_denylist.json").read_text(encoding="utf-8")).get(
        "tokens", []
    )
)
CANDIDATE = digest_obj({"artifact": "candidate-a"})


def _roster() -> SegregationPolicy:
    return SegregationPolicy(
        human_ids=frozenset({"alice", "bob", "carol"}),
        human_aliases={
            "alice": "alice",
            "alice@example.com": "alice",
            "alice.smith@example.com": "alice",
            "bob": "bob",
            "bob@example.com": "bob",
            "carol": "carol",
        },
        excluded_service_identities=frozenset({"*-bot", "factory-agent", "claude*"}),
    )


def _control(
    surface_id: str,
    criticality: str,
    *,
    component_id: str = "component-a",
    decided_by: str = "carol",
    wrong_cost: str = "The class-bounded failure described by the signed architecture.",
    side_effects: tuple[str, ...] = (),
    required_evidence: frozenset[str] = frozenset(),
    standard_flake_budget: int = 0,
) -> SurfaceControl:
    return SurfaceControl(
        surface_id=surface_id,
        component_id=component_id,
        criticality=criticality,
        decided_by=decided_by,
        wrong_cost=wrong_cost,
        side_effect_surface_ids=side_effects,
        required_evidence_ids=required_evidence,
        standard_flake_budget=standard_flake_budget,
    )


def _profile(*, surfaces: tuple[SurfaceControl, ...] | None = None) -> CriticalityProfile:
    return CriticalityProfile(
        surfaces=surfaces
        or (
            _control("critical-surface", CRITICALITY_CRITICAL),
            _control("standard-surface", CRITICALITY_STANDARD),
            _control("cosmetic-surface", CRITICALITY_COSMETIC),
        ),
        required_gate_ids=frozenset({"tests", "build"}),
    )


def _evidence(body: dict[str, Any]) -> EvidenceIntegrity:
    return EvidenceIntegrity(body=body, claimed_digest=digest_obj(body))


def _attestation(
    request: PromotionRequest,
    *,
    profile: CriticalityProfile | None = None,
) -> EvidenceIntegrity:
    selected_profile = profile or _profile()
    return _evidence(
        {
            **promotion_attestation_subject(request, selected_profile),
            "manifest": "change-evidence",
        }
    )


def _live(surface_id: str, candidate: str = CANDIDATE) -> EvidenceIntegrity:
    return _evidence(
        {
            "surface_id": surface_id,
            "candidate_digest": candidate,
            "result": "passed",
            "environment": "running-instance",
        }
    )


def _observation(
    surface_id: str,
    *,
    adequate: bool = True,
    live_result: str = "passed",
    deterministic: bool = True,
    flake_count: int = 0,
    automatic_retry_count: int = 0,
    evidence: tuple[NamedEvidence, ...] = (),
    quarantine: Quarantine | None = None,
) -> SurfaceObservation:
    return SurfaceObservation(
        surface_id=surface_id,
        oracle_adequate=adequate,
        live_result=live_result,
        live_evidence=_live(surface_id) if live_result == "passed" else None,
        evidence=evidence,
        deterministic=deterministic,
        flake_count=flake_count,
        automatic_retry_count=automatic_retry_count,
        quarantine=quarantine,
    )


def _good_provenance() -> ProvenanceBundle:
    phase_rows = (
        (PHASE_PRODUCT_SPECIFICATION, "phase-1", "behavior", "The interface returns the record."),
        (PHASE_ARCHITECTURE, "phase-2", "owner", "Component alpha owns the record."),
        (
            PHASE_OPERATIONAL_MATURITY,
            "phase-3",
            "failure",
            "An unavailable authority denies the mutation.",
        ),
    )
    artifacts = tuple(
        PhaseArtifact(
            artifact_id=artifact_id,
            phase=phase,
            version="1",
            source_digest=digest_obj({"verbatim": f"source for {artifact_id}"}),
            human_ratifier="human-1",
            validator_ratifier="validator-1",
            items=(IntentItem(item_id=item_id, canonical_statement=statement),),
        )
        for phase, artifact_id, item_id, statement in phase_rows
    )
    item = artifacts[0].items[0]
    claim = ProvenanceClaim(
        claim_id="requirement-1",
        kind=CLAIM_REQUIREMENT,
        backreference=IntentBackreference(
            artifact_id=artifacts[0].artifact_id,
            artifact_digest=artifacts[0].content_digest,
            item_id=item.item_id,
            intent_digest=item.intent_digest,
        ),
    )
    return ProvenanceBundle(
        artifacts=artifacts,
        claims=(claim,),
        trusted_artifact_digests={
            artifact.artifact_id: artifact.content_digest for artifact in artifacts
        },
    )


def _gate(
    gate_id: str,
    *,
    passed: bool = True,
    candidate: str = CANDIDATE,
) -> GateOutcome:
    unsigned = GateOutcome(
        id=gate_id,
        passed=passed,
        detail="passed" if passed else "failed",
        recorded_at=90,
    )
    return replace(unsigned, evidence=_evidence(unsigned.authority_body(candidate)))


def _passing_gates() -> tuple[GateOutcome, ...]:
    return (
        _gate("tests"),
        _gate("build"),
    )


def _good_tool_policy(provenance: ProvenanceBundle | None = None) -> ToolPolicyBundle:
    authority = provenance or _good_provenance()
    architecture = authority.artifacts[1]
    operations = authority.artifacts[2]
    policy = ToolPolicy(
        policy_id="run-policy",
        version="1",
        run_id="run-1",
        issued_at=1,
        expires_at=1_000,
        signed_by="alice",
        independently_approved_by="bob",
        inventory_tool_ids=frozenset({"workspace", "production-mutation"}),
        rules=(
            ToolRule(
                tool_id="workspace",
                tier=TOOL_TIER_ALLOWED,
                scope_ids=frozenset({"read-write"}),
                backreference=architecture.backreference(architecture.items[0]),
            ),
            ToolRule(
                tool_id="production-mutation",
                tier=TOOL_TIER_VERBOTEN,
                backreference=operations.backreference(operations.items[0]),
            ),
        ),
    )
    unsigned_probe = DenialProbe(
        probe_id="deny-production",
        tool_id="production-mutation",
        scope_id="write",
        attempted_at=10,
        refused=True,
        policy_digest=policy.content_digest,
        run_id=policy.run_id,
    )
    probe = replace(
        unsigned_probe,
        evidence=_evidence(unsigned_probe.authority_body()),
    )
    return ToolPolicyBundle(
        policy=policy,
        trusted_policy_digest=policy.content_digest,
        provenance=authority,
        denial_probes=(probe,),
    )


def _review(
    surface_id: str,
    *,
    reviewer: str = "carol",
    candidate: str = CANDIDATE,
    passed: bool = True,
    profile: CriticalityProfile | None = None,
) -> SpecialistReview:
    selected_profile = profile or _profile()
    body = {
        "surface_id": surface_id,
        "reviewer": reviewer,
        "candidate_digest": candidate,
        "criticality_profile_digest": selected_profile.content_digest,
        "passed": passed,
    }
    return SpecialistReview(
        surface_id=surface_id,
        reviewer=reviewer,
        candidate_digest=candidate,
        criticality_profile_digest=selected_profile.content_digest,
        passed=passed,
        evidence=_evidence(body),
    )


def _risk(
    surface_ids: tuple[str, ...],
    *,
    owner: str = "alice",
    candidate: str = CANDIDATE,
    expires_at: int = 200,
    profile: CriticalityProfile | None = None,
) -> RiskAcceptance:
    selected_profile = profile or _profile()
    body = {
        "owner": owner,
        "surface_ids": sorted(surface_ids),
        "candidate_digest": candidate,
        "criticality_profile_digest": selected_profile.content_digest,
        "expires_at": expires_at,
        "rationale": "The bounded Standard gap is accepted until the stated expiry.",
    }
    return RiskAcceptance(
        owner=owner,
        surface_ids=surface_ids,
        candidate_digest=candidate,
        criticality_profile_digest=selected_profile.content_digest,
        expires_at=expires_at,
        rationale=body["rationale"],
        evidence=_evidence(body),
    )


def _quarantine(
    surface_id: str,
    *,
    owner: str = "alice",
    expires_at: int = 200,
) -> Quarantine:
    body = {
        "surface_id": surface_id,
        "owner": owner,
        "expires_at": expires_at,
        "rationale": "The flake debt is owned and time-bounded.",
    }
    return Quarantine(
        owner=owner,
        expires_at=expires_at,
        rationale=body["rationale"],
        evidence=_evidence(body),
    )


def _request(
    *,
    profile: CriticalityProfile | None = None,
    **overrides: Any,
) -> PromotionRequest:
    selected_profile = profile or _profile()
    evidence_overridden = "evidence" in overrides
    tool_policy_overridden = "tool_policy" in overrides
    values: dict[str, Any] = {
        "candidate_digest": CANDIDATE,
        "disturbed_surface_ids": ("standard-surface",),
        "observations": (_observation("standard-surface"),),
        "gates": _passing_gates(),
        "implementer": "claude-opus",
        "verifier": "ci-bot",
        "approvers": (),
        "specialist_reviews": (),
        "evaluated_at": 100,
        "evidence": None,
        "provenance": _good_provenance(),
        "tool_policy": None,
    }
    values.update(overrides)
    if not tool_policy_overridden:
        authority = values["provenance"]
        values["tool_policy"] = _good_tool_policy(
            authority if isinstance(authority, ProvenanceBundle) else None
        )
    request = PromotionRequest(**values)
    if evidence_overridden:
        return request
    return replace(
        request,
        evidence=_attestation(request, profile=selected_profile),
    )


def _critical_request(
    *,
    profile: CriticalityProfile | None = None,
    **overrides: Any,
) -> PromotionRequest:
    selected_profile = profile or _profile()
    values: dict[str, Any] = {
        "disturbed_surface_ids": ("critical-surface",),
        "observations": (_observation("critical-surface"),),
        "approvers": ("alice", "bob"),
        "specialist_reviews": (_review("critical-surface", profile=selected_profile),),
    }
    values.update(overrides)
    return _request(profile=selected_profile, **values)


def _rebind(
    request: PromotionRequest,
    profile: CriticalityProfile | None = None,
) -> PromotionRequest:
    return replace(
        request,
        evidence=_attestation(request, profile=profile),
    )


def test_empty_request_blocks_default_deny() -> None:
    decision = decide_promotion(PromotionRequest(), _roster(), _profile())

    assert decision.allowed is False
    assert decision.disposition == DISPOSITION_BLOCK
    assert "candidate-digest-missing" in decision.reasons
    assert "criticality-profile-invalid:disturbed-surfaces-missing" in decision.reasons
    assert decision.required_approvers == CRITICAL_APPROVER_FLOOR


def test_standard_and_cosmetic_adequate_oracles_auto_promote_without_clicks() -> None:
    standard = decide_promotion(_request(), _roster(), _profile())
    cosmetic = decide_promotion(
        _request(
            disturbed_surface_ids=("cosmetic-surface",),
            observations=(_observation("cosmetic-surface"),),
        ),
        _roster(),
        _profile(),
    )

    assert standard.allowed and standard.disposition == DISPOSITION_PROMOTE
    assert cosmetic.allowed and cosmetic.disposition == DISPOSITION_PROMOTE
    assert standard.required_approvers == cosmetic.required_approvers == 0


def test_cosmetic_oracle_and_live_gaps_are_reported_and_promoted() -> None:
    request = _request(
        disturbed_surface_ids=("cosmetic-surface",),
        observations=(
            _observation(
                "cosmetic-surface",
                adequate=False,
                live_result="missing",
                deterministic=False,
                flake_count=2,
                automatic_retry_count=3,
            ),
        ),
    )

    decision = decide_promotion(request, _roster(), _profile())

    assert decision.allowed is True
    assert decision.disposition == DISPOSITION_REPORT_AND_PROMOTE
    assert "cosmetic-gap:cosmetic-surface:oracle-silent" in decision.reports
    assert "cosmetic-gap:cosmetic-surface:live-verification-missing" in decision.reports
    assert "cosmetic-surface:cosmetic-flake:2" in decision.reports
    assert decision.surfaces[0].automatic_retry_count == 3


def test_standard_gap_gates_until_candidate_bound_risk_acceptance() -> None:
    gap = _request(
        observations=(_observation("standard-surface", adequate=False),),
    )

    gated = decide_promotion(gap, _roster(), _profile())
    accepted_request = _rebind(replace(gap, risk_acceptance=_risk(("standard-surface",))))
    accepted = decide_promotion(
        accepted_request,
        _roster(),
        _profile(),
    )

    assert gated.allowed is False
    assert gated.disposition == DISPOSITION_GATE
    assert "standard-gap-requires-risk-acceptance:standard-surface" in gated.reasons
    assert accepted.allowed is True
    assert accepted.disposition == DISPOSITION_RISK_ACCEPTED
    assert any(report.startswith("standard-gap-risk-accepted:") for report in accepted.reports)


def test_expired_or_wrong_candidate_risk_acceptance_cannot_open_gate() -> None:
    gap = _request(observations=(_observation("standard-surface", adequate=False),))
    expired_request = _rebind(
        replace(gap, risk_acceptance=_risk(("standard-surface",), expires_at=100))
    )
    expired = decide_promotion(
        expired_request,
        _roster(),
        _profile(),
    )
    wrong_candidate_request = _rebind(
        replace(
            gap,
            risk_acceptance=_risk(
                ("standard-surface",),
                candidate=digest_obj({"artifact": "different"}),
            ),
        )
    )
    wrong_candidate = decide_promotion(
        wrong_candidate_request,
        _roster(),
        _profile(),
    )

    assert expired.disposition == DISPOSITION_GATE
    assert "risk-acceptance-expired" in expired.reasons
    assert wrong_candidate.disposition == DISPOSITION_BLOCK
    assert "risk-acceptance-candidate-mismatch" in wrong_candidate.reasons


def test_critical_happy_path_requires_specialist_review_and_two_humans() -> None:
    decision = decide_promotion(_critical_request(), _roster(), _profile())

    assert decision.allowed is True, decision.reasons
    assert decision.disposition == DISPOSITION_PROMOTE
    assert decision.highest_criticality == CRITICALITY_CRITICAL
    assert decision.required_approvers == CRITICAL_APPROVER_FLOOR == 2
    assert decision.approver_count == 2


def test_critical_oracle_gap_blocks_and_any_critical_waiver_is_prohibited() -> None:
    request = _critical_request(
        observations=(_observation("critical-surface", adequate=False),),
        risk_acceptance=_risk(("critical-surface",)),
    )

    decision = decide_promotion(request, _roster(), _profile())

    assert decision.allowed is False
    assert decision.disposition == DISPOSITION_BLOCK
    assert "critical-gap:critical-surface:oracle-silent" in decision.reasons
    assert "critical-risk-acceptance-prohibited:critical-surface" in decision.reasons


def test_critical_waiver_is_prohibited_even_when_oracle_is_adequate() -> None:
    decision = decide_promotion(
        _critical_request(risk_acceptance=_risk(("critical-surface",))),
        _roster(),
        _profile(),
    )

    assert decision.allowed is False
    assert "critical-risk-acceptance-prohibited:critical-surface" in decision.reasons


def test_critical_flake_or_retry_remains_negative_after_green() -> None:
    decision = decide_promotion(
        _critical_request(
            observations=(
                _observation(
                    "critical-surface",
                    flake_count=1,
                    automatic_retry_count=1,
                ),
            )
        ),
        _roster(),
        _profile(),
    )

    assert decision.allowed is False
    assert "negative-evidence:critical-surface:critical-test-flaked:1" in decision.reasons
    assert "negative-evidence:critical-surface:critical-automatic-retry:1" in decision.reasons
    assert decision.surfaces[0].flake_count == 1


def test_critical_nondeterminism_and_missing_specialist_review_block() -> None:
    decision = decide_promotion(
        _critical_request(
            observations=(_observation("critical-surface", deterministic=False),),
            specialist_reviews=(),
        ),
        _roster(),
        _profile(),
    )

    assert (
        "negative-evidence:critical-surface:critical-evidence-nondeterministic" in decision.reasons
    )
    assert "critical-gap:critical-surface:specialist-review-missing" in decision.reasons


def test_standard_flake_budget_requires_owned_unexpired_quarantine() -> None:
    profile = _profile(
        surfaces=(
            _control(
                "standard-surface",
                CRITICALITY_STANDARD,
                standard_flake_budget=1,
            ),
        )
    )
    good = _request(
        profile=profile,
        observations=(
            _observation(
                "standard-surface",
                flake_count=1,
                quarantine=_quarantine("standard-surface"),
            ),
        ),
    )
    expired = _rebind(
        replace(
            good,
            observations=(
                _observation(
                    "standard-surface",
                    flake_count=1,
                    quarantine=_quarantine("standard-surface", expires_at=100),
                ),
            ),
        ),
        profile,
    )

    allowed = decide_promotion(good, _roster(), profile)
    gated = decide_promotion(expired, _roster(), profile)

    assert allowed.allowed is True
    assert "standard-surface:standard-flake-quarantined:1" in allowed.reports
    assert gated.allowed is False
    assert gated.disposition == DISPOSITION_GATE


def test_declared_cosmetic_side_effect_to_critical_inherits_critical() -> None:
    profile = _profile(
        surfaces=(
            _control(
                "cosmetic-root",
                CRITICALITY_COSMETIC,
                side_effects=("critical-leaf",),
            ),
            _control("critical-leaf", CRITICALITY_CRITICAL),
        )
    )
    request = _request(
        profile=profile,
        disturbed_surface_ids=("cosmetic-root",),
        observations=(
            _observation("cosmetic-root"),
            _observation("critical-leaf", adequate=False),
        ),
        approvers=("alice", "bob"),
        specialist_reviews=(_review("critical-leaf", profile=profile),),
    )

    decision = decide_promotion(request, _roster(), profile)

    assert decision.highest_criticality == CRITICALITY_CRITICAL
    assert {surface.surface_id for surface in decision.surfaces} == {
        "cosmetic-root",
        "critical-leaf",
    }
    assert "critical-gap:critical-leaf:oracle-silent" in decision.reasons


def test_unclassified_surface_defaults_to_critical() -> None:
    request = _request(
        disturbed_surface_ids=("unknown-surface",),
        observations=(_observation("unknown-surface"),),
        approvers=("alice", "bob"),
        specialist_reviews=(_review("unknown-surface"),),
    )

    decision = decide_promotion(request, _roster(), _profile())

    assert decision.allowed is True, decision.reasons
    assert decision.highest_criticality == CRITICALITY_CRITICAL
    assert "surface-unclassified:unknown-surface" in decision.reports


def test_missing_attestation_is_class_disposed_but_tamper_blocks_all() -> None:
    cosmetic_missing = decide_promotion(
        _request(
            disturbed_surface_ids=("cosmetic-surface",),
            observations=(_observation("cosmetic-surface"),),
            evidence=None,
        ),
        _roster(),
        _profile(),
    )
    bad_body = {
        "candidate_digest": CANDIDATE,
        "criticality_profile_digest": _profile().content_digest,
    }
    bad = EvidenceIntegrity(
        body=bad_body,
        claimed_digest=digest_obj({**bad_body, "candidate_digest": "different"}),
    )
    cosmetic_tamper = decide_promotion(
        _request(
            disturbed_surface_ids=("cosmetic-surface",),
            observations=(_observation("cosmetic-surface"),),
            evidence=bad,
        ),
        _roster(),
        _profile(),
    )

    assert cosmetic_missing.allowed is True
    assert cosmetic_missing.disposition == DISPOSITION_REPORT_AND_PROMOTE
    assert "cosmetic-gap:cosmetic-surface:attestation-missing" in cosmetic_missing.reports
    assert cosmetic_tamper.allowed is False
    assert "attestation-digest-mismatch" in cosmetic_tamper.reasons


def test_attestation_cannot_be_replayed_under_a_different_control_profile() -> None:
    altered_profile = _profile(
        surfaces=(
            _control("critical-surface", CRITICALITY_CRITICAL),
            _control(
                "standard-surface",
                CRITICALITY_STANDARD,
                wrong_cost="A different human-ratified cost statement.",
            ),
            _control("cosmetic-surface", CRITICALITY_COSMETIC),
        )
    )

    decision = decide_promotion(_request(), _roster(), altered_profile)

    assert decision.allowed is False
    assert "attestation-subject-mismatch" in decision.reasons


def test_attestation_binds_disturbance_determinism_and_approval_inputs() -> None:
    cosmetic = _request(
        disturbed_surface_ids=("cosmetic-surface",),
        observations=(_observation("cosmetic-surface"),),
    )
    changed_observation = replace(
        cosmetic,
        observations=(
            _observation(
                "cosmetic-surface",
                flake_count=1,
                automatic_retry_count=1,
            ),
        ),
    )
    changed_disturbance = replace(
        cosmetic,
        disturbed_surface_ids=("standard-surface",),
        observations=(_observation("standard-surface"),),
    )
    critical = _critical_request()
    changed_approvers = replace(critical, approvers=("alice", "carol"))

    for tampered in (changed_observation, changed_disturbance, changed_approvers):
        decision = decide_promotion(tampered, _roster(), _profile())
        assert decision.allowed is False
        assert "attestation-subject-mismatch" in decision.reasons


def test_implementer_and_verifier_identities_are_required_even_when_attested() -> None:
    missing = decide_promotion(
        _request(implementer="", verifier=""),
        _roster(),
        _profile(),
    )

    assert missing.allowed is False
    assert "implementer-missing" in missing.reasons
    assert "verifier-missing" in missing.reasons


def test_missing_provenance_is_a_gap_but_unresolved_reference_is_integrity_failure() -> None:
    cosmetic = {
        "disturbed_surface_ids": ("cosmetic-surface",),
        "observations": (_observation("cosmetic-surface"),),
    }
    missing = decide_promotion(
        _request(**cosmetic, provenance=None),
        _roster(),
        _profile(),
    )
    good = _good_provenance()
    bad_claim = ProvenanceClaim(
        claim_id="requirement-1",
        kind=CLAIM_REQUIREMENT,
        backreference=IntentBackreference(
            artifact_id=good.artifacts[0].artifact_id,
            artifact_digest=good.artifacts[0].content_digest,
            item_id="absent",
            intent_digest=good.artifacts[0].items[0].intent_digest,
        ),
    )
    unresolved = ProvenanceBundle(
        artifacts=good.artifacts,
        claims=(bad_claim,),
        trusted_artifact_digests=good.trusted_artifact_digests,
    )
    invalid = decide_promotion(
        _request(**cosmetic, provenance=unresolved),
        _roster(),
        _profile(),
    )

    assert missing.allowed is True
    assert any("provenance-missing" in report for report in missing.reports)
    assert invalid.allowed is False
    assert any(
        reason.startswith("provenance-integrity:item-unresolved:") for reason in invalid.reasons
    )


def test_missing_tool_policy_blocks_because_the_run_has_no_capability_authority() -> None:
    decision = decide_promotion(
        _request(
            disturbed_surface_ids=("cosmetic-surface",),
            observations=(_observation("cosmetic-surface"),),
            tool_policy=None,
        ),
        _roster(),
        _profile(),
    )

    assert decision.allowed is False
    assert "tool-policy-missing" in decision.reasons


def test_tool_policy_must_derive_from_the_candidate_phase_artifact_versions() -> None:
    candidate_authority = _good_provenance()
    amended_architecture = replace(candidate_authority.artifacts[1], version="2")
    policy_artifacts = (
        candidate_authority.artifacts[0],
        amended_architecture,
        candidate_authority.artifacts[2],
    )
    policy_authority = ProvenanceBundle(
        artifacts=policy_artifacts,
        claims=candidate_authority.claims,
        trusted_artifact_digests={
            artifact.artifact_id: artifact.content_digest for artifact in policy_artifacts
        },
    )
    request = _request(
        provenance=candidate_authority,
        tool_policy=_good_tool_policy(policy_authority),
    )

    decision = decide_promotion(request, _roster(), _profile())

    assert decision.allowed is False
    assert "tool-policy-phase-artifacts-mismatch" in decision.reasons


def test_gate_absence_is_class_disposed_but_failed_gate_is_negative_evidence() -> None:
    cosmetic = {
        "disturbed_surface_ids": ("cosmetic-surface",),
        "observations": (_observation("cosmetic-surface"),),
    }
    missing = decide_promotion(
        _request(**cosmetic, gates=(_gate("tests"),)),
        _roster(),
        _profile(),
    )
    failed = decide_promotion(
        _request(
            **cosmetic,
            gates=(
                _gate("tests", passed=False),
                _gate("build"),
            ),
        ),
        _roster(),
        _profile(),
    )

    assert missing.allowed is True
    assert (
        "cosmetic-gap:cosmetic-surface:checklist-item-missing:build" in missing.reports
    )
    assert failed.allowed is False
    assert (
        "negative-evidence:cosmetic-surface:checklist-item-failed:tests"
        in failed.reasons
    )


def test_a_checked_box_without_cited_evidence_is_a_gap_and_tamper_blocks() -> None:
    cosmetic = {
        "disturbed_surface_ids": ("cosmetic-surface",),
        "observations": (_observation("cosmetic-surface"),),
    }
    uncited = GateOutcome(
        id="tests",
        passed=True,
        detail="remembered as green",
        recorded_at=90,
    )
    missing = decide_promotion(
        _request(**cosmetic, gates=(uncited, _gate("build"))),
        _roster(),
        _profile(),
    )
    valid = _gate("tests")
    assert valid.evidence is not None
    tampered = replace(
        valid,
        evidence=EvidenceIntegrity(
            body=valid.evidence.body,
            claimed_digest=digest_obj({"different": "body"}),
        ),
    )
    invalid = decide_promotion(
        _request(**cosmetic, gates=(tampered, _gate("build"))),
        _roster(),
        _profile(),
    )

    assert missing.allowed is True
    assert any("checklist-item-evidence-missing:tests" in report for report in missing.reports)
    assert invalid.allowed is False
    assert (
        "checklist-integrity:checklist-item-evidence-digest-mismatch:tests"
        in invalid.reasons
    )


def test_additional_required_evidence_is_subject_bound() -> None:
    profile = _profile(
        surfaces=(
            _control(
                "standard-surface",
                CRITICALITY_STANDARD,
                required_evidence=frozenset({"contract"}),
            ),
        )
    )
    body = {
        "surface_id": "standard-surface",
        "candidate_digest": CANDIDATE,
        "evidence_id": "contract",
        "result": "passed",
    }
    present = _request(
        profile=profile,
        observations=(
            _observation(
                "standard-surface",
                evidence=(
                    NamedEvidence(
                        evidence_id="contract",
                        integrity=_evidence(body),
                    ),
                ),
            ),
        ),
    )
    missing = _request(profile=profile)

    assert decide_promotion(present, _roster(), profile).allowed is True
    gated = decide_promotion(missing, _roster(), profile)
    assert gated.disposition == DISPOSITION_GATE


def test_approver_sod_and_distinct_human_floor_are_deny_wins() -> None:
    one_human_twice = _critical_request(
        approvers=("alice@example.com", "alice.smith@example.com"),
    )
    agent = _critical_request(approvers=("alice", "factory-agent"))

    duplicate = decide_promotion(one_human_twice, _roster(), _profile())
    denied_agent = decide_promotion(agent, _roster(), _profile())

    assert duplicate.approver_count == 1
    assert "insufficient-approvers:1/2" in duplicate.reasons
    assert "approver-is-agent:factory-agent" in denied_agent.reasons


def test_specialist_review_is_human_candidate_and_evidence_bound() -> None:
    wrong_candidate = digest_obj({"artifact": "wrong"})
    review = _review("critical-surface", candidate=wrong_candidate)

    decision = decide_promotion(
        _critical_request(specialist_reviews=(review,)),
        _roster(),
        _profile(),
    )

    assert decision.allowed is False
    assert "specialist-review-candidate-mismatch:critical-surface" in decision.reasons


def test_from_dict_and_decision_serialization_preserve_determinism_record() -> None:
    profile = CriticalityProfile.from_dict(
        {
            "surfaces": [
                {
                    "surface_id": "critical-surface",
                    "component_id": "component-a",
                    "criticality": "critical",
                    "decided_by": "carol",
                    "wrong_cost": "A critical failure.",
                }
            ],
            "required_gate_ids": ["tests", "build"],
        }
    )
    review = _review("critical-surface", profile=profile)
    assert review.evidence is not None
    provenance = _good_provenance()
    tool_policy = _good_tool_policy(provenance)
    raw_request: dict[str, Any] = {
        "candidate_digest": CANDIDATE,
        "disturbed_surface_ids": ["critical-surface"],
        "observations": [
            {
                "surface_id": "critical-surface",
                "oracle_adequate": True,
                "live_result": "passed",
                "live_evidence": {
                    "body": dict(_live("critical-surface").body or {}),
                    "claimed_digest": _live("critical-surface").claimed_digest,
                },
                "deterministic": True,
                "flake_count": 0,
                "automatic_retry_count": 0,
            },
        ],
        "gates": [_gate("tests").to_dict(), _gate("build").to_dict()],
        "implementer": "claude-opus",
        "verifier": "ci-bot",
        "approvers": ["alice", "bob"],
        "specialist_reviews": [
            {
                "surface_id": review.surface_id,
                "reviewer": review.reviewer,
                "candidate_digest": review.candidate_digest,
                "criticality_profile_digest": review.criticality_profile_digest,
                "passed": review.passed,
                "evidence": {
                    "body": dict(review.evidence.body or {}),
                    "claimed_digest": review.evidence.claimed_digest,
                },
            }
        ],
        "evaluated_at": 100,
        "provenance": provenance.to_dict(),
        "tool_policy": tool_policy.to_dict(),
    }
    unsigned = PromotionRequest.from_dict(raw_request)
    attestation = _attestation(unsigned, profile=profile)
    raw_request["evidence"] = {
        "body": dict(attestation.body or {}),
        "claimed_digest": attestation.claimed_digest,
    }
    request = PromotionRequest.from_dict(raw_request)

    decision = decide_promotion(request, _roster(), profile)
    serialized = decision.to_dict()

    assert decision.allowed is True, decision.reasons
    assert serialized["surfaces"][0]["deterministic"] is True
    assert serialized["surfaces"][0]["flake_count"] == 0
    assert serialized["surfaces"][0]["automatic_retry_count"] == 0
    assert serialized["criticality"]["surfaces"][0]["effective_criticality"] == "critical"


def _runs(text: str) -> set[str]:
    return {run for run in re.split(r"[^a-z0-9]+", text.lower()) if run}


def test_module_names_nothing_target_specific() -> None:
    runs = _runs(MODULE_PATH.read_text(encoding="utf-8"))
    assert not [token for token in DENYLIST_TOKENS if token in runs]
