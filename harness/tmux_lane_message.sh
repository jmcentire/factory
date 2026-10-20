#!/usr/bin/env bash
# Deliver one typed status probe, specification answer, progress review or wrap-up notice to a
# resumable tmux Codex lane, or report whether the lane is idle (no live turn: a delivery now
# resumes it). Raw Orchestrator prose remains forbidden: it can ask only the generated status
# question. The watchdog sends only the generated wrap-up notice (lane_watchdog.py), and only the
# Validator sends a progress review, to a Coder lane, through the coder leak guard.
set -euo pipefail
# shellcheck source=harness/factory_python.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/factory_python.sh"

RUN="${1:?usage: tmux_lane_message.sh <run> <validator|orchestrator|watchdog> <slot: coder|tester[-instance][.round]> <status|answer|review|wrapup|idle> [options]}"
SENDER="${2:?sender}"
SLOT="${3:?slot}"
KIND="${4:?status|answer|review|wrapup|idle}"
shift 4
case "$SENDER" in validator|orchestrator|watchdog) ;; *) echo "lane-message: invalid sender" >&2; exit 64 ;; esac
# A slot names one launched round of a lane exactly as tmux_lane.sh printed it: the role, an
# optional parallel instance (tester-b) and an optional round (tester-b.r3).
[[ "$SLOT" =~ ^(coder|tester)(-[a-z0-9][a-z0-9-]{0,15})?(\.[A-Za-z0-9][A-Za-z0-9_-]{0,39})?$ ]] || {
  echo "lane-message: invalid lane slot" >&2; exit 64;
}
LANE="${BASH_REMATCH[1]}"
WINDOW="${BASH_REMATCH[1]}${BASH_REMATCH[2]}"
case "$KIND" in status|answer|review|wrapup|idle) ;; *) echo "lane-message: kind must be status|answer|review|wrapup|idle" >&2; exit 64 ;; esac
case "$SENDER:$KIND" in
  watchdog:wrapup|watchdog:idle) ;;
  watchdog:*|*:wrapup|orchestrator:review) echo "lane-message: $SENDER may not send $KIND" >&2; exit 77 ;;
esac

# The lane's pane, found by exact window name first: tmux resolves a missing "$RUN:$WINDOW" to
# the session's active pane, which reads as a live lane and queues to nobody (#29, #69).
# tmux resolves a window name it cannot find to the session's current window, even with an
# exact-match "=" prefix, so a kill or interrupt aimed at "$RUN:<name>" for a vanished lane hits
# another lane. Resolve the exact name to its unique window id and act only on that id.
window_id() {
  tmux list-windows -t "=$RUN" -F '#{window_name} #{window_id}' 2>/dev/null |
    awk -v name="$1" '$1 == name { print $2; exit }'
}

lane_pane_state() {
  if tmux list-windows -t "$RUN" -F '#{window_name}' 2>/dev/null | grep -xF "$WINDOW" >/dev/null; then
    tmux display-message -p -t "$RUN:$WINDOW" '#{pane_dead}' 2>/dev/null || echo unknown
  else
    echo missing
  fi
}

RUNS_ARG="${FACTORY_RUNS_DIR:-${HARNESS_DIR:-.factory}/runs}"
QUESTION_ID=""
ANSWER_FILE=""
BASIS=""
AUTHORITY=""
VERDICT=""
REVIEW_FILE=""
MINUTES=""
WAIT_IDLE=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --runs) RUNS_ARG="$2"; shift 2 ;;
    --question-id) QUESTION_ID="$2"; shift 2 ;;
    --answer-file) ANSWER_FILE="$2"; shift 2 ;;
    --basis) BASIS="$2"; shift 2 ;;
    --authority) AUTHORITY="$2"; shift 2 ;;
    --verdict) VERDICT="$2"; shift 2 ;;
    --review-file) REVIEW_FILE="$2"; shift 2 ;;
    --minutes) MINUTES="$2"; shift 2 ;;
    --wait-idle) WAIT_IDLE="$2"; shift 2 ;;
    *) echo "lane-message: unknown argument: $1" >&2; exit 64 ;;
  esac
