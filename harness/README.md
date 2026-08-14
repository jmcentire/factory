# harness/ — the run's externalized functions

> Status: scripts land per `docs/HARNESS.md` (unratified proposal, adoption steps 1–3)
> plus the tmux layer binding the founder workflow. Deterministic pieces gate;
> everything model-shaped flags. Remove the model — all of this still stands.

## The two agent seats (founder-confirmed 2026-08-09, this session; enters the
## ledger verbatim at the first ceremony)

- **Validator — tactical.** Decision-making for getting things done: code this,
  test that, answer lane questions from task context, judge output against the
  goals. Holds the human↔AI context because induced understanding of the task
  lives there. The only hub into the lanes.
- **Orchestrator — strategic.** Process and goal enforcement over the whole
  project and its design docs. Audits the *Validator's* failure modes: announced
  work with no receipt; forgetting the triumvirate and doing lane work itself;
  misattributing authority (claims that resolve to no ledger entry); doing the
  opposite of the recorded ask; hyper-focus on what the founder doesn't care
  about; waiting on input it doesn't need; and cleanup debt accruing (the
  100-items-of-lingering-work state is almost never wanted). Detection is the
  dispatcher's (deterministic patterns, timers, receipt counts); judgment is the
  woken agent's; the verdict FLAGS to the Validator or escalates to the human —
  it never gates and never touches a lane.

## The workflow, bound to mechanisms

| Founder workflow step | Mechanism |
|---|---|
| 1. Fire up the factory | `harness/factory.sh <run> "<task>"` — pins base SHA, records the task verbatim + digest, grounds (control 7), opens tmux session `<run>` with windows `ctl` (dispatcher) and `validator`. N factories = N runs; run state lives under `.factory/runs/<run>/`. |
| 2–5. Human ↔ Validator settle spec, architecture, test plan | The `validator` window runs `/validate` (Phase A0 research first). Artifacts are settled one behavior-ledger row at a time and land content-addressed in `.factory/runs/<run>/artifacts/` with `.digest` files. |
| 6. Advocate/Sim passes, refinement | Mechanically gated: the dispatch gate checks the receipt that the pass ran; what Sim said stays advisory (two-layer split). |
| 7. Fire up orchestrator, engineer, tester | `harness/dispatch_lane.sh <run> coder\|tester --dispatch <file>` — refuses without the full authority tuple ("no oracle yet"). Lanes get asymmetric projections via `projection.sh`, never full clones. The orchestrator is two seats: `dispatcher.py` (resident script, window `ctl`, pays no tokens) + `/orchestrate` woken by `orchestrator_wake.sh` on triggers only. |
| 8. Orchestrator tracks tasks, takes minutes | `dispatcher.py` snapshots the human↔Validator window into `minutes/` — labeled `[INFERRED]`, non-authoritative, an index into the directive ledger, never the record. |
| 9. Validator launches/injects via tmux; active poll | Validator injects through `inject.sh` (topology-enforced, receipted; coder-bound results pass the bare pass/fail filter). The *dispatcher* owns the poll — an agent-owned poll cadence is the postmortem's named disease. |
| Stalls / lulls | Dispatcher stall FSM: lane-tending metric, confirm-before-prod, `idle-awaiting-handoff` never prodded, repo-diff metrics forbidden. Prods go to the Validator, not past it. |
| Done-ness | `endgame.sh <run> <final-sha> [lane-branches...]` — fresh checkout, `make ship` + isolation proof, composition merge of all lane branches, live proof (below), hygiene sweep (stash/worktree/branch/PR/untracked), verdict receipt. Run by the Validator; demanded by the run, not by mood. |
| Click-and-test proof | `proof.sh <run>` reads `.factory/target.conf` (see `target.conf.example`): a declared provision script, real entry-point probes (HTTP hits, CLI runs, out-of-band DB checks, screenshot/video captures — each receipted, outputs kept as evidence), access instructions for the human, teardown always. No target.conf = a **declared gap**, never a quiet pass. |
| Postmortem | `postmortem.py --root .factory/runs/<run>` — derives every number from recorded artifacts or prints UNDERIVED; per-agent feedback collected by the Validator, coordination-vs-build split for the next iteration. |

