"""One handoff contract for ignition and promotion metadata.

This validates the existing accepted resident-run shapes and bindings. It grants no
access and never reads author context; callers retain their own safe file I/O.
"""

from __future__ import annotations

import re
from typing import Any

from jev_screen import check_config
from orchestrator_channel import progress_review_every


class MetadataError(ValueError):
    """The coordination handoff cannot be used for this run."""


def validate_metadata(
    doc: dict[str, Any], *, run: str, target_state: str, commit: str, checkout: str
) -> bool:
    """Reject incompatible metadata; return whether it has the current contract."""
    expected = {
        "schema_version": "factory-harness/2", "run_id": run,
        "target_state_digest": target_state, "resolved_commit": commit,
        "checkout_id": checkout,
    }
    if any(doc.get(key) != value for key, value in expected.items()):
        raise MetadataError("harness metadata is not bound to current target-state")
    base_fields = {
        "schema_version", "run_id", "status", "task_digest", "target_state_digest",
        "target_manifest_digest", "resolved_commit", "checkout_id", "budget_usd",
        "budget_enforcement", "audit_interval_min", "engagement", "question_channel",
        "promise_window_min",
        "launcher_qualification", "lane_isolation", "interactive_validator_boundary",
        "validator_agent", "orchestrator_agent", "validator_contract", "created_at",
    }
    resident_fields = {
        "orchestrator_mode", "orchestrator_window", "orchestrator_visibility",
        "orchestrator_effects", "orchestrator_boundary", "orchestrator_cli_version",
        "orchestrator_cli_contract",
    }
    contract_fields = {
        "agreement_contract_version", "agreement_requirement_region_families",
        "guidance_contract_version", "guidance_generation", "guidance_state",
        "guidance_selection_digest", "guidance_source_digests",
    }
    close_fields = {"closed_at", "promotion_verdict", "promotion_verdict_digest"}
    # Every run has a resident Orchestrator (founder ruling 2026-09-21), so a
    # harness without the resident fields is not a closable run.
    allowed_shapes = {
        frozenset(base_fields | resident_fields),
        frozenset(base_fields | resident_fields | contract_fields),
        frozenset(base_fields | resident_fields | close_fields),
        frozenset(base_fields | resident_fields | contract_fields | close_fields),
    }
    # Current ignition adds these optional monitoring configurations. Validate them
    # with the same authorities as the runtime; never discard them from the subject.
    check_config(doc.get("jev_screen"))
    progress_review_every(doc)
    if frozenset(doc) - {"jev_screen", "progress_review"} not in allowed_shapes:
        raise MetadataError("harness metadata has unknown or missing fields")
    if doc.get("status") not in {"open", "closed"}:
        raise MetadataError("harness status is invalid")
    if doc.get("orchestrator_mode") != "resident-monitoring":
        raise MetadataError("run has no resident Orchestrator; every run requires one")
    if contract_fields <= set(doc):
        digest = re.compile(r"^sha256:[0-9a-f]{64}$")
        if doc["agreement_contract_version"] != "factory-agreement-contract/1":
            raise MetadataError("harness agreement contract is unsupported")
        if doc["guidance_contract_version"] != "factory-run-guidance/1":
            raise MetadataError("harness guidance contract is unsupported")
        generation = doc["guidance_generation"]
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
            raise MetadataError("harness guidance generation is invalid")
        if doc["guidance_state"] not in {
            "none", "pending-application", "routing-verified", "evidence-complete", "noncompliant"
        }:
            raise MetadataError("harness guidance state is invalid")
        selection = doc["guidance_selection_digest"]
        sources = doc["guidance_source_digests"]
        if not isinstance(sources, dict) or any(
            not isinstance(name, str) or not name or not isinstance(address, str)
            or not digest.fullmatch(address)
            for name, address in sources.items()
        ):
            raise MetadataError("harness guidance source map is invalid")
        expected_regions = ["authored-product"]
        if selection is None:
            if doc["guidance_state"] != "none" or sources:
                raise MetadataError("unselected guidance carries selected state")
        else:
            if not isinstance(selection, str) or not digest.fullmatch(selection) or not sources:
                raise MetadataError("selected guidance identity is invalid")
            expected_regions.append("run-guidance")
        if doc["agreement_requirement_region_families"] != expected_regions:
            raise MetadataError("agreement region families differ from guidance selection")
    return contract_fields <= set(doc)
