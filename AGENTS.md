# factory_core — working agreement (agents / Codex)

This mirrors `CLAUDE.md` for non-Claude agents. Read both as one working agreement.

Founder-owned, portable software-factory core. **Generic by construction — imports nothing
target-specific.** Proprietary IP of Jeremy McEntire (see `LICENSE`/`PROVENANCE.md`), shippable
independently of any consuming project.

## The rule you cannot break

`factory_core` is the generic core; every target is **data**, loaded at runtime through the
adapter seams in `factory_core/adapters.py`. Never import a target, never name a target token
in core code, never add a target as a dependency. `scripts/check_core_purity.py` enforces this,
fail-closed; `make ship` runs it first.

## Where things live

See `CLAUDE.md` "Layout". In short:
`factory_core/{manifest,provenance,target,adapters,roles}.py`, the guards in `scripts/`, tests
in `tests/`, and the synthetic empty target under
`tests/fixtures/synthetic_target/`.

## Commands

```bash
make check-purity   # boundary guarantee — run first
make check-doctrine # canonical doctrine structure and active-surface parity
make ship           # purity -> doctrine -> lint -> typecheck -> test, fail-closed
```

## Invariants

No target code in core · signing-identity segregation of duties
(implementer ≠ verifier ≠ approver; these are not extra workflow roles) · tamper-evident
hash-chained ledger · fail-closed provenance of intent against the three trusted phase
artifacts · data-only targets (no code references) · portability (green with no target pack
present). All are enforced by the guards and the test suite, not merely documented.

## Durable knowledge

Commit durable, team-audience kindex notes to `.kin/knowledge.jsonl`; keep coordination
ephemeral. Search before changing shared surfaces.
