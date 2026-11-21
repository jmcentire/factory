#!/usr/bin/env bash
# lane_env.sh — the environment IS the capability grant.
# Only explicitly named secrets enter the child environment.
# No grounding receipt or review backlog conditions an ordinary command.
set -euo pipefail
# shellcheck source=harness/factory_python.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/factory_python.sh"
MANIFEST="${1:?usage: lane_env.sh <env-manifest> -- <cmd> [args...]}"; shift
[ "${1:-}" = "--" ] && shift
S="${HARNESS_SECRETS:-$HOME/.harness/secrets}"        # one file per secret, named
LANE_ENV_PATH="${FACTORY_LANE_ENV_PATH-/usr/local/bin:/usr/bin:/bin}"
# Check only the separators and leading slashes, regardless of path encoding.
case "$LANE_ENV_PATH" in
  ''|[!/]*|*:[!/]*|*:)
    echo "lane-env: FACTORY_LANE_ENV_PATH must be a colon-separated list of absolute directories" >&2
    exit 64
    ;;
esac
ENVV=(HOME="$PWD" PATH="$LANE_ENV_PATH" TERM="${TERM:-dumb}" LANG=C.UTF-8)
while IFS= read -r name; do
  case "$name" in ''|\#*) continue ;; esac
  [ -f "$S/$name" ] || { echo "missing secret: $name" >&2; exit 78; }
  ENVV+=("$name=$(<"$S/$name")")
done < "$MANIFEST"
exec env -i "${ENVV[@]}" "$@"
