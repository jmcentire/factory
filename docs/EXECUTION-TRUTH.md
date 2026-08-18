# Execution truth: target resolution and exact execution authority

Status: active runtime contract.

This document defines the boundary between permission to discover target bytes and permission
to execute work against them. It exists because a human cannot authorize an exact commit while
the only target identity is a mutable ref, and because the live harness previously selected an
ambient checkout independently of the runtime authority chain.

## Product contract

A Factory run has two distinct human decisions before intake:

1. **Stage R — target resolution.** The human permits narrowly bounded, read-only contact with
   one credential-free repository URL and requested ref. Stage R never permits lane execution,
   source inspection, projection construction, or arbitrary repository operations.
2. **Stage E — execution.** After resolution, the human receives the canonical URL, exact commit,
   subpath, checkout identity, and evidence digests. The human authorizes the requested outcome
   against that exact target state. Only Stage E creates ordinary Factory intake.

PR1 never collapses the two decisions. Even an immutable requested commit produces a canonical
target state and requires a distinct Stage-E execution request and receipt. A future versioned
subject may add an explicit dual-grant field and dedicated denial tests; `factory-run/3` does not
infer that grant from an immutable-looking ref.

The user-visible guarantees are:

- the lane works only in a fresh, run-owned checkout at the authorized commit;
- the operator checkout may supply objects after mechanical URL/ref/object proof, but is never
  the lane checkout and is never mutated;
- every consumer reads the same retained target-state receipt;
- no `origin/main`, ambient `HEAD`, caller-supplied SHA, or current-working-directory fallback can
  select different bytes;
- cleanup and close inspect only resources the run created and recorded;
- pre-existing worktrees, branches, stashes, and untracked files are external/non-owned state and
  are never deleted or treated as run-created residue.

## State machine

New runs use `factory-run/3` and begin here:

```text
target-resolution-authorized
    -> target-resolved
    -> intake
    -> product-specification-ratified
    -> architecture-ratified
    -> operational-maturity-ratified
    -> ...existing lifecycle...
```

`factory-run/1` and `factory-run/2` ledgers remain verifiable and rebuildable, but cannot advance.
They do not silently acquire Stage-R or Stage-E authority. An in-flight legacy run must be
retained as audit evidence and explicitly replaced by a newly authorized v3 run; there is no
authority-preserving automatic migration because the missing Stage-R/Stage-E decisions cannot be
manufactured after the fact.

### Stage-R subject

`factory-target-resolution-request/1` is closed-schema data containing:

- run, repository, request, and generation identities;
- retained target-manifest content and source digests;
- normalized credential-free repository URL, requested ref, and canonical relative subpath;
- the exact allowed contact operations;
- a nonce and expiry;
- `lane_execution: false`.

An enrolled human signs an `authorize-target-resolution` authority receipt over the canonical
request digest. The receipt nonce and expiry must agree with the signed request. The runtime
persists the verified request, receipt envelope, genesis digest, and retained target manifest
before any contact operation.

### Canonical target state

Resolution produces immutable `factory-target-state/1` bytes containing:

- requested and canonical credential-free URL;
- requested ref, observed ref object, peeled object, and exact commit;
- target-manifest identity and digests;
- canonical control root, run-owned object store, source root, subpath, and workdir;
- observation method and freshness status;
- contact and resource-ledger heads;
- run-owned checkout identity and creation time.

The runtime records the target-state digest in its hash-chained run ledger. Any later difference
in URL, ref, commit, subpath, workdir, checkout identity, or retained target-state bytes is a hard
denial, not a reason to re-resolve implicitly.

### Stage-E subject

`factory-execution-request/1` carries the original verbatim request and requested outcome plus the
manifest digest, target-state digest, exact resolved commit, generation, and affected surfaces.
An enrolled human signs the canonical request digest with `authorize-change`. The runtime verifies
the receipt against the current Stage-R run and retained target state, consumes its nonce, stores
the request and envelope immutably, establishes the verbatim source digest, and moves to `intake`.

No lane, projection, source inspection, specification phase, or harness session may start before
that transition.

The retained `evidence/intake/execution-request.json` is not trusted because of its pathname. On
every harness context load the runtime finds the unique verified `intake` ledger entry, re-derives
the complete closed-schema request digest against that entry, rechecks its target-state/commit and
verbatim-source bindings, and—after ignition—requires `TASK.md` to contain those exact verbatim
bytes. Substituting either the request or task blocks before a lane resource is planned.

