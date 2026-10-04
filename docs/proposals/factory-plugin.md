# Building the Factory Workflow Plugin

Target: Claude Code plugins, shipped from the factory repo through one marketplace. The `factory` plugin adds `/factory:setup`, `/factory:start`, `/factory:resume` and `/factory:status`. Two smaller plugins, `factory-review` and `factory-standards`, carry the review passes and the standards as skills a team can adopt without the workflow. They run in Claude Code, not in chat.

**Authority is the human's word, not a signature.** Every phase works from what the accountable human said, under [Whose Word Counts](../standards/AUTHORITY.md) (H1–H8): stated items, latest first, with any exception they made to a maxim. An approval is the human saying yes in the session, recorded with the date and the hashes of what they approved. The human never signs: the host records their words and signs the record with keys it minted at project start. Tessera is the signing tool behind that, used only for integrity and attribution between workers. It is built from its pin by `make tessera`; setup checks for it like any other tool.

Four limits to design around:

1. **The wizard cannot set provider-side spend limits.** It can check keys and enforce the factory's own ceilings. For provider limits it asks you, and it records your answer as an attestation, never as a verification.
2. **The wizard is instructions. Only scripts and hooks are mechanical.** Anything that must hold (gates, checks, budgets) is a script. The skills are the conversation around the scripts.
3. **Claude Code's own token use is outside the factory's ledger.** The factory tools meter what they spend. The main agent's usage is governed by your plan or provider limit.
4. **An approval is evidence of a decision, not proof of identity.** `approve.sh` records that the human in this session said yes, quoting them. It does not prove who typed. A team that needs proof of identity puts the approval in a reviewed commit or PR, where its existing controls apply.

## Layout

One marketplace at the repo root lists three plugins. Each has its own version and its own changelog section, and each installs alone.

| Plugin | Contents | Needs the factory workflow? |
|---|---|---|
| `factory` | setup, start, resume, status; Advocate and Sim agents; gates, hooks, ledger | It is the workflow |
| `factory-review` | The 17 review passes (D1–D7, C1–C10) as one skill each, plus a router skill | No. Works on a diff, with intent from the conversation or the PR |
| `factory-standards` | `architect` (Helland-style, A-rules), `test-strategy` (TESTING.md rows), `write-code` (W-rules), `whose-word` (H-rules) | No |

```
.claude-plugin/
  marketplace.json                # lists factory, factory-review, factory-standards
plugins/
  factory-review/
    .claude-plugin/plugin.json
    skills/<pass-id>/SKILL.md     # GENERATED from docs/standards/REVIEW.md
    skills/review-router/SKILL.md # GENERATED: which pass for which change
  factory-standards/
    .claude-plugin/plugin.json
    skills/<name>/SKILL.md        # GENERATED from docs/standards/*.md
    skills/<name>/reference/      # the standard itself, copied at build time
factory/                          # the workflow plugin root
  .claude-plugin/
    plugin.json                   # name: "factory"
  .mcp.json                       # Kindex, Kinbase, and any tool exposed as MCP
  tools.json                      # the tool manifest: single source of truth
  skills/
    setup/SKILL.md                # /factory:setup
    start/SKILL.md                # /factory:start
    start/phases/{prd,architecture,testing,plan,contracts,build}.md
    resume/SKILL.md               # /factory:resume
    status/SKILL.md               # /factory:status
  agents/
    advocate.md                   # subagents: fresh context, return a verdict
    sim.md
  hooks/hooks.json
  scripts/
    setup-check.sh                # every setup check, no model, exit code = result
    gate.sh                       # phase gate for hooks and the git hook
    approve.sh                    # records an approval with artifact hashes
    spend.sh                      # reads and appends the spend ledger
  tests/
```

Plugin skills are namespaced, hence `/factory:start`. For `/factory start` with a space, add one standalone skill named `factory` that dispatches on its first argument. `setup` offers to install it into `~/.claude/skills/` and does so only on a yes, since that is outside the repo.

**Install path:** `/plugin marketplace add <owner>/factory`, then `/plugin install factory`, `/plugin install factory-review` or `/plugin install factory-standards`. The owner is wherever the factory repo is published; it is not named here.

### Generated skills, and the parity check

The standards are the single source. No skill restates a rule by hand.

