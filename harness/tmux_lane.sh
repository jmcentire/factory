#!/usr/bin/env bash
# Launch a resident author with the assigned model and independent input tree.
# Authentication, native session identity, input separation and explicit spend caps are
# real boundaries. Model approval, signatures and review-backlog holds are not prerequisites.
set -euo pipefail
# shellcheck source=harness/factory_python.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/factory_python.sh"

USAGE='usage: tmux_lane.sh <run> <coder|tester> <launch|qualify|freeze|retire-probe> [options]
  launch|qualify  --repo <the lane'"'"'s own clone> --prompt <brief file>
                  [--agent codex|codex-interactive|codex-ollama|cursor-agent|claude|claude-ollama|agy|muse|prime-agent] [--model <model>]
                  [--effort <low|medium|high|xhigh|max>]
                  [--persistent (resident native session; always on for Claude, which runs
                   as an interactive Claude Code session and never in print mode, and for
                   codex, the default, which is Codex'"'"'s own interactive session: the
                   lane agent codex-interactive, never codex exec)]
                  [--metered] [--spend-cap-usd <usd> --usd-per-hour <usd>]
                  [--wall-cap-minutes <min, metered lanes and qualify only>]
                  [--round <tag> --replace-session <why the session cannot resume>]
                  [--read-allowance-minutes <min, default 15>] [--stall-window-minutes <min, default 20>]
                  [--wrap-up-minutes <min, default 5; 0 = none>]
                  [--review-every-minutes <min, default off; 0 = none>]
                  [--cache-env <NAME=read-only dir>]... [--runtime-dir <read-only dir>]...
                  [--boundary-contract <host JSON> --boundary-digest <sha256>]
                  [--signet-contract <host JSON> --signet-digest <sha256>]
  qualify only    [--first-commit-minutes <min, default 15>] [--min-commits <n, default 1>]
  freeze          [--instance <name>] [--round <tag>]
  retire-probe    --round <probe tag> --reason <why evidence cannot be recovered>
  all             [--runs <runs dir>]'
RUN="${1:?$USAGE}"
ROLE="${2:?$USAGE}"
ACTION="${3:?$USAGE}"
shift 3
case "$ROLE" in coder|tester) ;; *) echo "role must be coder|tester" >&2; exit 64 ;; esac
case "$ACTION" in launch|qualify|freeze|retire-probe) ;; *) echo "invalid lane action" >&2; exit 64 ;; esac

RUNS_ARG="${FACTORY_RUNS_DIR:-${HARNESS_DIR:-.factory}/runs}"
REPOSITORY=""
PROMPT_SOURCE=""
AGENT_ARG=""
MODEL_ARG=""
EFFORT=""
PERSISTENT=0
PERSISTENT_ARGS=()
INSTANCE=""
ROUND=""
REPLACE_REASON=""
RETIRE_REASON=""
METERED_ARG=0
SPEND_CAP=""
RATE=""
WALL_CAP_MINUTES=""
READ_ALLOWANCE_MINUTES=15
STALL_WINDOW_MINUTES=20
FIRST_COMMIT_MINUTES=""
MIN_COMMITS=1
WRAP_UP_MINUTES=""
REVIEW_EVERY_MINUTES=""
LANE_ENV_ARGS=()
BOUNDARY_ARG_PATH=""
BOUNDARY_ARG_DIGEST=""
SIGNET_ARG_PATH=""
SIGNET_ARG_DIGEST=""
AUTHOR_CUSTODY_ARG_PATH=""
AUTHOR_CUSTODY_ARG_DIGEST=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --runs) RUNS_ARG="$2"; shift 2 ;;
    --repo) REPOSITORY="$2"; shift 2 ;;
    --prompt) PROMPT_SOURCE="$2"; shift 2 ;;
    --agent) AGENT_ARG="$2"; shift 2 ;;
    --model) MODEL_ARG="$2"; shift 2 ;;
    --effort) EFFORT="$2"; shift 2 ;;
    --persistent) PERSISTENT=1; PERSISTENT_ARGS=(--persistent); shift ;;
    --instance) INSTANCE="$2"; shift 2 ;;
    --round) ROUND="$2"; shift 2 ;;
    --replace-session) REPLACE_REASON="$2"; shift 2 ;;
    --reason) RETIRE_REASON="$2"; shift 2 ;;
    --metered) METERED_ARG=1; shift ;;
    --spend-cap-usd) SPEND_CAP="$2"; shift 2 ;;
    --usd-per-hour) RATE="$2"; shift 2 ;;
    --wall-cap-minutes) WALL_CAP_MINUTES="$2"; shift 2 ;;
    --read-allowance-minutes) READ_ALLOWANCE_MINUTES="$2"; shift 2 ;;
    --stall-window-minutes) STALL_WINDOW_MINUTES="$2"; shift 2 ;;
    --first-commit-minutes) FIRST_COMMIT_MINUTES="$2"; shift 2 ;;
    --min-commits) MIN_COMMITS="$2"; shift 2 ;;
    --wrap-up-minutes) WRAP_UP_MINUTES="$2"; shift 2 ;;
    --review-every-minutes) REVIEW_EVERY_MINUTES="$2"; shift 2 ;;
    --cache-env) LANE_ENV_ARGS+=(--cache-env "$2"); shift 2 ;;
    --runtime-dir) LANE_ENV_ARGS+=(--runtime-dir "$2"); shift 2 ;;
    --boundary-contract) BOUNDARY_ARG_PATH="$2"; shift 2 ;;
    --boundary-digest) BOUNDARY_ARG_DIGEST="$2"; shift 2 ;;
    --signet-contract) SIGNET_ARG_PATH="$2"; shift 2 ;;
    --signet-digest) SIGNET_ARG_DIGEST="$2"; shift 2 ;;
    --author-custody-contract) AUTHOR_CUSTODY_ARG_PATH="$2"; shift 2 ;;
    --author-custody-digest) AUTHOR_CUSTODY_ARG_DIGEST="$2"; shift 2 ;;
    *) echo "tmux-lane: unknown argument: $1" >&2; exit 64 ;;
  esac
done
# Closed host custody operations pass each role's fixed pins without changing the server's
# shared environment. A CLI pin may fill an absent setting, never replace a conflicting one.
for FAMILY in BOUNDARY SIGNET AUTHOR_CUSTODY; do
  PATH_ARG="${FAMILY}_ARG_PATH"; DIGEST_ARG="${FAMILY}_ARG_DIGEST"
  if [ -n "${!PATH_ARG}" ] || [ -n "${!DIGEST_ARG}" ]; then
    [ -n "${!PATH_ARG}" ] && [ -n "${!DIGEST_ARG}" ] || {
      echo "tmux-lane: $FAMILY contract and digest must be supplied together" >&2; exit 64;
    }
    PATH_ENV="FACTORY_${FAMILY}_CONTRACT"; DIGEST_ENV="FACTORY_${FAMILY}_DIGEST"
    [ "$FAMILY" != AUTHOR_CUSTODY ] || PATH_ENV="FACTORY_AUTHOR_CUSTODY_CONFIG"
    for PAIR in PATH DIGEST; do
      ARG_NAME="${PAIR}_ARG"; ENV_NAME="${PAIR}_ENV"
      ARG_NAME="${!ARG_NAME}"; ENV_NAME="${!ENV_NAME}"
      [ -z "${!ENV_NAME:-}" ] || [ "${!ENV_NAME}" = "${!ARG_NAME}" ] || {
        echo "tmux-lane: $FAMILY CLI setting conflicts with host binding" >&2; exit 77;
      }
      printf -v "$ENV_NAME" '%s' "${!ARG_NAME}"
      export "$ENV_NAME"
    done
    export "FACTORY_REQUIRE_${FAMILY}=1"
  fi
done
CHOICE_ARGS=()
[ -z "$AGENT_ARG" ] || CHOICE_ARGS+=(--agent "$AGENT_ARG")
[ -z "$MODEL_ARG" ] || CHOICE_ARGS+=(--model "$MODEL_ARG")
[ -z "$EFFORT" ] || CHOICE_ARGS+=(--effort "$EFFORT")
exec 4< <("$FACTORY_PYTHON" "$(dirname "${BASH_SOURCE[0]}")/model_settings.py" "$ROLE" ${CHOICE_ARGS[@]+"${CHOICE_ARGS[@]}"})
{ IFS= read -r -d '' AGENT <&4 && IFS= read -r -d '' MODEL_ARG <&4 &&
  IFS= read -r -d '' EFFORT <&4; } || exit 64
exec 4<&-
# An author lane on Codex is Codex's own interactive session, hosted by the lane wrapper: the
# adapter the lane agent `codex-interactive` names. `codex`, the default, is that adapter, and
# everything from here on, the launch record included, says `codex-interactive`. No author
# lane runs `codex exec` for it, and nothing falls back to it. `codex-ollama` is a different
# lane agent and is unchanged: Codex on a local Ollama model has not been run interactively.
[ "$AGENT" != codex ] || AGENT=codex-interactive
# Claude Code is a resident native terminal session for every action: it has no print mode here.
# So is Cursor's agent: its interactive program, never `-p`. So is Muse Code: its interactive
# session, never `muse exec`. So is Antigravity: its interactive session, never `--print`.
# So is Codex (`codex`, which is the lane agent codex-interactive): its interactive session,
# hosted by the wrapper, never `codex exec`.
case "$ACTION:$AGENT" in
  launch:claude|qualify:claude|launch:claude-ollama|qualify:claude-ollama|\
  launch:cursor-agent|qualify:cursor-agent|launch:muse|qualify:muse|launch:agy|qualify:agy|\
  launch:codex-interactive|qualify:codex-interactive)
    PERSISTENT=1; PERSISTENT_ARGS=(--persistent) ;;
esac
case "$AGENT" in codex|codex-interactive|codex-ollama|cursor-agent|claude|claude-ollama|agy|muse|prime-agent) ;;
  *) echo "tmux-lane: agent must be codex|codex-interactive|codex-ollama|cursor-agent|claude|claude-ollama|agy|muse|prime-agent" >&2; exit 64 ;;
