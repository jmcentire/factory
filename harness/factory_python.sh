#!/usr/bin/env bash
# The harness's Python is the factory's own interpreter, never whichever `python3` happens to be
# first on PATH. On a clean machine that one is often too old or lacks the factory's
# dependencies, and a harness that silently runs on it passes or fails by accident of what else
# is installed. Sourced at the top of every harness entry point that runs factory code.
#
# Resolution, once per process tree (the result is exported):
#   1. FACTORY_PYTHON, when the operator or a parent harness script already set it;
#   2. this checkout's `.venv/bin/python` (what `make install` builds);
#   3. `python3` on PATH, only if it can import the factory (CI installs it there).
# Otherwise the script stops with the fix. Bare `python3` calls in the sourcing script resolve
# to FACTORY_PYTHON through the function below; anything handed to another process (exec, tmux,
# env -i) must name "$FACTORY_PYTHON" explicitly.
#
# Not for scripts that run the TARGET's code or a caller's command (mutate.sh, flake.sh,
# receipt.sh): the function would capture the target's `python3`.

if [ -z "${FACTORY_PYTHON:-}" ]; then
  _factory_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
  if [ -x "$_factory_root/.venv/bin/python" ]; then
    FACTORY_PYTHON="$_factory_root/.venv/bin/python"
  elif FACTORY_PYTHON="$(command -v python3)" \
    && "$FACTORY_PYTHON" -c 'import sys, factory_core; sys.exit(sys.version_info < (3, 12))' \
      >/dev/null 2>&1; then
    :
  else
    echo "harness: no Python that can run the factory (3.12+ with its dependencies). Run \`make install\` in $_factory_root" >&2
    exit 69
  fi
  unset _factory_root
fi
export FACTORY_PYTHON

python3() { "$FACTORY_PYTHON" "$@"; }
