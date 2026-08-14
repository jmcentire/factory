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

WORK="${MUTATE_WORKDIR:-/tmp/mutate}/$NAME"
rm -rf "$WORK" 2>/dev/null
mkdir -p "$WORK" || exit 70
rsync -a --exclude .git --exclude .harness "$SRC"/ "$WORK"/ 2>/dev/null
rsync -a --delete "$TESTS"/tests/ "$WORK"/tests/ 2>/dev/null

: "${TEST_CMD:=python3 -m pytest tests/ -q -p no:randomly}"
verdict() { printf '%s: %s\n' "$NAME" "$*"; }

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
clean_out=$(cd "$WORK" && PYTHONPATH="$WORK/src" timeout "${MUTATE_TIMEOUT:-2000}" $TEST_CMD 2>&1 | tail -3)
if printf '%s' "$clean_out" | grep -qE "[0-9]+ (failed|error)"; then
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
out=$(cd "$WORK" && PYTHONPATH="$WORK/src" timeout "${MUTATE_TIMEOUT:-2000}" $TEST_CMD 2>&1)
if printf '%s' "$out" | grep -qE "[0-9]+ (failed|error)"; then
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
  printf '%s' "$clean" | awk '/^(FAILED|ERROR) /{
      line=$0; sub(/^(FAILED|ERROR) /,"",line); sub(/ - .*$/,"",line); print line
    }' > "$kf"
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
    named_hit=0
    while IFS= read -r k; do
      [ -n "$k" ] || continue
      if [ "$k" = "$NAMED_TEST" ] || [[ "$k" == "$NAMED_TEST["* ]]; then named_hit=1; break; fi
      case "$NAMED_TEST" in "$k"::*) named_hit=1; break ;; esac
    done < "$kf"
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