done
if [ "$KIND" = "idle" ]; then
  # The signal a supervisor waits on before delivering: idle means a delivery now resumes the lane.
  case "$(lane_pane_state)" in
    missing) echo "lane-message: $RUN:$SLOT idle (window gone)"; exit 0 ;;
    1) echo "lane-message: $RUN:$SLOT idle (pane dead)"; exit 0 ;;
    0) echo "lane-message: $RUN:$SLOT busy (a turn is running)"; exit 75 ;;
    *) echo "lane-message: cannot determine whether $RUN:$WINDOW is live" >&2; exit 70 ;;
  esac
fi

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
# A registry lane's launch shape (harness/agents.py turn-record): how to resume its session.
TURN_RECORD="$TMUX_ROOT/$SLOT-turn.json"

[ -f "$THREAD_FILE" ] && [ ! -L "$THREAD_FILE" ] || {
  echo "lane-message: no retained session for $RUN:$SLOT yet (a codex-interactive lane has none, and a registry lane has one once its harness starts streaming: launch a new --round instead)" >&2
  exit 70
}
THREAD_ID=$(tr -d '\r\n' < "$THREAD_FILE")
[[ "$THREAD_ID" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]] || {
  echo "lane-message: retained session id is malformed" >&2
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
          (active[0].get("kindex") or {}).get("mcp_override", ""),
          json.dumps(active[0].get("lane_env") or dict(cache_env=dict(), runtime_dirs=[])),
          "off" if active[0].get("sandbox") == "off" else "on")
# The Codex config overrides that selected the lane's model (launches before 0.8.15 have none).
config = active[0].get("model_config") or []
if not isinstance(config, list):
    raise SystemExit("lane-message: retained launch fields are malformed")
values += tuple(config)
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
IFS= read -r -d '' LANE_ENV_RECORD <&3 || {
  echo "lane-message: retained lane environment is missing or malformed" >&2; exit 70;
}
IFS= read -r -d '' LANE_SANDBOX <&3 || {
  echo "lane-message: retained lane sandbox is missing or malformed" >&2; exit 70;
}
MODEL_CONFIG=()
while IFS= read -r -d '' OVERRIDE <&3; do MODEL_CONFIG+=("$OVERRIDE"); done
exec 3<&-
case "$AGENT" in
  codex|codex-ollama) ;;
  cursor-agent|claude|claude-ollama|agy|muse|prime-agent)
    [ -f "$TURN_RECORD" ] && [ ! -L "$TURN_RECORD" ] || {
      echo "lane-message: the $AGENT lane has no turn record to resume it by; launch a new --round instead" >&2; exit 70;
    } ;;
  *) echo "lane-message: a $AGENT lane has no resumable session; launch a new --round instead" >&2; exit 70 ;;
esac
if [ "$AGENT" = "codex-ollama" ]; then
  # shellcheck source=harness/model_availability.sh
  source "$(cd "$(dirname "$0")" && pwd -P)/model_availability.sh"
  factory_require_ollama_model lane-message "the launch's model (relaunch with FACTORY_LANE_OLLAMA_MODEL)" "$MODEL" || exit 70
fi

