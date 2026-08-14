#!/usr/bin/env bash
# flake.sh — prove a suite is deterministic, by running it until it isn't.
#
# Why this exists: the doctrine holds that Critical evidence has ZERO flake/retry
# tolerance (CLAUDE.md, "Surface criticality"). A surface's `deterministic` claim
# cites a receipt, not a verdict in prose — and a receipt that says "deterministic"
# without having run the suite more than once is a claim with no evidence behind it.
# This script runs the suite N times and receipts what it observed: deterministic iff
# every run agreed, flake_count = the runs that dissented from the majority. The
# receipt is kind:"flake" in the same tamper-evident chain as the build (receipt.sh)
# and oracle (mutate.sh) receipts, so the promotion-gate translator reads one chain.
#
# A flaky suite is a FINDING (exit 1), not a failure of this script: the script did
# its job by surfacing it. A baseline that is not green is INVALID (exit 3): flake-
# detecting a red baseline manufactures a "flake" that is just the pre-existing red.
#
#   usage: flake.sh <name> --src <tree> --tests <tree> [--test-cmd "..."] [--runs N]
set -uo pipefail

NAME="${1:?usage: flake.sh <name> --src <tree> --tests <tree> [--test-cmd ...] [--runs N]}"; shift
SRC=""; TESTS=""; TEST_CMD=""; RUNS="${FLAKE_RUNS:-3}"
while [ $# -gt 0 ]; do
  case "$1" in
    --src)      SRC="$2"; shift 2 ;;
    --tests)    TESTS="$2"; shift 2 ;;
    --test-cmd) TEST_CMD="$2"; shift 2 ;;
    --runs)     RUNS="$2"; [ "$2" -ge 2 ] 2>/dev/null || { echo "--runs must be an integer >= 2 (one run cannot detect a flake)" >&2; exit 64; }; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 64 ;;
  esac
done
[ -n "$SRC" ] && [ -n "$TESTS" ] || { echo "--src and --tests are required" >&2; exit 64; }
case "$RUNS" in *[!0-9]*) echo "--runs must be an integer >= 2" >&2; exit 64 ;; esac
[ "$RUNS" -ge 2 ] || { echo "--runs must be >= 2 (one run cannot detect a flake)" >&2; exit 64; }

WORK="${MUTATE_WORKDIR:-/tmp/flake}/$NAME"
rm -rf "$WORK" 2>/dev/null
mkdir -p "$WORK" || exit 70
rsync -a --exclude .git --exclude .factory "$SRC"/ "$WORK"/ 2>/dev/null
rsync -a --delete "$TESTS"/tests/ "$WORK"/tests/ 2>/dev/null

: "${TEST_CMD:=python3 -m pytest tests/ -q -p no:randomly}"
verdict() { printf '%s: %s\n' "$NAME" "$*"; }

# --- GATE 1: the code under test must load FROM THIS TREE (same rationale as mutate.sh) ---
import_ok=$(cd "$WORK" && PYTHONPATH="$WORK/src" python3 - "$WORK" <<'PY' 2>&1
import os, sys, importlib
root = sys.argv[1]; sys.path.insert(0, root + "/src")
pkg = next((n for n in sorted(os.listdir(root + "/src"))
            if not n.startswith("_") and os.path.isdir(root + "/src/" + n)), None)
if pkg is None:
    print("NO-PACKAGE")
else:
    m = importlib.import_module(pkg)
    print("OK" if (m.__file__ or "").startswith(root) else "WRONG-TREE " + str(m.__file__))
PY
)
case "$import_ok" in
  OK*) ;;
  *) verdict "INVALID (import resolves outside the tree: $import_ok)"; exit 3 ;;
esac

# --- GATE 2: the baseline must be GREEN before flake-hunting -------------------
# A red baseline makes every dissenting run look like a flake when it is the same
# pre-existing red. Establish green once, then vary only the run.
clean_rc=0
( cd "$WORK" && PYTHONPATH="$WORK/src" timeout "${MUTATE_TIMEOUT:-2000}" $TEST_CMD >/dev/null 2>&1 ) || clean_rc=$?
if [ "$clean_rc" -ne 0 ]; then
  verdict "INVALID (baseline is not green — a red baseline manufactures false flakes)"
  exit 3
