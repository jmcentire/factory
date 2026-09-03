#!/usr/bin/env bash
# phase1_gate.sh — make Phase 1 FAILABLE.
#
# Why this exists: run v8's founder critique was "the factory failed on the first
# step." It is correct, and the existing precondition could not have caught it.
# dispatch_lane.sh verifies the Phase-A artifacts EXIST and are signed. All four
# existed, all four were signed — and six amendments were then authored AFTER
# ratification while the lanes were already coding, pinning the seam contract, the
# MCP accessor shape, the note/flag identity and the disclosure channel. One
# amendment retracted the one before it, an hour apart. Existence was never the
# question. Adequacy was, and nothing measured it.
#
# This gate measures the five adequacy surfaces now known to have failed in live
# runs, and it is
# fail-closed: a run that cannot pass it should not launch lanes.
#
#   usage: phase1_gate.sh <run> [--root <control-root>] [--workdir <target-workdir>]
# There is deliberately no ambient override. A human may amend and re-ratify the artifacts;
# an environment variable may not convert an inadequate phase into authority.
set -uo pipefail
RUN="${1:?usage: phase1_gate.sh <run> [--root <control-root>] [--workdir <target-workdir>]}"; shift || true
ROOT="${HARNESS_RUN_ROOT:-}"
WORKDIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT="$2"; shift 2 ;;
    --workdir) WORKDIR="$2"; shift 2 ;;
    --repo) echo "phase1: --repo is forbidden; pass checked --root/--workdir" >&2; exit 64 ;;
    *) echo "phase1: unknown argument: $1" >&2; exit 64 ;;
  esac
done
[ -n "$ROOT" ] || ROOT="${HARNESS_DIR:-.factory}/runs/$RUN"
[ -z "$WORKDIR" ] || [ -d "$WORKDIR" ] || { echo "phase1: workdir missing: $WORKDIR" >&2; exit 64; }
ART="$ROOT/artifacts"
[ -d "$ART" ] || { echo "phase1: no artifacts dir at $ART" >&2; exit 64; }

SPEC="$ART/product-specification.md"
STRAT="$ART/testing-strategy.md"
[ -s "$SPEC" ] || { echo "phase1: missing $SPEC" >&2; exit 64; }

FAILURES=0
note() { printf '  %s\n' "$*"; }
fail() { FAILURES=$((FAILURES+1)); printf 'FAIL  %s\n' "$*"; }
pass() { printf 'ok    %s\n' "$*"; }

echo "== phase1 adequacy gate — run $RUN =="

# --- A. No requirement may name the oracle that checks it --------------------
# v8's original R6.1 named a specific test function inside the signed spec, and
# the coder dispatch compounded it. Amendment 2 recorded it as a defect in the
# SPECIFICATION, not in either lane: a requirement written in terms of its test is
# an instruction to satisfy the test rather than the behavior. The requirement must
# state an observable effect; how it is checked belongs to the oracle.
# Only requirement BODIES are scanned — amendments that discuss the rule are not
# themselves violations, or this gate would flag its own postmortem.
req_body=$(awk '/^- \*\*R[0-9]+\.[0-9]+/,/^$/' "$SPEC")
if printf '%s' "$req_body" | grep -qiE 'test_[a-z0-9_]+|tests?/[A-Za-z0-9_]+\.py|::[A-Za-z_]|pytest'; then
  fail "A: a requirement names its oracle (test function, test path, or pytest node)"
  printf '%s' "$req_body" | grep -inE 'test_[a-z0-9_]+|tests?/[A-Za-z0-9_]+\.py|::[A-Za-z_]|pytest' \
    | head -4 | sed 's/^/        /'
  note "  a requirement states an observable EFFECT; the oracle is not the requirement"
else
  pass "A: no requirement names its oracle"
fi

# --- B. Every requirement is carried by the testing strategy ------------------
# An unreferenced requirement is one nobody has planned to observe. Cheap to check,
# and it is the link that makes the oracle-quality pass possible at all.
if [ -s "$STRAT" ]; then
  missing=""
  for id in $(grep -oE '^- \*\*R[0-9]+\.[0-9]+' "$SPEC" | grep -oE 'R[0-9]+\.[0-9]+' | sort -u); do
    grep -q "$id" "$STRAT" || missing="$missing $id"
  done
  if [ -n "$missing" ]; then
    fail "B: requirements absent from the testing strategy:$missing"
  else
    pass "B: every requirement appears in the testing strategy"
  fi
else
  fail "B: no testing-strategy.md — the oracle plan is not an artifact"
fi

