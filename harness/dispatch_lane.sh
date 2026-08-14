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

# Existence is not adequacy. Run v8 launched with all four Phase-A artifacts
# present and signed, then took six amendments authored WHILE the lanes coded —
# one retracting the one before it. The checks above could not have caught that,
# because they only ask whether the files are there. phase1_gate.sh asks whether
# they did a specification's work, and refuses the dispatch when they did not.
"$D/phase1_gate.sh" "$RUN" --repo "$(python3 -c "import json;print(json.load(open('$ROOT/run.json'))['repo'])")" \
  || fail "phase1 adequacy gate refused (see output above; PHASE1_ALLOW_GAPS=1 records a receipted gap)"

# A signed artifact may point a lane at something its projection can never hold.
# v8 discovered exactly that AFTER dispatch, mid-run, at the cost of a stall and a
# round trip — though both inputs, the include list and the artifact, were on disk
# before launch and nothing compared them. Reachability, not existence: a test the
# lane is about to write does not exist yet and must not trip this.
if [ "$ROLE" = "tester" ]; then
  (cd "$(python3 -c "import json;print(json.load(open('$ROOT/run.json'))['repo'])")" \
     && "$D/projection_receipt.sh" tester "$ART/testing-strategy.md") \
    || fail "projection receipt refused: the strategy names paths outside the tester's declared view"
fi

# Blocking-event gate (founder refinement — the time-kill). The orchestrator/
# dispatcher gets the validator's attention by writing a blocking event; this gate
# refuses to dispatch ANY lane while the validator has an unconsumed event (or
# this lane does), so the validator cannot start new work past an attention signal
# it has not consumed. The off-ramp is harness/consume_block.sh, which receipts
# the consumption into events.jsonl and clears the file. This is the production
# enforcement site — lane_env.sh enforces the same precondition once lanes route
# through it; dispatch_lane.sh is the per-task path that fires today.
for _bf in "$ROOT/lanes/validator.blocking" "$ROOT/lanes/$ROLE.blocking"; do
  if [ -s "$_bf" ]; then
    echo "blocking event pending — consume before dispatching:" >&2
    head -3 "$_bf" | sed 's/^/  /' >&2
    echo "  run: harness/consume_block.sh $RUN validator  (then $ROLE if its own is pending)" >&2
    exit 81
  fi
done

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
# ollama drives a coding CLI through Ollama's own integration launcher; the model
# must be explicit or it refuses headless ("model selection requires an interactive
# terminal"). The TUI is launched and the brief is delivered by inject.sh, matching
# how every other lane receives work, so one delivery path stays authoritative.
OLLAMA_MODEL="${OLLAMA_LANE_MODEL:-glm-5.2:cloud}"
case "$AGENT" in
  claude) LANE_CMD="claude \"$SKILL - $BRIEF\"" ;;
  # A lane's tool policy cannot be enforced by dispatch TEXT: an agent obeys its
  # global startup instructions BEFORE it reads any dispatch. In run v8 the codex
  # tester correctly reported that its oracle was contaminated before DISPATCH.md
  # could be opened — the operator's global AGENTS.md mandates a knowledge-graph
  # tag+search "in every session", and that graph holds the defect detail under
  # test. Isolation must therefore be established at LAUNCH: a lane-only home with
  # no MCP servers and lane-appropriate global instructions.
  codex)  LANE_CMD="CODEX_HOME='${CODEX_LANE_HOME:-$HOME/.codex-lane}' codex \"$ROLE_BRIEF\"" ;;
  gemini) LANE_CMD="agy -i \"$ROLE_BRIEF\"" ;;
  ollama) LANE_CMD="ollama launch opencode --model '$OLLAMA_MODEL'" ;;
  *) echo "unknown --agent '$AGENT' (claude|codex|gemini|ollama)" >&2; exit 64 ;;
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

# Agents that take no initial prompt on the command line get the brief delivered
# through the one sanctioned, receipted, delivery-hardened path.
if [ "$AGENT" = "ollama" ]; then
  sleep 8   # let the TUI finish binding the model before the first keystroke
  "$D/inject.sh" "$RUN" "$ROLE" "$ROLE_BRIEF" >/dev/null || \
    echo "WARNING: brief delivery to $ROLE failed — inject it manually" >&2
fi
echo "lane '$ROLE' launched in $RUN from $WS @ $SHA (agent: $AGENT)"