MESSAGE_TMP="$(mktemp "${TMPDIR:-/tmp}/factory-lane-message.XXXXXX")"
FULL_TMP="$(mktemp "${TMPDIR:-/tmp}/factory-lane-full.XXXXXX")"
INPUT_TMP="$(mktemp "${TMPDIR:-/tmp}/factory-lane-input.XXXXXX")"
trap 'rm -f "$MESSAGE_TMP" "$FULL_TMP" "$INPUT_TMP"' EXIT
# An answer past the inline ceiling travels as a digest-pinned file the lane reads (#41), in the
# FACTORY_RELAY pointer form relay_file.sh and inject.sh already use.
INPUTS="$TMUX_ROOT/$SLOT-inputs"
INPUT_DIGEST=""
if [ "$KIND" = "status" ]; then
  [ -z "$QUESTION_ID$ANSWER_FILE$AUTHORITY$VERDICT$REVIEW_FILE$MINUTES" ] || {
    echo "lane-message: status accepts no answer, review or wrap-up arguments" >&2
    exit 64
  }
  [ -z "$BASIS" ] || { echo "lane-message: status basis is generated" >&2; exit 64; }
  BASIS="supervisor requested explicit lane state; silence is not classified as a stall"
  AUTHORITY="runtime-protocol"
  MESSAGE_KIND="status-probe"
  printf '%s\n' \
    "FACTORY_STATUS_PROBE: Reply with FACTORY_STATUS: WORKING|BLOCKED|QUESTION|DONE and one concise factual sentence. If specification is missing or contradictory, end with one line made of the word FACTORY_QUESTION, a colon, and one concrete question, and stop guessing. Otherwise continue your assigned work." \
    > "$MESSAGE_TMP"
elif [ "$KIND" = "wrapup" ]; then
  # The watchdog's generated notice before a lane's cap stop (lane_watchdog.py): never prose.
  [ -z "$QUESTION_ID$ANSWER_FILE$AUTHORITY$BASIS$VERDICT$REVIEW_FILE" ] || {
    echo "lane-message: wrapup accepts only --minutes" >&2; exit 64;
  }
  [[ "$MINUTES" =~ ^[0-9]{1,3}$ ]] || { echo "lane-message: wrapup requires --minutes <n>" >&2; exit 64; }
  BASIS="the lane's wall-clock cap is $MINUTES minutes away; the watchdog stops it at the cap"
  AUTHORITY="runtime-protocol"
  MESSAGE_KIND="wrap-up"
  printf '%s\n' \
    "FACTORY_WRAP_UP: about $MINUTES minutes of this lane's time remain. Then the watchdog stops it, and the work is judged exactly as you leave it. Start nothing new. Finish or revert any half-made edit, make sure the work builds and runs through its real entrypoint, rerun the checks that passed before, commit, and end with the lane's done line." \
    > "$MESSAGE_TMP"
else
  [ -z "$MINUTES" ] || { echo "lane-message: --minutes belongs to wrapup" >&2; exit 64; }
  if [ "$KIND" = "review" ]; then
    # The Validator's progress review of a paused or finished Coder or Tester (lane_watchdog.py
    # pauses a Coder every --review-every-minutes): requirement ids and observed behaviour against
    # the ratified specification, resumed into the lane's own session so it keeps its context.
    # Founder, 2026-10-20: each role is one long-lived session; a review never becomes a new round.
    case "$LANE" in coder|tester) ;; *)
      echo "lane-message: a progress review goes to the Coder or the Tester" >&2; exit 64 ;;
    esac
    # A resumed turn of a lane that used up its cap is stopped by its watcher within a poll.
    if "$FACTORY_PYTHON" - "$TMUX_ROOT/$SLOT-watch.json" <<'PY'
import json, pathlib, sys
try:
    state = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(1)
