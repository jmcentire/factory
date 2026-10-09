#!/usr/bin/env bash
# Launch an operator-owned author lane in tmux, qualify a model for one, or freeze a lane's
# plain tree. The agent owns its standalone .git directory. This mode is
# coordination/unqualified: promotion still requires the qualified Factory path.
#
# This script is the only lane launcher (prompts/validate.md Phase B item 7). Every launch:
#   - binds the agent and model from the run's model profile (or --agent/--model without one);
#   - preflights the agent's sign-in non-interactively and refuses, with the exact fix, an agent
#     that would sit on a login screen (harness/lane_agent.py);
#   - refuses a metered agent without a per-round spend cap and a declared rate, reserves the
#     cap in the run's objective-budget ledger (harness/lane_budget.py), and refuses a metered
#     agent/model that has not passed a qualification probe;
#   - puts the founder's testing standard into every Tester prompt (harness/testing_standard.py);
#   - starts a watcher window beside the lane (harness/lane_watchdog.py lane) that stops a stalled or
#     capped lane and wakes the operator when the lane finishes. A lane with no watcher is not
#     launched.
set -euo pipefail
# shellcheck source=harness/factory_python.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/factory_python.sh"

USAGE='usage: tmux_lane.sh <run> <coder|tester> <launch|qualify|freeze> [options]
  launch|qualify  --repo <the lane'"'"'s own clone> --prompt <brief file>
                  [--agent codex|codex-interactive|codex-ollama|cursor-agent] [--model <model>]
                  [--instance <name>] [--round <tag>] [--metered]
                  [--spend-cap-usd <usd> --usd-per-hour <usd>] [--wall-cap-minutes <min>]
                  [--read-allowance-minutes <min, default 15>] [--stall-window-minutes <min, default 20>]
  qualify only    [--first-commit-minutes <min, default 15>] [--min-commits <n, default 1>]
  freeze          [--instance <name>] [--round <tag>]
  all             [--runs <runs dir>]'
RUN="${1:?$USAGE}"
ROLE="${2:?$USAGE}"
ACTION="${3:?$USAGE}"
shift 3
case "$ROLE" in coder|tester) ;; *) echo "role must be coder|tester" >&2; exit 64 ;; esac
case "$ACTION" in launch|qualify|freeze) ;; *) echo "action must be launch|qualify|freeze" >&2; exit 64 ;; esac

RUNS_ARG="${FACTORY_RUNS_DIR:-${HARNESS_DIR:-.factory}/runs}"
REPOSITORY=""
PROMPT_SOURCE=""
AGENT_ARG=""
MODEL_ARG=""
INSTANCE=""
ROUND=""
METERED_ARG=0
SPEND_CAP=""
RATE=""
WALL_CAP_MINUTES=""
READ_ALLOWANCE_MINUTES=15
STALL_WINDOW_MINUTES=20
FIRST_COMMIT_MINUTES=""
MIN_COMMITS=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --runs) RUNS_ARG="$2"; shift 2 ;;
    --repo) REPOSITORY="$2"; shift 2 ;;
    --prompt) PROMPT_SOURCE="$2"; shift 2 ;;
    --agent) AGENT_ARG="$2"; shift 2 ;;
    --model) MODEL_ARG="$2"; shift 2 ;;
    --instance) INSTANCE="$2"; shift 2 ;;
    --round) ROUND="$2"; shift 2 ;;
    --metered) METERED_ARG=1; shift ;;
    --spend-cap-usd) SPEND_CAP="$2"; shift 2 ;;
    --usd-per-hour) RATE="$2"; shift 2 ;;
    --wall-cap-minutes) WALL_CAP_MINUTES="$2"; shift 2 ;;
    --read-allowance-minutes) READ_ALLOWANCE_MINUTES="$2"; shift 2 ;;
    --stall-window-minutes) STALL_WINDOW_MINUTES="$2"; shift 2 ;;
    --first-commit-minutes) FIRST_COMMIT_MINUTES="$2"; shift 2 ;;
    --min-commits) MIN_COMMITS="$2"; shift 2 ;;
    *) echo "tmux-lane: unknown argument: $1" >&2; exit 64 ;;
  esac
done
AGENT="${AGENT_ARG:-codex}"
case "$AGENT" in codex|codex-interactive|codex-ollama|cursor-agent) ;;
  *) echo "tmux-lane: agent must be codex|codex-interactive|codex-ollama|cursor-agent" >&2; exit 64 ;;
esac
[ -z "$INSTANCE" ] || [[ "$INSTANCE" =~ ^[a-z0-9][a-z0-9-]{0,15}$ ]] || {
  echo "tmux-lane: --instance must be 1-16 lowercase letters, digits or '-'" >&2; exit 64;
}
if [ "$ACTION" = qualify ] && [ -z "$ROUND" ]; then
  ROUND="qualify-$(date -u +%Y%m%dT%H%M%SZ)"
fi
[ -z "$ROUND" ] || [[ "$ROUND" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$ ]] || {
  echo "tmux-lane: --round must be 1-40 letters, digits, '_' or '-'" >&2; exit 64;
}
# A lane is a role or a parallel instance of it (tester-b); a slot is one round of a lane. Each
# slot keeps its own prompt, journal, thread, log and watcher state under tmux-lanes/.
LANE="$ROLE${INSTANCE:+-$INSTANCE}"
SLOT="$LANE${ROUND:+.$ROUND}"

