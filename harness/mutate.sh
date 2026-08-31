#!/usr/bin/env bash
# mutate.sh — prove a guard actually guards, by breaking the thing it watches.
#
# Why this exists: run v8 reached a suite of 1723 passing tests that was blind to a
# data regression and four containment escapes. Every one was found by pointing a
# NEW LENS at the code; not one was found by the suite. Behind them was a single
# failure shape that appeared three times from three separate parties —
#
#   the check was written against the FIX'S ARTIFACT, not the PROHIBITED ACTION.
#
#   - the R1.5 canary watched config paths under a monkeypatched HOME, while the
#     code consulted an import-time constant pointing somewhere else entirely;
#   - a containment check asserted the git probe's START directory was inside the
#     root — true with or without the fix, because what escaped was the WALK;
#   - its repair recorded invocations only when kwargs["cwd"] was set, while the
#     code passes its directory as `git -C <dir>`;
#   - and the Validator's own probe checked RETURNED PATHS only, so it could not
#     see a forbidden READ that gets clamped before it returns.
#
# All four passed. All four were worthless. The only thing separating a guard from
# a claim is reverting the behavior and watching the guard fail. That is the whole
# job of this script.
#
#   usage: mutate.sh <name> <patch.py> --src <tree> --tests <tree> [--test-cmd "..."]
#                    [--named-test <nodeid>]
#
# <patch.py> receives the mutated tree root as argv[1]. It MUST assert its own
# anchor and exit nonzero when the anchor is missing — see GATE 3.
#
# --named-test <nodeid>: the oracle that owns this requirement. When set, a kill
# by ANY OTHER test is rejected as a symptom, not a failure (see the kill gate).
# The full suite still runs — per-test attribution manufactures blind spots the
# same way per-test greens manufacture false confidence; this only checks the
# kill is attributable to the named oracle, not that only one test ran.
set -uo pipefail

NAME="${1:?usage: mutate.sh <name> <patch.py> --src <tree> --tests <tree>}"; shift
PATCH="${1:?patch script}"; shift
SRC=""; TESTS=""; TEST_CMD=""; NAMED_TEST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --src)        SRC="$2"; shift 2 ;;
    --tests)      TESTS="$2"; shift 2 ;;
    --test-cmd)   TEST_CMD="$2"; shift 2 ;;
    --named-test) NAMED_TEST="$2"; [ -n "$2" ] || { echo "--named-test must be non-empty (an empty oracle silently disables attribution)" >&2; exit 64; }; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 64 ;;
  esac
done
[ -n "$SRC" ] && [ -n "$TESTS" ] || { echo "--src and --tests are required" >&2; exit 64; }
[ -f "$PATCH" ] || { echo "no patch script at $PATCH" >&2; exit 64; }

# --- oracle receipt (Gate N seam): machine-derive oracle adequacy from the kill ----
# A mutation test's outcome is the load-bearing input to a surface's oracle_adequate
# claim (SurfaceObservation.oracle_receipt + oracle_adequate). KILLED-by-the-named-oracle
# proves the oracle catches the regression; anything else (SURVIVED, KILLED-OUTSIDE-ORACLE,
# KILLED-UNATTRIBUTED, INVALID/PATCH-FAILED/NO-OP) does not. The receipt is best-effort: it
# must never change the mutation verdict or its exit code (the verdict on stdout is the
# authority the Validator reads; the receipt is the durable record the promotion-gate
# translator reads), so a receipt write that fails is silent. kind:"oracle" distinguishes it
# from the build receipts (receipt.sh, no kind) in the same tamper-evident chain. MUTATION_STARTED
# gates the trap so an arg-parse exit (before this point) is not mis-receipted as an outcome.
MUTATION_STARTED=1
_oracle_receipt() {
  local ec=$?
  [ "${MUTATION_STARTED:-0}" = 1 ] || return 0
  [ -n "${NAME:-}" ] || return 0
  local outcome adequate=0 verdict_text="${ORACLE_VERDICT:-}"
  case "$ec" in
    0) outcome=KILLED; [ -n "${NAMED_TEST:-}" ] && adequate=1 ;;
    1) outcome=SURVIVED ;;
    *) outcome=NOT_ADEQUATE ;;
  esac
  local H="${HARNESS_DIR:-.factory}"
  mkdir -p "$H/receipts" 2>/dev/null || return 0
  local ts rid
  ts=$(date -u +%FT%TZ 2>/dev/null); rid="O-$(date -u +%Y%m%dT%H%M%SZ 2>/dev/null)-$$-$RANDOM"
  OUTCOME="$outcome" ORACLE_ADEQUATE="$adequate" ORACLE_EC="$ec" \
  ORACLE_VERDICT="$verdict_text" ORACLE_NAME="$NAME" ORACLE_NAMED="${NAMED_TEST:-}" \
  ORACLE_CHAIN="$H/receipts/chain.jsonl" ORACLE_RID="$rid" ORACLE_TS="$ts" \
  python3 - <<'PY' 2>/dev/null || true
