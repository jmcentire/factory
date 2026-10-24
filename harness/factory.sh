#!/usr/bin/env bash
# Ignite coordination for an already Stage-E-authorized Factory run. Target selection belongs
# exclusively to factory_runtime; this script may consume target-state, never invent it.
set -euo pipefail
# shellcheck source=harness/factory_python.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/factory_python.sh"

RUN="${1:?usage: factory.sh <run> <verbatim-task-or-file> [--runs <path>] [--budget <usd>] [--audit-interval <min>] [--engagement interactive|scheduled|autonomous] [--question-channel <command>] [--jev-screen-calls <1..1000>]}"
TASK_IN="${2:?verbatim task text or file}"
shift 2
RUNS_ARG="${FACTORY_RUNS_DIR:-${HARNESS_DIR:-.factory}/runs}"
BUDGET=""
AUDIT_MIN="15"
ENGAGEMENT="scheduled"
QUESTION_CHANNEL=""
JEV_CALLS=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --runs) RUNS_ARG="$2"; shift 2 ;;
    --budget) BUDGET="$2"; shift 2 ;;
    --audit-interval) AUDIT_MIN="$2"; shift 2 ;;
    --engagement) ENGAGEMENT="$2"; shift 2 ;;
    --question-channel) QUESTION_CHANNEL="$2"; shift 2 ;;
    --jev-screen-calls) JEV_CALLS="$2"; shift 2 ;;
    --repo|--target-manifest|--sha)
      echo "factory: $1 is forbidden; authorize and resolve the exact target through Stage R/E" >&2
      exit 64 ;;
    *) echo "factory: unknown argument: $1" >&2; exit 64 ;;
  esac
done

D="$(cd "$(dirname "$0")" && pwd -P)"
# The seats read prompts from, and call the harness of, the checkout that ignites the run, never
# whichever checkout an installed /command or the operator's shell happens to name.
FACTORY_HOME="$(cd "$D/.." && pwd -P)"
export FACTORY_HOME
# The Orchestrator's System One screen is on in every run that has a key (founder, 2026-10-20):
# a run can no longer be launched blind by leaving the flag out. The operator's personal TypeSafe
# key may be named JMC_JEV_API_KEY. Captured lane data leaves this host only for this screen.
if [ -z "${TYPESAFE_API_KEY:-}" ] && [ -n "${JMC_JEV_API_KEY:-}" ]; then
  TYPESAFE_API_KEY="$JMC_JEV_API_KEY"; export TYPESAFE_API_KEY
fi
if [ -z "$JEV_CALLS" ] && [ -n "${TYPESAFE_API_KEY:-}" ]; then
  JEV_CALLS=1000
fi
JEV_CONFIG=$("$FACTORY_PYTHON" - "$D" "$JEV_CALLS" <<'PYJEV'
import json, os, sys
sys.path.insert(0, sys.argv[1])
from jev_screen import configuration
if not sys.argv[2]:
    print("null")
else:
    try:
        config = configuration(int(sys.argv[2]))
    except (ValueError, RuntimeError):
        raise SystemExit("factory: --jev-screen-calls must be 1..1000") from None
    if not os.environ.get("TYPESAFE_API_KEY"):
        raise SystemExit("factory: Jev selected but TYPESAFE_API_KEY is unavailable")
    print(json.dumps(config))
PYJEV
) || exit 64

# The operator chooses each seat's agent and, optionally, its model; the factory names no model.
# With no model named, each agent runs its own CLI's configured default. agy takes no model flag:
# its model is selected inside agy.
VALIDATOR_AGENT="${FACTORY_VALIDATOR_AGENT:-codex}"
ORCHESTRATOR_AGENT="${FACTORY_ORCHESTRATOR_AGENT:-agy}"
VALIDATOR_MODEL="${FACTORY_VALIDATOR_MODEL:-}"
ORCHESTRATOR_MODEL="${FACTORY_ORCHESTRATOR_MODEL:-}"
# Any registered harness (harness/agents.py) may hold either resident seat; ollama is Codex on a
# local model and stays a Validator option.
# kickoff: the session that started the run is the Validator (the founder's default, 2026-10-17).
# No second Validator is launched; the validator window mirrors that session's transcript and
# queues what the factory types into it in the Validator's inbox (harness/validator_mirror.py).
case "$VALIDATOR_AGENT" in
  codex|ollama|claude|claude-ollama|agy|muse|cursor-agent|prime-agent) ;;
  kickoff)
    VALIDATOR_TRANSCRIPT="${FACTORY_VALIDATOR_TRANSCRIPT:-}"
    if [ -z "$VALIDATOR_TRANSCRIPT" ] || [ -L "$VALIDATOR_TRANSCRIPT" ] || [ ! -f "$VALIDATOR_TRANSCRIPT" ]; then
      echo "factory: a kickoff Validator needs FACTORY_VALIDATOR_TRANSCRIPT, the kickoff session's transcript file" >&2
      exit 64
    fi
    VALIDATOR_TRANSCRIPT="$(cd "$(dirname "$VALIDATOR_TRANSCRIPT")" && pwd -P)/$(basename "$VALIDATOR_TRANSCRIPT")"
    ;;
  *) echo "factory: unknown validator agent '$VALIDATOR_AGENT' (kickoff|codex|ollama|claude|claude-ollama|agy|muse|cursor-agent|prime-agent)" >&2; exit 64 ;;
esac
case "$ORCHESTRATOR_AGENT" in
  agy|codex|claude|claude-ollama|muse|cursor-agent|prime-agent) ;;
  *) echo "factory: unsupported orchestrator agent '$ORCHESTRATOR_AGENT' (agy|codex|claude|claude-ollama|muse|cursor-agent|prime-agent)" >&2; exit 64 ;;
esac
# Seats run on the operator's signed-in plan, never a per-use API key (founder direction
# 2026-10-16), unless the operator sets FACTORY_SEAT_BILLING=metered. Effort is optional per seat.
SEAT_BILLING="${FACTORY_SEAT_BILLING:-plan}"
case "$SEAT_BILLING" in plan|metered) ;; *) echo "factory: FACTORY_SEAT_BILLING must be plan or metered" >&2; exit 64 ;; esac
VALIDATOR_EFFORT="${FACTORY_VALIDATOR_EFFORT:-}"
ORCHESTRATOR_EFFORT="${FACTORY_ORCHESTRATOR_EFFORT:-}"
for SEAT_EFFORT in "$VALIDATOR_EFFORT" "$ORCHESTRATOR_EFFORT"; do
  case "$SEAT_EFFORT" in ""|minimal|low|medium|high|xhigh|max) ;;
    *) echo "factory: seat effort must be minimal|low|medium|high|xhigh|max" >&2; exit 64 ;;
  esac
