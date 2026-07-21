# Factory-Native Orchestration: The Boundary

> What Factory owns that Pact and Baton do not — and, just as important, what it must never rebuild.

## Status of this document

This is a **positioning proposal**, written from an outside evaluation of the factory core against
the surrounding Exemplar stack, not a specification of running code. Its empirical claims about
Pact, Baton, and Exemplar were checked against those repositories' source on 2026-07-20 (versions
and file references are cited inline); its claims about `factory_core` distinguish, in the same
present-tense discipline the rest of these docs use, between what is **implemented library code**
and what is **doctrine**. Where it proposes a boundary, that boundary is a design intent for
review — a control described here is not a control that runs.

## The question this answers

Factory has no running orchestration engine; the engine, the live agent lanes, and the control
plane are doctrine (`README.md`, "Doctrine → code mapping"; `docs/SOFTWARE-FACTORY.md`, "Status").
The reasonable next question is "so build the engine" — and it is the wrong first move. The broader
Exemplar suite already ships two substantial orchestration engines. Building a third, generic one
would duplicate them and earn nothing. Before any engine work, Factory must state precisely what it
orchestrates that the existing engines do not. That is this document.

## The two engines that already exist (verified against source)

**Pact is the build-time execution plane.** It is a real contract-first, multi-agent build engine —
a `Scheduler` state machine with an event bus, a daemon, a budget tracker, and health
instrumentation (`pact/src/pact/scheduler.py`, `lifecycle.py`), roughly 32k LOC of `src` behind
2083 tests. It runs a full phase sequence (interview → decompose → contract → test → implement →
integrate → certify) and enforces tests-as-gates that fail closed. Default `pact run` is plan-only;
`--implement` drives the whole multi-agent build loop. It emits a tamper-evident
`CertificationArtifact` (SHA-256 per-artifact digests plus a self-integrity hash,
`certification.py:35`) and, on deploy, a `baton.yaml`. It explicitly hands **deploy off to Baton**,
**trust-gating off to Arbiter**, and **production attribution off to Sentinel**, and it does not own
those.

**Baton is the run-time execution plane.** It is a real cloud-agnostic circuit orchestrator —
async reverse-proxy adapters, health/custodian self-healing with anomaly detection, canary
promote/rollback, mock collapse, federation (`baton/src/baton/adapter.py`, `custodian.py`,
`canary.py`), roughly 14.3k LOC behind 983 tests, deployed. It emits OTLP spans and metrics and
runtime health state. It is **runtime-only**: it carries no change identity, no cross-stage
promotion object, and no content-addressed evidence. Its gates are runtime admission controls
(slot-time contract validation, an Arbiter trust check for low-trust nodes) and its "promotion" is
canary traffic-weight shifting — not a build-to-release gate.

Neither engine is Factory's to reimplement. The evaluation confirmed the obvious division:

| Plane | Owner | What it governs |
|---|---|---|
| Build-time execution | **Pact** | decomposition, contracts, hidden tests, the build loop, build certification |
| Run-time execution | **Baton** | topology, routing, health, self-healing, canary traffic promotion |
| **Cross-stage governance** | **Factory (proposed)** | the identity, evidence continuity, and promotion gate that bind the two |

## The gap: evidence is fragmented and the stages do not connect

The stack's lifecycle — build → deploy → runtime — is **documented but not composed**. Each phase
is a separate tool; nothing supervises the whole while carrying governance and evidence across the
seams. This is not an inference; the stack says so. `exemplar-stack` frames composition as future
manual work ("the next phase is composition"), and its own handoff doc marks the end-to-end
Reeve → Baton → Sentinel → Tessera flow as "Not Yet Enforced." No component is named `factory` or
`factory_core` anywhere in Exemplar or exemplar-stack; the niche is empty.

The evidence story is the sharpest symptom. Three real hash primitives exist, and all three are
**scoped and discontinuous**:

