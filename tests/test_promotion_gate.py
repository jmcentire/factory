"""Oracle-adequacy × surface-criticality promotion policy tests."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from factory_core.correction import (
    BASELINE_RESULT_FAILED,
    BASELINE_RESULT_PASSED,
    CONTROL_GREEN_NOW,
    CONTROL_RED_NOW,
    FAILURE_RELATION_DEFECT,
    FAILURE_RELATION_UNRELATED,
    LANE_CAPABILITY,
    LANE_CORRECTION,
    REPRODUCTION_REPRODUCED,
    ControlObservation,
    CorrectionRecord,
    ReproductionRecord,
)
from factory_core.criticality import (
    CRITICAL_APPROVER_FLOOR,
    CRITICALITY_COSMETIC,
    CRITICALITY_CRITICAL,
    CRITICALITY_STANDARD,
    CriticalityProfile,
    SurfaceControl,
)
from factory_core.independence import (
    INDEPENDENCE_MODERATE,
    INDEPENDENCE_STRONGER,
    INDEPENDENCE_STRONGEST,
    INDEPENDENCE_WEAKEST,
    ROLE_CODER,
    ROLE_TESTER,
    ROLE_VALIDATOR,
    STRUCTURAL_MODE_ISOLATED,
    AgentIdentity,
    IndependenceRecord,
    StructuralModeRecord,
)
from factory_core.manifest import SegregationPolicy, digest_obj
from factory_core.monitors import (
    MONITOR_AUTHORSHIP_GENERATED,
    MONITOR_AUTHORSHIP_HUMAN,
    MONITOR_DERIVATION_IMPLEMENTATION,
    MONITOR_DERIVATION_SPECIFICATION,
    Monitor,
)
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
    CLAIM_MONITOR,
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


def _profile(
    *,
    surfaces: tuple[SurfaceControl, ...] | None = None,
    delegates: frozenset[str] = frozenset({"alice", "bob", "carol"}),
) -> CriticalityProfile:
    return CriticalityProfile(
        surfaces=surfaces
        or (
            _control("critical-surface", CRITICALITY_CRITICAL),
            _control("standard-surface", CRITICALITY_STANDARD),
            _control("cosmetic-surface", CRITICALITY_COSMETIC),
        ),
        required_gate_ids=frozenset({"tests", "build"}),
        critical_ratification_delegates=delegates,
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
    oracle_receipt: str | None = None,
    oracle_receipt_evidence: EvidenceIntegrity | None = None,
    flake_receipt: str | None = None,
    flake_receipt_evidence: EvidenceIntegrity | None = None,
) -> SurfaceObservation:
    # Synthesize self-consistent receipts by default (None) so the happy path binds cleanly
    # under the enforcement cutover. Pass "" to force an explicitly absent receipt (testing the
    # hard-block); pass a real id + evidence to test binding/mismatch.
    if oracle_receipt is None:
        oracle_receipt = "M-default"
        oracle_receipt_evidence = _oracle_receipt_evidence("M-default", adequate=adequate)
    if flake_receipt is None:
        flake_receipt = "F-default"
        flake_receipt_evidence = _flake_receipt_evidence(
            "F-default",
            deterministic=deterministic,
            flake_count=flake_count,
            retry_count=automatic_retry_count,
        )
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
        oracle_receipt=oracle_receipt,
        oracle_receipt_evidence=oracle_receipt_evidence,
        flake_receipt=flake_receipt,
        flake_receipt_evidence=flake_receipt_evidence,
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
    # A monitor is an assertion about production and resolves on the same terms as a test
    # assertion, so the phase-3 criterion it watches is a claim in the same bundle.
    monitor_claim = ProvenanceClaim(
        claim_id="monitor-1",
        kind=CLAIM_MONITOR,
        backreference=artifacts[2].backreference(artifacts[2].items[0]),
    )
    return ProvenanceBundle(
        artifacts=artifacts,
        claims=(claim, monitor_claim),
        trusted_artifact_digests={
            artifact.artifact_id: artifact.content_digest for artifact in artifacts
        },
    )


def _monitor_backreference(provenance: ProvenanceBundle | None = None) -> IntentBackreference:
    authority = provenance or _good_provenance()
    operations = authority.artifacts[2]
    return operations.backreference(operations.items[0])


def _isolated_structural_mode() -> StructuralModeRecord:
    record = StructuralModeRecord(
        mode=STRUCTURAL_MODE_ISOLATED,
        decision_package_note=(
            "No signed interface contract anchored the oracle, so branch depth was not purchased."
        ),
    )
    return replace(record, mutation_evidence=_evidence(record.authority_body()))


def _independence(
    *,
    coder_family: str = "family-a",
    tester_family: str = "family-b",
    claimed_tier: str = INDEPENDENCE_STRONGER,
    shared_context: bool = False,
    channel_open: bool = False,
    mechanism_ids: tuple[str, ...] = (),
    structural: StructuralModeRecord | None = None,
) -> IndependenceRecord:
    return IndependenceRecord(
        agents=(
            AgentIdentity(
                role=ROLE_CODER,
                model_family=coder_family,
                model_version="2026-07",
                directive_version="coder-directive-3",
            ),
            AgentIdentity(
                role=ROLE_TESTER,
                model_family=tester_family,
                model_version="2026-07",
                directive_version="tester-directive-3",
            ),
            AgentIdentity(
                role=ROLE_VALIDATOR,
                model_family="family-c",
                model_version="2026-07",
                directive_version="validator-directive-3",
            ),
        ),
        shared_context=shared_context,
        channel_open=channel_open,
        mechanism_ids=mechanism_ids,
        claimed_tier=claimed_tier,
        structural_mode=structural if structural is not None else _isolated_structural_mode(),
    )


def _monitor(
    surface_id: str,
    *,
    derivation: str = MONITOR_DERIVATION_SPECIFICATION,
    authorship: str = MONITOR_AUTHORSHIP_HUMAN,
    author: str = "carol",
    backreference: IntentBackreference | None = None,
    provenance: ProvenanceBundle | None = None,
    notifies_human: bool = True,
    actionable_conclusion: str = "Page the surface owner with the unmet criterion.",
) -> Monitor:
    return Monitor(
        monitor_id=f"monitor-{surface_id}",
        surface_id=surface_id,
        derivation=derivation,
        authorship=authorship,
        author_identity=author,
        backreference=backreference or _monitor_backreference(provenance),
        actionable_conclusion=actionable_conclusion,
        notifies_human=notifies_human,
    )


def _reproduction(*, defect_id: str = "defect-1") -> ReproductionRecord:
    record = ReproductionRecord(
        defect_id=defect_id,
        result=REPRODUCTION_REPRODUCED,
        environment_id="ephemeral-1",
        disposable_environment=True,
        recorded_before_repair=True,
    )
    return replace(record, evidence=_evidence(record.authority_body()))


def _correction(
    *,
    defect_id: str = "defect-1",
    controls: tuple[ControlObservation, ...] | None = None,
    reproduction: ReproductionRecord | None = None,
    baseline_available: bool = True,
) -> CorrectionRecord:
    return CorrectionRecord(
        defect_id=defect_id,
        baseline_available=baseline_available,
        controls=controls
        if controls is not None
        else (
            ControlObservation(
                test_id="forces-the-defect",
                declared_role=CONTROL_RED_NOW,
                baseline_result=BASELINE_RESULT_FAILED,
                failure_relation=FAILURE_RELATION_DEFECT,
            ),
            ControlObservation(
                test_id="guards-unrelated-behavior",
                declared_role=CONTROL_GREEN_NOW,
                baseline_result=BASELINE_RESULT_PASSED,
            ),
        ),
        reproduction=reproduction
        if reproduction is not None
        else _reproduction(defect_id=defect_id),
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
    monitors_overridden = "monitors" in overrides
    values: dict[str, Any] = {
        "candidate_digest": CANDIDATE,
        "lane": LANE_CAPABILITY,
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
        "independence": _independence(),
    }
    values.update(overrides)
    # 1.1c: the core derives the disturbed set host-side from changed_paths + surface_map.
    # The helper keeps ``disturbed_surface_ids`` as a TEST-INTENT alias and translates it
    # into an exact path-per-surface binding, so the dozens of class-disposition tests read
    # unchanged while the core field they used to set no longer exists. Tests that exercise
    # derivation itself pass changed_paths/surface_map directly.
    alias = tuple(values.pop("disturbed_surface_ids"))
    if "changed_paths" not in values and "surface_map" not in values:
        values["changed_paths"] = tuple(f"src/{s}.py" for s in alias)
        values["surface_map"] = {f"src/{s}.py": s for s in alias}
    authority = values["provenance"]
    bundle = authority if isinstance(authority, ProvenanceBundle) else None
    if not tool_policy_overridden:
        values["tool_policy"] = _good_tool_policy(bundle)
    if not monitors_overridden:
        # The monitor set covers every surface the change disturbs; individual tests override it
        # to exercise a specific monitor defect.
        values["monitors"] = tuple(
            _monitor(surface_id, provenance=bundle) for surface_id in alias
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


# --------------------------------------------------------------------------
# Gate M (1.1c) — host-derived disturbed surfaces. No agent declaration exists to
# verify: decide_promotion derives the set from the host's changed paths via the
# plan-declared surface map. The forcing tests pin: derivation drives resolution,
# an unmapped path and an absent map route to implicit Critical (never the lightest
# tier), and a stale agent-declared field in the raw input is inert.
# --------------------------------------------------------------------------


def test_promotion_derives_surfaces_host_side_and_reports_the_derivation() -> None:
    """The GO sibling: mapped paths produce exactly the mapped surface set, the decision
    reports what it derived, and the baseline adequate-oracle standard request still
    auto-promotes with no receipt-envelope apparatus anywhere."""
    request = _request()
    decision = decide_promotion(request, _roster(), _profile())
    assert decision.allowed and decision.disposition == DISPOSITION_PROMOTE
    assert "disturbed-surface-derived:standard-surface" in decision.reports


def test_promotion_routes_unmapped_paths_to_implicit_critical() -> None:
    """A changed path with no plan-declared binding cannot pick its own tier: it becomes
    an implicit-Critical pseudo-surface whose unmet Critical obligations block. Shrinking
    the surface set is no longer a declaration an agent can make — the only way to shed
    the path is to change the ratified map."""
    request = _request(
        changed_paths=("src/standard-surface.py", "migrations/0042_drop.sql"),
        surface_map={"src/standard-surface.py": "standard-surface"},
        observations=(_observation("standard-surface"),),
        monitors=(_monitor("standard-surface", provenance=_good_provenance()),),
    )
    decision = decide_promotion(request, _roster(), _profile())
    assert decision.disposition == DISPOSITION_BLOCK
    assert (
        "disturbed-surface-unmapped-critical:migrations/0042_drop.sql" in decision.reports
    )
    assert decision.highest_criticality == CRITICALITY_CRITICAL
    assert "surface-unclassified:path:migrations/0042_drop.sql" in decision.reports


def test_promotion_missing_surface_map_routes_every_path_to_critical() -> None:
    """No map at all over a non-empty diff: every path is unmapped, the whole run pays
    the full Critical tier, and the absence itself is reported — over-verifying is the
    only safe default (plan 1.1c; matches criticality.py unknown-to-Critical)."""
    request = _request(
        changed_paths=("src/anything.py",),
        surface_map=None,
        observations=(_observation("standard-surface"),),
    )
    decision = decide_promotion(request, _roster(), _profile())
    assert decision.disposition == DISPOSITION_BLOCK
    assert "surface-map-missing" in decision.reports
    assert "disturbed-surface-unmapped-critical:src/anything.py" in decision.reports
    assert decision.highest_criticality == CRITICALITY_CRITICAL


def test_retired_agent_declared_surface_field_is_inert() -> None:
    """The route-around this slice deletes: a raw input still carrying the retired
    agent-declared ``disturbed_surface_ids`` (claiming only a cosmetic surface) cannot
    steer resolution — the host derivation from the diff wins, and the unmapped path
    still routes to Critical."""
    raw = _freeze_request_dict(
        _request(
            changed_paths=("core/danger.py",),
            surface_map=None,
            observations=(_observation("standard-surface"),),
        )
    )
    raw["disturbed_surface_ids"] = ["cosmetic-surface"]  # the retired declaration
    request = PromotionRequest.from_dict(raw)
    decision = decide_promotion(request, _roster(), _profile())
    assert decision.disposition == DISPOSITION_BLOCK
    assert decision.highest_criticality == CRITICALITY_CRITICAL
    assert "disturbed-surface-unmapped-critical:core/danger.py" in decision.reports


def test_profile_declaring_a_path_prefixed_surface_id_is_invalid() -> None:
    """The ``path:`` namespace is reserved for unmapped-path pseudo-surfaces: a profile
    that declares one could pre-classify an unmapped path below Critical (declare
    "path:migrations/drop.sql" Cosmetic and the collision resolves to Cosmetic). The
    declaration itself blocks, unconditionally — the GO sibling is every other test in
    this file, whose profiles declare no reserved id."""
    profile = CriticalityProfile.from_dict({
        "surfaces": [
            _profile().to_dict()["surfaces"][0],
            {
                "surface_id": "path:migrations/0042_drop.sql",
                "criticality": "cosmetic",
                "component_id": "db",
                "decided_by": "alice",
                "wrong_cost": "low",
            },
        ],
        "side_effects": [],
        "required_gate_ids": [],
        "critical_ratification_delegates": ["alice", "bob"],
    })
    request = _request(profile=profile)
    decision = decide_promotion(request, _roster(), profile)
    assert decision.disposition == DISPOSITION_BLOCK
    assert "surface-id-reserved:path:migrations/0042_drop.sql" in decision.reasons


def _freeze_request_dict(request: PromotionRequest) -> dict[str, Any]:
    """Serialize a request the way conftest's ``_freeze`` does, for from_dict round-trips."""
    from tests.conftest import _freeze

    frozen = _freeze(request)
    assert isinstance(frozen, dict)
    return frozen


