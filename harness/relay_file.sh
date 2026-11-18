#!/usr/bin/env bash
# relay_file.sh — relay multi-line text into a seat by file + sha256 (dogfood #20, #24).
# Multi-line text typed into a seat's tmux input is mangled: line breaks submit or split it,
# the first line or first character is dropped, and a signing statement can silently lose a
# line. So the text never rides the terminal: it is written verbatim to a content-addressed
# file under the run, and only a one-line pointer (path + sha256) is sent, through inject.sh,
# so topology, content guards and receipts all still apply. The seat recomputes the digest
# before acting (prompts/validate.md, "Relaying multi-line text into a seat").
# With --answer <escalation-key>, the relay is the human's answer to that escalation: it goes to
# the Validator through its input path (validator_input, kind escalation-response), recorded as
# owed and told once; exit 75 means recorded and still owed, retried by the next delivery.
# usage: relay_file.sh <run> <to-window> [--file <path>] [--answer <escalation-key>]
#        (message from stdin by default)
set -euo pipefail
RUN="${1:?usage: relay_file.sh <run> <to> [--file <path>] [--answer <key>]}"; TO="${2:?to}"
shift 2
SRC=-; ANSWER=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --file) SRC="${2:?--file needs a path}"; shift 2 ;;
    --answer) ANSWER="${2:?--answer needs the escalation key}"; shift 2 ;;
    *) echo "relay: unexpected argument: $1" >&2; exit 64 ;;
  esac
done
if [ -n "$ANSWER" ] && [ "$TO" != validator ]; then
  echo "relay: --answer goes to the validator only" >&2; exit 64
fi
if [ -n "$ANSWER" ] && ! [[ "$ANSWER" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$ ]]; then
  echo "relay: --answer takes an escalation key (letters, digits, . _ -)" >&2; exit 64
fi
D="$(cd "$(dirname "$0")" && pwd -P)"
ROOT="${HARNESS_RUN_ROOT:-${HARNESS_DIR:-.factory}/runs/$RUN}"

mkdir -p "$ROOT/relay"
TMP="$(mktemp "$ROOT/relay/.incoming.XXXXXX")"
trap 'rm -f "$TMP"' EXIT
if [ "$SRC" = "-" ]; then cat > "$TMP"; else cat < "$SRC" > "$TMP"; fi
[ -s "$TMP" ] || { echo "relay: empty message" >&2; exit 64; }
DIGEST=$(shasum -a 256 < "$TMP" | cut -d' ' -f1)
# The content guards (topology, coder verdict/oracle-leak filters) judge the file's bytes, not
# just the pointer: a pointer would otherwise carry any text past them.
# Relays are text. A NUL cannot ride argv, so the guards would judge different bytes than the
# seat receives; refuse it instead.
if [ "$(LC_ALL=C tr -d '\000' < "$TMP" | wc -c)" -ne "$(wc -c < "$TMP")" ]; then
  echo "relay refusal: binary content (NUL bytes); relays carry text only" >&2
  exit 80
fi
# ponytail: the bytes ride argv here, so a file past the OS argv limit is refused, not relayed.
INJECT_VALIDATE_ONLY=1 "$D/inject.sh" "$RUN" "$TO" "$(cat "$TMP")" >/dev/null
FILE="$(cd "$ROOT/relay" && pwd -P)/$DIGEST.txt"
chmod 0444 "$TMP"; mv -f "$TMP" "$FILE"
printf '{"ts":"%s","run":"%s","from":"%s","to":"%s","kind":"relay-file","sha256":"%s"}\n' \
  "$(date -u +%FT%TZ)" "$RUN" "${INJECT_FROM:-validator}" "$TO" "$DIGEST" >> "$ROOT/injections.jsonl"

# The pointer must stay one line and under inject.sh's delivery limit; the path is shell-quoted
# so the verification command reads the right file whatever the run root's name.
printf -v QFILE '%q' "$FILE"
POINTER="FACTORY_RELAY file=$QFILE sha256=$DIGEST -- run: shasum -a 256 < $QFILE ; act on the file only if the digest equals this sha256, else do not act and tell the sender"
if [ -n "$ANSWER" ]; then
  source "$D/factory_python.sh"
  exec "$FACTORY_PYTHON" - "$D" "$ROOT" "answer:$ANSWER:${DIGEST:0:12}" "${INJECT_FROM:-founder}" \
    "Answer to $ANSWER: $POINTER" <<'PYNOTIFY'
import pathlib, sys
sys.path.insert(0, sys.argv[1])
import validator_input
from run_record import SUPERVISOR_WAIT_S
ident = sys.argv[3]
try:
    told = validator_input.notify(pathlib.Path(sys.argv[2]), ident, "escalation-response",
                                  sys.argv[4], sys.argv[5], wait_s=SUPERVISOR_WAIT_S)
except validator_input.LedgerUnreadable as error:
    print(f"relay: {ident} is recorded; whether it was delivered is unknown: {error}",
          file=sys.stderr)
    sys.exit(74)
except validator_input.NoticeIdReused as error:
    print(f"relay: {error}", file=sys.stderr)
    sys.exit(65)
if not told:
    print(f"relay: {ident} is recorded and still owed to the Validator; the next delivery "
          "retries it", file=sys.stderr)
    sys.exit(75)
print(f"relayed -> validator as {ident} (delivered to its inbox; not an acknowledgment)")
PYNOTIFY
fi
exec "$D/inject.sh" "$RUN" "$TO" "$POINTER"