raise SystemExit(0 if isinstance(state, dict) and state.get("status") == "capped" else 1)
PY
    then
      echo "lane-message: $RUN:$SLOT reached its metered spend cap, so a resumed turn would be stopped at once." >&2
      echo "lane-message: plan lanes have no cap; move this work to a plan-billed lane (tmux_lane.sh --replace-session '<metered cap reached>')" >&2
      exit 70
    fi
    [ -z "$QUESTION_ID$ANSWER_FILE$AUTHORITY" ] || {
      echo "lane-message: review takes --verdict, --review-file and an optional --basis" >&2; exit 64;
    }
    case "$VERDICT" in on-track|redirect|repair) ;; *)
      echo "lane-message: review requires --verdict on-track|redirect|repair" >&2; exit 64 ;;
    esac
    BODY_FILE="$REVIEW_FILE"
    AUTHORITY="ratified-spec"
    BASIS="${BASIS:-Validator progress review against the ratified specification}"
    MESSAGE_KIND="progress-review"
    VERDICT_WORD=$(printf '%s' "$VERDICT" | tr 'a-z-' 'A-Z_')
    if [ "$LANE" = tester ]; then
      PREFIX="FACTORY_REVIEW verdict=$VERDICT_WORD"$'\n'"The Validator reviewed your tests against the ratified specification. It names requirements, amendments and the behaviour it observed, never the implementation."$'\n'
    else
      PREFIX="FACTORY_REVIEW verdict=$VERDICT_WORD"$'\n'"The Validator reviewed your work against the ratified specification. It names requirements and the behaviour it observed, never the tests."$'\n'
    fi
    if [ "$VERDICT" = repair ]; then
      SUFFIX=$'\n'"Fix what it names, keeping what already works: rerun the checks that passed before and restore any you break. Then commit and end with the lane's done line again."$'\n'
    else
      SUFFIX=$'\n'"Weigh it against the specification's own words. If it shows your approach will miss a requirement, change course now; if you are certain it misreads the specification, say so in one sentence, or ask your one question in the protocol's question form. Whatever you change, keep what already works: rerun the checks that passed before and restore any you break. Then continue the assigned work."$'\n'
    fi
  else
    [ -z "$VERDICT$REVIEW_FILE" ] || { echo "lane-message: --verdict and --review-file belong to review" >&2; exit 64; }
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
    BODY_FILE="$ANSWER_FILE"
    MESSAGE_KIND="spec-answer"
    PREFIX="FACTORY_ANSWER question_id=$QUESTION_ID authority=$AUTHORITY basis=$BASIS"$'\n'"This is specification input bound only to your question; it conveys no other lane's work."$'\n'
    SUFFIX=""
  fi
  [ -f "$BODY_FILE" ] && [ ! -L "$BODY_FILE" ] || {
    echo "lane-message: $KIND file must be a regular non-symlink file" >&2
    exit 70
  }
  INPUT_DIGEST=$("$FACTORY_PYTHON" - "$BODY_FILE" "$MESSAGE_TMP" "$PREFIX" "$FULL_TMP" "$INPUT_TMP" "$INPUTS" "$SUFFIX" "$KIND" <<'PY'
import hashlib, os, pathlib, shlex, stat, sys

source, destination = map(pathlib.Path, sys.argv[1:3])
prefix = sys.argv[3].encode("utf-8")
full, staged, inputs = map(pathlib.Path, sys.argv[4:7])
suffix = sys.argv[7].encode("utf-8")
label = sys.argv[8]
INLINE = 12_000
# ponytail: the full bytes ride argv into the coder leak guard, so the file ceiling stays well
# under ARG_MAX; raise both together if answers outgrow it.
CEILING = 256 * 1024
fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
try:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= CEILING:
        raise SystemExit(f"lane-message: {label} is not a bounded non-empty regular file")
    raw = b""
    while chunk := os.read(fd, 4096):
        raw += chunk
    after = os.fstat(fd)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise SystemExit(f"lane-message: {label} changed while read")
finally:
    os.close(fd)
raw.decode("utf-8")
if b"\0" in raw:
    raise SystemExit(f"lane-message: {label} carries NUL bytes; it must be text")
full.write_bytes(prefix + raw + suffix)
if len(raw) <= INLINE:
    destination.write_bytes(prefix + raw + suffix)
    raise SystemExit(0)
digest = hashlib.sha256(raw).hexdigest()
staged.write_bytes(raw)
path = shlex.quote(str(inputs / f"{digest}.txt"))
destination.write_bytes(prefix + (
    f"The {label} is {len(raw)} bytes, delivered as a file.\n"
    f"FACTORY_RELAY file={path} sha256={digest} -- run: shasum -a 256 < {path} ; act on the file "
    "only if the digest equals this sha256, else do not act and tell the sender\n"
).encode("utf-8") + suffix)
print(digest)
PY
  ) || exit $?
  if [ "$LANE" = "tester" ] && [ "$MESSAGE_KIND" = "progress-review" ]; then
    # The other direction of oracle independence (I3): a Tester review names requirements and
    # observed behaviour, never the implementation. Refuse a diff or a source path outside tests.
    if grep -E -q '^(diff --git|@@ |\+\+\+ |--- a/)|(^|[^A-Za-z0-9_./-])([A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.(py|ts|tsx|js|jsx|mjs|go|rs|rb|java|kt)($|[^A-Za-z0-9_])' \
        < <(grep -v -E '(^|[^A-Za-z0-9_-])tests?/' "$FULL_TMP"); then
      echo "lane-message: implementation-leak refusal: a Tester review names implementation source or a diff." >&2
      echo "Name the requirement and the behaviour observed from the product's outputs, never its code." >&2
      exit 77
    fi
  fi
  if [ "$LANE" = "coder" ]; then
    # The guard judges every byte the lane will read, the file included.
    MESSAGE=$(<"$FULL_TMP")
    INJECT_VALIDATE_ONLY=1 INJECT_FROM=validator HARNESS_RUN_ROOT="$ROOT" \
      "$D/inject.sh" "$RUN" coder "$MESSAGE" >/dev/null
  fi
  if [ -n "$INPUT_DIGEST" ]; then
    # Only now, past every content guard, does the file become readable at its pinned address.
    mkdir -p "$INPUTS"
    if [ ! -e "$INPUTS/$INPUT_DIGEST.txt" ]; then
      chmod 0444 "$INPUT_TMP"
      mv -f "$INPUT_TMP" "$INPUTS/$INPUT_DIGEST.txt"
    fi
    [ "$(shasum -a 256 < "$INPUTS/$INPUT_DIGEST.txt" | cut -d' ' -f1)" = "$INPUT_DIGEST" ] || {
      echo "lane-message: the answer file at its pinned address holds different bytes" >&2
      exit 70
    }
  fi