# --------------------------------------------------------------------------
# Gate N (slice 4) — observation-receipt binding. The self-reported oracle/determinism/
# flake fields must match the seam-attested receipt values (carried as content-addressed
# EvidenceIntegrity envelopes), or the run does not advance.
# --------------------------------------------------------------------------


def _oracle_receipt_evidence(receipt_id: str = "M-1", adequate: bool = True) -> EvidenceIntegrity:
    return _evidence({"receipt_id": receipt_id, "oracle_adequate": adequate})


def _flake_receipt_evidence(
    receipt_id: str = "F-1",
    deterministic: bool = True,
    flake_count: int = 0,
    retry_count: int = 0,
) -> EvidenceIntegrity:
    return _evidence({
        "receipt_id": receipt_id,
        "deterministic": deterministic,
        "flake_count": flake_count,
        "retry_count": retry_count,
    })


def test_promotion_rejects_oracle_binding_mismatch() -> None:
    """oracle_adequate is self-reported, but it binds to the mutation receipt: the agent
    claims adequate while the receipt attests not (the named oracle survived). Hard block."""
    obs = replace(
        _observation("standard-surface"),
        oracle_receipt="M-1",
        oracle_receipt_evidence=_oracle_receipt_evidence(adequate=False),
    )  # oracle_adequate defaults True -> contradicts the receipt
    request = _request(observations=(obs,))
    decision = decide_promotion(request, _roster(), _profile())
    assert decision.disposition == DISPOSITION_BLOCK
    assert "oracle-binding-mismatch:standard-surface" in decision.reasons


