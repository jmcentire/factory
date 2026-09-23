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


def test_autonomous_never_asks_and_never_blocks() -> None:
    assert not eng.blocks_on_questions(eng.AUTONOMOUS)
    assert eng.route(eng.AUTONOMOUS, item()).action == eng.RECORD_ASSUMPTION
    # Even the worst case proceeds: irreversible, Critical, weakly founded.
    worst = item(basis=eng.WEAK, reversibility=eng.IRREVERSIBLE, critical=True)
    routing = eng.route(eng.AUTONOMOUS, worst)
    assert routing.action == eng.RECORD_ESCALATION
    assert not routing.blocks


def test_a_weak_basis_is_an_escalation_not_a_confident_assumption() -> None:
    """Sim, 2026-08-27: otherwise the run launders high uncertainty into a
    documented certainty."""
    for level in (eng.AUTONOMOUS, eng.SCHEDULED):
        routing = eng.route(level, item(basis=eng.WEAK))
        assert routing.action == eng.RECORD_ESCALATION, level


def test_scheduled_sends_only_the_exceptional_and_still_does_not_block() -> None:
    exceptional = item(basis=eng.WEAK, reversibility=eng.IRREVERSIBLE)
    routing = eng.route(eng.SCHEDULED, exceptional)
    assert routing.action == eng.NOTIFY_CHANNEL
    assert not routing.blocks
    assert eng.route(eng.SCHEDULED, item(basis=eng.WEAK, critical=True)).action == (
        eng.NOTIFY_CHANNEL
    )
    # An ordinary undeterminable item is assumed, not sent.
    assert eng.route(eng.SCHEDULED, item()).action == eng.RECORD_ASSUMPTION
    assert not eng.blocks_on_questions(eng.SCHEDULED)


def test_only_interactive_blocks_or_announces() -> None:
    assert eng.route(eng.INTERACTIVE, item()).blocks
    assert eng.blocks_on_questions(eng.INTERACTIVE)
    assert eng.announces_before_acting(eng.INTERACTIVE)
    for level in (eng.AUTONOMOUS, eng.SCHEDULED):
        assert not eng.announces_before_acting(level), level


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
