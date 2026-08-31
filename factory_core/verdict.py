"""Mechanically unpersuadable verdict over a closed coverage map.

Run 1 failed at the verdict, not at the code: PASS_WITH_RISK_ACCEPTANCE asserted
"risks known and named" over territory the Tester had explicitly declared uncovered,
because the aggregation step granted summary prose epistemic authority the coverage
evidence never earned. This module is the corrective, and it fixes the mechanism
rather than the wording:

* the coverage map is the ONLY input the verdict function accepts — prose summaries,
  confidence assertions, and risk rationales are not inputs and cannot move the
  outcome;
* the verdict opens with a forced first line — "does it do the thing it was built to
  do?" — and anything but YES caps the verdict at INCOMPLETE, with no PASS variant
  reachable;
* declared-uncovered territory stays UNKNOWN until a characterization receipt clears
  it, and receipt acceptance is schema conformance plus signatures — never judgment:
  adequacy criteria are ratified inside the map itself, so territory that surfaced
  after ratification has no criterion and therefore no receipt can exist for it;
* composition is monotone: every evidence channel can only remove PASS-eligibility;
  only a valid characterization receipt restores it; no signal can launder another;
* the frame is closed and enumerated: membership is an exact-id lookup, anything not
  provably inside is outside, and outside can only shrink the verdict — enumeration
  incompleteness is safe rather than fatal.

The promotion decision remains the local-property gate beneath this layer; the
verdict is the global-property gate above it and can only be narrower. Shipping a
non-PASS verdict is not this module's concern: that is a separate, attributable
operator act, which is exactly what keeps it from happening informally.

Posture: stdlib only, pure, no clock, no disk. The caller supplies ledger positions
and the candidate address.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from factory_core.criticality import normalize_label
from factory_core.evidence import EvidenceIntegrity
from factory_core.manifest import digest_obj

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _is_sha256(value: str) -> bool:
    return bool(_SHA256_RE.fullmatch(value))

FIRST_LINE_YES = "yes"
FIRST_LINE_NO = "no"
FIRST_LINE_NOT_DEMONSTRATED = "not-demonstrated"
_FIRST_LINES = frozenset({FIRST_LINE_YES, FIRST_LINE_NO, FIRST_LINE_NOT_DEMONSTRATED})

TERRITORY_COVERED = "covered"
TERRITORY_UNCOVERED = "uncovered"
_TERRITORY_STATUSES = frozenset({TERRITORY_COVERED, TERRITORY_UNCOVERED})

TERRITORY_KIND_SCENARIO = "scenario"
TERRITORY_KIND_ORACLE = "oracle"
TERRITORY_KIND_SURFACE = "surface"
_TERRITORY_KINDS = frozenset(
    {TERRITORY_KIND_SCENARIO, TERRITORY_KIND_ORACLE, TERRITORY_KIND_SURFACE}
)

ISSUER_ROLE_TESTER = "tester"
ISSUER_ROLE_FRAME_CHECK = "frame-check"
_ISSUER_ROLES = frozenset({ISSUER_ROLE_TESTER, ISSUER_ROLE_FRAME_CHECK})

VERDICT_PASS = "pass"
VERDICT_PASS_ON_COVERED = "pass-on-covered-unknown-on-named"
VERDICT_INCOMPLETE = "incomplete"
VERDICT_BLOCK = "block"

# Monotone composition is asserted against this order: an added evidence channel may
# never move a verdict to a higher rank; only a valid characterization receipt may.
_VERDICT_RANK = {
    VERDICT_BLOCK: 0,
    VERDICT_INCOMPLETE: 1,
    VERDICT_PASS_ON_COVERED: 2,
    VERDICT_PASS: 3,
}


class VerdictError(ValueError):
    """Raised when a verdict input cannot be parsed without guessing."""


class PromotionFloorLike(Protocol):
    """The two typed facts the verdict reads from the promotion layer.

    The verdict deliberately consumes nothing else from the floor — reasons,
    reports, and surface detail are the promotion layer's own record. Binding this
    object to the *real* Gate L decision is the caller's provenance obligation (the
    command boundary reads the ``promotion_verdict.json`` that Gate L wrote).
    Declared as read-only properties so both the real (frozen) ``PromotionDecision``
    and the command-boundary ``PromotionFloor`` satisfy it structurally.
    """

    @property
    def allowed(self) -> bool: ...

    @property
    def disposition(self) -> str: ...


@dataclass(frozen=True)
class PromotionFloor:
    """A reconstructed promotion floor for the command boundary."""

    allowed: bool
    disposition: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> PromotionFloor:
        allowed = raw.get("allowed")
        if not isinstance(allowed, bool):
            raise VerdictError("promotion floor requires a boolean 'allowed'")
        return cls(
            allowed=allowed,
            disposition=_require_str(raw, "disposition", context="promotion floor"),
        )


def verdict_rank(disposition: str) -> int:
    """Total order used by the monotonicity forcing tests."""

    if disposition not in _VERDICT_RANK:
        raise VerdictError(f"unknown verdict disposition {disposition!r}")
    return _VERDICT_RANK[disposition]


def _require_str(raw: Mapping[str, Any], key: str, *, context: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise VerdictError(f"{context} requires a non-empty string {key!r}")
    return value.strip()


def _require_int(raw: Mapping[str, Any], key: str, *, context: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise VerdictError(f"{context} requires an integer {key!r}")
    return value


def _str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(str(item) for item in value)
    return (str(value),)


@dataclass(frozen=True)
class FiredProbe:
    """One probe invocation with chain-of-custody to a real-path outcome.

    A control never fired is absent: a receipt's probes must each carry the exact
    invocation and outcome addresses, and must have run on the real path. External
    data can evidence external behavior only — it can never attest an internal
    control — so the addresses here refer to the factory's own verification records.
    """

    probe_id: str
    invocation_digest: str
    outcome_digest: str
    real_path: bool = False

    def fired(self) -> bool:
        return (
            bool(normalize_label(self.probe_id))
            and _is_sha256(self.invocation_digest)
            and _is_sha256(self.outcome_digest)
            and self.real_path
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe_id": normalize_label(self.probe_id),
            "invocation_digest": self.invocation_digest,
            "outcome_digest": self.outcome_digest,
            "real_path": self.real_path,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> FiredProbe:
        return cls(
            probe_id=_require_str(raw, "probe_id", context="fired probe"),
            invocation_digest=_require_str(raw, "invocation_digest", context="fired probe"),
            outcome_digest=_require_str(raw, "outcome_digest", context="fired probe"),
            real_path=bool(raw.get("real_path", False)),
        )


@dataclass(frozen=True)
class AdequacyCriterion:
    """Ratified per-territory definition of what a characterization must contain.

    Receipt acceptance is schema conformance against this criterion, never judgment.
    A territory without a ratified criterion cannot be receipted at all — that is the
    escalation path for territory that surfaced after ratification, and it is what
    closes the thin-receipt-under-pressure back door.
    """

    territory_id: str
    required_probe_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "territory_id": normalize_label(self.territory_id),
            "required_probe_ids": sorted(
                normalize_label(probe_id)
                for probe_id in self.required_probe_ids
                if normalize_label(probe_id)
            ),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> AdequacyCriterion:
        probe_ids = _str_tuple(raw.get("required_probe_ids"))
        if not probe_ids:
            raise VerdictError("adequacy criterion requires required_probe_ids")
        return cls(
            territory_id=_require_str(raw, "territory_id", context="adequacy criterion"),
            required_probe_ids=probe_ids,
        )


@dataclass(frozen=True)
class CoverageTerritory:
    """One enumerated frame element: a scenario, oracle, or surface claim."""

    territory_id: str
    kind: str
    status: str
    declared_by: str
    declaration_position: int
    # Phase 1.5 additive join: "artifact_id:item_id" keys naming the oracle-link
    # expectations that will clear this territory. Omitted from the declaration body
    # when empty so every already-ratified declaration keeps its exact digest —
    # additive means old bytes re-derive unchanged, never re-signed.
    expectation_refs: tuple[str, ...] = ()

    def declaration_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "territory_id": normalize_label(self.territory_id),
            "kind": self.kind,
            "status": self.status,
            "declared_by": normalize_label(self.declared_by),
            "declaration_position": self.declaration_position,
        }
        if self.expectation_refs:
            body["expectation_refs"] = sorted(
                ref.strip() for ref in self.expectation_refs if ref.strip()
            )
        return body

    @property
    def declaration_digest(self) -> str:
        return digest_obj(self.declaration_body())

    def to_dict(self) -> dict[str, Any]:
        return self.declaration_body()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CoverageTerritory:
        kind = _require_str(raw, "kind", context="coverage territory")
        status = _require_str(raw, "status", context="coverage territory")
        if kind not in _TERRITORY_KINDS:
            raise VerdictError(
                f"coverage territory kind {kind!r} is not one of {sorted(_TERRITORY_KINDS)}"
            )
        if status not in _TERRITORY_STATUSES:
            raise VerdictError(
                f"coverage territory status {status!r} is not one of {sorted(_TERRITORY_STATUSES)}"
            )
        return cls(
            territory_id=_require_str(raw, "territory_id", context="coverage territory"),
            kind=kind,
            status=status,
            declared_by=_require_str(raw, "declared_by", context="coverage territory"),
            declaration_position=_require_int(
                raw, "declaration_position", context="coverage territory"
            ),
            expectation_refs=_str_tuple(raw.get("expectation_refs")),
        )


@dataclass(frozen=True)
class CoverageMap:
    """The closed, ratified enumeration the verdict computes from.

    Membership is an exact-id lookup. Anything not provably inside the map is
    outside, and outside can only shrink the verdict — so an incomplete enumeration
    is safe (it produces UNKNOWN), never permissive.
    """

    territories: tuple[CoverageTerritory, ...]
    adequacy: tuple[AdequacyCriterion, ...] = ()
    verb_ids: tuple[str, ...] = ()
    ratified_position: int = 0

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for territory in self.territories:
            key = normalize_label(territory.territory_id)
            if key in seen:
                raise VerdictError(f"duplicate coverage territory {key!r}")
            seen.add(key)
        for criterion in self.adequacy:
            if normalize_label(criterion.territory_id) not in seen:
                raise VerdictError(
                    "adequacy criterion names unknown territory "
                    f"{normalize_label(criterion.territory_id)!r}"
                )

    def territory(self, territory_id: str) -> CoverageTerritory | None:
        key = normalize_label(territory_id)
        for territory in self.territories:
            if normalize_label(territory.territory_id) == key:
                return territory
        return None

    def criterion(self, territory_id: str) -> AdequacyCriterion | None:
        key = normalize_label(territory_id)
        for criterion in self.adequacy:
            if normalize_label(criterion.territory_id) == key:
                return criterion
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "territories": [
                territory.to_dict()
                for territory in sorted(
                    self.territories, key=lambda item: normalize_label(item.territory_id)
                )
            ],
            "adequacy": [
                criterion.to_dict()
                for criterion in sorted(
                    self.adequacy, key=lambda item: normalize_label(item.territory_id)
                )
            ],
            "verb_ids": sorted(
                normalize_label(verb_id) for verb_id in self.verb_ids if normalize_label(verb_id)
            ),
            "ratified_position": self.ratified_position,
        }

    @property
    def content_digest(self) -> str:
        return digest_obj(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CoverageMap:
        territories_raw = raw.get("territories")
        if not isinstance(territories_raw, (list, tuple)) or not territories_raw:
            raise VerdictError("coverage map requires a non-empty territories list")
        return cls(
            territories=tuple(
                CoverageTerritory.from_dict(item)
                for item in territories_raw
                if isinstance(item, Mapping)
            ),
            adequacy=tuple(
                AdequacyCriterion.from_dict(item)
                for item in raw.get("adequacy", ())
                if isinstance(item, Mapping)
            ),
            verb_ids=_str_tuple(raw.get("verb_ids")),
            ratified_position=_require_int(raw, "ratified_position", context="coverage map"),
        )


@dataclass(frozen=True)
class CharacterizationReceipt:
    """Signed, probe-backed characterization of one declared-uncovered territory.

    A receipt never overrules the Tester's declaration — it supersedes it in time,
    which is why it must backreference the exact declaration and carry a later ledger
    position, and why a position at or after verdict evaluation is retroactive and
    invalid. ``observed_shape`` is descriptive for the human report; nothing in this
    module reads it to decide anything.
    """

    receipt_id: str
    territory_id: str
    backreference_digest: str
    issuer: str
    issuer_role: str
    probes: tuple[FiredProbe, ...]
    ledger_position: int
    observed_shape: str = ""
    residual_unknown: bool = False
    evidence: EvidenceIntegrity | None = None

    def authority_body(self) -> dict[str, Any]:
        return {
            "receipt_id": normalize_label(self.receipt_id),
            "territory_id": normalize_label(self.territory_id),
            "backreference_digest": self.backreference_digest,
            "issuer": normalize_label(self.issuer),
            "issuer_role": self.issuer_role,
            "probes": [probe.to_dict() for probe in self.probes],
            "ledger_position": self.ledger_position,
            "residual_unknown": self.residual_unknown,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CharacterizationReceipt:
        role = _require_str(raw, "issuer_role", context="characterization receipt")
        if role not in _ISSUER_ROLES:
            raise VerdictError(
                f"characterization receipt issuer_role {role!r} "
                f"is not one of {sorted(_ISSUER_ROLES)}"
            )
        return cls(
            receipt_id=_require_str(raw, "receipt_id", context="characterization receipt"),
            territory_id=_require_str(raw, "territory_id", context="characterization receipt"),
            backreference_digest=_require_str(
                raw, "backreference_digest", context="characterization receipt"
            ),
            issuer=_require_str(raw, "issuer", context="characterization receipt"),
            issuer_role=role,
            probes=tuple(
                FiredProbe.from_dict(item)
                for item in raw.get("probes", ())
                if isinstance(item, Mapping)
            ),
            ledger_position=_require_int(
                raw, "ledger_position", context="characterization receipt"
            ),
            observed_shape=str(raw.get("observed_shape", "")),
            residual_unknown=bool(raw.get("residual_unknown", False)),
            evidence=EvidenceIntegrity.from_dict(
                raw.get("evidence") if isinstance(raw.get("evidence"), Mapping) else None
            ),
        )


@dataclass(frozen=True)
class AssumptionRecord:
    """One dark-run assumption; the territories it touches become UNKNOWN.

    The record's prose fields (assumption, basis, decision) are for the operator's
    report. The verdict reads only the touched territory ids: an assumption can
    therefore never argue itself harmless, and a touched territory stays UNKNOWN even
    if the map declared it covered — the demonstrated path does not characterize the
    assumed surface.
    """

    assumption_id: str
    touched_territory_ids: tuple[str, ...]
    ledger_position: int
    assumption: str = ""
    basis: str = ""
    blast_radius: str = ""
    decision: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "assumption_id": normalize_label(self.assumption_id),
            "touched_territory_ids": sorted(
                normalize_label(territory_id)
                for territory_id in self.touched_territory_ids
                if normalize_label(territory_id)
            ),
            "ledger_position": self.ledger_position,
            "assumption": self.assumption,
            "basis": self.basis,
            "blast_radius": self.blast_radius,
            "decision": self.decision,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> AssumptionRecord:
        return cls(
            assumption_id=_require_str(raw, "assumption_id", context="assumption record"),
            touched_territory_ids=_str_tuple(raw.get("touched_territory_ids")),
            ledger_position=_require_int(raw, "ledger_position", context="assumption record"),
            assumption=str(raw.get("assumption", "")),
            basis=str(raw.get("basis", "")),
            blast_radius=str(raw.get("blast_radius", "")),
            decision=str(raw.get("decision", "")),
        )


@dataclass(frozen=True)
class FrameCheckResult:
    """The cold seat's binary over the exact promoted artifact.

    The seat receives the purpose sentence and live entrypoints only, chooses its own
    scenario instance at gate time, and drives the same content-addressed artifact
    promotion would ship — an artifact digest mismatch here is a staging attempt and
    blocks outright. The binary IS the forced first line; there is no discounting
    step because the verdict function does not weigh inputs.
    """

    first_line: str
    artifact_digest: str
    scenario_instance_digest: str
    evidence: EvidenceIntegrity | None = None

    def authority_body(self) -> dict[str, Any]:
        return {
            "first_line": self.first_line,
            "artifact_digest": self.artifact_digest,
            "scenario_instance_digest": self.scenario_instance_digest,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> FrameCheckResult:
        first_line = _require_str(raw, "first_line", context="frame check")
        if first_line not in _FIRST_LINES:
            raise VerdictError(
                f"frame check first_line {first_line!r} is not one of {sorted(_FIRST_LINES)}"
            )
        return cls(
            first_line=first_line,
            artifact_digest=_require_str(raw, "artifact_digest", context="frame check"),
            scenario_instance_digest=_require_str(
                raw, "scenario_instance_digest", context="frame check"
            ),
            evidence=EvidenceIntegrity.from_dict(
                raw.get("evidence") if isinstance(raw.get("evidence"), Mapping) else None
            ),
        )


@dataclass(frozen=True)
class Verdict:
    """The computed, independently inspectable global verdict."""

    first_line: str
    disposition: str
    allowed: bool
    unknown_territory_ids: tuple[str, ...]
    receipted_territory_ids: tuple[str, ...]
    outside_frame_ids: tuple[str, ...]
    reasons: tuple[str, ...]
    reports: tuple[str, ...]
    coverage_digest: str
    promotion_disposition: str
    candidate_digest: str = ""
    evaluated_position: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "first_line": self.first_line,
            "disposition": self.disposition,
            "allowed": self.allowed,
            "unknown_territory_ids": list(self.unknown_territory_ids),
            "receipted_territory_ids": list(self.receipted_territory_ids),
            "outside_frame_ids": list(self.outside_frame_ids),
            "reasons": list(self.reasons),
            "reports": list(self.reports),
            "coverage_digest": self.coverage_digest,
            "promotion_disposition": self.promotion_disposition,
            "candidate_digest": self.candidate_digest,
            "evaluated_position": self.evaluated_position,
        }


def _valid_receipt(
    receipt: CharacterizationReceipt,
    coverage: CoverageMap,
    *,
    validator: str,
    evaluated_position: int,
    reports: list[str],
    hard_reasons: list[str],
) -> str | None:
    """Return the territory id this receipt clears, or None with the defect reported.

    Every rejection path is a mechanical check. Invalid receipts leave the territory
    UNKNOWN (the verdict shrinks); tampered receipt evidence is an integrity failure
    that blocks every disposition, mirroring the promotion doctrine.
    """

    receipt_key = normalize_label(receipt.receipt_id)
    territory = coverage.territory(receipt.territory_id)
    if territory is None:
        reports.append(f"receipt-territory-outside-frame:{receipt_key}")
        return None
    territory_key = normalize_label(territory.territory_id)
    if territory.status != TERRITORY_UNCOVERED:
        reports.append(f"receipt-covers-non-gap:{receipt_key}:{territory_key}")
        return None
    if receipt.backreference_digest != territory.declaration_digest:
        reports.append(f"receipt-backreference-mismatch:{receipt_key}:{territory_key}")
        return None
    criterion = coverage.criterion(territory.territory_id)
    if criterion is None:
        reports.append(f"receipt-adequacy-unratified:{receipt_key}:{territory_key}")
        return None
    if validator and normalize_label(receipt.issuer) == normalize_label(validator):
        reports.append(f"receipt-issuer-is-validator:{receipt_key}")
        return None
    if receipt.ledger_position <= territory.declaration_position:
        reports.append(f"receipt-not-superseding:{receipt_key}:{territory_key}")
        return None
    if receipt.ledger_position >= evaluated_position:
        reports.append(f"receipt-retroactive:{receipt_key}")
        return None
    fired = {
        normalize_label(probe.probe_id) for probe in receipt.probes if probe.fired()
    }
    missing = [
        normalize_label(probe_id)
        for probe_id in criterion.required_probe_ids
        if normalize_label(probe_id) not in fired
    ]
    if missing:
        reports.extend(
            f"receipt-probe-missing:{receipt_key}:{probe_id}" for probe_id in sorted(missing)
        )
        return None
    if receipt.evidence is None or not receipt.evidence.present:
        reports.append(f"receipt-evidence-missing:{receipt_key}")
        return None
    if not receipt.evidence.verifies_binding(receipt.authority_body()):
        hard_reasons.append(f"receipt-evidence-invalid:{receipt_key}")
        return None
    if receipt.residual_unknown:
        reports.append(f"receipt-partial:{receipt_key}:{territory_key}")
        return None
    return territory_key


def compute_verdict(
    coverage: CoverageMap,
    promotion: PromotionFloorLike,
    frame_check: FrameCheckResult | None,
    *,
    candidate_digest: str,
    evaluated_position: int,
    receipts: tuple[CharacterizationReceipt, ...] = (),
    assumptions: tuple[AssumptionRecord, ...] = (),
    validator: str = "",
) -> Verdict:
    """Compute the global verdict. Pure, and deliberately unpersuadable.

    The only inputs are the ratified coverage map, the promotion decision beneath it,
    the frame-check binary, receipts, and assumption records — all typed. Free-text
    fields on any of them are carried into the report untouched and read by nothing.
    """

    hard_reasons: list[str] = []
    reasons: list[str] = []
    reports: list[str] = []

    if not promotion.allowed:
        hard_reasons.append(f"promotion-not-allowed:{promotion.disposition}")

    if not _is_sha256(candidate_digest):
        hard_reasons.append("candidate-digest-invalid")

    first_line = FIRST_LINE_NOT_DEMONSTRATED
    if frame_check is None:
        reasons.append("frame-check-missing")
    else:
        if frame_check.artifact_digest != candidate_digest:
            # The cold seat drove a different artifact than the one being promoted.
            # That is a staging attempt (or a wiring defect), not a softer NO.
            hard_reasons.append("frame-check-artifact-mismatch")
        if frame_check.evidence is None or not frame_check.evidence.present:
            reasons.append("frame-check-evidence-missing")
        elif not frame_check.evidence.verifies_binding(frame_check.authority_body()):
            hard_reasons.append("frame-check-evidence-invalid")
        else:
            first_line = frame_check.first_line

    receipted: set[str] = set()
    for receipt in receipts:
        cleared = _valid_receipt(
            receipt,
            coverage,
            validator=validator,
            evaluated_position=evaluated_position,
            reports=reports,
            hard_reasons=hard_reasons,
        )
        if cleared is not None:
            receipted.add(cleared)

    unknown: set[str] = set()
    for territory in coverage.territories:
        key = normalize_label(territory.territory_id)
        if territory.status == TERRITORY_UNCOVERED and key not in receipted:
            unknown.add(key)

    outside: set[str] = set()
    for assumption in assumptions:
        assumption_key = normalize_label(assumption.assumption_id)
        for touched in assumption.touched_territory_ids:
            touched_key = normalize_label(touched)
            if not touched_key:
                continue
            touched_territory = coverage.territory(touched_key)
            if touched_territory is None:
                outside.add(touched_key)
                unknown.add(touched_key)
                reports.append(f"assumption-outside-frame:{assumption_key}:{touched_key}")
                continue
            if touched_territory.status == TERRITORY_COVERED:
                reports.append(f"assumption-shadows-covered:{assumption_key}:{touched_key}")
            unknown.add(touched_key)
            receipted.discard(touched_key)

    if unknown:
        reasons.extend(f"unknown-territory:{territory_id}" for territory_id in sorted(unknown))
    if first_line != FIRST_LINE_YES:
        reasons.append(f"first-line-not-yes:{first_line}")

    if hard_reasons:
        disposition = VERDICT_BLOCK
    elif first_line != FIRST_LINE_YES:
        disposition = VERDICT_INCOMPLETE
    elif unknown:
        disposition = VERDICT_PASS_ON_COVERED
    else:
        disposition = VERDICT_PASS

    all_reasons = tuple(dict.fromkeys(hard_reasons + reasons))
    return Verdict(
        first_line=first_line,
        disposition=disposition,
        allowed=disposition == VERDICT_PASS,
        unknown_territory_ids=tuple(sorted(unknown)),
        receipted_territory_ids=tuple(sorted(receipted)),
        outside_frame_ids=tuple(sorted(outside)),
        reasons=all_reasons,
        reports=tuple(dict.fromkeys(reports)),
        coverage_digest=coverage.content_digest,
        promotion_disposition=promotion.disposition,
        candidate_digest=candidate_digest,
        evaluated_position=evaluated_position,
    )


_FIRST_LINE_RENDER = {
    FIRST_LINE_YES: "YES",
    FIRST_LINE_NO: "NO",
    FIRST_LINE_NOT_DEMONSTRATED: "NOT-DEMONSTRATED",
}

_DISPOSITION_RENDER = {
    VERDICT_PASS: "PASS",
    VERDICT_PASS_ON_COVERED: "PASS ON COVERED / UNKNOWN ON NAMED MASS",
    VERDICT_INCOMPLETE: "INCOMPLETE",
    VERDICT_BLOCK: "BLOCK",
}


def render_headline(verdict: Verdict) -> str:
    """Deterministic summary rendered from typed verdict fields only.

    The template is explicit conditional logic, not prose judgment: the forced first
    line always opens, the disposition line cannot exceed the computed disposition,
    and every unknown territory appears in the headline rather than a footnote. Run
    1's reporting failure — caveats in tables, confidence in prose — is structurally
    unavailable here because there is no free-text input to render from.
    """

    lines = [
        "Does it do the thing it was built to do? "
        + _FIRST_LINE_RENDER[verdict.first_line],
        "Verdict: " + _DISPOSITION_RENDER[verdict.disposition],
    ]
    if verdict.unknown_territory_ids:
        lines.append(
            "Unknown territory ("
            + str(len(verdict.unknown_territory_ids))
            + "): "
            + ", ".join(verdict.unknown_territory_ids)
        )
    if verdict.receipted_territory_ids:
        lines.append(
            "Characterized by receipt: " + ", ".join(verdict.receipted_territory_ids)
        )
    if verdict.outside_frame_ids:
        lines.append(
            "Outside the ratified frame (escalation expected): "
            + ", ".join(verdict.outside_frame_ids)
        )
    for reason in verdict.reasons:
        lines.append("Reason: " + reason)
    return "\n".join(lines)


def verdict_attestation_subject(verdict: Verdict, coverage: CoverageMap) -> dict[str, Any]:
    """Canonical body the ledger binds so the rendered summary cannot drift.

    The summary artifact must embed the digest of this subject; a downstream reader
    verifies it against the live ledger, which is what makes prose-that-exceeds-the-
    ledger detectable rather than merely discouraged.
    """

    return {
        "verdict": verdict.to_dict(),
        "coverage_digest": coverage.content_digest,
        "headline": render_headline(verdict),
    }
