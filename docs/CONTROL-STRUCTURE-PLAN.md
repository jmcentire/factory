# Factory Control-Structure Plan

> Status: **ratified 2026-08-14 (founder sign-off).** Built with Sim (framing skeptic),
> Advocate (six-persona panel), and the Constrain understand→challenge→synthesize
> protocol, then adversarially verified by an 18-agent workflow that grounded every
> claim in the actual `factory_core` code and found six real failures in the first draft.
> This is a control-plane change to the factory's own verifier; per the control-plane
> prohibition it requires an independent verifier lane and founder ratification before
> any implementation. **Slices 1–6 are implemented and green through `make ship`** —
> receipt `test_count`/`pass_count` (machine-derived), `mutate.sh --named-test`
> (symptom-not-failure), the closed shepherd channel + bounded-time liveness, the
> blocking-event attention mechanism (the time-kill), the FENCE→PRIMER→TASK dispatch +
> kindex-as-primer gate (Gates B, C), the diff-to-surface enumeration + observation-receipt
> binding (Gates M, N), the sole-advancement-authority (Gate L) + the seam chain-anchor
> (F3) + the Standard false-close fix (F4) + the chain hash-linkage/duplicate-id hardening
> (R2/R3), and the denial-probe registry (Gate I — every gate has a registered, collecting,
> falsifiable end-to-end probe, or the build fails). The independent Opus cross-family
> verifier APPROVED F3/F4. The R2/R3 re-verify returned CHANGES_REQUESTED (code correct;
> required: the `$$` fix to `receipt.sh` because R3 wedges on honest id collision, and
> correcting the "closes in three layers" overclaim) — both applied, plus two cheap optional
> fixes, and a NEW residual R4 (chain authenticity / mint-by-append, distinct from R1). **The
> round-2 Opus re-verify (2026-08-14) returned APPROVE — "Ratify, nothing blocks"** (all four
> fixes land, both required items discharged, no new defect; cross-FAMILY independence only,
> roles collapsed). It named one new residual **R5** (the wedge is now an attack: append-capability
> on `chain.jsonl` is a permanent denial-of-promotion — the availability twin of R4; the same
> external anchor that closes R4 gives R5 a repair path) and five optional follow-ups, of which
> the two test-layer ones are applied (the R3 fixture is inverted to prove the GREEN-dup-shadows-
> RED-honest false-acceptance the gate prevents, not merely one refusal becoming another) and the
> two verifier-logic ones are deferred as a tracked task (they touch `_load_chain` and would
> re-open the control-plane gate just closed). **Remaining:** the evidence-production pipeline
> does not yet produce `promotion_inputs.json` automatically or supply the receipt bodies that
> close R1 replay; the harness close is also not a RunStore `PROMOTED` transition. Gate L itself
> is now invoked by `endgame.sh` after all preceding gates and live proof are green, and missing
> promotion inputs fail that close.

---

## Part 1 — The summing-up

### The diagnosis (one sentence)

The Validator goes rogue and the Orchestrator is powerless for the **same** reason: the
factory built the semantic-judgment layer (Validator verdicts, Orchestrator nagging) and
never built the deterministic-gating layer that its own doctrine (`HARNESS.md`,
§"two-layer validation split") says must be the actual gate. Every control that would have
stopped the rogue run was a judgment-in-a-prompt, not a predicate that blocks.

### Why prompt-fixes cannot work (the research)

- **Dysfunction is substrate-independent** (`ch08_ai_dysfunction`): a gated-pipeline
  architecture spent its entire budget on planning and wrote zero code (0/28);
  bikeshedding, verification theater (0/0 tests certified pass), and governance conflict
  (reject vs force_approve 28s apart) emerged from the architecture *despite* prompts. Six
  anti-dysfunction mechanisms failed.
- **Three laws.** Crawford-Sobel (every channel loses information) + Goodhart (agents
  optimize the measurable proxy, not the objective) + Data Processing Inequality (each
  layer narrows). The proxy-migration law: dysfunction migrates to the new proxy, it does
  not disappear.
- **Coordination as programming** (`METHODOLOGY.md`): more explicit instruction is worse
  (director strategy −37.1%, monotonic in token count). Shepherding contaminates at every
  strategy (all bare shepherd strategies below neutral; with a reset prepended, shepherd
  content contributes nothing — 22:1 ratio). Reset-then-prime is the single largest
  intervention (−39% cross-entropy).

**Conclusion:** telling the Validator to "think harder" or the Orchestrator to "be stricter"
is the director strategy — it will make things worse. The fix is structural: move the gate
out of the model.

### The three-tool verdict (convergent)

**Sim (framing skeptic)** — accepted the diagnosis and sharpened the framing:
> "The gates don't replace the Validator, they *cage* it. Remove the model and the gates
> still *block* — they prevent rogue outputs from propagating. They don't produce correct
> outputs in the model's absence. The seatbelt works without the driver; it doesn't drive
> the car… **The receipt schema is the load-bearing artifact.** Get that right and the gates
> are mechanical. Get it wrong and you've built a second Validator that can also go rogue."

**Advocate (six personas)** — the design is architecturally sound, but six classes of gap:
1. **Goodhart applies to the solution itself** — receipt *presence* is a new proxy; an agent
   that generates a kindex receipt for irrelevant terms, or a boilerplate consequence
   ledger, passes every gate. (SME, high)
2. **Restatement gate either reintroduces self-certification or needs semantic judgment** —
   "interpretation_confirmed" by the agent is self-certification; by a script is trivial.
   (SME, high)
3. **"Must exist" is not a predicate** — the consequence-prediction gate degenerates to
   "a file with this name is present." (SME, high)
