# Lessons from the msg-r2 Messaging rebuild — 2026-10

A replacement rebuild of a messaging service (19 components, about 1,000 held-out cases, more than 90 Validator rulings). The four roles were Claude as Validator, Gemini as Orchestrator, Codex GPT as Coder, and first Kimi K3 on Ollama, later Grok via Cursor, as Tester.

By day 5 the core suites were green, but the run had spent about $1,200 of metered Ollama credit on a Tester model with little to show for it. It also lost hours to stalls that nothing caught.

The founder's verdict on the Ollama spend: "accomplished, near as I can tell, fuckall". Every lesson below is about a control that existed on paper and was not enforced.

1. **The Factory's enforced dispatch path was bypassed.**
   - **What the path enforces.** `harness/dispatch_lane.sh` refuses a model dispatch without an explicit objective budget, and `harness/watchdog.py` exists.
   - **What the run did instead.** It launched every lane through run-local scripts with no budget, no progress watchdog and no auth preflight. The earlier multi-gate run already wrote "metered lanes need a ledger, a cap, a fallback and a resume brief" (`lessons-multi-gate-run-2026-10.md` §6), and that prose did not stop it.
   - **Rule.** Lanes launch only through the Factory harness. A run-local launcher is a policy violation, not a convenience. If the harness lacks an agent or mode the run needs, extend the harness (with tests) before launching.

2. **A metered Tester model was never qualified and never cut.**
   - **What happened.** Kimi K3 looped on reading for hours per round: about ten rounds, one of them 3.7 hours, which nobody noticed because the ad-hoc watchdog had lapsed. Each round billed until the credit ran out.
   - **Rule: qualify first.** Before a model takes a lane, run a qualification probe: a small brief with 3-5 items, a hard spend cap and a wall-clock cap. Measure the time to first commit, and the fraction of its tests that go red at base for the named reason (Phase C Q3). A model that fails the probe does not get the lane.
   - **Rule: enforce caps by machine.** Every metered round has a spend cap and a no-progress cap (no commit within N minutes means stop and alert), enforced by the launcher, not by the Validator's attention.
   - **Rule: track cost against output.** Progress per dollar is a reported number. A lane whose output per dollar falls below the run's threshold is cut, and the founder is told.

3. **Lanes finished and sat idle.**
   - **What happened.** Completion was noticed only when the Validator happened to look; once, a full Coder round and a Tester round sat idle for an hour.
   - **Rule.** The launcher starts each lane's completion and stall watcher itself and wakes the Validator. A lane with no watcher is not launched.

4. **An interactive sign-in blocked the run for hours.**
   - **What happened.** The Coder's agent fell back to an interactive login screen. The Validator asked the founder to click a button, repeatedly, for hours, although a live credential was already on the machine and a non-interactive mode worked.
   - **Rule: preflight.** The launcher preflights auth non-interactively and uses non-interactive agent modes only.
   - **Rule: never park on a founder action.** Never park a run on a founder action the Validator can perform. When the founder truly must act, say so once, with the exact command.

5. **The suites never compared output with the system being replaced.**
   - **What happened.** Every suite was green, and then a parity shadow found 0 of 118 emails matching the monolith. No test rendered a scenario against the recorded legacy output.
   - **Rule.** For a replacement, a parity-shadow acceptance test (render every scenario and compare it with the legacy oracle through the cutover comparison) is a TESTING-STRATEGY row from the first round, and is part of every judge.
   - **Rule.** The adversarial review runs at the first green judge, not after the run is declared nearly done.

6. **Briefs omitted the founder's testing standard.**
   - **What happened.** Tester briefs pointed at the contracts, not at `TESTING.md`. The Grok Tester never opened it, and repeated the setup faults the standard forbids: unreached paths, patched globals, sleeps, unstable runtime flags.
   - **Rule.** Every Tester brief makes the testing standard and the run's TESTING-STRATEGY mandatory first reading, and every lane report cites the T-rules it relied on.

7. **The Validator's own wording cost rounds.**
   - **What happened.** Three rulings each cost a round:
     - one told the dispatcher to "read the clock afresh", contradicting the contract's `at` parameter;
     - one followed a held-out case literally against a visible case;
     - one named a config bound without adding the field.
   
     Brief advice also cost rounds: the Validator twice told Testers to patch `Math.random`, and once to use a flag that also seeds crypto.
   - **Rule.** Before dispatch, check every ruling against the signatures and types of the clauses it touches, as well as their prose (`ruling-discipline.md`: "a ruling needs its field"). Check technique advice in a brief against the testing standard. The Orchestrator reviews before dispatch, not after; "speed" is not an exception for rulings.

8. **Large repair rounds regress.**
   - **What happened.** A 35-item Coder round fixed most of its items and broke three systemic paths: a config read, a SQL ambiguity and the submit clock. Together these failed several hundred cases.
   - **Rule.** Cap a repair round at about 12 items, or split it across instances by disjoint scope. Judge after every round.
   - **Rule.** A parallel Coder instance works in its own clone, not a `git worktree`: the agent sandbox may forbid writes to the parent repository's git directory.

9. **Every new test is checked at base before it becomes a target.**
   - **What happened.** About 15% of the Grok Tester's first-pass tests passed against the defect or failed for the wrong reason: a missing second permission, an unreached path, a fixture runtime instead of the production layers.
   - **Rule.** Running each new test against the pre-fix candidate (Phase C Q3) caught every one of them. Keep it as a gate. No new test goes to the Coder as a target until it is red at base for its named reason.
