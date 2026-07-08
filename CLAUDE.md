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
- `factory_core/target.py` — `TargetManifest` loader (TOML + JSON Schema; refuses code references).
- `factory_core/adapters.py` — the five `typing.Protocol` seams (interfaces only).
- `factory_core/roles.py` — capability/role model schema (grants are per-target data).
- `scripts/check_core_purity.py` — the fail-closed anti-coupling guard.
- `core_purity_denylist.json` — the token-denylist **data file** the guard reads (empty on the
  generic core; a target fills in its own tokens as private config, never shipped in the core).
- `core_purity_baseline.json` — justified token exceptions (empty on a clean core).
- `tests/` — pytest suite, incl. the portability proof and the purity guard tests.
- `tests/fixtures/synthetic_target/` — the synthetic empty target for the portability proof.

## Commands

```bash
make check-purity   # the boundary guarantee — run this first
make test           # pytest suite
make lint           # ruff
make typecheck      # mypy
make ship           # every gate, fail-closed (purity -> lint -> typecheck -> test)
```

## Invariants (enforced, not just asserted)

- **No target code in core** — `check_core_purity.py`: import scan + token denylist (read from `core_purity_denylist.json`, empty on the generic core) + reverse-dep assert. Fail-closed.
- **Segregation of duties** — implementer, verifier, approver are three distinct identities; the ledger refuses any append with a two-role overlap.
- **Tamper-evident ledger** — append-only, content-addressed (SHA-256), hash-chained; `verify_chain` re-derives every address and link.
- **Data-only targets** — the `TargetManifest` loader refuses code references; a target may only *select* named seams the core already owns.
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
