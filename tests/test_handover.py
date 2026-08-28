"""Forcing tests for typed handovers and the composed ``__DONE__``.

The load-bearing test is the second run-1 pin: N handovers, each locally true and
each silently omitting the same verb, must fail composition with the missing verb
named — the composition-of-true-statements-produces-false-global defect, caught
mechanically instead of by a Validator reading prose.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from factory_core.evidence import EvidenceIntegrity
from factory_core.handover import (
    DONE_TOKEN,
    DoneComposition,
    Handover,
    HandoverError,
    HandoverScope,
    compose_done,
    done_attestation_subject,
    reserved_token_violation,
)
from factory_core.manifest import digest_obj
from factory_core.promotion import decide_promotion
from factory_core.verdict import (
    FIRST_LINE_YES,
    VERDICT_PASS,
    compute_verdict,
)
from tests.test_promotion_gate import (
    CANDIDATE,
    _observation,
    _profile,
    _rebind,
    _request,
    _risk,
    _roster,
)
from tests.test_verdict import EVALUATED_POSITION, _frame_check, _map

VERBS = ("ingest-observation", "detect-drift", "surface-finding")


def _pass_verdict(coverage):
    gap = _request(observations=(_observation("standard-surface", adequate=False),))
    accepted = _rebind(replace(gap, risk_acceptance=_risk(("standard-surface",))))
    decision = decide_promotion(accepted, _roster(), _profile())
    verdict = compute_verdict(
        coverage,
        decision,
        _frame_check(FIRST_LINE_YES),
        candidate_digest=CANDIDATE,
        evaluated_position=EVALUATED_POSITION,
    )
    assert verdict.disposition == VERDICT_PASS
    return verdict


def _verb_map():
    coverage = _map(status="covered")
    return replace(coverage, verb_ids=VERBS)


def _handover(
    handover_id: str,
    *,
    seat: str = "coder",
    completed: tuple[str, ...] = (),
    excluded: tuple[str, ...] = (),
    assumed: tuple[str, ...] = (),
    position: int = 100,
    retracts: bool = False,
    forcing_event_digest: str = "",
    tampered: bool = False,
) -> Handover:
    unsigned = Handover(
        handover_id=handover_id,
        from_seat=seat,
        claim="complete against the signed dispatch items",
        scope=HandoverScope(
            completed=completed,
            explicitly_excluded=excluded,
            assumed_in_scope_by_others=assumed,
        )
        if not retracts
        else HandoverScope(completed=(), explicitly_excluded=(), assumed_in_scope_by_others=()),
        ledger_position=position,
        retracts=retracts,
        forcing_event_digest=forcing_event_digest,
    )
    body = unsigned.authority_body()
    if tampered:
        body = {**body, "claim": "a different claim than the one signed"}
    return replace(unsigned, evidence=EvidenceIntegrity(body=body, claimed_digest=digest_obj(body)))


def test_run1_shared_silent_omission_cannot_compose_done() -> None:
    """The run-1 pin: every seat's claim is locally true; the union is still short.

    Coder and Tester each complete their assignment and each silently omit the same
    verb (surface-finding — the value chain nobody owned). Composition names the
    missing verb instead of aggregating the confident locals into a global claim.
    """

    coverage = _verb_map()
    verdict = _pass_verdict(coverage)
    composition = compose_done(
        coverage,
        (
            _handover("coder-1", seat="coder", completed=("ingest-observation",)),
            _handover("tester-1", seat="tester", completed=("detect-drift",)),
        ),
        verdict,
        validator="validator-agent",
    )
    assert composition.reachable is False
    assert composition.token == ""
    assert "verb-uncovered-by-handover:surface-finding" in composition.reasons
    assert composition.missing_verbs == ("surface-finding",)


def test_full_scope_union_over_pass_verdict_mints_the_singleton() -> None:
    coverage = _verb_map()
    verdict = _pass_verdict(coverage)
    composition = compose_done(
        coverage,
        (
            _handover("coder-1", seat="coder", completed=("ingest-observation", "detect-drift")),
            _handover("tester-1", seat="tester", completed=("surface-finding",)),
        ),
        verdict,
        validator="validator-agent",
    )
    assert composition.reachable is True
    assert composition.token == DONE_TOKEN
    assert composition.covered_verbs == tuple(sorted(VERBS))

    subject = done_attestation_subject(composition, coverage, verdict)
    assert subject["coverage_digest"] == coverage.content_digest
    assert subject["verdict_digest"] == digest_obj(verdict.to_dict())


def test_done_is_unreachable_without_pass_verdict() -> None:
    coverage = _verb_map()
    verdict = _pass_verdict(coverage)
    not_pass = replace(verdict, disposition="incomplete", allowed=False)
    composition = compose_done(
        coverage,
        (_handover("coder-1", completed=VERBS),),
        not_pass,
        validator="validator-agent",
    )
    assert composition.reachable is False
    assert "verdict-not-pass:incomplete" in composition.reasons


def test_retraction_reopens_the_verb_with_the_forcing_event_named() -> None:
    coverage = _verb_map()
    verdict = _pass_verdict(coverage)
    forcing = digest_obj({"forcing-event": "schema change invalidated the lane"})
    composition = compose_done(
        coverage,
        (
            _handover("coder-1", completed=VERBS, position=100),
            _handover(
                "coder-1",
                position=200,
                retracts=True,
                forcing_event_digest=forcing,
            ),
        ),
        verdict,
        validator="validator-agent",
    )
    assert composition.reachable is False
    assert f"handover-retracted:coder-1:{forcing}" in composition.reports
    assert "verb-uncovered-by-handover:detect-drift" in composition.reasons


def test_assumed_in_scope_by_others_must_be_claimed_by_someone() -> None:
    coverage = _verb_map()
    verdict = _pass_verdict(coverage)
    composition = compose_done(
        coverage,
        (
            _handover(
                "coder-1",
                completed=VERBS,
                assumed=("drain-worker-wiring",),
            ),
        ),
        verdict,
        validator="validator-agent",
    )
    assert composition.reachable is False
    assert "assumed-but-unclaimed:coder-1:drain-worker-wiring" in composition.reasons


def test_tampered_or_missing_handover_evidence_blocks_composition() -> None:
    coverage = _verb_map()
    verdict = _pass_verdict(coverage)
    tampered = compose_done(
        coverage,
        (_handover("coder-1", completed=VERBS, tampered=True),),
        verdict,
        validator="validator-agent",
    )
    assert tampered.reachable is False
    assert "handover-evidence-invalid:coder-1" in tampered.reasons

    unsigned = replace(_handover("coder-1", completed=VERBS), evidence=None)
    missing = compose_done(coverage, (unsigned,), verdict, validator="validator-agent")
    assert missing.reachable is False
    assert "handover-evidence-missing:coder-1" in missing.reasons


def test_empty_verb_set_cannot_read_as_nothing_left() -> None:
    coverage = replace(_map(status="covered"), verb_ids=())
    verdict = _pass_verdict(coverage)
    composition = compose_done(
        coverage,
        (_handover("coder-1", completed=("anything",)),),
        verdict,
        validator="validator-agent",
    )
    assert composition.reachable is False
    assert "coverage-map-has-no-ratified-verbs" in composition.reasons


def test_verdict_coverage_mismatch_blocks_composition() -> None:
    coverage = _verb_map()
    verdict = _pass_verdict(coverage)
    other_map = replace(coverage, ratified_position=coverage.ratified_position + 1)
    composition = compose_done(
        other_map,
        (_handover("coder-1", completed=VERBS),),
        verdict,
        validator="validator-agent",
    )
    assert composition.reachable is False
    assert "verdict-coverage-mismatch" in composition.reasons


def test_reserved_token_is_detected_anywhere_else() -> None:
    violation = reserved_token_violation(
        "lane output: implementation complete. __DONE__", source="coder-lane"
    )
    assert violation == "reserved-token-emission:coder-lane"
    assert reserved_token_violation("__HANDOVER__ with typed payload") is None


def test_malformed_handovers_are_refused_not_adjudicated() -> None:
    with pytest.raises(HandoverError):
        HandoverScope(completed=("a",), explicitly_excluded=("a",))
    with pytest.raises(HandoverError):
        Handover(
            handover_id="h",
            from_seat="coder",
            claim="",
            scope=HandoverScope(completed=()),
            ledger_position=1,
        )
    with pytest.raises(HandoverError):
        Handover(
            handover_id="h",
            from_seat="coder",
            claim="",
            scope=HandoverScope(completed=()),
            ledger_position=1,
            retracts=True,
        )
    with pytest.raises(HandoverError):
        Handover.from_dict(
            {"handover_id": "h", "from_seat": "coder", "scope": {"completed": ["x"]},
             "ledger_position": "soon"}
        )


def test_composition_result_is_typed_and_inspectable() -> None:
    coverage = _verb_map()
    verdict = _pass_verdict(coverage)
    composition = compose_done(
        coverage,
        (_handover("coder-1", completed=VERBS),),
        verdict,
        validator="validator-agent",
    )
    assert isinstance(composition, DoneComposition)
    round_trip = composition.to_dict()
    assert round_trip["token"] == DONE_TOKEN
    assert round_trip["coverage_digest"] == coverage.content_digest
