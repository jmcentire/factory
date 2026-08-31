#!/usr/bin/env bash
# record_no.sh — the host-written terminal-NO record (remediation plan Phase 0.2).
#
# The acceptance pass rule reads "harness terminal in {closed-green, closed-red,
# host-recorded-NO}" — never "verdict.json present", which only build-to-completion runs
# can satisfy. This script is the SOLE writer of the "no" disposition, analogous to
# promote.sh being sole writer of the close: a run that is not going to complete gets an
# explicit host-recorded terminal NO instead of an eternally-open harness that only the
# operator's memory distinguishes from a live one. (The closed status stays promote.sh's
# alone — this script never writes it, and the sole-writer guard holds that line.)
#
# Kinds are committed closed data (harness/terminal_no_kinds.json). Each kind carries its
# class: "signal" (a NO-relevant terminal — an early NO the instrument rewards) or "bound"
# (deadline expiry — excluded from the NO-relevant set so the deadline knob cannot
# manufacture the terminal event the instrument rewards). Unlike the refusal-event writer,
# an unknown kind REFUSES here: this is a deliberate host action, not a dying breath —
# a caller bug must not mint a mislabeled terminal record.
#
# The disposition lives in harness.json only (one owner per fact — no events.jsonl copy);
# the 0.3 checker and postmortem read it from there. A "no" run refuses promote.sh's close
# (its strict metadata check fail-closes on the status and audit fields, emitting its own
# refusal event) — terminal is terminal.
#
#   usage: record_no.sh <run> --kind <kind> --reason <text> [--runs <path>]
set -uo pipefail

RUN="${1:?usage: record_no.sh <run> --kind <kind> --reason <text> [--runs <path>]}"; shift || true
RUNS_ARG="${FACTORY_RUNS_DIR:-${HARNESS_DIR:-.factory}/runs}"
KIND=""; REASON=""
while [ $# -gt 0 ]; do case "$1" in
  --runs) RUNS_ARG="$2"; shift 2 ;;
  --kind) KIND="$2"; shift 2 ;;
  --reason) REASON="$2"; shift 2 ;;
  *) echo "record-no: unknown argument: $1" >&2; exit 64 ;;
esac; done
[ -n "$KIND" ] || { echo "record-no: --kind is required" >&2; exit 64; }
[ -n "$REASON" ] || { echo "record-no: --reason is required" >&2; exit 64; }

D="$(cd "$(dirname "$0")" && pwd -P)"
# shellcheck source=harness/run_context.sh
source "$D/run_context.sh"
factory_load_context "$RUN" "$RUNS_ARG" || exit $?
ROOT="$FACTORY_CONTROL_ROOT"
[ -f "$ROOT/harness.json" ] && [ ! -L "$ROOT/harness.json" ] || {
  echo "record-no: no checked harness.json at $ROOT" >&2; exit 64;
}

python3 - "$ROOT/harness.json" "$D/terminal_no_kinds.json" "$KIND" "$REASON" <<'PY'
import datetime, json, os, pathlib, stat, sys, tempfile

run_path, kinds_path, kind, reason = (
    pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3], sys.argv[4],
)

def read_regular(path: pathlib.Path) -> str:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise SystemExit(f"record-no: not a regular file: {path}")
        with os.fdopen(fd, encoding="utf-8") as handle:
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

kinds = json.loads(read_regular(kinds_path))["kinds"]
if kind not in kinds:
    print(f"record-no: unknown kind {kind!r} — registered: {sorted(kinds)}", file=sys.stderr)
    raise SystemExit(65)

doc = json.loads(read_regular(run_path))
status = doc.get("status")
# Single-quoted on purpose: the sole-writer guard greps shell scripts for the
# double-quoted JSON literal; this is a read, and must not register as a writer.
if status == 'closed':
    print("record-no: run is closed — a completed run cannot be recorded NO", file=sys.stderr)
    raise SystemExit(2)
if status == "no":
    if doc.get("no_kind") == kind and doc.get("no_reason") == reason:
        print(f"record-no: {doc.get('run_id')} already recorded NO — nothing to do (idempotent)")
        raise SystemExit(0)
    print("record-no: run already carries a different NO record — refusing to overwrite", file=sys.stderr)
    raise SystemExit(2)
if status != "open":
    print(f"record-no: harness status is invalid: {status!r}", file=sys.stderr)
    raise SystemExit(2)

doc["status"] = "no"
doc["no_kind"] = kind
doc["no_class"] = kinds[kind]["class"]
doc["no_reason"] = reason
doc["no_recorded_at"] = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
tmp = tempfile.NamedTemporaryFile(mode="w", dir=str(run_path.parent), suffix=".tmp", delete=False)
try:
    tmp.write(json.dumps(doc, indent=2) + "\n")
    tmp.flush()
    os.fsync(tmp.fileno())
    tmp.close()
    os.replace(tmp.name, run_path)
    sync_parent(run_path)
except OSError:
    os.unlink(tmp.name) if os.path.exists(tmp.name) else None
    raise
print(f"record-no: {doc.get('run_id')} recorded terminal NO ({kind}/{kinds[kind]['class']})")
PY
