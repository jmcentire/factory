# The Software Factory

> How we build and how we repair: humans own intent, architecture, and risk; the factory implements or fixes, proves conformance, and produces the evidence.

This is the unified specification. It replaces the separate capability-flow and correction-flow documents and the two vision and directive pairs that preceded it, because a system described in fragments is the very failure this factory exists to remove. There is one foundation, one control plane, one evidence model, and one set of non-negotiables. On top of that foundation run two flows: the **capability flow**, which builds a behavior that does not exist yet, and the **correction flow**, which restores a behavior that was supposed to hold and does not. The flows differ in their inputs, their first steps, and the strength of the oracle available to them, and they share everything else.

## Status of this document

This document specifies the system. The deployment status — which phases are wired and enforcing today and which are design only — is tracked in the operational guide, and a reader deciding whether to rely on a given control must check there. A control that is specified here is not the same as a control that is running, and a boundary described in the present tense is a boundary the design intends to enforce, which is not a claim that it enforces now. The same discipline the factory imposes on the software it builds — that nothing is marked done on the strength of a description — applies to the factory's own description of itself.

> For this repository, the honest split between what is **implemented** in `factory_core` and what is **doctrine/design only** is stated in the top-level `README.md` (see the "Doctrine → code mapping" section). Phase 0 (skeleton + purity guard) and the two extractions (invariant kernel, contract/completeness) are real, tested code; the orchestration engine, the ten live agent lanes, RBAC/SSO, and build/demo are doctrine, not running.

## How software companies solve this today, and why it is not enough

Elite engineering organizations already hold most of the pieces this document assembles. They hold them as cultural agreements and fragmented tooling rather than as a system, and the gaps between the pieces are where the failures live.

The spec-and-build split exists today as the RFC or the architecture review board. A design is written, debated, and approved, and then the document dies. The author goes to the source host and writes whatever they write, and a reviewer tries to verify the code against a fuzzy memory of the RFC during a rushed pull request. The agreement was real and the enforcement was not. The oracle problem is partially solved by consumer-driven contracts and strict typed interfaces — contract-testing frameworks and Protocol Buffers and gRPC — so that when one team changes an endpoint a contract test breaks another team's build automatically. That catches interface drift, but humans still write both the implementation and the test that judges it, which reproduces the exact correlated misreading this factory exists to prevent: a bug verified by a test written to match the bug. And verification fatigue is the daily reality of modern DevSecOps, where pipelines blast engineers with static-analysis findings and dependency alerts and thousands of test logs, and humans cope by ignoring them, configuring rules to bypass warnings and clicking merge because the wall of data is too dense to parse.

What the factory contributes is not a new idea in any one of these places. It is the **unification**. It turns what are currently cultural agreements and fragmented, post-hoc tools into an enforced state machine, where the design cannot die in a document because the build is generated against it, where the oracle cannot be written to match the bug because it is derived independently from a signed target, and where the human is shown a calibrated decision rather than a wall of logs.

## What this is, and why

We are moving from a model where engineers spend most of their throughput writing code to a model where engineers design and operate a system whose output is the desired code or the desired fix. The human's center of gravity shifts toward the specification, the architecture, the invariant, the diagnosis, and the risk decision. The factory implements against those human decisions, proves conformance to them, and produces the operational scaffolding humans routinely skip when rushed: the documentation, the runbooks, the dashboards, the alerts, the compliance artifacts.

This is a restructuring of where judgment lives. Humans are good at judgment about intent, about whether the right problem is being solved, about architecture and the tradeoffs with no clean answer, about diagnosing why a system fails, and about whether to accept a given risk. Machines are good at exhaustive, tireless, mechanical conformance checking against a fixed target, and at generating the surrounding artifacts at a consistency humans cannot sustain under deadline.

The premise that makes this safe rather than reckless is a single observation about how engineering processes fail. **The default failure mode of any process, human or machine, is premature and unverified confidence.** Someone declares a thing done and the declaration is accepted as evidence. It is not evidence, it is a hypothesis. The entire factory is built to refuse that hypothesis until it has survived an attempt to refute it, and this matters more when the implementer is an AI, because an agent that misreads a specification produces confident, well-formatted, plausible work that is wrong, and a second agent sharing the same misreading will cheerfully confirm it. The factory's guarantees come from forcing independence and adversarial proof into the loop, never from trusting any agent's self-report.

