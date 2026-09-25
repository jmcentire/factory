"""Who said it, whether the human saw it, and what that lets it authorize.

The invariant this module upholds: **the authority behind a directive is derived
from who spoke and whether the human demonstrably engaged, never asserted by the
lane that wants to rely on it.**

The founder named the four states directly (2026-09-24), and the point of the
list is that they are *distinguishable from the transcript* — so no lane should
need to be told how to tell them apart:

``STATED``
    The human said it. Self-certifying: *"I said it? It's certified."* The
    transcript line, and its digest, **is** the receipt. No ceremony, no second
    signature restating what the human already said. This is the tier whose
    absence produced `certify-phases.sh --certify` as a recurring chore.

``ENGAGED``
    The lane said it and the human demonstrably referenced it afterwards — *"you
    SAW that I saw it."* Presumed acceptable, and revocable.

``DISCLOSED``
    The lane said it to the human and no reference came back. *"Maybe I saw it,
    maybe I didn't. You saying it doesn't mean I saw it or thought about it or
    approved it."* Disclosure is **not** consent, and this is the tier the
    factory most needs to stop collapsing into ENGAGED, because doing so
    protects the lane rather than the human: a lane that announces a risk and
    proceeds has manufactured a record that reads like informed agreement.

``UNILATERAL``
    The lane decided and never surfaced it. *"You know I didn't see that."*

Two mechanical commitments make this a control rather than a vocabulary.

**Derivation is fail-closed.** The tier is computed from the citation's speaker
and the observable references that follow it. Where the evidence does not reach,
the *weaker* tier is returned — never the stronger. An unobservable engagement is
DISCLOSED; an unsurfaced statement is UNILATERAL. Ambiguity may not be resolved
in the direction of consent.

**The human's own words need no signature because they are already
authenticated.** ``harness/directive.py``'s provisional citation carries
``file:line:uuid:line-sha256``, so a claim that the human said something is
checkable against the transcript bytes. That is what lets STATED be
self-certifying without weakening anything: the agent still cannot sign the
founder's ledger, and it still cannot *forge* a citation — it can only point at
a line whose digest either matches or does not.

And one rule follows from *"you must prioritize what I SAID"*: a lane-originated
directive may never supersede a human-stated one. Precedence is by tier, not by
recency, so a later agent decision cannot quietly overwrite an earlier human
instruction.

Posture: stdlib only, pure, no clock, no disk. Reversibility and criticality come
from :mod:`factory_core.engagement` rather than a rival vocabulary.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from factory_core.engagement import (
    IRREVERSIBLE,
    REVERSIBILITY,
    REVERSIBLE,
    EngagementError,
)

SPEAKER_HUMAN: Final = "human"
SPEAKER_AGENT: Final = "agent"
SPEAKERS: Final[tuple[str, ...]] = (SPEAKER_HUMAN, SPEAKER_AGENT)

#: Authority tiers, weakest first. The order is load-bearing: precedence,
#: supersession and what may be authorized are all decided by rank.
UNILATERAL: Final = "unilateral"
DISCLOSED: Final = "disclosed"
ENGAGED: Final = "engaged"
STATED: Final = "stated"
TIERS: Final[tuple[str, ...]] = (UNILATERAL, DISCLOSED, ENGAGED, STATED)

#: What a lane may do with a directive at a given tier.
AUTHORIZED: Final = "authorized"
ESCALATE: Final = "escalate"


class DirectiveAuthorityError(ValueError):
    """A speaker, tier, or citation the factory does not define."""


def tier_rank(tier: str) -> int:
    """Position in :data:`TIERS`. Higher binds harder."""

    normalized = str(tier).strip().lower()
    if normalized not in TIERS:
        raise DirectiveAuthorityError(
            f"unknown authority tier {tier!r}; use one of {', '.join(TIERS)}"
        )
    return TIERS.index(normalized)


def _speaker(value: str) -> str:
    text = str(value).strip().lower()
    if text not in SPEAKERS:
        raise DirectiveAuthorityError(
            f"unknown speaker {value!r}; use one of {', '.join(SPEAKERS)}"
        )
    return text


@dataclass(frozen=True)
class Utterance:
    """One authenticated line of the transcript.

    ``line_digest`` is what makes a citation checkable rather than a claim: the
    same ``line-sha256`` the provisional ledger already records. A lane can point
    at a line; it cannot invent one whose digest matches.

    ``surfaced`` is False for reasoning the human never saw. It exists so that
    "I decided this quietly" has somewhere honest to land instead of being
    reported as something the lane told them.

    ``references`` are the ids this utterance cites. A human utterance
    referencing a lane's earlier one is the *only* observable evidence of
    engagement, which is why it is a field and not an inference.
    """

    utterance_id: str
    speaker: str
    position: int
    line_digest: str
    surfaced: bool = True
    references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.utterance_id).strip():
            raise DirectiveAuthorityError("an utterance needs a non-empty utterance_id")
        object.__setattr__(self, "speaker", _speaker(self.speaker))
        if not str(self.line_digest).strip():
            raise DirectiveAuthorityError(
                "an utterance needs a line_digest: an uncheckable citation is a claim, "
                "and the whole point is that the human's own words are already authenticated"
            )
        if self.speaker == SPEAKER_HUMAN and not self.surfaced:
            raise DirectiveAuthorityError(
                "a human utterance is surfaced by definition; marking it unsurfaced would "
                "let a lane downgrade something the human actually said"
            )


@dataclass(frozen=True)
class DirectiveAuthority:
    """The tier behind one directive, with the reason retained rather than inferred."""

    utterance_id: str
    speaker: str
    tier: str
    basis: str
    line_digest: str

    @property
    def is_human_authority(self) -> bool:
        """Whether the human is the source. Only STATED is."""

        return self.tier == STATED


def derive(
    utterance: Utterance, transcript: Sequence[Utterance] = ()
) -> DirectiveAuthority:
    """The tier for ``utterance``, computed from the transcript around it.

    Fail-closed at every branch: where the evidence for a stronger tier is not
    present, the weaker one is returned. Nothing here consults what the lane
    would like the tier to be.
    """

    if utterance.speaker == SPEAKER_HUMAN:
        return DirectiveAuthority(
            utterance_id=utterance.utterance_id,
            speaker=utterance.speaker,
            tier=STATED,
            basis="the human said it; the cited transcript line is the receipt",
            line_digest=utterance.line_digest,
        )

    if not utterance.surfaced:
        return DirectiveAuthority(
            utterance_id=utterance.utterance_id,
            speaker=utterance.speaker,
            tier=UNILATERAL,
            basis="the lane decided this and never put it in front of the human",
            line_digest=utterance.line_digest,
        )

    # Engagement is observable or it is not claimed. A human utterance merely
    # arriving later is opportunity, not evidence — treating it as evidence is
    # exactly how DISCLOSED gets laundered into ENGAGED.
    for other in transcript:
        if (
            other.speaker == SPEAKER_HUMAN
            and other.position > utterance.position
            and utterance.utterance_id in other.references
        ):
            return DirectiveAuthority(
                utterance_id=utterance.utterance_id,
                speaker=utterance.speaker,
                tier=ENGAGED,
                basis=(
                    f"the human referenced it in {other.utterance_id}; they saw it and "
                    "engaged, so it is presumed acceptable and remains revocable"
                ),
                line_digest=utterance.line_digest,
            )

    return DirectiveAuthority(
        utterance_id=utterance.utterance_id,
        speaker=utterance.speaker,
        tier=DISCLOSED,
        basis=(
            "the lane said it and no human reference came back; disclosure is not consent"
        ),
        line_digest=utterance.line_digest,
    )


@dataclass(frozen=True)
class Authorization:
    """Whether a tier may carry a particular action, and why."""

    outcome: str
    reason: str

    @property
    def allowed(self) -> bool:
        return self.outcome == AUTHORIZED


def authorizes(
    authority: DirectiveAuthority,
    *,
    reversibility: str = REVERSIBLE,
    critical: bool = False,
    determinable: bool = False,
) -> Authorization:
    """Whether ``authority`` is enough to carry this action on its own.

    The ladder is the founder's distinction turned into consequences:

    * ``STATED`` carries anything. It is the human's own instruction, and asking
      them to re-certify it is the ceremony this whole layer removes.
    * ``ENGAGED`` carries anything except an irreversible action on a Critical
      surface — presumed acceptable is not the same as chosen deliberately.
    * ``DISCLOSED`` carries only the reversible and non-Critical. It is the tier
      that must not quietly become consent.
    * ``UNILATERAL`` carries only what is *determinable* — derivable from a
      stronger directive, the code, the schema, or a criterion already given.
      Anything else the human has provably never seen, so it escalates.
    """

    if str(reversibility).strip().lower() not in REVERSIBILITY:
        raise EngagementError(
            f"unknown reversibility {reversibility!r}; use one of {', '.join(REVERSIBILITY)}"
        )
    reverse = str(reversibility).strip().lower()
    rank = tier_rank(authority.tier)

    if rank == tier_rank(STATED):
        return Authorization(AUTHORIZED, "the human stated it")
    if rank == tier_rank(ENGAGED):
        if reverse == IRREVERSIBLE and critical:
            return Authorization(
                ESCALATE,
                "engaged is presumed acceptance, not a deliberate choice: an irreversible "
                "action on a Critical surface needs the human to say so",
            )
        return Authorization(AUTHORIZED, "the human referenced it, so they saw and engaged")
    if rank == tier_rank(DISCLOSED):
        if reverse == REVERSIBLE and not critical:
            return Authorization(
                AUTHORIZED, "reversible and non-Critical; disclosure is enough to proceed"
            )
        return Authorization(
            ESCALATE,
            "the human was told and never responded; disclosure is not consent, and this "
            "action is not one to take on an unanswered notice",
        )
    if determinable:
        return Authorization(
            AUTHORIZED,
            "the human never saw this, but the answer is determinable from a stronger "
            "directive, the code, or a criterion already given",
        )
    return Authorization(
        ESCALATE,
        "the human provably never saw this and it is not determinable; it is the lane's "
        "own decision and must be surfaced rather than relied upon",
    )


def may_supersede(new: DirectiveAuthority, old: DirectiveAuthority) -> bool:
    """Whether ``new`` may replace ``old``.

    *"You must prioritize what I SAID."* Precedence is by tier, not recency: a
    lane-originated directive can never overwrite a human-stated one, however
    much later it arrives. Only the human supersedes the human.
    """

    return tier_rank(new.tier) >= tier_rank(old.tier)


def strongest(authorities: Iterable[DirectiveAuthority]) -> DirectiveAuthority | None:
    """The binding authority among several, or ``None`` when there are none.

    Ties keep the earliest supplied, so the result is deterministic rather than
    dependent on iteration order.
    """

    best: DirectiveAuthority | None = None
    for candidate in authorities:
        if best is None or tier_rank(candidate.tier) > tier_rank(best.tier):
            best = candidate
    return best


def receipts(authorities: Sequence[DirectiveAuthority]) -> tuple[dict[str, str], ...]:
    """The answer to *"show me the receipts"*, as data rather than prose.

    Each row names the utterance, who spoke it, the tier it earned, why it earned
    it, and the transcript digest that proves the line. This is what the founder
    asked for when they said a lane should be able to produce receipts on demand:
    not a narrative reconstruction, a list that can be checked.
    """

    return tuple(
        {
            "utterance_id": a.utterance_id,
            "speaker": a.speaker,
            "tier": a.tier,
            "basis": a.basis,
            "line_digest": a.line_digest,
        }
        for a in sorted(authorities, key=lambda a: (-tier_rank(a.tier), a.utterance_id))
    )
