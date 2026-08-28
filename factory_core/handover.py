"""Typed lane handovers and the single composed ``__DONE__``.

Run 1's most dangerous mechanism was token inflation: each seat truthfully emitted
``__DONE__`` meaning "my assignment," and the composition of true local statements
produced a false global claim of a production-operable system — with nobody lying,
so nothing to catch. Renaming the lane token is not the fix; a payload-free
``__HANDOVER__`` reproduces the identical collision one level down. The fix is a
typed handover schema whose scope is explicit and machine-checkable, plus a
composition rule:

* every lane completion carries ``{claim, evidence, scope: {completed,
  explicitly-excluded, assumed-in-scope-by-others}, preconditions_for_next}``;
* the Validator cannot silently aggregate — ``__DONE__`` is reachable only when the
  union of all handover ``completed`` sets covers the ratified verb set, so N
  handovers that each silently omit the same verb cannot compose into a global
  claim;
* ``__DONE__`` is minted by exactly one code path (``compose_done``), only over a
  PASS verdict from the unpersuadable verdict layer, and only for a named validator
  seat; the token appearing in any other lane content is an integrity violation the
  runtime detects with ``reserved_token_violation``;
* retraction is first-class: when a dependency change forces revision of a completed
  lane, a superseding record with a forcing-event backreference removes the earlier
  claim — the ledger never carries a silently false completion.

``__DONE__`` stays meaningful as the singleton precisely because it is the one token
with no scope qualifier: "no remaining scope exceptions," earned by composition over
the full upstream handover chain, never asserted.

Posture: stdlib only, pure, no clock, no disk.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from factory_core.criticality import normalize_label
from factory_core.evidence import EvidenceIntegrity
from factory_core.manifest import digest_obj
from factory_core.verdict import VERDICT_PASS, CoverageMap, Verdict

DONE_TOKEN = "__DONE__"
HANDOVER_TOKEN = "__HANDOVER__"


class HandoverError(ValueError):
    """Raised when a handover input cannot be parsed without guessing."""


def _labels(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items: tuple[Any, ...] = (value,)
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = tuple(value)
    else:
        items = (value,)
    return tuple(
        dict.fromkeys(normalize_label(str(item)) for item in items if normalize_label(str(item)))
    )


@dataclass(frozen=True)
class HandoverScope:
    """The explicit scope boundary a completion claim is true within.

    ``completed`` and ``explicitly_excluded`` are contradictory when they overlap —
    that is a malformed claim, refused at construction rather than adjudicated
    later. ``assumed_in_scope_by_others`` is the seat naming work it believes
    someone else owns; composition checks that belief against the union of actual
    completions, which is exactly where run 1's shared silent omission hid.
    """

    completed: tuple[str, ...]
    explicitly_excluded: tuple[str, ...] = ()
    assumed_in_scope_by_others: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        overlap = set(self.completed) & set(self.explicitly_excluded)
        if overlap:
            raise HandoverError(
                f"handover scope claims and excludes the same items: {sorted(overlap)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "completed": sorted(self.completed),
            "explicitly_excluded": sorted(self.explicitly_excluded),
            "assumed_in_scope_by_others": sorted(self.assumed_in_scope_by_others),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> HandoverScope:
        completed = _labels(raw.get("completed"))
        return cls(
            completed=completed,
            explicitly_excluded=_labels(raw.get("explicitly_excluded")),
            assumed_in_scope_by_others=_labels(raw.get("assumed_in_scope_by_others")),
        )


@dataclass(frozen=True)
class Handover:
    """One lane completion claim with an explicit, signed scope boundary.

    A retraction is a later record with the same ``handover_id``, ``retracts=True``,
    and a ``forcing_event_digest`` naming what invalidated the earlier claim. It
    contributes nothing to composition; it exists so the ledger's completion record
    is never silently false.
    """

    handover_id: str
    from_seat: str
    claim: str
    scope: HandoverScope
    ledger_position: int
    evidence_digests: tuple[str, ...] = ()
    preconditions_for_next: tuple[str, ...] = ()
    retracts: bool = False
    forcing_event_digest: str = ""
    evidence: EvidenceIntegrity | None = None

    def __post_init__(self) -> None:
        if self.retracts and not self.forcing_event_digest:
            raise HandoverError(
                "a retracting handover must carry the forcing_event_digest that "
                "invalidated the earlier claim"
            )
        if not self.retracts and not self.scope.completed:
            raise HandoverError("a non-retracting handover must complete at least one item")

    def authority_body(self) -> dict[str, Any]:
        return {
            "token": HANDOVER_TOKEN,
            "handover_id": normalize_label(self.handover_id),
            "from_seat": normalize_label(self.from_seat),
            "claim": self.claim,
            "scope": self.scope.to_dict(),
            "ledger_position": self.ledger_position,
            "evidence_digests": sorted(self.evidence_digests),
            "preconditions_for_next": sorted(self.preconditions_for_next),
            "retracts": self.retracts,
            "forcing_event_digest": self.forcing_event_digest,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Handover:
        handover_id = str(raw.get("handover_id", "")).strip()
        from_seat = str(raw.get("from_seat", "")).strip()
        if not handover_id or not from_seat:
            raise HandoverError("a handover requires handover_id and from_seat")
        scope_raw = raw.get("scope")
        if not isinstance(scope_raw, Mapping):
            raise HandoverError("a handover requires a scope object")
        position = raw.get("ledger_position")
        if isinstance(position, bool) or not isinstance(position, int):
            raise HandoverError("a handover requires an integer ledger_position")
        return cls(
            handover_id=handover_id,
            from_seat=from_seat,
            claim=str(raw.get("claim", "")),
            scope=HandoverScope.from_dict(scope_raw),
            ledger_position=position,
            evidence_digests=tuple(str(d) for d in raw.get("evidence_digests", ()) or ()),
            preconditions_for_next=_labels(raw.get("preconditions_for_next")),
            retracts=bool(raw.get("retracts", False)),
            forcing_event_digest=str(raw.get("forcing_event_digest", "")),
            evidence=EvidenceIntegrity.from_dict(
                raw.get("evidence") if isinstance(raw.get("evidence"), Mapping) else None
            ),
        )


@dataclass(frozen=True)
class DoneComposition:
    """The inspectable result of attempting to compose ``__DONE__``."""

    reachable: bool
    token: str
    validator: str
    covered_verbs: tuple[str, ...]
    missing_verbs: tuple[str, ...]
    reasons: tuple[str, ...]
    reports: tuple[str, ...]
    coverage_digest: str
    verdict_disposition: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "reachable": self.reachable,
            "token": self.token,
            "validator": self.validator,
            "covered_verbs": list(self.covered_verbs),
            "missing_verbs": list(self.missing_verbs),
            "reasons": list(self.reasons),
            "reports": list(self.reports),
            "coverage_digest": self.coverage_digest,
            "verdict_disposition": self.verdict_disposition,
        }


def reserved_token_violation(text: str, *, source: str = "") -> str | None:
    """Detect the reserved global token inside arbitrary lane content.

    ``__DONE__`` is issuable by exactly one code path (``compose_done``). Anywhere
    else — a lane message, an artifact, reviewed content — it is an integrity
    violation the caller must treat as a halt, not a claim. The scan is deliberately
    dumb: the cost of a false positive is a halted run and a human look; the cost of
    a missed emission is run 1.
    """

    if DONE_TOKEN in text:
        origin = normalize_label(source) or "unattributed-content"
        return f"reserved-token-emission:{origin}"
    return None


def _effective_handovers(
    handovers: tuple[Handover, ...],
    reports: list[str],
    hard_reasons: list[str],
) -> list[Handover]:
    """Resolve supersession by handover_id: the highest ledger position wins.

    A winning retraction removes the claim entirely; the report names the forcing
    event so the operator can see why a previously complete lane no longer counts.
    """

    by_id: dict[str, Handover] = {}
    for handover in handovers:
        if handover.evidence is None or not handover.evidence.present:
            hard_reasons.append(
                f"handover-evidence-missing:{normalize_label(handover.handover_id)}"
            )
            continue
        if not handover.evidence.verifies_binding(handover.authority_body()):
            hard_reasons.append(
                f"handover-evidence-invalid:{normalize_label(handover.handover_id)}"
            )
            continue
        key = normalize_label(handover.handover_id)
        current = by_id.get(key)
        if current is None or handover.ledger_position > current.ledger_position:
            by_id[key] = handover
    effective: list[Handover] = []
    for key in sorted(by_id):
        handover = by_id[key]
        if handover.retracts:
            reports.append(
                f"handover-retracted:{key}:{handover.forcing_event_digest}"
            )
            continue
        effective.append(handover)
    return effective


def compose_done(
    coverage: CoverageMap,
    handovers: tuple[Handover, ...],
    verdict: Verdict,
    *,
    validator: str,
) -> DoneComposition:
    """The only path that mints ``__DONE__``.

    Reachability requires: a named validator seat; a PASS from the unpersuadable
    verdict layer over this exact coverage map; and the union of effective handover
    completions covering the ratified verb set. Every failure is a typed reason —
    there is no aggregation step where prose could argue the gap closed.
    """

    hard_reasons: list[str] = []
    reasons: list[str] = []
    reports: list[str] = []

    validator_key = normalize_label(validator)
    if not validator_key:
        hard_reasons.append("done-requires-validator-seat")

    if verdict.coverage_digest != coverage.content_digest:
        hard_reasons.append("verdict-coverage-mismatch")
    if verdict.disposition != VERDICT_PASS:
        reasons.append(f"verdict-not-pass:{verdict.disposition}")

    effective = _effective_handovers(handovers, reports, hard_reasons)

    completed_union: set[str] = set()
    for handover in effective:
        completed_union.update(handover.scope.completed)

    verb_ids = tuple(
        dict.fromkeys(
            normalize_label(verb_id) for verb_id in coverage.verb_ids if normalize_label(verb_id)
        )
    )
    if not verb_ids:
        # A verb set nobody ratified means the scope-union check has nothing to
        # check against; an empty enumeration must not read as "nothing left".
        hard_reasons.append("coverage-map-has-no-ratified-verbs")

    missing = tuple(sorted(verb for verb in verb_ids if verb not in completed_union))
    reasons.extend(f"verb-uncovered-by-handover:{verb}" for verb in missing)

    for handover in effective:
        for assumed in handover.scope.assumed_in_scope_by_others:
            if assumed not in completed_union:
                reasons.append(
                    "assumed-but-unclaimed:"
                    f"{normalize_label(handover.handover_id)}:{assumed}"
                )

    all_reasons = tuple(dict.fromkeys(hard_reasons + reasons))
    reachable = not all_reasons
    return DoneComposition(
        reachable=reachable,
        token=DONE_TOKEN if reachable else "",
        validator=validator_key,
        covered_verbs=tuple(sorted(verb for verb in verb_ids if verb in completed_union)),
        missing_verbs=missing,
        reasons=all_reasons,
        reports=tuple(dict.fromkeys(reports)),
        coverage_digest=coverage.content_digest,
        verdict_disposition=verdict.disposition,
    )


def done_attestation_subject(
    composition: DoneComposition,
    coverage: CoverageMap,
    verdict: Verdict,
) -> dict[str, Any]:
    """Canonical body the Validator signs when (and only when) ``__DONE__`` minted.

    Binding the composition, the coverage map, and the verdict together means the
    token cannot be lifted onto a different candidate, map, or verdict later — the
    singleton claim carries its whole evidence chain.
    """

    return {
        "composition": composition.to_dict(),
        "coverage_digest": coverage.content_digest,
        "verdict_digest": digest_obj(verdict.to_dict()),
    }
