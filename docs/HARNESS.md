# The Harness

The current workflow is defined in [SOFTWARE-FACTORY.md](SOFTWARE-FACTORY.md).
The harness records inputs, launches the chosen native conversations, carries messages,
and runs project checks. The user owns scope and model choices. The Validator owns task
acceptance; the resident Orchestrator provides independent oversight. Coder and Tester
work from separate inputs. No catalog, model approval list, signatures, external
ratification, or assessment-backlog clearance is required to start ordinary work.

## Start and inspect a task

```sh
harness/factory.sh my-task /path/to/task.md --repo /path/to/project
harness/status.sh my-task
```

Use `--runs PATH` for a different local run directory. Use `--log PATH` only when optional
human-readable status copies are wanted; the default is `/dev/null`. A reader may ask an
LLM to summarize retained logs. Durable decisions, questions, blockers and handoffs are
retained regardless of that option.

The task record pins the repository, commit, tree, task bytes and private tmux socket.
An existing run is never silently overwritten or converted. Model selection follows
explicit choices, project configuration and shipped defaults, in that order. A provider
error does not authorize a model substitution. For a retired default, the Validator
verifies a current replacement and asks the user.

The launcher creates resident Validator and Orchestrator conversations. A kickoff
Validator reuses its existing conversation instead of creating another one. Each task
has its own private tmux socket; inspect and control only the exact owned session.
Never send status prose to a shell or fall back to another pane when an agent exits.

## Author inputs and conversations

The Validator uses `harness/role_inputs.py prepare` to make separate author repositories.
Coder receives source minus withheld tests; Tester receives selected public interfaces
and specifications, or an empty tree with the behavioral brief. Fresh Git histories
prevent omitted files from leaking through prior commits. The Validator must also inspect
semantic content; a digest cannot establish that a document contains no implementation.

`harness/tmux_lane.sh` launches those prepared inputs with the assigned native agent and
model. `harness/tmux_lane_message.sh` continues the retained conversation with typed
messages. Process/session identity and role boundaries remain checked. Optional metered
work needs an explicit budget and a reserved spend cap; plan-based work does not need a
fabricated budget. A successful reservation retry reuses the same cap. Corrupt or partial
budget evidence is preserved and reported, not treated as free capacity.

Every role retrieves relevant Kindex context when authorized. Author memory remains
private, with curated common context; a shared live graph must not leak one author's
work to the other. Retrieval receipts are not an execution prerequisite.

## Messages and oversight

`harness/inject.sh` places coordinator messages in durable file inboxes. It cannot send
raw text to an author pane. Author messages use the typed conversation path above.
A queued message is not an agent acknowledgment. Inbox appenders serialize writes;
failed appends roll back their own bytes, and a killed writer's unterminated fragment
is retained separately from subsequent messages. This is not an exactly-once delivery
protocol: after an uncertain outcome, inspect retained evidence before resending.
Optional-log failures warn after durable delivery and do not request another delivery.

`harness/orchestrator_checkpoint.sh RUN KIND DETAIL` queues a notification to the
Orchestrator. It does not wait for a cursor, assessment, or permission to continue.
The resident Orchestrator reads all retained activity using
`harness/dispatcher.py --run RUN --root RUN_ROOT --observe`, retaining returned cursors
for the next read. Output, exits and messages are evidence; silence is not proof of a
stall. Oversight addresses concrete findings without automatic backlog suspensions.

## Candidate checks and completion

```sh
harness/endgame.sh my-task EXACT_COMMIT --repo /candidate/repository -- python -m pytest
```

The Validator integrates independently authored tests before running the actual project
checks. Safe Git materialization uses the named commit, without repository hooks,
replacement objects, smudge filters or network downloads. Use `--require-lfs` when checks
need hydrated assets and `--lfs-objects PATH` for an explicit local object store. Retained
results state whether pointers remained, what ran, and its exit status.

Passing tests is evidence for the Validator's decision; it is not automatic task
acceptance, publication, installation or merge permission. Report the candidate,
observed behavior, failures, unrun checks and remaining work. Preserve evidence and
resident context until completion; stop only the intended owned processes.

## Historical interfaces

`dispatch_lane.sh`, `promote.sh`, `record_no.sh`, `abandon_legacy.sh` and
`orchestrator_wake.sh` now return migration diagnostics without reading or changing
run records. They no longer start signed dispatches, classify host terminal outcomes,
or create duplicate Orchestrators. Historical runs remain inspectable and are never
silently restarted by an ordinary command.

The optional generation and signed-ledger APIs under `factory_runtime` retain their own
contracts and tests. They are not prerequisites or another acceptance authority for
ordinary tasks. Prior harness architecture is available in Git history; it does not
override the current user's instructions. Release checks remain in `make ship`; focused
checks alone do not justify claiming that the full release is green.
