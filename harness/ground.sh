#!/usr/bin/env bash
# Re-derive run truth from disk. With --run, repository truth comes only from checked
# factory-target-state; the script never fetches or selects origin/main or ambient HEAD.
set -euo pipefail

RUN=""
RUNS_ARG="${FACTORY_RUNS_DIR:-${HARNESS_DIR:-.factory}/runs}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --run) RUN="$2"; shift 2 ;;
    --runs) RUNS_ARG="$2"; shift 2 ;;
    *) echo "ground: unknown argument: $1" >&2; exit 64 ;;
  esac
done

D="$(cd "$(dirname "$0")" && pwd -P)"
FACTORY_CLI="${FACTORY_CLI:-factory}"
if [ -n "$RUN" ]; then
  # shellcheck source=harness/run_context.sh
  source "$D/run_context.sh"
  factory_load_context "$RUN" "$RUNS_ARG"
  CONTROL="$FACTORY_CONTROL_ROOT"
  H="$FACTORY_HARNESS_ROOT"
  MARKER="$CONTROL/grounded"
  WORKDIR="$FACTORY_WORKDIR"
else
  # Legacy/hermetic mode exists for the standalone control drills only. It does not infer a
  # repository target; executable Factory launches always use --run.
  H="${HARNESS_DIR:-.factory}"
  CONTROL="$H"
  MARKER="$H/grounded"
  WORKDIR="$PWD"
  mkdir -p "$H"
fi

echo "== 1/6 directive ledger =="
( cd "$WORKDIR" && python3 "$D/directive.py" verify ${DIRECTIVE_REQUIRE_SIGS:+--sigs} )
( cd "$WORKDIR" && python3 "$D/directive.py" active --since "${LAST_GROUND:-1970-01-01T00:00:00}" )

echo "== 2/6 repository ground truth =="
if [ -n "$RUN" ]; then
  $FACTORY_CLI verify-target-state --runs "$FACTORY_RUNS_ROOT" --run-id "$RUN" >/dev/null
  echo "target-state = $FACTORY_TARGET_STATE_DIGEST"
  echo "commit       = $FACTORY_BASE_COMMIT"
  echo "source_root  = $FACTORY_SOURCE_ROOT"
  echo "workdir      = $FACTORY_WORKDIR"
else
  echo "(no authorized run context; repository selection disabled)"
fi

echo "== 3/6 cadence audit =="
HARNESS_DIR="$H" "$D/sched_audit.sh"

echo "== 4/6 tripwire =="
[ -n "${TRANSCRIPTS:-}" ] || { [ -d "$HOME/.claude/projects" ] && TRANSCRIPTS="$HOME/.claude/projects"; } || true
if [ -n "${TRANSCRIPTS:-}" ]; then
  HARNESS_DIR="$H" "$D/tripwire.sh" $TRANSCRIPTS
else
  echo "(set TRANSCRIPTS=<paths> to scan)"
fi

echo "== 5/6 channels =="
if command -v coord_list >/dev/null 2>&1; then
  coord_list | tee "$H/channels.now"
  if [ -f "$H/channels.reg" ]; then
    diff -u "$H/channels.reg" "$H/channels.now" || {
      echo "channel drift — reconcile before any lane runs"
      exit 4
    }
  else
    echo "no registry — review then: cp $H/channels.now $H/channels.reg"
  fi
else
  echo "(no coord_list on PATH)"
fi

echo "== 6/6 environment reconciliation =="
if compgen -G "$H/reconcile.d/*" >/dev/null 2>&1; then
  for reconciler in "$H"/reconcile.d/*; do
    [ -x "$reconciler" ] || continue
    echo "-- $(basename "$reconciler")"
    "$reconciler" || {
      echo "declared/live drift ($(basename "$reconciler")) — resolve before any lane runs"
      exit 5
    }
  done
else
  echo "(no reconcilers in $H/reconcile.d — register declared-vs-live probes for touched substrate)"
fi

# Recheck after every external audit. The marker says the same immutable checkout survived the
# full grounding window; it is not a timestamp asserted over a target verified only at entry.
if [ -n "$RUN" ]; then
  $FACTORY_CLI verify-target-state --runs "$FACTORY_RUNS_ROOT" --run-id "$RUN" >/dev/null
fi
python3 - "$MARKER" <<'PY'
import datetime, os, pathlib, tempfile, sys
path = pathlib.Path(sys.argv[1])
value = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ\n")
tmp = tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8")
try:
    tmp.write(value); tmp.flush(); os.fsync(tmp.fileno()); tmp.close()
    os.replace(tmp.name, path)
finally:
    if os.path.exists(tmp.name): os.unlink(tmp.name)
PY
echo "grounded @ $(tr -d '\n' < "$MARKER")"
