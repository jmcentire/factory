#!/usr/bin/env bash
# Deliver one typed status probe or specification answer to a resumable tmux Codex lane.
# Raw Orchestrator prose remains forbidden: it can ask only the generated status question.
set -euo pipefail

RUN="${1:?usage: tmux_lane_message.sh <run> <validator|orchestrator> <coder|tester> <status|answer> [options]}"
SENDER="${2:?sender}"
LANE="${3:?lane}"
KIND="${4:?status|answer}"
shift 4
case "$SENDER" in validator|orchestrator) ;; *) echo "lane-message: invalid sender" >&2; exit 64 ;; esac
case "$LANE" in coder|tester) ;; *) echo "lane-message: invalid lane" >&2; exit 64 ;; esac
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
FACTORY_PYTHON="${PYTHON:-python3}"
# shellcheck source=harness/run_context.sh
source "$D/run_context.sh"
factory_load_context "$RUN" "$RUNS_ARG"
ROOT="$FACTORY_CONTROL_ROOT"
TMUX_ROOT="$ROOT/tmux-lanes"
LAUNCHES="$TMUX_ROOT/$LANE-launch.jsonl"
THREAD_FILE="$TMUX_ROOT/$LANE-thread-id"
CODEX_EVENTS="$TMUX_ROOT/$LANE-codex-events.jsonl"

[ -f "$THREAD_FILE" ] && [ ! -L "$THREAD_FILE" ] || {
  echo "lane-message: no retained Codex thread for $RUN:$LANE yet" >&2
  exit 70
}
THREAD_ID=$(tr -d '\r\n' < "$THREAD_FILE")
[[ "$THREAD_ID" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]] || {
  echo "lane-message: retained Codex thread id is malformed" >&2
  exit 70
}

read -r REPOSITORY AGENT < <("$FACTORY_PYTHON" - "$LAUNCHES" "$RUN" "$LANE" <<'PY'
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
print(active[0]["repository"], active[0]["agent"])
PY
)
case "$AGENT" in codex|codex-ollama) ;; *) echo "lane-message: unsupported retained agent" >&2; exit 70 ;; esac

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
[ "$AGENT" != "codex-ollama" ] || LOCAL_ARGS="--oss --local-provider ollama"
PANE_DEAD=$(tmux display-message -p -t "$RUN:$LANE" '#{pane_dead}' 2>/dev/null || echo unknown)
if [ "$PANE_DEAD" = "0" ]; then
  MESSAGE=$(<"$RETAINED_MESSAGE")
  # Queue is a typed Codex-session operation, not terminal text injection.
  env -i HOME="$SAFE_HOME" USER="$SAFE_USER" PATH="$SAFE_PATH" TMPDIR="$SAFE_TMPDIR" \
    TERM="$SAFE_TERM" SHELL="$SAFE_SHELL" LANG="$SAFE_LANG" CODEX_HOME="$SAFE_CODEX_HOME" \
    codex $LOCAL_ARGS queue --thread "$THREAD_ID" --message "$MESSAGE" >/dev/null
  TRANSPORT="queue"
elif [ "$PANE_DEAD" = "1" ]; then
  PERMISSION_PROFILE='permissions.factory-lane={extends=":workspace",filesystem={":workspace_roots"={".git"="write"}}}'
  SHELL_POLICY='shell_environment_policy={inherit="core",ignore_default_excludes=false}'
  printf -v RESUME_CMD 'exec env -i HOME=%q USER=%q PATH=%q TMPDIR=%q TERM=%q SHELL=%q LANG=%q CODEX_HOME=%q FACTORY_RUNS_DIR=%q HARNESS_RUN_ROOT=%q %q %q --prompt %q --thread-file %q --events %q -- codex %s --ask-for-approval never -C %q exec --json resume --ignore-user-config --ignore-rules --strict-config -c %q -c %q -c %q %q -' \
    "$SAFE_HOME" "$SAFE_USER" "$SAFE_PATH" "$SAFE_TMPDIR" "$SAFE_TERM" "$SAFE_SHELL" "$SAFE_LANG" "$SAFE_CODEX_HOME" \
    "$FACTORY_RUNS_ROOT" "$ROOT" "$FACTORY_PYTHON" "$D/codex_lane_session.py" \
    "$RETAINED_MESSAGE" "$THREAD_FILE" "$CODEX_EVENTS" "$LOCAL_ARGS" "$REPOSITORY" \
    'default_permissions="factory-lane"' "$PERMISSION_PROFILE" "$SHELL_POLICY" "$THREAD_ID"
  tmux respawn-pane -k -t "$RUN:$LANE" -c "$REPOSITORY" "$RESUME_CMD"
  TRANSPORT="resume"
else
  echo "lane-message: cannot determine whether $RUN:$LANE is live" >&2
  exit 70
fi

"$FACTORY_PYTHON" "$D/lane_dialogue.py" delivered --root "$ROOT" \
  --message-id "$MESSAGE_ID" --thread-id "$THREAD_ID" --transport "$TRANSPORT" >/dev/null
"$FACTORY_PYTHON" "$D/orchestrator_channel.py" append --root "$ROOT" \
  --kind phase_transition --source "$SENDER" \
  --detail "$SENDER delivered $MESSAGE_KIND $MESSAGE_ID to $LANE via $TRANSPORT" >/dev/null
echo "lane-message: delivered $MESSAGE_ID to $RUN:$LANE via Codex $TRANSPORT"
