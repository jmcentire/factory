# The Harness

> Status: ratified 2026-08-14. Lane-start preflights (grounding, blocking-event) are
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
> Canonical copy: `$FACTORY_HOME/docs/HARNESS.md`. The former `HARNESS.md` mirror in the
> operator's standards repo (reconciled 2026-08-30 — it had drifted) was retired on 2026-10-03.
> Skill form: `/orchestrate` (`skills/orchestrate.md` here; `~/.claude/commands/` holds a
> thin loader reading `prompts/orchestrate.md`).

Locations: `$FACTORY_HOME` is this repository's checkout (default: the root of this checkout);
defined in `prompts/README.md`, Locations.

Interpreter: harness scripts run factory code on `$FACTORY_HOME/.venv/bin/python` (what
`make install` builds), never on whichever `python3` is first on PATH; `harness/factory_python.sh`
resolves it once (an explicit `FACTORY_PYTHON` wins) and stops with the fix when there is none.

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
| Cadence (when anything runs) | Runner timers + schedule registry | Durable schedules are registered in the run's schedule registry; `sched_audit` treats an unregistered timer as hostile and a kill order as immediate. |
| Evidence (what happened) | Receipt chain | A claim is a receipt id or it does not exist: exit code, log digest, tree SHA, dirty-state digest, chained. Absence claims carry a paired positive-control receipt. |
| Memory (what is remembered) | **kindex** — context, never authority | Provenance-tagged writes; reads are correction-aware (act on a node only with its tail acknowledged); search-before-build enforced as a receipt, not a nag. A role primer is an admitted, hashed context dependency. Resume consults the ledger on disk, never a summary. |
| Projection (who sees what) | **Cryptogram** | Per-lane sections encrypted to lane keys, orchestrator routes blind. Coder/Tester separation stops being a policy about where Pact's `decompose` output goes and becomes a decryption impossibility. |
| Budget | Runner objective ledger | Spend accounted per objective against evidence produced and uncertainty removed — "what did the last $100 buy" answerable at any time. Per-turn budgets are forbidden; they reward fragmentation. |
| Qualification | **Benchie** as configuration CI | `qualification_id = hash(model + prompts + runner + tool schemas + policy + projection + verifier + environment)`; any change requalifies. The session-start canary is a smoke test for gross configuration failure, never certification of the next trajectory. |
| Verdict | Validator + mechanical gates | Validator executes an oracle it did not author; the judge of the judge is the qualification suite plus Gate L and the founder's override afterwards — the gap the Validator postmortem named ("who judges the judge") closes structurally, not with a fourth agent. |

One role the map must name explicitly: the **Orchestrator**, one of the Factory's four roles
(Validator, Orchestrator, Coder, Tester), with two parts that both run for the whole run. The
**dispatcher** is its script part. It owns deterministic transport, launches lanes through
`lane_env`, relays, collects receipts, and enforces budgets, leases, blocks, and sole
advancement. It reasons about nothing.

In every run the **orchestrator-agent is resident** from ignition to terminal disposition. It
runs a check-in loop on a cadence that cannot be disabled (15 minutes by default; a zero or
negative interval falls back to the default), and its `orchestrator/OUTSTANDING-WORK.md` plan and
reminders are printed into the Validator's own output at every pre-dispatch and pre-verdict
checkpoint and shown by `status.sh`. The dispatcher, the pre-dispatch and pre-verdict
checkpoints, and the orchestrator channel refuse a run without it. The dispatcher also watches the
Orchestrator: if activity keeps arriving and the assessed cursor has not advanced for two check-in
intervals, it records `orchestrator_unresponsive`, blocks the Validator, and banners the incident.
Nothing stops the run: there is no run-wide stop file, the Orchestrator never kills a seat, and
no step waits on the founder (P-0001, 2026-10-18). The dispatcher answers a dialog a seat sits on
(a folder-trust or permission prompt) and records it; a usage limit is recorded and waited out.
The dispatcher samples a bounded last-60-line snapshot whenever a
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
model for each concrete chunk. Kindex-backed notes inform planning and narrow dispatch context,
not authority: `phase_compiler.py` and `run_guidance.py` never select guidance from Kindex. Only
the exact external-resume checkpoint can select a standard, loop, or recipe for compilation into
the three ratified authorities. Its effect set is exactly `{block, no-op}`: a block forces an
exact-subject Validator disposition once the Validator has fixed what it names; no-op grants
nothing and can satisfy no gate. Pre-dispatch and
pre-verdict checkpoints refuse until a retained assessment covers their activity cursor. The
assessment's observed harness status must match `harness.json`; it cannot call an open run closed.
For runs carrying `factory-run-guidance/1`, assessment/3 additionally binds the exact current
selection, application, evidence, and open-finding state re-derived by the host. The Orchestrator
reviews classifications, applicability bases, role/authority routing, and substantive evidence;
it may not rename `routing-verified` as compliance. Pre-dispatch requires routing and pre-verdict
requires evidence completion. A changed guidance artifact makes the assessment stale even if no
pane heuristic noticed the change.
Audit occurrence, cursor currency, exact-state binding, and a pending block are therefore
load-bearing deny conditions. The Orchestrator's prose and judgment remain advisory: they cannot
create the underlying routing/evidence state, satisfy another gate, or grant an advance.
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
retains an occurrence-specific question ID. Only the Validator can bind an answer or already
ratified specification to that ID; the Orchestrator can issue only a generated status probe. Both
travel to the exact retained thread, never through raw terminal input: an answer by `codex exec
resume` once the lane is idle (a lane mid-turn refuses it, exit 75), a status probe also by
`codex queue`, which is reported as not delivered and never receipted. Planned and delivered
records are separate, cross-lane answers and conflicting
second answers refuse, and neither author's work is projected into the other lane.