esac
if [ "$PERSISTENT" = 1 ]; then
  case "$ACTION:$AGENT" in
    launch:claude|qualify:claude|launch:claude-ollama|qualify:claude-ollama|\
    launch:cursor-agent|qualify:cursor-agent|launch:muse|qualify:muse|launch:agy|qualify:agy|\
    launch:codex-interactive|qualify:codex-interactive) ;;
    *) echo "tmux-lane: --persistent requires --agent claude, claude-ollama, cursor-agent, muse, agy or codex-interactive" >&2; exit 64 ;;
  esac
fi
case "$EFFORT" in ""|minimal|low|medium|high|xhigh|max) ;;
  *) echo "tmux-lane: --effort must be minimal|low|medium|high|xhigh|max" >&2; exit 64 ;;
esac
# cursor-agent names the effort in the model (grok-4.7-xhigh); codex-ollama's is the model's own.
case "$AGENT" in cursor-agent|codex-ollama)
  [ -z "$EFFORT" ] || { echo "tmux-lane: $AGENT takes its effort from the model name or config, not --effort" >&2; exit 64; } ;;
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
# tmux resolves a window name it cannot find to the session's current window, even with an
# exact-match "=" prefix, so acting on "$RUN:<name>" for a vanished lane hits another lane.
# Resolve the exact name to its unique window id and act only on that id.
window_id() {
  tmux list-windows -t "=$RUN" -F '#{window_name} #{window_id}' 2>/dev/null |
    awk -v name="$1" '$1 == name { print $2; exit }'
}

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

# The operator names the lane's agent (--agent, default codex) and, optionally, its model
# (--model). The factory names no model. MODEL_SOURCE records where the model came from:
# "operator" when named, "codex-config" when a codex lane carries over the model the operator's
# own Codex config selects, "agy-config" for Agy's selected model, and "agent" when the
# agent's CLI chooses for itself. codex-ollama alone needs a named model: Ollama must already
# have it, and Codex's --oss mode would otherwise pick and pull one.
MODEL="$MODEL_ARG"
MODEL_SOURCE=""
MODEL_CONFIG=()
[ -z "$MODEL" ] || MODEL_SOURCE=operator
if [ "$ACTION" != "freeze" ] && [ "$AGENT" = "codex-ollama" ]; then
  [ -z "$MODEL" ] || [ -z "${FACTORY_LANE_OLLAMA_MODEL:-}" ] || \
    [ "$MODEL" = "$FACTORY_LANE_OLLAMA_MODEL" ] || {
    echo "tmux-lane: --model $MODEL contradicts FACTORY_LANE_OLLAMA_MODEL=$FACTORY_LANE_OLLAMA_MODEL" >&2
    exit 64
  }
  MODEL="${MODEL:-${FACTORY_LANE_OLLAMA_MODEL:-}}"
  factory_require_ollama_model tmux-lane FACTORY_LANE_OLLAMA_MODEL "$MODEL" || exit $?
  MODEL_SOURCE=operator
fi
if [ "$ACTION" != "freeze" ]; then
  exec 4< <("$FACTORY_PYTHON" "$D/lane_agent.py" describe --agent "$AGENT")
  { IFS= read -r -d '' AGENT_EXECUTABLE <&4 && IFS= read -r -d '' PROMPT_VIA <&4 &&
    IFS= read -r -d '' AGENT_TTY <&4 && IFS= read -r -d '' AGENT_METERED <&4; } || exit 70
  exec 4<&-
  # Agy's resident input is a stream on stdin; Claude Code's is typed into its own terminal.
  [ "$PERSISTENT" != 1 ] || [ "$PROMPT_VIA" = terminal ] || PROMPT_VIA=stdin
  METERED=false
  if [ "$AGENT_METERED" = metered ] || [ "$METERED_ARG" = 1 ]; then
    METERED=true
  fi
  # A lane whose session a lane message can resume: a Codex exec thread, or a registry harness's
  # session (harness/agents.py), which the lane wrapper records the moment the harness streams it.
  case "$AGENT" in
    codex|codex-ollama|codex-interactive) AGENT_RESUMABLE=1 ;;
    *) DESCRIBED=$("$FACTORY_PYTHON" "$D/agents.py" describe --agent "$AGENT" | tr '\0' ' ')
       case "$DESCRIBED" in resumable*) AGENT_RESUMABLE=1 ;; *) AGENT_RESUMABLE=0 ;; esac
       case "$DESCRIBED" in *metered-only*) METERED=true ;; esac
       if [ "$METERED" = true ]; then
         # A harness with no metered launch says so here, before anything is prepared for it.
         UNMETERED=$("$FACTORY_PYTHON" "$D/agents.py" credential-env --agent "$AGENT" --billing metered 2>&1 >/dev/null) || \
           refuse 64 "${UNMETERED#agents: }" "launch the lane without --metered"
       fi ;;
  esac
  # Whether this lane's memory is its own, and what (if anything) keeps this provider from
  # running as its own interactive program with the operator's configuration: both come from
  # the registry, are recorded in the launch and are said aloud when the lane starts.
  # Asked by the lane agent's own name: `codex` (exec) and `codex-interactive` share a
  # registry entry but not a blocker.
  exec 4< <("$FACTORY_PYTHON" "$D/agents.py" describe --agent "$AGENT")
  { IFS= read -r -d '' _ <&4 && IFS= read -r -d '' _ <&4 &&
    IFS= read -r -d '' MEMORY_ISOLATION <&4 && IFS= read -r -d '' NATIVE_BLOCKER <&4 &&
    IFS= read -r -d '' START_REQUIRES <&4 && IFS= read -r -d '' PROTOCOL_NOTE <&4; } || exit 70
  exec 4<&-
  MEMORY_ISOLATION=${MEMORY_ISOLATION#memory:}
  if [ "$AGENT" = claude-ollama ] && [ -z "$MODEL" ]; then
    echo "tmux-lane: claude-ollama needs --model (an Ollama model, e.g. glm-5.3:cloud)" >&2; exit 64
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
    "send new work into its session: harness/tmux_lane_message.sh $RUN validator $LANE review|answer ..."

  # An earlier round of this lane may still be stopping although its window is gone (a window
  # that was closed, a tmux server that ended): its wrapper waits for its program and cleans
  # up last. A new round started in that time would run a second program in the lane's clone
  # beside the first, and this launcher would clear the earlier round's project files and
  # empty its cleanup manifest while that round's program is still using them. So "a new
  # round replaces a finished one, never a running one" is decided by the earlier rounds' own
  # processes, not only by their window: each earlier slot of this lane is waited for, by the
  # exact wrapper and program its receipt names (PID and start time) and for a bounded time,
  # before anything of theirs is cleaned, anything is recorded or anything is started.
  # Nothing is signalled. A lane that has never run has no receipt and is not made to wait or
  # to read the process table.
  #
  # The lane's window is looked at once, here, and that one answer decides the rest of this
  # launch. Seen live, the launch ends in the refusal further down that names the lane as
  # running, whatever a later look shows: a window that closes meanwhile leaves its round
  # stopping, which nobody has waited for. Seen gone, the earlier rounds are waited for now,
  # and only then is anything of theirs cleaned or anything started. So no path cleans or
  # starts without that wait having passed.
  LANE_LIVE=0
  if tmux list-windows -t "$RUN" -F '#{window_name} #{pane_dead}' 2>/dev/null | grep -qx "$LANE 0"; then
    LANE_LIVE=1
  fi
  if [ "$LANE_LIVE" = 0 ]; then
    for EARLIER in "$TMUX_ROOT/$LANE"-process.json "$TMUX_ROOT/$LANE".*-process.json; do
      [ -e "$EARLIER" ] || [ -L "$EARLIER" ] || continue
      EARLIER_SLOT=$(basename "$EARLIER" -process.json)
      OWNER_RC=0
      OWNER_SAID=$("$FACTORY_PYTHON" "$D/lane_watchdog.py" previous-owner --receipt "$EARLIER" 2>&1) || OWNER_RC=$?
      case "$OWNER_RC" in
        0) ;;
        75) refuse 75 "the lane's earlier round $EARLIER_SLOT is still stopping (${OWNER_SAID#lane-watchdog: still running: }); nothing was started or cleaned, and it was not signalled" \
              "launch again once it has ended" ;;
        *) refuse 70 "cannot tell whether the lane's earlier round $EARLIER_SLOT has stopped (its process receipt or the process table could not be read); nothing was started or cleaned" \
              "check $EARLIER, then launch again" ;;
      esac
    done
  fi

  "$FACTORY_PYTHON" "$D/lane_admission.py" --root "$ROOT" --run "$RUN" --role "$ROLE" \
    --lane "$LANE" --action "$ACTION" --repository "$REPOSITORY" || \
    refuse 70 "qualification/productive admission evidence is not ready"

  # Founder, 2026-10-20: the factory is long-lived agents with special roles. A run has one Coder
  # and one Tester, each one session for the whole run; new work arrives as a message into that
  # session. No second instance, no fresh round over a session that can still resume, and no
  # time limit on a plan-billed lane.
  if [ "$ACTION" = launch ]; then
    [ -z "$INSTANCE" ] || refuse 64 "a run has one $ROLE; --instance $INSTANCE would make a second" \
      "send the work into the $ROLE's session with harness/tmux_lane_message.sh; a model trial runs one factory per model"
    PRIOR_SESSION=$("$FACTORY_PYTHON" - "$TMUX_ROOT" "$RUN" "$ROLE" <<'PY'
import json, pathlib, sys

root, run, role = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
prior = ""
for thread in sorted(root.glob(f"{role}*-thread-id")):
    slot = thread.name.removesuffix("-thread-id")
    if slot != role and not slot.startswith(role + "."):
        continue
    if thread.is_symlink():
        raise SystemExit("tmux-lane: symlinked retained session")
    if not thread.is_file() or not thread.stat().st_size:
        continue
    journal = root / f"{slot}-launch.jsonl"
    try:
        if journal.is_symlink():
            raise ValueError("symlinked launch journal")
        rows = [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]
        active = [row for row in rows if row.get("status") == "active"]
        probe = (slot.startswith(role + ".") and len(active) == 1
                 and active[0].get("mode") == "qualify"
                 and active[0].get("run_id") == run and active[0].get("role") == role
                 and active[0].get("slot") == slot)
    except (OSError, ValueError, AttributeError):
        probe = False  # Ambiguous or legacy records never exempt a productive session.
    if not probe:
        prior = str(thread)
print(prior)
PY
    ) || refuse 70 "retained session classification is unavailable"
    # Qualification is a bounded probe, not the productive seat (as in claim_seat).
    # Keep its journal and provider session intact. The live-window and verified-stop
    # checks below still prevent launching over a running or unresolved probe.
    if [ -n "$PRIOR_SESSION" ]; then
      [ -n "$REPLACE_REASON" ] || refuse 64 \
        "the $ROLE already has a session ($(basename "$PRIOR_SESSION" -thread-id)); new work goes into it, not a new round" \
        "harness/tmux_lane_message.sh $RUN validator $ROLE review|answer ...; only an unrecoverable session is replaced: --replace-session '<why it cannot resume>'"
      # A replacement re-reads the whole context, so it is a fact of the run with its reason; the
      # Orchestrator and the founder's dash see every one.
      "$FACTORY_PYTHON" - "$ROOT/events.jsonl" "$SLOT" "$(basename "$PRIOR_SESSION" -thread-id)" \
        "$REPLACE_REASON" <<'PY' || refuse 70 "the fresh-session event could not be recorded"
