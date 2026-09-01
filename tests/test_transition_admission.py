"""Forcing tests for TRANSITION_ADMISSION (plan 4.1c, first migrated axis).

Three properties: released rows are FROZEN (a digest pin turns any edit red, in
both directions), both state paths consume the same row (changing the row moves
write AND derive behavior), and the unified answer closes the write/derive
drift the extraction exposed.
"""

from __future__ import annotations

import pytest

from factory_core.manifest import digest_obj
from factory_runtime.transition_admission import (
    TRANSITION_ADMISSION,
    allowed_authority_nonce_counts,
)

# The frozen pin: released rows are never edited. A current-version behavior
# change lands as a NEW keyed row; editing these bytes is the drift class
# 4.1c exists to kill, and this digest makes the edit red in both directions
# (row changed -> digest differs; row deleted -> KeyError).
_FROZEN_ROW_DIGESTS = {
    "factory-run/4": "sha256:bfdaa5b8f584d762e5eb9da6886225c4"
    "16e194d6496e4e12d5daa25bb58e3aad",
}


def _row_digest(version: str) -> str:
    row = TRANSITION_ADMISSION[version]
    return digest_obj(
        {
            "base_states": sorted(row.base_states),
            "phase_extras": list(row.phase_extras),
            "activation_dual_extra": row.activation_dual_extra,
        }
    )


def test_released_rows_are_frozen_both_directions() -> None:
    for version, expected in _FROZEN_ROW_DIGESTS.items():
        assert version in TRANSITION_ADMISSION, f"released row {version} deleted"
        assert _row_digest(version) == expected, (
            f"released row {version} was EDITED — released rows are frozen; a "
            f"behavior change lands as a new keyed row"
        )


def test_unknown_schema_version_resolves_to_the_current_row_fail_closed() -> None:
    current = allowed_authority_nonce_counts(
        schema_version="factory-run/5",
        destination="intake",
        phase_key=False,
        catalog_activation=False,
        test_change_activation=False,
    )
    unknown = allowed_authority_nonce_counts(
        schema_version="factory-run/999",
        destination="intake",
        phase_key=False,
        catalog_activation=False,
        test_change_activation=False,
    )
    assert unknown == current


def test_the_drift_the_extraction_exposed_is_closed() -> None:
    """The write path counted INTAKE only; the derive path counted
    TARGET_RESOLUTION_AUTHORIZED too. One row, one answer, both states."""
    for destination in ("intake", "target-resolution-authorized"):
        counts = allowed_authority_nonce_counts(
            schema_version="factory-run/5",
            destination=destination,
            phase_key=False,
            catalog_activation=False,
            test_change_activation=False,
        )
        assert counts == frozenset({1}), destination
    assert allowed_authority_nonce_counts(
        schema_version="factory-run/5",
        destination="building",
        phase_key=False,
        catalog_activation=False,
        test_change_activation=False,
    ) == frozenset({0})


def test_phase_and_activation_generations() -> None:
    phase = allowed_authority_nonce_counts(
        schema_version="factory-run/5",
        destination="product-specification-ratified",
        phase_key=True,
        catalog_activation=False,
        test_change_activation=False,
    )
    assert phase == frozenset({0, 1, 2})
    both = allowed_authority_nonce_counts(
        schema_version="factory-run/5",
        destination="building",
        phase_key=False,
        catalog_activation=True,
        test_change_activation=True,
    )
    # base 2 (one authority nonce each) + up to two dual-history validator nonces
    assert both == frozenset({2, 3, 4})


def test_both_state_paths_consume_the_same_row(tmp_path, monkeypatch) -> None:
    """The consume-the-table assertion: poison the module's one answer and BOTH
    the write path (transition) and the derive path (rebuild/load) refuse —
    proving neither carries a private twin of the rule."""
    import factory_runtime.state as state_module
    from factory_core.manifest import digest_obj as _digest
    from factory_runtime.state import RunState, RunStateError, RunStore
    from tests.conftest import create_intake_run

    runs = tmp_path / "runs"
    runs.mkdir()
    store = RunStore(runs)
    create_intake_run(
        store,
        run_id="r1",
        target_digest="sha256:" + "a" * 64,
        source_digest=_digest({"source": "r1"}),
    )

    def nothing_is_admissible(**_kwargs) -> frozenset[int]:
        return frozenset()

    monkeypatch.setattr(
        state_module, "allowed_authority_nonce_counts_for", nothing_is_admissible
    )
    with pytest.raises(RunStateError, match="nonce count"):
        store.transition(
            "r1",
            RunState.PRODUCT_SPECIFICATION_RATIFIED,
            actor="validator",
            artifact_digests={
                "product-specification": "sha256:" + "b" * 64,
                "product-specification:human-receipt": "sha256:" + "c" * 64,
            },
        )
    with pytest.raises(RunStateError, match="nonce count"):
        store.rebuild_projection("r1")  # derive path walks the same poisoned rule


