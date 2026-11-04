# Direct cleanup decisions — 2026-11-04

The goal is to produce clean, extensible, local, durable, maintainable, reliable software. Four roles and their independent inputs support that goal. An incident's historical explanation does not establish that its workaround should remain.

This is a code walk and a local candidate, not a claim that the replacement workflow is released or live-qualified. All five task-owned Factory servers and 121 recorded managed processes were stopped, with no survivors in the recorded shutdown. Previous worktrees and evidence remain. No new Factory or agent run has been started; native transport tests use disposable Python protocol fixtures.

## Evidence and decision basis

Current user direction removes the approved-model list, mandatory catalog and invented human signatures, and requires direct cleanup with Factory stopped. Later clarification preserves four roles and research-backed decisions. These instructions supersede historical implementation requirements.

Local `.kin/knowledge.jsonl`, `docs/DOCTRINE-KERNEL.md` E1/E3, and Kindex nodes `22c90265226a` and `c588a0b54d6f` distinguish enduring design from accumulated controls. The role ruling explicitly requires a resident, continuously vigilant Orchestrator watching every lane and making unprompted adherence/drift/block calls. The design record calls for asymmetric author projections and scoped Kindex, not merely two clean clones. E1/E3 identify correlated failure and repeated interpretation as research bases. Those records were read; the underlying studies have not been independently re-reviewed in this cleanup, so no new quantitative research claim is made here.

## Components and decisions

