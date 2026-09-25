"""The authority contract: who said it decides what it can carry.

Every probe names the founder distinction it enforces (2026-09-24). The load-
bearing ones are the two directions of the laundering risk: DISCLOSED must never
become ENGAGED on mere opportunity, and STATED must never need a second
signature.
"""

from __future__ import annotations

import pytest

from factory_core import directive_authority as da
from factory_core.engagement import IRREVERSIBLE, RECOVERABLE, REVERSIBLE


def human(i: str = "h1", pos: int = 1, refs: tuple[str, ...] = ()) -> da.Utterance:
    return da.Utterance(
        utterance_id=i,
        speaker=da.SPEAKER_HUMAN,
        position=pos,
        line_digest=f"sha256:{i}",
        references=refs,
    )


def agent(i: str = "a1", pos: int = 1, surfaced: bool = True) -> da.Utterance:
    return da.Utterance(
        utterance_id=i,
        speaker=da.SPEAKER_AGENT,
        position=pos,
        line_digest=f"sha256:{i}",
        surfaced=surfaced,
    )


def test_what_the_human_said_is_certified_by_having_been_said() -> None:
    """ "I said it? It's certified."

    No second signature, no ratification ceremony. The cited transcript line is
    the receipt, and this is the tier whose absence made per-phase certification
    a recurring chore.
    """

    authority = da.derive(human("h1"))
    assert authority.tier == da.STATED
    assert authority.is_human_authority
    # And it carries the worst case without escalating: irreversible, Critical.
    verdict = da.authorizes(authority, reversibility=IRREVERSIBLE, critical=True)
    assert verdict.allowed


def test_the_human_merely_speaking_later_is_opportunity_not_engagement() -> None:
    """The laundering this module exists to prevent.

    "You saying it doesn't mean I saw it or thought about it or approved it."
    A human utterance arriving afterwards proves only that they had the chance.
    Treating it as engagement is how DISCLOSED becomes fake consent.
    """

    told = agent("a1", pos=1)
    # The human speaks after, about something else entirely — no reference.
    transcript = [told, human("h2", pos=2)]
    authority = da.derive(told, transcript)
    assert authority.tier == da.DISCLOSED
    assert "not consent" in authority.basis


def test_engagement_requires_an_observable_reference() -> None:
    """ "There's things we discussed and I actually engaged with so you SAW that I saw it."""

    told = agent("a1", pos=1)
    transcript = [told, human("h2", pos=2, refs=("a1",))]
    authority = da.derive(told, transcript)
    assert authority.tier == da.ENGAGED
    assert "h2" in authority.basis


def test_a_reference_from_the_lane_itself_is_not_engagement() -> None:
    """Otherwise a lane could cite its own follow-up and manufacture consent."""

    told = agent("a1", pos=1)
    transcript = [told, agent("a2", pos=2)]
    object.__setattr__(transcript[1], "references", ("a1",))
    assert da.derive(told, transcript).tier == da.DISCLOSED


def test_a_reference_that_precedes_the_statement_is_not_engagement() -> None:
    """A human cannot have engaged with something that had not been said yet."""

    told = agent("a1", pos=5)
    earlier = human("h0", pos=1, refs=("a1",))
    assert da.derive(told, [earlier, told]).tier == da.DISCLOSED


def test_an_unsurfaced_decision_is_known_unseen() -> None:
    """ "Then there are decisions you make and I have no visibility into whatsoever."""

    authority = da.derive(agent("a1", surfaced=False))
    assert authority.tier == da.UNILATERAL
    assert "never put it in front of the human" in authority.basis


def test_a_human_utterance_cannot_be_marked_unsurfaced() -> None:
    """Otherwise a lane could downgrade something the human actually said."""

    with pytest.raises(da.DirectiveAuthorityError):
        da.Utterance(
            utterance_id="h1",
            speaker=da.SPEAKER_HUMAN,
            position=1,
            line_digest="sha256:h1",
            surfaced=False,
        )


def test_a_citation_without_a_line_digest_is_refused() -> None:
    """An uncheckable citation is a claim. The digest is what makes STATED safe."""

    with pytest.raises(da.DirectiveAuthorityError):
        da.Utterance(utterance_id="h1", speaker=da.SPEAKER_HUMAN, position=1, line_digest="")


def test_disclosure_carries_the_reversible_and_nothing_heavier() -> None:
    disclosed = da.derive(agent("a1"), [agent("a1"), human("h2", pos=2)])
    assert disclosed.tier == da.DISCLOSED
    assert da.authorizes(disclosed, reversibility=REVERSIBLE, critical=False).allowed
    for worse in (
        {"reversibility": IRREVERSIBLE, "critical": False},
        {"reversibility": RECOVERABLE, "critical": False},
        {"reversibility": REVERSIBLE, "critical": True},
    ):
        assert not da.authorizes(disclosed, **worse).allowed, worse


