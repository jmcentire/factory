"""Spec-derived production monitors as phase-3 outputs under provenance of intent.

A monitor derived from the implementation asserts what the code does. It is a change detector:
excellent at catching drift from yesterday's behavior, structurally incapable of catching
behavior that was wrong on day one, because the baseline it learned was the wrongness. A monitor
derived from an acceptance criterion or an invariant is an **oracle** — it fires when production
stops matching what was agreed rather than when production stops matching itself.

That is the same distinction the doctrine already draws for tests, and it does not change
because the artifact is a monitor. So this module enforces, mechanically:

* every monitor resolves a backreference to the exact phase-artifact item it watches — an
  unresolvable backreference is an unauthorized assertion about production, not an absence;
* a monitor recorded as implementation-derived cannot satisfy the obligation at all;
* monitor authorship is class-scoped — a Critical surface carries human-authored monitors,
  because the instrument you reach for when something goes seriously wrong must be one you can
  read under pressure, and "human-authored" must resolve to an enrolled human rather than being
  a label;
* a monitor that notifies a human carries a human-actionable conclusion, because detection is
  cheap and exhaustive while notification is expensive and earned; and
* monitor **density is recorded and never gated** — one monitor per N lines is a diagnostic and
  a terrible target, since a density target produces monitors written to increase the count.

Monitor ids, surface ids, and conclusions are opaque target data. Absences are class-disposed
gaps keyed to the surface; malformed, unresolvable, duplicated, and falsely attributed records
are integrity failures for every class.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from factory_core.criticality import (
    CRITICALITY_CRITICAL,
    ResolvedSurface,
    normalize_label,
)
from factory_core.manifest import SegregationPolicy
from factory_core.provenance import IntentBackreference

MONITOR_DERIVATION_SPECIFICATION = "specification"
MONITOR_DERIVATION_IMPLEMENTATION = "implementation"

MONITOR_DERIVATIONS: tuple[str, ...] = (
    MONITOR_DERIVATION_SPECIFICATION,
    MONITOR_DERIVATION_IMPLEMENTATION,
)

MONITOR_AUTHORSHIP_HUMAN = "human"
MONITOR_AUTHORSHIP_GENERATED = "generated"

MONITOR_AUTHORSHIPS: tuple[str, ...] = (
    MONITOR_AUTHORSHIP_HUMAN,
    MONITOR_AUTHORSHIP_GENERATED,
)


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(str(item) for item in value)
    return (str(value),)


@dataclass(frozen=True)
class Monitor:
    """One agreed production monitor and the authority it watches.

    ``fix_references`` is the monitor's own state. When a repair is proposed for something this
    monitor caught, the reference is appended *here* rather than held in the agent that triaged
    it, so a later trigger finds the in-flight fix and stands down. State in the agent dies with
    the agent's context; state on the monitor is what the next trigger reads.
    """

    monitor_id: str
    surface_id: str = ""
    derivation: str = ""
    authorship: str = ""
    author_identity: str = ""
    backreference: IntentBackreference | None = None
    actionable_conclusion: str = ""
    notifies_human: bool = False
    fix_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "monitor_id", normalize_label(self.monitor_id))
        object.__setattr__(self, "surface_id", normalize_label(self.surface_id))
        object.__setattr__(self, "derivation", normalize_label(self.derivation))
        object.__setattr__(self, "authorship", normalize_label(self.authorship))
        object.__setattr__(
            self,
            "fix_references",
            tuple(dict.fromkeys(item.strip() for item in self.fix_references if item.strip())),
        )

    @property
    def stands_down(self) -> bool:
        """Whether a proposed fix is already recorded on this monitor."""

        return bool(self.fix_references)

    def with_fix_reference(self, reference: str) -> Monitor:
        """Return this monitor with ``reference`` appended to its own state."""

        return replace(self, fix_references=self.fix_references + (reference,))

    def to_dict(self) -> dict[str, Any]:
        return {
            "monitor_id": self.monitor_id,
            "surface_id": self.surface_id,
            "derivation": self.derivation,
            "authorship": self.authorship,
            "author_identity": self.author_identity,
            "backreference": (
                self.backreference.to_dict() if self.backreference is not None else None
            ),
            "actionable_conclusion": self.actionable_conclusion,
            "notifies_human": self.notifies_human,
            "fix_references": list(self.fix_references),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Monitor:
        reference_raw = raw.get("backreference")
        return cls(
            monitor_id=str(raw.get("monitor_id", "")),
            surface_id=str(raw.get("surface_id", "")),
            derivation=str(raw.get("derivation", "")),
            authorship=str(raw.get("authorship", "")),
            author_identity=str(raw.get("author_identity", "")),
            backreference=(
                IntentBackreference.from_dict(reference_raw)
                if isinstance(reference_raw, Mapping)
                else None
            ),
            actionable_conclusion=str(raw.get("actionable_conclusion", "")),
            notifies_human=bool(raw.get("notifies_human", False)),
            fix_references=_as_str_tuple(raw.get("fix_references")),
        )


@dataclass(frozen=True)
class MonitorSetReport:
    """Independently inspectable monitor-set result.

    ``surface_gaps`` are ``(surface_id, code)`` pairs so the caller's criticality policy disposes
    each absence on the surface it belongs to. ``integrity_issues`` are unauthorized or malformed
    monitors and block every class. ``density`` is recorded for the decision package and is
    deliberately compared against nothing.
    """

    monitor_ids: tuple[str, ...]
    surface_gaps: tuple[tuple[str, str], ...]
    integrity_issues: tuple[str, ...]
    reports: tuple[str, ...]
    density: float | None = None

    @property
    def satisfied(self) -> bool:
        return not self.surface_gaps and not self.integrity_issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "monitor_ids": list(self.monitor_ids),
            "surface_gaps": [list(pair) for pair in self.surface_gaps],
            "integrity_issues": list(self.integrity_issues),
            "reports": list(self.reports),
            "density": self.density,
        }


def verify_monitor_set(
    monitors: Sequence[Monitor],
    surfaces: Sequence[ResolvedSurface],
    policy: SegregationPolicy,
    *,
    resolved_backreferences: Iterable[IntentBackreference] = (),
    authority_available: bool = True,
    declared_unit_count: int = 0,
) -> MonitorSetReport:
    """Verify the phase-3 monitor set against the surfaces a change disturbed.

    ``resolved_backreferences`` are the references a provenance verifier already resolved
    against trusted phase artifacts, so a monitor cannot vouch for its own authority. A monitor
    naming a surface outside the disturbance is reported rather than judged: it belongs to the
    standing monitor set, and this decision is about one candidate.

    ``authority_available`` says whether phase-artifact provenance verified at all. When it did
    not, an unresolved monitor reference is a consequence of that absence and is recorded as a
    class-disposed gap; the provenance defect keeps its own disposition. An unresolved reference
    against *working* authority is different in kind — that is a monitor asserting something no
    signed item carries — and it blocks every class.

    ``declared_unit_count`` is whatever unit the target counts density in (lines, handlers,
    routes). It only ever produces a recorded number.
    """

    resolved = frozenset(resolved_backreferences)
    surface_gaps: list[tuple[str, str]] = []
    integrity: list[str] = []
    reports: list[str] = []

    surface_by_id = {surface.surface_id: surface for surface in surfaces}
    covered: dict[str, list[str]] = {surface_id: [] for surface_id in surface_by_id}

    seen: set[str] = set()
    monitor_ids: list[str] = []
    for monitor in monitors:
        if not monitor.monitor_id:
            integrity.append("monitor-id-missing")
            continue
        if monitor.monitor_id in seen:
            integrity.append(f"monitor-duplicate:{monitor.monitor_id}")
            continue
        seen.add(monitor.monitor_id)
        monitor_ids.append(monitor.monitor_id)

        if not monitor.surface_id:
            integrity.append(f"monitor-surface-missing:{monitor.monitor_id}")
            continue
        surface = surface_by_id.get(monitor.surface_id)
        if surface is None:
            reports.append(f"monitor-outside-disturbance:{monitor.monitor_id}")
            continue
        covered[monitor.surface_id].append(monitor.monitor_id)

        _verify_derivation(monitor, surface_gaps, integrity)
        _verify_backreference(monitor, resolved, authority_available, surface_gaps, integrity)
        _verify_authorship(monitor, surface, policy, surface_gaps, integrity)
        _verify_notification(monitor, surface_gaps)

    for surface_id in sorted(surface_by_id):
        if not covered[surface_id]:
            surface_gaps.append((surface_id, "monitor-coverage-missing"))

    density: float | None = None
    if declared_unit_count > 0:
        density = len(monitor_ids) / declared_unit_count
        # Recorded, never gated. A density target produces monitors written to raise the count.
        reports.append(f"monitor-density-recorded:{len(monitor_ids)}/{declared_unit_count}")
    elif declared_unit_count < 0:
        integrity.append("monitor-declared-unit-count-invalid")

    return MonitorSetReport(
        monitor_ids=tuple(monitor_ids),
        surface_gaps=tuple(dict.fromkeys(surface_gaps)),
        integrity_issues=tuple(dict.fromkeys(integrity)),
        reports=tuple(dict.fromkeys(reports)),
        density=density,
    )


def _verify_derivation(
    monitor: Monitor,
    surface_gaps: list[tuple[str, str]],
    integrity: list[str],
) -> None:
    if not monitor.derivation:
        surface_gaps.append(
            (monitor.surface_id, f"monitor-derivation-unrecorded:{monitor.monitor_id}")
        )
        return
    if monitor.derivation not in MONITOR_DERIVATIONS:
        integrity.append(f"monitor-derivation-unknown:{monitor.monitor_id}:{monitor.derivation}")
        return
    if monitor.derivation == MONITOR_DERIVATION_IMPLEMENTATION:
        # A change detector cannot serve as an oracle: it learned the wrongness as its baseline.
        integrity.append(f"monitor-diff-derived:{monitor.monitor_id}")


def _verify_backreference(
    monitor: Monitor,
    resolved: frozenset[IntentBackreference],
    authority_available: bool,
    surface_gaps: list[tuple[str, str]],
    integrity: list[str],
) -> None:
    reference = monitor.backreference
    if reference is None:
        surface_gaps.append(
            (monitor.surface_id, f"monitor-backreference-missing:{monitor.monitor_id}")
        )
        return
    if reference in resolved:
        return
    if not authority_available:
        surface_gaps.append(
            (monitor.surface_id, f"monitor-authority-unavailable:{monitor.monitor_id}")
        )
        return
    # Unresolvable against working authority is not absence. A monitor asserting something no
    # signed item carries is an unauthorized assertion about production.
    integrity.append(f"monitor-backreference-unresolved:{monitor.monitor_id}")


def _verify_authorship(
    monitor: Monitor,
    surface: ResolvedSurface,
    policy: SegregationPolicy,
    surface_gaps: list[tuple[str, str]],
    integrity: list[str],
) -> None:
    if not monitor.authorship:
        surface_gaps.append(
            (monitor.surface_id, f"monitor-authorship-unrecorded:{monitor.monitor_id}")
        )
        return
    if monitor.authorship not in MONITOR_AUTHORSHIPS:
        integrity.append(f"monitor-authorship-unknown:{monitor.monitor_id}:{monitor.authorship}")
        return
    if monitor.authorship == MONITOR_AUTHORSHIP_HUMAN:
        if policy.resolve_human(monitor.author_identity) is None:
            integrity.append(f"monitor-author-not-enrolled-human:{monitor.monitor_id}")
        return
    if surface.effective_criticality == CRITICALITY_CRITICAL:
        # No waiver on this class: the only instrument on a hazard surface may not be one nobody
        # can read under pressure.
        surface_gaps.append(
            (monitor.surface_id, f"critical-monitor-not-human-authored:{monitor.monitor_id}")
        )


def _verify_notification(monitor: Monitor, surface_gaps: list[tuple[str, str]]) -> None:
    if monitor.notifies_human and not monitor.actionable_conclusion.strip():
        surface_gaps.append(
            (
                monitor.surface_id,
                f"monitor-notifies-without-actionable-conclusion:{monitor.monitor_id}",
            )
        )