import fcntl, hashlib, json, os
chain = os.environ["ORACLE_CHAIN"]
with open(chain, "a+") as f:
    fcntl.flock(f, fcntl.LOCK_EX)
    f.seek(0)
    lines = [l for l in f.read().splitlines() if l.strip()]
    # 4.2 change 2 (append-time half): a duplicate receipt id never enters the
    # chain — the R5 wedge is refused at the WRITER, inside this same flock,
    # instead of discovered at the next promote.
    existing_ids = set()
    for existing_line in lines:
        try:
            existing_ids.add(str(json.loads(existing_line).get("id", "")))
        except (ValueError, TypeError):
            pass
    _new_rid = os.environ["ORACLE_RID"]
    if _new_rid in existing_ids:
        raise SystemExit(
            "refusing duplicate receipt id %r: the chain already carries it "
            "(append-time R5 rejection)" % _new_rid
        )
    prev = json.loads(lines[-1])["hash"] if lines else "0"*64
    body = {"id": os.environ["ORACLE_RID"], "kind": "oracle", "ts": os.environ["ORACLE_TS"],
            "mutation_name": os.environ["ORACLE_NAME"],
            "named_test": os.environ.get("ORACLE_NAMED",""),
            "outcome": os.environ["OUTCOME"],
            "oracle_adequate": bool(int(os.environ["ORACLE_ADEQUATE"])),
            "exit": int(os.environ["ORACLE_EC"]),
            "verdict_text": os.environ.get("ORACLE_VERDICT",""),
            "prev_hash": prev}
    body["hash"] = hashlib.sha256(json.dumps(body, sort_keys=True,
                          separators=(",",":")).encode()).hexdigest()
    f.write(json.dumps(body, sort_keys=True, separators=(",",":")) + "\n")
PY
}
trap _oracle_receipt EXIT

WORK="${MUTATE_WORKDIR:-/tmp/mutate}/$NAME"
rm -rf "$WORK" 2>/dev/null
mkdir -p "$WORK" || exit 70
rsync -a --exclude .git --exclude .factory "$SRC"/ "$WORK"/ 2>/dev/null
rsync -a --delete "$TESTS"/tests/ "$WORK"/tests/ 2>/dev/null

: "${TEST_CMD:=python3 -m pytest tests/ -q -p no:randomly}"
verdict() { ORACLE_VERDICT="$*"; printf '%s: %s\n' "$NAME" "$*"; }

# --- GATE 1: the code under test must load FROM THIS TREE --------------------
# A stale site-packages .pth on the workstation aliases the package import to a
# different checkout, so a run can silently exercise other code and report a
# confident verdict about it.
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
  *) verdict "INVALID (import resolves outside the mutated tree: $import_ok)"; exit 3 ;;
esac

# --- GATE 2: the clean tree must be GREEN before anything is broken ----------
# "The guard caught it" means nothing if the guard was already failing. v8 produced
# exactly this: a tightened check that failed 5/5 on the UNMODIFIED tree, whose
# apparent kill was only the false red firing again.
# Capture pytest's OWN exit code, not tail's. Piping to `tail -3` makes $? reflect tail
# (always 0), so GATE 2 could never see a non-zero pytest exit — a conftest SyntaxError or
# collection crash in the CLEAN baseline exits non-zero with no "N failed/error" summary,
# slipped past the grep, and was accepted as green. The mutation was then falsely reported
# KILLED: the exact v8 false-red this gate exists to prevent. The line-146 premise ("GATE 2
# already proved the clean tree exits 0") is only true now that GATE 2 sees pytest's exit.
clean_full=$(cd "$WORK" && PYTHONPATH="$WORK/src" timeout "${MUTATE_TIMEOUT:-2000}" $TEST_CMD 2>&1); clean_rc=$?
clean_out=$(printf '%s' "$clean_full" | tail -3)
# The kill condition is pytest's EXIT CODE alone — not a grep for "N failed/error" in the
# summary. The grep was added to catch a collection crash that prints no summary line, but a
# collection crash exits NON-ZERO (pytest 2/4/5), so clean_rc already catches every case the
# grep did. The grep's ONLY independent effect was false-rejection: a pytest_terminal_summary
# hook that prints a non-failure line matching "N (failed|error) in <text>" (e.g. "1 error in
# configuration loading") matched the r4 anchor's " in " branch and INVALID-ed a genuinely
# green rc=0 baseline (r5). In -q mode pytest prints NO lowercase "N failed/error in Xs"
# timing line at all — the short-summary is uppercase FAILED/ERROR, which the case-sensitive
# grep never matched — so the grep was fully redundant with clean_rc for real failures too.
# Rely on the structured signal (exit code); keep grep only for killer EXTRACTION below.
if [ "$clean_rc" -ne 0 ]; then
  verdict "INVALID (baseline is not green — fix the false red first)"
  printf '%s\n' "$clean_out" | sed 's/^/    /'
  exit 3
