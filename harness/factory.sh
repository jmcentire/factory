#!/usr/bin/env bash
# Start an ordinary task with the operator's assigned resident agents.
set -euo pipefail
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$D/factory_python.sh"
RUN="${1:?usage: factory.sh <run> <task-or-file> --repo <repository> [--runs <path>] [--log <file>] [--budget <usd>]}"
TASK="${2:?task text or file required}"
shift 2
RUNS="${FACTORY_RUNS_DIR:-.factory/runs}"
REPO=""
EXTRA=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --runs) RUNS="${2:?runs path required}"; shift 2 ;;
    --repo) REPO="${2:?repository required}"; shift 2 ;;
    --log|--budget|--jev-screen|--jev-screen-calls) EXTRA+=("$1" "${2:?value required}"); shift 2 ;;
    *) echo "factory: unknown argument: $1" >&2; exit 64 ;;
  esac
done
[ -n "$REPO" ] || { echo "factory: --repo is required" >&2; exit 64; }
TMP_TASK="$(mktemp "${TMPDIR:-/tmp}/factory-task.XXXXXX")"
trap 'rm -f "$TMP_TASK"' EXIT
if [ -f "$TASK" ]; then cp "$TASK" "$TMP_TASK"; else printf '%s' "$TASK" > "$TMP_TASK"; fi
export PYTHONPATH="$D/.."
"$FACTORY_PYTHON" "$D/run_record.py" create --runs "$RUNS" --run "$RUN" --repo "$REPO" \
  --task-file "$TMP_TASK" ${EXTRA[@]+"${EXTRA[@]}"}
"$FACTORY_PYTHON" "$D/run_record.py" start --runs "$RUNS" --run "$RUN"
