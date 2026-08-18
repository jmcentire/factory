"""Deterministic qualification of the state-admission boundary.

This evaluator intentionally never compares model prose.  It consumes evidence-addressed
observations and asks only whether the runtime admitted or refused each named state family at
the required boundary.  Product acceptance remains a separate, mandatory gate.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from factory_core.manifest import digest_bytes, digest_obj
from factory_runtime.schema import DocumentValidationError, validate_document
from factory_runtime.state_admission import (
    StateAdmissionError,
    dependency_rule,
    derive_state_capsule,
    profile_digest,
    profile_document,
    verify_state_capsule,
)

SCENARIO_EXPECTATIONS: Mapping[str, str] = {
    "cold": "admitted",
    "exact-resume": "admitted",
    "compaction-boundary": "admitted",
    "stale": "refused-before-model",
    "structural-contradiction": "refused-before-model",
    "poisoned": "refused-before-model",
    "missing": "refused-before-model",
    "oversized-input": "refused-before-model",
}
SCENARIO_REFUSAL_CODES: Mapping[str, str] = {
    "cold": "",
    "exact-resume": "",
    "compaction-boundary": "",
    "stale": "PROFILE_MISMATCH",
    "structural-contradiction": "SCOPE_MISMATCH",
    "poisoned": "TRUST_PROFILE_CONTRADICTION",
    "missing": "MISSING_DEPENDENCY",
    "oversized-input": "OVERSIZED_DEPENDENCY",
}
_EXECUTOR_VERSION = "factory-state-admission-probe/1"


class StateQualificationError(ValueError):
    """Qualification observations or report are structurally untrustworthy."""


def scenario_set_digest() -> str:
    return digest_obj(
        {
            "dispositions": dict(sorted(SCENARIO_EXPECTATIONS.items())),
            "refusal_codes": dict(sorted(SCENARIO_REFUSAL_CODES.items())),
        }
    )


def qualification_executor_digest() -> str:
    """Content-address the code-owned probe contract independently of generated evidence."""

    return digest_obj(
        {
            "executor_version": _EXECUTOR_VERSION,
            "profile_digest": profile_digest("lane-dispatch"),
            "scenario_expectations": dict(sorted(SCENARIO_EXPECTATIONS.items())),
            "scenario_refusal_codes": dict(sorted(SCENARIO_REFUSAL_CODES.items())),
        }
    )


def _probe_dependencies() -> dict[str, bytes]:
    """Return one small complete lane state set for deterministic admission probes."""

    return {
        dependency_id: f"probe:{dependency_id}".encode()
        for dependency_id in (
            str(item["dependency_id"])
            for item in profile_document("lane-dispatch")["dependencies"]
        )
    }


def execute_state_qualification_observations(
    runner_configuration_digest: str,
) -> dict[str, Any]:
    """Execute the code-owned cold/warm/negative matrix without invoking a provider.

    This qualifier never invokes a model or broker. ``downstream_probe_reached`` records whether
    execution crossed the state-admission boundary; ``model_attempts`` and ``broker_effects``
    therefore remain zero for every scenario.
    """

    executor_digest = qualification_executor_digest()
    target_digest = digest_obj({"target": "state-qualification"})
    ledger_head = "sha256:" + "1" * 64
    resume_digest = digest_obj({"resume": "state-qualification"})
    admitted_outcome = digest_obj(
        {
            "boundary": "state-admission",
            "downstream_probe_reached": True,
            "executor_digest": executor_digest,
        }
    )
    records: list[dict[str, Any]] = []
    for scenario in SCENARIO_EXPECTATIONS:
        dependencies = _probe_dependencies()
        capsule: dict[str, Any] | None = None
        disposition = "admitted"
        downstream_probe_reached = False
        refusal_code = ""
        try:
            if scenario == "compaction-boundary":
                dependencies["role-primer"] = b"probe:role-primer:compacted"
            elif scenario == "missing":
                del dependencies["role-primer"]
            elif scenario == "oversized-input":
                maximum = dependency_rule("lane-dispatch", "role-primer").max_bytes
                dependencies["role-primer"] = b"x" * (maximum + 1)
            capsule = derive_state_capsule(
                purpose="lane-dispatch",
                run_id="state-qualification",
                generation=1,
                role="coder",
                target_state_digest=target_digest,
                run_ledger_head=ledger_head,
                resume_checkpoint_digest=resume_digest,
                dependencies=dependencies,
            )
            if scenario == "stale":
                capsule["profile_digest"] = "sha256:" + "0" * 64
            elif scenario == "structural-contradiction":
                capsule["run_id"] = "contradictory-run"
            elif scenario == "poisoned":
                primer = next(
                    item
                    for item in capsule["dependencies"]
                    if item["dependency_id"] == "role-primer"
                )
                primer["trust_class"] = "verified-state"
                capsule["dependency_set_digest"] = digest_obj(capsule["dependencies"])
            verify_state_capsule(
                capsule,
                expected_purpose="lane-dispatch",
                expected_run_id="state-qualification",
                expected_generation=1,
                expected_role="coder",
                expected_target_state_digest=target_digest,
                expected_run_ledger_head=ledger_head,
                expected_resume_checkpoint_digest=resume_digest,
                expected_dependencies=dependencies,
            )
            downstream_probe_reached = True
        except StateAdmissionError as exc:
            disposition = "refused-before-model"
            refusal_code = exc.code

        subject_digest = (
            digest_obj(capsule)
            if capsule is not None
            else digest_obj(
                {
                    dependency_id: digest_bytes(content)
                    for dependency_id, content in sorted(dependencies.items())
                }
            )
        )
        structural_outcome_digest = (
            admitted_outcome
            if disposition == "admitted"
            else digest_obj(
                {
                    "boundary": "state-admission",
                    "disposition": disposition,
                    "scenario": scenario,
                    "executor_digest": executor_digest,
                }
            )
        )
        evidence = {
            "schema_version": "factory-state-qualification-probe-receipt/1",
            "scenario": scenario,
            "disposition": disposition,
            "refusal_code": refusal_code,
            "subject_digest": subject_digest,
            "structural_outcome_digest": structural_outcome_digest,
            "downstream_probe_reached": downstream_probe_reached,
            "model_attempts": 0,
            "broker_effects": 0,
            "executor_digest": executor_digest,
            "runner_configuration_digest": runner_configuration_digest,
        }
        records.append(
            {
                "scenario": scenario,
                "disposition": disposition,
                "refusal_code": refusal_code,
                "subject_digest": subject_digest,
                "structural_outcome_digest": structural_outcome_digest,
                "evidence_digest": digest_obj(evidence),
                "downstream_probe_reached": downstream_probe_reached,
                "model_attempts": 0,
                "broker_effects": 0,
            }
        )
    observations = {
        "schema_version": "factory-state-qualification-observations/1",
        "profile_digest": profile_digest("lane-dispatch"),
        "runner_configuration_digest": runner_configuration_digest,
        "scenario_set_digest": scenario_set_digest(),
        "executor_digest": executor_digest,
        "observations": records,
    }
    try:
        validate_document("state-qualification-observations", observations)
    except DocumentValidationError as exc:
        raise StateQualificationError(str(exc)) from exc
    return observations


def qualify_state_observations(
    observations: Mapping[str, Any],
    *,
    qualification_id: str,
    clock: Callable[[], int] | None = None,
) -> dict[str, Any]:
    try:
        validate_document("state-qualification-observations", observations)
    except DocumentValidationError as exc:
        raise StateQualificationError(str(exc)) from exc
    if observations["profile_digest"] != profile_digest("lane-dispatch"):
        raise StateQualificationError("qualification observations use a stale state profile")
    if observations["scenario_set_digest"] != scenario_set_digest():
        raise StateQualificationError("qualification observations use a stale scenario set")
    if observations["executor_digest"] != qualification_executor_digest():
        raise StateQualificationError("qualification observations use a stale executor")
    records = list(observations["observations"])
    by_scenario = {str(record["scenario"]): record for record in records}
    if len(by_scenario) != len(records):
        raise StateQualificationError("qualification observations repeat a scenario")
    if set(by_scenario) != set(SCENARIO_EXPECTATIONS):
        raise StateQualificationError("qualification observations are not the exact scenario set")

    failures: list[str] = []
    positive_outcomes: set[str] = set()
    for scenario, expected in SCENARIO_EXPECTATIONS.items():
        record = by_scenario[scenario]
        observed = str(record["disposition"])
        if observed != expected:
            failures.append(f"{scenario}: expected {expected}, observed {observed}")
        expected_refusal = SCENARIO_REFUSAL_CODES[scenario]
        if record["refusal_code"] != expected_refusal:
            failures.append(
                f"{scenario}: expected refusal {expected_refusal or '<none>'}, "
                f"observed {record['refusal_code'] or '<none>'}"
            )
        if record["model_attempts"] != 0:
            failures.append(f"{scenario}: structural qualifier invoked a model")
        if record["broker_effects"] != 0:
            failures.append(f"{scenario}: structural qualifier produced a broker effect")
        if expected == "refused-before-model":
            if record["downstream_probe_reached"]:
                failures.append(f"{scenario}: refusal crossed the admission boundary")
        else:
            if not record["downstream_probe_reached"]:
                failures.append(f"{scenario}: admitted input did not reach the downstream probe")
            else:
                positive_outcomes.add(str(record["structural_outcome_digest"]))
    if len(positive_outcomes) != 1:
        failures.append(
            "cold, exact-resume, and compaction-boundary structural outcomes differ"
        )

    report = {
        "schema_version": "factory-state-qualification-report/1",
        "qualification_id": qualification_id,
        "profile_digest": observations["profile_digest"],
        "runner_configuration_digest": observations["runner_configuration_digest"],
        "scenario_set_digest": observations["scenario_set_digest"],
        "observations_digest": digest_obj(dict(observations)),
        "structural_outcome_digest": (
            next(iter(positive_outcomes))
            if not failures and len(positive_outcomes) == 1
            else ""
        ),
        "qualified": not failures,
        "failures": failures,
        "semantic_scope": "state-admission-only",
        "created_at": (clock or (lambda: int(time.time())))(),
    }
    try:
        validate_document("state-qualification-report", report)
    except DocumentValidationError as exc:
        raise StateQualificationError(str(exc)) from exc
    return report


def verify_state_qualification_report(
    report: Mapping[str, Any],
    *,
    observations: Mapping[str, Any],
    expected_profile_digest: str,
    expected_runner_configuration_digest: str,
) -> None:
    try:
        validate_document("state-qualification-report", report)
    except DocumentValidationError as exc:
        raise StateQualificationError(str(exc)) from exc
    expected = {
        "profile_digest": expected_profile_digest,
        "runner_configuration_digest": expected_runner_configuration_digest,
        "scenario_set_digest": scenario_set_digest(),
        "semantic_scope": "state-admission-only",
        "qualified": True,
    }
    for field, value in expected.items():
        if report[field] != value:
            raise StateQualificationError(f"state qualification report has wrong {field}")
    if report["failures"]:
        raise StateQualificationError("qualified state report retains failures")
    if report["observations_digest"] != digest_obj(dict(observations)):
        raise StateQualificationError(
            "state qualification report differs from its exact observations"
        )
    executed_observations = execute_state_qualification_observations(
        expected_runner_configuration_digest
    )
    if dict(observations) != executed_observations:
        raise StateQualificationError(
            "state qualification observations do not re-derive from the code-owned executor"
        )
    # A report is a deterministic materialized view, never an independently trusted
    # attestation. Re-derive every field from the externally bound observation bytes so a
    # caller cannot manufacture a schema-valid qualified result with opaque digests.
    expected_report = qualify_state_observations(
        observations,
        qualification_id=str(report["qualification_id"]),
        clock=lambda: int(report["created_at"]),
    )
    if dict(report) != expected_report:
        raise StateQualificationError(
            "state qualification report does not re-derive from its observations"
        )


__all__ = [
    "SCENARIO_EXPECTATIONS",
    "SCENARIO_REFUSAL_CODES",
    "StateQualificationError",
    "execute_state_qualification_observations",
    "qualify_state_observations",
    "qualification_executor_digest",
    "scenario_set_digest",
    "verify_state_qualification_report",
]
