#!/usr/bin/env bash
# status.sh — the run's status, DERIVED FROM STATE, never narrated.
#
# Why this exists: the Validator produces prose because prose is the cheapest
# thing that looks like progress. In run v8 it wrote polished status reports for
# hours across a run that was not moving, and once went twelve hours silent while
# every report it had written still read as competent. Narrative is credit for
# work; this file makes the state the only thing that shows, so the only way to
# improve the report is to improve the run.
#
# Everything below reads receipts, judge results, git, and lane state. Nothing
# here can be improved by writing about it.
#   usage: status.sh <run> [--repo <path>]
set -uo pipefail
RUN="${1:?usage: status.sh <run> [--repo <path>]}"; shift || true
REPO="$PWD"
while [ $# -gt 0 ]; do case "$1" in --repo) REPO="$2"; shift 2 ;; *) shift ;; esac; done
H="${HARNESS_DIR:-.harness}"; ROOT="$REPO/$H/runs/$RUN"
[ -d "$ROOT" ] || { echo "no run '$RUN' under $REPO/$H/runs" >&2; exit 64; }

say() { printf '%s\n' "$*"; }
say "# run $RUN — derived state, $(date -u +%FT%TZ)"

# --- what the judge actually says, newest first -----------------------------
say ""; say "## judge (from result.json, not from claims)"
latest=""
# Take the newest 8 BEFORE the loop. Piping the loop into `head` closes the pipe
# mid-iteration, and every later python child then dies on BrokenPipe.
for d in $(ls -td "$ROOT"/judge/*/ 2>/dev/null | head -8); do
  f="$d/result.json"; [ -f "$f" ] || continue
  [ -z "$latest" ] && latest="$d"
  python3 - "$f" "$(basename "$d")" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1])); r = d["red_now"]; g = d["green_now"]
    print(f"  {sys.argv[2]:<10} red {r.get('failed',0)}f/{r.get('passed',0)}p"
          f"  green {g.get('failed',0)}f/{g.get('passed',0)}p"
          f"  (exit {r.get('exit')})")
except Exception as e:
    print(f"  {sys.argv[2]:<10} UNREADABLE: {e}")
PY
done
[ -n "$latest" ] && { say ""; say "  still failing at newest judge:"
  grep -E "^FAILED" "$latest/red_now.out" 2>/dev/null | sed 's/.*:://;s/^/    /' | head -12; }

# --- lanes: what they committed, not what they said --------------------------
say ""; say "## lanes (commits and live state)"
for w in coder tester; do
  ws="$ROOT/workspaces/$w"
  [ -d "$ws" ] || { say "  $w: not dispatched"; continue; }
  n=$(git -C "$ws" rev-list --count HEAD 2>/dev/null || echo 0)
  head=$(git -C "$ws" log --oneline -1 2>/dev/null | cut -c1-64)
  live=$(tmux display -p -t "$RUN:$w" '#{pane_current_command}' 2>/dev/null || echo "gone")
  st=$(cat "$ROOT/lanes/$w.state" 2>/dev/null | head -1)
  say "  $w: $n commits | $live | ${st:-no-state}"
  say "      head: $head"
done

# --- the auditor: did it actually run? --------------------------------------
say ""; say "## independent audit"
# grep -c PRINTS a count and EXITS NONZERO on no-match, so `|| echo 0` would append
# a second line and break the numeric test below. Missing file: stdout empty, and
# the default covers it.
dead=$(grep -c ORCHESTRATOR_DID_NOT_RUN "$ROOT/wakes/receipts.jsonl" 2>/dev/null); dead=${dead:-0}
resp=$(ls "$ROOT"/wakes/*.response.md 2>/dev/null | wc -l | tr -d ' ')
say "  orchestrator responses: $resp | recorded dead wakes: $dead"
[ "${dead:-0}" -gt 0 ] && say "  ** an auditor that did not run is worse than none — downstream relaxed on nothing **"

# --- promises vs receipts ----------------------------------------------------
say ""; say "## claims vs evidence"
inj=$(wc -l < "$ROOT/injections.jsonl" 2>/dev/null | tr -d ' ' || echo 0)
ovr=$(grep -c '"oracle_override":1' "$ROOT/injections.jsonl" 2>/dev/null); ovr=${ovr:-0}
rec=$(wc -l < "$REPO/$H/receipts/chain.jsonl" 2>/dev/null | tr -d ' ' || echo 0)
say "  injections: $inj (oracle-guard overrides: $ovr) | receipts in chain: $rec"
say "  events: $(python3 -c "
import json,collections,sys
try:
    ev=[json.loads(l) for l in open('$ROOT/events.jsonl')]
    print(', '.join(f'{k}={v}' for k,v in sorted(collections.Counter(e['kind'] for e in ev).items())))
except Exception: print('none')" 2>/dev/null)"

# --- the target repo's own truth --------------------------------------------
say ""; say "## target repo"
say "  branch $(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null) @ $(git -C "$REPO" log --oneline -1 2>/dev/null | cut -c1-56)"
dirty=$(git -C "$REPO" status --porcelain 2>/dev/null | grep -vc '^?? \.harness/'); dirty=${dirty:-0}
say "  uncommitted (excluding run state): $dirty"