fi

PLAN_ARGS=(
  plan --root "$ROOT"
  --sender "$SENDER" --lane "$WINDOW" --kind "$MESSAGE_KIND"
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
# Resume on the model the lane launched with: the Codex config overrides that selected it when
# the launch carried over the operator's Codex config, else the model the operator named. The
# model is spliced into an argument list, so it must be one word of the charset tmux_lane.sh admits.
LOCAL_ARGS=""
if [ "$AGENT" = "codex-ollama" ]; then
  LOCAL_ARGS="--oss --local-provider ollama -m $MODEL"
elif [ "${#MODEL_CONFIG[@]}" -eq 0 ] && [ -n "$MODEL" ]; then
  case "$MODEL" in
    *[!A-Za-z0-9._:/-]*) echo "lane-message: retained model is malformed" >&2; exit 70 ;;
  esac
  LOCAL_ARGS="-m $MODEL"
fi
# A Codex lane launched with --effort recorded it (agents.py turn-record); resume at the same.
if [ -f "$TURN_RECORD" ] && [ ! -L "$TURN_RECORD" ] && case "$AGENT" in codex) true ;; *) false ;; esac; then
  TURN_EFFORT=$("$FACTORY_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("effort",""))' "$TURN_RECORD")
  case "$TURN_EFFORT" in ""|minimal|low|medium|high|xhigh|max) ;; *) echo "lane-message: the turn record's effort is malformed" >&2; exit 70 ;; esac
  [ -z "$TURN_EFFORT" ] || LOCAL_ARGS="${LOCAL_ARGS:+$LOCAL_ARGS }-c model_reasoning_effort=$TURN_EFFORT"
fi
RESUME_ARGS="$LOCAL_ARGS"
for OVERRIDE in ${MODEL_CONFIG[@]+"${MODEL_CONFIG[@]}"}; do
  printf -v QUOTED -- '-c %q' "$OVERRIDE"
  RESUME_ARGS="${RESUME_ARGS:+$RESUME_ARGS }$QUOTED"
