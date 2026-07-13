"""Promotion-gate tests — the fail-closed, default-deny merge/promotion decision.

These prove the extraction is correct AND target-agnostic:

  * default-deny: an empty request denies with a falsifiable reason set;
  * evidence integrity: a content-addressed artifact must be present and verify (constant-time);
    a tampered digest denies;
  * gate quorum: every profile-required gate must be present AND passed (missing vs failed);
  * segregation of duties: verifier != implementer; each approver resolves to an enrolled human,
    DENY-wins (an agent can never approve, even if also enrolled), distinct from impl/verifier;
  * the consequence floor: a consequential change (data-declared tier OR category) needs >= 2
    DISTINCT enrolled humans; a target may raise the floor but never lower it; a human with two
    aliases counts once; a non-consequential change still needs >= 1 human;
  * token check: the module names nothing target-specific.

Every tier/category/gate-id here is ABSTRACT test data (low/high/critical, financial, tests,
build) fed to the neutral engine — no target vocabulary. Hermetic, stdlib only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from factory_core.manifest import SegregationPolicy, digest_obj
from factory_core.promotion import (
    BASELINE_APPROVER_FLOOR,
    CONSEQUENTIAL_APPROVER_FLOOR,
    ConsequenceProfile,
    EvidenceIntegrity,
    GateOutcome,
    PromotionRequest,
    decide_promotion,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "factory_core" / "promotion.py"

DENYLIST_TOKENS = tuple(
    json.loads((REPO_ROOT / "core_purity_denylist.json").read_text(encoding="utf-8")).get(
        "tokens", []
    )
)


# --------------------------------------------------------------------------- #
# Shared fixtures (built in-code — no target present)
# --------------------------------------------------------------------------- #

def _roster() -> SegregationPolicy:
    """Two enrolled humans (each with aliases), plus an agent denylist."""
    return SegregationPolicy(
        human_ids=frozenset({"alice", "bob"}),
        human_aliases={
            "alice": "alice",
            "alice@example.com": "alice",
            "alice.smith@example.com": "alice",  # a second alias of the SAME human
            "bob": "bob",
            "bob@example.com": "bob",
        },
        excluded_service_identities=frozenset({"claude*", "*-bot", "factory-agent"}),
    )


def _profile(**kw: object) -> ConsequenceProfile:
    base: dict[str, object] = {
        "consequential_tiers": frozenset({"high", "critical"}),
        "consequential_categories": frozenset({"financial", "irreversible"}),
        "required_gate_ids": frozenset({"tests", "build"}),
        "baseline_min_approvers": 1,
        "consequential_min_approvers": 2,
    }
    base.update(kw)
    return ConsequenceProfile(**base)  # type: ignore[arg-type]


def _good_evidence() -> EvidenceIntegrity:
    body = {"spec_digest": "sha256:deadbeef", "artifact": "module-x", "checkout": "abc123"}
    return EvidenceIntegrity(body=body, claimed_digest=digest_obj(body))


def _passing_gates() -> tuple[GateOutcome, ...]:
    return (
        GateOutcome(id="tests", passed=True, detail="42 passed"),
        GateOutcome(id="build", passed=True, detail="clean"),
    )


# --------------------------------------------------------------------------- #
# Default-deny
# --------------------------------------------------------------------------- #

def test_empty_request_denies_default_deny() -> None:
    decision = decide_promotion(PromotionRequest(), _roster(), _profile())
    assert decision.allowed is False
    # nothing is affirmatively satisfied: no evidence, both gates missing, no approver.
    assert "evidence-missing" in decision.reasons
    assert "gate-missing:tests" in decision.reasons
    assert "gate-missing:build" in decision.reasons
    assert "insufficient-approvers:0/1" in decision.reasons
    assert decision.consequential is False
    assert decision.required_approvers == 1
    assert decision.approver_count == 0


def test_happy_path_non_consequential_one_human_allows() -> None:
    request = PromotionRequest(
        tier="low",
        gates=_passing_gates(),
        implementer="claude-opus",  # an agent may implement
        verifier="ci-bot",  # an agent may verify (the gates are the verification)
        approvers=("alice@example.com",),  # one enrolled human accepts the risk
        evidence=_good_evidence(),
    )
    decision = decide_promotion(request, _roster(), _profile())
    assert decision.allowed is True, decision.reasons
    assert decision.reasons == ()
    assert decision.consequential is False
    assert decision.required_approvers == 1
    assert decision.approver_count == 1


# --------------------------------------------------------------------------- #
# Segregation of duties — DENY-wins is the load-bearing property
# --------------------------------------------------------------------------- #

def test_agent_approver_denied_deny_wins_over_enrollment() -> None:
    # An identity that is BOTH on the human allowlist AND matches the agent denylist must NEVER
    # approve: the deny check runs first. This is DENY-wins, by construction.
    deny_wins = SegregationPolicy(
        human_ids=frozenset({"alice", "sneaky-bot"}),
        human_aliases={"alice": "alice", "sneaky-bot": "sneaky-bot"},
        excluded_service_identities=frozenset({"*-bot"}),
    )
    request = PromotionRequest(
        tier="low",
        gates=_passing_gates(),
        implementer="alice",
        verifier="ci-runner",
        approvers=("sneaky-bot",),
        evidence=_good_evidence(),
    )
    decision = decide_promotion(request, deny_wins, _profile())
    assert decision.allowed is False
    assert "approver-is-agent:sneaky-bot" in decision.reasons
    assert decision.approver_count == 0


def test_unenrolled_approver_denied() -> None:
    request = PromotionRequest(
        tier="low",
        gates=_passing_gates(),
        implementer="claude-opus",
        verifier="ci-bot",
        approvers=("mallory@example.com",),
        evidence=_good_evidence(),
    )
    decision = decide_promotion(request, _roster(), _profile())
    assert decision.allowed is False
    assert "approver-not-enrolled:mallory@example.com" in decision.reasons


def test_self_approval_denied_implementer_equals_approver() -> None:
    # alice implemented AND tries to approve via a different alias of the same human.
    request = PromotionRequest(
        tier="low",
        gates=_passing_gates(),
        implementer="alice@example.com",
        verifier="ci-bot",
        approvers=("alice.smith@example.com",),
        evidence=_good_evidence(),
    )
    decision = decide_promotion(request, _roster(), _profile())
    assert decision.allowed is False
    assert "implementer-equals-approver:alice.smith@example.com" in decision.reasons
    assert decision.approver_count == 0  # the self-approval is not counted


def test_verifier_equals_implementer_denied() -> None:
    request = PromotionRequest(
        tier="low",
        gates=_passing_gates(),
        implementer="claude-opus",
        verifier="claude-opus",  # same identity cannot self-verify
        approvers=("alice",),
        evidence=_good_evidence(),
    )
    decision = decide_promotion(request, _roster(), _profile())
    assert decision.allowed is False
    assert "verifier-equals-implementer" in decision.reasons


def test_empty_policy_denies_every_approver() -> None:
    # No enrolled humans at all -> nothing resolves -> deny (fail closed).
    request = PromotionRequest(
        tier="low",
        gates=_passing_gates(),
        implementer="claude-opus",
        verifier="ci-bot",
        approvers=("alice",),
        evidence=_good_evidence(),
    )
    decision = decide_promotion(request, SegregationPolicy(), _profile())
    assert decision.allowed is False
    assert "approver-not-enrolled:alice" in decision.reasons


# --------------------------------------------------------------------------- #
# The consequence-driven distinct-human floor
# --------------------------------------------------------------------------- #

def test_consequential_by_tier_requires_two_distinct_humans() -> None:
    one_human = PromotionRequest(
        tier="high",  # a consequential tier
        gates=_passing_gates(),
        implementer="claude-opus",
        verifier="ci-bot",
        approvers=("alice",),
        evidence=_good_evidence(),
    )
    decision = decide_promotion(one_human, _roster(), _profile())
    assert decision.consequential is True
    assert decision.required_approvers == 2
    assert decision.allowed is False
    assert "insufficient-approvers:1/2" in decision.reasons

    two_humans = PromotionRequest(
        tier="high",
        gates=_passing_gates(),
        implementer="claude-opus",
        verifier="ci-bot",
        approvers=("alice", "bob@example.com"),
        evidence=_good_evidence(),
    )
    ok = decide_promotion(two_humans, _roster(), _profile())
    assert ok.allowed is True, ok.reasons
    assert ok.consequential is True
    assert ok.approver_count == 2


def test_consequential_by_category_requires_two() -> None:
    request = PromotionRequest(
        tier="low",  # not a consequential tier
        categories=("financial",),  # but a consequential category
        gates=_passing_gates(),
        implementer="claude-opus",
        verifier="ci-bot",
        approvers=("alice",),
        evidence=_good_evidence(),
    )
    decision = decide_promotion(request, _roster(), _profile())
    assert decision.consequential is True
    assert decision.required_approvers == 2
    assert "insufficient-approvers:1/2" in decision.reasons


def test_two_aliases_of_one_human_count_once() -> None:
    request = PromotionRequest(
        tier="high",
        gates=_passing_gates(),
        implementer="claude-opus",
        verifier="ci-bot",
        approvers=("alice@example.com", "alice.smith@example.com"),  # SAME human, two aliases
        evidence=_good_evidence(),
    )
    decision = decide_promotion(request, _roster(), _profile())
    assert decision.approver_count == 1  # distinctness is by resolved human id
    assert decision.allowed is False
    assert "insufficient-approvers:1/2" in decision.reasons


def test_consequential_floor_cannot_be_lowered_by_profile() -> None:
    # A target that misconfigures the consequential minimum to 1 is still floored at 2.
    weak = _profile(consequential_min_approvers=1)
    assert weak.required_approvers("high", []) == CONSEQUENTIAL_APPROVER_FLOOR == 2
    # A target CAN raise the floor.
    strong = _profile(consequential_min_approvers=3)
    assert strong.required_approvers("critical", []) == 3


def test_baseline_floored_at_one_human() -> None:
    # A profile that requires zero baseline approvers is floored up to one enrolled human.
    zero = _profile(baseline_min_approvers=0)
    assert zero.required_approvers("low", []) == BASELINE_APPROVER_FLOOR == 1


# --------------------------------------------------------------------------- #
# Gate quorum + evidence integrity
# --------------------------------------------------------------------------- #

def test_gate_failed_vs_missing_are_distinguished() -> None:
    request = PromotionRequest(
        tier="low",
        gates=(GateOutcome(id="tests", passed=False, detail="1 failed"),),  # build absent
        implementer="claude-opus",
        verifier="ci-bot",
        approvers=("alice",),
        evidence=_good_evidence(),
    )
    decision = decide_promotion(request, _roster(), _profile())
    assert decision.allowed is False
    assert "gate-failed:tests" in decision.reasons
    assert "gate-missing:build" in decision.reasons


def test_evidence_tamper_denies() -> None:
    body = {"artifact": "module-x", "n": 1}
    wrong = digest_obj({"artifact": "module-x", "n": 2})
    tampered = EvidenceIntegrity(body=body, claimed_digest=wrong)
    assert tampered.verify() is False
    request = PromotionRequest(
        tier="low",
        gates=_passing_gates(),
        implementer="claude-opus",
        verifier="ci-bot",
        approvers=("alice",),
        evidence=tampered,
    )
    decision = decide_promotion(request, _roster(), _profile())
    assert decision.allowed is False
    assert "evidence-digest-mismatch" in decision.reasons


def test_evidence_absent_digest_denies() -> None:
    request = PromotionRequest(
        tier="low",
        gates=_passing_gates(),
        implementer="claude-opus",
        verifier="ci-bot",
        approvers=("alice",),
        evidence=EvidenceIntegrity(body={"x": 1}, claimed_digest=""),  # no claimed digest
    )
    decision = decide_promotion(request, _roster(), _profile())
    assert decision.allowed is False
    assert "evidence-missing" in decision.reasons


# --------------------------------------------------------------------------- #
# from_dict data ingestion + serialization
# --------------------------------------------------------------------------- #

def test_from_dict_round_trip_and_case_insensitive_labels() -> None:
    profile = ConsequenceProfile.from_dict(
        {
            "consequential_tiers": ["HIGH"],
            "consequential_categories": ["Financial"],
            "required_gate_ids": ["Tests", "Build"],
            "consequential_min_approvers": 2,
        }
    )
    request = PromotionRequest.from_dict(
        {
            "tier": "high",  # matches "HIGH" after normalization
            "gates": [
                {"id": "TESTS", "passed": True},  # matches "Tests" after normalization
                {"id": "build", "passed": True},
            ],
            "implementer": "claude-opus",
            "verifier": "ci-bot",
            "approvers": ["alice", "bob"],
            "evidence": {"body": {"a": 1}, "claimed_digest": digest_obj({"a": 1})},
        }
    )
    decision = decide_promotion(request, _roster(), profile)
    assert decision.allowed is True, decision.reasons
    assert decision.consequential is True
    assert decision.to_dict()["approver_count"] == 2


# --------------------------------------------------------------------------- #
# Purity: the module names nothing target-specific
# --------------------------------------------------------------------------- #

def _runs(text: str) -> set[str]:
    return {r for r in re.split(r"[^a-z0-9]+", text.lower()) if r}


def test_module_names_nothing_target_specific() -> None:
    runs = _runs(MODULE_PATH.read_text(encoding="utf-8"))
    hits = [tok for tok in DENYLIST_TOKENS if tok in runs]
    assert hits == [], f"promotion.py must name nothing target-specific; found {hits}"
