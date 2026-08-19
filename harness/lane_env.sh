#!/usr/bin/env bash
# lane_env.sh — the environment IS the capability grant.
# Enforces §1 (no lane inherits a profile; the ~40 keys can't leak because they
# were never present) plus two harness preflights: no run during an incident
# HALT, no run without a fresh grounding receipt.
set -euo pipefail
H="${FACTORY_HARNESS_ROOT:-${HARNESS_DIR:-.factory}}"
GROUND_ROOT="${HARNESS_RUN_ROOT:-$H}"
D="$(cd "$(dirname "$0")" && pwd -P)"
[ -e "$H/HALT" ] && { echo "HALT: $(head -1 "$H/HALT")" >&2; exit 75; }
if [ ! -e "$GROUND_ROOT/grounded" ] || [ -n "$(find "$GROUND_ROOT/grounded" -mmin +"${HARNESS_MAX_GROUND_MIN:-360}" 2>/dev/null)" ]; then
  echo "not grounded: run harness/ground.sh (re-derive state from disk, not memory)" >&2
  exit 76
fi
# Blocking-event precondition (founder refinement — the time-kill). The
# orchestrator/dispatcher gets a lane's attention by writing a blocking event,
# not by injecting prose into its pane mid-reasoning (shepherding contaminates;
# METHODOLOGY.md). lane_env refuses to START a lane past an unconsumed event, so
# the event moves work along: the lane cannot begin new work until the event is
# consumed (read and cleared) at a defined checkpoint between tasks. This is the
# legitimate control signal — class + evidence, never prose about the process.
# Gated on HARNESS_RUN/HARNESS_LANE so standalone use (no run context) is
# unaffected; the caller sets these when launching a lane under a run.
if [ -n "${HARNESS_RUN:-}" ] && [ -n "${HARNESS_LANE:-}" ]; then
  RUN_ROOT="${HARNESS_RUN_ROOT:-$H/runs/$HARNESS_RUN}"
  python3 "$D/attention_gate.py" check --root "$RUN_ROOT" --lane "$HARNESS_LANE" >/dev/null
fi
MANIFEST="${1:?usage: lane_env.sh <env-manifest> -- <cmd> [args...]}"; shift
[ "${1:-}" = "--" ] && shift
S="${HARNESS_SECRETS:-$HOME/.harness/secrets}"        # one file per secret, named
ENVV=(HOME="$PWD" PATH="/usr/local/bin:/usr/bin:/bin" TERM="${TERM:-dumb}" LANG=C.UTF-8)
while IFS= read -r name; do
  case "$name" in ''|\#*) continue ;; esac
  [ -f "$S/$name" ] || { echo "missing secret: $name" >&2; exit 78; }
  ENVV+=("$name=$(<"$S/$name")")
done < "$MANIFEST"
exec env -i "${ENVV[@]}" "$@"
