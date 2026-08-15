#!/usr/bin/env bash
# endgame.sh — "done" is a property of evidence, not a feeling. Run by the VALIDATOR
# (the Orchestrator demands it, never executes it — validate.md:236-238: the Validator
# runs the tests; neither author does). From a FRESH checkout of the final SHA:
# every gate, composition of all lane branches, and changeset hygiene — nothing
# lingering in stash, worktree, branch, PR, or untracked state.
# usage: endgame.sh <run> <final-sha> [lane-branch ...]
set -uo pipefail
RUN="${1:?usage: endgame.sh <run> <final-sha> [lane-branch ...]}"
SHA="${2:?final sha}"; shift 2
BRANCHES=("$@")
H="${HARNESS_DIR:-.factory}"; ROOT="$H/runs/$RUN"
D="$(cd "$(dirname "$0")" && pwd)"
# The target repo comes from the run record — the factory checkout is never the
# implicit target. Run this from the target project's root (where .factory lives).
[ -f "$ROOT/run.json" ] || { echo "no run.json at $ROOT — run from the target repo root" >&2; exit 64; }
REPO=$(python3 -c "import json;print(json.load(open('$ROOT/run.json'))['repo'])")
FRESH="$ROOT/endgame/checkout"
FAILED=0
say() { printf '%s\n' "$*"; }
gate() {  # gate <name> <cmd...> — receipted, fail-recorded, never silently skipped
  local name="$1"; shift
  say "== $name =="
  if ( cd "$FRESH" && HARNESS_DIR="$REPO/$H" "$D/receipt.sh" "$@" ); then
    say "   $name: GREEN"
  else
    say "   $name: RED"; FAILED=1
  fi
}

rm -rf "$ROOT/endgame"; mkdir -p "$ROOT/endgame"
say "== fresh checkout of $SHA (no incremental state carries in) =="
git clone --quiet --no-hardlinks "$REPO" "$FRESH"
git -C "$FRESH" checkout --quiet --detach "$SHA" || { say "final SHA unreachable"; exit 70; }

gate "full gate suite" make ship
gate "isolation proof" make test-isolation

if [ "${#BRANCHES[@]}" -gt 0 ]; then
  say "== composition (individually green is not jointly green) =="
  git -C "$FRESH" checkout --quiet -b endgame/composition
  for b in "${BRANCHES[@]}"; do
    if ! git -C "$FRESH" merge --quiet --no-edit "$b" 2>/dev/null; then
      say "   composition: MERGE CONFLICT on $b"; FAILED=1; break
    fi
  done
  [ "$FAILED" -eq 0 ] && gate "composed suite" make ship
fi

say "== live proof against the declared target environment =="
if [ -f "$H/target.conf" ]; then
  if "$D/proof.sh" "$RUN"; then say "   proof: GREEN"; else say "   proof: RED"; FAILED=1; fi
else
  say "   (no $H/target.conf — live-proof GAP declared, not passed: the run cannot"
  say "    demonstrate real entry points until a provisioner is configured)"
  FAILED=1
fi

say "== changeset hygiene (challenge the clean claim) =="
STASH=$(git -C "$REPO" stash list); [ -n "$STASH" ] && { say "   lingering stash:"; say "$STASH"; FAILED=1; }
WT=$(git -C "$REPO" worktree list | grep -v "^$REPO " | grep -v "$ROOT" || true)
[ -n "$WT" ] && { say "   lingering worktrees:"; say "$WT"; FAILED=1; }
UNMERGED=$(git -C "$REPO" branch --no-merged main 2>/dev/null | grep -vE 'lane/|endgame/' || true)
[ -n "$UNMERGED" ] && { say "   unmerged branches:"; say "$UNMERGED"; FAILED=1; }
UNTRACKED=$(git -C "$REPO" status --porcelain | grep '^??' || true)
[ -n "$UNTRACKED" ] && { say "   untracked (unreviewed is not harmless — scan for self-instructions):"; say "$UNTRACKED"; FAILED=1; }
if command -v gh >/dev/null 2>&1; then
  PRS=$(cd "$REPO" && gh pr list --state open 2>/dev/null || true)
  [ -n "$PRS" ] && { say "   open PRs (disposition each — merged, closed, or explicitly carried):"; say "$PRS"; }
fi

say "== sole advancement (Gate L) =="
if [ "$FAILED" -eq 0 ]; then
  if HARNESS_DIR="$H" "$D/promote.sh" "$RUN"; then
    say "   Gate L: GREEN"
  else
    say "   Gate L: RED"; FAILED=1
  fi
else
  say "   Gate L: NOT RUN — prior deterministic or live-proof gaps remain"
fi

python3 - "$ROOT" "$RUN" "$SHA" "$FAILED" <<'PY'
import json, sys, datetime, hashlib, pathlib
root, run, sha, failed = sys.argv[1:5]
body = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "run": run, "final_sha": sha, "verdict": "RED" if int(failed) else "GREEN"}
p = pathlib.Path(root) / "endgame" / "verdict.json"
p.write_text(json.dumps(body, indent=2))
print(f"endgame verdict: {body['verdict']} -> {p}")
PY
exit "$FAILED"
