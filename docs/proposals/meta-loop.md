# The meta-loop — cross-run failure telemetry for the factory

Status: **proposal, not ratified.** Draft v1, 2026-08-28.
Panel-reviewed: Simulacrum, Advocate (6 personas, 49 findings), Constrain (challenge harvest only;
synthesis artifacts rejected per the standing protocol — 8th reproduction of its invention defect).
All three lenses billed to `WANDER_ANTHROPIC_API_KEY`. Panel cost: $0.31 (Advocate) + Sim + Constrain.

---

## 0. What changed from draft v0, and why

Five structural corrections came out of the panel. They are listed first because they are the
substance of this document; the workstreams below are what remains after applying them.

| # | Correction | Raised by |
|---|---|---|
| C1 | **Emitters move out of the verdict path.** v0 emitted verdict-scope codes from `verdict.py` / `handover.py:compose_done` at PASS-mint time. That asks the machinery to self-assess its own overclaim at the moment of overclaiming — an agent that correctly self-identifies overclaim does not overclaim. It also violates the same non-weakening principle v0 cited from `triage.py`, and it makes the recorded-artifact replay test inexecutable. Replaced by a structurally independent post-run **auditor**. | Advocate/SME (critical), Constrain (independently) |
| C2 | **The vocabulary must be contract-derived, not narrative-derived.** v0's codes were induced from the run-1 post-mortem — a perfect detector of run 1 and structurally blind to everything else. The load-bearing condition in the CABP result was not "a vocabulary"; it was a vocabulary derived from a *contract*, which is why it had generative coverage. Codes are now predicates over existing schemas, narrative-only codes are dropped, and an `uncoded-pass` escape hatch makes vocabulary lag visible. | Sim, Advocate/SME + Good Friend + Adversarial + Sage (critical/high) |
| C3 | **Sequencing inverted; the kill condition moves before the sunk cost.** v0 ordered W1→W2→W3→W4 with the kill condition after W1 was already built. The W2 acceptance test is now step zero, written before any implementation, and it carries a **negative probe** — without one, a code that fires on every PASS passes the positive test perfectly. | Advocate/Good Friend (high), Constrain |
| C4 | **Every number carries its denominator. There is no minimum-N gate.** Draft v1 had one — the analyzer withheld any proposal until a code had fired in five distinct runs — and it was wrong for the same reason "wait until we have N paying customers before building anything" is wrong: *the mechanism that produces N is the thing being gated.* At N=2 with a gate at 5, the report stays silent through runs 3 and 4, which is precisely the window where knowledge is lowest and course correction is cheapest. The defect the gate was meant to prevent — an anecdote wearing the costume of a measurement — is prevented by the **label**, not by the silence. `verdict-overclaim: 2 firings / 2 runs` is honest. `verdict-overclaim, ranked #1` with the denominator stripped is not. This is the O-ring rule from the operator's own playbook applied here: a threshold that hides sub-threshold data *is* the normalized baseline, and the quiet reads as fine. | Founder correction, 2026-08-28 (overriding Advocate/SME + Good Friend + Sage; restoring Sim) |
| C5 | **Non-gating becomes a hard invariant with one expensive door.** v0 left it a design choice. The threat is not bad gating; it is *gradual* coupling with no single step where anyone lied — the exact shape of the run-1 `__DONE__` inflation. Now enforced by a two-layer denial probe, with the meta store's data channel converted back into a module coupling the existing guard already covers. | Constrain |

---

## 1. What transfers from CABP, stated precisely

A separate experiment (BYOA/CABP) showed an LLM optimizer diagnosing a structural API defect and
proposing a corrected policy document **from an aggregate error-code frequency table alone**,
never reading a transcript.

**What transfers:** counts-of-codes are a better optimizer input than prose.

**What does not transfer, and must not be laundered across:**

- CABP's vocabulary was derived from an **API contract** — an external, stable artifact — which is
  what gave it coverage beyond the failures already observed. A vocabulary induced from one
  post-mortem has no such property. This is the single most important disanalogy and it dictates
  C2.
- CABP had sufficient N. The factory has **N=2**.
- CABP operated under a differential-privacy obligation across many principals. The factory has a
  single operator who owns all the data.

**Stochastic resonance does not transfer at all.** SR requires a threshold, many independent
trials, and noise you are *forced* to add. Injecting noise into a two-sample frequency table
corrupts it. What survives is the weaker true thing: accumulate persistent sub-threshold modes
across runs so a mode crosses by accumulation rather than by spiking once.

---

## 2. What the factory already has (read from the tree, not assumed)

