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
# Phase 0.1 (remediation plan): refusal exits leave a derivable events.jsonl signal
# through the closed writer. The RED/GREEN verdict path is not instrumented — a rendered
# verdict is already a recorded terminal signal.
refusal_event() {
  python3 "$D/attention_gate.py" refusal-event --root "$ROOT" --kind refusal-endgame \
    --source endgame.sh --detail "$1" --exit-code "${2:-70}" || \
    echo "endgame: refusal event could not be recorded" >&2
}
factory_verify_target_state "$RUN" "$FACTORY_RUNS_ROOT" >/dev/null || \
  { rc=$?; refusal_event "target-state verification refused" "$rc"; exit "$rc"; }
if [ -e "$ROOT/dialogue/journal.jsonl" ] || [ -L "$ROOT/dialogue/journal.jsonl" ]; then
  python3 "$D/lane_dialogue.py" require-clear --root "$ROOT" >/dev/null || {
    rc=$?
    refusal_event "unanswered lane question blocks endgame" "$rc"
    exit "$rc"
  }
fi
"$D/orchestrator_checkpoint.sh" "$RUN" pre_verdict \
  "before rendering the endgame verdict for candidate resource $CANDIDATE_RESOURCE" \
  --runs "$FACTORY_RUNS_ROOT" || {
    rc=$?
    refusal_event "resident Orchestrator did not assess the pre-verdict checkpoint" "$rc"
    exit "$rc"
  }
python3 "$D/attention_gate.py" check --root "$ROOT" --lane validator || {
  rc=$?
  refusal_event "resident Orchestrator block is pending before verdict" "$rc"
  exit "$rc"
}

RESOURCES=$($FACTORY_CLI verify-resources --runs "$FACTORY_RUNS_ROOT" --run-id "$RUN") || \
  { rc=$?; refusal_event "resource verification refused" "$rc"; exit "$rc"; }
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
' "$CANDIDATE_RESOURCE") || { rc=$?; refusal_event "candidate resource resolution refused" "$rc"; exit "$rc"; }
git -C "$CANDIDATE" cat-file -e "$SHA^{commit}" 2>/dev/null || {
  refusal_event "final SHA is not a commit in the candidate resource"
  echo "endgame: $SHA is not a commit in run resource $CANDIDATE_RESOURCE" >&2
  exit 70
}
RESOLVED=$(git -C "$CANDIDATE" rev-parse --verify "$SHA^{commit}")
[ "$RESOLVED" = "$SHA" ] || { refusal_event "final SHA did not resolve exactly"; echo "endgame: final SHA did not resolve exactly" >&2; exit 70; }

# A green local suite is not evidence that producer/consumer paths agree. The
# Phase-A agreement register names every cross-path obligation; Phase C must now
# carry exact-candidate, exact-suite, exact-oracle two-direction witnesses (or the
# explicitly weaker independently reviewed structural-authority route).
AGREEMENT_EVIDENCE_GREEN=1
if ! python3 "$D/agreement_contract.py" verify-evidence --root "$ROOT" \
  --artifacts "$ROOT/artifacts" --candidate-sha "$SHA"; then
  AGREEMENT_EVIDENCE_GREEN=0
fi
GUIDANCE_EVIDENCE_GREEN=1
if ! python3 "$D/run_guidance.py" verify-evidence --root "$ROOT" \
  --artifacts "$ROOT/artifacts" --candidate-sha "$SHA"; then
  GUIDANCE_EVIDENCE_GREEN=0
fi

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
  refusal_event "candidate archive failed"
  exit 70
fi
( cd "$FRESH" && git init --quiet -b endgame/subject && git add -A && \
  git -c user.name=harness -c user.email=harness@local \
    commit --quiet -m "endgame subject $SHA" )
resource_event '{}' active

FAILED=0
say() { printf '%s\n' "$*"; }
if [ "$AGREEMENT_EVIDENCE_GREEN" -eq 1 ]; then
  say "== cross-path agreement evidence =="
  say "   exact-subject agreement evidence: GREEN"
else
  say "== cross-path agreement evidence =="
  say "   exact-subject agreement evidence: RED"
  FAILED=1
fi
if [ "$GUIDANCE_EVIDENCE_GREEN" -eq 1 ]; then
  say "== selected run-guidance evidence =="
  say "   exact obligation evidence: GREEN"
else
  say "== selected run-guidance evidence =="
  say "   exact obligation evidence: RED"
  FAILED=1
fi
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
  ENDGAME_ADMISSION=$(python3 - "$ROOT" "$RUN" "$SHA" "$CANDIDATE_RESOURCE" \
    "$FACTORY_TARGET_STATE_DIGEST" <<'PY'
import hashlib, json, os, pathlib, stat, sys

root = pathlib.Path(sys.argv[1])
run, candidate, resource, target_state = sys.argv[2:]
harness_path = root / "harness.json"
fd = os.open(
    harness_path,
    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
)
try:
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 64 * 1024:
        raise SystemExit("endgame: harness metadata is not a bounded regular file")
    harness = json.loads(os.read(fd, 64 * 1024 + 1))
finally:
    os.close(fd)
if "agreement_contract_version" not in harness:
    print("")
    raise SystemExit(0)
subject = dict(harness)
for field in ("closed_at", "promotion_verdict", "promotion_verdict_digest"):
    subject.pop(field, None)
subject["status"] = "open"
subject_raw = json.dumps(subject, sort_keys=True, separators=(",", ":")).encode()
body = {
    "schema_version": "factory-endgame-admission/1",
    "run_id": run,
    "candidate_sha": candidate,
    "candidate_resource": resource,
    "target_state_digest": target_state,
    "harness_subject_digest": "sha256:" + hashlib.sha256(subject_raw).hexdigest(),
    "checks": [
        "agreement-evidence",
        "guidance-evidence",
        "full-gate-suite",
        "isolation-proof",
        "live-proof",
        "target-state",
        "resource-hygiene",
    ],
    "issued_by": "endgame.sh",
}
raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode() + b"\n"
path = root / "endgame" / f"gate-l-{candidate}.json"
try:
    out = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
except FileExistsError:
    existing = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
    )
    try:
        installed = os.fstat(existing)
        if not stat.S_ISREG(installed.st_mode) or os.read(existing, len(raw) + 1) != raw:
            raise SystemExit("endgame: retained Gate-L admission has conflicting bytes")
    finally:
        os.close(existing)
else:
    with os.fdopen(out, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
print(path)
PY
  ) || {
    say "   Gate L: RED — green endgame admission could not be retained"
    FAILED=1
    ENDGAME_ADMISSION=""
  }
  PROMOTE_ARGS=("$RUN" --runs "$FACTORY_RUNS_ROOT")
  [ -z "$ENDGAME_ADMISSION" ] || \
    PROMOTE_ARGS+=(--endgame-admission "$ENDGAME_ADMISSION")
  if [ "$FAILED" -eq 0 ] && \
     FACTORY_RUNS_DIR="$FACTORY_RUNS_ROOT" HARNESS_RUN_ROOT="$ROOT" \
       "$D/promote.sh" "${PROMOTE_ARGS[@]}"; then
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
