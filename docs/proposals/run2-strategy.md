# Run-2 Strategy: From Conformance Engine to Product Factory

Status: **proposal, pending operator ratification.**
Provenance: drafted 2026-08-27 from the run-1 post-mortem (operator-authored, kindex
`f759632fc504`), then stress-tested through three independent lenses billed to the
Wander key: **Sim** (five claim-level rulings), **Constrain** (bounded clean-room
interview; challenge findings harvested, synthesis artifacts rejected per standing
rule), and **Advocate** (six personas, 55 findings). Lens rulings are captured as
kindex decisions linked to `da13cbaca4f3`. Kindex is context, not authority — only the
ratified versions of the artifacts below authorize anything.

## 0. The core fix, in one sentence

**No verdict exists until someone with nothing to protect runs the product from its
real entrypoint and watches the human-visible outcome happen.** Everything below is
the machinery that makes that sentence enforceable rather than aspirational: what
counts as evidence, how completion claims compose, and how a run that cannot reach
"yes" terminates honestly. (Terms of art — seat, lane, oracle, generation tuple —
are defined in `docs/DOCTRINE-KERNEL.md`; this document assumes them.)

## 1. The failure run 2 must make impossible

Run 1 shipped a well-verified skeleton. Not because anyone lied: each seat truthfully
scoped `__DONE__` down to "my assignment," and the composition of true local statements
produced a false global claim. The verdict overclaimed relative to its evidence —
PASS_WITH_RISK_ACCEPTANCE asserted "risks known and named" over territory the Tester
had explicitly declared uncovered — and the mechanism that permitted it was epistemic:
**summary prose was granted authority the coverage map never earned.** Every gate
measured a local property; "it works" is a global one; the one test of the product's
purpose sentence was never written, and the only check that reaches a framing error
(cold-context review) arrived post-disposition.

A factory that ships a defective product with an accurate defect list is working. A
factory whose verdict is wrong about its own product has failed at the only thing that
makes it a factory.

## 2. What run 2 keeps — and what it must re-earn

Kept unchanged: frame-interior verification, bidirectional discrimination (run 1
accepted ten true external findings and refuted a false HIGH the same day), provenance
and chain-of-custody rigor, refusal behavior, SoD, the tamper-evident ledger, data-only
targets, purity, bounded attempts, independence tiers.

Re-earned, not inherited (Advocate): **run-1 attestations are not valid run-2 inputs
by default.** Any attestation that relied on prose-attested controls is invalidated
and must re-fire under run-2 gates; the artifact-inheritance policy (carried as-is /
re-verified / discarded) is declared explicitly per artifact class at A2. Carrying
forward attestations produced by a known-flawed process without re-verification is the
same error as trusting the original verdicts.

## 3. Epistemic architecture (the primary fix)

Sim's ruling, adopted: the fix is not verdict wording — it is **what counts as
evidence for what claims, enforced structurally.**

- **The coverage map is the only input the verdict function accepts.** Prose
  summaries, confidence assertions, and risk acknowledgments are not valid inputs.
  Territory the Tester declared uncovered is UNKNOWN regardless of any synthesis. The
  verdict function is mechanically unpersuadable.
- **Forced first line** of every verdict: *"Does it do the thing it was built to do?
  YES / NO / NOT-DEMONSTRATED."* Anything but YES caps the verdict at INCOMPLETE; no
  PASS variant is reachable. (This first line *is* the simple three-state grammar;
  the richer vocabulary below it exists to carry scope, never to soften the headline.)
- **Verdicts shrink to fit coverage.** PASS_WITH_RISK_ACCEPTANCE is issuable only when
  every named-uncovered territory carries a characterization receipt. Otherwise the
  only honest form is PASS-on-covered / UNKNOWN-on-named-mass. A declared-uncovered
  list is a prediction, never a disclaimer.
