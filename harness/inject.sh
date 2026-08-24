#!/usr/bin/env bash
# inject.sh — the ONLY sanctioned path for putting text into a lane's pane, and the
# topology is enforced here, not remembered: Validator→{coder,tester,validator};
# dispatcher/orchestrator/founder→validator only. Every injection is receipted
# (sha256 of the message, from, to, ts) into .factory/runs/<run>/injections.jsonl.
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
ROOT="${HARNESS_RUN_ROOT:-${HARNESS_DIR:-.factory}/runs/$RUN}"

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

# Oracle-leak guard on ALL coder-bound traffic, not only --results traffic.
# The bare-outcome rule is a rule the Validator must remember at compose time, and
# in run v8 the Validator broke it twice from memory while actively editing the
# document that states it. A rule an executor must recall is not a control; this
# makes it one. Blocks test-file paths, test function names, pytest vocabulary and
# assertion text on the way to the implementation lane. There is no ambient
# override: an environment variable is not authority, and a bypass a signature
# never covered is how a guard becomes theater. A genuine false positive is a
# defect in the filter pattern, repaired through a ratified change — never
# stepped around at delivery time.
if [ "$TO" = "coder" ]; then
  if printf '%s' "$MSG" | grep -qiE 'tests?/[A-Za-z0-9_]+\.py|test_[a-z0-9_]+|(^|[^a-z])tests?($|[^a-z])|pytest|assertion|traceback|fixture|conftest|oracle|::[A-Za-z_]'; then
    echo "oracle-leak refusal: coder-bound message names a test, fixture, or assertion." >&2
    echo "The Coder never learns how the oracle is built. Report WHAT failed by" >&2
    echo "requirement id, or an observation of the implementation's own behavior." >&2
    echo "There is no ambient override: a genuine false positive is a filter defect —" >&2
    echo "Fix and re-ratify the filter pattern; the boundary does not open from the" >&2
    echo "environment." >&2
    exit 80
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
#    unless the target is running an agent. There is no ambient override for a
#    non-agent sink: an environment variable is not authority, and delivery into
#    a command parser is the exact effect this guard exists to prevent.
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
  # Agent CLIs and the runtimes/launchers they appear as in the pane: opencode runs
  # on bun, and `ollama launch <integration>` keeps the launcher as the pane command.
  claude|codex|node|python*|agy|ollama|opencode|bun|deno) ;;
  *)
    echo "refusing: $RUN:$TO is running '$TARGET_CMD', not an agent — injected text" >&2
    echo "would be PARSED AS A COMMAND in that pane's cwd. There is no ambient override:" >&2
    echo "run an agent in the pane, or use a file mailbox outside tmux injection." >&2
    exit 77 ;;
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
