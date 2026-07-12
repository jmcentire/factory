# factory_core

A founder-owned, **portable software-factory core**. It productizes a proven
software-delivery discipline — content-addressed evidence, human-approval gates with
segregation of duties, and a hard generic-core / target-as-data boundary — as a standalone,
separately-shippable Python package that imports **nothing target-specific**.

## The canonical doctrine

The foundation of this repository is a written doctrine, and the code implements pieces of
it. The doctrine is authoritative; `factory_core` is one (partial) implementation.

- **[`docs/SOFTWARE-FACTORY.md`](docs/SOFTWARE-FACTORY.md)** — the unified specification:
  one foundation, two flows (capability + correction), the seven non-negotiables,
  oracle-adequacy-not-blast-radius gating, the shared-vs-independent (spec-shared,
  oracle-independent) rule, the two controls (negative/positive) that bound a correction
  spec, the environment ladder, the content-addressed evidence plane, and the factory-is-
  itself-a-regulated-system control plane.
- **[`docs/AGENT-DIRECTIVES.md`](docs/AGENT-DIRECTIVES.md)** — the executable companion: the
  ten role directives across the two flows (capability: PM Spec, Eng Spec, Validator, Test,
  Code; correction: Triage/Root-Cause, Spec, Hidden-Test, Repair, Judge).
- **[`docs/VALIDATION-DIRECTIVE.md`](docs/VALIDATION-DIRECTIVE.md)** — the Validator/Judge
  process-completeness directive: no local-only work, durable `.kin`, current docs/specs/
  contracts, migration atomicity, PR/commit/merge/deploy evidence, live observability,
  waivers, rollback authority, and a reproducible evidence bundle for every pass.

The practices under `docs/practices/` and the sync log in `docs/PROVENANCE-SYNC.md` are
disciplines and records **under** this doctrine.

## Doctrine → code mapping

Each `factory_core` module implements a specific concept from the doctrine. The doctrine
demands this honesty (see the doctrine's "Status of this document": *a control specified is
not a control running*), so the table marks what is **implemented** vs **doctrine-only**.

| `factory_core` module / file | Doctrine concept it implements | Status |
|---|---|---|
| `manifest.py` | The evidence plane — the content-addressed, hash-chained, tamper-evident change-evidence manifest; write-time segregation of duties (implementer ≠ verifier ≠ approver). | **Implemented** |
| `invariant_kernel.py` | The capability-delta IR + the composition gate — can individually-safe deltas compose into a forbidden configuration? (the platform-invariant side of the gate). | **Implemented** |
| `contract.py` + `completeness.py` | Oracle adequacy + the FE↔BE contract discipline + launch-readiness — forward/reverse contract diff (every caller reaches a real provider; every provider is called or excused) and the falsifiable completeness lattice. | **Implemented** |
| `adapters.py` + `target.py` | The target-as-data boundary + the environment-ladder dependency seams — the five `Protocol` seams for all target contact, resolved by name from a signed data-only `TargetManifest` (never a code import). | **Implemented** |
| `roles.py` | The division of labor / role model — capabilities as the atomic unit, roles as named bundles, grants as per-target data (the RBAC schema, not the authority catalog). | **Implemented** (schema; live RBAC/SSO is doctrine-only) |
| `scripts/check_core_purity.py` + `core_purity_denylist.json` | "The factory is itself a regulated system" — the core is governed; target tokens are data, and an executable fail-closed guard proves the core imports nothing target-specific. | **Implemented** |

**Honest split (implemented vs doctrine-only).** Phase 0 (the skeleton + purity guard) and
the two extractions (the invariant kernel; the contract/completeness modules) are **real,
tested code**. The **orchestration engine, the ten live agent lanes, RBAC/SSO enforcement,
and the build/demo pipeline are doctrine/design, not running** — they are specified in the
docs above and are not wired here. A control specified is not a control running.

The defining constraint: `factory_core` is generic. Every per-target input — repo coordinates,
working-agreement docs, compliance rules, role bindings, IdP config — is **data loaded at
runtime through adapter seams**, never a code dependency. Point the factory at a new target by
swapping a data-only target pack; the core does not change. Correctness test: delete every
target pack and the core is still importable, testable, and green.

This repository has completed **Phase 0 plus the first two generic extractions**:
the core skeleton and purity guard, the invariant-kernel composition gate, and the
adapter-driven contract/completeness logic. It is a real, tested foundation, not a
running portal; the orchestration engine, authoring loops, RBAC/SSO, build/demo, and
affinity write-back are later phases (see the PRD).

## What's here

| Module | What it is |
|---|---|
| `factory_core/manifest.py` | The content-addressed (SHA-256), append-only, **hash-chained**, tamper-evident evidence ledger. Every append is **fail-closed on segregation of duties** (implementer, verifier, approver must be three distinct identities). Stdlib-only. |
| `factory_core/target.py` | The `TargetManifest` loader: parses a content-addressed TOML manifest (repo coords + ref + subpath, adapter selections, role/capability bindings, compliance-rule path, effort params, demo-env descriptor), validates it against a JSON Schema, and **refuses any code reference** — data in, never a code import. Fail-closed before adapter resolution. |
| `factory_core/adapters.py` | The five `typing.Protocol` seams for all target contact: `RepoAdapter`, `KnowledgeAdapter`, `ComplianceAdapter`, `IdpAdapter`, `ArtifactSink`. Interfaces only. |
| `factory_core/roles.py` | The role/capability model **schema**: a capability is the atomic unit, a role is a named bundle, and grants are **per-target data** — not core classes. |
| `factory_core/schemas/capability_delta.schema.json` | The neutral, target-agnostic **capability-delta IR** a spec declares for a change (nodes with abstract roles, flows with an abstract data-class, invariants by opaque id + degree). The analyzer composes it against the signed kernel + composition ledger. Names no target; all concrete vocabulary is per-target kernel data. |
| `scripts/check_core_purity.py` | The executable, fail-closed **anti-coupling guard** (import scan + token denylist + reverse-dependency assert), baseline-backed by `core_purity_baseline.json`. The token set is **data**, read from `core_purity_denylist.json` (empty on the generic core — nothing target-specific to catch; a consuming target fills in its own tokens as private config). |

## Quickstart

```bash
make dev          # install the package + dev tooling (editable)
make check-purity # prove the core imports nothing target-specific
make test         # run the pytest suite
make ship         # every gate, fail-closed: purity -> lint -> typecheck -> test
```

Nothing needs a target to run. The suite exercises the core end-to-end against a **synthetic
empty target** fixture (`tests/fixtures/synthetic_target/`).

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
