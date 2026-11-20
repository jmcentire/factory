# Software Factory

Factory coordinates an ordinary repository task with a Validator, a resident strategic Orchestrator, and independent Coder and Tester conversations. Start with `factory run NAME TASK --repo /path/to/repository`. No catalog, approved-model list, signatures or human ratification are required.

The current workflow is [docs/SOFTWARE-FACTORY.md](docs/SOFTWARE-FACTORY.md).

## Install

**On a Wander machine, use tooling-devshell.** Run `wander-tooling sync`; the devshell
ships Factory and keeps it updated. Your next devshell load moves an existing install
(including one from the installer link below) over to it, backing up every file it
replaces as `<name>.factory-backup-<hash>`. To keep working from your own checkout
(for example while developing Factory), create `~/.factory/keep-standalone` before
that load. Details: [tooling-devshell factory/README.md](https://github.com/wandercom/tooling-devshell/blob/dev/factory/README.md).

Without tooling-devshell, the local provisioning installer ships in this repository:

```bash
gh api -H "Accept: application/vnd.github.raw" repos/wandercom/chiron/contents/install.sh | bash
```

It downloads a checksummed `factory-setup` release binary, or builds the Go installer
from this repository when no matching release asset exists. The source fallback
requires Go. It provisions the pinned Factory checkout from `wandercom/chiron` and
writes machine-specific agent commands. Run `factory-setup --dry-run` to inspect its
plan, or build locally with `go build ./cmd/factory-setup`. The Go installer lives in
`cmd/factory-setup` and `internal/setup`; the Python runtime and its `make install`
workflow remain available below.

For an existing checkout from the old repository, update its origin before
running the provisioning installer:

```bash
git -C ~/Code/factory remote set-url origin https://github.com/wandercom/chiron.git
```

Needs git, Python 3.12+, tmux and the selected agent CLIs. Claude Code is needed only for its slash commands.

```bash
git clone https://github.com/wandercom/chiron.git ~/Code/factory
cd ~/Code/factory
make install
```

`make install` sets up the virtualenv, writes a `factory` launcher
to `~/.local/bin`, and installs the Claude Code commands `/validate`, `/engineer`, `/test`,
`/orchestrate`, `/build` and `/review` to `~/.claude/commands/`. Each command file is a short
loader that reads its prompt from this checkout, so a `git pull` updates them all. It ends by
running `make doctor`, which checks every piece and prints the exact fix for anything that is
missing. When doctor says `READY`, you are done. Otherwise apply the fixes it lists and run
`make doctor` again.

- **Change the locations** with `make install BINDIR=... CLAUDE_COMMANDS_DIR=...`.
- **Files you already have are safe.** The installer never overwrites or removes a file it did
  not write. If you edit an installed command (for example, to add machine-local auth notes),
  reinstalling leaves your version alone.
- **Update** with `git pull && make install`.
- **Remove** with `make uninstall`, which deletes only the unedited files the installer wrote.

To use it, open Claude Code in the repository you want to work on. Type `/build` for small
work, or `/validate` to start a full factory run. `factory --help` lists the CLI.

### Optional status reporting

Factory does not require Herald. Existing configured reporting still sends status, attention,
resolution and heartbeat updates to Herald's MCP. Set `FACTORY_HERALD=off` for local-only
reporting, or leave the default `auto` to use an existing token when present. Missing credentials
or remote failure preserve local records and report non-delivery without blocking the run.
See [the reporting contract](docs/SOFTWARE-FACTORY.md#defaults-forward-progress-and-optional-output).

### Choosing models

User choices win: lane `--agent/--model/--effort`, then `FACTORY_<ROLE>_AGENT/MODEL/EFFORT`, then role defaults in `~/.config/factory/config.toml` (or `FACTORY_CONFIG`), then shipped defaults. Shipped adapters are Codex for Validator/Coder/Tester and Agy for Orchestrator; no model name is mandated. An empty model uses the selected CLI's configured model, then that CLI's built-in default. A malformed or explicitly missing configuration reports an error instead of silently falling back.

For example, local defaults may contain:

```toml
[roles.coder]
agent = "claude"
model = "your-selected-model"
```

Changing the agent explicitly drops a different agent's model/effort defaults unless those are also assigned. Resuming a conversation preserves its recorded selection.

System One observations run at ordinary artifact and resident activity checkpoints. Without
usable credentials they report unavailable and work continues. Explicit permission to source
your profile can be configured in the same local file:

```toml
[system_one]
profile = true
```

`FACTORY_SYSTEM_ONE_PROFILE=1` or `0` overrides that setting when creating a run. The run
retains this non-secret choice for startup, resume and checkpoints; later ambient changes
do not silently grant profile access. The default is false. Only the bounded HTTP child
sources `~/.profile`; keys never reach coordinator, author or candidate environments.


If a provider reports a model retired, the Validator checks that provider's current replacement guidance and asks whether to use the suggested replacement. It does not silently migrate models. Unknown names, authentication failures and exhausted quota are not proof of retirement. No static model allowlist or background availability polling is needed.

| Role | Agents it can run | Naming a model (optional) |
|---|---|---|
| Validator | `codex` (default), `claude`, `agy`, `prime-agent`, `ollama` (Codex on a local Ollama model) | `FACTORY_VALIDATOR_AGENT`, `FACTORY_VALIDATOR_MODEL` (`FACTORY_VALIDATOR_OLLAMA_MODEL`, required for `ollama`) |
| Orchestrator | `agy` (default), `codex`, `claude`, `prime-agent` | `FACTORY_ORCHESTRATOR_AGENT`, `FACTORY_ORCHESTRATOR_MODEL` |
| Coder, Tester | `codex` (the default: Codex's own interactive session, hosted; `codex-interactive` is the same adapter by its own name), `claude` and `claude-ollama` (Claude Code's own interactive session, hosted; the second on a local Ollama model), `cursor-agent` (Cursor's own interactive agent, hosted; never `agent -p`), `muse` and `agy` (Muse Code and Antigravity, each run as its own session server on your sign-in and driven through its published protocol; transport only: refused wherever a Signet guard, author custody or a kernel boundary is required, and proven on a real model only by one text-only turn each, with no tool call; see `docs/HARNESS.md`), `codex-ollama` (`codex exec` on a local Ollama model; not converted), `prime-agent` (still print mode; not converted) | `tmux_lane.sh --agent ... --model ...` (`codex-ollama` and `claude-ollama` require one) |

`harness/agents.py` is the registry behind these lane agents: each entry states how the harness
starts the program (its own interactive session wherever one is hosted; `docs/HARNESS.md` says
what was run for each and what is still open), a model, an effort and a session to resume, and
which credential variables it reads. Those credentials reach a seat through an owner-only file, never
the tmux launch string. A `prime-agent` model may name its provider (`provider/model`). On a host
whose kernel cannot give Codex's sandbox a namespace (a gVisor container, for one), set
`FACTORY_LANE_SANDBOX=off`: the container is then the sandbox.

A `codex` lane with no `--model` runs the model your own Codex config selects. Each lane's launch
record states the model it actually ran. Ollama models must already be on the machine
(`ollama pull` them yourself). `codex-ollama` is metered, and `--metered` marks any other launch
metered: a metered lane launches only with a per-round spend cap, inside the run's `--budget`,
with no prior model qualification required.

**For an AI agent asked to install this:** clone it, run `make install`, then run `make doctor`
until it prints `READY`. Apply each listed fix as you go. Editing the user's shell profile (for
PATH), removing another `factory` install, or installing Python or Rust is the user's call, so
ask before doing it.

### Custom agent entrypoints

Set `FACTORY_AGENT_BIN` before `factory run` to an absolute, trusted directory
whose executable entries are provider wrappers or links to them. Use the CLI's
command name for each entry: `codex`, `claude`, `agent` (Cursor), `agy`, `muse`,
`prime-agent`, or `ollama`. Each wrapper can prepare its own environment and
then forward its arguments directly to the provider, for example
`exec /absolute/path/to/real/codex "$@"`. Use the real executable's absolute
path, since looking up `codex` on PATH can invoke the wrapper again. Keep the
directory and wrappers outside agent-writable workspaces. Forward the supplied
permissions and credential settings, and keep credentials restricted to the
provider context that Factory passed to the wrapper.

Factory records the directory and the resolved wrapper paths for the run. Those
entries take precedence over PATH for coordinator startup and the author lane's
checks, start, message delivery and resume. A `claude` entry also takes precedence
over `FACTORY_CLAUDE_LAUNCHER` for that run. Unlisted commands use the usual
lookup. Use a separate directory for a different set of wrappers. Adding,
removing, or retargeting an executable entry makes dependent operations fail,
and the refusal names each changed entry. A link is recorded by the file it
resolves to, so link to a stable path: a link into a versioned install directory
changes when that install is upgraded. Factory checks paths against accidental
configuration changes. Keep the wrapper contents stable for the lifetime of a run.

### Packaged runtime paths

`FACTORY_HOME` optionally selects a directory containing `prompts/` and `docs/`.
It supplies coordinator doctrine and the Tester's prompt/testing standard, while Python
imports and executable harness helpers still come from the installed Factory checkout.
Unset means this checkout; an empty or incomplete directory is rejected before run creation.
Relative paths resolve from the invoking directory. Set it consistently when starting or
restarting coordinators; resource contents are not copied or snapshotted.

`FACTORY_TMUX` optionally selects one executable (an absolute path is recommended for
packaged installations). Unset or empty means `tmux` on PATH. Pane launch resolves
the selection to an absolute executable before changing directories. It reaches
Factory's shell/Python controls, scrubbed coordinator environments and lane watchers;
it does not change the per-run private socket or select the operator's unrelated server.
A missing explicitly selected binary fails rather than substituting another executable.
Standalone `run-checkout.sh clean` uses the selected client and keeps work when
liveness cannot be established, including older records without a retained socket
directory or client errors. New runs record their socket directory when created.
Cleanup and archival use that recorded socket even when the caller changes
`TMUX_TMPDIR`.
`list` reports those runs as kept. A confirmed absent run/session may be cleaned.
`FACTORY_PYTHON` already selects the Factory-capable Python interpreter.

## Workflow and implementation

[docs/SOFTWARE-FACTORY.md](docs/SOFTWARE-FACTORY.md) defines the four roles and the
current workflow. [docs/HARNESS.md](docs/HARNESS.md) describes startup, independent
author inputs, durable messages, exact-candidate checks and completion.

Factory retains the task, repository, starting commit and tree before launching the
selected native conversations. The Validator interprets the requested capability and
accepts or rejects the result; the resident Orchestrator independently reviews the work
and process. Coder and Tester receive separate inputs and communicate through the Validator.

The generic core imports no target-specific code. Repository coordinates, public
contracts, role settings and target requirements are data loaded through adapter seams.
Module docstrings describe their invariants; [docs/GLOSSARY.md](docs/GLOSSARY.md) anchors
selected library terms to their code referents. Actual permissions and credentials remain
with the platform that enforces them.

## Developing the factory

```bash
make dev          # install the package + dev tooling (editable); `make install` includes this
make check-purity # prove the core imports nothing target-specific
make test         # run the pytest suite
make ship         # every gate, fail-closed: purity -> human-gates -> wiring -> authority -> harness -> denial-probes -> acceptance -> glossary -> lint -> typecheck -> test
make test-isolation # macOS: prove kernel-enforced Coder/Tester separation
```

Nothing needs a target to run. The suite exercises the core end-to-end against a **synthetic
empty target** fixture (`tests/fixtures/synthetic_target/`). The runtime integration additionally
uses deterministic synthetic Coder/Tester programs; that proves orchestration and isolation,
not model independence or semantic correctness.

## Practices & sync

- `docs/practices/change-surface-audit.md` — the Change-Surface Audit, an assessment of touched surfaces and the checks that protect their intended behavior.
- `docs/practices/frontend-architecture.md` — the generic front-end architecture the factory
  favors: clean separation of design / structure / data, progressive enhancement with
  server-rendered completion paths, container-scoped CSS cascade, and judicious use of
  globals. Pairs with the FE↔BE contract discipline in `factory_core/contract.py`.
- `docs/practices/verification-probes.md` — how the Validator closes the judge tail when the
  Tester's blind repairs stop converging: triage, probe-fix, delta, mutation and composition
  probes; prose relay that keeps the roles separate; escalation to line-precise corrections;
  and the independence accounting the verdict must state.
- `docs/practices/ruling-discipline.md` — the checklist and mechanical sweeps a Validator
  ruling passes before dispatch (fields, enum members and nullability carried everywhere;
  superseded text deleted; external behaviour grounded in documentation; one home per fact).
- `docs/practices/lessons-multi-gate-run-2026-10.md` — operational lessons from a three-gate
  replacement run: judge hygiene, metered-lane caps and fallbacks, focused specs, briefs that
  record-and-continue, mutation-checking a control's inputs.

## The two guarantees

**Purity.** `scripts/check_core_purity.py` is green iff `factory_core/`:
1. imports only stdlib + the package itself + a small reviewed third-party allowlist
   (currently just `jsonschema`) — any target import fails hard;
2. contains no un-baselined token from the configured denylist (`core_purity_denylist.json`,
   which is **empty** in this generic core) in its identifiers/strings; and
3. lists no target pack as a dependency in `pyproject.toml`.

**Portability.** `tests/test_portability.py` proves the core is importable, testable, and
green with **no target pack present** — there is no `targets/` package in the tree, only the
synthetic empty fixture, and the core reasons over it without importing anything
target-specific. Swap the target pack and the same core serves a new customer.

## Ownership

Proprietary, all rights reserved — copyright Jeremy McEntire (see `LICENSE`). Not open source.
The dependency arrow points **consumer → factory, never the reverse** (see `PROVENANCE.md`).


The optional [Jev runtime screen](docs/practices/jev-runtime.md) adds bounded, retained semantic
observations to the resident Orchestrator. Select it explicitly per run; it grants no authority.

### Run storage and build identity

`factory version --json` reports the installed builder name/package version, Factory Git revision
and dirty state (when available), harness schema and Python version. `factory --version`
is the short form. A Git checkout reports its own revision and dirty state. Outside a
checkout, a package can carry a full lowercase Git object name in
`factory_runtime/BUILD_REVISION`. The package reports that revision as clean without
running Git. Without a checkout or a stamp the revision is unknown.

New ordinary runs retain this identity as `factory_version` in `harness.json`,
separately from the target repository revision. Older records are
unknown, not evidence that a user ran the latest Factory. Use
`python harness/run_record.py provenance --runs RUNS --run RUN` to render that run's
`Built-By`/`FactoryRun` commit trailers and `PR built by NAME vVERSION` line. Adding
`--repo REPO --commit SHA --pr URL` checks actual task commits and fetched GitHub head/body,
retains the observation and queues it to the Orchestrator. Endgame and PR checks each invoke
one bounded System One observation through the existing client, with attribution metadata only.
For an explicitly authorized profile-backed check, set `FACTORY_SYSTEM_ONE_PROFILE=1` on the
Factory invocation. Coordinators receive only this flag. When no direct provider key is present,
the short-lived HTTP child sources `~/.profile` silently; no key is returned to the coordinator.
Commit-only checks explicitly report `pr_verification: not-checked`; provider failures report
`system_one.status: unavailable`. Neither changes test results or product acceptance.
Recheck after artifact changes;
this attributes the builder, independently of the product version being released.

Inspect storage before deciding what to reclaim:

```sh
factory cleanup --runs ~/.factory-runs
factory cleanup --runs .factory/runs --run finished-task --archive --completed
# After reviewing the plan and stopping the completed run's exact sessions:
factory cleanup --runs .factory/runs --run finished-task --archive --completed --apply
# If the source disk cannot stage a compressed copy, use another volume:
factory cleanup --runs .factory/runs --run finished-task --archive --completed \
  --archive-dir /Volumes/Archive/factory --apply
factory cleanup --restore /Volumes/Archive/factory/finished-task.tar.gz \
  --destination /new/path/finished-task --apply
```

Without `--apply`, cleanup/restore only report their plan. Archival currently supports
ordinary `factory-harness/3` directories, not arbitrary folders or historical signed
runs. `--completed` is an explicit declaration that work is finished and all writers
are stopped; recorded live worker/wrapper processes and exact tmux sessions still
refuse archival. Startup and archival are serialized. It never stops agents.

Archival preserves all regular-file bytes (including uncommitted files, local commits,
Git objects, generated caches and logs), directory/file modes and symlink targets.
It verifies the compressed archive against a per-file digest receipt before removing
the selected original directory. Keep the `.tar.gz` and `.archive.json` together.
Restore requires a new destination and never overwrites an existing directory. Hardlinked
files restore as independent files. Extended attributes (including resource forks) are
retained in the archive and private receipt. Restore attempts to apply them and reports
any OS-protected attributes it cannot restore; their original bytes remain archived.
Restored files belong to the restoring user.
New run roots and Coder/Tester repositories carry `.kindex-no-index`, Kindex's
presence marker excluding that directory and its descendants from automatic
registration and background indexing. Active and finished lanes, including
standalone clones, remain task-local. Kindex queries, captures and generated
knowledge files remain available; reviewed knowledge becomes eligible in the
canonical project after merge. The marker stays out of initial lane commits and
accepted snapshots, and is retained when a run is archived. Restore marks older
archives before restoring repository files and reports
`kindex_exclusion_marker_added`; all original archived files still verify against
the receipt. Neither cleanup nor restore opens Kindex stores or changes registries.

This requires a Kindex release that honors `.kindex-no-index`; older Kindex
versions can ignore it. Updating Factory does not retrofit existing run trees or
stop running agents. Deploy the matching Kindex exclusion support to also reject
previously registered temporary repositories. Inventory and any later archival
remain separate, explicitly scoped operations.

For already-created clones, run `factory cleanup --runs /actual/run/parent`
without `--apply` to inventory paths recorded in each run's
`inputs/coder.json` and `inputs/tester.json`. The `external_resources` report is a
reference inventory, not proof of exclusive ownership or a complete list of old
clones. This release has no bulk marker backfill command. An old standalone clone
outside Kindex's recognized temporary locations can remain eligible until it is
explicitly marked or otherwise excluded by Kindex. Do not infer that installing
this Factory change makes every existing clone safe, and do not use directory
names or a broad filesystem scan as authority to mark canonical repositories.
A separately scoped migration must verify lifecycle records and canonical paths
before marking existing clones; this change does not perform that migration.
Explicit lane preparation/relaunch marks its own repository, and archive restore
marks its new destination, but neither is a bulk backfill operation.

When refreshing a known transient candidate, use the installed Factory interpreter:

```sh
python -m factory_runtime.kindex_workspace sync --source /path/to/source \
  --destination /path/to/transient-candidate --delete --exclude .git --exclude .factory
```

The command creates a missing marker before rsync and protects existing markers
from deletion and replacement, including nested markers. It hides sender markers,
fails on rsync errors, and refuses overlapping source/destination trees or a linked
destination root. Overlap checks use filesystem identities as well as ancestry,
including case aliases. Caller exclude patterns cannot override marker protection.
Empty/clear-list (`!`) exclusion rules and arbitrary rsync flags such as
`--delete-excluded` are not accepted.
This is not a canonical publication command: use the existing lane export to omit
runtime markers from accepted work.

`harness/flake.sh` and `harness/mutate.sh` use the same sync boundary and reset
exclusive scratch contents in place while retaining the root marker. Normal
validation already materializes each candidate beneath a separately marked result
directory. Raw Git materialization and immutable snapshot helpers remain byte-exact;
their callers must keep temporary content beneath a marked ancestor.
Scratch reset refuses destinations with Git metadata; it is not an author-lane
reset command. Deletion-enabled sync preserves nested markers and therefore may
retain an otherwise empty stale directory containing a marker.

Upgrade at the next command boundary: updating Factory does not rewrite an active
shell's loaded script or an agent's already-issued rsync command. Replace future
ad-hoc candidate refreshes with the command above; do not restart productive agents
or touch live candidate data as part of installation. Any repair of an existing
missing marker must separately verify that exact path is a transient workspace.

Linked Git worktrees and alternate object stores require separate preservation and
are refused rather than producing an unusable archive. Sufficient staging space is
required: compression is not an in-place cure for an entirely full disk.

Inventory reports apparent regular-file lengths, not filesystem allocated blocks;
hardlinks and filesystem sharing can make physical reclaim differ from those totals.
It distinguishes run-root bytes from referenced external Coder/Tester repositories. Those external repositories are reported, **not reclaimed**, and the
report is not a claim of exclusive ownership. Unregistered candidate directories,
shared dependency caches and operator repositories remain outside this tool's scope.
Do not interpret a successful archive as closing or accepting a task.

At the end of work, the Validator/Orchestrator should inspect this inventory, record
which resources are retained and why, stop their exact finished sessions, and offer
or apply cleanup within the operator's authorization. Do not accumulate fresh clones
for successive judging rounds when a safe reusable workspace suffices. Standalone
Coder/Tester repositories remain necessary for the existing isolation boundary;
shared Git worktrees are not a drop-in replacement for those lanes.

Storage inventory reports run-directory bytes, compressed archive bytes, and their
combined local footprint separately. Archives on another volume and externally
referenced lane repositories are not included in that combined total.

### Publishing the provisioning installer

The Go installer and the Nix setup both use the publishing procedure in
[docs/FACTORY-PUBLISHING.md](docs/FACTORY-PUBLISHING.md). The Nix setup selects its
retained launcher when executing the same harness steps.

For a new installer binary release, first merge the tested source, then create
and push the intended `v*` tag and publish its GitHub release with the Factory
wheel/sdist assets through the existing release process. To embed the revision in those
assets, resolve the release tag to a full commit ID and export that exact commit with
`git archive` into a fresh staging directory. Write the ID followed by a newline to
`factory_runtime/BUILD_REVISION` in that directory, then run
`python -m build --sdist --wheel` there. Both artifacts carry the stamp.

The Factory setup workflow runs on the published release and attaches its four installer binaries
and `checksums.txt` to that existing release. It never creates a competing release.
Check that workflow succeeds before announcing the installer. Runtime-only
releases without installer assets are ignored by installer update notices.
`FACTORY_SETUP_FORCE_SOURCE=1` forces the startup script to build the selected
`FACTORY_SETUP_REF` from source; online PR CI uses this so an older released
binary cannot mask a broken source fallback.
