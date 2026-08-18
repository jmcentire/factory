#!/usr/bin/env bash
# Disable coordination for a pre-v2 harness without pretending it ran qualified execution.
# This preserves the run for inspection; it does not close or disposition run-owned resources.
set -euo pipefail

RUN="${1:?usage: abandon_legacy.sh <run> --actor human:<id> --reason <text> --acknowledge-unqualified-restart [--runs <path>]}"
shift
RUNS_ARG="${FACTORY_RUNS_DIR:-${HARNESS_DIR:-.factory}/runs}"
ACTOR=""
REASON=""
ACK=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --runs) RUNS_ARG="$2"; shift 2 ;;
    --actor) ACTOR="$2"; shift 2 ;;
    --reason) REASON="$2"; shift 2 ;;
    --acknowledge-unqualified-restart) ACK=1; shift ;;
    *) echo "legacy abandonment: unknown argument: $1" >&2; exit 64 ;;
  esac
done
[[ "$ACTOR" =~ ^human:[A-Za-z0-9._-]+$ ]] || {
  echo "legacy abandonment requires an explicit human:<id> operator" >&2; exit 64;
}
[ -n "$REASON" ] || { echo "legacy abandonment requires a reason" >&2; exit 64; }
[ "$ACK" -eq 1 ] || {
  echo "legacy abandonment requires --acknowledge-unqualified-restart" >&2; exit 64;
}

D="$(cd "$(dirname "$0")" && pwd -P)"
FACTORY_CLI="${FACTORY_CLI:-factory}"
# shellcheck source=harness/run_context.sh
source "$D/run_context.sh"
factory_load_context "$RUN" "$RUNS_ARG"
ROOT="$FACTORY_CONTROL_ROOT"
HARNESS_META="$ROOT/harness.json"
OUTPUT="$ROOT/legacy-harness-abandonment.json"

python3 - "$HARNESS_META" "$OUTPUT" "$RUN" "$FACTORY_TARGET_STATE_DIGEST" \
  "$ACTOR" "$REASON" <<'PY'
import datetime, hashlib, json, os, pathlib, stat, sys, unicodedata

source, output = map(pathlib.Path, sys.argv[1:3])
run, target_digest, actor, reason = sys.argv[3:]
if len(reason.encode("utf-8")) > 4096 or any(
    unicodedata.category(char) in {"Cc", "Cf"} for char in reason
):
    raise SystemExit("legacy abandonment reason must be bounded and control-free")
try:
    source_descriptor = os.open(
        source,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
except OSError as exc:
    raise SystemExit("legacy abandonment requires regular harness metadata") from exc
try:
    before = os.fstat(source_descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise SystemExit("legacy abandonment requires regular harness metadata")
    with os.fdopen(source_descriptor, "rb") as stream:
        source_descriptor = -1
        raw = stream.read(1_048_577)
        stream.seek(0)
        confirmed = stream.read(1_048_577)
        after = os.fstat(stream.fileno())
finally:
    if source_descriptor >= 0:
        os.close(source_descriptor)
identity = lambda value: (
    value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns
)
if (
    len(confirmed) > 1_048_576
    or raw != confirmed
    or identity(before) != identity(after)
    or before.st_size != len(confirmed)
):
    raise SystemExit("legacy harness metadata changed or exceeded its byte ceiling")
raw = confirmed
try:
    harness = json.loads(raw)
except json.JSONDecodeError as exc:
    raise SystemExit(f"legacy harness metadata is invalid: {exc}") from exc
if harness.get("schema_version") != "factory-harness/1":
    raise SystemExit("only factory-harness/1 may use the legacy abandonment ceremony")
if harness.get("run_id") != run or harness.get("target_state_digest") != target_digest:
    raise SystemExit("legacy harness metadata differs from the checked run target")
if harness.get("status") != "open":
    raise SystemExit("legacy harness is not open")
document = {
    "schema_version": "factory-legacy-harness-abandonment/1",
    "run_id": run,
    "target_state_digest": target_digest,
    "legacy_harness_source_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
    "legacy_schema_version": "factory-harness/1",
    "disposition": "abandoned-unqualified",
    "replacement_schema_version": "factory-harness/2",
    "actor": actor,
    "reason": reason,
    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
}
encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"
flags = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
descriptor = os.open(output, flags, 0o600)
try:
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        raise SystemExit("legacy abandonment receipt is not regular")
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        descriptor = -1
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
finally:
    if descriptor >= 0:
        os.close(descriptor)
directory = os.open(output.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
try:
    os.fsync(directory)
finally:
    os.close(directory)
print(
    "legacy harness coordination disabled; inspect and disposition run-owned resources "
    "before close, then start a new run under factory-harness/2"
)
PY
