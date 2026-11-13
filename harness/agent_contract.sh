#!/usr/bin/env bash
# The CLI each lane agent must offer. tmux_lane.sh runs this at launch; setup can run it
# alone to confirm (or update) an agent before a run, against this Factory version's needs.
#
# Usage: agent_contract.sh <agent> [executable] [persistent]
#   executable  registry agents only; default: harness/lane_agent.py describe
#   persistent  1 for a persistent lane
# Success: prints the contract name, a newline, then the agent's version.
# Failure: one reason on stderr naming the executable this PATH finds; exit 70.
set -uo pipefail

AGENT="${1:?usage: agent_contract.sh <agent> [executable] [persistent]}"
EXECUTABLE="${2:-}"
PERSISTENT="${3:-0}"
D=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

fail() { echo "$1" >&2; exit 70; }
at() { command -v "$1" 2>/dev/null || printf '%s (not on PATH)' "$1"; }
needs() {  # needs <help text> <cli name> <version> <flag>...
  local help="$1" name="$2" version="$3" required
  shift 3
  for required in "$@"; do
    printf '%s' "$help" | grep -q -- "$required" || \
      fail "$name CLI contract lacks $required ($version at $(at "${name%% *}"))"
  done
}

case "$AGENT" in
  codex|codex-ollama)
    VERSION=$(codex --version 2>/dev/null) || fail "codex CLI is not runnable ($(at codex))"
    EXEC_HELP=$(codex exec --help 2>/dev/null) || EXEC_HELP=""
    needs "$EXEC_HELP" codex "$VERSION" --ignore-user-config --ignore-rules --strict-config --json
    QUEUE_HELP=$(codex queue --help 2>/dev/null) || QUEUE_HELP=""
    printf '%s' "$QUEUE_HELP" | grep -q -- '--thread' || \
      fail "codex CLI contract lacks queue --thread ($VERSION at $(at codex))"
    CONTRACT="codex-exec-json-plus-queue-resume-v1" ;;
  codex-interactive)
    VERSION=$(codex --version 2>/dev/null) || fail "codex CLI is not runnable ($(at codex))"
    # --no-daemon keeps the TUI off the app-server daemon whose socket failure sent msg-r2's
    # Coder to a login screen.
    HELP=$(codex --help 2>/dev/null) || HELP=""
    needs "$HELP" codex "$VERSION" --no-daemon --no-alt-screen --strict-config
    CONTRACT="codex-tui-no-daemon-v1" ;;
  cursor-agent)
    VERSION=$(agent --version 2>/dev/null) || \
      fail "cursor-agent CLI (agent) is not runnable ($(at agent))"
    HELP=$(agent --help 2>/dev/null) || HELP=""
    for REQUIRED in --print --model --force --trust --workspace --output-format --resume \
        --approve-mcps; do
      printf '%s' "$HELP" | grep -q -- "$REQUIRED" || \
        fail "cursor-agent CLI contract lacks $REQUIRED ($VERSION at $(at agent))"
    done
    CONTRACT="cursor-agent-stream-resume-v1" ;;
  claude|claude-ollama)
    # The operator's own interactive Claude Code, hosted on a terminal (native_session.py).
    # Only what an interactive session needs is required; no print-mode flag is.
    VERSION=$(claude --version 2>/dev/null | head -1) || \
      fail "claude CLI is not runnable ($(at claude))"
    HELP=$(claude --help 2>/dev/null) || HELP=""
    needs "$HELP" claude "$VERSION" --session-id --resume --settings --mcp-config --name \
      --permission-mode
    CONTRACT="claude-native-terminal-v1" ;;
  agy|muse|prime-agent)
    # A registry harness (harness/agents.py): the registry builds its headless turn.
    if [ -z "$EXECUTABLE" ]; then
      # The Factory's own interpreter (exits 69 with the fix when there is none), never PATH's.
      # shellcheck source=harness/factory_python.sh
      source "$D/factory_python.sh"
      EXECUTABLE=$("$FACTORY_PYTHON" "$D/lane_agent.py" describe --agent "$AGENT" \
        | { IFS= read -r -d '' first; printf '%s' "$first"; }) || EXECUTABLE=""
      [ -n "$EXECUTABLE" ] || fail "$AGENT: lane_agent.py describe named no executable"
    fi
    VERSION=$("$EXECUTABLE" --version 2>/dev/null | head -1) || \
      fail "$AGENT CLI is not runnable ($(at "$EXECUTABLE"))"
    CONTRACT="agent-registry-headless-v1"
    [ "$PERSISTENT" != 1 ] || CONTRACT="agent-registry-persistent-stream-v1" ;;
  *)
    fail "$AGENT has no lane CLI contract" ;;
esac
printf '%s\n%s' "$CONTRACT" "$VERSION"
