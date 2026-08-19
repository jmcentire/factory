#!/usr/bin/env bash
# consume_block.sh — the off-ramp for the blocking-event attention channel.
#
# A blocking event (written by orchestrator_wake.sh or dispatcher.py _block) is a
# control signal: class + evidence, never prose. It gates lane dispatch —
# dispatch_lane.sh refuses to dispatch while lanes/validator.blocking (or the
# lane's own) is pending, so the validator cannot start new work past an attention
# signal it has not consumed. Without an off-ramp that control is a deadlock, not
# a control: the file only grows. This script is the consumption path.
#
# It reads the pending events, requires a structured consequence and its evidence,
# receipts EACH into events.jsonl, then durably truncates the file to release the
# dispatch gate. Reading is not a disposition; an advisory signal can only be
# stopped, narrowed, escalated, refuted, or resolved with an evidence address.
#
#   usage: consume_block.sh <run> <lane> --disposition <kind>
#          --reason <bounded text> --subject-digest sha256:<hex>
#          --evidence-file <run-retained-path> --evidence-digest sha256:<hex>
set -euo pipefail
RUN="${1:?usage: consume_block.sh <run> <lane>}"; LANE="${2:?lane}"
shift 2
[[ "$RUN" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || {
  echo "consume_block: invalid run identity" >&2; exit 64;
}
case "$LANE" in validator|coder|tester) ;;
  *) echo "consume_block: invalid lane identity" >&2; exit 64 ;;
esac
DISPOSITION=""; REASON=""; SUBJECT_DIGEST=""; EVIDENCE_FILE=""; EVIDENCE_DIGEST=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --disposition) DISPOSITION="$2"; shift 2 ;;
    --reason) REASON="$2"; shift 2 ;;
    --subject-digest) SUBJECT_DIGEST="$2"; shift 2 ;;
    --evidence-file) EVIDENCE_FILE="$2"; shift 2 ;;
    --evidence-digest) EVIDENCE_DIGEST="$2"; shift 2 ;;
    *) echo "consume_block: unknown argument: $1" >&2; exit 64 ;;
  esac
done
case "$DISPOSITION" in stop|narrow|escalate|refute|resolve) ;;
  *) echo "consume_block: a valid disposition is required" >&2; exit 64 ;;
esac
[ -n "$REASON" ] && [ "${#REASON}" -le 4096 ] || {
  echo "consume_block: a bounded disposition reason is required" >&2; exit 64;
}
[[ "$EVIDENCE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || {
  echo "consume_block: a canonical disposition evidence digest is required" >&2; exit 64;
}
[[ "$SUBJECT_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || {
  echo "consume_block: the exact blocking subject digest is required" >&2; exit 64;
}
[ -n "$EVIDENCE_FILE" ] || {
  echo "consume_block: a retained disposition evidence file is required" >&2; exit 64;
}
ROOT="${HARNESS_RUN_ROOT:-${HARNESS_DIR:-.factory}/runs/$RUN}"
D="$(cd "$(dirname "$0")" && pwd -P)"
BF="$ROOT/lanes/$LANE.blocking"
EV="$ROOT/events.jsonl"
[ -s "$BF" ] || { echo "no blocking event pending for $LANE" >&2; exit 0; }
n=$(python3 - "$BF" "$EV" "$ROOT" "$LANE" "$DISPOSITION" "$REASON" \
  "$SUBJECT_DIGEST" "$EVIDENCE_FILE" "$EVIDENCE_DIGEST" "$D" <<'PY'
import datetime, fcntl, hashlib, json, os, pathlib, secrets, stat, sys

blocking_path, events_path = map(pathlib.Path, sys.argv[1:3])
root = pathlib.Path(sys.argv[3])
lane, disposition, reason, subject_digest, evidence_file, evidence_digest = sys.argv[4:10]
harness_root = pathlib.Path(sys.argv[10])
sys.path.insert(0, str(harness_root))
from attention_gate import AttentionGateError, acquire_attention_lock, validate_blocking_event

MAX_BLOCKING_BYTES = 1_048_576
MAX_EVIDENCE_BYTES = 1_048_576

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))

def identity(metadata):
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )

