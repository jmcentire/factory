# Direct Factory cleanup: implemented changes versus proposals

This inventory describes the uncommitted candidate on `fix/direct-factory-cleanup`, based on `e3ea9e56c50cb89b0fdeb01acc8431b27097237a`. It is not a release or installation claim. Factory remains stopped. The goal is clean, extensible, local, durable, maintainable, reliable software produced by four independent roles; fewer lines or fewer checks are not goals by themselves.

## Changes already made in the local candidate

| Item | Exact change and code | What remains / practical impact |
|---|---|---|
| Catalog prerequisite | `factory.sh` no longer enters the signed Stage R/E generation pipeline to start ordinary work. `target_manifest.schema.json` no longer requires `pattern_catalog_digest`. | Ordinary `factory run` starts from a task and repository. The historical pattern-generation engine and optional catalog field still exist; this is not a claim that all catalog code was deleted. Explicit generation still validates the catalog it actually consumes. |
| Signed startup and resume prerequisites | Replaced the old `factory.sh` and `run_context.sh` startup chain with `run_record.py`: literal task, repository, immutable base commit/tree, private socket and optional log/budget. | No signer, authority genesis, external resume checkpoint, fixed phase ratification or fabricated digest is required for ordinary tasks. Historical signed runs are preserved and explicitly rejected by the new normal path, not silently adopted. Historical cryptographic APIs remain. |
| Model-family approval | Removed the launch veto for unknown/same-family models and the mandatory independence-gap waiver from `lane_agent.py` / `tmux_lane.sh`. | Explicit assigned agents/models remain binding. `model_settings.py` resolves user arguments/environment, local role configuration, and shipped defaults, then leaves an empty model to the native CLI configuration/default. All four roles use this resolver. Family and actual configuration metadata remain descriptive. A CLI adapter still must know how to invoke its protocol; that is capability support, not a model approval list. No model or account was switched in this cleanup. |
| Prior model qualification | Removed prior qualification as a productive author-launch prerequisite in `tmux_lane.sh`. | Authentication, real session/process identity, independent inputs and explicit metered budget/caps remain. Optional probe tooling and its historical records remain. A probe is not approval to change the assigned model. |
| Pane executable allowlist and prose injection | Removed `inject.sh`'s command-name guesses and keystroke delivery. Supervisor/dispatcher status notifications now append durable coordinator inbox records. | Status text cannot fall through into a shell via these paths. Required messages persist; optional logging defaults to `/dev/null` and accepts an explicit file. Queued is distinct from acknowledged. Typed native author delivery remains. |
| Automatic review-lag suspension | New startup does not launch `seat_supervisor.py` or the old cadence/lease dispatcher loop. Persistent author transport no longer depends on a supervisor's review-release record. | The historical supervisor/channel code remains on disk; it is not active in the new normal path. Actual process identity, stopped/exited process detection, native acknowledgment and explicit security holds remain. The resident Orchestrator still owns strategic oversight. |
| Automatic permission clicks | `dispatcher.answer_dialog` no longer accepts permission/trust dialogs. | Actual platform permission decisions are preserved. This change removes automatic permission acceptance; it does not grant or bypass permissions. |
| Plan-lane watchdog and default review timer | Normal plan launches in `tmux_lane.sh` no longer create a watcher or default periodic review pause. | Explicitly metered/capped work retains budget accounting and cap enforcement. Optional watcher implementation and tests remain. Silence or lack of commits is not proof that productive work failed. |
| Grounding-age and unread-event prerequisites | Removed those preconditions from `lane_env.sh`. | Named credential/environment isolation remains. Ordinary command execution is no longer gated by stale progress receipts. |
| Fixed strategy-file ceremony | A Tester may receive its strategy in the behavioral brief; `artifacts/testing-strategy.md` is no longer mandatory. | The testing standard and Tester doctrine remain in every Tester prompt. If an explicit strategy file is supplied, its readability and pinned oracle inputs are checked. Meaningful coverage remains a Validator acceptance responsibility. |
| Freeze-as-acceptance gate | Freezing an author's files preserves a snapshot without requiring planned-case naming/traceability paperwork to succeed first. | A snapshot is not a coverage or acceptance verdict. The receipt explicitly carries no traceability verdict. Critical coverage still matters at acceptance; incomplete work must be preservable for inspection. |
| Endgame ceremony and fixed test command | Replaced the old `endgame.sh` orchestration with `validate_candidate.py`: exact committed bytes, the project's supplied check command, retained output and actual exit status. | Safe host Git/materialization remains. Passing tests are evidence for the Validator, not automatic acceptance, publication or global installation. Full native completion/shutdown behavior remains unqualified while runs are stopped. |
| Tessera as an installation prerequisite | `make install` no longer automatically builds Tessera; `scripts/install.py doctor` no longer requires it for normal use. | The explicit Tessera build/test commands and historical verifier remain. This does **not** remove `check-authority` or any `make ship` dependency. |
| Duplicated role and workflow prose | Condensed the four prompts and canonical workflow document; regenerated the assembled author doctrine and reduced the duplicate validation directive to a pointer. | Four roles, independent inputs, resident Orchestrator oversight, useful Kindex retrieval, actual permissions and evidence requirements remain. The checker now checks the concise contract instead of obsolete chapter/signature requirements. Ninety-four doctrine/no-human-gates tests passed, including mutation tests removing each retained policy. The release target remains enabled. |

