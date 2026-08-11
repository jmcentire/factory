#!/usr/bin/env bash
# dispatch_lane.sh — a lane exists only downstream of a complete authority tuple.
# Validates the tuple (signed phase artifacts + digests + dispatch file), mints the
# lane's asymmetric projection, and opens the tmux window running the lane skill.
# Any missing member = "no oracle yet" — the lane is not launched (tester Gate 1).
# usage: dispatch_lane.sh <run> <coder|tester> --dispatch <file> [--sha <sha>]
set -euo pipefail
RUN="${1:?usage: dispatch_lane.sh <run> <coder|tester> --dispatch <file> [--sha <sha>]}"
ROLE="${2:?role}"; shift 2
DISPATCH=""; SHA=""; AGENT=""
while [ $# -gt 0 ]; do case "$1" in
  --dispatch) DISPATCH="$2"; shift 2 ;;
  --sha) SHA="$2"; shift 2 ;;
  --agent) AGENT="$2"; shift 2 ;;   # claude|codex|gemini (see lane launch below)
  *) echo "unknown arg: $1" >&2; exit 64 ;;
esac; done
H="${HARNESS_DIR:-.harness}"; ROOT="$H/runs/$RUN"
D="$(cd "$(dirname "$0")" && pwd)"
case "$ROLE" in coder|tester) ;; *) echo "role must be coder|tester" >&2; exit 64 ;; esac

fail() { echo "no oracle yet — $1" >&2; exit 70; }
[ -f "$ROOT/run.json" ] || fail "run '$RUN' has no run.json (harness/factory.sh first)"
[ -n "$DISPATCH" ] && [ -s "$DISPATCH" ] || fail "empty or missing --dispatch file (the \
Validator authors the dispatch: verbatim requirement + directive id, artifact digests, \
ratification receipt, operating mode, interface allowlist, confirmed interpretation)"

ART="$ROOT/artifacts"
need() { [ -s "$ART/$1" ] || fail "missing signed artifact: $ART/$1"; }
need product-specification.md; need product-specification.md.digest
need architecture.md; need architecture.md.digest
if [ "$ROLE" = "tester" ]; then
  need testing-strategy.md; need testing-strategy.md.digest
fi
grep -q "interpretation_confirmed: true" "$DISPATCH" || \
  fail "dispatch lacks 'interpretation_confirmed: true' (restatement gate, control 2)"

REPO=$(python3 -c "import json;print(json.load(open('$ROOT/run.json'))['repo'])")
[ -n "$SHA" ] || SHA=$(python3 -c "import json;print(json.load(open('$ROOT/run.json'))['base_sha'])")
WS="$ROOT/workspaces/$ROLE"
PROJ=$("$D/projection.sh" "$ROLE" "$REPO" "$SHA" "$WS")

RECEIPT="$ROOT/dispatches.jsonl"
python3 - "$RECEIPT" "$RUN" "$ROLE" "$SHA" "$DISPATCH" "$PROJ" <<'PY'
import hashlib, json, sys, datetime
rec, run, role, sha, dispatch, proj = sys.argv[1:7]
body = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "run": run, "role": role, "sha": sha,
        "dispatch_digest": hashlib.sha256(open(dispatch, "rb").read()).hexdigest(),
        "projection": json.loads(proj)}
with open(rec, "a") as f:
    f.write(json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n")
print("dispatch receipt:", body["dispatch_digest"][:12])
PY

cp "$DISPATCH" "$WS/DISPATCH.md"
SKILL=$([ "$ROLE" = "coder" ] && echo "/engineer" || echo "/test")
BRIEF="read DISPATCH.md in this directory; it is your dispatch. Work only from it and the signed artifacts it cites."
ROLE_BRIEF="$BRIEF You are the $ROLE lane: you hold one pen only, you never see the other lane's work, and your only upward channel is a question, a failure report, or a specification defect."

# The lane agent is a PARAMETER because §6 grades independence by model family and
# names different families across Coder and Tester as the cheap available
# improvement, "worth taking where the option exists". A hardcoded CLI made that
# option unreachable: batch0 ran both lanes on one family and could only ever
# record the weaker tier, while the alternative CLIs sat installed on the same box.
AGENT="${AGENT:-claude}"
case "$AGENT" in
  claude) LANE_CMD="claude \"$SKILL - $BRIEF\"" ;;
  codex)  LANE_CMD="codex \"$ROLE_BRIEF\"" ;;
  gemini) LANE_CMD="agy -i \"$ROLE_BRIEF\"" ;;
  *) echo "unknown --agent '$AGENT' (claude|codex|gemini)" >&2; exit 64 ;;
esac

# The derived independence tier is only auditable if the manifest records what
# actually launched, so stamp the agent onto this lane's dispatch receipt.
python3 -c "
import json, sys
rec, agent = sys.argv[1], sys.argv[2]
lines = [l for l in open(rec).read().splitlines() if l.strip()]
body = json.loads(lines[-1]); body['agent'] = agent
lines[-1] = json.dumps(body, sort_keys=True, separators=(',', ':'))
open(rec, 'w').write('\n'.join(lines) + '\n')
" "$RECEIPT" "$AGENT"

tmux new-window -t "$RUN" -n "$ROLE" -c "$WS" "$LANE_CMD"
echo "lane '$ROLE' launched in $RUN from $WS @ $SHA (agent: $AGENT)"
