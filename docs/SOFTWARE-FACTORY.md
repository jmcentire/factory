# Software Factory

Factory coordinates a task with a Validator, a resident strategic Orchestrator, and independent Coder and Tester conversations. The user chooses the task, repository and agents/models. The Validator owns acceptance. A model-family label is descriptive; Factory does not approve models.

Start with:

```sh
harness/factory.sh my-task 'Describe the required behavior' --repo /absolute/repository
```

A task file may replace the task text. `--runs` chooses the record directory, `--log` chooses optional human-readable logging, and `--budget` provides a ceiling for explicitly metered author lanes. The default logging destination is `/dev/null`. Required task inputs, messages, author output and validation results are always retained.

`FACTORY_VALIDATOR_AGENT`, `FACTORY_ORCHESTRATOR_AGENT`, `FACTORY_CODER_AGENT` and `FACTORY_TESTER_AGENT` select CLI adapters. Corresponding `_MODEL` and `_EFFORT` values preserve explicit choices; otherwise local role defaults in `~/.config/factory/config.toml` (or `FACTORY_CONFIG`), shipped role defaults, and the selected CLI's own configuration/defaults apply in that order. There is no model approval list. Adding a new CLI requires an adapter for its actual protocol, not permission to use a model. `FACTORY_VALIDATOR_AGENT=kickoff` uses the existing initiating conversation as Validator.

Native permission mode follows the user's choice. `FACTORY_LANE_SANDBOX=off` selects each supported native CLI's yolo/skip-permissions mode for coordinators and Codex authors; Cursor authors also receive explicit sandbox disablement. It does not grant external platform permission. Codex launch and resume use either the bypass flag or the approval flag, never both. Preserve productive sessions when changing configuration; do not restart a working author merely to normalize a setting.

The record pins the starting commit without changing the user's checkout. No catalog, signing key, human receipt, phase ratification, external checkpoint, boundary register or prior model trial is needed. The historical signed runtime and pattern-generation libraries are separate archival/research interfaces, not prerequisites or an alternate authority for normal tasks. Existing historical run records are preserved and are not silently converted or restarted.

The Validator gives Coder and Tester separate asymmetric input projections and behavioral briefs, preserving independent inputs. The Tester ordinarily authors tests, including development and regression coverage. The Coder may write a test only for an absolutely critical stable structure or invariant, with the explicit explanation and warning required by TESTING T3; ordinary development checks do not qualify. A clean clone alone does not protect the Tester from implementation or the Coder from independent test details. Preserve the provenance of inherited Coder-authored tests and assess their disposition rather than counting them as independent acceptance or silently deleting them. Needed independent coverage comes from source-free behavioral findings, not copying those tests into the Tester's input. Both use the assigned model and stay in their native conversations until the task completes. Claude Code, Cursor, Muse, Antigravity and interactive Codex author lanes are each the provider's own interactive session, hosted on a terminal the lane wrapper owns; none uses a print or headless mode (`codex` and `codex-ollama` lanes still run `codex exec`, and say so at launch). What reaches a running lane after its first message differs by provider and is never guessed: a later message is typed into Claude Code and Cursor and given to interactive Codex through its own `codex queue`, each counted as delivered only on the session's own report; Muse and Antigravity lanes take no later message automatically, so a review, an answer or a wrap-up notice is recorded as queued and held, and a person enters it in the lane's window. Delivery checks the actual retained process and session; it never substitutes a terminal pane or changes models. `docs/HARNESS.md` states, per provider, what was run and what is still open.

An interactive Codex lane is never continued through `codex exec`. The earlier hand-off, `--native-session-file` on the typed message command, is removed: the command refuses it by name, and it refuses a lane that an earlier Factory started with nothing hosting it. A hosted interactive Codex lane takes later messages through Codex's own `codex queue` while its session runs. Once that session has ended, the next lane message restarts it on the same conversation with Codex's own `codex resume`, that message given as the resume's launch argument and recorded before the program exists, so it is never given twice; later messages go through the queue again. Only a Codex TUI that an earlier Factory started with nothing hosting it is not continued by the Factory: `codex resume <session id>` in a window of the operator's continues that conversation, or `tmux_lane.sh launch --replace-session '<why>'` starts a new hosted session. Report which happened.

