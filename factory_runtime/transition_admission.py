"""TRANSITION_ADMISSION — declarative admission data both state paths consume.

Plan 4.1c, built incrementally: each admission axis moves out of the write-path
elif chain and its derive-side twin INTO one schema-version-keyed row here, and
both paths consume the same row — deleting the two-path-drift class one axis at
a time instead of betting the whole state machine on a single rewrite.

The stopping rule that keeps the drift class dead: released versions' rows are
FROZEN — never edited — and a digest pin over the row data turns any edit red;
current-version behavior changes land as a new keyed row.

First migrated axis: authority-nonce counting. The two inline computations had
already drifted when this module was extracted (the write path counted INTAKE
only; the derive path counted TARGET_RESOLUTION_AUTHORIZED or INTAKE) — the
exact defect this table exists to close.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Destinations whose transition consumes one intake/resolution authority nonce.
_AUTHORITY_NONCE_BASE_STATES = frozenset(
    {"target-resolution-authorized", "intake"}
)


@dataclass(frozen=True)
class _NonceAdmissionRow:
    """One schema version's authority-nonce counting rule (frozen once released)."""

    base_states: frozenset[str]
    #: nonce generations tolerated per phase ratification: single-seat records 1,
    #: dual-ratified history recorded 2, pre-nonce legacy recorded 0.
    phase_extras: tuple[int, ...]
    #: extra nonces tolerated per activation (catalog / test-change) from
    #: dual-ratified history's validator nonce.
    activation_dual_extra: bool


TRANSITION_ADMISSION: dict[str, _NonceAdmissionRow] = {
    # factory-run/5 — current. 4.1b single-seat authority with all three retained
    # ledger generations tolerated.
    "factory-run/5": _NonceAdmissionRow(
        base_states=_AUTHORITY_NONCE_BASE_STATES,
        phase_extras=(0, 1, 2),
        activation_dual_extra=True,
    ),
    # factory-run/4 — released; FROZEN. v4 ledgers replay under exactly these
    # rules forever.
    "factory-run/4": _NonceAdmissionRow(
        base_states=_AUTHORITY_NONCE_BASE_STATES,
        phase_extras=(0, 1, 2),
        activation_dual_extra=True,
    ),
}


def allowed_authority_nonce_counts(
    *,
    schema_version: str,
    destination: str,
    phase_key: bool,
    catalog_activation: bool,
    test_change_activation: bool,
) -> frozenset[int]:
    """The one answer both the write path and the derive path consume.

    An unknown schema version fails closed by resolving to the current row —
    the caller has already refused unknown versions before admission counting.
    """

    row = TRANSITION_ADMISSION.get(schema_version, TRANSITION_ADMISSION["factory-run/5"])
    base = (
        (1 if destination in row.base_states else 0)
        + (1 if catalog_activation else 0)
        + (1 if test_change_activation else 0)
    )
    allowed = {base}
    if row.activation_dual_extra:
        dual_extras = int(bool(catalog_activation)) + int(bool(test_change_activation))
        for extra in range(1, dual_extras + 1):
            allowed.add(base + extra)
    if phase_key:
        for extra in row.phase_extras:
            allowed.add(base + extra)
    return frozenset(allowed)