## Control root and target roots

The run directory is the canonical `control_root`. It holds ledgers, authority evidence, retained
manifests and requests, harness metadata, dispatches, and reports. It is never the target source.

The target-state receipt names:

- `object_store`: run-owned Git objects;
- `source_root`: a fresh detached run-owned checkout of the exact commit;
- `subpath`: a canonical relative path, possibly empty;
- `workdir`: `source_root / subpath`, proven contained in `source_root`.

`run.json` is solely the checked projection of the runtime ledger. Harness-only cadence and close
metadata live in `harness.json`; neither file may choose a repository, ref, SHA, or workdir.

## Resource ledger

Each run owns an append-only, hash-chained `resources.jsonl`. Every record includes the run and
generation, resource type and exact identifier, creator action/evidence, ownership class,
retained baseline, cleanup/disposition rule, status, and timestamp.

Contact intent is recorded before invocation. Object stores and worktrees are recorded when
created. A run-created resource missing from the ledger, a ledgered resource with no terminal
cleanup/disposition, or a tampered resource chain blocks terminal close. External/non-owned state
may be compared with a retained baseline but is never cleaned by the Factory.

Terminal close installs a content-addressed `resources.seal.json` over one verified ledger head
under the resource-append guard and a run-transition guard shared with lifecycle transitions.
Once installed, the resource API refuses every later event. Both Gate L and
`RunStore.transition(..., PROMOTED)` install or
re-verify that seal; the authoritative `PROMOTED` ledger entry binds both the sealed resource head
and seal digest, and `_derive` re-verifies them on every load. The lifecycle ledger and resource
ledger are therefore conjunctive authorities: neither can overrule an unresolved resource in the
other, and a direct run-ledger append cannot manufacture a valid promotion.

Endgame does not turn every active resource into "retained evidence." It may mechanically retain
only the exact target/object stores, the judged checkout, and the accepted candidate after proving
their paths still exist. It may mechanically dispose a tmux resource only after proving that exact
target is absent. Other active resources remain blocking until an operator makes and evidences a
specific disposition. Retention is a factual claim about residue, not a generic escape from close.

## CLI and harness flow

The runtime commands are intentionally separate human stops:

```text
factory authorize-target-resolution ...
factory resolve-target ...
factory authorize-change ...
harness/factory.sh <run> <verbatim-task> ...
```

`resolve-target` may use a local operator repository only as a mechanically verified, read-only
object source. It never reads that repository's working tree, index, `HEAD`, or dirt. It observes
the requested ref and normalized remote, copies objects without hardlinks or alternates, verifies
the commit and tree in the run-owned store, then re-reads the source remote and requested ref. A
change across that proof window denies; unrelated branches and working-tree state are irrelevant.
The resulting object store and checkout remain run-owned. `factory.sh` refuses a
run that is not at or beyond intake, refuses task bytes that differ from the retained Stage-E
request, and launches from the target-state workdir. `dispatch_lane.sh` has no SHA override.

"Read-only" describes repository contact and operator-source access. Stage R deliberately writes
the retained authority artifacts, resource intent, run-owned object store, and run-owned checkout;
it grants no write to the target repository or operator checkout.

The resource ledger uses event records, not mutable rows. A `planned` contact or resource event is
appended, flushed, fsynced, and closed before the Git child process or filesystem creation call.
The result appends `active`, `retained`, or `failed` for the same resource identity. A crash after
`planned` remains unresolved and blocks close until an explicit actor appends `abandoned` or
`disposed` with reason and evidence after inspecting run-owned residue.

Ledger durability is qualified only on a local POSIX filesystem that enforces `O_EXCL`,
`O_NOFOLLOW`, regular-file semantics, and file/directory `fsync`. Network or object-backed mounts
are not qualified by PR1. Filesystems or mount modes that do not make directory entries durable
under `fsync` are likewise unqualified; PR1 does not claim stronger power-loss semantics than the
host filesystem actually provides. A surviving `.lock` file is treated as evidence of an interrupted append,
not timed away: recovery requires proving no writer remains, preserving the lock bytes, verifying
the existing chain, and an explicit operator decision before removing it. Automated stale-lock
recovery is intentionally absent until that recovery ceremony is itself executable and receipted.
The same rule applies to a surviving `resources.guard` or `run-transition.guard`: it is
interruption evidence, not a lock whose age or recorded PID grants permission to delete it. A
crash after the resource seal but before promotion is safe to retry because the immutable seal is
idempotently re-verified. Every lifecycle append also supplies the exact lifecycle head from which
its transition was derived; a concurrent winner makes the stale append deny rather than extending
the wrong state.

