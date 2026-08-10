#!/usr/bin/env bash
# factory.sh — ignition: one command, one task, one factory. N factories = N runs.
#   harness/factory.sh <run-name> "<task text>"            (or a path to a task file)
#   harness/factory.sh <run-name> <task> --budget 100
# Creates .harness/runs/<run-name>/, pins the base SHA, records the task VERBATIM,
# grounds the session, and opens the tmux session:
#   window ctl        — the dispatcher (deterministic watcher; pays no model tokens)
#   window validator  — the Validator lane (maximal context; the human talks here)
# Coder/Tester windows are opened later by dispatch_lane.sh, only downstream of the
# three signed phase artifacts. The orchestrator-agent is invoked, not resident.
set -euo pipefail
RUN="${1:?usage: factory.sh <run-name> <task-text-or-file> [--budget <usd>] [--audit-interval <min>]}"
TASK_IN="${2:?task text or file}"; shift 2
BUDGET=""; AUDIT_MIN="45"
while [ $# -gt 0 ]; do case "$1" in
  --budget) BUDGET="$2"; shift 2 ;;
  --audit-interval) AUDIT_MIN="$2"; shift 2 ;;
  *) echo "unknown arg: $1" >&2; exit 64 ;;
esac; done

D="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$D/.." && pwd)"
H="${HARNESS_DIR:-.harness}"; ROOT="$H/runs/$RUN"
[ -e "$ROOT" ] && { echo "run '$RUN' already exists at $ROOT" >&2; exit 65; }
tmux has-session -t "$RUN" 2>/dev/null && { echo "tmux session '$RUN' already live" >&2; exit 65; }

mkdir -p "$ROOT/artifacts" "$ROOT/lanes" "$ROOT/leases" "$ROOT/minutes"
if [ -f "$TASK_IN" ]; then cp "$TASK_IN" "$ROOT/TASK.md"; else printf '%s\n' "$TASK_IN" > "$ROOT/TASK.md"; fi

git -C "$REPO" fetch --quiet origin 2>/dev/null || true
BASE_SHA=$(git -C "$REPO" rev-parse origin/main 2>/dev/null || git -C "$REPO" rev-parse HEAD)
python3 - "$ROOT" "$RUN" "$REPO" "$BASE_SHA" "$BUDGET" "$AUDIT_MIN" <<'PY'
import hashlib, json, sys, datetime
root, run, repo, sha, budget, audit = sys.argv[1:7]
task = open(f"{root}/TASK.md", "rb").read()
json.dump({"run": run, "repo": repo, "base_sha": sha,
           "task_digest": hashlib.sha256(task).hexdigest(),
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
echo "  run root   : $ROOT"
echo "  budget     : ${BUDGET:-'(none declared — spend still receipted per objective)'}"
echo "  next       : attach, talk to the Validator in window 'validator'"
