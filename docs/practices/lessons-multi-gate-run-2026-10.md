# Lessons from a three-gate replacement run — 2026-10

A multi-day run that added a pull-channel feed and a generic channel-controls capability to an
existing service, planned with PACT and built by the four roles (Validator, resident
Orchestrator, Coder, Tester) across three gates, with about 90 Validator rulings. It ended with
every suite green (1,773 tests), a mutation check on its central control holding, and a composed
service checked end to end. These are the lessons that generalize. The two load-bearing ones are
written up as practices: `verification-probes.md` and `ruling-discipline.md`.

1. **Blind test repair stalls; probes close it.** The Tester cannot run its tests against the
   implementation, so after the first judge its repairs were guesses. Failure counts stopped
   falling across three judges while the Tester committed hundreds of fixes. Verification probes
   (test-only fixes in scratch copies, defects proven against the contract, corrections relayed in
   prose) took the run to green in four relay rounds. Rule: switch to probes when a suite's
   failure count stops falling across two judges.
2. **Relayed prose decays in transcription.** A weaker Tester model applied verified prose notes
   imperfectly: skipped items, a correction meant for one file applied to another, a column the
   note said to remove left in place. Each costs a judge. Rule: escalate prose → delta corrections
   against the current files → line-precise corrections verified by applying exactly those edits.
3. **Most ruling-caused rounds were incomplete rulings, not hard questions.** Fields, enum
   members and nullability that a ruling implied but the stubs did not carry; a superseding ruling
   that left the old text standing. Each cost a Coder or Tester round. Rule:
   `ruling-discipline.md`, with the mechanical sweeps run before every dispatch.
4. **Ground third-party behaviour in its documentation before ruling.** The plan assumed an
   external API could pause and resume a schedule. Its documentation has no such operation.
   Reading the docs turned that into a declared "unsupported" answer and a recorded manual step,
   instead of code calling a guessed endpoint on a production account.
5. **Judge hygiene is part of the judge.**
   - A fresh database per suite, an empty cache, and a timeout per suite. A test that holds a lock
     the code under test also takes deadlocks forever; without a timeout one test stalls the
     whole judge for hours.
   - An ignore list for classified hanging tests, applied to the judge copy only.
   - Whole-suite runs, because tests sharing one database interfere through per-entity singleton
     rows (pointers, state machines, sequences). Tests set the state they depend on.
   - Run the exact tree you will publish, once more, before publishing it.
6. **Metered lanes need a ledger, a cap, a fallback and a resume brief.**
   - A per-round spend cap enforced by a proxy in front of the provider stops runaway rounds. The
     local ledger overstated the real bill about fivefold (cached-token pricing), so budget
     decisions read the provider's bill.
   - Monthly provider limits end rounds mid-work. Keep a same-family fallback route in the launcher
     (one flag), so a provider outage does not change the seat's model family. A family change is
     a requalification, and it is the founder's call.
   - When a round stops mid-work, the next brief starts with "deno check, finish and commit your
     uncommitted files first", then continues the same approved brief.
   - Briefs say: commit after each item (or every N tests), record a blocking question and keep
     working, ask at the end. Rounds that stopped on the first question wasted most of their budget.
7. **Give lanes a focused spec.** A 600k-character task file re-read every round exhausted the
   Tester's budget twice with nothing committed. A focused file (the design, the constraints, the
   current rulings) plus `rg`/`sed` reads fixed it.
8. **No placeholder implementations, even across gates.** When a later gate's operation was needed
   to assemble an earlier gate's component, the answer was to implement it, not to bind an
   operation that dies at the first call. A placeholder passes registration and fails in
   production.
9. **The Coder's static conformance check targets the current stubs.** Freezing the previous
   generation's type baseline turned every intended widening into a blocker. Behavioural
   compatibility is protected by the previous generation's runtime suites and wire shapes, not by
   frozen type unions.
10. **The Orchestrator's journal is append-only.** The resident Orchestrator sometimes re-created
    its journal file instead of appending. Check the journal's line count after each review.
11. **Triage before relaying.** After every judge, read-only triage agents (one per suite cluster)
    classify each failure as CODER, TESTER or SPEC with a contract citation before anything is
    routed. Routing raw failures to a lane trains it on the oracle.
12. **Mutation-check the inputs of a control, not only its decision.** The fence decision's
    mutants were all killed on the first check; six mutants in the queries that gather the fence's
    inputs survived. A control whose inputs can be weakened unnoticed is not covered.
