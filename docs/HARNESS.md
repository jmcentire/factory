# The Harness

> Status: ratified 2026-08-14. Lane-start preflights (HALT, grounding, blocking-event) are
> available in `harness/lane_env.sh` as compatibility controls. Live Coder/Tester
> dispatch instead routes through the executable runner boundary: externally anchored resume is
> re-verified, every admitted context dependency is captured in a closed content-addressed
> capsule, one runtime-derived effective-directive contract plus structured readback and exact
> role contract are required, a current structural state-qualification report is required, and
> macOS Seatbelt
> constrains files and process exec. Execution-truth PR1 (2026-08-15) wires Stage R/E authority,
> exact run-owned target-state, control/source-root separation, and run-resource accounting into
> the runtime and tmux consumers. Provider-only egress is still not enforced: the Seatbelt
> profile permits general outbound network, so manifests and receipts record
> `unrestricted-outbound`; a provider-only value is refused until a provider egress boundary
> exists. Build-time gates remain in the `make ship`
> order (purity → doctrine → authority → harness → denial-probes → lint → typecheck → test).
> `endgame.sh` invokes Gate L only after deterministic gates, live proof, exact target
> re-verification, and terminal run-resource hygiene, retaining an exact candidate/run/target/
> harness-bound green-endgame admission. Gate L requires that receipt for current-contract runs,
> rechecks it under the close lock, and then seals resources and closes `harness.json`; it does not
> itself create a RunStore `PROMOTED` transition, although that transition now independently
> requires and binds the same sealed resource head. The evidence pipeline still does not automatically
> produce `promotion_inputs.json`, and the R1 cross-run receipt binding and R4 chain-authenticity
> gaps remain. A green build is not a verified run; a verified run is the cage refusing on every
> gap.
> Canonical copy: `~/Code/factory/docs/HARNESS.md`; the former `~/Code/tools/HARNESS.md`
> mirror is now a thin pointer here (reconciled 2026-08-30 — it had drifted).
> Skill form: `/orchestrate` (`skills/orchestrate.md` here; `~/.claude/commands/` holds a
> thin loader reading `prompts/orchestrate.md`).

Two execution surfaces exist and must not be conflated. `factory_runtime` is the executable
authority/isolation/evidence path: its signed ledger, target-bound generation tuple, macOS
Seatbelt projections, frozen review bytes, and Validator-signed preview survive removal of the
models. The tmux scripts under `harness/` are a coordination and transition surface around target
work. A tmux window, Kindex room name, role prompt, or filesystem convention is not mechanical
lane isolation and never upgrades an independence claim. Kindex remains context and incident
history, never authority or a secure projection boundary.

## Definition

**The harness is the externalization of every function an agent would otherwise supply for
itself — memory of record, metric, cadence, authority, capability, identity, and verdict —
implemented so that the agent cannot supply them.**

An agent may propose anything and author nothing that governs its own run. This is I8
generalized from doctrine to infrastructure: not just "no agent modifies its own approval
rules," but no agent *is the source* of any function the run is judged by.

The test for whether something belongs to the harness: **remove the model entirely — does the
thing still stand?** The ledger, the state machine, the capability grants, the receipts, the
timers all survive the model's removal. A claim that exists only because an agent said it is
not harness; it is testimony.

Every failure in the three postmortems was an agent supplying one of these functions for
itself:

| Function | How the agent supplied it | Cost |
|---|---|---|
| Metric | Validator's self-authored scoreboard (SHA-diff cron) | 8 of 10 walkthrough PRs worthless; reporting penalized 407 times |
| Cadence | All three lanes' self-set crons | 96 cold-context dispatches; the cron survived a kill order ~11 hours |
| Memory | Post-compaction summaries treated as the record | "5 subagents" was actually 96; a postmortem about summary corruption written from a summary |
| Identity/routing | Coder inheriting the channel from its own prior prompt | ~12 hours polling; correctives delivered to a detached lane |
| Authority/dispatch | Tester promoting backlog items into its own queue | Real findings, zero of them requested; scope drift laundered as heroism |
| Provenance | Validator citing its own kindex node as a founder ruling | "founder ruled IN scope" with no locatable source |
| Verdict | Validator "ratifying" its own criticality downgrades | Procedural exemption read as substantive authority |

The industry's convergence statement, which the postmortems derived independently:
**agents reason; the runner remembers and schedules; policy authorizes; tools execute;
verifiers judge; humans hold the frame.**

## The two-layer validation split

Every control in the harness is one of two kinds, and the kinds must not impersonate each
other:

- **Deterministic, always-on, gating.** Capability checks, schema validation, hash chains,
  budget caps, transition rules, signature verification. Millisecond cost, runs on every
  action, *blocks*.
- **Semantic, sampled, flagging.** Trace audits against declared goals, Sim on framing and
  doneness claims, adversarial refutation swarms. Model cost, runs on trajectories, *flags*.

A judge is never a gate — it is probabilistic and routable-around. A predicate is never a
drift detector — compliant actions toward the wrong goal pass every predicate (the Coder's
"compliant and detached" state). The Sim failure (required, available, never invoked) gets
the mechanical treatment: **the harness checks the receipt that the audit ran; the audit's
content stays advisory.** "Was Sim invoked on this doneness claim" is a deterministic gate;
"what Sim said" is judgment.

The doctrine linter follows from this: every `[BINDING]` line in doctrine must name its
enforcement — a capability, a gate, a receipt requirement, or a sampled audit. A binding
line whose enforcement is "advice" is a backlog item, not a control.

## Layer map — what owns what