done
for SEAT_AGENT in "$VALIDATOR_AGENT" "$ORCHESTRATOR_AGENT"; do
  if [ "$SEAT_AGENT" = claude-ollama ]; then
    [ "$SEAT_AGENT" != "$VALIDATOR_AGENT" ] || [ -n "$VALIDATOR_MODEL" ] || {
      echo "factory: a claude-ollama Validator needs FACTORY_VALIDATOR_MODEL (e.g. glm-5.3:cloud)" >&2; exit 64; }
    [ "$SEAT_AGENT" != "$ORCHESTRATOR_AGENT" ] || [ -n "$ORCHESTRATOR_MODEL" ] || {
      echo "factory: a claude-ollama Orchestrator needs FACTORY_ORCHESTRATOR_MODEL (e.g. glm-5.3:cloud)" >&2; exit 64; }
  fi
done
# The flags that carry a seat's effort, by harness.
seat_effort_args() {  # <agent> <effort>
  [ -n "$2" ] || return 0
  case "$1" in
    claude|claude-ollama|agy) printf -- '--effort %s ' "$2" ;;
    muse) printf -- '--reasoning-effort %s ' "$2" ;;
    codex|ollama) printf -- '-c model_reasoning_effort=%s ' "$2" ;;
    prime-agent) printf -- '--thinking %s ' "$2" ;;
    *) ;;  # cursor-agent names the effort in the model
  esac
}
# A plan seat's command never sees the variables that would bill the API.
seat_unset_args() {  # <agent>
  [ "$SEAT_BILLING" = plan ] || return 0
  [ "$1" != ollama ] || set -- codex  # `ollama launch codex` is Codex on the local server
  local name
  while IFS= read -r name; do
    [ -n "$name" ] && printf -- '-u %s ' "$name"
  done < <("$FACTORY_PYTHON" "$D/agents.py" api-env --agent "$1")
}

if [ "$VALIDATOR_AGENT" = "ollama" ]; then
  # shellcheck source=harness/model_availability.sh
  source "$D/model_availability.sh"
  VALIDATOR_MODEL="${FACTORY_VALIDATOR_OLLAMA_MODEL:-$VALIDATOR_MODEL}"
  factory_require_ollama_model factory FACTORY_VALIDATOR_OLLAMA_MODEL "$VALIDATOR_MODEL" || exit $?
fi
FACTORY_CLI="${FACTORY_CLI:-factory}"
# shellcheck source=harness/run_context.sh
source "$D/run_context.sh"
factory_load_context "$RUN" "$RUNS_ARG"
ROOT="$FACTORY_CONTROL_ROOT"

case "$FACTORY_RUN_STATE" in
  promoted|blocked)
    echo "factory: run $RUN is $FACTORY_RUN_STATE and cannot be ignited" >&2
    exit 66 ;;
