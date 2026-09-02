# harness/ — the run's externalized functions

> Status: executable PR2. Runtime Stage R/E authority selects and freezes the exact target;
> externally anchored resume verifies it before grounding or dispatch; and model lanes cross the
> live boundary only through qualified macOS Seatbelt runners and signed typed broker operations.
> The human/Validator tmux surface remains operator-owned coordination, not a qualified lane.
> See `docs/EXECUTION-TRUTH.md` and `docs/HARNESS.md`.

## The two agent seats (founder-confirmed 2026-08-09, this session; enters the
## ledger verbatim at the first ceremony)

- **Validator — tactical.** Decision-making for getting things done: code this,
  test that, answer lane questions from task context, judge output against the
  goals. Holds the human↔AI context because induced understanding of the task
  lives there. The only hub into the lanes.
- **Orchestrator — strategic.** Process and goal enforcement over the whole
  project, its live conversation, Kindex context, and its design docs. It is a
  resident interactive agent, launched at ignition rather than a one-shot `agy -p`.
  The dispatcher supplies every bounded sampled pane snapshot it captures plus independent
  cadence under a monotonic cursor; it does not decide which events deserve judgment. The
  Orchestrator reconstructs the user's ultimate goal, classifies recent input, tests whether the current direction
  advances that goal, and projects implications and side effects. It also audits the
  *Validator's* failure modes: announced
  work with no receipt; forgetting the triumvirate and doing lane work itself;
  misattributing authority (claims that resolve to no ledger entry); doing the
  opposite of the recorded ask; hyper-focus on what the founder doesn't care
  about; waiting on input it doesn't need; and cleanup debt accruing (the
  100-items-of-lingering-work state is almost never wanted). Judgment is the
  resident agent's; deterministic checks remain independent fail-safes. It never sends free-form
  prose into a pane it judges; its only lane message is a generated typed status probe. It never
  acquires verdict authority. Its closed effect is `block|no-op`, and a block gates the next
  dispatch until the Validator records an evidence-bound `stop`, `narrow`, `escalate`, `refute`,
  or `resolve` disposition.

## The workflow, bound to mechanisms

