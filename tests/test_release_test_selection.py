"""Release test selection cannot grant tool access or hide failed evidence."""

from dataclasses import replace

import pytest

from factory_core.promotion import decide_promotion
from factory_core.tool_policy import authorize_tool_invocation
from tests.test_promotion_gate import (
    _critical_request,
    _good_tool_policy,
    _independence,
    _isolated_structural_mode,
    _observation,
    _profile,
    _roster,
)


def _optional_profile():
    profile = _profile()
    return replace(profile, surfaces=tuple(
        replace(surface, required_evidence_ids=frozenset()) for surface in profile.surfaces
    ))


def test_optional_campaigns_do_not_require_new_receipts_or_claim_a_pass():
    profile = _optional_profile()
    request = _critical_request(
        profile=profile,
        observations=(_observation("critical-surface", adequate=False,
                                  oracle_receipt="", live_result="missing"),),
        monitors=(),
        independence=_independence(structural=replace(
            _isolated_structural_mode(), mutation_evidence=None)),
        tool_policy=replace(_good_tool_policy(), denial_probes=()),
    )
    result = decide_promotion(request, _roster(), profile)
    assert result.allowed, result.reasons
    assert len(result.surfaces[0].reports) >= 4
    assert result.surfaces[0].live_result == "missing"
    assert any("verboten-denial-probe-missing" in item for item in result.reports)
    assert result.tool_policy_issues == ("verboten-denial-probe-missing:production-mutation",)


def test_explicit_required_campaign_remains_a_release_block():
    profile = _profile()
    result = decide_promotion(_critical_request(
        profile=profile, monitors=(),
        observations=(_observation("critical-surface", adequate=False,
                                  oracle_receipt="", live_result="missing"),),
    ), _roster(), profile)
    assert not result.allowed
    for gap in ("oracle-silent", "live-verification-missing", "monitor-coverage-missing"):
        assert f"critical-gap:critical-surface:{gap}" in result.reasons


@pytest.mark.parametrize("change,reason", [
    ({"live_result": "failed"}, "negative-evidence:critical-surface:live-verification-failed"),
    ({"oracle_receipt": ""}, "oracle-receipt-required:critical-surface"),
    ({"flake_count": 1}, "negative-evidence:critical-surface:critical-test-flaked:1"),
])
def test_optional_campaigns_never_hide_failures_or_unbacked_positive_claims(change, reason):
    profile = _optional_profile()
    result = decide_promotion(_critical_request(
        profile=profile, observations=(_observation("critical-surface", **change),),
    ), _roster(), profile)
    assert not result.allowed
    assert reason in result.reasons


def test_release_selection_does_not_change_live_tool_admission():
    complete = _good_tool_policy()
    missing = replace(complete, denial_probes=())
    for bundle, tool, scope, allowed in (
        (complete, "workspace", "read-write", True),
        (missing, "workspace", "read-write", False),
        (complete, "production-mutation", "write", False),
        (complete, "unknown", "write", False),
        (complete, "workspace", "other-scope", False),
    ):
        decision = authorize_tool_invocation(
            bundle, _roster(), principal="claude-opus", tool_id=tool,
            scope_id=scope, invocation_id="fixture-only", attempted_at=100,
        )
        assert decision.allowed is allowed
