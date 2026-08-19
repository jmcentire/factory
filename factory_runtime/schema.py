"""Strict loading and validation for runtime artifact schemas."""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from importlib.resources import files
from typing import Any

import jsonschema
from referencing import Registry, Resource

SCHEMA_NAMES = frozenset(
    {
        "acceptance-obligation-catalog",
        "acceptance-obligation-observations",
        "acceptance-obligation-report",
        "authorization-request",
        "authority-receipt",
        "broker-capability",
        "broker-effect",
        "broker-registry",
        "broker-request",
        "evidence-bundle",
        "build-plan",
        "execution-request",
        "effective-directive-contract",
        "genesis",
        "lane-dispatch",
        "orchestrator-projection",
        "pattern-catalog",
        "phase-artifact",
        "repair-brief",
        "resource-record",
        "resume-checkpoint",
        "role-contract",
        "runner-manifest",
        "runner-failure-receipt",
        "runner-invocation-diagnostic",
        "runner-output",
        "runner-projection",
        "runner-receipt",
        "runner-receipt-v2",
        "state-dependency-capsule",
        "state-admission-refusal",
        "state-admission-refusal-v1",
        "state-qualification-observations",
        "state-qualification-report",
        "directive-readback",
        "target-resolution-request",
        "target-state",
        "test-change-authorization",
        "transition-obligation-report",
        "transition-obligation-set",
        "validator-adversarial-review",
        "validator-review-subject",
    }
)


class DocumentValidationError(ValueError):
    """A runtime artifact does not satisfy its closed JSON Schema."""


@cache
def load_schema(name: str) -> Mapping[str, Any]:
    if name not in SCHEMA_NAMES:
        raise DocumentValidationError(f"unknown runtime schema: {name}")
    resource = files("factory_runtime.schemas").joinpath(f"{name}.schema.json")
    raw = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise DocumentValidationError(f"schema {name} is not a JSON object")
    return raw


@cache
def _schema_registry() -> Registry[Any]:
    resources = []
    for name in SCHEMA_NAMES:
        schema = load_schema(name)
        schema_id = schema.get("$id")
        if not isinstance(schema_id, str) or not schema_id:
            raise DocumentValidationError(f"schema {name} has no canonical $id")
        resources.append((schema_id, Resource.from_contents(schema)))
    return Registry().with_resources(resources)


def validate_document(name: str, document: Mapping[str, Any]) -> None:
    """Validate a document and report every structural defect in stable order."""

    selected_name = name
    if name == "runner-receipt" and document.get("schema_version") == "factory-runner-receipt/2":
        selected_name = "runner-receipt-v2"
    schema = load_schema(selected_name)
    validator = jsonschema.Draft202012Validator(schema, registry=_schema_registry())
    errors = sorted(
        validator.iter_errors(document),
        key=lambda error: (tuple(str(item) for item in error.absolute_path), error.message),
    )
    if not errors:
        return
    details: list[str] = []
    for error in errors:
        location = "/".join(str(item) for item in error.absolute_path) or "<root>"
        details.append(f"{location}: {error.message}")
    raise DocumentValidationError(f"{name} validation failed:\n  " + "\n  ".join(details))