| Function | Owner | Mechanism |
|---|---|---|
| Authority (what was said) | **Directive ledger** + Tessera | Append-only hash-chained JSONL of verbatim founder text; hardware-signed git commits (agent verifies, cannot sign); qualifier-preserving supersession. Chess genesis holds run-level authorization logic under the root signature. |
| Effective instructions (what this invocation receives) | **factory_runtime instruction control** | External resume configuration binds exact ledger, provisional-chain, and role-doctrine bytes. Before every dispatch or resume, runtime verifies both chains, resolves a closed canonical run/generation/role scope, blocks applicable live unsettled candidates, derives one bounded role-specific contract, requires exact directive and qualifier readback, and binds all three through the capsule and prompt. Source/head/scope/checkpoint drift refuses; context-only Kindex primers cannot enter the compiler or satisfy an effect. Unknown scope and cross-scope supersession refuse. Checkpoint binding proves selected bytes, not signer identity, natural-language conflict detection, or semantic comprehension. |
| Identity (who acts) | **Signet** | Per-lane principals from the enrollment registry (deliverable 8); short-lived scoped tokens, per-hop; deny-wins resolution. "The Tester cannot read the Coder's channel" becomes an ACL fact. |
| Policy (what a token may do) | **SPL** (jmcentire/agent-safe) | Total, gas-metered evaluation in-token at the tool boundary; sealed attenuation; expressions are approval rules, so human-signed under I8, never agent-authored into force. |
| State (what is true now) | **factory_runtime** RunState + **Chess** anchors | Per settled decisions: machine evidence chained in the factory_core ledger; anchor transitions (`*-ratified`, `human-approved`, `promoted`) carry Chess move records — prev/new state hash, actor key, signature over all four, re-execution at the anchor; anchors carry the ledger head digest only. |
| Capability (what is reachable) | `lane_env` + sandbox + egress allowlist | The environment *is* the grant: `env -i` construction from a manifest, secrets injected by name per-command, never a sourced profile. Seatbelt/containers for filesystem and network. Absence beats instruction. |
| Cadence (when anything runs) | Runner timers + schedule registry | Agents request schedules; humans grant them; `sched_audit` treats an unregistered timer as hostile and a kill order as immediate. |
| Evidence (what happened) | Receipt chain | A claim is a receipt id or it does not exist: exit code, log digest, tree SHA, dirty-state digest, chained. Absence claims carry a paired positive-control receipt. |
| Memory (what is remembered) | **kindex** — context, never authority | Provenance-tagged writes; reads are correction-aware (act on a node only with its tail acknowledged); search-before-build enforced as a receipt, not a nag. A role primer is an admitted, hashed context dependency. Resume consults the ledger on disk, never a summary. |
| Projection (who sees what) | **Cryptogram** | Per-lane sections encrypted to lane keys, orchestrator routes blind. Coder/Tester separation stops being a policy about where Pact's `decompose` output goes and becomes a decryption impossibility. |
| Budget | Runner objective ledger | Spend accounted per objective against evidence produced and uncertainty removed — "what did the last $100 buy" answerable at any time. Per-turn budgets are forbidden; they reward fragmentation. |
| Qualification | **Benchie** as configuration CI | `qualification_id = hash(model + prompts + runner + tool schemas + policy + projection + verifier + environment)`; any change requalifies. The session-start canary is a smoke test for gross configuration failure, never certification of the next trajectory. |
| Verdict | Validator + human gates | Validator executes an oracle it did not author; the judge of the judge is the qualification suite plus the human gate — the gap the Validator postmortem named ("who judges the judge") closes structurally, not with a fourth agent. |

One seat the map must name explicitly: the **orchestrator** — and it is two seats, not
one. The **dispatcher** is a script. It owns deterministic transport, launches lanes through
`lane_env`, relays, collects receipts, and enforces budgets, leases, blocks, and sole
advancement. It reasons about nothing.

In an interactive tmux run the **orchestrator-agent is resident** from ignition to terminal
disposition. The dispatcher samples a bounded last-60-line snapshot whenever a
Validator/Coder/Tester capture changes and appends every sample it obtained plus every
deterministic signal under a monotonic activity cursor and an independent cadence record. It
addresses only the Orchestrator's own pane with the complete captured cursor range. It may
coalesce notification timing; it may not decide which captured conversation bytes deserve
strategic judgment. A check the supervised Validator must opt into is not a check. This
coordination path journals pane text without redaction; do not place credentials or customer data
in these panes, and do not describe this unqualified tmux mode as a confidentiality boundary.

The resident Orchestrator reconstructs the user's ultimate goal from the bounded observed
conversation, retained run record, and native Kindex context; classifies recent input as
override/aside/intensity-change/refinement;
checks whether the current direction advances that goal, projects the outcome, implications and
side effects, and audits adherence. It keeps the outstanding-work ledger across turns. Its closed
assessment also classifies complexity and latent ambiguity, selects
`direct|clarify|decompose|deep`, first inventories apparent requirements and challenges
disproportionate complexity caused by implicit assumptions, inherited behavior, or requirement
interactions. The pressure point must name a counterfactual planning-mode, model-tier, boundary,
dependency, or necessary-chunk delta; diff size alone does not qualify. It writes bite-sized work
state to Kindex, pre-registers outcome
discriminators for competing causal hypotheses, and recommends the least expensive qualified
model for each concrete chunk. Kindex is normalized state from which a narrow dispatch projection
is derived—not a long context dump. Its effect set is exactly `{block, no-op}`: a block forces an exact-subject
human/Validator disposition; no-op grants nothing and can satisfy no gate. Pre-dispatch and
pre-verdict checkpoints refuse until a retained assessment covers their activity cursor. The
assessment's observed harness status must match `harness.json`; it cannot call an open run closed.
For runs carrying `factory-run-guidance/1`, assessment/3 additionally binds the exact current
selection, application, evidence, and open-finding state re-derived by the host. The Orchestrator
reviews classifications, applicability bases, role/authority routing, and substantive evidence;
it may not rename `routing-verified` as compliance. Pre-dispatch requires routing and pre-verdict
requires evidence completion. A changed guidance artifact makes the assessment stale even if no
pane heuristic noticed the change.
The block effect becomes durable before the report can make its cursor current. It clears only
through the existing exact-subject blocking-event disposition, followed by new activity and a new
assessment; clearing the event does not erase outstanding work.

