#!/usr/bin/env bash
# A checkpoint is a durable notification, never permission to continue work.
set -euo pipefail
D="$(cd "$(dirname "$0")" && pwd -P)"
source "$D/factory_python.sh"
[ "$#" -ge 3 ] || { echo "usage: orchestrator_checkpoint.sh RUN KIND DETAIL [--runs PATH]" >&2; exit 64; }
RUN="$1"; KIND="$2"; DETAIL="$3"; shift 3
RUNS="${FACTORY_RUNS_DIR:-${HARNESS_DIR:-.factory}/runs}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --runs) [ "$#" -ge 2 ] || exit 64; RUNS="$2"; shift 2 ;;
    *) echo "orchestrator-checkpoint: unknown argument: $1" >&2; exit 64 ;;
  esac
done
case "$KIND" in
  pre_dispatch|pre_verdict|phase_transition|user_imperative|pre_commit|pre_first_write) ;;
  *) echo "orchestrator-checkpoint: unsupported kind: $KIND" >&2; exit 64 ;;
esac
python3 - "$D" "$RUNS" "$RUN" "$KIND" "$DETAIL" <<'PYCODE'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from run_record import load, message
try:
    root, doc = load(Path(sys.argv[2]), sys.argv[3])
    message(root, "orchestrator", "validator",
            f"Checkpoint: {sys.argv[4]}\n{sys.argv[5]}", log=doc["log"])
except (OSError, ValueError) as error:
    print(f"orchestrator-checkpoint: {error}", file=sys.stderr)
    raise SystemExit(70)
print("Checkpoint queued in the Orchestrator inbox; not an acknowledgment or approval.")
PYCODE