esac
# One ignition per run at a time. The lock is on fd 9's open file description, so it is held
# until this shell exits (a crash included) and never outlives it; tmux is started with fd 9
# closed so the tmux server cannot inherit it. Every check and write below happens under it,
# so the cleanup on a failed attempt can only ever remove this attempt's own files.
exec 9>>"$ROOT/ignition.lock"
python3 -c 'import fcntl; fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)' 2>/dev/null || {
  echo "factory: another ignition of $RUN is in progress" >&2
  exit 75
}
[ ! -e "$FACTORY_HARNESS_META" ] && [ ! -L "$FACTORY_HARNESS_META" ] || {
  echo "factory: harness metadata already exists for $RUN" >&2
  exit 65
}
tmux has-session -t "$RUN" 2>/dev/null && {
  echo "factory: tmux session '$RUN' already exists; it is not adopted" >&2
  exit 65
}
# Each attempt records its own tmux-session resource: a terminal record cannot return to
# planned, so a retry after an attempt that removed its session (abandoned, residue=false)
# takes the next id. Any other earlier status is unresolved residue and is refused.
SESSION_RESOURCE=$($FACTORY_CLI verify-resources --runs "$FACTORY_RUNS_ROOT" --run-id "$RUN" |
  python3 -c '
import json, sys
attempts = {
    rid: r.get("status") for rid, r in json.load(sys.stdin).get("resources", {}).items()
    if r.get("resource_type") == "tmux-session"
}
open_ = sorted(f"{rid}={status}" for rid, status in attempts.items() if status != "abandoned")
if open_:
    raise SystemExit("factory: an earlier ignition left " + ", ".join(open_) +
                     "; disposition it before retrying")
print("tmux-session" if not attempts else f"tmux-session-{len(attempts) + 1}")
') || exit 65
IGNITION_FILES=""
RESOURCE_STATE=""
cleanup_ignition() {
  # An attempt that failed anywhere between planning its session and settling it is closed
  # here, so its resource row never refuses the next retry as still planned.
  if [ "$RESOURCE_STATE" = planned ]; then
    if tmux has-session -t "$RUN" 2>/dev/null; then
      resource_event '{"reason":"ignition failed after the session was created","residue":true}' failed || true
    else
      resource_event '{"reason":"ignition failed before the session was created","residue":false}' abandoned || true
    fi
  fi
  rm -f "$TASK_TMP"
  # A failed attempt leaves nothing that refuses the next one (exit 65 "already exists").
  [ -z "$IGNITION_FILES" ] || rm -f "$ROOT/harness.json" "$ROOT/TASK.md"
}

REQUEST="$ROOT/evidence/intake/execution-request.json"
[ -f "$REQUEST" ] && [ ! -L "$REQUEST" ] || {
  echo "factory: retained Stage-E execution request is missing or a symlink: $REQUEST" >&2
  exit 66
}

TASK_TMP="$(mktemp "${TMPDIR:-/tmp}/factory-task.XXXXXX")"
trap cleanup_ignition EXIT
if [ -f "$TASK_IN" ]; then
  cp "$TASK_IN" "$TASK_TMP"
else
  printf '%s' "$TASK_IN" > "$TASK_TMP"
fi
TASK_DIGEST=$(python3 - "$TASK_TMP" "$REQUEST" "$FACTORY_SOURCE_DIGEST" <<'PY'
import hashlib, json, pathlib, sys
task_path = pathlib.Path(sys.argv[1])
request_path = pathlib.Path(sys.argv[2])
source_digest = sys.argv[3]
raw = task_path.read_bytes()
request = json.loads(request_path.read_text(encoding="utf-8"))
expected = request.get("verbatim_request")
digest = "sha256:" + hashlib.sha256(raw).hexdigest()
if not isinstance(expected, str) or raw != expected.encode("utf-8"):
    raise SystemExit("factory: supplied task bytes differ from the retained Stage-E request")
if request.get("verbatim_request_digest") != digest or source_digest != digest:
    raise SystemExit("factory: supplied task digest differs from Stage-E authority")
print(digest)
PY
) || exit $?

# Per-run guidance is selected only through the exact configuration vector which
# verify-resume-checkpoint just accepted. The reserved source name identifies the
# selector; its bytes and every selected document were already checkpoint-bound.
# This admission step freezes those exact bytes into the run before metadata can
# name them. No ambient path or command-line attachment can add guidance.
GUIDANCE_CONFIG_ARGS=()
for ((index = 0; index < ${#FACTORY_VERIFIED_RESUME_CONFIG_ARGS[@]}; index += 2)); do
  [ "${FACTORY_VERIFIED_RESUME_CONFIG_ARGS[$index]}" = "--config-source" ] || exit 72
  GUIDANCE_CONFIG_ARGS+=(
    --config-source "${FACTORY_VERIFIED_RESUME_CONFIG_ARGS[$((index + 1))]}"
  )
done
GUIDANCE_ADMISSION=$(python3 "$D/run_guidance.py" admit --root "$ROOT" \
  --run-id "$RUN" --generation "$FACTORY_GENERATION" \
  "${GUIDANCE_CONFIG_ARGS[@]+"${GUIDANCE_CONFIG_ARGS[@]}"}" \
  "${FACTORY_VERIFIED_RESUME_CONFIG_DIGEST_ARGS[@]+"${FACTORY_VERIFIED_RESUME_CONFIG_DIGEST_ARGS[@]}"}") || exit $?

case "$ORCHESTRATOR_AGENT" in
  agy)
    ORCHESTRATOR_VERSION=$(agy --version 2>/dev/null) || {
      echo "factory: agy CLI is not runnable" >&2; exit 70;
    }
    ORCHESTRATOR_HELP=$(agy --help 2>&1) || exit 70
    REQUIRED_FLAGS="--new-project --prompt-interactive --dangerously-skip-permissions --add-dir"
    [ -z "$ORCHESTRATOR_MODEL" ] || REQUIRED_FLAGS="$REQUIRED_FLAGS --model"
    [ -z "$ORCHESTRATOR_EFFORT" ] || REQUIRED_FLAGS="$REQUIRED_FLAGS --effort"
    for REQUIRED in $REQUIRED_FLAGS; do
      printf '%s' "$ORCHESTRATOR_HELP" | grep -Eq -- "(^|[^[:alnum:]_-])$REQUIRED([^[:alnum:]_-]|$)" || {
        echo "factory: agy CLI contract lacks $REQUIRED ($ORCHESTRATOR_VERSION)" >&2
        exit 70
      }
    done
    ORCHESTRATOR_CLI_CONTRACT="agy-resident-new-project-unsandboxed-v2"
    ;;
  codex)
    ORCHESTRATOR_VERSION=$(codex --version 2>/dev/null) || {
      echo "factory: codex CLI is not runnable" >&2; exit 70;
    }
    ORCHESTRATOR_HELP=$(codex --help 2>/dev/null) || exit 70
    REQUIRED_FLAGS="--no-alt-screen --add-dir"
    [ -z "$ORCHESTRATOR_MODEL" ] || REQUIRED_FLAGS="$REQUIRED_FLAGS --model"
    for REQUIRED in $REQUIRED_FLAGS; do
      printf '%s' "$ORCHESTRATOR_HELP" | grep -q -- "$REQUIRED" || {
        echo "factory: codex CLI contract lacks $REQUIRED ($ORCHESTRATOR_VERSION)" >&2
        exit 70
      }
    done
    ORCHESTRATOR_CLI_CONTRACT="codex-resident-tui-v1"
    ;;
  claude|claude-ollama|muse|cursor-agent|prime-agent)
    case "$ORCHESTRATOR_AGENT" in
      claude-ollama) ORCHESTRATOR_EXECUTABLE=claude ;;
      cursor-agent) ORCHESTRATOR_EXECUTABLE=agent ;;
      *) ORCHESTRATOR_EXECUTABLE="$ORCHESTRATOR_AGENT" ;;
    esac
    ORCHESTRATOR_VERSION=$("$ORCHESTRATOR_EXECUTABLE" --version 2>/dev/null | head -1) || {
      echo "factory: $ORCHESTRATOR_AGENT CLI is not runnable" >&2; exit 70;
    }
    ORCHESTRATOR_CLI_CONTRACT="$ORCHESTRATOR_AGENT-resident-tui-v1"
    ;;
esac

# No harness metadata exists yet, so a file here can only be left by an ignition that
# failed, and is replaced.
if [ "$VALIDATOR_AGENT" = kickoff ]; then
  "$FACTORY_PYTHON" "$D/seat_supervisor.py" bind-validator --root "$ROOT" --session "$RUN" \
    --pid "${FACTORY_VALIDATOR_PID:?kickoff needs its executing Validator PID}" \
    --started "${FACTORY_VALIDATOR_STARTED:?kickoff needs its observed UTC ps lstart identity}" \
    --pane "${FACTORY_VALIDATOR_PANE:?kickoff needs its exact execution pane ID}" || exit 70
