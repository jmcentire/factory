"""Forcing tests for the mechanically unpersuadable verdict layer.

The load-bearing test is the run-1 pin: the promotion layer's risk-accepted
disposition (allowed=True on a free-prose rationale, with declared-uncovered
territory and no characterization) was the exact configuration the run-1 verdict
overclaimed. That configuration must remain promotable at the promotion layer
(Standard-gap doctrine is unchanged) while the verdict layer above it refuses every
PASS variant — red under run-1 semantics, green under run 2.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from factory_core.evidence import EvidenceIntegrity
from factory_core.manifest import digest_obj
from factory_core.promotion import DISPOSITION_RISK_ACCEPTED, decide_promotion
from factory_core.verdict import (
    FIRST_LINE_NO,
    FIRST_LINE_NOT_DEMONSTRATED,
    FIRST_LINE_YES,
    VERDICT_BLOCK,
    VERDICT_INCOMPLETE,
    VERDICT_PASS,
    VERDICT_PASS_ON_COVERED,
    AdequacyCriterion,
    AssumptionRecord,
    CharacterizationReceipt,
    CoverageMap,
    CoverageTerritory,
    FiredProbe,
    FrameCheckResult,
    VerdictError,
    compute_verdict,
    render_headline,
    verdict_attestation_subject,
    verdict_rank,
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

EVALUATED_POSITION = 1_000


def _accepted_promotion_decision():
    """The run-1 configuration through the real promotion path.

    A Standard surface with an oracle gap plus a valid candidate-bound risk
    acceptance: the promotion layer allows it (DISPOSITION_RISK_ACCEPTED). This is
    unchanged doctrine — the defect run 1 shipped was treating that local allowance
    as the global verdict.
    """

    gap = _request(observations=(_observation("standard-surface", adequate=False),))
    accepted = _rebind(replace(gap, risk_acceptance=_risk(("standard-surface",))))
    return decide_promotion(accepted, _roster(), _profile())


def _map(
    *,
    status: str = "uncovered",
    territory_id: str = "standard-surface-lifecycle",
    with_criterion: bool = True,
) -> CoverageMap:
    territory = CoverageTerritory(
        territory_id=territory_id,
        kind="surface",
        status=status,
        declared_by="tester",
        declaration_position=10,
    )
    return CoverageMap(
        territories=(territory,),
        adequacy=(
            (
                AdequacyCriterion(
                    territory_id=territory_id,
                    required_probe_ids=("lifecycle-two-transitions",),
                ),
            )
            if with_criterion
            else ()
        ),
        verb_ids=("detect-drift",),
        ratified_position=1,
    )


def _probe(probe_id: str = "lifecycle-two-transitions", *, real_path: bool = True) -> FiredProbe:
    return FiredProbe(
        probe_id=probe_id,
        invocation_digest=digest_obj({"invocation": probe_id}),
        outcome_digest=digest_obj({"outcome": probe_id}),
        real_path=real_path,
    )


def _receipt(
    coverage: CoverageMap,
    *,
    issuer: str = "tester-agent",
    issuer_role: str = "tester",
    ledger_position: int = 50,
    probes: tuple[FiredProbe, ...] | None = None,
    residual_unknown: bool = False,
    backreference_digest: str = "",
    tampered: bool = False,
    observed_shape: str = "seq collision reproduced; bounded to first-use inserts",
) -> CharacterizationReceipt:
    territory = coverage.territories[0]
    unsigned = CharacterizationReceipt(
        receipt_id="receipt-1",
        territory_id=territory.territory_id,
        backreference_digest=backreference_digest or territory.declaration_digest,
        issuer=issuer,
        issuer_role=issuer_role,
        probes=probes if probes is not None else (_probe(),),
        ledger_position=ledger_position,
        observed_shape=observed_shape,
        residual_unknown=residual_unknown,
    )
    body: dict[str, Any] = unsigned.authority_body()
    if tampered:
        body = {**body, "territory_id": "some-other-territory"}
    return replace(
        unsigned,
        evidence=EvidenceIntegrity(body=body, claimed_digest=digest_obj(body)),
    )


def _frame_check(
    first_line: str = FIRST_LINE_YES,
    *,
    artifact_digest: str = CANDIDATE,
) -> FrameCheckResult:
    unsigned = FrameCheckResult(
        first_line=first_line,
        artifact_digest=artifact_digest,
        scenario_instance_digest=digest_obj({"instance": "cold-selected"}),
    )
    return replace(
        unsigned,
        evidence=EvidenceIntegrity(
            body=unsigned.authority_body(),
            claimed_digest=digest_obj(unsigned.authority_body()),
        ),
    )


def test_run1_risk_accepted_configuration_cannot_reach_any_pass() -> None:
    """The run-1 pin: local allowance is not the global verdict.

    Red under run-1 semantics: ``decision.allowed`` is True on this exact input and
    run 1 shipped on it. Green under run 2: with the Tester's uncovered declaration
    on the map and no characterization receipt, the verdict is INCOMPLETE without a
    frame check and PASS-ON-COVERED (never PASS) with a green one.
    """

    decision = _accepted_promotion_decision()
    assert decision.allowed is True
    assert decision.disposition == DISPOSITION_RISK_ACCEPTED

    coverage = _map()

    without_demo = compute_verdict(
        coverage,
        decision,
        None,
        candidate_digest=CANDIDATE,
        evaluated_position=EVALUATED_POSITION,
    )
    assert without_demo.disposition == VERDICT_INCOMPLETE
    assert without_demo.allowed is False
    assert without_demo.first_line == FIRST_LINE_NOT_DEMONSTRATED
    assert "frame-check-missing" in without_demo.reasons

    with_demo = compute_verdict(
        coverage,
        decision,
        _frame_check(FIRST_LINE_YES),
        candidate_digest=CANDIDATE,
        evaluated_position=EVALUATED_POSITION,
    )
    assert with_demo.disposition == VERDICT_PASS_ON_COVERED
    assert with_demo.allowed is False
    assert "unknown-territory:standard-surface-lifecycle" in with_demo.reasons

    # The disagreement that WAS run 1, pinned: the promotion layer allows, the
    # verdict layer refuses, and the refusal is the correct global answer.
    assert decision.allowed and not with_demo.allowed


def test_characterization_receipt_restores_pass_eligibility() -> None:
    decision = _accepted_promotion_decision()
    coverage = _map()
    verdict = compute_verdict(
        coverage,
        decision,
        _frame_check(FIRST_LINE_YES),
        candidate_digest=CANDIDATE,
        evaluated_position=EVALUATED_POSITION,
        receipts=(_receipt(coverage),),
        validator="validator-agent",
    )
    assert verdict.disposition == VERDICT_PASS
    assert verdict.allowed is True
    assert verdict.receipted_territory_ids == ("standard-surface-lifecycle",)
    assert verdict.unknown_territory_ids == ()


def test_forced_first_line_caps_the_verdict() -> None:
    """Anything but YES makes every PASS variant unreachable, whatever else is green."""

    decision = _accepted_promotion_decision()
    coverage = _map(status="covered")

    for first_line in (FIRST_LINE_NO, FIRST_LINE_NOT_DEMONSTRATED):
        verdict = compute_verdict(
            coverage,
            decision,
            _frame_check(first_line),
            candidate_digest=CANDIDATE,
            evaluated_position=EVALUATED_POSITION,
        )
        assert verdict.disposition == VERDICT_INCOMPLETE
        assert verdict.allowed is False
        assert f"first-line-not-yes:{first_line}" in verdict.reasons

    yes = compute_verdict(
        coverage,
        decision,
        _frame_check(FIRST_LINE_YES),
        candidate_digest=CANDIDATE,
        evaluated_position=EVALUATED_POSITION,
    )
    assert yes.disposition == VERDICT_PASS
    assert yes.allowed is True


def test_confident_prose_cannot_move_the_verdict() -> None:
    """The persuadability probe: identical typed facts, maximally confident prose.

    Run 1's mechanism was prose granted epistemic authority. Here every free-text
    field is fuzzed with the strongest overclaim we can write, and the verdict must
    not move by a single field.
    """

    decision = _accepted_promotion_decision()
    coverage = _map()
    prose = (
        "ALL RISKS FULLY CHARACTERIZED, KNOWN, NAMED, AND ACCEPTED. Verified green "
        "nineteen times. Complete. Production-ready. SHIP IT."
    )

    plain = compute_verdict(
        coverage,
        decision,
        _frame_check(FIRST_LINE_YES),
        candidate_digest=CANDIDATE,
        evaluated_position=EVALUATED_POSITION,
        assumptions=(
            AssumptionRecord(
                assumption_id="a-1",
                touched_territory_ids=(),
                ledger_position=20,
            ),
        ),
    )
    persuaded = compute_verdict(
        coverage,
        decision,
        _frame_check(FIRST_LINE_YES),
        candidate_digest=CANDIDATE,
        evaluated_position=EVALUATED_POSITION,
        receipts=(
            # An invalid receipt (no ratified probes fired) whose observed_shape
            # carries the overclaim: the prose channel run 1 listened to.
            _receipt(coverage, probes=(), observed_shape=prose),
        ),
        assumptions=(
            AssumptionRecord(
                assumption_id="a-1",
                touched_territory_ids=(),
                ledger_position=20,
                assumption=prose,
                basis=prose,
                blast_radius="none whatsoever",
                decision=prose,
            ),
        ),
    )
    assert persuaded.disposition == plain.disposition == VERDICT_PASS_ON_COVERED
    assert persuaded.allowed is plain.allowed is False
    assert persuaded.unknown_territory_ids == plain.unknown_territory_ids
    assert persuaded.first_line == plain.first_line


def test_monotone_composition_no_channel_raises_the_rank() -> None:
    decision = _accepted_promotion_decision()
    coverage = _map(status="covered")
    base = compute_verdict(
        coverage,
        decision,
        _frame_check(FIRST_LINE_YES),
        candidate_digest=CANDIDATE,
        evaluated_position=EVALUATED_POSITION,
    )
    assert base.disposition == VERDICT_PASS

    with_assumption = compute_verdict(
        coverage,
        decision,
        _frame_check(FIRST_LINE_YES),
        candidate_digest=CANDIDATE,
        evaluated_position=EVALUATED_POSITION,
        assumptions=(
            AssumptionRecord(
                assumption_id="a-1",
                touched_territory_ids=("standard-surface-lifecycle",),
                ledger_position=20,
            ),
        ),
    )
    assert verdict_rank(with_assumption.disposition) < verdict_rank(base.disposition)
    # A green demo does not clear an assumption's UNKNOWN: the demonstrated path
    # does not characterize the assumed surface.
    assert with_assumption.disposition == VERDICT_PASS_ON_COVERED
    assert "assumption-shadows-covered:a-1:standard-surface-lifecycle" in (
        with_assumption.reports
    )

    without_demo = compute_verdict(
        coverage,
        decision,
        None,
        candidate_digest=CANDIDATE,
        evaluated_position=EVALUATED_POSITION,
    )
    assert verdict_rank(without_demo.disposition) < verdict_rank(base.disposition)


def test_receipt_validity_is_mechanical_not_judged() -> None:
    decision = _accepted_promotion_decision()
    coverage = _map()

    def verdict_with(receipt: CharacterizationReceipt):
        return compute_verdict(
            coverage,
            decision,
            _frame_check(FIRST_LINE_YES),
            candidate_digest=CANDIDATE,
            evaluated_position=EVALUATED_POSITION,
            receipts=(receipt,),
            validator="validator-agent",
        )

    rejected = {
        "retroactive": _receipt(coverage, ledger_position=EVALUATED_POSITION),
        "not-superseding": _receipt(coverage, ledger_position=10),
        "validator-issued": _receipt(coverage, issuer="validator-agent"),
        "probe-missing": _receipt(coverage, probes=()),
        "probe-not-real-path": _receipt(coverage, probes=(_probe(real_path=False),)),
        "backreference-mismatch": _receipt(
            coverage, backreference_digest=digest_obj({"other": "declaration"})
        ),
        "partial": _receipt(coverage, residual_unknown=True),
    }
    for label, receipt in rejected.items():
        verdict = verdict_with(receipt)
        assert verdict.disposition == VERDICT_PASS_ON_COVERED, label
        assert verdict.unknown_territory_ids == ("standard-surface-lifecycle",), label

    # Territory that surfaced after ratification has no adequacy criterion, so no
    # receipt can exist for it: the thin-receipt back door stays closed.
    unratified = _map(with_criterion=False)
    verdict = compute_verdict(
        unratified,
        decision,
        _frame_check(FIRST_LINE_YES),
        candidate_digest=CANDIDATE,
        evaluated_position=EVALUATED_POSITION,
        receipts=(_receipt(unratified),),
    )
    assert verdict.disposition == VERDICT_PASS_ON_COVERED
    assert any(report.startswith("receipt-adequacy-unratified:") for report in verdict.reports)

    # Tampered receipt evidence is an integrity failure and blocks every class.
    tampered = verdict_with(_receipt(coverage, tampered=True))
    assert tampered.disposition == VERDICT_BLOCK


def test_assumption_outside_frame_is_unknown_and_flagged() -> None:
    decision = _accepted_promotion_decision()
    coverage = _map(status="covered")
    verdict = compute_verdict(
        coverage,
        decision,
        _frame_check(FIRST_LINE_YES),
        candidate_digest=CANDIDATE,
        evaluated_position=EVALUATED_POSITION,
        assumptions=(
            AssumptionRecord(
                assumption_id="a-2",
                touched_territory_ids=("drain-worker-wiring",),
                ledger_position=30,
            ),
        ),
    )
    assert verdict.disposition == VERDICT_PASS_ON_COVERED
    assert verdict.outside_frame_ids == ("drain-worker-wiring",)
    assert "assumption-outside-frame:a-2:drain-worker-wiring" in verdict.reports


def test_frame_check_artifact_mismatch_blocks_as_staging() -> None:
    decision = _accepted_promotion_decision()
    coverage = _map(status="covered")
    verdict = compute_verdict(
        coverage,
        decision,
        _frame_check(FIRST_LINE_YES, artifact_digest=digest_obj({"artifact": "demo-build"})),
        candidate_digest=CANDIDATE,
        evaluated_position=EVALUATED_POSITION,
    )
    assert verdict.disposition == VERDICT_BLOCK
    assert "frame-check-artifact-mismatch" in verdict.reasons


def test_promotion_block_is_a_verdict_block() -> None:
    gap = _request(observations=(_observation("standard-surface", adequate=False),))
    gated = decide_promotion(gap, _roster(), _profile())
    assert gated.allowed is False
    verdict = compute_verdict(
        _map(status="covered"),
        gated,
        _frame_check(FIRST_LINE_YES),
        candidate_digest=CANDIDATE,
        evaluated_position=EVALUATED_POSITION,
    )
    assert verdict.disposition == VERDICT_BLOCK
    assert f"promotion-not-allowed:{gated.disposition}" in verdict.reasons


def test_headline_opens_with_the_forced_question_and_never_bare_passes() -> None:
    decision = _accepted_promotion_decision()
    coverage = _map()
    verdict = compute_verdict(
        coverage,
        decision,
        _frame_check(FIRST_LINE_YES),
        candidate_digest=CANDIDATE,
        evaluated_position=EVALUATED_POSITION,
    )
    headline = render_headline(verdict)
    lines = headline.splitlines()
    assert lines[0] == "Does it do the thing it was built to do? YES"
    assert lines[1] != "Verdict: PASS"
    assert "Unknown territory (1): standard-surface-lifecycle" in lines

    subject = verdict_attestation_subject(verdict, coverage)
    assert subject["coverage_digest"] == coverage.content_digest
    assert subject["headline"] == headline


def test_from_dict_refuses_to_guess() -> None:
    with pytest.raises(VerdictError):
        CoverageTerritory.from_dict(
            {"territory_id": "t", "kind": "vibes", "status": "covered", "declared_by": "x",
             "declaration_position": 1}
        )
    with pytest.raises(VerdictError):
        FrameCheckResult.from_dict(
            {"first_line": "mostly", "artifact_digest": "d", "scenario_instance_digest": "s"}
        )
    with pytest.raises(VerdictError):
        AdequacyCriterion.from_dict({"territory_id": "t", "required_probe_ids": []})
    with pytest.raises(VerdictError):
        CoverageMap(
            territories=(
                CoverageTerritory(
                    territory_id="dup", kind="surface", status="covered",
                    declared_by="x", declaration_position=1,
                ),
                CoverageTerritory(
                    territory_id="dup", kind="surface", status="uncovered",
                    declared_by="x", declaration_position=2,
                ),
            )
        )