- `scripts/build_skills.py` writes every `factory-review` and `factory-standards` SKILL.md from its standard. A pass skill is its REVIEW.md section (question, look-for, ignore, recording format) with frontmatter and the cited rules attached. A standards skill is a short procedure that cites rule IDs, with the full standard copied into `reference/`.
- `make check-skills` regenerates into a temporary directory and fails if any committed skill differs from what it generates. It joins `make ship`. Editing a skill by hand fails the build; edit the standard.
- Every generated skill page states three things: **when to use it**, **when not to**, and **how to tell it ran**. The last is an observable output, such as a pass record naming its question and listing what it covered, in the *Recording a pass* format.
- The router skill is generated from the same sources. It reads the change or the request and names the passes or skills that apply, with one line of reason each. It never runs them itself.
- Without the workflow, intent comes from the conversation, the ticket or the PR description, tagged by source under H2. A pass that cannot find a stated item for the change says so in its record rather than reviewing against its own guess.

### Packaging rules

These hold for all three plugins. Each one that can be checked is a CI gate, not a convention.

- **Self-hosted marketplace.** The factory is proprietary, so it does not ship through a public skills directory or a copy-into-your-repo installer. One install route per plugin, so no team ends up with every skill twice.
- **Explicit skill lists.** Each `plugin.json` lists its skills by path. CI fails if the list differs from the generated set.
- **Manifests validated.** CI runs Claude Code's plugin validator in strict mode on every manifest change.
- **One version source per plugin.** A stdlib Python script writes each plugin's version into its manifest and has a `--check` mode that CI runs. A rename or removal that breaks a user's invocation is a major version. The changelog names the replacement for anything removed.
- **Who invokes each skill is declared.** A user-invoked skill (`disable-model-invocation: true`) gets a one-line description for humans. A model-invoked skill gets "Use when…" trigger lines. The 17 pass skills are user-invoked, so their descriptions do not sit in every session's context. The router reaches them by dispatching a subagent that reads the pass file from the plugin. This dispatch path is an assumption to test first; if it fails, the router names the passes for the human to run.
- **Router accuracy is gated.** CI fails if a shipped skill is not routed or a routed skill is not shipped.
- **Every step says when it is done, and every skill says how to see it working.** Each generated skill has a *Done when* line per step and an *It's working if* check the human can make without opening the file.
- **Generated files are committed, not symlinked.** Some agent installers drop symlinks.
- **Betas stay out of the shipped set.** Unfinished skills live in a directory no manifest lists. Graduating one is a manifest entry, a router route and a changelog entry.
- **One install block.** README and every doc page copy one canonical install snippet, checked for drift.
- **Skill-writing guidance is ours.** The generator's template follows a short factory guide to writing for agents: what goes in always-loaded context and what does not, completion criteria per step, instructions stated positively. It is written fresh. Third-party text with its own license is not copied into this repo.

`/factory:setup` borrows four habits from the best setup skills seen: explore the repo before asking; lead each question with a recommended answer and skip any the exploration settled; write one pointer block into whichever of CLAUDE.md or AGENTS.md exists, updating it in place; and configure only the plugins that are installed. It keeps its scripted, model-free `setup-check.sh` as the verifier.

## Step 1: the tool manifest

Every check reads `tools.json`, so no script hard-codes a tool. One entry per tool (Constrain, Advocate, Sim, Pact, Kindex, Kinbase, the transmogrifier, and any others):

```json
{
  "name": "pact",
  "kind": "cli | mcp | both",
  "install": "<install command>",
  "check": "command -v pact",
  "version": "pact --version",
  "min_version": "x.y.z",
  "env": ["PACT_API_KEY"],
  "validate": "<zero-cost call that proves the key works>",
  "health": "<command that proves the tool works>",
  "spends": true
}
```

`validate` must cost nothing: a list-models or whoami endpoint, never a completion. If a provider has no free endpoint, `validate` runs only in the live smoke test.

**Acceptance:** a JSON schema for the manifest exists and validates it, and every tool the factory calls has an entry.

## Step 2: dry-run and the spend ledger

Every tool that spends honors two things.

- **`FACTORY_DRY_RUN=1`:** return canned output, call no model. The whole workflow becomes testable for free.
- **The ledger:** before each paid call, read `.factory/<name>/spend.jsonl`. If cumulative spend plus the estimated cost would pass the ceiling for the session or the current phase, stop and escalate. After the call, append the actual cost. A shared ledger is what makes the ceilings apply across tools, rather than each tool keeping its own count.

**Acceptance:** in dry-run every health command passes with zero provider calls. With a ceiling of $0.01 the first paid call escalates instead of running.

## Step 3: `/factory:setup`

All checks live in `scripts/setup-check.sh`, which runs without a model and exits nonzero on any failure. CI runs the script directly. The skill runs the same script, explains each result, and walks you through fixes. It is idempotent and safe to re-run.

