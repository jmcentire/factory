from __future__ import annotations

from typing import Any

import jsonschema
import pytest

from factory_core.manifest import digest_obj
from factory_runtime.schema import (
    SCHEMA_NAMES,
    DocumentValidationError,
    load_schema,
    validate_document,
)

DIGEST = "sha256:" + ("a" * 64)


def _phase(phase: str, artifact_id: str) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "phase": phase,
        "version": "1",
        "source_digest": DIGEST,
        "human_ratifier": "human:founder",
        "validator_ratifier": "agent:validator",
        "items": [
            {
                "item_id": f"{artifact_id}:1",
                "canonical_statement": f"{phase} is ratified",
                "supersedes": [],
            }
        ],
    }


def test_every_runtime_schema_is_a_valid_draft_2020_12_schema() -> None:
    for name in SCHEMA_NAMES:
        jsonschema.Draft202012Validator.check_schema(load_schema(name))


def test_phase_artifact_schema_is_closed() -> None:
    artifact = _phase("product-specification", "product")
    validate_document("phase-artifact", artifact)
    artifact["invented_authority"] = "ticket-123"

    with pytest.raises(DocumentValidationError, match="invented_authority"):
        validate_document("phase-artifact", artifact)


def test_evidence_bundle_resolves_the_phase_artifact_schema() -> None:
    phases = [
        _phase("product-specification", "product"),
        _phase("architecture", "architecture"),
        _phase("operational-maturity", "operations"),
    ]
    trusted = {phase["artifact_id"]: digest_obj(phase) for phase in phases}
    item = phases[0]["items"][0]
    document = {
        "schema_version": "factory-evidence-bundle/1",
        "run_id": "run-1",
        "target_digest": DIGEST,
        "source_digest": DIGEST,
        "candidate_digest": DIGEST,
        "acceptance_tests_digest": DIGEST,
        "ledger_head": DIGEST,
        "phase_artifacts": phases,
        "trusted_artifact_digests": trusted,
        "claims": [
            {
                "claim_id": "acceptance:1",
                "kind": "test-assertion",
                "backreference": {
                    "artifact_id": "product",
                    "artifact_digest": trusted["product"],
                    "item_id": item["item_id"],
                    "intent_digest": digest_obj(
                        {"canonical_statement": item["canonical_statement"]}
                    ),
                },
            }
        ],
        "checklist_results": [
            {
                "id": "tests",
                "passed": True,
                "detail": "passed",
                "recorded_at": 1,
                "evidence": {
                    "body": {"subject_digest": DIGEST},
                    "claimed_digest": DIGEST,
                },
            }
        ],
        "surface_evidence": [
            {
                "surface_id": "surface",
                "criticality": "critical",
                "oracle_adequate": True,
                "required_evidence_ids": ["tests"],
                "evidence_digests": {"tests": DIGEST},
            }
        ],
        "determinism_records": [
            {
                "surface_id": "surface",
                "criticality": "critical",
                "deterministic": True,
                "flake_count": 0,
                "automatic_retry_count": 0,
            }
        ],
    }

    validate_document("evidence-bundle", document)
