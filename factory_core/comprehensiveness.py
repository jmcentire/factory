"""factory_core.comprehensiveness — a deterministic, injection-resistant intake-completeness gate.

This is the generic extraction of the "is this submission complete enough to build?" gate
proven on the first consuming target. That target's implementation names its own submission
fields (problem statement, acceptance criteria, and so on) and its own thresholds. Here, none of
that vocabulary is present: the *fields*, the *thresholds*, and the *rules* are all **data** the
target supplies. This core owns only the neutral engine — the structural substance test, the
ordered rule registry, and the deterministic verdict.

This is a DIFFERENT discipline from :mod:`factory_core.completeness`, and the two are easy to
confuse. ``completeness`` answers *"is the finished work provably done?"* — a launch-readiness
lattice over an inventory of enumerated behaviors. ``comprehensiveness`` answers *"does this
INTAKE submission carry enough discrete information to proceed to build?"* — a gate over a
submission's discrete fields, run before any work exists. One gates the exit; this gates the
entrance.

The load-bearing property is **injection-resistance**, and it comes from a deliberate design
choice: every rule reads exactly ONE discrete field and asks a STRUCTURAL question — *"is this
field present and substantive?"* (length + word-token count) — never a SEMANTIC one (*"does it
say the right thing?"*). There is no rule that scans content for a keyword. Consequently:

  * an empty-of-substance field fails its rule no matter what text appears in OTHER fields
    (content elsewhere cannot satisfy a missing field);
  * a field full of adversarial / prompt-injection text that is nonetheless SUBSTANTIVE
    satisfies its presence rule — as it should: the gate judges structural completeness, not
    semantics. The real safety enforcement is mechanical and lives downstream, never in prose
    judgement here.

Because it is deterministic (same submission ⇒ same verdict, independent of any model) and
never invokes an LLM, the gate is a pure predicate — the exact opposite of asking a model
"is this comprehensive?", which a crafted submission could talk its way past.

Posture (matching the sibling modules): stdlib only, side-effect free at import (no module-level
mutable registry, no seed-on-import), no clock, no disk-reading, no target contact.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

# The verdicts. Generic factory vocabulary, not target-specific.
VERDICT_COMPREHENSIVE = "comprehensive"
VERDICT_NEEDS_INFO = "needs_info"

#: Word-like tokens: runs of letters/digits (Unicode), excluding underscore and punctuation. A
#: substance test counts these so a long string of punctuation or one mashed token cannot pass
#: on raw length alone.
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


class ComprehensivenessError(ValueError):
    """Raised on an invalid rule registration (fail closed — a dropped gate is never silent)."""


# --------------------------------------------------------------------------- #
# The structural substance test (the injection-resistance primitive)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SubstanceSpec:
    """The bar a field must clear to count as present-and-substantive.

    ``min_chars`` — minimum non-whitespace length after trimming; ``min_tokens`` — minimum count
    of word-like tokens. Both are STRUCTURAL (never semantic), which is what keeps the gate
    injection-resistant: a value can only pass by BEING substantive text, and substantive text
    in the right field is exactly the completeness the rule requires.
    """

    min_chars: int = 1
    min_tokens: int = 1


def is_substantive(value: Any, spec: SubstanceSpec) -> bool:
    """True iff ``value`` is a string with at least ``spec.min_chars`` trimmed characters AND at
    least ``spec.min_tokens`` word-like tokens. Non-strings (``None``, numbers, containers) are
    never substantive. Purely structural — no keyword/semantic inspection."""
    if not isinstance(value, str):
        return False
    trimmed = value.strip()
    if len(trimmed) < spec.min_chars:
        return False
    return len(_TOKEN.findall(trimmed)) >= spec.min_tokens


# --------------------------------------------------------------------------- #
# The result of one field-rule + the aggregate result
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class FieldGap:
    """A single unmet completeness requirement: the id of the rule that fired plus operator-
    facing guidance for what to add. Carries no field CONTENT — only the structural verdict."""

    rule_id: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"rule_id": self.rule_id, "detail": self.detail}


#: A rule is a pure function of the discrete submission fields returning a gap (unmet) or None
#: (satisfied). A rule MUST read only discrete fields and ask a structural question.
Rule = Callable[[Mapping[str, Any]], "FieldGap | None"]


@dataclass(frozen=True)
class ComprehensivenessResult:
    """The deterministic verdict: ``comprehensive`` iff no rule fired. ``gaps`` are in rule-
    registration order (deterministic), so the same submission always yields the same list."""

    verdict: str
    gaps: tuple[FieldGap, ...]

    @property
    def comprehensive(self) -> bool:
        return self.verdict == VERDICT_COMPREHENSIVE

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "gaps": [g.to_dict() for g in self.gaps]}


# --------------------------------------------------------------------------- #
# A declarative, DATA-driven rule: a substance requirement on one named field
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ConditionalSpec:
    """Select a different spec when a discrete SELECTOR field equals a controlled value.

    This is how a target expresses a threshold that varies by a discrete attribute (e.g. holding
    one kind of submission to a higher bar) WITHOUT breaking injection-resistance: the selection
    reads a discrete field and compares it (normalized) to a fixed value — it never parses free
    text. ``when_field`` is the selector field; ``equals`` is the value that activates ``spec``.
    """

    when_field: str
    equals: str
    spec: SubstanceSpec


@dataclass(frozen=True)
class SubstanceRequirement:
    """A declarative rule (the common case): field ``field`` must be present-and-substantive.

    It is DATA — a target supplies a list of these — yet it satisfies the :data:`Rule` protocol
    (it is callable), so the engine treats declarative and functional rules uniformly. It reads
    exactly ONE ``field`` for its verdict (plus, optionally, discrete selector fields to pick the
    spec), which is what preserves injection-resistance.
    """

    rule_id: str
    field: str
    detail: str = ""
    spec: SubstanceSpec = SubstanceSpec()
    conditional_specs: tuple[ConditionalSpec, ...] = ()

    def spec_for(self, submission: Mapping[str, Any]) -> SubstanceSpec:
        """The first conditional whose selector field matches, else the base spec. Selector
        comparison is normalized (str, trim, casefold) — a discrete structural lookup."""
        for cond in self.conditional_specs:
            if _norm(submission.get(cond.when_field)) == _norm(cond.equals):
                return cond.spec
        return self.spec

    def __call__(self, submission: Mapping[str, Any]) -> FieldGap | None:
        if is_substantive(submission.get(self.field), self.spec_for(submission)):
            return None
        return FieldGap(rule_id=self.rule_id, detail=self.detail)


def _norm(value: Any) -> str:
    return value.strip().casefold() if isinstance(value, str) else ""


# --------------------------------------------------------------------------- #
# The ordered rule registry (the gate itself)
# --------------------------------------------------------------------------- #

class ComprehensivenessGate:
    """An ordered registry of rules that deterministically decides comprehensive vs needs_info.

    Rules evaluate in registration order and the resulting gaps preserve that order, so the
    verdict is stable and reproducible. Registration is collision-guarded: a duplicate or empty
    rule id raises :class:`ComprehensivenessError` (a silently-dropped gate would be a hole in
    the entrance check, so it fails loudly at registration, never at evaluation).

    Instances are the unit of composition — there is no module-global mutable registry, so
    importing this module has no side effect and two gates never contaminate each other. A
    target builds its own gate from its own rules (typically :class:`SubstanceRequirement`
    data).
    """

    def __init__(self, rules: Iterable[tuple[str, Rule]] | None = None) -> None:
        self._rules: dict[str, Rule] = {}
        for rule_id, rule in rules or ():
            self.register(rule_id, rule)

    @classmethod
    def from_requirements(
        cls, requirements: Iterable[SubstanceRequirement]
    ) -> ComprehensivenessGate:
        """Build a gate from declarative substance requirements (each is its own rule)."""
        return cls((req.rule_id, req) for req in requirements)

    def register(self, rule_id: str, rule: Rule) -> None:
        """Register a rule under its id. Raises on an empty or duplicate id (fail loud)."""
        rid = rule_id.strip()
        if not rid:
            raise ComprehensivenessError("a comprehensiveness rule id must be non-empty")
        if rid in self._rules:
            raise ComprehensivenessError(f"comprehensiveness rule {rid!r} already registered")
        self._rules[rid] = rule

    def rule_ids(self) -> tuple[str, ...]:
        """The registered rule ids, in evaluation (registration) order."""
        return tuple(self._rules)

    def evaluate(self, submission: Mapping[str, Any]) -> ComprehensivenessResult:
        """Run every rule over the submission. Deterministic: gaps come out in registration
        order and the verdict is ``comprehensive`` iff no rule fired."""
        gaps: list[FieldGap] = []
        for rule in self._rules.values():
            gap = rule(submission)
            if gap is not None:
                gaps.append(gap)
        verdict = VERDICT_COMPREHENSIVE if not gaps else VERDICT_NEEDS_INFO
        return ComprehensivenessResult(verdict=verdict, gaps=tuple(gaps))
