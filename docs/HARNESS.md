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

Not exercised live, and still open: resuming a lane, a Cursor coordinator seat, a required
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
- **Resume limit.** A resumed Cursor session sent no start report in the tester's run. Until
  it reports something it stays `starting` and nothing is typed to it; a message returns
  `busy` with that reason. An operator typing a prompt in its window would make it report,
  but that is a person's intervention, not unattended operation: unattended resume of a
  Cursor session is not supported yet.
  "Not resumable" is the Factory's own limit and is recorded in one place, the registry
  (`agents.py describe --agent cursor-agent` prints `single`); the watchdog's list of lanes
  it may continue agrees with it. Cursor's own resume is not refused or dropped:
  `agents.py turn --agent cursor-agent --resume <id>` builds `--resume=<id>`, the same
  conversation. A message to a Cursor lane goes to its running session or is not delivered;
  it never starts a new session in its place.
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
- **Coordinator seats.** An Antigravity seat is allowed. It is not refused, and it is not
  put on print mode: it starts as Antigravity's own interactive session in its window, by
  the same seat command as before (`agy --add-dir <run> ... --prompt-interactive <prompt>`),
  and is driven there by a person. Starting one prints a warning that wakes and messages
  are not delivered into it: the earlier delivery was print mode fed over stdin, which is
  never used, and no supported route replaces it yet. A wake addressed to such a seat is
  reported `unsupported` and stays in the inbox. So an Antigravity seat works attended and
  does not work unattended.
- **Not observed.** The name Antigravity gives the lane's server (`lane_kindex`, by its
  changelog), a model calling it, any tool-use hook, and `--conversation` reporting.

### Interactive Codex lanes are Codex's own session, hosted

The lane agent `codex-interactive` runs `codex --no-daemon --no-alt-screen` on the same
terminal host, with the operator's sign-in, configuration, hooks and MCP servers intact.
`--no-daemon` keeps it off the operator's shared Codex server. The lane agents `codex` and
`codex-ollama` still run `codex exec` and are not converted. What follows was observed on
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
- **Not observed.** A message queued while a turn is running or while a Stop hook is
  deciding; any tool call, tool hook or Signet guard in the interactive session; a resumed
  session; `tmux_lane.sh` end to end.
- **Not resumable by the Factory.** `agents.py turn --agent codex-interactive --resume <id>`
  builds `codex resume <id>`, but no lane command restarts an ended session, so wrap-up
  and progress-review pauses are not offered.
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

Codex lanes (`codex`, `codex-ollama`) load the operator's whole Codex configuration but
still run `codex exec`; the hosted interactive lane agent is `codex-interactive` (above). Prime lanes still start in print mode with a
private configuration. Each launch records and prints `native_blocker`, asked by the lane
agent's own name: it is empty only for `claude` and `claude-ollama`,
and `python harness/agents.py describe --agent <lane agent>` prints it. For `cursor-agent`,
`muse`, `agy` and `codex-interactive` it says what was run and what is still open, and
the registry reports all four as not resumable by the Factory, so wrap-up and progress-review
pauses, which continue a session, are not offered for them. The rest:

| Provider | Established, and from where | Not established |
|---|---|---|
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