There is **no one-shot orchestrator mode**. `orchestrator_wake.sh` remains as a frozen-projection
audit: it runs only beside a resident Orchestrator, hands a sandboxed reviewer a closed projection
rather than a transcript, and is reproducible and separately receipted. It never substitutes for
the resident Orchestrator and refuses any run without one. Resident tmux coordination is
explicitly `operator-owned-tmux-unqualified`. Neither mode gives the model grant authority.
tmux pane capture is an inferred observation and never the retained response or an isolation
boundary.

For the frozen-projection audit, the executable projection is a closed nine-section document (trigger, task, phase snapshot,
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
dispatcher to inspect another path; missing context becomes a blocking question for the Validator.
Context cost then scales with exceptions rather than the run — the inverse of the
96-cold-loads bill. The projection, capsule, and exact final prompt bytes are retained; the
outcome receipt binds the prompt schema/assembler plus its byte count and SHA-256. This proves
what the agent received without claiming deterministic output or opaque provider-session replay.
Both seats hold zero grant authority:
manifests, registries, and the ledger are host-signed files they read and cannot write.
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

Ten controls, each mechanizing a specific postmortem lesson. The first six and the tenth
ship as scripts (see Scripts).

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

**5. Secret-exposure tripwire as an incident.** The ~40-key exposure was flagged four times as
a "watch" and never actioned. Credential-shaped content in any transcript or log is an incident
row (`incidents.jsonl`, file and line only, never the matched text) and a failed grounding, so the
next lane does not launch until the run scrubs the hit and re-grounds; rotating the exposed
credential is recorded as owed. An incident is not a queue entry, and it stops nothing else.

**6. Schedule registry and audit — durable timers vs leased wakeups.** No agent-authored
*durable* cadence survives its turn: OS timers live in the registry, and the
audit lists every live cron, launchd agent, `at` job, and user timer — anything
unregistered fails, and the kill path is tested, because the last stray ran eleven hours
past its kill order. Distinct from that: **leased in-objective wakeups** — the short
self-pokes that babysit a live deploy. Those are legitimate and necessary; they are bounded
by objective, count, and expiry, recorded in the runner's own lease table, auto-dead at
objective close, and never touch the OS scheduler. The lease is the grant; no wakeup waits
on anyone. Honest
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
(green-now gone red) is ruled on by the Validator and recorded, never silently reclassified; `SIDE_EFFECT_UNCERTAIN`
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

**10. Lane launch controls.** In the msg-r2 rebuild the Validator launched every lane with a
run-local script, so none of the harness's controls ran: a metered Tester model looped for
hours (about $1,200 of credit), finished lanes sat idle until someone looked, an author agent
sat on an interactive login screen for hours, and Tester briefs never carried the testing
standard (`docs/practices/lessons-msg-r2-2026-10.md`). Prose lessons had already said all of
it. So `harness/tmux_lane.sh` is the only lane launcher, and it enforces each lesson by
machine, before the agent starts and while it runs:

- **Agents.** `codex` (`codex exec`, non-interactive: the default author lane),
  `codex-interactive` (the Codex TUI with `--no-daemon`), `codex-ollama` (`codex exec --oss` on
  a model the local Ollama server already has) and `cursor-agent`
  (`agent -p [--model <model>] --force --trust --workspace <lane>`). The operator names the agent
  (`--agent`) and, optionally, the model (`--model`); the factory names no model. With none
  named, a `codex` lane carries over only the model keys of the operator's own Codex config
  (`model`, `model_provider`, `model_reasoning_effort`, ...; `lane_agent.py codex-model`),
  because it runs with `--ignore-user-config` to keep their MCP servers, sandbox and approvals
  out and would otherwise fall back to Codex's built-in model; `codex-interactive` and
  `cursor-agent` run their CLI's own configured default. Each launch row records the `model`,
  its `model_source` (`operator`, `codex-config` or `agent`) and the `model_config` overrides,
  and `tmux_lane_message.sh` resumes a lane on the same selection.
- **Dependency cache and runtime.** `--cache-env NAME=<dir>` (for example
  `DENO_DIR=<run>/dep-cache/deno-dir`) and `--runtime-dir <bin dir>` give the lane's own shell
  commands the pre-fetched store and a real runtime binary. Codex runs those commands under
  `shell_environment_policy.inherit="core"`, which drops every other variable, so the cache
  variables are set in the policy itself and the runtime directory goes first on PATH; both are
  recorded in the launch row (`lane_env`) and re-applied on every resume. Each directory must
  exist, have no symlink on its path or inside it, and hold no entry with a write bit, or the
  launch is refused.
- **Oracle inputs.** A Tester launch reads the Strategy's `oracle-input: <path> sha256:<digest>`
  lines (`harness/strategy_plan.py oracle-inputs`) and is refused when its tree lacks a cited
  input, reaches it through a linked directory, or holds bytes other than the pinned upstream
  digest. A Tester freeze reads its `planned-case: <id> [class]` and `test-modules: <glob>` lines
  (`strategy_plan.py traceability`) and is refused, leaving the lane open, while a Critical case
  id appears in no frozen test module outside a full-line comment; other gaps are recorded in the
  freeze row's `traceability_gaps`. That is a source-reference check only. Before Phase C,
  `discovery_check.py --candidate-sha <commit> --strategy` reads a `factory-test-inventory/1`
  document from `harness/test_inventory.py` (pytest, vitest or deno) in the integrated tree,
  which must be that commit with no change, and refuses while a Tester module has no runnable
  collected test or a Critical case id is in no runnable test's name. A skipped or ignored test
  (pytest `skip`/`skipif`/`xfail(run=False)`/`unittest.skip`, Deno `ignore`) is not runnable: it
  cannot fail. A green check writes `<run>/discovery/<slot>-<commit>.json`, bound to the commit,
  the Strategy's sha256 and the slot's latest freeze. `endgame.sh` refuses to render a verdict
  (`discovery_check.py require`) unless every Tester slot that froze has that receipt for the
  judged commit and the ratified Strategy's current bytes; no harness step starts Phase C
  itself, so endgame is the earliest mechanical point that holds the check.
- **Sign-in preflight.** `harness/lane_agent.py preflight` runs the agent's own status command
  (`codex login status`, `agent status --format json`) with stdin closed, a time bound, and the
  exact scrubbed environment the lane gets. Signed out, or a check that waits for input, is a
  refusal (exit 77) that prints the command that fixes it. Codex on Ollama has no ChatGPT
  sign-in; its model must already be on the Ollama server (`ollama show`). ollama.com sign-in
  for a `:cloud` model has no non-interactive status command, so it is not checked.
- **Spend caps.** A metered lane (`codex-ollama` always, or any launch the operator marks
  `--metered`) refuses to launch without `--spend-cap-usd` and
  `--usd-per-hour`. The cap is reserved in the run's objective-budget ledger
  (`budget-reservations.jsonl`, the same chain `dispatch_lane.sh` writes, through
  `harness/lane_budget.py`), so a run ignited without `--budget` cannot start a metered lane,
  and caps cannot add up past the budget. None of these agents exposes live spend, so **the
  enforced proxy is wall-clock time**: the watchdog stops the lane at `cap / rate` hours of
  active time, or at `--wall-cap-minutes` if that is sooner, and reports spend as
  `rate x active time`. A metered agent and model also needs a passing qualification probe for
  the role (below).
- **Progress watchdog.** Every launch starts `harness/lane_watchdog.py lane` in a
  `watch-<lane>` window; when it cannot start, the lane is stopped and the launch refused.
  After a read allowance (default 15 minutes) the lane must commit within the stall window
  (default 20 minutes), counted from the allowance's end or its last commit. A lane that does
  not is stopped and recorded (`refusal-lane-stall`); a lane at its cap likewise
  (`refusal-lane-cap`). A lane waiting on its own unanswered `FACTORY_QUESTION` is not stalled.
  When the lane prints `__LANE_DONE__ <slot> commits=<n>` or its process ends, the watcher wakes
  the operator: a `wake: true` row in `events.jsonl`, a row in `tmux-lanes/wake.jsonl` (tail it
  from the Validator's monitor), a `FACTORY_WAKE` line, and a tmux message. Commits come from the
  lane repository's reflog file, never from running git there. Each lane's end appends its
  commits, time to first commit, wall time, spend and commits per dollar to
  `tmux-lanes/lane-output.jsonl` (`lane_watchdog.py report --root <run root>`).
- **Native harness per model, plan billing, a private lane home.** Each model runs in its own
  vendor's harness (`harness/agents.py`): `claude` (Claude Code), `claude-ollama` (Claude Code on a
  local Ollama model such as GLM, as `ollama launch claude` runs it), `codex`, `agy`, `muse` (Muse
  Code) and `cursor-agent` (Grok and the other Cursor models; the effort is in the model name). A
  lane runs on the operator's signed-in plan unless launched `--metered` (founder direction
  2026-10-16): its env file admits no variable that would switch the harness to per-use API
  billing, its preflight proves the plan sign-in (`claude auth status` is `claude.ai`, `codex
  login status` is ChatGPT, `agent status`, `agy models`, Muse's stored sign-in with no
  `META_API_KEY`), and the lane wrapper stops a lane (`rc=78`) whose harness announces API billing
  in its first event. `agents.py lane-setup` builds the slot's agent home so the harness sees the
  lane's scoped Kindex (Kinbase read-only) and none of the operator's MCP servers, rules or hooks:
  `--mcp-config --strict-mcp-config --setting-sources project,local` for Claude Code, `--bare` for
  claude-ollama, an `XDG_CONFIG_HOME` for Muse, and for cursor-agent and agy a private `HOME`
  whose `Library` is the operator's (the keychain sign-in) and whose dot-files are the lane's.
  `--effort` sets the reasoning effort; `<slot>-turn.json` records how to resume the session.
  When `FACTORY_REQUIRE_SIGNET=1`, admission also requires `FACTORY_SIGNET_CONTRACT` and
  `FACTORY_SIGNET_DIGEST`. `signet_hooks.py` carries that exact installed-evaluator binding into
  Claude's explicit settings, Cursor's private hooks file, or Codex's explicit configuration
  overrides, including resume. It never copies the operator's other hooks or changes policy.
  Unsupported clients refuse a required binding. Missing/changed evaluator, invalid output and
  evaluator failure produce a denial; an ASK stays blocked on events that cannot enforce ASK.
  Cursor uses dedicated shell/MCP hooks so MCP server identity reaches Kindex's prerequisite
  rule without duplicate evaluation; its shell hook can still request a genuine scoped approval.
  **This is configuration plumbing, not runtime qualification.** Actual hook loading, Codex's
  hook trust and failure semantics, and scoped Kindex use still require client receipts. Codex
  can fail open when the hook process itself fails, so hooks alone are not a security boundary.
  Required Codex hooks now have a pre-model admission check: `codex_hook_admission.py` queries
  the installed client's read-only `hooks/list` API for the exact session-flag handler and
  refuses absent, disabled, modified or untrusted hooks. Launch and continuation check it;
  the headless turn wrapper checks again before starting either an initial or resumed turn.
  Timeout, malformed metadata and source errors refuse admission. No thread/model is started
  by this check, and no trust is granted or changed. Review the exact host-pinned hook through
  the client's supported `/hooks` flow when needed; configuration presence is insufficient.
  Metadata admission still requires a live denial probe before claiming provider acceptance.
