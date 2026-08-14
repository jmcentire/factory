#!/usr/bin/env bash
# sched_audit.sh — the runner owns cadence; an unregistered timer is hostile (§2, §6.2).
# Registry: .factory/schedule.registry, one approved regex per line. Everything the
# OS reports that matches nothing in the registry fails the audit.
# SCHED_AUDIT_INPUT=<file> substitutes a fixture for the OS scan (test seam only —
# the forced-negative drill in tests/ needs a deterministic timer list).
set -uo pipefail
REG="${HARNESS_DIR:-.factory}/schedule.registry"
tmp=$(mktemp)
if [ -n "${SCHED_AUDIT_INPUT:-}" ]; then
  sed '/^\s*$/d' "$SCHED_AUDIT_INPUT" > "$tmp"
else
  { crontab -l 2>/dev/null | grep -vE '^\s*(#|$)' || true
    command -v atq >/dev/null 2>&1 && atq 2>/dev/null || true
    command -v launchctl >/dev/null 2>&1 && \
      launchctl list 2>/dev/null | awk 'NR>1{print $3}' | grep -vE '^com\.apple\.' || true
    command -v systemctl >/dev/null 2>&1 && \
      systemctl --user list-timers --no-legend 2>/dev/null | awk '{print $NF}' || true
  } | sed '/^\s*$/d' > "$tmp"
fi
# The registry is human-authored, so it carries comments and blank lines; grep -Ef
# treats every line as a pattern, and a bracketed comment is an invalid character
# range that makes grep fail the WHOLE file — silently unregistering everything.
# Strip comments/blanks into a patterns-only temp file before matching.
pat=$(mktemp)
[ -f "$REG" ] && sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$REG" > "$pat"
bad=0
while IFS= read -r line; do
  if [ -s "$pat" ] && grep -qEf "$pat" <<<"$line"; then continue; fi
  echo "UNREGISTERED: $line"; bad=1
done < "$tmp"
rm -f "$tmp" "$pat"
if [ $bad -ne 0 ]; then
  echo "agents do not own timers — register these in $REG or kill them"; exit 3
fi
echo "cadence clean"