The Orchestrator continuously watches all roles, owns strategic oversight and process adherence, and may block a concrete action with evidence and a needed correction. The Validator owns product acceptance and addresses those findings. There is no cadence dispatcher typing into terminals and no supervisor suspending productive roles for review lag. Reviews happen when useful; elapsed time and journal backlog are not approvals.

Validation uses the actual project's checks against an immutable candidate:

```sh
harness/endgame.sh my-task EXACT_COMMIT --repo /candidate/clone -- python -m pytest
harness/status.sh my-task
```

The Validator integrates independently authored tests before invoking the checks and inspects their actual assertions under TESTING T4, T14–T17 and T36–T40. Internal-document/comment phrase checks, redundant fixtures and tests of doubles are rejected as evidence. Documents and comments receive semantic review; a real parser or shipped-manifest contract is a separate, limited claim. The harness materializes exact Git objects without executing repository hooks, filters or replacement objects, runs the supplied command, and retains its output and exit code. It does not equate tests passing with product acceptance or publish the result. Each retained result explicitly leaves acceptance unevaluated and is queued to the existing Orchestrator inbox with its exact candidate and output location. The Orchestrator inspects the evidence and raises concrete findings; a queued message is not a review. The Validator addresses those findings and reports behavior, observed negative and positive controls, gaps, residual risk and the candidate revision. No literal-prose gate, new approval artifact or test-count target substitutes for this work.

Use `python -m factory_runtime.kindex_workspace sync --source SOURCE --destination SCRATCH
--delete --exclude .git --exclude .factory` when refreshing a mutable transient candidate.
This creates missing `.kindex-no-index` metadata before copying and preserves existing markers
through deletion-enabled sync. Do not substitute raw `rsync --delete`, `git clean -ffdx`, or
whole-root deletion: each can remove the only exclusion marker. Keep any required whole-tree
replacement inside a separately marked, Factory-owned ancestor that the replacement cannot
delete. Never mark a shared parent or canonical checkout to achieve that containment.
The sync command is only for transient destinations, not publication into canonical source.
Accepted work still crosses through the lane export, which omits runtime markers.

All coordination text is data. `inject.sh` now queues coordination messages in durable inboxes; author communication uses `tmux_lane_message.sh`. A queued message is not an acknowledgment. Optional log suppression never discards decisions, blockers or handoffs. Each run uses a private tmux socket, and operations address that socket explicitly.

Every role uses relevant Kindex retrieval before broad scans and at material decisions. Current user instructions govern. Credential handling, platform permissions, selected billing mode, independent author inputs, safe host Git and exact session/process targeting remain real boundaries. There are no Factory-created human signoff ceremonies.

## Decisions that remain necessary

The goal is clean, extensible, local, durable, maintainable, reliable software. Four roles divide decisions rather than adding four approvals:

| Role | Owns | Communication | Must not do |
|---|---|---|---|
| Validator | Interpret the requested capability, coordinate work, integrate evidence and accept or reject the result. | User, Orchestrator, Coder, Tester | Write the implementation or independent oracle in place of its authors; move expected behavior to fit a candidate. |
| Orchestrator | Resident strategic oversight across all activity; identify drift, missing work and process failures. | User, Validator; typed author status probes | Become an implementation author, change assigned models, or invent user approval requirements. |
| Coder | Implementation against the task and public contract. | Validator only | Read the independent oracle, negotiate with the Tester, or accept its own work. |
| Tester | Independently derive and implement checks from the task and public contract. | Validator only | Inspect Coder implementation to manufacture expected results or weaken checks to fit it. |

Preserve three distinct concerns: the requested capability, architecture guidance, and testing/monitoring strategy. Their size follows the task; they may be concise sections in a brief. They are not three mandatory ceremonies. The current user request is authoritative intent. The Validator records consequential interpretations and resolves routine ambiguity; real uncertainty about intent remains visible.

Plans and optional recipes describe implementation mechanisms; they cannot add requirements or change expected behavior. The Tester never receives the Coder's construction plan, recipe selection or implementation. The writer of a fix does not control the judge, cannot negotiate the verdict, and cannot talk to the Tester.

Input separation and model diversity address different failure modes. Filtered input prevents direct implementation/oracle leakage. Different model families can reduce correlated misreading; this is a reason to prefer diversity when assignments permit, not a model-name approval system. Explicit assignments govern. Even with separated authors, the Validator helped interpret the specification and then judges against it; independent tests and Orchestrator oversight reduce that limitation but do not eliminate it. The existing design evidence is recorded in `docs/DOCTRINE-KERNEL.md` E1/E3 and Kindex; this cleanup does not claim a new empirical result.

