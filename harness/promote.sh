#!/usr/bin/env bash
# promote.sh — Gate L: the SOLE writer of a harness.json "closed" status.
#
# Why this exists: the doctrine (HARNESS.md, "two-layer validation split") holds that a
# judge is never a gate — semantic verdicts advise, they do not advance. Advancement is
# deterministic: a run closes ONLY through decide_promotion, the pure promotion decision in
# factory_core. Harness status lives separately from authoritative run.json, so a green
# `make ship` cannot silently masquerade as a runtime transition. This script renders
# the decide_promotion verdict (via the factory CLI, the trust anchor) and writes "closed"
# ONLY when the verdict allows. Fail-closed otherwise — a run with no gathered evidence, a
# blocked decision, or an unreachable CLI closes nothing.
#
# The factory CLI is the sole authority for the verdict (it calls decide_promotion, pure).
# promote.sh is the sole writer of "closed". The two are separated so the harness script
# stays generic glue (it invokes the CLI as a subprocess, like git or tmux — it never
# imports the factory package). The operator installs the factory (console script `factory`
# on PATH); FACTORY_CLI overrides the binary so tests can point at a venv or module form.
#
# HONEST SCOPE (2026-08-14): endgame.sh now invokes this script after every preceding gate,
# live proof, exact target verification, and resource checks are green, so Gate L is the live
# harness close path and a missing promotion_inputs.json fails that close. The evidence pipeline
# does not gather
# promotion_inputs.json automatically, and this harness status update is not a RunStore PROMOTED
# ledger transition. Those are separate remaining controls; neither is implied by this wiring.
#
#   usage: promote.sh <run> [--runs <path>]
set -uo pipefail

RUN="${1:?usage: promote.sh <run> [--runs <path>]}"; shift || true
RUNS_ARG="${FACTORY_RUNS_DIR:-${HARNESS_DIR:-.factory}/runs}"
while [ $# -gt 0 ]; do case "$1" in
  --runs) RUNS_ARG="$2"; shift 2 ;;
  *) echo "promote: unknown argument: $1" >&2; exit 64 ;;
esac; done
D="$(cd "$(dirname "$0")" && pwd -P)"
FACTORY_CLI="${FACTORY_CLI:-factory}"
# shellcheck source=harness/run_context.sh
source "$D/run_context.sh"
factory_load_context "$RUN" "$RUNS_ARG" || exit $?
ROOT="$FACTORY_CONTROL_ROOT"
# Phase 0.1 (remediation plan): every fail-closed refusal writes one events.jsonl row
# through the closed writer before exiting, so a run that dies at the close leaves a
# derivable signal. BLOCKED (exit 1) is not instrumented here: it renders a verdict
# file, which is already a recorded terminal signal.
refusal_event() {
  python3 "$D/attention_gate.py" refusal-event --root "$ROOT" --kind refusal-promote \
    --source promote.sh --detail "$1" --exit-code "${2:-2}" || \
    echo "promote: refusal event could not be recorded" >&2
}
[ -f "$ROOT/harness.json" ] && [ ! -L "$ROOT/harness.json" ] || {
  refusal_event "no checked harness.json" 64
  echo "promote: no checked harness.json at $ROOT" >&2; exit 64;
}
python3 - "$ROOT/harness.json" "$RUN" "$FACTORY_TARGET_STATE_DIGEST" \
  "$FACTORY_BASE_COMMIT" "$FACTORY_CHECKOUT_ID" <<'PY' || { refusal_event "harness metadata check refused" 66; exit 66; }
import json, os, stat, sys
path, run, target_state, commit, checkout = sys.argv[1:]
fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
try:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise SystemExit("promote: harness metadata is not a regular file")
    with os.fdopen(fd, encoding="utf-8") as handle:
        fd = -1
        doc = json.load(handle)
finally:
    if fd >= 0:
        os.close(fd)
expected = {
    "schema_version": "factory-harness/2", "run_id": run,
    "target_state_digest": target_state, "resolved_commit": commit,
    "checkout_id": checkout,
}
if any(doc.get(key) != value for key, value in expected.items()):
    raise SystemExit("promote: harness metadata is not bound to current target-state")