| Component | What the code does | Decision and goal | Candidate state / remaining work |
|---|---|---|---|
| Four role prompts, `prompts/{validate,orchestrate,engineer,test}.md` | Define intent interpretation, strategic oversight, implementation and independent testing. | **Keep roles; simplify instructions.** Preserve the Orchestrator's standing oversight, independent Coder/Tester inputs, and Validator acceptance. Remove repeated ratification paperwork. | Rewritten. An initial draft incorrectly called the Orchestrator advisory; corrected after reading the role ruling. Role semantics must not be weakened to make transport simpler. |
| `factory.sh`, CLI `factory run` | Previously required pre-existing signed Stage R/E state before starting native conversations. | **Simplify startup.** Task + explicit repository + assigned agents. A user's authorized request should be executable without a catalog or fake digest. | Local task entry implemented and stub-tested. No real agent launch performed. |
| `run_context.sh`, `run_record.py` | Previously rechecked external checkpoint/genesis/signature chains for ordinary reads. Now retains literal task, repository, base commit and private socket. | **Keep durable inputs; remove repeated authorization ceremony.** A record is not another workflow authority. | Implemented; dirty source checkout preserved. Historical signed records are not silently adopted. |
| Model-family gate in `lane_agent.py` / `tmux_lane.sh` | Refused unknown or same-family models unless a waiver or prior qualification existed. | **Remove model approval. Keep diversity as a research-backed design consideration.** Honor assigned models and report the actual arrangement; unknown provider classification must not veto a valid model. | Gate removed; metadata retained. Unknown/same-family launch cases pass. No account or model was changed. |
| Agent CLI adapter registry, `agents.py` | Encodes actual invocation, authentication, billing and session protocols. | **Keep and make extensible.** Protocol support is necessary; it is not permission to use a model. | Existing adapters retained. Literal model arguments tested. Further adapter permission/default review remains. |
| Pane command-name allowlist in `inject.sh` | Guessed whether a pane was an agent from names such as node/python, then sent keystrokes. | **Remove this workaround.** A process can exit between check and delivery; widening names cannot fix that race. | Status and coordination now append durable inbox records and never call tmux. Tests cover literal multiline/shell-shaped text and no terminal calls. |
| Optional log destination | Human-readable status was coupled to delivery surfaces. | **Separate optional log output from required task state.** Default `/dev/null`; explicit file supported. | Implemented in `run_record.py`, `inject.sh` and historical notification callers. Inbox persistence is independent of logging. |
| Native author input, `persistent_lane.py`, `codex_lane_session.py` | Owns a real child/session and delivers typed input, with native acknowledgments. | **Keep.** Durable conversation continuity and real acknowledgments solve delivery; shell input does not. | Existing persistent Claude/Agy implementation reused, supervisor release dependencies removed. Same-process/session, exit, stale binding, literal input, ACK and interruption tests pass with protocol fixtures. Actual Agy continuity remains unqualified. |
| `seat_supervisor.py` cadence/hold control | Mixed identity tracking, process suspension, review leases and shared stop/resume state. | **Separate necessary identity checks from automatic review-lag suspension.** Backlog is not proof that an agent is dead. | New startup does not launch this supervisor. Historical control code retained for evidence/analysis, not adopted into the new run. Exact process checks remain in typed transport. |
| `dispatcher.py` activity capture and delivery | Captures activity, emits cadence, classifies prompts, delivers prose, tracks leases and can answer dialogs. | **Keep full visibility; remove prose injection and automated permission acceptance.** Observation supports the Orchestrator; cursor debt must not become a second boss. | Injection and dialog acceptance removed. The resident Orchestrator now uses the existing dispatcher's read-only `--observe` path to read all other roles' retained output, launches, exits and coordinator messages; its own conversation supplies its activity. Byte offsets paginate data and have no control authority. Chunk completeness, appends, replacements, symlinks and literal shell-shaped text are tested. **Unfinished:** real native Orchestrator loop continuity remains unqualified while Factory is stopped. |
| `orchestrator_channel.py`, review cursors and agreement/boundary registers | Turn strategic observations and checklist coverage into mechanically gated transition state. | **Simplify, not erase oversight.** Concrete strategic findings need disposition; every journal row or identifier need not create another admission gate. | No new control layer added. Historical channel remains on disk for its old APIs. The current read-only observer does not require per-row assessments, review leases or this channel's admission state. |
| `lane_watchdog.py` | Combines liveness, spend caps, commit-rate/stall heuristics and timed review pauses. | **Keep real spend/liveness facts; remove timers as authority over plan work.** Silence or no commits does not prove failure. | Normal plan launches no longer create a watchdog or scheduled review pause. Explicitly capped work retains cap handling. Cap/watchdog regressions are included in the launcher sweep. The optional capped watcher remains distinct from normal plan work; native provider continuity is still unqualified. |
| Input projections, `projection_bundle.py`, `lanes.py`, `lane_repository.py` | Builds asymmetric role views and immutable exports; avoids host execution of author-controlled Git behavior. | **Keep genuine independence.** Tester must not see implementation; Coder must not see independent tests. | `role_inputs.py` now materializes the exact base commit safely, selects the Validator-declared paths, removes shared `.kin`, and creates fresh Git history. The productive launcher checks actual input bytes, including ignored files, before handoff. Tests cover hidden history, implementation/test exclusion, dirty source preservation, wrong paths, symlink disclosure and rejected full clones. Semantic classification of the selected documents remains the Validator's job; filename filtering alone cannot establish it. |
| `lane_kindex.py` and shared seed | Gives each author a scoped graph and identical curated historical inputs. | **Keep.** Useful recall must not become a live cross-role disclosure channel. | Existing scoped preparation retained. All four role instructions require relevant retrieval. No remote Kindex writes attempted. |
| `host_git.py` and exact candidate materialization | Reads immutable objects with hostile hooks, filters, attributes and replacement refs neutralized. | **Keep.** Tests must execute the candidate that is reported, without executing author-planted host Git commands. | Reused by simplified validation. Hostile config and exact-byte tests pass. |
| `endgame.sh`, `validate_candidate.py` | Previously imposed fixed checks, artifact registries and resource closure; now runs the chosen project checks on an exact commit. | **Simplify test execution, preserve judgment.** Real test results are evidence; they do not automatically prove product acceptance. | Implemented, including failures/missing commands and retained output. Project-specific setup and full independent-test integration still need operational validation. |
| Catalog schema and `generation.py` | Target schema required a catalog even for ordinary work; historical generation consumes pattern IR. | **Remove catalog from ordinary tasks.** Patterns are optional construction tooling, not a user's admission ticket. | Manifest field made optional; new native path does not use generation/catalog APIs. Historical pattern-generation code is not deleted or falsely declared catalog-free. |
| Tessera/signing, `authority.py`, `project_init.py`, resume chain | Cryptographically validates historical signed workflows. | **Remove from normal startup, retain truthful historical verification.** Do not fabricate signatures or silently reinterpret old records. | New path has no signer dependency. Installer no longer builds/requires Tessera for ordinary use. Historical cryptographic APIs remain. |
| `lane_env.sh`, credentials and billing | Clears ambient environment and admits explicitly named values. | **Keep least privilege; remove grounding-age/attention receipts as prerequisites.** | Environment isolation remains; stale-ground marker and unread-event prechecks removed. Metered costs still require explicit limits. |
| Private tmux sockets and exact process identity | Prevents selecting a stale/wrong pane or killing unrelated work. | **Keep.** Local process ownership is real, not paperwork. | New shell context explicitly targets a per-run private socket. Shutdown evidence confirms only owned servers/processes were stopped. |
| Release checks in `make ship` | Checks doctrine, authority, registered denial probes, acceptance/glossary records, static checks and tests. | **Do not conflate release assurance with runtime admission.** Identify obsolete assertions individually; preserve useful regression/security checks. | Unchanged broad gate chain. An attempted broad removal was rejected by automatic review and did not execute. Current `make ship` fails doctrine parity after the prompt redesign. Other release diagnostics were run individually without changing the gate chain. Wiring findings in three new modules were corrected by making their imports statically resolvable; the wiring check then passed. |

