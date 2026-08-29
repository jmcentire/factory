"""Post-run audit of defects that survive *on a PASS*, as countable closed-vocabulary codes.

``failure_classification`` covers terminal attempt failure. Run 1 did not fail that way:
it produced a PASS whose disposition was wrong about its own product, and emitted no code
at all. A defect that emits nothing is invisible to every mechanism downstream of it, so
the first requirement of a cross-run loop is that the interesting failures be *nameable*.

Three properties this module holds, each of which was a correction to an earlier design:

* **It runs after disposition, never inside it.** An agent that correctly self-identifies
  overclaim does not overclaim, so a self-assessment sited in the verdict path is the
  defendant filling in their own conviction form. The auditor is a separate read-only pass
  over artifacts that already exist, which is also the only siting under which replaying
  recorded artifacts is a meaningful test.

* **Every code is a predicate over a ratified schema, not a label for a remembered
  incident.** ``uncovered-mass-shipped`` is not the name of something that happened in
  August; it is *a territory declares uncovered, no receipt clears it, and the disposition
  is still a pass*. Predicates fire on instances nobody has imagined. Narrative labels
  ("verification followed salience") do not, and are absent here deliberately: a code that
  cannot be decided from artifacts would be decided by a model guessing, which is the prose
  input the whole design refuses.

* **Silence is distinguished from having no name for it.** ``uncoded-pass`` fires whenever a
  pass completes and nothing else did. Without it a quiet table means both "clean" and "our
  vocabulary has fallen behind", and those must never look alike.

Non-gating by construction: this module imports nothing from the promotion, verdict, or
handover *write* surfaces, and nothing in those paths may import it. That direction is the
one enforced by the reverse-dependency assert; the invariant it protects is that a code can
never acquire the power to block the thing it is describing.

Pure and stdlib-only: no clock, no disk, no I/O. The caller supplies the artifacts.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from factory_core.criticality import normalize_label
from factory_core.handover import DoneComposition, Handover
from factory_core.verdict import (
    TERRITORY_COVERED,
    TERRITORY_KIND_SCENARIO,
    TERRITORY_UNCOVERED,
    VERDICT_PASS,
    VERDICT_PASS_ON_COVERED,
    CoverageMap,
    Verdict,
    verdict_rank,
)

CODE_UNCOVERED_MASS_SHIPPED = "uncovered-mass-shipped"
CODE_VERDICT_OVERCLAIM = "verdict-overclaim"
CODE_SCOPE_UNION_GAP = "scope-union-gap"
CODE_ORACLE_FRAME_UNTESTED = "oracle-frame-untested"
CODE_UNCODED_PASS = "uncoded-pass"

#: The closed vocabulary. Order is the canonical report order and is part of the digest:
#: ``uncoded-pass`` is last because it is the residual, and a reader scanning the table
#: should meet the named defects first.
AUDIT_CODES: tuple[str, ...] = (
    CODE_UNCOVERED_MASS_SHIPPED,
    CODE_VERDICT_OVERCLAIM,
    CODE_SCOPE_UNION_GAP,
    CODE_ORACLE_FRAME_UNTESTED,
    CODE_UNCODED_PASS,
)

#: The predicate each code stands for, in one line, carried into the digest so that
#: *redefining* a code is as digest-visible as adding or retiring one. A boundary that
#: moves without the digest moving is how two incommensurable counts get summed.
_VOCABULARY: tuple[tuple[str, str], ...] = (
    (
        CODE_UNCOVERED_MASS_SHIPPED,
        "a territory declares uncovered, no receipt clears it, and the disposition is a pass",
    ),
    (
        CODE_VERDICT_OVERCLAIM,
        "the recorded promotion disposition outranks the computed verdict disposition",
    ),
    (
        CODE_SCOPE_UNION_GAP,
        "a verb missing from the done-composition was assumed in scope by another seat",
    ),
    (
        CODE_ORACLE_FRAME_UNTESTED,
        "no scenario territory is covered, so nothing exercises the product's own sentence",
    ),
    (
        CODE_UNCODED_PASS,
        "a pass completed and no other code in this vocabulary fired",
    ),
)

#: Dispositions this vocabulary applies to. A blocked or incomplete run already carries an
#: attributable failure path; re-describing it here would double-count the disposition the
#: factory reports anyway, and would let one loud non-pass run dominate the table.
_PASS_FAMILY = frozenset({VERDICT_PASS, VERDICT_PASS_ON_COVERED})


class AuditError(ValueError):
    """Raised when an artifact cannot be audited, rather than audited approximately."""


def vocabulary_digest() -> str:
    """Content digest of the vocabulary definition.

    Every query against the store groups by this before summing. Without it a later
    vocabulary change silently blends rows that mean different things — the same defect as
    pooling A/B arms, and it produces a number that looks like evidence.
    """

    body = json.dumps(_VOCABULARY, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuditReport:
    """One run's audit. Codes only — no prose, no summaries, nothing model-controlled."""

    run_id: str
    codes: tuple[str, ...]
    vocab_digest: str

    def rows(self) -> tuple[dict[str, Any], ...]:
        """Store rows for this run, one per fired code.

        Deliberately minimal. Configuration digests and integrity binding belong to the
        store layer, not here: the auditor has no business knowing about qualification
        bindings, and keeping it ignorant is what lets it stay a pure function.
        """

        return tuple(
            {
                "run_id": self.run_id,
                "code": code,
                "count": 1,
                "vocab_digest": self.vocab_digest,
            }
            for code in self.codes
        )