- `harness/postmortem.py` (198 lines) — derives a per-run postmortem from primary sources only;
  prints `UNDERIVED` rather than estimating; detects silent clears of the attention channel.
- Four per-agent feedback sections already reserved at
  `.factory/runs/<run>/feedback/{validator,coder,tester,orchestrator}.md`, with three fixed
  questions.
- `factory_runtime/failure_classification.py` — `FailureCapsule(owner, code, summary)` over a
  closed vocabulary of nine codes: `runner-invocation-timeout`, `runner-invocation-output-limit`,
  `caller-missing-terminal-report`, `validator-caller-exception`,
  `validator-acceptance-not-recorded`, `tester-receipt-missing`, `coder-receipt-missing`,
  `validator-caller-nonzero-exit`, `candidate-failed-acceptance`. It exists explicitly so raw lane
  prose never becomes authority.
- `factory_core/triage.py` — an agent evaluating an alert may not delete, weaken, or silence the
  monitor that produced it.
- `factory_core/qualification.py` — binds model, runner, role-contract digest, tool-schema digest,
  and effective-directive-contract digest; any drift invalidates every prior result.
- `scripts/check_core_purity.py` — import scan **plus reverse-dependency assert**, fail-closed in
  `make ship`. This is the mechanism §6 reuses.

## 3. The three gaps

**G1 — Not wired.** Nothing invokes `postmortem.py`; only `harness/README.md:43` documents the
invocation. `endgame.sh` terminates at `verdict.json`. Feedback collection is a prose instruction
at `prompts/validate.md:571` addressed to the Coder and Tester only, though four sections are
reserved. In batch0 the question was asked after the endgame; both lanes hit their spend limit
mid-answer and that data is permanently unrecoverable.

**G2 — No cross-run store.** Each postmortem is standalone. Nothing accumulates. Lesson extraction
is the operator reading one document and hand-writing doctrine.

**G3 — The failures that matter are uncountable.** `FailureCapsule` fires only on *terminal attempt
failure*. Run 1's actual failure — verdict overclaim, `__DONE__` inflation, shipping a
declared-uncovered mass — produced a **PASS**. It emitted no code. A frequency table built today
would count nine flavors of infrastructure crash and zero instances of the thing that broke the
factory.

---

## 4. The vocabulary (C2)

**Dropped from v1** — not decidable from artifacts, therefore decidable only by a model guessing,
which is the prose input the whole design forbids:
`salience-driven-verification`, `mutation-kill-overread`. These are post-hoc stories about why
people behaved as they did. They may return if someone finds a mechanical predicate for them.

**Retained, restated as predicates over existing schemas** — each fires on any future instance,
not only on a remembered one:

| Code | Predicate | Anchored to |
|---|---|---|
| `uncovered-mass-shipped` | a coverage class is declared OPEN **and** the verdict is PASS | coverage-map schema |
| `scope-union-gap` | the handover scope-union covers the ratified verb set only by composition of narrower local scopes | handover schema |
| `verdict-overclaim` | disposition asserted above what the ratified adequacy criteria support | `verdict.py` adequacy criteria |
| `oracle-frame-untested` | no acceptance-catalog scenario exercises the product's ratified purpose statement | acceptance catalog |

**The escape hatch** — the cheapest and most important entry:

| `uncoded-pass` | a PASS completed and no other code fired |
|---|---|

Without it, silence in the store is indistinguishable between *nothing went wrong* and *we have no
name for what went wrong*. A rising `uncoded-pass` rate is the signal that the vocabulary is
lagging the failure space. It is reported first, not buried.

New codes are authored **by a human, through the Diff-Intent Gate** — never by the analyzer. Any
vocabulary change bumps the vocabulary digest.

**Retirement is not deletion.** Retiring a code that the operator judges to be misfiring is
structurally what `triage.py` forbids: evaluating an alert and silencing the monitor that produced
it. The cheapest path to a quiet table is always retirement, and *misfiring* and *correctly
detecting something expensive* present identically to the person who would have to do the work.
So: rows are never deleted or recounted; retirement bumps the vocabulary digest, and the
retirement is written into the store as a **first-class row with a reason**, never as an absence.
A table that gets quieter over time must not be able to read as progress.

---

## 5. Workstreams, in execution order

### Step 0 — the W2 acceptance test (written before any implementation)

A replay harness that feeds run-1's recorded verdict, coverage map, and handover scope-union to a
not-yet-existing auditor interface.

- **Positive:** `verdict-overclaim` and `uncovered-mass-shipped` fire.
- **Negative (required):** an artifact set that should stay silent produces silence. This proves
  the code discriminates rather than always firing on any PASS.