| Primitive | Where | Scope | Missing |
|---|---|---|---|
| Pact `CertificationArtifact` | `pact/src/pact/certification.py:35` | one build snapshot | no chain, no segregation of duties |
| Exemplar `TesseraSeal` | `exemplar/src/reporter/reporter.py:208` | one product's review reports | no SoD, no build/runtime linkage |
| Baton OTLP + `.baton/events.jsonl` | `baton/src/baton/otel.py`, `dora.py` | runtime telemetry | not content-addressed, not a ledger |

A build produces a certification. A deploy produces OTLP events. Nothing carries the identity of a
change from the certification that proved it, through the promotion decision that released it, into
the runtime events that observed it. There is no cross-stage, tamper-evident, segregation-of-duties
ledger, and no default-deny promotion gate keyed on one. Arbiter, the nearest thing to a supervisor,
is a single classification-keyed **trust-gate client** (`pact/src/pact/arbiter.py:34`) — it returns
`human_gate_required` / `soak` / `blast_radius`; it does not verify an evidence chain and does not
enforce distinct implementer/verifier/approver identities.

## What Factory owns

**Factory is the cross-stage governance control plane.** It owns the spine that makes the existing
engines' handoff auditable and gated — and only that. Concretely, Factory owns:

1. **Change identity across stages.** One content-addressed identity for a change that persists from
   build certification through promotion into runtime observation, so evidence from different
   engines can be attributed to the same change.
2. **Evidence continuity.** A single append-only, content-addressed, hash-chained ledger
   (`factory_core/manifest.py` — implemented, tested) that *ingests* the engines' native evidence
   (Pact's `CertificationArtifact`, Baton's deploy/canary events) as entries rather than
   re-deriving them. Factory does not generate build or runtime evidence; it makes the trail
   continuous and tamper-evident end to end.
3. **The promotion gate.** A fail-closed, default-deny decision (`factory_core/promotion.py` —
   implemented, tested) that enforces segregation of duties (implementer ≠ verifier ≠ approver) and
   a consequence-tiered human-approver floor — guarantees no component in the stack provides today.
   Arbiter's trust score is an *input* to this gate, not a replacement for it.
4. **Role-lane governance.** The capability/role/grant model (`factory_core/roles.py` — schema
   implemented; the live lanes are doctrine) that says which identity may occupy which lane, so the
   SoD the gate enforces is grounded in enrolled principals, not convention.

These are more general than the stack's fragments and have no generic equivalent in it — verified by
comparison, not assumed. The overlap with the `pact.yaml` / `component_map.yaml` / `trust_policy.yaml`
convention is vocabulary only: those are loose, unsigned, unvalidated *build inputs* to Pact;
Factory's manifest, ledger, and gate are signed, schema-validated, hash-chained *governance*
primitives at a different layer and lifecycle phase.

## Non-goals (what Factory must never build)

- **Not a build engine.** Decomposition, contracts, the multi-agent build loop, and build
  certification are Pact's. Factory consumes Pact's output; it does not reproduce it.
- **Not a runtime orchestrator.** Topology, routing, health, self-healing, and canary traffic
  control are Baton's. Factory reads Baton's events as evidence; it does not move traffic.
- **Not a trust scorer.** Per-agent/component trust weighting is Arbiter's. Factory treats a trust
  verdict as a gate input.
- **Not a per-product composition.** Exemplar and Reeve wire the stack for one product each; they are
  explicitly not meant to be the permanent owner of the controls. Factory is the generic layer above
  any product — target-as-data, never target-specific (the purity guard enforces this: no
  `pact` / `baton` / `exemplar` / `arbiter` / `tessera` token appears in `factory_core/`).

## First slice, to prove the spine

Keep it to evidence continuity, the concern most clearly missing and most clearly Factory's:

> Ingest a Pact-style `CertificationArtifact` and a Baton deploy/canary event as entries in the
> `factory_core` ledger under one change identity, then run the `promotion.py` gate over that chain —
> demonstrating a single tamper-evident trail from build proof to runtime observation, gated by a
> default-deny, SoD-enforcing decision that neither engine offers.

This builds nothing either engine already has. It wires their existing outputs into the one thing
the stack is missing, and it exercises exactly the two primitives (`manifest.py`, `promotion.py`)
that are already real, tested, and generic in the core.
