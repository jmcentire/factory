"""factory_core.completeness — a neutral inventory-row status lattice + launch-readiness.

This is the generic extraction of the "make a completion claim falsifiable" discipline
proven on the first consuming target. That target's implementation parses that target's
behavior catalog, data inventory, POA&M, and open-questions markdown, and hardcodes that
target's launch rule. Here, none of those parsers are present: the **inventory rows are
inputs**, surfaced through the adapter seam (``KnowledgeAdapter.inventory_rows``), and the
readiness rule is expressed over a neutral row-status lattice. (A compliance-sourced row
seam is a possible future addition, but is deliberately not part of the current surface.)
This core owns only the target-agnostic parts: the status lattice, the
aggregation, the summary counts, and the launch-readiness predicate.

What is generic (here) vs. what is per-target data:

  * Generic (this module): the neutral :class:`RowStatus` lattice
    (``GAP < PARTIAL < DECLARED < PROVED``, plus the terminal ``EXCUSED`` off-ramp), the
    :class:`InventoryRow` and :class:`Inventory` shapes, per-dimension and per-status summary
    counts, and the falsifiable :func:`launch_ready` predicate ("every row is proved or
    excused; no open residual").
  * Per-target data (supplied through the seams): the rows themselves — which behaviors,
    routes, data paths, or residuals exist, which source produced each, and each row's current
    status. The parsers that read the target's source documents are impurity behind the
    adapter seams. This core names no document, no service, and no target dimension.

Posture (matching the sibling modules): stdlib only, side-effect free at import, no clock, no
disk-reading, no target contact.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

# --------------------------------------------------------------------------- #
# The neutral row-status lattice
# --------------------------------------------------------------------------- #

#: A row is a GAP (nothing yet), PARTIAL (in progress), DECLARED (mapped/claimed but not
#: proved), or PROVED (proved at the referenced revision — the only completing rung). EXCUSED
#: is a terminal off-ramp: a row that is intentionally out of scope (an external/organizational
#: dependency, a by-design internal), explicitly closed without needing proof. The lattice is
#: neutral: the core assigns these rungs no target meaning beyond their launch-readiness role.
STATUS_GAP = "GAP"
STATUS_PARTIAL = "PARTIAL"
STATUS_DECLARED = "DECLARED"
STATUS_PROVED = "PROVED"
STATUS_EXCUSED = "EXCUSED"

#: Progress ordering of the non-terminal rungs. EXCUSED is deliberately NOT ranked here — it is
#: not "more complete" than PROVED, it is a separate terminal disposition (see ``is_complete``).
STATUS_ORDER: tuple[str, ...] = (STATUS_GAP, STATUS_PARTIAL, STATUS_DECLARED, STATUS_PROVED)

_RANK = {status: rank for rank, status in enumerate(STATUS_ORDER)}

#: Every status the lattice recognizes. An unknown status string is coerced to ``GAP`` on the
#: way in (fail-closed: an unrecognized row reads as the least-complete rung, never as done).
KNOWN_STATUSES: frozenset[str] = frozenset(STATUS_ORDER) | {STATUS_EXCUSED}


def normalize_status(status: str) -> str:
    """Coerce an arbitrary status string to a lattice rung. Unknown -> ``GAP`` (fail-closed)."""
    candidate = status.strip().upper()
    return candidate if candidate in KNOWN_STATUSES else STATUS_GAP


def is_complete(status: str) -> bool:
    """A row completes iff it is PROVED (proved at revision) or EXCUSED (out of scope).

    DECLARED is explicitly NOT complete: a claim without proof does not close a row. This is
    the falsifiability rule — a completion claim survives only when backed by proof or by an
    explicit, terminal excuse.
    """
    return normalize_status(status) in (STATUS_PROVED, STATUS_EXCUSED)


def status_rank(status: str) -> int:
    """Progress rank of a non-terminal rung (GAP=0 .. PROVED=3). EXCUSED ranks as PROVED for
    the purpose of "at least this complete" comparisons, since both are terminal-complete."""
    normalized = normalize_status(status)
    if normalized == STATUS_EXCUSED:
        return _RANK[STATUS_PROVED]
    return _RANK[normalized]


def meet(a: str, b: str) -> str:
    """The lattice meet (greatest lower bound) of two rungs: the LEAST-complete of the two.

    Used to aggregate a row that must satisfy several dimensions at once — the row is only as
    complete as its weakest dimension. EXCUSED meets down to the other operand's rung (an
    excused dimension does not drag a real one up, nor pull it down).
    """
    na, nb = normalize_status(a), normalize_status(b)
    if na == STATUS_EXCUSED:
        return nb
    if nb == STATUS_EXCUSED:
        return na
    return na if _RANK[na] <= _RANK[nb] else nb


# --------------------------------------------------------------------------- #
# Neutral inventory row + inventory (what the adapter seams return)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class InventoryRow:
    """One falsifiable claim in the completeness ledger.

    ``dimension`` groups rows (behavior, route, data-path, residual, external-action — the
    target's terms; opaque to the core). ``id`` is a stable opaque row id; ``status`` is a
    lattice rung; ``source`` is a free-form pointer to the committed material that backs the
    row; ``detail`` is free-form. The status is normalized to a lattice rung on the way in.
    """

    id: str
    dimension: str = ""
    status: str = STATUS_GAP
    source: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", normalize_status(self.status))

    @property
    def complete(self) -> bool:
        return is_complete(self.status)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> InventoryRow:
        return cls(
            id=str(raw.get("id", "")).strip(),
            dimension=str(raw.get("dimension", "")).strip(),
            status=str(raw.get("status", STATUS_GAP)),
            source=str(raw.get("source", "")),
            detail=str(raw.get("detail", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "status": self.status}
        if self.dimension:
            out["dimension"] = self.dimension
        if self.source:
            out["source"] = self.source
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass(frozen=True)
class DimensionSummary:
    dimension: str
    total: int
    complete: int
    by_status: dict[str, int]

    @property
    def open(self) -> int:
        return self.total - self.complete

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "total": self.total,
            "complete": self.complete,
            "open": self.open,
            "by_status": dict(self.by_status),
        }


@dataclass(frozen=True)
class Inventory:
    """The composed completeness ledger: a flat set of neutral rows across dimensions.

    The core owns the aggregation, the summary counts, and the launch-readiness predicate; the
    target owns the source documents and the parsers that produced the rows (behind the seams).
    """

    rows: tuple[InventoryRow, ...] = ()

    @classmethod
    def from_dicts(cls, raws: Iterable[Mapping[str, Any]]) -> Inventory:
        return cls(rows=tuple(InventoryRow.from_dict(r) for r in raws))

    def dimensions(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for row in self.rows:
            seen.setdefault(row.dimension, None)
        return tuple(seen)

    def open_rows(self) -> tuple[InventoryRow, ...]:
        """Every row that has not reached a terminal-complete rung — the falsification set."""
        return tuple(row for row in self.rows if not row.complete)

    def dimension_summary(self, dimension: str) -> DimensionSummary:
        rows = [r for r in self.rows if r.dimension == dimension]
        by_status = Counter(r.status for r in rows)
        return DimensionSummary(
            dimension=dimension,
            total=len(rows),
            complete=sum(1 for r in rows if r.complete),
            by_status=dict(sorted(by_status.items())),
        )

    def summary(self) -> dict[str, Any]:
        by_status = Counter(r.status for r in self.rows)
        return {
            "total": len(self.rows),
            "complete": sum(1 for r in self.rows if r.complete),
            "open": len(self.open_rows()),
            "by_status": dict(sorted(by_status.items())),
            "by_dimension": {
                dim: self.dimension_summary(dim).to_dict() for dim in self.dimensions()
            },
        }


# --------------------------------------------------------------------------- #
# The falsifiable launch-readiness predicate
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class LaunchReadiness:
    """The launch-evidence verdict over a composed inventory.

    ``ready`` is true iff every row is terminal-complete (PROVED or EXCUSED) — no open GAP,
    PARTIAL, or DECLARED row remains. ``blocking`` lists the open rows (the exact reasons it is
    red); ``blocking_by_dimension`` counts them per dimension so a caller can see where the
    residual is. This is deliberately red-by-default: an empty inventory is *vacuously* ready
    only when the caller asserts there is nothing to prove — see :func:`launch_ready`.
    """

    ready: bool
    total: int
    complete: int
    blocking: tuple[InventoryRow, ...]

    @property
    def blocking_by_dimension(self) -> dict[str, int]:
        counts: Counter[str] = Counter(row.dimension for row in self.blocking)
        return dict(sorted(counts.items()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "total": self.total,
            "complete": self.complete,
            "open": self.total - self.complete,
            "blocking": [row.to_dict() for row in self.blocking],
            "blocking_by_dimension": self.blocking_by_dimension,
        }


def launch_ready(inventory: Inventory, *, require_nonempty: bool = True) -> LaunchReadiness:
    """Falsifiable launch-readiness: green only when every row is proved or excused.

    ``require_nonempty`` (default True) guards the vacuous-truth trap: an inventory with no
    rows is NOT ready, because "nothing to prove" almost always means "nothing was enumerated
    yet," not "everything is done." Pass ``require_nonempty=False`` only when an empty
    inventory is a legitimately complete state the caller intends.
    """
    blocking = inventory.open_rows()
    complete = len(inventory.rows) - len(blocking)
    ready = not blocking
    if require_nonempty and not inventory.rows:
        ready = False
    return LaunchReadiness(
        ready=ready,
        total=len(inventory.rows),
        complete=complete,
        blocking=blocking,
    )