Before first author launch, the Validator uses `harness/role_inputs.py prepare` to select actual files. The Coder receives source minus the declared tests; the Tester receives only selected interfaces/specification, or an empty repository with its behavioral brief. Each tree has a fresh Git history. The launcher checks the prepared tree before handoff. The Validator must inspect semantic content: neither filenames nor a digest can prove a document contains no leaked implementation. Curated shared Kindex context is copied into private lane stores; the live author graphs are not a shared communication channel.

The resident Orchestrator uses `harness/dispatcher.py --observe` to read retained output, launches, exits and coordinator inboxes. It supplies the previous byte offsets to read the next chunks, then waits for new activity. Its own conversation already contains its own activity. This replaces terminal injection and automatic backlog suspension with observation and concrete decisions. A log append or successful read is not an acknowledgment or completed review.

## Regular quality check-ins

The Validator and Orchestrator each review both code quality and test quality throughout the work: at the initial approach, the first useful increment, later material increments or risk changes, and each correction round. Do not wait for final tests or completion to discover an approach is wrong. Inspect actual code, assertions and observed outcomes as they become available, including affected boundaries outside the diff. Keep useful author work moving while reviewing. Maintain Coder/Tester independence when relaying findings.

Review architecture for local, explicit ownership and reasoning; cohesive modules; necessary extension seams; and code that is understandable and maintainable. Check debugging, logging and telemetry against concrete failure and operating needs: can someone diagnose the failure, correlate relevant work and see its disposition without leaking secrets or creating noisy, unbounded output? Use the applicable architecture/code/testing standards and existing repository conventions. A small local utility does not automatically need services, abstractions, remote telemetry or an observability platform. Prefer the smallest sound design and useful existing instrumentation.

Guide with a concrete consequence, evidence and the smallest useful correction. Distinguish necessary corrections from optional improvements and inapplicable guidance. Allow a scoped exclusion with its reason and residual risk in the existing brief or review; no new exception form is needed. Ask the user when an exclusion changes required behavior, creates a consequential tradeoff or exceeds delegated authority. An exclusion cannot silently waive a real security boundary, known damaging defect or required evidence.

The Validator checks the value of every proposed next correction: what improved, what remains, and what another round is likely to buy. Drop an unproductive or nitpicky argument immediately, record the reason briefly, and move on; do not spend three or four rounds debating it. Neither role reopens a resolved point without materially new evidence. The Orchestrator must challenge the Validator's prioritization too, not merely confirm it.

For an issue still driving useful corrections or disputed insistence, reassess together after three meaningful feedback/correction/review rounds and escalate to the user no later than the fourth unresolved round. A round is a substantive attempted correction and its review, not each test invocation, message, or newly numbered sub-iteration. Escalation does not require agreement that the issue matters: the Validator may be wrong about its importance. Give the user what was identified and corrected, the remaining evidence and both positions, the cost of another iteration, and a recommendation with bounded options. Use the initiating conversation or an authorized user-facing channel; a local queued question is not user delivery. Continue unaffected work, but do not keep cycling the disputed issue, declare missing evidence passed, or weaken a valid invariant to avoid escalation. <!-- gate-exempt: The user explicitly requested bounded correction-loop escalation on 2026-11-06, including disputed importance; this is not a new approval prerequisite or run-wide stop. -->

## Evidence and consequences

Keep one authoritative owner per fact, least privilege, durable evidence, visible failure and honest reports. Fail closed on real hazards: unknown permission, secret handling, unsafe filesystem/Git behavior and wrong process identity. Do not turn an inconclusive formatting or progress heuristic into authority to stop the entire run.

Retain eight principles: fail closed on hazards; one authoritative owner per fact; least privilege; auditability; no silent failure; honest documentation and reports; provenance of intent; and observed verification rather than self-attestation. A forbidden capability must be unavailable to the agent, not merely discouraged in a prompt. Actual platform permissions govern tool use; Factory cannot override an external denial. Honor already granted authority for ordinary required work, including trust of the authorized lane folder, without asking again. Startup records Claude Code folder trust for the run's repository and run folder before a Claude Code coordinator launches.

