"""Step 0 of the meta-loop: the auditor's acceptance test, written before the auditor.

Two halves, and the negative half is not optional. A code that fires on every PASS
passes the positive probe perfectly, so the positive probe alone proves nothing about
discrimination. Each predicate therefore gets a paired case: one artifact set that must
produce it, and one that must stay silent.

Run 1's recorded artifacts are not on this disk (``.factory/`` is absent), so the
positive fixtures reconstruct run 1's *shape* against the real typed surfaces —
``CoverageMap``/``Verdict``/``DoneComposition`` — rather than replaying its exact bytes.
That is a weaker claim than a byte replay and is stated here rather than implied.
"""

from __future__ import annotations

import pytest

from factory_core.audit import (
    AUDIT_CODES,
    CODE_ORACLE_FRAME_UNTESTED,
    CODE_SCOPE_UNION_GAP,
    CODE_UNCODED_PASS,
    CODE_UNCOVERED_MASS_SHIPPED,
    CODE_VERDICT_OVERCLAIM,
    audit_run,
)
from factory_core.handover import DoneComposition, Handover, HandoverScope
from factory_core.verdict import (
    TERRITORY_COVERED,
    TERRITORY_KIND_ORACLE,
    TERRITORY_KIND_SCENARIO,
    TERRITORY_UNCOVERED,
    VERDICT_PASS,
    VERDICT_PASS_ON_COVERED,
    CoverageMap,
    CoverageTerritory,
    Verdict,
    VerdictError,
)


def territory(
    territory_id: str,
    *,
    kind: str = TERRITORY_KIND_SCENARIO,
    status: str = TERRITORY_COVERED,
    position: int = 1,
) -> CoverageTerritory:
    return CoverageTerritory(
        territory_id=territory_id,
        kind=kind,
        status=status,
        declared_by="validator",
        declaration_position=position,
    )


def verdict(
    *,
    disposition: str = VERDICT_PASS,
    unknown: tuple[str, ...] = (),
    receipted: tuple[str, ...] = (),
    promotion: str = VERDICT_PASS,
) -> Verdict:
    return Verdict(
        first_line="yes",
        disposition=disposition,
        allowed=disposition == VERDICT_PASS,
        unknown_territory_ids=unknown,
        receipted_territory_ids=receipted,
        outside_frame_ids=(),
        reasons=(),
        reports=(),
        coverage_digest="d" * 64,
        promotion_disposition=promotion,
    )


def composition(
    *,
    covered: tuple[str, ...] = ("ship",),
    missing: tuple[str, ...] = (),
    disposition: str = VERDICT_PASS,
) -> DoneComposition:
    return DoneComposition(
        reachable=not missing,
        token="__DONE__" if not missing else "",
        validator="validator",
        covered_verbs=covered,
        missing_verbs=missing,
        reasons=(),
        reports=(),
        coverage_digest="d" * 64,
        verdict_disposition=disposition,
    )


def handover(
    handover_id: str,
    *,
    completed: tuple[str, ...] = (),
    assumed: tuple[str, ...] = (),
    position: int = 1,
) -> Handover:
    return Handover(
        handover_id=handover_id,
        from_seat="coder",
        claim="complete against dispatch",
        scope=HandoverScope(completed=completed, assumed_in_scope_by_others=assumed),
        ledger_position=position,
    )


CLEAN_COVERAGE = CoverageMap(
    territories=(
        territory("scenario.reservation-drifts-someone-is-told"),
        territory("oracle.producer-emits", kind=TERRITORY_KIND_ORACLE),
    ),
    verb_ids=("ship",),
)


def test_clean_run_emits_only_the_escape_hatch() -> None:
    """The negative probe for every substantive code at once.

    A run with full coverage, a mechanically consistent PASS, and a closed scope union
    must produce no substantive code. It still produces ``uncoded-pass``, which is the
    point of that code: silence and "nothing we can name" are different facts.
    """

    report = audit_run(
        run_id="run-clean",
        coverage=CLEAN_COVERAGE,
        verdict=verdict(),
        composition=composition(),
        handovers=(handover("h1", completed=("ship",)),),
    )
    assert report.codes == (CODE_UNCODED_PASS,)