fi
IGNITION_FILES=1
python3 - "$TASK_TMP" "$ROOT/TASK.md" "$FACTORY_HARNESS_META" "$RUN" \
  "$BUDGET" "$AUDIT_MIN" "$ENGAGEMENT" "$QUESTION_CHANNEL" "$TASK_DIGEST" "$FACTORY_TARGET_STATE_DIGEST" \
  "$FACTORY_TARGET_MANIFEST_DIGEST" "$FACTORY_BASE_COMMIT" "$FACTORY_CHECKOUT_ID" \
  "$VALIDATOR_AGENT" "$ORCHESTRATOR_AGENT" "$ORCHESTRATOR_VERSION" \
  "$ORCHESTRATOR_CLI_CONTRACT" "$FACTORY_GENERATION" "$GUIDANCE_ADMISSION" "$JEV_CONFIG" <<'PY'
import datetime, json, os, pathlib, sys, tempfile
(
    task_source, task_dest, metadata_path, run, budget, audit, engagement, question_channel, task_digest,
    target_state_digest, manifest_digest, commit, checkout_id, validator_agent,
    orchestrator_agent, orchestrator_version, orchestrator_cli_contract,
    generation_text, guidance_admission_raw, jev_config_raw,
) = sys.argv[1:]
audit_value = int(audit)
if audit_value < 1:
    raise SystemExit("factory: audit interval must be positive")
# How much of the human this run gets. Refused, never guessed.
if engagement not in {"interactive", "scheduled", "autonomous"}:
    raise SystemExit(
        "factory: engagement must be interactive, scheduled, or autonomous"
    )
if engagement == "scheduled" and not question_channel:
    # Scheduled sends only the exceptional question. With no channel there is
    # nowhere to send it, so it is recorded and reported instead — said out
    # loud here, because a silent downgrade to autonomous is the failure.
    print(
        "factory: engagement scheduled with no --question-channel: exceptional "
        "questions will be recorded in the run and reported, never sent",
        file=sys.stderr,
    )
budget_value = None if budget == "" else float(budget)
if budget_value is not None and budget_value <= 0:
    raise SystemExit("factory: budget must be positive")
task_bytes = pathlib.Path(task_source).read_bytes()
task_path = pathlib.Path(task_dest)
guidance = json.loads(guidance_admission_raw)
if set(guidance) != {
    "required", "schema_version", "selection_digest", "source_digests", "state"
} or guidance["schema_version"] != "factory-run-guidance/1":
    raise SystemExit("factory: guidance admission summary is malformed")

