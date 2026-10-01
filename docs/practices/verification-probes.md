# Verification probes: closing the judge tail without collapsing the roles

A practice for Phase C. It exists because the Tester may never run its tests against the
implementation (it never sees implementation source), so every repair it makes after a
judge is a guess. Guesses converge while failures are plentiful and cheap to explain, then
stall. In one run the same suite went 157, 121, 125 failures across three judges while the
Tester committed hundreds of "fixes". Probes are how the Validator turns that stall into a
short, verified list without becoming the author of the suite.

## When to switch

Switch from triage-and-relay to probes when **a suite's failure count stops falling across
two consecutive judges** while the lanes report their work complete. Do not wait for a
third judge: each blind round costs a full Tester round and teaches nothing new.

## What a probe is

A probe is a Validator-side agent with read access to the assembled judge workspace (the
implementation plus the Tester's suite) that works only in a disposable copy of it. Kinds:

| Kind | Starts from | Job | Output |
|---|---|---|---|
| **Triage** | the judge workspace, read-only | classify every failure as CODER, TESTER or SPEC against the contract | a cluster table with contract citations and file:line |
| **Probe-fix** | a copy of the judge workspace | change only test files, only as the contract allows, until the suite passes; prove every remaining failure as CODER (clause, observed vs required, source line) or SPEC (question and options) | prose test changes, proven defects, spec questions |
| **Delta** | a copy of the **current** Tester files | the same, but report corrections relative to what the Tester actually wrote, quoting the wrong and the right form | a correction list against the Tester's current files |
| **Mutation** | a copy of the green workspace | weaken each condition of a declared control, one at a time, and record which test kills it | a mutant table and the verdict on the control |
| **Composition** | the built service | exercise what no unit test can observe (the composed root, the live HTTP surface, wiring, shutdown) against refused external endpoints | pass/fail per declared check with evidence |

Every probe runs on its own fresh databases and services, drops them at the end, never
edits the base workspace or any lane, and reports in a bounded length.

## Relay rules (the part that keeps the roles separate)

1. **The Tester authors the suite.** A probe's edits are diagnostic evidence for the
   Validator. They never enter a lane as code. The Validator relays **prose**: file, test,
   what to change, and the contract reason. Short literal fragments (a call, a value, an
   SQL clause) are allowed; whole blocks are not. The probe's diff stays with the Validator
   and is used only to check the Tester's later work.
2. **Scrub every relay.** Strip implementation paths, line numbers and any description of how
   the implementation is written before it reaches the Tester. Scan the relay files for them
   mechanically.
3. **The Coder receives behaviour and location, never tests.** A proven defect goes to the
   Coder as the contract clause, the observed versus required behaviour, and its own source
   location. Never the test name, assertion, fixture or trace (Phase B, item 6).
4. **Every relay passes the Orchestrator** before dispatch, including the correction lists.
5. **A probe's spec question is the Validator's ruling to make**, recorded with its fields,
   enum members and nullability in the same change (see `ruling-discipline.md`).

## Verification requirements

- **File-by-file and whole-suite.** A judge that runs a whole suite on one database exposes
  cross-file interference that per-file runs hide (shared per-channel rows, sequences,
  go-live state). A probe's green claim needs both.
- **Cross-check before attributing a regression.** When a judge after a Coder round shows new
  failures, run the probes' own verified tests against the new implementation. If they pass,
  the gap is the Tester's transcription. If they fail, the implementation changed behaviour
  the probe had adapted to, which is either a Coder regression or a Validator ruling that was
  wrong. In one run this cross-check found the Validator's own ruling had made a command wait
  out a four-hour bound before succeeding.
- **Timeouts are mandatory.** Wrap every suite in a timeout; a test that deadlocks (for
  example, holding an advisory lock that the code under test takes itself) otherwise stalls the
  whole judge. Mark a classified hanging test ignored **in the judge copy only**, never in a
  lane.

## Escalating precision

Prose notes lose information when a weaker lane re-types them. Escalate in this order:

1. **Prose notes** per suite: what each test should assert and why.
2. **Delta corrections** against the Tester's current files, quoting wrong and right forms.
3. **Line-precise corrections**: each item quotes the current line by number and gives the
   exact replacement, verified by applying exactly those edits to a copy and running the suite.
   Instruct the Tester to apply them literally, in order, without restructuring.

Move up a level as soon as a round shows corrections that were "not applied" or "applied
differently".

## Independence accounting

Probes reduce oracle independence: the final suite was shaped by Validator-verified
corrections. State it in the verdict, not in a footnote:

- which suites were probe-corrected, and to which level (prose, delta, line-precise);
- that every correction cites the contract and none was derived from implementation output;
- which controls were confirmed by mutation (the mutation probe is the evidence that the
  corrected oracle is about the requirement, not about the implementation).

A Critical surface whose oracle exists only as probe-relayed corrections, without a mutation
check on its controls, is not adequately evidenced.

## Worked numbers (one run)

Three judges of blind repair left about 560 failures across nine suites. One round of six
probe-fix probes took two suites to fully green with test changes alone and the rest to a
handful of proven implementation defects. Two delta passes and one line-precise pass then took
every suite green (1,773 tests). A mutation probe found six input-gathering mutants of a fence
control that no test killed; after one more probe-corrected round, all 24 mutants were killed.
