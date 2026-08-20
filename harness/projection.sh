#!/usr/bin/env bash
# Mint an asymmetric lane projection from an already verified, run-owned source checkout.
# usage: projection.sh <coder|tester> <source-root> <exact-commit> <dest-dir>
set -euo pipefail
ROLE="${1:?usage: projection.sh <coder|tester> <source-root> <exact-commit> <dest-dir>}"
SRC="${2:?source root}"
SHA="${3:?exact commit}"
DEST="${4:?destination}"
CONF="${HARNESS_PROJECTION_CONF:-$SRC/.factory/projection.conf}"

[ -d "$SRC" ] && [ ! -L "$SRC" ] || { echo "refusing: source root is missing or a symlink" >&2; exit 64; }
[ ! -e "$DEST" ] && [ ! -L "$DEST" ] || {
  echo "refusing: $DEST exists (projections are minted fresh)" >&2
  exit 65
}
case "$ROLE" in coder|tester) ;; *) echo "unknown role: $ROLE" >&2; exit 64 ;; esac

EXPECTED=$(git -C "$SRC" rev-parse --verify "$SHA^{commit}" 2>/dev/null) || {
  echo "refusing: exact authorized commit is unavailable in source root" >&2
  exit 66
}
[ "$EXPECTED" = "$SHA" ] || { echo "refusing: commit did not resolve exactly" >&2; exit 66; }
TREE=$(git -C "$SRC" rev-parse --verify "$SHA^{tree}")
[ -z "$(git -C "$SRC" status --porcelain --untracked-files=all)" ] || {
  echo "refusing: immutable source checkout diverged before projection" >&2
  exit 67
}

if [ -e "$CONF" ] || [ -L "$CONF" ]; then
  python3 - "$SRC" "$CONF" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve(strict=True)
conf = pathlib.Path(sys.argv[2])
if conf.is_symlink() or not conf.is_file():
    raise SystemExit("refusing: projection config must be a regular non-symlink file")
resolved = conf.resolve(strict=True)
if not resolved.is_relative_to(root):
    raise SystemExit("refusing: projection config escapes the immutable source root")
PY
fi

read_paths() {
  python3 - "$CONF" "$1" <<'PY'
import pathlib, sys
conf, key = pathlib.Path(sys.argv[1]), sys.argv[2]
if not conf.is_file():
    raise SystemExit(0)
for number, line in enumerate(conf.read_text(encoding="utf-8").splitlines(), 1):
    prefix = key + ":"
    if not line.startswith(prefix):
        continue
    value = line[len(prefix):].strip()
    path = pathlib.PurePosixPath(value)
    if (not value or value in {"."} or "\\" in value or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.as_posix() != value.rstrip("/")):
        raise SystemExit(f"refusing: unsafe {key} path on line {number}: {value!r}")
    print(path.as_posix())
PY
}

EXCLUDE_TEXT=$(read_paths coder-exclude) || exit $?
INCLUDE_TEXT=$(read_paths tester-include) || exit $?
EXCLUDES=()
INCLUDES=()
if [ -n "$EXCLUDE_TEXT" ]; then
  while IFS= read -r path; do EXCLUDES+=("$path"); done <<<"$EXCLUDE_TEXT"
fi
if [ -n "$INCLUDE_TEXT" ]; then
  while IFS= read -r path; do INCLUDES+=("$path"); done <<<"$INCLUDE_TEXT"
fi
if [ "$ROLE" = tester ] && [ "${#INCLUDES[@]}" -eq 0 ]; then
  echo "refusing: no tester projection declared in $CONF (tester-include: <path> lines)." >&2
  echo "An undeclared oracle view is a contamination vector, not a default." >&2
  exit 66
fi

mkdir -p "$DEST"
if [ "$ROLE" = coder ]; then
  ARCHIVE_ARGS=(git -C "$SRC" archive "$SHA")
  if [ "${#EXCLUDES[@]}" -gt 0 ]; then
    ARCHIVE_ARGS+=(-- .)
    for excluded in "${EXCLUDES[@]}"; do
      ARCHIVE_ARGS+=(":(exclude)$excluded")
    done
  fi
  "${ARCHIVE_ARGS[@]}" | tar -x -C "$DEST"
else
  git -C "$SRC" archive "$SHA" -- "${INCLUDES[@]}" | tar -x -C "$DEST"
fi

# Prove the source was unchanged across the copy window; a projection receipt over a moving
# baseline is not evidence of any one subject.
[ "$TREE" = "$(git -C "$SRC" rev-parse --verify "$SHA^{tree}")" ] && \
  [ -z "$(git -C "$SRC" status --porcelain --untracked-files=all)" ] || {
    echo "refusing: immutable source checkout diverged during projection" >&2
    exit 67
  }

( cd "$DEST" && git init --quiet -b "lane/$ROLE" && git add -A && \
  git -c user.name=harness -c user.email=harness@local \
    commit --quiet -m "projection: $ROLE view of immutable target" )

python3 - "$ROLE" "$SHA" "$TREE" "$SRC" "$DEST" <<'PY'
import hashlib, json, os, pathlib, stat, sys
role, commit, tree, source, destination = sys.argv[1:]
root = pathlib.Path(destination)
h = hashlib.sha256()
for base, dirs, files in os.walk(root, followlinks=False):
    dirs[:] = sorted(name for name in dirs if name != ".git")
    for name in sorted(files):
        path = pathlib.Path(base) / name
        rel = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        h.update(rel.encode() + b"\0" + oct(stat.S_IMODE(mode)).encode() + b"\0")
        if path.is_symlink():
            h.update(b"link\0" + os.readlink(path).encode())
        else:
            h.update(b"file\0" + path.read_bytes())
body = {
    "role": role, "sha": commit, "tree": tree, "source_root": source,
    "dest": destination, "manifest_digest": "sha256:" + h.hexdigest(),
}
print(json.dumps(body, sort_keys=True, separators=(",", ":")))
PY