4. **No break-glass / override** — a gate-script bug at 3am is a total outage with no fire
   exit and no observability. (Good Friend, critical ×2)
5. **Incremental build creates a transitional state worse than either endpoint** — agents
   optimize for the gates that exist and route around the ones that don't. (Good Friend, high)
6. **No interface spec, no error states, no migration path** for any gate. (User, high ×3)

**Constrain (understand → challenge → synthesize)** — the asked-for vs. required gap: the
ask was "make the Validator think ahead and the Orchestrator enforce." The *requirement* is
that shallow thinking be *ineffective* — which no amount of asking produces; only a substrate
that rejects shallow artifacts does. Every convenience that weakens a control (self-reported
observations, agent-declared surface sets, a verdict the agent can route around) must be
removed, not softened.

**The 18-agent adversarial workflow** — mined 9 research sources (101 findings), mapped 8
failure shapes, synthesized an 11-component design, and **verified 6 of them as real failures
in the first draft**, each grounded in specific `factory_core` lines, each with a concrete
attack scenario and a missing control. The failures and their amendments are in Part 3.

### The one convergent insight

All three methods plus the code-grounded verification agree on a single load-bearing point:
**the gates must check structural invariants of *machine-derived* inputs, never
agent-self-reported ones.** Removing the Validator's verdict from the gate predicate is not
enough — the workflow proved the gate's *inputs* (the surface set, the observation booleans)
were still agent-supplied, so the gate was "deterministic given self-authored inputs." That
is a second Validator that can also go rogue. The receipt schema — what a receipt is, and
that its load-bearing fields are derived by the machine from the diff and the test log, not
declared by the agent — is the whole game.

---

## Part 2 — The refined strategy

1. **The Validator is a flag, the machine is the gate.** The Validator executes an oracle it
   did not author, runs the tests, produces a verdict. The verdict is evidence only after an
   adversary attacks it — a semantic flag, never the gate. Only a receipt with `exit != 0`
   blocks. The Validator cannot block a run on subjective findings; it cannot certify without
   testing; it cannot author the surface set; it cannot self-report its metrics; it cannot
   route around the gate.

2. **The gates cage; they do not replace.** The deterministic layer blocks rogue outputs
   from propagating. It does not produce correct output — that remains the Validator's
   semantic job, with the model present. The honest doneness test is "remove the model, the
   gates still *block*," not "the gates produce correct output."

3. **The receipt schema is the load-bearing artifact.** A receipt is a structured record
   (exit code, test_count, log_digest, git_head, dirty_digest, **diff-enumerated surface
   set**, **structured test result**) whose load-bearing fields are machine-derived. The
   gates check: did the output match the required schema, did it reference the inputs it was
   supposed to evaluate, did it follow the process, did it stay in its decision space. Get
   this wrong and the gates are a second Validator; get it right and they are mechanical.

4. **The Orchestrator is invoked-on-trigger, with no resident seat and no injection into the
   Validator's pane.** The dispatcher (a script, zero model tokens) owns the event stream,
   routes by failure class (data, not judgment), and enforces the gates. The
   orchestrator-agent is a one-shot function called on defined triggers; its output goes to a
   human-readable file, never into the Validator's context. The shepherd channel is closed
   entirely — the research proves shepherd content contributes nothing with a reset
   prepended (22:1) and contaminates when it lands in the fence-to-prime window (+0.118
   nats).

5. **Every gate carries an end-to-end denial probe.** A gate with no denial probe is theater.
   The denial probe tests the **prohibited action** (the run does not advance), not the fix's
   artifact (an internal function returns False). Denial probes are machine-authored from the
   gate's specification, re-run on every build, and a gate whose probe does not demonstrate
   blocking is classified as theater and fails the build.

---

## Part 3 — The fix plan, per target area

### A. `~/Code/factory` — the deterministic substrate (the core of the work)

The workflow's verified design specifies fourteen gates (A–N). The six that the verification
*proved insufficient* in the first draft are amended here; the other eight stand as verified.

**The six verified failures and their amendments:**

