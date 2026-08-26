# prompts/ — role and review prompts for the factory

Prompt files for the lanes and gates the factory runs. Each is a self-contained
instruction document, usable as a dispatch prompt (`claude "/validate …"`-style) or as
the text a runner injects into a lane.

| File | Role | Source (pulled 2026-08-25) |
|---|---|---|
| `validate.md` | Validator — owns the human relationship, the signed artifacts, running the tests, and the verdict | `~/.claude/commands/validate.md` |
| `engineer.md` | Coder — the implementation, against the signed specification | `~/.claude/commands/engineer.md` |
| `test.md` | Tester — the tests, against the signed specification; never reads the implementation | `~/.claude/commands/test.md` |
| `orchestrate.md` | Orchestrator-agent — the advisory runner seat beside the enforcing dispatcher scripts | `~/.claude/commands/orchestrate.md` |
| `code-review.md` | The code-review standard every reviewing agent binds to | `~/Code/tools/CODE-REVIEW-STANDARD.md` |
| `diff-intent-gate.md` | Standing directive for every lane: diffs are checked against declared intent; agents escalate, humans ratify | `~/Code/tools/DIFF-INTENT-GATE.md` |

## Provenance and sanitization

These are copies of their sources with two classes of deliberate change:

- **Target-token removal.** The core is generic by construction, so consumer-specific
  repo names and domain terms in `validate.md` and `code-review.md` were rewritten to
  generic "target" phrasing with the same semantics.
- **Cadence and state-keeping sections (added 2026-08-25, founder-directed).** Each lane
  prompt now names its behavior loop: the Validator and orchestrator register durable
  status-loop reminders and close them at run end; the Coder and Tester run bounded work
  loops with upward-report exits and no monitoring duties. The Validator additionally
  shares the run plan with the orchestrator and owes its rule-adherence calls high
  deference; the orchestrator carries the matching state-keeper duty (outstanding-work
  ledger, adherence calls) with no new grant authority. These sections exist here first;
  the `~/.claude/commands/` sources have not yet been synced.

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
