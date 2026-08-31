# prompts/ — role and review prompts for the factory

Prompt files for the lanes and gates the factory runs. Each is a self-contained
instruction document, usable as a dispatch prompt (`claude "/validate …"`-style) or as
the text a runner injects into a lane.

**These files are the canonical source.** The operator's live agent surfaces
(`~/.claude/commands/`, `~/.codex/prompts/`, `~/.gemini/config/skills/`) and the
cross-project pointers in `~/Code/tools/` are thin loaders that read the files here —
reconciled 2026-08-30 so a canonical edit propagates everywhere without fan-out and no
external copy can drift.

| File | Role |
|---|---|
| `validate.md` | Validator — owns the human relationship, the signed artifacts, running the tests, and the verdict |
| `engineer.md` | Coder — the implementation, against the signed specification |
| `test.md` | Tester — the tests, against the signed specification; never reads the implementation |
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

- **The Production-Grade Build Playbook** (`~/Code/tools/production-build-playbook/`) —
  the governing doctrine all four lane prompts cite (Chapter 0 first). It is a ~10k-line
  book with its own repo and assembly script; it stays at its source rather than being
  vendored here.
- `skills/orchestrate.md` and `skills/review.md` in this repo predate this directory and
  remain where the harness already reads them; `/review` (the orchestrator's independent
  alignment check) lives there.
- The interactive `/code-review` skill shipped inside Claude Code is embedded in the
  binary; `code-review.md` here is the standard that governs how its findings — and any
  reviewing agent — are judged.