def test_promotion_rejects_oracle_receipt_evidence_missing() -> None:
    """Citing an oracle receipt but omitting its envelope is route-arounding — fail-closed."""
    obs = replace(
        _observation("standard-surface"), oracle_receipt="M-1", oracle_receipt_evidence=None
    )  # cite a receipt but omit its envelope
    request = _request(observations=(obs,))
    decision = decide_promotion(request, _roster(), _profile())
    assert decision.disposition == DISPOSITION_BLOCK
    assert "oracle-receipt-evidence-missing:standard-surface" in decision.reasons


def test_promotion_rejects_flake_binding_mismatch() -> None:
    """deterministic/flake_count/retry_count bind to the flake-detection receipt. A
    self-reported value that contradicts the attested receipt is a hard block, per field."""
    obs = replace(
        _observation("standard-surface"),
        flake_receipt="F-1",
        flake_receipt_evidence=_flake_receipt_evidence(
            deterministic=False, flake_count=3, retry_count=1
        ),
    )  # defaults: deterministic=True, flake_count=0, retry=0 -> all contradict
    request = _request(observations=(obs,))
    decision = decide_promotion(request, _roster(), _profile())
    assert decision.disposition == DISPOSITION_BLOCK
    assert "flake-binding-mismatch:standard-surface:deterministic" in decision.reasons
    assert "flake-binding-mismatch:standard-surface:flake_count" in decision.reasons
    assert "flake-binding-mismatch:standard-surface:retry_count" in decision.reasons


