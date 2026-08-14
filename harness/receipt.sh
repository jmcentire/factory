#!/usr/bin/env bash
# receipt.sh — a claim of execution is a receipt id or it does not exist.
# Wraps any command; captures exit code, output digest, tree state; chains it.
set -uo pipefail
H="${HARNESS_DIR:-.factory}"; R="$H/receipts"; mkdir -p "$R"
# $$ (the PID) is load-bearing now that R3 rejects duplicate receipt ids in _load_chain:
# $RANDOM alone collides across two receipt.sh calls in the same UTC second (measured
# ~C(n,2)/32768 per second), and a collision hard-raises in the append-only chain with no
# repair path, wedging every future promotion on that harness. The PID disambiguates
# concurrent processes the way mutate.sh:76 and flake.sh:98 already do.
export _RID="R-$(date -u +%Y%m%dT%H%M%SZ)-$$-$RANDOM"
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
    # The foot is NOT reliably the last non-empty line: pytest plugins print AFTER the terminal
    # summary. pytest_unconfigure fires at session teardown, after summary_stats, and
    # coverage/telemetry plugins emit their report there — a "Coverage: 100%" line after the
    # real foot made the r5 "foot = last non-empty line" anchor miss a real 2-pass run and
    # record test_count=None (r6: false-rejection of a green build, a regression the r5
    # last-line anchor introduced). Scan BACKWARD for the last line that BOTH ends in pytest's
    # timing suffix "in <time>s" (optionally '='-padded, the r6 fix) AND carries a pytest foot
    # keyword. The shape requirement excludes a pytest progress line ("s [100%]") that carries a
    # keyword but does not end in "in Xs"; the keyword requirement excludes a post-foot
    # NON-summary plugin line ("Coverage: 100%" / "done in 3.2s" carries no pytest keyword) —
    # and because the real foot is printed at teardown AFTER all test stdout, it is the LAST
    # keyword-bearing "in Xs" line, so scan-backward picks it over a test's earlier "5 passed
    # in 0.1s" stdout (the r5/r7 gain). r7 found the hole in "provided the real foot matches a
    # foot pattern": an xfail-only foot "1 xfailed in 0.02s" matched the r6 keyword set's
    # executed-only alternation, so scan-backward skipped it and fell back to test stdout. The
    # foot-keyword set here is the COMPLETE pytest 9.0.3 terminal set: executed (passed,
    # failed, error, xfailed, xpassed) AND non-executed (skipped, deselected, warning, "no
    # tests ran"). A future pytest foot keyword outside this set would not anchor (the set is
    # complete for 9.0.3, not for all time).
    lines = [ln for ln in log_clean.splitlines() if ln.strip()]
    footshape_re = rb'\bin \d[\d.]*s\b[ =]*$'
    footkw_re = rb'(?:passed|failed|error|xfailed|xpassed|skipped|deselected|warnings?|no tests ran)'
    foot = b''
    for ln in reversed(lines):
        if re.search(footshape_re, ln) and re.search(footkw_re, ln):
            foot = ln
            break
    # VACUOUS vs EXECUTED vs NOT-A-TEST-RUNNER — three states, classified from the foot's
    # keywords, NOT by enumerating every vacuous combination (the r8/r9 lesson: enumerate the
    # mixes and the next one bites you; classify the property instead). An EXECUTED test
    # produced a pass/fail/error/xfail/xpass verdict; those five keywords are the only ones that
    # count. A foot that carries a pytest keyword but NONE of those five is VACUOUS — 0
    # verifying tests ran — however the non-executed counts combine ("no tests ran", "N
    # skipped[, M deselected][, K warnings]", "N warnings" alone, which pytest prints INSTEAD
    # of "no tests ran" when 0 tests ran but a warning was emitted). The >0 gate must REJECT a
    # vacuous run (test_count=0), not skip it as "not a test runner" (None) — None lets a
    # 0-test build through, the exact false-acceptance this closes. The classification is sound
    # because no non-executed keyword contains an executed one as a substring (skipped /
    # deselected / warning / "no tests ran" hold none of passed/failed/error/xfailed/xpassed).
    # A foot found via the anchor always carries a pytest keyword (the anchor requires it), so
    # the no-keyword branch is only reached when no foot was found — a crash before the terminal
    # summary, or genuinely not a test runner: then vacuous_coll (the "collected 0 items" /
    # "0 selected" body marker, which precedes the foot) is the fallback that still yields 0,
    # else None (not a test runner / incomplete run). All anchors match pytest's real terminal
    # signals, never an unanchored substring (the r4 lesson: `re.search(rb'no tests ran', ...)`
    # anywhere matches the phrase inside a test's OWN stdout). "0 selected" only appears when
    # zero tests will run (a mixed run prints "M selected", M>0), so it is a clean deselect-all
    # marker that -k NoSuchName, -m NoSuchMarker and --deselect all share.
    executed_re = rb'(?:passed|failed|error|xfailed|xpassed)'
    vacuous_coll = (re.search(rb'(?:^|\n)[ =]*collected 0 items[ \t]*(?:\n|$)', log_clean)
                    or re.search(rb'(?:^|\n)[ =]*collected \d+ items[^\n]*\b0 selected\b',
                                 log_clean))
    if foot and re.search(executed_re, foot):
        # EXECUTED foot. pytest 9 orders failures FIRST ("1 failed, 2 passed in 0.03s"); extract
        # each count independently by keyword so passed-first, failed-first, and xfail-bearing
        # feet all parse. xfailed/xpassed are EXECUTED tests (the test ran and produced a
        # verdict), so they count toward test_count — an xfail-only run is NOT "no tests ran"
        # and the >0 gate must not reject it as such (r7: the prior set read None for a real
        # xfail run, misclassifying a test runner as "not a test runner"). pass_count stays
        # passed-only: xfailed is an expected failure (not a pass), and xpassed is a pass only
        # under non-strict xfail (strict mode treats it as a failure) — the receipt does not
        # adjudicate strict-vs-non-strict, that is the promotion gate's oracle-adequacy call, not
        # the count's. The literal space in '(\d+) failed' stops "xfailed" feeding the failed
        # branch (the char after the digit-space is 'x', not 'f'); symmetric for xpassed/passed.
        # '\berror' matches "error" and the "error" in "errors".
        pass_count = _g(foot, rb'(\d+) passed')
        test_count = (pass_count + _g(foot, rb'(\d+) failed')
                      + _g(foot, rb'(\d+) error') + _g(foot, rb'(\d+) xfailed')
                      + _g(foot, rb'(\d+) xpassed'))
    elif foot:
        # pytest-keyword foot but NO executed keyword: vacuous (0 verifying tests), regardless
        # of how skipped/deselected/warning/"no tests ran" combine. (This is the structural fix
        # for the r9 gap: "1 warning in 0.00s" and "2 skipped, 1 warning in 0.00s" read None
        # under the enumerated vacuous patterns — a false-acceptance, since a 0-test build then
        # passes the >0 gate by being skipped as "not a test runner". Classifying by the
        # executed-keyword property subsumes every vacuous mix in one check.)
        test_count = pass_count = 0
    elif vacuous_coll:
        # No foot found (crash before the terminal summary, or -q suppressed it) but the body
        # shows a 0-collection / 0-selected marker: vacuous.
        test_count = pass_count = 0
    # else: no foot and no collection marker -> genuinely not a test runner, or an incomplete
    # run (no terminal summary printed). test_count stays None; a >0 gate skips it.
    # RESIDUAL (undetectable from the log + exit code, and NOT a regression — r4 had it too):
    # a plugin or conftest that prints a summary-shaped line AFTER the real foot forges the
    # count, because scan-backward picks the last keyword-bearing "in Xs" line and the forged
    # line is one. The receipt cannot tell a real pytest foot from a forged one by position or
    # content, because the agent controls the test config. This is the same class as a wrapper
    # that echoes a forged "1 passed": authenticating the COMMAND (not its log) is a higher
    # gate's job, and the receipt has never claimed otherwise. The count is honest GIVEN an
    # honest test config; a config that forges its own summary is an agent supplying its own
    # count. The forgery is SYMMETRIC and either direction is undetectable: a plugin printing
    # an EXECUTED-shaped line ("3 passed in 0.07s") after a vacuous foot forges a count
    # (false-ACCEPTANCE — a 0-test build promotes); a plugin printing a VACUOUS-shaped line
    # ("1 warning in 0.5s") after an executed foot forges a 0 (false-REJECTION — a real build
    # is held). The r9 structural fix (widening the keyword set to include "warning" so the
    # natural 0-test "1 warning in 0.00s" foot is classified vacuous, not None) makes the
    # second direction reachable where the enumerated r8 set did not — but it is the same
    # undetectable forgery class, and it is fail-CLOSED (a held build is investigated; a
    # promoted 0-test build is the exact failure the gate exists to prevent), so the r9 trade
    # is correct on every NATURAL foot shape and wrong only on a forged config. "Prefer an
    # executed foot over a vacuous one" would close the false-rejection but REOPEN the r5/r7
    # test-stdout false-acceptance (a skip-only run whose test prints "5 passed in 0.1s" under
    # -s would pick the executed stdout over the real vacuous foot) — a NATURAL case, worse
    # than the forged case it fixes — so it is not taken. (A future hardening — prefer a foot
    # line that carried ANSI color before stripping, since pytest colorizes its summary and a
    # bare plugin print does not — is partial and only helps when TERM/color is on, so it is
    # not relied on here.)

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
