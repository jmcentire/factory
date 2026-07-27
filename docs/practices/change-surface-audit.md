# Change-Surface Audit — a factory spec-phase requirement

**Under the doctrine:** this is a practice under the canonical doctrine in
[`../SOFTWARE-FACTORY.md`](../SOFTWARE-FACTORY.md) — it operationalizes the
incomplete-enumeration defense (§14) across the three phases and rides the
oracle-adequacy gate.

**Status:** required three-phase deliverable of the factory process. Target-agnostic:
this discipline is authored before the build loop for *any* target the factory serves;
it names no target and depends on no target code.

**Provenance:** generalized from the software-factory playbook (§3.7.2) proven on the
origin/reference target. See `docs/PROVENANCE-SYNC.md` for the sync record.

## The requirement

The signed phase artifacts for a change carry one explicit **Change-Surface Audit**.
Phase 1 states the observable affected behavior, phase 2 enumerates *every* structural
surface and interaction the change touches — walking the whole consumer / accessor /
caller / reporting / job / contract tree — and phase 3 authorizes the tests and
operational evidence for each one. This is the "shake the tree" discipline done before
the build loop, not discovered at implementation time, so the blast radius and the
surface-criticality inheritance graph are explicit **before any code exists**.

A change whose spec does not enumerate its touched surfaces and their
invariant-vs-changed classification is an **incomplete spec** and does not advance to
the build phase. This is a gate, not a suggestion.

## Per-interaction classification

For each touched interaction the spec classifies it and calls it out **by name**:

- **HELD INVARIANT** — the interaction's observable contract must not change:
  behavior, payload shape, ordering, error semantics, the authorization / role gate,
  and any tenant / row isolation. Each held-invariant interaction gets a **locking
  test** that fails if the contract drifts, and that locking test must be authorized
  in the signed Testing and Monitoring Strategy before build.
- **INTENTIONALLY CHANGED** — the new contract is stated explicitly, and the spec
  carries (a) a test asserting the **new** contract and (b) a test or argument that
  every *other* consumer of the surface is **unaffected**, or is migrated in the same
  change. Those new-contract and other-consumer proof tests must also be authorized in
  the signed Testing and Monitoring Strategy.

Every touched interaction must land in exactly one of these two buckets. "Not
considered" is not a valid classification — an unclassified touched surface fails the
audit.

This invariant-vs-changed classification is orthogonal to **Critical / Standard /
Cosmetic**. The former says whether the contract moves; criticality says what being
wrong costs and therefore how an evidence gap is disposed. Phase 2 records, for every
surface, the human decider, wrong-cost rationale, criticality class, and every
side-effect edge. An omitted or invalid criticality decision resolves to Critical. A
change inherits the highest class reachable through the declared edges.

Phase 2 also enumerates every tool, credential, network route, and external integration
the change or its verification may reach. Phase 2 or phase 3 authorizes one exact tier
and scope for each entry; the signed run tool policy projects those decisions into
enforceable grants. A tool absent from the inventory is Verboten rather than implicitly
available.

## How it rides the rails the factory already has

- **Present consumers.** Enumerate every system that reads or acts on the object being
  changed — the whole accessor / reporting / job / contract tree — and decide, per
  consumer, whether the change applies to it.
- **Future accessors.** Make inclusion of any new class **explicit and opt-in at the
  accessor layer**: a clearly-named accessor returns the new class; existing accessors
  default-exclude it so future consumers do not silently inherit it. Default-safe;
  opt-in to include.
- **Observability.** Add the logging / alerts / telemetry / reporting that let someone
  diagnose the change later.

For platform-level invariant surfaces, the audit feeds the **capability delta** and its
composition gate (see the invariant-kernel IR and the capability-delta schema at
`factory_core/schemas/capability_delta.schema.json`). For everything else it generalizes
the three "what could go wrong?" passes (present consumers → future accessors →
observability) from an implementation-time habit into a **named spec deliverable with
tests attached**.

## The failure class it catches

The **interaction regression**: a shared surface silently changing one consumer's
contract while still satisfying its own local spec. The classic instances are a
frontend calling a moved or removed backend endpoint (a forward-contract break), and
two owners building the same surface in parallel (a discovery collision). Both are
typically caught *late*, by gates near release. Auditing touched surfaces at product-
and technical-spec time — with explicit invariant-vs-allowed-change call-outs and
locking tests — moves that catch **left**, before code exists.

## Minimum audit shape (spec template)

```
Change-Surface Audit
--------------------
For change: <change id / title>

Touched surfaces (walk the consumer / accessor / caller / reporting / job / contract tree):

| Surface / interaction | Consumers | Classification      | Locking or contract test | Other-consumer proof |
|-----------------------|-----------|---------------------|--------------------------|----------------------|
| <surface A>           | <who>     | HELD_INVARIANT      | <test id>                | n/a                  |
| <surface B>           | <who>     | INTENTIONALLY_CHANGED | <new-contract test id> | <unaffected/migrated proof> |

Surface control profile:

| Surface | Component | Criticality | Human decider | What being wrong costs | Side effects reach |
|---------|-----------|-------------|---------------|------------------------|--------------------|
| <surface A> | <component> | <Critical / Standard / Cosmetic> | <human> | <worst outcome> | <surface ids> |

Present consumers:  <enumeration + per-consumer decision>
Future accessors:   <the opt-in accessor + the default-exclude of existing accessors>
Observability:      <logging / alerts / telemetry / reporting added>

Operational-maturity linkage:
  <Testing and Monitoring Strategy item ids for each test/proof>

Provenance linkage:
  <exact phase-artifact digest + item backreference for each requirement, constraint, task, and assertion>

Tool-policy linkage:
  <inventory item, tier, scope, and exact phase-2/3 backreference for every reachable capability>

Capability-delta linkage (platform-invariant surfaces only):
  <capability_delta document reference, or "none — no platform-invariant surface touched">
```

## Acceptance rule

The audit is complete when:

1. every touched interaction is enumerated (the tree is walked, not sampled), and
2. each is classified HELD_INVARIANT or INTENTIONALLY_CHANGED, and
3. every HELD_INVARIANT interaction has a named locking test, and
4. every INTENTIONALLY_CHANGED interaction has a new-contract test **and** an
   other-consumer-unaffected proof (test or migration), and
5. every named test/proof is authorized in the signed Testing and Monitoring Strategy
   with fixture, environment rung, expected evidence, and a resolvable backreference,
   and
6. the audit scans for sites not in its enumeration and fails when a new one appears,
7. each surface carries a human-decided criticality class and wrong-cost rationale,
8. every declared side-effect target is also present in the profile and the change
   inherits the highest reachable class, and
9. any platform-invariant surface it touches is reflected in a capability delta, and
10. every reachable tool, credential, route, and integration has exactly one phase-authorized
    tier and scope in the run tool policy.

An incomplete audit blocks advancement to the build phase.