The honest version is stronger than the inflated one. The factory does not guarantee correctness. It produces independently verifiable evidence, it makes important failures harder to hide, and it materially lowers the probability of undetected error. Where this document calls evidence trustworthy, it means tamper-evident, independently verifiable, and rooted in defined trust authorities — never unfakeable, because no evidence chain protects against compromise of the thing that verifies it, which is why the factory's own verifiers are themselves governed.

## The division of labor: who owns what

The factory does not author architecture. Humans do.

Humans author architectural intent: the service and module boundaries, the ownership of state and business capabilities, the interaction patterns and the direction of dependencies, the transaction and consistency boundaries, the trust boundaries, the high-level data models, the deployment topology, and which tradeoffs are acceptable. They stage the development work and sequence the dependencies. The factory may critique that architecture, detect cycles, identify ownership conflicts, model failure paths, and check that the implementation conforms to it, but the factory may not change it. Changing architectural intent requires an explicit human decision. A defect that can only be fixed by changing the architecture leaves the correction flow and enters the capability flow as a human-authored redesign.

Controls fall into categories, and the category determines the treatment:

- **Deterministic preventive** (no secrets in source, permitted dependency direction, schema backward-compatibility) — enforced automatically and block.
- **Deterministic detective** (missing runbook, unowned alert, stale contract) — detected automatically and reported or block.
- **Evidence-assisted judgment** (architectural coherence, acceptable coupling, adequate failure behavior) — the factory gathers evidence and a human decides.
- **Primarily human judgment** (product utility, organizational ownership, business tradeoff) — human decision with rationale recorded.
- **Runtime empirical** (SLO compliance, canary health, recovery behavior) — observed continuously and enforced where thresholds are meaningful.

A control is recorded explicitly, with its intent, scope, enforcement mechanism and whether that mechanism is automated, failure behavior, the tests and runtime signals that verify it, its owner, the changes that trigger its review, and whether an exception is permitted.

## What is shared and what is independent

The **specs are the shared contract**. The interface a unit exposes, its boundary, its inputs, its observable effects, and its data topology are defined in the signed specs and shared by every agent that touches the unit. The agent that implements and the agent that writes the tests both read the interface and the schema from the same signed contract. This sharing is the precondition for the whole system. Divergence at the interface level, including the database schema, is something the shared spec prevents by being the one definition no agent in the build or repair loop is allowed to author.

What must be **independent** is narrower. It is the **oracle** — the determination of what counts as the right answer. A test whose behavioral expectation was inferred from what the code happens to do is worthless as independent evidence, because it passes whenever the code is self-consistent, including when the code is wrong. So expected behavior comes from a signed target and is fixed before the implementation is inspected, and in the correction flow the adversarial tests that judge a repair are written by a separate agent the repair agent cannot read.

The failure mode all of this defends against is **correlated misreading** — the trap agents drawn from the same model fall into most easily, because they share blind spots. That is the simulacrum of carefulness, the appearance of rigor with none of its substance. Because separate prompts to the same model are not strong independence, the factory's correctness authority is primarily a system of **reproducible mechanisms** rather than agent panels: type systems, linters, schema validators, static analyzers, policy-as-code, spec-derived acceptance tests, mutation testing, property-based and fuzz and metamorphic and differential testing, reference models, hidden and incident-derived regression cases, and live probes against a running system. Agent reasoning sits on top of that mechanical base, never in place of it.

A signed specification is immutable for a particular run but is not presumed infallible. No agent silently reinterprets the spec. Any agent, test, operator, or human raises a specification defect with contradictory evidence, the current version stays frozen, a human intent owner approves or rejects an amendment, and an approved amendment produces a new signed version that invalidates and reruns all affected plans, tests, controls, and evidence.

## The seven non-negotiables

Every agent in every flow enforces these, in its own domain, on every change.

