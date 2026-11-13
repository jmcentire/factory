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

The launcher reports a run started only after each seat has registered its process. A
seat that stops or never registers fails the start: the launcher closes the run's tmux
session, keeps the task record, and queues the seat's last wrapper line for the Validator
with the command that retries the start on the same record:

```sh
python harness/run_record.py start --runs <runs> --run <run>
```

Start runs from a shell outside any sandbox. On macOS the run's tmux server and every
seat inherit the launching shell's sandbox, and an agent there cannot save its state; the
launcher warns when it detects one.

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

### Claude Code is the operator's own interactive session

Claude Code is never run in print mode (`-p`), never billed to an API key, never has its
own state files read or rewritten, and never has the operator's settings, hooks, plugins,
`CLAUDE.md` or MCP servers suppressed. A Claude seat or lane is the interactive program,
started through the operator's launcher on a terminal the lane wrapper owns
(`harness/native_session.py`) and shown in its tmux window, where the operator can use it.

- **Launcher.** `FACTORY_CLAUDE_LAUNCHER` names the command. Unset, it is `cl` when the
  operator's login shell defines it, otherwise the `claude` executable. A shell function is
  run by the login shell (`$SHELL -l -c`), which sources the operator's profile itself. The
  API-billing variables are unset immediately before the launcher runs; none is ever set.
- **What is added.** `--name=<run>-<slot>`, `--session-id <uuid>` (or `--resume <uuid>` to
  continue), `--settings <file>` carrying four observation hooks beside the operator's own,
  the assigned `--model`/`--effort` if any, the lane's Kindex by `--mcp-config`, and the
  run root by `--add-dir` for coordinators.
- **Permissions are the operator's, through every launcher.** The Factory passes Claude Code
  no permission mode: not `bypassPermissions` for author lanes and not `default` for
  coordinators, both of which it used to impose. What a session may do without asking is
  decided where the operator decides it natively: their launcher (`cl` passes its own
  permissive flags, and stays permissive) and their Claude Code settings. An operator who
  wants the permissive mode from the `claude` executable selects it explicitly with
  `FACTORY_LANE_SANDBOX=off`, and the Factory then passes Claude Code's own
  `--dangerously-skip-permissions`. Nothing is passed over a launcher that is the
  operator's own shell function, even then: the function is the more specific choice. A
  lane launch prints which of these applies. A session whose settings make it ask leaves
  the question in its window; from the Factory's side it stays `prompt-observed`, or
  `asking-operator` when Claude Code reports the question.
- **Messages** are typed into that terminal, to the exact process the wrapper started,
  each followed by a typed mark `[factory-message:<id>]`. A state names only what was
  observed: `queued` (accepted, not one byte typed), `submitted` (typing began; if the
  session is working, the message is in Claude Code's own input queue), `observed` (the
  session's own prompt hook reported a prompt carrying the mark), `unknown` (typing began
  under an earlier wrapper process and no report was seen). `busy` means it was not
  accepted, with the reason. `observed` is receipt and the end of what can be known; there
  is no "acknowledged" or "completed". Asking again reports the state and never types a
  message twice, across wrapper restarts too. One message of the wrapper's is unaccounted
  for at a time; that is its custody rule, not a claim about the session.
- **What the hooks can establish.** They are observation hooks added beside the operator's
  own, which run in parallel and may reject a prompt or make the session continue past a
  stop. A prompt report means the prompt reached the session, not that it was accepted. A
  stop report means the session reported ending a response, not that the work is complete
  or successful, and it is never tied to a message: on Claude Code 2.1.323 a prompt typed
  during a continued stop was taken from the queue while the earlier turn was still
  finishing. `phase` names the last report: `starting`, `started`, `prompt-observed`,
  `stop-reported`, `asking-operator` (a permission or input question is on screen; nothing
  is typed then, because keys would answer it), `waiting-for-input` (Claude Code's own
  `idle_prompt` notice, about a minute after it stops). A folder-trust or sign-in question
  keeps the seat at `starting` with its first message `queued`; answer it in the window.
  Nothing is inferred from the screen, from silence or from elapsed time, and nothing is
  typed into a session that has not reported starting.