D="$(cd "$(dirname "$0")" && pwd -P)"
# shellcheck source=harness/model_availability.sh
source "$D/model_availability.sh"
REPO_ROOT="$(cd "$D/.." && pwd -P)"
# shellcheck source=harness/run_context.sh
source "$D/run_context.sh"
factory_load_context "$RUN" "$RUNS_ARG"
ROOT="$FACTORY_CONTROL_ROOT"
TMUX_ROOT="$ROOT/tmux-lanes"

# Every refusal after the run root resolves leaves one events.jsonl row (refusal-tmux-lane).
refuse() {
  local code="$1" message="$2" remediation="${3:-}"
  "$FACTORY_PYTHON" "$D/attention_gate.py" refusal-event --root "$ROOT" \
    --kind refusal-tmux-lane --source tmux_lane.sh --detail "$SLOT: $message" \
    --exit-code "$code" >/dev/null 2>&1 || echo "tmux-lane: refusal event could not be recorded" >&2
  echo "tmux-lane: $message" >&2
  [ -z "$remediation" ] || echo "tmux-lane: remediation: $remediation" >&2
  exit "$code"
}

# The run's model-profile.json, which factory.sh writes for every run, says how it was started.
# With a profile, it binds this lane's agent and model, and --agent, --model or
# FACTORY_LANE_OLLAMA_MODEL is refused if it disagrees, never silently preferred. With an
# explicit none, they keep their earlier meaning. A qualify probe exists to try a candidate the
# profile does not bind yet, so it may name another agent and model.
MODEL=""
PROFILE_METERED=""
PROFILE_SNAPSHOT="$ROOT/model-profile.json"
PROFILE_STATE=""
if [ "$ACTION" != "freeze" ]; then
  # factory.sh records how every run was started. Without that record the lane cannot tell a run
  # started with no profile from one whose record was lost, so it refuses rather than guess.
  [ -e "$PROFILE_SNAPSHOT" ] || [ -L "$PROFILE_SNAPSHOT" ] || {
    echo "tmux-lane: $PROFILE_SNAPSHOT is missing. factory.sh records every run's model profile (an explicit none when it used none), so this run was ignited before 0.8.11 or the record was lost; ignite a new run with harness/factory.sh" >&2
    exit 64
  }
  exec 3< <("$FACTORY_PYTHON" "$D/model_profile.py" binding \
    --snapshot "$PROFILE_SNAPSHOT" --role "$ROLE")
  IFS= read -r -d '' PROFILE_STATE <&3 || exit 64
  case "$PROFILE_STATE" in
    profile|none) ;;
    *) echo "tmux-lane: the run's model profile record is malformed" >&2; exit 70 ;;
  esac
fi
if [ "$PROFILE_STATE" = "profile" ]; then
  { IFS= read -r -d '' PROFILE_AGENT <&3 && IFS= read -r -d '' PROFILE_MODEL <&3 &&
    IFS= read -r -d '' PROFILE_METERED <&3; } || exit 70
  exec 3<&-
  if [ "$ACTION" = "qualify" ] && [ -n "$AGENT_ARG$MODEL_ARG" ]; then
    MODEL="${MODEL_ARG:-$PROFILE_MODEL}"
    [ -n "$AGENT_ARG" ] || AGENT="$PROFILE_AGENT"
    # The profile's metered mark describes its own binding, not a candidate's.
    [ "$AGENT" = "$PROFILE_AGENT" ] && [ "$MODEL" = "$PROFILE_MODEL" ] || PROFILE_METERED=""
  else
    [ -z "$AGENT_ARG" ] || [ "$AGENT_ARG" = "$PROFILE_AGENT" ] || {
      echo "tmux-lane: --agent $AGENT_ARG contradicts the run's model profile ($ROLE: $PROFILE_AGENT)" >&2
      exit 64
    }
    [ -z "$MODEL_ARG" ] || [ "$MODEL_ARG" = "$PROFILE_MODEL" ] || {
      echo "tmux-lane: --model $MODEL_ARG contradicts the run's model profile ($ROLE: $PROFILE_MODEL)" >&2
      exit 64
    }
    AGENT="$PROFILE_AGENT"
    MODEL="$PROFILE_MODEL"
  fi
  if [ "$AGENT" = "codex-ollama" ]; then
    [ -z "${FACTORY_LANE_OLLAMA_MODEL:-}" ] || [ "$FACTORY_LANE_OLLAMA_MODEL" = "$MODEL" ] || {
      echo "tmux-lane: FACTORY_LANE_OLLAMA_MODEL=$FACTORY_LANE_OLLAMA_MODEL contradicts the run's model profile ($ROLE: $MODEL)" >&2
      exit 64
    }
    factory_require_ollama_model tmux-lane "the $ROLE model in the run's model profile" "$MODEL" || exit $?
  fi
