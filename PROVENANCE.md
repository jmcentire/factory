# Provenance & Ownership

**`factory_core` is the separate, founder-owned intellectual property of Jeremy McEntire.**
It is not part of any consuming project, and it is shippable on its own.

## Separate IP, separately shippable

This package is a standalone, generic software-factory core in the same family as the
founder's other owned tools (Baton, Exemplar, Advocate, Sim). It was built to be used by a
first consuming project, but it is owned and licensed independently and can be extracted and
delivered to an unrelated customer by swapping a data-only target pack — no consumer code
travels with it.

- **License:** proprietary, all rights reserved (see `LICENSE`). Not open source.
- **Copyright:** Jeremy McEntire.
- **Distribution:** a versioned build of `factory_core` is published to the founder's private
  registry; consumers receive only a limited *consumer* grant to depend on that build.

## The dependency arrow points one way

```
   consumer project  ───depends on──▶  factory_core
        (a target)                     (this repo)

   factory_core  ──depends on──▶  (nothing target-specific, ever)
```

A consumer depends on `factory_core`. `factory_core` depends on **no** consumer: it imports
nothing target-specific, lists no target as a dependency, and names no target in its code.
Every per-target input (repo coordinates, working-agreement docs, compliance rules, role
bindings, IdP config) is **data** loaded at runtime through the adapter seams in
`factory_core/adapters.py` — never a code import.

## How the boundary is enforced (not just asserted)

`scripts/check_core_purity.py` is an executable, fail-closed guard wired into `make ship`:

1. **Import scan** — every import in `factory_core/` must resolve to the allowlisted set
   (stdlib + the package itself + a small, reviewed third-party allowlist). Any target import
   fails hard.
2. **Token denylist** — target-specific tokens in identifiers/strings must be pre-justified in
   `core_purity_baseline.json`; a new hit fails.
3. **Reverse-dependency assert** — `pyproject.toml` may list no target pack as a dependency.

**Correctness test (the portability proof):** delete every target pack and the core is still
importable, testable, and green. `tests/test_portability.py` asserts exactly this — the only
target in the tree is a synthetic empty fixture, and there is no `targets/` package at all.

## Open items carried from the design review

- **Manifest signing / key anchoring (OQ17).** The loader is fail-closed and rides a canonical
  content address, with a trust-root verification *seam* (`verify_signature`) and a
  `require_signature` posture. Anchoring the signing key out-of-repo with rotation/revocation
  is a deferred founder decision, wired here but not yet bundled.