1. **Environment:** OS, Claude Code version, git repo root, working tree state.
2. **Tools:** for each entry run `check` and `version`, compare against `min_version`, then run `health`. On a failure, offer the `install` command and run it only after you say yes.
3. **MCP servers:** the plugin's `.mcp.json` starts them when the plugin is enabled. Setup verifies that each one connects and lists its tools.
4. **Credentials:** test each `env` var with `[ -n "$VAR" ]` and print present or missing, never the value. Then run `validate`.
   - If a key is missing, say how to set it yourself, in the shell profile or OS keychain. The wizard never asks for a secret in chat.
   - Check that `.gitignore` covers `.env.local`, `.factory/config.local.json` and `.factory/setup-report.md`.
5. **Spend:**
   - *Provider side:* for each key, show where its limit is set (for Anthropic, the Console workspace limit). Ask whether one is set and record the answer with the date.
   - *Factory side:* write the session and phase ceilings to `.factory/config.json`. That file is committed, so the team shares the defaults. Personal overrides go in the ignored `config.local.json`.
6. **Kindex and Kinbase:** initialize the repo's Kindex graph if absent. Verify that Kinbase connects and that your identity resolves to an owner record.
7. **Fallbacks:** for each tool that failed and was not installed, say what the workflow does without it and what that costs. Every `tools.json` entry has a `fallback` field: the manual procedure (for Advocate, a self-review across the six personas in a fresh subagent) and a one-line statement of how the evidence weakens. Setup asks the human to accept each fallback; the answer is recorded and shown again in `/factory:status`.
8. **Role binding:** detect which model providers have working keys (Anthropic, OpenAI, Google, and any others in `tools.json`). Propose which model plays each of the four roles (Validator, Orchestrator, Coder, Tester) and which runs Advocate and Sim, favoring a different model family for review and for the Tester than for the Coder when one is available. With one provider, say so and say what independence is lost. Write the binding to `.factory/config.json`; personal overrides go in `config.local.json`. `/factory:status` shows the binding in force, and the human can change it at any phase boundary.
9. **Smoke test:** run a canned one-line spec through Constrain then Pact in dry-run. If you agree, run it once live under a $0.25 ceiling enforced by the ledger.
10. **Report:** write `.factory/setup-report.md` as a table of PASS, WARN and FAIL, with the fix command beside each failure.

**Acceptance:** on a clean container the script lists every missing item. After the fixes it exits 0. A secret scanner run over the session transcript and `.factory/` finds nothing.

## Step 4: `/factory:start`

Asks for a project name (a ticket id or a slug), then says:

> We'll begin by discussing what you'd like to accomplish. We'll settle the product spec, then the architecture, then how we'll test it. Nothing is built until you and I are both happy.

It creates `.factory/<name>/`, writes `<name>` to `.factory/active`, and walks the phases. On a repo with no `.factory/keys/genesis.tessera.json` it first runs `factory init`, which mints one key per worker, a host-held key for the human principal, the ledger chain root and the genesis, and grants each worker only its own key. It tells the human the keys are minted and that they will never be asked to sign anything. Each phase file is loaded only when that phase is reached.

| Phase | What happens | Tools | Output |
|---|---|---|---|
| 1. PRD | Guided interview. Name the gap between what was asked for and what is required. Find who owns the area and what was decided before. | Kinbase, Kindex, Advocate, Sim | `prd.md` |
| 1b. Survey | Read the code the feature will touch against the A-rules, before any design. See below. | Kindex, Kinbase, `pact assess` | `survey.md` |
| 2. Architecture | Check the design against the repo's architecture rules and prior decisions, and dispose of every survey row. | Kindex, transmogrifier, Advocate, Sim | `architecture.md` |
| 3. Testing | See below. | Advocate, Sim | `testing.md` |
| 4. Plan | Constrain builds the plan from the three artifacts as vertical slices, followed by one adversarial round. | Constrain, Advocate | `plan.md` |
| 5. Contracts | Pact forms the testing contracts first. Every open question is ruled on before the gate. | Pact | contracts the human approved |
| 6. Build | Hand off to the factory's existing phases. The phase file points to the build workflow (`prompts/build.md`, or the four-role run for Critical work) and does not restate it. | factory | the build |

**Each phase file carries its own standing instructions,** so the human never repeats them. The architecture phase opens with the invocation *"Helland-style: authoritative about our own transaction, everything upstream is an input, reconcile the economics in settlement,"* checks the design against the A-rules, and names the authority for every fact. The testing phase writes `testing.md` as one row per invariant in the TESTING.md T5 shape. Every phase that produces an artifact runs Advocate and Sim on it before asking for approval, without being told to.

