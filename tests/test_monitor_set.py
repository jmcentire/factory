"""Spec-derived monitors: resolvable authority, class-scoped authorship, recorded density."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from factory_core.criticality import (
    BASE_REQUIRED_EVIDENCE_IDS,
    CRITICALITY_COSMETIC,
    CRITICALITY_CRITICAL,
    CRITICALITY_STANDARD,
    ResolvedSurface,
)
from factory_core.manifest import SegregationPolicy, digest_obj
from factory_core.monitors import (
    MONITOR_AUTHORSHIP_GENERATED,
    MONITOR_AUTHORSHIP_HUMAN,
    MONITOR_DERIVATION_IMPLEMENTATION,
    MONITOR_DERIVATION_SPECIFICATION,
    Monitor,
    verify_monitor_set,
)
from factory_core.provenance import IntentBackreference, IntentItem, PhaseArtifact


def _roster() -> SegregationPolicy:
    return SegregationPolicy(
        human_ids=frozenset({"alice", "carol"}),
        human_aliases={"alice": "alice", "carol": "carol"},
        excluded_service_identities=frozenset({"*-bot", "factory-agent"}),
    )


def _artifact() -> PhaseArtifact:
    return PhaseArtifact(
        artifact_id="phase-3",
        phase="operational-maturity",
        version="1",
        source_digest=digest_obj({"verbatim": "source"}),
        human_ratifier="human-1",
        validator_ratifier="validator-1",
        items=(
            IntentItem(
                item_id="criterion",
                canonical_statement="A rejected mutation returns a typed refusal.",
            ),
        ),
    )


def _reference() -> IntentBackreference:
    artifact = _artifact()
    return artifact.backreference(artifact.items[0])


def _surface(surface_id: str, criticality: str) -> ResolvedSurface:
    return ResolvedSurface(
        surface_id=surface_id,
        component_id="component-a",
        declared_criticality=criticality,
        effective_criticality=criticality,
        decided_by="carol",
        wrong_cost="The class-bounded failure.",
        required_evidence_ids=BASE_REQUIRED_EVIDENCE_IDS,
        standard_flake_budget=0,
        side_effect_surface_ids=(),
    )


def _monitor(surface_id: str, **overrides: Any) -> Monitor:
    values: dict[str, Any] = {
        "monitor_id": f"monitor-{surface_id}",
        "surface_id": surface_id,
        "derivation": MONITOR_DERIVATION_SPECIFICATION,
        "authorship": MONITOR_AUTHORSHIP_HUMAN,
        "author_identity": "carol",
        "backreference": _reference(),
        "actionable_conclusion": "Page the surface owner with the unmet criterion.",
        "notifies_human": True,
    }
    values.update(overrides)
    return Monitor(**values)


def _verify(
    monitors: tuple[Monitor, ...],
    surfaces: tuple[ResolvedSurface, ...],
    **kwargs: Any,
) -> Any:
    kwargs.setdefault("resolved_backreferences", (_reference(),))
    return verify_monitor_set(monitors, surfaces, _roster(), **kwargs)


def test_a_spec_derived_monitor_with_resolved_authority_satisfies_the_set() -> None:
    surface = _surface("standard-surface", CRITICALITY_STANDARD)

    report = _verify((_monitor("standard-surface"),), (surface,))

    assert report.satisfied is True
    assert report.monitor_ids == ("monitor-standard-surface",)


def test_a_diff_derived_monitor_cannot_serve_as_an_oracle() -> None:
    surface = _surface("standard-surface", CRITICALITY_STANDARD)

    report = _verify(
        (_monitor("standard-surface", derivation=MONITOR_DERIVATION_IMPLEMENTATION),),
        (surface,),
    )

    # An integrity failure, not a class-disposed absence: it asserts what the code does.
    assert "monitor-diff-derived:monitor-standard-surface" in report.integrity_issues
    assert report.satisfied is False


def test_unrecorded_or_unknown_derivation_are_distinguished() -> None:
    surface = _surface("standard-surface", CRITICALITY_STANDARD)

    unrecorded = _verify((_monitor("standard-surface", derivation=""),), (surface,))
    unknown = _verify((_monitor("standard-surface", derivation="vibes"),), (surface,))

    assert (
        "standard-surface",
        "monitor-derivation-unrecorded:monitor-standard-surface",
    ) in unrecorded.surface_gaps
    assert (
        "monitor-derivation-unknown:monitor-standard-surface:vibes" in unknown.integrity_issues
    )


def test_an_unresolvable_backreference_is_an_unauthorized_assertion_about_production() -> None:
    surface = _surface("cosmetic-surface", CRITICALITY_COSMETIC)
    fabricated = _monitor("cosmetic-surface", backreference=replace(_reference(), item_id="absent"))

    report = _verify((fabricated,), (surface,))

    # It blocks on a cosmetic surface too: this is not an evidence gap, it is a false citation.
    assert "monitor-backreference-unresolved:monitor-cosmetic-surface" in report.integrity_issues


def test_a_missing_backreference_is_a_class_disposed_gap() -> None:
    surface = _surface("standard-surface", CRITICALITY_STANDARD)

    report = _verify((_monitor("standard-surface", backreference=None),), (surface,))

    assert (
        "standard-surface",
        "monitor-backreference-missing:monitor-standard-surface",
    ) in report.surface_gaps
    assert report.integrity_issues == ()


def test_without_working_authority_an_unresolved_reference_is_an_absence() -> None:
    surface = _surface("standard-surface", CRITICALITY_STANDARD)

    report = _verify(
        (_monitor("standard-surface"),),
        (surface,),
        resolved_backreferences=(),
        authority_available=False,
    )

    assert (
        "standard-surface",
        "monitor-authority-unavailable:monitor-standard-surface",
    ) in report.surface_gaps
    assert report.integrity_issues == ()


def test_critical_surfaces_carry_human_authored_monitors() -> None:
    critical = _surface("critical-surface", CRITICALITY_CRITICAL)
    standard = _surface("standard-surface", CRITICALITY_STANDARD)

    generated_on_critical = _verify(
        (_monitor("critical-surface", authorship=MONITOR_AUTHORSHIP_GENERATED),),
        (critical,),
    )
    generated_on_standard = _verify(
        (_monitor("standard-surface", authorship=MONITOR_AUTHORSHIP_GENERATED),),
        (standard,),
    )

    assert (
        "critical-surface",
        "critical-monitor-not-human-authored:monitor-critical-surface",
    ) in generated_on_critical.surface_gaps
    # Standard and cosmetic surfaces take generated monitors without a finding.
    assert generated_on_standard.satisfied is True


def test_human_authorship_must_resolve_to_an_enrolled_human() -> None:
    critical = _surface("critical-surface", CRITICALITY_CRITICAL)

    agent_authored = _verify(
        (_monitor("critical-surface", author_identity="factory-agent"),),
        (critical,),
    )
    unenrolled = _verify(
        (_monitor("critical-surface", author_identity="dana"),),
        (critical,),
    )

    assert (
        "monitor-author-not-enrolled-human:monitor-critical-surface"
        in agent_authored.integrity_issues
    )
    assert (
        "monitor-author-not-enrolled-human:monitor-critical-surface" in unenrolled.integrity_issues
    )


def test_unrecorded_or_unknown_authorship_are_distinguished() -> None:
    surface = _surface("standard-surface", CRITICALITY_STANDARD)

    unrecorded = _verify((_monitor("standard-surface", authorship=""),), (surface,))
    unknown = _verify((_monitor("standard-surface", authorship="committee"),), (surface,))

    assert (
        "standard-surface",
        "monitor-authorship-unrecorded:monitor-standard-surface",
    ) in unrecorded.surface_gaps
    assert (
        "monitor-authorship-unknown:monitor-standard-surface:committee"
        in unknown.integrity_issues
    )


def test_an_uncovered_disturbed_surface_is_a_gap_on_that_surface() -> None:
    critical = _surface("critical-surface", CRITICALITY_CRITICAL)
    cosmetic = _surface("cosmetic-surface", CRITICALITY_COSMETIC)

    report = _verify((_monitor("critical-surface"),), (critical, cosmetic))

    assert ("cosmetic-surface", "monitor-coverage-missing") in report.surface_gaps
    assert ("critical-surface", "monitor-coverage-missing") not in report.surface_gaps


def test_a_notifying_monitor_carries_a_human_actionable_conclusion() -> None:
    surface = _surface("standard-surface", CRITICALITY_STANDARD)

    noisy = _verify(
        (_monitor("standard-surface", actionable_conclusion="   "),),
        (surface,),
    )
    quiet = _verify(
        (_monitor("standard-surface", actionable_conclusion="", notifies_human=False),),
        (surface,),
    )

    assert (
        "standard-surface",
        "monitor-notifies-without-actionable-conclusion:monitor-standard-surface",
    ) in noisy.surface_gaps
    # Detection without notification is the exhaustive half and needs no conclusion.
    assert quiet.satisfied is True


def test_duplicate_and_unaddressed_monitors_are_integrity_failures() -> None:
    surface = _surface("standard-surface", CRITICALITY_STANDARD)

    report = _verify(
        (
            _monitor("standard-surface"),
            _monitor("standard-surface"),
            _monitor("standard-surface", monitor_id=""),
            _monitor("", monitor_id="monitor-orphan"),
        ),
        (surface,),
    )

    assert "monitor-duplicate:monitor-standard-surface" in report.integrity_issues
    assert "monitor-id-missing" in report.integrity_issues
    assert "monitor-surface-missing:monitor-orphan" in report.integrity_issues


def test_a_monitor_outside_the_disturbance_is_reported_not_judged() -> None:
    surface = _surface("standard-surface", CRITICALITY_STANDARD)

    report = _verify(
        (
            _monitor("standard-surface"),
            _monitor("other-surface", derivation=MONITOR_DERIVATION_IMPLEMENTATION),
        ),
        (surface,),
    )

    assert "monitor-outside-disturbance:monitor-other-surface" in report.reports
    assert report.integrity_issues == ()


def test_density_is_recorded_and_gated_on_nothing() -> None:
    surface = _surface("standard-surface", CRITICALITY_STANDARD)

    sparse = _verify((_monitor("standard-surface"),), (surface,), declared_unit_count=1_000)
    dense = _verify((_monitor("standard-surface"),), (surface,), declared_unit_count=1)
    invalid = _verify((_monitor("standard-surface"),), (surface,), declared_unit_count=-1)

    assert sparse.density == 1 / 1_000
    assert dense.density == 1.0
    # One monitor per 1000 units and one per unit are both satisfied: density is a diagnostic.
    assert sparse.satisfied is True and dense.satisfied is True
    assert "monitor-density-recorded:1/1000" in sparse.reports
    assert "monitor-declared-unit-count-invalid" in invalid.integrity_issues


def test_monitor_state_lives_on_the_monitor() -> None:
    monitor = _monitor("standard-surface")

    assert monitor.stands_down is False

    updated = monitor.with_fix_reference("defect-14")

    assert updated.stands_down is True
    assert updated.fix_references == ("defect-14",)
    # The original is untouched: monitors are values, and the record is the appended one.
    assert monitor.stands_down is False


def test_monitor_round_trips_through_dicts() -> None:
    monitor = _monitor("standard-surface").with_fix_reference("defect-14")

    restored = Monitor.from_dict(json.loads(json.dumps(monitor.to_dict())))

    assert restored == monitor
