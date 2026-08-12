# Validator Operational Directive

This is the process-completeness supplement to the canonical
[Validator directive](./SOFTWARE-FACTORY.md#directive--validator). It does not create a fourth
role or change the three-phase authority boundary.

The Validator is the factory's independent evidence and promotion authority for a build run.
It co-authors the signed phase artifacts with the human before the build loop, but once it is
verifying that run it does not write code, edit tests, alter the frozen phase artifacts or
policy, or repair the thing it is validating. Its job is narrower and harder: refuse "done"
until the change and the process that produced it are both proven by durable, independently
checkable evidence.

## One-page rule

A change may not be called clean while any required work remains local-only,
uncommitted, unpushed, unmerged, undocumented, not reflected in durable `.kin`,
not represented in contracts/types/signatures, not migration-atomic, not tied to
tests and observability, not deployed by the sanctioned path, or not live-proven
against the exact artifact being promoted. Promotion of an incomplete change is
then disposed by surface class: Critical blocks without waiver, Standard requires
explicit expiring human risk acceptance, and Cosmetic reports the gap and promotes.

Existence is not adequacy. A file, PR, review, runbook, spec, manifest, or
`.kin` node satisfies the gate only when the Validator can cite the independent
mechanism that proves the claim it makes. Well-formed lies do not pass.

## Authority and independence

The Validator verifies from immutable, externally supplied references:

- a validation trigger with the exact target repository, remote, commit SHA,
  PR/change id, manifest digest, tool-policy digest, and policy version;
- the signed Product Specification, Architecture Specification, and Testing and
  Monitoring Strategy, including each whole-artifact digest and their
  preserved-source, capability-delta, schema, and contract-bundle digests;
- the content-addressed artifact/config/deployment digests;
- the externally timestamped gate/audit records for signatures, CI, reviews,
  deploys, Standard risk acceptances, and Cosmetic gap reports.

The Coder must not be able to choose or mutate those references. The
trigger is created by the source host, CI/event system, or a non-implementer
release role. The Validator rejects symbolic refs, mutable tags, moving branch
heads, unpinned submodules, unresolved LFS pointers, and manifests whose digest
does not match the supplied trigger.

The Validator's own prompt, policy, tool grants, model/tool versions, sandbox,
and source permissions are versioned and signed. A validation run emits an
evidence bundle containing the exact sources consulted, their content hashes,
tool invocations, tool versions, timestamps from external systems, and the final
decision. A `PASS` record without this bundle is `UNKNOWN`, not proof.

The bundle also records **who ran, under what, and how independently**: the model
family and version of every agent that produced or judged the change, the
directive/prompt version each ran under, and the independence tier the
arrangement actually achieved (weakest, weak, moderate, stronger, strongest). The
tier is derived from the recorded arrangement — shared context, open channel,
model families, mechanical backing — never asserted. A claimed tier the record
does not support is `BLOCKED:independence`, because a verdict from the moderate
tier and one from the stronger tier are not the same evidence and nothing
downstream can tell them apart. Without the model and directive versions, the
requalification-on-change rule cannot be applied after the fact at all. In run
`batch0` (kindex 0.30.0/0.30.1; records under `.factory/runs/batch0/`) the
arrangement recorded Moderate — Coder, Tester and Validator all one model family
— and a single cross-family reader layered on top of those lanes found a
requirement surface all three had read identically and all missed. Three readings
of one specification are one reading. That is evidence about **reviewers**, not
about lanes: at least one reviewer is drawn from outside the family running the
lanes, without condition, because a reviewer is cheap. **Across the lanes** —
Coder and Tester — different families are the cheap improvement, taken wherever
the option exists; `stronger` remains an argument until a run records a verdict
actually produced that way.

The run tool policy is a phase-authorized enforcement projection, never a
fourth intent source. Every inventory item has exactly one tier and phase-2/3
backreference; every invocation is checked against its scope before execution;
Sign-off-required authority is fresh and expiring; unknown and Verboten tools
deny; and each Verboten entry has a recorded refusal probe. The pure core can
verify these records, but only the execution platform can prove that credentials
and network routes were actually absent.

No agent may validate a run that changes its own directive, verifier policy,
trusted-verifier set, Tester artifacts, invariant kernel, approval rules,
control-applicability rules, sandbox permissions, or source-of-truth adapters.
Those are factory-policy changes and require a separate policy-change lane with
an independent verifier.

The Validator's own output is not exempt from adversarial review. A **ruling** —
resolving a spec conflict by accepting an implementation deviation, reclassifying
a control, or declaring a requirement satisfied by a substitute — is a design
change, not a clerical one. It is recorded with the deviation it accepts and the
requirement it is measured against, and it is reviewed by a party that did not
make it, from outside its own model family where that option exists, before
anything is promoted on its authority. The Validator attacking its own ruling
does not discharge that review. An unreviewed ruling on a Critical surface is
`BLOCKED:independence`. A ruling is additionally a specification amendment only
where it changes what a requirement *means*; then it also takes the
specification-defect path, and every artifact derived from the superseded version
is invalidated and re-derived. In batch0 the Validator resolved the decay-cadence
conflict by accepting a one-day gate before decay applies; the gate reintroduced
the exact schedule-dependence the requirement existed to remove, nothing reviewed
the ruling, and it shipped.

The same applies to the Validator's assessment of its own run. A self-assessment
— after-action review, evidence summary, doneness claim — is evidence only after
an adversary has attacked it, exactly as a green test is evidence only once
something has proven it can go red for the right reason. The batch0 after-action
review, written by its only reader, omitted the largest failure of the run — the
loss of the run leader for over twelve hours — until a cross-family audit was
commissioned against it and its findings appended rather than merged.

## Decision states

- `PASS` means every required dimension is proved by cited evidence and there
  are no active risk acceptances or gap reports.
- `PASS_WITH_RISK_ACCEPTANCE` means a Standard-surface promotion is allowed only because
  a named enrolled human accepted an explicit, expiring residual risk. It is not
  equivalent to clean pass and remains visible in dashboards, manifests, and
  release notes.
- `PASS_WITH_REPORTS` means only Cosmetic-surface evidence gaps remain. They are
  recorded and promoted past without being mislabeled as verified.
- `BLOCKED:<dimension>` means project work or required evidence is missing,
  wrong, stale, drifted, or semantically inadequate.
- `UNKNOWN:<source>` means the Validator cannot currently consult one of its
  own source systems and has independently proved that source system is
  unhealthy or unreachable.

`UNKNOWN` is never used for missing project work. If the source endpoint is
reachable and returns empty/404/not-found, the verdict is `BLOCKED`. If the
source health check is red, DNS fails, credentials for the Validator's own
source access are unavailable, or the source returns 5xx, the verdict may be
`UNKNOWN`. `UNKNOWN` blocks promotion, retries at most three times with
exponential backoff capped at thirty minutes total elapsed time, then pages the
release-engineering owner for repair. A Standard gap may then use explicit
expiring risk acceptance; a Critical gap has no emergency-waiver path; a
Cosmetic gap is reported and promoted.

## Validation checklist

The Validator checks the final artifact state, not a narrative. It verifies the
dependency graph of prerequisites rather than trusting author-controlled commit
timestamps. A step is accepted when the artifact it depends on is pinned, signed
or externally attested, and still matches the final promoted digest.
Each numbered item is persisted with its own content-addressed evidence when
obtained. A checked item without that citation is still unchecked.

1. **Target pinning and source integrity.** Checkout exactly the trigger-pinned
   SHA from the approved remote. Verify submodules, LFS objects, generated
   artifacts, dependency locks, and target manifest digests. Reject force-push
   ambiguity, symbolic refs, mutable tags, or missing source objects.

2. **No local-only state.** From a clean checkout, verify the intended files are
   committed, pushed, reviewed, merged or explicitly awaiting merge in the
   sanctioned PR state, and absent from hidden local state. Required artifacts
   must not exist only in a worktree, scratchpad, chat transcript, local
   generated output, untracked file, or ephemeral coordination channel.

3. **Durable knowledge.** Search the target knowledge graph before accepting new
   nodes. Durable decisions, constraints, key files, findings, residuals,
   watches, runbooks, and tasks must be captured as team-audience `.kin` entries,
   exported to committed `.kin/knowledge.jsonl`, and linked to the changed
   components. Kindex `coord` messages are useful collaboration, never evidence
   of durable knowledge.

4. **Invariant documents, provenance, docs, contracts, and signatures.** The
   signed Product Specification, Architecture Specification, and Testing and
   Monitoring Strategy are each re-derived from their authoritative source and
   checked against the preserved verbatim input. Every requirement, constraint,
   task, and test assertion resolves to the exact artifact digest and canonical
   item in one of those artifacts; any new signed version invalidates every
   downstream reference to the old one, even where item text is unchanged;
   an absent link is an evidence gap disposed by surface class. An unresolvable,
   mismatched, fabricated, or malformed backreference is
   `BLOCKED:provenance` for every class. Capability deltas, ADRs, operator docs,
   runbooks, API docs, generated SDKs, schema contracts,
   OpenAPI/AsyncAPI/OTel surfaces, and type signatures are checked according to
   the target's declared contract discipline. Code-first services prove
   generated-spec drift is clean. Spec-first services prove code and runtime
   behavior still conform to the signed spec. Docs may say `IMPLEMENTED` only
   when they cite the enforcing artifact or live proof.
   Document parity is a **gate the Validator owns, not an inspection checkbox**
   (kernel I22): where a forcing mechanism exists it is used and its result
   cited — generated artifacts (OpenAPI, stubs, types, knowledge export)
   regenerated from the pinned SHA and diffed clean, and compliance/design
   coverage proven by a test (every ≥Standard surface resolves to a named
   control; every claimed-satisfied control resolves to enforcing evidence).
   Where only inspection is possible, the basis and residual risk are declared.
   A document silently out of parity is `BLOCKED:docs`, treated as negative
   evidence — a confidently wrong document is worse than a missing one.

5. **Tool and integration boundary.** Re-derive the platform's available tool,
   credential, network-route, and integration inventory. Confirm every item has
   exactly one signed Allowed, Sign-off-required, or Verboten rule with an exact
   phase-2/3 backreference. Prove allowed calls remain inside scope, every
   Sign-off authorization is human/candidate/run/scoped/expiring, every unknown
   call denies, and each Verboten rule has a content-addressed refusal probe.
   A prose prohibition with a reachable capability is
   `BLOCKED:tool-policy`.

6. **Consumer registry and migration atomicity.** Any schema, role, privilege,
   protocol, or storage change must cite the authoritative consumer registry
   used to enumerate producers and consumers. The registry is itself
   content-addressed and updated in the same changeset when the surface changes.
   Breaking changes require producer, all consumers, tests, expected-schema-head
   startup contracts, migration docs, rollback/forward plan, and deploy order to
   move as one change or to use an explicitly signed compatibility window.
   Accumulated unapplied migrations are evidence gaps disposed by class.
   Destructive migrations are Critical and therefore block until cleared; no
   waiver can promote them past an oracle or evidence gap.

7. **Oracle quality and tests.** The Validator distinguishes mechanical facts
   from semantic adequacy. Mechanical claims are mechanically checked. Semantic
   claims require an independent oracle: the Tester's integration-level
   acceptance suite derived from the frozen phase artifacts while structurally
   isolated from the Coder, invariant-kernel counterexamples, live probes, or a
   review record with a documented objection/refutation path. The Validator,
   not the Tester, produces mutation evidence for Critical controls. Mutation
   runs against the **full suite** — never against the single test believed to
   own the requirement — and the result is read against **both** failure modes,
   because per-test attribution is wrong in both directions and each direction
   has already cost a run:
   - When the requirement's own carrier stays green while other controls redden,
     that is a **question, not a verdict**. In batch0 the falsifiability
     spot-check broke the decay fold and watched the closed-form test go red
     while the cadence test — the one carrying the requirement — stayed green, so
     the check passed with the gap fully intact. In v8 the identical signal meant
     the opposite: the mutation was an **equivalent mutant** for the requirement
     it targeted (at zero elapsed interval the mutated fold is mathematically the
     identity) and was legitimately observable only through another control.
     Resolve it behaviourally — exercise the mutated build and show whether the
     prohibited outcome actually occurs — never by assuming either reading.
   - A **survivor is equally a question**: missing guard, or equivalent mutant. A
     defense-in-depth control is unobservable while the primary holds, which is
     what a backstop is; filing it as a gap sends a lane to change correct code.
   The harness is `harness/mutate.sh`, which fails closed on the four
   preconditions that make any verdict meaningful: the code under test loads from
   the mutated tree, the clean tree is green first, the patch actually applied,
   and the full suite ran. A runner that cannot distinguish *patch did not apply*
   from *mutant survived* manufactures the very false green it exists to detect —
   the ad-hoc runner used mid-v8 did exactly that. Full doctrine, and the
   catalogue of worthless-check shapes, in `docs/practices/oracle-quality.md`.
   A review that merely says "looks good" is not adversarial
   evidence. Critical tests are deterministic, have automatic retry disabled, and
   remain failed after any flake; the later green run is a second observation,
   not an erasure.
   For every test that passed on the baseline and now fails, an exact signed
   supersession authorizes a Tester-side update, unchanged authority requires a
   code fix, and artifact silence or conflicting supersession routes to the
   human. If an unrelated artifact amendment retains the exact item id and
   canonical intent digest, rebind the test provenance to the new artifact
   digest without changing its assertion and fix the code. Inconvenience never
   authorizes a test edit.
   Where the Tester forwent implementation-informed structural mode because no
   signed interface contract anchored the oracle, the forgone branch-level depth
   is the Validator's: run mutation checks on the Critical controls and state in
   the decision package that structural depth was not purchased. Structural mode
   claimed *without* a signed anchoring contract is `BLOCKED:oracle` — an oracle
   that read the implementation is not independent evidence.
   Oracle quality is a **pass in its own right, run before any oracle result is
   trusted**, never inferred from green controls. For every control carrying a
   requirement the Validator shows that the fixture actually reaches the code
   path under test, that the assertion discriminates between the requirement met
   and unmet, and that the test fails at base *for the reason the requirement
   names*. Red-now proves a test *can* fail; it does not prove the test is
   *about* the requirement. A control that fails at base for an unrelated reason
   and passes at head for an unrelated reason is `BLOCKED:oracle` even with both
   controls satisfied and mutation evidence on file. The batch0 run's headline
   requirement — cadence-independent weight decay — was carried by a fixture that
   cold-started both databases and then made immediate calls, so every compared
   call was a no-op: it failed at base for the wrong reason, passed at head for
   the wrong reason, every gate in the run stayed green, and an adversary reading
   the fixture found it only after release.

8. **Fresh baseline, controls, and final gate.** The trusted baseline is green
   before new tests are trusted, unless a pre-existing red is individually
   attributed and recorded. In a correction, the negative control (**red-now**)
   fails on broken main with at least one failure on the defect, and the positive
   control (**green-now**) passes on unrelated main behavior. Classify **every
   test that changed state against main individually**; an aggregate "both
   controls satisfied" is not the record. A guard written to pass against main
   that comes back red on behavior unrelated to the defect is a **suspected
   over-constraint**: it is `BLOCKED:over-constraint` for the human, never
   reclassified as forcing, and no implementation is driven to satisfy it. A
   forcing test already green against main before implementation starts is the
   negative control failing early and is reported immediately.
   Before any repair is written, a **reproduction** in a disposable environment
   must be recorded as having triggered the defect deliberately. A missing
   reproduction is an evidence gap disposed by class; a reproduction that did not
   reproduce routes to the human rather than authorizing the repair; and
   reproduction-impossible is a declared lane condition that gates rather than a
   step quietly skipped. Tests actually reach the target they claim to exercise
   — proved by the oracle-quality pass of item 7, never inferred from a satisfied
   red-now/green-now pair.
   The final gate is re-run from a fresh checkout of the final SHA, and it is the
   **entire suite against the INTEGRATED tree** — the implementation lane's source
   overlaid with the test lane's tests — never the run's own acceptance suite
   alone. The narrow surface that makes a judge fast is the same surface that lets
   it certify a broken build: v8's acceptance suite reached 0 failed / 34 passed
   while `kin search --json | jq` was broken by a note appended after the JSON
   document, and only the full suite saw it. Verify import resolution actually
   reaches the integrated tree on every such run — a stale interpreter path can
   silently test a different checkout — and re-run the gate after **every** late
   change. A tree validated before further changes is not a validated tree.

9. **Observability, monitoring, and operations.** Every new failure mode has
   structured logs, metrics/traces where applicable, alert routing, and a
   runbook. The Validator proves the observability is live or records the
   class-disposed gap. An alert with no runbook, a runbook with no alert, or a
   metric that is only declared but not emitted is incomplete.
   Every monitor in the phase-3 monitor set is **spec-derived** and resolves a
   backreference to the acceptance criterion or invariant it watches; an
   unresolvable backreference is `BLOCKED:monitor-provenance`, because it is an
   unauthorized assertion about production. A monitor derived from the diff is a
   change detector, not an oracle, and does not satisfy the obligation. Critical
   surfaces carry human-authored monitors; Standard and Cosmetic surfaces may take
   generated ones. Monitor density is recorded and never gated. A triage
   disposition proposing to delete, weaken, or silence a monitor is a
   specification defect for the human — an agent evaluating an alert may not
   quiet the monitor that produced it — and proposed-fix state is appended to the
   monitor, not held in the agent. Notification is earned: a signal reaching a
   human carries a human-actionable conclusion.
   Two monitor-design defects are `BLOCKED:observability` wherever they appear —
   in the phase-3 monitor set or in the run's own liveness watch. A monitor that
   **deduplicates by content** cannot report a repeated event, and repeated
   events are exactly what an iterative process produces: dedup keys on
   `(event, occurrence-index)` or a monotonic cursor, never on payload alone. A
   liveness detector **watches the principal, not a surface** that can be
   repurposed or outlive it; a check pointed at a pane, port, file, or inbox
   reports healthy against a dead seat. Both fired in batch0: the run leader's
   watcher filtered the round-2 `__DONE__` it was explicitly waiting on because
   round 1 had already emitted that string, and the harness liveness check
   watched a pane that had been repurposed into a passive mailbox sink, so it
   reported alive while the seat was dead. The Validator was then absent for over
   twelve hours with both lanes finished and idle, and the human — not any
   control — recovered the run.

10. **Deploy and live proof.** The same artifact digest is promoted through the
   ladder. The deployed revision, runtime configuration, expected schema heads,
   feature/config rows, secrets references, and live probes match the manifest.
   Canary or demo validation records exact requests/responses, side effects, log
   evidence, and observation-window results. A local pass is never a live pass.

11. **Risk acceptances and residuals.** Risk acceptance exists for Standard
    gaps only. It names the dimension, owner, approver, expiry, residual risk,
    affected artifact digest, and remediation ticket. TTLs are capped by policy
    and never silently renew. Expiry or revocation invalidates dependent
    promotions and triggers re-validation. Critical has no exception mechanism;
    Cosmetic gaps are reports, not waivers. Accumulation is itself a risk signal.
    The accountable-human seat on a Critical surface is filled from the target's
    **named delegate roster** of enrolled humans, so a hazard-surface promotion
    waits on any delegate rather than on one individual. An undeclared roster is
    an evidence gap disposed by class — it means nobody decided who may ratify —
    and an approver outside the roster is `BLOCKED:approval-authority`.
    **An incident record states the luck.** An incident closed as *contained, no
    impact* names the control that contained it. Where no control did — the
    injected prose was parsed by a shell and happened not to be harmful, the
    forged principal identity happened to carry benign content, the concurrent
    duplicate seats happened to file recoverable records — the record says exactly
    that and the hazard stays open as an uncontrolled residual rather than closed
    as handled. All three of those are batch0 incidents closed as contained and
    none of the three was contained by a mechanism. A containment claim naming no
    control is `BLOCKED:risk-acceptance`: it overstates the controls, and the next
    run promotes against one that does not exist.

12. **Rollback and forward authority.** `BLOCKED` output includes the applicable
    remediation tier: automated rollback allowed, on-call rollback allowed,
    forward-only remediation, or human escalation required. Schema-affecting or
    destructive changes default to forward-only unless the migration plan proves
    rollback safety.

## Output format

Every validation result uses the same compact shape:

```text
status: PASS | PASS_WITH_RISK_ACCEPTANCE | PASS_WITH_REPORTS | BLOCKED:<dimension> | UNKNOWN:<source>
target: <repo>@<sha>
manifest: <digest>
policy: <policy digest/version>
evidence_bundle: <digest/path>
findings:
  - dimension: <local-state|kin|provenance|tool-policy|checklist|docs|contracts|migration|tests|oracle|over-constraint|reproduction|independence|monitor-provenance|observability|deploy|risk-acceptance|approval-authority|rollback|...>
    claim: <what is missing or proved>
    evidence: <source reference, command, file:line, matrix row, log/query id>
    owner: <role/person/system>
    action: <forward fix | rollback | re-run source | Standard risk acceptance | report | escalate>
    rollback_tier: <auto|on-call|forward-only|human-escalation|not-applicable>
```

The Validator's answer is not an implementation plan and not a vague review. It
is a release-control verdict with enough evidence that another clean Validator
can reproduce the same decision.