| Founder workflow step | Mechanism |
|---|---|
| 0. Authorize exact execution | Runtime Stage R authorizes bounded target resolution; `resolve-target` creates the exact run-owned target-state; distinct Stage E authorizes the verbatim request against that state. No tmux or lane exists yet. |
| 1. Fire up the factory | `harness/factory.sh <run> "<verbatim-task>" --runs <runs-root>` — refuses pre-intake or mismatched task bytes, re-verifies target-state, grounds, records tmux intent, and opens persistent `orchestrator`, `validator`, and deterministic `ctl` windows. |
| 2–5. Human ↔ Validator settle spec, architecture, test plan | The `validator` window runs `/validate` (Phase A0 research first). Before decomposition it challenges explicit, implicit, and inherited requirements that disproportionately drive complexity. Retained planning/lane/review sources get two separately recorded, source-bound extraction passes; `semantic_union.py update-spec` materializes every observation and ruling into the Product Specification before artifacts are settled and content-addressed. Phase A refuses an open or stale union. |
| 6. Advocate/Sim passes, refinement | Mechanically gated: the dispatch gate checks the receipt that the pass ran; what Sim said stays advisory (two-layer split). |
| 7. Fire up orchestrator, engineer, tester | The strategic Orchestrator is already resident. `harness/dispatch_lane.sh <run> coder\|tester --dispatch <file>` remains the qualified path: closed dispatch, asymmetric projection, structural qualification, hard ceilings, and signed typed broker operations. `harness/tmux_lane.sh` is the explicitly unqualified dogfood/coordination alternate: it launches a real Codex agent in a standalone repository with agent-owned `.git`, then crosses only a no-Git content-addressed regular-file export. |
| 8. Orchestrator tracks goal and tasks | `dispatcher.py` appends every bounded sampled pane snapshot and cadence record to `orchestrator/activity.jsonl`; notifications go only to the Orchestrator's own pane. The agent uses Kindex as normalized bite-sized work/experiment state, derives chunk-specific dispatch context, identifies disproportionate requirement/assumption pressure before decomposing, classifies complexity/ambiguity, recommends the least expensive qualified model per chunk, and emits a closed goal/direction/consequence/adherence/lifecycle assessment. `minutes/` remains `[INFERRED]` and non-authoritative. |
| 9. Validator dispatches immutable objectives; active poll | Validator dispatches through `dispatch_lane.sh`; unqualified tmux author lanes use `tmux_lane.sh`. A lane emits `FACTORY_QUESTION` instead of guessing. `lane_dialogue.py` binds a Validator answer to that exact lane/question, while either supervisor can issue the generated status probe through `tmux_lane_message.sh`; Codex queue/resume preserves the thread. |
| Stalls / lulls | In resident tmux mode quiet time emits `liveness_unknown`, never a guessed stall. A lane with a pending typed question is already `waiting-on-validator` and does not raise a liveness alarm. Otherwise Validator and Orchestrator inspect tmux and use the typed status probe; `/loop`, when available, repeats `status.sh`/inspection rather than model attempts. `idle-awaiting-handoff` is healthy and repo-diff metrics remain forbidden. |
| Done-ness | `endgame.sh <run> <final-sha> --candidate-resource <resource-id> --runs <runs-root>` — accepts only a recorded run-owned candidate, archives the exact object into a recorded endgame worktree, runs deterministic gates and live proof, verifies target/resource closure, then routes to Gate L. A BLOCK completes only the judging pass; the run stays open. Only Gate L can write `closed`, while `record_no.sh` alone writes terminal `no`. |
| Click-and-test proof | `proof.sh <run>` reads `.factory/target.conf` (see `target.conf.example`): a declared provision script, real entry-point probes (HTTP hits, CLI runs, out-of-band DB checks, screenshot/video captures — each receipted, outputs kept as evidence), access instructions for the human, teardown always. No target.conf = a **declared gap**, never a quiet pass. |
| Postmortem | `postmortem.py --root .factory/runs/<run>` — derives every number from recorded artifacts or prints UNDERIVED; per-agent feedback collected by the Validator, coordination-vs-build split for the next iteration. |

## Semantic evidence union

The evidence set is a closed directory, not a prose appendix:

```text
artifacts/semantic-evidence/
├── sources/{planning-pass,lane-trace,adversarial-review}/<source-id>.source
├── extractions/{planning-pass,lane-trace,adversarial-review}/<source-id>/<pass>.json
└── rulings.json
```

Each retained source needs at least two separately recorded extraction manifests. Every extraction
binds the source SHA-256, records its claimed extractor/version/configuration provenance, and names
the exact spans/questions it found. Observation IDs are derived from those bytes and questions;
there is no authored merge key that can silently make two findings one.
`rulings.json` has exactly one `resolved`, `not-an-ambiguity`, or `deferred` row per derived
observation. A deferred row is `open` and blocks.

Before ratification, render the exact checklist into the Product Specification:

```bash
python3 harness/semantic_union.py update-spec \
  --artifacts .factory/runs/<run>/artifacts \
  --spec .factory/runs/<run>/artifacts/product-specification.md
```

After the Product Specification has a `.digest`, `update-spec` refuses; evidence changes require
an explicit superseding/re-ratification cycle. `phase1_gate.sh` invokes `semantic_union.py verify`,
which re-reads every source and extraction, recomputes the complete input closure, byte-compares
the generated signed section, and rejects any open item. The guarantee is conservation after
extraction. The manifests do not authenticate extractor identity or prove recall; extraction recall
and the quality of the human ruling remain semantic judgments. Downstream `FACTORY_QUESTION`
records are the escape evidence used to measure and improve them. Until producer-driven enrollment
lands, the generated section and CLI summary render
`producer_enrollment_coverage=unknown-until-producer-inventory-is-joined`; an enrolled union must
not be presented as whole-run semantic coverage.

## Genericity: the target is data

The scripts here are generic machinery. The target is runtime data; core code never imports or
names a consuming project:

