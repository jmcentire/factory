"""Safe, host-checked lifecycle evidence for topology-dependent acceptance.

The Validator may retain detailed diagnostics privately, but Factory needs a small
public fact before deciding whether a failed attempt is a Coder repair. This
module accepts only digests and a closed phase vocabulary; it never parses test
output or candidate logs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory_core.manifest import digest_obj
from factory_runtime.schema import DocumentValidationError, validate_document

_BEHAVIOR_PHASES = frozenset({"behavior-started", "behavior-complete"})


@dataclass(frozen=True)
class AcceptanceLifecycle:
    """The first host-safe phase reached by one Validator acceptance execution."""

    required: bool
    phase: str | None
    digest: str = ""

    @property
    def behavior_started(self) -> bool:
        return self.phase in _BEHAVIOR_PHASES

    @property
    def behavior_complete(self) -> bool:
        return self.phase == "behavior-complete"

    @property
    def setup_failure(self) -> bool:
        return self.required and not self.behavior_started

    @property
    def artifact_digests(self) -> dict[str, str]:
        return {"acceptance-lifecycle": self.digest} if self.digest else {}


def load_acceptance_lifecycle(
    path: str | Path,
    *,
    required: bool,
    candidate_digest: str,
    acceptance_tests_digest: str,
    command_digest: str,
    configuration_digest: str,
    environment_digest: str,
) -> AcceptanceLifecycle | None:
    """Load a receipt without treating Validator-controlled text as authority.

    A missing or malformed required receipt is an unrecorded setup phase. This
    fails closed while preserving the distinction from candidate behavior.
    """

    if not required:
        return None
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        return AcceptanceLifecycle(required=True, phase=None)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            return AcceptanceLifecycle(required=True, phase=None)
        validate_document("acceptance-lifecycle", document)
        expected: dict[str, Any] = {
            "candidate_digest": candidate_digest,
            "acceptance_tests_digest": acceptance_tests_digest,
            "command_digest": command_digest,
            "configuration_digest": configuration_digest,
            "environment_digest": environment_digest,
        }
        if any(document.get(field) != value for field, value in expected.items()):
            return AcceptanceLifecycle(required=True, phase=None)
        return AcceptanceLifecycle(
            required=True,
            phase=str(document["reached_phase"]),
            digest=digest_obj(document),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, DocumentValidationError):
        return AcceptanceLifecycle(required=True, phase=None)
