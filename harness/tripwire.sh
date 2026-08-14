#!/usr/bin/env bash
# tripwire.sh — a credential in a transcript is an incident, not a backlog entry (§1).
# On a hit: writes HALT (which lane_env refuses to run past), prints the single-item
# report, exits 2. A human deletes HALT after rotation; nothing else clears it.
set -uo pipefail
H="${HARNESS_DIR:-.factory}"; mkdir -p "$H"
PAT='-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}'
PAT+='|sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,}'
PAT+='|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35}'
PAT+='|"private_key_id"|"type": *"service_account"'
# -e is load-bearing: $PAT begins with dashes ("-----BEGIN"), and without -e grep
# parses it as options and errors out silently — a tripwire that always says clean.
# The forced-negative drill in tests/ exists to keep this failure impossible.
hits=$(grep -rInE -e "$PAT" "$@" 2>/dev/null | head -50 || true)
if [ -n "$hits" ]; then
  { echo "INCIDENT $(date -u +%FT%TZ): credential-shaped content in scanned paths"
    echo "$hits"; } > "$H/HALT"
  echo "================ STOP ================"
  echo "Credential exposure. This is the only item:"
  echo "$hits" | cut -c1-160
  echo "All lanes halted until a human rotates and clears $H/HALT"
  echo "======================================"
  exit 2
fi
echo "clean"
