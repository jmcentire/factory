# Changelog

All notable changes to Factory are recorded here. Versions follow Semantic Versioning while the
public API is still pre-1.0.

## [0.6.0] - 2026-09-02

### Added

- `factory-run-guidance/1` lets the user select additional standards, process loops, and recipes
  per run through the exact external-resume configuration checkpoint. The selector and every
  source are retained content-addressed; every obligation receives an explicit application or N/A
  disposition, a subject-derived authority route, and an independent classification/application
  review.
- Exact role projections keep selected guidance narrow: Coder receives only applied obligations
  scoped to its role, while Tester receives behavioral obligations only and never whole source
  documents or constructional recipes. A main-module/entrypoint recipe therefore becomes an
  explicit Architecture/Testing conformance requirement without becoming invented Product intent.
- `phase_compiler.py` owns deterministic semantic-union → run-guidance → agreement generation and
  verification. Gate GUIDE refuses source substitution, obligation loss, misrouting, dispatch
  before application, and verdict before exact-candidate observations bound to the current
  selection, application row, obligation, and raw evidence; resolved findings use a separately
  typed record with the same candidate binding.
- Resident Orchestrator assessment/3 binds its guidance judgment to host-derived selection,
  application, evidence, and finding digests. Pre-dispatch requires `routing-verified`;
  pre-verdict requires `evidence-complete`; noncompliance forces its monotone block.
- Current-contract Gate L requires a canonical candidate/run/target/harness-bound green-endgame
  admission and rechecks it under the atomic close lock. Finishing a judging pass, or calling
  `promote.sh` directly, can no longer impersonate completion of the full endgame route.

### Changed

- Authored Product requirement identities stop before any generated-region marker, so adding or
  reordering deterministic compiler regions cannot stale the agreement participant inventory for
  unchanged authored text.
- Documentation distinguishes exact selection, correct routing, and substantive compliance.
  All applied obligations require exact-candidate evidence, while digest, route, and evidence-
  membership checks do not claim that arbitrary standards prose was exhaustively interpreted or
  that cited evidence is substantively adequate.
- Objective-budget reservation now precedes workspace/resource mutation atomically. A crash after
  the durable reservation and before the first resource record leaves no lane residue, and an
  exact retry reuses the same reservation rather than charging the objective twice.
- The agreement and supervision boundaries are explicit: asymmetric mismatch witnesses detect
  either side drifting but not a shared same-direction error; Orchestrator audit currency is a
  deny-only prerequisite while its judgment cannot grant; and Kindex informs planning context but
  cannot select run guidance or become compilation authority.

## [0.5.0] - 2026-09-02

### Added

- Real resident tmux sessions for the Validator, Orchestrator, Coder, and Tester. Codex author
  lanes use standalone repositories with lane-local Git metadata and receive a lane-scoped write
  profile that includes all of that local `.git`, so they can inspect and checkpoint their own
  work and audit their delta before committing. The lane owns its local branch and history;
  isolation is at the standalone-repository boundary, not a restricted Git ref namespace. They
  retain the actual resumable thread identity, and export only a bounded plain-file candidate for
  host review. Agent commits are useful checkpoints; the host still judges frozen bytes.
- A typed, bidirectional lane-question protocol. A Coder or Tester can block on an occurrence-bound
  specification question retained directly from the completed Codex assistant-message event;
  only the Validator can bind and deliver the answer back into the same conversation, while the
  Orchestrator may issue status probes but cannot invent semantics.
- Typed liveness observations for resident sessions. Silence is `unknown`, a pending question is
  `waiting-on-validator`, and the supervisors use tmux plus the resumable status channel instead
  of pretending elapsed-output heuristics distinguish reasoning from I/O hangs.
- A resident strategic Orchestrator contract over the complete activity cursor. It continuously
  evaluates the user's goal, direction, implications, side effects, adherence, hidden ambiguity,
  disproportionate requirements, simpler counterfactuals, task decomposition, Kindex-backed work
  state, causal discriminators, model-tier routing, and exact harness lifecycle. Its only effects
  remain monotone `block | no-op`; it can neither grant nor close.
- Evidence-conserving semantic compilation from every retained planning pass, lane trace, and
  adversarial review into an exact ruled Product Specification section, so a token mention cannot
  stand in for an enumerated and closed decision.
- `factory-agreement-contract/1`, which derives single-path versus cross-path requirements from an
  exact participant inventory and writes the complete register into the signed Testing Strategy.
  Gate AGR refuses missing membership, multi-participant downgrades, and endgame without distinct
  producer/consumer mismatch witnesses bound to the exact candidate, local suite, and relational
  oracle. This closes the failure where quote and hold were each tested but their shared decision
  was never tested for agreement.