fi

# --- GATE 3: the patch must apply AND must actually change the tree ----------
# A patch whose anchor has drifted raises, changes nothing, and the suite then
# passes — indistinguishable from a surviving mutant unless checked. The ad-hoc
# runner used in v8 reported precisely that false SURVIVED for a patch that had
# died on an IndentationError. A mutation harness that cannot tell "did not apply"
# from "survived" manufactures the very false green it exists to detect.
#
# The exit code alone is NOT sufficient, and THIS SCRIPT SHIPPED WITH THAT HOLE:
# a patch that exits 0 without touching a byte also came back SURVIVED. Same
# defect, same tool, one variant away — the author tested the nonzero-exit case
# and stopped. That is the exact failure this harness exists to catch, committed
# by the person writing the catcher, which is why the tree is now digested before
# and after and an unchanged tree is INVALID rather than a survival.
tree_digest() {
  find "$1" -type f -not -path '*/__pycache__/*' -not -path '*/.git/*' \
    -exec shasum -a 256 {} + 2>/dev/null | sort -k2 | shasum -a 256 | cut -d' ' -f1
}
before_digest=$(tree_digest "$WORK")
patch_out=$(python3 "$PATCH" "$WORK" 2>&1); patch_rc=$?
if [ "$patch_rc" -ne 0 ]; then
  verdict "PATCH-FAILED (anchor missing or drifted — this is NOT a survival)"
  printf '%s\n' "$patch_out" | tail -3 | sed 's/^/    /'
  exit 3
fi
if [ "$(tree_digest "$WORK")" = "$before_digest" ]; then
  verdict "NO-OP PATCH (exited 0 but changed nothing — this is NOT a survival)"
  echo "    Tree digest is identical before and after the patch. A mutation that" >&2
  echo "    mutates nothing cannot be survived. Fix the patch, then re-run." >&2
  exit 3
fi

# --- GATE 4: the mutation must be present in the source that loads -----------
# Editing a file is not the same as the interpreter loading it: bytecode caches, a
# shadowing install, or a patch that edited a sibling copy all break the link
# between "file changed" and "behavior changed".
if [ -n "${MUTATE_MARKER:-}" ]; then
  if ! grep -rq -- "$MUTATE_MARKER" "$WORK/src"; then
    verdict "INVALID (marker '$MUTATE_MARKER' absent from mutated source)"; exit 3
  fi
fi

