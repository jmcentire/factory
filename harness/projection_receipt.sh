#!/usr/bin/env bash
# projection_receipt.sh — refuse a dispatch whose signed strategy points a lane at
# something that lane can NEVER see.
#
# Why this exists: in run v8 the Tester's projection did not contain paths the
# signed testing strategy told it to use. That is not a lane error and no amount of
# lane skill recovers from it — the projection is built fail-closed from
# `tester-include:` lines, so a path outside that set is invisible by construction.
# The lane discovered it AFTER dispatch, mid-run, and the cost was a stall plus a
# round trip through the Validator. Every input for that discovery existed BEFORE
# launch: the include list and the artifact are both on disk. Nothing checked them
# against each other.
#
# The check is deliberately about REACHABILITY, not existence. A test file the
# Tester is about to write does not exist yet and must not trip this; a source
# module outside the include set can never exist in that workspace and must.
#
#   usage: projection_receipt.sh <role> <artifact.md> [--conf <path>]
set -uo pipefail
ROLE="${1:?usage: projection_receipt.sh <role> <artifact.md> [--conf <path>]}"
ART="${2:?artifact}"
CONF="${HARNESS_PROJECTION_CONF:-.factory/projection.conf}"
shift 2
while [ $# -gt 0 ]; do case "$1" in --conf) CONF="$2"; shift 2 ;; *) shift ;; esac; done

[ -s "$ART" ] && [ ! -L "$ART" ] || {
  echo "projection-receipt: artifact is missing, empty, or a symlink: $ART" >&2
  exit 64
}

# The coder receives the full tree minus explicit exclusions, so an unreachable
# path is not a meaningful category there. Only the include-listed roles are gated.
if [ "$ROLE" != "tester" ]; then
  echo "projection-receipt: role '$ROLE' is not include-listed; nothing to verify"
  exit 0
fi
[ -f "$CONF" ] && [ ! -L "$CONF" ] || {
  echo "projection-receipt: no regular non-symlink $CONF — projection is undeclared" >&2
  exit 66
}

INCLUDE_TEXT=$(python3 - "$CONF" "${FACTORY_SOURCE_ROOT:-}" <<'PY'
import pathlib, sys

config = pathlib.Path(sys.argv[1])
source_text = sys.argv[2]
if config.is_symlink() or not config.is_file():
    raise SystemExit("projection-receipt: config must be a regular non-symlink file")
if source_text:
    source = pathlib.Path(source_text).resolve(strict=True)
    if not config.resolve(strict=True).is_relative_to(source):
        raise SystemExit("projection-receipt: config escapes the immutable source root")
for number, line in enumerate(config.read_text(encoding="utf-8").splitlines(), 1):
    prefix = "tester-include:"
    if not line.startswith(prefix):
        continue
    value = line[len(prefix):].strip()
    path = pathlib.PurePosixPath(value)
    if (not value or value == "." or "\\" in value or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.as_posix() != value.rstrip("/")):
        raise SystemExit(
            f"projection-receipt: unsafe tester-include path on line {number}: {value!r}"
        )
    print(path.as_posix())
PY
) || exit $?
INCL=()
if [ -n "$INCLUDE_TEXT" ]; then
  while IFS= read -r path; do INCL+=("$path"); done <<<"$INCLUDE_TEXT"
fi
[ "${#INCL[@]}" -gt 0 ] || { echo "projection-receipt: no tester-include lines in $CONF" >&2; exit 66; }

# Candidate paths named by the artifact: repo-relative, with a real extension or a
# directory-ish prefix. Backticked spans and bare tokens both count.
REFS=()
while IFS= read -r ref; do REFS+=("$ref"); done < <(
  grep -oE '(^|[^A-Za-z0-9_/.-])([A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(py|toml|cfg|ini|json|ya?ml|md|txt|sh)' "$ART" \
    | sed -E 's/^[^A-Za-z0-9_/.-]//' | sort -u
)

if [ "${#REFS[@]}" -eq 0 ]; then
  echo "projection-receipt: artifact names no repo-relative paths — nothing to verify"
  exit 0
fi

UNREACHABLE=()
for ref in "${REFS[@]}"; do
  ok=0
  for inc in "${INCL[@]}"; do
    case "$ref" in
      "$inc"|"$inc"/*) ok=1; break ;;
    esac
  done
  [ "$ok" -eq 1 ] || UNREACHABLE+=("$ref")
done

echo "projection-receipt: $ROLE — ${#REFS[@]} referenced path(s), ${#UNREACHABLE[@]} unreachable"
if [ "${#UNREACHABLE[@]}" -gt 0 ]; then
  echo
  echo "The signed artifact points this lane at paths its projection can never contain:"
  printf '    %s\n' "${UNREACHABLE[@]}"
  echo
  echo "Declared view: $(printf '%s ' "${INCL[@]}")"
  echo "Either widen the projection deliberately, or restate the artifact in terms"
  echo "the lane can actually reach. Widening to fix an artifact is a contamination"
  echo "decision, not a convenience — an oracle that reads the implementation is not"
  echo "independent evidence."
  exit 67
fi
echo "projection-receipt: every referenced path is inside the declared view"
