#!/usr/bin/env bash
# Deliver one typed status probe or specification answer to a resumable tmux Codex lane.
# Raw Orchestrator prose remains forbidden: it can ask only the generated status question.
set -euo pipefail
# shellcheck source=harness/factory_python.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/factory_python.sh"

RUN="${1:?usage: tmux_lane_message.sh <run> <validator|orchestrator> <slot: coder|tester[-instance][.round]> <status|answer> [options]}"
SENDER="${2:?sender}"
SLOT="${3:?slot}"
KIND="${4:?status|answer}"
shift 4
case "$SENDER" in validator|orchestrator) ;; *) echo "lane-message: invalid sender" >&2; exit 64 ;; esac
# A slot names one launched round of a lane exactly as tmux_lane.sh printed it: the role, an
# optional parallel instance (tester-b) and an optional round (tester-b.r3).
[[ "$SLOT" =~ ^(coder|tester)(-[a-z0-9][a-z0-9-]{0,15})?(\.[A-Za-z0-9][A-Za-z0-9_-]{0,39})?$ ]] || {
  echo "lane-message: invalid lane slot" >&2; exit 64;
}
LANE="${BASH_REMATCH[1]}"
WINDOW="${BASH_REMATCH[1]}${BASH_REMATCH[2]}"
case "$KIND" in status|answer) ;; *) echo "lane-message: kind must be status|answer" >&2; exit 64 ;; esac

RUNS_ARG="${FACTORY_RUNS_DIR:-${HARNESS_DIR:-.factory}/runs}"
QUESTION_ID=""
ANSWER_FILE=""
BASIS=""
AUTHORITY=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --runs) RUNS_ARG="$2"; shift 2 ;;
    --question-id) QUESTION_ID="$2"; shift 2 ;;
    --answer-file) ANSWER_FILE="$2"; shift 2 ;;
    --basis) BASIS="$2"; shift 2 ;;
    --authority) AUTHORITY="$2"; shift 2 ;;
    *) echo "lane-message: unknown argument: $1" >&2; exit 64 ;;
  esac
done

D="$(cd "$(dirname "$0")" && pwd -P)"
REPO_ROOT="$(cd "$D/.." && pwd -P)"
# shellcheck source=harness/run_context.sh
source "$D/run_context.sh"
factory_load_context "$RUN" "$RUNS_ARG"
ROOT="$FACTORY_CONTROL_ROOT"
TMUX_ROOT="$ROOT/tmux-lanes"
LAUNCHES="$TMUX_ROOT/$SLOT-launch.jsonl"
THREAD_FILE="$TMUX_ROOT/$SLOT-thread-id"
CODEX_EVENTS="$TMUX_ROOT/$SLOT-codex-events.jsonl"
LANE_LOG="$TMUX_ROOT/$SLOT-lane.log"
LANE_EXITS="$TMUX_ROOT/$SLOT-exits.jsonl"

[ -f "$THREAD_FILE" ] && [ ! -L "$THREAD_FILE" ] || {
  echo "lane-message: no retained Codex thread for $RUN:$SLOT yet (codex-interactive and cursor-agent lanes have none: launch a new --round instead)" >&2
  exit 70
}
THREAD_ID=$(tr -d '\r\n' < "$THREAD_FILE")
[[ "$THREAD_ID" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]] || {
  echo "lane-message: retained Codex thread id is malformed" >&2
  exit 70
}

