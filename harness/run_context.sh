#!/usr/bin/env bash
# Read local task inputs. No signatures, catalog, review cursor or external checkpoint.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/factory_python.sh"

factory_load_context() {
  local run="${1:?run required}" runs="${2:?runs directory required}"
  local d values_file
  d="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
  values_file="$(mktemp "${TMPDIR:-/tmp}/factory-context.XXXXXX")" || return
  PYTHONPATH="$d/.." "$FACTORY_PYTHON" "$d/run_record.py" context --run "$run" --runs "$runs" > "$values_file" || {
    rm -f "$values_file"; return 70;
  }
  { IFS= read -r -d '' FACTORY_CONTROL_ROOT && IFS= read -r -d '' FACTORY_WORKDIR &&
    IFS= read -r -d '' FACTORY_BASE_COMMIT && IFS= read -r -d '' FACTORY_BASE_TREE &&
    IFS= read -r -d '' FACTORY_SOURCE_DIGEST && IFS= read -r -d '' FACTORY_TMUX_SOCKET &&
    IFS= read -r -d '' FACTORY_LOG; } < "$values_file" || { rm -f "$values_file"; return 70; }
  rm -f "$values_file"
  FACTORY_RUNS_ROOT="$(dirname "$FACTORY_CONTROL_ROOT")"
  FACTORY_RUNS_DIR="$FACTORY_RUNS_ROOT"
  FACTORY_HARNESS_ROOT="$(dirname "$FACTORY_RUNS_ROOT")"
  FACTORY_HARNESS_META="$FACTORY_CONTROL_ROOT/harness.json"
  FACTORY_SOURCE_ROOT="$FACTORY_WORKDIR"
  # A generation is a budget ledger label, not a phase approval.
  FACTORY_GENERATION=0
  export FACTORY_CONTROL_ROOT FACTORY_WORKDIR FACTORY_BASE_COMMIT FACTORY_BASE_TREE
  export FACTORY_SOURCE_DIGEST FACTORY_TMUX_SOCKET FACTORY_LOG FACTORY_RUNS_ROOT
  export FACTORY_RUNS_DIR FACTORY_HARNESS_ROOT FACTORY_HARNESS_META FACTORY_SOURCE_ROOT FACTORY_GENERATION
}

factory_resolve_tmux() {
  # Pane launchers call this before constructing commands that will change cwd.
  local selected_tmux
  selected_tmux=$(type -P -- "${FACTORY_TMUX:-tmux}") || {
    echo "factory: FACTORY_TMUX executable is unavailable: ${FACTORY_TMUX:-tmux}" >&2
    return 69
  }
  case "$selected_tmux" in /*) ;; *) selected_tmux="$PWD/$selected_tmux" ;; esac
  FACTORY_TMUX=$selected_tmux
  export FACTORY_TMUX
}

# Every shell operation is addressed to this run's private server, including teardown.
tmux() { command "${FACTORY_TMUX:-tmux}" -L "${FACTORY_TMUX_SOCKET:?load the run before using tmux}" "$@"; }

factory_seat_env() {
  local var
  for var in FACTORY_RUNS_DIR FACTORY_HARNESS_ROOT FACTORY_CONTROL_ROOT FACTORY_TMUX_SOCKET \
    FACTORY_LOG FACTORY_PYTHON FACTORY_HOME FACTORY_TMUX; do
    [ -z "${!var:-}" ] || printf ' %s=%q' "$var" "${!var}"
  done
  printf ' CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=false'
}