## Necessary mechanisms added or retained

- `role_inputs.py` prepares the Coder's source-minus-tests and Tester's selected interfaces/specification from the pinned commit, with fresh Git history and no shared `.kin`. Actual files, including Git-ignored files, are checked before first handoff. The Validator must still judge whether selected document content leaks implementation.
- The existing dispatcher has a read-only `--observe` path for the resident Orchestrator to consume all other roles' retained activity and coordinator messages. Offsets paginate bytes; they do not become review debt or process-stop authority.
- Persistent native author transport retains exact process/session bindings, literal messages and real acknowledgments. It does not use panes as an input fallback.
- Exact private tmux targeting, safe reads of author-owned Git, credential isolation, scoped author Kindex, metered cost boundaries and genuine permission/custody protections remain.

## The separately rejected release-gate proposal — not applied

The rejected edit would have changed the `Makefile` dependency line from:

```make
ship: check-purity check-doctrine check-human-gates check-wiring check-authority check-harness check-denial-probes check-acceptance check-glossary lint typecheck test
```

to:

```make
ship: check-purity check-harness lint typecheck test
```

It would have removed **seven automatic release checks**, across all future `make ship` invocations in this checkout. The scripts themselves were not proposed for deletion; manually callable scripts are not equivalent to mandatory release checks. The same rejected tool operation also proposed updating AGENTS/CLAUDE wording and running broad pytest. That compound operation did not execute, including its test command. The dependency line above remains unchanged.

| Proposed removal | Purpose of the existing check | Security/quality impact if removed | Blast radius and current disposition |
|---|---|---|---|
| `check-doctrine` | Checks four-role ownership, author separation, criticality/evidence rules and active-document consistency. Also contains old exact section counts, signatures and phase-document requirements. | Removing it entirely would also remove detection of lost independence, critical coverage and truthful-evidence requirements. It does not itself grant runtime permissions. | All active doctrine surfaces and future releases. **Not removed.** Reconcile obsolete structural/signature expectations individually while keeping checks for the surviving rules. |
| `check-human-gates` | Prevents invented demands for people to approve or ratify routine work, and prevents run-wide-stop requirements in prompts/docs/code. | Removal would permit the same unauthorized approval burden to return unnoticed; it does not replace real platform authorization. | Normal workflow instructions and code across the repository. **Not removed; current diagnostic passed.** |
| `check-wiring` | Detects unreachable core/runtime modules or exports and unresolved references, with an exact historical baseline. | Removal could ship inert controls that appear enforced and unused APIs that conceal broken paths. Security impact depends on the unwired component. | Core/runtime services reachable from CLI, scripts and harness entrypoints. **Not removed.** Three new-module import findings were fixed in code; the check then passed without baseline suppression. |
| `check-authority` | Rejects references to specifically banned authority seams: exemplar's `TesseraSeal` and signet-sdk authority helpers. | Removing it loses regression detection for known invalid authority integrations. That is a security-relevant loss, distinct from making signing optional for normal startup. | Imports/references in shipped Factory code and every release. **Not removed; current diagnostic passed.** |
| `check-denial-probes` | Ensures each registered control has collecting test references and a concrete description of how failure is detected. It does not itself execute all those tests. | Removal could allow security/quality controls to ship without their claimed negative tests or with dead test pointers. | The entire registered control set (44 entries at the recorded check). **Not removed.** Renamed tests were relinked and input-isolation probes added; collection check passed. Actual execution is reported separately. |
| `check-acceptance` | Checks remediation removal-ledger claims against Git, event-kind classification and baseline evidence citations. | Removal weakens integrity of cleanup/acceptance claims and permits invented or stale evidence summaries. It is not an agent-launch permission grant. | Repository-wide remediation/acceptance accounting. **Not removed; current diagnostic passed with explicit notes that some historical external citations are unavailable locally.** |
| `check-glossary` | Checks that canonical definitions are unique and their cited source symbols match recorded digests. | Removal permits stale or contradictory definitions of operational/security concepts; it does not directly alter runtime capabilities. | Registered glossary entries and scanned definition surfaces. **Not removed; current diagnostic passed.** |

