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

# Delivery hardening (batch0 findings INC-3/INC-4, three distinct defects):
#
# 1. A pane hosting a SHELL parses the message as a command — injected text is
#    arbitrary execution in the repo cwd, and backticks command-substitute. Refuse
#    unless the target is running an agent, or INJECT_ALLOW_SHELL=1 declares a
#    deliberate mailbox sink.
# 2. Agent TUIs treat a large send-keys burst as a bracketed paste and ABSORB the
#    trailing Enter, so the message sits in the input box unsubmitted (observed:
#    two lanes stalled ~20 minutes). Send Enter as a separate, delayed keypress,
#    then a second one to clear a collapsed-paste confirmation.
# 3. Payloads past the tty line-buffer limit wedge the terminal and silently
#    discard everything after, including the Enter. Refuse oversized payloads.
LIMIT="${INJECT_MAX_CHARS:-1000}"
if [ "${#MSG}" -gt "$LIMIT" ]; then
  echo "refusing: message is ${#MSG} chars, over the $LIMIT-char delivery limit —" >&2
  echo "oversized injections wedge the tty line buffer and drop everything after," >&2
  echo "including the Enter. Split it, or write a file and inject the path." >&2
  exit 78
fi
TARGET_CMD=$(tmux display -p -t "$RUN:$TO" '#{pane_current_command}' 2>/dev/null || echo "")
[ -n "$TARGET_CMD" ] || { echo "refusing: no live pane at $RUN:$TO" >&2; exit 76; }
case "$TARGET_CMD" in
  claude|codex|node|python*|agy) ;;
  *)
    if [ "${INJECT_ALLOW_SHELL:-0}" != "1" ]; then
      echo "refusing: $RUN:$TO is running '$TARGET_CMD', not an agent — injected text" >&2
      echo "would be PARSED AS A COMMAND in that pane's cwd. Set INJECT_ALLOW_SHELL=1" >&2
      echo "only for a deliberate non-executing sink (e.g. 'cat >> inbox.log')." >&2
      exit 77
    fi ;;
esac

tmux send-keys -t "$RUN:$TO" -l -- "$MSG"      # -l: literal, no key-name parsing
sleep "${INJECT_SUBMIT_DELAY:-2}"
tmux send-keys -t "$RUN:$TO" Enter
sleep 1
tmux send-keys -t "$RUN:$TO" Enter             # clears a collapsed-paste prompt

# Verify submission rather than assuming it: the input box must not still hold it.
sleep 1
if tmux capture-pane -p -t "$RUN:$TO" 2>/dev/null | grep -qF "${MSG:0:40}" \
   && tmux capture-pane -p -t "$RUN:$TO" 2>/dev/null | grep -q "^❯.*${MSG:0:20}"; then
  echo "WARNING: message may still be unsubmitted in $RUN:$TO — check the pane" >&2
fi
echo "injected -> $RUN:$TO ($DIGEST)"