The res-r1 v2 run demonstrates the required diagnostic discipline. It pre-registered incomplete
enumeration versus addendum blindness as distinct causes. Both lanes repeatedly cited every added
semantic subsection and those semantics landed, while known omissions and a new live-UBR hold
ambiguity surfaced; that selects incomplete enumeration. The next semantic artifact therefore
comes from the mechanical union of every lane-trace ambiguity and every adversarial-review finding,
with an explicit ruling and per-item `open|closed` assertion. A grep hit, token mention, or section
citation count cannot close an item. `semantic_union.py` implements the conservation boundary:
every retained planning/lane/review source needs two separately recorded digest-bound extraction
manifests; every extracted observation keeps a content-derived identity and exact ruling; and
the compiler embeds its full input-closure digest plus canonical checklist into the signed Product
Specification. Phase A freshly derives and byte-compares that section and refuses open items.
This proves no retained, extracted observation disappeared. The manifests retain claimed
extractor/configuration provenance but do not authenticate it. The mechanism deliberately does not
claim that a model noticed every ambiguity in raw prose or that a ruling is correct; lane questions
surface and measure those escapes. The generated section and CLI summary explicitly render
producer-enrollment coverage as unknown until the producer inventory is mechanically joined, so a
closed enrolled union cannot masquerade as whole-run semantic completeness.

A tmux author that cannot proceed without guessing emits `FACTORY_QUESTION:`. The dispatcher
retains an occurrence-specific question ID. Only the Validator can bind a human answer or already
ratified specification to that ID; the Orchestrator can issue only a generated status probe. Both
travel through `codex queue` or `codex exec resume` to the exact retained thread, never through raw
terminal input. Planned and delivered records are separate, cross-lane answers and conflicting
second answers refuse, and neither author's work is projected into the other lane.

The **qualified non-interactive path remains one-shot**. There the dispatcher invokes the
Orchestrator only on defined exception triggers and hands it a closed projection, not a
transcript. That mode is reproducible and separately receipted; resident tmux coordination is
explicitly `operator-owned-tmux-unqualified`. Neither mode gives the model grant authority.
tmux pane capture is an inferred observation and never the retained response or an isolation
boundary.

For the qualified headless path, the executable projection is a closed nine-section document (trigger, task, phase snapshot,
receipt/event/minutes tails, active directives, run projection, and harness metadata), built
from one stable-open confirmed read of each bounded input and one stable bounded-suffix read of
each append-only log, then bound by a state-dependency capsule. A mature log may grow without
disabling the wake; only the admitted tail enters the projection. The snapshot is pinned to the
initial size, so concurrent appends do not invalidate already-read bytes. Complete UTF-8 records
are admitted without byte slicing, oversized records are marked as omitted, and minutes input
enumeration has a code-owned file-count ceiling.
The runtime itself derives the phase snapshot from the exact three ratified artifacts and the
run projection from the verified ledger. It also derives `active-directives` from the exact
checkpoint-configured directive sources; missing, corrupt, substituted, or oversized sources
refuse instead of becoming an empty list. The wake caller cannot supply any of those sections.
The agent
starts in a fresh empty working directory and cannot extend that admitted set by asking the
dispatcher to inspect another path; missing context becomes a blocking question for the human.
Context cost then scales with exceptions rather than the run — the inverse of the
96-cold-loads bill. The projection, capsule, and exact final prompt bytes are retained; the
outcome receipt binds the prompt schema/assembler plus its byte count and SHA-256. This proves
what the agent received without claiming deterministic output or opaque provider-session replay.
Both seats hold zero grant authority:
manifests, registries, and the ledger are human-signed files they read and cannot write.
The executable sink is one-way: a bounded response is labeled `untrusted-advisory` and
appended only as `validator-blocking-only` data; no parser converts its prose into a broker
request, signature, ledger transition, or cleanup action.
That advisory data does exert bounded influence through the dispatcher: it blocks the next lane
dispatch until a Validator records an exact-subject `stop`, `narrow`, `escalate`, `refute`, or
`resolve` disposition. The disposition must retain exact evidence bytes under the run root and
bind their digest; acknowledgement or elapsed time cannot clear the gate. This forces a response
to the finding without promoting the orchestrator into authority or making it judge its own claim.
The advisory process is supervised while it runs: a code-owned wall ceiling and combined output
ceiling terminate the client's dedicated process group before post-processing, even if its
principal exited first. The dispatcher starts the wrapper in a separate process group, sends TERM
so the supervisor can close the client group, then KILLs the wrapper group after a fixed grace.
This is an availability boundary for the supported clients, not a kernel proof against a
deliberately detached process; that stronger claim belongs with qualified isolation.
The adapter supplies the same receipted semantic prompt through the client's real interface. Agy
receives a retained, hashed `stream-json` stdin envelope and a required empty `-p` value, avoiding
per-argument OS limits; its terminal event is structurally parsed and its raw bounded stream is
retained. Codex receives the prompt as text stdin. No bare option or malformed terminal stream is
mistaken for a successful invocation. Before launch, the supervisor stable-reads stdin, durably
retains those admitted bytes, and also passes an unlinked read-only snapshot descriptor to the
client; its own retained receipt binds those presented bytes, exact captured stdout/stderr, and the live
truncation bit. On timeout or interruption the supervisor drains already-written pipe bytes within
a fixed bound and marks incomplete drainage as truncation. Post-processing must match every
retained artifact to that supervisor receipt rather than infer delivery or truncation.
The wake receipt labels the advisory CLI sandbox `cli-declared-not-independently-qualified`:
the dispatcher exposes no artifact-pull seam and launches from a fresh directory, but Factory
does not yet claim a kernel-qualified projection-only confidentiality boundary or a
named-secret-only process environment for Agy/Codex. Until that launcher is routed through a
qualified clean environment, ambient non-Factory credentials are outside this proof.
An orchestrator that can edit a manifest is the meta-agent-with-authorization trap rebuilt
with better vocabulary. A resident Orchestrator therefore writes only its outstanding-work and
assessment artifacts; the deterministic consumer admits only block/no-op and refuses any grant.

Mapping to the field, so nothing here is exotic: the directive ledger with taint-carrying
downstream references is CaMeL's typed provenance; SPL-at-the-boundary is the
AgentSpec/Cedar pattern of deterministic predicates outside the model; resume-from-disk is
the Temporal/LangGraph durable-execution pattern; hold-until-decided two-phase confirms are
the effect-gate pattern; evidence-gap blocking before commitment actions is ECLoop's
result arrived at from our own INVESTIGATE discipline. One caution: several named systems
in the third survey (SOUNDGATE, NCS, Prompt Fencing, Agent-Airlock) could not be traced to
primary sources — the *mechanisms* are sound and appear here; nothing depends on adopting
those products.

