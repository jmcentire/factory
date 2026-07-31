# The Doctrine Kernel — tiered intent inventory

> **Status: RATIFIED by the founder, 2026-07-21; reconciled to the founder-supplied
> three-role/three-phase doctrine revision, 2026-07-26, and Criticality amendment,
> 2026-07-27, plus the invariant/tool/checklist amendment, 2026-07-27.** An agent may still
> treat a statement as *more* protected than listed, never
> less. Only the founder may move a statement down a tier or remove it. Changes to this file
> pass through the Diff-Intent Gate
> ([`practices/diff-intent-gate.md`](./practices/diff-intent-gate.md)) like any other
> doctrine change — an agent proposing an edit here escalates; it never ratifies.

This is the prose analog of the invariant kernel: the declared inventory of what each
load-bearing statement *is*, so the Diff-Intent Gate does tier lookup instead of
judgment. Four tiers:

- **S — Sacrosanct.** Founder axioms. Change requires an explicit founder signature;
  there is no evidence that revises them from below.
- **I — Invariant by design.** Engineered invariants. Changeable only by explicit
  redesign through the human gate, with the redesign named as such.
- **E — Epistemic.** Held because current evidence supports them. Revisable — but only
  with new cited evidence and provenance, through the human gate.
- **X — Exercise to the reader.** Deliberately unbound. Implementation freedom;
  no gate beyond the ordinary flows.

## Tier S — Sacrosanct

| # | Statement (anchor) |
|---|---|
| S1 | Humans own product intent, architectural decisions, authority, and acceptable risk. The Validator drafts and the human decides; the factory implements, proves conformance, and produces evidence. |
| S2 | **Exactly three roles: Validator, Coder, Tester; exactly three pre-build phases: product specification, architecture, operational maturity.** Coder and Tester share the signed spec, have no channel to each other, and the Validator runs the tests. |
| S3 | Intent authority exists only in the Product Specification, Architecture Specification, and Testing and Monitoring Strategy, each signed, content-addressed, immutable for the run, and compared with the preserved verbatim input — never memory, a mutable ticket, a comment, or an agent's summary. The manifest records evidence; it does not originate intent. |
| S4 | Agents escalate; humans ratify. No agent ratifies a material change to declared intent; genesis and mutation of doctrine require a human signature. |
| S5 | Honesty in self-reports: nothing marked done, implemented, or satisfied that is partial or absent. |

## Tier I — Invariant by design