def test_activation_axis_unifies_the_twin_derivations() -> None:
    """Second migrated axis: array-shape refusal, only-when-building refusal,
    catalog predicate (gated on obligation replay), and ratified-key assembly
    are one answer with the refusal context naming the ledger entry."""
    from factory_runtime.transition_admission import (
        ACCEPTANCE_OBLIGATION_CATALOG_KEY,
        TEST_CHANGE_AUTHORIZATION_KEY,
        AdmissionRefusal,
        transition_activations,
    )

    with pytest.raises(AdmissionRefusal, match="exact array"):
        transition_activations(
            destination="building",
            phase_key=None,
            changed_existing_tests_raw="not-a-list",
            catalog_digest_recorded=False,
            obligation_replay=True,
        )
    with pytest.raises(
        AdmissionRefusal, match="ledger entry 3 test expectation changes"
    ):
        transition_activations(
            destination="intake",
            phase_key=None,
            changed_existing_tests_raw=["t1"],
            catalog_digest_recorded=False,
            obligation_replay=True,
            context="ledger entry 3 ",
        )

    first_build = transition_activations(
        destination="building",
        phase_key=None,
        changed_existing_tests_raw=["t1"],
        catalog_digest_recorded=False,
        obligation_replay=True,
    )
    assert first_build.catalog_activation
    assert first_build.test_change_activation
    assert first_build.ratified_artifact_keys == frozenset(
        {ACCEPTANCE_OBLIGATION_CATALOG_KEY, TEST_CHANGE_AUTHORIZATION_KEY}
    )
    # pre-obligation-replay versions never activate a catalog; a recorded
    # catalog digest means no re-activation on later builds.
    assert not transition_activations(
        destination="building",
        phase_key=None,
        changed_existing_tests_raw=[],
        catalog_digest_recorded=False,
        obligation_replay=False,
    ).catalog_activation
    assert not transition_activations(
        destination="building",
        phase_key=None,
        changed_existing_tests_raw=[],
        catalog_digest_recorded=True,
        obligation_replay=True,
    ).catalog_activation
    phase = transition_activations(
        destination="product-specification-ratified",
        phase_key="product-specification",
        changed_existing_tests_raw=[],
        catalog_digest_recorded=False,
        obligation_replay=True,
    )
    assert phase.ratified_artifact_keys == frozenset({"product-specification"})


def test_both_state_paths_consume_the_same_activation_axis(
    tmp_path, monkeypatch
) -> None:
    """Poison the second axis' one answer and BOTH the write path and the
    derive path refuse — neither carries a private twin of the derivation."""
    import factory_runtime.state as state_module
    from factory_core.manifest import digest_obj as _digest
    from factory_runtime.state import RunState, RunStateError, RunStore
    from factory_runtime.transition_admission import AdmissionRefusal
    from tests.conftest import create_intake_run

    runs = tmp_path / "runs"
    runs.mkdir()
    store = RunStore(runs)
    create_intake_run(
        store,
        run_id="r1",
        target_digest="sha256:" + "a" * 64,
        source_digest=_digest({"source": "r1"}),
    )

    def poisoned(**_kwargs):
        raise AdmissionRefusal("activation axis poisoned")

    monkeypatch.setattr(state_module, "transition_activations_for", poisoned)
    with pytest.raises(RunStateError, match="activation axis poisoned"):
        store.transition(
            "r1",
            RunState.PRODUCT_SPECIFICATION_RATIFIED,
            actor="validator",
            artifact_digests={
                "product-specification": "sha256:" + "b" * 64,
                "product-specification:human-receipt": "sha256:" + "c" * 64,
            },
        )
    with pytest.raises(RunStateError, match="activation axis poisoned"):
        store.rebuild_projection("r1")


