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
import fcntl, hashlib, json, os
chain = os.environ["_RCHAIN"]
with open(chain, "a+") as f:
    fcntl.flock(f, fcntl.LOCK_EX)          # concurrent lanes: no interleaved links
    f.seek(0)
    lines = [l for l in f.read().splitlines() if l.strip()]
    prev = json.loads(lines[-1])["hash"] if lines else "0"*64
    body = {"id": os.environ["_RID"], "ts": os.environ["_RSTART"],
            "cmd": os.environ["_RCMD"], "exit": int(os.environ["_REC"]),
            "git_head": os.environ["_RHEAD"], "dirty_digest": os.environ["_RDIRTY"],
            "log": os.environ["_RLOG"],
            "log_digest": hashlib.sha256(open(os.environ["_RLOG"],"rb").read()).hexdigest(),
            "prev_hash": prev}
    body["hash"] = hashlib.sha256(json.dumps(body, sort_keys=True,
                                  separators=(",",":")).encode()).hexdigest()
    f.write(json.dumps(body, sort_keys=True, separators=(",",":")) + "\n")
print(body["id"], "exit=" + str(body["exit"]))
PY
exit $_REC
