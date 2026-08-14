#!/usr/bin/env bash
# receipt.sh — a claim of execution is a receipt id or it does not exist.
# Wraps any command; captures exit code, output digest, tree state; chains it.
set -uo pipefail
H="${HARNESS_DIR:-.harness}"; R="$H/receipts"; mkdir -p "$R"
export _RID="R-$(date -u +%Y%m%dT%H%M%SZ)-$RANDOM"
export _RLOG="$R/$_RID.log" _RCHAIN="$R/chain.jsonl"
export _RCMD="$*" _RSTART="$(date -u +%FT%TZ)"
export _RHEAD="$(git rev-parse HEAD 2>/dev/null || echo none)"
export _RDIRTY="$(git status --porcelain 2>/dev/null | python3 -c \
  'import sys,hashlib;print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
"$@" >"$_RLOG" 2>&1; export _REC=$?
python3 - <<'PY'
import fcntl, hashlib, json, os, re
chain = os.environ["_RCHAIN"]
with open(chain, "a+") as f:
    fcntl.flock(f, fcntl.LOCK_EX)          # concurrent lanes: no interleaved links
    f.seek(0)
    lines = [l for l in f.read().splitlines() if l.strip()]
    prev = json.loads(lines[-1])["hash"] if lines else "0"*64

    # Machine-derive test counts from the command's OWN output. A receipt wraps
    # arbitrary commands; the count is parsed from the log the command itself
    # produced — never supplied by the agent (an agent that self-reports its count
    # would be judging its own work). This is the load-bearing field a promotion
    # gate reads: test_count > 0 rejects "exit 0 with no tests run."
    #
    # Three honest states, distinguished from the log alone:
    #  - a real pytest summary line ("N passed[, M failed[, K errors]] in Xs") at
    #    start of a line -> test_count = passed+failed+errors. Anchored to a line
    #    start so a stray "passed" in build output or prose cannot feed the count.
    #  - a vacuous run ("no tests ran" / "collected 0 items") -> test_count = 0,
    #    NOT null: a test runner that ran 0 tests is the exact case the >0 gate
    #    exists to reject, and null would let it through as "not a test runner."
    #  - genuinely not a test runner (no summary, no vacuous marker) -> null, and a
    #    >0 gate skips it. Authenticating the command itself (a wrapper that echoes
    #    a forged "1 passed") is a higher gate's job; the receipt cannot, from the
    #    log alone, tell a real pytest run from a forgery.
    log_bytes = open(os.environ["_RLOG"], "rb").read()
    def _g(text, pat):
        m = re.search(pat, text)
        return int(m.group(1)) if m else 0
    test_count = pass_count = None
    # The pytest short-summary line is "N passed[, M failed[, K errors]] in Xs".
    # The " in <duration>" trailer is what separates a real summary from a stray
    # own-line "N passed ..." in build output: a start-of-line anchor alone blocks
    # MID-line strays but not own-line ones, and an own-line stray matches the
    # summary branch first and — by elif precedence — shadows the vacuous-run
    # marker, reading a vacuous run as test_count>0 and passing the very >0 gate
    # it exists to reject (the dangerous false-acceptance direction). The trailer
    # closes that: "1 passed validation check" has no " in <digit>" so it cannot
    # feed the count, and a vacuous "no tests ran in 0.00s" falls through to 0.
    # Take the LAST match: pytest prints its summary at the foot of the output, so
    # a stray own-line "N passed in Xs" earlier cannot shadow the real one later.
    matches = list(re.finditer(rb'(?:^|\n)\s*(\d+) passed[^\n]*\bin \d', log_bytes))
    if matches:
        sm = matches[-1]
        line = sm.group(0)
        pass_count = int(sm.group(1))
        test_count = pass_count + _g(line, rb'(\d+) failed') + _g(line, rb'(\d+) error')
    elif re.search(rb'no tests ran|no tests collected|collected 0 items', log_bytes):
        test_count = pass_count = 0

    body = {"id": os.environ["_RID"], "ts": os.environ["_RSTART"],
            "cmd": os.environ["_RCMD"], "exit": int(os.environ["_REC"]),
            "git_head": os.environ["_RHEAD"], "dirty_digest": os.environ["_RDIRTY"],
            "log": os.environ["_RLOG"],
            "log_digest": hashlib.sha256(log_bytes).hexdigest(),
            "test_count": test_count, "pass_count": pass_count,
            "prev_hash": prev}
    body["hash"] = hashlib.sha256(json.dumps(body, sort_keys=True,
                                  separators=(",",":")).encode()).hexdigest()
    f.write(json.dumps(body, sort_keys=True, separators=(",",":")) + "\n")
print(body["id"], "exit=" + str(body["exit"]))
PY
exit $_REC