- **When a message is typed.** Never because a phase looks idle. It is typed when the
  session has reported starting, no earlier message of the wrapper's is unaccounted for,
  and the session is not asking the operator a question. Claude Code queues input it
  receives while working, as it does for a person typing.
- **Limits.** How Claude Code reports pasted text is its own business, so a report is
  matched by the typed mark, and `text_match` says whether the reported text was also
  byte-identical (`exact`), carried only the mark (`token`), or was the message without
  the mark (`body`). A retained delivery record that cannot be read, or that belongs to
  another conversation which received input, stops the start: nothing is retyped.
- **Status.** `python harness/run_record.py --runs R --run ID status` lists each coordinator
  seat under `seats`. `python harness/persistent_lane.py status --root RUN_ROOT --slot SLOT`
  prints one live session, and exits 70 with the reason once its process has ended; the
  retained record then holds `phase: exited`.
- **Resume.** A session that reported starting keeps its id in `<slot>-session-id`
  (`-thread-id` for authors). Starting the seat again continues that conversation with
  `--resume`, interactively.
- **Interrupt** is refused for these sessions: Claude Code runs no stop hook for an
  interrupted turn, so the harness could not tell a stopped turn from a running one.

### Cursor lanes are Cursor's own interactive agent

A `cursor-agent` lane runs `agent --force --trust --approve-mcps --workspace <clone>`
interactively on the same terminal host, never `-p`. The operator's HOME, `~/.cursor`,
sign-in, rules, hooks and MCP servers are the lane's too, untouched and all still loaded.
The interface was proven against the interactive program by the independent tester on
2026-11-13 (cursor-agent 2026.11.05 and 2026.11.13); this adapter has been run only against
a stub shaped by that evidence.

- **Where lane configuration goes.** Cursor offers one lane-scoped place: the project files
  `.cursor/hooks.json` and `.cursor/mcp.json` in its workspace. The wrapper writes them into
  the lane's clone when the session starts: four observation hooks (`sessionStart`,
  `beforeSubmitPrompt`, `stop`, `sessionEnd`), any required Signet hooks, and the lane's
  Kindex. They load beside the operator's user-level hooks and servers.
- **The target's files are never touched.** If the clone already has either file, it is the
  target project's own; it is not edited, merged into or replaced, and the lane refuses to
  start with that reason. Nothing is written outside the clone.