fi

# --- the actual question: does it agree with itself across N runs? ------------
# Record each run's exit code. A run that exits 0 passed; non-zero failed. The suite
# is deterministic iff every run agreed (all pass OR all fail). flake_count is the
# number of runs that dissented from the majority outcome: 0 when deterministic, and
# the minority count when mixed. automatic_retry_count is 0 — this producer performs
# no automatic retries (it runs N intentional executions, not a retry-on-failure loop);
# a retry-performing harness records its own retry count in its own receipt.
exits=()
for i in $(seq 1 "$RUNS"); do
  rc=0
  ( cd "$WORK" && PYTHONPATH="$WORK/src" timeout "${MUTATE_TIMEOUT:-2000}" $TEST_CMD >/dev/null 2>&1 ) || rc=$?
  exits+=("$rc")
done
pass_count=0; fail_count=0
for rc in "${exits[@]}"; do [ "$rc" -eq 0 ] && pass_count=$((pass_count+1)) || fail_count=$((fail_count+1)); done
if [ "$pass_count" -eq "$RUNS" ] || [ "$fail_count" -eq "$RUNS" ]; then
  deterministic=1; flake_count=0
else
  deterministic=0; flake_count=$(( pass_count < fail_count ? pass_count : fail_count ))
fi

# --- flake receipt (Gate N seam): machine-derive determinism from the runs -----
# Best-effort like the oracle receipt: a receipt write that fails is silent and never
# changes the verdict or exit code. kind:"flake" distinguishes it in the chain.
H="${HARNESS_DIR:-.factory}"
mkdir -p "$H/receipts" 2>/dev/null || true
ts=$(date -u +%FT%TZ 2>/dev/null); rid="F-$(date -u +%Y%m%dT%H%M%SZ 2>/dev/null)-$$-$RANDOM"
FLAKE_RID="$rid" FLAKE_TS="$ts" FLAKE_NAME="$NAME" FLAKE_RUNS="$RUNS" \
FLAKE_PASS="$pass_count" FLAKE_FAIL="$fail_count" FLAKE_DET="$deterministic" \
FLAKE_FLAKE="$flake_count" FLAKE_EXITS="${exits[*]}" \
FLAKE_CHAIN="$H/receipts/chain.jsonl" \
python3 - <<'PY' 2>/dev/null || true
import fcntl, hashlib, json, os
chain = os.environ["FLAKE_CHAIN"]
with open(chain, "a+") as f:
    fcntl.flock(f, fcntl.LOCK_EX)
    f.seek(0)
    lines = [l for l in f.read().splitlines() if l.strip()]
    prev = json.loads(lines[-1])["hash"] if lines else "0"*64
    body = {"id": os.environ["FLAKE_RID"], "kind": "flake", "ts": os.environ["FLAKE_TS"],
            "name": os.environ["FLAKE_NAME"], "runs": int(os.environ["FLAKE_RUNS"]),
            "pass_count": int(os.environ["FLAKE_PASS"]),
            "fail_count": int(os.environ["FLAKE_FAIL"]),
            "deterministic": bool(int(os.environ["FLAKE_DET"])),
            "flake_count": int(os.environ["FLAKE_FLAKE"]),
            "automatic_retry_count": 0,
            "run_exits": [int(x) for x in os.environ["FLAKE_EXITS"].split()],
            "prev_hash": prev}
    body["hash"] = hashlib.sha256(json.dumps(body, sort_keys=True,
                          separators=(",",":")).encode()).hexdigest()
    f.write(json.dumps(body, sort_keys=True, separators=(",",":")) + "\n")
PY

if [ "$deterministic" -eq 1 ]; then
  verdict "DETERMINISTIC ($RUNS runs, all agreed: $pass_count passed)"
  exit 0
fi
verdict "FLAKY ($RUNS runs: $pass_count passed / $fail_count failed — $flake_count dissented from the majority)"
exit 1