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
  "$FACTORY_TARGET_MANIFEST_DIGEST" "$FACTORY_BASE_COMMIT" "$FACTORY_CHECKOUT_ID" <<'PY'
import datetime, json, os, pathlib, sys, tempfile
(
    task_source, task_dest, metadata_path, run, budget, audit, task_digest,
    target_state_digest, manifest_digest, commit, checkout_id,
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
    "schema_version": "factory-harness/1",
    "run_id": run,
    "status": "open",
    "task_digest": task_digest,
    "target_state_digest": target_state_digest,
    "target_manifest_digest": manifest_digest,
    "resolved_commit": commit,
    "checkout_id": checkout_id,
    "budget_usd": budget_value,
    "budget_enforcement": "UNQUALIFIED_PR2",
    "audit_interval_min": audit_value,
    "promise_window_min": 10,
    "launcher_qualification": "UNQUALIFIED_PR2",
    "lane_isolation": "UNQUALIFIED_PR2",
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
  $FACTORY_CLI record-resource --runs "$FACTORY_RUNS_ROOT" --run-id "$RUN" \
    --resource-id tmux-session --resource-type tmux-session --identifier "$RUN" \
    --creator-action harness-ignition --ownership run-owned \
    --baseline-json '{"absent_at_plan":true}' --disposition-json "$1" \
    --evidence-json "{\"target-state\":\"$FACTORY_TARGET_STATE_DIGEST\"}" \
    --status "$2" --actor harness-ignition >/dev/null
}
resource_event '{}' planned

printf -v CTL_CMD 'exec env FACTORY_RUNS_DIR=%q FACTORY_HARNESS_ROOT=%q HARNESS_RUN_ROOT=%q python3 %q --run %q --root %q' \
  "$FACTORY_RUNS_ROOT" "$FACTORY_HARNESS_ROOT" "$ROOT" "$D/dispatcher.py" "$RUN" "$ROOT"
VALIDATOR_PROMPT="/validate - the verbatim task is in $ROOT/TASK.md and is bound by the Stage-E execution receipt. Re-derive the checked run projection before acting. Negotiate sufficiently deep product, architecture, and testing/monitoring artifacts with the human; launch lanes only through harness/dispatch_lane.sh. The tmux launcher and lane isolation remain UNQUALIFIED_PR2."
printf -v VALIDATOR_CMD 'exec env FACTORY_RUNS_DIR=%q FACTORY_HARNESS_ROOT=%q HARNESS_RUN_ROOT=%q claude %q' \
  "$FACTORY_RUNS_ROOT" "$FACTORY_HARNESS_ROOT" "$ROOT" "$VALIDATOR_PROMPT"

if ! tmux new-session -d -s "$RUN" -n ctl -c "$FACTORY_WORKDIR" "$CTL_CMD"; then
  resource_event '{"reason":"tmux creation failed","residue":false}' abandoned || true
  echo "factory: failed to create tmux session" >&2
  exit 70
fi
resource_event '{}' active
if ! tmux new-window -t "$RUN" -n validator -c "$FACTORY_WORKDIR" "$VALIDATOR_CMD"; then
  echo "factory: validator window failed; tmux session remains recorded active" >&2
  exit 70
fi

echo "factory '$RUN' is live: tmux attach -t $RUN"
echo "  exact commit : $FACTORY_BASE_COMMIT"
echo "  target state : $FACTORY_TARGET_STATE_DIGEST"
echo "  control root : $ROOT"
echo "  source root  : $FACTORY_SOURCE_ROOT"
echo "  workdir      : $FACTORY_WORKDIR"
echo "  launcher     : UNQUALIFIED_PR2"
