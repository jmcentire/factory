"""Evidence-backed checklist tests."""

from __future__ import annotations

from dataclasses import replace

from factory_core.checklist import ChecklistItemResult, verify_checklist
from factory_core.evidence import EvidenceIntegrity
from factory_core.manifest import digest_obj

SUBJECT = digest_obj({"candidate": "one"})


def _result(
    item_id: str,
    *,
    passed: bool = True,
    recorded_at: int = 10,
) -> ChecklistItemResult:
    unsigned = ChecklistItemResult(
        id=item_id,
        passed=passed,
        detail="observed result",
        recorded_at=recorded_at,
    )
    body = unsigned.authority_body(SUBJECT)
    return replace(
        unsigned,
        evidence=EvidenceIntegrity(body=body, claimed_digest=digest_obj(body)),
    )


def test_all_required_items_need_individually_bound_evidence() -> None:
    report = verify_checklist(("build", "tests"), (_result("build"), _result("tests")), SUBJECT)

    assert report.satisfied is True
    assert report.satisfied_item_ids == ("build", "tests")
    assert report.gaps == ()


def test_unchecked_item_and_late_summary_without_item_evidence_remain_visible_gaps() -> None:
    uncited = ChecklistItemResult(
        id="tests",
        passed=True,
        detail="remembered as green",
        recorded_at=0,
    )
    report = verify_checklist(("build", "tests"), (_result("build"), uncited), SUBJECT)

    assert report.satisfied is False
    assert "checklist-item-recorded-at-missing:tests" in report.gaps
    assert "checklist-item-evidence-missing:tests" in report.gaps
    assert "tests" not in report.satisfied_item_ids


def test_failed_item_is_negative_even_with_valid_evidence() -> None:
    report = verify_checklist(("tests",), (_result("tests", passed=False),), SUBJECT)

    assert report.satisfied is False
    assert report.failures == ("checklist-item-failed:tests",)
    assert report.integrity_issues == ()


def test_wrong_subject_or_tampered_evidence_is_integrity_failure() -> None:
    valid = _result("tests")
    assert valid.evidence is not None
    wrong_subject_body = valid.authority_body(digest_obj({"candidate": "other"}))
    wrong_subject = replace(
        valid,
        evidence=EvidenceIntegrity(
            body=wrong_subject_body,
            claimed_digest=digest_obj(wrong_subject_body),
        ),
    )
    tampered = replace(
        valid,
        evidence=EvidenceIntegrity(
            body=valid.evidence.body,
            claimed_digest=digest_obj({"not": "the evidence body"}),
        ),
    )

    wrong_report = verify_checklist(("tests",), (wrong_subject,), SUBJECT)
    tamper_report = verify_checklist(("tests",), (tampered,), SUBJECT)

    assert "checklist-item-evidence-subject-mismatch:tests" in wrong_report.integrity_issues
    assert "checklist-item-evidence-digest-mismatch:tests" in tamper_report.integrity_issues


def test_duplicate_result_cannot_satisfy_a_checklist_by_ordering() -> None:
    report = verify_checklist(("tests",), (_result("tests"), _result("TESTS")), SUBJECT)

    assert report.satisfied is False
    assert "checklist-item-duplicate:tests" in report.integrity_issues