1. **Fail closed on the hazards.** Uncertainty involving authorization or identity, data integrity, privacy boundaries, safety decisions, security controls, irreversible or legally consequential effects, or required transactional audit denies, halts, or refuses. A hardening control absent at boot stops the boot. Other failure classes follow an explicit safe-degradation disposition the spec states (condition, disposition, max duration, exhaustion behavior, rationale). A failure with no specified disposition is a gap the spec must close, not a runtime decision.
2. **Single authoritative owner per fact.** Every authoritative business fact has exactly one human-approved owning component, and a mutation commits atomically with its required audit evidence within that authority. One owner per fact, not one store for all state.
3. **Least privilege.** Every actor, role, component, and route holds the minimum capability for its function, scoped to the minimum boundary. A repair never widens a grant to make a fix simpler.
4. **Full auditability.** Every significant mutation commits its audit record atomically with the business state; every regulated read produces durable access evidence under its stated failure policy.
5. **No silent failure.** Every external call is handled, every error is typed and structured and carries context, recovered or propagated per its disposition, never swallowed.
6. **Honesty in docs and self-reports.** Nothing marked implemented that is partial or absent; every control marked satisfied cites its enforcing artifact; partial capability is marked partial with the gap named and residual risk disclosed with a named human owner.
7. **Live-verified, not self-attested.** Doneness is established by independent adversarial verification and live end-to-end validation against a running instance.

## Two flows off one foundation

- **Capability flow** builds a future truth. Input is a product ask; no prior correct behavior exists, so it constructs its oracle from the specification and hardens that spec by refutation before locking it. Roles: PM Spec, Eng Spec, Validator, Test, Code.
- **Correction flow** restores a violated truth. Input is a defect/incident/failing test/alert/anomaly; a running system correct on everything but the fault is a **trusted oracle** the capability flow never has. Roles: Triage and Root-Cause, Spec, Hidden-Test, Repair, Judge.

The asymmetry: the correction flow can bound its spec from both sides against trusted ground truth, so on a defect with a known baseline it has stronger mechanical evidence than the capability flow has on greenfield work. Where the correction flow itself has no baseline (the greenfield repair), it is as weak as the capability flow and treated accordingly.

## Routing

A change enters the **correction flow** when it points at a behavior the system was supposed to exhibit and does not (or exhibits and should not); the **capability flow** when it asks for a behavior that does not exist yet. The Triage agent additionally routes: a contained instance (or a class fixed as an instance with the class recorded) proceeds autonomously; a systemic defect routes to a human, and where the response is architectural it leaves the correction flow for the capability flow.

## Lifecycle and the environment ladder

The environment ladder is the physical progression: local workspace → ephemeral per-change environment → shared integration → pre-production → production. The **same built artifact is promoted up the ladder by digest** rather than rebuilt at each rung. Evidence is allocated so each rung adds evidence the earlier ones could not provide.

- **Local:** formatting, types, lint, focused unit and property tests.
- **Ephemeral:** full unit suite against real disposable dependencies, migration tests, contract verification, security scans.
- **Shared integration:** cross-change compatibility, critical multi-service journeys; surfaces conflicts the dev factory allowed to diverge.
- **Pre-production:** deployment and config correctness, dynamic security testing, load/soak/fault injection, backup/restore rehearsal, observability-effect tests; can shadow production against de-identified data.
- **Production:** synthetic probes, representative canary analysis, SLO and business-invariant monitoring, bounded shadowing, automatic rollback where safe.

Dependencies: owned critical dependencies (the database) run as **real disposable instances**; internal services are simulated by **authoritative executable mocks produced by those services' own pipelines**; genuinely unavailable third parties are simulated with a **contract test that continuously verifies the simulation still matches reality**. Isolation restrictions are a safety boundary only if the platform actually enforces them — enforcement is verified, not assumed.

## The capability flow (building a future truth)

1. **Specification.** Asserts a future truth as behavior, never implementation. Itemized into individually checkable items; every gap surfaced as an explicit open question. A **spec-simulation loop** generates radical scenarios, edge-case states, and logical contradictions, presenting an interactive ledger of concrete behaviors for the human to accept or refute — the contract is hardened by iterative refutation before it is locked. Names the fail-closed disposition of every hazard-class failure.
2. **Design formalization.** Human provides architectural intent; the factory formalizes it into precise machine-readable contracts (OpenAPI, AsyncAPI, the OpenTelemetry surface) and challenges it where unsound (cycles, ownership conflicts, multiple writers, tight coupling) — raising each for human decision, not redrawing by fiat. **The database schema is a first-class interface contract.** Default noun is component; a service boundary appears only where the human-approved architecture justifies it. The Validator confirms the formalized plan solves the signed specification before a line is written.
3. **Dev factory** (per-change environment, three roles). **Code** implements and crafts artifacts. **Test** writes spec-derived acceptance tests (oracle from the Product Spec, frozen before the implementation is inspected) and implementation-informed structural tests (may inspect code to hunt weaknesses, may never redefine a spec expectation). **Validator** refuses doneness until the work survives refutation, using a clean-context reader in genuine physical isolation as a supplementary lens corroborated by mechanical evidence. Mocks are authoritative executable artifacts, never agent-generated assumptions.
4. **Promotion and production.** Same artifact promoted by digest. Canary measures change-specific correctness signals, business invariants, and side-effect reconciliation — not just latency and error rate. Rollback labeled per change as two-way-door, compatibility-window, or one-way-door; a change is not cleared for production until its reversibility is labeled and the matching recovery posture exists. **A hard capability boundary separates the authority to diagnose from the authority to ship a cure.**

