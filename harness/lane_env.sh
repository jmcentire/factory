#!/usr/bin/env bash
# lane_env.sh — the environment IS the capability grant.
# Enforces §1 (no lane inherits a profile; the ~40 keys can't leak because they
# were never present) plus two harness preflights: no run during an incident
# HALT, no run without a fresh grounding receipt.
set -euo pipefail
H="${HARNESS_DIR:-.harness}"
[ -e "$H/HALT" ] && { echo "HALT: $(head -1 "$H/HALT")" >&2; exit 75; }
if [ ! -e "$H/grounded" ] || [ -n "$(find "$H/grounded" -mmin +"${HARNESS_MAX_GROUND_MIN:-360}" 2>/dev/null)" ]; then
  echo "not grounded: run harness/ground.sh (re-derive state from disk, not memory)" >&2
  exit 76
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