def test_uncovered_mass_shipped_fires_on_a_pass_carrying_named_unknown() -> None:
    """Positive probe: run 1's disclosure-priced-as-a-disclaimer, made countable."""

    coverage = CoverageMap(
        territories=(
            territory("scenario.reservation-drifts-someone-is-told"),
            territory(
                "scenario.drain-behavior-under-crash",
                status=TERRITORY_UNCOVERED,
                position=2,
            ),
        ),
        verb_ids=("ship",),
    )
    report = audit_run(
        run_id="run-1",
        coverage=coverage,
        verdict=verdict(
            disposition=VERDICT_PASS_ON_COVERED,
            unknown=("scenario.drain-behavior-under-crash",),
            promotion=VERDICT_PASS_ON_COVERED,
        ),
        composition=composition(disposition=VERDICT_PASS_ON_COVERED),
        handovers=(handover("h1", completed=("ship",)),),
    )
    assert CODE_UNCOVERED_MASS_SHIPPED in report.codes
    assert CODE_UNCODED_PASS not in report.codes


def test_uncovered_mass_is_silent_when_the_gap_was_receipted() -> None:
    """Negative probe: a cleared gap is not a shipped gap."""

    coverage = CoverageMap(
        territories=(
            territory("scenario.reservation-drifts-someone-is-told"),
            territory("scenario.drain-behavior", status=TERRITORY_UNCOVERED, position=2),
        ),
        verb_ids=("ship",),
    )
    report = audit_run(
        run_id="run-receipted",
        coverage=coverage,
        verdict=verdict(receipted=("scenario.drain-behavior",)),
        composition=composition(),
        handovers=(handover("h1", completed=("ship",)),),
    )
    assert CODE_UNCOVERED_MASS_SHIPPED not in report.codes


def test_verdict_overclaim_fires_when_promotion_outranks_the_computed_disposition() -> None:
    """Positive probe: the recorded claim asserts more than the computation supports."""

    coverage = CoverageMap(
        territories=(
            territory("scenario.reservation-drifts-someone-is-told"),
            territory("scenario.drain-behavior", status=TERRITORY_UNCOVERED, position=2),
        ),
        verb_ids=("ship",),
    )
    report = audit_run(
        run_id="run-overclaim",
        coverage=coverage,
        verdict=verdict(
            disposition=VERDICT_PASS_ON_COVERED,
            unknown=("scenario.drain-behavior",),
            promotion=VERDICT_PASS,
        ),
        composition=composition(disposition=VERDICT_PASS_ON_COVERED),
        handovers=(handover("h1", completed=("ship",)),),
    )
    assert CODE_VERDICT_OVERCLAIM in report.codes


def test_verdict_overclaim_is_silent_when_the_claim_matches_the_computation() -> None:
    """Negative probe: agreeing dispositions are not an overclaim, at any rank."""

    report = audit_run(
        run_id="run-agreeing",
        coverage=CLEAN_COVERAGE,
        verdict=verdict(disposition=VERDICT_PASS, promotion=VERDICT_PASS),
        composition=composition(),
        handovers=(handover("h1", completed=("ship",)),),
    )
    assert CODE_VERDICT_OVERCLAIM not in report.codes


def test_scope_union_gap_fires_when_a_verb_is_assumed_by_others_and_owned_by_none() -> None:
    """Positive probe: run 1's shared silent omission — everyone assumed someone else."""

    report = audit_run(
        run_id="run-gap",
        coverage=CLEAN_COVERAGE,
        verdict=verdict(),
        composition=composition(covered=(), missing=("assemble-the-halves",)),
        handovers=(
            handover("h-coder", completed=("build",), assumed=("assemble-the-halves",)),
            handover(
                "h-tester",
                completed=("oracles",),
                assumed=("assemble-the-halves",),
                position=2,
            ),
        ),
    )
    assert CODE_SCOPE_UNION_GAP in report.codes


def test_scope_union_gap_is_silent_when_the_missing_verb_was_never_assumed() -> None:
    """Negative probe: a verb nobody claimed is an ordinary gap, not the composition defect.

    The code names a specific failure — a belief that someone else owned it — not the
    generic fact of incompleteness. Conflating the two would make it fire on every
    unfinished run and measure nothing.
    """

    report = audit_run(
        run_id="run-plain-gap",
        coverage=CLEAN_COVERAGE,
        verdict=verdict(),
        composition=composition(covered=(), missing=("assemble-the-halves",)),
        handovers=(handover("h-coder", completed=("build",)),),
    )
    assert CODE_SCOPE_UNION_GAP not in report.codes