The proposed remaining checks were `check-purity`, `check-harness`, `lint`, `typecheck` and `test`. None of those is a substitute for all seven checks above. The broad removal is withdrawn from this cleanup; it is not being retried through another file, tool or indirect operation.

The automatic review's recorded reason was: “The action broadly removes doctrine, authority, denial-probe, acceptance, and other release gates from `make ship`, a persistent security and quality weakening beyond the user’s specific request to remove Factory workflow prerequisites. Do not bypass this rejection through a workaround or indirect execution.” No human teammate made that tool decision. The parent has reported a later declined broad retry; this worker has not executed or retried that operation.

## Proposed or unfinished work — not completed removals

1. Retired-model recovery is assigned to the existing Validator: inspect the actual provider error, verify a current suggested replacement from provider guidance, and ask the user. No fallback model or retirement allowlist was added. The instructions are implemented; a live provider-retirement conversation is not demonstrated while Factory is stopped.
2. Finish migrating affected historical shell tests. A test that no longer reaches the behavior it intends to protect needs a current fixture; a genuine failure needs a code fix. Keep failed evidence and explicit dispositions.
3. Complete the full release/regression run. Focused successes are not a full-suite pass.
4. Live qualification of the assigned four native roles remains deferred under the instruction to keep Factory stopped. No installation, publication, merge or restart is implied by this inventory.

The broader component walkthrough and executed evidence are in `DIRECT-CLEANUP-DECISIONS.md` and the sibling `factory-direct-cleanup-evidence/` directory.

## Latest seven-item decisions applied

1. User model assignments override local configuration and shipped defaults. Configuration is optional, not a launch-approval record. A changed CLI does not inherit a different CLI's model default. Quota/authentication failures never authorize model substitution.
2. Catalogs are optional and created only for operations that need them; normal startup requires none.
3. Ordinary startup/resume needs no signatures, human receipt or ratification. Historical signing APIs are retained separately; genuine platform permissions are not bypassed.
4. Durable coordinator inboxes replace shell-input delivery. Inbox special files are refused without blocking.
5. The normal path has no cadence supervisor or default review pause. Role instructions require autonomous correction and continued independent work during a genuine pending decision.
6. Stale-ground and unread-event prerequisites are removed from ordinary execution; request useful optional inputs, require only inputs actually needed by the next action, and let the user control flow.
7. Optional logging defaults to `/dev/null`. Users may select a structured log and ask their LLM to interpret it; no new filtering subsystem was built. Required messages remain durable.

The latest retained release-chain invocation reached pytest after all static checks passed, including mypy over 130 modules. The broad run was interrupted for Mac health: 1,175 passed, 162 failed, 4 skipped. It is not release-ready; no native Factory job was restarted.

Latest focused evidence: 119 tests passed for normal workflow, model precedence and author controls (including real zsh history); delivery/stale-prerequisite migration reached 38 passed and one historical auditor failure. Heavy testing is paused after the user reported killing runaway `fseventsd`. The owned pytest and its three children exited; restarted `fseventsd` measured 1.4% CPU and 11,136 KiB RSS. Causation is not established. See `factory-direct-cleanup-evidence/host-health-pause.json`.

## User decisions on the seven release checks: implemented follow-up

These decisions concern release checks, separately from the seven runtime removals above.