import datetime, json, os, sys
path, slot, prior, reason = sys.argv[1:]
row = json.dumps({
    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "kind": "fresh-session", "class": "lane", "source": "tmux_lane.sh", "wake": True,
    "detail": f"{slot} replaces {prior}'s session: {reason}",
}, sort_keys=True, separators=(",", ":"))
fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
try:
    os.write(fd, (row + "\n").encode("utf-8"))
finally:
    os.close(fd)
PY
    fi
  fi

  # Caps, in one place. A metered agent needs a per-round spend cap AND the rate it bills at:
  # no agent we run exposes live spend, so the watchdog enforces the cap as wall-clock time
  # (cap / rate) and reports spend as rate x active time. A qualification probe is always
  # wall-capped. Each field prints on its own line; an error prints the refusal.
  CAPS=$("$FACTORY_PYTHON" - "$METERED" "$ACTION" "$SPEND_CAP" "$RATE" "$WALL_CAP_MINUTES" \
    "$READ_ALLOWANCE_MINUTES" "$STALL_WINDOW_MINUTES" "$FIRST_COMMIT_MINUTES" "$MIN_COMMITS" \
    "$ROLE" "$WRAP_UP_MINUTES" "$REVIEW_EVERY_MINUTES" "$AGENT_RESUMABLE" <<'PY'
import decimal, sys

(metered, action, cap, rate, wall, allowance, window, first, minimum,
 role, wrap, review, resumable_flag) = sys.argv[1:]

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
if wall and metered != "true" and action != "qualify":
    raise SystemExit(
        "PLAN LANE: a plan-billed lane has no time limit (founder, 2026-10-20); a cap exists only to "
        "bound metered spend. Drop --wall-cap-minutes; stall and liveness checks still apply"
    )
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

def minutes_or_off(text, label, default):
    if text == "":
        return default
    if text == "0":
        return 0
    return int(amount(text, label) * 60)

# Both end the live turn and continue the lane's retained session, so only a resumable lane (a
# Codex exec thread, or a registry harness's recorded session) can have them. A wrap-up needs a cap to precede and room inside it: none on
# a cap under three wrap-ups. Progress reviews pause a Coder doing real work, never a
# qualification probe or a Tester.
resumable = resumable_flag == "1"
wrap_s = minutes_or_off(wrap, "--wrap-up-minutes", 5 * 60 if resumable else 0) if wall_cap else 0
if wrap_s and int(wall_cap) < 3 * wrap_s:
    wrap_s = 0
review_s = minutes_or_off(
    review, "--review-every-minutes",
    0,
)
if (wrap_s or review_s) and not resumable:
    raise SystemExit("--wrap-up-minutes and --review-every-minutes need a resumable lane")
if review_s and (role != "coder" or action != "launch"):
    raise SystemExit("--review-every-minutes applies to a Coder launch only")
fields = [
    "" if spend_cap is None else str(spend_cap), "" if per_hour is None else str(per_hour),
    wall_cap, microusd, str(read_s), str(window_s), str(first_s), minimum,
    str(wrap_s), str(review_s),
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
    IFS= read -r MIN_COMMITS && IFS= read -r WRAP_S && IFS= read -r REVIEW_S; } <<< "$CAPS" ||
    refuse 70 "lane caps could not be read back"

  REPOSITORY_RECEIPT=$(PYTHONPATH="$REPO_ROOT" "$FACTORY_PYTHON" \
    "$REPO_ROOT/harness/lane_repository.py" validate --source "$REPOSITORY") || \
    refuse 70 "the lane repository is not a standalone clone" \
      "give each lane and each parallel instance its own clone (git clone <source> <dir>), never a git worktree: the Codex sandbox blocks writes to the parent repository's git directory"
  REPOSITORY=$(printf '%s' "$REPOSITORY_RECEIPT" | "$FACTORY_PYTHON" -c \
    'import json,sys; print(json.load(sys.stdin)["root"])')
  # A native session removes the project files it wrote when it ends; one that was killed
  # could not. Remove exactly those (by each earlier slot's own record, unchanged bytes
  # only) before this lane's inputs are checked, unless the lane is still running. Only this
  # lane's own slots ("$LANE" and "$LANE.<round>") and its own clone are looked at: another
  # instance such as "$LANE-b" is another lane, with its own clone and possibly live.
  # "Still running" is the answer taken above, with the wait for the earlier rounds: looking
  # at the window again here could find it just closed and clean under a round nobody waited
  # for.
  if [ "$LANE_LIVE" = 0 ]; then
    for MANIFEST in "$TMUX_ROOT/$LANE"-agent-home/*-project-files.json \
        "$TMUX_ROOT/$LANE".*-agent-home/*-project-files.json \
        "$REPOSITORY"/.git/factory-runtime/*/agent/*-project-files.json; do
      [ -f "$MANIFEST" ] && [ ! -L "$MANIFEST" ] || continue
      "$FACTORY_PYTHON" "$D/native_session.py" clean-project --manifest "$MANIFEST" >/dev/null || \
        refuse 70 "an earlier session's project files could not be accounted for ($MANIFEST)"
    done
    # With the lane's own leftovers gone, anything at the paths a Cursor or Muse lane writes
    # is the target project's. It is never edited or replaced, so the lane cannot run here.
    # Say so now, before a budget is reserved or a launch recorded, not from inside the lane.
    if [ "$AGENT" = cursor-agent ]; then
      OCCUPIED=$("$FACTORY_PYTHON" "$D/native_session.py" check-project --workspace "$REPOSITORY" 2>&1) || \
        refuse 70 "the lane's repository has Cursor project configuration of its own, which is not edited or replaced: $OCCUPIED" \
          "a Cursor lane reads its hooks and its Kindex only from those paths and cannot run in this repository; choose another agent for this lane"
    fi
    if [ "$AGENT" = codex-interactive ]; then
      OCCUPIED=$("$FACTORY_PYTHON" "$D/native_session.py" check-project --agent codex-interactive --workspace "$REPOSITORY" 2>&1) || \
        refuse 70 "the lane's repository has a Codex project hook file of its own, which is not edited or replaced: $OCCUPIED" \
          "an interactive Codex lane's hooks can be added beside the operator's only through that path, so it cannot run in this repository; choose another agent for this lane"
    fi
    if [ "$AGENT" = agy ]; then
      OCCUPIED=$("$FACTORY_PYTHON" "$D/native_session.py" check-project --agent agy --workspace "$REPOSITORY" 2>&1) || \
        refuse 70 "the lane's repository has an Antigravity plugin of its own at .agents/plugins/lane, which is not edited or added to: $OCCUPIED" \
          "an Antigravity lane's hooks and Kindex reach it only as a plugin in that directory, so it cannot run in this repository; choose another agent for this lane"
    fi
    if [ "$AGENT" = muse ]; then
      OCCUPIED=$("$FACTORY_PYTHON" "$D/native_session.py" check-project --agent muse --workspace "$REPOSITORY" 2>&1) || \
        refuse 70 "the lane's repository has a Muse project hook file of its own, which is not edited or replaced: $OCCUPIED" \
          "a Muse lane's hooks can be added beside the operator's only through that path, so it cannot run in this repository; choose another agent for this lane"
    fi
  fi
  if [ "$ACTION" = launch ]; then
    PYTHONPATH="$REPO_ROOT" "$FACTORY_PYTHON" "$D/role_inputs.py" verify \
      --runs "$RUNS_ARG" --run "$RUN" --role "$ROLE" --repo "$REPOSITORY" \
      ${REPLACE_REASON:+--replacing} >/dev/null || \
      refuse 70 "author inputs are not the prepared role projection" \
        "use role_inputs.py prepare: Coder gets source minus tests; Tester gets selected public interfaces and specification, never a full clone"
  fi
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
  # Productive peers share a seed; earlier qualification probes are not product inputs.
  OTHER_ROLE=$([ "$ROLE" = coder ] && echo tester || echo coder)
  SEED_DIGEST=$("$FACTORY_PYTHON" - "$TMUX_ROOT" "$ROOT/lane-seed.jsonl" "$OTHER_ROLE" "$ACTION" <<'PY'
import hashlib, json, pathlib, sys

root, seed, other = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
action = sys.argv[4]
mine = "sha256:" + hashlib.sha256(seed.read_bytes()).hexdigest() if seed.is_file() else ""
active = []
for journal in sorted(root.glob("*-launch.jsonl")):
    for line in journal.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if action == "launch" and row.get("mode") == "qualify":
            continue
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


  # A tmux window per lane: a new round replaces a finished one, never a running one. A lane
  # seen running when this launch began is refused even if its window has closed since (its
  # round may still be stopping, and this launch did not wait for it or clean after it); so
  # is one whose window is live now.
  WINDOWS=$(tmux list-windows -t "$RUN" -F '#{window_name} #{pane_dead}' 2>/dev/null || true)
  if [ "$LANE_LIVE" = 1 ] || printf '%s\n' "$WINDOWS" | grep -qx "$LANE 0"; then
    refuse 70 "lane $LANE is still running in $RUN:$LANE" \
      "wait for its watcher to report it done, or stop it, before launching another round"
  fi

  SAFE_HOME="${HOME:?tmux-lane: HOME is required for agent authentication}"
  SAFE_USER="${USER:-$(id -un)}"
  SAFE_PATH="${PATH:?tmux-lane: PATH is required}"
  SAFE_TMPDIR="${TMPDIR:-/tmp}"
  SAFE_TERM="${TERM:-xterm-256color}"
  [ "$SAFE_TERM" != dumb ] || SAFE_TERM=xterm-256color
  SAFE_SHELL="${SHELL:-/bin/bash}"
  SAFE_LANG="${LANG:-en_US.UTF-8}"
  SAFE_CODEX_HOME="${CODEX_HOME:-$SAFE_HOME/.codex}"
  LANE_ENV=(env -i HOME="$SAFE_HOME" USER="$SAFE_USER" PATH="$SAFE_PATH" TMPDIR="$SAFE_TMPDIR"
    TERM="$SAFE_TERM" SHELL="$SAFE_SHELL" LANG="$SAFE_LANG" CODEX_HOME="$SAFE_CODEX_HOME"
    ${CLAUDE_CONFIG_DIR:+CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR"})

  # A codex lane runs with the operator's own Codex configuration: nothing of it is ignored
  # or disabled. With no --model, the model keys of that config are read here only so the
  # launch record names what the lane ran and a resume runs the same; the lane runs what the
  # operator's own `codex` would. A config that names no model leaves Codex's own default.
  if [ "$AGENT" = codex-interactive ] && [ -z "$MODEL" ]; then
    exec 4< <("$FACTORY_PYTHON" "$D/lane_agent.py" codex-model --codex-home "$SAFE_CODEX_HOME")
    IFS= read -r -d '' MODEL <&4 || refuse 70 "the model in $SAFE_CODEX_HOME/config.toml could not be read" \
      "fix $SAFE_CODEX_HOME/config.toml, or name the model with --model"
    while IFS= read -r -d '' OVERRIDE <&4; do MODEL_CONFIG+=("$OVERRIDE"); done
    exec 4<&-
    [ "${#MODEL_CONFIG[@]}" -eq 0 ] || MODEL_SOURCE=codex-config
  fi
  if [ "$AGENT" = agy ] && [ -z "$MODEL" ]; then
    exec 4< <("$FACTORY_PYTHON" "$D/lane_agent.py" agy-model --operator-home "$SAFE_HOME")
    IFS= read -r -d '' MODEL <&4 || refuse 70 "Agy's configured model could not be resolved" \
      "restore a valid Agy model selection, or name the selected model with --model"
    exec 4<&-
    [ -z "$MODEL" ] || MODEL_SOURCE=agy-config
  fi
  [ -n "$MODEL_SOURCE" ] || MODEL_SOURCE=agent

  # Record model-family metadata without granting or withholding admission.
  INDEPENDENCE=""
  if [ "$ACTION" = launch ]; then
    INDEPENDENCE=$("$FACTORY_PYTHON" "$D/lane_agent.py" seat claim --root "$TMUX_ROOT" \
      --role "$ROLE" --slot "$SLOT" --agent "$AGENT" --model "$MODEL" \
      --codex-home "$SAFE_CODEX_HOME" --pid $$) || exit 70
  fi

  # The dependency cache and runtime the lane's own commands use (dogfood #31/#61): set in the
  # Codex shell policy, because inherit="core" drops anything else, and recorded in the launch so
  # every resume sets the same.
  ENV_OUT="$(mktemp "${TMPDIR:-/tmp}/factory-lane-env.XXXXXX")"
  ENV_ERROR=$("$FACTORY_PYTHON" "$D/lane_agent.py" environment --path "$SAFE_PATH" \
    ${LANE_ENV_ARGS[@]+"${LANE_ENV_ARGS[@]}"} 2>&1 >"$ENV_OUT") || {
    rm -f "$ENV_OUT"
    refuse 64 "lane environment refused: ${ENV_ERROR#lane-agent: }" \
      "pass --cache-env NAME=<read-only store dir> and --runtime-dir <read-only bin dir>"
  }
  { IFS= read -r -d '' SHELL_POLICY && IFS= read -r -d '' LANE_PATH &&
    IFS= read -r -d '' LANE_ENV_RECORD && IFS= read -r -d '' LANE_ENV_NOTICE &&
    IFS= read -r -d '' LANE_ENV_ASSIGN; } < "$ENV_OUT" || {
    rm -f "$ENV_OUT"; exit 70;
  }
  rm -f "$ENV_OUT"

  # Sign-in, checked the way the lane will run: the same scrubbed environment, stdin closed,
  # bounded time. An agent that is signed out, or whose check waits for a person, would sit on
  # a login screen; it is refused here with the exact command that fixes it.
  set +e
  PREFLIGHT_BILLING=plan
  [ "$METERED" != true ] || PREFLIGHT_BILLING=metered
  AUTH=$("${LANE_ENV[@]}" "$FACTORY_PYTHON" "$D/lane_agent.py" preflight --agent "$AGENT" \
    --billing "$PREFLIGHT_BILLING" 2>&1)
  AUTH_RC=$?
  set -e
  if [ "$AUTH_RC" -ne 0 ]; then
    REMEDIATION=$(printf '%s\n' "$AUTH" | sed -n 's/^lane-agent: remediation: //p' | head -1)
    REASON=$(printf '%s\n' "$AUTH" | grep -v '^lane-agent: remediation: ' | head -1 | sed 's/^lane-agent: //')
    refuse 77 "AUTH: $AGENT cannot start non-interactively: $REASON" "$REMEDIATION"
  fi

  QUALIFICATIONS="$FACTORY_RUNS_ROOT/lane-qualifications.jsonl"

  # The agent's CLI contract, before anything is written (harness/agent_contract.sh, which
  # setup also runs to confirm or update an agent before a run).
  CONTRACT_REPORT=$("$D/agent_contract.sh" "$AGENT" "$AGENT_EXECUTABLE" "$PERSISTENT" 2>&1) || \
    refuse 70 "$CONTRACT_REPORT" \
      "update that executable (its own update command, brew or npm), or put a current one first on PATH"
  CLI_CONTRACT=${CONTRACT_REPORT%%$'\n'*}
  AGENT_VERSION=${CONTRACT_REPORT#*$'\n'}

  # Keep the testing standard. A concise strategy may be in the brief rather than a fixed file.
  STANDARD_RECEIPT='null'
  STANDARD_BLOCK=""
  if [ "$ROLE" = tester ]; then
    STANDARD_BLOCK="$TMUX_ROOT/$SLOT-testing-standard.md"
    STRATEGY_ARGS=()
    if [ -e "$ROOT/artifacts/testing-strategy.md" ] || [ -L "$ROOT/artifacts/testing-strategy.md" ]; then
      STRATEGY_ARGS=(--strategy "$ROOT/artifacts/testing-strategy.md")
    fi
    STANDARD_RECEIPT=$("$FACTORY_PYTHON" "$D/testing_standard.py" block \
      --factory-home "$REPO_ROOT" ${STRATEGY_ARGS[@]+"${STRATEGY_ARGS[@]}"} --output "$STANDARD_BLOCK" 2>&1) || \
      refuse 70 "TESTING STANDARD: $STANDARD_RECEIPT" \
        "restore docs/standards/TESTING.md and prompts/test.md in $REPO_ROOT"
    # Every oracle input the Strategy cites is in the Tester's tree as its pinned bytes
    # (dogfood #36, #65): found mid-build it costs a round, reformatted it silently governs.
    if [ "${#STRATEGY_ARGS[@]}" -gt 0 ]; then
      ORACLE_INPUTS=$("$FACTORY_PYTHON" "$D/strategy_plan.py" oracle-inputs \
      --strategy "$ROOT/artifacts/testing-strategy.md" --repo "$REPOSITORY" 2>&1) || \
      refuse 70 "ORACLE INPUTS: ${ORACLE_INPUTS#strategy-plan: }" \
        "project each cited input into the Tester's tree from its pinned upstream commit (git show <pin>:<path>), byte for byte, and keep formatters off it"
    fi
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
  # The prompt is a short main prompt plus files the lane reads itself (founder, 2026-10-18:
  # "giving a main prompt and subsequent inputs or references to files"). The verbatim task and
  # any testing standard go into the lane's own untracked .git/factory-brief/, which its sandbox
  # reads and its commits never carry; the prompt names each with its byte count and sha256.
  BRIEF_MANIFEST="$TMUX_ROOT/$SLOT-brief.json"
  # What is left of the cap, as the watcher counts it; the lane runs this itself.
  printf -v CLOCK_CMD '%q %q remaining --root %q --slot %q' \
    "$FACTORY_PYTHON" "$D/lane_watchdog.py" "$ROOT" "$SLOT"
  RESUMABLE="$AGENT_RESUMABLE"
  PROMPT_DIGEST=$(LANE_PROTOCOL_NOTE="$PROTOCOL_NOTE" PYTHONPATH="$REPO_ROOT" "$FACTORY_PYTHON" - "$TASK_COPY" "$PROMPT" "$ROLE" "$SLOT" "$STANDARD_BLOCK" \
    "$LANE_ENV_NOTICE" "$WALL_CAP_S" "$WRAP_S" "$REVIEW_S" "$RESUMABLE" "$CLOCK_CMD" \
    "$REPOSITORY" "$BRIEF_MANIFEST" "$ROOT" "$D" <<'PY'
import hashlib, json, os, pathlib, sys

source, destination = map(pathlib.Path, sys.argv[1:3])
role, slot, standard, environment, wall, wrap, review, resumable, clock = sys.argv[3:12]
repository, manifest = pathlib.Path(sys.argv[12]), pathlib.Path(sys.argv[13])
task = source.read_bytes()
protocol = f"""FACTORY TMUX LANE PROTOCOL (role={role}, slot={slot})
- You are a real author agent, not a prompt printer. Work the task and use tools.
- This standalone repository, including its .git directory, is yours. Make useful
  checkpoint commits; do not ask the host to commit for you.
- Commit useful coherent changes. Silence or time since a commit does not prove failure.
- Before each checkpoint, inspect your own status/diff, run the relevant checks,
  and commit only the work assigned to this lane.
- Do not read or infer the other author lane's work.
- If a missing or contradictory specification would make you guess semantics, stop
  the turn with one final line: the word FACTORY_QUESTION, a colon, then one concrete
  question. Do not guess.
- A FACTORY_ANSWER is specification input bound to that question, never information
  about the other lane. Resume from your own work after receiving it.
- On FACTORY_STATUS_PROBE, answer with a leading FACTORY_STATUS: WORKING, BLOCKED,
  QUESTION, or DONE and one concise factual sentence. Continue if unblocked.
- When the assigned work is committed, end with one line of the form
  __LANE_DONE__ {slot} commits=<the number of commits you made>
- A turn ending is not the run ending. Preserve this conversation until the Validator
  has judged the result and completed the task.
"""
if wall:
    protocol += (
        f"- This lane's work is capped at {int(wall) // 60} minutes of active time across its\n"
        f"  turns; `{clock}` prints what is left. Use that time: a requirement you have not\n"
        "  exercised is not done.\n"
    )
    if int(wrap) and resumable == "1":
        protocol += (
            f"- About {int(wrap) // 60} minutes before the cap you receive FACTORY_WRAP_UP. Then start\n"
            "  nothing new: finish or revert any half-made edit, leave the work building and\n"
            "  running, commit, and end with the done line. At the cap the watchdog stops the lane.\n"
        )
    else:
        protocol += "- At the cap the watchdog stops the lane: keep the committed work building.\n"
if int(review) and resumable == "1":
    protocol += (
        f"- Every {int(review) // 60} minutes of work the watchdog pauses this lane and the Validator\n"
        "  reviews it against the task specification; a FACTORY_REVIEW then resumes this\n"
        "  same session. Weigh it against the specification's own words and keep what already\n"
        "  works.\n"
    )
if environment:
    protocol += "- Your shell commands run with this environment:\n" + environment + "\n"
# What this lane's provider needs said (harness/agents.py protocol_note), e.g. which MCP
# server is the lane's own Kindex.
protocol += os.environ.get("LANE_PROTOCOL_NOTE", "")
git_dir = repository / ".git"
if not git_dir.is_dir() or git_dir.is_symlink():
    raise SystemExit("tmux-lane: the lane repository has no .git directory to hold its brief")
brief_dir = git_dir / "factory-brief"
brief_dir.mkdir(mode=0o700, exist_ok=True)
brief = []
inputs = []
if standard:
    inputs.append(("testing-standard.md", "the testing standard, the Tester doctrine and this "
                   "run's testing strategy", pathlib.Path(standard).read_bytes()))
inputs.append(("task.txt", "the verbatim lane task", task))
sys.path.insert(0, sys.argv[15])  # Trusted harness, never a target-owned namespace package.
from run_record import builder_attribution, read_json
attribution = builder_attribution(read_json(pathlib.Path(sys.argv[14]) / "harness.json"))
identity = ("Use these exact trailers on task commits:\n" + attribution["commit_trailers"]
            + "\nRetained builder identity: " + json.dumps(attribution["identity"])
            + "\nIf the version is unknown, report it; never infer it from the target or current install.\n")
inputs.append(("builder.txt", "this run's retained builder attribution", identity.encode()))
for name, what, body in inputs:
    target = brief_dir / name
    out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
                  0o600)
    with os.fdopen(out, "wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    brief.append({"path": str(target), "what": what, "bytes": len(body),
                  "sha256": "sha256:" + hashlib.sha256(body).hexdigest()})
protocol += "\nREAD FIRST, IN FULL, BEFORE ANY OTHER STEP\n"
protocol += ("These files are this lane's inputs. Read every line of each, check its sha256, and "
             "treat it exactly as if it were written here. Do not edit, move or commit them.\n")
for item in brief:
    protocol += f"- {item['path']} ({item['what']}; {item['bytes']} bytes, {item['sha256']})\n"
raw = protocol.encode("utf-8")
fd = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as stream:
    json.dump(brief, stream, sort_keys=True, separators=(",", ":"))
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
  # Measured against the harness's delivery limit and recorded in the launch row. Over the limit
  # is never a refusal or a fallback to another agent: lane_agent.py places an oversized argv
  # turn in .git/factory-brief/ and passes a pointer with its sha256.
  PROMPT_BYTES=$(wc -c < "$PROMPT" | tr -d ' ')

  # A metered round's cap is reserved against the run's objective budget.
  # A run created without --budget cannot start a metered round.
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

  BOUNDARY_RECORD=$("$FACTORY_PYTHON" "$D/validator_boundary.py" select-lane \
    --role "$ROLE" --cwd "$REPOSITORY") || refuse 77 "author boundary admission failed"
  KINDEX_HOME="$TMUX_ROOT/$SLOT-kindex-home"
  AGENT_HOME="$TMUX_ROOT/$SLOT-agent-home"
  if [ "$BOUNDARY_RECORD" != null ]; then
    # Mutable client/Kindex state belongs to this lane, not the host control tree that its
    # kernel profile protects. .git is already verified standalone and excluded from commits.
    PRIVATE_RUNTIME=$("$FACTORY_PYTHON" - "$REPOSITORY" "$SLOT" <<'PY'
import pathlib, sys
git = pathlib.Path(sys.argv[1]) / ".git"
root = git / "factory-runtime" / sys.argv[2]
if git.is_symlink() or not git.is_dir() or root.resolve() != root:
    raise SystemExit("tmux-lane: private runtime path is aliased or unavailable")
print(root)
PY
    ) || refuse 77 "author private runtime cannot be isolated"
    KINDEX_HOME="$PRIVATE_RUNTIME/kindex"
    AGENT_HOME="$PRIVATE_RUNTIME/agent"
  fi
  # Kindex scoped to this lane's own copy of the target's .kin (harness/lane_kindex.py).
  SEED_ARGS=()
  [ ! -e "$ROOT/lane-seed.jsonl" ] || SEED_ARGS=(--seed "$ROOT/lane-seed.jsonl")
  KINDEX=$("$FACTORY_PYTHON" "$D/lane_kindex.py" prepare --repo "$REPOSITORY" \
    --home "$KINDEX_HOME" ${SEED_ARGS[@]+"${SEED_ARGS[@]}"}) || exit 70
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
  PROFILE_DIGEST=$(printf '%s\n%s' "$PERMISSION_PROFILE" "$SHELL_POLICY" | shasum -a 256 | cut -d' ' -f1)
  LANE_LOG="$TMUX_ROOT/$SLOT-lane.log"
  LANE_EXITS="$TMUX_ROOT/$SLOT-exits.jsonl"
  BOUNDARY_MODEL_ARGS=""
  BOUNDARY_CODEX_ARGS=""
  if [ "$BOUNDARY_RECORD" != null ]; then
    printf -v BOUNDARY_MODEL_ARGS ' --boundary-record %q --boundary-role %q' "$BOUNDARY_RECORD" "$ROLE"
    printf -v BOUNDARY_CODEX_ARGS ' --boundary-record %q' "$BOUNDARY_RECORD"
  fi
  AUTHOR_CUSTODY=$("$FACTORY_PYTHON" - "$D" "$ROLE" "$AGENT" "$REPOSITORY" "$BOUNDARY_RECORD" "$ROOT" <<'PY'
import json, os, pathlib, sys
sys.path.insert(0, sys.argv[1])
import author_custody
print(json.dumps(author_custody.select(dict(os.environ), sys.argv[2], sys.argv[3],
    pathlib.Path(sys.argv[4]), json.loads(sys.argv[5]), pathlib.Path(sys.argv[6]))))
PY
  ) || refuse 77 "required author custody admission failed"
  AUTHOR_HOOK_ARGS=()
  if [ "$AUTHOR_CUSTODY" != null ]; then
    [ -n "${FACTORY_SIGNET_CONTRACT:-}" ] && [ -n "${FACTORY_SIGNET_DIGEST:-}" ] || \
      refuse 77 "author custody requires its Signet hook binding"
    AUTHOR_HOOK_ARGS=(--author-config "$FACTORY_AUTHOR_CUSTODY_CONFIG" --author-digest "$FACTORY_AUTHOR_CUSTODY_DIGEST")
  fi
  DIALOGUE_BASELINE=$("$FACTORY_PYTHON" "$D/lane_admission.py" --root "$ROOT" --run "$RUN" \
    --role "$ROLE" --action baseline) || refuse 70 "cannot retain dialogue baseline"
  PLANNED=$(LANE_START_REQUIRES="$START_REQUIRES" LANE_MEMORY_ISOLATION="$MEMORY_ISOLATION" LANE_NATIVE_BLOCKER="$NATIVE_BLOCKER" LANE_DIALOGUE_BASELINE="$DIALOGUE_BASELINE" LANE_PERSISTENT="$PERSISTENT" LANE_NATIVE_DIR="$AGENT_HOME" LANE_CLAUDE_LAUNCHER="${FACTORY_CLAUDE_LAUNCHER:-}" LANE_AUTHOR_CUSTODY="$AUTHOR_CUSTODY" LANE_KERNEL_BOUNDARY="$BOUNDARY_RECORD" LANE_INDEPENDENCE="$INDEPENDENCE" "$FACTORY_PYTHON" - "$RUN" "$ROLE" "$AGENT" "$REPOSITORY" \
    "$PROMPT" "$PROMPT_DIGEST" "$TASK_DIGEST" "$PROFILE_DIGEST" \
    "$REPOSITORY_RECEIPT" "$AGENT_VERSION" "$MODEL" "$KINDEX" \
    "$LANE" "$SLOT" "$INSTANCE" "$ROUND" "$ACTION" "$METERED" "$PROMPT_VIA" "$AGENT_TTY" \
    "$CLI_CONTRACT" "$AUTH" "$SPEND_CAP" "$RATE" "$WALL_CAP_S" "$READ_ALLOWANCE_S" \
    "$STALL_WINDOW_S" "$FIRST_COMMIT_S" "$MIN_COMMITS" "$BUDGET_RESERVATION" \
    "$STANDARD_RECEIPT" "$LANE_LOG" "$LANE_EXITS" "$QUALIFICATIONS" "$LANE_ENV_RECORD" "$MODEL_SOURCE" \
    "$WRAP_S" "$REVIEW_S" "$FACTORY_RUNS_ROOT" "${FACTORY_LANE_SANDBOX:-on}" \
    "$PROMPT_BYTES" "$BRIEF_MANIFEST" \
    ${MODEL_CONFIG[@]+"${MODEL_CONFIG[@]}"} <<'PY'
import datetime, json, os, pathlib, sys
(
    run, role, agent, repository, prompt, prompt_digest, task_digest,
    profile_digest, preflight, agent_version, model, kindex,
    lane, slot, instance, round_tag, action, metered, prompt_via, tty,
    cli_contract, auth, spend_cap, rate, wall_cap, read_allowance,
    stall_window, first_commit, min_commits, reservation,
    standard, lane_log, lane_exits, qualifications, lane_env, model_source,
    wrap_s, review_s, runs_dir, sandbox, prompt_bytes, brief_manifest,
) = sys.argv[1:43]
model_config = sys.argv[43:]
# One argv element holds at most 131071 bytes (MAX_ARG_STRLEN - 1); stdin has no such ceiling.
# A native terminal session is typed at most 16384 bytes (native_session.TYPED_LIMIT).
prompt_limit = {"argv": 131071, "terminal": 16384}.get(prompt_via)
over_limit = prompt_limit is not None and int(prompt_bytes) > prompt_limit
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
    "dialogue_baseline": int(os.environ["LANE_DIALOGUE_BASELINE"]),
    "agent": agent,
    **({"persistent": True} if os.environ["LANE_PERSISTENT"] == "1" else {}),
    # What a lane message needs to restart an ended hosted session as it was started
    # (tmux_lane_message.sh): where its hook settings and journal live, and the command the
    # operator starts Claude Code with (null: none was named, so the default applies again).
    **({"native": {"dir": os.environ["LANE_NATIVE_DIR"],
                   "claude_launcher": os.environ["LANE_CLAUDE_LAUNCHER"] or None}}
       if os.environ["LANE_PERSISTENT"] == "1" else {}),
    # What the lane actually ran, for provenance: the model, where it came from (operator,
    # codex-config, agy-config or agent), and config overrides, which a resume reuses.
    "model": model,
    "model_source": model_source,
    "model_config": model_config,
    # The lane's model family and, beside the other lane, the independence tier achieved (I19).
    "independence": json.loads(os.environ.get("LANE_INDEPENDENCE") or "null"),
    "terminal": tty == "terminal",
    "prompt_via": prompt_via,
    "prompt_bytes": int(prompt_bytes),
    "prompt_limit": prompt_limit,
    "prompt_delivery": "file-pointer" if over_limit else prompt_via,
    "brief": json.loads(pathlib.Path(brief_manifest).read_text(encoding="utf-8")),
    "auth_preflight": auth.strip(),
    "metered": metered == "true",
    # FACTORY_LANE_SANDBOX=off (a host whose kernel gives Codex's sandbox no namespace) runs the
    # lane unsandboxed; the record says so, and a resume reuses this, never the ambient value.
    "sandbox": "off" if sandbox == "off" else "on",
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
        # lane_watchdog.py: the wrap-up before the cap and the Coder's progress-review pauses,
        # both delivered by resuming the lane's thread through tmux_lane_message.sh.
        "wrap_s": int(wrap_s),
        "review_every_s": int(review_s),
        "review_wait_s": 900,
        "runs_dir": runs_dir,
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
    # "lane-only": the lane sees its scoped Kindex and none of the operator's MCP servers.
    # "beside-operator": the operator's settings and servers are intact and the lane's Kindex
    # is added beside them, so memory is not a boundary between the lanes.
    "memory_isolation": os.environ["LANE_MEMORY_ISOLATION"],
    # Why this provider still runs in print mode with a private configuration, or null.
    "native_blocker": os.environ["LANE_NATIVE_BLOCKER"] or None,
    # What the provider's own program asks the operator at start; the Factory answers none.
    "start_requires": os.environ["LANE_START_REQUIRES"] or None,
    "lane_env": json.loads(lane_env),
    "repository": repository,
    "prompt_path": prompt,
    "prompt_digest": prompt_digest,
    "verbatim_task_digest": task_digest,
    "permission_profile_digest": "sha256:" + profile_digest,
    "agent_version": agent_version,
    "cli_contract": cli_contract,
    "repository_preflight": json.loads(preflight),
    "boundary": "operator-owned-tmux-unqualified",
    "kernel_boundary": json.loads(os.environ["LANE_KERNEL_BOUNDARY"]),
    "author_custody": json.loads(os.environ["LANE_AUTHOR_CUSTODY"]),
    "host_git_after_agent_start": "forbidden",
}, sort_keys=True, separators=(",", ":")))
PY
  )
  append_event "$PLANNED"

  LOCAL_ARGS=""
  # Add the approved evaluator hook beside the operator's own hook and MCP configuration.
  # This settings selection does not waive Codex's hook trust review or attest runtime loading.
  if [ "${FACTORY_REQUIRE_SIGNET:-0}" = 1 ] || [ -n "${FACTORY_SIGNET_CONTRACT:-}" ] || \
      [ -n "${FACTORY_SIGNET_DIGEST:-}" ]; then
    [ -n "${FACTORY_SIGNET_CONTRACT:-}" ] && [ -n "${FACTORY_SIGNET_DIGEST:-}" ] || \
      refuse 77 "required Signet hook contract and digest are missing"
    case "$AGENT" in
      codex|codex-interactive)
        SIGNET_OVERRIDE=$("${LANE_ENV[@]}" "$FACTORY_PYTHON" "$D/signet_hooks.py" --client codex \
          --contract "$FACTORY_SIGNET_CONTRACT" --digest "$FACTORY_SIGNET_DIGEST" \
          --render-codex --check-codex-cwd "$REPOSITORY" ${AUTHOR_HOOK_ARGS[@]+"${AUTHOR_HOOK_ARGS[@]}"}) || \
          refuse 77 "required Signet hook configuration or exact Codex hook trust is unavailable" \
            "review the host-pinned hook with Codex /hooks; no lane was started"
        printf -v SIGNET_ARGS -- '-c %q -c %q' 'features.hooks=true' "$SIGNET_OVERRIDE"
        ;;
      claude|cursor-agent) SIGNET_ARGS="" ;;
      *) refuse 77 "required Signet client adapter is not qualified" ;;
    esac
  else
    SIGNET_ARGS=""
  fi
  if [ "$AGENT" = "codex-ollama" ]; then
    printf -v LOCAL_ARGS -- '--oss --local-provider ollama -m %q' "$MODEL"
  elif [ "$MODEL_SOURCE" = operator ] && case "$AGENT" in codex|codex-interactive) true ;; *) false ;; esac; then
    printf -v LOCAL_ARGS -- '-m %q' "$MODEL"
  fi
  for OVERRIDE in ${MODEL_CONFIG[@]+"${MODEL_CONFIG[@]}"}; do
    printf -v QUOTED -- '-c %q' "$OVERRIDE"
    LOCAL_ARGS="${LOCAL_ARGS:+$LOCAL_ARGS }$QUOTED"
  done
  [ -z "$SIGNET_ARGS" ] || LOCAL_ARGS="${LOCAL_ARGS:+$LOCAL_ARGS }$SIGNET_ARGS"
  # A Codex lane's --effort travels as a config override, last so it wins; one closed-set word.
  case "$AGENT" in codex|codex-interactive)
    [ -z "$EFFORT" ] || LOCAL_ARGS="${LOCAL_ARGS:+$LOCAL_ARGS }-c model_reasoning_effort=$EFFORT" ;;
  esac
  if [ "$AGENT" = codex ] || [ "$AGENT" = codex-interactive ]; then
    : > "$TMUX_ROOT/$SLOT-lane-setup.nul"
    if [ -n "$SIGNET_ARGS" ]; then
      printf '%s\0' '-c' 'features.hooks=true' '-c' "$SIGNET_OVERRIDE" > \
        "$TMUX_ROOT/$SLOT-lane-setup.nul"
    fi
    # The interactive lane's record names it and says it is a resident session, which is
    # what a lane message checks before it hands a message to the wrapper.
    TURN_RECORD_AGENT=codex
    [ "$AGENT" != codex-interactive ] || TURN_RECORD_AGENT=codex-interactive
    "$FACTORY_PYTHON" "$D/agents.py" turn-record --output "$TMUX_ROOT/$SLOT-turn.json" \
      --agent "$TURN_RECORD_AGENT" --model "$MODEL" --effort "$EFFORT" --billing plan \
      --env-file "" --setup "$TMUX_ROOT/$SLOT-lane-setup.nul" ${PERSISTENT_ARGS[@]+"${PERSISTENT_ARGS[@]}"} || \
      refuse 70 "the lane's turn record could not be written"
  fi
  THREAD_FILE="$TMUX_ROOT/$SLOT-thread-id"
  CODEX_EVENTS="$TMUX_ROOT/$SLOT-codex-events.jsonl"
  printf -v ENV_PREFIX 'exec env -i HOME=%q USER=%q PATH=%q TMPDIR=%q TERM=%q SHELL=%q LANG=%q CODEX_HOME=%q FACTORY_RUNS_DIR=%q HARNESS_RUN_ROOT=%q' \
    "$SAFE_HOME" "$SAFE_USER" "$LANE_PATH" "$SAFE_TMPDIR" "$SAFE_TERM" "$SAFE_SHELL" "$SAFE_LANG" "$SAFE_CODEX_HOME" \
    "$FACTORY_RUNS_ROOT" "$ROOT"
  # Agents without a shell policy (cursor-agent) read the cache variables from their own env.
  ENV_PREFIX+="${LANE_ENV_ASSIGN:+ $LANE_ENV_ASSIGN}"
  printf -v SOCKET_ENV " FACTORY_TMUX_SOCKET=%q" "$FACTORY_TMUX_SOCKET"
  ENV_PREFIX+="$SOCKET_ENV"
  # The command the operator starts Claude Code with (harness/native_session.py).
  if [ -n "${FACTORY_CLAUDE_LAUNCHER:-}" ]; then
    printf -v LAUNCHER_ENV " FACTORY_CLAUDE_LAUNCHER=%q" "$FACTORY_CLAUDE_LAUNCHER"
    ENV_PREFIX+="$LAUNCHER_ENV"
  fi
  # Every lane process runs under lane_agent.py run: it tees output to the lane log and records
  # the process's end in the exits journal the watcher reads.
  printf -v WRAP '%q %q run --slot %q --log %q --exits %q' \
    "$FACTORY_PYTHON" "$D/lane_agent.py" "$SLOT" "$LANE_LOG" "$LANE_EXITS"
  if [ "$PERSISTENT" = 1 ]; then
    printf -v PERSISTENT_WRAP ' --persistent --run-root %q' "$ROOT"
    WRAP+="$PERSISTENT_WRAP"
    case "$AGENT" in claude|claude-ollama|cursor-agent|muse|agy|codex-interactive)
      # The session's own hook commands write its lifecycle journal here, so it is the lane's
      # private runtime, which a kernel boundary leaves writable, not the host control tree.
      # For Cursor, Muse and Antigravity it also holds the record of the project files the
      # session wrote.
      printf -v NATIVE_WRAP ' --native-dir %q' "$AGENT_HOME"
      WRAP+="$NATIVE_WRAP" ;;
    esac
  fi
  # Codex's trusted event/session driver stays outside the model boundary. Other clients
  # are direct children of lane_agent, which retains their process/exit evidence on the host.
  case "$AGENT" in codex-ollama) ;; *) WRAP+="$BOUNDARY_MODEL_ARGS" ;; esac
  # On a host whose kernel will not give Codex's own sandbox a namespace (a gVisor container,
  # for one), the operator sets FACTORY_LANE_SANDBOX=off: the container is then the sandbox.
  CODEX_SANDBOX_ARG=""
  [ "${FACTORY_LANE_SANDBOX:-on}" != off ] || CODEX_SANDBOX_ARG="--dangerously-bypass-approvals-and-sandbox"
  # Codex refuses --ask-for-approval together with the bypass flag (rc=2), so the TUI takes one or the other.
  CODEX_POLICY_ARG="${CODEX_SANDBOX_ARG:---ask-for-approval never}"
  # The operator's Codex configuration, rules, apps and MCP servers stay as they are. The lane
  # adds its permission profile, shell policy and scoped Kindex as -c overrides beside them.
  case "$AGENT" in
    codex-ollama)
      # The one lane that still runs `codex exec` (with --oss): see the note where AGENT is set.
      printf -v LANE_CMD '%s %s -- %q %q --prompt %q --thread-file %q --events %q --root %q --role %q%s -- codex %s exec %s --json -C %q -c %q -c %q -c %q -c %q -' \
        "$ENV_PREFIX" "$WRAP" "$FACTORY_PYTHON" "$D/codex_lane_session.py" \
        "$PROMPT" "$THREAD_FILE" "$CODEX_EVENTS" "$ROOT" "$LANE" "$BOUNDARY_CODEX_ARGS" "$CODEX_POLICY_ARG" "$LOCAL_ARGS" "$REPOSITORY" \
        'default_permissions="factory-lane"' "$PERMISSION_PROFILE" "$SHELL_POLICY" "$KINDEX_OVERRIDE" ;;
    codex-interactive)
      # Codex's own interactive session on a terminal the wrapper owns (harness/native_session.py
      # codex_plan): the wrapper adds the lane's report hooks and gives the first message at
      # launch. Codex skips a hook it has not been told to trust; FACTORY_LANE_SANDBOX=off is
      # the explicit per-run choice that also passes its own bypass for that. Nothing else
      # about trust is decided here.
      CODEX_HOOK_TRUST_ARG=""
      [ "${FACTORY_LANE_SANDBOX:-on}" != off ] || CODEX_HOOK_TRUST_ARG="--dangerously-bypass-hook-trust"
      printf -v LANE_CMD '%s %s --session-agent codex-interactive --session-file %q --prompt-arg %q -- codex --no-daemon --no-alt-screen %s %s %s -C %q -c %q -c %q -c %q -c %q' \
        "$ENV_PREFIX" "$WRAP" "$THREAD_FILE" "$PROMPT" "$CODEX_POLICY_ARG" "$CODEX_HOOK_TRUST_ARG" "$LOCAL_ARGS" "$REPOSITORY" \
        'default_permissions="factory-lane"' "$PERMISSION_PROFILE" "$SHELL_POLICY" "$KINDEX_OVERRIDE" ;;
    cursor-agent|claude|claude-ollama|agy|muse|prime-agent)
      # A registry harness (harness/agents.py). Its lane configuration (the lane's scoped
      # Kindex) and its variables are built under the slot's agent home; the variables reach the
      # lane through an owner-only file the wrapper loads, never through this launch string. A
      # plan lane admits no API key, and the wrapper stops it if the harness reports per-use
      # billing anyway. Claude Code keeps every operator setting, hook, plugin and MCP server.
      BILLING=plan
      [ "$METERED" != true ] || BILLING=metered
      CREDENTIALS="$TMUX_ROOT/$SLOT-credentials.env"
      SETUP_OUT="$TMUX_ROOT/$SLOT-lane-setup.nul"
      "$FACTORY_PYTHON" "$D/agents.py" lane-setup --agent "$AGENT" --billing "$BILLING" \
        --home "$AGENT_HOME" --model "$MODEL" --kindex-record "$KINDEX" \
        --env-file "$CREDENTIALS" > "$SETUP_OUT" || \
        refuse 70 "the agent registry could not prepare $AGENT's lane home"
      case "$AGENT" in claude|claude-ollama)
        # No permission mode is imposed on Claude Code. FACTORY_LANE_SANDBOX=off is the
        # configuration that adds its native permissive flag, retained with the turn. A
        # launcher that is a shell function carries its own flags, and the wrapper passes
        # no permission argument over it.
        if [ "${FACTORY_LANE_SANDBOX:-on}" = off ]; then
          printf '%s\0' '--dangerously-skip-permissions' >> "$SETUP_OUT"
        fi ;;
      esac
      if [ "$AGENT" = cursor-agent ] && [ "${FACTORY_LANE_SANDBOX:-on}" = off ]; then
        # Retain the explicit choice with the turn, so later resumes do not fall back to
        # the user's unrelated Cursor configuration.
        printf '%s\0' '--sandbox' 'disabled' >> "$SETUP_OUT"
      fi
      if [ "$AGENT" = agy ] && [ "${FACTORY_LANE_SANDBOX:-on}" = off ]; then
        # Nothing is imposed on Antigravity's approvals otherwise. This is the one
        # configuration that adds its native permissive flag, retained with the turn.
        printf '%s\0' '--dangerously-skip-permissions' >> "$SETUP_OUT"
      fi
      if [ "$AGENT" = muse ] && [ "${FACTORY_LANE_SANDBOX:-on}" = off ]; then
        # Nothing is imposed on Muse's approvals or sandbox otherwise. This is the one
        # configuration that adds its native permissive flag, retained with the turn.
        printf '%s\0' '--yolo' >> "$SETUP_OUT"
      fi
      SETUP_ARGS=()
      while IFS= read -r -d '' PART; do SETUP_ARGS+=("--extra-arg=$PART"); done < "$SETUP_OUT"
      TURN=()
      while IFS= read -r -d '' PART; do TURN+=("$PART"); done < <(
        "$FACTORY_PYTHON" "$D/agents.py" turn --agent "$AGENT" --model "$MODEL" \
          --effort "$EFFORT" --cwd "$REPOSITORY" ${PERSISTENT_ARGS[@]+"${PERSISTENT_ARGS[@]}"} \
          ${SETUP_ARGS[@]+"${SETUP_ARGS[@]}"})
      [ "${#TURN[@]}" -ge 2 ] || refuse 70 "the agent registry built no turn for $AGENT"
      # What a lane message needs to resume this session the same way (tmux_lane_message.sh).
      "$FACTORY_PYTHON" "$D/agents.py" turn-record --output "$TMUX_ROOT/$SLOT-turn.json" \
        --agent "$AGENT" --model "$MODEL" --effort "$EFFORT" --billing "$BILLING" \
        --env-file "$CREDENTIALS" --setup "$SETUP_OUT" ${PERSISTENT_ARGS[@]+"${PERSISTENT_ARGS[@]}"} || \
        refuse 70 "the lane's turn record could not be written"
      QUOTED_TURN=""
      for PART in "${TURN[@]:1}"; do printf -v QUOTED_TURN '%s %q' "$QUOTED_TURN" "$PART"; done
      PLAN_FLAG=""
      [ "$BILLING" != plan ] || PLAN_FLAG="--plan-only"
      printf -v LANE_CMD '%s %s --env-file %q --session-agent %q --session-file %q %s --prompt-arg %q --%s' \
        "$ENV_PREFIX" "$WRAP" "$CREDENTIALS" "$AGENT" "$THREAD_FILE" "$PLAN_FLAG" "$PROMPT" "$QUOTED_TURN" ;;
  esac
  if [ "$AUTHOR_CUSTODY" != null ]; then
    printf -v LANE_CMD '%q %q --config %q --digest %q --shell-command %q' \
      "$FACTORY_PYTHON" "$D/author_custody.py" "$FACTORY_AUTHOR_CUSTODY_CONFIG" \
      "$FACTORY_AUTHOR_CUSTODY_DIGEST" "$LANE_CMD"
  fi
  if [ "$AGENT_TTY" = terminal ]; then
    # Output-only observation starts inside the pane before the native TUI. It never sends
    # status prose as input, and remains attached to this pane rather than a window name.
    printf -v CAPTURE_CMD '%q %q capture --runs %q --run %q --slot %q' \
      "$FACTORY_PYTHON" "$D/run_record.py" "$FACTORY_RUNS_ROOT" "$RUN" "$SLOT"
    # Keep the exact pane target without a shell-sensitive =target.
    printf -v CAPTURE_PIPE 'tmux -L %q pipe-pane -O -t "$TMUX_PANE" %q' \
      "$FACTORY_TMUX_SOCKET" "$CAPTURE_CMD"
    LANE_CMD="$CAPTURE_PIPE && $LANE_CMD"
  fi
  # Retain this pane before starting any author, including one that exits immediately.
  # Setting the option after new-window races the author and can lose exit evidence.
  printf -v RETAIN_PANE 'tmux -L %q set-option -w -t "$TMUX_PANE" remain-on-exit on' \
    "$FACTORY_TMUX_SOCKET"
  LANE_CMD="$RETAIN_PANE && $LANE_CMD"
  # Keep host-side observation on the launcher's tmux server. Do not pass this
  # control address through the author's deliberately scrubbed environment.
  WATCH_ENV_PREFIX="$ENV_PREFIX"
  if [ -n "${TMUX:-}" ]; then
    printf -v WATCH_TMUX ' TMUX=%q' "$TMUX"
    WATCH_ENV_PREFIX+="$WATCH_TMUX"
  fi
  printf -v WATCH_CMD '%s %q %q lane --root %q --slot %q' \
    "$WATCH_ENV_PREFIX" "$FACTORY_PYTHON" "$D/lane_watchdog.py" "$ROOT" "$SLOT"

  mark() {
    "$FACTORY_PYTHON" - "$PLANNED" "$1" <<'PY'
import datetime, json, sys
row = json.loads(sys.argv[1]); row["status"] = sys.argv[2]
row["ts"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
print(json.dumps(row, sort_keys=True, separators=(",", ":")))
PY
  }
  # A wrap-up notice is a lane message the watcher starts on its own, and the watcher's scrubbed
  # environment cannot load the run's context. Record, owner-only, the environment this launch
  # ran under for the names tmux_lane_message.sh reads; without the record a lane keeps the plain
  # cap stop.
  if [ "$WRAP_S" != 0 ]; then
    DELIVERY_ENV="$TMUX_ROOT/$SLOT-delivery.env"
    ( umask 077; : > "$DELIVERY_ENV"
      for NAME in FACTORY_PYTHON FACTORY_RUNS_DIR \
        HOME USER PATH LANG TERM SHELL TMPDIR CODEX_HOME TMUX; do
        VALUE="${!NAME:-}"
        [ -n "$VALUE" ] && [[ "$VALUE" != *$'\n'* ]] && printf '%s=%s\n' "$NAME" "$VALUE" >> "$DELIVERY_ENV"
      done; true )
  fi
  # A finished round's window (and its watcher's) is replaced; a running one was refused above.
  # A round its watcher had paused for review, or was wrapping up, ends as superseded.
  "$FACTORY_PYTHON" "$D/lane_watchdog.py" close --root "$ROOT" --lane "$LANE" --by "$SLOT" || \
    refuse 70 "prior round stop/exit is unverified; refusing replacement"
  if printf '%s\n' "$WINDOWS" | grep -qx "$LANE 1"; then
    WID=$(window_id "$LANE"); [ -z "$WID" ] || tmux kill-window -t "$WID" >/dev/null 2>&1 || true
  fi
  if printf '%s\n' "$WINDOWS" | grep -q "^watch-$LANE "; then
    WID=$(window_id "watch-$LANE"); [ -z "$WID" ] || tmux kill-window -t "$WID" >/dev/null 2>&1 || true
  fi
  if ! tmux new-window -t "$RUN" -n "$LANE" -c "$REPOSITORY" "$LANE_CMD"; then
    append_event "$(mark launch-failed)"
    refuse 70 "failed to launch $RUN:$LANE"
  fi
  # Completion and stall observation are required even when a plan lane has no cap.
  if ! tmux new-window -d -t "$RUN" -n "watch-$LANE" -c "$ROOT" "$WATCH_CMD"; then
    WID=$(window_id "$LANE"); [ -z "$WID" ] || tmux kill-window -t "$WID" >/dev/null 2>&1 || true
    append_event "$(mark launch-failed)"
    refuse 70 "the watcher for $RUN:$LANE could not start, so the lane was stopped: a lane with no watcher is not launched"
  fi
  if [ "$ACTION" = launch ]; then
    # A lane that cannot hold its seat does not run: stop it and its watcher. The seat is
    # confirmed before the launch is recorded active, so a running lane always holds its seat.
    if ! "$FACTORY_PYTHON" "$D/lane_agent.py" seat confirm --root "$TMUX_ROOT" --role "$ROLE" \
      --slot "$SLOT"; then
      for W in "$LANE" "watch-$LANE"; do
        WID=$(window_id "$W"); [ -z "$WID" ] || tmux kill-window -t "$WID" >/dev/null 2>&1 || true
      done
      append_event "$(mark launch-failed)"
      refuse 70 "the $ROLE seat could not be confirmed for $SLOT, so the lane was stopped"
    fi
  fi
  append_event "$(mark active)"
  echo "tmux-lane: launched $AGENT${MODEL:+ ($MODEL)} in $RUN:$LANE as $SLOT"
  if [ "$MEMORY_ISOLATION" = beside-operator ]; then
    echo "tmux-lane: memory: the operator's own settings and MCP servers are intact and the lane's Kindex is added beside them. Any memory server the operator configured is visible to this lane, so Coder/Tester independence rests on their separate inputs and clones, not on memory."
  fi
  [ -z "$NATIVE_BLOCKER" ] || echo "tmux-lane: open for this provider: $NATIVE_BLOCKER"
  [ -z "$START_REQUIRES" ] || echo "tmux-lane: this start asks you: $START_REQUIRES"
  case "$AGENT" in claude|claude-ollama)
    if [ "${FACTORY_LANE_SANDBOX:-on}" = off ]; then
      echo "tmux-lane: permissions: you selected FACTORY_LANE_SANDBOX=off, so Claude Code is given --dangerously-skip-permissions (not when your launcher is your own shell function, which decides for itself)."
    else
      echo "tmux-lane: permissions: yours. The Factory passes Claude Code no permission mode; your launcher and your Claude Code settings decide what this lane may do without asking. If they ask, the question waits in the lane's window."
    fi ;;
  esac
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
FREEZE_LAUNCH=$("$FACTORY_PYTHON" - "$EVENTS" "$RUN" "$ROLE" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
run, role = sys.argv[2:]
rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
active = [row for row in rows if row.get("status") == "active"]
if len(active) != 1 or active[0].get("run_id") != run or active[0].get("role") != role:
    raise SystemExit("tmux-lane: retained active launch record is missing or ambiguous")
mode = active[0].get("mode")
if mode not in ("qualify", "launch"):
    raise SystemExit("tmux-lane: retained launch mode is missing or invalid")
print(mode + (":persistent" if active[0].get("persistent") else ""))
print(active[0]["repository"])
PY
) || exit $?
LAUNCH_MODE=${FREEZE_LAUNCH%%$'\n'*}
REPOSITORY=${FREEZE_LAUNCH#*$'\n'}
# A qualification probe is frozen once its process has ended (checked below), resident or not.
if [ "$LAUNCH_MODE" = launch:persistent ]; then
  echo "tmux-lane: validate a resident author's committed SHA with endgame.sh; do not freeze its live working tree" >&2
  exit 64
fi
"$FACTORY_PYTHON" "$D/lane_dialogue.py" require-clear --root "$ROOT" \
  --lane "$LANE" >/dev/null || {
  echo "tmux-lane: unanswered $LANE question must be answered before freeze" >&2
  exit 70
}
# By exact window name: a missing window resolves to the session's active pane (#69).
if tmux list-windows -t "$RUN" -F '#{window_name}' 2>/dev/null | grep -xF "$LANE" >/dev/null; then
  PANE_DEAD=$(tmux display-message -p -t "$RUN:$LANE" '#{pane_dead}' 2>/dev/null || true)
else
  PANE_DEAD=1
fi
[ "$PANE_DEAD" = "1" ] || {
  echo "tmux-lane: $RUN:$LANE is not quiescent; stop the agent before freezing" >&2
  exit 70
}
# A native session removes the project files it wrote when it ends. One that was killed
# could not; remove exactly those files (by the lane's own record, unchanged bytes only)
# before the tree is exported, so no candidate carries a lane's hook or MCP file.
for MANIFEST in "$TMUX_ROOT/$SLOT-agent-home/$SLOT-project-files.json" \
    "$REPOSITORY/.git/factory-runtime/$SLOT/agent/$SLOT-project-files.json"; do
  [ ! -f "$MANIFEST" ] || [ -L "$MANIFEST" ] || {
    LEFT=$("$FACTORY_PYTHON" "$D/native_session.py" clean-project --manifest "$MANIFEST") || exit 70
    [ -z "$LEFT" ] || {
      echo "tmux-lane: a file this lane wrote changed afterwards and was left in place; inspect it before freezing: $LEFT" >&2
      exit 70
    }
  }
done

EXPORT=$(PYTHONPATH="$REPO_ROOT" "$FACTORY_PYTHON" "$REPO_ROOT/harness/lane_repository.py" freeze \
  --source "$REPOSITORY" --store "$TMUX_ROOT/snapshots" --durable-through "$ROOT" \
  --projection-conf "${HARNESS_PROJECTION_CONF:-$FACTORY_WORKDIR/.factory/projection.conf}") || exit $?
PLAN_GAPS=null
FROZEN=$("$FACTORY_PYTHON" - "$RUN" "$ROLE" "$EXPORT" "$SLOT" "$PLAN_GAPS" "$LAUNCH_MODE" <<'PY'
import datetime, json, sys
run, role, export, slot, traceability_gaps, mode = sys.argv[1:]
print(json.dumps({
    "mode": mode,
    "traceability_gaps": json.loads(traceability_gaps),
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