## Verification and limitations

- 121 focused tests pass across normal task records/validation/logging, model selection, safe host Git, adapters and native stream protocol fixtures.
- Seven focused real-shell launcher tests pass with stub CLIs for unknown/same-family models and exact seat confirmation.
- Lint passes; mypy reports no issues across 128 source files.
- Shell syntax and Python compilation checks passed at their recorded stage; run the complete current checks again after the remaining integration work.
- Full release readiness is **not established**. `make ship` stops on obsolete/changed doctrine assertions; the gate chain has not been bypassed. The broad full-suite command bundled with the rejected gate edit never ran.
- No native agent work, global installation, publication, merge, deployment or Factory restart occurred.

The two identified input/visibility gaps now have implementations and fixture evidence. Operational proof with the assigned native roles has deliberately not been attempted under the stop instruction. Remaining work includes reconciling the concise doctrine with its existing structural check and migrating affected historical shell tests without discarding genuine protections. The local candidate must not be presented as a released or live-qualified four-role Factory.

## Additional executed checks and failure dispositions

- The initial broader regression sweep stopped at 20 failures: 410 passed and 3 skipped. Its output is retained; this is not a full-suite pass.
- Nine author boundary/custody cases failed before reaching their assertions because their fixture invoked the old signed startup. The fixture now creates ordinary task records and role projections. All 34 tests in `test_author_boundary.py` and `test_author_custody.py` pass with the original permission, identity and confinement assertions intact.
- Installer and doctrine-assembly compatibility: 69 tests pass. The installer test now verifies that normal installation works without Tessera. Assembly tests retain the nested-heading truncation regression with an explicit fixture and verify both flat and nested prompts rather than demanding five headings in a live prompt.
- The first complete launcher sweep reported 83 passed and 11 failed. It exposed a real `printf` option-parsing defect for explicit model flags; both model argument forms now use `printf ... --`. Other failures reflected removed watcher/model-qualification/fixed-strategy-file requirements, the deliberately non-adopted historical dispatch path, and changed input preparation. Their expectations were updated against the described behavior, with the old failure output retained. The final rerun outcome is recorded in the checkpoint/evidence directory.
- Input verification was strengthened after inspection: Git status can hide ignored files, so the first-launch comparison hashes actual filesystem inputs. A regression proves an ignored implementation file is rejected.
- The denied broad release-gate edit was not retried. The existing gate chain remains. Narrow registry maintenance updates renamed model-selection/materialization probes and adds input-isolation cases; it does not remove release targets.
- Purity, lint, typecheck (129 source files), shell/Python harness checks, wiring, forbidden-authority, no-human-gates, acceptance-ledger and glossary diagnostics passed at their recorded stages. The acceptance-ledger diagnostic notes that its historical external citations are unavailable on this machine; do not convert that note into a newly verified external evidence claim.