### Changed

- The default resident Orchestrator launch uses an actual interactive Agy project rather than
  one-shot print mode. Dispatcher cursor wakes and independent cadence now let it monitor ordinary
  activity continuously, while typed tmux pokes keep coordination separate from evidence or
  isolation claims.
- Gate L remains the only close authority. A finished judging pass with failed tests, a blocking
  verdict, unanswered questions, stale agreement evidence, or unresolved findings leaves the run
  open for remediation.

## [0.4.1] - 2026-08-24

### Changed

- Every remaining ambient environment override across the guard boundaries is a hard denial.
  The `inject.sh` oracle-leak and shell-target refusals are unconditional;
  `INJECT_ALLOW_ORACLE_WORDS`, `ORACLE_OVERRIDE`, and `INJECT_ALLOW_SHELL` are retired, and the
  injection receipt no longer carries an `oracle_override` field. A genuine false positive is a
  filter defect fixed through a ratified change, never stepped around at delivery time.
- The denial-probe gate refuses ambient `GATES_TSV` / `DENIAL_PROBE_NODEIDS` whenever present;
  the test-fixture seam is explicit argv pinned by the Makefile invocation, so the registry that
  proves every gate blocks cannot be redirected from the environment.
- `HARNESS_MAX_GROUND_MIN` tightens only: a value above the 360-minute default is clamped and a
  non-positive-integer value refuses. Extending trust in a stale ground is a ratified policy
  change, not a knob.
- Red-now and mutation-style tests prove no ambient variable can flip a decision, gate outcome,
  or recorded evidence — verified against v0.4.0 with eight discriminating failures, including
  the denial-probe gate going GREEN off an ambient registry — and a structural sweep pins
  `harness/` and `scripts/` against reintroducing a reader of the retired names. Gates E and I
  register the new probes with extended red-now mutations.

### Explicit boundaries

- The recorded `network_mode=unrestricted-outbound` qualification claim is pinned to what the
  Seatbelt profile actually enforces: the three runner schemas `const` the claim, the profile's
  general-outbound grant is asserted alongside them, and the stronger `model-api-only` wording
  may appear nowhere in `factory_runtime`. When a provider egress boundary is enforced and
  independently tested, the pin and the schemas change together in one ratified change — never
  separately.

## [0.4.0] - 2026-08-19

### Added

- A mandatory immutable Validator adversarial-review gate before preview. A closed subject binds
  the complete bounded Git-object baseline, canonical candidate change set, exact ratified
  intent/architecture/operations, the exact Stage-E operator request, build input, build plan,
  pattern catalog, acceptance obligations, frozen implementation/tests and snapshots, resume
  checkpoint, configuration and exceptional test-change authority, target state, and frozen
  Validator execution identity. Validator source
  is constrained to one admitted Python file and streamed from the exact captured bytes rather
  than reopened through a mutable pathname; the Factory Python installation is the explicit host
  runtime TCB, and the retained execution snapshot is reverified after the process exits. The
  versioned standalone-source ABI binds `python -`, `<stdin>`, exhausted standard input, no
  source-directory import path, no interpreter flags, and no additional path-bound arguments into
  the ratified Validator configuration and environment identities. The closed report
  requires eight code-owned engineering lenses, exact cited bytes, content-addressed findings,
  an exact ordered disposition for every Product, Architecture, and Operational Maturity item,
  clean-claim challenges that select exact authority and produced evidence, and failure probes
  covering every acceptance-observation effect through a host-derived method. Purely formal
  non-vacuity rules use a version-stable ASCII control-prose alphabet, letter/token floors, and an
  exact minimum pairwise letter-stream edit distance to reject short, padded, repeated-token,
  exact-copy, and below-threshold near-copy narrative fields without claiming semantic insight or
  general copy detection. The host re-derives coverage and verdict; the `/1` protocol grants
  no self-refutation
  authority, so every emitted finding prevents a clean verdict. Review remains evidence-only and
  grants no merge, release, deploy, or promotion authority.
- PREVIEW admission and every ledger replay now require an explicit Tessera verifier bound to the
  active founder-anchored genesis and the recorded Validator identity. The state store derives a
  versioned verification receipt from the exact retained envelope; shaped signatures, wrong keys,
  caller-supplied receipts, and unanchored `status`/projection rebuilds fail closed. Evidence
  bundle `/3` authenticates one exact non-circular PREVIEW admission subject, including every
  retained review, execution, authority, candidate, and test artifact consumed by the transition.