def _labels(values: Iterable[str]) -> set[str]:
    return {normalize_label(value) for value in values if normalize_label(value)}


def _uncovered_mass_shipped(coverage: CoverageMap, verdict: Verdict) -> bool:
    """A named gap carried into a pass.

    This is not a hidden state — ``verdict.py`` forces PASS-ON-COVERED whenever unknown
    territory remains, so the factory already discloses it. Counting it anyway is the
    point: run 1's disclosure was a prediction priced as a disclaimer, and a disclosure
    nobody tallies is one nobody acts on.
    """

    receipted = _labels(verdict.receipted_territory_ids)
    return any(
        territory.status == TERRITORY_UNCOVERED
        and normalize_label(territory.territory_id) not in receipted
        for territory in coverage.territories
    )


def _verdict_overclaim(verdict: Verdict) -> bool:
    """The recorded claim asserts more than the computation supports.

    Mechanically decidable because both dispositions are on one total order. This is the
    generative form of run 1's failure: it fires for any future disagreement between what
    was computed and what was carried forward, not only for the pair seen in August.
    """

    return verdict_rank(verdict.promotion_disposition) > verdict_rank(verdict.disposition)


def _scope_union_gap(composition: DoneComposition, handovers: Sequence[Handover]) -> bool:
    """A verb nobody delivered that at least one seat believed someone else owned.

    The code names the *shared silent omission*, not incompleteness in general. A verb
    simply left undone is an ordinary gap and gets no code — conflating the two would fire
    on every unfinished run and measure nothing.
    """

    missing = _labels(composition.missing_verbs)
    if not missing:
        return False
    assumed: set[str] = set()
    for record in handovers:
        if record.retracts:
            continue
        assumed |= _labels(record.scope.assumed_in_scope_by_others)
    return bool(missing & assumed)


def _oracle_frame_untested(coverage: CoverageMap) -> bool:
    """Nothing covered exercises the product's own one-sentence purpose.

    Run 1 shipped with zero tests of "a reservation drifts; someone is told" while its
    oracle and surface territories were green. Scenario coverage is the frame check this
    predicate stands in for.
    """

    return not any(
        territory.kind == TERRITORY_KIND_SCENARIO and territory.status == TERRITORY_COVERED
        for territory in coverage.territories
    )


def audit_run(
    *,
    run_id: str,
    coverage: CoverageMap,
    verdict: Verdict,
    composition: DoneComposition,
    handovers: Sequence[Handover] = (),
) -> AuditReport:
    """Audit one completed run and return its codes.

    Read-only over every argument. Fails closed on an unrecognised disposition rather than
    skipping the run: a run that cannot be audited must be visible as an error, because a
    silent skip is indistinguishable from a clean result in the store.
    """

    run_key = normalize_label(run_id)
    if not run_key:
        raise AuditError("audit requires a run_id")

    # verdict_rank refuses unknown dispositions; call it on both before any predicate so a
    # malformed artifact cannot produce a partial audit.
    disposition_rank = verdict_rank(verdict.disposition)
    verdict_rank(verdict.promotion_disposition)
    del disposition_rank

    if verdict.disposition not in _PASS_FAMILY:
        return AuditReport(run_id=run_key, codes=(), vocab_digest=vocabulary_digest())

    fired: list[str] = []
    if _uncovered_mass_shipped(coverage, verdict):
        fired.append(CODE_UNCOVERED_MASS_SHIPPED)
    if _verdict_overclaim(verdict):
        fired.append(CODE_VERDICT_OVERCLAIM)
    if _scope_union_gap(composition, handovers):
        fired.append(CODE_SCOPE_UNION_GAP)
    if _oracle_frame_untested(coverage):
        fired.append(CODE_ORACLE_FRAME_UNTESTED)
    if not fired:
        fired.append(CODE_UNCODED_PASS)

    ordered = tuple(code for code in AUDIT_CODES if code in fired)
    return AuditReport(run_id=run_key, codes=ordered, vocab_digest=vocabulary_digest())


def frequency_table(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Counts with their denominators attached, grouped by vocabulary digest.

    There is no minimum-N gate and there must not be one. Withholding a count until it is
    "significant" starves the runs that would produce significance, and hides the signal
    during exactly the window when acting on it is cheapest. The honest control is the
    denominator: ``k firings across n runs`` is a fact at n=1. A rank with the n stripped
    off is what turns two observations into the costume of a measurement, so this never
    emits one.
    """

    by_digest: dict[str, dict[str, set[str]]] = {}
    runs_by_digest: dict[str, set[str]] = {}
    for row in rows:
        digest = str(row.get("vocab_digest", ""))
        code = str(row.get("code", ""))
        run = str(row.get("run_id", ""))
        if not digest or code not in AUDIT_CODES or not run:
            raise AuditError(f"refusing to count a malformed row: {dict(row)!r}")
        by_digest.setdefault(digest, {}).setdefault(code, set()).add(run)
        runs_by_digest.setdefault(digest, set()).add(run)

    table: list[dict[str, Any]] = []
    for digest in sorted(by_digest):
        total_runs = len(runs_by_digest[digest])
        for code in AUDIT_CODES:
            runs = by_digest[digest].get(code)
            if not runs:
                continue
            table.append(
                {
                    "vocab_digest": digest,
                    "code": code,
                    "firings": len(runs),
                    "runs_observed": total_runs,
                    "label": f"{code} — {len(runs)} firings across {total_runs} runs",
                }
            )
    return tuple(table)