Gate L records `closed_at` and the exact promotion-verdict filename and content digest inside the
same atomically replaced, file-and-directory-fsynced `harness.json` that changes status to
`closed`. The close status therefore cannot become visible without its audit binding.

The baseline source checkout is immutable. Before and after each projection, and again at close,
the harness re-derives the retained target-state digest; verifies the object-store commit/tree and
detached baseline commit; and rejects any tracked, staged, or untracked baseline change as
`target-state-diverged`. The event marks the resource compromised, admits no lane output produced
after divergence, and blocks close pending explicit disposition. The PR2 supervisor terminates
the qualified runner process group on wall, idle, output, or process-limit breach; it does not
claim instantaneous revocation against a compromised host kernel.

## PR1 and PR2 boundaries

The live model lane is now an executable PR2 boundary. It receives a bounded path-free data
projection in a fresh workspace, a closed environment with only manifest-named secrets, dedicated
configuration homes, and no source/control/object-store path. macOS Seatbelt permits the exact
Codex executable (directly or through `ollama launch codex`), network access, and private runner
workspace while denying arbitrary shell execution and target/control access. Two canaries must
pass and the second must resume the first session before the task is sent. Process-tree, wall,
idle, output, token, attempt, and monetary ceilings fail closed; monetary evidence is labeled
`observed-post-call` unless the provider supplies a hard limit.

Before backend qualification or any model attempt, the CLI opens each context dependency once,
confirms two bounded reads through that descriptor agree, and rejects symlinks or an
identity/size/time change during
the read, verifies its external resume/configuration binding, and derives a closed
`factory-state-dependency-capsule/1`. The capsule covers the exact target state, ledger head,
phase references and the exact three canonical phase artifacts, frozen task, lane projection,
role-specific Kindex primer, runner manifest, output schema, broker registry, resume evidence,
configuration set, and structural qualification observations and report. The runner receives
those same in-memory bytes; it does not reopen caller paths. Missing,
unknown, duplicate, oversized, stale-profile, trust-escalated, or byte-substituted dependencies
refuse before a model call and produce a bounded refusal receipt with zero model attempts and zero
broker effects. The capsule is provenance and integrity evidence, not an authority source.

State qualification compares deterministic dispositions, never model prose. Its code-owned
executor invokes no model and produces no broker effect: cold, exact-resume, and
compaction-boundary fixtures must reach one equivalent downstream probe, while stale,
contradictory, poisoned, missing, and oversized-input fixtures must stop at admission. Both the
observations and materialized report are bound to the exact runner configuration and admitted
through the external resume configuration set. This qualifies only the state-admission boundary;
live runner isolation, product behavior, oracle adequacy, and the next stochastic trajectory
remain separate gates. Pre-capsule sessions restart as new attempts/generations rather than
receiving a compatibility bypass.

The model cannot name a path, command, argv, script, or working directory. It may emit only a
closed typed broker request carrying an opaque signed capability digest and kind-specific data.
The host resolves that handle through an externally checkpoint-bound registry, requires file
roots to equal active run-owned resource-ledger identifiers, or uses fixed host-owned verifier
argv, and verifies effects by content rehash, durable reopen/rehash, or two identical no-network
runs. A failed canary reaches no broker operation. The interactive human/Validator tmux window is
operator-owned coordination and is not represented as a qualified model lane. When an operator
explicitly selects Claude for that window, the process is operator-equivalent and unsandboxed;
it supplies no lane-isolation evidence. Codex is the default and Ollama-launched Codex is the
supported alternate.

Seatbelt currently permits outbound network generally so the model client can reach its provider.
It does **not** yet enforce provider-only destination egress. Manifests and receipts therefore
must say `network_mode=unrestricted-outbound`; the schema refuses `model-api-only` until a provider
allowlist/proxy (including DNS and resolved-address controls) is enforced and independently tested.
File/process containment remains enforced; provider-only egress does not.

