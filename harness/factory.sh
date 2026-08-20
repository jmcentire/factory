#!/usr/bin/env bash
# Ignite coordination for an already Stage-E-authorized Factory run. Target selection belongs
# exclusively to factory_runtime; this script may consume target-state, never invent it.
set -euo pipefail

RUN="${1:?usage: factory.sh <run> <verbatim-task-or-file> [--runs <path>] [--budget <usd>] [--audit-interval <min>]}"
TASK_IN="${2:?verbatim task text or file}"
shift 2
RUNS_ARG="${FACTORY_RUNS_DIR:-${HARNESS_DIR:-.factory}/runs}"
BUDGET=""
AUDIT_MIN="45"
VALIDATOR_AGENT="${FACTORY_VALIDATOR_AGENT:-codex}"
ORCHESTRATOR_AGENT="${FACTORY_ORCHESTRATOR_AGENT:-agy}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --runs) RUNS_ARG="$2"; shift 2 ;;
    --budget) BUDGET="$2"; shift 2 ;;
    --audit-interval) AUDIT_MIN="$2"; shift 2 ;;
    --repo|--target-manifest|--sha)
      echo "factory: $1 is forbidden; authorize and resolve the exact target through Stage R/E" >&2
      exit 64 ;;
    *) echo "factory: unknown argument: $1" >&2; exit 64 ;;
  esac
done
case "$VALIDATOR_AGENT" in
  codex|ollama|claude) ;;
  *) echo "factory: unknown validator agent '$VALIDATOR_AGENT' (codex|ollama|claude)" >&2; exit 64 ;;
esac
case "$ORCHESTRATOR_AGENT" in
  agy|codex) ;;
  *) echo "factory: unsupported orchestrator agent '$ORCHESTRATOR_AGENT' (agy|codex)" >&2; exit 64 ;;
esac

D="$(cd "$(dirname "$0")" && pwd -P)"
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
[ ! -e "$FACTORY_HARNESS_META" ] && [ ! -L "$FACTORY_HARNESS_META" ] || {
  echo "factory: harness metadata already exists for $RUN" >&2
  exit 65
}
tmux has-session -t "$RUN" 2>/dev/null && {
  echo "factory: tmux session '$RUN' already exists; it is not adopted" >&2
  exit 65
}

REQUEST="$ROOT/evidence/intake/execution-request.json"
[ -f "$REQUEST" ] && [ ! -L "$REQUEST" ] || {
  echo "factory: retained Stage-E execution request is missing or a symlink: $REQUEST" >&2
  exit 66
}

TASK_TMP="$(mktemp "${TMPDIR:-/tmp}/factory-task.XXXXXX")"
trap 'rm -f "$TASK_TMP"' EXIT
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

python3 - "$TASK_TMP" "$ROOT/TASK.md" "$FACTORY_HARNESS_META" "$RUN" \
  "$BUDGET" "$AUDIT_MIN" "$TASK_DIGEST" "$FACTORY_TARGET_STATE_DIGEST" \
  "$FACTORY_TARGET_MANIFEST_DIGEST" "$FACTORY_BASE_COMMIT" "$FACTORY_CHECKOUT_ID" \
  "$VALIDATOR_AGENT" "$ORCHESTRATOR_AGENT" <<'PY'
import datetime, json, os, pathlib, sys, tempfile
(
    task_source, task_dest, metadata_path, run, budget, audit, task_digest,
    target_state_digest, manifest_digest, commit, checkout_id, validator_agent,
    orchestrator_agent,
) = sys.argv[1:]
audit_value = int(audit)
if audit_value < 1:
    raise SystemExit("factory: audit interval must be positive")
budget_value = None if budget == "" else float(budget)
if budget_value is not None and budget_value <= 0:
    raise SystemExit("factory: budget must be positive")