elif [ "$PROFILE_STATE" = "none" ]; then
  exec 3<&-
  # Started without a profile: --agent, --model and FACTORY_LANE_OLLAMA_MODEL name the binding.
  MODEL="$MODEL_ARG"
  if [ "$AGENT" = "codex-ollama" ]; then
    [ -z "$MODEL" ] || [ -z "${FACTORY_LANE_OLLAMA_MODEL:-}" ] || \
      [ "$MODEL" = "$FACTORY_LANE_OLLAMA_MODEL" ] || {
      echo "tmux-lane: --model $MODEL contradicts FACTORY_LANE_OLLAMA_MODEL=$FACTORY_LANE_OLLAMA_MODEL" >&2
      exit 64
    }
    MODEL="${MODEL:-${FACTORY_LANE_OLLAMA_MODEL:-}}"
    factory_require_ollama_model tmux-lane FACTORY_LANE_OLLAMA_MODEL "$MODEL" || exit $?
  fi
fi
if [ "$ACTION" != "freeze" ]; then
  case "$MODEL" in
    *[!A-Za-z0-9._:/-]*) echo "tmux-lane: model is not one word of [A-Za-z0-9._:/-]: $MODEL" >&2; exit 64 ;;
  esac
  [ "$AGENT" != "cursor-agent" ] || [ -n "$MODEL" ] || {
    echo "tmux-lane: cursor-agent needs a named model (the run's model profile, or --model); the factory never picks one" >&2
    exit 64
  }
  exec 4< <("$FACTORY_PYTHON" "$D/lane_agent.py" describe --agent "$AGENT")
  { IFS= read -r -d '' AGENT_EXECUTABLE <&4 && IFS= read -r -d '' PROMPT_VIA <&4 &&
    IFS= read -r -d '' AGENT_TTY <&4 && IFS= read -r -d '' AGENT_METERED <&4; } || exit 70
  exec 4<&-
  METERED=false
  if [ "$AGENT_METERED" = metered ] || [ "$PROFILE_METERED" = metered ] || [ "$METERED_ARG" = 1 ]; then
    METERED=true
  fi
fi
mkdir -p "$TMUX_ROOT"
chmod 700 "$TMUX_ROOT"
EVENTS="$TMUX_ROOT/$SLOT-launch.jsonl"

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

if [ "$ACTION" != "freeze" ]; then
  [ -n "$REPOSITORY" ] && [ -n "$PROMPT_SOURCE" ] || {
    echo "tmux-lane: $ACTION requires --repo and --prompt" >&2
    exit 64
  }
  [ -f "$PROMPT_SOURCE" ] && [ ! -L "$PROMPT_SOURCE" ] || {
    echo "tmux-lane: prompt must be a regular non-symlink file" >&2
    exit 70
  }
  [ ! -s "$EVENTS" ] || refuse 64 "slot $SLOT was already launched in this run" \
    "pass --round <tag> to launch a new round of $LANE"

  # Caps, in one place. A metered agent needs a per-round spend cap AND the rate it bills at:
  # no agent we run exposes live spend, so the watchdog enforces the cap as wall-clock time
  # (cap / rate) and reports spend as rate x active time. A qualification probe is always
  # wall-capped. Each field prints on its own line; an error prints the refusal.
  CAPS=$("$FACTORY_PYTHON" - "$METERED" "$ACTION" "$SPEND_CAP" "$RATE" "$WALL_CAP_MINUTES" \
    "$READ_ALLOWANCE_MINUTES" "$STALL_WINDOW_MINUTES" "$FIRST_COMMIT_MINUTES" "$MIN_COMMITS" \
    <<'PY'
import decimal, sys

metered, action, cap, rate, wall, allowance, window, first, minimum = sys.argv[1:]

def amount(text, label):
    try:
        value = decimal.Decimal(text)
    except decimal.InvalidOperation:
        raise SystemExit(f"{label} is not a number: {text}")
    if not value.is_finite() or value <= 0:
        raise SystemExit(f"{label} must be positive: {text}")
    return value

if metered == "true" and (not cap or not rate):
    raise SystemExit(
        "METERED: this agent bills per use and needs an explicit per-round spend cap and the "
        "rate it bills at"
    )
spend_cap = amount(cap, "--spend-cap-usd") if cap else None
per_hour = amount(rate, "--usd-per-hour") if rate else None
caps_s = []
if wall:
    caps_s.append(amount(wall, "--wall-cap-minutes") * 60)
if spend_cap is not None and per_hour is not None:
    caps_s.append(spend_cap / per_hour * 3600)
if action == "qualify" and not caps_s:
    caps_s.append(decimal.Decimal(30 * 60))
wall_cap = str(int(min(caps_s))) if caps_s else ""
microusd = ""
if spend_cap is not None:
    scaled = spend_cap * 1_000_000
    if scaled != scaled.to_integral_value():
        raise SystemExit("--spend-cap-usd must be exact to the microdollar")
    microusd = str(int(scaled))
read_s = int(amount(allowance, "--read-allowance-minutes") * 60)
window_s = int(amount(window, "--stall-window-minutes") * 60)
first_s = int(amount(first, "--first-commit-minutes") * 60) if first else read_s
if not minimum.isdigit() or int(minimum) < 1:
    raise SystemExit("--min-commits must be a positive integer")
fields = [
    "" if spend_cap is None else str(spend_cap), "" if per_hour is None else str(per_hour),
    wall_cap, microusd, str(read_s), str(window_s), str(first_s), minimum,
]
sys.stdout.write("".join(field + "\n" for field in fields))
PY
  ) || {
    case "$METERED" in
      true) refuse 64 "$AGENT${MODEL:+:$MODEL} is metered; refusing to launch without a spend cap" \
        "pass --spend-cap-usd <usd for this round> --usd-per-hour <the rate it bills at> (and optionally --wall-cap-minutes)" ;;
      *) refuse 64 "lane caps are malformed" ;;
    esac
  }
  { IFS= read -r SPEND_CAP && IFS= read -r RATE && IFS= read -r WALL_CAP_S &&
    IFS= read -r CAP_MICROUSD && IFS= read -r READ_ALLOWANCE_S &&
    IFS= read -r STALL_WINDOW_S && IFS= read -r FIRST_COMMIT_S &&
    IFS= read -r MIN_COMMITS; } <<< "$CAPS" || refuse 70 "lane caps could not be read back"

  REPOSITORY_RECEIPT=$(PYTHONPATH="$REPO_ROOT" "$FACTORY_PYTHON" \
    "$REPO_ROOT/harness/lane_repository.py" validate --source "$REPOSITORY") || \
    refuse 70 "the lane repository is not a standalone clone" \
      "give each lane and each parallel instance its own clone (git clone <source> <dir>), never a git worktree: the Codex sandbox blocks writes to the parent repository's git directory"
  REPOSITORY=$(printf '%s' "$REPOSITORY_RECEIPT" | "$FACTORY_PYTHON" -c \
    'import json,sys; print(json.load(sys.stdin)["root"])')
  "$FACTORY_PYTHON" - "$ROOT" "$REPOSITORY" <<'PY'
