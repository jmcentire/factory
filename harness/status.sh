#!/usr/bin/env bash
# Render one run from checked runtime state and run-owned evidence only.
set -euo pipefail
RUN="${1:?usage: status.sh <run> [--runs <path>]}"
shift || true
RUNS_ARG="${FACTORY_RUNS_DIR:-${HARNESS_DIR:-.factory}/runs}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --runs) RUNS_ARG="$2"; shift 2 ;;
    --repo|--sha) echo "status: $1 is forbidden; target-state selects the subject" >&2; exit 64 ;;
    *) echo "status: unknown argument: $1" >&2; exit 64 ;;
  esac
done
D="$(cd "$(dirname "$0")" && pwd -P)"
FACTORY_CLI="${FACTORY_CLI:-factory}"
# shellcheck source=harness/run_context.sh
source "$D/run_context.sh"
factory_load_context "$RUN" "$RUNS_ARG"
ROOT="$FACTORY_CONTROL_ROOT"

say() { printf '%s\n' "$*"; }
say "# run $RUN — checked state, $(date -u +%FT%TZ)"
say ""
say "## authority and target"
say "  runtime state : $FACTORY_RUN_STATE"
say "  target-state  : $FACTORY_TARGET_STATE_DIGEST"
say "  commit        : $FACTORY_BASE_COMMIT"
say "  tree          : $FACTORY_BASE_TREE"
say "  source_root   : $FACTORY_SOURCE_ROOT"
say "  workdir       : $FACTORY_WORKDIR"
if [ -f "$ROOT/harness.json" ] && [ ! -L "$ROOT/harness.json" ]; then
  abandonment_state="absent"
  if [ -e "$ROOT/legacy-harness-abandonment.json" ] || \
     [ -L "$ROOT/legacy-harness-abandonment.json" ]; then
    if python3 "$D/legacy_abandonment.py" --harness "$ROOT/harness.json" \
      --receipt "$ROOT/legacy-harness-abandonment.json" --run "$RUN" >/dev/null 2>&1; then
      abandonment_state="verified"
    else
      abandonment_state="invalid"
    fi
  fi
  python3 - "$ROOT/harness.json" "$abandonment_state" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
abandonment = sys.argv[2]
state = {
    "verified": "abandoned-legacy",
    "invalid": "INVALID (unverified legacy abandonment marker)",
}.get(abandonment, doc.get("status", "UNKNOWN"))
print(f"  harness state : {state}")
print(f"  launcher      : {doc.get('launcher_qualification', 'UNQUALIFIED')}")
print(f"  isolation     : {doc.get('lane_isolation', 'UNQUALIFIED')}")
print(f"  budget        : {doc.get('budget_usd')} ({doc.get('budget_enforcement', 'UNQUALIFIED')})")
PY
else
  if [ -e "$ROOT/harness.json" ] || [ -L "$ROOT/harness.json" ]; then
    say "  harness state : INVALID (not a regular retained artifact)"
  else
    say "  harness state : not ignited"
  fi
fi

say ""
say "## judge (from result.json)"
latest=""
while IFS= read -r -d '' directory; do
  file="$directory/result.json"; [ -f "$file" ] || continue
  [ -z "$latest" ] && latest="$directory"
  python3 - "$file" "$(basename "$directory")" <<'PY'
import json, sys
try:
    doc = json.load(open(sys.argv[1])); red = doc["red_now"]; green = doc["green_now"]
    print(f"  {sys.argv[2]:<10} red {red.get('failed',0)}f/{red.get('passed',0)}p"
          f"  green {green.get('failed',0)}f/{green.get('passed',0)}p")
except Exception as exc:
    print(f"  {sys.argv[2]:<10} UNREADABLE: {exc}")
PY
done < <(python3 - "$ROOT/judge" <<'PY'
import os, pathlib, sys

root = pathlib.Path(sys.argv[1])
if root.is_dir() and not root.is_symlink():
    directories = sorted(
        (path for path in root.iterdir() if path.is_dir() and not path.is_symlink()),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )[:8]
    for directory in directories:
        sys.stdout.buffer.write(os.fsencode(str(directory)) + b"\0")
PY
)
[ -n "$latest" ] || say "  no judge receipts"

say ""
say "## run-owned lanes"
for role in coder tester; do
  workspace="$ROOT/workspaces/$role"
  if [ ! -d "$workspace" ]; then say "  $role: not dispatched"; continue; fi
  count=$(git -C "$workspace" rev-list --count HEAD 2>/dev/null || echo 0)
  head=$(git -C "$workspace" log --oneline -1 2>/dev/null | cut -c1-64)
  live=$(tmux display -p -t "$RUN:$role" '#{pane_current_command}' 2>/dev/null || echo gone)
  state=$(head -1 "$ROOT/lanes/$role.state" 2>/dev/null || true)
  say "  $role: $count commits | $live | ${state:-no-state}"
  say "      head: $head"
done

say ""
say "## run resources"
$FACTORY_CLI verify-resources --runs "$FACTORY_RUNS_ROOT" --run-id "$RUN" || true

say ""
say "## evidence counts"
receipts=$(wc -l < "$FACTORY_HARNESS_ROOT/receipts/chain.jsonl" 2>/dev/null | tr -d ' ' || echo 0)
dispatches=$(wc -l < "$ROOT/dispatches.jsonl" 2>/dev/null | tr -d ' ' || echo 0)
events=$(wc -l < "$ROOT/events.jsonl" 2>/dev/null | tr -d ' ' || echo 0)
say "  receipts=$receipts dispatches=$dispatches events=$events"
