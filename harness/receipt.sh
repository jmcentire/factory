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
# Gate M (slice 4): machine-derive changed paths from the diff against the run base. The
# diff is taken AFTER the command ran (the candidate is the post-command working tree), so
# it captures everything the build changed. Empty when no base is supplied — the receipt is
# not a candidate-build receipt and the promotion gate runs advisory for the migration
# window. Generic: no target knowledge; the surface mapping is a caller-supplied data file.
if [ -n "${HARNESS_BASE_SHA:-}" ]; then
  # Union tracked changes since the base with untracked new files: a candidate build
  # creates new files, and `git diff` alone misses untracked ones (they are not yet
  # tracked, so git does not diff them). --exclude-standard respects .gitignore, so the
  # harness/receipts dir and transient .factory/runs do not pollute the surface set.
  export _RCHANGED="$( { git diff --name-only "$HARNESS_BASE_SHA" 2>/dev/null; \
     git ls-files --others --exclude-standard 2>/dev/null; } | sort -u )"
else
  export _RCHANGED=""
fi
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
    # pytest 9 emits ANSI color on the summary line even when stdout is a pipe (it keys
    # off TERM, set in every tmux pane), and its foot line is padded with '=':
    #   "========================= N passed in X.XXs ========================="
    # Both defeat a naive anchor: the ANSI escapes sit before the digit (so \s* then
    # (\d+) sees 0x1b, not a digit), and \s* does not consume '=' (so the line starting
    # with '=' fails the anchor). Without stripping/tolerating these, a REAL pytest run
    # is misparsed as test_count=None — "not a test runner" — and the load-bearing >0
    # gate is inert against the very command it wraps. mutate.sh strips ANSI for its own
    # extraction; the receipt must do the same for the count it derives.
    log_clean = re.sub(rb'\x1b\[[0-9;]*m', b'', log_bytes)
    def _g(text, pat):
        m = re.search(pat, text)
        return int(m.group(1)) if m else 0
    test_count = pass_count = None
    # pytest's terminal foot is the LAST non-empty line of the log. Every test's own stdout
    # (under -s) prints DURING the run, before the terminal phase, so a test that prints
    # "5 passed in 0.1s" can never be the foot — the real foot follows it. The r5 lesson:
    # "take the last regex match ANYWHERE in the log" fell back to a test's own summary-shaped
    # stdout when the real foot was keyword-less (a skip-only run), and fabricated
    # test_count=5 for a run that executed zero tests — defeating the load-bearing >0 gate
    # this receipt exists to enforce. Anchor to the foot's POSITION (last line), not to a
    # content regex matched anywhere; the content is confirmation, the position is the
    # structural fact. (Residual: a trylast pytest_terminal_summary hook that prints after
    # the foot moves the last line off it — a real run then reads as None; benign for a
    # passing build, accepted for the right reason on the wrong line. A 0-test run there is
    # exotic-squared and still caught by the collection-line anchor below.)
    lines = [ln for ln in log_clean.splitlines() if ln.strip()]
    foot = lines[-1] if lines else b''
    # VACUOUS: collected but executed no verifying test. The >0 gate must REJECT this
    # (test_count=0), not skip it as "not a test runner" (None) — None lets a 0-test build
    # through, the exact false-acceptance this branch closes. Four terminal shapes, all
    # anchored to pytest's real signals, never an unanchored substring (the r4 lesson:
    # `re.search(rb'no tests ran', ...)` anywhere matches the phrase inside a test's OWN
    # stdout). The collection line ("collected 0 items" / "collected N items / 0 selected")
    # precedes the foot, so it is searched in the body; the keyword-less feet ("no tests ran
    # in Xs", "N skipped in Xs", "N deselected in Xs") ARE the foot, so they are matched at
    # the last line. The "0 selected" token only appears when zero tests will run — a mixed
    # run prints "M selected" with M>0 — so it is a clean deselect-all marker that
    # -k NoSuchName, -m NoSuchMarker and --deselect all share. The keyword-less skip/
    # deselected feet are start-anchored (re.match on the foot) and require " in " directly
    # after the keyword, so a mixed foot "1 passed, 1 skipped in 0.03s" (starts with
    # "passed"; comma before "skipped") does NOT match — only an all-skipped/deselected foot.
    vacuous_foot = re.match(rb'[ =]*no tests ran in \d[\d.]*s', foot)
    vacuous_skip = re.match(rb'[ =]*\d+ (?:skipped|deselected) in \d[\d.]*s', foot)
    vacuous_coll = (re.search(rb'(?:^|\n)[ =]*collected 0 items[ \t]*(?:\n|$)', log_clean)
                    or re.search(rb'(?:^|\n)[ =]*collected \d+ items[^\n]*\b0 selected\b',
                                 log_clean))
    if vacuous_foot or vacuous_skip or vacuous_coll:
        test_count = pass_count = 0
    else:
        # Keyword-bearing foot (passed/failed/error) at the last line. pytest 9 orders
        # failures FIRST ("1 failed, 2 passed in 0.03s"); extract each count independently
        # by keyword so passed-first and failed-first both parse. The last-line anchor
        # excludes a test's mid-log summary-shaped stdout (it is never the foot). The
        # '\bin \d[\d.]*s\b' timing suffix excludes non-summary lines that merely contain a
        # digit + "passed"; the '[ =]*' absorbs pytest's '=' separator padding.
        m = re.match(rb'[ =]*[^\n]*\b\d+ (?:passed|failed|error)[^\n]*\bin \d[\d.]*s\b', foot)
        if m:
            line = m.group(0)
            pass_count = _g(line, rb'(\d+) passed')
            test_count = pass_count + _g(line, rb'(\d+) failed') + _g(line, rb'(\d+) error')

    # Gate M (slice 4): changed paths machine-derived in the bash above (git diff against
    # the run base, taken after the command ran), and — when the caller supplies a surface
    # map (target data, read from a file: data-driven, not a code import, so the generic
    # boundary holds) — the mapped disturbed-surface set. Both are machine-derived; neither
    # is agent-supplied. The promotion gate verifies the request binds to these. An unmapped
    # path is reported under unmapped_paths, not silently dropped into the surface set (a
    # path with no surface mapping is a target-config gap for the runtime to resolve).
    base_sha = os.environ.get("HARNESS_BASE_SHA")
    if base_sha:
        changed_paths = [p for p in os.environ.get("_RCHANGED", "").splitlines() if p.strip()]
        changed_paths_digest = hashlib.sha256(
            "\n".join(changed_paths).encode()).hexdigest()
    else:
        changed_paths = None
        changed_paths_digest = None
    disturbed_surface_ids = None
    surface_map_digest = None
    unmapped_paths = None
    surface_map_path = os.environ.get("HARNESS_SURFACE_MAP")
    if surface_map_path and changed_paths and os.path.exists(surface_map_path):
        import fnmatch
        with open(surface_map_path, "rb") as mf:
            map_bytes = mf.read()
        surface_map_digest = hashlib.sha256(map_bytes).hexdigest()
        try:
            surface_map = json.loads(map_bytes)
        except json.JSONDecodeError:
            surface_map = {}
        # Deterministic glob order: the first sorted glob that matches a path wins, so the
        # mapping is stable across runs and machines. isinstance guards a malformed map.
        # fnmatchcase (not fnmatch): case-sensitive, so the mapping is deterministic across
        # macOS/POSIX rather than inheriting the platform's case-folding.
        globs = sorted(surface_map.keys()) if isinstance(surface_map, dict) else []
        mapped = set()
        unmapped = []
        for path in changed_paths:
            hit = next((str(surface_map[g]) for g in globs
                        if fnmatch.fnmatchcase(path, g)), None)
            if hit is not None:
                mapped.add(hit)
            else:
                unmapped.append(path)
        disturbed_surface_ids = sorted(mapped) if mapped else None
        unmapped_paths = sorted(unmapped) if unmapped else None

    body = {"id": os.environ["_RID"], "ts": os.environ["_RSTART"],
            "cmd": os.environ["_RCMD"], "exit": int(os.environ["_REC"]),
            "git_head": os.environ["_RHEAD"], "dirty_digest": os.environ["_RDIRTY"],
            "log": os.environ["_RLOG"],
            "log_digest": hashlib.sha256(log_bytes).hexdigest(),
            "test_count": test_count, "pass_count": pass_count,
            "changed_paths": changed_paths,
            "changed_paths_digest": changed_paths_digest,
            "disturbed_surface_ids": disturbed_surface_ids,
            "surface_map_digest": surface_map_digest,
            "unmapped_paths": unmapped_paths,
            "prev_hash": prev}
    body["hash"] = hashlib.sha256(json.dumps(body, sort_keys=True,
                                  separators=(",",":")).encode()).hexdigest()
    f.write(json.dumps(body, sort_keys=True, separators=(",",":")) + "\n")
print(body["id"], "exit=" + str(body["exit"]))
PY
exit $_REC
