#!/usr/bin/env bash
# proof.sh — "click and test" is a property the run proves, not promises.
# The target declares how its world comes up and what demonstrates it working:
# .harness/target.conf supplies a provision script, probes (entry points exercised
# for real — HTTP hits, CLI invocations, DB checks out of band, screenshot/video
# capture commands), access instructions, and teardown. The harness owns the
# requirement and the receipts; the target owns the probes (control 9's division).
# Every probe's exit code and output land under the run's proof/ directory and in
# the receipt chain. No target.conf = a declared gap (exit 64), never a quiet pass.
#
# .harness/target.conf format (one per line):
#   provision: <script>        # brings the environment up; env PROOF_DIR is set
#   teardown:  <script>        # always runs, even after failures
#   access:    <path-or-text>  # where a human finds entry points + credentials doc
#   probe: <name>:: <command>  # exercised via receipt.sh; nonzero = proof failure
# usage: proof.sh <run>
set -uo pipefail
RUN="${1:?usage: proof.sh <run>}"
H="${HARNESS_DIR:-.harness}"; ROOT="$H/runs/$RUN"
CONF="${HARNESS_TARGET_CONF:-$H/target.conf}"
D="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "$CONF" ]; then
  echo "no target.conf — the factory cannot prove 'done' on a system it cannot" >&2
  echo "bring up. Declare provision/probe/access/teardown in $CONF." >&2
  echo "This is a declared gap, not a pass." >&2
  exit 64
fi

conf_one() { grep -E "^$1:" "$CONF" | head -1 | sed "s/^$1:[[:space:]]*//"; }
PROVISION=$(conf_one provision); TEARDOWN=$(conf_one teardown); ACCESS=$(conf_one access)
[ -n "$PROVISION" ] || { echo "target.conf declares no provision: script" >&2; exit 64; }

PROOF="$ROOT/proof"; mkdir -p "$PROOF"
export PROOF_DIR="$PROOF"
FAILED=0

echo "== provision =="
if ! "$D/receipt.sh" bash -c "$PROVISION"; then
  echo "provision failed — nothing to prove against" >&2
  FAILED=1
fi

if [ "$FAILED" -eq 0 ]; then
  echo "== probes (each receipted; output kept as evidence) =="
  while IFS= read -r line; do
    NAME="${line%%::*}"; CMD="${line#*::}"
    NAME=$(printf '%s' "$NAME" | sed 's/^probe:[[:space:]]*//; s/[[:space:]]*$//')
    printf -- '-- probe: %s\n' "$NAME"
    RECEIPT_LINE=$("$D/receipt.sh" bash -c "$CMD"); rc=$?
    RID=${RECEIPT_LINE%% *}
    # The probe's real output is the receipt's log — copy it out as the evidence
    # artifact so a human can read proof without walking the chain.
    cp "$H/receipts/$RID.log" "$PROOF/$NAME.out" 2>/dev/null \
      || printf '%s\n' "$RECEIPT_LINE" > "$PROOF/$NAME.out"
    if [ "$rc" -eq 0 ]; then
      echo "   $NAME: GREEN ($RID)"
    else
      echo "   $NAME: RED ($RID — see $PROOF/$NAME.out)"; FAILED=1
    fi
  done < <(grep -E '^probe:' "$CONF")
fi

if [ -n "$TEARDOWN" ]; then
  echo "== teardown (always) =="
  "$D/receipt.sh" bash -c "$TEARDOWN" || { echo "   teardown failed"; FAILED=1; }
fi

python3 - "$PROOF" "$RUN" "$ACCESS" "$FAILED" <<'PY'
import json, sys, datetime, pathlib
proof, run, access, failed = sys.argv[1:5]
p = pathlib.Path(proof)
outs = sorted(f.name for f in p.glob("*.out"))
body = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "run": run, "verdict": "RED" if int(failed) else "GREEN",
        "access": access or "(none declared)", "evidence": outs}
(p / "summary.json").write_text(json.dumps(body, indent=2))
print(f"proof verdict: {body['verdict']} — evidence: {', '.join(outs) or '(none)'}")
print(f"access instructions: {body['access']}")
PY
exit $FAILED
