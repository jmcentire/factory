#!/usr/bin/env bash
# promote.sh — Gate L: the SOLE writer of a run's "closed" status.
#
# Why this exists: the doctrine (HARNESS.md, "two-layer validation split") holds that a
# judge is never a gate — semantic verdicts advise, they do not advance. Advancement is
# deterministic: a run closes ONLY through decide_promotion, the pure promotion decision in
# factory_core. Before this script, nothing wrote run.json "closed" (factory.sh writes
# "open"; the dispatcher READS "closed" to stop but never writes it), so a green `make ship`
# was the de-facto close path — a route-around with no verified evidence. This script renders
# the decide_promotion verdict (via the factory CLI, the trust anchor) and writes "closed"
# ONLY when the verdict allows. Fail-closed otherwise — a run with no gathered evidence, a
# blocked decision, or an unreachable CLI closes nothing.
#
# The factory CLI is the sole authority for the verdict (it calls decide_promotion, pure).
# promote.sh is the sole writer of "closed". The two are separated so the harness script
# stays generic glue (it invokes the CLI as a subprocess, like git or tmux — it never
# imports the factory package). The operator installs the factory (console script `factory`
# on PATH); FACTORY_CLI overrides the binary so tests can point at a venv or module form.
#
# HONEST SCOPE (2026-08-14): endgame.sh now invokes this script after every preceding gate,
# live proof, and hygiene check is green, so Gate L is the live harness close path and a missing
# promotion_inputs.json fails that close. The evidence-production pipeline still does not gather
# promotion_inputs.json automatically, and this harness status update is not a RunStore PROMOTED
# ledger transition. Those are separate remaining controls; neither is implied by this wiring.
#
#   usage: promote.sh <run>
set -uo pipefail

RUN="${1:?usage: promote.sh <run>}"
H="${HARNESS_DIR:-.factory}"; ROOT="$H/runs/$RUN"
[ -f "$ROOT/run.json" ] || { echo "promote: no run.json at $ROOT — run from the target repo root" >&2; exit 64; }

FACTORY_CLI="${FACTORY_CLI:-factory}"
VERDICT_FILE="$ROOT/promotion_verdict.json"
VERDICT_STDOUT="$ROOT/promotion_verdict.json.stdout"
REJECTION="$ROOT/promotion_rejection.txt"
CLOSE_AUDIT="$ROOT/close.json"

# --- render the verdict: the factory CLI calls decide_promotion (pure, fail-closed) -----
# Exit 2 from the CLI means a refused control (missing/unreadable promotion_inputs.json, or
# a malformed one) — the run has not gathered its evidence and no verdict is rendered.
#
# FRESHNESS (Opus F2): a stale or hand-written promotion_verdict.json must NOT satisfy the
# close. We remove both the verdict file and the captured stdout BEFORE the CLI call, so the
# only way a verdict file can exist after this point is that THIS invocation's CLI wrote it.
# A no-op FACTORY_CLI (e.g. `true`) writes nothing and the close fail-closes below.
rm -f "$VERDICT_FILE" "$VERDICT_STDOUT"
if ! $FACTORY_CLI promote --runs "$H/runs" --run-id "$RUN" >"$VERDICT_STDOUT" 2>"$REJECTION"; then
  echo "promote: refused — no verdict rendered (decide_promotion could not ground a decision)" >&2
  [ -s "$REJECTION" ] && sed 's/^/  /' "$REJECTION" >&2
  exit 2
fi
# The CLI writes promotion_verdict.json (the audited record) and emits the same decision to
# stdout. A missing verdict file means the CLI exited 0 without rendering one — fail-closed.
[ -f "$VERDICT_FILE" ] || { echo "promote: factory CLI exited 0 but wrote no $VERDICT_FILE" >&2; exit 2; }
# BINDING (Opus F2): the verdict file must be THIS invocation's output, not a forgery. The
# CLI writes the file and prints the identical decision to stdout; a byte-for-byte match
# proves the file was produced by the CLI call we just made. A stale/forged file that
# somehow survived the rm -f above (or a CLI that writes one thing and prints another) is
# caught here and fail-closes.
if ! diff -q "$VERDICT_FILE" "$VERDICT_STDOUT" >/dev/null 2>&1; then
  echo "promote: verdict file does not match CLI stdout — refusing a stale/forged verdict" >&2
  exit 2
