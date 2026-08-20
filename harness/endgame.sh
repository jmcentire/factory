#!/usr/bin/env bash
# Judge an exact candidate already present in a run-owned workspace. Nothing here inspects,
# merges, mutates, or blocks on unrelated operator branches, stashes, worktrees, PRs, or dirt.
set -euo pipefail

RUN="${1:?usage: endgame.sh <run> <final-sha> --candidate-resource <id> [--runs <path>]}"
SHA="${2:?final sha}"
shift 2
RUNS_ARG="${FACTORY_RUNS_DIR:-${HARNESS_DIR:-.factory}/runs}"
CANDIDATE_RESOURCE=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --runs) RUNS_ARG="$2"; shift 2 ;;
    --candidate-resource) CANDIDATE_RESOURCE="$2"; shift 2 ;;
    *) echo "endgame: unknown argument: $1" >&2; exit 64 ;;
  esac
done
[ -n "$CANDIDATE_RESOURCE" ] || {
  echo "endgame: --candidate-resource is required; an ambient SHA is not a subject" >&2
  exit 64
}
[[ "$SHA" =~ ^[0-9a-f]{40}$|^[0-9a-f]{64}$ ]] || {
  echo "endgame: final SHA must be an exact object id" >&2
  exit 64
}

D="$(cd "$(dirname "$0")" && pwd -P)"
FACTORY_CLI="${FACTORY_CLI:-factory}"
# shellcheck source=harness/run_context.sh
source "$D/run_context.sh"
factory_load_context "$RUN" "$RUNS_ARG"
ROOT="$FACTORY_CONTROL_ROOT"
factory_verify_target_state "$RUN" "$FACTORY_RUNS_ROOT" >/dev/null

