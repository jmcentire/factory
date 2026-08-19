# Software Factory

A founder-owned, **portable software-factory core and executable runtime**. It productizes a proven
software-delivery discipline — content-addressed evidence, human-approval gates with
segregation of duties, and a hard generic-core / target-as-data boundary — as a standalone,
separately-shippable Python package that imports **nothing target-specific**. The pure
`factory_core` policy layer is joined by `factory_runtime`, the impure orchestration boundary.

## The canonical doctrine

The foundation of this repository is a written doctrine, and the code implements pieces of
it. The doctrine is authoritative; this repository reports its running subset below.

- **[`docs/SOFTWARE-FACTORY.md`](docs/SOFTWARE-FACTORY.md)** — the unified specification:
  exactly three roles (**Validator, Coder, Tester**), exactly three pre-build phases
  (product specification, architecture, operational maturity), two flows (capability +
  correction), the eight non-negotiables, shared-spec/no-shared-channel independence,
  oracle-adequacy-not-blast-radius gating, human-decided surface criticality, class-scoped
  determinism and evidence-gap disposition, the three signed/content-addressed invariant
  documents, authorization-based existing-test disposition, evidence-backed checklist gates,
  signed scoped tool tiers, the red-now/green-now correction controls and the suspected
  over-constraint rule, reproduction as the correction's negative control, graded independence
  tiers recorded per run, spec-derived monitors that a triage agent cannot silence, environment
  ladder, content-addressed evidence plane, and regulated control plane. Part II contains the
  authoritative self-contained role directives.
- **[`docs/AGENT-DIRECTIVES.md`](docs/AGENT-DIRECTIVES.md)** — a stable compatibility entry
  point into the role directives embedded in the canonical specification; it deliberately
  does not duplicate them.
- **[`docs/VALIDATION-DIRECTIVE.md`](docs/VALIDATION-DIRECTIVE.md)** — the Validator's
  process-completeness operational supplement: phase-artifact provenance, no local-only work,
  durable `.kin`, current docs/contracts, migration atomicity, PR/commit/merge/deploy
  evidence, live observability, class-scoped gap disposition and Standard risk acceptance,
  rollback authority, and a reproducible evidence bundle for every pass.

The practices under `docs/practices/` and the sync log in `docs/PROVENANCE-SYNC.md` are
disciplines and records **under** this doctrine.

## Doctrine → code mapping

Each core and runtime module implements a specific concept from the doctrine. The doctrine
demands this honesty (see the doctrine's "Status of this document": *a control specified is
not a control running*), so the table marks what is **implemented** vs **doctrine-only**.