fi

# --- the verdict is the sole authority to close -----------------------------------------
# `allowed` must be exactly true (JSON bool). A blocked decision (allowed=false) is a
# finding, not a failure of this script: the cage did its job by refusing to advance a run
# the evidence does not support. A verdict that is unreadable or missing the field is
# fail-closed — we never infer consent from a malformed verdict.
ALLOWED=$(python3 - "$VERDICT_FILE" <<'PY'
import json, sys
try:
    v = json.load(open(sys.argv[1]))
except Exception as exc:
    print(f"unreadable:{exc}"); sys.exit(3)
if v.get("allowed") is True:
    print("true"); sys.exit(0)
print("blocked"); sys.exit(0)
PY
)
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "promote: verdict unreadable — $ALLOWED" >&2; exit 2
fi
if [ "$ALLOWED" != "true" ]; then
  echo "promote: decision BLOCKED — run not allowed to close (verdict in $VERDICT_FILE)" >&2
  echo "  disposition: $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("disposition","?"))' "$VERDICT_FILE" 2>/dev/null || echo '?')" >&2
  # Exit 1 is BLOCKED (the cage refused to advance). Write-failure uses a distinct code (70)
  # so a blocked decision is never confused with a failure to persist the close.
  exit 1
fi

# --- SOLE WRITER: flip run.json status open -> closed (atomic) -------------------------
# No other harness script writes "closed". The dispatcher reads it to stop; factory.sh
# writes "open". This is the one advancement path, reached only through decide_promotion.
#
# This run.json is the manual harness control record written by factory.sh; it is not the
# authoritative RunStore projection used by factory_runtime. promote.sh edits only its harness
# "status" key, which the dispatcher reads. The close audit metadata lives in a separate
# close.json. A RunStore PROMOTED transition, including its signed approval/CI evidence, remains
# a separate unwired runtime control and must never be inferred from this harness close.
#
# Atomic write (Opus F5): tmpfile + os.replace so the dispatcher's poll never reads a
# half-written run.json. Exit 70 on write-failure (Opus F7) so it is distinct from BLOCKED (1).
if ! python3 - "$ROOT/run.json" "$RUN" "$VERDICT_FILE" "$CLOSE_AUDIT" <<'PY' 2>>"$REJECTION"
import json, os, sys, datetime, pathlib, tempfile
run_path = pathlib.Path(sys.argv[1])
run = sys.argv[2]  # the run id — a string, not a path
verdict_file = pathlib.Path(sys.argv[3])
close_audit = pathlib.Path(sys.argv[4])
doc = json.loads(run_path.read_text())
if doc.get("status") == "closed":
    print(f"promote: {run} already closed — nothing to do (idempotent)")
    sys.exit(0)
doc["status"] = "closed"
# Atomic replace: write a temp file in the same dir, fsync, then os.replace (rename is atomic
# on POSIX). A crash mid-write leaves the old "open" run.json intact rather than a truncated one.
tmp = tempfile.NamedTemporaryFile(
    mode="w", dir=str(run_path.parent), suffix=".tmp", delete=False)
try:
    tmp.write(json.dumps(doc, indent=2) + "\n")
    tmp.flush()
    os.fsync(tmp.fileno())
    tmp.close()
    os.replace(tmp.name, run_path)
except OSError:
    os.unlink(tmp.name) if os.path.exists(tmp.name) else None
    raise  # surfaces as a non-zero exit; the `if !` below maps it to exit 70
# Close audit: a separate record rebuild-projection cannot erase. Carries the verdict file
# name and a timestamp so the postmortem can re-locate the decision that closed this run.
audit = {
    "run": run,
    "closed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "promotion_verdict": verdict_file.name,
}
close_audit.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
print(f"promote: {run} closed — sole-advancement via decide_promotion verdict")
PY
then
  echo "promote: run.json close write failed — run NOT closed" >&2
  [ -s "$REJECTION" ] && sed 's/^/  /' "$REJECTION" >&2
  exit 70
fi
