# factory_core — working agreement (Claude)

Founder-owned, portable software-factory core. Generic by construction: it imports **nothing
target-specific**. This is Jeremy McEntire's own IP (proprietary; see `LICENSE`/`PROVENANCE.md`),
separate from and shippable independently of any consuming project.

This file is the authoritative self-description. The factory dogfoods itself: it can target
this very repo, reading this file and `.kin/knowledge.jsonl` to bootstrap its understanding.

## The one boundary that matters

`factory_core` is the generic core; every target is **data**. Per-target inputs (repo coords,
working-agreement docs, compliance rules, role bindings, IdP config) are loaded at runtime
through the adapter seams in `factory_core/adapters.py` — never a code import. Deleting every
target pack must leave the core importable, testable, and green.

**Before you touch `factory_core/`, ask: does this introduce anything target-specific?** If a
change would import a target, name a target token, or add a target dependency, it is wrong by
construction — the purity guard will (and must) reject it.

## Layout

- `factory_core/manifest.py` — content-addressed, hash-chained, SoD-enforcing evidence ledger (stdlib-only).
- `factory_core/evidence.py` / `checklist.py` — subject-bound evidence and independently cited
  checklist items whose omissions remain visible.
- `factory_core/criticality.py` — surface control profile, declared side-effect closure, and
  unclassified-to-Critical resolution.