def test_promotion_rejects_flake_attested_value_missing() -> None:
    """A cited flake receipt whose envelope omits an attested value is fail-closed, not
    advisory: the agent cannot cite a receipt and skip the binding by omitting the value."""
    ev = _evidence({"receipt_id": "F-1"})  # no deterministic/flake_count/retry_count
    obs = replace(
        _observation("standard-surface"), flake_receipt="F-1", flake_receipt_evidence=ev
    )
    request = _request(observations=(obs,))
    decision = decide_promotion(request, _roster(), _profile())
    assert decision.disposition == DISPOSITION_BLOCK
    assert "flake-attested-value-missing:standard-surface:deterministic" in decision.reasons


def test_promotion_accepts_when_observation_matches_receipts() -> None:
    """When the self-reported values equal the attested receipt values the bindings hold
    and the decision proceeds on its other evidence — no binding-mismatch hard reason."""
    obs = replace(
        _observation("standard-surface"),
        oracle_receipt="M-1",
        oracle_receipt_evidence=_oracle_receipt_evidence(adequate=True),
        flake_receipt="F-1",
        flake_receipt_evidence=_flake_receipt_evidence(
            deterministic=True, flake_count=0, retry_count=0
        ),
    )
    request = _request(observations=(obs,))
    decision = decide_promotion(request, _roster(), _profile())
    assert not any(r.startswith("oracle-binding-mismatch") for r in decision.reasons)
    assert not any(r.startswith("flake-binding-mismatch") for r in decision.reasons)
    assert decision.allowed and decision.disposition == DISPOSITION_PROMOTE


