"""Evidence-backed checklist gates.

The checklist definition is the gate. A required item with no result is therefore visible as a
gap, and a result marked passed is not satisfied until its content-addressed evidence binds the
exact subject and observation. The verifier is generic: phase gates, build gates, and release
gates can all use the same shape.

The module cannot prove *when* an external writer persisted an item. It makes that obligation
auditable by requiring a caller-supplied ``recorded_at`` value in the individually addressed
item evidence. The append-only manifest or artifact sink owns write ordering.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from factory_core.evidence import EvidenceIntegrity


def normalize_checklist_id(value: str) -> str:
    """Normalize opaque target-supplied ids for deterministic matching."""

    return value.strip().casefold()


def _mapping_sequence(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


@dataclass(frozen=True)
class ChecklistItemResult:
    """One observed checklist item, persisted when the observation was obtained."""

    id: str
    passed: bool
    detail: str = ""
    recorded_at: int = 0
    evidence: EvidenceIntegrity | None = None

    def authority_body(self, subject_digest: str) -> dict[str, Any]:
        """The exact fields an item's evidence must bind."""

        return {
            "checklist_item_id": normalize_checklist_id(self.id),
            "subject_digest": subject_digest,
            "passed": self.passed,
            "detail": self.detail,
            "recorded_at": self.recorded_at,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "passed": self.passed,
            "detail": self.detail,
            "recorded_at": self.recorded_at,
            "evidence": self.evidence.to_dict() if self.evidence is not None else None,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ChecklistItemResult:
        recorded_at_raw = raw.get("recorded_at", 0)
        try:
            recorded_at = int(recorded_at_raw)
        except (TypeError, ValueError):
            recorded_at = 0
        evidence_raw = raw.get("evidence")
        return cls(
            id=str(raw.get("id", "")),
            passed=bool(raw.get("passed", False)),
            detail=str(raw.get("detail", "")),
            recorded_at=recorded_at,
            evidence=EvidenceIntegrity.from_dict(
                evidence_raw if isinstance(evidence_raw, Mapping) else None
            ),
        )


@dataclass(frozen=True)
class ChecklistReport:
    """Independently inspectable checklist result.

    ``gaps`` are absences and may be disposed by a caller's criticality policy.
    ``failures`` are observed negative evidence. ``integrity_issues`` are malformed,
    duplicated, mismatched, or tampered records. Neither failures nor integrity issues are
    convertible into a waiver.
    """

    required_item_ids: tuple[str, ...]
    satisfied_item_ids: tuple[str, ...]
    gaps: tuple[str, ...]
    failures: tuple[str, ...]
    integrity_issues: tuple[str, ...]
    reports: tuple[str, ...]

    @property
    def satisfied(self) -> bool:
        return not self.gaps and not self.failures and not self.integrity_issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "required_item_ids": list(self.required_item_ids),
            "satisfied_item_ids": list(self.satisfied_item_ids),
            "gaps": list(self.gaps),
            "failures": list(self.failures),
            "integrity_issues": list(self.integrity_issues),
            "reports": list(self.reports),
        }


def verify_checklist(
    required_item_ids: Iterable[str],
    results: Sequence[ChecklistItemResult],
    subject_digest: str,
) -> ChecklistReport:
    """Verify a checklist against individually cited evidence.

    The function accumulates every defect so an unchecked item remains visible in the
    resulting manifest. Unknown result ids are reported but do not satisfy a required item.
    """

    required_list = tuple(
        normalize_checklist_id(item_id)
        for item_id in required_item_ids
        if normalize_checklist_id(item_id)
    )
    required = tuple(dict.fromkeys(required_list))
    integrity: list[str] = []
    gaps: list[str] = []
    failures: list[str] = []
    reports: list[str] = []
    satisfied: list[str] = []

    if len(required_list) != len(required):
        integrity.append("checklist-required-item-duplicate")

    indexed: dict[str, ChecklistItemResult] = {}
    for captured_result in results:
        item_id = normalize_checklist_id(captured_result.id)
        if not item_id:
            integrity.append("checklist-item-id-missing")
            continue
        if item_id in indexed:
            integrity.append(f"checklist-item-duplicate:{item_id}")
            continue
        indexed[item_id] = captured_result
        if item_id not in required:
            reports.append(f"checklist-item-outside-definition:{item_id}")

    for item_id in required:
        result_record = indexed.get(item_id)
        if result_record is None:
            gaps.append(f"checklist-item-missing:{item_id}")
            continue

        item_has_gap = False
        item_has_integrity_issue = False
        if result_record.recorded_at <= 0:
            gaps.append(f"checklist-item-recorded-at-missing:{item_id}")
            item_has_gap = True

        evidence = result_record.evidence
        if evidence is None or not evidence.present:
            gaps.append(f"checklist-item-evidence-missing:{item_id}")
            item_has_gap = True
        elif not evidence.verify():
            integrity.append(f"checklist-item-evidence-digest-mismatch:{item_id}")
            item_has_integrity_issue = True
        elif not evidence.verifies_binding(result_record.authority_body(subject_digest)):
            integrity.append(f"checklist-item-evidence-subject-mismatch:{item_id}")
            item_has_integrity_issue = True

        if not result_record.passed:
            failures.append(f"checklist-item-failed:{item_id}")
        elif not item_has_gap and not item_has_integrity_issue:
            satisfied.append(item_id)

    return ChecklistReport(
        required_item_ids=required,
        satisfied_item_ids=tuple(satisfied),
        gaps=tuple(dict.fromkeys(gaps)),
        failures=tuple(dict.fromkeys(failures)),
        integrity_issues=tuple(dict.fromkeys(integrity)),
        reports=tuple(dict.fromkeys(reports)),
    )
