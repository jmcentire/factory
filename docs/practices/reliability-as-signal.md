# Reliability as Signal — a factory operate-phase doctrine

**Under the doctrine:** this is a practice under the canonical doctrine in
[`../SOFTWARE-FACTORY.md`](../SOFTWARE-FACTORY.md). It extends the **correction flow**
into runtime: a production failure becomes signal, the signal becomes a reproducing
test, and the factory closes the loop with a validated patch — the same triage →
hidden-test → repair → judge cycle, now fed by production rather than only by a reported
bug.

**Status:** operate-phase doctrine of the factory process. Target-agnostic: it names no
target and depends on no target code.

**Provenance:** the full, exhaustive treatment is the Production-Grade Build Playbook,
**Phase 7.6 — "Reliability as Signal: A Living, Closed-Loop Control System"**
(`production-build-playbook/src/07c-reliability-as-signal.md`). This doc states the
load-bearing rules and how they ride the factory's flows; the playbook chapter is the
reference and carries the two-tier gate.

## The stance

Read every rule here as a **heuristic, not arithmetic** — it names a property that must
hold or a failure mode to avoid, never a universal constant or formula. Any single value
or formula is wrong in some circumstance; the open values (ceilings, budgets, thresholds,
retention windows, masks) are owner- and context-set, and in a living system they are
*learned and re-tuned from outcomes*. **False precision in a guard is itself an
anti-pattern**, and so is the nirvana reflex — attacking a sound guard for failing to be a
perfect formula when the alternative is ignoring the outage.

## The load-bearing rules

1. **A failure is signal, a recovery is not silence, a page is not a symptom, severity is
   confirmed impact — not rule identity.** Everything learned about a failure is captured
   as typed, disposition-tagged, scope-estimated signal.
2. **Two orthogonal axes.** *SEV = impact* (per incident, posture-invariant confirmed
   blast radius); *DEFCON = awareness* (system posture over time; scoped, not global;
   widens capture by traversing the event graph under unresolved-and-undiagnosed;
   outputs are cost-gated capture/detection, never pages). Raising awareness improves the
   ability to *confirm* impact; it never inflates severity.
3. **Recovery signals, but SLO burn is driven by *measured* objective violation, not the
   declared disposition label.** Disposition (Recovered / Degraded / Failed) and
   objective-violation are two recorded facts; a recovery touching an unmeasured
   dimension is Degraded-unknown and escalates.
4. **Two-layer error contract** — RFC 9457 problem+json externally (no internals), a
   typed internal signal (failure-type, disposition, retry-safety, scope+confidence,
   provenance, cause-chain) as a versioned interface.
5. **Failure-mode coverage, not line coverage,** gates critical paths: enumerate every
   failure mode over a *reviewed* denominator, map each to a handler + declared
   disposition, and give each a forcing test that asserts both the disposition and the
   emitted signal. Metamorphic relations stand in where there is no oracle.
6. **Validate before you page — and never serialize a catastrophic page behind
   validation.** The deterministic `validate → scope → severity → page` path owns the
   page decision on one model-free clock; the agent enriches within an MTTR-scaled
   budget and never gates it.
7. **The canary must fail first** — same artifact by digest, stricter escalation, a
   disposition-rate comparison analyzed with volume-aware sequential inference.
8. **The control plane proves itself alive** — a dead posture controller looks exactly
   like a calm system, so liveness is itself emitted evidence and its silence pages.
9. **The loop is living and closes into the factory** — values are learned not set;
   masking is reversible demotion, never deletion (lower the volume, never disconnect the
   wire); the captured failure window *is* the reproducing test the factory writes a fix
   against. **Authoring is not shipping.**

## How it rides the factory's rails

- **Correction flow.** The captured failure window is the **hidden test**: it reproduces
  the incident and fails before the repair, passes after. The repair agent writes the
  fix; the judge validates; **no auto-authored patch merges without its reproducing
  test.** This is the playbook's "prove it, don't assert it," made the merge gate.
- **Authority separation.** The diagnostic agent may observe, hypothesize, and run
  allowlisted read-only diagnostics or pre-approved reversible mitigations — it never
  becomes the authority that ships an unbounded cure, and no agent alters the
  target / verifier / policy while proving its own work.
- **Evidence model.** Every disposition emits a signal; the control plane's liveness is
  evidence; the effort × impact prioritizer's estimates are scored against outcomes so
  the expenditure policy itself is auditable.

## Gate linkage

The playbook chapter carries a **two-tier gate**. Adopt **Tier A** (the settled doctrine
above) into the operate-phase gate, each item checked with cited evidence. Treat **Tier
B** as funded spikes, never gates — structural (shape-of-cascade) detection, whole-system
deterministic simulation, mutual-information-governed posture-input selection, and the
OpenEvent event-relationship graph. The binding rule: **no Tier-A gate or output may be
reachable only through a Tier-B input** — the loop must compute a valid result with every
research bet absent.

## Impact valuation & the learned baseline

Three pieces the severity model depends on, each with a safety rail that is gated
(the learning itself is a funded spike):

- **Impact is a declared per-event `value`** (a constant or a field expression, e.g.
  `0.10 * subtotal`), evaluated at incident time against the instance so a large
  checkout outranks a small one. It is **unit-declared** — impact never crosses units
  (no raw cross-unit sum) — the expression is **sandboxed and fail-closes to UNKNOWN
  impact (which escalates), never zero**, and it is **gated by disposition** (realized
  impact = value × disposition factor: Recovered ≈ 0 user-impact, Failed = full,
  Degraded = partial).
- **The OpenEvent graph is the learned baseline** structural detection measures novelty
  against. **Declared edges** are contract; **derived edges** are hypotheses carrying
  confidence + decay, mined asynchronously and **consumed as a prior, never causation** —
  provenance-separated the way the two-layer error contract is, and a derived edge that
  *moves severity* is MI-governed. (Same shape as the provenance-tracked, edge-decaying,
  typed relationship graph a knowledge graph already implements.)
- **Thresholds are percentiles of the learned per-unit incident-value distribution** —
  self-calibrating, but this carries the doctrine's most dangerous failure, **the
  normalized baseline**: a baseline learned while chronically broken calibrates "normal"
  to dysfunction and rounds a real outage down to ordinary (the O-ring failure). Required
  rails: **absolute SEV-1 floors override percentiles**, the **baseline's own trend is a
  meta-signal** (not the new normal), and **cold-start uses declared thresholds**; never
  pool units.