def test_promotion_blocks_when_observation_receipts_absent() -> None:
    """Enforcement cutover: a positive adequacy/determinism claim with no seam-attested
    receipt is a hard block, not advisory — the self-report cannot stand without the
    machine-derived backing. The receipt is mandated only for positive claims (the lie
    vector); an honest gap needs no receipt (see the companion test below)."""
    obs = _observation("standard-surface", oracle_receipt="", flake_receipt="")
    request = _request(observations=(obs,))
    decision = decide_promotion(request, _roster(), _profile())
    assert decision.disposition == DISPOSITION_BLOCK
    assert "oracle-receipt-required:standard-surface" in decision.reasons
    assert "flake-receipt-required:standard-surface" in decision.reasons


def test_oracle_honest_gap_needs_no_receipt() -> None:
    """The ORACLE admission (adequate=False) is a pure admission — no positive claim, and no
    load-bearing positive value to attest — so no receipt is mandated: a Standard surface with
    a silent oracle gates on risk acceptance, it does not hard-block on a missing oracle receipt.

    The flake side here is kept clean (deterministic=True with a receipt) so this isolates the
    oracle-admission doctrine. The flake admission is NOT symmetric — see
    test_standard_flake_admission_requires_receipt_closes_false_close.
    """
    obs = _observation(
        "standard-surface",
        adequate=False,
        deterministic=True,  # clean flake side: a receipt is synthesized by _observation
        oracle_receipt="",
    )
    request = _request(observations=(obs,))
    decision = decide_promotion(request, _roster(), _profile())
    assert not any(r.startswith("oracle-receipt-required") for r in decision.reasons)
    assert not any(r.startswith("flake-receipt-required") for r in decision.reasons)
    assert decision.disposition == DISPOSITION_GATE
    assert "standard-gap-requires-risk-acceptance:standard-surface" in decision.reasons