The Validator's refutation includes process completeness, not only behavior. The
Validator verifies from immutable source references that required knowledge,
docs, specs, contracts, migrations, generated artifacts, PR/commit/merge state,
deployment evidence, monitoring, alerts, and runbooks are durable and current.
Nothing local-only can satisfy the gate. The detailed pass/block/unknown/waiver
rules live in [`VALIDATION-DIRECTIVE.md`](./VALIDATION-DIRECTIVE.md).

## The correction flow (restoring a violated truth)

1. **Triage and Root-Cause.** Diagnoses the actual cause (not the symptom) using the running system; classifies as instance / class / systemic; routes. A distinct role because the agent that writes the patch is least inclined to find a systemic cause.
2. **Spec.** Turns the diagnosed defect into a contract and visible tests against the existing interface and schema. Two controls bound the spec against the one oracle the flow trusts — the pre-defect behavior of main:
   - **Negative control** (not too weak): the new tests must FAIL against the current broken main, with at least one failing on the defect.
   - **Positive control** (not too strong): the new tests must PASS against main on all behavior unrelated to the defect. A test that fails against main for an unrelated reason is an over-constraint and is rejected. The residue (a fix that legitimately changes previously-correct behavior) is flagged as an over-constraint and routes to a human.
3. **Hidden-Test.** Writes adversarial tests in a protected location the Repair agent cannot read, bound to the same contract. Results return **coarsely** (pass/fail with fixed categories, no test names or traces) so the hidden suite cannot become an interactive debugger.
4. **Repair.** Writes the implementation against the merged contract without touching the contract, visible tests, hidden tests, or control plane. Fixes the cause, not the symptom. The image is identified by digest; the hidden suite judges that exact artifact; the **verdict is identified by a compound key over build/image/spec/contract** so it cannot be replayed against a different artifact.
5. **Judge** (the correction flow's Validator). Confirms the spec bounded the defect from both sides; fetches the hidden-suite verdict against the exact artifact and confirms the compound key binds; verifies mechanical evidence adversarially; confirms test integrity and critical-control mutation; re-derives every cited fact; drives the repair live. The hidden suite is one judge among the mechanical evidence and the live pass, not the sole authority.

**Retry is recovery, not search.** A fresh agent from clean context carries the diagnosis, the merged spec, the visible tests, and a bare pass-or-fail history only — none of the failed patch, transcript, categories, or explanation. The budget caps the cost of sampling, not its logic; no-op/metadata-only/fingerprint-identical retries do not reset the budget. A greenfield repair (no baseline) falls back to a sensitivity measure and defaults to **gated** regardless of hazard class.

## The gate: depth keys on oracle adequacy, not blast radius

**Risk is whether the test suite comprehends the change, and that is independent of how many lines moved.** A large change against a complete oracle is safe; a small change against a stale oracle that silently assumed the old architecture is dangerous. So verification depth and the human gate key on whether the oracle exercises the surfaces the change disturbs (including side effects), not on diff size.

The labor allocation is the inverse of the common instinct: **agents take the large, well-specified work whose errors are loud and whose oracle is comprehensive; humans take the small, subtle work where correctness depends on implications no test encodes.** When agents take small work, batch many through one lane to amortize setup. Regardless of the gate, the **consequential surfaces** — authorization, cryptography, destructive migrations, money movement, retention/deletion, safety decisions, and the factory's own control plane — draw mandatory specialist human review independent of change size.

The gate presents a **compact decision package**: what changed and why, the surfaces affected, the oracle's coverage of them, the risk and blast radius, the evidence produced, the controls automatically verified, the controls that required judgment, the residual risks, and the recovery posture — leading with the anomalies and the departures from standard patterns.

Vocabulary: **Agents produce attestations. Policy engines produce pass-or-fail decisions. Accountable humans provide approval or risk acceptance.** An exception to a control requires an explicit, expiring risk acceptance owned by a named human.

## The evidence plane

The authoritative record is the **content-addressed change-evidence manifest** — the immutable ledger recording the digests of the source, the specification, the build plan or contract, the control policy, the artifact, and the configuration; the verifier identity and version; the spec-control results (including the negative-control baseline and positive-control result where present); the test/mutation/security/contract results; the hidden-suite verdict and its compound key where present; the per-environment results; the residual risks; the waivers; and the human approvals. The same artifact digest is promoted, and promotion verifies every cited fact against its authoritative source rather than trusting the manifest's own summary. **The ticket links to and summarizes the manifest; it is not the record.** The manifest is tamper-evident, independently verifiable, rooted in defined trust authorities — not unfakeable.

Every Validator/Judge `PASS` also carries a process-completeness evidence bundle:
the pinned SHA and manifest digest, source hashes consulted, generated-contract
drift results, `.kin` export state, migration consumer registry and expected
schema heads, PR/merge/deploy records, live probe evidence, observability/runbook
proof, waiver records, and rollback/forward authority. A pass record without
that bundle is `UNKNOWN`, not evidence.

Artifacts are produced per an **applicability matrix** (runbook when an alert/operator action exists, migration guide when a migration occurs, decision record when a significant decision is made), each with its trigger, owner, freshness rule, and validation — not every conceivable artifact for every change.

## The factory is itself a production-grade regulated system

Its prompts, models, tools, and policies are versioned; a change to a model or prompt triggers a **requalification suite**. Runs are reproducible from recorded manifests; execution is sandboxed with verified network/secrets isolation; it is hardened against repository-content and prompt-injection attacks. The persistent knowledge graph is **governed** — every node carries source authority, timestamp, ownership, confidence, and expiry; stale context is sandboxed rather than injected; a component change sweeps the graph for related decision records and invariants and reconciles them.

**No agent may modify its own directive, verification policy, approval rules, control-applicability rules, trusted-verifier set, or sandbox permissions while producing or verifying a change under that policy.** The factory must never approve a change to its own approval mechanism; a change to a verifier requires independent approval. Changes to the factory require a separately approved policy version, regression and qualification runs, captured prompt/model/tool/dependency versions, short-lived credentials, network/filesystem allowlists, a complete tool-call audit, cost and execution budgets, and a human-owned control-policy repository.

## The core guarantee

Agents implement or repair, inspect, test, and gather evidence against a versioned target. Correctness is evaluated by an independent evidence and policy plane using deterministic checks, independent oracles, adversarial probes, and live observations. In the correction flow the spec is bounded from both sides against the trusted pre-defect behavior of main. **Depth of verification and review keys on whether the oracle comprehends the change, not on how large it is.** Production automation is capability-bounded, auditable, and limited to approved reversible actions. **No agent may alter the target, the verifier, or the promotion policy while proving its own work.**

The factory does not guarantee correctness, and it does not ask to be trusted on consensus, mutable tickets, or discretionary clicks. It produces independently verifiable, tamper-evident evidence, makes important failures harder to hide, and materially lowers the probability of undetected error — while keeping the decisions that require human judgment in human hands and the authority that could corrupt the verification out of the hands of the thing being verified.

---

## Reinforcing philosophies

The doctrine above is the primary specification. Three surrounding philosophies reinforce it and are folded in here only where they sharpen a point the doctrine already makes.

- **Production-grade minimalism.** Prefer the smallest correct system, but never cut the controls that make failure observable, diagnosable, reversible, and auditable. The operational test is whether an operator can answer *what broke, why, and how to roll back* without deploying new code. This is the same standard the non-negotiables (no silent failure, full auditability, fail-closed) and the environment ladder (reversibility labeled before production) encode — minimalism is a bias toward the smallest system that still carries those controls, not an excuse to drop them.
- **Honesty in self-reports.** Nothing is marked implemented that is partial or absent; every control marked satisfied cites its enforcing artifact; partial capability is marked partial with the gap named and the residual risk disclosed with a named human owner. This is non-negotiable #6, and it applies recursively to the factory's own description of itself (see "Status of this document").
- **Live-verified, not self-attested.** Doneness is established by independent adversarial verification and live end-to-end validation against a running instance — never by an agent's confident summary. This is non-negotiable #7 and the reason the gate keys on oracle adequacy rather than diff size.

The companion executable directives — the ten role agents that operate this specification across the two flows — are in [`AGENT-DIRECTIVES.md`](./AGENT-DIRECTIVES.md).