| `factory_core` module / file | Doctrine concept it implements | Status |
|---|---|---|
| `manifest.py` | The evidence plane — the content-addressed, hash-chained, tamper-evident change-evidence manifest; write-time segregation of duties (implementer ≠ verifier ≠ approver); constant-time leaf verification (`verify_digest`). | **Implemented** |
| `evidence.py` + `checklist.py` | Reusable content-addressed evidence binding and checklist gates: every required item is independently recorded against the exact subject; missing/uncited is a visible gap, failed is negative evidence, and tampered/wrong-subject is an integrity failure. | **Implemented** (pure verifier; append timing/platform persistence remains external) |
| `criticality.py` | §3.5 — the human-decided per-surface control profile, content address, declared side-effect closure, highest-class inheritance, and unclassified/invalid-classification → Critical default. It explicitly does not claim the supplied topology is complete. | **Implemented** (profile resolution; phase-2 topology enumeration remains external) |
| `promotion.py` | The two-axis promotion gate — class-disposed oracle/evidence gaps, universal rejection of negative or mismatched evidence, deterministic zero-flake/zero-retry Critical evidence, Critical specialist review and two-human floor, Standard candidate-bound expiring risk acceptance, Cosmetic report-and-promote, signed tool-policy verification, evidence-backed checklist evaluation, the declared lane with its controls, the derived independence tier, the monitor set, and the Critical named-delegate roster. The attestation binds the exact profile plus every decision input and cited evidence/policy address. | **Implemented** |
| `provenance.py` | Non-negotiable #7 and invariant-document identity — a verifier over exactly one externally trusted Product Specification, Architecture Specification, and Testing and Monitoring Strategy. Every downstream reference binds both the exact artifact digest and canonical item digest, so any amended artifact version invalidates old derived work. A monitor resolves on the same terms as a test assertion. It distinguishes absent links from invalid ones and verifies provenance, not semantic equivalence. | **Implemented** (pure verifier + promotion wiring) |
| `build_plan.py` | The recipe/build-IR boundary: an externally trusted catalog of versioned, pre-qualified construction patterns and a disposable per-run recipe book that instantiates them with immutable configuration. Every Product/Architecture item must map to a build step, every Product expectation to an Operational oracle, and every Operational item must be used; stale run, target, catalog, input, or phase bindings refuse. The plan carries references, not independent behavioral authority. | **Implemented** (pure verifier; resolving the pattern artifact and qualification-evidence bytes from an external artifact sink remains unwired) |
| `independence.py` | §6 graded independence — the five-tier ladder **derived** from the recorded arrangement (shared context, open channel, model families, mechanical backing) rather than asserted, the per-agent model/version/directive-version record that makes requalification auditable, and the structural-depth trade (implementation-informed mode only against a resolved signed contract; otherwise isolation plus the Validator's mutation obligation). A claim above the derived tier is an integrity failure. | **Implemented** (pure verifier + promotion/runtime wiring; model and channel facts are caller-supplied) |
| `monitors.py` | Phase-3 spec-derived monitors — each resolving its own backreference (unresolvable is an unauthorized assertion about production), diff-derived rejected as a change detector, class-scoped authorship with human-authored Critical monitors resolved against the enrolled roster, notification requiring a human-actionable conclusion, monitor state carried on the monitor, and density recorded but gated on nothing. | **Implemented** (pure verifier; monitor deployment and firing are external) |
| `triage.py` | The observability-layer writer/judge separation — an agent evaluating an alert may investigate and propose a fix but may not delete, weaken, or silence the monitor that produced it; silencing requires a human-ratified specification defect bound to that exact monitor and action, ratified by someone other than the evaluator. Plus detect-everything/notify-selectively: notification requires an actionable, refuted conclusion. | **Implemented** (pure decision; alert transport is external) |
| `correction.py` | The correction lane — red-now/green-now controls classified per test, the red guard as a **suspected over-constraint** that stops for a human and is never reclassified, the recognition check for an already-green forcing test, greenfield gating, and the reproduction requirement (disposable environment, recorded before the repair; impossible gates; did-not-reproduce routes to the human). | **Implemented** (pure classifier; baselines and reproduction execution remain external) |
| `test_disposition.py` | The three-way disposition for a formerly passing test that now fails: an exact same-phase supersession plus an externally trusted affirmative ruling over the run, current phase versions, exact old/new behavior, exact signed replacement statement, and frozen assertion/family permits a Tester-side update. Retained behavior requires fixing the implementation; silence/conflict routes to the human. The ruling acknowledges impact and cannot invent intent. | **Implemented** (pure classifier; runtime signature verification is below) |
| `tool_policy.py` | The run-scoped Allowed / Sign-off-required / Verboten policy: exact inventory coverage, phase-2/3 backreferences, independently approved content address, scope ceilings, fresh expiring human authorizations, unknown-tool denial, and content-addressed Verboten denial probes. | **Implemented** (pure pre-execution decision and verifier; credential/network removal and resource probes must be wired by the platform) |
| `invariant_kernel.py` | The capability-delta IR + the composition gate — can individually-safe deltas compose into a forbidden configuration? (the platform-invariant side of the gate). | **Implemented** |
| `contract.py` + `completeness.py` | Oracle adequacy + the FE↔BE contract discipline + launch-readiness — forward/reverse contract diff (every caller reaches a real provider; every provider is called or excused) and the falsifiable completeness lattice (the exit gate). | **Implemented** |
| `comprehensiveness.py` | The intake-completeness gate (the entrance) — a deterministic, injection-resistant registry of structural field predicates that decides comprehensive vs needs-info without an LLM. Fields/thresholds/rules are data. | **Implemented** |
| `adapters.py` + `target.py` | The target-as-data boundary + the environment-ladder dependency seams — the five `Protocol` seams for all target contact, resolved by name from a signed data-only `TargetManifest` (never a code import). The manifest also binds the operational build ABI: authorized pattern-catalog digest, construction modes, and hard attempt ceiling. | **Implemented** |
| `roles.py` | Target RBAC bundles — capabilities as the atomic unit, roles as per-target named bundles, grants as per-target data. These target roles are not additional factory workflow roles. | **Implemented** (schema; live RBAC/SSO is doctrine-only) |
| `scripts/check_core_purity.py` + `scripts/check_doctrine_sync.py` | "The factory is itself a regulated system" — executable fail-closed guards prove the core imports nothing target-specific and the active documentation still has the canonical three-role/three-phase structure. | **Implemented** |

### Executable runtime status

| Runtime surface | What is enforcing now | Boundary that remains |
|---|---|---|
| `state.py` + `transition_obligations.py` | `factory-run/4` is a hash-chained lifecycle whose `run.json` is only a checked projection. Every legal transition selects a code-owned, versioned obligation set, durably retains it and its report content-addressed, and re-derives both on every load. Evidence stages privately and publishes by no-replace link; exact existing evidence is stable-read and fsynced, then its entire containing directory chain is fsynced through the run before ledger admission. Failed or concurrent writers therefore cannot expose a partial canonical address or publish the transition first. Unknown triggers, missing evidence, direct-ledger bypass, stale generations, and obligation replay deny. Attempts are unique, monotone, and bounded; a blocked retry requires one typed signed-brief event whose exact digests and authorized attempt id are consumed by the immediately following build. Specification amendments clear derived generation state. | The shipped orchestrator intentionally stops at `preview`; later human/CI/promotion states remain explicit runtime transitions. Legacy runs re-derive read-only and cannot advance. |
| `schema.py` + `schemas/` | Closed Draft 2020-12 contracts for authority, phase, recipe/build, test-change, transition-obligation, acceptance-obligation, runner, broker, resume, state-dependency, state-qualification, orchestrator-projection, refusal, and evidence artifacts. | A schema proves closed structure, not semantic agreement; meaning remains human+Validator work. |
| `durability.py` + `tessera.py` + `authority.py` | Real Tessera Ed25519 envelope creation/validation; the exact linked envelope inode and containing directory are fsynced before `wrap_json` returns, and ledger callers fsync the remaining directory chain through their known durable root; externally pinned founder genesis; unique enrolled keys; exact run/repository/action/subject/signer/capability/expiry/nonce receipt checks. | These guarantees require the documented qualified local POSIX filesystem. Key rotation/revocation and managed HSM/KMS custody are not wired. Signet is not a prerequisite or authority here. |
| `test_change_authority.py` | Before an existing expectation may change, the runtime resolves the exact old-to-new supersession in current phase authority, freezes sorted assertion/family membership, binds run/generation/target/phase versions, verifies distinct enrolled human and Validator receipts over the same ruling, and retains the exact verified bytes. The lifecycle transition records the ruling and both receipts. | The caller must identify the actual changed existing-test set; automatic target-diff extraction remains a target execution seam. |
| `workflow.py` + `repair.py` + `failure_classification.py` | A change enters only through a canonical request whose signed subject includes the exact run and target digest. Each phase artifact must bind the authorized verbatim-source digest and requires distinct human and Validator receipts over its exact bytes. Terminal failures are structurally classified; recoverable failed subjects may receive one bounded Repair Brief signed by the exact Validator recorded on that causal failed attempt, distinct from its Coder and Tester. Its run/head, failed and next attempt ids, candidate/tests, phase versions, exact intent backreferences, retained bytes, event identity, and retry Validator all re-verify before retry. A crash after canonical envelope publication but before event admission resumes only by authenticating that exact orphan and recording the missing event. | A Repair Brief is derived operational guidance, never behavioral authority. Its actions remain Validator-authored prose: structured Tester fields are refused and Tester remains isolated, but semantic oracle-hint detection is not claimed. New or changed intent returns to phase ratification. Campaign time is observed around callbacks; an injected attempt runner owns its hard per-call ceiling. The interactive human/Validator authoring UI and behavior ledger are not built. |
| `generation.py` + `snapshot.py` | Before authoring, the runtime re-derives the target ABI and all three ratified phase artifacts, verifies the recipe book, and retains exact target/catalog/plan/build-input/readiness bytes under their addresses. Review snapshots retain exact regular-file bytes, paths, and modes—not hashes without recoverable subjects—and fail verification after payload, manifest, link, special-file, or write-mode tampering. | Storage is application-level read-only CAS with tamper detection, not hardware WORM. Pattern artifact/qualification payload retrieval from an external artifact sink remains unwired. |
| `isolation.py` + `lanes.py` | On macOS, deny-default Seatbelt gives Coder and Tester disjoint authoring projections and no network. Validator alone receives both frozen author snapshots and executes the suite. A signed Repair Brief is copied only into Coder's fresh retry projection; Tester never receives it. Acceptance catalogs bind exact tests, assertions, expected effects, evidence ids, Validator argv/config, phase versions, distinct human/Validator ratification, and a review-round ceiling; raw observations are re-derived into a retained report before preview. | This backend is macOS-only and fails closed elsewhere. Linux needs an independently qualified backend. |
| `state_admission.py` + `state_qualification.py` | Every lane conditions on one closed, versioned dependency profile and exact-byte capsule. Stable bounded reads reject symlinks and read-time mutation; missing, unknown, duplicate, oversized, stale, trust-escalated, or substituted inputs deny before a model call. A code-owned deterministic executor compares cold/exact-resume/compaction admissions and stale/contradictory/poisoned/missing/oversized refusals without invoking a model or comparing prose. | The qualifier covers state admission only, not product semantics, live runner isolation, or the next stochastic trajectory. Its observations and materialized report are produced outside the dispatched lane and must both be bound into the external resume configuration set. |
| `instruction_control.py` | Exact externally checkpoint-bound directive/provisional chains derive one bounded effective-directive contract through a closed canonical run/generation/role scope grammar. Applicable unsettled provisional state blocks; cross-scope or qualifier-losing supersession refuses; refusal never activates candidate text; and a structured lane dispatch must exactly read back every active id, directive quote, and qualifier quote. The runtime also compiles shared doctrine plus exactly one role into a provider-neutral role contract. All three artifacts enter the closed lane capsule and exact retained prompt. | Checkpoint and hash-chain verification do not authenticate the directive author. Instruction delivery and exact readback are not semantic comprehension or adherence. Product intent remains in ratified phase artifacts; effects remain governed by policy, obligations, broker capabilities, and promotion gates. |
| `runner.py` + `runner_isolation.py` + `projection_bundle.py` + `broker.py` | Live lane dispatch uses a closed environment, named-secret files, dedicated config homes, a bounded path-free data projection, the exact admitted state capsule, exact executable/model/version/config/billing-key-name receipts, two canaries, same-session resume proof, wall/idle/process/output/token/cost ceilings, and Codex or `ollama launch codex` only. Prompt/3 executions emit runner-receipt/3; the original receipt/2 schema remains immutable for historical validation but cannot execute after the instruction-contract cutover. A failed invocation retains a bounded named-secret-redacted diagnostic in Validator-private evidence and exposes only a small safe failure capsule downstream; diagnostic-retention failure preserves the actual model-attempt count. The networked model has no target/control filesystem path or shell authority. Effects cross through opaque signed capabilities resolved to host-owned paths or fixed argv, then operation-specific rehash or deterministic-rerun verification. Failed canaries execute no broker request. | Cost is honestly `observed-post-call` unless a provider supplies a hard limit; the objective reserves per-runner ceilings before dispatch. The qualified backend is macOS Seatbelt on a local run filesystem, but currently permits general outbound network: provider-only egress is not yet enforced. Broker accounting locks do not claim network-filesystem semantics. The interactive human/Validator tmux surface is operator-owned coordination, not a qualified model lane. |
| `resume.py` + `run_context.sh` | Grounding and every dispatch first compare the canonical checkpoint to an independently supplied SHA-256 digest before opening mutable run state, then re-verify the pinned founder root/genesis, retained Stage-R/Stage-E Tessera signatures, target/source/generation, lifecycle/resource prefixes, configuration bytes, predecessor lineage, and retention/erasure policy. Root substitution, rollback, fork, config drift, and retained-envelope mutation deny. | The checkpoint is content-addressed, not itself signed or independently custodied by Factory. Its protection against whole-control-root substitution depends on the operator obtaining the expected digest from separate trustworthy custody. Factory does not provide WORM custody, timestamping, or an external anchoring service. |
| `evidence_plane.py` + `orchestrator.py` + `orchestrator_projection.py` | Checklist and acceptance evidence append into verified chains. Candidate/test snapshots are immutable subjects; validating binds them and the acceptance report, and every preview bundle re-verifies the exact generation tuple. A real Tessera test proves a failed attempt, bounded retry, signed preview, and reproducibility after mutable author workspaces change. Harness wakes freeze a closed nine-section exception projection and state capsule for a one-shot advisory orchestrator; the runtime derives phase/run authority sections, Antigravity is the metadata-bound default, Codex the fallback, and neither receives gate or mutation authority. | Signed criticality-profile ingestion, target deployment/live probes, specialist review, human approval, CI handoff, production promotion/rollback, and monitor deployment/firing remain unwired. The Agy/Codex wake is bounded coordination, not a new authority source; its CLI sandbox is recorded as declared but not independently kernel-qualified for projection-only reads. An explicitly selected interactive Claude Validator remains operator-equivalent and unsandboxed, not a qualified lane. |
| `.github/workflows/ci.yml` | Pinned action revisions, pinned Tessera commit, deterministic core gate, real Tessera tests, and a macOS kernel-isolation/full-runtime job. | This proves the repository’s synthetic vertical slice, not a target’s production deployment. |

The honest boundary is therefore: **the generic bootstrap path is running through externally
anchored resume, closed state admission, structurally qualified model dispatch, typed effects,
state obligations, and a signed preview
over a synthetic target. The complete factory described by the doctrine is not.** There is no
chat-first collaborative phase-authoring UI, target deployment ladder, production promotion,
managed identity system, live SSO/RBAC integration, or independently custodied anchor service.
The tmux surface coordinates the human and Validator; room names, Kindex channels, and prose
fences never substitute for runner isolation, typed capability checks, or signed ledger authority.

## Specs, recipes, and rebuilds

The three ratified artifacts answer different human questions. The **Product Specification**
states the outcome and user-visible behavior. The **Architecture Specification** settles the
major interfaces, boundaries, data/authority ownership, and operational shape. The **Testing and
Monitoring Strategy** states the user expectations and independent oracles that decide whether
the result works. The human and Validator may negotiate these through as many chat turns as
needed; generation begins only after all three are agreed, sufficiently deep, and independently
ratified.

A **recipe pattern** is narrower: a reusable, versioned construction mechanism with addresses for
its implementation and qualification evidence. It is a standard way to build something, not a
statement that the product should do it. A per-run **build plan (recipe book)** instantiates those
patterns with configuration, orders their dependencies, links every Product/Architecture item to
construction, and links each expectation to an Operational oracle. It is disposable derived IR:
any phase amendment invalidates it, and it can never supersede a spec or alter a test expectation.

Promotion cares whether the resulting product satisfies the agreed oracles, not whether generated
code is aesthetically consistent. `regenerate` keeps a complete rewrite ordinary; `brownfield`
exists for a deliberately scoped small correction. Existing tests are immutable by default: an
agent may change one only when current signed authority uniquely supersedes the old behavior and a
single exact ruling names the assertion (or frozen family) and expected change, then an enrolled
human and a distinct enrolled Validator independently sign that ruling.

The runtime currently binds the selected construction mode but does not yet derive and enforce a
`brownfield` changed-path/surface ceiling against the produced candidate; that remains a target
execution/promotion seam, so the mode label alone proves no scope conformance. A target ABI is
immutable within a run. An intentional ABI change starts a newly authorized run rather than being
accepted as benign drift inside the old one.

### Authorized-change bootstrap path

This is the running, file-and-CLI intake path. It is the bootstrap answer to “how does a change
become authorized?”; it is not the future collaborative UI.

1. A founder creates a Tessera key, writes a `genesis` document that enrolls each human and
   agent identity under a unique public key and minimum capability set, signs it as
   `factory-genesis`, and distributes the root public key through an external trusted channel.
   `factory verify-genesis` refuses a document that does not validate under that pinned key.
2. A contributor writes an `authorization-request` before implementation. The closed schema
   requires the exact `run_id`, repository, target digest, preserved verbatim request and its
   digest, proposed outcome, and disturbed surfaces. Validate and address it:

   ```bash
   factory validate-document --schema authorization-request --input request.json
   factory digest-json --input request.json
   ```

3. An enrolled human decides. For an authorization, they write an `authority-receipt` whose
   action is `authorize-change` and whose subject is the request digest, then sign it without
   exposing key material to Factory:

   ```bash
   factory tessera-wrap --payload receipt.json --kind factory-authority-receipt \
     --key human.key --output receipt.tessera.json --tessera-bin /path/to/tessera
   factory authorize-change --runs ./runs --run-id RUN --target-digest sha256:... \
     --request request.json --receipt receipt.tessera.json \
     --genesis genesis.tessera.json --root-public-key PUBLIC_KEY \
     --tessera-bin /path/to/tessera
   ```

4. The human and Validator co-author each invariant document in order: Product Specification,
   Architecture Specification, then Testing and Monitoring Strategy. Each document retains the
   authorized verbatim-source digest. Both parties independently sign exact-subject receipts;
   `factory ratify-phase` accepts the document only when the two enrolled identities, keys,
   capabilities, run, action, subject, expiry, and nonces verify.
5. After all three ratifications, a target-bound recipe book and human+Validator-ratified
   acceptance-obligation catalog are verified and retained. Qualified Coder and Tester runners
   receive asymmetric path-free projections and can request only signed typed broker effects.
   Their outputs freeze before review; only Validator executes the exact catalog-bound suite and
   supplies raw observations from which the runtime derives the acceptance report. Attempts and
   review rounds are bounded before the signed preview bundle can be written. The executable
   worked example is `tests/test_tessera_cli_integration.py`; `make test-tessera` runs it with
   real Ed25519 keys and the real Tessera binary.

The founder genesis is the one explicit bootstrap authority. Subsequent request and phase
authority is machine-verifiable. Signet is neither silently assumed nor required by this path.

The defining constraint: `factory_core` is generic. Every per-target input — repo coordinates,
working-agreement docs, compliance rules, role bindings, IdP config — is **data loaded at
runtime through adapter seams**, never a code dependency. Point the factory at a new target by
swapping a data-only target pack; the core does not change. Correctness test: delete every
target pack and the core is still importable, testable, and green.

## What's here

| Module | What it is |
|---|---|
| `factory_core/manifest.py` | The content-addressed (SHA-256), append-only, **hash-chained**, tamper-evident evidence ledger. Every append is **fail-closed on segregation of duties** (implementer, verifier, approver must be three distinct identities); `SegregationPolicy` resolves identities **DENY-wins** (an agent denylist beats the human allowlist); `verify_digest` is the constant-time leaf tamper check. Stdlib-only. |
| `factory_core/evidence.py` + `factory_core/checklist.py` | Shared subject-bound evidence verification and the generic checklist item/report model. A checked item without its own valid citation is still a gap. |
| `factory_core/criticality.py` | The fixed three-class (**Critical / Standard / Cosmetic**) surface-policy model. Components, surfaces, wrong-cost rationales, human deciders, side-effect edges, extra evidence ids, and Standard flake budgets are target **data**. Unknown/unclassified resolves Critical. |
| `factory_core/promotion.py` | The pure promotion decision (`decide_promotion`) over criticality resolution, oracle/live/determinism observations, trusted invariant documents, signed tool policy, evidence-backed checklist, specialist review, risk acceptance, and SoD. Critical gaps block without waiver; Standard gaps gate; Cosmetic gaps report-and-promote. |
| `factory_core/provenance.py` | The **provenance-of-intent** verifier (`verify_intent_provenance`): exactly one trusted Product Specification, Architecture Specification, and Testing and Monitoring Strategy; artifact-and-item digest binding for every downstream requirement, constraint, task, and test assertion; and explicit signed supersession metadata. Missing links are labeled for class disposition; unresolvable/mismatched links remain integrity failures. |
| `factory_core/build_plan.py` | Qualified recipe-pattern catalog and per-run recipe book/build IR: immutable step configuration, dependency graph, complete implementation coverage, expectation→oracle matrix, and exact run/target/input/catalog/phase invalidation. |
| `factory_core/test_disposition.py` | Pure authorization-based classification of formerly passing failures: update only on same-phase signed supersession plus one exact externally trusted human-authored, Validator-ratified impact ruling; otherwise fix the code when authority is unchanged or route silence/conflict to the human. |
| `factory_core/independence.py` | The five-tier independence ladder (**weakest → strongest**), derived from the recorded arrangement and never asserted; the per-agent model family/version and directive version; and the structural-mode trade. Fail-closed: an unrecorded arrangement derives the weakest tier, and an overclaimed tier blocks. |
| `factory_core/monitors.py` | Spec-derived monitors with resolvable authority, class-scoped authorship (**Critical ⇒ human-authored**), notification requiring an actionable conclusion, monitor-carried fix state, and density recorded without a threshold. |
| `factory_core/triage.py` | The triage decision (`decide_triage`) that cannot silence its own alert's monitor without a human-ratified specification defect, plus the earned-notification decision (`decide_notification`). |
| `factory_core/correction.py` | The red-now/green-now control classifier — a red guard is a **suspected over-constraint** raised, never repurposed — plus the recognition check, greenfield gating, and the reproduction-before-repair requirement. |
| `factory_core/tool_policy.py` | Pure signed run-policy verifier and pre-execution invocation decision over opaque target-supplied tool/scope ids. Verboten and unknown deny, Sign-off authority expires, and denial probes prove configured refusal. |
| `factory_core/comprehensiveness.py` | The **deterministic, injection-resistant** intake-completeness gate: an ordered, collision-guarded registry of **structural** field predicates (present-and-substantive by length + word-token count, never semantic, **never an LLM**) that decides comprehensive vs needs-info. The entrance analogue of `completeness.py`'s exit gate. Fields / thresholds / rules are per-target **data**. Stdlib-only. |
| `factory_core/target.py` | The `TargetManifest` loader: parses a content-addressed TOML manifest (repo coords/ref, adapters, compliance, roles, operational build ABI, effort, demo environment), validates it against JSON Schema, and **refuses any code reference**—data in, never a code import. |
| `factory_core/adapters.py` | The five `typing.Protocol` seams for all target contact: `RepoAdapter`, `KnowledgeAdapter`, `ComplianceAdapter`, `IdpAdapter`, `ArtifactSink`. Interfaces only. |
| `factory_core/roles.py` | The target role/capability model **schema**: a capability is the atomic unit, an RBAC role is a named bundle, and grants are **per-target data**. It is distinct from the three factory workflow roles. |
| `factory_core/schemas/capability_delta.schema.json` | The neutral, target-agnostic **capability-delta IR** a spec declares for a change (nodes with abstract roles, flows with an abstract data-class, invariants by opaque id + degree). The analyzer composes it against the signed kernel + composition ledger. Names no target; all concrete vocabulary is per-target kernel data. |
| `factory_runtime/state.py` | Persisted lifecycle ledger and checked projection, including phase-amendment invalidation, receipt replay refusal, complete frozen generation tuples, and monotone bounded attempts. |
| `factory_runtime/durability.py` + `tessera.py` + `authority.py` | Local-POSIX evidence durability, the real Tessera CLI boundary, externally pinned genesis, and subject-bound receipt verification. |
| `factory_runtime/workflow.py` + `repair.py` + `failure_classification.py` | Authorized-change intake, dual-receipt invariant-document ratification, typed terminal-failure routing, and Validator-signed bounded repair over one immutable failed subject. |
| `factory_runtime/generation.py` + `snapshot.py` | Target/phase/build-plan readiness plus content-addressed retention and re-verification of exact generation and review bytes. |
| `factory_runtime/isolation.py` + `lanes.py` | Qualified macOS Seatbelt enforcement and asymmetric build-input/recipe projections for the separated Coder/Tester → Validator loop. |
| `factory_runtime/evidence_plane.py` + `orchestrator.py` | Append-as-observed checklist journal, retained review snapshots, generation/attempt receipts, reproducible evidence bundle, and executable path through signed preview. |
| `factory_runtime/cli.py` | `factory` commands for schema validation, canonical JSON addressing, target ABI inspection, genesis verification, intake, phase ratification, status, projection rebuild, Tessera wrapping, and Gate L rendering. |
| `scripts/check_core_purity.py` | The executable, fail-closed **anti-coupling guard** (import scan + token denylist + reverse-dependency assert), baseline-backed by `core_purity_baseline.json`. The token set is **data**, read from `core_purity_denylist.json` (empty on the generic core — nothing target-specific to catch; a consuming target fills in its own tokens as private config). |

## Quickstart

```bash
make dev          # install the package + dev tooling (editable)
make check-purity # prove the core imports nothing target-specific
make check-doctrine # prove active doctrine surfaces retain the canonical structure
make test         # run the pytest suite
make ship         # every gate, fail-closed: purity -> doctrine -> lint -> typecheck -> test
make test-isolation # macOS: prove kernel-enforced Coder/Tester separation
make test-tessera # build ../tessera first; prove real signatures and the runtime to preview
factory --help    # executable intake/ratification/status boundary
```

Nothing needs a target to run. The suite exercises the core end-to-end against a **synthetic
empty target** fixture (`tests/fixtures/synthetic_target/`). The runtime integration additionally
uses deterministic synthetic Coder/Tester programs; that proves orchestration and isolation,
not model independence or semantic correctness.

## Practices & sync

- `docs/practices/change-surface-audit.md` — the Change-Surface Audit, a required
  spec-phase deliverable of the factory process (every change spec enumerates its touched
  surfaces and classifies each HELD-INVARIANT or INTENTIONALLY-CHANGED, with tests).
- `docs/practices/frontend-architecture.md` — the generic front-end architecture the factory
  favors: clean separation of design / structure / data, progressive enhancement with
  server-rendered completion paths, container-scoped CSS cascade, and judicious use of
  globals. Pairs with the FE↔BE contract discipline in `factory_core/contract.py`.
- `docs/PROVENANCE-SYNC.md` — the standing record of which generic factory advances have
  been propagated from the origin/reference target into this pure core, what remains
  planned, and the repeatable mechanism for future propagation. Every sync keeps the
  purity guard green.

## The two guarantees

**Purity.** `scripts/check_core_purity.py` is green iff `factory_core/`:
1. imports only stdlib + the package itself + a small reviewed third-party allowlist
   (currently just `jsonschema`) — any target import fails hard;
2. contains no un-baselined token from the configured denylist (`core_purity_denylist.json`,
   which is **empty** in this generic core) in its identifiers/strings; and
3. lists no target pack as a dependency in `pyproject.toml`.

**Portability.** `tests/test_portability.py` proves the core is importable, testable, and
green with **no target pack present** — there is no `targets/` package in the tree, only the
synthetic empty fixture, and the core reasons over it without importing anything
target-specific. Swap the target pack and the same core serves a new customer.

## Ownership

Proprietary, all rights reserved — copyright Jeremy McEntire (see `LICENSE`). Not open source.
The dependency arrow points **consumer → factory, never the reverse** (see `PROVENANCE.md`).
