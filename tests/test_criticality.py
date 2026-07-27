"""Surface-criticality profile and declared-side-effect inheritance tests."""

from __future__ import annotations

import json
import re
from pathlib import Path

from factory_core.criticality import (
    BASE_REQUIRED_EVIDENCE_IDS,
    CRITICAL_APPROVER_FLOOR,
    CRITICALITY_COSMETIC,
    CRITICALITY_CRITICAL,
    CRITICALITY_STANDARD,
    CriticalityProfile,
    SurfaceControl,
    resolve_criticality,
)
from factory_core.manifest import SegregationPolicy

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "factory_core" / "criticality.py"
DENYLIST_TOKENS = tuple(
    json.loads((REPO_ROOT / "core_purity_denylist.json").read_text(encoding="utf-8")).get(
        "tokens", []
    )
)


def _policy() -> SegregationPolicy:
    return SegregationPolicy(
        human_ids=frozenset({"alice", "bob"}),
        human_aliases={
            "alice": "alice",
            "alice@example.com": "alice",
            "bob": "bob",
        },
        excluded_service_identities=frozenset({"*-bot", "factory-agent"}),
    )


def _surface(
    surface_id: str,
    criticality: str,
    *,
    side_effects: tuple[str, ...] = (),
    decided_by: str = "alice",
    component_id: str = "component-a",
    wrong_cost: str = "A bounded class-appropriate failure.",
) -> SurfaceControl:
    return SurfaceControl(
        surface_id=surface_id,
        component_id=component_id,
        criticality=criticality,
        decided_by=decided_by,
        wrong_cost=wrong_cost,
        side_effect_surface_ids=side_effects,
    )


def test_declared_side_effect_closure_inherits_highest_class() -> None:
    profile = CriticalityProfile(
        surfaces=(
            _surface("display", CRITICALITY_COSMETIC, side_effects=("records",)),
            _surface("records", CRITICALITY_CRITICAL, component_id="component-b"),
        )
    )

    resolution = resolve_criticality(profile, ("display",), _policy())

    assert resolution.surface_ids == ("display", "records")
    assert resolution.highest_criticality == CRITICALITY_CRITICAL
    assert resolution.blocking_issues == ()
    assert resolution.reports == ()


def test_unknown_and_invalidly_decided_surfaces_resolve_critical() -> None:
    profile = CriticalityProfile(
        surfaces=(
            _surface(
                "ordinary",
                CRITICALITY_STANDARD,
                decided_by="factory-agent",
            ),
        )
    )

    invalid_decider = resolve_criticality(profile, ("ordinary",), _policy())
    unknown = resolve_criticality(profile, ("new-surface",), _policy())

    assert invalid_decider.highest_criticality == CRITICALITY_CRITICAL
    assert "classification-decider-invalid:ordinary" in invalid_decider.reports
    assert unknown.highest_criticality == CRITICALITY_CRITICAL
    assert "surface-unclassified:new-surface" in unknown.reports


def test_missing_component_or_wrong_cost_cannot_lower_class() -> None:
    profile = CriticalityProfile(
        surfaces=(
            _surface(
                "display",
                CRITICALITY_COSMETIC,
                component_id="",
                wrong_cost="",
            ),
        )
    )

    resolution = resolve_criticality(profile, ("display",), _policy())

    assert resolution.highest_criticality == CRITICALITY_CRITICAL
    assert "surface-component-missing:display" in resolution.reports
    assert "classification-rationale-missing:display" in resolution.reports


def test_required_evidence_always_contains_the_three_doctrine_links() -> None:
    control = SurfaceControl(
        surface_id="ordinary",
        component_id="component-a",
        criticality=CRITICALITY_STANDARD,
        decided_by="alice",
        wrong_cost="A reversible business-logic error.",
        required_evidence_ids=frozenset({"contract"}),
    )

    resolution = resolve_criticality(
        CriticalityProfile(surfaces=(control,)),
        ("ordinary",),
        _policy(),
    )

    assert resolution.surfaces[0].required_evidence_ids == (
        BASE_REQUIRED_EVIDENCE_IDS | {"contract"}
    )


def test_structural_profile_defects_block_and_empty_disturbance_never_passes() -> None:
    duplicate = _surface("same", CRITICALITY_STANDARD)
    resolution = resolve_criticality(
        CriticalityProfile(
            surfaces=(
                duplicate,
                duplicate,
                _surface("bad-budget", CRITICALITY_STANDARD),
            )
        ),
        (),
        _policy(),
    )
    bad_budget = CriticalityProfile(
        surfaces=(
            SurfaceControl(
                surface_id="bad-budget",
                component_id="component-a",
                criticality=CRITICALITY_STANDARD,
                decided_by="alice",
                wrong_cost="Reversible.",
                standard_flake_budget=-1,
            ),
        )
    )
    budget_resolution = resolve_criticality(bad_budget, ("bad-budget",), _policy())

    assert "surface-id-duplicate:same" in resolution.blocking_issues
    assert "disturbed-surfaces-missing" in resolution.blocking_issues
    assert "standard-flake-budget-invalid:bad-budget" in budget_resolution.blocking_issues


def test_profile_from_dict_normalizes_and_cannot_lower_critical_floor() -> None:
    profile = CriticalityProfile.from_dict(
        {
            "surfaces": [
                {
                    "surface_id": " Display ",
                    "component_id": " Component-A ",
                    "criticality": " COSMETIC ",
                    "decided_by": "alice@example.com",
                    "wrong_cost": "Aesthetic only.",
                    "required_evidence_ids": ["Visual-Diff"],
                    "side_effect_surface_ids": ["DISPLAY"],
                }
            ],
            "required_gate_ids": ["Tests"],
            "critical_min_approvers": 1,
        }
    )

    assert profile.required_critical_approvers == CRITICAL_APPROVER_FLOOR == 2
    assert profile.required_gate_ids == frozenset({"tests"})
    assert profile.surfaces[0].surface_id == "display"
    assert profile.surfaces[0].required_evidence_ids == frozenset({"visual-diff"})


def test_profile_content_address_moves_when_class_or_side_effect_changes() -> None:
    standard = CriticalityProfile(surfaces=(_surface("ordinary", CRITICALITY_STANDARD),))
    critical = CriticalityProfile(surfaces=(_surface("ordinary", CRITICALITY_CRITICAL),))
    with_side_effect = CriticalityProfile(
        surfaces=(
            _surface(
                "ordinary",
                CRITICALITY_STANDARD,
                side_effects=("another-surface",),
            ),
        )
    )

    assert standard.content_digest != critical.content_digest
    assert standard.content_digest != with_side_effect.content_digest


def _runs(text: str) -> set[str]:
    return {run for run in re.split(r"[^a-z0-9]+", text.lower()) if run}


def test_module_names_nothing_target_specific() -> None:
    runs = _runs(MODULE_PATH.read_text(encoding="utf-8"))
    assert not [token for token in DENYLIST_TOKENS if token in runs]