**Phase 1, the PRD interview.** The phase file sets the mechanics, so the interview has an order and an end:

- Keep a dependency tree of open decisions. Each round asks, numbered, only the questions whose prerequisites are settled. A question that waits on another waits for a later round.
- Facts are the agent's job, never the human's. A fact lookup goes to a subagent, and only the questions downstream of it wait.
- In the PRD the human proposes and the agent counters. The agent offers no recommended answers here; it does in architecture and testing, where it is the proposer.
- When the human uses a term that conflicts with the repo's vocabulary, or describes behavior the current code contradicts, stop and ask which is right, citing file:line. Record each settled term in `prd.md` with its rejected synonyms (W4).
- Before writing requirements, check by domain concept, not by wording, that the feature does not already exist, and say where you looked.
- When a question belongs to the area's owner rather than the human present, write `.factory/<name>/questions-<owner>.md`: most important first, one idea per question, each naming the decision it unblocks. Log it in `decisions.md` as open.
- The interview ends when no open decision remains. Approval is item by item under H1, never one "looks good" for the whole document.

**Phase 1b, the survey.** It exists for the feature being built, not for the health of the codebase. It reads only the change surface: the modules the feature will read, write, call or be called by, with their owners from Kinbase. It checks them for semantic A-rule violations with file:line: a second write path for an entity the feature writes (A22), callers branching on source of truth (A13), a copied field used as authority (A1, A5), vendor detail past its adapter (A24), references pointing outward (A10), ceremony interfaces (A20). It classifies each touched dependency as in-process, local and substitutable, owned remote, or external, for the testing phase. It reads Kindex decisions and recorded exceptions first, so a deliberate, contained hack at a named seam is not re-litigated. `pact assess` supplies hubs and cycles as a mechanical first pass where its language is supported. Output is one table, one row per finding: surface, owner, rule, file:line, and a disposition of FIT, CORRECT-IN-SCOPE, CONTAIN (with an owner) or OUT (recorded, not touched). Phase 2 must dispose of every row, and CORRECT-IN-SCOPE rows join the change surface. Open-ended codebase health work, hot-spot hunting and refactoring candidates are out of scope.

**Phase 2, architecture.** For each new interface or abstraction the design introduces, dispatch three or more independent subagents to propose its operations under different constraints: fewest entry points; the commonest caller trivial; a port at each cross-owner seam. Compare them on A12–A14, pick one, and record the rejected designs with the reason. When a state model is uncertain, build a single-file, clickable logic prototype under `.factory/<name>/`: a pure reducer, buttons in the domain's words, and scenarios that include an attempt at something that should be illegal. The human drives it. The outcomes the human confirms become stated items and seed the T10 sequence tests. The prototype never promotes and never counts as evidence. A decision goes to Kindex as a decision node when it is hard to reverse, surprising without context, and the result of a real trade-off.

**Phase 4, plan.** Each chunk is a vertical slice whose acceptance can be checked on its own and that fits one fresh context. Prefactoring is its own chunk, ordered first. Show the human the chunk list with its blocking edges and ask whether the granularity is right.

**Authority in every artifact.** Each requirement and design item in `prd.md`, `architecture.md` and `testing.md` carries one annotation in the AUTHORITY.md format: its source (H2), how firmly the human said it (decided, preferred or tentative), the date, the `decisions.md` entry holding their words, and its standing if it is not current (H8). An *inferred* item cannot be approved until the human confirms it. *Open* items block the gate. A maxim the human sets aside is an *exception* entry in `decisions.md`, scoped to its case (H5). Approval does not freeze an item as true: when a test, a measurement or the running system contradicts an approved item, it is marked *questioned* with the evidence and goes back to the human, whoever proposed it.

**What goes in git.** The artifacts, `decisions.md` with the human's words verbatim, and `state.json` with each approval's words and artifact hashes are committed; they are the project's knowledge. Keys, the chain root, grants, run ledgers and every signed `*.tessera.json` record are local integrity evidence: `factory init` writes the `.factory/.gitignore` that keeps them out.

**Phase 3, testing.** This is where the testing improvements live. `testing.md` holds:

