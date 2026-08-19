# Changelog

All notable changes to Factory are recorded here. Versions follow Semantic Versioning while the
public API is still pre-1.0.

## Unreleased

### Added

- A closed effective-directive contract derived from externally checkpoint-bound directive and
  provisional sources, with a canonical run/generation/role scope grammar, same-scope
  qualifier-preserving supersession, future-time refusal, applicable live-candidate blocking,
  structured per-directive and per-qualifier readback, and one compiled role contract admitted
  through every lane state capsule and prompt.
- Exact-subject advisory dispositions. Clearing a blocking event now requires a typed consequence,
  bounded reason, and exact evidence bytes copied into a content-addressed run artifact before the
  event is receipted and the dispatch gate is durably released.
- Bounded, named-secret-redacted Validator-private diagnostics for failed model invocations, paired
  with a small downstream-safe failure capsule rather than raw runner or oracle output.

### Changed

- Orchestrator directive selection no longer accepts ambient paths, malformed chains, missing
  sources, or an empty-list fallback. Lane dispatch no longer accepts a bare
  `interpretation_confirmed: true` substring.
- Supported signed and provisional directive writers now share the runtime's closed scope grammar,
  validate the complete prospective chains before publishing, serialize mutations, and forbid a
  supersession from changing its parent's scope. Invalid input leaves both directive chains
  unchanged instead of permanently poisoning their append-only history.
- Dispatcher and orchestrator event writers, the disposition consumer, and dispatch admission now
  share one run-local attention lock. The blocker check and acquisition of a crash-released role
  mutex form one ordering point: earlier events deny this invocation; later events gate the next
  one. Legacy `lane_env.sh` admission uses the same serialized check.
- Blocking events use closed producer shapes. Their disposition receipts and containing directory
  are durable before the blocker is truncated, so malformed evidence and first-use crash windows
  cannot silently clear the gate.
- Exact blocking-event retries repair an interrupted event/receipt publication without duplicating
  the event, and partial JSONL appends roll back before the shared lock is released.
- Exact caller dispatch bytes and instruction artifacts recover idempotently after a crash between
  publications. Existing bytes are stable-read, re-derived, and reused; different bytes refuse.
- Runner prompt/3 executions emit `factory-runner-receipt/3`. The original receipt/2 schema remains
  immutable for historical validation and is explicitly non-executable after this cutover.
- Runner evidence publication fsyncs the containing directory, and a diagnostic-retention failure
  preserves the real post-model attempt count instead of being laundered into pre-model refusal.

### Explicit boundaries

- External checkpoint binding and hash-chain verification prove the exact directive bytes selected;
  they do not independently prove founder/hardware-signer identity or semantic model compliance.
- Kindex remains contextual memory and incident history, never instruction authority or an
  enforcement boundary. Behavioral adherence qualification and a closed advisory process
  environment remain planned work.

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
  and fsynced as exact regular inodes, then every containing directory is fsynced through the
  known durable run/root boundary before a ledger transition may cite them. Transition-obligation
  files stage privately and publish by no-replace hard link, so failed or concurrent writes cannot
  expose a partial canonical address or outrun evidence durability.
- A valid canonical Repair Brief left by a crash before ledger admission is authenticated against
  the causal Validator, exact payload, canonical address, and authorized retry before its missing
  event is admitted. Existing malformed, wrong-key, wrong-subject, or non-canonical files deny.

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