def test_oracle_frame_untested_fires_when_no_scenario_is_covered() -> None:
    """Positive probe: oracles and surfaces present, the product's own sentence unexercised."""

    coverage = CoverageMap(
        territories=(
            territory("oracle.producer-emits", kind=TERRITORY_KIND_ORACLE),
            territory(
                "scenario.reservation-drifts",
                status=TERRITORY_UNCOVERED,
                position=2,
            ),
        ),
        verb_ids=("ship",),
    )
    report = audit_run(
        run_id="run-frame",
        coverage=coverage,
        verdict=verdict(
            disposition=VERDICT_PASS_ON_COVERED,
            unknown=("scenario.reservation-drifts",),
            promotion=VERDICT_PASS_ON_COVERED,
        ),
        composition=composition(disposition=VERDICT_PASS_ON_COVERED),
        handovers=(handover("h1", completed=("ship",)),),
    )
    assert CODE_ORACLE_FRAME_UNTESTED in report.codes


def test_oracle_frame_untested_is_silent_when_a_scenario_is_covered() -> None:
    """Negative probe: one covered scenario is enough to clear the predicate."""

    report = audit_run(
        run_id="run-framed",
        coverage=CLEAN_COVERAGE,
        verdict=verdict(),
        composition=composition(),
        handovers=(handover("h1", completed=("ship",)),),
    )
    assert CODE_ORACLE_FRAME_UNTESTED not in report.codes


def test_no_code_fires_on_a_non_pass_verdict() -> None:
    """The vocabulary is about defects that survive *on a PASS*.

    A blocked or incomplete run already has an attributable failure path; emitting
    verdict-scope codes there would double-count the disposition the factory already
    reports, and would let a loud non-PASS run dominate the frequency table.
    """

    report = audit_run(
        run_id="run-blocked",
        coverage=CLEAN_COVERAGE,
        verdict=verdict(disposition="block", promotion="block"),
        composition=composition(disposition="block"),
        handovers=(),
    )
    assert report.codes == ()


def test_every_emitted_code_is_a_member_of_the_closed_vocabulary() -> None:
    """No free text reaches the store. An unknown code is a write error, not a row."""

    report = audit_run(
        run_id="run-clean",
        coverage=CLEAN_COVERAGE,
        verdict=verdict(),
        composition=composition(),
        handovers=(handover("h1", completed=("ship",)),),
    )
    assert set(report.codes) <= set(AUDIT_CODES)
    for row in report.rows():
        assert row["code"] in AUDIT_CODES
        assert row["run_id"] == "run-clean"
        assert set(row) == {"run_id", "code", "count", "vocab_digest"}


def test_rows_carry_a_stable_vocabulary_digest() -> None:
    """Every row is grouped by this before summing, so a vocabulary change cannot
    silently blend incommensurable counts."""

    first = audit_run(
        run_id="a",
        coverage=CLEAN_COVERAGE,
        verdict=verdict(),
        composition=composition(),
        handovers=(handover("h1", completed=("ship",)),),
    )
    second = audit_run(
        run_id="b",
        coverage=CLEAN_COVERAGE,
        verdict=verdict(),
        composition=composition(),
        handovers=(handover("h1", completed=("ship",)),),
    )
    digests = {row["vocab_digest"] for row in first.rows()} | {
        row["vocab_digest"] for row in second.rows()
    }
    assert len(digests) == 1
    assert len(digests.pop()) == 64


def test_auditor_is_read_only_over_its_inputs() -> None:
    """The auditor may not mutate the artifacts it judges — it has no write path by
    construction, and this pins that as a behavioral fact rather than a claim."""

    coverage = CLEAN_COVERAGE
    subject = verdict()
    before = (coverage.to_dict(), subject.to_dict())
    audit_run(
        run_id="run-clean",
        coverage=coverage,
        verdict=subject,
        composition=composition(),
        handovers=(handover("h1", completed=("ship",)),),
    )
    assert (coverage.to_dict(), subject.to_dict()) == before


def test_unknown_disposition_is_refused_rather_than_guessed() -> None:
    """Fail closed. An unrecognised disposition is a schema error, never a silent skip."""

    with pytest.raises(VerdictError):
        audit_run(
            run_id="run-bogus",
            coverage=CLEAN_COVERAGE,
            verdict=verdict(disposition="probably-fine", promotion="probably-fine"),
            composition=composition(),
            handovers=(),
        )
