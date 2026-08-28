# Dogfood Run 1 — Agreement Packet (A1–A4 draft, pending operator ratification)

Target: `scripts/check_wiring.py` — the wiring-audit gate from the run-2 strategy
(§7 gate 4), built *by* a factory run so the run exercises A1–A4, the dark-run
taxonomy, and the report format on a real workload while manufacturing a gate the
next run inherits. Drafted 2026-08-27; nothing below is authoritative until the
operator ratifies each agreement individually.

## A1 — Purpose sentence (requires active ratification with adversarial probe)

**Proposed:** "Every service the build provides is reachable from a declared
entrypoint; a zero-caller export introduced by new code turns the gate red before
merge."

Adversarial alternatives the operator must actively REJECT (per the ratified
purpose-sentence governance — passive ratification is not ratification):

- **Alt A:** "The wiring audit reports unreachable exports for human review."
  (Plausible and wrong: a report is not a gate — run 1's corpus replay was a signal
  nobody was forced to read. If this is the sentence, the tool is decorative by
  construction.)
- **Alt B:** "Every module in the codebase is imported somewhere."
  (Plausible and wrong: import-reachability is not entrypoint-reachability — a test
  importing a dead service masks exactly the run-1 FindingService case. If this is
  the sentence, the gate certifies the masking.)

## A2 — Build agreement (scope, non-goals, inheritance)

- Deliverable: `scripts/check_wiring.py` (stdlib-only, fail-closed) + a `make
  check-wiring` target wired into `make ship`, + `wiring_baseline.json` (data file
  of justified pre-existing exceptions, empty-preferred, same pattern as
  `core_purity_baseline.json`).
- Definitions: *entrypoints* are `factory_runtime/cli.py`, `scripts/*`, `harness/*`;
  *tests are not entrypoints* (a symbol reachable only from tests is dead wiring —
  that is the point). A *provided service* is a public (non-underscore) module-level
  class or function in `factory_core`/`factory_runtime`.
- Non-goals: cross-repo targets; dynamic dispatch resolution beyond static
  import/attribute analysis; runtime instrumentation. (A statically undecidable
  reference is a *finding to classify*, never a silent pass.)
- Artifact inheritance: none — fresh code, run-2 gates from birth.

## A3 — Architecture alignment

- Static analysis via stdlib `ast` over the two package trees; no new dependency
  (purity allowlist untouched).
- Reachability = transitive closure from entrypoint modules over imports and
  attribute references; anything unresolvable statically is reported as
  `unresolved-reference` (fail-closed classification, not a skip).
- Output: typed findings (`zero-caller-export`, `unreachable-module`,
  `unresolved-reference`, `parse-failure`) with file:symbol coordinates; exit
  non-zero on any finding not in the baseline.
- Executor topology: plain `python3` invocation from make — identical in CI and
  local (no parity gap to enumerate beyond that).

## A4 — Test plan (closes only with the does-the-thing scenario present)

Coverage map territories (each content-addressed at ratification):

1. **Scenario `wiring-red-on-seeded-dead-export`** (the does-the-thing scenario,
   red-before/green-after): inject a synthetic zero-caller public function into a
   copy of `factory_core`; the gate must go red naming that exact symbol; remove
   it; the gate must go green. Doneness for this run IS this scenario.
2. **Scenario `test-import-does-not-mask`**: the seeded dead export is imported by
   a test file; the gate must STILL go red (tests are not entrypoints).
3. **Scenario `fail-closed-on-parse-failure`** (failure-injection variant): a
   syntactically invalid file in the tree turns the gate red with `parse-failure`,
   never a skip. Silence detection is part of the verb.
4. **Oracle `baseline-is-data`**: baseline entries suppress named pre-existing
   findings only; an empty baseline plus a clean tree exits zero.
5. **Uncovered territory (declared, characterization procedure named)**: dynamic
   `getattr`-style references — declared uncovered at ratification; the ratified
   resolution procedure is the `unresolved-reference` finding class (conservative,
   fail-closed), with a characterization receipt required before any
   PASS_WITH_RISK_ACCEPTANCE over that territory.

Ratified verbs (`verb_ids` for the scope-union check): `detect-dead-export`,
`resolve-reachability`, `enforce-at-ship`.

Attempt ceiling: 3 per lane. Escalation predicate additions: any change to the
purity allowlist, `make ship` ordering, or scenario 1's expected outcome.

## Run mechanics

Validator drafts dispatches from this packet; Coder and Tester lanes run blind per
existing lane projections; the verdict is computed by `factory_core.verdict`
(coverage map above), `__DONE__` only via `compose_done` over the three verbs. The
frame-check seat drives scenario 1 against the exact built artifact digest before
any disposition.
