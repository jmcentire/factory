#!/usr/bin/env bash
# Shared checked projection loader. This is sourced by harness entry points; it never selects a
# repository, ref, SHA, or working directory from cwd, an operator checkout, or harness metadata.

factory_load_context() {
  local run="${1:?run id required}"
  local runs_in="${2:?runs root required}"
  local cli="${FACTORY_CLI:-factory}"
  local runs root status

  [[ "$run" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || {
    echo "Invalid Factory run id: $run" >&2
    return 64
  }
  [ -d "$runs_in" ] || { echo "Factory runs root does not exist: $runs_in" >&2; return 64; }
  runs="$(cd "$runs_in" && pwd -P)"
  root="$runs/$run"
  [ -d "$root" ] || { echo "Factory run does not exist: $root" >&2; return 64; }
  status="$($cli status --runs "$runs" --run-id "$run")" || return $?

  local values=()
  mapfile -t values < <(
    printf '%s' "$status" | python3 -c '
import json, pathlib, sys
expected_root = pathlib.Path(sys.argv[1]).resolve(strict=True)
doc = json.load(sys.stdin)
if doc.get("schema_version") != "factory-run/3":
    raise SystemExit("legacy run schemas cannot dispatch")
allowed = {
    "intake", "product-specification-ratified", "architecture-ratified",
    "operational-maturity-ratified", "building", "validating", "preview",
    "human-approved", "ci", "promoted", "specification-defect", "blocked",
}
state = doc.get("state")
if state not in allowed:
    raise SystemExit(f"run is not execution-authorized: {state!r}")
target = doc.get("target_state")
if not isinstance(target, dict):
    raise SystemExit("run projection has no target_state")
control = pathlib.Path(str(target.get("control_root", ""))).resolve(strict=True)
if control != expected_root:
    raise SystemExit(f"target-state control_root mismatch: {control} != {expected_root}")
fields = (
    str(doc.get("state", "")), str(doc.get("target_state_digest", "")),
    str(target.get("source_root", "")), str(target.get("workdir", "")),
    str(target.get("object_store", "")), str(target.get("resolved_commit", "")),
    str(target.get("resolved_tree", "")), str(target.get("checkout_id", "")),
    str(doc.get("target_digest", "")), str(doc.get("source_digest", "")),
    str(doc.get("generation", "")),
)
if any("\n" in value or "\r" in value for value in fields):
    raise SystemExit("run context fields may not contain newlines")
print("\n".join(fields))
' "$root"
  )
  [ "${#values[@]}" -eq 11 ] || {
    echo "Factory projection could not produce a complete execution context" >&2
    return 70
  }
  $cli verify-target-state --runs "$runs" --run-id "$run" >/dev/null || return $?
  # Stage-E request bytes are retained separately from the lifecycle ledger, so re-derive their
  # digest against the unique intake entry on every harness read. Once ignition metadata exists,
  # TASK.md must also be the exact signed verbatim request; no consumer may trust the neighboring
  # file path by convention alone.
  if [ -e "$root/harness.json" ] || [ -L "$root/harness.json" ]; then
    [ -f "$root/TASK.md" ] && [ ! -L "$root/TASK.md" ] || {
      echo "Factory task artifact is missing or a symlink: $root/TASK.md" >&2
      return 70
    }
    $cli verify-execution-request --runs "$runs" --run-id "$run" \
      --task-file "$root/TASK.md" >/dev/null || return $?
  else
    $cli verify-execution-request --runs "$runs" --run-id "$run" >/dev/null || return $?
  fi

  FACTORY_RUNS_ROOT="$runs"
  FACTORY_CONTROL_ROOT="$root"
  FACTORY_HARNESS_ROOT="$(dirname "$runs")"
  FACTORY_HARNESS_META="$root/harness.json"
  FACTORY_RUN_STATE="${values[0]}"
  FACTORY_TARGET_STATE_DIGEST="${values[1]}"
  FACTORY_SOURCE_ROOT="${values[2]}"
  FACTORY_WORKDIR="${values[3]}"
  FACTORY_OBJECT_STORE="${values[4]}"
  FACTORY_BASE_COMMIT="${values[5]}"
  FACTORY_BASE_TREE="${values[6]}"
  FACTORY_CHECKOUT_ID="${values[7]}"
  FACTORY_TARGET_MANIFEST_DIGEST="${values[8]}"
  FACTORY_SOURCE_DIGEST="${values[9]}"
  FACTORY_GENERATION="${values[10]}"
  export FACTORY_RUNS_ROOT FACTORY_CONTROL_ROOT FACTORY_HARNESS_ROOT
  export FACTORY_HARNESS_META FACTORY_RUN_STATE
  export FACTORY_TARGET_STATE_DIGEST FACTORY_SOURCE_ROOT FACTORY_WORKDIR
  export FACTORY_OBJECT_STORE FACTORY_BASE_COMMIT FACTORY_BASE_TREE
  export FACTORY_CHECKOUT_ID FACTORY_TARGET_MANIFEST_DIGEST
  export FACTORY_SOURCE_DIGEST
  export FACTORY_GENERATION
}