The runtime proves that a valid enrolled-human signature covers the canonical Stage-E request; it
does not prove that a particular UI made the human meaningfully inspect it. A production signing
surface must present the canonical URL, requested ref, exact commit, subpath, target-state digest,
verbatim task, and requested outcome together without a hidden or bulk-approval path. That UX is
not claimed by this CLI/runtime PR.

The durable local ledger remains tamper-evident, not a WORM service or a defense against arbitrary
host-owner compromise. Grounding and dispatch now require a separately supplied content-addressed
checkpoint and independently obtained expected digest before opening mutable run state. The
checkpoint itself is not signed by Factory; verification rechecks the pinned root/genesis,
retained Stage-R/Stage-E Tessera envelopes, lifecycle/resource prefixes, target/source/generation,
configuration bytes, predecessor lineage, and retention policy; rollback, fork, whole-root
substitution, and configuration drift deny. Factory does not provide independent custody,
timestamping, or erasure enforcement for that checkpoint. Deployments requiring those properties
must anchor it in a separately administered append-only service or equivalent WORM medium.

## Test and mutation plan

The implementation must prove at least:

- wrong root/genesis, signer, capability, action, subject, run, generation, nonce, expiry, replay,
  manifest digest, target-state digest, commit, URL, ref, or subpath denies;
- invalid Stage-R authority causes zero repository contact and creates no object store/worktree;
- Stage R alone cannot launch, inspect source, create a projection, or authorize intake;
- contact intent exists durably before the resolver process is invoked;
- mutable-ref movement between observation and fetch denies instead of selecting newer bytes;
- tag peeling recursively resolves only to a commit; missing refs, non-commit objects, and movement
  deny under named codes `ref-not-found`, `ref-not-a-commit`, and `ref-moved`, with no fallback;
- local object sources must prove their normalized remote and selected object and remain unchanged;
- target-state and resource-ledger tampering is detected;
- workdir traversal, absolute subpaths, symlink escape, and checkout-path reuse deny;
- every harness consumer uses the retained control/source/workdir/commit tuple;
- retained Stage-E request and `TASK.md` substitution deny before lane-resource planning;
- caller SHA, `origin/main`, ambient `HEAD`, fetch-failure fallback, and ambient checkout dirt cannot
  affect execution;
- legacy run schemas verify but cannot dispatch or advance;
- unrelated user worktrees, branches, stashes, PRs, and untracked files are neither deleted nor
  terminal-close blockers;
- run-created unledgered or undisposed resources block close;
- full-control-root substitution, checkpoint rollback/fork, retained authority-envelope mutation,
  configuration drift, and retention-policy substitution deny before mutable state is trusted;
- ambient credentials never enter the runner; wrong executable/model/config/output schema denies;
  failed canary, failed same-session resume, no-artifact stall, cost/token/process/output ceilings,
  and process-tree escape stop without a broker effect;
- the exact closed state-dependency membership is required; missing, unknown, duplicate,
  oversized, stale, trust-escalated, or changed bytes refuse before the model and write a bounded
  zero-attempt/zero-effect refusal receipt;
- cold, exact-resume, and compaction-boundary qualification observations agree structurally while
  stale, contradictory, poisoned, missing, and oversized fixtures refuse before model/effect;
- the orchestrator sees only a retained closed nine-section projection and capsule, starts in a
  fresh working directory, and remains advisory with no grant, gate, state-transition, or cleanup
  authority; its response is retained only as `untrusted-advisory`,
  `validator-blocking-only` data, while process-environment credential isolation remains
  explicitly unqualified;
- a model-supplied path, command, argv, script, unknown capability, wrong operation definition,
  wrong run-owned resource root, capability replay, or idempotency-key substitution denies;
- every transition re-derives its code-selected obligation set/report; unknown trigger, missing
  evidence, direct ledger bypass, or changed test without one exact current ruling signed by both
  an enrolled human and distinct Validator denies, as does receipt replay;
- acceptance reports bind the exact tests, assertions, expected effects, evidence membership,
  Validator argv/config, immutable snapshots, phase versions, and bounded review round;
- promotion binds a durable terminal resource seal, later resource appends deny, and a direct
  ledger append without that exact seal fails projection re-derivation;
- deleting each new subject/receipt/digest/containment check makes a mutation test fail.