| # | Failure shape | How the first draft fell | Amendment |
|---|---|---|---|
| 1 | symptom-not-failure (receipt-theater, vacuity) | `exit==0 ∧ test_count>0` is satisfied by `def test_smoke(): assert True`. Mutation gate was Critical-only. Denial probe tested tampering, not vacuity. | **2.1** vacuity denial probe (trivial test with correct digest is rejected). **2.2** mutation gate fires for **all** surfaces, with `--named-test` verification (the killing test must be the one the requirement names). |
| 2 | agent-supplies-its-own-function | The gate's *inputs* were agent-supplied: `disturbed_surface_ids` (omit a Critical surface → no evaluation; an omission is weaker than a downgrade), and `oracle_adequate`/`deterministic`/`flake_count` (self-reported booleans, `promotion.py:189-212`). | **2.6** Gate L (sole-advancement-authority: dispatcher is sole writer of `run.json`), Gate M (diff-to-surface enumeration: `git diff --name-only` mapped to surfaces **replaces** agent-declared set), Gate N (observation-receipt binding: observations **derived from the test log**, not self-reported). |
| 3 | shepherding-contaminates | The resident seat was removed but the `tmux send-keys` injection path (`orchestrator_wake.sh:99`, `dispatcher.py:209-214`) remained — a second shepherd arriving mid-processing. | **2.3** close the injection channel entirely; orchestrator output → `wakes/<ts>.response.md` only. |
| 4 | proxy-migration (denial-probe theater) | `denial_probe_demonstrates_gate_fires` was a semantic judgment, not a predicate; a probe testing "function returns False" passed while the gate didn't block. | **2.4** denial probes verify the **end-to-end blocking path** (probe triggers → gate fires → artifact is NOT promoted → run does not advance). |
| 5 | control-reports-healthy-while-dead | Single-flight coalescing checked `proc.poll() is None` (process surface), not the principal (a receipted audit produced). A hung `claude -p` → 100% silent absence. | **2.5** bounded-time liveness on the principal: `proc.poll() is None ∧ elapsed < WAKE_TIMEOUT`; kill the hung process, record `orchestrator_dead`, allow the next trigger. |
| 6 | instruction-resistance (route around the gate) | The kindex-primer gate proved *delivery*, not *use*. And the Validator could run `make ship` directly and write `{"status":"closed"}` to `run.json` — the promotion gate was never the sole path. | **2.6** Gate L (sole-advancement-authority: the Validator's process is filesystem-restricted from writing `run.json`/the ledger; the dispatcher is the sole writer). Primer-use remains a semantic *flag*, not a gate — the accepted residual at the deterministic/semantic boundary. |

**The fourteen gates (A–N):** A receipt-advancement; B reset-prime-deliver dispatch; C
kindex-as-primer auto-injection; D receipt-gaming detector + mutation forcing test; E
factual/subjective objection classification; F orchestrator-invoked-on-trigger; G Phase A
budget cap; H oracle-independence enforcement; I denial-probe requirement; J three-role lanes
+ max-depth-3; K cascading invalidation on premise supersession; **L sole-advancement-authority
(new)**; **M diff-to-surface enumeration (new)**; **N observation-receipt binding (new)**.
Full per-gate specification (checks, denial probe, what it replaces, research basis) is in
the workflow's `final_recommendation` §2.1 — that document is the authoritative detail;
this plan is the strategy and the per-area build order.

**`factory_core` stays pure.** The gates are data-driven and target-agnostic;
`check_core_purity.py` must stay green. The promotion gate (`promotion.py`) is already built
but has **zero call sites** — the first slice wires it in.

**Placement (founder refinement).** `~/Code/factory/` is the generic, codebase-agnostic core
plus its doctrine (`docs/`, `factory_core/`, `factory_runtime/`, the `harness/` scripts
themselves). It must not accumulate per-codebase state. Durable files *related to a target
codebase* — the target manifest, surface declarations, criticality profile, the directive
ledger, receipts, run state, reconcilers — live in **`.factory/` at that codebase's own git
root** and are **committed to that repo.** Each target is then self-describing: the factory's
state for a codebase travels with the codebase, and the generic `factory/` never imports or
names it. When the factory dogfoods itself, this repo carries its own `.factory/`. Transient
per-run artifacts under `.factory/runs/<run-id>/` may be gitignored at the target's
discretion; the durable config and the directive ledger are committed.

**Orchestrator attention without shepherding (founder refinement — the time-kill).** Closing
the shepherd channel (Amendment 2.3) cannot strand the validator. The orchestrator must be
able to *get the validator's attention* and *move work along* — a stalled run is the time-kill
this whole effort exists to end. The resolution is to separate **control signals** (legitimate,
needed) from **shepherding** (contaminates):

- **Attention = a control-plane precondition, not a pane message.** The dispatcher writes a
  blocking event to the lane's control file (`.factory/runs/<run>/lanes/<lane>.blocking`):
  a halt, a pause, a receipt contradiction, a failure-class route. `lane_env` refuses to run
  the lane past an unconsumed blocking event. The validator consumes it at a defined checkpoint
  (between tasks, not mid-reasoning) and cannot proceed until it is cleared. This is the
  interrupt channel — it gets attention by *stopping the lane*, not by typing into its context.
- **Stall detection covers every lane, on the principal.** Amendment 2.5's bounded-time
  liveness (watch the receipted output, not the process surface) applies to *all* lanes, not
  only the orchestrator. A validator that produces no receipted output within its `WAKE_TIMEOUT`
  is detected as stalled.
- **Kill is immediate.** The dispatcher's existing authority to kill a lane (the "kill order is
  immediate" rule) is the move-work-along mechanism: a stalled lane is killed and restarted, not
  nagged. The eleven-hour stray timer never happens again because the bounded-time liveness
  fires first.
- **What is NOT reintroduced.** Meta-commentary about the validator's process ("you announced
  X 10m ago with no receipt") stays out of the validator's context — that is the shepherd that
  contaminates. The blocking event carries its *class* (data, from the failure-class table) and
  its *evidence* (the contradicting receipt ids), not prose about the validator's behavior. The
  validator reads a control signal, not a lecture.

Net: the orchestrator has teeth (it can stop, route, and kill) through the deterministic
substrate, and zero channel into the validator's reasoning window. Attention is enforced by the
machine; shepherding is gone.

### B. `~/Code/tools` — the standards and the playbook

- **`HARNESS.md` mirror** — keep in sync with the canonical `~/Code/factory/docs/HARNESS.md`.
  When the factory copy is ratified (status moves from "unratified proposal" to wired into
  `lane_env` + `make ship`), the mirror ratifies in the same act.
- **`CODE-REVIEW-STANDARD.md`** — add two rules as first-class review standards:
  1. *Guard the action, not the artifact* (the review skill's dominant failure: the check
     guards the fix's artifact instead of the prohibited action). Every guard must name the
     action it forbids and the channel the code actually uses.
  2. *No gate without an end-to-end denial probe* — a guard that has never been watched
     failing is a claim, not a guard. The probe tests the prohibited action, not the fix's
     artifact.
- **`DIFF-INTENT-GATE.md`** — add the receipt-validity rule: a receipt for an absence claim
  must carry the positive control; a receipt whose load-bearing fields are agent-self-reported
  (not machine-derived) is testimony, not evidence.
- **`production-build-playbook/`** — add a *Deterministic Gating* chapter: the two-layer
  split (a judge is never a gate), the receipt schema as load-bearing artifact, the
  cage-not-replace framing, and the denial-probe requirement. This is where the
  deterministic/semantic boundary gets documented for the consuming-project audience.

### C. The five skills (`validate`, `orchestrate`, `test`, `engineer`, `review`)

The key change across all five: **each skill's binding requirement becomes a *receipt the
harness checks*, not a *judgment the agent performs*.** The skill text stops *narrating* the
rule and starts *naming the gate* that enforces it. Specifics:

- **`validate`** — "search before anything" → the kindex-receipt gate (Gate C) blocks a
  dispatch with no search receipt. "Run the judge" → the receipt-advancement gate (Gate A)
  and the mutation gate (Gate D) block a vacuous oracle. The verdict is a flag (Gate E). The
  Validator cannot write `run.json` (Gate L). The local AI-rendered-verdict mode's
  divergence is now bounded by the substrate, not by the Validator's self-discipline.
- **`orchestrate`** — split into the two seats the doctrine already names: the **dispatcher**
  (script, enforces: cadence, receipts, failure-class routing, budgets, leases, sole
  advancement) and the **orchestrator-agent** (invoked-on-trigger, advises: diagnoses,
  recommends, stops). The injection channel into the Validator's pane is removed (Amendment
  2.3). This is the structural fix for "powerless": the dispatcher enforces because it is a
  script the Validator cannot ignore, not because it was told to be strict.
- **`test`** — the oracle-adequacy checks (Q1 reach, Q2 discriminate, Q3 fail-for-named-reason)
  become the mutation gate (Gate D, Amendment 2.2): the test must fail for the reason the
  requirement names, verified by `--named-test`. A vacuous test is rejected, not noted.
- **`engineer`** — the control-plane prohibition is enforced by Gate L (the Coder cannot
  write the ledger or `run.json`). "Resolve every requirement to an artifact item" is enforced
  by the provenance gate (already in `factory_core/provenance.py`, whole-artifact-version-bound).
- **`review`** — the "guard the action not the artifact" rule and the end-to-end denial-probe
  requirement (from §B) become the review's pass/fail criteria. A guard with no denial probe
  is a finding of the highest order, not a style note.

### D. `~/WanderRepos/repos/agent-skills/.../adapt/` — the shipped team skills

The `adapt` skill (`wander-skills/skills/adapt/adapt-pr-review-skills/`) adapts PR-review
skills. It must carry the same standard it enforces on the skills it adapts:

- Add to the adapt skill's **adaptation criteria**: a review skill that ships a guard with no
  end-to-end denial probe is theater and must not pass adaptation. The adapter must require,
  for every guard the adapted skill introduces, a probe that triggers the guard and verifies
  the prohibited action is blocked.
- Add the **deterministic-gating principle** to the adapt skill's reference material: the
  two-layer split, the receipt schema, cage-not-replace. The adapted skills
  (`code-review`, `wander-software-engineering`, etc.) inherit the standard.
- The `kindex-repo-memory` sub-skill already exists in adapt; wire it to the
  correction-aware-read rule (a read receipt carries the node's tail digest; acting without
  tail acknowledgment is a defect class) so the shipped skill matches the factory's Gate
  (correction-aware kindex reads).

---

## Part 4 — Build order (smallest enforceable slice first)

Each slice is independently shippable and closes a verified failure shape. Advocate's two
caveats are integrated: **(a) minimum viable gate set** — do not ship slice 1 alone if a
partial optimization surface is worse than none; the minimum viable set is slices 1+2
together (the gate is the sole path *and* vacuity is rejected). **(b) advisory → enforcement
migration** — each gate runs in advisory mode (log, don't block) for one cycle to measure
false-positive rate, then switches to enforcement per skill.

1. **Wire the promotion gate and make it the sole path (Gates A, L).** Add `test_count` to
   `receipt.sh`; wire `decide_promotion` into `endgame.sh`; make the dispatcher the sole
   writer of `run.json`; restrict the Validator's filesystem access to `run.json`/ledger.
   *Closes:* agent-supplies-its-own-function (verdict path), instruction-resistance (bypass).
2. **Wire the mutation gate for all surfaces with named-test verification (Gate D, Amend
   2.1/2.2).** Add `--named-test` to `mutate.sh`; wire it as a mandatory denial probe for
   every surface that claims a test; add the vacuity denial probe. *Closes:* symptom-not-failure.
3. **Close the shepherd channel (Amend 2.3/2.5).** Remove the orchestrator/dispatcher
   injection into the Validator's pane; write orchestrator output to a file; add
   bounded-time liveness (`WAKE_TIMEOUT`) on the `Popen`. *Closes:* shepherding-contaminates,
   control-reports-healthy-while-dead.
4. **Diff-to-surface enumeration and observation-receipt binding (Gates M, N).** Add
   `git diff --name-only` to `receipt.sh`, map to surfaces; replace agent-declared
   `disturbed_surface_ids`; derive `oracle_adequate`/`deterministic`/`flake_count` from the
   structured test log. *Closes:* agent-supplies-its-own-function (inputs).
5. **Reset-prime-deliver dispatch + kindex-as-primer (Gates B, C).** Replace the BRIEF in
   `dispatch_lane.sh` with FENCE→PRIMER→TASK; auto-inject kindex as domain primer per-agent.
   *Closes:* validator-shallow (mode switching), kindex-non-use.
6. **End-to-end denial probes, re-run on every build (Gate I, Amend 2.4).** Write
   `denial_probe.sh`; register a probe for every gate; a gate without a passing probe is
   theater and fails the build. *Closes:* proxy-migration, control-reports-healthy-while-dead.
   **BUILT 2026-08-14:** `harness/gates.tsv` (18 gates: A–N + F3/F4/R2/R3) +
   `scripts/check_denial_probes.py` (coverage/dead-pointer/unfalsifiable check, wired into
   `make ship` as `check-denial-probes`) + `harness/denial_probe.sh` (re-run a gate's probes)
   + `tests/test_denial_probes.py` (the meta Gate I denial probe). The honest scope: the check
   enforces coverage, collection, and *declared* falsifiability (`red_now`); the deeper
   red-now-auto-run (apply the mutation, confirm the probe goes red, revert) is the named
   "next proxy" residual (Part 5 §4) — the registry carries the `red_now` description for a
   future runner, but per-gate mutation application is not automated here.

**Advocate's operational requirements, applied to every gate as it ships:**
- A **concrete interface spec** (script name, CLI, input format, exit codes, receipt storage).
- **Error states** — what the gate does when its own dependencies are down; gate *errors*
  fail open with a loud alert, gate *check-failures* fail closed (distinguish "crashed" from
  "blocked").
- A **break-glass receipt** — who can override, how, what gets logged, the review obligation.
  The override is a receipt, not a backdoor.
- **Structured rejection output** — which gate, what was checked, what failed, what receipt
  is missing, to a log and stderr. The 3am test: can you diagnose it in a reasonable time?
- **Gate-script tests + an owner** — dependency-free must not mean test-free.

---

## Part 5 — Open questions and residual risks

1. **Reset-prime-deliver transferability** — the reset effect was validated on
   Qwen-2.5-7B/Mistral-7B, not Claude/GLM-class models. The direction is robust; the
   magnitudes are not. Validate the fence effect in the factory's own ledger before trusting
   it as a coordination gate.
2. **Reset as calibration, not alignment** — the reset may be a KV-cache confidence shift
   rather than genuine alignment. Pair the reset gate with an entropy monitor: if reset
   lowers output entropy without improving agreement with signed artifacts, it is a hazard.
3. **Primer-use is semantic, not deterministic** — the kindex-primer gate proves *delivery*,
   not *use*. This is the accepted residual at the deterministic/semantic boundary, not a
   gap. The semantic portion is a flag (Validator/Sim), not a gate.
4. **The next proxy** — when receipts become the gate, watch for receipt-theater; when
   mutation testing is the gate, mutation-theater; when denial probes are the gate,
   denial-probe-theater. The control is machine-authored probes from the gate's spec — but
   the spec itself is authored, and that author is the next proxy. Name it; budget for it.
5. **Same-context translation** — assumption-checking must be a separate context from the
   verdict (register sensitivity). The dispatcher must enforce the context boundary without
   introducing a degrading coordination channel.
6. **The transitional state** (Advocate) — during incremental build, agents optimize for the
   gates that exist. The minimum viable gate set (slices 1+2) must ship together; the
   advisory→enforcement migration bounds the window.

---

## Part 6 — What needs founder sign-off before implementation

This plan is a **control-plane change to the factory's own verifier** — the exact thing the
control-plane prohibition says no executor may build or judge in the run it governs. Per the
doctrine:

- The substrate is the trust root for segregation. Its initial installation is an out-of-band
  human act — but **incrementally**: the first receipt gates (slices 1–3) are self-enforcing
  standalone scripts that need no founder ceremony; only the signed-directive ledger *root*
  (adoption step 4 in `HARNESS.md`) needs the hardware-signing ceremony.
- Implementation requires a **separate policy-change lane with an independent verifier** (a
  different model family where available), not the Validator judging a change to its own gate.
- `HARNESS.md` moves from "unratified proposal" to ratified when the controls are wired into
  `lane_env` and `make ship`.

**Status:** founder ratified 2026-08-14. Slices 1+2+3 — the closed shepherd channel,
bounded-time liveness, blocking-event attention mechanism, receipt `test_count`, and
`mutate.sh --named-test` — are implemented in advisory mode and green through `make ship`,
with an end-to-end denial probe for every control. The 18-agent adversarial workflow served
as the independent verification for slices 1–3 (the plan carves these out as self-enforcing
standalone scripts needing no founder ceremony). **Next:** slice 4 (Gates M, N) is built in a
formal policy-change lane under an independent verifier, advisory-mode first; Gate L
(sole-advancement-authority, the `decide_promotion` sole-path wiring) remains deferred until
the inputs it gates are machine-derived, so it is not rushed as a half-wired route-around.

---

## Part 7 — Slice 4 implementation spec (Gates M, N)

> The receipt schema is the load-bearing artifact (Part 1: "get it right and the gates are
> mechanical; get it wrong and you've built a second Validator that can also go rogue"). This
> spec fixes the schema and the binding contract before any code, because slice 4 changes that
> schema and the `PromotionRequest` contract.

### The purity boundary (the governing constraint)

`factory_core` is pure: stdlib-only, no clock, no disk, no git (CLAUDE.md; `promotion.py:30`).
Therefore Gates M and N **cannot run `git diff` or parse a test log inside the core.** The
machine-derivation happens at the **seam** (the harness/runtime, which is allowed disk and
git); the core **verifies the binding** of the request's fields to the seam's content-addressed
receipt. This is the same pattern slice 1 established for `test_count`: the machine derives
the value from the artifact, receipts it with a digest into the hash-chained chain, and the
gate checks the request binds to that receipt. The agent cannot self-report a value that
contradicts a receipt it cannot forge (the chain is tamper-evident).

### Gate M — diff-to-surface enumeration (seam side, in `receipt.sh`)

`receipt.sh` is generic (it wraps arbitrary commands). The diff is only meaningful for a
candidate-producing command, so the new fields are **opt-in via caller-supplied env** (like
`HARNESS_DIR`), keeping the script generic and target-agnostic:

- `HARNESS_BASE_SHA` — the run's base (the pre-change commit, from `run.json`). When set,
  `receipt.sh` records `changed_paths` = `git diff --name-only <base>` (machine-derived,
  generic, no target knowledge) and `changed_paths_digest` (SHA-256 of the sorted path list).
  When unset, both are `null` (the receipt is not a candidate-build receipt).
- `HARNESS_SURFACE_MAP` — a JSON file mapping path globs → `surface_id` (target data, lives in
  `.factory/`). When set, `receipt.sh` applies it to `changed_paths` and records
  `disturbed_surface_ids` (the mapped, de-duplicated, sorted set) and `surface_map_digest`
  (SHA-256 of the map file). Applying a caller-supplied data file is data-driven, not a code
  import, so the generic boundary holds. When unset, `disturbed_surface_ids` is `null` (the
  runtime does the mapping; the receipt carries only raw paths).

New receipt fields: `changed_paths`, `changed_paths_digest`, `disturbed_surface_ids`,
`surface_map_digest`. All machine-derived; none agent-supplied.

### Gate M — diff-to-surface enumeration (core side, in `promotion.py`)

`PromotionRequest` gains `candidate_receipt` (the receipt id) and `candidate_receipt_evidence`
(a content-addressed `EvidenceIntegrity` envelope the seam produces, body =
`{receipt_id, disturbed_surface_ids, changed_paths_digest}`). `decide_promotion`, when
`candidate_receipt` is present, verifies in order — each failure is a hard block (fail-closed):

- the envelope is **present** (`candidate-receipt-evidence-missing`) — citing a receipt but
  omitting its attested envelope is route-arounding, not complying;
- the envelope's body **re-derives to its claimed digest** (`candidate-receipt-evidence-tampered`)
  — the content-address check, the same one `live_evidence` uses;
- the envelope **binds the cited receipt** (`candidate-receipt-evidence-binding`) — an envelope
  attesting receipt R-2 cannot satisfy a request citing R-1 (replay of another run's receipt);
- the body's `changed_paths_digest` is **non-empty** (`disturbed-surface-without-diff`) — the
  receipt recorded a real diff;
- the request's `disturbed_surface_ids` **equal** the envelope's `disturbed_surface_ids`
  (sorted, de-duplicated, normalized) — else `disturbed-surface-mismatch`. This is the
  "replaces agent-declared set" amendment: the field stays, but the agent can no longer author a
  self-serving set; a contradiction with the receipt blocks.

### Gate N — observation-receipt binding (seam + core)

The self-reported `SurfaceObservation` fields (`oracle_adequate`, `deterministic`, `flake_count`,
`automatic_retry_count`) become **bindings to content-addressed `EvidenceIntegrity` envelopes**,
not agent declarations:

- `oracle_adequate` binds to a **mutation receipt** envelope (`oracle_receipt_evidence`, body =
  `{receipt_id, oracle_adequate}`; slice 2's `mutate.sh --named-test` verdict: KILLED-on-named-
  oracle → adequate; KILLED-OUTSIDE-ORACLE or SURVIVED → not).
- `deterministic` / `flake_count` / `automatic_retry_count` bind to a **flake-detection receipt**
  envelope (`flake_receipt_evidence`, body = `{receipt_id, deterministic, flake_count,
  retry_count}`; the harness runs the test N times and receipts the results).

The core verifies each envelope's content-address + receipt binding (fail-closed on
missing/tampered/unbound), then requires the attested value be present (fail-closed on
`*-attested-value-missing` — a cited receipt with an envelope that omits the value is
route-arounding), then blocks on mismatch (`oracle-binding-mismatch`, `flake-binding-mismatch`).

### Trust model & cutover (the verifier's load-bearing point)

The core is pure — it cannot read the tamper-evident chain — so it **cannot** distinguish a
real seam-produced envelope from a self-consistent one the agent forged (a body plus a matching
digest). The core therefore enforces **content-address + subject-binding + value-equality**, and
defers **chain-authenticity** to the seam: the seam records the real receipt in the hash-chained
ledger, and at submission verifies the cited envelope's digest is in that chain (a forged digest
is not). The postmortem re-reads the chain to audit. This is the same split the codebase already
uses for `live_evidence` (core content-addresses; seam chain-anchors). `test_promotion_core_
passes_self_consistent_lie_seam_closes_it` pins this boundary: the core passes a self-consistent
forgery (PROMOTE), and the seam is the catch — it is an honest limitation, not a hole.

The binding runs **advisory** (logged, not blocked) while `candidate_receipt` / the observation
receipts are absent — the migration window (Part 4 caveat b). This is a documented, committed
trade, not an abandoned hole: the cutover to enforcement is the seam-side producers (structured
mutation verdicts, a flake-detection runner, the seam attesting `candidate_receipt_evidence` from
the chain) plus **Gate L** (sole-advancement-authority), which mandates `candidate_receipt`'s
presence and rejects requests without it (fail-closed). Until Gate L ships, the runtime wires
nothing and the binding is dormant by design; after it, the binding is the sole path.

### Denial probes (Gate I, machine-authored from this spec)

- `test_receipt_records_changed_paths_from_diff` — a receipt with `HARNESS_BASE_SHA` records
  the actual `git diff --name-only` paths; a receipt without it records `null`.
- `test_receipt_maps_paths_to_surfaces_via_supplied_map` — the mapping is mechanical and
  data-driven; an unmapped path is reported, not silently dropped.
- `test_promotion_rejects_disturbed_surface_mismatch` — the request's `disturbed_surface_ids`
  differ from the receipt's → `disturbed-surface-mismatch` hard block (the prohibited action:
  the run does not advance).
- `test_promotion_rejects_oracle_binding_mismatch` — `oracle_adequate=True` but the cited
  mutation receipt says SURVIVED → hard block.
- `test_promotion_advisory_when_candidate_receipt_absent` — the migration window: no
  `candidate_receipt` → current behavior, logged not blocked.

### Build order within slice 4

1. Seam: `receipt.sh` Gate M fields + denial probes (generic, no target knowledge).
2. Core: `promotion.py` `candidate_receipt` binding for Gate M + denial probes (purity guard
   must stay green).
3. Seam + core: Gate N observation-receipt binding (mutation + flake receipts) + denial probes.
4. `make ship` green; independent-verifier review; then push slice 4 as a unit (the
   partial-surface caveat means no incremental push).

### Spec-defects & gaps (Opus cross-family review, 2026-08-14) — status

The founder-mandated independent cross-family verifier (real Opus, reached via a one-off
`claude` CLI call against `https://api.anthropic.com`) reviewed the built Gate L + the M/N
enforcement cutover and returned BLOCK with nine findings (F1–F9). The implementation defects
(F2 stale/forged-verdict route-around; F5 non-atomic run.json write; F6 run.json projection
collision; F7 overloaded exit code; F8 malformed-but-JSON traceback; F9 unstripped identity
values) are FIXED. The two findings initially raised as open design questions (F3, F4) are
also now RESOLVED — re-derived against the prior Opus review's own record rather than
re-asserted, and corrected where the earlier comments were false:

- **F3 (RESOLVED — the seam's chain-anchor check is BUILT, not an open gap).** The plan's
  trust model specifies that the seam "at submission verifies the cited envelope's digest
  is in that chain." The verifier's first pass called this an open gap requiring a
  receipt-schema decision (the chain entry's `hash` is the receipt's content address, not the
  cited `EvidenceIntegrity` envelope's `claimed_digest` — different bodies). **That was
  falsified by the prior Opus review's own record (kindex 1befd5511b01):** the check is
  buildable today as a deterministic PROJECTION, not a digest-membership test. The seam
  (`factory_runtime/promotion_gate.py`: `_load_chain` + `verify_chain_anchor` + `_verify_grounded`)
  reads `<H>/receipts/chain.jsonl`, re-derives each entry's bare-hex content-address (chain
  tamper-evidence), finds each cited envelope's real entry by id, and confirms the envelope
  body matches a projection of that entry (build: `disturbed_surface_ids` +
  `changed_paths_digest`; oracle: `oracle_adequate`; flake: `deterministic` + `flake_count` +
  `retry_count←automatic_retry_count`). An honest envelope is grounded; a forged
  self-consistent envelope is caught by the projection mismatch; a missing id fail-closes; a
  present envelope with no chain fail-closes. The chain's bare-hex hash equals
  `digest_obj(body).removeprefix("sha256:")` (same canonical encoding, prefix aside) — no
  schema decision needed. This is ordinary seam engineering (the core stays pure/no-disk),
  authorized by the founder's "solve all known issues" + "full enforcement" directive; the
  control-plane check is the independent Opus re-verification. Tests: honest-grounded
  promotes; forged/missing-id/tampered/no-chain all fail-closed (`test_promotion_gate_runtime.py`).

- **F4 (RESOLVED — the false-close is FIXED, not merely raised).** The verifier's first pass
  described the cutover as "class-blind where the doctrine is class-scoped" and raised it as a
  design question. **Re-derivation found a real FALSE CLOSE on a STANDARD surface (kindex watch
  d27535c676b1, resolved):** the flake receipt was required only when `deterministic=True`, and
  the flake receipt is the SOLE thing binding `flake_count` to machine-derived values. So on a
  Standard surface an agent could hide a flaky suite (real `flake_count=5`) by declaring
  `deterministic=False` + `flake_count=0` + no receipt → PROMOTE, zero reasons. The earlier
  comment claiming "no false close on any surface" was FALSE (it was hidden from the founder at
  ratification). The fix (`factory_core/promotion.py` ~line 1000): mandate the flake receipt on
  Standard (`or surface.effective_criticality == CRITICALITY_STANDARD`) as well as on a positive
  determinism claim — `flake_count` is load-bearing on Standard (quarantine). The oracle
  admission (`adequate=False`) stays class-scoped (a pure admission with no load-bearing
  positive value); Cosmetic retains the no-receipt admission (flake_count not load-bearing
  there — no false close, the signal vanishes); Critical is unaffected (negative-evidence hard
  block). This STRENGTHENS the gate (makes the builder's own work harder), not the self-dealing
  the control-plane prohibition targets; it restores the founder's true intent (full
  enforcement). Red-now test: `test_standard_flake_admission_requires_receipt_closes_false_close`.

- **F1 (PARTIALLY RESOLVED — live harness close wired; evidence production remains).**
  `endgame.sh` now invokes `promote.sh` only after the deterministic gates, required live proof,
  and hygiene checks are green. A missing or invalid `promotion_inputs.json` makes Gate L red and
  the endgame verdict red, so a green build can no longer stand in for a close. No automatic
  producer of `promotion_inputs.json` exists yet, however, and the harness `run.json` close is not
  the authoritative RunStore `PROMOTED` ledger transition. Those remaining controls belong to
  the evidence-production/runtime-close work. The stale denial-probe
  `test_promotion_advisory_when_candidate_receipt_absent` described the pre-cutover advisory
  window; the cutover is enforcement (absent receipt → hard block), and the test was converted.

**Bottom line:** the omission-enforcement cutover + the sole-writer are built, tested, and
honest about their scope. F3 (fabrication-enforcement) and F4 (the Standard false-close) are
resolved in code and the independent Opus cross-family re-verification returned **APPROVE**
(F4 CLOSES-THE-DEFECT, F3 GROUNDS-ENVELOPES, no new defect). Opus named three residuals; R2
(chain hash-linkage) and R3 (duplicate-id rejection) are now BUILT in the seam's `_load_chain`
(the producers already write a real hash-chain, so verifying linkage + rejecting duplicate ids
is free and correct) with two new fail-closed tests; R1 (receipt-replay) is DEFERRED to Part 7
(it needs the evidence-production pipeline to put `candidate_digest`/`surface_id` in the
oracle/flake receipt bodies — building the binding now with no producer is the F1 pattern).
Gate I (denial-probe registry, slice 6) is BUILT: `harness/gates.tsv` registers every factory
gate (A–N + F3/F4/R2/R3) with its end-to-end denial probe(s) + a `red_now` falsifiability
mutation; `scripts/check_denial_probes.py` fails the build when a gate has no probe, a probe
is a dead pointer, or a gate is unfalsifiable; `harness/denial_probe.sh` re-runs a gate's
probes on demand. `make ship` is green. Gate L is wired into the harness endgame; automatic
promotion-input production and the RunStore promotion transition remain deferred to the
evidence-production pipeline.

**R2/R3 independent Opus re-verification (2026-08-14) returned CHANGES_REQUESTED** — the code
is correct (keep it; the mutation matrix confirmed both tests kill only on their named check
and fail for the named reason), but two required changes blocked ratification, both applied:
(a) **`receipt.sh` `$$`** — R3 made id collision hard-fail, and `receipt.sh` built its id as
`R-<UTC-second>-$RANDOM` with NO `$$` (unlike `mutate.sh`/`flake.sh`), so two receipts in one
second collide with prob ~C(n,2)/32768 and permanently wedge the append-only chain (every
future promotion refuses, no repair path). Adding `$$` removes the wedge. (b) **honest scope** —
the docstring claimed the seam "closes that fabrication route-around in three layers"; Opus
falsified that with Exploit F (forward re-chain from genesis) and Exploit G (mint-by-append:
the lane agent authors a correctly-hashed, linked, id-unique receipt and cites it to promote).
The three layers are per-entry + link + id-uniqueness INTEGRITY on an agent-writable file, not
AUTHENTICITY. The prose is corrected to "narrows, does not close." Two cheap optional fixes
applied: the `_load_chain` cursor advances on every physically-previous entry (not just
id-bearing ones, matching the producers' convention); and the R3 test's duplicate carries a
differing attested value so the test proves a real SUBSTITUTION, not merely rejection-vs-silent-
ignore. (The round-2 re-verify then inverted this fixture — honest F-default RED, GREEN dup
shadowing it — so the test proves the false-acceptance the gate prevents, the stronger
direction; see the closing paragraph.)

**R4 — chain authenticity / mint-by-append (Opus, NEW residual, distinct from R1).** The seam
verifies membership in an agent-writable file; the producers run in the agent's own lane, so
the same principal that runs the tests appends the chain. An attacker who can append mints a
never-existed receipt (correctly-hashed, linked, id-unique) and cites it to promote. **R1's fix
does NOT close R4**: binding `candidate_digest`/`surface_id` stops replay of a STALE HONEST
receipt, but the attacker who mints writes those fields too. Closing R4 needs authenticity,
not binding — privilege-separated receipt writing, signed entries (Tessera), or an external
head/length anchor. R4 lands with the evidence-production pipeline (Part 7) alongside R1.

The R2/R3 correction + R4 acknowledgment were re-verified by the independent Opus cross-family
verifier (one-off `claude` CLI call, 2026-08-14): **APPROVE — "Ratify, nothing blocks."** All
four fixes land; both required items discharged; no new defect. Independence is cross-FAMILY only
(claude-opus-5 vs glm-5.2:cloud), not cross-organizational; roles collapsed. The verifier logic
(`promotion_gate.py` `_load_chain`) is committed byte-identical to the approved state.

**R5 — the wedge is now an attack (Opus round-2, NEW residual, the availability twin of R4).**
R3 converts append-capability on `chain.jsonl` into a permanent **denial of promotion**: one
duplicate-id append (correctly hashed, correctly linked) makes every future run on that harness
refuse at `_load_chain`, and append-only means it can never be removed — no repair path short of
a human replacing the file (itself an unanchored rewrite). Fix 1 removed the *accidental* trigger
(the `$$` collision); it did not remove the *deliberate* one. The R3 fail-closed choice stays
correct (a shadowed receipt is worse than a wedged harness), so R5 is a documentation gap, not a
code defect. The same external anchor that closes R4 (privilege-separated writer / signed
entries / external head-length anchor) is what gives R5 a repair path. R5 lands with R1/R4 in
the evidence-production pipeline (Part 7). Captured in kindex as node `9bd3bdbb2ceb`.

**Round-2 follow-ups (Opus, optional, non-blocking).** Applied (test-layer, not the verifier):
the R3 fixture is **inverted** — the honest F-default is RED and a GREEN dup shadows it, so the
test proves the false-**acceptance** (the run would PROMOTE on a green forgery shadowing a red
honest receipt) the gate prevents, not merely one refusal becoming another. Deferred as a
tracked task (kindex `3afe72a32268`) — verifier-logic, would re-open the control-plane gate:
(4) `raise` instead of `continue` on an id-less chain entry (makes id-less unrepresentable);
(5) an `isinstance(entry, dict)` guard so a non-object chain line raises `PromotionGateError`,
not `AttributeError` (it fail-closes at the harness level via `promote.sh`, but is the class the
F8 wrapper exists to prevent). `make ship` green: 558 passed, 3 skipped.