task_bytes = pathlib.Path(task_source).read_bytes()
task_path = pathlib.Path(task_dest)

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
    "promise_window_min": 10,
    "launcher_qualification": "QUALIFIED_PR2",
    "lane_isolation": "QUALIFIED_PR2",
    "interactive_validator_boundary": "operator-owned-tmux",
    "validator_agent": validator_agent,
    "orchestrator_agent": orchestrator_agent,
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
    --resource-id tmux-session --resource-type tmux-session --identifier "$RUN" \
    --creator-action harness-ignition --ownership run-owned \
    --baseline-json '{"absent_at_plan":true}' --disposition-json "$1" \
    --evidence-json "{\"target-state\":\"$FACTORY_TARGET_STATE_DIGEST\"}" \
    --status "$2" --actor harness-ignition >/dev/null
}
resource_event '{}' planned

printf -v CTL_CMD 'exec env FACTORY_RUNS_DIR=%q FACTORY_HARNESS_ROOT=%q HARNESS_RUN_ROOT=%q python3 %q --run %q --root %q' \
  "$FACTORY_RUNS_ROOT" "$FACTORY_HARNESS_ROOT" "$ROOT" "$D/dispatcher.py" "$RUN" "$ROOT"
VALIDATOR_PROMPT="Act as the Validator under docs/VALIDATION-DIRECTIVE.md and the /validate contract. The verbatim task is in $ROOT/TASK.md and is bound by the Stage-E execution receipt. Re-derive the checked run projection before acting. Negotiate sufficiently deep product, architecture, and testing/monitoring artifacts with the human; launch model lanes only through the qualified harness/dispatch_lane.sh runner and typed broker. This interactive Validator window is operator-owned coordination, not a qualified model lane or a billed runner receipt."
case "$VALIDATOR_AGENT" in
  codex)
    printf -v VALIDATOR_CMD 'exec env FACTORY_RUNS_DIR=%q FACTORY_HARNESS_ROOT=%q HARNESS_RUN_ROOT=%q codex --sandbox workspace-write %q' \
      "$FACTORY_RUNS_ROOT" "$FACTORY_HARNESS_ROOT" "$ROOT" "$VALIDATOR_PROMPT"
    ;;
  ollama)
    VALIDATOR_MODEL="${FACTORY_VALIDATOR_OLLAMA_MODEL:-glm-5.2:cloud}"
    printf -v VALIDATOR_CMD 'exec env FACTORY_RUNS_DIR=%q FACTORY_HARNESS_ROOT=%q HARNESS_RUN_ROOT=%q ollama launch codex --model %q -- --sandbox workspace-write %q' \
      "$FACTORY_RUNS_ROOT" "$FACTORY_HARNESS_ROOT" "$ROOT" "$VALIDATOR_MODEL" "$VALIDATOR_PROMPT"
    ;;
  claude)
    printf -v VALIDATOR_CMD 'exec env FACTORY_RUNS_DIR=%q FACTORY_HARNESS_ROOT=%q HARNESS_RUN_ROOT=%q claude %q' \
      "$FACTORY_RUNS_ROOT" "$FACTORY_HARNESS_ROOT" "$ROOT" "$VALIDATOR_PROMPT"
    ;;
esac

if ! tmux new-session -d -s "$RUN" -n ctl -c "$FACTORY_WORKDIR" "$CTL_CMD"; then
  resource_event '{"reason":"tmux creation failed","residue":false}' abandoned || true
  echo "factory: failed to create tmux session" >&2
  exit 70
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
resource_event '{}' active

echo "factory '$RUN' is live: tmux attach -t $RUN"
echo "  exact commit : $FACTORY_BASE_COMMIT"
echo "  target state : $FACTORY_TARGET_STATE_DIGEST"
echo "  control root : $ROOT"
echo "  source root  : $FACTORY_SOURCE_ROOT"
echo "  workdir      : $FACTORY_WORKDIR"
echo "  model lanes  : QUALIFIED_PR2"
echo "  validator    : operator-owned coordination"
echo "  validator ai : $VALIDATOR_AGENT"
echo "  orchestrator : $ORCHESTRATOR_AGENT (advisory)"