The component table is an itemized walk of the active workflow and its principal supporting boundaries, not a claim that every historical module has been removed or exhaustively revalidated. The scope of unfinished qualification is explicit so the simplification does not become another unsupported claim of readiness.

## Final checkpoint for this turn

Factory remains stopped: the 13:10 UTC read-only recheck found none of the 121 original PID/start/UID identities and no live session on any of the five owned private servers. Work and previous evidence remain on disk.

The final affected-suite run reported 139 passed and one failed expectation: the freeze receipt uses `traceability_gaps: null`, not an absent field. Both mean no coverage verdict, but the test now asserts the actual explicit-null contract. The targeted follow-up passed all 27 cases, including that correction and input/activity/normal-workflow checks. No product code was changed to make that expectation pass. Real disposable zsh checks also passed twice, with the shell live and exited; status persisted in the inbox, made no tmux contact and did not change history. Test cleanup addressed only its newly created private socket after verifying server identity.

The latest static/harness/denial-probe checks passed. Full `make ship` remains unqualified; the unchanged doctrine checker still asserts the former document's structure and signed workflow. The initial full-suite sweep did not reach all tests. Do not infer a full-suite pass by adding the focused counts together.

All changes are uncommitted on `fix/direct-factory-cleanup` from `e3ea9e56c50cb89b0fdeb01acc8431b27097237a`. Nothing was installed globally, published, merged or restarted. No replacement model or account was used. The complete candidate patch and logs are retained in the sibling `factory-direct-cleanup-evidence/` directory. The broad release-gate-chain removal rejected by automatic approval review did not execute; no retry or equivalent wholesale gate removal was made.

## Latest checkpoint: seven decisions and host-health pause

Model defaults now share one resolver across all four roles: user arguments/environment, local role defaults, shipped defaults, then native CLI configuration/defaults. Unknown/retired names are never silently replaced. Confirmed retirement is handled by the existing Validator asking about a verified current replacement; that live conversation is not qualified while Factory is stopped. No new availability poller or model approval list was built.

The doctrine checker now protects the agreed workflow rather than old signature/chapter ceremony; 94 doctrine/no-human-gate tests passed. The seven release checks remain enabled. All static release checks, lint and mypy over 130 modules passed before pytest. The new focused suite passed 119 cases. Delivery/stale-prerequisite test migration passed 38 cases with one historical auditor failure.

The user reported killing fseventsd at 250% CPU/125GB memory. I interrupted the verified owned broad pytest PID 73899 and confirmed it and children 4922/5273/5274 exited. It ended with 162 failed, 1,175 passed, 4 skipped: incomplete and failing, not release-ready. A replacement fseventsd PID 83218 fell from 39.4% to 1.4% CPU with about 11 MiB RSS. The test creates many temporary Git repositories and files, so it may contribute filesystem events; no trace establishes the original cause. Heavy checks remain paused. No Factory restart or system-settings change occurred.

The candidate remains uncommitted and uninstalled. `candidate.patch` from the earlier checkpoint is stale; current files are authoritative. Detailed implemented-versus-proposed removals and each release gate's impact are in `DIRECT-REMOVAL-INVENTORY.md`.

## Subsequent release-check implementation and status correction

See the latest section of DIRECT-REMOVAL-INVENTORY.md for implemented tunability, exact authority overrides, wiring diagnostics and the preserved user signature wording. Targeted tests passed 27 + 1 + 3 + 4 cases; no broad run was restarted.

IMPORTANT CORRECTION: the earlier statement that no native agents were started during cleanup was incorrect. A historical startup test had no stubs because it expected rejection. The new normal path accepted it in two broad runs and launched temporary coordinators. Both exact test-owned runx sessions and all six surviving process identities are now verified gone. The original repair runs remained stopped. The fixture has been corrected and unmocked launchers are now blocked. See accidental-fixture-launch-stop.json for exact sockets/PIDs and verification.

Full regression remains failing/incomplete, with a first-cause inventory for all 162 observed failures. Heavy checks stay paused following renewed fseventsd CPU load. No merge, publication or global installation occurred.

## Small-check follow-up: remaining 21 failures reviewed

