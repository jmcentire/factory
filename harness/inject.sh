#!/usr/bin/env bash
# inject.sh — retain a coordination message in a file inbox. The
# topology is enforced here, not remembered: Validator→{coder,tester,validator};
# dispatcher/validator/founder→orchestrator; dispatcher/founder→validator.
# The Orchestrator never sends raw terminal text into a pane it judges; its generated
# status probe uses tmux_lane_message.sh and the Codex session API. Every injection is receipted
# (sha256 of the message, from, to, ts) into .factory/runs/<run>/injections.jsonl.
# Coder-bound *result* traffic passes a verdict filter: bare pass/fail only — never
# a test name, assertion, or trace. The Validator's classified finding travels as an
# ordinary message under the oracle-leak guard below (validate.md, rule 6).
# usage: inject.sh <run> <to-window> [--results] "<message>"
#        INJECT_FROM=dispatcher inject.sh <run> orchestrator "<message>"
set -euo pipefail
RUN="${1:?usage: inject.sh <run> <to> [--results] <message>}"; TO="${2:?to}"; shift 2
RESULTS=0
[ "${1:-}" = "--results" ] && { RESULTS=1; shift; }
MSG="${1:?message}"
FROM="${INJECT_FROM:-validator}"
ROOT="${HARNESS_RUN_ROOT:-${HARNESS_DIR:-.factory}/runs/$RUN}"

case "$TO" in
  coder|tester)
    if [ "$FROM" != "validator" ]; then
      echo "topology refusal: $FROM may not inject into $TO (Validator is the only hub" >&2
      echo "into lanes; Orchestrator probes use the typed Codex-session channel)" >&2
      exit 77
    fi
    ;;
  validator)
    case "$FROM" in validator|orchestrator|coder|tester|dispatcher|founder) ;; *)
      echo "topology refusal: unknown principal '$FROM'" >&2; exit 77 ;; esac
    ;;
  orchestrator)
    case "$FROM" in dispatcher|validator|coder|tester|founder) ;; *)
      echo "topology refusal: $FROM may not inject into the Orchestrator's own pane" >&2
      exit 77 ;; esac
    ;;
  *) echo "unknown agent window: $TO (coder|tester|validator|orchestrator)" >&2; exit 64 ;;
esac

if [ "$TO" = "coder" ] && [ "$RESULTS" -eq 1 ]; then
  # Exactly one verdict line (one trailing newline allowed): a matching first line must not carry
  # anything after it. grep reads to EOF (no -q) so pipefail cannot flip the test.
  VERDICT="${MSG%$'\n'}"
  if [[ "$VERDICT" == *$'\n'* ]] \
    || ! printf '%s' "$VERDICT" | grep -E '^(PASS|FAIL)( [A-Za-z0-9._/#-]+)?( \([0-9]+/[0-9]+\))?$' >/dev/null; then
    echo "verdict filter refusal: coder-bound results are bare pass/fail only —" >&2
    echo "no test names, assertions, or traces cross this boundary" >&2
    exit 79
  fi
fi

# Oracle-leak guard on ALL coder-bound traffic, not only --results traffic.
# The bare-outcome rule is a rule the Validator must remember at compose time, and
# in run v8 the Validator broke it twice from memory while actively editing the
# document that states it. A rule an executor must recall is not a control; this
# makes it one. Blocks test-file paths and names, test function names, test-runner
# vocabulary and assertion text on the way to the implementation lane. It matches test
# DETAIL, never the bare words "test" or "oracle": those blocked a ratified Architecture
# naming the `test` task and the Coder's own `.factory/spec/oracle-inputs` path (dogfood #66,
# #80), while `assert balance == 31337` went through. There is no ambient
# override: an environment variable is not authority, and a bypass a signature
# never covered is how a guard becomes theater. A genuine false positive is a
# defect in the filter pattern, repaired through a authorized change — never
# stepped around at delivery time.
W='(^|[^A-Za-z0-9_])'
LEAK_PATTERN="${W}test_[a-z0-9_]+|tests?/[A-Za-z0-9_./-]*\.(py|ts|tsx|js|jsx|mjs|go|rs|rb|java|kt)($|[^A-Za-z0-9_])"
LEAK_PATTERN+="|[A-Za-z0-9_-]+[._](test|spec)\.[a-z]{1,4}($|[^A-Za-z0-9_])|conftest|traceback"
LEAK_PATTERN+="|::[A-Za-z_]|${W}assert[A-Za-z]*[[:space:]]*\(|${W}expect[[:space:]]*\("
LEAK_PATTERN+="|${W}assert[[:space:]]+[^[:space:]]+[[:space:]]*(==|!=|<|>)|Deno\.test"
if [ "$TO" = "coder" ]; then
  if printf '%s' "$MSG" | grep -iE "$LEAK_PATTERN" >/dev/null; then
    echo "oracle-leak refusal: coder-bound message names a test, fixture, or assertion." >&2
    echo "The Coder never learns how the oracle is built. Report WHAT failed by" >&2
    echo "requirement id, or an observation of the implementation's own behavior." >&2
    echo "There is no ambient override: a genuine false positive is a filter defect —" >&2
    echo "Fix the filter pattern; the boundary does not open from the" >&2
    echo "environment." >&2
    exit 80
  fi
fi

# Typed out-of-band transports reuse the topology and oracle-leak checks above,
# then deliver through their own session API. Validation-only performs no receipt
# write and no tmux input; it is deliberately placed after every content guard.
if [ "${INJECT_VALIDATE_ONLY:-0}" = "1" ]; then
  echo "validated: topology and content boundary"
  exit 0
fi

# Status is data, never terminal input. Author delivery uses the typed native transport.
case "$TO" in coder|tester)
  echo "inject: use tmux_lane_message.sh for an author message" >&2; exit 64 ;;
esac
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$D/factory_python.sh"
PYTHONPATH="$D/.." "$FACTORY_PYTHON" - "$ROOT" "$TO" "$FROM" "$MSG" "${FACTORY_LOG:-/dev/null}" <<'PYMSG'
from pathlib import Path
import sys
from harness.run_record import message
message(Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4], log=sys.argv[5])
PYMSG
printf 'queued -> %s:%s (durable inbox; not an agent acknowledgment)\n' "$RUN" "$TO"
