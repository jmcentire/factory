#!/usr/bin/env bash
# Append a strategic checkpoint, notify the resident Orchestrator, and wait for
# a schema-checked assessment that covers it. The checkpoint transport makes no
# semantic judgment; the Orchestrator alone returns block or no-op.
set -euo pipefail

RUN="${1:?usage: orchestrator_checkpoint.sh <run> <kind> <detail> [--runs <path>]}"
KIND="${2:?kind}"
DETAIL="${3:?detail}"
shift 3
RUNS_ARG="${FACTORY_RUNS_DIR:-${HARNESS_DIR:-.factory}/runs}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --runs) RUNS_ARG="$2"; shift 2 ;;
    *) echo "orchestrator-checkpoint: unknown argument: $1" >&2; exit 64 ;;
  esac
done
case "$KIND" in
  pre_dispatch|pre_verdict|phase_transition|user_imperative|pre_commit|pre_first_write) ;;
  *) echo "orchestrator-checkpoint: unsupported kind: $KIND" >&2; exit 64 ;;
esac

D="$(cd "$(dirname "$0")" && pwd -P)"
# shellcheck source=harness/run_context.sh
source "$D/run_context.sh"
factory_load_context "$RUN" "$RUNS_ARG"
ROOT="$FACTORY_CONTROL_ROOT"

MODE=$(python3 - "$FACTORY_HARNESS_META" <<'PY'
import json, sys
try:
    doc = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    print("invalid")
else:
    print(doc.get("orchestrator_mode", "headless-projection"))
PY
)
if [ "$MODE" != "resident-monitoring" ]; then
  [ "$MODE" != "invalid" ] || {
    echo "orchestrator-checkpoint: harness metadata is unreadable" >&2
    exit 70
  }
  exit 0
fi

CURSOR=$(python3 "$D/orchestrator_channel.py" append --root "$ROOT" \
  --kind "$KIND" --source validator --detail "$DETAIL") || exit $?
MESSAGE="FACTORY_CHECKPOINT cursor=$CURSOR kind=$KIND. Consume EVERY unassessed record through this cursor from orchestrator/activity.jsonl. Follow orchestrator/ROLE.md's complete monitoring loop, update orchestrator/OUTSTANDING-WORK.md, write assessment/3, and submit it with orchestrator/bin/orchestrator_channel.py. Decide only block or no-op; never grant or close."
if ! INJECT_FROM=validator HARNESS_RUN_ROOT="$ROOT" INJECT_SUBMIT_DELAY=0.1 \
  "$D/inject.sh" "$RUN" orchestrator "$MESSAGE" >/dev/null; then
  echo "orchestrator-checkpoint: resident Orchestrator notification failed" >&2
  exit 70
fi

TIMEOUT="${FACTORY_ORCHESTRATOR_CHECKPOINT_TIMEOUT:-300}"
[[ "$TIMEOUT" =~ ^[0-9]+$ ]] && [ "$TIMEOUT" -gt 0 ] || {
  echo "orchestrator-checkpoint: timeout must be a positive integer" >&2
  exit 64
}
DEADLINE=$((SECONDS + TIMEOUT))
while [ "$SECONDS" -lt "$DEADLINE" ]; do
  if python3 "$D/orchestrator_channel.py" require-through --root "$ROOT" \
    --cursor "$CURSOR" >/dev/null 2>&1 && \
    python3 "$D/orchestrator_channel.py" require-current --root "$ROOT" \
      >/dev/null 2>&1; then
      exit 0
  fi
  sleep 1
done
echo "orchestrator-checkpoint: no current assessment through cursor $CURSOR within ${TIMEOUT}s" >&2
exit 70
