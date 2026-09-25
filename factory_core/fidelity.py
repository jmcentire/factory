"""Did we build the thing that was asked for, and do we admit it when we didn't.

The invariant this module upholds: **a lane's own judgment about its own work is
an input to the verdict, not prose in a summary — and a deliverable nobody built
cannot be passed over in silence.**

Three failures motivated it, all founder-reported, all the same missing wire.

**"It knows it's shit and delivers it anyway"** (2026-09-24). The belief was
always there; it was rendered into a sentence and dropped on the floor. Nothing
read it, so nothing could act on it. Every other input to a verdict — a digest,
a receipt, an exit code — is a value that flows into a decision. This one was
narrative. So the objective the lane *was* wired to (gates green, then done) was
satisfiable while the judgment was ignored, and it was ignored. The corrective is
not an exhortation to be honest: it is a required, typed field, on an artifact
that is invalid without it, which a gate reads.

**"Nine out of ten are PASS_WITH_RISK_ACCEPTANCE"** (2026-09-24). Look at the
exits a lane actually has. BLOCK is honest, terminal, and reads as failure. PASS
is a lie when the work is bad. PASS_WITH_RISK_ACCEPTANCE is honest, terminal,
*and* reads as success — the only door that is both truthful and open. It became
universal because it was the lowest-energy state satisfying "terminate" and
"don't lie", not because anyone abused it. Two changes follow: risk acceptance
requires a **costed alternative** and is refused outright when the fix is bounded
(the founder's words: "not everything is rearchitect the entire company or
abandon this ticket; the vast majority are accomplishable"), and a new
non-terminal honest exit exists — ``REWORK`` — so admitting a defect no longer
means ending the run in apparent failure.

**"Two and a half days of everything BUT what I asked"** (2026-09-24: a
distribution service that gained availability changes, pricing work and new
internal APIs, and zero code to send what we have to a channel). This is the
worst of the three and the cheapest to catch. ``verdict.py`` already forces the
first line — *does it do the thing it was built to do?* — but "the thing" was
never bound to the request, so a lane could answer YES about whatever it chose to
build. Here the request is **enumerated**, every artifact **names the deliverable
it serves**, and a deliverable with no artifact is ``NOT_ATTEMPTED``. That fires
on day one, not on day three.

Posture, matching ``verdict.py``: stdlib only, pure, no clock, no disk. Summary
prose, confidence language and rationale are not inputs here and cannot move the
outcome. The caller supplies the enumeration and the attributions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

# ---------------------------------------------------------------------------
# What a lane may say about one deliverable.
# ---------------------------------------------------------------------------

#: Built, and it does what the cited request asked for.
DELIVERED: Final = "delivered"
#: Built, but it does something other than what was asked.
DIVERGES: Final = "diverges"
#: Some of the asked-for behaviour is present and some is not.
PARTIAL: Final = "partial"
#: Nothing was built for this. The 2.5-day failure lives here.
NOT_ATTEMPTED: Final = "not-attempted"

DISPOSITIONS: Final[tuple[str, ...]] = (DELIVERED, DIVERGES, PARTIAL, NOT_ATTEMPTED)

#: Dispositions that leave the promise unkept. Named rather than derived by
#: negation, so adding a disposition forces a decision about which side it is on.
SHORTFALLS: Final[frozenset[str]] = frozenset({DIVERGES, PARTIAL, NOT_ATTEMPTED})


# ---------------------------------------------------------------------------
# How expensive the remedy is. This is what makes risk acceptance rare.
# ---------------------------------------------------------------------------

#: The fix fits inside this ticket: hours or days, no boundary moves. Risk
#: acceptance is NOT available here — the run reworks instead.
BOUNDED: Final = "bounded"
#: The remedy is abandoning the ticket or moving an architectural boundary.
#: This is the only shape risk acceptance was ever meant for.
STRUCTURAL: Final = "structural"

FIX_SCOPES: Final[tuple[str, ...]] = (BOUNDED, STRUCTURAL)


# ---------------------------------------------------------------------------
# What the run is allowed to do next.
# ---------------------------------------------------------------------------

#: Every deliverable is delivered. The only disposition that may reach done.
FIDELITY_DELIVERS: Final = "delivers"
#: A shortfall whose fix is bounded. Non-terminal, honest, not a failure: keep
#: working. This is the exit whose absence made risk acceptance load-bearing.
FIDELITY_REWORK: Final = "rework"
#: Every shortfall is structural. The human decides; the run does not.
FIDELITY_RISK_ACCEPTANCE_ELIGIBLE: Final = "risk-acceptance-eligible"
#: The enumeration and the work disagree: something unasked-for was built, or a
#: deliverable has no work attributed to it at all.
FIDELITY_SCOPE_DRIFT: Final = "scope-drift"
#: A deliverable carries no assessment. Fail closed: silence is not delivery.
FIDELITY_UNASSESSED: Final = "unassessed"


class FidelityError(ValueError):
    """A value the factory does not define, or a self-report it cannot accept."""


def _require(value: str, allowed: tuple[str, ...], field: str) -> str:
    text = str(value).strip().lower()
    if text not in allowed:
        raise FidelityError(f"unknown {field} {value!r}; use one of {', '.join(allowed)}")
    return text


@dataclass(frozen=True)
class Deliverable:
    """One thing the request asked for, enumerated before work begins.

    ``directive_ref`` is the citation — the addressed utterance, ticket item, or
    ratified specification clause this came from. It is mandatory because a
    deliverable nobody can trace to a request is the lane's own idea, and the
    whole point is to tell those apart.
    """

    deliverable_id: str
    directive_ref: str
    summary: str

    def __post_init__(self) -> None:
        # Checked field by field rather than through getattr: a dynamic attribute
        # reference is exactly what the wiring guard refuses to resolve, and it is
        # right to — a control whose own references cannot be traced is a control
        # nobody can audit.
        if not str(self.deliverable_id).strip():
            raise FidelityError("a deliverable needs a non-empty deliverable_id")
        if not str(self.directive_ref).strip():
            raise FidelityError(
                "a deliverable needs a non-empty directive_ref: cite the utterance, "
                "ticket item, or ratified clause it came from, or it is the lane's own idea"
            )
        if not str(self.summary).strip():
            raise FidelityError("a deliverable needs a non-empty summary")


@dataclass(frozen=True)
class Assessment:
    """A lane's own verdict on its own work against one deliverable.

    This is the field whose absence let "it knows it's shit" evaporate. It is
    typed, required, and read by a gate.

    ``divergence`` and ``fix_scope`` are mandatory for every shortfall: a lane
    that cannot say *what* is wrong and *how big the remedy is* has not actually
    assessed anything, and an uncosted shortfall is precisely how risk
    acceptance became a shrug.
    """

    deliverable_id: str
    disposition: str
    divergence: str = ""
    fix_scope: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "disposition", _require(self.disposition, DISPOSITIONS, "disposition")
        )
        if self.disposition == DELIVERED:
            if self.fix_scope:
                raise FidelityError(
                    "a delivered deliverable has nothing to fix; drop the fix_scope"
                )
            return
        if not str(self.divergence).strip():
            raise FidelityError(
                f"disposition {self.disposition!r} must name the divergence: what does the "
                "work do that the request did not ask for, or fail to do that it did"
            )
        object.__setattr__(
            self, "fix_scope", _require(self.fix_scope, FIX_SCOPES, "fix_scope")
        )


@dataclass(frozen=True)
class Attribution:
    """A built artifact and the deliverable it serves.

    ``deliverable_id`` may be empty, and that is the point: unattributed work is
    recorded as such rather than quietly counted as progress.
    """

    artifact_ref: str
    deliverable_id: str = ""

    def __post_init__(self) -> None:
        if not str(self.artifact_ref).strip():
            raise FidelityError("an attribution needs a non-empty artifact_ref")


@dataclass(frozen=True)
class Fidelity:
    """Where the run stands against what was asked, and why."""

    disposition: str
    reason: str
    unserved: tuple[str, ...] = ()
    unasked: tuple[str, ...] = ()
    shortfalls: tuple[str, ...] = ()

    @property
    def may_reach_done(self) -> bool:
        """Only a clean delivery may reach done.

        Risk acceptance deliberately does NOT pass here. It is an eligibility to
        put the decision in front of a human, never a self-issued permission to
        ship — which is what it had degenerated into.
        """

        return self.disposition == FIDELITY_DELIVERS


def _unserved_deliverables(
    deliverables: Iterable[Deliverable], attributions: Iterable[Attribution]
) -> tuple[str, ...]:
    """Deliverables with no artifact attributed to them, in enumeration order.

    This is the distribution-service failure reduced to a computation: two and a
    half days of availability, pricing and API work, and nothing attributed to
    "send what we have to a channel". No amount of adjacent output clears it.
    """

    served = {a.deliverable_id for a in attributions if a.deliverable_id}
    return tuple(d.deliverable_id for d in deliverables if d.deliverable_id not in served)


def _unasked_artifacts(
    deliverables: Iterable[Deliverable], attributions: Iterable[Attribution]
) -> tuple[str, ...]:
    """Artifacts serving no enumerated deliverable.

    Either the work was not asked for, or the enumeration is incomplete. Both are
    the human's call, and both are invisible without this list.
    """

    known = {d.deliverable_id for d in deliverables}
    return tuple(
        a.artifact_ref
        for a in attributions
        if not a.deliverable_id or a.deliverable_id not in known
    )


def risk_acceptance_available(assessments: Iterable[Assessment]) -> bool:
    """Whether risk acceptance is even on the table.

    It is available only when every shortfall is ``STRUCTURAL`` — abandon the
    ticket or move a boundary. One bounded fix removes the option entirely,
    because a bounded fix is work, and work is what the run is for.
    """

    shortfalls = [a for a in assessments if a.disposition in SHORTFALLS]
    if not shortfalls:
        return False
    return all(a.fix_scope == STRUCTURAL for a in shortfalls)


def render(
    deliverables: Sequence[Deliverable],
    assessments: Iterable[Assessment],
    attributions: Iterable[Attribution] = (),
) -> Fidelity:
    """What the run may do next, from the enumeration and the self-reports alone.

    Order of the checks is the doctrine, strongest objection first:

    1. **Nothing asked for is missing, and nothing unasked-for was built.** A
       structural mismatch between request and work outranks any judgment about
       quality, because it means the lanes and the human are discussing
       different projects.
    2. **Every deliverable carries an assessment.** Absence fails closed; a lane
       that says nothing has not said yes.
    3. **A deliverable a lane claims to have delivered has work behind it.** No
       self-certifying something into existence.
    4. **A bounded shortfall means rework, not risk acceptance.**
    5. Only then may risk acceptance be offered to the human.
    """

    enumerated = list(deliverables)
    if not enumerated:
        raise FidelityError(
            "fidelity needs an enumerated request; an empty enumeration would make any "
            "work vacuously faithful, which is the failure this module exists to catch"
        )

    attributed = list(attributions)
    by_id: Mapping[str, Assessment] = {a.deliverable_id: a for a in assessments}

    unserved = _unserved_deliverables(enumerated, attributed)
    unasked = _unasked_artifacts(enumerated, attributed)
    if unserved or unasked:
        parts = []
        if unserved:
            parts.append(f"asked for and not built: {', '.join(unserved)}")
        if unasked:
            parts.append(f"built and not asked for: {', '.join(unasked)}")
        return Fidelity(
            FIDELITY_SCOPE_DRIFT,
            "; ".join(parts),
            unserved=unserved,
            unasked=unasked,
        )

    missing = tuple(d.deliverable_id for d in enumerated if d.deliverable_id not in by_id)
    if missing:
        return Fidelity(
            FIDELITY_UNASSESSED,
            f"no self-assessment for: {', '.join(missing)}; silence is not delivery",
        )

    shortfalls = tuple(
        d.deliverable_id for d in enumerated if by_id[d.deliverable_id].disposition in SHORTFALLS
    )
    if not shortfalls:
        return Fidelity(FIDELITY_DELIVERS, "every enumerated deliverable is delivered")

    reported = [by_id[i] for i in shortfalls]
    if any(a.fix_scope == BOUNDED for a in reported):
        bounded = tuple(a.deliverable_id for a in reported if a.fix_scope == BOUNDED)
        return Fidelity(
            FIDELITY_REWORK,
            "the fix is bounded, so it is work rather than a risk to accept: "
            f"{', '.join(bounded)}",
            shortfalls=shortfalls,
        )
    return Fidelity(
        FIDELITY_RISK_ACCEPTANCE_ELIGIBLE,
        "every shortfall needs a boundary moved or the ticket abandoned; the human decides",
        shortfalls=shortfalls,
    )


#: Above this share of verdicts, risk acceptance has stopped being exceptional.
#: The founder's observation sets the shape: "when 9 out of 10 are
#: PASS_WITH_RISK_ACCEPTANCE then we're using it as a get out of jail free card."
DEFAULT_RISK_ACCEPTANCE_CEILING: Final = 0.25


def risk_acceptance_is_systematic(
    dispositions: Sequence[str], ceiling: float = DEFAULT_RISK_ACCEPTANCE_CEILING
) -> bool:
    """Whether risk acceptance has become the norm rather than the exception.

    This is the O-ring rail from the production-build doctrine, pointed at the
    factory's own verdicts: a baseline learned while chronically broken rounds a
    real outage down to ordinary. When most runs end in accepted risk, a genuine
    "this needs a rearchitect" is indistinguishable from routine, and the rate
    itself is the signal — not any individual run, each of which may be locally
    valid. Escalate the systematic.
    """

    if not dispositions:
        return False
    accepted = sum(1 for d in dispositions if d == FIDELITY_RISK_ACCEPTANCE_ELIGIBLE)
    return accepted / len(dispositions) > ceiling
