#!/usr/bin/env bash
# Historical entry point: refuse before reading or changing any run.
printf '%s\n' \
  'abandon_legacy: signed legacy abandonment is retired.' \
  'Historical evidence is preserved. Start an ordinary task with factory.sh; stop any owned old processes explicitly by their verified identity.' >&2
exit 64