- Every invariant, as a plain sentence ("a guest is never charged for dates that were not confirmed").
- For each invariant, how it will be attacked: a property test, a contract test or a fault injection.
- For each invariant, a **red-capable proof**: a deliberately broken implementation, or a mutation, that the test must catch. A test that cannot be shown to fail is not counted.
- Any invariant that cannot be tested, flagged for a person to rule on.
- The seams where tests attach, listed and confirmed with the human before invariants are mapped to attacks: existing seams first, the highest one that exposes the invariant, as few as possible (T6).

Pact's contracts in Phase 5 are written from this file. In Build, the blind test author works from the contracts, and the red-capable proofs run before the green ones count.

**Advocate and Sim** run as subagents that call the tools and return only the verdict and its reasons. Their review stays separate from the author's context, and the main context stays small.

**Gates.** `scripts/approve.sh` records each approval in `state.json`: phase, time, the human's words of approval, and the hash of every approved artifact. The hash detects change; it is not a signature. `gate.sh` recomputes those hashes on every check. If an approved artifact changed, its approval and every later one go stale, and the gate says which.

**Surfacing.** Every question, decision, change and escalation is appended to `decisions.md` and shown in chat at the same moment. Each entry gives an id, a type, what happened, why, and who ruled. The agent never changes an approved artifact silently: the change is an entry, shown before it takes effect.

**Kindex.** On each approval, the phase's decisions and their reasons are written to Kindex, so the next session on this code starts with them.

**`/factory:status`** prints the active project, its phase, open questions, stale approvals and spend against ceilings. **`/factory:resume`** reloads the active project and reopens at the first unapproved phase, showing the open questions first.

**Acceptance:** a scripted dry-run goes from `start` to the build handoff. Every gate blocks until it is approved. Editing `prd.md` after approval makes the later approvals stale. Running `resume` after a killed session opens at the right phase.

## Step 5: mechanical gates

Hooks in `hooks/hooks.json`:

- **SessionStart:** if `.factory/active` names a project, print its name, phase and open-question count, and suggest `/factory:resume`.
- **PreToolUse on Write and Edit:** `gate.sh` blocks any write outside `.factory/` while the active phase is before Build, with exit code 2 and a message naming the phase.
- **PreToolUse on Bash:** the same gate, applied to commands that write (redirection, `sed -i`, `tee`, `git apply`, and the like). Pattern matching on shell commands is best-effort, which is why there is a backstop.
- **PreToolUse on Bash, every phase:** deny commands that destroy history or the working tree: `reset --hard`, `clean -f`, `checkout` or `restore` of paths, `branch -D`, force-push, `stash drop`. Exit 2 with a message that names the command and tells the agent to ask the human. Ordinary push is allowed only to the project's own branch. The matcher parses the command rather than grepping for a substring, so `git -C . reset --hard` is caught and `echo "git push"` is not. Phase gating stops at Build; this rule does not.
- **Stop:** snapshot `state.json` and the open questions.

**Backstop:** setup installs a git pre-commit hook that runs `gate.sh` against the staged paths. Whatever slipped past the tool hooks cannot be committed before Build.

`.factory/<name>/` is committed. The prd, architecture, testing, plan, decisions and state files are the factory's notes, kept in the repository on purpose. Only the local config, the setup report and the spend ledger are ignored.

**Acceptance:** each destructive git pattern has a canary command that the hook must refuse in every phase, including after Build (T27). A source write during Phase 2 is refused through Write, through Bash redirection, and at commit, and each refusal names the phase. After the Phase 5 approval, all three are allowed.

## Step 6: tests

- `setup-check.sh` in a container with the tools missing, then present: the report and the exit codes are asserted.
- `make check-skills` on a clean tree passes; a hand edit to a generated skill fails it; an edit to the standard without regenerating fails it.
- Each `factory-review` and `factory-standards` skill installed alone, in a repo with no `.factory/`, produces its stated "how to tell it ran" output on a fixture diff.
- Role binding with one provider, then two: the proposal and the stated independence loss are asserted.
- `claude -p` drives the setup skill end to end in dry-run.
- A scripted dry-run of the full start flow, covering the Step 4 acceptance.
- The gate tests from Step 5.
- The ledger test from Step 2.
- A secret scan over every transcript and `.factory/` produced by the runs above.

## Fill in before building

These go into `tools.json` and the phase files and nowhere else:

- Each tool's binary name, install command, whether it is MCP or CLI, and its fallback.
- Each tool's env vars, zero-cost `validate` call and `health` command.
- The Kinbase endpoint.
- The default ceilings per session and per phase.
- The default role binding per provider combination.

Filled in already: the transmogrifier's CLI is `transmogrify` (`transmogrify translate "<text>" --register technical`), and the build workflow file is `prompts/build.md`.
