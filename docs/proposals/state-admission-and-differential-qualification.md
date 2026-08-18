# State admission and differential qualification

> Status: implemented Factory slice, pending release. This document does not grant
> authority and does not weaken any ratified Factory invariant.

## Outcome

Make accumulated agent state an explicit, bounded input to execution instead of an ambient
property of a long-lived session. The deterministic runtime will admit an exact dependency set,
bind it to the model dispatch and result receipt, and qualify the admission boundary against
cold, resumed, stale, structurally contradictory, poisoned, compaction-boundary, missing, and
oversized-input cases. Qualification compares deterministic admission and acceptance
dispositions; it never compares model prose or token streams.

Kindex remains the durable contextual-memory layer. A Kindex primer or read receipt is context,
never authority; the only intent authorities remain the three ratified phase artifacts.

## Why the orchestrator is the leverage point

The model-facing orchestrator is not the control. The resident dispatcher and runtime boundary
are the control because they decide what state is admitted, when a model is invoked, what the
model can reach, and whether its result may progress. The invoked orchestrator-agent continues to
flag and advise only. It receives a bounded, content-addressed projection and holds no grant,
gate, signing, or advancement authority.

## Executable slice

1. Add a closed `factory-state-dependency-capsule/1` contract. Every dependency has a logical
   identifier, kind, trust class, byte count, and SHA-256 content address. Dependency kinds have
   code-owned trust ceilings so a Kindex primer, tool result, channel message, or model summary
   cannot be mislabeled as authority. The schema validates structure; a separate closed profile
   defines required membership and trust ceilings. The profile digest is externally checkpointed
   with the other runtime configuration so a policy change invalidates resume.
2. Derive the lane capsule inside `run-model`, after external resume verification and before the
   model is called. Bind the current target state, run-ledger head, three phase artifacts, frozen
   task, role projection, role-scoped Kindex primer, runner manifest, output schema, broker
   registry, configuration set, and resume lineage. The shell may name files; it may not testify
   to their digests. The runtime opens each dependency once, reads bounded bytes, validates any
   existing signature or checkpoint, and hashes those same in-memory bytes. Paths and contents
   are not stored in the capsule. A hostile process able to mutate runtime memory or descriptors
   is outside this slice's trust boundary; pre/post-read identity changes deny. Stable-read and
   ledger durability are qualified only on the repository's declared local POSIX filesystem
   boundary; network and object-backed mounts remain unqualified.
3. Require every canary and handoff to echo the capsule digest. Record the capsule, task,
   projection, and external resume-checkpoint digests in a versioned runner receipt. Broker
   execution rejects a handoff whose capsule no longer matches the retained frozen state. The
   capsule is also bound to run, generation, role, target, and resume lineage, so an echo from a
   different run is not replayable. A rejection is terminal for that attempt: an intentional
   amendment requires a new externally anchored run or generation and a newly derived capsule;
   there is no accept-on-mismatch override. The receipt also binds the prompt schema and assembler
   versions plus the ordered byte count and SHA-256 digest of every canary/task prompt. Those exact
   bytes are retained mode `0600` in the run-owned input workspace. This proves what was supplied;
   it does not claim deterministic model output or reproducibility of opaque provider session state.
4. Replace the orchestrator wake's ad hoc Markdown transcript slice with a bounded structured
   canonical-JSON projection whose sections are length-bounded, encoded as data, individually
   hashed, and covered by the same capsule rules.
   Dynamic minutes and Kindex/directive summaries remain explicitly contextual. The trigger,
   checked run state, task, event tail, and receipt tail are separately attributable. Retain the
   exact assembled advisory prompt and bind its schema/assembler, byte count, and SHA-256 in the
   outcome receipt.
