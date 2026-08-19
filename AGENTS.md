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
`factory_core/{manifest,evidence,checklist,criticality,promotion,provenance,build_plan,test_disposition,tool_policy,target,adapters,roles}.py`,
the executable boundary under
`factory_runtime/{state,schema,durability,tessera,authority,workflow,generation,snapshot,isolation,lanes,transition_obligations,acceptance_obligations,test_change_authority,resume,runner,runner_isolation,projection_bundle,broker,evidence_plane,orchestrator,cli}.py`,
the guards in `scripts/`, tests in `tests/`, and the synthetic empty target under
`tests/fixtures/synthetic_target/`.

## Commands

```bash
make check-purity   # boundary guarantee — run first
make check-doctrine # canonical doctrine structure and active-surface parity
make ship           # purity -> doctrine -> lint -> typecheck -> test, fail-closed
make test-isolation # macOS Seatbelt denial and Coder/Tester separation proof
make test-tessera   # real Tessera signatures and signed runtime-through-preview proof
```

## Invariants

No target code in core · signing-identity segregation of duties
(implementer ≠ verifier ≠ approver; these are not extra workflow roles) · tamper-evident
hash-chained ledger · fail-closed provenance of intent against the three trusted phase
artifacts with artifact-version invalidation · evidence-backed checklist items · signed scoped
tool tiers (unknown and Verboten deny; Sign-off authority expires) · authorization-based
existing-test disposition · human-decided surface criticality (unclassified is Critical; no
waiver or flake tolerance on Critical) · authority-bound recipe/build IR · exact retained
generation/review bytes · bounded build attempts · human+Validator-authorized exact test changes ·
data-only targets with an operational build ABI (no code references) · portability
(green with no target pack present) · code-selected transition/acceptance obligations ·
externally anchored resume · closed path-free model runners and typed brokered effects. All are enforced by the guards and the test suite, not
merely documented.

## Durable knowledge

Commit durable, team-audience kindex notes to `.kin/knowledge.jsonl`; keep coordination
ephemeral. Search before changing shared surfaces.