**Kill condition sits here.** If the auditor cannot be made to discriminate on recorded artifacts,
nothing downstream is built. No sunk cost precedes this gate.

### W2 — the auditor

A structurally independent post-run component in the `postmortem.py` position. Reads three
already-existing structured artifacts **read-only** — verdict, coverage map, handover scope-union —
and emits codes by comparing them. No write path to any of them; no path by which its output can
suppress a future emission. Judged against the Step 0 harness.

*Qualification:* because it runs after disposition and cannot influence the verdict of the run it
audits, it is not in that run's qualification path and needs no role qualification. That changes
the instant its output gates anything — see §6.

### W1 — wire the existing loop

- `endgame.sh` invokes `postmortem.py` after writing `verdict.json`.
- Feedback collection extends to all four lanes and moves before endgame.
- **Non-fatal by construction:** the postmortem's exit code is captured separately into a
  `postmortem-status.json` (success/failed/skipped). A meta-layer failure must never roll back,
  fail, or ambiguate the run's canonical verdict record.
- *Acceptance:* a run produces `postmortem.md` with four collected sections, zero `UNCOLLECTED`,
  and a status artifact; an induced postmortem failure leaves `verdict.json` intact and
  `endgame.sh` green.

Deliberately **after** W2: wiring a postmortem that emits nothing worth counting is premature.
Note also that the collected feedback is agent self-report — testimony from witnesses with a
structural interest in not fully reporting the crime. It is context for the operator, never
evidence, and never an analyzer input.

### W3 — the meta store

`.factory/meta/codes.jsonl`, append-only. Strictly after W2 is proven: a store populated by an
unvalidated emitter is a precise count of the wrong thing, and being append-only there is no clean
way to disown those rows later.

Row shape — no free text, no summaries, nothing model-controlled:

- `run_id` (binds the row; prevents replay count inflation)
- `code` — validated against the vocabulary enum at write time; an unrecognized value is a write
  error, not a row
- `vocab_digest` — hash of the canonical vocabulary definition. **Every query groups by this before
  summing.** Without it, a vocabulary change silently blends incommensurable counts.
- the configuration digests already required by `qualification.py`
- `count`

*Acceptance:* two runs produce two distinct row sets; a third-party reader computes a frequency
table with `jq` alone, grouping by `vocab_digest`, with no model in the loop.

### W4 — the analyzer

**No minimum-N gate** (C4). The analyzer reports whatever the store holds, from the first run
onward, and every number carries its denominator: `code — k firings across n runs`. A count is
never rendered as a rank, a share, or an ordering with the *n* stripped off, because that is the
transformation that turns two observations into the costume of a measurement. Confidence language
is derived from *n* at render time; it is never used to suppress the row.

The withheld-until-N design was the failure it meant to prevent. A code firing 2/2 is exactly the
signal worth seeing while course correction is still nearly free; a gate at five hides it through
runs three and four and surfaces it once it is expensive. And the gate throttles its own input —
the runs that produce N are the runs it silences.

*On the "reads only counts and doctrine" constraint* — v0 stated this incoherently, since doctrine
**is** prose (Advocate/Adversarial, high). The correct statement: the analyzer reads counts plus
the doctrine surface it proposes to change, and **never reads any artifact produced by a run** —
no `feedback/*.md`, no `postmortem.md`, no transcripts. Doctrine is a stable human-ratified input;
run-derived prose is the untrusted evidence. That is the line.

*And it must be structural, not behavioral* (Advocate/Good Friend). Under operational pressure the
first instinct will be to hand the analyzer a postmortem "for context," at which point it has
quietly become the fifth-agent summarizer §6 rejects. The analyzer runs with filesystem access to
`codes.jsonl` and the doctrine surfaces and nothing else, enforced at the runner level through the
existing projection-bundle mechanism. If that proves too restrictive in practice, the constraint is
wrong and gets renegotiated explicitly — never eroded quietly.

*Acceptance:* a denial probe shows an analyzer-proposed diff cannot reach a role prompt without a
human ratification event.

---

## 6. The non-gating invariant (C5)

**Hard invariant, now:** auditor and analyzer output is non-gating. Not a current-state boundary
everyone intends to revisit — a soft boundary is the same shape as the run-1 failure, true local
steps composing into a false global state with no moment where anyone lied.

**Two-layer denial probe. Both required.**

- *Static (the pathway check).* Reuse `check_core_purity.py`'s mechanism: assert the auditor imports
  nothing from the promotion, verdict, or handover write surfaces, and — the important direction —
  a **reverse-dependency assert that nothing in the promotion or verdict path imports the auditor**.
  This fails on the commit that creates the coupling, not on the run where the coupling first
  changes an outcome.
