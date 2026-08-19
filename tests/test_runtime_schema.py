from __future__ import annotations

from typing import Any

import jsonschema
import pytest

from factory_core.manifest import digest_obj
from factory_runtime.runner_termination import RUNNER_TERMINATION_REASONS
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


def test_runner_diagnostic_schema_uses_the_supervisor_termination_vocabulary() -> None:
    schema = load_schema("runner-invocation-diagnostic")
    assert set(schema["properties"]["termination_reason"]["enum"]) == set(
        RUNNER_TERMINATION_REASONS
    )
    failure_schema = load_schema("runner-failure-receipt")
    assert set(failure_schema["$defs"]["termination_reason"]["enum"]) == set(
        RUNNER_TERMINATION_REASONS
    )


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
        "schema_version": "factory-evidence-bundle/2",
        "run_id": "run-1",
        "target_digest": DIGEST,
        "source_digest": DIGEST,
        "candidate_digest": DIGEST,
        "acceptance_tests_digest": DIGEST,
        "generation_artifacts": {
            "target-manifest-source": DIGEST,
            "pattern-catalog": DIGEST,
            "pattern-catalog-source": DIGEST,
            "build-plan": DIGEST,
            "build-plan-source": DIGEST,
            "build-input": DIGEST,
            "generation-readiness": DIGEST,
        },
        "review_snapshots": {
            "coder-output": DIGEST,
            "tester-output": DIGEST,
        },
        "build_attempt": {"number": 1, "limit": 1},
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
        "lane": "capability",
        "independence": {
            "agents": [
                {
                    "role": role,
                    "model_family": f"family-{role}",
                    "model_version": "2026-07",
                    "directive_version": f"{role}-3",
                }
                for role in ("coder", "tester", "validator")
            ],
            "shared_context": False,
            "channel_open": False,
            "mechanism_ids": [],
            "claimed_tier": "stronger",
            "derived_tier": "stronger",
            "structural_mode": {
                "mode": "isolated",
                "contract_backreference": None,
                "mutation_evidence": {
                    "body": {"structural_mode": "isolated"},
                    "claimed_digest": DIGEST,
                },
                "decision_package_note": "Branch depth was not purchased.",
            },
        },
        "monitors": [
            {
                "monitor_id": "monitor-surface",
                "surface_id": "surface",
                "derivation": "specification",
                "authorship": "human",
                "author_identity": "human:founder",
                "backreference": {
                    "artifact_id": "product",
                    "artifact_digest": trusted["product"],
                    "item_id": item["item_id"],
                    "intent_digest": digest_obj(
                        {"canonical_statement": item["canonical_statement"]}
                    ),
                },
                "actionable_conclusion": "Page the surface owner.",
                "notifies_human": True,
                "fix_references": [],
            }
        ],
        "monitor_declared_unit_count": 75,
    }

    validate_document("evidence-bundle", document)


def test_the_bundle_schema_refuses_a_diff_derived_monitor_and_an_unrecorded_tier() -> None:
    """The record cannot carry a change detector, or omit what makes a verdict comparable."""

    monitor = {
        "monitor_id": "monitor-surface",
        "surface_id": "surface",
        "derivation": "implementation",
        "authorship": "human",
        "author_identity": "human:founder",
        "backreference": {
            "artifact_id": "product",
            "artifact_digest": DIGEST,
            "item_id": "item",
            "intent_digest": DIGEST,
        },
        "actionable_conclusion": "Page the surface owner.",
        "notifies_human": True,
        "fix_references": [],
    }
    schema = load_schema("evidence-bundle")
    monitor_schema = schema["$defs"]["monitor"]
    independence_schema = schema["$defs"]["independence"]

    assert monitor_schema["properties"]["derivation"] == {"const": "specification"}
    assert monitor["derivation"] not in ("specification",)
    for required in ("claimed_tier", "derived_tier", "agents", "structural_mode"):
        assert required in independence_schema["required"]
    assert independence_schema["properties"]["agents"]["items"]["required"] == [
        "role",
        "model_family",
        "model_version",
        "directive_version",
    ]
