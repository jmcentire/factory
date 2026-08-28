"""Forcing tests for the single-operator tool-policy anchor (ratified 2026-08-27).

The disposition exists for exactly one roster shape — one enrolled human — and it
substitutes an externally signed policy-digest binding for the independent human
approver, with a permanent disclosure. Every other combination keeps the
two-distinct-humans rule, fail-closed: these tests pin that the amendment cannot
be used to dodge a real second human, to self-approve, or to pass without the
anchor actually binding this exact policy.
"""

from __future__ import annotations

from dataclasses import replace

from factory_core.criticality import CRITICALITY_STANDARD
from factory_core.evidence import EvidenceIntegrity
from factory_core.manifest import SegregationPolicy, digest_obj
from factory_core.promotion import decide_promotion
from factory_core.tool_policy import (
    tool_policy_issue_is_gap,
    verify_tool_policy,
)
from tests.test_promotion_gate import (
    _control,
    _good_tool_policy,
    _monitor,
    _profile,
    _request,
    _roster,
)

EVALUATED_AT = 100


def _solo_roster() -> SegregationPolicy:
    return SegregationPolicy(
        human_ids=frozenset({"alice"}),
        human_aliases={"alice": "alice", "alice@example.com": "alice"},
        excluded_service_identities=frozenset({"*-bot", "factory-agent", "claude*"}),
    )


def _solo_bundle(*, anchored: bool = True, anchor_digest: str = "", approver: str = ""):
    bundle = _good_tool_policy()
    policy = replace(bundle.policy, independently_approved_by=approver)
    probes = []
    for probe in bundle.denial_probes:
        rebound = replace(probe, policy_digest=policy.content_digest, evidence=None)
        body = rebound.authority_body()
        probes.append(
            replace(rebound, evidence=EvidenceIntegrity(body=body, claimed_digest=digest_obj(body)))
        )
    anchor = None
    if anchored:
        body = {"policy_digest": anchor_digest or policy.content_digest}
        anchor = EvidenceIntegrity(body=body, claimed_digest=digest_obj(body))
    return replace(
        bundle,
        policy=policy,
        trusted_policy_digest=policy.content_digest,
        denial_probes=tuple(probes),
        single_operator_anchor=anchor,
    )


def test_solo_roster_with_binding_anchor_satisfies_and_discloses() -> None:
    report = verify_tool_policy(_solo_bundle(), _solo_roster(), EVALUATED_AT)
    assert report.satisfied is True
    assert not any(issue.startswith("tool-policy-signer") for issue in report.issues)
    assert any(
        r.startswith("tool-policy-single-operator-anchored:alice") for r in report.reports
    )


def test_solo_roster_without_anchor_is_invalid_not_a_gap() -> None:
    report = verify_tool_policy(_solo_bundle(anchored=False), _solo_roster(), EVALUATED_AT)
    assert "tool-policy-single-operator-anchor-missing" in report.issues
    assert tool_policy_issue_is_gap("tool-policy-single-operator-anchor-missing") is False


def test_anchor_must_bind_this_exact_policy_digest() -> None:
    report = verify_tool_policy(
        _solo_bundle(anchor_digest=digest_obj({"policy": "some-other"})),
        _solo_roster(),
        EVALUATED_AT,
    )
    assert "tool-policy-single-operator-anchor-invalid" in report.issues


def test_sole_human_self_approval_is_still_a_lie() -> None:
    report = verify_tool_policy(
        _solo_bundle(anchored=False, approver="alice"), _solo_roster(), EVALUATED_AT
    )
    assert "tool-policy-signer-equals-independent-approver" in report.issues


def test_anchor_cannot_dodge_a_real_second_human() -> None:
    report = verify_tool_policy(_solo_bundle(approver=""), _roster(), EVALUATED_AT)
    assert "tool-policy-anchor-with-multiple-humans" in report.issues


def test_anchor_beside_a_named_approver_is_ambiguous_authority() -> None:
    report = verify_tool_policy(_solo_bundle(approver="bob"), _solo_roster(), EVALUATED_AT)
    assert "tool-policy-anchor-and-approver-both-present" in report.issues


def test_multi_human_behavior_is_unchanged() -> None:
    report = verify_tool_policy(_good_tool_policy(), _roster(), EVALUATED_AT)
    assert report.satisfied is True
    assert report.reports == ()


def test_promotion_over_anchored_solo_policy_carries_the_disclosure() -> None:
    profile = _profile(
        surfaces=(
            _control("standard-surface", CRITICALITY_STANDARD, decided_by="alice"),
        ),
        delegates=frozenset({"alice"}),
    )
    request = _request(
        profile=profile,
        tool_policy=_solo_bundle(),
        monitors=(_monitor("standard-surface", author="alice"),),
    )
    decision = decide_promotion(request, _solo_roster(), profile)
    assert decision.allowed is True
    assert any(
        r.startswith("tool-policy-single-operator-anchored:alice") for r in decision.reports
    )
