#!/usr/bin/env bash
# factory.sh — ignition: one command, one task, one factory. N factories = N runs.
#   harness/factory.sh <run-name> "<task text>"            (or a path to a task file)
#   harness/factory.sh <run-name> <task> --target-manifest .factory/target.toml --budget 100
# Creates .factory/runs/<run-name>/, pins the base SHA, records the task VERBATIM,
# grounds the session, and opens the tmux session:
#   window ctl        — the dispatcher (deterministic watcher; pays no model tokens)
#   window validator  — the Validator lane (maximal context; the human talks here)
# Coder/Tester windows are opened later by dispatch_lane.sh, only downstream of the
# three signed phase artifacts. The orchestrator-agent is invoked, not resident.
set -euo pipefail
RUN="${1:?usage: factory.sh <run-name> <task-text-or-file> [--repo <path>] [--target-manifest <path>] [--budget <usd>] [--audit-interval <min>]}"
TASK_IN="${2:?task text or file}"; shift 2
BUDGET=""; AUDIT_MIN="45"; REPO_ARG=""; TARGET_MANIFEST_ARG=""
while [ $# -gt 0 ]; do case "$1" in
  --repo) REPO_ARG="$2"; shift 2 ;;
  --target-manifest) TARGET_MANIFEST_ARG="$2"; shift 2 ;;
  --budget) BUDGET="$2"; shift 2 ;;
  --audit-interval) AUDIT_MIN="$2"; shift 2 ;;
  *) echo "unknown arg: $1" >&2; exit 64 ;;
esac; done

D="$(cd "$(dirname "$0")" && pwd)"
# The factory is generic; the TARGET is data. All run state, config, and authority
# roots (.factory/, DIRECTIVES/) live with the target project, never with the
# factory checkout. Default target: the invoking directory's repo.
REPO="${REPO_ARG:-$PWD}"
[ -d "$REPO" ] || { echo "target repo does not exist: $REPO" >&2; exit 64; }
REPO="$(cd "$REPO" && pwd)"
git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  echo "target is not a git repository: $REPO — the factory is generic; the target" >&2
  echo "is data. Point --repo at the project this run builds." >&2
  exit 64
}
cd "$REPO"
H="${HARNESS_DIR:-.factory}"; ROOT="$H/runs/$RUN"
[ -e "$ROOT" ] && { echo "run '$RUN' already exists at $ROOT" >&2; exit 65; }
tmux has-session -t "$RUN" 2>/dev/null && { echo "tmux session '$RUN' already live" >&2; exit 65; }

# The target pack is an operational ABI, not optional context. Validate it through the same
# package boundary the runtime uses before creating any run state, then retain the exact bytes
# under the run. A missing or malformed pack is AUTHORITY_BLOCKED, never a generic run with a
# target name supplied later in prose.
TARGET_MANIFEST="${TARGET_MANIFEST_ARG:-$REPO/$H/target.toml}"
[ -f "$TARGET_MANIFEST" ] || {
  echo "target manifest is required: $TARGET_MANIFEST" >&2
  echo "provide --target-manifest with a valid factory-target-manifest/1 descriptor" >&2
  exit 66
}
FACTORY_CLI="${FACTORY_CLI:-factory}"
if ! $FACTORY_CLI inspect-target --manifest "$TARGET_MANIFEST" >/dev/null; then
  echo "target manifest failed the Factory operational-ABI gate: $TARGET_MANIFEST" >&2
  exit 66
fi

mkdir -p "$ROOT/artifacts" "$ROOT/lanes" "$ROOT/leases" "$ROOT/minutes"
cp "$TARGET_MANIFEST" "$ROOT/target.toml"
$FACTORY_CLI inspect-target --manifest "$ROOT/target.toml" > "$ROOT/target.json"
if [ -f "$TASK_IN" ]; then cp "$TASK_IN" "$ROOT/TASK.md"; else printf '%s\n' "$TASK_IN" > "$ROOT/TASK.md"; fi

git -C "$REPO" fetch --quiet origin 2>/dev/null || true
BASE_SHA=$(git -C "$REPO" rev-parse origin/main 2>/dev/null || git -C "$REPO" rev-parse HEAD)
python3 - "$ROOT" "$RUN" "$REPO" "$BASE_SHA" "$BUDGET" "$AUDIT_MIN" <<'PY'
import hashlib, json, sys, datetime
root, run, repo, sha, budget, audit = sys.argv[1:7]
task = open(f"{root}/TASK.md", "rb").read()
target = json.load(open(f"{root}/target.json"))
json.dump({"run": run, "repo": repo, "base_sha": sha,
           "task_digest": hashlib.sha256(task).hexdigest(),
           "target_manifest": {"path": "target.toml",
                               "target_id": target["target_id"],
                               "content_digest": target["content_digest"],
                               "source_digest": target["source_digest"],
                               "build": target["build"]},
           "budget_usd": float(budget) if budget else None,
           "audit_interval_min": int(audit),
           "status": "open",
           "created_at": datetime.datetime.now(datetime.timezone.utc)
                         .isoformat(timespec="seconds")},
          open(f"{root}/run.json", "w"), indent=2)
print(f"run.json written: base={sha[:12]} task_digest={hashlib.sha256(task).hexdigest()[:12]}")
PY

echo "== grounding (control 7) =="
( cd "$REPO" && "$D/ground.sh" )

tmux new-session -d -s "$RUN" -n ctl -c "$REPO" \
  "python3 '$D/dispatcher.py' --run '$RUN' --root '$ROOT'; echo '[dispatcher exited]'; read -r _"
tmux new-window -t "$RUN" -n validator -c "$REPO" \
  "claude \"/validate - the task is in $ROOT/TASK.md (verbatim; digest in run.json). Phase A0 first: search kindex, fetch authoritative docs, capture research nodes. Then Phase A with the founder in this window: product spec, architecture, test plan — each settled, signed, content-addressed into $ROOT/artifacts/. Lanes launch only via harness/dispatch_lane.sh, only after ratification.\""

echo ""
echo "factory '$RUN' is live:  tmux attach -t $RUN"
echo "  base SHA   : $BASE_SHA"
echo "  target ABI : $(python3 -c "import json; d=json.load(open('$ROOT/target.json')); print(d['target_id'], d['content_digest'][:19])")"
echo "  run root   : $ROOT"
echo "  budget     : ${BUDGET:-'(none declared — spend still receipted per objective)'}"
echo "  next       : attach, talk to the Validator in window 'validator'"