def test_membership_tuples_are_pinned_shared_data() -> None:
    """Third migrated axis: the artifact-key membership tuples both paths
    enumerate are single shared constants. The pin makes any edit loud — a
    membership change is a run-contract change, reviewed here, never a silent
    drift between two inline twins."""
    from factory_runtime.transition_admission import (
        IMMUTABLE_AFTER_VALIDATION_KEYS,
        PREVIEW_REQUIRED_ARTIFACT_KEYS,
        VALIDATION_SUBJECT_KEYS,
        VALIDATOR_EXECUTION_ARTIFACT_KEYS,
    )

    exec_keys = (
        "validator-execution-manifest",
        "validator-execution-configuration",
        "validator-execution-environment",
        "validator-execution-snapshot",
    )
    assert VALIDATOR_EXECUTION_ARTIFACT_KEYS == exec_keys
    assert VALIDATION_SUBJECT_KEYS == (
        "candidate",
        "acceptance-tests",
        "coder-output-snapshot",
        "tester-output-snapshot",
        *exec_keys,
    )
    assert IMMUTABLE_AFTER_VALIDATION_KEYS == ("candidate", "acceptance-tests", *exec_keys)
    assert PREVIEW_REQUIRED_ARTIFACT_KEYS == (
        "candidate",
        "acceptance-tests",
        "acceptance-obligation-report",
        "validator-review-subject",
        "validator-adversarial-review",
        "base-source-snapshot",
        "candidate-change-set",
        "validator-review-authority-context",
        "validator-review-observations-source",
        *exec_keys,
        "evidence-bundle",
        "evidence-envelope",
    )
    # the immutable set is a subset of the validation subject: nothing can be
    # frozen at preview that was never part of the validation subject.
    assert set(IMMUTABLE_AFTER_VALIDATION_KEYS) <= set(VALIDATION_SUBJECT_KEYS)


def test_authority_destination_walk_derives_from_the_row() -> None:
    """The re-based obligation walk (cross-axis resolution 4): destinations
    come off the row (base states nonce-consuming, ratifications and building
    activations human-receipted), never re-asserted by a consumer."""
    from factory_runtime.transition_admission import authority_destination_walk

    walk = authority_destination_walk(
        schema_version="factory-run/5",
        ratification_destinations=("product-specification-ratified",),
    )
    assert {w.destination for w in walk} == {
        "intake",
        "target-resolution-authorized",
        "product-specification-ratified",
        "building",
    }
    # membership is the walk's one owned fact (8-3: receipt/nonce flags were
    # deleted as asserted-but-unconsumed restatements of the obligation layer).
    # no current row admits external bytes, so no walked destination names a
    # validator — the day one does, the preflight check below starts firing.
    assert all(not w.named_validator for w in walk)


def test_preflight_refuses_an_uncallable_admission_validator(monkeypatch) -> None:
    """A byte-admitting row whose named validator is not registered callable is
    a hard NO at hour zero — bytes at that destination could never be admitted.
    GO sibling: the current row (no byte admission) emits no such finding."""
    import factory_runtime.transition_admission as admission_module
    from factory_core.criticality import CriticalityProfile
    from factory_core.manifest import SegregationPolicy
    from factory_runtime.preflight import run_preflight

    policy = SegregationPolicy(human_ids=frozenset({"human:founder"}))
    profile = CriticalityProfile.from_dict(
        {
            "profile_id": "p",
            "decider": "human:founder",
            "components": {},
            "surfaces": {},
        }
    )
    clean = run_preflight(profile=profile, policy=policy)
    assert "preflight-admission-validator-uncallable" not in [
        f.code for f in clean.hard_no
    ]

    poisoned_row = admission_module._NonceAdmissionRow(
        base_states=frozenset({"intake"}),
        phase_extras=(0,),
        activation_dual_extra=False,
        admits_external_bytes=True,
        named_validator="registered-nowhere",
    )
    monkeypatch.setitem(
        admission_module.TRANSITION_ADMISSION, "factory-run/5", poisoned_row
    )
    poisoned = run_preflight(profile=profile, policy=policy)
    assert not poisoned.go
    assert "preflight-admission-validator-uncallable" in [
        f.code for f in poisoned.hard_no
    ]


def test_every_byte_admitting_row_names_a_callable_validator() -> None:
    """4.1's rule for LLM entry rows, as a contract rather than a count: a row
    that admits externally produced bytes must name its mechanical validator,
    and the name must resolve to a callable in ADMISSION_VALIDATORS. No current
    row admits bytes — this test is the reason the first one that does cannot
    land unvalidated."""
    from factory_runtime.transition_admission import ADMISSION_VALIDATORS

    for version, row in TRANSITION_ADMISSION.items():
        if row.admits_external_bytes:
            assert row.named_validator, (
                f"{version}: byte-admitting row names no validator"
            )
            validator = ADMISSION_VALIDATORS.get(row.named_validator)
            assert callable(validator), (
                f"{version}: named validator {row.named_validator!r} does not "
                f"resolve to a callable"
            )
        else:
            assert not row.named_validator, (
                f"{version}: a validator name on a non-byte-admitting row is "
                f"dead configuration"
            )
