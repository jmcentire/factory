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
  work with no receipt; forgetting the role separation and doing lane work itself;
  misattributing authority (claims that resolve to no ledger entry); doing the
  opposite of the recorded ask; hyper-focus on what the founder doesn't care
  about; waiting on input it doesn't need; and cleanup debt accruing (the
  100-items-of-lingering-work state is almost never wanted). Judgment is the
  resident agent's; deterministic checks remain independent fail-safes. It never sends free-form
  prose into a pane it judges; its only lane message is a generated typed status probe. It never
  acquires verdict authority. Its closed effect is `block|halt|no-op`; a halt sets HALT and kills the Validator's window
  (only a human clears it), and a block gates the next
  dispatch until the Validator records an evidence-bound `stop`, `narrow`, `escalate`, `refute`,
  or `resolve` disposition.

## The workflow, bound to mechanisms

| Founder workflow step | Mechanism |
|---|---|
| 0. Authorize exact execution | Runtime Stage R authorizes bounded target resolution; `resolve-target` creates the exact run-owned target-state; distinct Stage E authorizes the verbatim request against that state. No tmux or lane exists yet. |
| 1. Fire up the factory | `harness/factory.sh <run> "<verbatim-task>" --runs <runs-root>` — refuses pre-intake or mismatched task bytes, re-verifies target-state, grounds, records tmux intent, and opens persistent `orchestrator`, `validator`, and deterministic `ctl` windows. After ignition, attach (`tmux attach -t <run>`) and accept the folder-trust prompt in each seat window or the run will sit idle. |
| 2–5. Human ↔ Validator settle spec, architecture, test plan | The `validator` window runs `/validate` (Phase A0 research first). Before decomposition it challenges explicit, implicit, and inherited requirements that disproportionately drive complexity. Retained planning/lane/review sources get two separately recorded, source-bound extraction passes. Any checkpoint-selected standards, loops, and recipes are dispositioned obligation-by-obligation and independently reviewed. `phase_compiler.py` materializes semantic union → guidance → agreement into only the proper authorities before they are settled and content-addressed. Phase A refuses an open, stale, missing, or misrouted member. |
| 6. Advocate/Sim passes, refinement | Mechanically gated: the dispatch gate checks the receipt that the pass ran; what Sim said stays advisory (two-layer split). |
| 7. Fire up orchestrator, engineer, tester | The strategic Orchestrator is already resident. `harness/dispatch_lane.sh <run> coder\|tester --dispatch <file>` is the opt-in qualified path, used only when the human asks for it: closed dispatch, asymmetric projection, structural qualification, hard ceilings, and signed typed broker operations. `harness/tmux_lane.sh` is the default lane runner (unqualified; it cannot promote) and the only author-lane launcher: it launches a real agent (`codex` exec by default, `codex-interactive`, `codex-ollama` or `cursor-agent`) in a standalone clone with agent-owned `.git`, then crosses only a no-Git content-addressed regular-file export. Before launch it preflights the agent's sign-in non-interactively, refuses a metered agent without a spend cap, budget reservation and passing qualification probe (`tmux_lane.sh <run> <role> qualify`), and puts the testing standard into every Tester prompt; it starts a `watch-<lane>` watcher (`lane_watchdog.py`) or does not launch. A run-local launcher is a policy violation. |
| 8. Orchestrator tracks goal and tasks | `dispatcher.py` appends every bounded sampled pane snapshot and cadence record to `orchestrator/activity.jsonl`; notifications go only to the Orchestrator's own pane. The agent uses Kindex as normalized bite-sized work/experiment state, derives chunk-specific dispatch context, identifies disproportionate requirement/assumption pressure before decomposing, classifies complexity/ambiguity, recommends the least expensive qualified model per chunk, and emits a closed goal/direction/consequence/adherence/lifecycle assessment. Assessment/3 also audits selected-guidance classification, application, N/A bases, routing, evidence, and open findings against host-derived digests. `minutes/` remains `[INFERRED]` and non-authoritative. |
| 9. Validator dispatches immutable objectives; active poll | Validator dispatches through `tmux_lane.sh` by default; the qualified `dispatch_lane.sh` is opt-in. A lane emits `FACTORY_QUESTION` instead of guessing. `lane_dialogue.py` binds a Validator answer to that exact lane/question, while either supervisor can issue the generated status probe through `tmux_lane_message.sh`; Codex queue/resume preserves the thread. |
| Stalls / lulls | Every tmux author lane's watcher stops a lane that makes no commit within its window (default 20 minutes after a 15-minute read allowance) or reaches its cap (`refusal-lane-stall`, `refusal-lane-cap`), and wakes the operator on `__LANE_DONE__`, exit or stop (`tmux-lanes/wake.jsonl`, `events.jsonl`); `lane_watchdog.py report` gives each lane's commits per dollar. In resident tmux mode the dispatcher's quiet time emits `liveness_unknown`, never a guessed stall. A lane with a pending typed question is already `waiting-on-validator` and does not raise a liveness alarm. Otherwise Validator and Orchestrator inspect tmux and use the typed status probe; `/loop`, when available, repeats `status.sh`/inspection rather than model attempts. `idle-awaiting-handoff` is healthy and repo-diff metrics remain forbidden. |
| Done-ness | `endgame.sh <run> <final-sha> --candidate-resource <resource-id> --runs <runs-root>` — accepts only a recorded run-owned candidate, archives the exact object into a recorded endgame worktree, runs deterministic gates and live proof, verifies target/resource closure, then issues the candidate-bound green-endgame admission consumed by Gate L. A direct current-run `promote.sh` call without that admission refuses. A BLOCK completes only the judging pass; the run stays open. Only Gate L can write `closed`, while `record_no.sh` alone writes terminal `no`. |
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

