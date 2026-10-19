"""The engagement contract: what the run does instead of asking."""

from __future__ import annotations

import pytest

from factory_core import engagement as eng


def item(**overrides: object) -> eng.Item:
    base: dict[str, object] = {"determinable": False, "owned_here": True}
    base.update(overrides)
    return eng.Item(**base)  # type: ignore[arg-type]


def test_a_determinable_answer_is_never_a_question_at_any_engagement() -> None:
    """Question-theatre (founder, 2026-09-02): a lane that reaches a defensible
    answer and hands it back as a question is offloading, not escalating."""
    for level in eng.ENGAGEMENTS:
        routing = eng.route(level, item(determinable=True, critical=True, basis=eng.WEAK))
        assert routing.action == eng.DECIDE_LOCALLY, level
        assert not routing.blocks


def test_another_authoritys_fact_is_never_this_runs_question() -> None:
    """One authority per fact is also the escalation filter."""
    for level in eng.ENGAGEMENTS:
        routing = eng.route(level, item(owned_here=False, basis=eng.WEAK, critical=True))
        assert routing.action == eng.DECIDE_LOCALLY, level


def test_nothing_but_a_genuine_impasse_ever_waits_on_the_human() -> None:
    """The impasse rule (founder, 2026-10-18): "Do you know what needs fucking doing?
    YES. THEN THERE IS NO FUCKING IMPASSE." Uncertainty, risk and Critical surfaces
    are decided by the run and recorded; none of them waits on anyone."""
    worst = item(basis=eng.WEAK, reversibility=eng.IRREVERSIBLE, critical=True)
    assert not hasattr(eng, "blocks_on_questions")
    assert not hasattr(eng, "announces_before_acting")  # no veto window at any level
    for level in eng.ENGAGEMENTS:
        assert eng.route(level, item()).action == eng.RECORD_ASSUMPTION, level
        routing = eng.route(level, worst)
        assert routing.action == eng.RECORD_ESCALATION, level
        assert not routing.blocks, level


def test_a_weak_basis_is_an_escalation_not_a_confident_assumption() -> None:
    """Sim, 2026-08-27: otherwise the run launders high uncertainty into a
    documented certainty. It is still decided and the run still proceeds."""
    for level in eng.ENGAGEMENTS:
        routing = eng.route(level, item(basis=eng.WEAK))
        assert routing.action == eng.RECORD_ESCALATION, level
        assert not routing.blocks


def test_a_genuine_impasse_reaches_the_human_the_way_the_engagement_says() -> None:
    impasse = item(impasse=True)
    assert eng.route(eng.INTERACTIVE, impasse).action == eng.BLOCK_FOR_HUMAN
    assert eng.route(eng.INTERACTIVE, impasse).blocks
    assert eng.route(eng.SCHEDULED, impasse).action == eng.NOTIFY_CHANNEL
    autonomous = eng.route(eng.AUTONOMOUS, impasse)
    assert autonomous.action == eng.RECORD_ESCALATION
    assert not autonomous.blocks


def test_a_claimed_impasse_on_a_determinable_item_is_not_one() -> None:
    """If any role can determine the answer, no role is at an impasse."""
    for level in eng.ENGAGEMENTS:
        routing = eng.route(level, item(impasse=True, determinable=True))
        assert routing.action == eng.DECIDE_LOCALLY, level


def test_an_unconfigured_or_unknown_engagement_is_refused_never_guessed() -> None:
    for value in ("", "none", "semi", "INTERACTIVE-ish"):
        with pytest.raises(eng.EngagementError):
            eng.normalize(value)
    # Case and surrounding space are accepted; the name itself is not invented.
    assert eng.normalize("  Autonomous ") == eng.AUTONOMOUS


def test_basis_and_reversibility_reject_values_the_factory_does_not_define() -> None:
    with pytest.raises(eng.EngagementError):
        item(basis="probably")
    with pytest.raises(eng.EngagementError):
        item(reversibility="mostly")
