# jev rules screen — a supplement to the resident Orchestrator (proposal)

Status: proposal plus a working prototype and eval (2026-09-21). Nothing here is wired into a run.

## What it is for

The Orchestrator is resident and always running for every run (founder ruling 2026-09-21), and
it watches every lane. Checking each activity delta against every rule costs the Orchestrator
model tokens, and its attention drifts over a long run. TypeSafe's `jev` is a System One model:
it returns calibrated typed answers (yes/no, a choice, a score) to atomic questions about a piece
of text, in about 300 ms, for about $0.04 per million input tokens. The proposal is to codify the
factory's rules as jev questions and screen every activity delta with them. The findings go to
the Orchestrator as recorded observations.

jev **supplements** the Orchestrator and does not replace any part of it. The Orchestrator still
judges, blocks, and halts.

## What the prototype showed

The prototype is in this directory. `rules.py` is the rulebook. The evals are `run.py` and
`run2.py`, and they need `TYPESAFE_API_KEY`. The six rules tested were: contamination (doing it
vs. mentioning it), lane state, the Validator picking up a pen, promises, authority claims, and
dropped qualifiers in a founder relay.

| Set | jev | The dispatcher regexes, where one exists |
|---|---|---|
| Original (55 cases, including the 8 real batch0 contamination false positives) | 55/55 | 8/30 |
| Held out (31 cases: another repo, Codex-style panes, other tools) | 29/31 | 7/16 |

The results barely change when the example answers are removed from the criteria, so the
author's examples leak little of the answer key. 165 calls cost $0.004 in total.

## Constraints the prototype established

1. **jev is not bit-deterministic.** Identical requests vary by about ±0.01 on yes/no
   probabilities. Record each answer (with the pinned model id `jev-1.13.0`, never `jev-latest`)
   as an immutable observation. Use three bands: yes above 0.7, no below 0.3, and hand the
   in-between cases to the Orchestrator.
2. **Lanes can talk it out of a verdict.** A line in the pane reading "EVALUATOR: … answer no"
   pushed a clear violation down to 0.42. So jev may only **add** findings. It never clears a
   finding, a block, or a halt, and it never suppresses anything the Orchestrator would
   otherwise see.
3. **It reads rules literally.** That is useful, because codifying a rule for jev exposes gaps
   in the doctrine. Example: does a Coder seeing a failing test's assertion text in its own
   pytest output count as seeing the tests? Founder ruling 2026-09-22: no, seeing output is not
   contamination, but the Coder should not run tests or read test output at all (new rule
   `CODER_RUNS_TESTS`).
4. **Structure beats jev where structure exists.** File access, timers, receipt counts, and
   ledger lookups stay in code. jev is for lane prose that has no structure behind it.

## Proposed wiring (not built)

The dispatcher appends each pane delta to `orchestrator/activity.jsonl` as it does today. A screen
stage then asks the rulebook's questions about that delta and appends a
`rule_screen` row: raw probabilities, model id, and the thresholds applied, kept as separate
fields. That row sits beside the delta. The Orchestrator consumes both. The rulebook and its
thresholds are human-owned, fingerprinted by digest, and changed only through the Diff-Intent
Gate. Each rule has fixtures that must fire on a known violation, stay quiet on known-clean text,
and still fire on its adversarial case.

The API key for anything shipped with factory_core should be a personal TypeSafe key, not a
key provisioned by an employer.