## Per-run selected standards, loops, and recipes

The user may add run-specific guidance only through the external resume checkpoint's exact
configuration set. Add the canonical selector under the reserved name `factory-run-guidance` and
add every source document under the source name used by that selector. The same complete
`--config-source NAME=/absolute/path` vector must be supplied when deriving and independently
verifying the checkpoint; `factory.sh` consumes that verified vector together with the per-source
digests returned by the verifier, then stable-reads every source against those digests. The paths
cannot be swapped consistently between checkpoint verification and admission. There is no ambient
attachment or post-ignition add-on:

```bash
factory derive-resume-checkpoint ... \
  --config-source factory-run-guidance=/abs/run-guidance.json \
  --config-source infra-recipe=/abs/infrastructure-standard.md
```

The readable shape below shows a construction requirement such as the package's main-module name
and executable entrypoint. The file on disk must be canonical compact JSON with sorted keys; its
run id, generation, source digest, document membership, obligation membership, roles, and
subject-derived authority targets are all exact.

```json
{
  "schema_version": "factory-run-guidance-selection/1",
  "run_id": "example-r1",
  "generation": 1,
  "documents": [{
    "source_name": "infra-recipe",
    "source_digest": "sha256:<digest-of-infrastructure-standard.md>",
    "kind": "recipe",
    "obligations": [{
      "obligation_id": "G-MAIN-ENTRYPOINT",
      "text": "Name the main module as specified and expose the required executable entrypoint.",
      "subject_class": "constructional",
      "classification_basis": "This constrains assembly shape rather than caller-visible behavior.",
      "roles": ["validator", "orchestrator", "coder"],
      "authority_targets": ["architecture", "testing-strategy"]
    }]
  }]
}
```

Before ratification the Validator dispositions every G-* obligation in
`artifacts/guidance/application.json` as `applied` or `not-applicable`, supplies the required
acceptance-obligation, process-checkpoint, or architecture-conformance binding, and obtains an
independent classification/application review whose subject digest covers that exact application
row. The selection is immutable after ignition; changing
the selected documents starts a new run rather than mutating the meaning of one underway. Then one
compiler owns every generated region:

```bash
python3 harness/phase_compiler.py update \
  --root .factory/runs/<run> \
  --artifacts .factory/runs/<run>/artifacts
```

`phase1_gate.sh` re-derives semantic union → guidance → agreement and byte-compares the result.
Dispatch automatically adds only the role's applied projection; the Tester receives behavioral
obligations only and never the source document or a constructional recipe. The Orchestrator's
assessment/3 is bound to the current selection, application, evidence, and finding digests.
Pre-dispatch requires `routing-verified`; pre-verdict requires `evidence-complete`. Endgame verifies
`artifacts/guidance/evidence.json` against the exact candidate for every applied obligation. Each
cited observation binds the run, generation, selection, application, obligation, candidate,
verification method, and non-empty raw evidence references. A resolved finding requires the same
candidate-bound typed resolution shape; an old remediation receipt cannot be relabelled under a
new candidate. Mechanical verification proves exact
source, routing, subject, and evidence membership; the Validator, independent reviewer, and
resident Orchestrator judge whether the route and evidence actually satisfy the selected guidance.

## Cross-path agreement evidence