Classify risk by behavior and reachable side effects. Critical behavior needs adequate independent coverage before acceptance; an unresolved critical coverage gap blocks acceptance of that behavior. Standard and cosmetic work can use proportionate checks, with limitations reported. Unknown consequences deserve investigation and conservative treatment, not an invented waiver. Oracle adequacy and consequence severity are separate questions: a passing weak oracle does not establish safety.

| Criticality | Consequence | Evidence and acceptance |
|---|---|---|
| Critical | Security, privacy, money, irreversible state or externally consequential side effects. Unclassified behavior is treated as critical until examined. | Adequate independent coverage is required. No waiver can substitute for missing critical evidence. |
| Standard | Recoverable functional failures with understood consequences. | Run relevant behavioral and regression checks; report unresolved limitations. |
| Cosmetic | Presentation-only defects with no functional or externally consequential side effects. | Use proportionate checks and report limitations. |

The classification includes the surfaces reached by side effects. The Validator assigns it per component and reachable surface. An acceptance claim needs cited evidence; an unchecked item remains a visible gap. Review the exact frozen candidate, not a moving working tree.

For a repair, reproduce the defect in a disposable environment when possible and retain the failing result. Test the intended success path, the reported failure, and meaningful regressions. A test that was already green does not prove that it catches the defect. If reproduction is unavailable, state what remains unproven. Tests should derive from expected behavior, not mirror implementation branches. Critical deterministic guarantees need deterministic evidence; repeating a flaky check until green is not qualification.

The negative control demonstrates the defect on the broken baseline; the positive control protects behavior that already works. A previously passing control that fails after a change is a regression to investigate, not evidence that the new test forced the fix. Suspected over-constraint is a concrete contract question for the Validator. Preserve the original failure and answer rather than silently adjusting expected results.

When an existing test fails, the Validator determines whether the candidate violates its intended contract, the baseline was already broken, or the test asserts obsolete behavior. Preserve the original failure and the reason for any changed expectation. The author may not silently weaken the test to claim success. Monitoring should observe specification-derived behavior and consequences, not merely the lines that changed.

For software with operational monitors, each monitor must be understandable and traceable to the criterion it watches. An unresolved criterion is a coverage/authority defect; monitor count is diagnostic, not a target. Generated monitors may cover any criticality class. Record who or what actually authored them; never label agent output as human-authored or require human authorship as a gate. An alert-triage agent must not delete or weaken the monitor that raised its alert. Retain monitor state outside the agent's transient context. Detect relevant events, notify selectively, and ensure each notification communicates a meaningful finding or requested action.

Acceptance cites the actual candidate and executed evidence, distinguishes untested claims from observed behavior, and names residual risk. Retained commits, logs and results make local work recoverable; they are not substitutes for judgment or platform authorization. Publication, installation and external side effects remain subject to the user's actual scope and platform permissions.

Retain the recoverable subjects as well as their hashes. Record each role's assigned and observed model/client version, distinguishing unknown values and configured choices from live observations. Record the actual independence arrangement and its limits: a different name is not necessarily a different family, and separate conversations are not necessarily separate inputs. Keep transient coordination out of product commits; durable decisions and evidence remain in the local run record.

This document specifies the intended simplified workflow. It does not assert that every implementation path or native provider is qualified. Current cleanup evidence and unfinished work are in `docs/DIRECT-CLEANUP-DECISIONS.md`.

## Defaults, forward progress and optional output

The user sets models; configuration supplies defaults; shipped configuration supplies the defaults beneath those. A confirmed retired default requires an actionable user question with a suggested current replacement verified against the provider's available models or retirement guidance. Do not infer retirement from quota, authentication, an unknown family or a generic failure; never silently change an assigned model. Continue independent work while that decision is pending.

Create a catalog only when the particular operation needs one, and only then. Optional plans, historical checkpoints and unread events do not delay ordinary work. Act on recoverable problems autonomously. Ask for optional inputs when useful, require an input only when the next action actually cannot work without it, and let the user control the flow.

For readable output, set `--log /absolute/path/status.jsonl` and ask your LLM to summarize that file or explain a particular event. The optional file contains structured records, not a polished narrative. Its default is `/dev/null`; required decisions and messages remain in the run inboxes. No extra log-processing service is required.

