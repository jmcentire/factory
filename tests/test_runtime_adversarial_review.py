from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory_core.manifest import digest_bytes, digest_obj
from factory_runtime.adversarial_review import (
    REQUIRED_COMPLETENESS_CHECKS,
    REQUIRED_REVIEW_DIMENSIONS,
    AdversarialReviewError,
    build_validator_review_subject,
    canonical_document_bytes,
    load_canonical_review_report,
    retain_validator_adversarial_review,
    verify_validator_adversarial_review,
)
from factory_runtime.snapshot import tree_digest


def _digest(label: str) -> str:
    return digest_bytes(label.encode())


def _reference(source: str, path: str, data: bytes) -> dict[str, object]:
    lines = data.splitlines(keepends=True)
    return {
        "source": source,
        "path": path,
        "start_line": 1,
        "end_line": len(lines),
        "excerpt_digest": digest_bytes(b"".join(lines)),
    }


def _fixture(tmp_path: Path) -> tuple[dict, dict, dict, dict[str, Path]]:
    implementation = tmp_path / "implementation"
    tests = tmp_path / "tests"
    implementation.mkdir(parents=True)
    tests.mkdir(parents=True)
    implementation_bytes = b"def add(left, right):\n    return left + right\n"
    tests_bytes = b"from candidate import add\nassert add(2, 3) == 5\n"
    build_input_bytes = b'{"phase_artifacts":[]}\n'
    pattern_catalog_bytes = b'{"patterns":[]}\n'
    build_plan_bytes = b'{"steps":[]}\n'
    acceptance_catalog_bytes = b'{"obligations":[]}\n'
    observations = {"passed": True, "tests": ["addition"]}
    observations_bytes = canonical_document_bytes(observations)
    paths = {
        "implementation": implementation / "candidate.py",
        "tests": tests / "acceptance_test.py",
        "build_input": tmp_path / "build-input.json",
        "pattern_catalog": tmp_path / "pattern-catalog.json",
        "build_plan": tmp_path / "build-plan.json",
        "acceptance_catalog": tmp_path / "acceptance-obligation-catalog.json",
        "observations": tmp_path / "acceptance-obligation-observations.json",
    }
    paths["implementation"].write_bytes(implementation_bytes)
    paths["tests"].write_bytes(tests_bytes)
    paths["build_input"].write_bytes(build_input_bytes)
    paths["pattern_catalog"].write_bytes(pattern_catalog_bytes)
    paths["build_plan"].write_bytes(build_plan_bytes)
    paths["acceptance_catalog"].write_bytes(acceptance_catalog_bytes)
    paths["observations"].write_bytes(observations_bytes)
    subject = build_validator_review_subject(
        run_id="run-1",
        generation=1,
        target_digest=_digest("target"),
        target_state_digest=_digest("target-state"),
        resolved_commit="commit-1",
        resolved_tree="tree-1",
        reviewer_identity="agent:validator",
        build_input_digest=digest_bytes(build_input_bytes),
        pattern_catalog_digest=digest_obj(json.loads(pattern_catalog_bytes)),
        pattern_catalog_source_digest=digest_bytes(pattern_catalog_bytes),
        build_plan_digest=digest_obj(json.loads(build_plan_bytes)),
        build_plan_source_digest=digest_bytes(build_plan_bytes),
        phase_artifact_digests={
            "product-specification": _digest("product"),
            "architecture": _digest("architecture"),
            "operational-maturity": _digest("operations"),
        },
        acceptance_obligation_catalog_digest=digest_obj(
            json.loads(acceptance_catalog_bytes)
        ),
        acceptance_obligation_catalog_source_digest=digest_bytes(
            acceptance_catalog_bytes
        ),
        candidate_digest=tree_digest(implementation),
        acceptance_tests_digest=tree_digest(tests),
        coder_output_snapshot_digest=_digest("coder-snapshot"),
        tester_output_snapshot_digest=_digest("tester-snapshot"),
        command_digest=_digest("command"),
        configuration_digest=_digest("configuration"),
        environment_digest=_digest("environment"),
    )
    evidence = {
        "implementation": _reference(
            "implementation", "candidate.py", implementation_bytes
        ),
        "tests": _reference("acceptance-tests", "acceptance_test.py", tests_bytes),
        "build_input": _reference("build-input", "build-input.json", build_input_bytes),
        "pattern_catalog": _reference(
            "pattern-catalog", "pattern-catalog.json", pattern_catalog_bytes
        ),
        "build_plan": _reference("build-plan", "build-plan.json", build_plan_bytes),
        "acceptance_catalog": _reference(
            "acceptance-obligation-catalog",
            "acceptance-obligation-catalog.json",
            acceptance_catalog_bytes,
        ),
        "observations": _reference(
            "acceptance-observations",
            "acceptance-obligation-observations.json",
            observations_bytes,
        ),
    }
    report = {
        "schema_version": "factory-validator-adversarial-review/1",
        "authority": "review-evidence-only",
        "subject_digest": digest_obj(subject),
        "reviewer_identity": "agent:validator",
        "acceptance_observations_digest": digest_obj(observations),
        "dimensions": [
            {
                "dimension_id": dimension,
                "state": "COMPLETED",
                "summary": f"Reviewed {dimension}.",
                "evidence": [evidence["implementation"], evidence["build_input"]],
            }
            for dimension in REQUIRED_REVIEW_DIMENSIONS
        ],
        "findings": [],
        "completeness": {
            "state": "COMPLETED",
            "summary": "Attempted to disprove the clean claim.",
            "checks": [
                {
                    "check_id": check_id,
                    "state": "COMPLETED",
                    "summary": f"Completed {check_id}.",
                    "evidence": [evidence["build_input"], evidence["observations"]],
                }
                for check_id in REQUIRED_COMPLETENESS_CHECKS
            ],
            "evidence": [
                evidence["tests"],
                evidence["observations"],
                evidence["pattern_catalog"],
                evidence["build_plan"],
                evidence["acceptance_catalog"],
            ],
        },
        "verdict": "CLEAN_QUALIFIED",
    }
    return subject, report, observations, paths