All 21 formerly unreviewed failures now have individual first-cause/disposition records in `regression-triage-initial.json`. Eight original cases have passing migrated checks: startup preflight recovery, two inbox-notification cases, model selection, two coordinator-failure cases, and two legacy-schema refusals. The remaining 13 original assertions remain pending; two now also have passing current-path behavioral coverage. No historical failure was hidden by a skip or marked green solely from classification.

Code defects fixed: coordinator directories are checked before launch; a partial launch cleans up only its own exact private session and retains an actionable error. A candidate that cannot be materialized now produces a retained validation-failed result without running commands or claiming a verified candidate. Result-write failure cannot produce a reported PASS. These tests use missing executables, mocks or explicit test stubs, never real agents.

The guard was rechecked through the exact valid-startup path that previously escaped: without explicit stubs it fails with the fixture's exit97 and creates no native session. The selected-model fixture first hit the sandbox's ps restriction; its single host rerun used stub agents/tmux and passed. Broad tests remain paused. Current native sessions remain stopped; no publication, merge, installation or commit occurred.

## Final 13-case migration within the reviewed subset

The remaining 13 historical assertions have been replaced by 14 executed behavioral cases, in small batches: seven ordinary-validation cases, five failure paths, and two Python-only persistent-session cases. All passed. The superseded test functions and five endgame shell-string mutation entries were removed only after replacement behavior passed; unrelated promotion tests and scanner mutations remain. Registry references now point to the replacement tests.

| Historical expectation | Current exercised invariant |
|---|---|
| Missing promotion inputs | Validation needs an actual command; missing checks cannot create acceptance evidence. |
| Missing run.json | Validation cannot create or accept an unknown run. |
| Endgame must invoke Gate L | Actual test exit0/exit7 is recorded; neither silently accepts/promotes/closes the task. |
| Endgame refusal event | Invalid candidate through the real endgame CLI retains a failure result and cannot execute checks. |
| All shell refusals call a helper | Missing command separator is an explicit usage failure, never PASS or a validation attempt. |
| Immutable close-write error | Result-storage failure yields an error and no reported PASS or task-state mutation. |
| Five shell-string removal mutations | Actual changed task, non-commit object, missing candidate repository, escaping symlink and unavailable test executable all fail truthfully. A separate valid-case control passes. |
| Partial/mismatched orphan retry | Missing process evidence or wrong lane identity blocks message delivery; restoring the original binding allows one delivery on the same Python simulator process. |

All 21 formerly unreviewed cases now have passing current-path replacements. This resolves that subset, not the entire original162-failure/full-suite inventory. Historical startup/dispatch/endgame fixtures in other groups still require migration and the full regression has not been rerun. No Factory/native agent CLI or tmux session was started by these batches; the two session fixtures run a tiny Python protocol simulator. Heavy tests remain paused.

## Startup and communication regression checkpoint

All 32 cases in the original “old startup invocation omits explicit repository” group now have executed current-path coverage. Four prompt-protocol cases and three retired-option cases also have passing replacements. The detailed old-to-new mapping is in the sibling evidence directory's `regression-triage-initial.json`; this is not a claim that all 162 original failures or the full suite are resolved.

Two runtime defects were reproduced or identified and corrected:

- The Ollama coordinator path now reuses `model_availability.sh` before launch. Missing names and unavailable models are refused; an available, explicitly selected model reaches the launch unchanged. It never downloads or chooses a replacement model.
- A failed kickoff-mode start can retry with its unchanged private Validator brief. A changed brief or substituted symlink is refused without overwriting it. The existing failure notice remains in the inbox.

The startup tests execute temporary CLI stubs with generated arguments and environment, verifying credential isolation, exact task/repository/commit binding, literal model/effort choices, assigned author choices, platform permissions, and pinned Python. One test executes the actual lane wrapper around a Python stub that rejects a native flag: it retains exit 64 and does not try another model. This is wrapper evidence, not qualification of a live provider CLI.

The fixture guard now covers Ollama and handles a negative-path call followed by installation of an explicit temporary stub. Once that stub is removed, the native executable is blocked again. No native agent, real Kindex service, or tmux server was launched by these batches. Sandbox-denied `ps` attempts are retained alongside the bounded host reruns; host reruns used only temporary stubs.

