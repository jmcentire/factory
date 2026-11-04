#!/usr/bin/env bash
# Historical entry point: refuse before reading or changing any run.
printf '%s\n' \
  'promote: automatic harness closure is retired.' \
  'Use endgame.sh for exact candidate checks. The Validator records task acceptance; this command cannot merge, publish or close a task.' >&2
exit 64