# --- the actual question -----------------------------------------------------
# Always the FULL suite, never the single test you believe owns the requirement.
# Per-test attribution manufactures false blind spots the same way per-test greens
# manufacture false confidence: in v8 a mutation "survived" the oracle it was aimed
# at and was killed by a different one, because at zero elapsed interval the
# mutated fold was mathematically the identity and observable only elsewhere.
out=$(cd "$WORK" && PYTHONPATH="$WORK/src" timeout "${MUTATE_TIMEOUT:-2000}" $TEST_CMD 2>&1); test_rc=$?
# A conftest.py SyntaxError (or any collection-time crash) exits non-zero with NO
# "N failed/error" summary line — pytest prints "ImportError while loading conftest"
# and a traceback, then stops. The kill condition is the EXIT CODE alone: a collection
# crash exits non-zero (pytest 2/4/5), so test_rc catches it without a summary grep,
# and a grep for "N failed/error" only added false-rejection (a hook line matching
# "N error in <text>", r5 — see GATE 2). (GATE 2 already proved the clean tree exits 0,
# so a non-zero exit here is the mutation's effect, not a pre-existing one.) With no
# FAILED/ERROR rows to extract, the killers list is empty: an unattributed KILL (or,
# with --named-test, an outside-oracle) — never a SURVIVAL.
if [ "$test_rc" -ne 0 ]; then
  # Extract the failing nodeid from each FAILED/ERROR short-summary line. pytest
  # prints "FAILED <nodeid> - <reason>" or "ERROR <nodeid-or-file> - <reason>"; the
  # nodeid is everything between the marker and " - ", so it can contain spaces
  # (pytest 9 emits literal spaces in parametrize-string IDs:
  # "FAILED tests/x.py::test_g[with space] - ..."). A [^ ]+ token truncates at the
  # first space, dropping the "space]" tail and mis-attributing the kill, and a
  # `for k in $killers` word-split fragments it further. awk strips the marker
  # prefix and the " - <reason>" suffix, preserving the full nodeid; the list stays
  # newline-delimited so spaces survive the read. No head cap: the named oracle can
  # be any failing row, not just the first four.
  # pytest 9 emits ANSI color even when stdout is a pipe — it keys color off TERM,
  # which a tmux pane sets — so a line that reads "FAILED <nodeid> - <reason>"
  # arrives as "<ESC>[31mFAILED<ESC>[0m <nodeid>...". An anchor on ^(FAILED|ERROR)
  # then matches NOTHING: every --named-test kill was silently read as
  # KILLED-OUTSIDE-ORACLE because zero killers were captured, the attribution
  # never firing at all. Strip the color first so the extraction sees the text
  # pytest meant to print. (BSD sed has no \x1b escape; $'...' expands it to the
  # literal ESC byte, portable on this shell.)
  clean=$(printf '%s' "$out" | sed $'s/\x1b\\[[0-9;]*m//g')
  kf="$WORK/killers.txt"
  printf '%s' "$clean" | python3 -c '
import re, sys
# pytest short-summary: "FAILED <nodeid> - <reason>" / "ERROR <nodeid-or-file> - <reason>".
# The nodeid can contain spaces in TWO places: a parametrize id ([a - b], [with space]) AND
# the file path itself (tests/test_thing bar.py — legal, pytest 9.0.3 collects it). A
# space-forbidding token ([^ ]+) drops the whole row when the PATH has a space, mis-
# attributing a real kill as <unnamed> or, with --named-test, falsely rejecting the exact
# oracle that failed as outside-oracle. Split on the pytest SEPARATOR " - " at bracket
# depth 0 instead: track [] nesting so " - " inside a parametrize id is skipped, and a
# literal "]" inside an id (pytest escapes it) does not corrupt the depth. The nodeid is
# everything between the marker and the first depth-0 " - " (or the whole remainder when
# there is none — the no-reason end-of-line case). This admits spaces in both path and id.
for line in sys.stdin:
    line = line.rstrip("\n")
    m = re.match(r"^(?:FAILED|ERROR) (.+)", line)
    if not m:
        continue
    rest = m.group(1)
    depth = 0
    cut = len(rest)
    i = 0
    while i < len(rest):
        c = rest[i]
        if c == "[":
            depth += 1
        elif c == "]":
            if depth > 0:
                depth -= 1
        elif depth == 0 and rest[i:i+3] == " - ":
            cut = i
            break
        i += 1
    print(rest[:cut])
