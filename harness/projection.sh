#!/usr/bin/env bash
# projection.sh — asymmetric lane workspaces from one pinned SHA (I3; isolated loop).
# A lane sees exactly what its role permits, and the projection is data, not
# discipline. Both roles are built from `git archive` into a FRESH repo with no
# ancestry: upstream history and commit messages are a contamination channel
# (commit messages alone contaminated the Tester in the postmortem), so no lane
# receives them. The lane's repo starts at one scrubbed base commit.
#   coder:  full tree at the SHA (real toolchain, contract tests included), minus
#           paths declared `coder-exclude:` in .harness/projection.conf.
#   tester: ONLY the paths declared `tester-include:`. No declared tester
#           projection = refusal, not a guess — fail-closed beats an accidental
#           full view.
# usage: projection.sh <coder|tester> <src-repo> <sha> <dest-dir>
set -euo pipefail
ROLE="${1:?usage: projection.sh <coder|tester> <src-repo> <sha> <dest-dir>}"
SRC="${2:?src repo}"; SHA="${3:?pinned sha}"; DEST="${4:?dest dir}"
CONF="${HARNESS_PROJECTION_CONF:-.harness/projection.conf}"
[ -e "$DEST" ] && { echo "refusing: $DEST exists (projections are minted fresh)" >&2; exit 65; }

conf_lines() { { [ -f "$CONF" ] && grep -E "^$1:" "$CONF" | sed "s/^$1:[[:space:]]*//"; } || true; }

mkdir -p "$DEST"
case "$ROLE" in
  coder)
    git -C "$SRC" archive "$SHA" | tar -x -C "$DEST"
    while IFS= read -r excl; do
      [ -n "$excl" ] && rm -rf "${DEST:?}/$excl"
    done < <(conf_lines coder-exclude)
    ;;
  tester)
    mapfile -t INCL < <(conf_lines tester-include)
    if [ "${#INCL[@]}" -eq 0 ]; then
      echo "refusing: no tester projection declared in $CONF (tester-include: <path> lines)." >&2
      echo "An undeclared oracle view is a contamination vector, not a default." >&2
      rmdir "$DEST" 2>/dev/null || true
      exit 66
    fi
    git -C "$SRC" archive "$SHA" -- "${INCL[@]}" | tar -x -C "$DEST"
    ;;
  *) rmdir "$DEST" 2>/dev/null || true; echo "unknown role: $ROLE" >&2; exit 64 ;;
esac

# One scrubbed base commit; no upstream ancestry to leak, a real repo to work in.
( cd "$DEST" && git init --quiet -b "lane/$ROLE" && git add -A && \
  git -c user.name=harness -c user.email=harness@local \
    commit --quiet -m "projection: $ROLE view of $SHA" )

MANIFEST=$( (cd "$DEST" && find . -path ./.git -prune -o -type f -print | sort | \
  xargs shasum -a 256 2>/dev/null) | shasum -a 256 | cut -d' ' -f1)
echo "{\"role\":\"$ROLE\",\"sha\":\"$SHA\",\"dest\":\"$DEST\",\"manifest_digest\":\"$MANIFEST\"}"
