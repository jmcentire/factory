# Changelog

All notable changes to Factory are recorded here. Versions follow Semantic Versioning while the
public API is still pre-1.0.

## [0.2.0] - 2026-08-16

### Added

- `factory-run/4` code-selected transition obligation sets and reports, re-derived on every load;
  unknown triggers, missing evidence, replay, tamper, and direct-ledger bypass deny.
- Human+Validator-ratified acceptance-obligation catalogs binding exact tests, assertions,
  evidence membership, expected effects, Validator argv/config, phase versions, immutable review
  subjects, and bounded review rounds. Preview requires the runtime-derived report.
- Externally anchored resume checkpoints over root/genesis, retained Stage-R/Stage-E Tessera
  envelopes, lifecycle/resource prefixes, target/generation/configuration, predecessor lineage,
  and retention policy. Grounding and dispatch verify the checkpoint before mutable state.
- A macOS Seatbelt hardened model runner with path-free projections, named-secret-only closed
  environments, dedicated homes, exact model/runner/config receipts, two canaries, same-session
  resume proof, process-tree supervision, and wall/idle/output/token/cost ceilings.
- Signed typed broker capabilities and checkpoint-bound host registries. Models cannot supply
  paths, commands, argv, scripts, or working directories; effects require operation-specific
  rehash or deterministic no-network rerun evidence and are idempotently retained.
- Exact test-change authorizations bound to run/generation/target/current phase versions, the
  phase-authorized old-to-new behavior replacement, and sorted assertion/family membership, with
  separate enrolled human and Validator signatures retained and spent by the build transition.

### Changed

- `dispatch_lane.sh` no longer launches model lanes in tmux. It reserves objective cost ceilings,
  invokes only qualified Codex or Ollama-to-Codex adapters, proves failed canaries execute no
  broker operation, and records runner workspaces and immutable handoffs as run-owned resources.
- Existing tests remain immutable unless one current exact affirmative ruling names the assertion
  or frozen family and precise expected behavior change, and an enrolled human plus a distinct
  Validator independently sign that same content address.

### Explicit boundaries

- The qualified runner is macOS-only; Linux has no qualified backend.
- Cost receipts are observed after provider calls unless the provider offers a hard limit.
- Factory verifies externally supplied resume anchors but does not provide their independent WORM
  custody, timestamping, or erasure enforcement.
- The human/Validator tmux surface is operator-owned coordination, not a qualified model lane.
- Chat-first phase negotiation, target deployment, and production promotion remain outside this
  release; the tested vertical slice ends at signed preview.

## [0.1.0] - 2026-08-15

Initial packaged release of the generic Factory core and executable runtime.

### Included

- A target-as-data core with purity, doctrine, provenance, criticality, checklist, recipe-plan,
  test-disposition, tool-policy, and promotion controls.
- Real Tessera-signed authority and phase ratification through a retained, signed preview.
- `factory-run/3`, separating bounded target-resolution authority (Stage R) from execution
  authority (Stage E) over an exact resolved commit, subpath, and verbatim task.
- Run-owned target checkouts, exact target-state re-derivation, lifecycle compare-and-swap, and a
  hash-chained resource ledger whose terminal seal is bound into promotion.
- Immutable generation and review subjects, bounded attempts, deterministic harness consumers,
  and exact close-verdict receipts.
- macOS Seatbelt isolation proofs, real-Tessera integration tests, and fail-closed CI gates.

### Explicit boundaries

- The interactive model launcher is not yet qualified as a live isolated, metered execution
  boundary; that is PR2.
- Local ledgers are tamper-evident, not independently custodied WORM storage. Resume-time external
  anchor verification remains future work.
- Runtime transitions do not yet select and enforce versioned state-triggered obligation sets.
- The release proves the generic synthetic path. It does not claim a production target deployment,
  managed identity/HSM custody, or the full doctrine-described Factory.