Author tests preserve local Git ownership, independent projections and private Kindex stores. A shared author repository and a changed shared seed are rejected. Completed-probe history in that fixture is explicitly synthetic; it is not live qualification evidence. A frozen file export retains its launch mode and cannot assert product acceptance. Missing Critical coverage remains for Validator adjudication rather than turning an export operation into another acceptance authority.

Both author prompts again describe the executable question protocol, preserving native conversation context and independent work while a concrete contract answer is pending. The Orchestrator/Validator prompt tests now protect concrete oversight and response duties without demanding a fixed historical rule table. Assembled doctrine was regenerated and its consistency check passed.

Explicit credential scanning still detects and records a leak in the selected run's Kindex files without scanning unrelated conversations or copying the matched secret. This is an explicit check; normal startup no longer performs the historical grounding scan. Retired `--engagement` and `--jev-screen-calls` startup options fail visibly without creating a run or contacting tmux. Existing optional historical routing/screen tests remain; no release target was removed.

Executed small batches: final startup/guard batch 26 passed (one wrapper case intentionally selected in the separate host batch); final host batch 4 passed; author launch/export batch 4 passed; question/scanner batch 2 passed; task creation batch 2 passed; guard/retry batch 2 passed; prompt batch 4 passed; retired options 3 passed. Counts overlap and must not be summed into full-suite coverage. Ruff, doctrine consistency and diff whitespace checks are recorded separately. The original full sweep remains incomplete and failing.

A read-only health check during this work observed fseventsd PID 83218 at 0.4% CPU and 14,704 KiB RSS. This is a point-in-time observation, not a cause attribution or proof of sustained recovery. Heavy tests remain paused; this work stayed in small targeted batches. Remaining groups include historical signed-context/dispatch/endgame paths and associated coverage, plus eventual full validation and native qualification under appropriate authorization. Changes remain local, uncommitted and uninstalled.

## Context, endgame and resumed-message checkpoint

The 12-case historical endgame group and the five-case removed-configuration-helper group now have passing current-path coverage. Five cases from the historical-context group also pass after migrating their message fixtures. The mappings are retained in `context-endgame-mapping.json` and `regression-triage-initial.json` in the sibling evidence directory. Other historical dispatch, crash-recovery, promotion/closure and context cases remain; no full-suite or release pass is claimed.

Actual corrections:

1. Exact-candidate validation now discovers a local LFS store in the candidate or original task repository, or accepts `--lfs-objects PATH`. It uses the existing digest/size-verifying materializer, with no network fetch, git-lfs execution, hook or smudge filter. `--require-lfs` prevents checks when pointers remain; this also catches a planted attributes override. Without that requirement, pointer-only checks are explicitly reported in stdout and evidence and do not establish asset behavior. An incomplete discovered store or corrupt object is a real validation failure. Literal pointer examples that are not LFS-routed remain literal. The candidate/result records retain the actual object-source path and hydration counts.
2. Codex resume now shell-quotes the retained model instead of rejecting literal names containing spaces or interpolating unquoted text. Nonstrings and NULs remain refused. The assigned model and original sandbox setting survive resume; ambient changes do not replace them.
3. Resume starts a watchdog only when the retained launch has an explicit wall cap, matching initial launch. Uncapped plan work no longer gains automatic pauses simply because it receives a message. The positive capped case still creates the enforcement watcher.
4. The activity reader no longer consumes an incomplete UTF-8 character at end-of-file. It retains that fragment behind the cursor until another append completes it. The regression failed before the correction and passes afterward.

The obsolete 818-line headless audit script was replaced by an explicit retired-command diagnostic directing callers to the resident Orchestrator's existing `dispatcher.py --observe` reader. The old path depended on removed signed configuration helpers and could create a duplicate one-shot agent. It now exits before launching any executable or mutating current/historical task records. Eight historical audit tests were mapped to resident observation and no-launch/no-mutation coverage; obsolete fixture helpers were removed. No release target was removed.

Coverage retained: exact task/commit/repository context despite ambient forged settings; shell-literal context fields; changed task refusal; local LFS hydration and missing/corrupt object failures; unchanged project checks on the exact candidate (a dirty working-tree fix cannot turn an old failing commit green); hostile Git attributes/filters cannot hide or rewrite judged files; source-free specification answers stay on the retained thread; the Orchestrator cannot answer an author's specification; queued work is not falsely acknowledged; real stop/custody checks remain; observations do not become product verdicts or agent control actions.

