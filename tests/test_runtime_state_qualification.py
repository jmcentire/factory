from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from factory_core.manifest import digest_obj
from factory_runtime.state_qualification import (
    SCENARIO_EXPECTATIONS,
    SCENARIO_REFUSAL_CODES,
    StateQualificationError,
    execute_state_qualification_observations,
    qualify_state_observations,
    verify_state_qualification_report,
)


def _observations() -> dict[str, Any]:
    return execute_state_qualification_observations(digest_obj({"runner": "fixture"}))


def test_exact_scenario_set_qualifies_structural_boundary_only() -> None:
    observations = _observations()

    report = qualify_state_observations(
        observations, qualification_id="qualification-1", clock=lambda: 100
    )
    assert report["qualified"] is True
    assert report["failures"] == []
    assert report["semantic_scope"] == "state-admission-only"
    verify_state_qualification_report(
        report,
        observations=observations,
        expected_profile_digest=observations["profile_digest"],
        expected_runner_configuration_digest=observations[
            "runner_configuration_digest"
        ],
    )


def test_code_owned_executor_proves_expected_boundary_dispositions() -> None:
    observations = _observations()
    by_scenario = {
        record["scenario"]: record for record in observations["observations"]
    }

    for scenario, expected in SCENARIO_EXPECTATIONS.items():
        assert by_scenario[scenario]["disposition"] == expected
        assert by_scenario[scenario]["refusal_code"] == SCENARIO_REFUSAL_CODES[scenario]
        assert by_scenario[scenario]["downstream_probe_reached"] is (
            expected == "admitted"
        )
        assert by_scenario[scenario]["model_attempts"] == 0
        assert by_scenario[scenario]["broker_effects"] == 0


def test_nonzero_model_counter_unqualifies_structural_report() -> None:
    observations = _observations()
    forged = deepcopy(observations)
    forged["observations"][0]["model_attempts"] = 9
    report = qualify_state_observations(
        forged, qualification_id="qualification-forged", clock=lambda: 100
    )

    assert report["qualified"] is False
    assert "invoked a model" in "\n".join(report["failures"])
    with pytest.raises(StateQualificationError, match="wrong qualified"):
        verify_state_qualification_report(
            report,
            observations=forged,
            expected_profile_digest=observations["profile_digest"],
            expected_runner_configuration_digest=observations[
                "runner_configuration_digest"
            ],
        )


def test_caller_authored_observation_subject_is_refused_by_executor() -> None:
    observations = _observations()
    forged = deepcopy(observations)
    forged["observations"][0]["subject_digest"] = digest_obj({"invented": "subject"})
    report = qualify_state_observations(
        forged, qualification_id="qualification-forged", clock=lambda: 100
    )

    assert report["qualified"] is True
    with pytest.raises(StateQualificationError, match="code-owned executor"):
        verify_state_qualification_report(
            report,
            observations=forged,
            expected_profile_digest=observations["profile_digest"],
            expected_runner_configuration_digest=observations[
                "runner_configuration_digest"
            ],
        )

def test_schema_valid_forged_report_cannot_self_attest() -> None:
    observations = _observations()
    report = qualify_state_observations(
        observations, qualification_id="qualification-1", clock=lambda: 100
    )
    forged = deepcopy(report)
    forged["observations_digest"] = digest_obj({"invented": "observations"})

    with pytest.raises(StateQualificationError, match="exact observations"):
        verify_state_qualification_report(
            forged,
            observations=observations,
            expected_profile_digest=observations["profile_digest"],
            expected_runner_configuration_digest=observations[
                "runner_configuration_digest"
            ],
        )


def test_report_must_rederive_every_field_from_bound_observations() -> None:
    observations = _observations()
    report = qualify_state_observations(
        observations, qualification_id="qualification-1", clock=lambda: 100
    )
    forged = deepcopy(report)
    forged["structural_outcome_digest"] = digest_obj({"invented": "outcome"})

    with pytest.raises(StateQualificationError, match="re-derive"):
        verify_state_qualification_report(
            forged,
            observations=observations,
            expected_profile_digest=observations["profile_digest"],
            expected_runner_configuration_digest=observations[
                "runner_configuration_digest"
            ],
        )


@pytest.mark.parametrize("scenario", sorted(SCENARIO_EXPECTATIONS))
def test_forced_negative_mutation_unqualifies_every_scenario(scenario: str) -> None:
    observations = _observations()
    record = next(item for item in observations["observations"] if item["scenario"] == scenario)
    record["disposition"] = (
        "refused-before-model" if record["disposition"] == "admitted" else "admitted"
    )

    report = qualify_state_observations(
        observations, qualification_id="qualification-mutated", clock=lambda: 100
    )

    assert report["qualified"] is False
    assert any(scenario in failure for failure in report["failures"])


def test_negative_scenario_with_any_model_attempt_is_unqualified() -> None:
    observations = _observations()
    stale = next(item for item in observations["observations"] if item["scenario"] == "stale")
    stale["model_attempts"] = 1

    report = qualify_state_observations(
        observations, qualification_id="late-refusal", clock=lambda: 100
    )

    assert report["qualified"] is False
    assert "invoked a model" in "\n".join(report["failures"])


def test_negative_scenario_must_refuse_for_its_expected_reason() -> None:
    observations = _observations()
    poisoned = next(
        item for item in observations["observations"] if item["scenario"] == "poisoned"
    )
    poisoned["refusal_code"] = "DEPENDENCY_SET_MISMATCH"

    report = qualify_state_observations(
        observations, qualification_id="wrong-refusal", clock=lambda: 100
    )

    assert report["qualified"] is False
    assert "expected refusal TRUST_PROFILE_CONTRADICTION" in "\n".join(
        report["failures"]
    )


def test_invalid_admitted_observation_cannot_publish_consensus_digest() -> None:
    observations = _observations()
    cold = next(item for item in observations["observations"] if item["scenario"] == "cold")
    cold["downstream_probe_reached"] = False

    report = qualify_state_observations(
        observations, qualification_id="invalid-admission", clock=lambda: 100
    )

    assert report["qualified"] is False
    assert report["structural_outcome_digest"] == ""


def test_compaction_may_change_subject_but_not_structural_outcome() -> None:
    observations = _observations()
    compacted = next(
        item for item in observations["observations"] if item["scenario"] == "compaction-boundary"
    )
    compacted["subject_digest"] = digest_obj({"new": "capsule-after-compaction"})
    report = qualify_state_observations(
        observations, qualification_id="compacted", clock=lambda: 100
    )
    assert report["qualified"] is True

    mutated = deepcopy(observations)
    next(
        item for item in mutated["observations"] if item["scenario"] == "compaction-boundary"
    )["structural_outcome_digest"] = digest_obj({"different": "outcome"})
    report = qualify_state_observations(
        mutated, qualification_id="compacted-drift", clock=lambda: 100
    )
    assert report["qualified"] is False


def test_missing_or_duplicate_scenario_is_not_a_qualifiable_document() -> None:
    observations = _observations()
    observations["observations"][-1] = dict(observations["observations"][0])

    with pytest.raises(StateQualificationError, match="repeat"):
        qualify_state_observations(
            observations, qualification_id="duplicate", clock=lambda: 100
        )