5. Add a deterministic state-differential qualifier. Named scenario families require cold,
   exact-resume, and compaction-boundary inputs to produce the same structural disposition even
   when their capsule digests differ. Stale, structurally contradictory, poisoned, missing, and
   oversized admitted inputs must be refused before the downstream probe. The qualifier itself
   invokes no model and produces no broker effect; live runner qualification and receipts prove
   those separate boundaries. Oversized
   model output is a distinct post-call bounded-stream failure. New dependency kinds automatically
   inherit mutation, missing, trust-escalation, and size properties; unknown or hybrid states deny.
   Dispatch re-executes this small deterministic matrix and re-derives the supplied report under
   the transition lease; an old materialized report cannot stand in for current code behavior.
   This qualifier is necessary but never sufficient for promotion: independent product acceptance
   evidence remains mandatory.
6. Remove ambient gap variables from this path. Missing phase artifacts or role primer deny;
   they cannot be converted into an unsigned event by setting an environment variable.
7. Prefer Codex or Ollama-to-Codex for qualified build/test lanes, Codex for the interactive
   Validator surface, and sandboxed Antigravity for invoked orchestration, with Codex as the
   supported fallback. Claude is refused at the automated orchestrator boundary because its
   current adapter does not declare a filesystem sandbox. Runner cost/time/token budgets
   remain enforced by the existing signed manifest. Antigravity runs from a fresh empty directory
   with slash commands disabled and a bounded projection supplied on standard input; it remains
   advisory even if its own sandbox fails. The CLI sandbox declaration is receipted but is not
   independently kernel-qualified, so it does not support a confidentiality or lane-independence
   claim. We do not claim Agy plan-mode enforcement: its CLI reports that plan mode has no effect
   when slash commands are disabled.

## Lifecycle and cutover

- A role-scoped Kindex primer is snapshotted once for an attempt. Later Kindex writes do not alter
  that attempt and do not invalidate ratified phase authority; a retry receives a new capsule.
- A contextual mismatch may be requeued as a new attempt. An authority, configuration, target, or
  resume mismatch requires the existing external checkpoint/generation ceremony.
- Pre-capsule sessions and v1 runner receipts are not grandfathered or silently upgraded. In-flight
  legacy attempts stop at cutover. An explicit human-named abandonment receipt disables their
  dispatcher but does not claim close or resource disposition; run-owned resources remain visible
  until inspected and dispositioned, and execution restarts as a new v2 run.
- Refusals emit bounded structured reason codes and expected logical identifiers, never missing
  content, guessed paths, arbitrary requested-purpose strings, or fabricated digests.
- Historical receipts remain immutable. Revocation is enforced through the existing active
  generation/configuration invalidation path; this slice does not claim a passive audit record is
  an enforcement mechanism.

## Verification

- Closed-schema, duplicate, trust-escalation, stale-digest, symlink, mutation, and size-limit
  tests for capsules and orchestrator projections.
- Runner tests proving no model attempt happens after capsule failure, that task/output/
  projection/resume mutations invalidate the receipt chain, and that each retained prompt exactly
  matches the ordered digest and byte count in the runner receipt.
- Differential fixtures plus property/mutation tests for every dependency kind and every expected
  disposition, including partial construction and unknown/hybrid input.
- Harness tests proving no unsigned gap override, no Claude default, sandboxed Antigravity
  invocation, and no orchestrator authority field.
- `make check-purity`, focused tests, `make ship`, `make test-isolation`, and `make test-tessera`.

## Non-goals

- Replacing Kindex with a new memory database.
- Treating a capsule, hash, or Kindex node as authority or authenticity.
- Detecting semantic contradictions inside ratified prose by comparing hashes. That remains a
  human/specification-validation responsibility.
- Letting the orchestrator-agent select triggers, edit manifests, advance state, or clear blocks.
- Claiming that synthetic differential fixtures prove semantic correctness of a model. They
  qualify the state-admission and replay boundary; acceptance tests still judge product behavior.
- Claiming provider-only egress. The current Seatbelt profile permits general outbound traffic,
  so manifests and receipts honestly record `unrestricted-outbound` until a provider allowlist or
  proxy is mechanically enforced and independently qualified.
- Preserving an unbounded transcript. Exact resume means externally anchored lineage plus an
  admitted dependency set, not replaying every prior token.