- *Runtime (the behavioral check).* Emit a maximally alarming code; attempt a promotion; show it
  proceeds.

Neither alone suffices: the static check cannot see dynamic dispatch, the runtime probe cannot
prove absence of a path.

**The data channel.** Both layers above are module-shaped; `codes.jsonl` is a filesystem artifact,
so a promotion-path read of it — even innocent reporting — creates coupling no import scanner sees,
and establishes the channel through which gating arrives later as a one-line conditional
(Constrain). The fix converts it back to module shape rather than adding an unreliable
filesystem-read scanner that a constructed path defeats:

1. The store has **exactly one accessor module**. The path constant exists there and nowhere else.
   The coupling then *is* an import, and the existing reverse-dependency assert covers it unchanged.
2. A literal scan asserts the store's filename and directory appear in no source file except the
   accessor. Cheap, and it catches the honest violation — which is nearly all of them.
3. The store is written outside the run-directory tree the promotion path is given, so an innocent
   reporting read is not merely disallowed but unaddressable from where that code runs.

A determined constructed-path read defeats all three. That is not the threat model. The threat
model is innocent reporting today, one-line conditional in six months, and every step of that path
is honest and name-level detectable at the commit that introduces it.

**Where the invariant lives.** Not a doc, not a code comment — both decay silently and neither
survives a contributor or a model session that never read this. It lives as an **executable failing
test in `make ship`**. Crossing the line requires deleting or weakening a named test, which appears
in a diff, which is exactly what the Diff-Intent Gate catches. The prose statement is additionally
registered with `check_doctrine_sync.py` so the written rule and the executable rule cannot drift
from each other. *A control that is only asserted is not a control; a control never fired is
absent.*

**The one door out.** Promoting the auditor to load-bearing requires it to pass the same behavioral
qualification the four role instructions pass — a passing probe and a passing adversarial
counter-probe at the exact configuration across all four run classes — plus a human signature.
Deliberately expensive, so the upgrade is a decision on the record rather than a drift. The
qualification scaffolding is **not** pre-built: building the load-bearing path is itself the
pressure that gets it used.

---

## 7. Open, and honestly open

- **Does the loop discover, or only re-detect?** C2 improves this materially — contract-anchored
  predicates fire on unseen instances, and `uncoded-pass` makes vocabulary lag visible — but it does
  not fully solve it. Advocate's Good Friend calls the unfixed version "a monument to run 1"; Sim
  argues the accumulator's real value is *forcing classification at write time*, so that at N=50 the
  question "is this the same failure as run 3?" is one grep rather than three hours of prose
  archaeology. Both are right about different things. The plan proceeds on Sim's ground with C2 as
  the mitigation.
- **N — settled, not open.** Advocate/Good Friend and Sage argued for building nothing until N≥10,
  probably N≥30. Sim argued the N worry is about *inference*, not about whether the instrumentation
  is worth having, and that the accumulator's value is forcing classification at write time — which
  is real at n=1. Sim is right and the founder ruling (2026-08-28) closes it: build it, run it, and
  report every count with its denominator. Withholding the signal until N is large starves the very
  runs that produce N, and the mechanism was never a statistical inference engine in the first place
  — it is a ledger that makes "is this the same failure as run 3?" a grep instead of an afternoon of
  prose archaeology.
- **Post-hoc-only defects.** Run 1's overclaim was established by seven cold-context external agents
  arriving *after* disposition. C1 makes the auditor post-run, which helps; it does not make an
  external frame-check unnecessary. Nothing here replaces that.
- **Decay half-life** for accumulating sub-threshold modes: undefined, deferred to W4. No data yet.

## 8. Rejected findings, with reasons

- *"What 'the factory' does is never stated"; "no onboarding path"* (Advocate/User, 2 critical).
  Rejected. This is an internal design proposal for a single operator; `CLAUDE.md` is the
  authoritative self-description. Padding it with orientation prose would make it worse, not
  clearer. The cheap sub-findings were accepted: the nine-code vocabulary is now listed (§2), and
  the kill condition is no longer buried (§5, Step 0).
- *Grandfather clause for analyzer-triggered requalification* (Advocate/Adversarial, medium).
  Rejected as out of scope: the auditor is non-gating by §6, so it triggers no requalification.
- *Integrity/hash-chaining of `codes.jsonl`* (Advocate/Red Team, high). Deferred, not rejected. The
  factory already owns a hash-chained content-addressed ledger in `manifest.py`; if the store ever
  becomes load-bearing it should move there rather than grow its own integrity scheme. While
  non-gating, a plain append-only file is proportionate. Atomic append and `run_id` binding are in
  scope now (§5, W3).