Executed batches: LFS 10 passed plus two CLI-option cases; ordinary context 3 passed; validation/resume regressions 16 passed; migrated message cases 5 passed, then the changed two-case correction passed; activity observation 9 passed; the message module first had 13 passed and one obsolete kickoff-supervisor expectation, whose current-path replacement then passed. These batches overlap and must not be summed into a full-suite result. Ruff, shell syntax, doctrine consistency, gate-reference resolution and diff whitespace checks passed. No native agent or tmux server was launched; all message tests used temporary stubs.

The optional remote Kindex search was rejected by automatic approval review as a potential disclosure of repository-derived information to a remote destination. Recent parent history contained no new approval for that disclosure. The search was not retried or rerouted; local work continued. This is a denied optional lookup, not a code-work blocker.

A later read-only sample found fseventsd PID 83218 at 0.6% CPU and 15,008 KiB RSS. Small batches continued; no heavy full run or system change occurred. Factory remains stopped. The candidate remains local, uncommitted, uninstalled and unqualified for release. The exported `candidate.patch` is older than the current checkout; the checkout and current reports are authoritative.

Accuracy note for downstream summaries: the earlier kickoff-brief defect rejected a retry because the brief already existed; it did not overwrite the saved brief. The correction permits unchanged-brief retry while continuing to refuse changed or substituted briefs.


## Direct cleanup checkpoint: historical dispatch, crash recovery and closure

The remaining historical dispatch/closure groups were migrated to the current workflow.
Factory and native agents stayed stopped. Tests used temporary stub executables or small
Python subprocesses; no remote Kindex request was retried, no model changed, and no release
was installed, published or merged.

Implemented:

- Retired `dispatch_lane.sh`, `promote.sh`, `record_no.sh` and `abandon_legacy.sh` with
  immediate diagnostic exit 64. They do not inspect or alter a run, launch an agent,
  manufacture approval, classify a terminal outcome, or silently adopt historical work.
- Reduced `orchestrator_checkpoint.sh` to durable notification. It preserves exact detail,
  reports queued rather than acknowledged, and never waits for assessment cursors or a
  boundary-register permission check. Inbox failure remains an error.
- Fixed inbox append recovery: serialize writers, finish short writes, roll back a failed
  append's own bytes, and separate a killed writer's partial final line from the next record.
  Preserve the interrupted bytes with a visible diagnostic. This is process-crash recovery,
  not an exactly-once protocol or a power-loss experiment.
- Optional-log failures now warn after successful durable delivery rather than reporting
  delivery failed and encouraging a duplicate send. Unsafe symlinks, FIFOs and directories
  receive no message content.
- Replaced the historical harness guide with current commands, authority boundaries and
  explicit limitations. Updated affected registry claims and test references; no Makefile
  release target was removed.

Coverage migration:

- Removed 60 test functions tied to the retired shell implementation, with itemized reasons
  and surviving behavioral references in `dispatch-closure-migration.json`. Five now-unused
  fixture helpers were removed. One cross-module historical-dispatch fixture was simplified
  and passed separately.
- All entries in the tracked historical signed-context, removed configuration-helper and
  historical dispatch crash groups now have explicit migrations. Original failing evidence
  remains retained. This is not a claim that the full repository suite passed.
- Independent input filtering, pinned task/source bytes, private author Kindex, metered budget
  reservation, actual candidate outcomes and failure retention remain exercised. The optional
  runtime instruction-publication recovery test still passes; its implementation was not
  removed to make ordinary workflow tests green.

Executed focused batches:

| Batch | Observed result |
|---|---|
| Inbox crash/I/O recovery and existing sink protections | 13 passed |
| Retired commands and nonblocking checkpoints | 20 passed |
| Budget concurrency/retry, author inputs, candidate results and startup retry | 32 passed |
| Launch caps, private Kindex stubs, ordinary context and historical compiler recovery | 7 passed initially; 2 sandbox `ps` failures then passed with narrowly approved host identity access |
| Retained historical behavior and role-doctrine assembly | 16 passed |
| Simplified historical-dispatch fixture | 1 passed |