def test_engagement_stops_short_of_the_irreversible_critical_case() -> None:
    """Presumed acceptance is not a deliberate choice."""

    engaged = da.derive(agent("a1"), [agent("a1"), human("h2", pos=2, refs=("a1",))])
    assert da.authorizes(engaged, reversibility=IRREVERSIBLE, critical=False).allowed
    assert da.authorizes(engaged, reversibility=REVERSIBLE, critical=True).allowed
    blocked = da.authorizes(engaged, reversibility=IRREVERSIBLE, critical=True)
    assert not blocked.allowed
    assert "needs the human to say so" in blocked.reason


def test_a_unilateral_decision_carries_only_what_is_determinable() -> None:
    unilateral = da.derive(agent("a1", surfaced=False))
    assert da.authorizes(unilateral, determinable=True).allowed
    undeterminable = da.authorizes(unilateral, determinable=False)
    assert not undeterminable.allowed
    assert "provably never saw" in undeterminable.reason


def test_a_lane_can_never_supersede_what_the_human_said() -> None:
    """ "You must prioritize what I SAID." Precedence is by tier, not recency."""

    said = da.derive(human("h1", pos=1))
    later_lane_decision = da.derive(agent("a9", pos=99, surfaced=False))
    assert not da.may_supersede(later_lane_decision, said)
    # Only the human supersedes the human.
    assert da.may_supersede(da.derive(human("h2", pos=100)), said)
    # And a stronger lane tier may replace a weaker one.
    disclosed = da.derive(agent("a1", pos=2), [agent("a1", pos=2), human("h3", pos=3)])
    assert da.may_supersede(disclosed, later_lane_decision)


def test_the_strongest_authority_binds_and_ties_are_deterministic() -> None:
    said = da.derive(human("h1"))
    quiet = da.derive(agent("a1", surfaced=False))
    assert da.strongest([quiet, said]).tier == da.STATED
    assert da.strongest([]) is None
    # A tie keeps the first supplied rather than depending on iteration order.
    other = da.derive(agent("a2", surfaced=False))
    assert da.strongest([quiet, other]).utterance_id == "a1"
    assert da.strongest([other, quiet]).utterance_id == "a2"


def test_receipts_are_checkable_data_not_a_narrative() -> None:
    """ "When I bitch and ask for receipts, you can provide them."""

    rows = da.receipts(
        [
            da.derive(agent("a1", surfaced=False)),
            da.derive(human("h1")),
        ]
    )
    assert [r["tier"] for r in rows] == [da.STATED, da.UNILATERAL]  # strongest first
    for row in rows:
        assert set(row) == {"utterance_id", "speaker", "tier", "basis", "line_digest"}
        assert row["line_digest"].startswith("sha256:")


def test_an_undefined_tier_or_speaker_is_refused_never_guessed() -> None:
    with pytest.raises(da.DirectiveAuthorityError):
        da.tier_rank("probably-fine")
    with pytest.raises(da.DirectiveAuthorityError):
        da.Utterance(utterance_id="x", speaker="nobody", position=1, line_digest="sha256:x")


def test_tier_order_is_weakest_first_and_load_bearing() -> None:
    assert da.TIERS == (da.UNILATERAL, da.DISCLOSED, da.ENGAGED, da.STATED)
    ranks = [da.tier_rank(t) for t in da.TIERS]
    assert ranks == sorted(ranks)


def test_scope_subsumption_catches_the_global_case() -> None:
    """Pre-release review finding: string equality missed the case it was for.

    A founder directive scoped `global` carries no selectors and so applies
    everywhere, but it never string-matched a narrower provisional scope — exactly
    the pairing the precedence check exists to mark subordinate.
    """

    import importlib.util
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("_d", root / "harness" / "directive.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # `global` subsumes everything.
    assert module._scopes_overlap("global", "run=r1;role=coder")
    # A shared selector must agree; disagreement is different territory.
    assert module._scopes_overlap("role=coder", "run=r1;role=coder")
    assert not module._scopes_overlap("role=tester", "run=r1;role=coder")
    # The narrower scope does not subsume the broader one.
    assert not module._scopes_overlap("run=r1;role=coder", "global")
    # An unparseable scope is not treated as overlapping.
    assert not module._scopes_overlap("!!not a scope!!", "global")


def test_a_provisional_citing_the_founder_is_not_subordinate() -> None:
    """The distinction that made the precedence check real rather than constant.

    Before the citation recorded its speaker, both sides of the comparison were
    fabricated literals and may_supersede() was a compile-time False — the branch
    was decoration. Deriving from the recorded speaker makes it vary.
    """

    founder = da.derive(
        da.Utterance(
            utterance_id="signed",
            speaker=da.SPEAKER_HUMAN,
            position=0,
            line_digest="sha256:" + "0" * 64,
        )
    )
    cited_human = da.derive(
        da.Utterance(
            utterance_id="P-0001",
            speaker=da.SPEAKER_HUMAN,
            position=1,
            line_digest="sha256:" + "1" * 64,
        )
    )
    cited_agent = da.derive(
        da.Utterance(
            utterance_id="P-0002",
            speaker=da.SPEAKER_AGENT,
            position=2,
            line_digest="sha256:" + "2" * 64,
        )
    )
    assert da.may_supersede(cited_human, founder)  # the founder's own words
    assert not da.may_supersede(cited_agent, founder)  # the lane's
