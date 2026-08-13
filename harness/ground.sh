#!/usr/bin/env bash
# ground.sh — resume from disk, never from a summary (coder §0.5; durable-execution
# pattern). Produces the grounding marker lane_env requires. Run at session start
# and after every compaction.
set -euo pipefail
H="${HARNESS_DIR:-.harness}"; mkdir -p "$H"
D="$(cd "$(dirname "$0")" && pwd)"

echo "== 1/6 directive ledger =="
python3 "$D/directive.py" verify ${DIRECTIVE_REQUIRE_SIGS:+--sigs}
python3 "$D/directive.py" active --since "${LAST_GROUND:-1970-01-01T00:00:00}"

echo "== 2/6 repository ground truth =="
git fetch --quiet origin 2>/dev/null || echo "(no origin)"
echo "origin/main = $(git rev-parse origin/main 2>/dev/null || echo n/a)"

echo "== 3/6 cadence audit =="
"$D/sched_audit.sh"

echo "== 4/6 tripwire =="
# Default ON, but only where transcripts actually live: an unset var silently
# disabled the only credential check in the harness, and a hard default would
# break grounding in sandboxes that have no transcript dir at all.
[ -n "${TRANSCRIPTS:-}" ] || { [ -d "$HOME/.claude/projects" ] && TRANSCRIPTS="$HOME/.claude/projects"; } || true
if [ -n "${TRANSCRIPTS:-}" ]; then "$D/tripwire.sh" $TRANSCRIPTS
else echo "(set TRANSCRIPTS=<paths> to scan)"; fi

echo "== 5/6 channels =="
if command -v coord_list >/dev/null 2>&1; then
  coord_list | tee "$H/channels.now"
  if [ -f "$H/channels.reg" ]; then diff -u "$H/channels.reg" "$H/channels.now" \
    || { echo "channel drift — reconcile before any lane runs"; exit 4; }
  else echo "no registry — review then: cp $H/channels.now $H/channels.reg"; fi
else echo "(no coord_list on PATH)"; fi

echo "== 6/6 environment reconciliation =="
if compgen -G "$H/reconcile.d/*" >/dev/null 2>&1; then
  for r in "$H"/reconcile.d/*; do
    [ -x "$r" ] || continue
    echo "-- $(basename "$r")"
    "$r" || { echo "declared/live drift ($(basename "$r")) — resolve before any lane runs"; exit 5; }
  done
else
  echo "(no reconcilers in $H/reconcile.d — register terraform-vs-live, config-vs-runtime,"
  echo " image-digest probes for the substrate this objective touches)"
fi

date -u +%FT%TZ > "$H/grounded"
echo "grounded @ $(cat "$H/grounded")"
