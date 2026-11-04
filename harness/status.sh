#!/usr/bin/env bash
# Status is a read, never an authorization transition.
set -euo pipefail
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$D/run_context.sh"
RUN="${1:?usage: status.sh <run> [--runs <path>]}"; shift
RUNS="${FACTORY_RUNS_DIR:-.factory/runs}"
if [ "${1:-}" = --runs ]; then RUNS="${2:?runs path required}"; shift 2; fi
[ "$#" -eq 0 ] || { echo 'status: unknown arguments' >&2; exit 64; }
factory_load_context "$RUN" "$RUNS"
PYTHONPATH="$D/.." "$FACTORY_PYTHON" "$D/run_record.py" status --runs "$RUNS" --run "$RUN"
# Missing/exited agents are displayed as such; they do not prevent reading task results.
if ! tmux list-panes -a -F '#{session_name}:#{window_name} dead=#{pane_dead} pid=#{pane_pid}' 2>/dev/null; then
  printf '%s\n' 'No live tmux server for this run.'
fi
