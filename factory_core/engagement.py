"""How much of the human the run gets, and what happens instead of asking.

The invariant this module upholds is the impasse rule (founder, 2026-10-18,
verbatim: "The only thing it fucking needs is if there's a [genuine] impasse."
and "Do you know what needs fucking doing? YES. THEN THERE IS NO FUCKING
IMPASSE."): **the run involves the founder only at a genuine impasse**, which
is when no role in the run knows what needs doing. Uncertainty is not an
impasse, risk is not, a gate is not, and a policy question is not. When any
role knows what needs doing, it does it, records what it decided and the
evidence, and proceeds; the founder may override any recorded decision after
the fact, and the override governs from then on.

Engagement is set once, at ignition. It changes only how a genuine impasse
reaches the human, never whether the run stops for anything else:

``interactive``
    The human is present. A genuine impasse waits for them, and only that item
    waits; everything else proceeds.

``scheduled`` (the default)
    A genuine impasse goes out through whatever channel the human configured
    and that item waits; the run keeps going on everything else.

``autonomous``
    Nothing leaves the run. A genuine impasse is recorded as an escalation and
    the report leads with it.

Two rules bind every level, because both came from watching the failure:

1. **Determinable is never a question.** A lane that reaches a defensible
   answer and hands it back as a question is offloading dressed as diligence
   (founder, 2026-09-02).
2. **Ask whose fact it is.** A fact owned by another authority — another
   service, the caller, or a criterion the human already stated — is not this
   run's question in any engagement. One authority per fact is also the
   escalation filter.

And one category exists so that uncertainty is not laundered into confidence
(Sim, 2026-08-27): when the basis for a decision is too weak for its own
blast-radius estimate to mean anything, the item is an **escalation record**,
not an assumption record. Both are recorded and the run proceeds on both; they
are not the same claim.
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
    ``impasse``
        No role in the run knows what needs doing. Uncertainty, risk, a gate,
        or a policy question is never an impasse: if any role knows what it
        would do, that is the answer, recorded with its basis.
    """

    determinable: bool
    owned_here: bool
    basis: str = STRONG
    reversibility: str = REVERSIBLE
    critical: bool = False
    impasse: bool = False

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
        """Whether this item waits on the human. Only a genuine impasse ever does."""

        return self.action == BLOCK_FOR_HUMAN


def route(engagement: str, item: Item) -> Routing:
    """Where ``item`` goes under ``engagement``.

    The order of the checks is the doctrine: determinable first, ownership
    second, impasse third. Only a genuine impasse reaches the human; every
    other item is decided by the run, recorded with its basis, and the run
    proceeds. Engagement only chooses how an impasse reaches the human.
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
    if item.impasse:
        if level == AUTONOMOUS:
            return Routing(
                RECORD_ESCALATION,
                "genuine impasse: no role knows what needs doing; nothing leaves the run, "
                "the report leads with it",
            )
        if level == SCHEDULED:
            return Routing(
                NOTIFY_CHANNEL,
                "genuine impasse: no role knows what needs doing; sent to the human, and "
                "only this item waits",
            )
        return Routing(
            BLOCK_FOR_HUMAN,
            "genuine impasse: no role knows what needs doing; only this item waits on the human",
        )
    if item.basis == WEAK:
        return Routing(
            RECORD_ESCALATION,
            "weak basis: decided on best judgment and recorded as an escalation; the run "
            "does not stop",
        )
    return Routing(RECORD_ASSUMPTION, "decided on a stated basis; the run does not stop")