- New lifecycles use `factory-run/5`, which versions the immutable review/execution tuple and
  cryptographic verification receipt instead of silently changing the released `/4` contract.
  Released v0.3 `/4` ledgers retain their exact historical transition-obligation and validation
  replay profile, remain read-only, and require current cryptographic envelope verification when
  replaying PREVIEW; they are never silently upgraded or credited with evidence they did not record.

- A closed effective-directive contract derived from externally checkpoint-bound directive and
  provisional sources, with a canonical run/generation/role scope grammar, same-scope
  qualifier-preserving supersession, future-time refusal, applicable live-candidate blocking,
  structured per-directive and per-qualifier readback, and one compiled role contract admitted
  through every lane state capsule and prompt.
- Exact-subject advisory dispositions. Clearing a blocking event now requires a typed consequence,
  bounded reason, and exact evidence bytes copied into a content-addressed run artifact before the
  event is receipted and the dispatch gate is durably released.
- Bounded, named-secret-redacted Validator-private diagnostics for failed model invocations, paired
  with a closed host-authored failure receipt and a small downstream-safe failure capsule rather
  than raw runner or oracle output. The receipt binds the exact private runner executable snapshot,
  any adapter child executable, canonical qualification document, runner, model, configuration,
  state, every prompt presented through the failed invocation, termination, and diagnostic evidence
  across the Python/CLI/shell lane boundary.

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
  the event, discard only unterminated blocker tails, and roll back partial JSONL appends from a
  boundary selected under the common file lock. Malformed unrelated legacy event rows grant no
  authority but cannot wedge an independently receipted blocker.
- Inherited dispatch descriptors no longer confer admission authority: the recursive lane process
  repeats blocker admission under the shared attention lock. Consumption requires exactly one
  closed durable producer receipt for every pending event before the gate can clear.
- Exact caller dispatch bytes and instruction artifacts recover idempotently after a crash between
  publications. Existing bytes are stable-read, re-derived, and reused; different bytes refuse.
- A lane crash after a complete runner-failure receipt no longer wedges exact retry. Dispatch
  adopts only the fully re-derived orphan failure state, retains all failure evidence without a
  second model call, and rejects partial, mutated, unbudgeted, or wrongly scoped workspaces.
- Runner prompt/3 executions emit `factory-runner-receipt/3`. The original receipt/2 schema remains
  immutable for historical validation and is explicitly non-executable after this cutover.
- Runner evidence publication fsyncs the containing directory, and a diagnostic-retention failure
  preserves the real post-model attempt count instead of being laundered into pre-model refusal.
- Runner supervision, diagnostics, schemas, and failure classification share one closed termination
  vocabulary. Model-controlled stdout/stderr cannot assign failure ownership; the lane independently
  re-derives and retains the exact failure receipt, private diagnostic, state capsule, canonical
  qualification, complete presented-prompt sequence, and primary/child executable snapshots,
  records their digests, retains the workspace, and executes no broker operation.
- Counted post-launch runner failures, including missing output artifacts and supervisor errors,
  now retain typed diagnostics and receipts. Existing failure evidence is compared and fsynced
  through one stable descriptor, and state capsules require exact canonical bytes rather than only
  parsed-object equality.
- Release CI now builds and inspects both wheel and sdist, installs and smokes each artifact outside
  the source checkout, requires every runtime schema and CLI entry point, preserves the historical
  runner-receipt/2 bytes, and excludes the incomplete repository test tree from published sdists.
- Generation blobs and Coder/Tester review trees now require a caller-declared durable run boundary.
  New and idempotently reused snapshots fsync exact contents, internal directory entries, the
  published content address, and every ancestor through that boundary before a ledger transition
  may cite them. Content-address publication is serialized per address and exact `0700` crash
  orphans are descriptor-verified, sealed, and resynchronized on retry; malformed or substituted
  writable destinations remain preserved for forensics and fail closed.
- Resume, status, execution-request verification, endgame, and promotion shell paths reopen
  post-PREVIEW runs with the same externally pinned genesis, root key, and Tessera verifier rather
  than falling back to an unauthenticated structural replay. Post-PREVIEW resource mutations are
  covered by the real-Tessera integration proof.
- Every workflow operation after Stage R that consumes signed authority now reopens the run's
  verified genesis entry and requires the configured authority policy to match it before receipt
  verification or evidence retention. Reusing principal labels under a different genesis cannot
  ratify a phase or authorize later workflow evidence.

### Explicit boundaries

- External checkpoint binding and hash-chain verification prove the exact directive bytes selected;
  they do not independently prove founder/hardware-signer identity or semantic model compliance.
- The runtime re-derives the contract on every dispatch/resume and rejects structural drift, but it
  does not pretend to infer whether independently active natural-language directives conflict.
  Explicit supersession and provisional ratification/refusal remain human authority actions.
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