RESOURCES=$($FACTORY_CLI verify-resources --runs "$FACTORY_RUNS_ROOT" --run-id "$RUN")
CANDIDATE=$(printf '%s' "$RESOURCES" | python3 -c '
import json, pathlib, sys
resource_id = sys.argv[1]
doc = json.load(sys.stdin)
resource = doc.get("resources", {}).get(resource_id)
if not isinstance(resource, dict):
    raise SystemExit(f"endgame: unknown candidate resource: {resource_id}")
if resource.get("ownership") != "run-owned":
    raise SystemExit("endgame: candidate resource is not run-owned")
if resource.get("resource_type") not in {"lane-workspace", "source-worktree"}:
    raise SystemExit("endgame: candidate resource is not a source/workspace")
if resource.get("status") not in {"active", "retained"}:
    raise SystemExit("endgame: candidate resource is not active or retained")
raw_path = pathlib.Path(str(resource.get("identifier", "")))
if raw_path.is_symlink():
    raise SystemExit("endgame: candidate resource path is a symlink")
path = raw_path.resolve(strict=True)
if not path.is_dir():
    raise SystemExit("endgame: candidate resource path is invalid")
print(path)
' "$CANDIDATE_RESOURCE")
git -C "$CANDIDATE" cat-file -e "$SHA^{commit}" 2>/dev/null || {
  echo "endgame: $SHA is not a commit in run resource $CANDIDATE_RESOURCE" >&2
  exit 70
}
RESOLVED=$(git -C "$CANDIDATE" rev-parse --verify "$SHA^{commit}")
[ "$RESOLVED" = "$SHA" ] || { echo "endgame: final SHA did not resolve exactly" >&2; exit 70; }

RESOURCE_ID="endgame-checkout-${SHA:0:12}-$$"
FRESH="$ROOT/endgame/$RESOURCE_ID"
EVIDENCE=$(python3 - "$FACTORY_TARGET_STATE_DIGEST" "$CANDIDATE_RESOURCE" "$SHA" <<'PY'
import hashlib, json, sys

target_state, candidate_resource, commit = sys.argv[1:]
address = lambda value: "sha256:" + hashlib.sha256(value.encode()).hexdigest()
print(json.dumps({
    "target-state": target_state,
    "candidate-resource-ref": address(candidate_resource),
    "candidate-commit-ref": address(commit),
}, sort_keys=True, separators=(",", ":")))
PY
)
resource_event() {
  factory_record_resource --runs "$FACTORY_RUNS_ROOT" --run-id "$RUN" \
    --resource-id "$RESOURCE_ID" --resource-type endgame-worktree --identifier "$FRESH" \
    --creator-action endgame --ownership run-owned --baseline-json '{"absent_at_plan":true}' \
    --disposition-json "$1" --evidence-json "$EVIDENCE" --status "$2" \
    --actor endgame-validator >/dev/null
}
resource_event '{}' planned
mkdir -p "$FRESH"
if ! git -C "$CANDIDATE" archive "$SHA" | tar -x -C "$FRESH"; then
  resource_event '{"reason":"candidate archive failed","residue":true}' failed || true
  exit 70
fi
( cd "$FRESH" && git init --quiet -b endgame/subject && git add -A && \
  git -c user.name=harness -c user.email=harness@local \
    commit --quiet -m "endgame subject $SHA" )
resource_event '{}' active

FAILED=0
say() { printf '%s\n' "$*"; }
gate() {
  local name="$1"; shift
  say "== $name =="
  if ( cd "$FRESH" && \
       HARNESS_DIR="$FACTORY_HARNESS_ROOT" HARNESS_RUN_ROOT="$ROOT" \
       HARNESS_BASE_SHA="" "$D/receipt.sh" "$@" ); then
    say "   $name: GREEN"
  else
    say "   $name: RED"; FAILED=1
  fi
}

gate "full gate suite" make ship
gate "isolation proof" make test-isolation

say "== live proof against the declared exact-target environment =="
TARGET_CONF="${HARNESS_TARGET_CONF:-$FACTORY_WORKDIR/.factory/target.conf}"
if [ -f "$TARGET_CONF" ] && [ ! -L "$TARGET_CONF" ]; then
  if ( cd "$FRESH" && HARNESS_DIR="$FACTORY_HARNESS_ROOT" HARNESS_RUN_ROOT="$ROOT" \
       HARNESS_TARGET_CONF="$TARGET_CONF" "$D/proof.sh" "$RUN" ); then
    say "   proof: GREEN"
  else
    say "   proof: RED"; FAILED=1
  fi
else
  say "   no exact-target proof configuration: $TARGET_CONF"
  FAILED=1
fi

say "== exact-subject and run-owned-resource hygiene =="
factory_verify_target_state "$RUN" "$FACTORY_RUNS_ROOT" >/dev/null || FAILED=1
if [ -n "$(git -C "$FRESH" status --porcelain --untracked-files=all)" ]; then
  say "   endgame checkout changed while judging; evidence no longer names one subject"
  FAILED=1
fi

# Endgame may mechanically disposition only what it can prove. Exact target/object stores, the
# judged checkout, and the accepted candidate are intentionally retained if they still exist.
# Absent tmux resources are disposed. Other active resources are an operator decision and remain
# blocking; a blanket "retain everything" would launder leaks into successful close records.
ACTIVE_ROWS=$($FACTORY_CLI verify-resources --runs "$FACTORY_RUNS_ROOT" --run-id "$RUN" | \
  python3 -c 'import json,sys; d=json.load(sys.stdin); rows=[]
for rid,r in d.get("resources",{}).items():
    if r.get("ownership")=="run-owned" and r.get("status")=="active":
        rows.append((rid, str(r.get("resource_type","")), str(r.get("identifier",""))))
print("\n".join("\t".join(row) for row in rows))')
while IFS=$'\t' read -r resource_id resource_type identifier; do
  [ -n "$resource_id" ] || continue
  case "$resource_type" in
    object-store|source-worktree|endgame-worktree)
      if [ -d "$identifier" ] && [ ! -L "$identifier" ]; then
        factory_disposition_resource --runs "$FACTORY_RUNS_ROOT" --run-id "$RUN" \
          --resource-id "$resource_id" --status retained \
          --reason "verified path retained for exact-target/endgame reproducibility" \
          --residue true --evidence-json "$EVIDENCE" --actor endgame-validator >/dev/null || \
          FAILED=1
      else
        say "   $resource_id: active $resource_type is missing or symlinked; explicit review required"
        FAILED=1
      fi
      ;;
    lane-workspace)
      if [ "$resource_id" = "$CANDIDATE_RESOURCE" ] && \
         [ -d "$identifier" ] && [ ! -L "$identifier" ]; then
        factory_disposition_resource --runs "$FACTORY_RUNS_ROOT" --run-id "$RUN" \
          --resource-id "$resource_id" --status retained \
          --reason "accepted candidate workspace retained for inspection" --residue true \
          --evidence-json "$EVIDENCE" --actor endgame-validator >/dev/null || FAILED=1
      else
        say "   $resource_id: non-candidate lane workspace requires explicit disposition"
        FAILED=1
      fi
      ;;
    tmux-session|tmux-window)
      if ! command -v tmux >/dev/null 2>&1; then
        say "   $resource_id: tmux unavailable; resource absence cannot be proved"
        FAILED=1
      elif tmux display-message -p -t "$identifier" '#{session_name}:#{window_name}' \
           >/dev/null 2>&1; then
        say "   $resource_id: tmux resource is still live; close it before Gate L"
        FAILED=1
      else
        factory_disposition_resource --runs "$FACTORY_RUNS_ROOT" --run-id "$RUN" \
          --resource-id "$resource_id" --status disposed \
          --reason "tmux target mechanically absent at endgame" --residue false \
          --evidence-json "$EVIDENCE" --actor endgame-validator >/dev/null || FAILED=1
      fi
      ;;
    *)
      say "   $resource_id: active $resource_type requires explicit disposition"
      FAILED=1
      ;;
  esac
done <<<"$ACTIVE_ROWS"

say "== sole harness close (Gate L) =="
if [ "$FAILED" -eq 0 ]; then
  if FACTORY_RUNS_DIR="$FACTORY_RUNS_ROOT" HARNESS_RUN_ROOT="$ROOT" \
       "$D/promote.sh" "$RUN" --runs "$FACTORY_RUNS_ROOT"; then
    say "   Gate L: GREEN"
  else
    say "   Gate L: RED"; FAILED=1
  fi
else
  say "   Gate L: NOT RUN — deterministic or live-proof gaps remain"
fi

mkdir -p "$ROOT/endgame"
python3 - "$ROOT" "$RUN" "$SHA" "$CANDIDATE_RESOURCE" "$FAILED" <<'PY'
import datetime, json, os, pathlib, sys, tempfile
root, run, sha, resource, failed = sys.argv[1:]
body = {
    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "run": run, "final_sha": sha, "candidate_resource": resource,
    "verdict": "RED" if int(failed) else "GREEN",
}
path = pathlib.Path(root) / "endgame" / "verdict.json"
temporary = tempfile.NamedTemporaryFile(
    mode="w", dir=path.parent, prefix=".verdict-", delete=False, encoding="utf-8"
)
try:
    json.dump(body, temporary, indent=2, sort_keys=True)
    temporary.write("\n")
    temporary.flush()
    os.fsync(temporary.fileno())
    temporary.close()
    os.replace(temporary.name, path)
    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
except BaseException:
    temporary.close()
    if os.path.exists(temporary.name):
        os.unlink(temporary.name)
    raise
print(f"endgame verdict: {body['verdict']} -> {path}")
PY
exit "$FAILED"