New runs declare `factory-agreement-contract/1` at ignition. Before ratification, enumerate every
participant for every Product requirement into `artifacts/agreement/participant-inventory.json`,
write the exact `artifacts/agreement/contract.json`, and render it into the Testing Strategy:

```bash
python3 harness/agreement_contract.py update-strategy \
  --root .factory/runs/<run> \
  --artifacts .factory/runs/<run>/artifacts
```

Participant cardinality derives the class: one is single-path; two or more is cross-path and has
no downgrade flag. Cross-path entries must name the shared authority, semantic residue, relational
oracle, asymmetric mismatch plans, and all rollout/data/retry/duplication/ordering/error axes.
`phase1_gate.sh` verifies exact membership and generated bytes.

In Phase C, run `agreement_probe.py` once for `producer` and once for `consumer`, using distinct
patches. Each probe archives the exact candidate commit, proves both selected suites green before
mutation, proves the unchanged local suite stays green while the unchanged agreement oracle turns
red, and emits a content-addressed witness. Assemble those references under
`artifacts/agreement/evidence.json`; `endgame.sh` rechecks the candidate, suite, oracle, commands,
and both witness directions. Testing quote and hold separately—even end to end—does not prove that
they agree on the same decision. Historical runs with no agreement ignition field remain legacy.

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
- Keys are per-**project** and the human holds none: `factory init` mints one key per worker,
  a host-held human-principal key that records only what the human said, the genesis and the
  ledger chain root, into `.factory/keys/`. Each worker is granted only its own key
  (`.factory/keys/grants/<role>`, consumed by `lane_env.sh`).
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
- `tripwire.sh` — control 5: credential-shaped content → HALT, human-cleared only. `ground.sh` scans only this run's seat transcripts (the workdir's Claude project dir and any `<dir>-*` nested ones); a sibling workdir such as `/repo/x-other` for `/repo/x` shares that prefix and is scanned too, because Claude's directory encoding cannot tell them apart. Set `TRANSCRIPTS=<paths>` to pin the scan.
- `relay_file.sh <run> <window> [--file <path>]` — relays multi-line text into a seat by file: writes the exact bytes to `<run>/relay/<sha256>.txt` and sends one `FACTORY_RELAY file=… sha256=…` line through `inject.sh` (topology, content guards and receipts still apply). `inject.sh` does the same on its own for any multi-line or oversized message. The seat recomputes the digest before acting.
- `sched_audit.sh` — control 6: unregistered OS timer = hostile. (`SCHED_AUDIT_INPUT`
  is a test seam for the forced-negative drill.)
- `ground.sh` — control 7 + 9: resume from disk; reconcilers under `.factory/reconcile.d/`.
- `factory.sh` opens a persistent Orchestrator plus the operator-owned Validator/dispatcher
  coordination surface. `orchestrator_channel.py` carries the complete activity cursor and
  monotone assessments; `orchestrator_checkpoint.sh` requires them before dispatch/verdict.
  `tmux_lane.sh` gives unqualified author lanes local Git checkpoints and requires a
  status/diff plus relevant-check audit before each commit; `lane_agent.py` names its agents,
  preflights their sign-in and wraps every lane process; `lane_budget.py` is the one writer of
  the objective-budget ledger; `lane_watchdog.py` is each lane's stall, cap and completion
  watcher; `testing_standard.py` builds the Tester's mandatory first reading;
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
2. **Hub ownership**: the prompts agree that under the harness the dispatcher is the
   hub (`validate.md` defers to `orchestrate.md`); who owns that seat is still to ratify. This build gives lanes to the Validator and makes the
   Orchestrator an independent resident monitor (dispatcher → Orchestrator pane;
   Orchestrator report → monotone blocking channel). It cannot inject into the
   Validator or an author lane. `/orchestrate`'s direct relay-to-lanes behavior is
   intentionally not implemented.
3. **Tester falsifiability under interface-only projection**: `test.md`
   (Falsifiability) now asks for the mutation named at the contract, which is how
   the gate is honored today; Gate D applies it to production code. Ratify that, or
   grant a post-freeze mutation pass to the Validator.
4. **"Real ingestion paths unless optimal"**: doctrine's criterion is the §9
   dependency table, and "real" never means prod. The endgame runs real paths in
   disposable checkouts/previews only.
5. Founder decisions still open from the PR stack: n=1/I2 collision, Verboten list
   as data file, `check_authority` ban scope, receipt-reuse reach of I6, and the
   `_derive` prior-phase continuity gap recorded on PR #11.