The initial inbox regression run recorded 7 failures and 1 pass before the fix; syscall-hook
cases also required the writer to reach the new explicit-write seam, so that count must not
be described as seven independently reproduced production bugs. Final tests exercise actual
SIGKILL before write, after partial write and after sync, disk-write/sync failures, and unsafe
optional logs. Budget tests kill a real Python reservation writer after sync and show exact
retry reuses one reservation; competing processes cannot oversubscribe the limit. Partial or
tampered budget evidence still refuses further spending rather than inventing free capacity.

Ruff, shell syntax, doctrine consistency, wiring, human-gate scan and diff whitespace passed.
All gate test references resolve by AST; this is not execution of every registered probe.
Focused counts overlap prior checkpoints and must not be added into a full-suite total.

Remaining: the broad release run is still incomplete (last interrupted result: 162 failed,
1175 passed, 4 skipped). Heavy regression remains paused under the host-health instruction;
the attempted read-only `ps` health sample was sandbox-blocked, so no new CPU/recovery claim
is made. Native assigned-role qualification is not performed while Factory must stay stopped.
The current checkout and refreshed patch are uncommitted and not release-ready.


## Final bounded regression continuation — 2026-11-04

No user input or permission decision is needed for the completed code cleanup. Factory and
native agents remain stopped; the candidate is uncommitted and has not been installed,
published or merged. This entry supersedes earlier pending-regression and health statements.

The serial sweep covered all 151 current test files. Reconciliation against the final
2,620-case collection records 2,618 passing cases across the partitioned batches and focused
reruns, one platform-specific skip, and one deliberately unrun installed-Claude plugin probe.
This is not one final `make ship` invocation or a productive live-agent demonstration. Tests
were run serially at low priority with native-command blockers; disposable shell/counter tests
used private tmux sockets and ownership-checked cleanup. Earlier failures remain in the logs.
The final Tessera batch passed all 25 formerly skipped cases using the already installed binary.
All 11 retained static targets passed after the final code changes, including lint and mypy
for 130 source files. No Makefile release target was removed.

Additional concrete fixes found by this sweep:

- Durable correction messages now retain the SHA-256 text digest promised by the delivery
  receipt. Symlink refusal still preserves the target and reports the contracted error.
- Removed remaining automatic review-backlog stop/alert behavior from the historical seat
  supervisor. Explicit pause/resume and genuine process-identity protection remain. Explicit
  resume can clear an obsolete lag hold without dropping an identity fault. Six real private-
  tmux custody cases passed, including running counters during unread assessment backlog.
- The optional runtime worker-environment API now refuses the host's human key as a worker
  role. The migrated negative test failed before this fix and passed afterward. This is a
  credential boundary, not a signature prerequisite for normal Factory work.
- Scoped repository rules are explicitly carried into independent author briefs. Obsolete
  prompt-heading assertions were replaced with current behavior coverage, and two compiler
  fixtures now consume the actual role-doctrine source. The existing Tessera replay test uses
  the explicit runtime CLI, retains anchor validation, and rejects an unanchored append.
- A subprocess test now uses its actual test interpreter instead of an unrelated incomplete
  virtualenv. No dependency installation was required.

Evidence: sibling `factory-direct-cleanup-evidence/serial-regression-final.json` indexes the
batch logs, failed-before/fixed-after checks and final collection. The six historical procedure
assertions have a separate migration map. Current shell-history cases passed after the digest
fix. Optional logging remains `/dev/null` by default; durable messages are not discarded.

Final lightweight host sample: fseventsd 0.3% CPU and 14,864 KiB RSS, 83% memory free,
load averages 8.96/7.22/7.96. No processes matched the cleanup checkout or five named repair
runs. Unrelated user sessions were not touched. No remote Kindex request was retried.

Remaining qualification limits: the installed-Claude plugin probe was deliberately not run,
and assigned native roles have not completed a productive job using this candidate while
Factory must remain stopped. Therefore this report does not claim a shipped release or live
end-to-end qualification. Neither limitation blocks review of the preserved patch; no new
approval request is being introduced.
