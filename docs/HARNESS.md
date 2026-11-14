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

A start that follows an earlier life of the same run waits for that life to finish
stopping. A seat's wrapper, once signalled, waits for its program and writes its exit
record last, which can be seconds after its tmux session is gone; and a seat's exits
journal is one file for every life of the seat, its rows naming no process. A new life
started in that time would have the old life's exit record land beside its own start and
be taken for it: the launcher would report "the <role> stopped during startup" and close
a healthy new session. So before anything is started, the launcher looks at the exact
processes the seat's process receipt names (`<role>-process.json`: the wrapper and its
program, each by PID and start time) and waits up to 15 seconds for them to end. If one is
still running then, the start is refused (exit 70) with its PID and the retry command;
if the receipt or the process table cannot be read, it is refused the same way. The
earlier life is never signalled, and no session is replaced. A lane message that restarts
an ended lane session does the same for the lane's slot (exit 75 while its earlier
process is still stopping, with the message left planned), so the lane's watcher never
counts an earlier life's exit record against the new one. A new round of a lane
(`tmux_lane.sh launch --round ...`) does it for every earlier slot of that lane whose
window is gone: a closed window or an ended tmux server can leave the earlier round's
wrapper and program running in the lane's clone for a while, and a new round is not
started beside them. The launch waits up to 15 seconds per earlier slot, then refuses
(exit 75 while one is still running, naming it; exit 70 when that cannot be told) before
it cleans the earlier round's project files, records anything or starts anything. A lane
that has never run is not made to wait. The lane's window is looked at once per launch: a
lane seen running is refused as running (exit 70) even if its window closes during the
launch, so nothing is cleaned or started on any path that did not wait for the earlier
round.
`python harness/lane_watchdog.py previous-owner --receipt <file>` is the same check on its
own: exit 0 when the earlier owner has stopped or there is none, 75 while it runs, 70
when it cannot be told.

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
- **A session that has ended is restarted on its own conversation, not replaced.** When a
  lane message finds the lane's pane ended (the session finished or was closed, or the
  watcher ended it for a review pause or a wrap-up, whose exit it verifies first),
  `tmux_lane_message.sh` asks the registry whether the Factory can restart that program.
  For Claude Code it starts the hosted session again in the pane with Claude Code's own
  `--resume <uuid>`, under the wrapper, launcher, settings, Kindex, boundary and custody
  the launch recorded (the launch record's `native` entry), and with a watcher as at first
  launch. The conversation's delivery record is kept, so the first prompt is never given
  again. The message is given only after the session reports starting, and is recorded
  delivered only on the session's own prompt report. If the session does not report
  starting within 90s (it may be asking its folder-trust question) the command exits 75
  with the message still planned; if the program ends first it exits 70. A lane whose
  program the registry reports as neither resumable nor restartable
  is refused with exit 70 and the message stays planned: the operator
  continues it with the program's own resume, or starts a new session with
  `--replace-session`. A lane launched before the `native` entry was recorded is refused
  the same way. Shown with a stand-in program that calls no model, not with Claude Code.
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

A `cursor-agent` lane runs `agent --workspace <clone>` interactively on the same terminal
host, never `-p`. The operator's HOME, `~/.cursor`, sign-in, rules, hooks, MCP servers and
permission settings are the lane's too, untouched and all still in force.
The interface was proven against the interactive program by the independent tester on
2026-11-13 (cursor-agent 2026.11.05 and 2026.11.13), and the tester then ran this adapter
against real Cursor 2026.11.13 on the operator's subscription, with no API key and no
blanket approval:

- At `aa8870b`: a lane started by `tmux_lane.sh launch --agent cursor-agent` took its first
  prompt, and a second message sent by `tmux_lane_message.sh` was reported `observed` with
  matching text, in the same session. Cursor accepted the generated permission file and
  listed `lane-kindex` ready beside the operator's servers.
- At `aae71ef`: the model called the lane server's `scope_info`, read-only, and no separate
  MCP approval question appeared after the workspace-trust answer.

Not exercised live, and still open: restarting a lane through the lane commands (below),
a Cursor coordinator seat, a required
Signet guard enforcing end to end, what the lane's Kindex returns for a seeded search, and
a live launch into a clone that already has Cursor project files (that refusal and the
candidate guard were exercised on temporary repositories, not through a live lane).

- **Permissions are the operator's own; the Factory approves nothing for them.** With the
  operator's configuration loaded, Cursor's blanket flags would reach everything they have
  configured, so none is passed. `--force` ("force allow commands unless explicitly
  denied") and `--approve-mcps` ("automatically approve all MCP servers") are gone.
  `--trust` is gone too: Cursor documents it as headless-only, so it granted nothing in an
  interactive session. Cursor therefore asks its own questions in the lane's window: whether
  to trust the workspace, and whether to approve the lane's MCP server. `agent mcp enable
  lane-kindex`, run by the operator in the lane's clone, is Cursor's per-server approval;
  the Factory does not run it, because where Cursor keeps that list is not documented. What
  the lane may run without asking is whatever the operator's own Cursor permission
  configuration allows (`~/.cursor/cli-config.json`). A launch prints all of this as "this
  start asks you" and records it as `start_requires`.
- **The one rule the Factory adds.** Cursor's documented per-server permission is a project
  rule: `.cursor/cli.json` with `permissions.allow: ["Mcp(lane-kindex:*)"]` and an empty
  `permissions.deny`, which Cursor's schema requires to be present (real Cursor refused to
  start without it). It covers the tools of the lane's own Kindex server and nothing else. That server is named `lane-kindex`
  rather than `kindex` so the rule cannot match a server of the operator's.
- **Where lane configuration goes.** Cursor offers one lane-scoped place: the project files
  `.cursor/hooks.json`, `.cursor/mcp.json` and `.cursor/cli.json` in its workspace. The
  wrapper writes them into the lane's clone when the session starts: four observation hooks
  (`sessionStart`, `beforeSubmitPrompt`, `stop`, `sessionEnd`), any required Signet hooks,
  the lane's Kindex, and the rule above. They apply beside the operator's own.
- **The target's files are never touched.** If the clone already has any of those files, it
  is the target project's own; it is not edited, merged into or replaced, and the lane refuses to
  start with that reason. Nothing is written outside the clone.
- **Nothing leaks into a candidate.** The paths are listed in the clone's
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
- **An ended session is restarted by a lane message.** Observed of Cursor itself
  (cursor-agent 2026.11.13, 2026-11-14, the account's own sign-in with no API key, a
  synthetic workspace Cursor already trusted, four one-word turns; the hook capture was
  read independently): `agent --resume=<id>` given nothing reported nothing, not even a
  start, as the tester had seen. `agent --resume=<id> ... -- <message>` (Cursor's own
  `[prompt...]`) reported that message at once on the same conversation id, byte for byte,
  including one that began with a dash and held a newline, then its stop with the same
  generation id; it sent no start report. A message then typed into the resumed session
  was reported normally. So `tmux_lane_message.sh` restarts an ended Cursor session in its
  pane with the lane's own arguments, `--resume=<id>`, and the message after `--`. The
  wrapper records that hand-over before the program exists, under the message's own id,
  and a message is never handed over twice: if the restarted program ends before
  reporting, that message is `unknown` and a message with other content, or from another
  sender, is needed. A message to a running session is typed into it, as before; a first
  launch is unchanged.
- **Restartable, not resumable.** The registry keeps two facts apart. *Resumable*: the
  watcher may end a live session to pause it for review or wrap it up, and a lane message
  continues it. *Restartable*: a lane message restarts a session that has already ended,
  and nothing more. Cursor is restartable only (`agents.py describe --agent cursor-agent`
  prints `restartable`; it is not in the watchdog's list). So `tmux_lane.sh` refuses
  `--review-every-minutes` and `--wrap-up-minutes` for a Cursor lane, and the watcher
  never ends a Cursor session in order to continue it. The reason is evidence, not
  design: restarting a Cursor session after a clean end was run against Cursor; the
  watcher's stop of a live Cursor session, and a restart after it, was not.
- **What was run, and against what.**
  *With a stand-in program that calls no model*, through `tmux_lane.sh` and
  `tmux_lane_message.sh`: live follow-up, clean end, restart with the message `observed`
  on the same conversation id, a follow-up typed into the restarted session, a failed
  restart, no repeat, a restart by another sender's message; five messages, five different
  marks; and a launch asking for a review pause refused.
  *Against Cursor itself, once, through the public commands* (2026-11-14,
  cursor-agent 2026.11.13, the account's sign-in with no API key in the lane's
  environment, a new synthetic workspace, default sandbox setting, no blanket option):
  `tmux_lane.sh launch --agent cursor-agent` started the lane; Cursor asked its trust
  question for the new workspace, which also enables that workspace's own server (the
  lane's Kindex), and it was answered `a` in the lane's window; the first message was
  `observed`; the model read the lane's two input files and asked to run one `shasum`
  and `wc` command, approved once. After a clean end (`.cursor/` removed, exit 0),
  `tmux_lane_message.sh ... status` restarted the same conversation id, **with no trust
  or approval question asked**, and that message was `observed` (`via: launch-argument`,
  exact); Cursor's answer drew on the first turn. A second status message was typed into
  the restarted session and `observed`. Cursor's own hook receipts for the run: one
  conversation id; start, prompt, stop, end, prompt, stop, prompt, stop, end (no start
  report on the resume); three prompt reports with three generation ids, each bound to
  one message. For the two typed messages Cursor's report carried no mark and they were
  matched by their exact text, as before; the launch-argument message carried its mark.
- **Not run against Cursor, and not claimed.** A watcher's stop of a live session and a
  restart after it (which is why no pause is offered); a restart after a crash, or after
  any end that was not a clean exit; a restart whose program fails; a restart in a
  workspace Cursor was not asked to trust at launch; a required Signet guard on a first or
  a restarted session. A message to a Cursor session that ended in one of those ways is
  not refused: the same restart is attempted, and what Cursor does then has not been
  seen.
  One more thing seen: Cursor's workspace trust follows the exact spelling of the path
  (`/private/tmp/...` was trusted, the same folder as `/tmp/...` was asked about again).
- **Typing, as observed.** Cursor's terminal takes a bracketed paste followed by the typed
  mark and Enter: the tester's second message at `aa8870b` was reported with matching text.
  One message, one Cursor version; a message Cursor does not report stays `submitted`.
- **A forced commit is refused at validation.** `.git/info/exclude` keeps the lane's project
  files out of an ordinary `git add`, and the lane's prompt says to leave them alone, but
  an author can still force one into a commit. `validate_candidate.py` therefore refuses a
  candidate that carries one (`status: validation-failed`, `exit_code: 70` in its retained
  result) before running any check. The run's base commit is consulted first: a file that
  is byte-for-byte what the target held there is the target's own, whatever it contains,
  and is never refused. A file that is new or changed since then is refused when it is
  byte-for-byte what a lane's manifest recorded, or the run launched a Cursor lane; a file
  the base did not have at all is also refused when it holds the lane's own hook command or
  server name. A file the target already had and has changed, in a run with no Cursor
  lane, is the target's own work and is left alone. The guard looks at exactly the three paths a
  Cursor lane writes; a copy of a lane's file committed under another name or elsewhere in
  the tree is not looked for. A link at one of those paths is compared as a link and never
  followed. Lane manifests are read from the run's control tree and from the lane clone's
  own private runtime.
  When the run's base commit cannot be read (Git fails or times out), nothing is concluded
  about the candidate: the result is `status: validation-unavailable`, `exit_code: 70`,
  with the Git error, and no check is run. Only a base commit that Git read and that lists
  nothing at a path counts as the target not having it.
- **Signet sees the lane's Kindex as Kindex.** A required Signet hook evaluates a call to
  `lane-kindex` as `mcp__kindex__<tool>`, so the lane's own memory satisfies rules written
  about Kindex. A call to the operator's server named `kindex` is refused under that hook
  with a message naming the lane's server: it must not stand in for the lane's memory.
- **The refusal for a target's own files comes first.** For a Cursor lane `tmux_lane.sh`
  checks the three paths (after clearing the lane's own leftovers) before any budget is
  reserved or launch recorded, and refuses there with the path that is occupied.
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

### Muse and Antigravity lanes are hosted through the provider's own session server

A `muse` or `agy` author lane no longer drives the provider's terminal UI. The lane wrapper
runs `harness/protocol_host.py` in the lane's window, and that program starts the
provider's own stdio server (`muse serve`; `agy acp --stdio`) on the operator's HOME,
sign-in and settings, and speaks the provider's published protocol to it (Muse's session
protocol; the Agent Client Protocol). `META_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY` and
`AGY_LLM_GATEWAY_API_KEY` are always removed from the server's environment, however the
host was started and whatever it was told. No print mode, no
`muse exec`, no settings edited, and nothing written into the lane's clone: there is no
`.muse/hooks.json` and no `.agents/plugins/lane/` for a hosted lane. The two sections after
this one describe the earlier terminal route and its evidence; lanes no longer use it.

**This is transport, not a guard.** It is groundwork. A protected launch, or a message
that would restart an ended session under protection, is refused with exit 77 before the
provider's program is run at all (its sign-in check included), before any record and
before any process. Protected means any of: `FACTORY_REQUIRE_SIGNET`,
`FACTORY_REQUIRE_AUTHOR_CUSTODY` or `FACTORY_REQUIRE_BOUNDARY` set to anything but empty or
`0`; a Signet contract or digest, an author-custody config or digest, or a boundary
contract or digest named (by variable or by the launcher's own options); or a boundary
record handed to the lane wrapper. The kernel boundary is in that list because it has not
been qualified around a host program and the provider's server under it. The refusal is made by `protocol_host` itself, which every hosted
start goes through, so the direct entries refuse as the launcher does: `lane_agent.py run`
(exit 77, nothing recorded), `lane_agent.py preflight` (exit 77, the provider's account
command is not run) and `protocol_host.py run`. The reason is specific: neither provider asks about every action
(what an existing grant, rule or permissive mode already allows is never presented), both
skip a hook that fails or times out, and Muse 1.4.20 lists a per-connection hook method in
its exported experimental schema that its servers do not serve (`hook/declare` answers
"method not found"). So no gate on every tool action and no read isolation is established
for either, and none is claimed.

- **Identity.** A new session is the provider's `session/new` (Antigravity) or
  `session/start` with an id the host mints (Muse). An ended session is re-entered only
  with `session/resume` and the kept id, and the answer must name that id. An error
  (Antigravity `-32603`, Muse `-32020`), another id, no id, a timeout or the server ending
  sends no prompt, makes no other session, and ends the host with exit 70; the message
  command then exits 70 and says why. `session/load` is never sent: Antigravity accepts an
  unknown id through it, and its own resume error suggests it.
- **Messages.** The first message, and the message that restarts an ended session, is
  given when the host starts; the wrapper records that hand-over first
  (`<slot>-launch-intent[-<message id>].json`) and never gives a message twice. A later
  message to a running lane goes from the wrapper to the host over the host's own socket
  (the host is checked by PID and start time, and by session id), and the host gives it to
  the server: Muse as `turn/start` with `ifBusy: "queue"` under a command id that is the
  message's own and is on disk before it is sent (a record of those ids that is there
  and damaged is refused, never replaced); Antigravity as `session/prompt`, one at
  a time, only after the previous prompt's answer. One message of the wrapper's may be
  unaccounted for at a time; another is answered `busy`.
- **What is recorded.** `observed` only on a report that is that submission's own. Muse:
  `turn/started` for that command id (or the turn id Muse's acknowledgement named). For
  Antigravity an update names no prompt, so none is a receipt by arriving (a late frame of
  the turn before, a mode or option notice): the receipt is the session's echo of the
  message carrying its mark, or else the answer to that very `session/prompt` request, so
  in practice a message to Antigravity is `submitted` while its turn runs and `observed`
  when it ends. A prompt the server refuses is not received, and the host ends (exit 70).
  A frame counts for the lane only if it names the lane's session id exactly; one naming
  another, or none, settles nothing. The turn's end is a separate
  report carrying the provider's own word (`completed`, `failed`, `cancelled`; a stop
  reason), kept on the message as `response_stop`, and the reply's text goes to the lane
  log, so a lane's done line is seen. A failed or cancelled turn is not called done.
- **Questions to the operator.** A permission or approval request the session does raise
  is shown in the lane's window and held, and the lane reports `asking-operator`. A person
  presses `a` to allow that one action or `d` to deny it. Only once-only choices are ever
  sent; nothing is decided because nobody answered. `FACTORY_LANE_SANDBOX=off` remains the
  one explicit selection of the provider's own permissive mode (Muse `allowAll`,
  Antigravity `--dangerously-skip-permissions`), as before.
- **Model and effort.** Muse: `--model` is its server's own option; effort is given on
  each turn. Antigravity: the lane's model and effort are put in force through the
  session's own options, and after the last change the server's own account of its
  options must show both in force together (a later change can undo an earlier one). A
  model the session does not offer, or a final answer that does not show what was asked
  for, is refused before any prompt rather than run under another's name.
- **Not resumable by the watcher.** The registry reports both as `restartable`: a message
  restarts an ended session, and nothing stops a live one for a pause or wrap-up, so
  `--review-every-minutes` and `--wrap-up-minutes` stay refused. A slot an earlier Factory
  started as the provider's terminal UI is not restarted as a hosted one (exit 70; start a
  hosted session with `--replace-session`).
- **After the host ends,** `muse resume <id>` or `agy --conversation <id>` reopens the
  same session in the provider's own UI.
- **What was run.** Both providers against stand-in servers that call no model, through
  `tmux_lane.sh` and `tmux_lane_message.sh`: launch, a later message, the done line, a
  restart on the same session, an unknown and a wrong session refused with no prompt and no
  `session/load`, and the guard-required refusals. Muse also against Muse Code 1.4.20's own
  server on its echo provider (no model, a throwaway HOME): launch, a later message, a
  restart on the same session, and an unknown session refused. **Not run:** any model turn
  or tool call on either provider; Antigravity's real server at all (its frames before a
  prompt's answer, and its model option names, are taken from the protocol and from
  recorded handshakes, not from a turn); an approval answered in the window against a real
  server; the lane's Kindex reaching a real session.

### Muse lanes are Muse Code's own interactive session

A Muse lane runs `muse --workspace <clone>` on the same terminal host, with the operator's
HOME, sign-in, settings, hooks and MCP servers intact: no `muse exec`, no replaced
`XDG_CONFIG_HOME`, no link to the sign-in file. Everything below was observed on Muse Code
1.4.20 with its echo provider (which calls no model and needs no sign-in), by the adapter's
author and independently by the tester on 2026-11-13. It is converted, not accepted.

- **First message at launch.** Muse reports a session's start only when its first prompt is
  submitted, so the lane's first message is Muse's launch argument (after `--`), with its
  mark. Its delivery is `submitted` from the start (`via: launch-argument`) and `observed`
  when Muse's prompt report carries the mark.
- **Handed over once.** A launch argument reaches Muse the moment it is spawned, before any
  session state can exist. So the wrapper first writes `<slot>-launch-intent.json`,
  durably, and only then spawns Muse; the record is never cleared. If one already exists
  for the slot, or the slot's delivery record shows a first message handed over to a
  session that never reported, the start is refused before Muse runs: what the earlier
  start's session did with the message is unknown, and unknown is not permission to give
  it again. Start a replacement lane, or remove the record knowing the message was not
  taken.
- **Later messages are not typed.** After the launch argument the Factory types nothing
  into a Muse session. A later message to the lane is accepted, kept `queued` with `held`
  giving the reason, and never typed automatically; a person can enter it in the lane's
  window. The reason is the tester's finding at `ef8fdd7`
  (`/tmp/factory-muse-ef/RESULT.md`): with an operator Stop hook still deciding (it slept
  12 seconds, then asked Muse to continue), a follow-up typed 1.2 seconds after the lane's
  own stop report was taken into the continued earlier turn. Muse reported no prompt for
  it, the continuation's stop carried the earlier turn's id, and the message stayed
  `submitted`. A stop report comes before Muse decides whether to stop, so it is not a safe
  moment, and no other report marks one. Until Muse is shown to report the point after
  which it will not continue, or to offer a delivery route of its own (`muse
  session-message send` refused an ordinary process: `sender_unverified`), a review, an
  answer or a wrap-up notice does not reach a Muse lane unattended. The Factory approves
  nothing and changes no hook or approval setting to get around this.
- **A held message is delivered by a person, and then it settles.** `tmux_lane_message.sh`
  answers a held message with exit 75, says it was not typed, and names its entry file
  (`<slot>-held-<message id>.txt` in the lane's own directory, owner-only), which holds the
  one thing that delivers it: the message followed by its own mark,
  `[factory-message:<mark>]`. The operator pastes that entry in the lane's window when
  they judge the session ready. Nothing is typed for them and no moment is chosen for
  them. When the session's own prompt report is exactly that entry, the message becomes
  `observed` with `via: operator-entered`, its receipt goes into the dialogue journal,
  `persistent_lane.py status` shows it, the entry file is removed, and the next message to
  the lane is accepted (and held in its turn). Until then a further message is answered
  `busy` and names the held one.
  **The words without the mark settle nothing.** Messages often have the same words (every
  status request does), and a report of those words cannot be told from an earlier
  submission reported again, or from a later model call of a turn already under way. The
  mark is made from the session and the message's id, so a prompt that carries it was
  submitted for that message and after it was held. No counter of the provider's is taken
  as proof of a fresh submission. Also settling nothing: the entry of an earlier message,
  a mark that is no message's, and the mark around other words. Each is the operator's own
  input and is counted as foreign. The message command is not the way to look for the
  receipt: once a message is received, the same words sent again are a new message.
  "Exactly" ignores line endings and whitespace at the two ends, and nothing else.
  Run against Muse Code 1.4.20 with its echo provider (no account, no model), through the
  lane wrapper, 2026-11-14, with two messages of the same two-line words: the words alone
  left the first held; its entry file, pasted, settled it and the file was removed; the
  second was then accepted with a different mark; the first message's entry entered again,
  the words alone, and the words with a mark that was nobody's each left the second held;
  its own entry settled it. Before this change no entry settled anything: the first
  message stayed held for good and every later one was `busy`.
  Not run: the message command itself against a Muse lane (the launcher needs Muse's
  account sign-in), or a signed-in model.
- **Hooks beside the operator's.** The lane's observation hooks are the project file
  `.muse/hooks.json` in the lane's own clone, which Muse runs in addition to the operator's
  hooks. It has the custody a Cursor lane's project files have: never written over a
  target's own file (the launch is refused), excluded from commits, removed at exit and
  before a relaunch or a freeze, and refused in a candidate commit. A per-run
  `-c hooks=...` is refused in a lane's arguments, because Muse replaces the operator's
  hooks with it.
- **Kindex beside the operator's.** The lane's Kindex is one more server for that run,
  `-c mcpServers.lane-kindex=...`, which Muse adds to the operator's servers and writes
  nowhere. Its variables are set by `/usr/bin/env` in front of its command, because Muse
  refuses literal `env` values in that form.
- **Reports.** `SessionStart`, `UserPromptSubmit`, `Stop` and `SessionEnd`, in Claude Code's
  names. A prompt's report and its turn's stop report share a turn id, so that stop is
  recorded on the message as `response_stop`; when a Stop hook continues the turn, the
  continuation's stop carries the same turn id and replaces it.
- **Permissions.** Nothing is imposed: no `--yolo`, no `--disable-request-user-input`.
  `FACTORY_LANE_SANDBOX=off` is the one configuration that adds `--yolo`.
- **Sign-in check.** `muse usage`, Muse's own account command. The sign-in file is not
  opened. A plan lane refuses `META_API_KEY`, which would override the account.
- **Resume limit.** `muse resume <id>` continues the same session, and the registry builds
  it whenever a session id is given. A resumed session reports nothing until a prompt is
  typed, and Muse refuses a prompt beside `resume`, so it stays `starting` and is not typed
  to: unattended resume is not supported, the registry reports Muse as not resumable by the
  Factory, and wrap-up and progress-review pauses are not offered for it. That is less than
  the print-mode lane did, and it is the cost of not using print mode.
- **No Signet guard, and why.** A launch that requires a guard is refused for `muse`.
  Observed on 1.4.20 with the echo provider and a user shell action (`!touch …`, which
  Muse reports as tool `shell`): `PreToolUse` fires, also under `--yolo`; an explicit deny
  with a reason blocks the tool, and so does exit 2. The tool runs when the hook exits 1,
  prints something that is not JSON, exceeds its timeout, or cannot be started. No
  supported fail-closed hook setting has been qualified; tested `failClosed`/`required`
  entries were disabled. This tested hook-only configuration cannot provide fail-closed
  enforcement across those failures. A user shell action is not held for approval even in
  `--approval-mode untrusted`, and the permission hook is not consulted for it: this
  user-shell probe did not exercise Muse's approval path.
- **Not established.** Anything a signed-in model run would show: a model calling
  `lane-kindex`, a model's own tool call through these hooks, and any report of Muse
  asking the operator a question. Muse refreshes its own sign-in file when it starts; that
  is Muse's doing.

### Antigravity lanes are Antigravity's own interactive session

An `agy` lane runs `agy` in its clone on the same terminal host, with the operator's HOME,
sign-in, settings, hooks, plugins and MCP servers intact: no `--print`, no private HOME, no
links to the operator's state, and no `agy mcp add` or `agy plugin install`, which write the
operator's configuration. It is a partial, author-lane adapter and is not accepted. The
evidence is of two kinds, both on agy 1.5.8 in a trusted throwaway workspace on 2026-11-13,
and they are not the same thing:

- **Lifecycle probes, no model turn.** By the adapter's author and independently by the
  tester: a probe plugin whose `PreInvocation` hook stopped each prompt before the model
  (`terminationReason: TERMINAL_CUSTOM_HOOK`). These show how Antigravity loads a
  workspace plugin and what its hooks report. They are not model turns and show nothing
  about a response or a completion.
- **Actual executions of this adapter.** By the author only: `lane_agent.py run
  --persistent` hosting real `agy` twice, with the lane's own hooks, which do not stop the
  model, on the operator's subscription. Each gave one start report, one prompt report
  matched to the launch message, and one stop report with `terminationReason:
  NO_TOOL_CALL`. A third direct run captured the raw stop payload. The model's reply was
  not read or checked in any of them, and no tool was called.

- **A plugin in the clone.** The lane's hooks and its Kindex are the plugin directory
  `.agents/plugins/lane/` (`plugin.json`, `hooks.json`, `mcp_config.json`). Antigravity
  finds it there by itself once the workspace is trusted, with no `plugins.json`, runs its
  hooks beside the operator's and starts its server beside the operator's. The directory
  has the custody the other lanes' project files have: the launch is refused if the target
  already has a `lane` plugin of its own, the files are excluded from commits, removed at
  exit and before a relaunch or a freeze, and refused in a candidate commit.
- **Trust is the operator's.** Antigravity asks about a workspace it has not been told to
  trust, and only for that exact directory; a child directory is asked about again. The
  Factory does not answer.
- **First message at launch.** Antigravity reports a session's start with its first
  prompt, so the lane's first message is given at launch with `--prompt-interactive`, with
  its mark. The hand-over is recorded before Antigravity is spawned and never repeated for
  a slot, as for Muse.
- **Reports.** `SessionStart` names the conversation. `PreInvocation` runs before every
  model call and carries the latest user message, so it is the prompt report; a repeated
  report of the same message is counted once. `Stop` is recorded with Antigravity's own
  `fullyIdle`, `continuationActive` and `terminationReason`, as given.
- **Later messages are not typed.** No report is taken to mean the session is ready for
  input. `fullyIdle` was `true` on every stop seen, but none of those turns was continued
  by a Stop hook, which is the case that matters (it is what swallowed a typed message in
  Muse). A later message is kept `queued` with the reason and left for a person to enter:
  a review, an answer or a wrap-up notice does not reach an Antigravity lane unattended.
- **A held message is delivered by a person, and then it settles.** `tmux_lane_message.sh`
  answers a held message with exit 75, says it was not typed, and names its entry file
  (`<slot>-held-<message id>.txt` in the lane's own directory, owner-only), which holds the
  one thing that delivers it: the message followed by its own mark,
  `[factory-message:<mark>]`. The operator pastes that entry in the lane's window when
  they judge the session ready. Nothing is typed for them and no moment is chosen for
  them. When the session's own prompt report is exactly that entry, the message becomes
  `observed` with `via: operator-entered`, its receipt goes into the dialogue journal,
  `persistent_lane.py status` shows it, the entry file is removed, and the next message to
  the lane is accepted (and held in its turn). Until then a further message is answered
  `busy` and names the held one.
  **The words without the mark settle nothing.** Messages often have the same words (every
  status request does), and a report of those words cannot be told from an earlier
  submission reported again, or from a later model call of a turn already under way. The
  mark is made from the session and the message's id, so a prompt that carries it was
  submitted for that message and after it was held. No counter of the provider's is taken
  as proof of a fresh submission. Also settling nothing: the entry of an earlier message,
  a mark that is no message's, and the mark around other words. Each is the operator's own
  input and is counted as foreign. The message command is not the way to look for the
  receipt: once a message is received, the same words sent again are a new message.
  "Exactly" ignores line endings and whitespace at the two ends, and nothing else.
  For Antigravity this is the same code as for Muse and was not run against Antigravity:
  only the mapping of its prompt report (`PreInvocation`, `lastUserInput`) to the text
  that is compared was checked.
- **No completion line.** In the actual executions the stop report carried no model text
  under any field, so a lane's done line is not seen from its reports.
- **Permissions.** Nothing is imposed. `FACTORY_LANE_SANDBOX=off` is the one configuration
  that adds `--dangerously-skip-permissions`.
- **No Signet guard.** A launch that requires a guard is refused for `agy`. A user shell
  action typed in the session (`!touch …`) ran with no hook called. This user-shell route
  did not exercise `PreToolUse`; model-tool hook failure behavior remains unverified.
  Antigravity does have native
  permission rules (`command(...)`, `mcp(server/tool)`, a `strict` tool-permission
  setting, by its changelog) that do not depend on a hook running; whether a lane can set
  them for one session, and how a hook's approval combines with them, is not established.
- **Resume.** `--conversation <id>` is built whenever an id is given. No lane command
  restarts an ended session; the registry reports `agy` as not resumable by the Factory.
  That is deliberate, on the qualification below.
- **Restart qualification (agy 1.5.9, 2026-11-14): not enough to restart an ended
  session.** Lifecycle probes by the adapter's author, their reports read independently by
  the tester: normal permissions (no `--dangerously-skip-permissions`), no print mode, no
  API key, a trusted throwaway workspace, a probe plugin at `.agents/plugins/lane/`. Every
  `PreInvocation` list ended in a handler returning `terminationBehavior: "terminate"`.
  Seven invocations gave seven stop reports with `terminationReason: TERMINAL_CUSTOM_HOOK` and
  `executionNum: 0`, and no `PostInvocation` report. By Antigravity's own hook reports,
  then, each turn was ended before the model was invoked. That is what the reports show;
  it is not an independent account of usage or billing. None of this is evidence about a
  model turn.
  - *When the hook runs, it sees the conversation first.* `--conversation <id>
    --prompt-interactive <message>` on an existing conversation sent no `SessionStart`;
    its `PreInvocation` named the same conversation (`initialNumSteps` above 1) and carried
    the message exactly, a leading dash and a second line included. The hook's `terminate`
    was followed by a stop report naming the hook as the reason.
  - *An unknown conversation is not refused.* Antigravity warned on stderr, made a new
    conversation, reported `SessionStart` and `PreInvocation` under the new id with the
    message submitted to it, and later exited 0.
  - *A failed hook is skipped.* A `PreInvocation` handler that exited 1, printed text that
    is not JSON, ran past its `timeout` (and was killed), or named a command that does not
    exist was logged as failed by Antigravity, and the next handler in the list ran.
    Antigravity's hook documentation says nothing about a failed handler; its changelog
    describes failing hook commands as "silently skipped".
  - *Inferred, not observed.* That a turn whose only `PreInvocation` handler fails goes on
    to call the model. Seeing it takes a model call, and these probes were built so that
    the hook reports show none. What was seen is that the failure did not stop the turn
    and that the handler after it did.
  - *What follows.* A lane's hook can stop a turn in the wrong conversation only when it
    starts, answers in time and Antigravity has loaded the plugin. With the kept
    conversation gone and the hook not answering, the lane's message could proceed in a new
    conversation without a trustworthy binding report; these probes do not establish
    whether a model call follows. That hook is also the lane's only report. The Factory
    therefore cannot hold a restarted session to its conversation, and restarting an
    ended Antigravity session is not built. Unchanged: a later message is held for a
    person to enter and settles by its mark; a launch that requires a Signet guard is
    refused; `--review-every-minutes` and `--wrap-up-minutes` are refused for `agy`.
- **Coordinator seats.** An Antigravity seat is allowed. It is not refused, and it is not
  put on print mode: it starts as Antigravity's own interactive session in its window, by
  the same seat command as before (`agy --add-dir <run> ... --prompt-interactive <prompt>`),
  and is driven there by a person. Starting one prints a warning that wakes and messages
  are not delivered into it: the earlier delivery was print mode fed over stdin, which is
  never used, and no supported route replaces it yet. A wake addressed to such a seat is
  reported `unsupported` and stays in the inbox. So an Antigravity seat works attended and
  does not work unattended.
- **Not observed.** The name Antigravity gives the lane's server (`lane_kindex`, by its
  changelog), a model calling it, any tool-use hook, and a model turn in a continued
  conversation.

### Interactive Codex lanes are Codex's own session, hosted

The lane agent `codex`, the default author lane, runs `codex --no-daemon --no-alt-screen`
on the same terminal host, with the operator's sign-in, configuration, hooks and MCP
servers intact. `--no-daemon` keeps it off the operator's shared Codex server.
`codex-interactive` is the same adapter by its own name, and is the name every record
carries: the launcher turns `codex` into it at once, so a launch record, a turn record and
a lane message all say `codex-interactive`. No author lane runs `codex exec` for it and
nothing falls back to `codex exec`: a lane that an earlier Factory started as `codex exec`
is refused a message and told to start a hosted session with `--replace-session`. An
explicit `--model` and `--effort` are passed to the session (`-m`, `-c
model_reasoning_effort=`) and again when it is restarted; with no `--model`, the model
keys of the operator's own Codex configuration are recorded and passed the same way, as
they were for the `codex exec` lane, so the record names what ran. Billing is Codex's own
sign-in, checked before launch as before. Only `codex-ollama` still runs `codex exec`:
Codex's interactive program on a local Ollama model has not been run. **What changes for
an operator:** a default Codex lane now needs what an interactive session needs (the two
trusts below); without them it works but reports nothing. What follows was observed on
codex-cli 0.167.0 on the operator's ChatGPT sign-in on 2026-11-13, by the adapter's author
only: three one-word turns in direct probes, then two one-word turns of this adapter
through `lane_agent.py`. It is an author-lane adapter and is not accepted.

- **First message at launch.** Given as Codex's launch argument (after `--`), with its
  mark; Codex's own prompt hook reported it. The hand-over is recorded before Codex is
  spawned and never repeated for a slot.
- **Which session is the lane's.** Codex's hooks also report sessions the lane did not
  start (a background memory-writing agent of Codex's own was seen). The lane binds to the
  session whose prompt report carries the launch mark, not to the first start report.
  That binding then stands: a later start report from another session is counted and
  ignored, and does not stop deliveries.
- **Later messages go through `codex queue`.** Nothing is typed. The wrapper runs
  `codex queue --thread <session> --message <text>`; the delivery is `submitted`
  (`via: provider-queue`) from before the command runs and `observed` only when Codex's
  prompt hook reports the mark. Codex's "Queued message …" answer is kept as `queue_said`
  and is not a receipt. The command must exit 0 and must itself name the lane's session
  as the one it queued for; otherwise the delivery is `unknown` and is never given again.
  An `unknown` delivery is settled only by Codex itself: a later prompt report carrying
  that message's mark and exactly what was given makes it `observed` (`settled_from:
  unknown`).
- **Reports.** `SessionStart`, `UserPromptSubmit`, `Stop`, `SessionEnd`, in Claude Code's
  names. A prompt's report and its turn's stop share a turn id, and the stop carries the
  reply text, so a lane's done line is visible.
- **Hooks beside the operator's.** The lane's report hooks are the project file
  `.codex/hooks.json` in the lane's own clone, under the custody the other lanes' project
  files have (launch refused over a target's own file, excluded from commits, removed at
  exit, refused in a candidate).
- **Two trusts, neither given by the Factory.** Codex asks about a folder it has not been
  told to trust and says it saves the answer. Codex also skips a hook it has not been told
  to trust: the operator reviews the lane's hooks with Codex's `/hooks`, or launches with
  `FACTORY_LANE_SANDBOX=off`, the explicit per-run choice, which passes Codex's own
  `--dangerously-bypass-hook-trust` beside its approval and sandbox bypass. That option
  covers every enabled hook in the run, not only the lane's. Without either, the lane
  works but reports nothing: it stays `starting` and its first message stays `submitted`.
  A per-run `-c projects."<folder>".trust_level="trusted"` did not suppress the folder
  question.
- **Codex's own resume and queue, observed (2026-11-14, four one-word turns, one
  session).** `codex resume <options> <id>` keeps the session id and its hooks go on
  reporting it. Given no message, the resumed session reported nothing, not even a start,
  and first stopped on Codex's own "Update available" question. `codex queue` to that
  silent session then produced its start report (`source: resume`), the message and its
  stop. `codex resume <options> <id> -- <message>` produced start, message and stop at
  once, with no update question. A message queued while a Stop hook was held for 15s was
  reported as its own prompt, mark intact, in the second the hook returned.
- **An ended session is restarted by a lane message.** `tmux_lane_message.sh` restarts it
  in its pane with `codex resume`, the lane's own options, the session id last, and the
  message after `--` as the launch argument, because a resumed session given nothing
  reports nothing. The wrapper records that hand-over before the program exists, under
  the message's own id, and a message is never handed over twice: if the restarted
  program ends before reporting, that message is `unknown` and a message with other
  content, or from another sender, is needed to restart the session. The registry
  therefore reports `codex-interactive` as resumable by the Factory, and wrap-up and
  progress-review pauses are offered for it. Run through `tmux_lane.sh` and
  `tmux_lane_message.sh` with a stand-in program that calls no model: live message by
  queue, ended, restarted with the message `observed`, a failed restart, no repeat, and a
  restart by another sender's message.
- **Run once against Codex itself, through the public commands** (2026-11-14, codex-cli
  0.167.0, the operator's ChatGPT sign-in with no API key in the lane's environment, a
  synthetic folder Codex already trusted, `FACTORY_LANE_SANDBOX=off`):
  `tmux_lane.sh launch --agent codex-interactive` started the lane with no folder
  question; the first message was `observed` (exact) and the session bound by its mark;
  the model read the lane's two input files with shell commands, called the lane's
  Kindex, and answered. After a clean end (`.codex/` removed, exit 0),
  `tmux_lane_message.sh ... status` restarted the same session id with `codex resume`
  and that message was `observed` (`via: launch-argument`, exact); Codex's answer drew on
  the earlier turn. Two further status messages were `observed` through the queue. Codex's
  own hook receipts for the run: one session id throughout, four prompt reports with four
  different marks, none twice.
- **Not run against Codex.** A restart after a crash rather than a clean end; a restart
  whose program fails; a watcher's pause or wrap-up of a Codex session.
- **Not observed.** A message queued while the model is in the middle of a turn; any tool
  hook or Signet guard in the interactive session.
- **No `codex exec` behind it.** `tmux_lane_message.sh --native-session-file`, which handed
  an exited interactive session to `codex exec resume` after reading Codex's own session
  file, is removed and refused by name. A `codex-interactive` lane that an earlier Factory
  started with nothing hosting it is refused a message for the same reason. To continue
  such a conversation the operator uses Codex's own `codex resume <session id>`.

### Memory isolation, said plainly

A launch records `memory_isolation` and prints it. `lane-only` means the lane sees its
scoped Kindex and none of the operator's MCP servers. `beside-operator` (Claude, Cursor, Muse,
Antigravity and Codex lanes) means the operator's settings and MCP servers are intact and the lane's Kindex is
added beside them: any memory server the operator configured is visible to the lane, so
Coder and Tester independence rests on their separate inputs and clones, not on memory.
That is the consequence of not suppressing the operator's configuration, and it is the
operator's to weigh.

### Providers not yet native, and exactly why

`codex-ollama` lanes load the operator's whole Codex configuration but still run `codex
exec`; the lane agent `codex` is the hosted interactive session (above). Prime lanes still
start in print mode with a private configuration. Each launch records and prints `native_blocker`, asked by the lane
agent's own name: it is empty only for `claude` and `claude-ollama`,
and `python harness/agents.py describe --agent <lane agent>` prints it. For `cursor-agent`,
`muse`, `agy` and `codex-interactive` it says what was run and what is still open. The
registry reports `cursor-agent`, `muse` and `agy` as not resumable by the Factory (a Cursor
session that has ended is restarted by a lane message, which is a different thing), so wrap-up
and progress-review pauses, which continue a session, are not offered for them. The rest:

| Provider | Established, and from where | Not established |
|---|---|---|
| Prime | Nothing: the program is not installed where this was written. | Everything. |
| Codex on Ollama (`codex-ollama`) | That `codex exec --oss` runs on a model the local Ollama server has, with the operator's configuration intact. | Everything about Codex's interactive program on a local model: whether its hooks report, whether `codex queue` reaches it, whether `codex resume` keeps its session. Until one of those runs it stays on `codex exec`. |

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
