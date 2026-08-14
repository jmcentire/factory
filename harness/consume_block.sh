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
# It reads the pending events, receipts EACH into events.jsonl as a
# blocking_consumed record (so clearing-without-reading is visible by its
# absence — a lane that `rm`s the file leaves no consumption receipt), then
# atomically truncates the file to release the dispatch gate. The validator calls
# it at a between-tasks checkpoint.
#
#   usage: consume_block.sh <run> <lane>
set -euo pipefail
RUN="${1:?usage: consume_block.sh <run> <lane>}"; LANE="${2:?lane}"
H="${HARNESS_DIR:-.factory}"; BF="$H/runs/$RUN/lanes/$LANE.blocking"
EV="$H/runs/$RUN/events.jsonl"
[ -s "$BF" ] || { echo "no blocking event pending for $LANE" >&2; exit 0; }
# Receipt each event into events.jsonl as a blocking_consumed record, then
# atomically truncate. The receipt is written by python (not printf '%s') so a
# non-JSON line in <lane>.blocking — which a process with filesystem access
# could place there — cannot corrupt the ledger: it is parsed and, on failure,
# embedded as an escaped string under event_raw with a parse_error flag, keeping
# events.jsonl well-formed for every downstream reader (postmortem.py, status.sh).
n=$(python3 - "$BF" "$EV" "$LANE" <<'PY'
import json, sys, datetime
bf, ev, lane = sys.argv[1:4]
lines = [l for l in open(bf).read().splitlines() if l.strip()]
ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
with open(ev, "a") as out:
    for line in lines:
        try:
            event = json.loads(line)
            rec = {"ts": ts, "kind": "blocking_consumed", "lane": lane, "event": event}
        except json.JSONDecodeError:
            rec = {"ts": ts, "kind": "blocking_consumed", "lane": lane,
                   "event_raw": line, "parse_error": True}
        out.write(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n")
print(len(lines))
PY
)
: > "$BF"   # atomic truncate: the dispatch gate is released
echo "consumed $n blocking event(s) for $LANE; dispatch gate released"