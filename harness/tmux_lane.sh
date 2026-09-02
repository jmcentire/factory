#!/usr/bin/env bash
# Launch an operator-owned Codex author lane in tmux, then freeze its plain tree.
# This mode deliberately lets the agent own its standalone .git directory. It is
# coordination/unqualified: promotion still requires the qualified Factory path.
set -euo pipefail

RUN="${1:?usage: tmux_lane.sh <run> <coder|tester> <launch|freeze> [options]}"
ROLE="${2:?role}"
ACTION="${3:?launch|freeze}"
shift 3
case "$ROLE" in coder|tester) ;; *) echo "role must be coder|tester" >&2; exit 64 ;; esac
case "$ACTION" in launch|freeze) ;; *) echo "action must be launch|freeze" >&2; exit 64 ;; esac

RUNS_ARG="${FACTORY_RUNS_DIR:-${HARNESS_DIR:-.factory}/runs}"
REPOSITORY=""
PROMPT_SOURCE=""
AGENT="codex"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --runs) RUNS_ARG="$2"; shift 2 ;;
    --repo) REPOSITORY="$2"; shift 2 ;;
    --prompt) PROMPT_SOURCE="$2"; shift 2 ;;
    --agent) AGENT="$2"; shift 2 ;;
    *) echo "tmux-lane: unknown argument: $1" >&2; exit 64 ;;
  esac
done
case "$AGENT" in codex|codex-ollama) ;;
  *) echo "tmux-lane: agent must be codex|codex-ollama" >&2; exit 64 ;;
esac

D="$(cd "$(dirname "$0")" && pwd -P)"
REPO_ROOT="$(cd "$D/.." && pwd -P)"
FACTORY_PYTHON="${PYTHON:-python3}"
# shellcheck source=harness/run_context.sh
source "$D/run_context.sh"
factory_load_context "$RUN" "$RUNS_ARG"
ROOT="$FACTORY_CONTROL_ROOT"
TMUX_ROOT="$ROOT/tmux-lanes"
EVENTS="$TMUX_ROOT/$ROLE-launch.jsonl"
mkdir -p "$TMUX_ROOT"
chmod 700 "$TMUX_ROOT"

append_event() {
  "$FACTORY_PYTHON" - "$EVENTS" "$1" <<'PY'
import json, os, pathlib, sys

path = pathlib.Path(sys.argv[1])
row = json.loads(sys.argv[2])
payload = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
fd = os.open(
    path,
    os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
    0o600,
)
try:
    os.fchmod(fd, 0o600)
    written = 0
    while written < len(payload):
        count = os.write(fd, payload[written:])
        if count < 1:
            raise OSError("tmux lane journal append made no progress")
        written += count
    os.fsync(fd)
finally:
    os.close(fd)
PY
}

if [ "$ACTION" = "launch" ]; then
  [ -n "$REPOSITORY" ] && [ -n "$PROMPT_SOURCE" ] || {
    echo "tmux-lane: launch requires --repo and --prompt" >&2
    exit 64
  }
  [ -f "$PROMPT_SOURCE" ] && [ ! -L "$PROMPT_SOURCE" ] || {
    echo "tmux-lane: prompt must be a regular non-symlink file" >&2
    exit 70
  }
  REPOSITORY_RECEIPT=$(PYTHONPATH="$REPO_ROOT" "$FACTORY_PYTHON" \
    "$REPO_ROOT/harness/lane_repository.py" validate --source "$REPOSITORY") || exit $?
  REPOSITORY=$(printf '%s' "$REPOSITORY_RECEIPT" | "$FACTORY_PYTHON" -c \
    'import json,sys; print(json.load(sys.stdin)["root"])')
  "$FACTORY_PYTHON" - "$ROOT" "$REPOSITORY" <<'PY'
import os, pathlib, sys

control = pathlib.Path(sys.argv[1]).resolve(strict=True)
lane = pathlib.Path(sys.argv[2]).resolve(strict=True)
if os.path.commonpath((control, lane)) in {str(control), str(lane)}:
    raise SystemExit("tmux-lane: lane repository and control root may not overlap")
