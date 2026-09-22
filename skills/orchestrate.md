# /orchestrate — the resident supervisory seat

Read `/Users/jmcentire/Code/factory/prompts/orchestrate.md` and follow it as this command's
complete instructions. That file is the single canonical source, and the harness copies it
into each run as `orchestrator/ROLE.md`. This file is a thin pointer so that no second copy
can drift.

The Factory has exactly four roles: Validator, Orchestrator, Coder, Tester. The Orchestrator
is resident and always running for every run, watching every lane and speaking up without
being asked. There is no one-shot, wake-only, or orchestrator-less mode.