- **The characterization receipt is a defined artifact** (Constrain's back-door probe):
  issued by the seat that fired the probe (Tester, or frame-check for demo-adjacent
  territory) — never the Validator, which holds no evidence-producing power; contents
  are the content-addressed territory element (with an explicit backreference to the
  Tester's uncovered declaration), the fired probes with chain-of-custody, the
  observed risk shape, and any residual UNKNOWN remainder. **Adequacy is never
  judged**: per-territory adequacy criteria are ratified at A4 inside the coverage
  map, so acceptance is schema conformance plus signatures. Territory that surfaces
  mid-run has no ratified adequacy criterion, so no receipt can exist for it — it
  stays UNKNOWN until a human re-ratifies at a savepoint. Receipts enter the ledger at
  issuance; the verdict function accepts only receipts whose chain position precedes
  verdict computation (no retroactive construction). A receipt never overrules a
  Tester declaration — it supersedes it in time, valid only with the backreference and
  ratified adequacy met; an unresolved Tester contest is an escalation, never a
  Validator choice.
- **Monotone composition law** (Constrain's cross-mechanism stress test, stated as an
  explicit constraint): every evidence channel — demo binary, assumption UNKNOWNs,
  handover scopes, oracle results — can only *remove* PASS-eligibility; only a signed
  characterization receipt can restore it. A green demo does not clear an UNKNOWN on a
  surface it crosses; a failed demo caps at INCOMPLETE regardless of pristine coverage
  elsewhere. No signal can launder another.
- **Coverage of coverage** (Advocate): the map's own completeness is audited, not
  assumed. Before the map computes a verdict, an independent top-down derivation of
  expected coverage territory from the purpose sentence is compared against the
  bottom-up map; territory expected but absent is a **map defect**, not a coverage
  gap — run 1's "silently does nothing was never on the map" enters here.
- **Two kinds of unanticipated surface** (Constrain's final probe): (a) inside the
  product's behavioral scope but missed at enumeration — a frame defect requiring
  re-ratification (the coverage-of-coverage derivation is the detector); (b) genuinely
  outside scope — substrate, environment, implementation detail — where permanent
  UNKNOWN is the correct state and no receipt is owed. The classifier is derivability
  from the purpose sentence and A2 scope; an unclassifiable surface defaults to (a)
  and escalates, because misfiling a scope surface as substrate is the dangerous
  direction.
- **Retention outlives abandonment** (Constrain's storage probe): coverage maps,
  assumption/escalation registers, characterization receipts, verdicts, and
  termination records are audit evidence — an abandoned run is itself a disposition,
  and its records persist in the ledger regardless of operator instruction; the
  operator can abandon a run, not erase its history. Visibility follows the enrolled
  roster; retention policy is declared up front (the resume machinery already verifies
  it).
- **Reporting integrity, tamper-evident** (Advocate): the summary is generated from
  the ledger (coverage map → template) as a signed, logged step; the summary artifact
  embeds the digest of the ledger snapshot it was generated from, verifiable
  downstream. The template's rules are explicit conditional logic over the coverage-map
  schema (`{verb, scenario-id, result, evidence-type, scope-boundary}`) — a checked
  artifact, not a prose convention. Caveats appear in the headline.

## 4. Completion semantics

- `__DONE__` is reserved for exactly one claim in the entire factory: the system does
  the thing it was built to do, demonstrated end-to-end from real entrypoints, real
  migration runner, real executors, flags on.
- Lane completions emit `__HANDOVER__` with a **typed, machine-checkable payload**
  (Sim): `{from, claim, evidence, scope: {completed, explicitly-excluded,
  assumed-in-scope-by-others}, preconditions_for_next}`. The Validator cannot silently
  aggregate; the gap between "dispatch items implemented" and "production-operable
  system" lives in the schema where a gate can catch it.
- **Scope-union check** (Advocate/SME): `__DONE__` is reachable only when the union of
  all `__HANDOVER__` scope declarations covers the full ratified verb set — checked
  mechanically, so N handovers that each silently omit the same item cannot compose
  into a global claim.
- **Emission control** (Advocate/Red Team): `__DONE__` is issuable by exactly one seat
  (the Validator) and only when the gate conditions are computably met (all gate
  digests present, forced first line = YES). The token appearing anywhere else — any
  lane, any artifact, any reviewed content — is an integrity violation that halts the
  run, not a claim.
- **Retraction is first-class** (Advocate): when a dependency change forces revision of
  a completed lane, its `__HANDOVER__` is superseded by a signed record with a
  provenance link to the forcing event (the existing supersession pattern). The ledger
  never carries a silently false completion claim.

## 5. The does-the-thing scenario

- Phase A carries a signed scenario **family** per product verb, written in the
  product's purpose language, red before the build, green after, executed before any
  verdict exists. It is the wiring gate, integration tier, and acceptance test
  collapsed into the one test a well-verified skeleton cannot satisfy.
- **Ratifiability criterion** (Advocate/Adversarial): a scenario is ratifiable only if
  expressed as observable external behavior — a non-author, given only the purpose
  sentence and the scenario description, can judge pass/fail without access to
  internals. This is what keeps A4 ratification from being theater the operator cannot
  evaluate.
- **Failure-injection variant required** (Advocate/Good Friend): the family includes at
  least one silence-detection case — kill the worker/executor mid-flow and verify the
  system *surfaces a finding* rather than stalling silently. Detecting "does nothing"
  is part of doing the thing; this is gate 6's scenario-level face.
- **Anti-Goodhart instance selection**: A4 ratifies the scenario *space* (paths, error
  cases, concurrency variants); the **concrete instance is chosen by the frame-check
  seat at gate time**, not known to the factory during the run. A fixed known demo
  would become the new defect attractor — run 1's named-ritual failure with better
  branding.
- **Synthetic-counterparty admissibility** (Sim + SME's reframing): the operative
  question is the **mandatory integration boundary** — the point where synthetic-left
  meets real-right must be the same boundary the real counterparty would traverse, and
  it must itself be exercised. A producer written to make the test pass is a mirror,
  not a counterparty. Admissibility bar: (a) behavior derived from *observed* real
  traffic (contract capture in a non-CI slot), never documentation; (b) full behavioral
  envelope — schema edge cases, timing, ordering inversions, duplicates, partial
  payloads; (c) drift re-validation on a cadence that fails the gate beyond tolerance.
  The producer is a first-class artifact with provenance requirements. Default absent
  all three: synthetic is inadmissible for this gate — real counterparty or
  NOT-DEMONSTRATED.
- **Purpose-sentence governance** (Sim + Advocate/Red Team + Constrain): the real
  gaming risk is drift — a sentence written to be passable. Three guards: (a) the
  adversarial probe — the Validator presents at least two plausible *alternative*
  purpose sentences; the operator actively rejects them; accepted and rejected are
  both recorded (passive ratification is not ratification); (b) a scope-regeneration
  check — the coverage-of-coverage derivation must regenerate the A2-agreed scope from
  the sentence top-down; A2 territory not derivable from the sentence is a sentence
  defect that blocks ratification; (c) agreement dependency tracking lets later-phase
  discoveries force A1 re-ratification. The honest residual: humans own intent — a
  wrong-but-consistent sentence ratified after forced discrimination is an attributable
  operator decision the factory must not overrule. The factory makes intent errors
  visible, bounded (everything downstream binds to the sentence digest), and cheap to
  fix (savepoint economics); it does not make them impossible, and claiming otherwise
  would be the overclaim failure again.

## 6. The interaction model (front-load agreement, then run dark)

Four individually ratified agreements, then an autonomous run. For each agreement:
initiator is the Validator; the success condition is a signed artifact; non-response
or ambiguity blocks (the phase does not close); the artifact is the listed output.

- **A1 induced understanding** → ratified purpose sentence (with the adversarial
  probe above). **A2 build agreement** → scope, non-goals, artifact-inheritance
  policy. **A3 architecture alignment** → boundaries, trust, state, deployment shape,
  and the **executor topology as an enumerated signed artifact** — (executor type,
  runtime version, concurrency model, configuration) per state machine; what cannot be
  signed here is UNKNOWN-on-executor-parity later, not assumed. **A4 test-plan
  agreement** → the coverage map: named oracles, named scenario families, named
  surfaces, each content-addressed; per-phase attempt ceilings signed here (operator-
  set; a ceiling cannot rise after authoring starts — existing doctrine). A4 closes
  only with the does-the-thing family present and **zero known frame-touching
  unknowns** (a known open edge is resolved pre-run, or its resolution procedure and
  admissibility bar are themselves a named scenario element with a decidable
  acceptance test — deferring with a named procedure is admissible; deferring with
  hope is not).
- **Dependencies are tracked, not assumed away** (Sim): a later agreement can discover
  a contradiction in an earlier one and trigger its re-ratification *before* the run
  starts. New verbs discovered mid-run (SME) do not silently extend scope: they are
  frame-touching by definition → escalation → A4 amendment or a savepoint branch.
- **Authority scope is explicit** (Sim): agreements declare what the operator is
  authorized to approve; hard-stop logic checks against that scope, not just stated
  intent.
- **The dark run**: no mid-run questions if at all possible. Three response classes,
  ratified at A4, decided mechanically — the frame is **closed and enumerated**, so
  membership is a digest lookup; anything not provably inside is outside, and outside
  is automatically UNKNOWN + escalation-eligible (fail-closed; enumeration
  incompleteness can only shrink the verdict):
  1. **ASSUMPTION** — bounded blast radius, inside covered territory: record
     `{assumption, basis, blast radius if wrong, decision taken}` and proceed. Every
     assumption enters the coverage map as UNKNOWN on the surfaces it touches — it
     shrinks the verdict by construction and can never silently hollow the frame.
     **Composition is audited, not assumed** (Advocate): dependency-chained
     assumptions carry a single shared blast radius; scope-overlapping assumptions on
     the same surface escalate; the composed register is itself a verdict input.
  2. **ESCALATION** — the unknown touches the verification frame (would render any
     scenario or oracle unverifiable or weaker — run 1's corpus-replay demotion is the
     canonical member), touches a signed A1–A4 artifact, *or* has a basis too weak to
     bound its own blast radius (Sim: otherwise dark runs launder high uncertainty
     into confident-looking records). Suspend at the savepoint; wake the operator.
     This class is priced honestly (Good Friend): an escalation costs operator
     re-engagement, and that is the point — the classes exist so that cost lands
     where the uncertainty is, not in the final report.
  3. **HARD STOP** — mechanically evaluable conditions only (Advocate): actions on
     resources tagged operator-owned, schema changes to signed artifacts,
     irreversible/destructive effects. A hard stop that requires judgment to detect
     is a hope, not a gate. Resulting state: run parks at the savepoint, operator is
     notified with the triggering condition and the preserved work; resume or branch.
- **Savepoints and branching**: every phase boundary and frozen generation tuple is a
  named resumable checkpoint. The operator reads the report, rejects an assumption,
  rewinds, and branches without repeating the agreements; digest-binding prices a
  branch at exactly the invalidated region — where "derived" is the **transitive
  closure of declared dependencies, and reads are declared** (Advocate/SME: "observed
  but not formally consumed" is not a category; lane projections already bound what a
  seat can observe, and anything observable is declared). Artifact commits carry
  branch provenance, checked at commit time (no artifact joins the ledger from an
  invalidated line).
  - **Side-effect register** (Sim): every external effect is a typed brokered handle;
    the register records each with its inverse or an `irreversible` flag. A branch is
    a resume: entry re-verifies the environment (existing resume machinery) plus
    reconciles the register diff of the abandoned head — invertible effects roll back
    mechanically; a ratified **branch-blocking class** (migrations, credential
    consumption, external counterparty writes, anything irreversible — and any
    unclassified effect type) blocks until the operator signs one of exactly two
    clearances: "environment restored, evidence attached" or "divergence accepted as
    inherited precondition" (inherited divergence enters the coverage map as UNKNOWN).
    For anticipated branches, the operator may pre-sign a **conditional clearance** at
    ratification — a closed decidable predicate over savepoint identity, effect type,
    and evidence-digest bounds, reusing the tool policy's signed/scoped/expiring
    machinery; a non-matching predicate degrades to live escalation, never auto-grant.
  - **Three termination paths** (Constrain), distinct headlines because they demand
    different operator responses:
    `NOT-DEMONSTRATED(ceiling)` — ratified attempts exhausted; report carries the full
    assumption/escalation registers, savepoint tree, and the operator's options
    (rewind, branch, re-ratify, or — under the operator's own authority, recorded as
    such — ship anyway; the verdict itself is never overridden, the override is a
    separate signed operator act, which is what keeps it from happening informally).
    `SUSPENDED(escalation)` — parked at a savepoint; nothing concluded.
    `NOT-DEMONSTRATED(structural)` — YES provably unreachable: early termination is a
    verdict-affecting act, so it requires a machine-checkable witness (the specific
    content-addressed frame element now unsatisfiable, and why); claimed
    unreachability without a witness is not actionable and the run continues. The
    terminating seat may never delete, weaken, or reclassify the blocking frame
    element — that path is a human-ratified specification defect only.

## 7. Mechanical gates (each cheap; each would have fired in run 1)

For each: trigger, owner, output artifact, failure action.

1. **Value-chain forcing test** — at gate time, pre-verdict; frame-check seat selects
   the instance; output is the forced first line's value; failure caps at INCOMPLETE.
2. **No control attested by prose** — a **fired probe** is one *initiated by the
   factory's own verification machinery, with chain-of-custody linking invocation to a
   measurable outcome on the real path* (Advocate: external data can evidence external
   behavior only — an externally-sourced attestation can never attest an internal
   control). Continuous; owner is the Tester lane; unproven control = absent control.
3. **Real-substrate lifecycle tier** — every state machine through ≥2 transitions
   against a real database on **each executor in the A3-signed topology enumeration**;
   unsigned topology → UNKNOWN-on-executor-parity, not PASS. Pre-verdict; Tester.
4. **Wiring audit** — a script beside `check_core_purity.py`, fail-closed: every
   layer-provided service reachable from an entrypoint; zero-caller export in new code
   fails the gate. Per build; output feeds the coverage map.
5. **Route-coverage oracle** — auth claims name the route; the probe exercises that
   route. Per claim; Tester.
6. **"Silently does nothing" is Critical** — detection/function receives the
   mutation-program treatment refusal received; zero flake tolerance, no waiver;
   includes the failure-injection scenario variant (§5).

## 8. The frame-check seat

Cold-context agents drive the ratified purpose sentence end-to-end,
**pre-disposition, as a required gate**. Contract, hardened by Constrain and Advocate:

- **Input contract — behavioral only**: the seat receives exactly the ratified purpose
  sentence, live entrypoint access to the candidate, and the freedom to choose its own
  inputs and scenario instance at gate time. **No source code, no documentation, no
  factory artifacts, no coverage map** — the code itself telegraphs the factory's
  frame (Advocate: an agent reading the code reverse-engineers intent instead of
  checking it; run 1's spec-by-comment passed review exactly this way). The code-side
  checks are mechanical and separately owned (gate 4). If the product cannot be
  exercised from external entrypoints alone, that is itself a finding.
- **Staging is impossible by construction**: the seat drives the same
  content-addressed artifact by digest that promotion would ship (the canary rule).
- **Gate, not advisory — structurally**: the seat's binary result *is* the value of
  the verdict's forced first line, entering the coverage map from which the verdict
  function computes. There is no discounting step, because the Validator does not
  weigh inputs. An advisory frame-check would restate run 1's
  PASS_WITH_RISK_ACCEPTANCE failure.
- Isolation and arrangement are recorded and feed the existing independence-tier
  derivation; the seat consumes nothing from the run, so existing independence rules
  are untouched.

## 9. Where this lands in code

- Verdict function / coverage map / characterization receipts / coverage-of-coverage →
  `factory_core/promotion.py` (`RiskAcceptance`, `PromotionRequest`) plus a coverage-
  map artifact bound through `factory_core/evidence.py` provenance.
- Does-the-thing scenario families / ratifiability / instance selection →
  `factory_runtime/acceptance_obligations.py` (`AcceptanceObligationCatalog` already
  binds ratified criteria to exact tests, execution config, immutable subjects).
- `__HANDOVER__` payload, scope-union check, emission control, retraction → lane
  projections (`factory_runtime/lanes.py`), ledger schema at the content layer
  (`factory_core/manifest.py` stays stdlib-only), supersession pattern from
  `factory_core/test_disposition.py`.
- Assumption/escalation/hard-stop taxonomy, closed frame, termination paths →
  code-selected obligation sets (`factory_runtime/transition_obligations.py` pattern),
  `factory_core/triage.py` (the no-silencing invariant already lives there).
- Side-effect register / branch clearances / conditional pre-clearance →
  `factory_runtime/broker.py` (typed effect handles exist; add inverse/irreversible
  classification), `factory_runtime/resume.py` (branch entry = checkpoint verification
  + register reconciliation), `factory_core/tool_policy.py` (signed/scoped/expiring
  grant machinery).
- Savepoint naming / branch provenance / declared-read dependency closure →
  `factory_runtime/state.py`, `snapshot.py`, `factory_core/provenance.py`.
- Wiring audit → new `scripts/` guard beside `check_core_purity.py`.
- Frame-check gate → `factory_runtime/orchestrator.py` before disposition;
  arrangement recorded into `factory_core/independence.py`.

## 10. Sequencing

1. Verdict function + coverage map + forced first line + monotone composition (the
   epistemic core — everything else feeds it).
2. `__HANDOVER__` schema + scope-union + emission control + ledger-digest-bound
   reporting (kills token inflation and prose authority together).
3. Does-the-thing scenario machinery in acceptance catalogs + frame-check gate with
   the behavioral-only input contract.
4. Real-substrate lifecycle tier + wiring audit + fired-probe rule.
5. Dark-run taxonomy + side-effect register + branch clearances + termination paths.
6. Synthetic-producer admissibility (defined now; built per-target when a verb crosses
   an external boundary).

## 11. Lens traceability

- **Sim** (kindex): epistemic architecture `59bf9bf9b050`; typed handover
  `9d49600bf670`; synthetic-producer admissibility `7433f8476a5b`; dark-run five bends
  `d285790ba655`; frame-check/purpose-sentence governance `8da3f9534aef`.
- **Constrain** (clean-room, challenges harvested — kindex `7362c5b24301`; synthesis
  rejected, zero-byte constraints.yaml reproduced): decidable escalation predicate;
  closed-frame representation with fail-closed membership; A4 zero-known-unknowns exit
  criterion; three termination paths with the structural witness; branch-blocking
  side-effect class, two clearance forms, conditional pre-clearance; frame-check input
  contract and gate-not-advisory; the monotone composition law; the characterization
  receipt as a fully defined artifact; frame-defect vs. out-of-scope classification;
  retention outlives abandonment.
- **Advocate** (55 findings; accepted): tamper-evident summary generation; fired-probe
  definition; `__DONE__` emission control and scope-union check; handover retraction;
  coverage-of-coverage audit; observed-equals-consumed dependency closure; executor
  topology enumeration; assumption-composition audit; ratifiability criterion;
  failure-injection scenario variant; purpose-sentence adversarial probe; run-1
  attestation re-earning; mechanical hard-stop conditions; anti-Goodhart instance
  selection; NOT-DEMONSTRATED runbook with formal (never informal) override.
- **Advocate (rejected, with reasons)**: *remove savepoint branching as speculative*
  (Sage) — rejected: branch-back from cached state is an operator-stated requirement
  and the machinery (frozen tuples, retained bytes, resume verification) already
  exists; *collapse frame-check into the e2e test* (Sage) — rejected: a
  factory-known test is a defect attractor (run 1's named-ritual failure); gate-time
  instance selection by a cold seat is the anti-Goodhart property and cannot be
  supplied by a test the factory wrote for itself; *reduce verdict vocabulary to three
  states* (Sage) — partially adopted: the forced first line is exactly that
  three-state grammar; the scoped vocabulary beneath it is load-bearing per Sim's
  typed-schema ruling and exists to carry scope, not to soften headlines.
