#!/usr/bin/env bash
# Historical entry point: refuse before reading or changing any run.
printf '%s\n' \
  'record_no: host terminal-NO classification is retired.' \
  'Report the concrete outcome to the Validator through inject.sh. A deadline or exit code is evidence, not a task decision.' >&2
exit 64