- **Every resumable lane, not only Codex.** The wrapper records the session id the harness streams
  first (`--session-agent`, `<slot>-thread-id`), so the watcher's wrap-up and review pauses, and
  every lane message, resume a registry lane's own session (`--resume`, `--session-id` or
  `--conversation`). Only `codex-interactive` has no resumable session.
- **Wrap-up before the cap.** A lane is told its cap in its prompt, with the command that prints
  what is left (`lane_watchdog.py remaining --root <run root> --slot <slot>`). For a resumable
  lane the watcher interrupts the live turn `--wrap-up-minutes` (default 5) before
  the cap and resumes the thread with a generated `FACTORY_WRAP_UP` (`tmux_lane_message.sh <run>
  watchdog <slot> wrapup`), so the lane spends its last minutes leaving work that builds instead
  of being stopped mid-edit. The cap stop still follows. The watcher's environment is scrubbed, so
  the launch records, owner-only, the environment a lane message needs
  (`tmux-lanes/<slot>-delivery.env`) and the notice runs in its own session from it, its output in
  `<slot>-wrapup.log`; a notice that does not resume the lane within five minutes ends it as
  capped.
- **Progress reviews.** A resumable Coder launch pauses after every `--review-every-minutes`
  (default 18; `0` turns it off) of active work: the watcher ends the turn, keeps the pane, and
  wakes the Validator (`lane-review-due`). The Validator freezes the lane, reviews the frozen tree
  against the ratified specification and resumes the same thread with `tmux_lane_message.sh <run>
  validator <slot> review --verdict on-track|redirect|repair --review-file <file>`, a
  `FACTORY_REVIEW` that passes the coder leak guard. Paused time is not counted toward the cap; a
  review missing after 15 minutes wakes the Validator again (`lane-review-overdue`). The stall
  window carries across a pause instead of granting a fresh read allowance, and a new round
  launched over a paused lane closes it as `superseded`. The same `review --verdict repair`
  carries a finding after a judge into the Coder's own thread; a lane at its cap refuses it.
