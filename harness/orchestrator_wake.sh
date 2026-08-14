#!/usr/bin/env bash
# orchestrator_wake.sh — the orchestrator-agent is invoked, not resident.
# Builds the projection (triggering event + receipt-chain tail + active directives),
# records WHICH ids the agent saw (so "what did it know when it decided" replays),
# then invokes /orchestrate headless. The agent speaks only to the Validator: its
# output is delivered via inject.sh with INJECT_FROM=orchestrator, which the
# topology in inject.sh permits toward the validator window alone.
set -euo pipefail
RUN="${1:?usage: orchestrator_wake.sh <run> <trigger-json>}"
TRIGGER="${2:?trigger json}"
H="${HARNESS_DIR:-.factory}"; ROOT="$H/runs/$RUN"
D="$(cd "$(dirname "$0")" && pwd)"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
PROJ="$ROOT/wakes/$TS.projection.md"
mkdir -p "$ROOT/wakes"

{ echo "# Orchestrator wake projection — $TS"
  echo "## Trigger"
  echo '```json'; printf '%s\n' "$TRIGGER"; echo '```'
  echo "## The task, verbatim"
  cat "$ROOT/TASK.md" 2>/dev/null || echo "(no TASK.md)"
  echo "## Phase artifacts on record"
  ls -la "$ROOT/artifacts" 2>/dev/null || echo "(none ratified yet)"
  echo "## Receipt-chain tail (last 15)"
  tail -15 "$H/receipts/chain.jsonl" 2>/dev/null || echo "(no receipts yet)"
  echo "## Recent events (last 25)"
  tail -25 "$ROOT/events.jsonl" 2>/dev/null || echo "(no events)"
  echo "## Recent minutes (non-authoritative, [INFERRED])"
  tail -40 "$ROOT"/minutes/*.log 2>/dev/null || echo "(no minutes)"
  echo "## Active directives"
  python3 "$D/directive.py" active 2>/dev/null || echo "(no ledger)"
  echo "## Run"
  cat "$ROOT/run.json"
} > "$PROJ"

printf '{"ts":"%s","projection":"%s","projection_sha256":"%s"}\n' \
  "$(date -u +%FT%TZ)" "$PROJ" "$(shasum -a 256 "$PROJ" | cut -d' ' -f1)" \
  >> "$ROOT/wakes/receipts.jsonl"

# The orchestrator agent is a PARAMETER (ORCH_AGENT=claude|gemini|codex). Batch0 ran
# it on the same family as the Validator it audits — an auditor sharing its subject's
# frame is the weakest possible arrangement — and lost the seat entirely when that
# one account hit a spend cap. A different family is both better independence and an
# independent failure domain.
ORCH_AGENT="${ORCH_AGENT:-claude}"
case "$ORCH_AGENT" in
  claude) ORCH_CMD=(claude -p) ;;
  gemini) ORCH_CMD=(agy --dangerously-skip-permissions -p) ;;
  codex)  ORCH_CMD=(codex exec --sandbox read-only) ;;
  *) echo "unknown ORCH_AGENT '$ORCH_AGENT' (claude|gemini|codex)" >&2; exit 64 ;;
esac

set +e
OUT=$("${ORCH_CMD[@]}" "/orchestrate - you are woken for one exception, not residency. \
Your seat is strategy and process; the Validator's is tactics. Your charter: the \
run stays pointed at what the founder asked for. The failure modes you audit are \
the Validator's, not the lanes': (1) announced work with no receipt — saying is \
not doing; (2) triumvirate bypass — the Validator coding or testing itself while \
lanes idle; (3) authority misattribution — claims that resolve to no ledger entry \
get the sentence 'I cannot find where you said this' and a stop; (4) inversion — \
doing the opposite of the recorded ask; (5) hyper-focus — polishing what the \
founder doesn't care about while the ask sits. Also audit cleanup debt: branches, \
stashes, worktrees, PRs, and unstaged changes accruing beyond the run's needs — \
accrual is almost never what the founder wants. The projection is at $PROJ (read \
it; the repo's design docs under docs/ are yours to read; pull artifacts by \
receipt id if the projection is insufficient). You FLAG, never gate: reply with \
the single message the Validator needs, or 'ESCALATE TO HUMAN: <why>' if only the \
founder can resolve it. You hold zero grant authority; you speak to the Validator \
only." 2>/dev/null)
ORCH_RC=$?
set -e
[ -n "$OUT" ] || OUT="(orchestrator invocation failed)"

# An auditor that cannot be shown to have run is not an auditor. v8 sent five wakes
# whose prompt was a stray flag; every reply was the model asking what was wanted, and
# nothing detected it because only emptiness was checked.
#
# It then did it AGAIN, through this very control. The invocation was wrapped in
# `|| echo "(orchestrator invocation failed)"`, which discarded the exit status and
# produced a non-empty string that matched none of the clarify-phrases below — so a
# failed invocation was written out as a normal response and counted as a live audit.
# Five of v8's sixteen wakes failed that way with ZERO dead-wake records, across the
# entire endgame, while this check reported itself healthy. The lesson is the one the
# run kept relearning: the detector watched for symptoms it IMAGINED (phrasings) and
# never for the failure it was built to catch (the command not running). Status is now
# read from the exit code, which cannot be paraphrased, with the string match kept only
# as a secondary net for a command that exits 0 while refusing to work.
mkdir -p "$ROOT/lanes"
if [ "$ORCH_RC" -ne 0 ] || [ -z "$OUT" ] \
   || printf '%s' "$OUT" | grep -qiE "orchestrator invocation failed|clarify what|no surrounding command|didn't include the command|what would you like me to do"; then
  printf '{"ts":"%s","wake":"%s","status":"ORCHESTRATOR_DID_NOT_RUN","excerpt":"%s"}\n' \
    "$(date -u +%FT%TZ)" "$TS" "$(printf '%s' "$OUT" | tr -d '"' | tr '\n' ' ' | cut -c1-120)" \
    >> "$ROOT/wakes/receipts.jsonl"
  echo "ORCHESTRATOR DID NOT RUN at $TS — invocation produced no audit" >&2
  # Attention, not shepherding: a blocking event the lane cannot run past, carrying
  # its class and evidence (the wake id), not prose injected into the validator's
  # pane mid-reasoning. Shepherding contaminates (METHODOLOGY.md: -22:1 with reset);
  # a pane injection is also a surface that stays warm after the seat behind it is
  # dead. lane_env enforces this as a precondition; the validator consumes it
  # between tasks and clears it. This is the founder's time-kill fix — the
  # orchestrator can get the validator's attention and move work along — without
  # reintroducing the contaminating channel.
  _evt=$(printf '{"ts":"%s","class":"orchestrator_dead","wake":"%s","excerpt":"%s"}' \
    "$(date -u +%FT%TZ)" "$TS" "$(printf '%s' "$OUT" | tr -d '"' | tr '\n' ' ' | cut -c1-120)")
  printf '%s\n' "$_evt" >> "$ROOT/lanes/validator.blocking"
  # Receipt the write so a silent clear is visible by its absence (see _block).
  printf '{"ts":"%s","kind":"blocking_written","lane":"validator","event":%s}\n' \
    "$(date -u +%FT%TZ)" "$_evt" >> "$ROOT/events.jsonl"
fi

if [ -n "$OUT" ]; then
  printf '%s\n' "$OUT" > "$ROOT/wakes/$TS.response.md"
  # The orchestrator's reply lives in a file the validator reads at a defined
  # checkpoint, never injected into its pane mid-reasoning. Attention is a
  # control-plane precondition lane_env enforces, not a nudge typed into the
  # reasoning stream.
  _evt=$(printf '{"ts":"%s","class":"orchestrator_response","response":"%s","wake":"%s"}' \
    "$(date -u +%FT%TZ)" "$ROOT/wakes/$TS.response.md" "$TS")
  printf '%s\n' "$_evt" >> "$ROOT/lanes/validator.blocking"
  printf '{"ts":"%s","kind":"blocking_written","lane":"validator","event":%s}\n' \
    "$(date -u +%FT%TZ)" "$_evt" >> "$ROOT/events.jsonl"
fi
