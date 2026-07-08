# factory_core

A founder-owned, **portable software-factory core**. It productizes a proven
software-delivery discipline — content-addressed evidence, human-approval gates with
segregation of duties, and a hard generic-core / target-as-data boundary — as a standalone,
separately-shippable Python package that imports **nothing target-specific**.

The defining constraint: `factory_core` is generic. Every per-target input — repo coordinates,
working-agreement docs, compliance rules, role bindings, IdP config — is **data loaded at
runtime through adapter seams**, never a code dependency. Point the factory at a new target by
swapping a data-only target pack; the core does not change. Correctness test: delete every
target pack and the core is still importable, testable, and green.

This is **Phase 0** — the core skeleton and the purity guard. It is a real, tested foundation,
not a running portal; the orchestration engine, authoring loops, RBAC/SSO, build/demo, and
affinity write-back are later phases (see the PRD).

## What's here (Phase 0)

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
