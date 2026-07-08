"""Capability-delta schema tests.

The capability-delta JSON Schema is the neutral, target-agnostic IR a spec declares for a
change so the analyzer can compose it (with the shipped baseline + in-flight deltas) against
the signed invariant kernel. These tests assert the schema is a well-formed draft-2020-12
schema, that it accepts a minimal and a rich neutral instance, that it rejects malformed
instances, and — the purity-relevant part — that the schema itself names nothing
target-specific.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "factory_core" / "schemas" / "capability_delta.schema.json"

# The same denylist the purity guard applies to core .py files, read from the SAME data file
# (core_purity_denylist.json) rather than hardcoded here. The schema is data, not code, so the
# guard does not scan it — this test extends the guarantee to the schema file so the neutral IR
# cannot silently acquire a configured target token. On the generic, public core the token set
# is empty, so this check is trivially green; a consuming target that configures tokens gets the
# guarantee extended over the schema for free.
DENYLIST_TOKENS = tuple(
    json.loads((REPO_ROOT / "core_purity_denylist.json").read_text(encoding="utf-8"))
    .get("tokens", [])
)


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_schema_is_a_valid_draft_2020_12_schema(schema: dict) -> None:
    # check_schema raises SchemaError if the schema document is itself invalid.
    jsonschema.Draft202012Validator.check_schema(schema)


def test_minimal_instance_validates(schema: dict) -> None:
    instance = {"schema_version": "factory-capability-delta/1", "id": "feature-x"}
    jsonschema.validate(instance=instance, schema=schema)


def test_rich_neutral_instance_validates(schema: dict) -> None:
    instance = {
        "schema_version": "factory-capability-delta/1",
        "id": "feature-export-link",
        "summary": "Adds an export path that links two records.",
        "stage": "candidate",
        "nodes": [
            {"id": "sensitive_store", "roles": ["sensitive_store"]},
            {"id": "external_sink", "roles": ["uncontrolled_egress"]},
        ],
        "flows": [
            {
                "source": "sensitive_store",
                "target": "external_sink",
                "relation": "export",
                "data_class": "sensitive",
                "rationale": "operator-initiated export",
            }
        ],
        "invariants_touched": [
            {"id": "NO-UNCONTROLLED-EGRESS", "degree": "D0"}
        ],
        "implementation_surfaces": [
            {"path_or_component": "components/export", "expected_relation": "adds export edge"}
        ],
        "signature": {
            "content_digest": "sha256:deadbeef",
            "signer": "ci",
            "algorithm": "ed25519",
            "value": "...",
        },
    }
    jsonschema.validate(instance=instance, schema=schema)


def test_missing_required_id_is_rejected(schema: dict) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance={"schema_version": "factory-capability-delta/1"}, schema=schema
        )


def test_unknown_top_level_field_is_rejected(schema: dict) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance={
                "schema_version": "factory-capability-delta/1",
                "id": "feature-x",
                "not_a_real_field": True,
            },
            schema=schema,
        )


def test_bad_invariant_degree_is_rejected(schema: dict) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance={
                "schema_version": "factory-capability-delta/1",
                "id": "feature-x",
                "invariants_touched": [{"id": "SOME-LAW", "degree": "D9"}],
            },
            schema=schema,
        )


def test_bad_stage_is_rejected(schema: dict) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance={
                "schema_version": "factory-capability-delta/1",
                "id": "feature-x",
                "stage": "not-a-stage",
            },
            schema=schema,
        )


def test_schema_names_nothing_target_specific() -> None:
    text = SCHEMA_PATH.read_text(encoding="utf-8").lower()
    runs = set()
    token = ""
    for ch in text:
        if ch.isalnum():
            token += ch
        else:
            if token:
                runs.add(token)
            token = ""
    if token:
        runs.add(token)
    hits = [tok for tok in DENYLIST_TOKENS if tok in runs]
    assert hits == [], f"capability-delta schema must name nothing target-specific; found {hits}"
