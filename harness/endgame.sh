#!/usr/bin/env bash
# Retain checks for the exact candidate; the Validator owns the final task assessment.
set -euo pipefail
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$D/factory_python.sh"
exec env PYTHONPATH="$D/.." "$FACTORY_PYTHON" "$D/validate_candidate.py" "$@"