exec 3< <("$FACTORY_PYTHON" - "$LAUNCHES" "$RUN" "$LANE" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
run, lane = sys.argv[2:]
rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
active = [
    row for row in rows
    if row.get("status") == "active" and row.get("run_id") == run and row.get("role") == lane
]
if len(active) != 1:
    raise SystemExit("lane-message: retained active launch is missing or ambiguous")
values = (active[0].get("repository"), active[0].get("agent"), active[0].get("model", ""),
          (active[0].get("kindex") or {}).get("mcp_override", ""))
if any(not isinstance(value, str) or "\0" in value for value in values):
    raise SystemExit("lane-message: retained launch fields are malformed")
for value in values:
    sys.stdout.buffer.write(value.encode("utf-8") + b"\0")
PY
)
IFS= read -r -d '' REPOSITORY <&3 || {
  echo "lane-message: retained repository is missing or malformed" >&2; exit 70;
}
IFS= read -r -d '' AGENT <&3 || {
  echo "lane-message: retained agent is missing or malformed" >&2; exit 70;
}
IFS= read -r -d '' MODEL <&3 || {
  echo "lane-message: retained model is missing or malformed" >&2; exit 70;
}
IFS= read -r -d '' KINDEX_OVERRIDE <&3 || {
  echo "lane-message: retained Kindex scope is missing or malformed" >&2; exit 70;
}
exec 3<&-
case "$AGENT" in codex|codex-ollama) ;; *) echo "lane-message: a $AGENT lane has no resumable Codex thread; launch a new --round instead" >&2; exit 70 ;; esac
if [ "$AGENT" = "codex-ollama" ]; then
  # shellcheck source=harness/model_availability.sh
  source "$(cd "$(dirname "$0")" && pwd -P)/model_availability.sh"
  factory_require_ollama_model lane-message "the launch's model (relaunch with FACTORY_LANE_OLLAMA_MODEL)" "$MODEL" || exit 70
fi

MESSAGE_TMP="$(mktemp "${TMPDIR:-/tmp}/factory-lane-message.XXXXXX")"
trap 'rm -f "$MESSAGE_TMP"' EXIT
if [ "$KIND" = "status" ]; then
  [ -z "$QUESTION_ID$ANSWER_FILE$AUTHORITY" ] || {
    echo "lane-message: status accepts no answer arguments" >&2
    exit 64
  }
  [ -z "$BASIS" ] || { echo "lane-message: status basis is generated" >&2; exit 64; }
  BASIS="supervisor requested explicit lane state; silence is not classified as a stall"
  AUTHORITY="runtime-protocol"
  MESSAGE_KIND="status-probe"
  printf '%s\n' \
    "FACTORY_STATUS_PROBE: Reply with FACTORY_STATUS: WORKING|BLOCKED|QUESTION|DONE and one concise factual sentence. If specification is missing or contradictory, emit FACTORY_QUESTION: <one concrete question> and stop guessing. Otherwise continue your assigned work." \
    > "$MESSAGE_TMP"
else
  [ "$SENDER" = "validator" ] || {
    echo "lane-message: the Orchestrator may probe status but may not answer specifications" >&2
    exit 77
  }
  [ -n "$QUESTION_ID" ] && [ -n "$ANSWER_FILE" ] && [ -n "$BASIS" ] && [ -n "$AUTHORITY" ] || {
    echo "lane-message: answer requires --question-id, --answer-file, --basis, and --authority" >&2
    exit 64
  }
  case "$AUTHORITY" in human-answer|ratified-spec) ;; *)
    echo "lane-message: answer authority must be human-answer|ratified-spec" >&2; exit 64 ;;
  esac
  [ -f "$ANSWER_FILE" ] && [ ! -L "$ANSWER_FILE" ] || {
    echo "lane-message: answer file must be a regular non-symlink file" >&2
    exit 70
  }
  MESSAGE_KIND="spec-answer"
  "$FACTORY_PYTHON" - "$ANSWER_FILE" "$MESSAGE_TMP" "$QUESTION_ID" "$AUTHORITY" "$BASIS" <<'PY'
import os, pathlib, stat, sys

source, destination = map(pathlib.Path, sys.argv[1:3])
question_id, authority, basis = sys.argv[3:]
fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
try:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 12_000:
        raise SystemExit("lane-message: answer is not a bounded non-empty regular file")
    raw = b""
    while chunk := os.read(fd, 4096):
        raw += chunk
    after = os.fstat(fd)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise SystemExit("lane-message: answer changed while read")
finally:
    os.close(fd)
