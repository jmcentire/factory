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
The tree, not a listing here, is the authority.

## Commands

```bash
make install        # put this checkout to use (venv, launcher, /commands); ends in doctor
make doctor         # read-only: is the install complete? prints the fix for each gap
make check-purity   # the boundary guarantee — run this first
make test           # pytest suite
make lint           # ruff
make typecheck      # mypy
make ship           # every gate, fail-closed (purity -> wiring -> authority -> harness -> denial-probes -> acceptance -> glossary -> lint -> typecheck -> test)
make test-isolation # macOS kernel isolation proof
make test-tessera   # real signing + runtime-through-preview proof
```

## Working boundaries

Follow the current normal workflow in `docs/SOFTWARE-FACTORY.md`. The user owns scope and agent/model choices; the Validator owns task acceptance; the resident Orchestrator independently owns strategic oversight and process adherence. Do not recreate catalog, approved-model, signature, ratification, external-checkpoint or cadence-hold prerequisites. Historical code and memory describe previous designs, not authority to retain them.

Preserve actual platform permissions, credentials, independent Coder/Tester inputs, exact process targeting and safe reads of agent-owned Git repositories. Do not claim work is verified when checks have failed or have not run. Run meaningful checks for the changed behavior, and report any broader release-check failures accurately.

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

Every role should retrieve relevant Kindex context before broad repository scans and at material decisions. Current user instructions govern. Read local `.kin/knowledge.jsonl` when useful; write memory only where authorized. Optional memory writes, graph coverage and research receipts are not prerequisites for task execution.