done
PANE_DEAD=$(lane_pane_state)
# The watchdog ends the live turn just before it delivers a wrap-up; give the pane that long to die.
[[ "$WAIT_IDLE" =~ ^[0-9]{1,3}$ ]] || { echo "lane-message: --wait-idle takes whole seconds" >&2; exit 64; }
while [ "$PANE_DEAD" = "0" ] && [ "$WAIT_IDLE" -gt 0 ]; do
  sleep 1
  WAIT_IDLE=$((WAIT_IDLE - 1))
  PANE_DEAD=$(lane_pane_state)
done
if [ "$PANE_DEAD" = "0" ]; then
  # A queued message waits for the lane's next turn boundary (observed: up to ~55 min, and once
  # never), and nothing reports when it is consumed. So a queue is never a delivery receipt (#68).
  if [ "$MESSAGE_KIND" != "status-probe" ]; then
    echo "lane-message: $MESSAGE_ID planned but NOT delivered: $RUN:$SLOT is mid-turn, and a queued" >&2
    echo "message has no delivery receipt. Retry when '$0 $RUN $SENDER $SLOT idle' reports idle;" >&2
    echo "the retry reuses this planned message." >&2
    exit 75
  fi
  case "$AGENT" in codex|codex-ollama) ;; *)
    echo "lane-message: $MESSAGE_ID planned but NOT delivered: a live $AGENT turn takes no queued message;" >&2
    echo "retry when '$0 $RUN $SENDER $SLOT idle' reports idle." >&2
    exit 75 ;;
  esac
  MESSAGE=$(<"$RETAINED_MESSAGE")
  # Queue is a typed Codex-session operation, not terminal text injection.
  env -i HOME="$SAFE_HOME" USER="$SAFE_USER" PATH="$SAFE_PATH" TMPDIR="$SAFE_TMPDIR" \
    TERM="$SAFE_TERM" SHELL="$SAFE_SHELL" LANG="$SAFE_LANG" CODEX_HOME="$SAFE_CODEX_HOME" \
    codex $LOCAL_ARGS queue --thread "$THREAD_ID" --message "$MESSAGE" >/dev/null
  echo "lane-message: queued $MESSAGE_ID to $RUN:$SLOT, NOT delivered: it reaches the lane at its next turn boundary, with no receipt"
  exit 0
