#!/usr/bin/env bash
# Historical entry point: refuse before reading or changing any run.
printf '%s\n' \
  'dispatch_lane: signed dispatch is retired.' \
  'Prepare independent inputs with role_inputs.py, then launch the assigned author with tmux_lane.sh.' >&2
exit 64
