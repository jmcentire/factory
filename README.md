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
| `independence.py` | §6 graded independence — the five-tier ladder **derived** from the recorded arrangement (shared context, open channel, model families, mechanical backing) rather than asserted, the per-agent model/version/directive-version record that makes requalification auditable, and the structural-depth trade (implementation-informed mode only against a resolved signed contract; otherwise isolation plus the Validator's mutation obligation). A claim above the derived tier is an integrity failure. | **Implemented** (pure verifier + promotion/runtime wiring; model and channel facts are caller-supplied) |
| `monitors.py` | Phase-3 spec-derived monitors — each resolving its own backreference (unresolvable is an unauthorized assertion about production), diff-derived rejected as a change detector, class-scoped authorship with human-authored Critical monitors resolved against the enrolled roster, notification requiring a human-actionable conclusion, monitor state carried on the monitor, and density recorded but gated on nothing. | **Implemented** (pure verifier; monitor deployment and firing are external) |
| `triage.py` | The observability-layer writer/judge separation — an agent evaluating an alert may investigate and propose a fix but may not delete, weaken, or silence the monitor that produced it; silencing requires a human-ratified specification defect bound to that exact monitor and action, ratified by someone other than the evaluator. Plus detect-everything/notify-selectively: notification requires an actionable, refuted conclusion. | **Implemented** (pure decision; alert transport is external) |
| `correction.py` | The correction lane — red-now/green-now controls classified per test, the red guard as a **suspected over-constraint** that stops for a human and is never reclassified, the recognition check for an already-green forcing test, greenfield gating, and the reproduction requirement (disposable environment, recorded before the repair; impossible gates; did-not-reproduce routes to the human). | **Implemented** (pure classifier; baselines and reproduction execution remain external) |
| `test_disposition.py` | The three-way disposition for a formerly passing test that now fails: a signed exact supersession authorizes a Tester-side update, retained exact behavior (including under a newly addressed artifact version) requires fixing the implementation and rebinding provenance, and silence/conflict routes to the human. | **Implemented** (pure classifier; lane routing remains orchestration) |
| `tool_policy.py` | The run-scoped Allowed / Sign-off-required / Verboten policy: exact inventory coverage, phase-2/3 backreferences, independently approved content address, scope ceilings, fresh expiring human authorizations, unknown-tool denial, and content-addressed Verboten denial probes. | **Implemented** (pure pre-execution decision and verifier; credential/network removal and resource probes must be wired by the platform) |
| `invariant_kernel.py` | The capability-delta IR + the composition gate — can individually-safe deltas compose into a forbidden configuration? (the platform-invariant side of the gate). | **Implemented** |
| `contract.py` + `completeness.py` | Oracle adequacy + the FE↔BE contract discipline + launch-readiness — forward/reverse contract diff (every caller reaches a real provider; every provider is called or excused) and the falsifiable completeness lattice (the exit gate). | **Implemented** |
| `comprehensiveness.py` | The intake-completeness gate (the entrance) — a deterministic, injection-resistant registry of structural field predicates that decides comprehensive vs needs-info without an LLM. Fields/thresholds/rules are data. | **Implemented** |
| `adapters.py` + `target.py` | The target-as-data boundary + the environment-ladder dependency seams — the five `Protocol` seams for all target contact, resolved by name from a signed data-only `TargetManifest` (never a code import). | **Implemented** |
| `roles.py` | Target RBAC bundles — capabilities as the atomic unit, roles as per-target named bundles, grants as per-target data. These target roles are not additional factory workflow roles. | **Implemented** (schema; live RBAC/SSO is doctrine-only) |
| `scripts/check_core_purity.py` + `scripts/check_doctrine_sync.py` | "The factory is itself a regulated system" — executable fail-closed guards prove the core imports nothing target-specific and the active documentation still has the canonical three-role/three-phase structure. | **Implemented** |

### Executable runtime status

| Runtime surface | What is enforcing now | Boundary that remains |
|---|---|---|
| `state.py` | Hash-chained lifecycle from intake through promotion; `run.json` is only a checked projection. Illegal skips, tampered ledgers/projections, run-bound receipt replay, and stale downstream phase artifacts after a specification defect refuse. Blocked clean-context attempts can re-enter building without inheriting failed lane artifacts. | The code has later states, but the shipped orchestrator intentionally stops at `preview`. |
| `schema.py` + `schemas/` | Closed Draft 2020-12 contracts for authorized requests, genesis, receipts, the three invariant documents, and evidence bundles. | Semantic agreement remains human+Validator work. |
| `tessera.py` + `authority.py` | Real Tessera Ed25519 envelope creation/validation; externally pinned founder genesis; unique enrolled keys; exact run/repository/action/subject/signer/capability/expiry/nonce receipt checks. | Key rotation/revocation and managed HSM/KMS custody are not wired. Signet is not a prerequisite or authority here. |
| `workflow.py` | A change enters only through a canonical request whose signed subject includes the exact run and target digest. Each phase artifact must bind the authorized verbatim-source digest and requires distinct human and Validator receipts over its exact bytes. | The interactive human/Validator authoring UI and behavior ledger are not built. Artifacts currently enter through files/CLI. |
| `isolation.py` + `lanes.py` | On macOS, deny-default Seatbelt profiles give Coder and Tester disjoint read/write grants and no network. Live forbidden read/write plus reachable loopback bind/connect probes must pass. Outputs reject links and special files. Validator alone receives copied outputs and runs the suite; automated repair receives only pass/fail. | This backend is macOS-only and fails closed elsewhere. Linux needs an independently qualified isolation backend before its lanes count as isolated. Agent/model launch commands and their narrow trusted-file grants are caller supplied. |
| `evidence_plane.py` + `orchestrator.py` | Checklist items append when observed into a hash chain. Candidate and acceptance-test addresses are re-derived from the validating transition, surface citations resolve only to passed candidate-bound checklist evidence, and exact phase bytes plus Tester backreferences enter a Validator-signed Tessera bundle. The bundle also carries the declared lane, the per-agent model/directive versions with the **derived** independence tier, and the monitor set — the closed schema refuses to write a bundle that omits them, and monitor authorship resolves against the signed genesis roster. A real end-to-end test reaches `preview`, including fail-then-clean-retry recovery. | Oracle-adequacy, determinism, independence, monitor, and correction records are Validator inputs at this preview boundary. Signed phase-2 criticality-profile ingestion, fresh target deployment, live probes, specialist review, human approval, CI handoff, production promotion, rollback, correction baselines, monitor deployment and firing, and runtime tool/credential grants remain unwired. |
| `.github/workflows/ci.yml` | Pinned action revisions, pinned Tessera commit, deterministic core gate, real Tessera tests, and a macOS kernel-isolation/full-runtime job. | This proves the repository’s synthetic vertical slice, not a target’s production deployment. |

The honest boundary is therefore: **the generic bootstrap path is running through a signed
preview over a synthetic target. The complete factory described by the doctrine is not.**
There is no portal, collaborative phase-authoring loop, target deployment ladder, production
promotion, managed identity system, or live SSO/RBAC integration yet.

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
5. After all three ratifications, `FactoryOrchestrator` accepts caller-supplied agent launch
   commands and narrow trusted-file grants, runs Coder and Tester independently, lets only the
   Validator execute the acceptance suite, and writes the signed preview bundle. The executable
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
| `factory_core/test_disposition.py` | Pure authorization-based classification of formerly passing failures: update test only on an exact signed supersession, otherwise fix the code when authority is unchanged or route silence/conflict to the human. |
| `factory_core/independence.py` | The five-tier independence ladder (**weakest → strongest**), derived from the recorded arrangement and never asserted; the per-agent model family/version and directive version; and the structural-mode trade. Fail-closed: an unrecorded arrangement derives the weakest tier, and an overclaimed tier blocks. |
| `factory_core/monitors.py` | Spec-derived monitors with resolvable authority, class-scoped authorship (**Critical ⇒ human-authored**), notification requiring an actionable conclusion, monitor-carried fix state, and density recorded without a threshold. |
| `factory_core/triage.py` | The triage decision (`decide_triage`) that cannot silence its own alert's monitor without a human-ratified specification defect, plus the earned-notification decision (`decide_notification`). |
| `factory_core/correction.py` | The red-now/green-now control classifier — a red guard is a **suspected over-constraint** raised, never repurposed — plus the recognition check, greenfield gating, and the reproduction-before-repair requirement. |
| `factory_core/tool_policy.py` | Pure signed run-policy verifier and pre-execution invocation decision over opaque target-supplied tool/scope ids. Verboten and unknown deny, Sign-off authority expires, and denial probes prove configured refusal. |
| `factory_core/comprehensiveness.py` | The **deterministic, injection-resistant** intake-completeness gate: an ordered, collision-guarded registry of **structural** field predicates (present-and-substantive by length + word-token count, never semantic, **never an LLM**) that decides comprehensive vs needs-info. The entrance analogue of `completeness.py`'s exit gate. Fields / thresholds / rules are per-target **data**. Stdlib-only. |
| `factory_core/target.py` | The `TargetManifest` loader: parses a content-addressed TOML manifest (repo coords + ref + subpath, adapter selections, role/capability bindings, compliance-rule path, effort params, demo-env descriptor), validates it against a JSON Schema, and **refuses any code reference** — data in, never a code import. Fail-closed before adapter resolution. |
| `factory_core/adapters.py` | The five `typing.Protocol` seams for all target contact: `RepoAdapter`, `KnowledgeAdapter`, `ComplianceAdapter`, `IdpAdapter`, `ArtifactSink`. Interfaces only. |
| `factory_core/roles.py` | The target role/capability model **schema**: a capability is the atomic unit, an RBAC role is a named bundle, and grants are **per-target data**. It is distinct from the three factory workflow roles. |
| `factory_core/schemas/capability_delta.schema.json` | The neutral, target-agnostic **capability-delta IR** a spec declares for a change (nodes with abstract roles, flows with an abstract data-class, invariants by opaque id + degree). The analyzer composes it against the signed kernel + composition ledger. Names no target; all concrete vocabulary is per-target kernel data. |
| `factory_runtime/state.py` | Persisted lifecycle ledger and checked projection, including phase-amendment invalidation and authority-receipt replay refusal. |
| `factory_runtime/tessera.py` + `authority.py` | Real Tessera CLI boundary plus externally pinned genesis and subject-bound receipt verification. |
| `factory_runtime/workflow.py` | Authorized-change intake and dual-receipt invariant-document ratification. |
| `factory_runtime/isolation.py` + `lanes.py` | Qualified macOS Seatbelt enforcement and the separated Coder/Tester → Validator build loop. |
| `factory_runtime/evidence_plane.py` + `orchestrator.py` | Append-as-observed checklist journal, reproducible evidence bundle, and executable path through signed preview. |
| `factory_runtime/cli.py` | `factory` commands for schema validation, canonical JSON addressing, genesis verification, intake, phase ratification, status, projection rebuild, and Tessera wrapping. |
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