base_fields = {
    "schema_version", "run_id", "status", "task_digest", "target_state_digest",
    "target_manifest_digest", "resolved_commit", "checkout_id", "budget_usd",
    "budget_enforcement", "audit_interval_min", "promise_window_min",
    "launcher_qualification", "lane_isolation", "interactive_validator_boundary",
    "validator_agent", "orchestrator_agent", "validator_contract", "created_at",
}
close_fields = {"closed_at", "promotion_verdict", "promotion_verdict_digest"}
if set(doc) not in (base_fields, base_fields | close_fields):
    raise SystemExit("promote: harness metadata has unknown or missing fields")
if doc.get("status") not in {"open", "closed"}:
    raise SystemExit("promote: harness status is invalid")
PY

VERDICT_FILE="$ROOT/promotion_verdict.json"
VERDICT_STDOUT="$ROOT/promotion_verdict.json.stdout"
REJECTION="$ROOT/promotion_rejection.txt"

# A close is about this exact target and only this run's resources. The operator's ambient
# checkout, branches, worktrees, stashes, and PRs are not inspected. Every run-created or
# contacted resource must already have an admissible explicit terminal disposition.
factory_verify_target_state "$RUN" "$FACTORY_RUNS_ROOT" >/dev/null || \
  { rc=$?; refusal_event "target-state verification refused" "$rc"; exit "$rc"; }
if ! $FACTORY_CLI verify-resources --runs "$FACTORY_RUNS_ROOT" --run-id "$RUN" --for-close \
  >/dev/null 2>"$REJECTION"; then
  refusal_event "run-owned resources lack a terminal disposition"
  echo "promote: run-owned resources lack a terminal disposition" >&2
  [ -s "$REJECTION" ] && sed 's/^/  /' "$REJECTION" >&2
  exit 2
fi

# --- render the verdict: the factory CLI calls decide_promotion (pure, fail-closed) -----
# Exit 2 from the CLI means a refused control (missing/unreadable promotion_inputs.json, or
# a malformed one) — the run has not gathered its evidence and no verdict is rendered.
#
# FRESHNESS (Opus F2): a stale or hand-written promotion_verdict.json must NOT satisfy the
# close. We remove both the verdict file and the captured stdout BEFORE the CLI call, so the
# only way a verdict file can exist after this point is that THIS invocation's CLI wrote it.
# A no-op FACTORY_CLI (e.g. `true`) writes nothing and the close fail-closes below.
rm -f "$VERDICT_FILE" "$VERDICT_STDOUT"
if ! $FACTORY_CLI promote --runs "$FACTORY_RUNS_ROOT" --run-id "$RUN" >"$VERDICT_STDOUT" 2>"$REJECTION"; then
  refusal_event "no verdict rendered: decide_promotion could not ground a decision"
  echo "promote: refused — no verdict rendered (decide_promotion could not ground a decision)" >&2
  [ -s "$REJECTION" ] && sed 's/^/  /' "$REJECTION" >&2
  exit 2
fi
# The CLI writes promotion_verdict.json (the audited record) and emits the same decision to
# stdout. A missing verdict file means the CLI exited 0 without rendering one — fail-closed.
[ -f "$VERDICT_FILE" ] || { refusal_event "factory CLI exited 0 but wrote no verdict file"; echo "promote: factory CLI exited 0 but wrote no $VERDICT_FILE" >&2; exit 2; }
# BINDING (Opus F2): the verdict file must be THIS invocation's output, not a forgery. The
# CLI writes the file and prints the identical decision to stdout; a byte-for-byte match
# proves the file was produced by the CLI call we just made. A stale/forged file that
# somehow survived the rm -f above (or a CLI that writes one thing and prints another) is
# caught here and fail-closes.
if ! diff -q "$VERDICT_FILE" "$VERDICT_STDOUT" >/dev/null 2>&1; then
  refusal_event "verdict file does not match CLI stdout: stale/forged verdict refused"
  echo "promote: verdict file does not match CLI stdout — refusing a stale/forged verdict" >&2
  exit 2
fi

# --- the verdict is the sole authority to close -----------------------------------------
# `allowed` must be exactly true (JSON bool). A blocked decision (allowed=false) is a
# finding, not a failure of this script: the cage did its job by refusing to advance a run
# the evidence does not support. A verdict that is unreadable or missing the field is
# fail-closed — we never infer consent from a malformed verdict.
ALLOWED=$(python3 - "$VERDICT_FILE" <<'PY'
import json, sys
try:
    v = json.load(open(sys.argv[1]))
except Exception as exc:
    print(f"unreadable:{exc}"); sys.exit(3)
if v.get("allowed") is True:
    print("true"); sys.exit(0)
print("blocked"); sys.exit(0)
PY
)
rc=$?
if [ "$rc" -ne 0 ]; then
  refusal_event "verdict unreadable"
  echo "promote: verdict unreadable — $ALLOWED" >&2; exit 2
