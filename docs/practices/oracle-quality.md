# Oracle Quality — proving a guard guards

Companion to `VALIDATION-DIRECTIVE.md`. Tooling: `harness/mutate.sh`.

A passing suite reports that nothing it can see is broken. It says nothing about
what it can see. Run v8 reached 1723 passing tests while carrying a data
regression and four containment escapes; every one was found by pointing a new
lens at the code, none by the suite. This page exists so that stops being luck.

## The one rule

**Guard the prohibited ACTION, not the fix's ARTIFACT.**

This single error produced four worthless checks in one run, from three separate
parties including the Validator:

| The check asserted | What actually escaped |
|---|---|
| config canaries at a monkeypatched `HOME` | the code read an import-time constant pointing elsewhere |
| the git probe's *start directory* was inside the root | the *walk upward* from that contained start |
| invocations carrying `kwargs["cwd"]` | the code passes its directory as `git -C <dir>` |
| the *returned path* was contained | a forbidden *read* that gets clamped before returning |

Every one passed. Every one was worthless. Note the third: it was written *after*
being told the second was non-discriminating, and repeated the same shape.

The forcing question is never "what does this fix produce?" It is:

> **What action is forbidden, and what would I observe if it happened anyway?**

Then observe *that*, on the channel the code actually uses — not the channel you
imagine it uses.

## A guard that has never been mutated is a claim

Not a guard. The only proof is reverting the behavior and watching the check fail.
Use `harness/mutate.sh`; it enforces the four preconditions that make a verdict
mean anything:

1. **The code under test loads from the mutated tree.** A stale `.pth` can alias
   the import to a different checkout and produce a confident verdict about code
   you never touched.
2. **The clean tree is green first.** "The guard caught it" is meaningless if the
   guard was already failing. v8 shipped a tightened check that failed 5/5 on the
   *unmodified* tree; its apparent kill was the false red firing again.
3. **The patch actually applied.** A drifted anchor raises, changes nothing, and
   the suite passes — indistinguishable from survival unless checked. The ad-hoc
   runner used in v8 reported exactly that false `SURVIVED`. A mutation harness
   that cannot tell *did not apply* from *survived* manufactures the false green
   it exists to detect.
4. **The whole suite runs, not the one test you think owns the requirement.**
   Per-test attribution manufactures false blind spots exactly as per-test greens
   manufacture false confidence.

## Survivors are a question, not a finding

A survivor means *either* a missing guard *or* an equivalent mutant. Decide
behaviourally — exercise the mutated build and show the prohibited outcome
actually occurs — before dispatching anyone. Two v8 survivors were equivalent:

- a zero-elapsed-interval fold that is mathematically the identity, observable
  only for future-dated intervals (and killed there, by a different oracle);
- a defense-in-depth containment clamp, unobservable while the primary
  containment holds — which is what a backstop *is*. It stays.

Filing either as a gap would have sent a lane to change correct code.

## When a defect is not observable, say so

If the prohibited behavior cannot be detected through the contract at practical
cost, the correct output is a **documented unguarded property** carrying its
measurement — not a fabricated oracle. v8 hit this with a tracker inconsistency
worth ~7e-7 over 400 folds against an observable quantity rounded to 4 decimals:
it would take ~28,000 consecutive folds to move anything visible. Three oracle
designs failed on it, the last because the Validator directed the Tester to
assert on private implementation state its projection cannot see — reaching past
the seam the run had built. Delete the check, keep the fix, record the property.

**A test that cannot discriminate is worse than no test: it reports coverage that
does not exist.**

## Green on the acceptance suite is not green on the product

The judge's narrow surface is what makes a run fast and is also what lets it
certify a broken build. v8's acceptance suite went fully green while
`kin search --json | jq` was broken by a note appended after the JSON document.

**Gate the release on the full suite run against the INTEGRATED tree** — the
implementation lane's source overlaid with the test lane's tests — re-run after
*every* late change, with import resolution verified each time. A tree validated
before additional changes is not a validated tree.

## Gates prevent regression; adversaries find defects

These are different jobs, and only one of them finds what nobody thought to ask
about. In field use (the batch0 run), a run whose every gate was green — a
red-now/green-now pair, a 1,659-test rail, the ship target, an isolation proof,
five live probes and changeset hygiene — shipped a release that was wrong twice
over, and every defect that mattered was found by an adversary. The gates were
not worthless: they proved the absence of regression, which is why the fixes
could be made quickly. But a gate can only re-ask a question someone already
wrote down, so a process assembled only from gates ships its defects with a
clean bill of health.

Run every gate; then point adversaries at the suite itself — finders enumerate
tests that could not fail, independent refuters try to name the mutation that
turns each red (TESTING.md T31), and whatever survives blocks. Read a green board
as "nothing known broke," never as "nothing is broken." (Formerly TESTING.md T9;
moved here on 2026-10-03 when How We Test was rewritten as T1–T40.)