- A signed target-resolution request names one credential-free URL, exact requested ref, and
  subpath. Runtime produces a fresh run-owned object store and detached checkout; `factory.sh`
  has no repository, ref, SHA, or cwd fallback.
- `--runs <path>` names the control plane. Runtime authority, retained target-state, resource
  records, harness coordination, and evidence live under `<runs>/<run>/`; source bytes do not.
- Target-owned `.factory/` configuration (`projection.conf`, `target.conf`, reconcilers) is read
  from the immutable target workdir after target-state verification.
- Env seams: `HARNESS_DIR`, `HARNESS_PROJECTION_CONF`,
  `HARNESS_TARGET_CONF`, `HARNESS_MAX_GROUND_MIN` (tighten-only: values above the
  360-minute default are refused), plus externally supplied
  `FACTORY_RESUME_*`, `FACTORY_RUNNER_*`, and `FACTORY_BROKER_REGISTRY_DIR` paths. Secrets are
  read only from the named-secret root declared by the checkpoint-bound runner manifest.
  Directive ledger, provisional chain, and role doctrine paths are not ambient seams: the external
  resume configuration must name them exactly as `factory-directive-ledger`,
  `factory-directive-provisional`, and `factory-role-doctrine`.
- The founder's hardware signing key is per-**founder**, not per-project: one key
  signs many project ledgers; each project's ledger root is its own chain.
- This repo's own `.factory/` and `DIRECTIVES/` exist because the factory
  dogfoods itself as a target — they govern factory runs against factory.

## Scripts (control number from docs/HARNESS.md)

- `directive.py` — control 1/1a: verbatim hash-chained ledger, qualifier-preserving
  same-scope supersession, provisional side chain, one shared closed run/generation/role grammar,
  prospective whole-chain validation, serialized durable writers, and `verify --sigs`.
- `consume_block.sh` — exact-subject typed disposition of advisory/stall events; it copies the
  supplied run-owned evidence into a content-addressed run artifact before receipting and release.
  A read, stale subject digest, unretained digest string, or acknowledgement cannot clear the gate.
- `lane_env.sh` — legacy deterministic-command helper: `env -i` from a manifest; refuses HALT,
  stale grounding, and an applicable blocker through the same serialized attention-admission
  protocol as supported producers. Model dispatch uses the stronger `factory run-model` boundary,
  which also removes profile inheritance, qualifies Seatbelt, constrains process trees, and
  receipts config.
- `receipt.sh` — control 3 substrate: chained execution receipts; absence claims
  need a paired positive control.
- `tripwire.sh` — control 5: credential-shaped content → HALT, human-cleared only.
- `sched_audit.sh` — control 6: unregistered OS timer = hostile. (`SCHED_AUDIT_INPUT`
  is a test seam for the forced-negative drill.)
- `ground.sh` — control 7 + 9: resume from disk; reconcilers under `.factory/reconcile.d/`.
- `factory.sh` opens a persistent Orchestrator plus the operator-owned Validator/dispatcher
  coordination surface. `orchestrator_channel.py` carries the complete activity cursor and
  monotone assessments; `orchestrator_checkpoint.sh` requires them before dispatch/verdict.
  `tmux_lane.sh` gives unqualified Codex author lanes local Git checkpoints and requires a
  status/diff plus relevant-check audit before each commit;
  `codex_lane_session.py` retains the real thread; and `lane_dialogue.py` plus
  `tmux_lane_message.sh` provide typed questions, answers, and status probes. Freeze crosses only
  plain regular-file output without invoking lane Git after handoff. `dispatch_lane.sh` and
  `projection.sh` form the qualified model boundary; `inject.sh`, `dispatcher.py`, and
  `orchestrator_wake.sh` serve coordination; `endgame.sh` and `postmortem.py` close and report.

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
   Orchestrator an independent resident monitor (dispatcher → Orchestrator pane;
   Orchestrator report → monotone blocking channel). It cannot inject into the
   Validator or an author lane. `/orchestrate`'s direct relay-to-lanes behavior is
   intentionally not implemented.
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