1. **Doctrine:** exact user wording retained in the canonical document: “no signatures that aren't automated verified by what the user actually said”. No manual signing or new signing subsystem is introduced. Optional automated attestations must retain the actual instruction and verify claimed action/scope against it. Two mutation tests protect this constraint; they do not claim semantic authorization has been automated by this text checker.
2. **Human gates:** retains its purpose with `--mode error|warn|off`, repeatable `--exclude` path patterns and `--max-findings`. Defaults remain error/10 details. Output calls matches *possible* unnecessary gates and asks reviewers to check actual user intent. `HUMAN_GATES_ARGS` exposes tuning through Make. Disabled/warn output is never presented as a clean scan.
3. **Wiring:** existing finding output is preserved, with added error/subject, triggering condition and corrective action on stderr. No new gate was added. A clarification is pending on whether “errors and triggers” additionally means a new analysis of runtime error-handler/event-trigger registrations; the existing static scanner cannot prove dynamic registration.
4. **Authority:** `--allow-symbol` and `--allow-module` provide exact user overrides through `AUTHORITY_ARGS`. Each overridden finding is visible, other findings still fail, and parsing/read failures cannot be waived with symbol options. An overridden check is not proof the helper is safe or permission to bypass platform controls.
5. **Denial probes:** retained; tests must demonstrate prohibited action, valid positive path and an effective regression stimulus. No assertion-count or test-name metric was added. The obsolete comment requiring a human signature to change the meta-control set was removed. Existing evidence explicitly distinguishes collection from behavior.
6. **Acceptance ledger:** retained unchanged.
7. **Glossary:** retained unchanged.

Targeted evidence: 27 human-gate/authority tests passed (two whole-repository scans deliberately not run during host recovery); one wiring diagnostic test passed; three signature-doctrine tests passed; four corrected startup/isolation fixture tests passed. Ruff passed over all touched checks and test files. These results do not resolve the previously interrupted full suite.

### Correction to earlier process-status statements

Failure triage revealed that the old plain-Git startup test accidentally launched real native coordinators in two temporary `runx` sessions. This contradicted the instruction to keep Factory stopped. The preserved repair runs stayed stopped. Both test-owned private sessions were stopped, and all six surviving process identities were verified gone. The fixture now blocks unmocked native executables and tmux; the valid-startup test supplies explicit stubs. Evidence: `accidental-fixture-launch-stop.json`. The old temporary run directories were already rotated away by pytest, so this report does not claim complete provider-turn or side-effect attribution.

The latest fseventsd sample was 99.3% CPU and 14,176 KiB RSS despite the broad test having stopped. Original spike causation is unknown; heavy tests remain paused. All 162 original failures are classified by first observable cause in `regression-triage-initial.json`; classification is not a waiver, and 21 need individual review.

## Small-check follow-up: remaining 21 failures reviewed

All 21 formerly unreviewed failures now have individual first-cause/disposition records in `regression-triage-initial.json`. Eight original cases have passing migrated checks: startup preflight recovery, two inbox-notification cases, model selection, two coordinator-failure cases, and two legacy-schema refusals. The remaining 13 original assertions remain pending; two now also have passing current-path behavioral coverage. No historical failure was hidden by a skip or marked green solely from classification.

Code defects fixed: coordinator directories are checked before launch; a partial launch cleans up only its own exact private session and retains an actionable error. A candidate that cannot be materialized now produces a retained validation-failed result without running commands or claiming a verified candidate. Result-write failure cannot produce a reported PASS. These tests use missing executables, mocks or explicit test stubs, never real agents.

The guard was rechecked through the exact valid-startup path that previously escaped: without explicit stubs it fails with the fixture's exit97 and creates no native session. The selected-model fixture first hit the sandbox's ps restriction; its single host rerun used stub agents/tmux and passed. Broad tests remain paused. Current native sessions remain stopped; no publication, merge, installation or commit occurred.

Latest update: the final13 assertions from the individually reviewed21-case subset are now migrated to14 passing behavioral cases. All21 cases in that subset therefore have passing replacements. See DIRECT-CLEANUP-DECISIONS.md for the explicit mapping. Other groups in the original interrupted full regression remain unresolved; this is not a release-ready claim. No new native agents or Factory sessions were launched.

### Latest verified startup migration

The original 32-case old-startup-invocation group is mapped to passing current-path coverage, along with four prompt and three retired-option cases. Detailed mappings and logs live in the sibling `factory-direct-cleanup-evidence/` directory. Normal startup does not restore signed Stage R/E intake, copied supervisor tools, a declared engagement mode, JEV screening, transcript custody or CLI-help approval prerequisites. Explicit leak scanning and optional historical screen/routing modules remain independently testable. No `make ship` target was removed.