def _verify(tmp_path: Path, subject: dict, report: dict, observations: dict, paths: dict):
    return verify_validator_adversarial_review(
        report,
        subject=subject,
        reviewer_identity="agent:validator",
        acceptance_observations=observations,
        implementation_root=paths["implementation"].parent,
        tests_root=paths["tests"].parent,
        build_input_path=paths["build_input"],
        pattern_catalog_path=paths["pattern_catalog"],
        build_plan_path=paths["build_plan"],
        acceptance_catalog_path=paths["acceptance_catalog"],
        acceptance_observations_path=paths["observations"],
    )


def test_clean_review_binds_every_dimension_and_exact_evidence(tmp_path: Path) -> None:
    subject, report, observations, paths = _fixture(tmp_path)

    verified = _verify(tmp_path, subject, report, observations, paths)

    assert verified.passed is True
    assert verified.subject_digest == digest_obj(subject)
    assert verified.report_digest == digest_obj(report)


def test_review_refuses_missing_dimension_and_mutated_evidence(tmp_path: Path) -> None:
    subject, report, observations, paths = _fixture(tmp_path)
    report["dimensions"] = report["dimensions"][:-1]
    with pytest.raises(AdversarialReviewError, match="validation failed|dimensions"):
        _verify(tmp_path, subject, report, observations, paths)

    subject, report, observations, paths = _fixture(tmp_path / "mutation")
    paths["implementation"].write_text("def add(left, right):\n    return 0\n")
    with pytest.raises(AdversarialReviewError, match="implementation tree|excerpt digest"):
        _verify(tmp_path, subject, report, observations, paths)


def test_surviving_blocking_finding_rederives_block(tmp_path: Path) -> None:
    subject, report, observations, paths = _fixture(tmp_path)
    evidence = report["dimensions"][0]["evidence"]
    identity = {
        "dimension_id": "intent-conformance",
        "severity": "blocking",
        "statement": "The implementation contradicts the product requirement.",
        "consequence": "The requested outcome is absent.",
        "evidence": evidence,
    }
    report["findings"] = [
        {
            "finding_id": digest_obj(identity),
            **identity,
            "refutation": {
                "state": "SURVIVES",
                "summary": "The cited requirement and implementation remain contradictory.",
            },
        }
    ]
    report["verdict"] = "BLOCK"

    verified = _verify(tmp_path, subject, report, observations, paths)

    assert verified.passed is False
    assert verified.verdict == "BLOCK"


def test_incomplete_check_preserves_failure_class_and_blocks_clean_claim(
    tmp_path: Path,
) -> None:
    subject, report, observations, paths = _fixture(tmp_path)
    report["completeness"]["checks"][5]["state"] = "TIMEOUT"
    report["completeness"]["checks"][5]["summary"] = (
        "The independent provider did not finish before its bounded deadline."
    )
    report["completeness"]["state"] = "TIMEOUT"
    report["completeness"]["summary"] = "The clean claim is incomplete after timeout."
    report["verdict"] = "INCOMPLETE"

    verified = _verify(tmp_path, subject, report, observations, paths)

    assert verified.passed is False
    assert verified.verdict == "INCOMPLETE"


def test_review_refuses_a_subject_with_a_different_protocol_digest(tmp_path: Path) -> None:
    subject, report, observations, paths = _fixture(tmp_path)
    subject["protocol"]["protocol_digest"] = _digest("different-protocol")
    report["subject_digest"] = digest_obj(subject)

    with pytest.raises(AdversarialReviewError, match="wrong protocol contract"):
        _verify(tmp_path, subject, report, observations, paths)


def test_report_must_be_canonical_and_retention_is_idempotent(tmp_path: Path) -> None:
    subject, report, observations, paths = _fixture(tmp_path)
    report_path = tmp_path / "review.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(AdversarialReviewError, match="not canonical"):
        load_canonical_review_report(report_path)
    report_path.write_bytes(canonical_document_bytes(report))
    loaded = load_canonical_review_report(report_path)
    verified = _verify(tmp_path, subject, loaded, observations, paths)

    first = retain_validator_adversarial_review(tmp_path / "runs", "run-1", verified)
    second = retain_validator_adversarial_review(tmp_path / "runs", "run-1", verified)

    assert first == second
    retained = (
        tmp_path
        / "runs"
        / "run-1"
        / "evidence"
        / "validator-adversarial-reviews"
        / verified.subject_digest.removeprefix("sha256:")
    )
    assert (retained / "subject.json").read_bytes() == canonical_document_bytes(subject)