# --- C. The strategy must say how each oracle is shown NON-VACUOUS ------------
# batch0 shipped a vacuous oracle on its headline requirement: it cold-started both
# databases and then made immediate calls, so every compared call was a no-op. It
# failed at base for the wrong reason and passed at head for the wrong reason, and
# every gate in that run stayed green. Red-now proves a test CAN fail; it never
# proves the test is ABOUT the requirement. The strategy must declare the method
# per requirement, before anyone writes code.
# A single keyword anywhere in the document is NOT a declaration, and this check
# shipped accepting exactly that: a strategy whose entire content was the tokens
# "R1.1 R1.2. mutation" passed. Worse, it would have passed v8's real strategy,
# whose prescribed method WAS the defect. The declaration must sit with the
# requirement it describes, so the method is stated per requirement rather than
# gestured at once for the document.
if [ -s "$STRAT" ]; then
  cgap=$(python3 - "$SPEC" "$STRAT" <<'PY'
import re, sys
spec, strat = open(sys.argv[1]).read(), open(sys.argv[2]).read().splitlines()
ids = sorted(set(re.findall(r'^- \*\*(R\d+\.\d+)', spec, re.M)))
tok = re.compile(r'non-?vacu|reachab|discriminat|mutation|forcing|red[- ]now', re.I)
missing = []
for rid in ids:
    hit = False
    for i, line in enumerate(strat):
        if rid in line:
            lo, hi = max(0, i - 5), min(len(strat), i + 6)
            if any(tok.search(l) for l in strat[lo:hi]):
                hit = True; break
    if not hit:
        missing.append(rid)
# Proximity alone is defeated by a one-line document, where a single token sits
# within the window of every requirement — the exact strategy that passed before.
# One declaration cannot serve N requirements, so require at least as many
# distinct declarations as there are requirements.
decls = sum(1 for l in strat if tok.search(l))
if ids and decls < len(ids):
    missing.append(f"[only {decls} declaration(s) for {len(ids)} requirements]")
print(" ".join(missing))
PY
)
  if [ -z "$cgap" ]; then
    pass "C: every requirement states how its oracle is shown non-vacuous"
  else
    fail "C: no non-vacuous-oracle method stated near these requirements:$cgap"
    note "  a keyword elsewhere in the document is not a declaration — state, per"
    note "  requirement, how its oracle is shown to reach the path, to discriminate,"
    note "  and to fail at base FOR THE REASON THE REQUIREMENT NAMES"
  fi
fi

# --- D. Oracle-contract manifest ---------------------------------------------
# Proposed by the v8 tester after losing time to three Phase-A contract defects
# discovered only AFTER dispatch: test-visible signatures, return/tuple shapes,
# and marker registration locations that no artifact declared. The oracle lane
# cannot see the implementation by design, so anything it must call is a contract
# and belongs in Phase A, signed and digested like the rest.
if [ -s "$ART/oracle-contract.md" ]; then
  pass "D: oracle-contract manifest present"
else
  fail "D: no oracle-contract.md (test-visible signatures, return shapes, marker locations)"
fi

# --- E. Evidence-derived semantic closure -----------------------------------
# res-r1 v2 proved that appended semantics transmit when they are actually
# written, while known lane-trace ambiguities and adversarial findings were
# omitted from the amendment assembled from memory. Token presence is not a
# disposition. The signed Product Specification must contain the exact canonical
# union derived from every retained source's separately recorded extraction manifests,
# and every member must have a closed typed ruling before either lane launches.
semantic_result=$(python3 "$(cd "$(dirname "$0")" && pwd -P)/semantic_union.py" verify \
  --artifacts "$ART" --spec "$SPEC" 2>&1)
if [ "$?" -eq 0 ]; then
  pass "E: signed Product Specification exactly closes the semantic evidence union"
  note "  $semantic_result"
else
  fail "E: semantic evidence union is absent, stale, incomplete, or open"
  note "  $semantic_result"
fi

# --- F. Cross-path agreement closure -----------------------------------------
# A collection of locally adequate path tests is not an oracle for the agreement
# between those paths. New runs close an exact participant inventory over every
# configured Product-requirement region and render the resulting single/cross-path
# classification into the signed Testing Strategy. Runs that predate the ignition
# metadata remain legacy rather than being silently reinterpreted.
agreement_result=$(python3 "$(cd "$(dirname "$0")" && pwd -P)/agreement_contract.py" \
  verify-plan --root "$ROOT" --artifacts "$ART" 2>&1)
if [ "$?" -eq 0 ]; then
  pass "F: agreement plan exactly covers every configured requirement region"
  note "  $agreement_result"
else
  fail "F: agreement plan is absent, stale, incomplete, or downgraded"
  note "  $agreement_result"
fi

# --- G. One ordered compiler for per-run guidance and agreement --------------
# Standards, loops, and recipes selected through the external resume checkpoint
# are configuration until their obligation-by-obligation application is rendered
# into the three ratified authorities. The compiler verifies those exact regions
# in semantic-union -> guidance -> agreement order. With no selection this is an
# explicit none path; a missing or hand-edited selected obligation is a refusal.
compiler_result=$(python3 "$(cd "$(dirname "$0")" && pwd -P)/phase_compiler.py" verify \
  --root "$ROOT" --artifacts "$ART" 2>&1)
if [ "$?" -eq 0 ]; then
  pass "G: ordered phase compiler exactly applies selected run guidance"
  note "  $compiler_result"
else
  fail "G: selected guidance is absent, stale, misrouted, or out of order"
  note "  $compiler_result"
fi

# --- report: post-ratification amendments are the adequacy measurement --------
AMEND=$(grep -h '^## AMENDMENT' "$ART"/*.md 2>/dev/null | wc -l | tr -d ' ')
echo
echo "post-ratification amendments so far: ${AMEND:-0}   (v8 baseline: 6, all authored while lanes coded)"

if [ "$FAILURES" -gt 0 ]; then
  echo
  echo "phase1 gate: $FAILURES failure(s). Lanes must not launch."
  echo "Fix and re-ratify the artifacts before dispatch."
  # Phase 0.1: gate G's refusal leaves a derivable signal before exiting.
  python3 "$(cd "$(dirname "$0")" && pwd -P)/attention_gate.py" refusal-event \
    --root "$ROOT" --kind refusal-phase1-gate --source phase1_gate.sh \
    --detail "phase1 gate: $FAILURES failure(s); lanes must not launch" --exit-code 71 || \
    echo "phase1: refusal event could not be recorded" >&2
  exit 71
fi
echo
echo "phase1 gate: clean."
