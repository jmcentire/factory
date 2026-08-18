"""Bounded, path-free projection for an invoked advisory orchestrator."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from factory_core.manifest import digest_bytes, digest_obj
from factory_runtime.schema import DocumentValidationError, validate_document
from factory_runtime.state_admission import StateAdmissionError, verify_state_capsule

_MAX_TOTAL_BYTES = 393_216


class OrchestratorProjectionError(ValueError):
    """The orchestrator projection was incomplete, oversized, or misbound."""


def build_orchestrator_projection(
    sections: Mapping[str, bytes],
    *,
    state_capsule: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        verify_state_capsule(
            state_capsule,
            expected_purpose="orchestrator-wake",
            expected_role="orchestrator",
            expected_dependencies=sections,
        )
    except StateAdmissionError as exc:
        raise OrchestratorProjectionError(str(exc)) from exc
    capsule_entries = {
        str(entry["dependency_id"]): entry for entry in state_capsule["dependencies"]
    }
    total = sum(len(raw) for raw in sections.values())
    if total > _MAX_TOTAL_BYTES:
        raise OrchestratorProjectionError(
            f"orchestrator projection exceeds {_MAX_TOTAL_BYTES} bytes"
        )
    entries: list[dict[str, Any]] = []
    for section_id in sorted(sections):
        raw = sections[section_id]
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OrchestratorProjectionError(
                f"orchestrator section {section_id!r} is not UTF-8"
            ) from exc
        capsule_entry = capsule_entries[section_id]
        if capsule_entry["content_digest"] != digest_bytes(raw):
            raise OrchestratorProjectionError(
                f"orchestrator section {section_id!r} differs from its capsule"
            )
        entries.append(
            {
                "section_id": section_id,
                "trust_class": capsule_entry["trust_class"],
                "content_digest": capsule_entry["content_digest"],
                "byte_count": len(raw),
                "content": content,
            }
        )
    document = {
        "schema_version": "factory-orchestrator-projection/1",
        "run_id": state_capsule["run_id"],
        "generation": state_capsule["generation"],
        "target_state_digest": state_capsule["target_state_digest"],
        "run_ledger_head": state_capsule["run_ledger_head"],
        "resume_checkpoint_digest": state_capsule["resume_checkpoint_digest"],
        "state_profile_digest": state_capsule["profile_digest"],
        "state_capsule_digest": digest_obj(dict(state_capsule)),
        "sections": entries,
    }
    try:
        validate_document("orchestrator-projection", document)
    except DocumentValidationError as exc:
        raise OrchestratorProjectionError(str(exc)) from exc
    return document


__all__ = ["OrchestratorProjectionError", "build_orchestrator_projection"]