def sync_parent(path: pathlib.Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(path.parent, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)

if task_path.exists() or task_path.is_symlink():
    raise SystemExit(f"factory: refusing existing task artifact: {task_path}")
fd = os.open(task_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
with os.fdopen(fd, "wb") as stream:
    stream.write(task_bytes)
    stream.flush()
    os.fsync(stream.fileno())
sync_parent(task_path)
metadata = {
    "schema_version": "factory-harness/2",
    "run_id": run,
    "status": "open",
    "task_digest": task_digest,
    "target_state_digest": target_state_digest,
    "target_manifest_digest": manifest_digest,
    "resolved_commit": commit,
    "checkout_id": checkout_id,
    "budget_usd": budget_value,
    "budget_enforcement": (
        "reserved-runner-ceilings" if budget_value is not None else "not-requested"
    ),
    "audit_interval_min": audit_value,
    "engagement": engagement,
    "question_channel": question_channel or None,
    "promise_window_min": 10,
    "launcher_qualification": "QUALIFIED_PR2",
    "lane_isolation": "QUALIFIED_PR2",
    "interactive_validator_boundary": "operator-owned-tmux",
    "validator_agent": validator_agent,
    # The Orchestrator's code-and-progress review (assessment/5) is owed every 2nd cadence tick
    # and at every slice boundary (founder direction 2026-10-16; harness/orchestrator_channel.py).
    "progress_review": {"every_cadence_ticks": 2},
    "orchestrator_agent": orchestrator_agent,
    "orchestrator_cli_version": orchestrator_version,
    "orchestrator_cli_contract": orchestrator_cli_contract,
    "orchestrator_mode": "resident-monitoring",
    "jev_screen": json.loads(jev_config_raw),
    "orchestrator_window": "orchestrator",
    "orchestrator_visibility": "bounded-sampled-pane-snapshots-plus-cadence",
    "orchestrator_effects": "monotone-block-or-no-op",
    "orchestrator_boundary": "operator-owned-tmux-unqualified",
    "agreement_contract_version": "factory-agreement-contract/1",
    "agreement_requirement_region_families": (
        ["authored-product", "run-guidance"]
        if guidance["required"]
        else ["authored-product"]
    ),
    "guidance_contract_version": "factory-run-guidance/1",
    "guidance_generation": int(generation_text),
    "guidance_state": guidance["state"],
    "guidance_selection_digest": guidance["selection_digest"],
    "guidance_source_digests": guidance["source_digests"],
    "validator_contract": "docs/VALIDATION-DIRECTIVE.md + /validate",
    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
}
path = pathlib.Path(metadata_path)
tmp = tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8")
try:
    json.dump(metadata, tmp, indent=2, sort_keys=True)
    tmp.write("\n")
    tmp.flush()
    os.fsync(tmp.fileno())
    tmp.close()
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    os.link(tmp.name, path)
    os.unlink(tmp.name)
    sync_parent(path)
except BaseException:
    tmp.close()
    if os.path.exists(tmp.name):
        os.unlink(tmp.name)
    raise
PY

echo "== grounding against immutable target-state =="
FACTORY_RUNS_DIR="$FACTORY_RUNS_ROOT" HARNESS_RUN_ROOT="$ROOT" \
  "$D/ground.sh" --run "$RUN" --runs "$FACTORY_RUNS_ROOT"

resource_event() {
  factory_record_resource --runs "$FACTORY_RUNS_ROOT" --run-id "$RUN" \
    --resource-id "$SESSION_RESOURCE" --resource-type tmux-session --identifier "$RUN" \
    --creator-action harness-ignition --ownership run-owned \
    --baseline-json '{"absent_at_plan":true}' --disposition-json "$1" \
    --evidence-json "{\"target-state\":\"$FACTORY_TARGET_STATE_DIGEST\"}" \
    --status "$2" --actor harness-ignition >/dev/null || return $?
  # Callers use `|| true`, which suspends set -e in here, so a failed write must not mark the
  # state recorded; the EXIT trap then still closes a planned attempt.
  RESOURCE_STATE="$2"
}
resource_event '{}' planned

SEAT_ENV="$(factory_seat_env)"
printf -v CTL_CMD 'exec env %s %q %q --run %q --root %q' \
  "$SEAT_ENV" "$FACTORY_PYTHON" "$D/dispatcher.py" "$RUN" "$ROOT"
VALIDATOR_PROMPT="Act as the Validator under $FACTORY_HOME/docs/VALIDATION-DIRECTIVE.md and the /validate contract ($FACTORY_HOME/prompts/validate.md); FACTORY_HOME=$FACTORY_HOME, and the harness is $D. The verbatim task is in $ROOT/TASK.md and is bound by the Stage-E execution receipt. Re-derive the checked run projection before acting. Derive sufficiently deep product, architecture, and testing/monitoring artifacts from the founder's words in TASK.md and decide what they leave open, recording each decision and its basis; the founder is involved only at a genuine impasse (no role in the run knows what needs doing), never asked to approve, confirm or ratify what they already asked for; launch model lanes only through the qualified harness/dispatch_lane.sh runner and typed broker. This interactive Validator window is operator-owned coordination, not a qualified model lane or a billed runner receipt."
VALIDATOR_PROMPT+=" If harness.json names selected run guidance, inspect its exact retained sources, classify and apply every obligation, obtain an independent classification/application review, and run phase_compiler.py so the generated regions enter only the proper ratified authorities. Treat routing as routing rather than compliance; collect exact-candidate evidence before verdict."
VALIDATOR_PROMPT+=" Before drafting Phase A, snapshot the pinned target's own rule files with python3 $D/repo_rules.py snapshot and read them all; cite every touched rule by path and id in the Product Specification, quote those rules verbatim into both lane briefs (the lanes cannot see the freeze-excluded guidance files), decide any needed rule exception yourself and record the rule, the case and the basis, and state per touched rule in the PR body why the change complies."
VALIDATOR_PROMPT+=" Every Coder lane on a resumable harness pauses every 18 minutes of work and wakes you with lane-review-due: freeze it, review the frozen tree against the ratified specification, and resume it within minutes with tmux_lane_message.sh review (requirement ids and observed behaviour, never the tests); review each Tester lane on the same cadence for oracle quality and coverage against the testing strategy, never against the code; at every slice boundary and at least every 60 minutes write an evidenced debrief to artifacts/debriefs/ (harness/validator_debrief.py check; overdue says when one is owed); send findings after a judge into the Coder's own thread the same way, and reproduce a Tester FAIL from the implementation's own outputs before you route it."
ORCHESTRATOR_PROMPT="You are the resident strategic Orchestrator for Factory run $RUN. Read $ROOT/orchestrator/ROLE.md, $ROOT/TASK.md, and the retained run record before acting. Stay alive in this interactive session: never conclude that one turn ends your job. Use Kindex natively through its MCP tools at startup and on every material trajectory check to recover the user's ongoing goal, prior corrections, and relevant implications; the user's current inputs remain authority. The dispatcher will send FACTORY_ACTIVITY cursor ranges to this pane. Consume every journal row in each range, without selecting only anomaly-looking rows. Monitor the conversation for the user's ultimate goal, classify whether recent input overrides, refines, intensifies, or merely sits aside from that goal, decide whether the present direction advances it, project what happens if the action continues, and identify implications and side effects and whether they are desirable. Before decomposing, inventory explicit and ratified requirements separately from implicit assumptions and inherited code behavior; expose any one requirement or interaction that drives disproportionate complexity, state the simpler path and the counterfactual planning-mode/model-tier/boundary/dependency/chunk delta, and either cite why it is fixed or decide the simpler path, record the decision and its basis, and hand it to the Validator; the founder is involved only at a genuine impasse, when no role in the run knows what needs doing. Only then classify task complexity and latent ambiguity, select a direct/clarify/decompose/deep planning mode, break necessary work into concrete chunks, and recommend the least expensive qualified model tier capable of each chunk; reserve top-tier models for genuinely hard work and state why. Run the check-in loop in ROLE.md on every cadence row: check in on the Validator, Coder, and Tester and answer the every-tick check-in questions, and the boundary questions at every slice boundary, dispatch, verdict, and promote. Treat every cross-boundary identifier (principal, role, grant, table, metric or resource type, log field, token audience, env var, service or topic name) as unverified until $ROOT/artifacts/boundary-register.json records it on both sides: read $ROOT/orchestrator/BOUNDARIES.md, run $ROOT/orchestrator/bin/boundary_register.py verify --root $ROOT --workdir <the run's target workdir> whenever lanes touch a boundary, and block on any identifier a lane uses that the register lacks or names differently, or on access presumed without a record. Also audit Factory rule adherence and keep $ROOT/orchestrator/OUTSTANDING-WORK.md current as the plan, the task list, and the Validator's reminders; it is printed to the Validator at every checkpoint. Record every conclusion using $ROOT/orchestrator/bin/orchestrator_channel.py and the closed assessment shape in ROLE.md. You watch the Validator, Coder, and Tester consoles through every journal row, and you hold authority over all three, including the Validator. Your machine effects are block or no-op: a block gates the Validator's next transition until the Validator disposes of it, and nothing stops the run or waits on the founder. Block when the Validator ignores your adherence calls, picks up a pen, or acts as if it owns the run, and say exactly what it must fix. You can never grant or advance a transition. Raw pane injection remains forbidden, but you and the Validator may use $D/tmux_lane_message.sh status to poke a tmux Codex author through its typed session channel; only the Validator may bind and deliver a specification answer. Never call a run closed unless harness.json already says closed through Gate L. tmux is a coordination surface, not an isolation or evidence boundary."
ORCHESTRATOR_PROMPT+=" Independently audit any selected run-guidance source, classification/application, generated authority routing, N/A basis, and exact-candidate evidence. The channel derives and binds your assessment to that state: routing-verified is not compliance, noncompliance blocks, dispatch requires routing, and verdict requires evidence-complete."
# A model the operator named goes on the command line. Without one these stay empty and each
# agent runs its own configured default.
VALIDATOR_MODEL_ARGS=""
case "$VALIDATOR_AGENT" in
  codex|claude|claude-ollama|agy|muse|cursor-agent|prime-agent)
    [ -z "$VALIDATOR_MODEL" ] || printf -v VALIDATOR_MODEL_ARGS -- '--model %q ' "$VALIDATOR_MODEL" ;;
esac
VALIDATOR_UNSET=""
if [ "$VALIDATOR_AGENT" != kickoff ]; then
  VALIDATOR_MODEL_ARGS+="$(seat_effort_args "$VALIDATOR_AGENT" "$VALIDATOR_EFFORT")"
  VALIDATOR_UNSET="$(seat_unset_args "$VALIDATOR_AGENT")"
fi
ORCHESTRATOR_MODEL_ARGS=""
if [ -n "$ORCHESTRATOR_MODEL" ]; then
  printf -v ORCHESTRATOR_MODEL_ARGS -- '--model %q ' "$ORCHESTRATOR_MODEL"
fi
ORCHESTRATOR_MODEL_ARGS+="$(seat_effort_args "$ORCHESTRATOR_AGENT" "$ORCHESTRATOR_EFFORT")"
# On a host whose kernel cannot give Codex's sandbox a namespace, the operator sets
# FACTORY_LANE_SANDBOX=off and the container is the sandbox (harness/tmux_lane.sh, same switch).
CODEX_SANDBOX_FLAGS="--sandbox workspace-write --ask-for-approval never"
[ "${FACTORY_LANE_SANDBOX:-on}" != off ] || CODEX_SANDBOX_FLAGS="--dangerously-bypass-approvals-and-sandbox"
if [ "${FACTORY_LANE_SANDBOX:-on}" = off ]; then
  # An unsandboxed seat is a fact of the run, recorded where the run's events are read, not only
  # implied by an inherited environment variable.
  "$FACTORY_PYTHON" - "$ROOT/events.jsonl" <<'PY' || exit 70
import datetime, json, os, sys
row = json.dumps({
    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "kind": "seat-sandbox-off", "class": "seat", "source": "factory.sh", "wake": False,
    "detail": "FACTORY_LANE_SANDBOX=off: Codex seats run with "
              "--dangerously-bypass-approvals-and-sandbox; the host must be the sandbox",
}, sort_keys=True, separators=(",", ":"))
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
try:
    os.write(fd, (row + "\n").encode("utf-8"))
finally:
    os.close(fd)
PY
fi
case "$VALIDATOR_AGENT" in
  kickoff)
    # The kickoff session reads its brief from the run and watches its inbox; nothing launches.
    VALIDATOR_PROMPT+=" You are the session that kicked off this run; no other Validator exists. Watch $ROOT/validator/inbox.jsonl for the whole run (each line is a notice or an Orchestrator correction the factory typed into your window) and act on every line as it arrives. While the Orchestrator has you stopped, every lane is held: apply the correction, then the Orchestrator resumes you."
    mkdir -p -m 700 "$ROOT/validator"
    ( umask 077; printf '%s\n' "$VALIDATOR_PROMPT" > "$ROOT/validator/BRIEF.md" )
    : > "$ROOT/validator/inbox.jsonl"
    chmod 600 "$ROOT/validator/inbox.jsonl"
    printf -v VALIDATOR_CMD 'exec %q %q --transcript %q --inbox %q' \
      "$FACTORY_PYTHON" "$D/validator_mirror.py" "$VALIDATOR_TRANSCRIPT" "$ROOT/validator/inbox.jsonl"
    ;;
  codex)
    printf -v VALIDATOR_CMD 'exec env %s%s codex %s %s%q' \
      "$VALIDATOR_UNSET" "$SEAT_ENV" "$CODEX_SANDBOX_FLAGS" "$VALIDATOR_MODEL_ARGS" "$VALIDATOR_PROMPT"
    ;;
  ollama)
    printf -v VALIDATOR_CMD 'exec env %s ollama launch codex --model %q -- --sandbox workspace-write --ask-for-approval never %q' \
      "$SEAT_ENV" "$VALIDATOR_MODEL" "$VALIDATOR_PROMPT"
    ;;
  claude)
    printf -v VALIDATOR_CMD 'exec env %s%s claude --permission-mode bypassPermissions %s%q' \
      "$VALIDATOR_UNSET" "$SEAT_ENV" "$VALIDATOR_MODEL_ARGS" "$VALIDATOR_PROMPT"
    ;;
  claude-ollama)
    # Claude Code on the local Ollama server: its fixed variables travel in an owner-only file.
    VALIDATOR_SEAT_ENV="$ROOT/validator-seat.env"
    "$FACTORY_PYTHON" "$D/agents.py" seat-env --agent claude-ollama --billing "$SEAT_BILLING" \
      --model "$VALIDATOR_MODEL" --env-file "$VALIDATOR_SEAT_ENV" || exit 70
    printf -v VALIDATOR_CMD 'exec env %s%s %q %q run --slot validator --log %q --exits %q --terminal --env-file %q -- claude --permission-mode bypassPermissions %s%q' \
      "$VALIDATOR_UNSET" "$SEAT_ENV" "$FACTORY_PYTHON" "$D/lane_agent.py" \
      "$ROOT/validator-lane.log" "$ROOT/validator-exits.jsonl" "$VALIDATOR_SEAT_ENV" \
      "$VALIDATOR_MODEL_ARGS" "$VALIDATOR_PROMPT"
    ;;
  agy)
    printf -v VALIDATOR_CMD 'exec env %s%s agy --dangerously-skip-permissions --add-dir %q %s--prompt-interactive %q' \
      "$VALIDATOR_UNSET" "$SEAT_ENV" "$FACTORY_WORKDIR" "$VALIDATOR_MODEL_ARGS" "$VALIDATOR_PROMPT"
    ;;
  muse)
    printf -v VALIDATOR_CMD 'exec env %s%s muse --yolo --workspace %q %s%q' \
      "$VALIDATOR_UNSET" "$SEAT_ENV" "$FACTORY_WORKDIR" "$VALIDATOR_MODEL_ARGS" "$VALIDATOR_PROMPT"
    ;;
  cursor-agent)
    printf -v VALIDATOR_CMD 'exec env %s%s agent --force --trust --approve-mcps --workspace %q %s%q' \
      "$VALIDATOR_UNSET" "$SEAT_ENV" "$FACTORY_WORKDIR" "$VALIDATOR_MODEL_ARGS" "$VALIDATOR_PROMPT"
    ;;
  prime-agent)
    printf -v VALIDATOR_CMD 'exec env %s%s prime-agent %s-- %q' \
      "$VALIDATOR_UNSET" "$SEAT_ENV" "$VALIDATOR_MODEL_ARGS" "$VALIDATOR_PROMPT"
    ;;