- **Qualification probe.** `tmux_lane.sh <run> <role> qualify --repo <clone> --prompt <brief>
  [--agent ... --model ...]` runs a small capped brief (always wall-capped, 30 minutes by
  default) and records time to first commit, commit count and a pass or fail
  (`--first-commit-minutes`, `--min-commits`) in the runs directory's
  `lane-qualifications.jsonl`. The fraction of the probe's new tests that are red at base for
  the named reason is the Validator's Phase C Q3 check on its commits.
- **Testing standard for every Tester.** A Tester lane's prompt opens with the bytes of
  `docs/standards/TESTING.md`, `prompts/test.md` and the run's `artifacts/testing-strategy.md`,
  each fenced with its sha256 (`harness/testing_standard.py`), as mandatory first reading. The
  qualified `dispatch_lane.sh` tester task opens with the first two; the ratified strategy
  reaches it through run-model (Gate B). A Tester launch whose standard cannot be read is
  refused.
- **One Coder and one Tester, each one long-lived session** (founder, 2026-10-20). New work,
  reviews and answers go into the role's session through `tmux_lane_message.sh`. `--instance` is
  refused at launch. `--round <tag>` is refused while the role has a session that can resume;
  `--replace-session '<why>'` replaces an unrecoverable one and records a `fresh-session` event.
  A plan lane takes no `--wall-cap-minutes`; a cap bounds metered spend only.