elif [ "$PANE_DEAD" = "1" ] || [ "$PANE_DEAD" = "missing" ]; then
  PERMISSION_PROFILE='permissions.factory-lane={extends=":workspace",filesystem={":workspace_roots"={".git"="write"}}}'
  # The resumed turn's commands get the same dependency cache and runtime as the launch
  # (dogfood #31/#61), re-validated: a store made writable since launch is refused.
  exec 4< <("$FACTORY_PYTHON" "$D/lane_agent.py" environment --path "$SAFE_PATH" \
    --record "$LANE_ENV_RECORD")
  { IFS= read -r -d '' SHELL_POLICY <&4 && IFS= read -r -d '' LANE_PATH <&4 &&
    IFS= read -r -d '' _ <&4 && IFS= read -r -d '' _ <&4 &&
    IFS= read -r -d '' LANE_ENV_ASSIGN <&4; } || {
    echo "lane-message: the launch's lane environment no longer holds (see lane-agent above)" >&2
    exit 70
  }
  exec 4<&-
  # The resumed lane gets the same scoped Kindex it launched with (launches before 0.8.8 had none).
  KINDEX_C=""
  [ -z "$KINDEX_OVERRIDE" ] || printf -v KINDEX_C -- '-c %q' "$KINDEX_OVERRIDE"
  printf -v ENV_PREFIX 'exec env -i HOME=%q USER=%q PATH=%q TMPDIR=%q TERM=%q SHELL=%q LANG=%q CODEX_HOME=%q FACTORY_RUNS_DIR=%q HARNESS_RUN_ROOT=%q' \
    "$SAFE_HOME" "$SAFE_USER" "$LANE_PATH" "$SAFE_TMPDIR" "$SAFE_TERM" "$SAFE_SHELL" "$SAFE_LANG" "$SAFE_CODEX_HOME" \
    "$FACTORY_RUNS_ROOT" "$ROOT"
  ENV_PREFIX+="${LANE_ENV_ASSIGN:+ $LANE_ENV_ASSIGN}"
  # The resumed turn runs under the same lane wrapper as the launch, so it reaches the lane log
  # and records its end, and a fresh watcher is started for it below.
  case "$AGENT" in
    codex|codex-ollama)
  # The resume runs sandboxed exactly as its launch did (the launch record's FACTORY_LANE_SANDBOX).
  RESUME_SANDBOX=""
  [ "$LANE_SANDBOX" != off ] || RESUME_SANDBOX="--dangerously-bypass-approvals-and-sandbox "
  printf -v RESUME_CMD '%s %q %q run --slot %q --log %q --exits %q -- %q %q --prompt %q --thread-file %q --events %q --root %q --role %q -- codex %s --ask-for-approval never -C %q exec --json resume %s--ignore-user-config --ignore-rules --strict-config -c %q -c %q -c %q %s %q -' \
    "$ENV_PREFIX" "$FACTORY_PYTHON" "$D/lane_agent.py" "$SLOT" "$LANE_LOG" "$LANE_EXITS" \
    "$FACTORY_PYTHON" "$D/codex_lane_session.py" \
    "$RETAINED_MESSAGE" "$THREAD_FILE" "$CODEX_EVENTS" "$ROOT" "$WINDOW" "$RESUME_ARGS" "$REPOSITORY" \
    "$RESUME_SANDBOX" 'default_permissions="factory-lane"' "$PERMISSION_PROFILE" "$SHELL_POLICY" "$KINDEX_C" "$THREAD_ID" ;;
    *)
      # A registry harness resumes its own session with the arguments, env file and billing it
      # launched with (its turn record); the wrapper keeps recording the session and billing.
      exec 5< <("$FACTORY_PYTHON" - "$TURN_RECORD" "$AGENT" <<'PY'
import json, sys
record = json.load(open(sys.argv[1], encoding="utf-8"))
if record.get("schema_version") != "factory-lane-turn/1" or record.get("agent") != sys.argv[2]:
    raise SystemExit("lane-message: the turn record does not match this lane")
fields = [record.get("model", ""), record.get("effort", ""), record.get("billing", ""),
          record.get("env_file", ""), *record.get("extra", [])]
if any(not isinstance(field, str) or "\0" in field for field in fields):
    raise SystemExit("lane-message: the turn record is malformed")
sys.stdout.write("".join(field + "\0" for field in fields))
PY
)
      { IFS= read -r -d '' TURN_MODEL <&5 && IFS= read -r -d '' TURN_EFFORT <&5 &&
        IFS= read -r -d '' TURN_BILLING <&5 && IFS= read -r -d '' TURN_ENV_FILE <&5; } || {
        echo "lane-message: the turn record could not be read" >&2; exit 70;
      }
      TURN_EXTRA=()
      while IFS= read -r -d '' PART <&5; do TURN_EXTRA+=("--extra-arg=$PART"); done
      exec 5<&-
      TURN=()
      while IFS= read -r -d '' PART; do TURN+=("$PART"); done < <(
        "$FACTORY_PYTHON" "$D/agents.py" turn --agent "$AGENT" --model "$TURN_MODEL" \
          --effort "$TURN_EFFORT" --cwd "$REPOSITORY" --resume "$THREAD_ID" \
          ${TURN_EXTRA[@]+"${TURN_EXTRA[@]}"})
      [ "${#TURN[@]}" -ge 2 ] || { echo "lane-message: the agent registry built no resume turn" >&2; exit 70; }
      QUOTED_TURN=""
      for PART in "${TURN[@]:1}"; do printf -v QUOTED_TURN '%s %q' "$QUOTED_TURN" "$PART"; done
      PLAN_FLAG=""
      [ "$TURN_BILLING" != plan ] || PLAN_FLAG="--plan-only"
      printf -v RESUME_CMD '%s %q %q run --slot %q --log %q --exits %q --env-file %q --session-agent %q --session-file %q %s --prompt-arg %q --%s' \
        "$ENV_PREFIX" "$FACTORY_PYTHON" "$D/lane_agent.py" "$SLOT" "$LANE_LOG" "$LANE_EXITS" \
        "$TURN_ENV_FILE" "$AGENT" "$THREAD_FILE" "$PLAN_FLAG" "$RETAINED_MESSAGE" "$QUOTED_TURN" ;;
  esac
  printf -v WATCH_CMD '%s %q %q lane --root %q --slot %q --new-life' \
    "$ENV_PREFIX" "$FACTORY_PYTHON" "$D/lane_watchdog.py" "$ROOT" "$SLOT"
  # A resumed turn can change the tree after a review freeze, so the journal records the reopen
  # before the turn starts: discovery_check.latest_freeze then refuses until the lane is frozen
  # again, instead of passing the mid-run review snapshot as the candidate. Recorded first, so a
  # resume that fails to start still reads as unfrozen (fail closed).
  # The turn is measured against its delivery limit and recorded; over the limit is never a
  # refusal (lane_agent.py places an oversized argv turn in .git/factory-brief/ by reference).
  "$FACTORY_PYTHON" - "$LAUNCHES" "$RUN" "$LANE" "$SLOT" "$SENDER" "$MESSAGE_KIND" "$MESSAGE_ID" \
    "$RETAINED_MESSAGE" "$AGENT" <<'PY'
