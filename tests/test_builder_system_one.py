"""Independent composition checks for automatic, metadata-only SystemOne builder review.

The source-free contract requires one advisory provider observation per retained checkpoint;
it cannot verify an unseen PR or replace a test result. Real Git/report/validation paths run.
Only the existing provider request seam is replaced; this does not qualify the remote service.
"""

import json
import sys

import pytest

from harness import jev_screen, run_record, validate_candidate
from tests.test_builder_provenance import BUILDER, PR_LINE, TRAILERS, commit
from tests.test_builder_provenance import builder_run as builder_run


@pytest.fixture(autouse=True)
def no_ambient_provider_keys(monkeypatch):
    for name in ("TYPESAFE_API_KEY", "JMC_JEV_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def response(payload):
    return {"model": jev_screen.MODEL_ID,
            "answers": {key: {"type": "noul", "noul": 0.01} for key in payload["questions"]}}


def test_builder_review_sends_only_metadata_and_unavailable_never_claims_an_observation(
    builder_run,
):
    _, root, repo, doc = builder_run
    (repo / "source.txt").write_text("PRIVATE_SOURCE_SENTINEL\n")
    candidate = commit(repo, "PRIVATE_COMMIT_SUBJECT\n\n" + TRAILERS)
    pr = {"url": "https://github.com/fixture-owner/product/pull/7", "headRefOid": candidate,
          "body": f"PRIVATE_PR_BODY\n\n{PR_LINE}\nFactoryRun: task"}
    report = run_record.check_builder(root, doc, repo, candidate, pr=pr)
    calls = []

    def observe(payload):
        calls.append(payload)
        return response(payload)

    review = jev_screen.builder_review(report, call=observe)
    assert len(calls) == 1 and review["status"] == "observed"
    assert review["model"] == calls[0]["model"] == jev_screen.MODEL_ID
    assert review["input"] == calls[0]
    assert review["input_digest"] == jev_screen.digest(calls[0])
    assert calls[0]["state"] == {
        "expected_builder": {"name": "Factory", "version": BUILDER}, "candidate": candidate,
        "commit_trailers": {candidate: TRAILERS.splitlines()}, "pr_verification": "checked",
        "pr_head": candidate, "pr_lines": [PR_LINE, "FactoryRun: task"],
    }
    serialized = json.dumps(calls[0])
    assert all(private not in serialized for private in
               ("PRIVATE_SOURCE_SENTINEL", "PRIVATE_COMMIT_SUBJECT", "PRIVATE_PR_BODY", str(repo)))
    unknown = run_record.check_builder(root, {**doc, "factory_version": {}}, repo, candidate)
    skipped = jev_screen.builder_review(
        unknown, call=lambda _: pytest.fail("unknown identity sent"),
    )
    assert skipped["status"] == "unavailable" and skipped["reason"] == "missing-builder-identity"
    missing_key = jev_screen.builder_review(report)
    assert missing_key["status"] == "unavailable" and missing_key["reason"] == "missing-key"
    malformed = jev_screen.builder_review(report, call=lambda _: {})
    assert malformed["status"] == "unavailable"


def test_endgame_automatically_reviews_once_without_verifying_absent_pr_or_changing_exit(
    builder_run, monkeypatch,
):
    runs, root, repo, _ = builder_run
    candidate = commit(repo, "Task implementation without attribution")
    calls = []
    monkeypatch.setattr(jev_screen, "request", lambda payload: (calls.append(payload),
                                                               response(payload))[1])
    for count, exit_code in enumerate((0, 7), start=1):
        code, attempt = validate_candidate.validate(
            runs, "task", repo, candidate,
            [sys.executable, "-c", "from pathlib import Path; "
             "assert Path('source.txt').read_text(); " + f"raise SystemExit({exit_code})"],
        )
        result_path = attempt / "result.json"
        result = json.loads(result_path.read_text())
        assert code == result["exit_code"] == exit_code and len(calls) == count
        provenance = result["builder_provenance"]
        assert provenance["status"] == "mismatch" and provenance["pr_verification"] == "not-checked"
        assert provenance["system_one"]["status"] == "observed"
        assert provenance["system_one"]["input"]["state"]["pr_verification"] == "not-checked"
        notices = [json.loads(line) for line in
                   (root / "orchestrator/inbox.jsonl").read_text().splitlines()]
        notice = next(row for row in notices if str(result_path) in row["text"])
        assert "not-checked" in notice["text"] and "observed" in notice["text"]


def test_retained_checkpoint_records_checked_pr_and_safe_provider_failure(builder_run, monkeypatch):
    _, root, repo, doc = builder_run
    candidate = commit(repo, "Attributed change\n\n" + TRAILERS)
    pr = {"url": "https://github.com/fixture-owner/product/pull/7", "headRefOid": candidate,
          "body": f"{PR_LINE}\nFactoryRun: task"}
    report = run_record.check_builder(root, doc, repo, candidate, pr=pr)
    calls = []

    def unavailable(payload):
        calls.append(payload)
        raise RuntimeError("SECRET_PROVIDER_EXCEPTION_DO_NOT_RETAIN")

    monkeypatch.setattr(jev_screen, "request", unavailable)
    path = run_record.retain_provenance(root, report)
    retained = json.loads(path.read_text())
    assert len(calls) == 1 and retained["pr_verification"] == "checked"
    assert retained["system_one"]["status"] == "unavailable"
    assert retained["system_one"]["reason"] == "provider-unavailable"
    inbox = (root / "orchestrator/inbox.jsonl").read_text()
    assert "SECRET_PROVIDER_EXCEPTION_DO_NOT_RETAIN" not in path.read_text() + inbox
    notice = json.loads(inbox)
    assert str(path) in notice["text"] and "checked" in notice["text"]
    assert "unavailable" in notice["text"]
