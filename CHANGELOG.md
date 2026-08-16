# Changelog

All notable changes to Factory are recorded here. Versions follow Semantic Versioning while the
public API is still pre-1.0.

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