def test_standard_flake_admission_requires_receipt_closes_false_close() -> None:
    """Opus F4 (cross-family review, 2026-08-14): on a Standard surface, the flake receipt was
    required ONLY when deterministic=True, and the flake receipt is the ONLY thing binding
    flake_count to a machine-derived value. So an agent could hide a flaky suite (real
    flake_count=5) by declaring deterministic=False + flake_count=0 with NO receipt — the
    receipt was waived because deterministic=False was treated as an admission, and the run
    PROMOTED with zero blocking reasons (a false close, empirically verified).

    The fix: the flake receipt is mandated on Standard (where flake_count is load-bearing for
    the quarantine disposition), regardless of the determinism claim. The "admission needs no
    receipt" doctrine holds for the ORACLE side (a pure admission) but NOT for the flake side
    on Standard, because flake_count is a load-bearing value the receipt attests. Cosmetic
    retains the no-receipt admission path (flake_count is not load-bearing there — no false
    close). This is the red-now test: reverting the Standard clause of the receipt requirement
    (removing ``or surface.effective_criticality == CRITICALITY_STANDARD``) turns this red as
    the game case promotes again.
    """
    obs = replace(
        _observation(
            "standard-surface",
            adequate=True,
            live_result="passed",
            deterministic=False,
            flake_count=0,
            automatic_retry_count=0,
        ),
        oracle_receipt="M-1",
        oracle_receipt_evidence=_oracle_receipt_evidence("M-1", adequate=True),
        flake_receipt="",
        flake_receipt_evidence=None,
    )
    request = _request(observations=(obs,))
    decision = decide_promotion(request, _roster(), _profile())
    assert decision.disposition == DISPOSITION_BLOCK
    assert "flake-receipt-required:standard-surface" in decision.reasons
    # The honest-gap alternative (adequate=False) still needs no ORACLE receipt — the oracle
    # side is unaffected by the flake-side fix.
    assert not any(r.startswith("oracle-receipt-required") for r in decision.reasons)


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
        changed_paths=("src/standard-surface.py",),
        surface_map={"src/standard-surface.py": "standard-surface"},
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
            "critical_ratification_delegates": ["alice", "bob", "carol"],
        }
    )
    review = _review("critical-surface", profile=profile)
    assert review.evidence is not None
    provenance = _good_provenance()
    tool_policy = _good_tool_policy(provenance)
    oracle_ev = _oracle_receipt_evidence("M-1", adequate=True)
    flake_ev = _flake_receipt_evidence("F-1", deterministic=True, flake_count=0, retry_count=0)
    raw_request: dict[str, Any] = {
        "candidate_digest": CANDIDATE,
        "lane": LANE_CAPABILITY,
        "independence": _independence().to_dict(),
        "monitors": [_monitor("critical-surface", provenance=provenance).to_dict()],
        "monitor_declared_unit_count": 75,
        "changed_paths": ["src/critical-surface.py"],
        "surface_map": {"src/critical-surface.py": "critical-surface"},
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
                "oracle_receipt": "M-1",
                "oracle_receipt_evidence": {
                    "body": dict(oracle_ev.body or {}),
                    "claimed_digest": oracle_ev.claimed_digest,
                },
                "flake_receipt": "F-1",
                "flake_receipt_evidence": {
                    "body": dict(flake_ev.body or {}),
                    "claimed_digest": flake_ev.claimed_digest,
                },
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
    assert serialized["lane"] == LANE_CAPABILITY
    assert serialized["independence"]["derived_tier"] == INDEPENDENCE_STRONGER
    assert serialized["independence"]["claimed_tier"] == INDEPENDENCE_STRONGER
    assert serialized["monitors"]["monitor_ids"] == ["monitor-critical-surface"]
    # Density is carried into the record and compared against nothing.
    assert serialized["monitors"]["density"] == 1 / 75
    assert serialized["correction"] is None


def test_an_undeclared_lane_is_a_class_disposed_gap_and_an_unknown_lane_blocks() -> None:
    undeclared = decide_promotion(_request(lane=""), _roster(), _profile())
    unknown = decide_promotion(_request(lane="hotfix"), _roster(), _profile())
    cosmetic = decide_promotion(
        _request(
            lane="",
            disturbed_surface_ids=("cosmetic-surface",),
            observations=(_observation("cosmetic-surface"),),
        ),
        _roster(),
        _profile(),
    )

    assert undeclared.disposition == DISPOSITION_GATE
    assert "standard-gap-requires-risk-acceptance:standard-surface" in undeclared.reasons
    assert "lane-undeclared" in undeclared.surfaces[0].gaps
    assert unknown.disposition == DISPOSITION_BLOCK
    assert "lane-unknown:hotfix" in unknown.reasons
    assert cosmetic.allowed is True


def test_the_correction_lane_carries_both_controls_and_a_reproduction() -> None:
    complete = decide_promotion(
        _request(lane=LANE_CORRECTION, correction=_correction()),
        _roster(),
        _profile(),
    )
    without_record = decide_promotion(
        _request(lane=LANE_CORRECTION, correction=None),
        _roster(),
        _profile(),
    )
    without_reproduction = decide_promotion(
        _request(
            lane=LANE_CORRECTION,
            correction=CorrectionRecord(
                defect_id="defect-1",
                baseline_available=True,
                controls=_correction().controls,
            ),
        ),
        _roster(),
        _profile(),
    )

    assert complete.allowed is True, complete.reasons
    assert complete.correction is not None and complete.correction.satisfied is True
    assert "correction-gap:correction-record-missing" in without_record.surfaces[0].gaps
    assert "correction-gap:reproduction-missing" in without_reproduction.surfaces[0].gaps


def test_a_red_guard_gates_the_correction_promotion_for_a_human() -> None:
    red_guard = ControlObservation(
        test_id="guards-unrelated-behavior",
        declared_role=CONTROL_GREEN_NOW,
        baseline_result=BASELINE_RESULT_FAILED,
        failure_relation=FAILURE_RELATION_UNRELATED,
    )
    forcing = ControlObservation(
        test_id="forces-the-defect",
        declared_role=CONTROL_RED_NOW,
        baseline_result=BASELINE_RESULT_FAILED,
        failure_relation=FAILURE_RELATION_DEFECT,
    )

    decision = decide_promotion(
        _request(
            lane=LANE_CORRECTION,
            correction=_correction(controls=(forcing, red_guard)),
        ),
        _roster(),
        _profile(),
    )

    assert decision.allowed is False
    assert decision.disposition == DISPOSITION_GATE
    assert (
        "correction-review:suspected-over-constraint:guards-unrelated-behavior"
        in decision.reasons
    )


def test_a_repair_whose_reproduction_was_recorded_after_the_fact_blocks() -> None:
    late = replace(_reproduction(), recorded_before_repair=False)
    late = replace(late, evidence=_evidence(late.authority_body()))

    decision = decide_promotion(
        _request(lane=LANE_CORRECTION, correction=_correction(reproduction=late)),
        _roster(),
        _profile(),
    )

    assert decision.disposition == DISPOSITION_BLOCK
    assert "correction-failure:reproduction-not-recorded-before-repair" in decision.reasons


def test_a_correction_record_outside_the_correction_lane_is_reported() -> None:
    decision = decide_promotion(
        _request(lane=LANE_CAPABILITY, correction=_correction()),
        _roster(),
        _profile(),
    )

    assert decision.allowed is True
    assert "correction-record-outside-correction-lane" in decision.reports
    assert decision.correction is None


def test_an_unresolved_monitor_backreference_blocks_even_a_cosmetic_surface() -> None:
    provenance = _good_provenance()
    fabricated = _monitor(
        "cosmetic-surface",
        provenance=provenance,
        backreference=replace(_monitor_backreference(provenance), item_id="absent"),
    )

    decision = decide_promotion(
        _request(
            provenance=provenance,
            disturbed_surface_ids=("cosmetic-surface",),
            observations=(_observation("cosmetic-surface"),),
            monitors=(fabricated,),
        ),
        _roster(),
        _profile(),
    )

    assert decision.disposition == DISPOSITION_BLOCK
    assert (
        "monitor-integrity:monitor-backreference-unresolved:monitor-cosmetic-surface"
        in decision.reasons
    )


def test_a_diff_derived_monitor_blocks_and_an_uncovered_critical_surface_has_no_waiver() -> None:
    profile = _profile()
    diff_monitor = _monitor("standard-surface", derivation=MONITOR_DERIVATION_IMPLEMENTATION)
    diff_derived = decide_promotion(
        _request(monitors=(diff_monitor,)),
        _roster(),
        profile,
    )
    generated_on_critical = decide_promotion(
        _critical_request(
            profile=profile,
            monitors=(_monitor("critical-surface", authorship=MONITOR_AUTHORSHIP_GENERATED),),
        ),
        _roster(),
        profile,
    )
    uncovered = decide_promotion(
        _critical_request(profile=profile, monitors=()),
        _roster(),
        profile,
    )

    assert "monitor-integrity:monitor-diff-derived:monitor-standard-surface" in diff_derived.reasons
    assert generated_on_critical.disposition == DISPOSITION_BLOCK
    assert (
        "critical-gap:critical-surface:critical-monitor-not-human-authored:monitor-critical-surface"
        in generated_on_critical.reasons
    )
    assert "critical-gap:critical-surface:monitor-coverage-missing" in uncovered.reasons


def test_monitor_density_is_recorded_without_a_threshold() -> None:
    sparse = decide_promotion(
        _request(monitor_declared_unit_count=10_000),
        _roster(),
        _profile(),
    )

    assert sparse.allowed is True
    assert sparse.monitors is not None and sparse.monitors.density == 1 / 10_000
    assert "monitor-density-recorded:1/10000" in sparse.reports


def test_an_overclaimed_independence_tier_blocks_every_class() -> None:
    decision = decide_promotion(
        _request(
            disturbed_surface_ids=("cosmetic-surface",),
            observations=(_observation("cosmetic-surface"),),
            independence=_independence(
                coder_family="family-a",
                tester_family="family-a",
                claimed_tier=INDEPENDENCE_STRONGER,
            ),
        ),
        _roster(),
        _profile(),
    )

    assert decision.disposition == DISPOSITION_BLOCK
    assert (
        f"independence-integrity:independence-tier-overclaimed:"
        f"{INDEPENDENCE_STRONGER}:{INDEPENDENCE_MODERATE}" in decision.reasons
    )


def test_an_unrecorded_independence_arrangement_is_a_gap_and_an_open_channel_blocks() -> None:
    unrecorded = decide_promotion(_request(independence=None), _roster(), _profile())
    channel = decide_promotion(
        _request(
            independence=_independence(channel_open=True, claimed_tier=INDEPENDENCE_WEAKEST),
        ),
        _roster(),
        _profile(),
    )
    mechanical = decide_promotion(
        _request(
            independence=_independence(
                mechanism_ids=("schema-validator",),
                claimed_tier=INDEPENDENCE_STRONGEST,
            ),
        ),
        _roster(),
        _profile(),
    )

    assert "independence-record-missing" in unrecorded.surfaces[0].gaps
    assert "independence-failure:independence-coder-tester-channel-open" in channel.reasons
    assert mechanical.allowed is True
    assert f"independence-tier-derived:{INDEPENDENCE_STRONGEST}" in mechanical.reports


def test_the_verdict_records_the_derived_tier_and_every_agent_version() -> None:
    incomplete = decide_promotion(
        _request(
            independence=replace(
                _independence(),
                agents=(
                    AgentIdentity(role=ROLE_CODER, model_family="family-a"),
                    AgentIdentity(
                        role=ROLE_TESTER,
                        model_family="family-b",
                        model_version="2026-07",
                        directive_version="tester-directive-3",
                    ),
                    AgentIdentity(
                        role=ROLE_VALIDATOR,
                        model_family="family-c",
                        model_version="2026-07",
                        directive_version="validator-directive-3",
                    ),
                ),
            )
        ),
        _roster(),
        _profile(),
    )

    assert "independence-model-version-unrecorded:coder" in incomplete.surfaces[0].gaps
    assert "independence-directive-version-unrecorded:coder" in incomplete.surfaces[0].gaps
    assert incomplete.independence is not None
    assert incomplete.independence.derived_tier == INDEPENDENCE_STRONGER


def test_critical_promotion_requires_a_declared_delegate_roster() -> None:
    undeclared = _profile(delegates=frozenset())
    unenrolled = _profile(delegates=frozenset({"triage-bot"}))
    narrow = _profile(delegates=frozenset({"alice"}))

    without_roster = decide_promotion(
        _critical_request(profile=undeclared),
        _roster(),
        undeclared,
    )
    agent_delegate = decide_promotion(
        _critical_request(profile=unenrolled),
        _roster(),
        unenrolled,
    )
    outside_roster = decide_promotion(
        _critical_request(profile=narrow),
        _roster(),
        narrow,
    )

    assert (
        "critical-gap:critical-surface:critical-ratification-delegates-undeclared"
        in without_roster.reasons
    )
    assert "critical-delegate-not-enrolled-human:triage-bot" in agent_delegate.reasons
    # Bob approved but is not on the roster: the seat is filled from the decided list.
    assert "approver-outside-delegate-roster:bob" in outside_roster.reasons


def test_a_standard_change_needs_no_delegate_roster() -> None:
    undeclared = _profile(delegates=frozenset())

    decision = decide_promotion(_request(profile=undeclared), _roster(), undeclared)

    assert decision.allowed is True, decision.reasons


def test_the_attestation_binds_the_lane_monitors_independence_and_correction() -> None:
    profile = _profile()
    signed = _request(lane=LANE_CORRECTION, correction=_correction(), profile=profile)

    swapped_monitors = decide_promotion(replace(signed, monitors=()), _roster(), profile)
    swapped_lane = decide_promotion(replace(signed, lane=LANE_CAPABILITY), _roster(), profile)
    swapped_independence = decide_promotion(
        replace(signed, independence=_independence(claimed_tier=INDEPENDENCE_MODERATE)),
        _roster(),
        profile,
    )
    swapped_correction = decide_promotion(replace(signed, correction=None), _roster(), profile)

    for decision in (
        swapped_monitors,
        swapped_lane,
        swapped_independence,
        swapped_correction,
    ):
        assert "attestation-subject-mismatch" in decision.reasons
        assert decision.disposition == DISPOSITION_BLOCK


def _runs(text: str) -> set[str]:
    return {run for run in re.split(r"[^a-z0-9]+", text.lower()) if run}


def test_module_names_nothing_target_specific() -> None:
    runs = _runs(MODULE_PATH.read_text(encoding="utf-8"))
    assert not [token for token in DENYLIST_TOKENS if token in runs]
