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
# HONEST SCOPE (updated 2026-09-02): current-contract runs must present the canonical
# candidate-bound endgame admission issued after every preceding gate, live proof, exact target
# verification, and resource check is green. Gate L rechecks that admission against live harness
# metadata under the close lock. A direct current-run call and a missing promotion_inputs.json
# both fail closed. The evidence pipeline
# does not gather
# promotion_inputs.json automatically, and this harness status update is not a RunStore PROMOTED
# ledger transition. Those are separate remaining controls; neither is implied by this wiring.
#
#   usage: promote.sh <run> [--runs <path>] [--endgame-admission <path>]
set -uo pipefail

RUN="${1:?usage: promote.sh <run> [--runs <path>] [--endgame-admission <path>]}"; shift || true
RUNS_ARG="${FACTORY_RUNS_DIR:-${HARNESS_DIR:-.factory}/runs}"
ENDGAME_ADMISSION=""
while [ $# -gt 0 ]; do case "$1" in
  --runs) RUNS_ARG="$2"; shift 2 ;;
  --endgame-admission) ENDGAME_ADMISSION="$2"; shift 2 ;;
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
CURRENT_CONTRACT=$(python3 - "$ROOT/harness.json" "$RUN" "$FACTORY_TARGET_STATE_DIGEST" \
  "$FACTORY_BASE_COMMIT" "$FACTORY_CHECKOUT_ID" <<'PY'
import json, os, re, stat, sys
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
resident_fields = {
    "orchestrator_mode", "orchestrator_window", "orchestrator_visibility",
    "orchestrator_effects", "orchestrator_boundary", "orchestrator_cli_version",
    "orchestrator_cli_contract",
}
contract_fields = {
    "agreement_contract_version", "agreement_requirement_region_families",
    "guidance_contract_version", "guidance_generation", "guidance_state",
    "guidance_selection_digest", "guidance_source_digests",
}
close_fields = {"closed_at", "promotion_verdict", "promotion_verdict_digest"}
allowed_shapes = {
    frozenset(base_fields),
    frozenset(base_fields | resident_fields),
    frozenset(base_fields | resident_fields | contract_fields),
    frozenset(base_fields | close_fields),
    frozenset(base_fields | resident_fields | close_fields),
    frozenset(base_fields | resident_fields | contract_fields | close_fields),
}
if frozenset(doc) not in allowed_shapes:
    raise SystemExit("promote: harness metadata has unknown or missing fields")
if doc.get("status") not in {"open", "closed"}:
    raise SystemExit("promote: harness status is invalid")
if contract_fields <= set(doc):
    digest = re.compile(r"^sha256:[0-9a-f]{64}$")
    if doc["agreement_contract_version"] != "factory-agreement-contract/1":
        raise SystemExit("promote: harness agreement contract is unsupported")
    if doc["guidance_contract_version"] != "factory-run-guidance/1":
        raise SystemExit("promote: harness guidance contract is unsupported")
    generation = doc["guidance_generation"]
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise SystemExit("promote: harness guidance generation is invalid")
    if doc["guidance_state"] not in {
        "none", "pending-application", "routing-verified", "evidence-complete", "noncompliant"
    }:
        raise SystemExit("promote: harness guidance state is invalid")
    selection = doc["guidance_selection_digest"]
    sources = doc["guidance_source_digests"]
    if not isinstance(sources, dict) or any(
        not isinstance(name, str) or not name or not isinstance(address, str)
        or not digest.fullmatch(address)
        for name, address in sources.items()
    ):
        raise SystemExit("promote: harness guidance source map is invalid")
    expected_regions = ["authored-product"]
    if selection is None:
        if doc["guidance_state"] != "none" or sources:
            raise SystemExit("promote: unselected guidance carries selected state")
    else:
        if not isinstance(selection, str) or not digest.fullmatch(selection) or not sources:
            raise SystemExit("promote: selected guidance identity is invalid")
        expected_regions.append("run-guidance")
    if doc["agreement_requirement_region_families"] != expected_regions:
        raise SystemExit("promote: agreement region families differ from guidance selection")
print("true" if contract_fields <= set(doc) else "false")
PY
) || { refusal_event "harness metadata check refused" 66; exit 66; }

if [ "$CURRENT_CONTRACT" = "true" ]; then
  [ -n "$ENDGAME_ADMISSION" ] || {
    refusal_event "current contract has no green endgame admission" 66
    echo "promote: current-contract run must arrive through a green endgame admission" >&2
    exit 66
  }
  if ! python3 - "$ROOT" "$ROOT/harness.json" "$ENDGAME_ADMISSION" "$RUN" \
    "$FACTORY_TARGET_STATE_DIGEST" <<'PY'
import hashlib, json, os, pathlib, re, stat, sys

root = pathlib.Path(sys.argv[1]).resolve(strict=True)
harness_path = pathlib.Path(sys.argv[2])
path = pathlib.Path(sys.argv[3])
run, target_state = sys.argv[4:]
try:
    expected_parent = (root / "endgame").resolve(strict=True)
    installed_parent = path.parent.resolve(strict=True)
except OSError as exc:
    raise SystemExit(f"promote: endgame admission parent is invalid: {exc}") from exc
if installed_parent != expected_parent or path.is_symlink():
    raise SystemExit("promote: endgame admission is outside the run or linked")
fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
try:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 16_384:
        raise SystemExit("promote: endgame admission is not a bounded regular file")
    raw = os.read(fd, 16_385)
    after = os.fstat(fd)
finally:
    os.close(fd)
if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
):
    raise SystemExit("promote: endgame admission changed while read")
try:
    value = json.loads(raw)