## Build compilation and convergence

The frame stays upstream of generation. The Validator derives it from the founder's words and
decides what they leave open (the founder may talk it through for as long as they choose, and
nothing waits on them), but author lanes do not begin until three distinct artifacts are
sufficiently deep, decided, and independently ratified:

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
not become a Tester-visible product behavior unless an observable behavior is separately ratified.
`not-applicable` is a visible ratified disposition, not omission. The generated state is called
`routing-verified`; substantive compliance needs observations bound to the exact candidate,
selection, application, obligation, and raw evidence plus independent judgment. The selected set
is immutable for one run; a different set requires a new external checkpoint and run.

Validation now includes a mandatory adversarial code review over the same immutable evidence, not
an optional instruction to "look carefully." Before the Validator runs, the host issues a closed
review subject binding the exact Stage-E execution-request bytes from the externally anchored
resume checkpoint, the frozen implementation/tests and snapshots, all three ratified phase
artifacts and their ordered review-item inventories, complete Git-object baseline (embedded, or by
commit and tree with a SHA-256 listing digest when the target sets `build.review_baseline =
"git-reference"`) and canonical candidate change set, build input,
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
signed authority uniquely supersedes the old same-phase behavior and a recorded ruling binds the
exact assertion (or frozen family) and exact signed replacement statement.
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

2. **Ongoing work, recorded as it happens.** Every consequential action is recorded as
   verb → object → environment (`deploy` → `staging`) with its class and its inverse in the
   side-effect register, then taken. No action waits in a veto window (P-0001). The record is
   itself a receipt, and the founder may reverse any action afterwards.

3. **Decisions the founder may override, and genuine impasses.** Every decision the run made
   for the founder, with its basis, and anything no role in the run knew how to do.

Every recorded action carries a class, and **the class is data** — resolved from the control
profile's criticality and reversibility, never assigned by the agent whose action it governs:
**reversible**, **recoverable**, or **irreversible**. An irreversible action is flagged in the
register with why it was taken and what recovering from it would take; it is still taken when
the run knows it needs doing. Oracle silence on a Critical surface is not an action class: it
blocks the verdict until the oracle is fixed.

A redirect from the founder at any time opens a provisional directive cited to their words
(control 1a), in force at once, rather than being absorbed as chat.

## Scripts