def stable_regular(path, maximum, label):
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SystemExit(f"{label} is not a regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw) > maximum:
        raise SystemExit(f"{label} exceeds {maximum} bytes")
    if identity(before) != identity(after):
        raise SystemExit(f"{label} changed while being admitted")
    return raw

def open_directory(path):
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise SystemExit(f"unsafe evidence directory: {path}")
    return descriptor

def ensure_directory_chain(parent, base):
    current = base
    for component in parent.relative_to(base).parts:
        parent_fd = open_directory(current)
        child = current / component
        try:
            try:
                os.mkdir(child, 0o700)
            except FileExistsError:
                pass
            child_fd = open_directory(child)
            try:
                os.fsync(child_fd)
                os.fsync(parent_fd)
            finally:
                os.close(child_fd)
        finally:
            os.close(parent_fd)
        current = child

def retain_evidence(raw, digest):
    destination_dir = root / "evidence" / "blocking-dispositions"
    ensure_directory_chain(destination_dir, root)
    destination = destination_dir / f"{digest.removeprefix('sha256:')}.evidence"
    pending = destination_dir / f".pending-{os.getpid()}-{secrets.token_hex(8)}"
    pending_fd = os.open(
        pending,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(pending_fd, "wb") as stream:
            pending_fd = -1
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(pending, destination, follow_symlinks=False)
        except FileExistsError:
            existing = stable_regular(destination, MAX_EVIDENCE_BYTES, "retained evidence")
            if existing != raw:
                raise SystemExit("retained evidence address contains different bytes")
        retained_fd = os.open(
            destination,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            if not stat.S_ISREG(os.fstat(retained_fd).st_mode):
                raise SystemExit("retained evidence is not regular")
            os.fsync(retained_fd)
        finally:
            os.close(retained_fd)
        directory_fd = open_directory(destination_dir)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if pending_fd >= 0:
            os.close(pending_fd)
        try:
            os.unlink(pending)
        except FileNotFoundError:
            pass
        directory_fd = open_directory(destination_dir)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return destination.relative_to(root).as_posix()

if not reason.strip() or len(reason.encode("utf-8")) > 4096:
    raise SystemExit("disposition reason is empty or exceeds 4096 bytes")
try:
    root_resolved = root.resolve(strict=True)
    requested_evidence = pathlib.Path(evidence_file)
    if not requested_evidence.is_absolute():
        requested_evidence = root / requested_evidence
    evidence_path = requested_evidence.resolve(strict=True)
    evidence_path.relative_to(root_resolved)
except (FileNotFoundError, OSError, ValueError) as exc:
    raise SystemExit("disposition evidence must be an existing file under the run root") from exc

attention_fd = acquire_attention_lock(root)
blocking_flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
blocking_fd = os.open(blocking_path, blocking_flags)
if not stat.S_ISREG(os.fstat(blocking_fd).st_mode):
    os.close(blocking_fd)
    raise SystemExit("blocking source is not a regular file")
with os.fdopen(blocking_fd, "r+b") as blocking:
    fcntl.flock(blocking, fcntl.LOCK_EX)
    blocking.seek(0)
    blocking_raw = blocking.read(MAX_BLOCKING_BYTES + 1)
    if len(blocking_raw) > MAX_BLOCKING_BYTES:
        raise SystemExit(f"blocking source exceeds {MAX_BLOCKING_BYTES} bytes")
    actual_subject_digest = "sha256:" + hashlib.sha256(blocking_raw).hexdigest()
    if actual_subject_digest != subject_digest:
        raise SystemExit("blocking subject changed before disposition")
    try:
        blocking_text = blocking_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SystemExit("blocking source is not UTF-8") from exc
    events = []
    for number, line in enumerate(blocking_text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"blocking event {number} is not JSON: {exc}") from exc
        if not isinstance(event, dict):
            raise SystemExit(f"blocking event {number} is not an object")
        try:
            events.append(validate_blocking_event(event))
        except AttentionGateError as exc:
            raise SystemExit(f"blocking event {number} is invalid: {exc}") from exc
    if not events:
        raise SystemExit("blocking source contains no events")
    event_digests = [
        "sha256:" + hashlib.sha256(canonical(event).encode("utf-8")).hexdigest()
        for event in events
    ]
    if len(event_digests) != len(set(event_digests)):
        raise SystemExit("blocking source repeats an event identity")
    evidence_raw = stable_regular(evidence_path, MAX_EVIDENCE_BYTES, "disposition evidence")
    actual_evidence_digest = "sha256:" + hashlib.sha256(evidence_raw).hexdigest()
    if actual_evidence_digest != evidence_digest:
        raise SystemExit("disposition evidence differs from its supplied digest")
    evidence_id = retain_evidence(evidence_raw, evidence_digest)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    event_flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    event_fd = os.open(events_path, event_flags, 0o600)
    if not stat.S_ISREG(os.fstat(event_fd).st_mode):
        os.close(event_fd)
        raise SystemExit("events sink is not a regular file")
    with os.fdopen(event_fd, "r+", encoding="utf-8") as sink:
        fcntl.flock(sink, fcntl.LOCK_EX)
        sink.seek(0)
        prior = {}
        for number, line in enumerate(sink, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"events ledger row {number} is not JSON: {exc}") from exc
            if record.get("kind") == "blocking_consumed" and record.get("lane") == lane:
                prior[str(record.get("event_digest", ""))] = record
        timestamp = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
        sink.seek(0, os.SEEK_END)
        for event, event_digest in zip(events, event_digests, strict=True):
            stable = {
                "kind": "blocking_consumed",
                "lane": lane,
                "event": event,
                "event_digest": event_digest,
                "blocking_subject_digest": subject_digest,
                "disposition": disposition,
                "disposition_reason": reason,
                "disposition_evidence_id": evidence_id,
                "disposition_evidence_digest": evidence_digest,
                "disposition_evidence_byte_count": len(evidence_raw),
            }
            existing = prior.get(event_digest)
            if existing is not None:
                comparable = {key: existing.get(key) for key in stable}
                if comparable != stable:
                    raise SystemExit("blocking event was already dispositioned differently")
                continue
            sink.write(canonical({"ts": timestamp, **stable}) + "\n")
        sink.flush()
        os.fsync(sink.fileno())
    # The receipt pathname must be durable before the gate can be cleared. Otherwise a crash on
    # first use could preserve the fsynced truncation while losing the newly created ledger entry.
    event_directory = open_directory(events_path.parent)
    try:
        os.fsync(event_directory)
    finally:
        os.close(event_directory)
    if os.environ.get("FACTORY_TEST_CONSUME_CRASH_AFTER_RECEIPT_SYNC") == "1":
        raise SystemExit("injected crash after durable receipt and before gate clear")
    blocking.seek(0)
    blocking.truncate(0)
    blocking.flush()
    os.fsync(blocking.fileno())

for directory_path in dict.fromkeys((blocking_path.parent, events_path.parent)):
    directory = os.open(
        directory_path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
os.close(attention_fd)
print(len(events))
PY
)
echo "dispositioned $n blocking event(s) for $LANE; dispatch gate released"
