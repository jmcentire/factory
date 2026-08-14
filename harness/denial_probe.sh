#!/usr/bin/env bash
# denial_probe.sh — re-run a factory gate's end-to-end denial probe on demand (Gate I runner).
#
# The registry (harness/gates.tsv) maps each factory gate to the pytest node-ids that
# demonstrate it blocks the prohibited action (the run does NOT advance). The build-time
# coverage check is scripts/check_denial_probes.py; THIS script is the human/CI entry point
# for re-running a single gate's probe or every gate's probes without the whole suite.
#
# A denial probe tests the END-TO-END blocking path, not the fix's artifact: the probe feeds
# the prohibited input and asserts the gate blocks it (exit nonzero, status not written,
# receipt rejected). A probe that asserts "the internal function returned False" would not
# catch a gate that fails to wire that function into the blocking path — the registry's
# red_now column names the mutation that turns the probe red so a probe can be audited.
#
# usage:
#   denial_probe.sh --list              # print every gate and its registered probes
#   denial_probe.sh <gate>              # run one gate's probes (e.g. denial_probe.sh M)
#   denial_probe.sh --all               # run every gate's probes (a denial-probe-only pass)
# exit: nonzero if any probe fails (a gate does not block its prohibited action).
set -uo pipefail
D="$(cd "$(dirname "$0")" && pwd)"; REG="$D/gates.tsv"
[ -f "$REG" ] || { echo "no gate registry at $REG" >&2; exit 2; }

# Read the registry into parallel arrays, skipping '#' and blank lines.
gate_ids=(); gate_names=(); gate_probes=()
while IFS= read -r line; do
  [ -z "$line" ] && continue
  case "$line" in \#*) continue;; esac
  g="${line%%$'\t'*}"; rest="${line#*$'\t'}"
  name="${rest%%$'\t'*}"; rest="${rest#*$'\t'}"
  prohibits="${rest%%$'\t'*}"; rest="${rest#*$'\t'}"
  probes="${rest%%$'\t'*}"
  gate_ids+=("$g"); gate_names+=("$name"); gate_probes+=("${probes//;/ }")
done < "$REG"

probe_for() {  # echo the node-ids for a gate id, or return 1 if unknown
  local want="$1" i
  for i in "${!gate_ids[@]}"; do
    if [ "${gate_ids[$i]}" = "$want" ]; then echo "${gate_probes[$i]}"; return 0; fi
  done
  return 1
}

if [ "$#" -eq 0 ] || [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  cat >&2 <<EOF
usage: denial_probe.sh --list | <gate> | --all
gates: ${gate_ids[*]}
EOF
  exit 64
fi

if [ "${1:-}" = "--list" ]; then
  for i in "${!gate_ids[@]}"; do
    printf '%-4s %s\n    prohibits: %s\n    probes:    %s\n' \
      "${gate_ids[$i]}" "${gate_names[$i]}" "" "${gate_probes[$i]}"
  done
  exit 0
fi

if [ "${1:-}" = "--all" ]; then
  ALL=""; for p in "${gate_probes[@]}"; do ALL="$ALL $p"; done
  [ -n "$ALL" ] || { echo "no probes registered" >&2; exit 2; }
  echo "== denial probes: all gates =="
  # shellcheck disable=SC2086
  exec python3 -m pytest -q $ALL
fi

GATE="$1"
NODES="$(probe_for "$GATE")" || { echo "unknown gate: $GATE (see denial_probe.sh --list)" >&2; exit 64; }
[ -n "$NODES" ] || { echo "gate $GATE has no registered probes" >&2; exit 1; }
echo "== denial probe: gate $GATE =="
# shellcheck disable=SC2086
exec python3 -m pytest -q $NODES