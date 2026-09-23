"""How much of the human the run gets, and what happens instead of asking.

The invariant this module upholds: **the run never stops to ask for something
it could determine, and never asks at all below the engagement the human
chose.** Engagement is set once, at ignition, by the human; the lanes read it
and never negotiate it.

Three levels, and nothing between them:

``interactive``
    The human is present. An undeterminable question about a fact this run
    owns blocks until they answer. This is a working session, not a pipeline.

``scheduled`` (the default)
    Every back-and-forth happens up front. During the run nothing blocks:
    an undeterminable question becomes a recorded assumption, and only an
    *exceptional* one — weak basis on an irreversible or Critical surface —
    is sent out through whatever channel the human configured. The run keeps
    going while it is out; an answer that arrives is applied, and one that
    never arrives leaves the assumption standing in the report.

``autonomous``
    Zero human involvement. Nothing leaves the run. Every undeterminable item
    is recorded and the work continues; the report leads with what was assumed
    and what could not be assumed safely. If it is wrong, it is addressed when
    it is done.

Two rules bind every level, because both came from watching the failure:

1. **Determinable is never a question.** A lane that reaches a defensible
   answer and hands it back as a question is offloading dressed as diligence
   (founder, 2026-09-02). Escalate what is *undeterminable*, never what is
   merely unknown.
2. **Ask whose fact it is.** A fact owned by another authority — another
   service, the caller, or a criterion the human already stated — is not this
   run's question in any engagement. One authority per fact is also the
   escalation filter.

And one category exists so that uncertainty is not laundered into confidence
(Sim, 2026-08-27): when the basis for an assumption is too weak for its own
blast-radius estimate to mean anything, the item is an **escalation record**,
not an assumption record. Both are reported; they are not the same claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

INTERACTIVE: Final = "interactive"
SCHEDULED: Final = "scheduled"
AUTONOMOUS: Final = "autonomous"

#: Every engagement, weakest human involvement first.
ENGAGEMENTS: Final[tuple[str, ...]] = (AUTONOMOUS, SCHEDULED, INTERACTIVE)

#: What the run does with an item instead of (or as) asking.
DECIDE_LOCALLY: Final = "decide-locally"
RECORD_ASSUMPTION: Final = "record-assumption"
RECORD_ESCALATION: Final = "record-escalation"
NOTIFY_CHANNEL: Final = "notify-channel"
BLOCK_FOR_HUMAN: Final = "block-for-human"

#: How hard an action is to take back. Reversibility decides what counts as
#: exceptional; it never decides whether the run may proceed.
REVERSIBLE: Final = "reversible"
RECOVERABLE: Final = "recoverable"
IRREVERSIBLE: Final = "irreversible"
REVERSIBILITY: Final[tuple[str, ...]] = (REVERSIBLE, RECOVERABLE, IRREVERSIBLE)

#: How well founded a proposed assumption is.
STRONG: Final = "strong"
WEAK: Final = "weak"
BASIS: Final[tuple[str, ...]] = (STRONG, WEAK)


class EngagementError(ValueError):
    """An engagement, basis, or reversibility value the factory does not define."""


def normalize(value: str) -> str:
    """The engagement named by ``value``, or raise.

    There is no default here on purpose: a run that cannot say how much of the
    human it gets has not been configured, and guessing it is how a pipeline
    quietly becomes interactive (or a working session quietly runs dark).
    """

    text = str(value).strip().lower()
    if text not in ENGAGEMENTS:
        raise EngagementError(
            f"unknown engagement {value!r}; use one of {', '.join(ENGAGEMENTS)}"
        )
    return text


def _require(value: str, allowed: tuple[str, ...], field: str) -> str:
    text = str(value).strip().lower()
    if text not in allowed:
        raise EngagementError(f"unknown {field} {value!r}; use one of {', '.join(allowed)}")
    return text


@dataclass(frozen=True)
class Item:
    """One thing the run could stop for.

    ``determinable``
        The answer is derivable from the signed artifacts, the code, the
        schema, the history, or a criterion the human already gave.
    ``owned_here``
        This run is the authority for the fact. False means the fact belongs
        to another service, the caller, or a policy declared elsewhere.
    ``basis``
        How well founded the proposed assumption is if the run proceeds.
    ``reversibility``
        How hard the resulting action is to take back.
    ``critical``
        The surface is Critical under the run's criticality rules.
    """

    determinable: bool
    owned_here: bool
    basis: str = STRONG
    reversibility: str = REVERSIBLE
    critical: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "basis", _require(self.basis, BASIS, "basis"))
        object.__setattr__(
            self, "reversibility", _require(self.reversibility, REVERSIBILITY, "reversibility")
        )


@dataclass(frozen=True)
class Routing:
    """What happens to an item, and why — the why is retained, not inferred."""

    action: str
    reason: str

    @property
    def blocks(self) -> bool:
        """Whether the run stops here. Only ``interactive`` ever does."""

        return self.action == BLOCK_FOR_HUMAN


def route(engagement: str, item: Item) -> Routing:
    """Where ``item`` goes under ``engagement``.

    The order of the checks is the doctrine: determinable first, ownership
    second, engagement last. A question that fails either of the first two is
    not a question in any engagement, so no level of human involvement can
    resurrect it.
    """

    level = normalize(engagement)
    if item.determinable:
        return Routing(
            DECIDE_LOCALLY,
            "determinable from the artifacts, the code, or a criterion already given",
        )
    if not item.owned_here:
        return Routing(
            DECIDE_LOCALLY,
            "another authority owns this fact; record the delegation, do not ask",
        )
    if level == AUTONOMOUS:
        if item.basis == WEAK:
            return Routing(
                RECORD_ESCALATION,
                "zero engagement: the basis is too weak to assume; recorded for the report",
            )
        return Routing(RECORD_ASSUMPTION, "zero engagement: assumed on a stated basis")
    if level == SCHEDULED:
        if item.basis == WEAK and (item.reversibility == IRREVERSIBLE or item.critical):
            return Routing(
                NOTIFY_CHANNEL,
                "exceptional: weak basis on an irreversible or Critical surface",
            )
        if item.basis == WEAK:
            return Routing(
                RECORD_ESCALATION,
                "weak basis, recoverable surface: recorded rather than sent",
            )
        return Routing(RECORD_ASSUMPTION, "assumed on a stated basis; the run does not stop")
    return Routing(BLOCK_FOR_HUMAN, "interactive: the human is present and owns this call")


def blocks_on_questions(engagement: str) -> bool:
    """Whether an undeterminable owned question can stop this run at all."""

    return normalize(engagement) == INTERACTIVE


def announces_before_acting(engagement: str) -> bool:
    """Whether consequential actions get a veto window before execution.

    Only the interactive level has someone to veto. The other two record the
    action in the side-effect register and proceed; an irreversible action is
    flagged there, which is what makes the report readable afterwards.
    """

    return normalize(engagement) == INTERACTIVE
