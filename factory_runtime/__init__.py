"""Executable orchestration around :mod:`factory_core`.

``factory_core`` remains the pure, target-independent policy and verification substrate.
This package owns the impure work the core deliberately does not: persisted run state,
process isolation, external signature verification, and command-line orchestration.

The runtime is still generic. Targets enter as signed data and concrete adapters registered
against the core's five seams; no target package is imported here.
"""

from factory_runtime.state import (
    RunProjection,
    RunState,
    RunStateError,
    RunStore,
)

__all__ = [
    "RunProjection",
    "RunState",
    "RunStateError",
    "RunStore",
]