fi
if [ "$ALLOWED" != "true" ]; then
  echo "promote: decision BLOCKED — run not allowed to close (verdict in $VERDICT_FILE)" >&2
  echo "  disposition: $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("disposition","?"))' "$VERDICT_FILE" 2>/dev/null || echo '?')" >&2
  # Exit 1 is BLOCKED (the cage refused to advance). Write-failure uses a distinct code (70)
  # so a blocked decision is never confused with a failure to persist the close.
  exit 1
fi

# Freeze the exact terminal resource head only after the promotion verdict allows. The first
# close check above prevents wasted evidence work; this second check is the commit point. It
# re-verifies under the resource guard, writes a content-addressed/fsynced seal, and makes every
# supported later resource append fail. A blocked verdict therefore does not prematurely seal a
# still-open run, while a race between the first check and this point is caught here.
if ! $FACTORY_CLI verify-resources --runs "$FACTORY_RUNS_ROOT" --run-id "$RUN" \
  --for-close --seal --actor gate-l >/dev/null 2>"$REJECTION"; then
  refusal_event "terminal resource seal refused: run not closed"
  echo "promote: terminal resource seal refused — run not closed" >&2
  [ -s "$REJECTION" ] && sed 's/^/  /' "$REJECTION" >&2
  exit 2
fi

# --- SOLE WRITER: flip harness.json status open -> closed (atomic) ---------------------
# No other harness script writes "closed". The dispatcher reads harness.json to stop;
# factory.sh writes "open". This is the one harness-close path.
#
# harness.json is coordination metadata, separate from authoritative RunStore run.json.
# A RunStore PROMOTED transition remains a separate unwired runtime control and must never be
# inferred from this harness close.
#
# Atomic write (Opus F5): tmpfile + os.replace so the dispatcher's poll never reads a
# half-written harness.json. Exit 70 on write-failure so it is distinct from BLOCKED (1).
if ! python3 - "$ROOT/harness.json" "$RUN" "$VERDICT_FILE" <<'PY' 2>>"$REJECTION"
import datetime, hashlib, json, os, pathlib, stat, sys, tempfile
run_path = pathlib.Path(sys.argv[1])
run = sys.argv[2]  # the run id — a string, not a path
verdict_file = pathlib.Path(sys.argv[3])

def read_regular(path: pathlib.Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"not a regular file: {path}")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            return handle.read()
    finally:
        if fd >= 0:
            os.close(fd)

def sync_parent(path: pathlib.Path) -> None:
    fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(f"parent is not a directory: {path.parent}")
        os.fsync(fd)
    finally:
        os.close(fd)

doc = json.loads(read_regular(run_path))
if doc.get("status") == "closed":
    print(f"promote: {run} already closed — nothing to do (idempotent)")
    sys.exit(0)
closed_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
verdict_bytes = read_regular(verdict_file)
doc["status"] = "closed"
doc["closed_at"] = closed_at
doc["promotion_verdict"] = verdict_file.name
doc["promotion_verdict_digest"] = "sha256:" + hashlib.sha256(verdict_bytes).hexdigest()
# Atomic replace: write a temp file in the same dir, fsync, then os.replace (rename is atomic
# on POSIX). The verdict identity is part of this same atomic record, so a crash cannot expose a
# closed status without its close audit.
tmp = tempfile.NamedTemporaryFile(
    mode="w", dir=str(run_path.parent), suffix=".tmp", delete=False)
try:
    tmp.write(json.dumps(doc, indent=2) + "\n")
    tmp.flush()
    os.fsync(tmp.fileno())
    tmp.close()
    os.replace(tmp.name, run_path)
    sync_parent(run_path)
except OSError:
    os.unlink(tmp.name) if os.path.exists(tmp.name) else None
    raise  # surfaces as a non-zero exit; the `if !` below maps it to exit 70
print(f"promote: {run} closed — sole-advancement via decide_promotion verdict")
PY
then
  refusal_event "harness.json close write failed: run NOT closed" 70
  echo "promote: harness.json close write failed — run NOT closed" >&2
  [ -s "$REJECTION" ] && sed 's/^/  /' "$REJECTION" >&2
  exit 70
fi
