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
