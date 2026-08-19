"""Closed termination vocabulary shared by supervision, receipts, and classification."""

from __future__ import annotations

COMPLETED = "completed"
PROCESS_LIMIT = "process-limit"
WALL_LIMIT = "wall-limit"
IDLE_LIMIT = "idle-limit"
OUTPUT_LIMIT = "output-limit"
PROCESS_ESCAPE = "process-escape"
EXIT_NONZERO = "exit-nonzero"

RUNNER_TERMINATION_REASONS = frozenset(
    {
        COMPLETED,
        PROCESS_LIMIT,
        WALL_LIMIT,
        IDLE_LIMIT,
        OUTPUT_LIMIT,
        PROCESS_ESCAPE,
        EXIT_NONZERO,
    }
)