esac

# Prompt size is verified on delegation (founder, 2026-10-18): each seat prompt is measured
# against the one-argv-element limit its CLI receives it under, and the measurement is a run
# event. The seat prompts are short main prompts that name the files the seat reads itself.
"$FACTORY_PYTHON" - "$ROOT/events.jsonl" "$VALIDATOR_AGENT" "$VALIDATOR_PROMPT" \
  "$ORCHESTRATOR_AGENT" "$ORCHESTRATOR_PROMPT" <<'PY' || exit 70
import datetime, json, os, sys
events, *pairs = sys.argv[1:]
seats = {}
for seat, (agent, prompt) in zip(("validator", "orchestrator"), (pairs[0:2], pairs[2:4])):
    size = len(prompt.encode("utf-8"))
    seats[seat] = {"agent": agent, "prompt_bytes": size, "prompt_limit": 131071,
                   "within_limit": size <= 131071}
row = json.dumps({
    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "kind": "seat-prompt-size", "class": "seat", "source": "factory.sh", "wake": False,
    "seats": seats,
}, sort_keys=True, separators=(",", ":"))
fd = os.open(events, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
try:
    os.write(fd, (row + "\n").encode("utf-8"))
finally:
    os.close(fd)
PY
SAFE_HOME="${HOME:?factory: HOME is required to locate agent authentication}"
SAFE_USER="${USER:-$(id -un)}"
SAFE_PATH="${PATH:?factory: PATH is required}"
SAFE_TMPDIR="${TMPDIR:-/tmp}"
SAFE_TERM="${TERM:-xterm-256color}"
SAFE_SHELL="${SHELL:-/bin/bash}"
SAFE_LANG="${LANG:-en_US.UTF-8}"
case "$ORCHESTRATOR_AGENT" in
  agy)
    # Founder ruling: omit --sandbox so this seat can reach tmux and monitor the run;
    # never give the Orchestrator less reach than the Validator.
    printf -v ORCHESTRATOR_CMD 'exec env -i %s HOME=%q USER=%q PATH=%q TMPDIR=%q TERM=%q SHELL=%q LANG=%q agy --new-project --dangerously-skip-permissions --add-dir %q %s--prompt-interactive %q' \
      "$SEAT_ENV" "$SAFE_HOME" "$SAFE_USER" "$SAFE_PATH" "$SAFE_TMPDIR" "$SAFE_TERM" "$SAFE_SHELL" "$SAFE_LANG" \
      "$FACTORY_WORKDIR" "$ORCHESTRATOR_MODEL_ARGS" "$ORCHESTRATOR_PROMPT"
    ;;
  claude|claude-ollama|muse|cursor-agent|prime-agent)
    # A cleared environment admits only the harness's named variables (on a plan seat, never an
    # API key) and its fixed seat variables, through an owner-only file the lane wrapper loads,
    # so no credential appears in the tmux launch string.
    ORCH_CREDENTIALS="$ROOT/orchestrator-credentials.env"
    "$FACTORY_PYTHON" "$D/agents.py" seat-env --agent "$ORCHESTRATOR_AGENT" \
      --billing "$SEAT_BILLING" --model "$ORCHESTRATOR_MODEL" --env-file "$ORCH_CREDENTIALS" || exit 70
    case "$ORCHESTRATOR_AGENT" in
      claude|claude-ollama)
        printf -v ORCH_TAIL 'claude --permission-mode bypassPermissions %s%q' "$ORCHESTRATOR_MODEL_ARGS" "$ORCHESTRATOR_PROMPT" ;;
      muse)
        printf -v ORCH_TAIL 'muse --yolo --workspace %q %s%q' "$FACTORY_WORKDIR" "$ORCHESTRATOR_MODEL_ARGS" "$ORCHESTRATOR_PROMPT" ;;
      cursor-agent)
        printf -v ORCH_TAIL 'agent --force --trust --approve-mcps --workspace %q %s%q' "$FACTORY_WORKDIR" "$ORCHESTRATOR_MODEL_ARGS" "$ORCHESTRATOR_PROMPT" ;;
      *)
        printf -v ORCH_TAIL 'prime-agent --cwd %q %s-- %q' "$FACTORY_WORKDIR" "$ORCHESTRATOR_MODEL_ARGS" "$ORCHESTRATOR_PROMPT" ;;
    esac
    printf -v ORCHESTRATOR_CMD 'exec env -i %s HOME=%q USER=%q PATH=%q TMPDIR=%q TERM=%q SHELL=%q LANG=%q %q %q run --slot orchestrator --log %q --exits %q --terminal --env-file %q -- %s' \
      "$SEAT_ENV" "$SAFE_HOME" "$SAFE_USER" "$SAFE_PATH" "$SAFE_TMPDIR" "$SAFE_TERM" "$SAFE_SHELL" "$SAFE_LANG" \
      "$FACTORY_PYTHON" "$D/lane_agent.py" \
      "$ROOT/orchestrator-lane.log" "$ROOT/orchestrator-exits.jsonl" "$ORCH_CREDENTIALS" "$ORCH_TAIL"
    ;;
  codex)
    SAFE_CODEX_HOME="${CODEX_HOME:-$SAFE_HOME/.codex}"
    printf -v ORCHESTRATOR_CMD 'exec env -i %s HOME=%q USER=%q PATH=%q TMPDIR=%q TERM=%q SHELL=%q LANG=%q CODEX_HOME=%q codex %s --add-dir %q --no-alt-screen %s%q' \
      "$SEAT_ENV" "$SAFE_HOME" "$SAFE_USER" "$SAFE_PATH" "$SAFE_TMPDIR" "$SAFE_TERM" "$SAFE_SHELL" "$SAFE_LANG" "$SAFE_CODEX_HOME" \
      "$CODEX_SANDBOX_FLAGS" "$FACTORY_WORKDIR" "$ORCHESTRATOR_MODEL_ARGS" "$ORCHESTRATOR_PROMPT"
    ;;
