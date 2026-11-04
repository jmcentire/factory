#!/usr/bin/env bash
# The resident Orchestrator owns oversight; a one-shot duplicate is not another role.
printf '%s\n' \
  'orchestrator-wake: one-shot audits are retired; keep the existing Orchestrator conversation.' \
  'Read retained activity with dispatcher.py --run RUN --root RUN_ROOT --observe.' >&2
exit 64
