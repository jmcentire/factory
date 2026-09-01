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

The module map lives with the code: every `factory_core/` and `factory_runtime/`
module opens with a docstring stating the invariant it upholds, and
[`docs/GLOSSARY.md`](docs/GLOSSARY.md) anchors the load-bearing terms to exact
symbols at recorded digests (stale definitions fail `make check-glossary`).
The former per-module listing here was a restatement that drifted from the
tree; the tree is the authority (remediation 5.2).

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

The canonical rulebook is [`docs/SOFTWARE-FACTORY.md`](docs/SOFTWARE-FACTORY.md)
Part I; the enforcement lives in the ship chain (`make ship`: purity, doctrine,
wiring, authority, harness, denial-probes, acceptance, glossary, lint,
typecheck, tests) and every gate in `harness/gates.tsv` carries a collecting
denial probe with a named red_now. The former ~70-line restatement here was
prose that could drift from the checks; the checks are the authority
(remediation 5.2). Two working rules stay stated because they bind YOU rather
than the code: never weaken, delete, or silence a monitor or gate you are being
evaluated by, and never mark partial work done.

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
