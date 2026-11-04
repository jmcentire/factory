# Software Factory

Factory coordinates a task with a Validator, a resident strategic Orchestrator, and independent Coder and Tester conversations. The user chooses the task, repository and agents/models. The Validator owns acceptance. A model-family label is descriptive; Factory does not approve models.

Start with:

```sh
harness/factory.sh my-task 'Describe the required behavior' --repo /absolute/repository
```

A task file may replace the task text. `--runs` chooses the record directory, `--log` chooses optional human-readable logging, and `--budget` provides a ceiling for explicitly metered author lanes. The default logging destination is `/dev/null`. Required task inputs, messages, author output and validation results are always retained.

`FACTORY_VALIDATOR_AGENT`, `FACTORY_ORCHESTRATOR_AGENT`, `FACTORY_CODER_AGENT` and `FACTORY_TESTER_AGENT` select CLI adapters. Corresponding `_MODEL` and `_EFFORT` values preserve explicit choices; otherwise local role defaults in `~/.config/factory/config.toml` (or `FACTORY_CONFIG`), shipped role defaults, and the selected CLI's own configuration/defaults apply in that order. There is no model approval list. Adding a new CLI requires an adapter for its actual protocol, not permission to use a model. `FACTORY_VALIDATOR_AGENT=kickoff` uses the existing initiating conversation as Validator.

The record pins the starting commit without changing the user's checkout. No catalog, signing key, human receipt, phase ratification, external checkpoint, boundary register or prior model trial is needed. The historical signed runtime and pattern-generation libraries are separate archival/research interfaces, not prerequisites or an alternate authority for normal tasks. Existing historical run records are preserved and are not silently converted or restarted.

The Validator gives Coder and Tester separate asymmetric input projections and behavioral briefs, preserving independent inputs. A clean clone alone does not protect the Tester from implementation or the Coder from independent test details. Both use the assigned model and stay in their native conversations until the task completes. Claude and Antigravity author launches use resident native input. Typed session delivery checks the actual retained process and session; it never substitutes a terminal pane or changes models.

The Orchestrator continuously watches all roles, owns strategic oversight and process adherence, and may block a concrete action with evidence and a needed correction. The Validator owns product acceptance and addresses those findings. There is no cadence dispatcher typing into terminals, no automatic permission-dialog acceptance, and no supervisor suspending productive roles for review lag. Reviews happen when useful; elapsed time and journal backlog are not approvals.

Validation uses the actual project's checks against an immutable candidate:

```sh
harness/endgame.sh my-task EXACT_COMMIT --repo /candidate/clone -- python -m pytest
harness/status.sh my-task
```

The Validator integrates independently authored tests before invoking the checks. The harness materializes exact Git objects without executing repository hooks, filters or replacement objects, runs the supplied command, and retains its output and exit code. It does not equate tests passing with product acceptance or publish the result. The Validator reports behavior, checks, residual risk and the candidate revision.

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

## Evidence and consequences

Keep one authoritative owner per fact, least privilege, durable evidence, visible failure and honest reports. Fail closed on real hazards: unknown permission, secret handling, unsafe filesystem/Git behavior and wrong process identity. Do not turn an inconclusive formatting or progress heuristic into authority to stop the entire run.

Retain eight principles: fail closed on hazards; one authoritative owner per fact; least privilege; auditability; no silent failure; honest documentation and reports; provenance of intent; and observed verification rather than self-attestation. A forbidden capability must be unavailable to the agent, not merely discouraged in a prompt. Actual platform permissions govern tool use; Factory cannot grant them or click through their dialogs.

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

## Release-check choices

User direction for signatures: “no signatures that aren't automated verified by what the user actually said”. No manual signature or repeated authorization ceremony is required. If an automated attestation is used, retain the actual user instruction and verify that the claimed action and scope follow it; a signature or content hash alone is not proof of permission. This is a constraint on optional attestations, not a requirement to create them.

The human-gate wording check is a heuristic. Tune it with `make HUMAN_GATES_ARGS='--mode warn --max-findings 5' check-human-gates`, `--mode off`, or repeatable `--exclude` repository-relative path patterns. The report distinguishes warnings and disabled checks from a clean scan. Existing local `gate-exempt` explanations remain available.

The authority check supports explicit user overrides, for example `make AUTHORITY_ARGS='--allow-symbol TesseraSeal --allow-module exemplar' check-authority`. Each overridden finding is reported; other findings and unreadable/invalid source still fail. These options change a local release lint, not platform permissions, and do not validate an unsafe helper. Agents use overrides only within the user's actual direction.

Denial tests must exercise the prohibited action and its absence, including a positive control for valid work; a collecting test or a matching string is not proof of behavior. Use actual baseline failures or bounded mutation checks where they establish that the test detects the regression. Acceptance-ledger and glossary checks remain enabled.