esac

mkdir -p "$ROOT/orchestrator"
chmod 700 "$ROOT/orchestrator"
mkdir -p "$ROOT/orchestrator/bin"
# -f: a failed earlier attempt left these read-only; the retry replaces them.
cp -f "$D/../prompts/orchestrate.md" "$ROOT/orchestrator/ROLE.md"
cp -f "$D/../docs/practices/jev-runtime.md" "$ROOT/orchestrator/JEV.md"
cp -f "$D/../docs/practices/boundary-identifiers.md" "$ROOT/orchestrator/BOUNDARIES.md"
cp -f "$D/orchestrator_channel.py" "$D/attention_gate.py" "$D/lane_dialogue.py" \
  "$D/run_guidance.py" "$D/agreement_contract.py" "$D/jev_screen.py" "$D/jev_rules.py" \
  "$D/boundary_register.py" "$D/escalate.py" "$D/seat_supervisor.py" "$D/lane_watchdog.py" \
  "$ROOT/orchestrator/bin/"
# The Orchestrator runs these tools by their shebang. Pin it to the interpreter that started the
# run, so they never fall through to whatever `python3` the agent's PATH holds (lane_dialogue
# imports factory_core). A shebang cannot carry whitespace; such a path keeps `env python3`.
case "$FACTORY_PYTHON" in
  *[[:space:]]*) ;;
  *) for tool in "$ROOT"/orchestrator/bin/*.py; do
       python3 - "$tool" "$FACTORY_PYTHON" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
first, rest = path.read_text(encoding="utf-8").split("\n", 1)
if first.startswith("#!"):
    path.write_text("#!" + sys.argv[2] + "\n" + rest, encoding="utf-8")
PY
     done ;;
esac
chmod 400 "$ROOT/orchestrator/ROLE.md" "$ROOT/orchestrator/JEV.md" "$ROOT/orchestrator/BOUNDARIES.md"
chmod 500 "$ROOT/orchestrator/bin/orchestrator_channel.py" \
  "$ROOT/orchestrator/bin/attention_gate.py" "$ROOT/orchestrator/bin/lane_dialogue.py" \
  "$ROOT/orchestrator/bin/run_guidance.py" "$ROOT/orchestrator/bin/agreement_contract.py" \
  "$ROOT/orchestrator/bin/jev_screen.py" "$ROOT/orchestrator/bin/jev_rules.py" \
  "$ROOT/orchestrator/bin/boundary_register.py" "$ROOT/orchestrator/bin/escalate.py" \
  "$ROOT/orchestrator/bin/seat_supervisor.py" "$ROOT/orchestrator/bin/lane_watchdog.py"
if ! tmux new-session -d -s "$RUN" -n orchestrator -c "$ROOT" "$ORCHESTRATOR_CMD" 9>&-; then
  resource_event '{"reason":"tmux creation failed","residue":false}' abandoned || true
  echo "factory: failed to create tmux session" >&2
  exit 70
fi
if [ -n "$JEV_CALLS" ]; then
  # Run-scoped tmux memory only: never put the credential in a launch command,
  # prompt, receipt, or retained file. Author lanes and Orchestrator use env -i.
  tmux set-environment -t "$RUN" TYPESAFE_API_KEY "$TYPESAFE_API_KEY"
fi

if ! tmux new-window -t "$RUN" -n validator -c "$FACTORY_WORKDIR" "$VALIDATOR_CMD"; then
  if tmux kill-session -t "$RUN" 2>/dev/null; then
    resource_event '{"reason":"validator window failed; created session removed","residue":false}' abandoned || true
  else
    resource_event '{"reason":"validator window failed; session cleanup unverified","residue":true}' failed || true
  fi
  echo "factory: validator window failed; created tmux session was terminally accounted" >&2
  exit 70
fi
if ! tmux new-window -t "$RUN" -n ctl -c "$FACTORY_WORKDIR" "$CTL_CMD"; then
  if tmux kill-session -t "$RUN" 2>/dev/null; then
    resource_event '{"reason":"dispatcher window failed; created session removed","residue":false}' abandoned || true
  else
    resource_event '{"reason":"dispatcher window failed; session cleanup unverified","residue":true}' failed || true
  fi
  echo "factory: dispatcher window failed; created tmux session was terminally accounted" >&2
  exit 70
fi
# The seats work only while the Orchestrator judges them (harness/seat_supervisor.py): unassessed
# activity past the lease pauses the Validator, the Coder and the Tester, and the Orchestrator can
# correct, stop or resume any of them. It also beats Herald's heartbeat, the run's dead-man switch.
printf -v SUPERVISOR_CMD 'exec %q %q run --root %q --session %q' \
  "$FACTORY_PYTHON" "$D/seat_supervisor.py" "$ROOT" "$RUN"
if ! tmux new-window -d -t "$RUN" -n supervisor -c "$ROOT" "$SUPERVISOR_CMD"; then
  echo "factory: the seat supervisor failed to start; a run without it is refused" >&2
  tmux kill-session -t "$RUN" 2>/dev/null || true
  resource_event '{"reason":"seat supervisor window failed","residue":false}' abandoned || true
  exit 70
fi
IGNITION_FILES=""
tmux select-window -t "$RUN:validator" 2>/dev/null || true
resource_event '{}' active

echo "factory '$RUN' is live: tmux attach -t $RUN"
echo "  — the dispatcher answers each seat's folder-trust and permission prompts —"
echo "  exact commit : $FACTORY_BASE_COMMIT"
echo "  target state : $FACTORY_TARGET_STATE_DIGEST"
echo "  control root : $ROOT"
echo "  source root  : $FACTORY_SOURCE_ROOT"
echo "  workdir      : $FACTORY_WORKDIR"
echo "  model lanes  : QUALIFIED_PR2"
echo "  sandbox      : ${FACTORY_LANE_SANDBOX:-on}"
echo "  validator    : operator-owned coordination"
echo "  validator ai : $VALIDATOR_AGENT${VALIDATOR_MODEL:+ ($VALIDATOR_MODEL)}"
if [ "$VALIDATOR_AGENT" = kickoff ]; then
  echo "  kickoff brief: $ROOT/validator/BRIEF.md (read it now)"
  echo "  kickoff inbox: $ROOT/validator/inbox.jsonl (watch it for the whole run)"
fi
echo "  orchestrator : $ORCHESTRATOR_AGENT${ORCHESTRATOR_MODEL:+ ($ORCHESTRATOR_MODEL)} (resident strategic monitor; tmux-unqualified)"