## Codified controls

Nine controls, each mechanizing a specific postmortem lesson. The first six ship as
scripts (see Scripts).

**1. Directive ledger with qualifier-preserving supersession.** Every major failure was a
real instruction with a qualifier removed ("two-way doors" → doors; "poll to *tend the
lanes*" → poll to produce artifacts). So qualifiers are first-class fields, and superseding
an entry mechanically requires a disposition — kept, dropped, or modified — for every
qualifier the parent carried. A supersession that silently drops a qualifier is rejected at
write time. This is the piece none of the surveyed vendors sell.

**1a. The live-ruling bridge.** A hardware-signed ledger lags a live session, and rulings
issued in chat get acted on immediately — correctly. Without a defined path, agents either
stall on live rulings or improvise, and improvising is the disease this cures. So:
provisional entries live in a **side chain** (`provisional.jsonl`), agent-appendable,
hash-chained, each citing the transcript precisely (file:line:uuid:line-sha) and carrying a
TTL. They never enter the signed ledger; they are *settled* by it — batch-ratified at the
next ceremony into signed entries that confirm verbatim or correct, or refused. A live,
unsettled candidate blocks model dispatch; it is never silently added to the operative set.
Refusal settles it without activating its text. Expiry removes the candidate from the live
blocking set but never turns it into authority. The chain remains in the exact source digest so
history cannot be rewritten by expiry.

**2. Structured readback gate.** Corrective instructions carry a diagnosis and a remedy; agents
latch onto the remedy and satisfy it in ways that worsen the diagnosis. Before work starts, a
closed lane-dispatch document must bind the exact run, generation, role, and every effective
directive id, with its source quote, every qualifier source quote in source order, operational
consequences, and ambiguity states. Any missing id or qualifier, altered quote, unknown scope,
wrong scope tuple, or unresolved ambiguity blocks before model use. The runtime adds the exact
effective-contract digest. This is exact translation evidence with no author-identity claim, not
proof that the model understood or will obey it; observable obligations and effect gates still
judge behavior.

**3. Positive-control receipts.** Four wrong conclusions in one day, each a correct answer
to a too-narrow query. An absence claim ("this is dead code," "no CI exists," "the role has
no grants") is admissible only as a pair of receipt ids: the probe, and the identical
method run against something known to be present in the intended scope. If the control also
returns empty, the scope was wrong, not the world.

**4. Correction-aware kindex reads.** The Twilio repeat happened with the correct answer
already in the graph, founder-authored, in a node the agent had quoted earlier that day —
corrections accrete at the bottom, and reading stopped above them. A kindex read receipt
includes the node's tail digest; acting on a node without tail acknowledgment is a defect
class, and re-read-before-acting (not before theorizing) is the checked behavior.

**5. Secret-exposure tripwire with stop-the-line semantics.** The ~40-key exposure was
flagged four times as a "watch" and never actioned, because "only the human can fix it" was
read as deprioritization. Credential-shaped content in any transcript or log halts every
lane — a `HALT` file that `lane_env` refuses to run past — and produces a single-item
report. An incident is not a queue entry.

**6. Schedule registry and audit — durable timers vs leased wakeups.** No agent-authored
*durable* cadence survives its turn: OS timers live in the registry, human-granted, and the
audit lists every live cron, launchd agent, `at` job, and user timer — anything
unregistered fails, and the kill path is tested, because the last stray ran eleven hours
past its kill order. Distinct from that: **leased in-objective wakeups** — the short
self-pokes that babysit a live deploy. Those are legitimate and necessary; they are bounded
by objective, count, and expiry, recorded in the runner's own lease table, auto-dead at
objective close, and never touch the OS scheduler. A strict reading that requires a human
grant per wakeup kills the babysitting that ships deploys; the lease is the grant. Honest
limit: `sched_audit` sees OS timers only — runner-internal scheduling is auditable only if
the runner's lease table is, which is a runner requirement, not a script.

**7. Session grounding.** Every failure of the resume path came from consulting an
agent-authored summary. Session start and post-compaction re-entry re-derive from disk:
the authority and resource ledgers are verified and re-read; the retained target-state digest,
exact commit/tree, and clean run-owned checkout are re-derived; channel list is pulled live and
diffed against the registry; cadence is audited; and the tripwire runs. No `origin/main`, ambient
`HEAD`, operator checkout, or caller-supplied SHA participates in target selection. Lane execution
is blocked without a fresh grounding receipt.

**8. Failure classes as runner state.** A failure's class determines who may resolve it —
never the agent's prose or its stdout/stderr. Supervision, receipts, schemas, and classification
share one closed termination vocabulary. After any model attempt, the host writes a closed failure
receipt over the exact runner/model/configuration, state capsule, failed prompt, private redacted
diagnostic, and safe failure capsule. The lane independently verifies and durably retains those
bytes, records their digests in the run-resource ledger, retains the workspace, and cannot invoke
the broker. The classes that matter most, from the postmortems and the
research: `POLICY_DENIED` hard-stops with no alternative-path retry (a creative agent
routing around a 403 is the failure, not the fix); `AUTHORITY_AMBIGUOUS` freezes the branch
for ratification; `ORACLE_DEFECT` is never the Coder's to resolve; `BASELINE_CONFLICT`
(green-now gone red) goes to a human, never silently reclassified; `SIDE_EFFECT_UNCERTAIN`
reconciles external state before any retry; `EVIDENCE_UNAVAILABLE` blocks on critical
surfaces; repeated same-class failure routes upward instead of buying a third version of
the same guess.

**9. Environment-reconciliation receipts.** The harness test cuts both ways. Seven deploy
blockers in one night — declared IAM diverging from live grants, a DB host silently
inherited from production, an exact-SHA image coupling — all survive the model's removal:
a human running the same lane hits all seven. So declared-vs-live drift is harness scope
even though no agent misbehaved. `lane_env` says the environment *is* the grant for
capabilities; the same principle governs the substrate agents deploy onto. Grounding
therefore reconciles declared truth against live truth for whatever the objective touches:
registered per-target reconcilers (terraform-vs-live IAM, tfvars-vs-runtime config, image
digest expectations) run at ground time, and drift blocks the lane exactly as channel
drift does. The harness owns the requirement and the receipt; the target owns the probe.

## Build compilation and convergence

Human negotiation stays upstream of generation. The founder and Validator may go back and forth
through induced understanding for as many interactions as improve the result, but author lanes do
not begin until three distinct artifacts are sufficiently deep, agreed, and independently
ratified:

1. **Product Specification** — the requested outcome and user-visible behavioral commitments.
2. **Architecture Specification** — the major interfaces, boundaries, state/authority ownership,
   and operational decisions.
3. **Testing and Monitoring Strategy** — the user expectations, acceptance oracles, failure
   discrimination, and production observations that judge the result.

A recipe is not a fourth artifact. In `factory_core.build_plan`, a recipe pattern is a reusable,
versioned construction mechanism carrying addresses for its implementation and qualification
evidence. A recipe book/build plan is disposable per-run IR: it instantiates those mechanisms
with immutable configuration, orders dependencies, maps every Product/Architecture item to a
construction step, maps every Product expectation to a Testing/Monitoring oracle, and uses every
Testing/Monitoring item. Any change to the
run, target, catalog, build input, or phase artifacts invalidates it. Coder and Validator receive
this IR; Tester receives only the common ratified build input.

A per-run guidance selection is likewise not a fourth artifact. The user selects exact
standards, loops, or recipes by adding a canonical `factory-run-guidance-selection/1` plus every
named source to the externally anchored resume checkpoint's configuration set. Ignition retains
those exact bytes using both the accepted source vector and the verifier-returned digest map, so a
post-verification path substitution refuses. Before ratification, the Validator dispositions every selected obligation,
states why its subject is behavioral, procedural, or constructional, binds it to the corresponding
acceptance obligation, process checkpoint, or Architecture/Testing conformance requirement, and obtains an
independent review of classification and application bound to that exact application row.
`phase_compiler.py` then renders the
obligations into only the relevant portions of the three authorities in a fixed order. As a
worked infrastructure case, “the package main is named X and exposes entrypoint Y” is
constructional: it binds Architecture and a conformance-evidence route, reaches the Coder, and does
not become a Tester-visible product behavior unless the human separately ratifies an observable behavior.
`not-applicable` is a visible ratified disposition, not omission. The generated state is called
`routing-verified`; substantive compliance needs observations bound to the exact candidate,
selection, application, obligation, and raw evidence plus independent judgment. The selected set
is immutable for one run; a different set requires a new external checkpoint and run.

Validation now includes a mandatory adversarial code review over the same immutable evidence, not
an optional instruction to "look carefully." Before the Validator runs, the host issues a closed
review subject binding the exact Stage-E execution-request bytes from the externally anchored
resume checkpoint, the frozen implementation/tests and snapshots, all three ratified phase
artifacts and their ordered review-item inventories, complete bounded Git-object baseline and canonical candidate change set, build input,
plan, pattern catalog, acceptance obligations, resume checkpoint, configuration and exceptional
test-change authority, target state, and frozen Validator execution identity. Target-controlled
Validator source is constrained to one admitted Python file and supplied from the exact captured
bytes over standard input, so the launch never reopens that source pathname. The Factory Python
installation is the explicit host runtime TCB, and the execution snapshot is reverified after the
process exits. This is the closed `standalone-python-source/1` ABI: the runtime launches
`python -`, so `sys.argv[0]` is `-`, `__file__` is `<stdin>`, standard input is at EOF when the
source begins executing, and the original script directory is not added to `sys.path`.
Interpreter flags and additional path-bound arguments are outside this ABI and refuse before any
author lane launches. The ratified `factory-validator-configuration/3`,
`factory-validator-environment/3`, and `isolated-build-loop/3` identities bind these semantics.
The Validator's
canonical report must cover intent, architecture, redundancy, clarity, separation of concerns,
test adequacy, correctness/failure, and scope; disposition every Product, Architecture, and
Operational Maturity item in exact host order; cite exact retained line bytes; bind each
failure-mode probe to an actual observed obligation/effect and any selected executed test; record
the host-derived probe method; record clean-claim challenges that select distinct exact authority
and produced-evidence references for the code-owned comparison method; reject empty, repeated, or
formally vacuous narratives through a closed ASCII control-prose alphabet, letter/token floors,
and an exact minimum pairwise letter-stream edit distance without claiming semantic insight or
general copy detection; content-address every probe,
challenge, and finding; and
complete every host-declared clean-claim check. The `/1` report cannot self-refute:
every emitted finding survives and prevents a clean verdict. The host re-derives the subject,
cited evidence, completeness, item membership, observation bindings, identities, and verdict.
Empty probe/challenge sets, an unresolved item, or anything stale, incomplete,
blocking, or found stops
`VALIDATING -> PREVIEW`. `CLEAN_QUALIFIED` is evidence only and cannot authorize merge, release,
deployment, or promotion.

PREVIEW is also a cryptographic boundary, not merely a structurally valid envelope. Admission and
every replay require a host-supplied Tessera verifier, the active founder-anchored genesis, and the
exact Validator identity recorded by the validating transition. The state store verifies the
retained envelope against that Validator's enrolled key and reproduces its code-owned verification
receipt. A signature-shaped document, a different enrolled signer, a caller-supplied receipt, or a
reader without those external anchors refuses. Accordingly, `factory status` and
`rebuild-projection` require `--genesis`, the pinned `--root-public-key`, and `--tessera-bin` for a
run whose ledger has reached PREVIEW.

These requirements are versioned as `factory-run/5`. Released v0.3 `factory-run/4` ledgers remain
read-only and replay against their frozen historical validation and transition-obligation profile;
if they reached PREVIEW, replay still requires current cryptographic verification of their exact
retained envelope. Factory never fabricates the later review/verification records for a historical
run and never silently upgrades it. Continued authoring begins as a newly authorized `/5` run with
explicit lineage to the old run and ledger head.

The result is judged by agreed behavior and evidence, not generated-code aesthetics. `regenerate`
keeps a complete rewrite ordinary; `brownfield` supports a deliberately scoped correction. The
target ABI and plan both bound authoring attempts, and the ledger will not raise the ceiling after
generation starts. After the final permitted attempt, remaining defects produce a blocked handoff,
not another self-authorized specification round. Existing tests remain immutable unless current
signed authority uniquely supersedes the old same-phase behavior and a separately trusted human
ruling binds the exact assertion (or frozen family) and exact signed replacement statement.
The runtime binds the construction mode today; mechanical `brownfield` path/surface-ceiling
enforcement against the produced candidate remains unwired. The adversarial review therefore
fails closed as `INCOMPLETE` for `brownfield`; only `regenerate` currently supplies a complete
baseline-to-candidate change set that can advance to preview. A legitimate target-ABI change starts
a new authorized run rather than bypassing drift detection inside an existing run.

## The human surface

The founder's console is the third projection of the same event stream: lanes get
cryptogram sections, the orchestrator-agent gets the exception projection, the human gets
the decision projection. It is rendered by the dispatcher from the receipt chain and
runner state — no lane narrates and no agent summarizes, so the surface costs the run
nothing and cannot drift from the record. This is the structural answer to the
most-repeated directive in the postmortem corpus ("talk to me"): visibility stops being a
behavior agents remember and becomes a property of the machinery.

Three views:

1. **Tasks.** The runner's objectives with state and spend-against-evidence, drilling down
   on any key decision to its provenance: the directive (verbatim, `D-####`, or a
   provisional with its transcript citation), the recorded interpretation, the receipts
   that discharged it. "Why did it do X" is answered by the chain, never by asking an
   agent to reconstruct.

2. **Ongoing work, announced before it happens.** Every consequential action is stated
   ahead of execution as verb → object → environment (`deploy` → `staging`), with a short
   visible timer during which the human can act. The announcement is itself a receipt.

3. **Exceptions.** Errors, issues, and decisions routed to the human by failure class,
   each carrying its recommendation where one exists.

Every announced item carries one of three classes, and **the class is data** — resolved
from the control profile's criticality and reversibility, never assigned by the agent
whose action it governs; an agent classifying its own action into a timer class is
self-authorization through the console.

- **Announce — act to stop.** Reversible, pre-authorized actions. Timer elapses → proceed.
  This is the two-way-door grant made structural: the run keeps moving while the human
  holds a standing veto, instead of the grant living in an agent's memory of a qualifier.
- **Default — act to override.** Pending decisions with a strong recommendation. Timer
  elapses → the default applies, and the record says so: *default-applied under policy,
  window elapsed* — never rendered as approval. A defaulted decision is attributable to
  the policy that set the default, not to the human who didn't click.
- **Require — act to proceed.** Sincerely blocking: irreversible, authority-changing, or
  oracle-silent on a critical surface. No timer, no default; the run holds. A timer here
  is the waiver path re-entering — fail-closed becoming a speed bump — so the class admits
  none. Unclassified lands here, for the same reason an unclassified surface is Critical.

Announcement receipts carry
`{action, target, class, window, announced_at, outcome}` where outcome is one of
`vetoed | approved | overridden | default_applied | held` — so a human choice and an
elapsed window are distinguishable forever. A veto or redirect during the window routes as
a lane event; a redirect that changes what was authorized opens a provisional directive
(control 1a) rather than being absorbed as chat. Window lengths are short, configurable,
and consequence-scaled; Announce-class work proceeding while the human is away is the
point, Require-class work holding while they're away is also the point.

## Scripts

Layout: `harness/` for the scripts, `.factory/` for state (receipts, HALT, grounding
marker, registries, `reconcile.d/`), `DIRECTIVES/` as a git repo for the ledger (signed
chain `ledger.jsonl` + agent-appendable `provisional.jsonl` side chain). All are
dependency-free (bash + python3 + git):

- `harness/directive.py` — append/supersede (qualifier dispositions enforced), verify
  (`--sigs` requires signature-clean git history), `provisional`/`ratify` for the
  live-ruling bridge (control 1a), and verified `active` inspection. Runtime consumers derive
  their own closed effective contract rather than trusting this human-readable listing.
- `harness/attention_gate.py` — validates the closed producer-event shapes and owns the shared
  run-local lock that orders blocking-event production, disposition, and dispatch admission;
  exact retries repair only unterminated blocker tails, and an inherited dispatch descriptor
  repeats admission rather than becoming authority.
- `harness/consume_block.sh` — locks the exact pending subject, rejects malformed producer events,
  requires exactly one durable producer receipt plus a typed disposition, retains and hashes the
  run-owned evidence bytes, and fsyncs the receipt plus its parent before durably releasing the
  dispatch gate. Unrelated malformed legacy event rows are ignored and grant no authority.
- `harness/lane_env.sh` — `env -i` from a manifest; refuses to run past `HALT` or without
  a fresh grounding marker. It is a compatibility control; live Coder/Tester isolation is
  owned by `factory_runtime.runner_isolation`, not this script.
- `harness/receipt.sh` — wraps any command; exit code, log digest, tree SHA, dirty-state
  digest, flock-serialized per-worktree chain.
- `harness/tripwire.sh` — credential-shaped content (incl. GCP service-account JSON)
  halts every lane via `HALT`; only a human clears it.
- `harness/sched_audit.sh` — every OS timer must match the human-approved registry.
- `harness/ground.sh` — verifies the runtime-selected target-state, authority ledger, cadence,
  tripwire, channel list, and `reconcile.d/*` declared-vs-live probes; writes the grounding marker.
- `harness/factory.sh` — ignition only after Stage E; verifies exact task bytes and target-state,
  admits any `factory-run-guidance` selector and its named source documents only from the exact
  external-resume configuration vector already verified against its independently held digest,
  and freezes those bytes into the run before metadata names them;
  writes separate coordination metadata, records tmux intent, then opens persistent
  `orchestrator`, interactive `validator`, and deterministic `ctl` windows. The resident Agy
  launch uses `--new-project --prompt-interactive`, never print mode; the explicit new-project
  launch prevents an unattended project-trust prompt from swallowing the initial brief. It does
  not pass `--disable-slash-commands`, so host-supported interactive controls such as `/loop`
  remain available to the resident monitor.
  An explicitly selected interactive Claude Validator is an opt-in, operator-equivalent,
  unsandboxed process: it is not a qualified lane and contributes no filesystem-isolation
  evidence. Codex is the default; Ollama-launched Codex is the supported alternate.
- `harness/dispatch_lane.sh` — re-derives target-state; under the shared attention lock checks both
  applicable blockers and acquires a crash-released role mutex as one admission ordering point;
  durably freezes or exact-reuses caller dispatch bytes; mints the declared asymmetric projection;
  verifies/appends the dispatch chain; requires a role-specific Kindex primer and externally
  checkpoint-bound structural qualification report; then invokes the qualified model runner and
  typed broker. A failed invocation crosses this boundary only through the closed verified failure
  receipt and retained exact evidence: canonical qualification, every presented prompt, private
  primary/child executable snapshots, state capsule, and bounded diagnostic. No failed canary
  reaches the broker. Ambient gap flags cannot bypass either precondition.
- `harness/orchestrator_channel.py` + `orchestrator_checkpoint.sh` — append every resident-mode
  bounded observed activity snapshot under a monotonic cursor; validate the closed strategic
  assessment (goal, input class, trajectory, side effects, adherence, requirement-pressure
  analysis, planning mode, Kindex-backed chunks, causal discriminators, and exact harness
  lifecycle state plus exact selected-guidance state); and admit only `block|no-op`. Dispatch and
  verdict wait for the checkpoint cursor and the required routing/evidence state.
- `harness/semantic_union.py` + `phase1_gate.sh` — discover the closed retained source tree,
  require two separately recorded source-digest-bound extraction manifests, preserve every
  distinct observation, require an exact typed ruling, render the input closure into the Product
  Specification, and refuse missing/open/stale/hand-edited unions before dispatch. The compiler
  enforces post-extraction conservation, not semantic recall, authenticated provenance, or wisdom;
  producer-enrollment coverage remains machine-visible as unknown until its inventory is joined.
- `harness/run_guidance.py` + `phase_compiler.py` — admit checkpoint-selected standards, loops,
  and recipes; require exact obligation membership, subject-derived enforcement routes, concrete
  applicability bases, and independent classification/application reviews; project only applied
  role-scoped obligations; and render semantic union → guidance → agreement in one deterministic
  order. Behavioral items enter Product/Testing, procedural items enter Architecture/Testing,
  and constructional items enter Architecture/Testing as conformance requirements.
  The controls prove byte identity, exact-row review binding, membership, routing, and
  exact-candidate observation references—not that arbitrary standards prose was exhaustively
  interpreted, that reviewer identity is authenticated, or that routed code substantively conforms.
- `harness/agreement_contract.py` + `agreement_probe.py` — for runs whose ignition metadata names
  `factory-agreement-contract/1`, close an exact participant inventory over the configured Product
  requirement-region families, derive single-path versus cross-path from participant cardinality,
  and byte-compare the generated Testing Strategy register before dispatch. At endgame, each
  cross-path requirement needs distinct producer/consumer mismatch receipts that bind the exact
  candidate, unchanged selected local suite, and unchanged agreement oracle; the only alternate
  route is an exact-candidate independent review showing one structural authority carries all
  semantic residue. Older runs without the ignition field keep their released semantics.
- `harness/tmux_lane.sh` + `codex_lane_session.py` + `lane_dialogue.py` +
  `tmux_lane_message.sh` + `factory_runtime/lane_repository.py` — unqualified authoring/dogfood
  mode. Preflight requires a standalone repository whose Git/common directory is local and a
  Codex CLI exposing the exact permission/session flags; the retained launch records its version.
  Codex is launched as the agent harness with a lane-scoped permission profile that reopens the
  whole `.git` directory inside that standalone repository. The author owns that local branch and
  history; Factory claims repository isolation, not per-ref Git ACLs. Before each checkpoint the
  lane inspects its own status/diff, runs
  relevant checks, and commits only its assigned work. The JSON event wrapper retains the real
  thread ID and records an exact `FACTORY_QUESTION` only from a completed assistant-message event,
  so question blocking does not depend on lossy pane sampling and typed status probes or answers
  can queue or resume that conversation. From agent start onward
  the whole repository—including Git hooks and config—is untrusted and host Git is forbidden.
  After the pane is dead, a descriptor-relative bounded walk excludes root `.git`, rejects links,
  special entries, nested Git metadata, portable-name collisions, privileged modes and ceiling
  violations, then publishes a content-addressed regular-file snapshot. Commits are useful author
  checkpoints, never promotion evidence.
- `harness/orchestrator_wake.sh` — verifies external resume, freezes a closed bounded exception
  projection plus capsule, and invokes a one-shot advisory orchestrator in a fresh empty directory.
  Append-only sources are read as stable bounded suffixes rather than rejected when their full
  history grows; `supervise_advisory.py` enforces the live wall/output ceilings before retention.
  The agent choice is frozen in harness metadata; ambient substitution denies. Antigravity is the
  default and Codex is the supported fallback. The current Claude adapter is refused because it
  does not declare a filesystem sandbox. Agy/Codex CLI sandboxing remains explicitly unqualified
  as a kernel-enforced read boundary.
- `harness/endgame.sh` — accepts only an exact candidate SHA in a named run-owned resource,
  verifies cross-path agreement evidence and every selected run-guidance obligation against that
  candidate when the run declared those contracts, archives the exact
  object into a recorded endgame worktree, runs ship/isolation/live proof, verifies exact target
  and terminal resource dispositions, then retains the canonical green-endgame admission for
  that exact candidate and harness subject. It never inspects or cleans ambient user state.
- `harness/promote.sh` — Gate L harness close, driven only by the pure promotion verdict and its
  chain-anchor checks. A current-contract run also requires the run-local green-endgame admission;
  the subject is checked before promotion and again under the atomic close lock. After an allowing
  verdict it seals the exact terminal resource head. It does not create a RunStore `PROMOTED`
  transition.

The green-endgame admission is a deterministic receipt from the trusted host control script, not
a signature that defends against a malicious operator who can rewrite the harness or run control
root. Its purpose is to make the supported current-run close path executable and fail closed:
agents cannot mint it, `promote.sh` cannot omit it, and an accidental direct promotion call cannot
stand in for the endgame. The operator and host substrate remain the trust root.

A completed judging pass is not a completed run. `VERDICT: BLOCK`, failing tests, unresolved
review findings, missing inputs, or a refused Gate L all leave `harness.json` open and route to
remediation. A run becomes terminal only through Gate L writing `closed` after an allowing
promotion verdict and, for current runs, a candidate-bound green-endgame admission, or
`record_no.sh` writing `no`; ending a model turn, closing a Kindex session
tag, or declaring completion in chat has no lifecycle effect.

The active scripts live in `~/Code/factory/harness/`; tests and the denial-probe registry are the
enforcement inventory, not this prose.

Founder ceremony, once: `git init DIRECTIVES && git config commit.gpgsign true` with the
signing key on hardware requiring touch. Every append is committed signed. The agent runs
`verify --sigs` at grounding; an entry the founder didn't physically touch a key for is not
a directive. Distribute the founder public key with the harness — a fresh machine without
it fails `verify --sigs` closed, which is correct behavior, so the keyring install is part
of machine setup, not an error to route around.

Receipt convention the runner enforces: any prose claim "ran X" / "X passed" must cite an
`R-` id whose `cmd` and `exit` support it. An absence claim cites two — probe and positive
control, identical method, and the control's exit demonstrates the scope contained
something. Chains are per-worktree and never merged: the exclusive lock handles
concurrency within a worktree, a verdict package cites receipts by (worktree, id, hash),
and cross-worktree ordering is not a property receipts claim.

`lane_env` grounding staleness defaults to six hours; on incident days set
`HARNESS_MAX_GROUND_MIN=60` — six hours of credit is calibrated for steady state, not for
a substrate that is actively moving. The knob tightens only: a value above the 360-minute
default is refused (clamped to 360), because extending trust in a stale ground is a
ratified policy change, not an environment variable.

## Anti-features

Named because each was proposed somewhere in the last month and each is a trap:

- **Elapsed silence called a stall.** An unchanged pane cannot distinguish a reasoning loop from
  an I/O hang, and a warm pane cannot prove its principal is alive. In resident tmux mode the
dispatcher therefore reports `liveness_unknown` rather than blocking on a timer. The Validator
and Orchestrator inspect process/pane state and use the typed Codex-session status probe; the
response classifies `WORKING|BLOCKED|QUESTION|DONE`. Where the host supports `/loop`, it repeats
`status.sh` plus this inspection/probe—not raw model attempts. Leases, process identity,
checkpoint ages, and explicit replies are separate observations; none is silently promoted into
a semantic liveness verdict. A pending typed question is a known `waiting-on-validator` state and
suppresses the quiet-time alarm; it is already blocked by the question gate.
- **A meta-agent with authorization.** A planner may decompose, diagnose, and recommend. It
  may not grant capabilities, move gates, rewrite authority, or reinterpret a policy denial
  as an invitation to find another route. The supervisor that detects drift must not itself
  be a drifting agent.
- **An LLM judge as a gate.** Sampled audits flag; deterministic checks block. Inverting
  this gives you a bypassable gate and an alert wall.
- **Software-only signing sold as tamper-proof.** On a host the agent controls, any key it
  can read to verify it can read to forge. Software chains are tamper-*evident* — valuable,
  and honestly labeled. Authenticity of founder directives requires the key to live in
  hardware that demands a touch. This resolves the runner doc's "hardware signing is
  ceremony" against the postmortem's §9.2: it isn't ceremony *for directives on an
  agent-controlled host*; it is the only construction that works there. Everywhere else the
  assurance ladder applies (digests → chained receipts → org signing → hardware).
- **Per-session Benchie as certification.** Passing four canaries at 8am proves the
  configuration loaded, not that hour six retains a qualifier. Qualification is per
  configuration generation; the session canary is a smoke test.
- **Retry on `POLICY_DENIED`.** Ever.

## Adoption order

1. **Containment trio, today:** `tripwire`, `lane_env` (with `env -i` manifests),
   `sched_audit`. These three would have prevented the credential exposure, the 96 cold
   dispatches, and the eleven-hour cron overrun — the entire bill of the MEA session.
2. **Ledger + grounding:** founder ceremony (first hardware-signed commit — the same act,
   rhyming deliberately with slice-0 genesis), with the provisional bridge live from day
   one so in-session rulings have a path other than stall-or-improvise; then `ground.sh`
   wired as the precondition `lane_env` already checks, with per-target reconcilers
   registered for whatever substrate the first objectives touch. Backfill the 266
   extracted founder directives as the initial corpus; `mea_founder_directives.md` is
   exactly the input format.
3. **Receipts into the gates:** `make ship` and every lane claim route through
   `receipt.sh`; the positive-control convention starts being enforced in review; the
   human surface renders from the same chain — task list, pre-action announcements with
   veto windows, exception queue — as the third projection.
4. **Chess anchors on `human-approved` / `promoted`** — slice 5, per the ratified build
   order: the staged human decision (approve / request changes / abandon) before merge,
   CI after, both producing signed move records instead of a non-empty actor string.
5. **Cryptogram projection** in the build loop: per-lane encrypted sections, blind routing;
   oracle independence becomes a decryption impossibility. Sequencing flag, not a decision:
   live-run evidence (three-plus Tester contaminations, every one caught rather than
   prevented, all via the Validator's own channel messages) argues for pulling this ahead
   of step 4; the counterweight is that unsigned promotion is the higher-consequence gap.
   Founder's call.
6. **Benchie as configuration CI**, seeded the way OpenAI seeds theirs: every real factory
   incident becomes a policy test, a structural invariant, or a regression benchmark. The
   three postmortems plus the deploy saga are the first fifteen cases — the saga entering
   as the first environment-reconciliation regression.