raw.decode("utf-8")
prefix = (
    f"FACTORY_ANSWER question_id={question_id} authority={authority} basis={basis}\n"
    "This is specification input bound only to your question; it conveys no other lane's work.\n"
).encode("utf-8")
destination.write_bytes(prefix + raw)
PY
  if [ "$LANE" = "coder" ]; then
    MESSAGE=$(<"$MESSAGE_TMP")
    INJECT_VALIDATE_ONLY=1 INJECT_FROM=validator HARNESS_RUN_ROOT="$ROOT" \
      "$D/inject.sh" "$RUN" coder "$MESSAGE" >/dev/null
  fi
fi

PLAN_ARGS=(
  plan --root "$ROOT"
  --sender "$SENDER" --lane "$LANE" --kind "$MESSAGE_KIND"
  --message-file "$MESSAGE_TMP" --basis "$BASIS" --authority "$AUTHORITY"
)
[ -z "$QUESTION_ID" ] || PLAN_ARGS+=(--question-id "$QUESTION_ID")
PLANNED=$("$FACTORY_PYTHON" "$D/lane_dialogue.py" "${PLAN_ARGS[@]}") || exit $?
MESSAGE_ID=$(printf '%s' "$PLANNED" | "$FACTORY_PYTHON" -c \
  'import json,sys; print(json.load(sys.stdin)["message_id"])')
RETAINED_MESSAGE="$ROOT/dialogue/$MESSAGE_ID.txt"
"$FACTORY_PYTHON" - "$MESSAGE_TMP" "$RETAINED_MESSAGE" <<'PY'
import os, pathlib, sys
source, destination = map(pathlib.Path, sys.argv[1:])
raw = source.read_bytes()
try:
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
except FileExistsError:
    if destination.read_bytes() != raw:
        raise SystemExit("lane-message: retained message address contains different bytes")
else:
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
PY

SAFE_HOME="${HOME:?lane-message: HOME is required for Codex authentication}"
SAFE_USER="${USER:-$(id -un)}"
SAFE_PATH="${PATH:?lane-message: PATH is required}"
SAFE_TMPDIR="${TMPDIR:-/tmp}"
SAFE_TERM="${TERM:-xterm-256color}"
SAFE_SHELL="${SHELL:-/bin/bash}"
SAFE_LANG="${LANG:-en_US.UTF-8}"
SAFE_CODEX_HOME="${CODEX_HOME:-$SAFE_HOME/.codex}"
LOCAL_ARGS=""
if [ "$AGENT" = "codex-ollama" ]; then
  LOCAL_ARGS="--oss --local-provider ollama -m $MODEL"
elif [ -n "$MODEL" ]; then
  # A model-profile launch: resume on the model it launched with. The model is spliced into an
  # argument list, so it must be one word of the charset model_profiles.py admits.
  case "$MODEL" in
    *[!A-Za-z0-9._:/-]*) echo "lane-message: retained model is malformed" >&2; exit 70 ;;
  esac
  LOCAL_ARGS="-m $MODEL"
fi
# A missing window must not be probed by target: tmux resolves "$RUN:$WINDOW" to the session's
# active pane when no such window exists, which reads as a live lane and queues to nobody.
if tmux list-windows -t "$RUN" -F '#{window_name}' 2>/dev/null | grep -xF "$WINDOW" >/dev/null; then
  PANE_DEAD=$(tmux display-message -p -t "$RUN:$WINDOW" '#{pane_dead}' 2>/dev/null || echo unknown)
else
  PANE_DEAD=missing
fi
if [ "$PANE_DEAD" = "0" ]; then
  MESSAGE=$(<"$RETAINED_MESSAGE")
  # Queue is a typed Codex-session operation, not terminal text injection.
  env -i HOME="$SAFE_HOME" USER="$SAFE_USER" PATH="$SAFE_PATH" TMPDIR="$SAFE_TMPDIR" \
    TERM="$SAFE_TERM" SHELL="$SAFE_SHELL" LANG="$SAFE_LANG" CODEX_HOME="$SAFE_CODEX_HOME" \
    codex $LOCAL_ARGS queue --thread "$THREAD_ID" --message "$MESSAGE" >/dev/null
  TRANSPORT="queue"