import os, pathlib, sys

control = pathlib.Path(sys.argv[1]).resolve(strict=True)
lane = pathlib.Path(sys.argv[2]).resolve(strict=True)
if os.path.commonpath((control, lane)) in {str(control), str(lane)}:
    raise SystemExit("tmux-lane: lane repository and control root may not overlap")
PY
  # Each lane works in its own copy of the target, so its files and its .kin are its own. A
  # repository shared with (or nested in) another lane's would be a channel between them; a new
  # round of the same lane may reuse its own clone.
  "$FACTORY_PYTHON" - "$TMUX_ROOT" "$REPOSITORY" "$LANE" <<'PY'
import json, os, pathlib, sys

root, mine, lane = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]).resolve(), sys.argv[3]
for journal in sorted(root.glob("*-launch.jsonl")):
    for line in journal.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        theirs, other = row.get("repository"), row.get("lane") or row.get("role")
        if row.get("status") != "active" or not theirs or other == lane:
            continue
        theirs = pathlib.Path(theirs).resolve()
        if os.path.commonpath((mine, theirs)) in {str(mine), str(theirs)}:
            raise SystemExit(
                f"tmux-lane: the {other} lane already works in {theirs}; give each lane its own copy"
            )
PY
  # Shared means shared: every lane starts from the same seed, or the later one does not start.
  OTHER_ROLE=$([ "$ROLE" = coder ] && echo tester || echo coder)
  SEED_DIGEST=$("$FACTORY_PYTHON" - "$TMUX_ROOT" "$ROOT/lane-seed.jsonl" "$OTHER_ROLE" <<'PY'
import hashlib, json, pathlib, sys

root, seed, other = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
mine = "sha256:" + hashlib.sha256(seed.read_bytes()).hexdigest() if seed.is_file() else ""
active = []
for journal in sorted(root.glob("*-launch.jsonl")):
    for line in journal.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("status") == "active" and row.get("role") == other:
            active.append(row)
if active:
    latest = max(active, key=lambda row: str(row.get("ts", "")))
    theirs = ((latest.get("kindex") or {}).get("seed") or {}).get("digest") or ""
    if theirs != mine:
        raise SystemExit(
            f"tmux-lane: the {other} lane launched with a different shared seed; both lanes "
            "must start from the same lane-seed.jsonl (relaunch both, or restore it)"
        )
print(mine)
PY
  ) || exit 70

  # A tmux window per lane: a new round replaces a finished one, never a running one.
  WINDOWS=$(tmux list-windows -t "$RUN" -F '#{window_name} #{pane_dead}' 2>/dev/null || true)
  if printf '%s\n' "$WINDOWS" | grep -qx "$LANE 0"; then
    refuse 70 "lane $LANE is still running in $RUN:$LANE" \
      "wait for its watcher to report it done, or stop it, before launching another round"
  fi

  SAFE_HOME="${HOME:?tmux-lane: HOME is required for agent authentication}"
  SAFE_USER="${USER:-$(id -un)}"
  SAFE_PATH="${PATH:?tmux-lane: PATH is required}"
  SAFE_TMPDIR="${TMPDIR:-/tmp}"
  SAFE_TERM="${TERM:-xterm-256color}"
  SAFE_SHELL="${SHELL:-/bin/bash}"
  SAFE_LANG="${LANG:-en_US.UTF-8}"
  SAFE_CODEX_HOME="${CODEX_HOME:-$SAFE_HOME/.codex}"
  LANE_ENV=(env -i HOME="$SAFE_HOME" USER="$SAFE_USER" PATH="$SAFE_PATH" TMPDIR="$SAFE_TMPDIR"
    TERM="$SAFE_TERM" SHELL="$SAFE_SHELL" LANG="$SAFE_LANG" CODEX_HOME="$SAFE_CODEX_HOME")

  # Sign-in, checked the way the lane will run: the same scrubbed environment, stdin closed,
  # bounded time. An agent that is signed out, or whose check waits for a person, would sit on
  # a login screen; it is refused here with the exact command that fixes it.
  set +e
  AUTH=$("${LANE_ENV[@]}" "$FACTORY_PYTHON" "$D/lane_agent.py" preflight --agent "$AGENT" 2>&1)
  AUTH_RC=$?
  set -e
  if [ "$AUTH_RC" -ne 0 ]; then
    REMEDIATION=$(printf '%s\n' "$AUTH" | sed -n 's/^lane-agent: remediation: //p' | head -1)
    REASON=$(printf '%s\n' "$AUTH" | grep -v '^lane-agent: remediation: ' | head -1 | sed 's/^lane-agent: //')
    refuse 77 "AUTH: $AGENT cannot start non-interactively: $REASON" "$REMEDIATION"
  fi

  # A metered model takes a lane only after it passed a qualification probe for this role.
  QUALIFICATIONS="$FACTORY_RUNS_ROOT/lane-qualifications.jsonl"
  if [ "$ACTION" = launch ] && [ "$METERED" = true ]; then
    "$FACTORY_PYTHON" - "$QUALIFICATIONS" "$ROLE" "$AGENT" "$MODEL" <<'PY' || \
      refuse 77 "UNQUALIFIED: $AGENT:$MODEL has no passing $ROLE qualification probe" \
        "harness/tmux_lane.sh $RUN $ROLE qualify --repo <a clone> --prompt <a 3-5 item brief> --agent $AGENT${MODEL:+ --model $MODEL} --spend-cap-usd <usd> --usd-per-hour <usd>"