' > "$kf"
  # No FAILED/ERROR rows captured: a collection-time crash (conftest SyntaxError,
  # ImportError while loading conftest, timeout) killed the suite before any test ran.
  # test_rc != 0 brought us here, so the kill is real (the suite reddened). With no
  # --named-test, "KILLED by: <unnamed>" is honest: the mutation was detected, no
  # oracle was named so none is expected to be attributed. With --named-test, the
  # crash is NOT "outside-oracle" — that verdict says a test failed but not the named
  # one, and here NO test failed (collection crashed before any could run); the named
  # oracle never ran, so the break is real but not demonstrated by it. Fail-closed
  # (exit 3) only in the --named-test case: a human confirms the mutation broke the
  # requirement the oracle owns, since the oracle never ran.
  if [ ! -s "$kf" ] && [ -n "$NAMED_TEST" ]; then
    verdict "KILLED-UNATTRIBUTED (suite-wide collection crash — '$NAMED_TEST' did not run; the break is real but not demonstrated by the oracle)"
    printf '%s\n' "$clean" | tail -8 | sed 's/^/    /'
    exit 3
  fi
  # --named-test: a kill by any test OTHER than the named oracle is a symptom,
  # not the requirement's failure (the batch0 cadence-vs-closed-form shape). The
  # full suite still runs; this only attributes the kill. Match per-killer with a
  # boundary: exact equality, a parametrized-id prefix delimited by '[' (pytest's
  # parametrize boundary — the only thing that legally abuts a nodeid, so a prefix
  # without it is a collision, not a match), OR a file-level collection ERROR (no
  # "::") which kills every test in that file — attribute it to the named oracle
  # when the oracle's file matches. An unbounded substring grep matched
  # "tests/x.py::test_g" inside the unrelated killer "tests/x.py::test_guard".
  if [ -n "$NAMED_TEST" ]; then
    # Attribute by prefix-matching the named oracle against the RAW FAILED/ERROR line, not
    # by extracting a nodeid from it and comparing. pytest's short-summary is
    # "FAILED <nodeid> - <reason>" where the nodeid can contain literal '[' / ']' / " - "
    # inside a parametrize id (pytest 9 does NOT escape them), so any bracket-depth or
    # token split mis-extracts the nodeid and the exact oracle that failed is rejected as
    # outside-oracle. Matching the KNOWN named-test string as a prefix of the raw line
    # sidesteps extraction entirely: the ambiguity is in parsing an UNKNOWN nodeid out, not
    # in matching a KNOWN one in. Quoting "$NAMED_TEST" inside the case patterns makes its
    # '[' / ']' / '*' literal (glob meta is disabled inside double quotes), so a parametrize
    # id's brackets do not corrupt the match. Three boundary modes mirror the old compare:
    # exact (line is "FAILED <NAMED_TEST> - <reason>" or bare "FAILED <NAMED_TEST>"),
    # parametrized (NAMED_TEST is the base; line is "FAILED <NAMED_TEST>[id] - ..."), and
    # file-level collection ERROR (the oracle's file errored: "ERROR <file> - ...").
    named_hit=0
    nt_file="${NAMED_TEST%%::*}"
    while IFS= read -r line; do
      case "$line" in
        "FAILED $NAMED_TEST - "*|"FAILED $NAMED_TEST"|"ERROR $NAMED_TEST - "*|"ERROR $NAMED_TEST")
          named_hit=1; break ;;
        "FAILED $NAMED_TEST["*|"ERROR $NAMED_TEST["*)
          named_hit=1; break ;;
        "ERROR $nt_file - "*|"ERROR $nt_file")
          # file-level collection ERROR (no '::'): attribute when the oracle lives in
          # that file. pytest prints the bare "ERROR <file>" (no ' - reason') for a
          # collection crash, or "ERROR <file> - <reason>" for some error types; both
          # kill every test in that file, so attribute to the named oracle in it.
          [ "$nt_file" != "$NAMED_TEST" ] && { named_hit=1; break; } ;;
      esac
    done < <(printf '%s' "$clean" | grep -E '^(FAILED|ERROR)')
    if [ "$named_hit" -ne 1 ]; then
      verdict "KILLED-OUTSIDE-ORACLE (a test failed but '$NAMED_TEST' did not — symptom, not the requirement's failure)"
      printf '%s\n' "$clean" | grep -E '^(FAILED|ERROR)' | head -8 | sed 's/^/    /'
      echo "    The suite reddened, but not on the test the requirement names. This is" >&2
      echo "    not a kill of the behavior under test; re-derive which behavior the" >&2
      echo "    requirement actually names, or name the oracle that owns it." >&2
      exit 3
    fi
  fi
  killers_display=$(tr '\n' ' ' < "$kf")
  verdict "KILLED by: ${killers_display:-<unnamed>}"
  exit 0
fi

verdict "*** SURVIVED *** — the behavior was removed and every test still passed."
echo "    $(printf '%s' "$out" | tail -1)"
echo "    Either a guard is missing, or this is an EQUIVALENT MUTANT."
echo "    Do not file a finding until you have decided which, BEHAVIOURALLY:"
echo "    exercise the mutated build directly and show the prohibited outcome"
echo "    actually occurs. In v8 two survivors were equivalent mutants — a"
echo "    zero-interval fold that is the identity, and a defense-in-depth clamp"
echo "    unobservable while the primary containment holds. Reporting either as"
echo "    a gap would have sent a lane to change correct code."
exit 1