elif [ "$PANE_DEAD" = "1" ] || [ "$PANE_DEAD" = "missing" ]; then
  PERMISSION_PROFILE='permissions.factory-lane={extends=":workspace",filesystem={":workspace_roots"={".git"="write"}}}'
  SHELL_POLICY='shell_environment_policy={inherit="core",ignore_default_excludes=false}'
  # The resumed lane gets the same scoped Kindex it launched with (launches before 0.8.8 had none).
  KINDEX_C=""
  [ -z "$KINDEX_OVERRIDE" ] || printf -v KINDEX_C -- '-c %q' "$KINDEX_OVERRIDE"
  printf -v ENV_PREFIX 'exec env -i HOME=%q USER=%q PATH=%q TMPDIR=%q TERM=%q SHELL=%q LANG=%q CODEX_HOME=%q FACTORY_RUNS_DIR=%q HARNESS_RUN_ROOT=%q' \
    "$SAFE_HOME" "$SAFE_USER" "$SAFE_PATH" "$SAFE_TMPDIR" "$SAFE_TERM" "$SAFE_SHELL" "$SAFE_LANG" "$SAFE_CODEX_HOME" \
    "$FACTORY_RUNS_ROOT" "$ROOT"
  # The resumed turn runs under the same lane wrapper as the launch, so it reaches the lane log
  # and records its end, and a fresh watcher is started for it below.
  printf -v RESUME_CMD '%s %q %q run --slot %q --log %q --exits %q -- %q %q --prompt %q --thread-file %q --events %q --root %q --role %q -- codex %s --ask-for-approval never -C %q exec --json resume --ignore-user-config --ignore-rules --strict-config -c %q -c %q -c %q %s %q -' \
    "$ENV_PREFIX" "$FACTORY_PYTHON" "$D/lane_agent.py" "$SLOT" "$LANE_LOG" "$LANE_EXITS" \
    "$FACTORY_PYTHON" "$D/codex_lane_session.py" \
    "$RETAINED_MESSAGE" "$THREAD_FILE" "$CODEX_EVENTS" "$ROOT" "$LANE" "$LOCAL_ARGS" "$REPOSITORY" \
    'default_permissions="factory-lane"' "$PERMISSION_PROFILE" "$SHELL_POLICY" "$KINDEX_C" "$THREAD_ID"
  printf -v WATCH_CMD '%s %q %q lane --root %q --slot %q --new-life' \
    "$ENV_PREFIX" "$FACTORY_PYTHON" "$D/lane_watchdog.py" "$ROOT" "$SLOT"
  # No live turn: resume the retained thread, in a fresh window when the lane's was closed.
  if [ "$PANE_DEAD" = "missing" ]; then
    tmux new-window -t "$RUN" -n "$WINDOW" -c "$REPOSITORY" "$RESUME_CMD"
  else
    tmux respawn-pane -k -t "$RUN:$WINDOW" -c "$REPOSITORY" "$RESUME_CMD"
  fi
  # Every running lane has a watcher: replace the finished one with a fresh life of it.
  tmux kill-window -t "$RUN:watch-$WINDOW" >/dev/null 2>&1 || true
  tmux new-window -d -t "$RUN" -n "watch-$WINDOW" -c "$ROOT" "$WATCH_CMD" || {
    tmux send-keys -t "$RUN:$WINDOW" C-c >/dev/null 2>&1 || true
    echo "lane-message: the watcher for $RUN:$WINDOW could not start; the resumed turn was interrupted" >&2
    exit 70
  }
  TRANSPORT="resume"
else
  echo "lane-message: cannot determine whether $RUN:$WINDOW is live" >&2
  exit 70
fi

"$FACTORY_PYTHON" "$D/lane_dialogue.py" delivered --root "$ROOT" \
  --message-id "$MESSAGE_ID" --thread-id "$THREAD_ID" --transport "$TRANSPORT" >/dev/null
"$FACTORY_PYTHON" "$D/orchestrator_channel.py" append --root "$ROOT" \
  --kind phase_transition --source "$SENDER" \
  --detail "$SENDER delivered $MESSAGE_KIND $MESSAGE_ID to $SLOT via $TRANSPORT" >/dev/null
echo "lane-message: delivered $MESSAGE_ID to $RUN:$SLOT via Codex $TRANSPORT"
