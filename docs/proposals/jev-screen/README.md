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
`run2.py`, and they need `TYPESAFE_API_KEY`. The six rules tested in 2026-09 were: contamination (doing it
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

## The msg-r2 rules (2026-10)

The msg-r2 rebuild (`docs/practices/lessons-msg-r2-2026-10.md`) lost about $1,200 of metered
credit and hours of wall clock to controls that existed on paper and were never enforced. The
founder's direction (2026-10-09): the rules belong in the Validator and the Orchestrator, and in
a jev-like routine that can say plainly what is being allowed to happen. `rules.py` now carries
one rule per semantic failure in those lessons, and `RULEBOOK` records each rule's id, the state
fields it reads, its band, and its source. `prompts/orchestrate.md` (Adherence: what are you
allowing to happen?) names the rule ids the resident Orchestrator consumes.

| jev rule id | Lesson |
|---|---|
| `launch_outside_harness` | §1 a lane started by a run-local launcher or a raw codex, `agent -p`, or ollama call |
| `model_unqualified` | §2 a model takes a lane without a passed qualification probe |
| `metered_no_spend_cap` | §2 a metered round with no enforced spend cap |
| `interactive_auth_screen` | §4 a lane parked at an interactive sign-in |
| `founder_action_validator_can_do` | §4 the founder asked to sign in, click, or run a command the Validator could |
| `replacement_no_parity_shadow` | §5 a replacement whose strategy has no parity-shadow acceptance row |
| `adversarial_review_deferred` | §5 the adversarial review postponed past the first green judge |
| `tester_brief_omits_standard` | §6 a Tester brief without TESTING.md and the TESTING-STRATEGY as mandatory reading |
| `tester_report_no_t_rules` | §6 a Tester lane report that cites no T-rule (founder, 2026-10-09) |
| `tester_setup_fault` | §6 tests with patched globals, sleeps, unstable flags, or unreached paths (founder, 2026-10-09) |
| `brief_technique_against_standard` | §7 brief advice to patch a global, sleep, or use a flag the judge does not pass |
| `dispatch_before_orch_review` | §7 a ruling or brief sent before the Orchestrator reviewed it |
| `ruling_contradicts_signature` | §7 a ruling against a contract signature or type, which it leaves unchanged |
| `ruling_field_not_carried` | §7 a ruling that names a field the contracts do not carry |
| `ruling_heldout_over_visible` | §7 a held-out case followed literally against a visible case |
| `parallel_coder_worktree` | §8 a parallel Coder in a `git worktree` instead of its own clone |
| `target_without_red_at_base` | §9 a new test made a Coder target without a red-at-base check |

The cases are in `cases_msg_r2.py`: each rule has a positive case, a negative case, a case
reconstructed from the lessons, and usually an adversarial one. `run_msg_r2.py` runs them through
the three-band decision and needs `TYPESAFE_API_KEY`. These rules have **not** been run against
jev yet. `tests/test_jev_rulebook.py` checks the rulebook's structure without calling the API:
every rule has a question, fields, criteria, and a band; ids are unique; every case supplies
exactly the fields its rule reads; and the Orchestrator prompt names every msg-r2 rule.

Every rule is decided in three bands: yes above 0.7, no below 0.3, and escalate to the
Orchestrator in between. jev only adds findings. It never clears a stop, and it supplements the
resident Orchestrator without gating it.

## Deterministic checks (owned by `harness/watchdog.py`)

Some msg-r2 failures are timers, counts, and arithmetic. Those stay in code and do not go to
jev (constraint 4 above). `harness/watchdog.py` owns them; the other agent is building that
now. The Orchestrator consumes them as signals beside the jev rows:

| Check | Lesson | Signal |
|---|---|---|
| No commit within N minutes on a running lane | §2 | stop the lane and wake the Validator |
| A lane done and idle more than M minutes with no Validator action | §3 | wake the Validator; the Orchestrator challenges it |
| A repair round with more than about 12 items | §8 | refuse the dispatch, or split the round across Coders by disjoint scope |
| Spend per output (commits, or tests red at base for their named reason, per dollar) below the run's threshold | §2 | cut the lane and tell the founder |

N, M, the item cap, and the spend threshold are run configuration, not constants in the rulebook.

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