Herald is optional outward reporting, not the transport between Factory roles and not a startup prerequisite. `harness/escalate.py` retains run status, attention items, questions and heartbeat results under the run's `orchestrator/` directory. `FACTORY_HERALD=auto` (default) also reports through the existing Herald MCP connection when `HERALD_FACTORY_TOKEN` or `~/.harness/secrets/HERALD_FACTORY_TOKEN` is configured. `HERALD_URL` selects its base URL (default `https://dash.herald.tools`). `FACTORY_HERALD=off` never reads that credential or contacts Herald; it does not remove the user's connector or credentials.

Missing credentials, disabled reporting and a remote delivery failure do not stop Factory. Each result and local record explicitly says `delivered: false` and why (`not-configured`, `disabled`, `failed` or a local-only resolution). Remote success alone says `delivered: true`. Reporting degradation returns success for the local operation, not proof of human receipt. Questions and cited answers still remain in the local ledger, and answered questions are not sent again. With Herald off, use the initiating conversation and run records for human-facing questions; no remote stale-agent alert is promised. The reporter neither issues credentials nor retries failed remote delivery automatically. Invalid input/configuration and a local record-write failure remain errors.


## Release-check choices

User direction for signatures: “no signatures that aren't automated verified by what the user actually said”. No manual signature or repeated authorization ceremony is required. If an automated attestation is used, retain the actual user instruction and verify that the claimed action and scope follow it; a signature or content hash alone is not proof of permission. This is a constraint on optional attestations, not a requirement to create them.

The human-gate wording check is a heuristic. Tune it with `make HUMAN_GATES_ARGS='--mode warn --max-findings 5' check-human-gates`, `--mode off`, or repeatable `--exclude` repository-relative path patterns. The report distinguishes warnings and disabled checks from a clean scan. Existing local `gate-exempt` explanations remain available.

The authority check supports explicit user overrides, for example `make AUTHORITY_ARGS='--allow-symbol TesseraSeal --allow-module exemplar' check-authority`. Each overridden finding is reported; other findings and unreadable/invalid source still fail. These options change a local release lint, not platform permissions, and do not validate an unsafe helper. Agents use overrides only within the user's actual direction.

The testing standard is supplied to Tester lanes; instruction delivery is not proof of compliance. The former literal-prose `check-doctrine` gate and its tests were removed: they established only the behavior of their lint rules, not that agents follow instructions or that runtime controls work. Never rebuild them as behavioral acceptance. Validator and Orchestrator own semantic review of the actual evidence.

Denial tests must exercise the prohibited action and its absence, including a positive control for valid work; a collecting test or a matching string is not proof of behavior. Use actual baseline failures or bounded mutation checks where they establish that the test detects the regression. Acceptance-ledger and glossary checks remain enabled.

### Durable coordinator wake delivery