Layout: `harness/` for the scripts, `.factory/` for state (receipts, incidents, grounding
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
  repeats admission rather than becoming authority. Admission is a cursor over the receipts:
  every event with a `blocking_written` receipt needs its own `blocking_consumed` receipt, so
  emptying the file opens nothing, and a receipted event missing from the file is restored.
  `attention_gate.py reopen --root <run> --lane <lane> --event-digest <d> --reason <text>`
  reopens a consumed event as a new `blocking_reopened` event citing it, with its own receipt.
- `harness/consume_block.sh` — locks the exact pending subject, rejects malformed producer events,
  requires exactly one durable producer receipt plus a typed disposition, retains and hashes the
  run-owned evidence bytes, and fsyncs the receipt plus its parent before durably releasing the
  dispatch gate. Unrelated malformed legacy event rows are ignored and grant no authority.
  The subject is the whole file (one disposition for every pending event) or one event's
  canonical digest (that event alone is receipted and removed; the rest stay pending).
- `harness/lane_env.sh` — `env -i` from a manifest; refuses to run without a fresh grounding
  marker. It is a compatibility control; live Coder/Tester isolation is
  owned by `factory_runtime.runner_isolation`, not this script.
- `harness/receipt.sh` — wraps any command; exit code, log digest, tree SHA, dirty-state
  digest, flock-serialized per-worktree chain.
- `harness/tripwire.sh` — credential-shaped content (incl. GCP service-account JSON) is an
  incident row and a failed grounding; the run scrubs it and re-grounds.
- `harness/sched_audit.sh` — every OS timer must match the run's schedule registry.
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
  `FACTORY_VALIDATOR_AGENT=kickoff` makes the session that kicked off the run the Validator (the
  founder's default): no Validator agent is launched, `FACTORY_VALIDATOR_TRANSCRIPT` names that
  session's transcript, and the `validator` window runs `harness/validator_mirror.py`, which
  prints the transcript as it grows (so the dispatcher, its failure-mode checks and the
  Orchestrator see the Validator's real console) and appends every line typed into the window
  (dispatcher notices, Orchestrator corrections) to `<run>/validator/inbox.jsonl`, which the
  kickoff session watches. Its brief is `<run>/validator/BRIEF.md`. Ignition requires
  `FACTORY_VALIDATOR_PID`, `FACTORY_VALIDATOR_STARTED` (the normalized UTC `ps lstart` value),
  and `FACTORY_VALIDATOR_PANE` (exact `%id`) for the **executing** Validator. The supervisor
  verifies that PID/start belongs to the pane and persists an immutable per-run binding at
  `orchestrator/validator-process.json`; the mirror is never a substitute signal target.
  The bound Validator must be a worker child of its terminal shell/wrapper, not the tmux
  pane-root process: tmux automatically resumes stopped pane roots. Resident Validators and
  authors use the existing `lane_agent.py` wrapper's PID/start receipt; supervision pauses the
  verified worker tree while the wrapper waits. An Orchestrator stop or overdue assessment
  requests a pause of that worker tree. Acknowledgement requires repeated stopped-state
  observations over at least one second with unchanged CPU time. Terminal exit is distinct
  from resumable pause; an exited worker cannot be resumed.
  `seat-state.json` records requested holds; `seat-processes.json` records each signal attempt
  and observed acknowledgement. `status --root <run>` exposes both. Unknown observation,
  changed identity or failed delivery stays pending; a control command returns 75 when not
  acknowledged. A sent signal alone is not success. Resume requires recorded custody and
  confirmed process state; author holds survive until the Validator's resume is acknowledged.
  Author admission checks the binding and unresolved Validator holds. These checks identify
  processes, not their served models. Individual PID/start revalidation avoids process-group
  spill and known PID reuse, but POSIX signalling retains a check-to-signal race; observed-tree
  tracking cannot prove containment of children that detach between polls.
  `rebind-validator` is the host-only maintenance path for a client that must restart to load
  updated session hooks. It requires the exact prior identity/custody, observed exit of every
  tracked prior descendant, an already paused replacement tree in the declared pane, and a
  current accepted Orchestrator no-op disposition. Before moving the binding it durably retains
  the old binding/custody and handoff subject. Existing holds remain, and a new validation hold
  keeps the replacement paused until separately qualified. Interrupted persistence can be
  completed against that same immutable subject with fresh supervision. This operation never
  launches/resumes the client, clears an attention/policy block, or attests its model/account.
  An explicitly selected interactive Claude Validator is an opt-in, operator-equivalent,
  unsandboxed process: it is not a qualified lane and contributes no filesystem-isolation
  evidence. Codex is the default; Ollama-launched Codex is the supported alternate.
  An Ollama-backed Validator or tmux lane takes its model only from the operator
  (`FACTORY_VALIDATOR_OLLAMA_MODEL`, `FACTORY_LANE_OLLAMA_MODEL`) and is refused unless that
  model is already available (`harness/model_availability.sh`); the factory never picks,
  pulls or installs a model.
  Any other seat's model is optional and named by the operator at run time:
  `FACTORY_VALIDATOR_MODEL` and `FACTORY_ORCHESTRATOR_MODEL` pass `--model` to a Codex or Claude
  Validator and a Codex Orchestrator (agy takes no model flag; select its model inside agy), and
  `tmux_lane.sh --model` names a lane's. Unset, each agent runs its own configured default. The
  qualified dispatch path takes its model from the runner manifest.
- `harness/policy_custody.py` — optional host-bound Signet evaluation in the existing
  `lane_custody.py` service. The admitted seat sends tool metadata for evaluation, never
  execution. The host pins the client, evaluator contract, implementation hashes and run.
  A non-prerequisite denial persists a hold before returning; a failed evaluation leaves an
  uncertain hold. Subsequent tool requests and fixed custody operations cannot proceed by
  changing spelling, tool or request ID. The journal contains identity, digests and outcomes,
  not tool inputs. A prerequisite exception must name an exact owner policy revision and
  recovery reason: the original DENY remains, and the real recovery call still goes through
  Signet. This is not a Kindex success marker or an exemption from other checks.
  Only the host's `lane_custody.py release-policy` command can release a named hold against
  a current accepted Orchestrator no-op assessment covering its incident notice, with no
  unresolved Validator attention block. A cadence no-op cannot override a prior block. There is no
  release socket operation. A missing notice after a crash is recorded first and requires a
  subsequent assessment; release never silently treats the crash as success. Old receipts
  remain after release, and every later request is evaluated again.
  Activation is explicit: a protected custody plan's `policy` binding and the session hook's
  `--custody-socket` / `--custody-digest` must agree. Existing stateless hooks do not acquire
  this protection merely because code is present. A missing or invalid endpoint blocks,
  without falling back to local evaluation. Host receipt/control paths must be outside the
  seat's writable boundary and actual provider hook loading must be qualified separately.
  This guards calls reaching that hook, not unobserved provider refusals, already executing
  actions, model text generation or an unconfined alternate executor. Actual process pause
  remains the supervisor's separate acknowledged control.
  Claude's native modules can reject `classic.PreToolUse` without invoking ordinary settings
  hooks. A settings-only custody hook therefore cannot retain those denials. The optional
  `harness/claude_custody.py` generator prepares a session-local native module with an explicit
  dependency on the existing native Signet owner, placing custody before that owner. It uses
  the same pinned custody endpoint and adapter; it neither edits nor replaces the installed
  owner. An allow still invokes downstream policy, host checks and redaction. Ask/deny and
  adapter failures do not invoke the next handler. The generator refuses to overwrite an
  existing plugin directory and does not activate anything. Host-protect the generated files
  and qualify actual dependency ordering in the installed client, including a real denial
  followed by a different tool, before claiming coverage. Earlier built-in/managed guards
  can still reject before this module: their outcomes are not thereby custody-observed.
  See [Claude module ordering](https://code.claude.com/docs/en/plugins/mods/events#the-order-mods-run-in)
  and [settings-hook placement](https://code.claude.com/docs/en/plugins/mods/events#where-settings-hooks-run-in-the-order).
- `harness/dispatch_lane.sh` — re-derives target-state; under the shared attention lock checks both
  applicable blockers and acquires a crash-released role mutex as one admission ordering point;
  durably freezes or exact-reuses caller dispatch bytes; mints the declared asymmetric projection;
  verifies/appends the dispatch chain; requires a role-specific Kindex primer and externally
  checkpoint-bound structural qualification report; then invokes the qualified model runner and
  typed broker. A failed invocation crosses this boundary only through the closed verified failure
  receipt and retained exact evidence: canonical qualification, every presented prompt, private
  primary/child executable snapshots, state capsule, and bounded diagnostic. No failed canary
  reaches the broker. Ambient gap flags cannot bypass either precondition.
  It reserves the runner's cost ceiling against the objective budget through
  `harness/lane_budget.py` (the ledger's one writer), opens a Tester task with the testing
  standard (control 10), and launches what the role's runner manifest declares: `codex` and
  `ollama` keep their legacy adapters, and any other `--agent` must equal a manifest adapter that
  declares its own invocation. Its runner carries its own credential through named secrets, so
  the tmux sign-in preflight does not apply to it.
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
  semantic residue. These are asymmetric disagreement detectors, not two independent redundant
  implementations: they prove the relational oracle catches either participant drifting while the
  other remains correct. Shared same-direction wrong behavior can still pass and remains the job
  of an independent semantic oracle and adversarial review. Older runs without the ignition field
  keep their released semantics.
- `harness/tmux_lane.sh` + `lane_agent.py` + `lane_watchdog.py` + `lane_budget.py` +
  `testing_standard.py` + `codex_lane_session.py` + `lane_dialogue.py` +
  `tmux_lane_message.sh` + `factory_runtime/lane_repository.py` — unqualified authoring/dogfood
  mode, and the only lane launcher (control 10 states its launch controls):

  ```
  harness/tmux_lane.sh <run> <coder|tester> launch --repo <own clone> --prompt <brief>
      [--agent codex|codex-interactive|codex-ollama|cursor-agent] [--model <m>]
      [--metered --spend-cap-usd <usd> --usd-per-hour <usd> [--wall-cap-minutes <m>]]
      [--round <tag> --replace-session <why the session cannot resume>]
      [--cache-env <NAME=read-only dir>]... [--runtime-dir <read-only dir>]...
      [--read-allowance-minutes 15] [--stall-window-minutes 20]
  harness/tmux_lane.sh <run> <coder|tester> qualify ... [--first-commit-minutes 15] [--min-commits 1]
  harness/tmux_lane.sh <run> <coder|tester> freeze [--instance <name>] [--round <tag>]
  harness/tmux_lane_message.sh <run> <validator|orchestrator> <slot> status|answer|idle ...
  harness/lane_watchdog.py report --root <run root>
  ```

  A slot is one session of one role (`coder`, `tester`; `coder.r2` only after a recorded
  replacement); every slot has its own
  prompt, launch journal, thread, log (`<slot>-lane.log`), exits journal and watcher state under
  `tmux-lanes/`. Every lane process runs under `lane_agent.py run`, which tees its output to the
  lane log and records its end. Only `codex` and `codex-ollama` lanes keep a resumable thread
  for `tmux_lane_message.sh`; a resume starts a new life of the lane's watcher. `idle` reports
  whether a slot has no live turn (window gone or pane dead: exit 0) or is mid-turn (exit 75),
  reading the lane window by exact name; freeze reads it the same way.
  An answer over 12,000 bytes (up to 256 KiB) is delivered as a read-only file
  at `tmux-lanes/<slot>-inputs/<sha256>.txt` with a `FACTORY_RELAY file=… sha256=…` pointer;
  the coder leak guard judges the file's bytes before the file is placed. These answer files, and
  the `<run>/relay/<sha256>.txt` files `relay_file.sh` and `inject.sh` write for multi-line or
  oversized messages, sit under the run root, which a tmux lane is not denied reading: tmux lanes
  claim no read isolation for them. The guards judge what is delivered; nothing stops one lane
  from reading another lane's relayed text if it goes looking.
  Preflight requires a standalone repository whose Git/common directory is local and an agent
  CLI exposing the exact flags the launch uses; the retained launch records its version.
  Codex is launched as the agent harness with a lane-scoped permission profile that reopens the
  whole `.git` directory inside that standalone repository. The author owns that local branch and
  history; Factory claims repository isolation, not per-ref Git ACLs. Before each checkpoint the
  lane inspects its own status/diff, runs
  relevant checks, and commits only its assigned work. The JSON event wrapper retains the real
  thread ID and records an exact `FACTORY_QUESTION` only from a completed assistant-message event,
  so question blocking does not depend on lossy pane sampling and typed status probes or answers
  can queue or resume that conversation. From agent start onward
  the whole repository—including Git hooks and config—is untrusted and host Git is forbidden.
  Where the harness must still read a commit from such a repository (endgame's candidate, the
  agreement probe), it goes through `harness/host_git.py` (`factory_runtime/host_git.py`, gate
  HGT): a closed environment whose `GIT_CONFIG_COUNT` overrides outrank the repository's config
  (hooks, fsmonitor and every transport off), no replace objects, and a tree written from raw
  objects, so no planted filter, attribute or replace ref changes what is judged.
  After the pane is dead, a descriptor-relative bounded walk excludes root `.git`, rejects links,
  special entries, nested Git metadata, portable-name collisions, privileged modes and ceiling
  violations, then publishes a content-addressed regular-file snapshot. Commits are useful author
  checkpoints, never promotion evidence.
  Each lane needs its own repository copy; a launch on a copy another lane already uses (or one
  nested in it) is refused. `harness/lane_kindex.py` gives the lane `kindex-lite --repo <copy>`
  (Kindex >= 0.48.0; SOFTWARE-FACTORY §6, Memory is scoped by role), bound to the copy's
  `.kin/local/kindex` store. Before launch, under a private home in
  `tmux-lanes/<slot>-kindex-home/`, the store is loaded from approved inputs only: the copy's
  `.kin/knowledge.jsonl` (or `.json`), the run's shared `lane-seed.jsonl` (each node tagged
  `lane-seed`, so `list_nodes(tags="lane-seed")` returns it), and the copy's `.kin/events`
  Kinbase evidence (verified with `kin kinbase sync --mode raw`, no Kinbase binary). A copy that
  arrives with its own `.kin/local` store is refused. The launch is then refused, never
  downgraded, unless the lane's own server lists `kinbase_sync` and `kinbase_explain`, lacks
  `kinbase_submit` and `kinbase_status`, and its `scope_info` names this copy with submissions
  disallowed. The server's PATH holds no `kinbase`, so live Kinbase lookups stay off. Both lanes
  must load the same seed. A failed attempt removes what it created; a lane's home is bound only
  after a successful launch, and a relaunch keeps its store. The launch row records the server,
  version, tools, store, digests and Kinbase counts, and a resumed lane gets the same server. The
  freeze leaves `.kin/local` and `.kin/events` out of the snapshot. The scoped server reaches
  Codex lanes through their `-c mcp_servers.kindex=...` override; `cursor-agent` takes no such
  flag, so a Cursor lane runs without the scoped Kindex and its brief must carry what it needs.
- `harness/orchestrator_wake.sh` — verifies external resume, freezes a closed bounded exception
  projection plus capsule, and runs a sandboxed frozen-projection audit in a fresh empty directory.
  It refuses any run without a resident Orchestrator and never substitutes for one.
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

The active scripts live in `$FACTORY_HOME/harness/`; tests and the denial-probe registry are the
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
   order: the staged, recorded decision (approve / request changes / abandon) before merge,
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


### Jev runtime observations

`factory.sh --jev-screen-calls N` opts this run into the bounded semantic screen described in
[the runtime guide](practices/jev-runtime.md). The dispatcher records a receipt for each activity
cursor and assessment/4 requires consumption. It never replaces the resident Orchestrator or
changes the deterministic gates. Without selection, `harness.json` records `jev_screen: null`.

Every tmux Tester launch and qualification now requires `artifacts/testing-strategy.md`; missing
or unreadable strategy bytes refuse launch. Injection proves delivery; applied T-rules and
behavioral evidence in the lane report are still reviewed by the Validator and Orchestrator.