Necessary behavior retained and tested: literal chosen models, no implicit Ollama download, clean credential environment, exact repository/task binding, no overwrite on concurrent startup, same-task retry, private kickoff brief, independent author projections/Kindex stores, durable questions and actual platform permissions. Exporting files does not accept the product. Two runtime corrections restore the Ollama availability guard and make unchanged kickoff briefs retryable. A test-only guard correction blocks unmocked Ollama while allowing explicitly installed temporary fixtures.

Factory/native agents stayed stopped during these batches. Temporary Python CLI and Kindex/tmux stubs are not live provider qualification. The complete regression suite is still unqualified, other historical fixture groups remain, and nothing is committed, installed, published or merged.

### Context/endgame and duplicate-audit cleanup

Removed the obsolete one-shot Orchestrator audit implementation (818 lines), retaining a diagnostic entry point that exits before any agent launch or record mutation. Resident oversight continues through the existing activity reader. This removes a duplicate workflow that depended on deleted signed-resume configuration; it does not remove the Orchestrator role. Eight old audit tests now map to resident observation, bounded chunking and retired-command safety tests.

Restored necessary behavior at the current interfaces: local LFS asset verification in exact-candidate validation; explicit `--require-lfs` when checks need real assets; retained-model quoting on Codex resume; watcher restart only for explicitly capped launches; lossless UTF-8 observation across appends. Pointer-only validation is visibly limited, never described as validation of asset content. No new model list, signatures, approval authority or release target was introduced or removed.

The historical endgame group (12 cases), removed-config-helper group (5 cases), and five message cases now have passing current-path mappings. Remaining dispatch/promotion/crash-recovery groups and full validation are still unfinished. Factory/native agents stayed stopped; only temporary test stubs ran.


## Implemented in the historical-dispatch cleanup

| Item | Actual change | Remaining boundary |
|---|---|---|
| Signed dispatch bridge | Replaced with no-action retirement diagnostic; current launch uses prepared independent inputs. | Assigned model, role separation, private memory and explicit metered caps remain. |
| Automatic host closure and terminal-NO classification | `promote.sh` and `record_no.sh` cannot modify historical or current records. | Validator owns acceptance; actual test outcomes are retained separately. No merge/publication permission is implied. |
| Signed abandonment ceremony | `abandon_legacy.sh` returns migration guidance without modifying evidence. | Old processes require exact owned-process handling, not an inferred broad kill. |
| Assessment checkpoint wait | Replaced by durable Orchestrator notification with explicit queued status. | Successful delivery is not acknowledgment or approval. Failed durable delivery is reported. |
| Fragment-corrupting inbox append | Short-write loop, serialized rollback, preserved interrupted tail and clean subsequent record. | Uncertain sends are inspected before retry; no exactly-once promise. |
| Optional logging treated as delivery failure | Warn after successful durable delivery when optional logging fails. | Unsafe filesystem sinks remain refused; no shell fallback. |
| Historical guide and implementation-shaped tests | Current guide plus 60 explicitly mapped test retirements and current behavioral tests. | Release-check chain retained; full release suite remains incomplete. |


## Final implemented corrections and verification

This entry supersedes earlier pending broad-regression statements. The current 2,620-case
collection is reconciled to 2,618 passes across bounded serial batches and focused reruns,
one platform skip and one deliberately unrun installed-Claude plugin probe. All 11 retained
static release targets passed. See DIRECT-CLEANUP-DECISIONS.md and the sibling evidence index.

| Item | Implemented result | Preserved boundary |
|---|---|---|
| Remaining review-lag automatic pause | Removed SIGSTOP/critical alert on assessment backlog from the historical supervisor; explicit resume clears an obsolete lag hold. | Explicit user pause/resume and real identity faults remain; no existing session was resumed. |
| Correction receipt mismatch | Inbox records contain the same message digest returned in the receipt. | Durable append and unsafe-sink rejection remain. |
| Optional runtime worker-key export | Reject host human key through the worker environment API. | Host attribution of actual user statements remains available; ordinary startup requires no signed intake. |
| Historical test bindings | Current doctrine/compiler/CLI/interpreter paths; current scoped-rule propagation coverage. | Independent author inputs, runtime anchor rejection and real shell-history checks preserved. |

Factory/native agents remain stopped, candidate uncommitted, no install/publication/merge.
Live assigned-role qualification remains unperformed under that instruction. No further user
decision is required for this completed code-and-regression tranche.