PY

  TASK_COPY="$TMUX_ROOT/$ROLE-task.txt"
  TASK_DIGEST=$("$FACTORY_PYTHON" - "$PROMPT_SOURCE" "$TASK_COPY" <<'PY'
import hashlib, os, pathlib, stat, sys

source, destination = map(pathlib.Path, sys.argv[1:])
fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
try:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or before.st_size > 4_194_304:
        raise SystemExit("tmux-lane: prompt is not a bounded regular file")
    raw = b""
    while chunk := os.read(fd, 1024 * 1024):
        raw += chunk
    after = os.fstat(fd)
    if not raw or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise SystemExit("tmux-lane: prompt is empty or changed while read")
finally:
    os.close(fd)
try:
    out = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
except FileExistsError:
    if destination.read_bytes() != raw:
        raise SystemExit("tmux-lane: retained prompt address contains different bytes")
else:
    with os.fdopen(out, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
print("sha256:" + hashlib.sha256(raw).hexdigest())
PY
  ) || exit $?

  PROMPT="$TMUX_ROOT/$ROLE-prompt.txt"
  PROMPT_DIGEST=$("$FACTORY_PYTHON" - "$TASK_COPY" "$PROMPT" "$ROLE" <<'PY'
import hashlib, os, pathlib, sys

source, destination = map(pathlib.Path, sys.argv[1:3])
role = sys.argv[3]
task = source.read_bytes()
protocol = f"""FACTORY TMUX LANE PROTOCOL (role={role})
- You are a real author agent, not a prompt printer. Work the task and use tools.
- This standalone repository, including its .git directory, is yours. Make useful
  checkpoint commits; do not ask the host to commit for you.
- Before each checkpoint, inspect your own status/diff, run the relevant checks,
  and commit only the work assigned to this lane.
- Do not read or infer the other author lane's work.
- If a missing or contradictory specification would make you guess semantics, stop
  the turn with one line: FACTORY_QUESTION: <one concrete question>. Do not guess.
- A FACTORY_ANSWER is specification input bound to that question, never information
  about the other lane. Resume from your own work after receiving it.
- On FACTORY_STATUS_PROBE, answer with a leading FACTORY_STATUS: WORKING, BLOCKED,
  QUESTION, or DONE and one concise factual sentence. Continue if unblocked.
- A turn ending is not the run ending. The Validator alone judges and Gate L alone
  closes the run.

VERBATIM LANE TASK
""".encode("utf-8")
raw = protocol + task
fd = os.open(
    destination,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
    0o600,
)
with os.fdopen(fd, "wb") as stream:
    stream.write(raw)
    stream.flush()
    os.fsync(stream.fileno())
print("sha256:" + hashlib.sha256(raw).hexdigest())
PY
  ) || exit $?

  # This is the final host Git use in the lane. After tmux starts the agent, the
  # repository is radioactive and only the no-Git freeze path may inspect it.
  git -C "$REPOSITORY" config user.name "Factory ${ROLE^}"
  git -C "$REPOSITORY" config user.email "factory-${ROLE}@local"

  CODEX_VERSION=$(codex --version 2>/dev/null) || {
    echo "tmux-lane: codex CLI is not runnable" >&2
    exit 70
  }
  CODEX_EXEC_HELP=$(codex exec --help 2>/dev/null) || exit 70
  CODEX_QUEUE_HELP=$(codex queue --help 2>/dev/null) || exit 70
  for REQUIRED in --ignore-user-config --ignore-rules --strict-config --json; do
    printf '%s' "$CODEX_EXEC_HELP" | grep -q -- "$REQUIRED" || {
      echo "tmux-lane: codex CLI contract lacks $REQUIRED ($CODEX_VERSION)" >&2
      exit 70
    }
  done
  printf '%s' "$CODEX_QUEUE_HELP" | grep -q -- '--thread' || {
    echo "tmux-lane: codex CLI contract lacks queue --thread ($CODEX_VERSION)" >&2
    exit 70
  }

  PERMISSION_PROFILE='permissions.factory-lane={extends=":workspace",filesystem={":workspace_roots"={".git"="write"}}}'
  SHELL_POLICY='shell_environment_policy={inherit="core",ignore_default_excludes=false}'
  PROFILE_DIGEST=$(printf '%s\n%s' "$PERMISSION_PROFILE" "$SHELL_POLICY" | shasum -a 256 | cut -d' ' -f1)
  PLANNED=$("$FACTORY_PYTHON" - "$RUN" "$ROLE" "$AGENT" "$REPOSITORY" \
    "$PROMPT" "$PROMPT_DIGEST" "$TASK_DIGEST" "$PROFILE_DIGEST" \
    "$REPOSITORY_RECEIPT" "$CODEX_VERSION" <<'PY'
import datetime, json, sys
(
    run, role, agent, repository, prompt, prompt_digest, task_digest,
    profile_digest, preflight, agent_version,
) = sys.argv[1:]
print(json.dumps({
    "schema_version": "factory-tmux-lane-launch/1",
    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "status": "planned",
    "run_id": run,
    "role": role,
    "agent": agent,
    "repository": repository,
    "prompt_path": prompt,
    "prompt_digest": prompt_digest,
    "verbatim_task_digest": task_digest,
    "permission_profile_digest": "sha256:" + profile_digest,
    "agent_version": agent_version,
    "cli_contract": "codex-exec-json-plus-queue-resume-v1",
    "repository_preflight": json.loads(preflight),
    "boundary": "operator-owned-tmux-unqualified",
    "host_git_after_agent_start": "forbidden",
}, sort_keys=True, separators=(",", ":")))
PY
  )
  append_event "$PLANNED"

  SAFE_HOME="${HOME:?tmux-lane: HOME is required for Codex authentication}"
  SAFE_USER="${USER:-$(id -un)}"
  SAFE_PATH="${PATH:?tmux-lane: PATH is required}"
  SAFE_TMPDIR="${TMPDIR:-/tmp}"
  SAFE_TERM="${TERM:-xterm-256color}"
  SAFE_SHELL="${SHELL:-/bin/bash}"
  SAFE_LANG="${LANG:-en_US.UTF-8}"
  SAFE_CODEX_HOME="${CODEX_HOME:-$SAFE_HOME/.codex}"
  LOCAL_ARGS=""
  if [ "$AGENT" = "codex-ollama" ]; then
    LOCAL_ARGS="--oss --local-provider ollama"
  fi
  THREAD_FILE="$TMUX_ROOT/$ROLE-thread-id"
  CODEX_EVENTS="$TMUX_ROOT/$ROLE-codex-events.jsonl"
  printf -v LANE_CMD 'exec env -i HOME=%q USER=%q PATH=%q TMPDIR=%q TERM=%q SHELL=%q LANG=%q CODEX_HOME=%q FACTORY_RUNS_DIR=%q HARNESS_RUN_ROOT=%q %q %q --prompt %q --thread-file %q --events %q -- codex --ask-for-approval never exec %s --ignore-user-config --ignore-rules --strict-config --json -C %q -c %q -c %q -c %q -' \
    "$SAFE_HOME" "$SAFE_USER" "$SAFE_PATH" "$SAFE_TMPDIR" "$SAFE_TERM" "$SAFE_SHELL" "$SAFE_LANG" "$SAFE_CODEX_HOME" \
    "$FACTORY_RUNS_ROOT" "$ROOT" "$FACTORY_PYTHON" "$D/codex_lane_session.py" \
    "$PROMPT" "$THREAD_FILE" "$CODEX_EVENTS" "$LOCAL_ARGS" "$REPOSITORY" \
    'default_permissions="factory-lane"' "$PERMISSION_PROFILE" "$SHELL_POLICY"

  tmux set-option -t "$RUN" remain-on-exit on >/dev/null
  if ! tmux new-window -t "$RUN" -n "$ROLE" -c "$REPOSITORY" "$LANE_CMD"; then
    FAILED=$("$FACTORY_PYTHON" - "$PLANNED" <<'PY'
import json, sys
row = json.loads(sys.argv[1]); row["status"] = "launch-failed"; print(json.dumps(row, sort_keys=True, separators=(",", ":")))
PY
    )
    append_event "$FAILED"
    echo "tmux-lane: failed to launch $RUN:$ROLE" >&2
    exit 70
  fi
  ACTIVE=$("$FACTORY_PYTHON" - "$PLANNED" <<'PY'
import datetime, json, sys
row = json.loads(sys.argv[1]); row["status"] = "active"; row["ts"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"); print(json.dumps(row, sort_keys=True, separators=(",", ":")))
PY
  )
  append_event "$ACTIVE"
  echo "tmux-lane: launched $AGENT agent in $RUN:$ROLE"
  echo "tmux-lane: the entire repository is now agent-owned; do not run host Git there"
  exit 0
fi

[ -z "$REPOSITORY" ] && [ -z "$PROMPT_SOURCE" ] || {
  echo "tmux-lane: freeze reads the retained launch receipt; --repo/--prompt are refused" >&2
  exit 64
}
[ -s "$EVENTS" ] && [ ! -L "$EVENTS" ] || {
  echo "tmux-lane: no retained launch record for $ROLE" >&2
  exit 70
}
REPOSITORY=$("$FACTORY_PYTHON" - "$EVENTS" "$RUN" "$ROLE" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
run, role = sys.argv[2:]
rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
active = [row for row in rows if row.get("status") == "active"]
if len(active) != 1 or active[0].get("run_id") != run or active[0].get("role") != role:
    raise SystemExit("tmux-lane: retained active launch record is missing or ambiguous")
print(active[0]["repository"])
PY
) || exit $?
"$FACTORY_PYTHON" "$D/lane_dialogue.py" require-clear --root "$ROOT" \
  --lane "$ROLE" >/dev/null || {
  echo "tmux-lane: unanswered $ROLE question must be answered before freeze" >&2
  exit 70
}
PANE_DEAD=$(tmux display-message -p -t "$RUN:$ROLE" '#{pane_dead}' 2>/dev/null || true)
[ "$PANE_DEAD" = "1" ] || {
  echo "tmux-lane: $RUN:$ROLE is not quiescent; stop the agent before freezing" >&2
  exit 70
}

EXPORT=$(PYTHONPATH="$REPO_ROOT" "$FACTORY_PYTHON" "$REPO_ROOT/harness/lane_repository.py" freeze \
  --source "$REPOSITORY" --store "$TMUX_ROOT/snapshots" --durable-through "$ROOT") || exit $?
FROZEN=$("$FACTORY_PYTHON" - "$RUN" "$ROLE" "$EXPORT" <<'PY'
import datetime, json, sys
run, role, export = sys.argv[1:]
print(json.dumps({
    "schema_version": "factory-tmux-lane-freeze/1",
    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "run_id": run,
    "role": role,
    "pane_dead": True,
    "export": json.loads(export),
    "boundary": "regular-files-only-no-git",
}, sort_keys=True, separators=(",", ":")))
PY
)
append_event "$FROZEN"
printf '%s\n' "$EXPORT"