- `factory_core/promotion.py` — oracle-adequacy × criticality promotion decision.
- `factory_core/verdict.py` — the global-property layer above promotion: closed
  content-addressed coverage map, ratified adequacy criteria, characterization
  receipts, frame-check binary, assumption records, and the mechanically
  unpersuadable monotone verdict with the forced first line ("does it do the thing
  it was built to do?"); prose is never an input.
- `factory_core/handover.py` — typed `__HANDOVER__` lane completions with explicit
  scope boundaries, first-class retraction bound to a forcing event, the
  reserved-token scan, and `compose_done` — the only path that mints `__DONE__`,
  reachable solely when the handover scope-union covers the ratified verb set over a
  PASS verdict.
- `factory_core/provenance.py` — canonical phase-artifact/backreference verifier and
  absence-vs-integrity issue classification, including whole-artifact version binding.
- `factory_core/build_plan.py` — qualified recipe-pattern catalog and disposable per-run build
  IR with immutable configuration, full intent coverage, oracle links, and freshness binding.
- `factory_core/test_disposition.py` — same-phase signed supersession plus an exact externally
  trusted, dual-ratified impact ruling / regression / ambiguity classifier for formerly passing
  tests.
- `factory_core/independence.py` — the five-tier independence ladder derived from the recorded
  arrangement, per-agent model/directive versions, and the structural-depth trade.
- `factory_core/monitors.py` / `triage.py` — spec-derived monitors with resolvable authority and
  class-scoped authorship, and triage that cannot silence the monitor it evaluated.
- `factory_core/correction.py` — red-now/green-now controls, the suspected-over-constraint rule,
  and reproduction before repair.
- `factory_core/tool_policy.py` — signed Allowed / Sign-off-required / Verboten run-policy and
  scoped pre-execution decision.
- `factory_core/target.py` — `TargetManifest` loader (TOML + JSON Schema; refuses code
  references and binds pattern catalog, construction modes, and attempt ceiling).
- `factory_core/adapters.py` — the five `typing.Protocol` seams (interfaces only).
- `factory_core/roles.py` — capability/role model schema (grants are per-target data).
- `factory_runtime/state.py` — persisted lifecycle ledger, checked projection, frozen generation
  tuple, and bounded-attempt enforcement.
- `factory_runtime/durability.py` / `tessera.py` / `authority.py` — local-POSIX evidence
  durability, real Tessera CLI, and external authority.
- `factory_runtime/workflow.py` — authorized intake and invariant-document ratification.
- `factory_runtime/generation.py` / `snapshot.py` — target/phase/build-plan readiness and retained
  exact generation/review bytes.
- `factory_runtime/isolation.py` / `lanes.py` — qualified platform isolation and asymmetric role
  projections (Tester never receives construction IR).
- `factory_runtime/transition_obligations.py` / `acceptance_obligations.py` — code-selected,
  versioned lifecycle obligations plus human-ratified acceptance catalogs (Validator receipt retained as attribution) and
  host-derived reports over exact immutable subjects.
- `factory_runtime/test_change_authority.py` — retained exact test-expectation rulings bound to
  current phase supersession, signed by an enrolled human (Validator receipt as attribution).
- `factory_runtime/resume.py` — pre-mutable-state verification of externally supplied checkpoints
  over retained signed roots, lineage, resources, configuration, and retention policy.
- `factory_runtime/runner.py` / `runner_isolation.py` / `projection_bundle.py` — closed-environment,
  resource-bounded, path-free model execution with canary and same-session-resume qualification.
- `factory_runtime/broker.py` — typed signed capability handles resolved through checkpoint-bound
  host registries; models never choose paths, executables, argv, working directories, or scripts.
- `factory_runtime/evidence_plane.py` / `orchestrator.py` — retained-output evidence and runtime
  to signed preview.
- `factory_runtime/cli.py` — executable command boundary.
- `scripts/check_core_purity.py` — the fail-closed anti-coupling guard.
- `scripts/check_doctrine_sync.py` — structural parity guard for the active doctrine surfaces.
- `core_purity_denylist.json` — the token-denylist **data file** the guard reads (empty on the
  generic core; a target fills in its own tokens as private config, never shipped in the core).
- `core_purity_baseline.json` — justified token exceptions (empty on a clean core).
- `tests/` — pytest suite, incl. the portability proof and the purity guard tests.
- `tests/fixtures/synthetic_target/` — the synthetic empty target for the portability proof.
- `prompts/` — the role and review prompts (Validator/Coder/Tester/orchestrator lanes, the
  code-review standard, the diff-intent gate) with each lane's behavior loop and the
  Validator↔orchestrator state-keeper protocol; generic by construction (target tokens
  removed). See `prompts/README.md` for provenance and the canonical-source map.

## Commands

```bash
make check-purity   # the boundary guarantee — run this first
make check-doctrine # structural doctrine parity (three roles / phases / eight rules)
make test           # pytest suite
make lint           # ruff
make typecheck      # mypy
make ship           # every gate, fail-closed (purity -> doctrine -> lint -> typecheck -> test)
make test-isolation # macOS kernel isolation proof
make test-tessera   # real signing + runtime-through-preview proof
```

## Invariants (enforced, not just asserted)

- **No target code in core** — `check_core_purity.py`: import scan + token denylist (read from `core_purity_denylist.json`, empty on the generic core) + reverse-dep assert. Fail-closed.
- **Segregation of duties** — implementer, verifier, approver are three distinct signing
  identities (not extra workflow roles); the ledger refuses any append with a two-identity
  overlap.
- **Tamper-evident ledger** — append-only, content-addressed (SHA-256), hash-chained; `verify_chain` re-derives every address and link.
- **Provenance of intent** — downstream requirements, constraints, tasks, and test assertions
  resolve to canonical items in one externally trusted artifact for each of the three phases;
  every reference binds the whole artifact digest so any new signed version invalidates old
  derived work; missing links are class-disposed while unresolved or mismatched references
  fail closed.
- **Checklist gates** — each required item is independently content-addressed against the
  candidate; unchecked/uncited remains a gap and negative or invalid evidence cannot pass.
- **State-triggered obligations** — every legal transition code-selects a closed, versioned
  lifecycle obligation set and re-derives the retained set/report on every load; unknown triggers,
  missing or stale evidence, replay, and direct-ledger bypass deny. Acceptance catalogs separately
  bind every ratified criterion to exact tests, assertions, expected effects, Validator execution
  configuration, immutable subject, and bounded review rounds.
- **Derived build IR** — Product, Architecture, and Testing/Monitoring artifacts remain the only
  intent authority. Recipe patterns are pre-qualified mechanisms; per-run recipe books carry
  instantiated configuration plus exact backreferences and oracle links, invalidate on any
  phase/target/catalog/input change, and cannot change a test expectation.
- **Retained bytes and bounded convergence** — each build attempt names a complete frozen
  generation tuple; reviewed Coder/Tester bytes are retained and re-derived; attempt ceilings
  live in both target ABI and plan and cannot rise after authoring starts.
- **Existing tests are immutable by default** — changing one requires a unique same-phase signed
  supersession plus one exact affirmative ruling over the run/generation/target, current phase
  versions, assertion or frozen family, and expected replacement statement. An enrolled human signs
  that same ruling (sole authority, 4.1b); the Validator receipt is retained as verified
  attribution; the ruling cannot invent or invert behavior.
- **Tool capability boundary** — every declared tool has exactly one signed tier and a
  phase-2/3 backreference; unknown/Verboten denies, Sign-off grants are scoped and expiring,
  and denial probes demonstrate enforcement. Platform credential/network removal remains an
  external integration obligation. The run policy is signed by one enrolled human and
  independently approved by a second; when the enrolled roster contains exactly one human
  (ratified single-operator disposition, 2026-08-27), the independent-approval seat is
  filled by an externally signed anchor binding the exact policy digest, and every decision
  over it carries a permanent disclosure — an anchor can never substitute when a second
  human exists, and self-approval remains invalid.
- **Externally anchored resume and brokered execution** — grounding and dispatch verify an
  independently supplied checkpoint before mutable state. Networked model lanes receive only a
  bounded path-free projection in a closed named-secret environment, must pass canary/resume
  qualification, and can request only typed host-owned effects under resource ceilings.
- **Surface criticality** — human-decided Critical/Standard/Cosmetic per surface; declared side
  effects inherit the highest class; unclassified is Critical; a Critical gap has no waiver and
  Critical evidence has zero flake/retry tolerance. The accountable-human seat on a Critical
  surface is filled from a named delegate roster; an undeclared roster is a gap, not a
  permission.
- **Graded independence** — the tier is *derived* from the recorded arrangement (shared context,
  open channel, model families, mechanical backing), never asserted; every agent that produced
  or judged the change records its model, version, and directive version; a claim above the
  derived tier blocks; an open Coder↔Tester channel is negative evidence for every class.
- **Spec-derived monitors** — every monitor resolves a backreference to the criterion or
  invariant it watches (unresolvable is an unauthorized assertion about production), a
  diff-derived monitor is a change detector and cannot satisfy the obligation, Critical surfaces
  carry human-authored monitors resolved against the enrolled roster, and density is recorded but
  never gated. An agent evaluating an alert may not delete, weaken, or silence the monitor that
  produced it — that is a human-ratified specification defect.
- **Correction controls** — red-now/green-now classified per test; a green guard that comes back
  red is a *suspected over-constraint* that stops for a human and is never reclassified as
  forcing; a defect is reproduced in a disposable environment before any repair is written.
- **Doctrine parity** — active docs retain exactly Validator/Coder/Tester, the three phases, all
  eight non-negotiables, and the structural criticality/determinism policy; historical records
  are not rewritten.
- **Data-only targets** — the `TargetManifest` loader refuses code references; a target may only
  *select* named seams the core already owns and must declare its operational build ABI.
- **Portability** — the full suite passes with no target pack present (only the synthetic empty fixture).

## Style

- Prefer stdlib; the only runtime third-party dependency is `jsonschema` (allowlisted). Adding
  a runtime dependency means updating the purity allowlist and justifying it.
- `manifest.py` stays stdlib-only and side-effect-free at import: no clock, no disk-reading
  identity resolution. Impurity lives behind seams.
- Small, reviewable modules. Docstrings state the *why* and the invariant a piece upholds.

## Durable knowledge (`.kin/`)

Capture durable decisions/constraints/key-files to kindex with `audience=team`; they export to
`.kin/knowledge.jsonl` and ship with the code. Coordination scratch is ephemeral — never
commit it. Search `.kin`/kindex before changing shared surfaces.

Every Validator-kicked run begins with a **research phase (Phase A0)**: search the graph
first, fetch the authoritative documentation for whatever the run touches, and capture
each source as a kindex node — provenance (URL/version, fetch date), run tag, domain
tags, and a one-line annotation of what it constrains for this run — linked to the
constraints and decisions it informs. Dispatches cite these nodes so the Coder and
Tester inherit ground truth instead of re-deriving it. Kindex is context, never
authority: research nodes inform artifacts; only signed artifacts authorize. See
`docs/HARNESS.md` (memory layer) and the `/validate` skill's Phase A0.
