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
#
# <patch.py> receives the mutated tree root as argv[1]. It MUST assert its own
# anchor and exit nonzero when the anchor is missing — see GATE 3.
set -uo pipefail

NAME="${1:?usage: mutate.sh <name> <patch.py> --src <tree> --tests <tree>}"; shift
PATCH="${1:?patch script}"; shift
SRC=""; TESTS=""; TEST_CMD=""
while [ $# -gt 0 ]; do
  case "$1" in
    --src)      SRC="$2"; shift 2 ;;
    --tests)    TESTS="$2"; shift 2 ;;
    --test-cmd) TEST_CMD="$2"; shift 2 ;;
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
  killers=$(printf '%s' "$out" | grep -oE '^FAILED [^ ]+' | sed 's/^FAILED //' | head -4 | tr '\n' ' ')
  verdict "KILLED by: ${killers:-<unnamed>}"
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