- **Nothing leaks into a candidate.** The two paths are listed in the clone's
  `.git/info/exclude`, so no commit carries them. The wrapper removes exactly what it wrote
  (recorded with digests in `<slot>-project-files.json` under the lane's private runtime)
  when the session ends. A killed wrapper cannot; the next launch of that lane and a freeze
  both remove them first (`native_session.py clean-project --manifest <file>`), and a file
  whose bytes changed since is left and named instead of deleted.
- **Session identity.** Cursor chooses its conversation id; the session's own start report
  names it and it is bound then (`session_id_source: reported`) and kept in
  `<slot>-thread-id`. The wrapper continues a conversation when it is started with
  `--resume=<id>`, but no lane command does that yet: an author lane whose process has
  ended is replaced with `--replace-session`, or resumed by the operator by hand.
- **Receipt.** States are Claude's: `queued`, `submitted`, `observed`, `unknown`. Cursor
  also gives each prompt a generation id that its stop report repeats, so an observed
  message gains `response_stop` (with Cursor's `status`) when the session reports that its
  response to that prompt ended. That is the session's word that it stopped, not acceptance
  of the work.
- **Resume limit.** A resumed Cursor session sent no start report in the tester's run. Until
  it reports something it stays `starting` and nothing is typed to it; a message returns
  `busy` with that reason. An operator typing a prompt in its window would make it report,
  but that is a person's intervention, not unattended operation: unattended resume of a
  Cursor session is not supported yet.
- **Not established.** That Cursor's terminal takes a bracketed paste followed by typed text
  and Enter the way Claude Code does. If it does not, messages stay `submitted`.
- **Coordinator seats are not converted, for a reason.** A Validator or Orchestrator works in
  the operator's own repository, not a lane's clone. Writing `.cursor/` project files there
  would be writing the operator's files, which this adapter never does. A Cursor coordinator
  therefore still runs its interactive program directly in its pane, and automatic inbox
  wake remains unsupported for it. It needs lane configuration loaded from outside the
  workspace (the candidate is `agent --plugin-dir`, unqualified) before it can be hosted.
- **Export integrity.** A freeze copies every regular file in the clone except `.git` and
  the target's own declared excludes, so a project file still present would be exported.
  That is why the files exist only while the session runs, why a freeze removes any that a
  killed session left before it copies anything, and why it stops if one of them changed.

### Memory isolation, said plainly

A launch records `memory_isolation` and prints it. `lane-only` means the lane sees its
scoped Kindex and none of the operator's MCP servers. `beside-operator` (Claude, Cursor and
Codex lanes) means the operator's settings and MCP servers are intact and the lane's Kindex is
added beside them: any memory server the operator configured is visible to the lane, so
Coder and Tester independence rests on their separate inputs and clones, not on memory.
That is the consequence of not suppressing the operator's configuration, and it is the
operator's to weigh.

### Providers not yet native, and exactly why

Codex lanes (`codex`, `codex-ollama`) load the operator's whole Codex configuration but
still run `codex exec`. Antigravity, Muse and Prime lanes still start in print mode with a
private configuration. Each launch records and prints `native_blocker`, asked by the lane
agent's own name: it is empty only for `claude`, `claude-ollama`, `cursor-agent` and
`codex-interactive`, and `python harness/agents.py describe --agent <lane agent>` prints it. They are not converted
because doing so today would cost the lane its Kindex and any way to send it a message:

| Provider | Established, and from where | Not established |
|---|---|---|
| Antigravity | From its own `agy changelog`: hooks.json events `SessionStart`, `PreInvocation` (input carries `lastUserInput`), `Stop` (input carries `continuation_active`); follow-up messages queue until the turn ends by default; plugins may bundle MCP servers and a workspace may list plugins in `.agents/plugins.json`. | That a project `.agents/hooks.json` and a workspace plugin load without an edit to the operator's configuration or a trust prompt. `agy mcp add` writes the operator's configuration and is not an option. |
| Muse | Probed live on muse 1.4.6 (2026-10-12) and from its help: events `sessionStart`, `userPromptSubmit`, `stop`, `notification`, `sessionEnd`; hooks by per-run `-c hooks=<json>` or project `.muse/hooks.json` (off until the workspace is trusted); project MCP file `<repository>/.mcp.json`. | Whether `-c hooks=` adds to or replaces the operator's hooks, and the `userPromptSubmit` payload. No sign-in status command, so the preflight still reads whether a sign-in is stored. |
| Prime | Nothing: the program is not installed where this was written. | Everything. |
| Codex (`codex`, `codex-ollama`) | From `codex --help` and this harness's own use: the interactive program is `codex [PROMPT]` (lane agent `codex-interactive`); `codex resume [SESSION_ID]`; `codex queue` queues a message for an existing session; hooks given by `-c` are skipped until the operator trusts them. The operator's configuration is already intact. | Any report from the interactive program that it started, received a prompt or stopped, and that `codex queue` reaches a live interactive session. Until then a `codex-interactive` lane can be neither messaged nor resumed by the Factory, so it cannot replace `codex exec` for unattended work. |

To clear a row, a live session of that program must show: a report when the session is
ready for input, a report carrying a submitted prompt, a report when it stops, and the
lane's Kindex loading from the additive mechanism named. Nothing is typed into a program
that cannot say it is ready, because the keys could answer a sign-in, trust or permission
question instead.

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

## Release checks

Release checks remain in `make ship`; focused checks alone do not justify claiming
that the full release is green. Prior run evidence remains on disk and is never
silently restarted by an ordinary command.