| # | Statement (anchor) |
|---|---|
| I1 | The eight non-negotiables (fail-closed on hazards; single authoritative owner per fact; least privilege; full auditability; no silent failure; honesty; provenance of intent; live-verified not self-attested). |
| I2 | Segregation of duties: implementer ≠ verifier ≠ approver; identity resolution is deny-wins; Critical changes carry the ≥2-distinct-enrolled-humans floor and mandatory specialist review. |
| I3 | Oracle independence: Coder and Tester receive the same signed spec but have no channel; the Tester never sees the implementation, the Coder never sees the tests, and the Validator returns only bare failure outcomes to an automated repair context. |
| I4 | The two controls (negative and positive) bound every correction spec against the trusted baseline. |
| I5 | Oracle adequacy and criticality are independent axes: depth keys on oracle adequacy, never blast radius; a gap blocks on Critical, gates for expiring human risk acceptance on Standard, and reports-and-promotes on Cosmetic. |
| I6 | The same built artifact is promoted up the ladder by digest; the evidence plane is content-addressed, hash-chained, and tamper-evident. |
| I7 | The generic core names no target: targets are data behind adapter seams, never code imports (the purity guard enforces the code side; this doctrine enforces the prose side). |
| I8 | No agent modifies its own directive, verifier set, approval rules, or sandbox permissions while producing or verifying a change under that policy. |
| I9 | Provenance of intent distinguishes absence from corruption: a missing link is an evidence gap disposed by surface class; an unresolvable, mismatched, fabricated, or malformed link blocks every class. |
| I10 | Criticality is human-decided per surface; a change inherits the highest declared side effect; unclassified is Critical. Critical evidence is deterministic with zero flake/retry tolerance and no waiver. |
| I11 | Every downstream backreference binds both the exact invariant-document digest and canonical item digest. A new signed artifact version invalidates every plan, test, control, and evidence reference derived from the old version. |
| I12 | Every run tool, credential, route, and integration has exactly one signed tier: Allowed, Sign-off required, or Verboten. Unknown and Verboten are absent/denied; scope ceilings are enforced before execution; Sign-off authority is human, fresh, scoped, and expiring; denial probes demonstrate the boundary. The tool policy projects phase-2/3 authority and cannot widen it. |
| I13 | Every phase and promotion gate is an explicit checklist whose items are satisfied only by individually cited, content-addressed evidence recorded when obtained. Unchecked or uncited remains a visible gap; negative or invalid evidence cannot be converted to a pass. |
| I14 | A formerly passing test is updated only when an exact signed item supersedes its asserted behavior. Unchanged authority means fix the implementation; artifact silence or conflicting supersession routes to the human. |
| I15 | A green-now guard that fails against main on behavior unrelated to the defect is a **suspected over-constraint**: it stops and routes to the human. It is never reclassified as a red-now forcing test, and no implementation is driven to satisfy it. Every test that changed state against main is classified individually, not in aggregate. |
| I16 | Every production monitor is **spec-derived** and carries a resolvable backreference to the acceptance criterion or invariant it watches; an unresolvable monitor backreference is an unauthorized assertion about production and blocks. Monitor authorship is class-scoped: Critical surfaces carry human-authored monitors. Density is recorded, never gated. |
| I17 | An agent that evaluates an alert may not delete, weaken, or silence the monitor that produced it. Silencing is a change to the oracle: it is a proposal ratified by a human through the specification-defect path. Monitor state (proposed-fix references) lives on the monitor, not in the agent. |
| I18 | A defect is reproduced in a disposable environment and the reproduction recorded before any repair is written. Reproduction-impossible is a declared lane condition that gates; a reproduction that does not reproduce routes to the human rather than authorizing a repair. |
| I19 | The independence tier actually achieved, plus the model, model version, and directive/prompt version of every agent that produced or judged the change, are recorded in the change-evidence manifest. A claimed tier the recorded arrangement does not support is a false verdict and blocks every class. |
| I20 | Detection is exhaustive; notification is earned. A signal reaching a human carries a human-actionable conclusion, and an unactionable alert is answered with a better conclusion or a specification defect — never a quieter monitor. |
| I21 | The accountable-human seat on a Critical surface is filled from an explicit **named delegate roster** of enrolled humans recorded per target. An undeclared roster is an evidence gap disposed of by class, never a permissive default. |
| I22 | Document parity is a Validator-gated control, not an inspection checkbox: the Coder produces current docs/contracts/generated-artifacts/compliance-corpus, the Tester authors the parity tests, and the Validator gates from the pinned SHA. It is machine-forced where mechanizable — generated artifacts (OpenAPI, stubs, types, knowledge export) are regenerated and diffed clean, and compliance/design coverage is a test (every ≥Standard surface resolves to a named control; every claimed-satisfied control resolves to enforcing evidence); where only inspection is possible, the basis and residual risk are declared. A document silently out of parity is negative evidence, not an absent nicety. |

## Tier E — Epistemic

| # | Statement (anchor) | Current evidence basis |
|---|---|---|
| E1 | Cross-model diversity reduces correlated misreading; the verifier runs a different vendor than the implementer. | Correlated-failure research + converged industry practice. Revisable if measurement shows a better independence mechanism. |
| E2 | Agents take large, loud, well-oracled work; humans take small, subtle work. | Oracle-adequacy observations. Revisable as oracle coverage changes. |
| E3 | Interpretation depth degrades intent (each re-delegation is a lossy reconstruction); breadth against a fixed artifact preserves independence. | Founder multi-agent research (hop/relay and swarm studies). Refinable — e.g., relay degradation saturates; production-under-reconstruction diverges. |
| E4 | Retry is recovery, not search: fresh context, bare pass/fail history, budget caps. A flaky test rerun to green is the same sampling error at test granularity. | Observed retry pathologies. Parameter values are per-target data; Critical test evidence has no retry budget. |

## Tier X — Exercise to the reader

| # | Freedom |
|---|---|
| X1 | Implementation structure, style, and libraries within the signed contracts. |
| X2 | Vendor/model assignment per role, provided E1's independence holds. |
| X3 | All per-target configuration within the fixed doctrine: surface/component ids, human-decided classes and wrong-cost rationales, side-effect edges, Standard flake budgets, gate/evidence ids, tool/inventory/scope ids within the fixed tier semantics, denylist tokens, adapter selections, and environment-rung composition. |
| X4 | Tooling that assists a stage (spec ingest, diagnosis, coordination), provided it claims no role authority. |

## Rule of interpretation

A statement not listed here is not thereby free: unlisted commitment language falls to
the Diff-Intent Gate's inference lane and is escalated when in doubt. The kernel exists
so that the common case is a lookup, not a judgment — and so that when an agent infers,
the inference is visibly an inference.
