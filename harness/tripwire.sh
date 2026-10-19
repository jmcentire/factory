#!/usr/bin/env bash
# tripwire.sh — a credential in a transcript is an incident, not a backlog entry (§1).
# On a hit: appends an incident row to $H/incidents.jsonl, prints the hit locations (never the
# matched text) and exits 2, which fails grounding and so blocks the next lane launch. The run
# scrubs the hit, re-grounds and continues. Rotating the exposed credential is recorded as owed:
# only the account holder can rotate a provider key, and nothing waits on it.
set -uo pipefail
H="${HARNESS_DIR:-.factory}"; mkdir -p "$H"
PAT='-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}'
PAT+='|(^|[^A-Za-z0-9]|\\[bfnrt]|\\u[0-9A-Fa-f]{4}|%[0-9A-Fa-f]{2}|\[[0-9;:]*m)sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,}'
PAT+='|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35}'
PAT+='|"private_key_id"|"type": *"service_account"'
# -H is load-bearing too: GNU grep omits the file name when given a single file, and then the
# "file:line" cut below would carry the matched text instead of the location.
# -e is load-bearing: $PAT begins with dashes ("-----BEGIN"), and without -e grep
# parses it as options and errors out silently — a tripwire that always says clean.
# The forced-negative drill in tests/ exists to keep this failure impossible.
hits=$(grep -rInHE -e "$PAT" "$@" 2>/dev/null | head -50 || true)
if [ -n "$hits" ]; then
  # file:line only: the incident record must not become a second copy of the credential.
  locations="$(printf '%s\n' "$hits" | cut -d: -f1,2 | tr '\n' ' ' | tr -d '"\\')"
  printf '{"ts":"%s","kind":"credential_exposure","locations":"%s","owed":"scrub and re-ground; rotate the credential"}\n' \
    "$(date -u +%FT%TZ)" "$locations" >> "$H/incidents.jsonl"
  echo "================ INCIDENT ================"
  echo "Credential exposure at:"
  printf '%s\n' "$hits" | cut -d: -f1,2
  echo "Grounding fails until the run scrubs these and re-grounds; rotation is recorded as owed."
  echo "=========================================="
  exit 2
fi
echo "clean"