## Genericity: the target is data

The scripts here are generic machinery. Every root they act on is **per-project
data living with the target**, never with the factory checkout:

- `--repo <path>` on `factory.sh` names the target (default: the invoking
  directory's repo). Run state lands in the **target's** `.factory/runs/<run>/`.
- The target carries its own `.factory/` (schedule.registry, reconcile.d/,
  projection.conf, target.conf) and its own `DIRECTIVES/` ledger repo.
- Env seams: `HARNESS_DIR`, `DIRECTIVE_LEDGER`, `HARNESS_SECRETS`,
  `HARNESS_PROJECTION_CONF`, `HARNESS_TARGET_CONF`, `HARNESS_MAX_GROUND_MIN`.
- The founder's hardware signing key is per-**founder**, not per-project: one key
  signs many project ledgers; each project's ledger root is its own chain.
- This repo's own `.factory/` and `DIRECTIVES/` exist because the factory
  dogfoods itself as a target — they govern factory runs against factory.

## Scripts (control number from docs/HARNESS.md)

- `directive.py` — control 1/1a: verbatim hash-chained ledger, qualifier-preserving
  supersession, provisional side chain, `verify --sigs`.
- `lane_env.sh` — capability = environment: `env -i` from a manifest; refuses HALT
  and stale grounding.
- `receipt.sh` — control 3 substrate: chained execution receipts; absence claims
  need a paired positive control.
- `tripwire.sh` — control 5: credential-shaped content → HALT, human-cleared only.
- `sched_audit.sh` — control 6: unregistered OS timer = hostile. (`SCHED_AUDIT_INPUT`
  is a test seam for the forced-negative drill.)
- `ground.sh` — control 7 + 9: resume from disk; reconcilers under `.factory/reconcile.d/`.
- `factory.sh`, `dispatch_lane.sh`, `projection.sh`, `inject.sh`, `dispatcher.py`,
  `orchestrator_wake.sh`, `endgame.sh`, `postmortem.py` — the tmux layer (above).

Forced-negative drills for all of it: `tests/test_harness_scripts.py`, wired into
`make ship` via the `test` gate; syntax/chain gate via `make check-harness`.

## Deviations from the ratification proposal (each deliberate, each reversible)

1. `sched_audit.sh` gained the `SCHED_AUDIT_INPUT` fixture seam — the OS-timer scan
   is otherwise untestable deterministically.
2. `.factory/schedule.registry` ships with a PROPOSED, UNRATIFIED workstation
   baseline; the founder deletes any line not granted.
3. `DIRECTIVES/` ships as a README only; `git init` + `commit.gpgsign` is the
   founder ceremony — an agent must not perform it.

## Ratification items this build surfaces (decide, then the wording changes)

1. **"Clean clone of the same HEAD"** (workflow text) vs asymmetric projections
   (doctrine I3, built here). Sim-endorsed reading: clean clone meant isolation,
   not symmetry. Ratify or overrule.
2. **Hub ownership**: `validate.md` and `orchestrate.md` both claim the identical
   hub-and-spoke seat. This build gives lanes to the Validator and makes the
   orchestrator out-of-band (dispatcher = transport/process control;
   orchestrator-agent → Validator only). `/orchestrate`'s relay-to-lanes and
   receipt-bounce behaviors as written are NOT implemented; the skill text needs a
   founder edit to match, or this build needs reversing.
3. **Tester falsifiability under interface-only projection**: `test.md:102-104`
   (name the production-code mutation that turns each test red) is unsatisfiable
   without seeing production code. Options: accept contract-level mutation naming,
   or grant a post-freeze mutation pass to the Validator. Undecided = the gate is
   honored at contract level and flagged in each run.
4. **"Real ingestion paths unless optimal"**: doctrine's criterion is the §9
   dependency table, and "real" never means prod. The endgame runs real paths in
   disposable checkouts/previews only.
5. Founder decisions still open from the PR stack: n=1/I2 collision, Verboten list
   as data file, `check_authority` ban scope, receipt-reuse reach of I6, and the
   `_derive` prior-phase continuity gap recorded on PR #11.