import datetime, json, os, sys
journal, run, role, slot, sender, kind, message_id, message, agent = sys.argv[1:]
size = os.path.getsize(message)
# Codex resumes read the turn from stdin; every registry harness takes it as one argv element.
limit = None if agent in ("codex", "codex-ollama") else 131071
delivery = "stdin" if limit is None else ("file-pointer" if size > limit else "argv")
row = json.dumps({
    "schema_version": "factory-tmux-lane-reopen/1",
    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "run_id": run, "role": role, "slot": slot, "sender": sender,
    "message_kind": kind, "message_id": message_id,
    "prompt_bytes": size, "prompt_limit": limit, "prompt_delivery": delivery,
}, sort_keys=True, separators=(",", ":"))
fd = os.open(journal, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
try:
    os.write(fd, (row + "\n").encode("utf-8"))
    os.fsync(fd)
finally:
    os.close(fd)
PY
  # No live turn: resume the retained thread, in a fresh window when the lane's was closed.
  if [ "$PANE_DEAD" = "missing" ]; then
    tmux new-window -t "$RUN" -n "$WINDOW" -c "$REPOSITORY" "$RESUME_CMD"
    tmux set-option -w -t "$RUN:$WINDOW" remain-on-exit on >/dev/null 2>&1 || true
  else
    tmux respawn-pane -k -t "$RUN:$WINDOW" -c "$REPOSITORY" "$RESUME_CMD"
  fi
  # Every running lane has a watcher: replace the finished one with a fresh life of it.
  WATCH_WID=$(window_id "watch-$WINDOW")
  [ -z "$WATCH_WID" ] || tmux kill-window -t "$WATCH_WID" >/dev/null 2>&1 || true
  tmux new-window -d -t "$RUN" -n "watch-$WINDOW" -c "$ROOT" "$WATCH_CMD" || {
    WID=$(window_id "$WINDOW"); [ -z "$WID" ] || tmux send-keys -t "$WID" C-c >/dev/null 2>&1 || true
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
# The activity feed is the Orchestrator's input; its own deliveries are not echoed back to it.
if [ "$SENDER" = validator ]; then
  "$FACTORY_PYTHON" "$D/orchestrator_channel.py" append --root "$ROOT" \
    --kind phase_transition --source "$SENDER" \
    --detail "$SENDER delivered $MESSAGE_KIND $MESSAGE_ID to $SLOT via $TRANSPORT" >/dev/null
fi
case "$AGENT" in codex|codex-ollama) VIA=Codex ;; *) VIA="$AGENT" ;; esac
echo "lane-message: delivered $MESSAGE_ID to $RUN:$SLOT via $VIA $TRANSPORT"
