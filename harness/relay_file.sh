#!/usr/bin/env bash
# relay_file.sh — relay multi-line text into a seat by file + sha256 (dogfood #20, #24).
# Multi-line text typed into a seat's tmux input is mangled: line breaks submit or split it,
# the first line or first character is dropped, and a signing statement can silently lose a
# line. So the text never rides the terminal: it is written verbatim to a content-addressed
# file under the run, and only a one-line pointer (path + sha256) is sent, through inject.sh,
# so topology, content guards and receipts all still apply. The seat recomputes the digest
# before acting (prompts/validate.md, "Relaying multi-line text into a seat").
# usage: relay_file.sh <run> <to-window> [--file <path>]     (message from stdin by default)
set -euo pipefail
RUN="${1:?usage: relay_file.sh <run> <to> [--file <path>]}"; TO="${2:?to}"; shift 2
SRC=-
if [ "${1:-}" = "--file" ]; then SRC="${2:?--file needs a path}"; shift 2; fi
[ "$#" -eq 0 ] || { echo "relay: unexpected argument: $1" >&2; exit 64; }
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
exec "$D/inject.sh" "$RUN" "$TO" "FACTORY_RELAY file=$QFILE sha256=$DIGEST -- run: shasum -a 256 < $QFILE ; act on the file only if the digest equals this sha256, else do not act and tell the sender"
