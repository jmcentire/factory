"""Surface-criticality control profiles and inheritance.

Criticality is not a risk score and not a property of a diff. It is human-decided data about
what a surface is for and therefore what being wrong costs. The promotion engine uses it only
to decide:

* how an oracle or evidence *gap* is disposed; and
* how deterministic evidence must be before it counts.

This module owns the neutral data model and the declared side-effect closure. It does not
pretend that a supplied topology is complete: the phase-2 artifact and its parity/enumeration
controls must establish that separately. What it does guarantee is that every explicitly
disturbed surface, and every surface reachable through declared side effects, participates in
the decision. An unknown, unclassified, invalidly classified, or non-human-decided surface is
resolved as critical.

All component ids, surface ids, gate ids, and additional evidence ids are target data. The only
vocabulary fixed in the core is the doctrine's three classes.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from factory_core.manifest import SegregationPolicy, digest_obj

CRITICALITY_CRITICAL = "critical"
CRITICALITY_STANDARD = "standard"
CRITICALITY_COSMETIC = "cosmetic"

CRITICALITY_CLASSES: tuple[str, ...] = (
    CRITICALITY_CRITICAL,
    CRITICALITY_STANDARD,
    CRITICALITY_COSMETIC,
)

CRITICAL_APPROVER_FLOOR = 2

# These are the three evidence-chain links named by the doctrine. A surface may require more
# evidence through its control profile, but it may not remove these.
BASE_REQUIRED_EVIDENCE_IDS: frozenset[str] = frozenset(
    {
        "attestation",
        "provenance",
        "live-verification",
    }
)

_CLASS_RANK = {
    CRITICALITY_COSMETIC: 0,
    CRITICALITY_STANDARD: 1,
    CRITICALITY_CRITICAL: 2,
}


class CriticalityError(ValueError):
    """Raised when criticality input cannot be parsed without guessing."""


def normalize_label(value: str) -> str:
    """Normalize target-supplied labels for deterministic matching."""

    return value.strip().casefold()


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(str(item) for item in value)
    return (str(value),)


def _as_int(value: Any, *, field_name: str, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise CriticalityError(f"{field_name!r} must be an integer, got {value!r}") from exc


@dataclass(frozen=True)
class SurfaceControl:
    """One human-decided surface entry from the phase-2 control profile.

    ``side_effect_surface_ids`` is declarative topology. Closure over it enforces inheritance
    for every edge supplied, but does not prove that no real edge was omitted.
    """

    surface_id: str
    component_id: str = ""
    criticality: str = ""
    decided_by: str = ""
    wrong_cost: str = ""
    side_effect_surface_ids: tuple[str, ...] = ()
    required_evidence_ids: frozenset[str] = frozenset()
    standard_flake_budget: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "surface_id", normalize_label(self.surface_id))
        object.__setattr__(self, "component_id", normalize_label(self.component_id))
        object.__setattr__(self, "criticality", normalize_label(self.criticality))
        object.__setattr__(
            self,
            "side_effect_surface_ids",
            tuple(
                dict.fromkeys(
                    normalize_label(item)
                    for item in self.side_effect_surface_ids
                    if normalize_label(item)
                )
            ),
        )
        object.__setattr__(
            self,
            "required_evidence_ids",
            frozenset(
                normalize_label(item)
                for item in self.required_evidence_ids
                if normalize_label(item)
            ),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> SurfaceControl:
        return cls(
            surface_id=str(raw.get("surface_id", "")),
            component_id=str(raw.get("component_id", "")),
            criticality=str(raw.get("criticality", "")),
            decided_by=str(raw.get("decided_by", "")),
            wrong_cost=str(raw.get("wrong_cost", "")),
            side_effect_surface_ids=_as_str_tuple(raw.get("side_effect_surface_ids")),
            required_evidence_ids=frozenset(_as_str_tuple(raw.get("required_evidence_ids"))),
            standard_flake_budget=_as_int(
                raw.get("standard_flake_budget"),
                field_name="standard_flake_budget",
                default=0,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "component_id": self.component_id,
            "criticality": self.criticality,
            "decided_by": self.decided_by,
            "wrong_cost": self.wrong_cost,
            "side_effect_surface_ids": list(self.side_effect_surface_ids),
            "required_evidence_ids": sorted(self.required_evidence_ids),
            "standard_flake_budget": self.standard_flake_budget,
        }


@dataclass(frozen=True)
class CriticalityProfile:
    """The target's surface policy as data.

    A target may raise the critical distinct-human approval floor but cannot lower the
    doctrine floor of two.

    ``critical_ratification_delegates`` is the named roster that fills the accountable-human seat
    on a Critical surface. The principle it implements — an agent never occupies that seat — was
    adopted with an explicit availability cost: a hazard-surface promotion waits on *any* named
    delegate rather than on one individual, because a rule that blocks on one person's calendar is
    a rule that gets suspended the first time it is inconvenient. An empty roster is not a
    permissive default; it means nobody decided who may ratify, and the promotion layer disposes
    of that as an evidence gap by class.
    """

    surfaces: tuple[SurfaceControl, ...] = ()
    required_gate_ids: frozenset[str] = frozenset()
    critical_min_approvers: int = CRITICAL_APPROVER_FLOOR
    critical_ratification_delegates: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "required_gate_ids",
            frozenset(
                normalize_label(item) for item in self.required_gate_ids if normalize_label(item)
            ),
        )
        object.__setattr__(
            self,
            "critical_ratification_delegates",
            frozenset(
                item.strip()
                for item in self.critical_ratification_delegates
                if item.strip()
            ),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CriticalityProfile:
        raw_surfaces = raw.get("surfaces")
        surfaces = tuple(
            SurfaceControl.from_dict(item)
            for item in (raw_surfaces if isinstance(raw_surfaces, Sequence) else ())
            if isinstance(item, Mapping)
        )
        return cls(
            surfaces=surfaces,
            required_gate_ids=frozenset(_as_str_tuple(raw.get("required_gate_ids"))),
            critical_min_approvers=_as_int(
                raw.get("critical_min_approvers"),
                field_name="critical_min_approvers",
                default=CRITICAL_APPROVER_FLOOR,
            ),
            critical_ratification_delegates=frozenset(
                _as_str_tuple(raw.get("critical_ratification_delegates"))
            ),
        )

    @property
    def required_critical_approvers(self) -> int:
        return max(CRITICAL_APPROVER_FLOOR, self.critical_min_approvers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "surfaces": [surface.to_dict() for surface in self.surfaces],
            "required_gate_ids": sorted(self.required_gate_ids),
            "critical_min_approvers": self.critical_min_approvers,
            "critical_ratification_delegates": sorted(self.critical_ratification_delegates),
        }

    @property
    def content_digest(self) -> str:
        """Content address of the exact control profile used for a decision."""

        return digest_obj(self.to_dict())


@dataclass(frozen=True)
class ResolvedSurface:
    """One surface as evaluated for a specific change."""

    surface_id: str
    component_id: str
    declared_criticality: str
    effective_criticality: str
    decided_by: str
    wrong_cost: str
    required_evidence_ids: frozenset[str]
    standard_flake_budget: int
    side_effect_surface_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "component_id": self.component_id,
            "declared_criticality": self.declared_criticality,
            "effective_criticality": self.effective_criticality,
            "decided_by": self.decided_by,
            "wrong_cost": self.wrong_cost,
            "required_evidence_ids": sorted(self.required_evidence_ids),
            "standard_flake_budget": self.standard_flake_budget,
            "side_effect_surface_ids": list(self.side_effect_surface_ids),
        }


@dataclass(frozen=True)
class CriticalityResolution:
    """Declared side-effect closure and its fail-closed classification result."""

    surfaces: tuple[ResolvedSurface, ...]
    highest_criticality: str
    blocking_issues: tuple[str, ...]
    reports: tuple[str, ...]

    @property
    def surface_ids(self) -> tuple[str, ...]:
        return tuple(surface.surface_id for surface in self.surfaces)

    def to_dict(self) -> dict[str, Any]:
        return {
            "surfaces": [surface.to_dict() for surface in self.surfaces],
            "highest_criticality": self.highest_criticality,
            "blocking_issues": list(self.blocking_issues),
            "reports": list(self.reports),
        }


def _effective_class(
    control: SurfaceControl,
    policy: SegregationPolicy,
) -> tuple[str, tuple[str, ...]]:
    reports: list[str] = []
    declared = control.criticality
    if declared not in _CLASS_RANK:
        reports.append(f"surface-unclassified:{control.surface_id}")
    if not control.component_id:
        reports.append(f"surface-component-missing:{control.surface_id}")
    if policy.resolve_human(control.decided_by) is None:
        reports.append(f"classification-decider-invalid:{control.surface_id}")
    if not control.wrong_cost.strip():
        reports.append(f"classification-rationale-missing:{control.surface_id}")

    valid = (
        declared in _CLASS_RANK
        and bool(control.component_id)
        and policy.resolve_human(control.decided_by) is not None
        and bool(control.wrong_cost.strip())
    )
    return (declared if valid else CRITICALITY_CRITICAL), tuple(reports)


def resolve_criticality(
    profile: CriticalityProfile,
    disturbed_surface_ids: Iterable[str],
    policy: SegregationPolicy,
) -> CriticalityResolution:
    """Resolve explicit disturbances plus declared side effects to their highest class.

    Unknown or invalidly classified surfaces become implicit Critical entries. Duplicate
    profile entries and invalid flake budgets are structural policy defects and block rather
    than relying on whichever duplicate happened to be indexed first.
    """

    blocking: list[str] = []
    reports: list[str] = []
    controls: dict[str, SurfaceControl] = {}

    for profile_control in profile.surfaces:
        if not profile_control.surface_id:
            blocking.append("surface-id-missing")
            continue
        if profile_control.surface_id in controls:
            blocking.append(f"surface-id-duplicate:{profile_control.surface_id}")
            continue
        if profile_control.standard_flake_budget < 0:
            blocking.append(f"standard-flake-budget-invalid:{profile_control.surface_id}")
        controls[profile_control.surface_id] = profile_control

    roots = tuple(
        dict.fromkeys(
            normalize_label(surface_id)
            for surface_id in disturbed_surface_ids
            if normalize_label(surface_id)
        )
    )
    if not roots:
        blocking.append("disturbed-surfaces-missing")

    queue: deque[str] = deque(roots)
    visited: set[str] = set()
    resolved: list[ResolvedSurface] = []

    while queue:
        surface_id = queue.popleft()
        if surface_id in visited:
            continue
        visited.add(surface_id)

        resolved_control = controls.get(surface_id)
        if resolved_control is None:
            reports.append(f"surface-unclassified:{surface_id}")
            resolved.append(
                ResolvedSurface(
                    surface_id=surface_id,
                    component_id="",
                    declared_criticality="",
                    effective_criticality=CRITICALITY_CRITICAL,
                    decided_by="",
                    wrong_cost="",
                    required_evidence_ids=BASE_REQUIRED_EVIDENCE_IDS,
                    standard_flake_budget=0,
                    side_effect_surface_ids=(),
                )
            )
            continue

        effective, classification_reports = _effective_class(resolved_control, policy)
        reports.extend(classification_reports)
        resolved.append(
            ResolvedSurface(
                surface_id=resolved_control.surface_id,
                component_id=resolved_control.component_id,
                declared_criticality=resolved_control.criticality,
                effective_criticality=effective,
                decided_by=resolved_control.decided_by,
                wrong_cost=resolved_control.wrong_cost,
                required_evidence_ids=(
                    BASE_REQUIRED_EVIDENCE_IDS | resolved_control.required_evidence_ids
                ),
                standard_flake_budget=max(0, resolved_control.standard_flake_budget),
                side_effect_surface_ids=resolved_control.side_effect_surface_ids,
            )
        )
        queue.extend(resolved_control.side_effect_surface_ids)

    resolved.sort(key=lambda item: item.surface_id)
    highest = max(
        (surface.effective_criticality for surface in resolved),
        key=lambda item: _CLASS_RANK[item],
        default=CRITICALITY_CRITICAL,
    )
    return CriticalityResolution(
        surfaces=tuple(resolved),
        highest_criticality=highest,
        blocking_issues=tuple(dict.fromkeys(blocking)),
        reports=tuple(dict.fromkeys(reports)),
    )