A Claude coordinator seat is the operator's own interactive Claude Code session, started
through their launcher on their subscription and shown in its tmux window; it is never print
mode and never an API key (`docs/HARNESS.md`, "Claude Code is the operator's own interactive
session"). Inbox batches are typed into that session and count as delivered only when the
session itself reports receiving them. An Antigravity coordinator seat is Antigravity's own
interactive session in its tmux window, never print mode and never a JSON stream; nothing
delivers an inbox batch or a message into it, because Antigravity has no supported route for
that yet. A wake for it is reported `unsupported` and stays in the inbox, and the seat is worked
by a person in its window: attended, not unattended. `inject.sh RUN validator|orchestrator
"message"` retains operator input in the seat's inbox; it reaches a Claude Code seat as above
and waits there for an Antigravity seat. This retains one native conversation without
introducing another AI orchestrator. Existing runs are not converted or restarted. Other adapters
retain their current terminal behavior and report automatic inbox wake as unsupported; the worker
never guesses a safe terminal prompt or substitutes an agent.

A deterministic `python harness/inbox_wake.py run --root RUN_ROOT` worker starts with new runs. It drains retained
coordinator inboxes and author exit records, including completions written before it started.
A durable batch ID survives retries and worker restarts. Busy native sessions are left alone;
only their acknowledged input boundary can accept another batch. An acknowledgment proves
native receipt, not completion, validation or approval. Pending/uncertain writes retain their
same ID; they are not replayed as new requests. Native process/session identity is checked on
all delivery attempts. The worker does not restart a dead seat.

Use `python harness/inbox_wake.py status --root RUN_ROOT` for delivery state and
`python harness/inbox_wake.py stop --root RUN_ROOT` when
the run is complete. The run's tmux session also owns the worker lifetime. Missing or dead
recipients remain visible, never recorded as delivered. Startup and periodic drain use retained
records rather than waiting only for a future file change. Role routing and independent author
inputs are unchanged: this worker delivers to coordinators, not directly across author lanes.

Native permission denials remain visible in the retained agent output; an idle/completed native
turn is not successful task completion. This transport does not provide an interactive tool-approval
response UI and never answers permission requests itself. Resolve permission blockers through the
operator's existing authorized controls, not by silently widening an agent's permissions.

## Storage at task completion

Before declaring the working environment tidy, inspect `factory cleanup --runs ROOT`.
Record retained evidence and referenced external lanes, distinguish active work from
completed work, and surface the explicit archive plan. Stop only exact finished
processes. Within operator authorization, `factory cleanup --runs ROOT --run RUN
--archive --completed --apply` preserves a verified compressed copy before removing
that selected run directory. It does not accept the task, stop agents or clean external
lanes automatically. Keep archives and receipts together and verify restore when
retention matters. Unknown ownership, live processes and linked Git stores refuse.

Use `factory version --json` with the new run's retained `factory_version` when
investigating an issue. Current installation identity is not proof of the version
that produced an older run. See README's storage section for limits and restoration.

### Builder attribution on generated artifacts

Run creation retains the builder's name/version/revision/dirty state in `harness.json` under
`factory_version`. `run_record.py provenance --runs RUNS --run RUN` renders commit trailers and
`PR built by NAME vVERSION` from that record, not from the target package or current installation.
The host supplies only that identity to each author's private brief. Older Factory records with
version but no name imply Factory; a missing version remains unknown. Direct work without a
Factory run must not acquire Factory or Chiron attribution retrospectively.

Endgame records check actual candidate-range commit messages after the retained target or
author projection bases. Upstream commits merged after the pin also appear in this range;
review their original attribution separately and never relabel imported history as Factory work. The Validator checks actual GitHub metadata with
`run_record.py provenance --runs RUNS --run RUN --repo REPO --commit SHA --pr URL` after a PR
update and before merge. That read-only check records the exact head/body and queues findings to
the Orchestrator, who independently reviews them. Observations apply to that snapshot; later
changes require another check. Missing or conflicting stamps are review findings; test-command
outcomes stay separate. Identity consistency does not establish cryptographic authorship.
Published commits are preserved.

Each endgame or explicit provenance checkpoint attempts one bounded System One observation through
the existing Jev client. Only retained builder name/version, exact candidate ID, observed
attribution lines, PR head and verification coverage are sent; source code and PR prose are
excluded. The same result file retains the exact payload/digest, pinned model and observed
results or a fixed unavailability reason. There is no background worker, automatic retry or
new acceptance gate. A crash before persistence can lose an observation; no exactly-once claim
is made. `TYPESAFE_API_KEY` and the existing `JMC_JEV_API_KEY` alias are supported in the invoking
environment; absent credentials or provider failures are reported as unavailable. Resident
coordinator environments intentionally omit these keys. With explicit user authorization,
`FACTORY_SYSTEM_ONE_PROFILE=1` on the Factory invocation passes only that non-secret flag to
coordinators. If no direct provider key is present, the existing short-lived HTTP child sources
`~/.profile` with startup output suppressed and profile input disconnected from the request.
It selects only the System One key, clears unrelated environment variables, and returns only
validated model/probability fields; no credential returns to the coordinator or enters author
or candidate environments. The eight-second request deadline includes profile loading, followed
by at most one second of cleanup of this invocation's own process group. An unreadable profile
or missing key is explicit unavailability. The default remains no profile loading; no broker,
worker, profile edit or machine-wide setting is introduced. Candidate test commands do not
inherit either the provider key or this flag.

Endgame checks commits, so its PR coverage is explicitly `not-checked`. The PR command records
`checked` only when it compares the fetched snapshot; that means inspected, not necessarily
consistent. Skipping the PR command leaves that coverage gap visible even if tests pass or
System One reports a low mismatch probability. The Orchestrator reviews actual artifact
findings, PR coverage and the separate provider observation; none implies product acceptance.
