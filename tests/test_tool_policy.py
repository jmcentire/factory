"""Signed tool-policy tier, scope, authorization, and denial-probe tests."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from factory_core.evidence import EvidenceIntegrity
from factory_core.manifest import SegregationPolicy, digest_obj
from factory_core.provenance import (
    PHASE_ARCHITECTURE,
    PHASE_OPERATIONAL_MATURITY,
    PHASE_PRODUCT_SPECIFICATION,
    IntentItem,
    PhaseArtifact,
    ProvenanceBundle,
)
from factory_core.tool_policy import (
    AUTHORIZATION_USE,
    TOOL_TIER_ALLOWED,
    TOOL_TIER_SIGNOFF_REQUIRED,
    TOOL_TIER_VERBOTEN,
    DenialProbe,
    ToolAuthorization,
    ToolPolicy,
    ToolPolicyBundle,
    ToolRule,
    authorize_tool_invocation,
    tool_policy_issue_is_gap,
    verify_tool_policy,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "factory_core" / "tool_policy.py"
DENYLIST_TOKENS = tuple(
    json.loads((REPO_ROOT / "core_purity_denylist.json").read_text(encoding="utf-8")).get(
        "tokens", []
    )
)


def _evidence(body: dict[str, Any]) -> EvidenceIntegrity:
    return EvidenceIntegrity(body=body, claimed_digest=digest_obj(body))


def _roster() -> SegregationPolicy:
    return SegregationPolicy(
        human_ids=frozenset({"alice", "bob", "carol"}),
        human_aliases={"alice": "alice", "bob": "bob", "carol": "carol"},
        excluded_service_identities=frozenset({"agent-*", "*-bot"}),
    )


def _provenance() -> ProvenanceBundle:
    rows = (
        (
            "product-v1",
            PHASE_PRODUCT_SPECIFICATION,
            "behavior",
            "The observable behavior is delivered.",
        ),
        (
            "architecture-v1",
            PHASE_ARCHITECTURE,
            "tools",
            "The run uses only the declared scoped tool boundary.",
        ),
        (
            "operations-v1",
            PHASE_OPERATIONAL_MATURITY,
            "probe",
            "Forbidden capabilities are probed and must refuse.",
        ),
    )
    artifacts = tuple(
        PhaseArtifact(
            artifact_id=artifact_id,
            phase=phase,
            version="1",
            source_digest=digest_obj({"source": artifact_id}),
            human_ratifier="alice",
            validator_ratifier="validator",
            items=(IntentItem(item_id=item_id, canonical_statement=statement),),
        )
        for artifact_id, phase, item_id, statement in rows
    )
    return ProvenanceBundle(
        artifacts=artifacts,
        claims=(),
        trusted_artifact_digests={
            artifact.artifact_id: artifact.content_digest for artifact in artifacts
        },
    )


def _policy(provenance: ProvenanceBundle | None = None) -> ToolPolicy:
    authority = provenance or _provenance()
    architecture = authority.artifacts[1]
    operations = authority.artifacts[2]
    return ToolPolicy(
        policy_id="run-policy",
        version="1",
        run_id="run-1",
        issued_at=10,
        expires_at=100,
        signed_by="alice",
        independently_approved_by="bob",
        inventory_tool_ids=frozenset({"repo", "release", "production-mutation"}),
        rules=(
            ToolRule(
                tool_id="repo",
                tier=TOOL_TIER_ALLOWED,
                scope_ids=frozenset({"read"}),
                backreference=architecture.backreference(architecture.items[0]),
            ),
            ToolRule(
                tool_id="release",
                tier=TOOL_TIER_SIGNOFF_REQUIRED,
                scope_ids=frozenset({"staging"}),
                backreference=architecture.backreference(architecture.items[0]),
            ),
            ToolRule(
                tool_id="production-mutation",
                tier=TOOL_TIER_VERBOTEN,
                backreference=operations.backreference(operations.items[0]),
            ),
        ),
    )


def _authorization(policy: ToolPolicy) -> ToolAuthorization:
    unsigned = ToolAuthorization(
        authorization_id="auth-1",
        tool_id="release",
        principal="agent-builder",
        scope_ids=frozenset({"staging"}),
        mode=AUTHORIZATION_USE,
        issued_at=20,
        expires_at=80,
        authorized_by="carol",
        policy_digest=policy.content_digest,
        run_id=policy.run_id,
        invocation_id="invoke-1",
    )
    return replace(unsigned, evidence=_evidence(unsigned.authority_body()))


def _probe(policy: ToolPolicy) -> DenialProbe:
    unsigned = DenialProbe(
        probe_id="deny-1",
        tool_id="production-mutation",
        scope_id="write",
        attempted_at=15,
        refused=True,
        policy_digest=policy.content_digest,
        run_id=policy.run_id,
    )
    return replace(unsigned, evidence=_evidence(unsigned.authority_body()))


def _bundle(
    *,
    provenance: ProvenanceBundle | None = None,
    policy: ToolPolicy | None = None,
    with_authorization: bool = True,
    with_probe: bool = True,
) -> ToolPolicyBundle:
    authority = provenance or _provenance()
    selected_policy = policy or _policy(authority)
    return ToolPolicyBundle(
        policy=selected_policy,
        trusted_policy_digest=selected_policy.content_digest,
        provenance=authority,
        authorizations=(_authorization(selected_policy),) if with_authorization else (),
        denial_probes=(_probe(selected_policy),) if with_probe else (),
    )


def test_valid_policy_has_exact_tier_coverage_and_verified_denial_probe() -> None:
    bundle = _bundle()

    report = verify_tool_policy(bundle, _roster(), 50)

    assert report.satisfied is True, report.issues
    assert report.verified_tool_ids == ("production-mutation", "release", "repo")
    assert report.verified_authorization_ids == ("auth-1",)
    assert report.verified_denial_probe_ids == ("deny-1",)


def test_allowed_is_still_scope_bounded_and_unknown_is_default_verboten() -> None:
    bundle = _bundle()

    allowed = authorize_tool_invocation(
        bundle,
        _roster(),
        principal="agent-builder",
        tool_id="repo",
        scope_id="read",
        invocation_id="read-1",
        attempted_at=50,
    )
    overbroad = authorize_tool_invocation(
        bundle,
        _roster(),
        principal="agent-builder",
        tool_id="repo",
        scope_id="write",
        invocation_id="write-1",
        attempted_at=50,
    )
    unknown = authorize_tool_invocation(
        bundle,
        _roster(),
        principal="agent-builder",
        tool_id="undeclared",
        scope_id="anything",
        invocation_id="unknown-1",
        attempted_at=50,
    )

    assert allowed.allowed is True
    assert overbroad.allowed is False and overbroad.reason == "tool-scope-outside-policy"
    assert unknown.allowed is False and unknown.reason == "tool-unknown-default-verboten"


def test_signoff_is_fresh_scoped_human_authority_not_a_standing_memory() -> None:
    without = _bundle(with_authorization=False)
    with_authorization = _bundle()

    denied = authorize_tool_invocation(
        without,
        _roster(),
        principal="agent-builder",
        tool_id="release",
        scope_id="staging",
        invocation_id="invoke-1",
        attempted_at=50,
    )
    allowed = authorize_tool_invocation(
        with_authorization,
        _roster(),
        principal="agent-builder",
        tool_id="release",
        scope_id="staging",
        invocation_id="invoke-1",
        attempted_at=50,
    )
    wrong_use = authorize_tool_invocation(
        with_authorization,
        _roster(),
        principal="agent-builder",
        tool_id="release",
        scope_id="staging",
        invocation_id="invoke-2",
        attempted_at=50,
    )

    assert denied.allowed is False and denied.reason == "tool-signoff-required"
    assert allowed.allowed is True and allowed.authorization_id == "auth-1"
    assert wrong_use.allowed is False and wrong_use.reason == "tool-signoff-required"


def test_verboten_cannot_be_opened_by_supplying_an_authorization() -> None:
    bundle = _bundle()
    forbidden_auth = replace(
        _authorization(bundle.policy),
        authorization_id="auth-forbidden",
        tool_id="production-mutation",
    )
    forbidden_auth = replace(
        forbidden_auth,
        evidence=_evidence(forbidden_auth.authority_body()),
    )
    invalid_bundle = replace(
        bundle,
        authorizations=(*bundle.authorizations, forbidden_auth),
    )

    report = verify_tool_policy(invalid_bundle, _roster(), 50)
    decision = authorize_tool_invocation(
        invalid_bundle,
        _roster(),
        principal="agent-builder",
        tool_id="production-mutation",
        scope_id="write",
        invocation_id="forbidden-1",
        attempted_at=50,
    )

    assert report.satisfied is False
    assert any(
        issue.startswith("tool-authorization-tier-invalid:auth-forbidden")
        for issue in report.issues
    )
    assert decision.allowed is False


def test_unclassified_inventory_and_unproved_verboten_boundary_are_visible_gaps() -> None:
    bundle = _bundle(with_probe=False)
    policy = replace(
        bundle.policy,
        inventory_tool_ids=(*bundle.policy.inventory_tool_ids, "unclassified"),
    )
    incomplete = replace(
        bundle,
        policy=policy,
        trusted_policy_digest=policy.content_digest,
        authorizations=(_authorization(policy),),
    )

    report = verify_tool_policy(incomplete, _roster(), 50)

    assert "tool-unclassified:unclassified" in report.issues
    assert "verboten-denial-probe-missing:production-mutation" in report.issues
    assert tool_policy_issue_is_gap("tool-unclassified:unclassified")
    assert tool_policy_issue_is_gap("verboten-denial-probe-missing:production-mutation")


def test_product_intent_cannot_be_used_as_tool_policy_architecture_authority() -> None:
    provenance = _provenance()
    policy = _policy(provenance)
    product = provenance.artifacts[0]
    bad_rule = replace(
        policy.rules[0],
        backreference=product.backreference(product.items[0]),
    )
    invalid_policy = replace(policy, rules=(bad_rule, *policy.rules[1:]))
    bundle = _bundle(provenance=provenance, policy=invalid_policy)

    report = verify_tool_policy(bundle, _roster(), 50)

    assert "tool-policy-authority-phase-invalid:repo" in report.issues


def test_policy_digest_and_independent_approval_are_not_self_attested() -> None:
    bundle = _bundle()
    wrong_digest = replace(bundle, trusted_policy_digest=digest_obj({"different": True}))
    same_signer_policy = replace(
        bundle.policy,
        independently_approved_by=bundle.policy.signed_by,
    )
    same_signer = _bundle(policy=same_signer_policy)

    digest_report = verify_tool_policy(wrong_digest, _roster(), 50)
    signer_report = verify_tool_policy(same_signer, _roster(), 50)

    assert "tool-policy-digest-mismatch" in digest_report.issues
    assert "tool-policy-signer-equals-independent-approver" in signer_report.issues


def test_bundle_round_trip_preserves_the_signed_policy_and_records() -> None:
    bundle = _bundle()
    loaded = ToolPolicyBundle.from_dict(bundle.to_dict())

    assert loaded is not None
    assert loaded.to_dict() == bundle.to_dict()
    assert verify_tool_policy(loaded, _roster(), 50).satisfied is True


def _runs(text: str) -> set[str]:
    return {run for run in re.split(r"[^a-z0-9]+", text.lower()) if run}


def test_module_names_nothing_target_specific() -> None:
    runs = _runs(MODULE_PATH.read_text(encoding="utf-8"))
    assert not [token for token in DENYLIST_TOKENS if token in runs]