except (UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise SystemExit("promote: endgame admission is not JSON") from exc
canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
if raw != canonical or not isinstance(value, dict):
    raise SystemExit("promote: endgame admission is not a canonical object")
expected_fields = {
    "schema_version", "run_id", "candidate_sha", "candidate_resource",
    "target_state_digest", "harness_subject_digest", "checks", "issued_by",
}
if set(value) != expected_fields:
    raise SystemExit("promote: endgame admission fields differ")
candidate = value.get("candidate_sha")
candidate_resource = value.get("candidate_resource")
if (
    value.get("schema_version") != "factory-endgame-admission/1"
    or value.get("run_id") != run
    or not isinstance(candidate, str)
    or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", candidate)
    or not isinstance(candidate_resource, str)
    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", candidate_resource)
    or value.get("target_state_digest") != target_state
    or value.get("issued_by") != "endgame.sh"
):
    raise SystemExit("promote: endgame admission belongs to another subject")
if path.name != f"gate-l-{candidate}.json":
    raise SystemExit("promote: endgame admission address differs from its candidate")
checks = [
    "agreement-evidence", "guidance-evidence", "full-gate-suite", "isolation-proof",
    "live-proof", "target-state", "resource-hygiene",
]
if value.get("checks") != checks:
    raise SystemExit("promote: endgame admission does not close every required check")
harness = json.loads(harness_path.read_bytes())
for field in ("closed_at", "promotion_verdict", "promotion_verdict_digest"):
    harness.pop(field, None)
harness["status"] = "open"
harness_subject = json.dumps(harness, sort_keys=True, separators=(",", ":")).encode()
observed = "sha256:" + hashlib.sha256(harness_subject).hexdigest()
if value.get("harness_subject_digest") != observed:
    raise SystemExit("promote: endgame admission harness subject is stale")
PY
  then
    refusal_event "green endgame admission check refused" 66
    exit 66
  fi
elif [ -n "$ENDGAME_ADMISSION" ]; then
  refusal_event "legacy run supplied an inapplicable endgame admission" 66
  echo "promote: legacy run may not smuggle a current-contract endgame admission" >&2
  exit 66
fi

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
python3 - "$ROOT/harness.json" "$RUN" "$VERDICT_FILE" "$ENDGAME_ADMISSION" <<'PY' 2>>"$REJECTION"
import datetime, fcntl, hashlib, json, os, pathlib, stat, sys, tempfile

run_path = pathlib.Path(sys.argv[1])
# Round-3 carryover: promote's close and record_no's terminal write share one
# advisory run-root lock (crash-released flock) so the two host writers can
# never interleave a read-modify-write on harness.json.
_lock = os.open(str(run_path.parent / '.harness.write.lock'), os.O_CREAT | os.O_RDWR, 0o600)
fcntl.flock(_lock, fcntl.LOCK_EX)
run = sys.argv[2]  # the run id — a string, not a path
verdict_file = pathlib.Path(sys.argv[3])
admission_file = pathlib.Path(sys.argv[4]) if sys.argv[4] else None

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
if doc.get("status") != "open":
    # Round-6 6-5: the locked flip mirrors the pre-check instead of trusting it —
    # between verdict render and this lock, record_no may have written a terminal
    # "no" (watchdog deadline, operator, preflight). A recorded NO is never erased
    # by a later green verdict; distinct exit so the wrapper reports the refusal.
    print(
        f"promote: {run} carries terminal status "
        f"{doc.get('status')!r} — close refused, the NO stands",
        file=sys.stderr,
    )
    sys.exit(71)

# Recheck the endgame route under the same lock as the status flip. The earlier
# admission check rejects malformed or wrong-subject receipts before doing
# promotion work; this check prevents harness metadata from changing between
# that check and the atomic close. Legacy runs remain valid only without an
# admission, while every current-contract run must still match its retained
# endgame subject exactly.
current_contract = "agreement_contract_version" in doc
if current_contract != (admission_file is not None):
    print("promote: endgame admission applicability changed before close", file=sys.stderr)
    sys.exit(72)
if admission_file is not None:
    try:
        admission = json.loads(read_regular(admission_file))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"promote: endgame admission unreadable at close: {exc}", file=sys.stderr)
        sys.exit(72)
    subject = dict(doc)
    for field in ("closed_at", "promotion_verdict", "promotion_verdict_digest"):
        subject.pop(field, None)
    subject["status"] = "open"
    subject_raw = json.dumps(subject, sort_keys=True, separators=(",", ":")).encode()
    expected_subject = "sha256:" + hashlib.sha256(subject_raw).hexdigest()
    if admission.get("harness_subject_digest") != expected_subject:
        print("promote: endgame admission became stale before close", file=sys.stderr)
        sys.exit(72)
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
flip_rc=$?
if [ "$flip_rc" -ne 0 ]; then
  if [ "$flip_rc" -eq 71 ]; then
    refusal_event "terminal NO recorded during close: promote refused to erase it" 71
    echo "promote: terminal NO recorded during close — the NO stands, run NOT closed" >&2
    [ -s "$REJECTION" ] && sed 's/^/  /' "$REJECTION" >&2
    exit 71
  fi
  if [ "$flip_rc" -eq 72 ]; then
    refusal_event "green endgame admission changed or became stale before close" 72
    echo "promote: green endgame admission changed or became stale — run NOT closed" >&2
    [ -s "$REJECTION" ] && sed 's/^/  /' "$REJECTION" >&2
    exit 72
  fi
  refusal_event "harness.json close write failed: run NOT closed" 70
  echo "promote: harness.json close write failed — run NOT closed" >&2
  [ -s "$REJECTION" ] && sed 's/^/  /' "$REJECTION" >&2
  exit 70
fi
