#!/usr/bin/env bash
# Mint an asymmetric lane projection from an already verified, run-owned source checkout.
# usage: projection.sh <coder|tester> <source-root> <exact-commit> <dest-dir>
#
# A tester-include path absent at the pin is skipped and named in the receipt (absent_includes):
# the Tester's own suite (tests/acceptance) never exists before the Tester writes it. When the pin
# has no `contracts` and the run supplies its ratified contracts (HARNESS_RUN_CONTRACTS, a
# directory, with HARNESS_ORACLE_CONTRACT, the oracle-contract.md that declares every contract
# file's sha256), the Tester's view gets those files at contracts/, each checked against its
# declared digest, and the receipt names them (run_contracts). herald-dash-r2 stalled on exactly
# this: the Validator authors contracts/ during Phase A, so the pin cannot hold them.
set -euo pipefail
# shellcheck source=harness/factory_python.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/factory_python.sh"
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
# A status that could not be read is not a clean status: `[ -z "$(git status)" ]` alone reads a
# failed git as an unchanged checkout.
source_is_clean() {
  local status
  status=$(git -C "$SRC" status --porcelain --untracked-files=all) || {
    echo "refusing: source checkout status could not be read" >&2
    return 1
  }
  [ -z "$status" ]
}
source_is_clean || {
  echo "refusing: immutable source checkout diverged before projection" >&2
  exit 67
}

# Both lanes need a declared projection: without one the Coder would get the full tree, tests
# included, with no exclusions.
[ -e "$CONF" ] || [ -L "$CONF" ] || {
  echo "refusing: no projection config at $CONF; an undeclared projection is a contamination vector (the coder would get the full tree, tests included)" >&2
  exit 66
}
{
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
}

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

PRESENT=()
ABSENT=()
if [ "$ROLE" = tester ]; then
  for included in "${INCLUDES[@]}"; do
    if git -C "$SRC" cat-file -e "$SHA:$included" 2>/dev/null; then
      PRESENT+=("$included")
    else
      ABSENT+=("$included")
    fi
  done
fi
RUN_CONTRACTS="${HARNESS_RUN_CONTRACTS:-}"
SUPPLY_CONTRACTS=0
if [ "$ROLE" = tester ] && [ -n "$RUN_CONTRACTS" ]; then
  for absent in ${ABSENT[@]+"${ABSENT[@]}"}; do
    if [ "$absent" = contracts ]; then SUPPLY_CONTRACTS=1; fi
  done
fi
if [ "$ROLE" = tester ] && [ "${#PRESENT[@]}" -eq 0 ] && [ "$SUPPLY_CONTRACTS" -eq 0 ]; then
  echo "refusing: no tester-include path exists at $SHA and the run supplied no contracts" >&2
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
  if [ "${#PRESENT[@]}" -gt 0 ]; then
    git -C "$SRC" archive "$SHA" -- "${PRESENT[@]}" | tar -x -C "$DEST"
  fi
fi
RUN_CONTRACTS_RECEIPT=null
if [ "$SUPPLY_CONTRACTS" -eq 1 ]; then
  RUN_CONTRACTS_RECEIPT=$(python3 - "$RUN_CONTRACTS" "${HARNESS_ORACLE_CONTRACT:-}" "$DEST" <<'PY'
import hashlib, json, pathlib, re, shutil, sys
source, oracle, destination = pathlib.Path(sys.argv[1]), sys.argv[2], pathlib.Path(sys.argv[3])
if not oracle:
    raise SystemExit("refusing: HARNESS_RUN_CONTRACTS needs HARNESS_ORACLE_CONTRACT to check it")
oracle_path = pathlib.Path(oracle)
for label, item in (("run contracts", source), ("oracle contract", oracle_path)):
    if item.is_symlink() or not item.exists():
        raise SystemExit(f"refusing: {label} {item} is missing or a symlink")
row = re.compile(r"^\|\s*`contracts/([^`/]+)`\s*\|\s*`(sha256:[0-9a-f]{64})`\s*\|")
declared = {}
for line in oracle_path.read_text(encoding="utf-8").splitlines():
    match = row.match(line.strip())
    if match:
        declared[match.group(1)] = match.group(2)
if not declared:
    raise SystemExit(f"refusing: {oracle_path} declares no contracts/<file> sha256 digests")
present = {}
for item in sorted(source.iterdir()):
    if item.is_symlink() or not item.is_file():
        raise SystemExit(f"refusing: run contract {item.name} is not a regular file")
    present[item.name] = item
if set(present) != set(declared):
    missing = sorted(set(declared) - set(present))
    extra = sorted(set(present) - set(declared))
    raise SystemExit(f"refusing: run contracts differ from the oracle contract "
                     f"(missing {missing}, undeclared {extra})")
target = destination / "contracts"
target.mkdir()
files = {}
for name, item in present.items():
    body = item.read_bytes()
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    if digest != declared[name]:
        raise SystemExit(f"refusing: contracts/{name} is {digest}, the oracle contract says "
                         f"{declared[name]}")
    (target / name).write_bytes(body)
    files[name] = digest
oracle_digest = "sha256:" + hashlib.sha256(oracle_path.read_bytes()).hexdigest()
print(json.dumps({"source": str(source), "oracle_contract": str(oracle_path),
                  "oracle_contract_digest": oracle_digest, "files": files},
                 sort_keys=True, separators=(",", ":")))
PY
  ) || exit 66
fi
ABSENT_JSON=$(python3 -c 'import json, sys; print(json.dumps(sys.argv[1:]))' ${ABSENT[@]+"${ABSENT[@]}"})

# Prove the source was unchanged across the copy window; a projection receipt over a moving
# baseline is not evidence of any one subject.
[ "$TREE" = "$(git -C "$SRC" rev-parse --verify "$SHA^{tree}")" ] && source_is_clean || {
    echo "refusing: immutable source checkout diverged during projection" >&2
    exit 67
  }

( cd "$DEST" && git init --quiet -b "lane/$ROLE" && git add -A && \
  git -c user.name=harness -c user.email=harness@local \
    commit --quiet -m "projection: $ROLE view of immutable target" )

python3 - "$ROLE" "$SHA" "$TREE" "$SRC" "$DEST" "$ABSENT_JSON" "$RUN_CONTRACTS_RECEIPT" <<'PY'
import hashlib, json, os, pathlib, stat, sys
role, commit, tree, source, destination, absent, supplied = sys.argv[1:]
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
if role == "tester":
    body["absent_includes"] = json.loads(absent)
    body["run_contracts"] = json.loads(supplied)
print(json.dumps(body, sort_keys=True, separators=(",", ":")))
PY
