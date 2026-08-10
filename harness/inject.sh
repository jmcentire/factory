#!/usr/bin/env bash
# inject.sh — the ONLY sanctioned path for putting text into a lane's pane, and the
# topology is enforced here, not remembered: Validator→{coder,tester,validator};
# dispatcher/orchestrator/founder→validator only. Every injection is receipted
# (sha256 of the message, from, to, ts) into .harness/runs/<run>/injections.jsonl.
# Coder-bound *result* traffic passes a verdict filter: bare pass/fail only — never
# a test name, assertion, or trace (validate.md:230-232).
# usage: inject.sh <run> <to-window> [--results] "<message>"
#        INJECT_FROM=orchestrator inject.sh <run> validator "<message>"
set -euo pipefail
RUN="${1:?usage: inject.sh <run> <to> [--results] <message>}"; TO="${2:?to}"; shift 2
RESULTS=0
[ "${1:-}" = "--results" ] && { RESULTS=1; shift; }
MSG="${1:?message}"
FROM="${INJECT_FROM:-validator}"
ROOT="${HARNESS_DIR:-.harness}/runs/$RUN"

case "$TO" in
  coder|tester)
    if [ "$FROM" != "validator" ]; then
      echo "topology refusal: $FROM may not inject into $TO (Validator is the only hub" >&2
      echo "into lanes; orchestrator/dispatcher speak to the Validator alone)" >&2
      exit 77
    fi
    ;;
  validator)
    case "$FROM" in validator|orchestrator|dispatcher|founder) ;; *)
      echo "topology refusal: unknown principal '$FROM'" >&2; exit 77 ;; esac
    ;;
  *) echo "unknown lane window: $TO (coder|tester|validator)" >&2; exit 64 ;;
esac

if [ "$TO" = "coder" ] && [ "$RESULTS" -eq 1 ]; then
  if ! printf '%s' "$MSG" | grep -qE '^(PASS|FAIL)( [A-Za-z0-9._/#-]+)?( \([0-9]+/[0-9]+\))?$'; then
    echo "verdict filter refusal: coder-bound results are bare pass/fail only —" >&2
    echo "no test names, assertions, or traces cross this boundary" >&2
    exit 79
  fi
fi

mkdir -p "$ROOT"
DIGEST=$(printf '%s' "$MSG" | shasum -a 256 | cut -d' ' -f1)
printf '{"ts":"%s","run":"%s","from":"%s","to":"%s","results":%s,"sha256":"%s"}\n' \
  "$(date -u +%FT%TZ)" "$RUN" "$FROM" "$TO" "$RESULTS" "$DIGEST" >> "$ROOT/injections.jsonl"

if [ "${INJECT_DRY_RUN:-0}" = "1" ]; then echo "dry-run: receipted, not sent"; exit 0; fi
tmux send-keys -t "$RUN:$TO" "$MSG" Enter
echo "injected -> $RUN:$TO ($DIGEST)"
