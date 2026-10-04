# prompts/ — role and review prompts for the factory

Prompt files for the lanes and gates the factory runs. Each is a self-contained
instruction document, usable as a dispatch prompt (`claude "/validate …"`-style) or as
the text a runner injects into a lane.

**These files are the canonical source.** The operator's live agent surfaces
(`~/.claude/commands/`, `~/.codex/prompts/`, `~/.gemini/config/skills/`) are thin
loaders that read the files here —
reconciled 2026-08-30 so a canonical edit propagates everywhere without fan-out and no
external copy can drift.

## Locations

Every prompt refers to this repository by name so the same prompt bytes work on
any machine. When unset it resolves to the root of the checkout the prompt was
read from, so an existing installation changes nothing:

| Name | Meaning | When unset |
|---|---|---|
| `$FACTORY_HOME` | This repository's checkout | The root of this checkout |

The names are documented locations, not shell expansion: a prompt file is read
by an agent, not by a shell, so the operator (or the thin loader that dispatches
the prompt) states the actual paths once and the prompt cites them by name.
Loaders may export the variable into the agent's environment or substitute
them before dispatch; either is conforming.

| File | Role |
|---|---|
| `validate.md` | Validator — owns the human relationship, the signed artifacts, running the tests, and the verdict |
| `engineer.md` | Coder — the implementation, against the signed specification |
| `test.md` | Tester — the tests, against the signed specification; never reads the implementation |
| `build.md` | `/build` — the single-agent research-plan-build-ship pipeline; collapsed roles, so Cosmetic and small Standard work only |
| `orchestrate.md` | Orchestrator-agent — the advisory runner seat beside the enforcing dispatcher scripts |
| `code-review.md` | The code-review standard every reviewing agent binds to |
| `diff-intent-gate.md` | Standing directive for every lane: diffs are checked against declared intent; agents escalate, humans ratify |

## Genericity and target data

- **Target-token removal.** The core is generic by construction, so consumer-specific
  repo names and domain terms in `validate.md` and `code-review.md` are written as
  generic "target" phrasing with the same semantics.
- **Target-specific operational bindings live outside this repo as data** — in the
  machine-local loader (interim) and in the consuming target's pack once authored —
  never as prompt bytes here.
- **Cadence and state-keeping sections (added 2026-08-25, founder-directed).** Each lane
  prompt names its behavior loop: the Validator and orchestrator register durable
  status-loop reminders and close them at run end; the Coder and Tester run bounded work
  loops with upward-report exits and no monitoring duties. The Validator additionally
  shares the run plan with the orchestrator and owes its rule-adherence calls high
  deference; the orchestrator carries the matching state-keeper duty (outstanding-work
  ledger, adherence calls) with no new grant authority.

## What is referenced, not copied

- **The doctrine and the standards live in this repo, not here.** The lane prompts cite
  [`docs/SOFTWARE-FACTORY.md`](../docs/SOFTWARE-FACTORY.md) (§3, the four roles, first) and
  the engineering standards in [`docs/standards/`](../docs/standards/):
  [ARCHITECTURE](../docs/standards/ARCHITECTURE.md) (A-rules),
  [HOW-WE-WRITE-CODE](../docs/standards/HOW-WE-WRITE-CODE.md) (W-rules),
  [TESTING](../docs/standards/TESTING.md) (T-rules),
  [REVIEW](../docs/standards/REVIEW.md) (design and code passes), and
  [RESPONSE-STANDARD](../docs/standards/RESPONSE-STANDARD.md) (how any lane talks to a
  human). Operate-phase practices — [Reliability as Signal](../docs/practices/reliability-as-signal.md)
  and [Graduated Incident Response](../docs/practices/incident-response.md) — sit under
  `docs/practices/`. The external Production-Grade Build Playbook these prompts previously
  cited was retired on 2026-10-03; its load-bearing content now lives in those documents.
- `skills/orchestrate.md` and `skills/review.md` in this repo predate this directory and
  are loaded by each agent's own skill loader, not by the harness;
  `/review` (the orchestrator's independent alignment check) lives there.
- The interactive `/code-review` skill shipped inside Claude Code is embedded in the
  binary; `code-review.md` here is the standard that governs how its findings — and any
  reviewing agent — are judged.