import json, pathlib, sys

path, role, agent, model = pathlib.Path(sys.argv[1]), *sys.argv[2:]
rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()] if path.is_file() and not path.is_symlink() else []
passed = [row for row in rows if (row.get("role"), row.get("agent"), row.get("model"),
          row.get("verdict")) == (role, agent, model, "pass")]
raise SystemExit(0 if passed else 1)
PY
  fi

  # The agent's CLI contract, before anything is written.
  case "$AGENT" in
    codex|codex-ollama)
      AGENT_VERSION=$(codex --version 2>/dev/null) || refuse 70 "codex CLI is not runnable"
      CODEX_EXEC_HELP=$(codex exec --help 2>/dev/null) || exit 70
      CODEX_QUEUE_HELP=$(codex queue --help 2>/dev/null) || exit 70
      for REQUIRED in --ignore-user-config --ignore-rules --strict-config --json; do
        printf '%s' "$CODEX_EXEC_HELP" | grep -q -- "$REQUIRED" || \
          refuse 70 "codex CLI contract lacks $REQUIRED ($AGENT_VERSION)"
      done
      printf '%s' "$CODEX_QUEUE_HELP" | grep -q -- '--thread' || \
        refuse 70 "codex CLI contract lacks queue --thread ($AGENT_VERSION)"
      CLI_CONTRACT="codex-exec-json-plus-queue-resume-v1" ;;
    codex-interactive)
      AGENT_VERSION=$(codex --version 2>/dev/null) || refuse 70 "codex CLI is not runnable"
      CODEX_HELP=$(codex --help 2>/dev/null) || exit 70
      # --no-daemon keeps the TUI off the app-server daemon whose socket failure sent msg-r2's
      # Coder to a login screen.
      for REQUIRED in --no-daemon --no-alt-screen --strict-config; do
        printf '%s' "$CODEX_HELP" | grep -q -- "$REQUIRED" || \
          refuse 70 "codex CLI contract lacks $REQUIRED ($AGENT_VERSION)"
      done
      CLI_CONTRACT="codex-tui-no-daemon-v1" ;;
    cursor-agent)
      AGENT_VERSION=$(agent --version 2>/dev/null) || refuse 70 "cursor-agent CLI (agent) is not runnable"
      AGENT_HELP=$(agent --help 2>/dev/null) || exit 70
      for REQUIRED in --print --model --force --trust --workspace; do
        printf '%s' "$AGENT_HELP" | grep -q -- "$REQUIRED" || \
          refuse 70 "cursor-agent CLI contract lacks $REQUIRED ($AGENT_VERSION)"
      done
      CLI_CONTRACT="cursor-agent-print-v1" ;;
  esac

  # Every Tester prompt opens with the founder's testing standard, the Tester doctrine and the
  # run's testing strategy, as bytes with digests. Without them the Tester does not start.
  STANDARD_RECEIPT='null'
  STANDARD_BLOCK=""
  if [ "$ROLE" = tester ]; then
    STANDARD_BLOCK="$TMUX_ROOT/$SLOT-testing-standard.md"
    STRATEGY_ARGS=(--strategy "$ROOT/artifacts/testing-strategy.md")
    STANDARD_RECEIPT=$("$FACTORY_PYTHON" "$D/testing_standard.py" block \
      --factory-home "$REPO_ROOT" ${STRATEGY_ARGS[@]+"${STRATEGY_ARGS[@]}"} --output "$STANDARD_BLOCK" 2>&1) || \
      refuse 70 "TESTING STANDARD: $STANDARD_RECEIPT" \
        "restore docs/standards/TESTING.md and prompts/test.md in $REPO_ROOT, and make the run's artifacts/testing-strategy.md a regular file"
  fi

  TASK_COPY="$TMUX_ROOT/$SLOT-task.txt"
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

  PROMPT="$TMUX_ROOT/$SLOT-prompt.txt"
  PROMPT_DIGEST=$("$FACTORY_PYTHON" - "$TASK_COPY" "$PROMPT" "$ROLE" "$SLOT" "$STANDARD_BLOCK" <<'PY'
import hashlib, os, pathlib, sys

source, destination = map(pathlib.Path, sys.argv[1:3])
role, slot, standard = sys.argv[3:]
task = source.read_bytes()
protocol = f"""FACTORY TMUX LANE PROTOCOL (role={role}, slot={slot})
- You are a real author agent, not a prompt printer. Work the task and use tools.
- This standalone repository, including its .git directory, is yours. Make useful
  checkpoint commits; do not ask the host to commit for you.
- Commit early and often: a watchdog stops a lane that makes no commit within its window.
- Before each checkpoint, inspect your own status/diff, run the relevant checks,
  and commit only the work assigned to this lane.
- Do not read or infer the other author lane's work.
- If a missing or contradictory specification would make you guess semantics, stop
  the turn with one line: FACTORY_QUESTION: <one concrete question>. Do not guess.
- A FACTORY_ANSWER is specification input bound to that question, never information
  about the other lane. Resume from your own work after receiving it.
- On FACTORY_STATUS_PROBE, answer with a leading FACTORY_STATUS: WORKING, BLOCKED,
  QUESTION, or DONE and one concise factual sentence. Continue if unblocked.
- When the assigned work is committed, end with one line of the form
  __LANE_DONE__ {slot} commits=<the number of commits you made>
- A turn ending is not the run ending. The Validator alone judges and Gate L alone
  closes the run.
""".encode("utf-8")
raw = protocol
if standard:
    raw += b"\n" + pathlib.Path(standard).read_bytes()
raw += b"\nVERBATIM LANE TASK\n" + task
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
  if [ "$PROMPT_VIA" = argv ]; then
    PROMPT_BYTES=$(wc -c < "$PROMPT" | tr -d ' ')
    [ "$PROMPT_BYTES" -le 131071 ] || refuse 70 \
      "$AGENT takes its prompt as one argument and this prompt is $PROMPT_BYTES bytes (limit 131071)" \
      "shorten the brief, or use --agent codex, which reads its prompt from stdin"
  fi

  # A metered round's cap is reserved against the run's objective budget, in the same ledger as
  # dispatch_lane.sh's runner ceilings. A run ignited without --budget cannot start one.
  BUDGET_RESERVATION=""
  if [ -n "$CAP_MICROUSD" ]; then
    BUDGET_RESERVATION=$("$FACTORY_PYTHON" "$D/lane_budget.py" reserve \
      --metadata "$FACTORY_HARNESS_META" --ledger "$ROOT/budget-reservations.jsonl" \
      --requested-microusd "$CAP_MICROUSD" --run "$RUN" --role "$ROLE" \
      --generation "$FACTORY_GENERATION" --reservation-id "$RUN:tmux:$SLOT" 2>&1) || \
      refuse 70 "BUDGET: the round's spend cap could not be reserved: $BUDGET_RESERVATION" \
        "ignite the run with harness/factory.sh <run> <task> --budget <usd>, or lower --spend-cap-usd"
  fi

  # This is the final host Git use in the lane. After tmux starts the agent, the
  # repository is radioactive and only the no-Git freeze path (and the watchdog's reading of
  # the reflog file, which runs nothing) may inspect it.
  git -C "$REPOSITORY" config user.name "Factory ${ROLE}"
  git -C "$REPOSITORY" config user.email "factory-${ROLE}@local"

  # Kindex scoped to this lane's own copy of the target's .kin (harness/lane_kindex.py).
  SEED_ARGS=()
  [ ! -e "$ROOT/lane-seed.jsonl" ] || SEED_ARGS=(--seed "$ROOT/lane-seed.jsonl")
  KINDEX=$("$FACTORY_PYTHON" "$D/lane_kindex.py" prepare --repo "$REPOSITORY" \
    --home "$TMUX_ROOT/$SLOT-kindex-home" ${SEED_ARGS[@]+"${SEED_ARGS[@]}"}) || exit 70
  # The seed may not move between the check above and the load: the store must hold what was checked.
  printf '%s' "$KINDEX" | "$FACTORY_PYTHON" -c '
import json, sys
seed = json.load(sys.stdin).get("seed") or {}
if (seed.get("digest") or "") != sys.argv[1]:
    raise SystemExit("tmux-lane: lane-seed.jsonl changed during launch; relaunch")
' "$SEED_DIGEST" || exit 70
  KINDEX_OVERRIDE=$(printf '%s' "$KINDEX" | "$FACTORY_PYTHON" -c \
    'import json,sys; print(json.load(sys.stdin)["mcp_override"])')

  PERMISSION_PROFILE='permissions.factory-lane={extends=":workspace",filesystem={":workspace_roots"={".git"="write"}}}'
  SHELL_POLICY='shell_environment_policy={inherit="core",ignore_default_excludes=false}'
  PROFILE_DIGEST=$(printf '%s\n%s' "$PERMISSION_PROFILE" "$SHELL_POLICY" | shasum -a 256 | cut -d' ' -f1)
  LANE_LOG="$TMUX_ROOT/$SLOT-lane.log"
  LANE_EXITS="$TMUX_ROOT/$SLOT-exits.jsonl"
  PLANNED=$("$FACTORY_PYTHON" - "$RUN" "$ROLE" "$AGENT" "$REPOSITORY" \
    "$PROMPT" "$PROMPT_DIGEST" "$TASK_DIGEST" "$PROFILE_DIGEST" \
    "$REPOSITORY_RECEIPT" "$AGENT_VERSION" "$MODEL" "$KINDEX" \
    "$LANE" "$SLOT" "$INSTANCE" "$ROUND" "$ACTION" "$METERED" "$PROMPT_VIA" "$AGENT_TTY" \
    "$CLI_CONTRACT" "$AUTH" "$SPEND_CAP" "$RATE" "$WALL_CAP_S" "$READ_ALLOWANCE_S" \
    "$STALL_WINDOW_S" "$FIRST_COMMIT_S" "$MIN_COMMITS" "$BUDGET_RESERVATION" \
    "$STANDARD_RECEIPT" "$LANE_LOG" "$LANE_EXITS" "$QUALIFICATIONS" <<'PY'
import datetime, json, sys
(
    run, role, agent, repository, prompt, prompt_digest, task_digest,
    profile_digest, preflight, agent_version, model, kindex,
    lane, slot, instance, round_tag, action, metered, prompt_via, tty,
    cli_contract, auth, spend_cap, rate, wall_cap, read_allowance,
    stall_window, first_commit, min_commits, reservation,
    standard, lane_log, lane_exits, qualifications,
) = sys.argv[1:]
print(json.dumps({
    "schema_version": "factory-tmux-lane-launch/1",
    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "status": "planned",
    "run_id": run,
    "role": role,
    "lane": lane,
    "slot": slot,
    "instance": instance or None,
    "round": round_tag or None,
    "mode": action,
    "agent": agent,
    "model": model,
    "terminal": tty == "terminal",
    "prompt_via": prompt_via,
    "auth_preflight": auth.strip(),
    "metered": metered == "true",
    "caps": {
        "spend_cap_usd": spend_cap or None,
        "usd_per_hour": rate or None,
        "wall_cap_s": int(wall_cap) if wall_cap else None,
        "budget_reservation": reservation or None,
        "enforcement": "wall-clock-proxy" if wall_cap else "none",
    },
    "watch": {
        "read_allowance_s": int(read_allowance),
        "stall_window_s": int(stall_window),
        "poll_s": 30,
    },
    "qualify": (
        {"first_commit_s": int(first_commit), "min_commits": int(min_commits),
         "ledger": qualifications}
        if action == "qualify" else None
    ),
    "testing_standard": json.loads(standard),
    "lane_log": lane_log,
    "lane_exits": lane_exits,
    "tmux_target": f"{run}:{lane}",
    "watch_target": f"{run}:watch-{lane}",
    "kindex": json.loads(kindex),
    "repository": repository,
    "prompt_path": prompt,
    "prompt_digest": prompt_digest,
    "verbatim_task_digest": task_digest,
    "permission_profile_digest": "sha256:" + profile_digest,
    "agent_version": agent_version,
    "cli_contract": cli_contract,
    "repository_preflight": json.loads(preflight),
    "boundary": "operator-owned-tmux-unqualified",
    "host_git_after_agent_start": "forbidden",
}, sort_keys=True, separators=(",", ":")))
PY
  )
  append_event "$PLANNED"

  LOCAL_ARGS=""
  if [ "$AGENT" = "codex-ollama" ]; then
    LOCAL_ARGS="--oss --local-provider ollama -m $MODEL"
  elif [ -n "$MODEL" ] && [ "$AGENT" != "cursor-agent" ]; then
    # A profile's model. Lanes run with --ignore-user-config, so without it Codex would fall
    # back to its built-in default: a model nobody named.
    LOCAL_ARGS="-m $MODEL"
  fi
  THREAD_FILE="$TMUX_ROOT/$SLOT-thread-id"
  CODEX_EVENTS="$TMUX_ROOT/$SLOT-codex-events.jsonl"
  printf -v ENV_PREFIX 'exec env -i HOME=%q USER=%q PATH=%q TMPDIR=%q TERM=%q SHELL=%q LANG=%q CODEX_HOME=%q FACTORY_RUNS_DIR=%q HARNESS_RUN_ROOT=%q' \
    "$SAFE_HOME" "$SAFE_USER" "$SAFE_PATH" "$SAFE_TMPDIR" "$SAFE_TERM" "$SAFE_SHELL" "$SAFE_LANG" "$SAFE_CODEX_HOME" \
    "$FACTORY_RUNS_ROOT" "$ROOT"
  # Every lane process runs under lane_agent.py run: it tees output to the lane log and records
  # the process's end in the exits journal the watcher reads.
  printf -v WRAP '%q %q run --slot %q --log %q --exits %q' \
    "$FACTORY_PYTHON" "$D/lane_agent.py" "$SLOT" "$LANE_LOG" "$LANE_EXITS"
  case "$AGENT" in
    codex|codex-ollama)
      printf -v LANE_CMD '%s %s -- %q %q --prompt %q --thread-file %q --events %q --root %q --role %q -- codex --ask-for-approval never exec %s --ignore-user-config --ignore-rules --strict-config --json -C %q -c %q -c %q -c %q -c %q -' \
        "$ENV_PREFIX" "$WRAP" "$FACTORY_PYTHON" "$D/codex_lane_session.py" \
        "$PROMPT" "$THREAD_FILE" "$CODEX_EVENTS" "$ROOT" "$ROLE" "$LOCAL_ARGS" "$REPOSITORY" \
        'default_permissions="factory-lane"' "$PERMISSION_PROFILE" "$SHELL_POLICY" "$KINDEX_OVERRIDE" ;;
    codex-interactive)
      printf -v LANE_CMD '%s %s --terminal --prompt-arg %q -- codex --no-daemon --no-alt-screen --strict-config --ask-for-approval never %s -C %q -c %q -c %q -c %q -c %q' \
        "$ENV_PREFIX" "$WRAP" "$PROMPT" "$LOCAL_ARGS" "$REPOSITORY" \
        'default_permissions="factory-lane"' "$PERMISSION_PROFILE" "$SHELL_POLICY" "$KINDEX_OVERRIDE" ;;
    cursor-agent)
      printf -v LANE_CMD '%s %s --prompt-arg %q -- agent -p --model %q --force --trust --workspace %q' \
        "$ENV_PREFIX" "$WRAP" "$PROMPT" "$MODEL" "$REPOSITORY" ;;
  esac
  printf -v WATCH_CMD '%s %q %q lane --root %q --slot %q' \
    "$ENV_PREFIX" "$FACTORY_PYTHON" "$D/lane_watchdog.py" "$ROOT" "$SLOT"

  mark() {
    "$FACTORY_PYTHON" - "$PLANNED" "$1" <<'PY'
import datetime, json, sys
row = json.loads(sys.argv[1]); row["status"] = sys.argv[2]
row["ts"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
print(json.dumps(row, sort_keys=True, separators=(",", ":")))
PY
  }
  tmux set-option -t "$RUN" remain-on-exit on >/dev/null
  # A finished round's window (and its watcher's) is replaced; a running one was refused above.
  if printf '%s\n' "$WINDOWS" | grep -qx "$LANE 1"; then
    tmux kill-window -t "$RUN:$LANE" >/dev/null 2>&1 || true
  fi
  if printf '%s\n' "$WINDOWS" | grep -q "^watch-$LANE "; then
    tmux kill-window -t "$RUN:watch-$LANE" >/dev/null 2>&1 || true
  fi
  if ! tmux new-window -t "$RUN" -n "$LANE" -c "$REPOSITORY" "$LANE_CMD"; then
    append_event "$(mark launch-failed)"
    refuse 70 "failed to launch $RUN:$LANE"
  fi
  if ! tmux new-window -d -t "$RUN" -n "watch-$LANE" -c "$ROOT" "$WATCH_CMD"; then
    tmux kill-window -t "$RUN:$LANE" >/dev/null 2>&1 || true
    append_event "$(mark launch-failed)"
    refuse 70 "the watcher for $RUN:$LANE could not start, so the lane was stopped: a lane with no watcher is not launched"
  fi
  append_event "$(mark active)"
  echo "tmux-lane: launched $AGENT${MODEL:+ ($MODEL)} in $RUN:$LANE as $SLOT; watcher in $RUN:watch-$LANE"
  [ -z "$WALL_CAP_S" ] || echo "tmux-lane: capped at $((WALL_CAP_S / 60)) min wall clock${SPEND_CAP:+ (\$$SPEND_CAP at \$$RATE/h)}"
  echo "tmux-lane: wakes land in $TMUX_ROOT/wake.jsonl and $ROOT/events.jsonl"
  echo "tmux-lane: the entire repository is now agent-owned; do not run host Git there"
  exit 0
fi

[ -z "$REPOSITORY" ] && [ -z "$PROMPT_SOURCE" ] || {
  echo "tmux-lane: freeze reads the retained launch receipt; --repo/--prompt are refused" >&2
  exit 64
}
[ -s "$EVENTS" ] && [ ! -L "$EVENTS" ] || {
  echo "tmux-lane: no retained launch record for $SLOT" >&2
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
PANE_DEAD=$(tmux display-message -p -t "$RUN:$LANE" '#{pane_dead}' 2>/dev/null || true)
[ "$PANE_DEAD" = "1" ] || {
  echo "tmux-lane: $RUN:$LANE is not quiescent; stop the agent before freezing" >&2
  exit 70
}

EXPORT=$(PYTHONPATH="$REPO_ROOT" "$FACTORY_PYTHON" "$REPO_ROOT/harness/lane_repository.py" freeze \
  --source "$REPOSITORY" --store "$TMUX_ROOT/snapshots" --durable-through "$ROOT" \
  --projection-conf "${HARNESS_PROJECTION_CONF:-$FACTORY_WORKDIR/.factory/projection.conf}") || exit $?
FROZEN=$("$FACTORY_PYTHON" - "$RUN" "$ROLE" "$EXPORT" "$SLOT" <<'PY'
import datetime, json, sys
run, role, export, slot = sys.argv[1:]
print(json.dumps({
    "schema_version": "factory-tmux-lane-freeze/1",
    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "run_id": run,
    "role": role,
    "slot": slot,
    "pane_dead": True,
    "export": json.loads(export),
    "boundary": "regular-files-only-no-git",
}, sort_keys=True, separators=(",", ":")))
PY
)
append_event "$FROZEN"
printf '%s\n' "$EXPORT"
