# Changelog

All notable changes to Factory are recorded here. Versions follow Semantic Versioning while the
public API is still pre-1.0.

## [0.3.0] - 2026-08-18

### Added

- Closed state-dependency profiles and exact-byte capsules for every model lane, plus a
  deterministic differential qualifier covering cold, resumed, compaction-boundary, stale,
  contradictory, poisoned, missing, and oversized state before model invocation.
- A bounded one-shot advisory-orchestrator path using Antigravity by default and Codex as the
  fallback. It receives a closed runtime-derived projection, has no authority or effect path,
  and returns only retained `untrusted-advisory` evidence.
- Typed terminal-failure classification and a bounded repair supervisor. A Validator-signed
  Repair Brief is derived from one failed immutable candidate/test subject, cites exact ratified
  intent items, authorizes one fresh attempt id, and reaches Coder but never Tester.
- A real Tessera integration that deliberately fails one candidate, records the exact signed
  repair event, runs an isolated fresh retry, and reaches a signed preview.

### Changed

- Runner prompts, state inputs, orchestrator prompts, client wire bytes, stdout, stderr,
  termination reason, and truncation state are retained and content-addressed. Timeout and signal
  paths drain already-written output within a fixed bound and report incomplete capture.
- `BLOCKED` retries now require an immediately preceding typed repair event. The subsequent
  `BUILDING` transition must consume its exact payload/envelope digests and unique authorized
  attempt id; a failed attempt cannot issue multiple briefs before retry. The signing identity
  must be the Validator recorded on that causal failed attempt, remain distinct from its Coder
  and Tester, and remain the Validator on the authorized retry.
- Repair outcomes must belong to the same run and current ledger head. Candidate and acceptance
  test digests are re-derived from the blocked ledger, and every intent backreference resolves
  against the retained ratified phase artifacts.
- Signed Tessera envelopes and idempotently reused obligation/authority evidence are stable-read
  and fsynced as exact regular inodes with their final directory entries before a durable ledger
  transition may cite them. Concurrent identical writers cannot outrun evidence durability.

### Explicit boundaries

- Repair Briefs are operational guidance, not behavioral authority. They cannot alter a phase
  artifact, expectation, test oracle, or existing-test disposition; new intent returns to the
  human phase loop.
- Repair actions remain Validator-authored prose. The runtime rejects structured Tester-oracle
  fields and keeps the Tester projection separate, but does not semantically prove that prose
  contains no oracle hint. Automatic target-diff extraction of changed existing tests also
  remains unwired; the caller-identified set is still checked through separate signed authority.
- The repair campaign wall limit is observed around callbacks. Each attempt runner must enforce
  its own hard per-call limit; the built-in macOS sandbox does so for its processes.
- Kindex remains contextual memory and incident history, never authority, isolation, or a secure
  projection boundary.
- The qualified build backend remains macOS-only and permits general outbound network. Advisory
  Agy/Codex process-environment confidentiality and provider-only egress remain unqualified.
- The release ends at a signed synthetic preview. It does not claim target deployment,
  production promotion, independently custodied WORM evidence, or a chat-first authoring UI.

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
