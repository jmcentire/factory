# The Doctrine Kernel — tiered intent inventory

> **Status: RATIFIED by the founder, 2026-07-21** (in-session: "The kernel's tiers look
> good"). An agent may still treat a statement as *more* protected than listed, never
> less. Only the founder may move a statement down a tier or remove it. Changes to this
> file pass through the Diff-Intent Gate
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
| S1 | Humans own intent, architecture, and risk; the factory implements, proves conformance, and produces evidence. |
| S2 | **Exactly three standing agent roles: Code, Test, Validate.** Specs are human-owned artifacts; diagnosis is a stage; the hidden suite is a protected Test-role instance. |
| S3 | The authority is signed artifacts — the specs and the content-addressed manifest — never memory, never a mutable ticket, never an agent's summary. |
| S4 | Agents escalate; humans ratify. No agent ratifies a material change to declared intent; genesis and mutation of doctrine require a human signature. |
| S5 | Honesty in self-reports: nothing marked done, implemented, or satisfied that is partial or absent. |

## Tier I — Invariant by design

| # | Statement (anchor) |
|---|---|
| I1 | The seven non-negotiables (fail-closed on hazards; single authoritative owner per fact; least privilege; full auditability; no silent failure; honesty; live-verified not self-attested). |
| I2 | Segregation of duties: implementer ≠ verifier ≠ approver; identity resolution is deny-wins; consequential changes carry the ≥2-distinct-enrolled-humans floor. |
| I3 | Oracle independence: expected behavior derives from the signed spec and is frozen before the implementation is inspected; the hidden suite is unreadable by the repairing agent and returns only coarse verdicts. |
| I4 | The two controls (negative and positive) bound every correction spec against the trusted baseline. |
| I5 | The gate keys on oracle adequacy, not blast radius; consequential surfaces draw mandatory human review regardless of size. |
| I6 | The same built artifact is promoted up the ladder by digest; the evidence plane is content-addressed, hash-chained, and tamper-evident. |
| I7 | The generic core names no target: targets are data behind adapter seams, never code imports (the purity guard enforces the code side; this doctrine enforces the prose side). |
| I8 | No agent modifies its own directive, verifier set, approval rules, or sandbox permissions while producing or verifying a change under that policy. |

## Tier E — Epistemic

| # | Statement (anchor) | Current evidence basis |
|---|---|---|
| E1 | Cross-model diversity reduces correlated misreading; the verifier runs a different vendor than the implementer. | Correlated-failure research + converged industry practice. Revisable if measurement shows a better independence mechanism. |
| E2 | Agents take large, loud, well-oracled work; humans take small, subtle work. | Oracle-adequacy observations. Revisable as oracle coverage changes. |
| E3 | Interpretation depth degrades intent (each re-delegation is a lossy reconstruction); breadth against a fixed artifact preserves independence. | Founder multi-agent research (hop/relay and swarm studies). Refinable — e.g., relay degradation saturates; production-under-reconstruction diverges. |
| E4 | Retry is recovery, not search: fresh context, bare pass/fail history, budget caps. | Observed retry pathologies. Parameter values are per-target data. |

## Tier X — Exercise to the reader

| # | Freedom |
|---|---|
| X1 | Implementation structure, style, and libraries within the signed contracts. |
| X2 | Vendor/model assignment per role, provided E1's independence holds. |
| X3 | All per-target configuration: tiers, categories, thresholds, denylist tokens, adapter selections, environment-rung composition. |
| X4 | Tooling that assists a stage (spec ingest, diagnosis, coordination), provided it claims no role authority. |

## Rule of interpretation

A statement not listed here is not thereby free: unlisted commitment language falls to
the Diff-Intent Gate's inference lane and is escalated when in doubt. The kernel exists
so that the common case is a lookup, not a judgment — and so that when an agent infers,
the inference is visibly an inference.
