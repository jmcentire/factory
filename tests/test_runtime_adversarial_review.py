from __future__ import annotations

import base64
import copy
import json
import os
import subprocess
from pathlib import Path

import pytest

import factory_runtime.adversarial_review as adversarial_review_module
from factory_core.manifest import digest_bytes, digest_obj
from factory_core.provenance import (
    PHASE_ARCHITECTURE,
    PHASE_OPERATIONAL_MATURITY,
    PHASE_PRODUCT_SPECIFICATION,
    IntentItem,
    PhaseArtifact,
)
from factory_runtime.adversarial_review import (
    REQUIRED_COMPLETENESS_CHECKS,
    REQUIRED_REVIEW_DIMENSIONS,
    AdversarialReviewError,
    build_review_authority_context,
    build_validator_review_subject,
    canonical_document_bytes,
    load_canonical_review_report,
    retain_validator_adversarial_review,
    verify_validator_adversarial_review,
)
from factory_runtime.candidate_diff import build_candidate_review_context
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


def _git(arguments: list[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=cwd, capture_output=True, check=False, text=True
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _fixture(tmp_path: Path) -> tuple[dict, dict, dict, dict[str, Path]]:
    implementation = tmp_path / "implementation"
    tests = tmp_path / "tests"
    implementation.mkdir(parents=True)
    tests.mkdir(parents=True)
    implementation_bytes = b"def add(left, right):\n    return left + right\n"
    tests_bytes = b"from candidate import add\nassert add(2, 3) == 5\n"
    target_digest = _digest("target")
    artifacts = (
        PhaseArtifact(
            artifact_id="product",
            phase=PHASE_PRODUCT_SPECIFICATION,
            version="1",
            source_digest=_digest("product-source"),
            human_ratifier="human:owner",
            validator_ratifier="agent:validator",
            items=(
                IntentItem(
                    item_id="product:add",
                    canonical_statement="The product adds two integers.",
                ),
            ),
        ),
        PhaseArtifact(
            artifact_id="architecture",
            phase=PHASE_ARCHITECTURE,
            version="1",
            source_digest=_digest("architecture-source"),
            human_ratifier="human:owner",
            validator_ratifier="agent:validator",
            items=(
                IntentItem(
                    item_id="architecture:function",
                    canonical_statement="Expose addition through one focused function.",
                ),
            ),
        ),
        PhaseArtifact(
            artifact_id="operations",
            phase=PHASE_OPERATIONAL_MATURITY,
            version="1",
            source_digest=_digest("operations-source"),
            human_ratifier="human:owner",
            validator_ratifier="agent:validator",
            items=(
                IntentItem(
                    item_id="operations:addition-test",
                    canonical_statement="Acceptance evidence proves representative addition.",
                ),
            ),
        ),
    )
    phase_artifact_digests = {artifact.phase: artifact.content_digest for artifact in artifacts}
    build_input = {
        "schema_version": "factory-build-input/1",
        "run_id": "run-1",
        "target_digest": target_digest,
        "phase_artifacts": [artifact.body() for artifact in artifacts],
    }
    build_input_bytes = json.dumps(build_input, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    pattern_catalog_bytes = b'{"patterns":[]}\n'
    build_plan_bytes = b'{"steps":[]}\n'
    acceptance_catalog_bytes = b'{"obligations":[]}\n'
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
    source = tmp_path / "source"
    source.mkdir()
    _git(["init", "-q"], cwd=source)
    baseline_bytes = b"def add(left, right):\n    return left - right\n"
    (source / "candidate.py").write_bytes(baseline_bytes)
    _git(["add", "."], cwd=source)
    _git(
        [
            "-c",
            "user.name=Factory Test",
            "-c",
            "user.email=factory@example.test",
            "commit",
            "-qm",
            "baseline",
        ],
        cwd=source,
    )
    resolved_commit = _git(["rev-parse", "HEAD"], cwd=source)
    resolved_tree = _git(["rev-parse", "HEAD^{tree}"], cwd=source)
    object_store = tmp_path / "objects.git"
    _git(["clone", "-q", "--bare", str(source), str(object_store)])
    candidate_digest = tree_digest(implementation)
    assertion_digest = digest_obj({"test_id": "addition", "expectation": "add(2, 3) returns 5"})
    output_digest = digest_obj({"exit_status": 0, "output": "addition passed"})
    effect_digest = digest_obj(
        {
            "obligation_id": "addition-obligation",
            "test_id": "addition",
            "assertion_digest": assertion_digest,
            "output_digest": output_digest,
        }
    )
    observations = {
        "schema_version": "factory-acceptance-obligation-observations/1",
        "run_id": "run-1",
        "generation": 1,
        "catalog_digest": digest_obj(json.loads(acceptance_catalog_bytes)),
        "trigger_id": "fixture-trigger",
        "candidate_digest": candidate_digest,
        "acceptance_tests_digest": tree_digest(tests),
        "command_digest": _digest("command"),
        "configuration_digest": _digest("configuration"),
        "environment_digest": _digest("environment"),
        "started_at": 1,
        "finished_at": 2,
        "results": [
            {
                "obligation_id": "addition-obligation",
                "verifier_id": "validator-test-execution-v1",
                "passed": True,
                "evidence_digests": {"candidate": candidate_digest},
                "test_results": [
                    {
                        "test_id": "addition",
                        "assertion_digest": assertion_digest,
                        "exit_status": 0,
                        "output_digest": output_digest,
                    }
                ],
                "effect_digest": effect_digest,
            }
        ],
    }
    observations_bytes = canonical_document_bytes(observations)
    paths["observations"].write_bytes(observations_bytes)
    base_source_snapshot, candidate_change_set = build_candidate_review_context(
        target_state={
            "object_store": str(object_store),
            "resolved_commit": resolved_commit,
            "resolved_tree": resolved_tree,
            "subpath": "",
        },
        candidate_root=implementation,
        candidate_digest=candidate_digest,
        construction_mode="regenerate",
    )
    verbatim_request = "Implement integer addition and prove it with acceptance evidence."
    execution_request = {
        "schema_version": "factory-execution-request/1",
        "request_id": "request-1",
        "run_id": "run-1",
        "repository_id": "fixture-repository",
        "generation": 1,
        "target_manifest_digest": target_digest,
        "target_state_digest": _digest("target-state"),
        "resolved_commit": resolved_commit,
        "proposed_by": "human:owner",
        "verbatim_request": verbatim_request,
        "verbatim_request_digest": digest_bytes(verbatim_request.encode("utf-8")),
        "requested_outcome": "Integer addition is correct and acceptance-tested.",
        "surfaces": [
            {
                "surface_id": "addition",
                "proposed_criticality": "critical",
                "reason": "The function is the fixture's public behavior.",
            }
        ],
        "created_at": 1,
    }
    execution_request_bytes = canonical_document_bytes(execution_request)
    checkpoint = {
        "checkpoint": "fixture",
        "execution_request_digest": digest_obj(execution_request),
    }
    checkpoint_bytes = canonical_document_bytes(checkpoint)
    configuration_bytes = b'{"configuration":"fixture"}\n'
    authority_context = build_review_authority_context(
        resume_checkpoint_digest=digest_obj(checkpoint),
        resume_checkpoint_source_digest=digest_bytes(checkpoint_bytes),
        resume_checkpoint_bytes=checkpoint_bytes,
        configuration_sources={"factory": configuration_bytes},
        expected_configuration_digests={"factory": digest_bytes(configuration_bytes)},
        changed_existing_tests=(),
        test_change_artifacts={},
        test_change_sources={},
    )
    subject = build_validator_review_subject(
        run_id="run-1",
        generation=1,
        target_digest=target_digest,
        target_state_digest=_digest("target-state"),
        resolved_commit=resolved_commit,
        resolved_tree=resolved_tree,
        reviewer_identity="agent:validator",
        base_source_snapshot=base_source_snapshot,
        candidate_change_set=candidate_change_set,
        authority_context=authority_context,
        execution_request_bytes=execution_request_bytes,
        build_input=build_input,
        build_input_digest=digest_obj(build_input),
        pattern_catalog_digest=digest_obj(json.loads(pattern_catalog_bytes)),
        pattern_catalog_source_digest=digest_bytes(pattern_catalog_bytes),
        build_plan_digest=digest_obj(json.loads(build_plan_bytes)),
        build_plan_source_digest=digest_bytes(build_plan_bytes),
        phase_artifact_digests=phase_artifact_digests,
        acceptance_obligation_catalog_digest=digest_obj(json.loads(acceptance_catalog_bytes)),
        acceptance_obligation_catalog_source_digest=digest_bytes(acceptance_catalog_bytes),
        candidate_digest=candidate_digest,
        acceptance_tests_digest=tree_digest(tests),
        coder_output_snapshot_digest=_digest("coder-snapshot"),
        tester_output_snapshot_digest=_digest("tester-snapshot"),
        command_digest=_digest("command"),
        configuration_digest=_digest("configuration"),
        environment_digest=_digest("environment"),
    )
    evidence = {
        "implementation": _reference("implementation", "candidate.py", implementation_bytes),
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
        "baseline": _reference("baseline-source", "candidate.py", baseline_bytes),
        "change_set": _reference(
            "candidate-change-set",
            "candidate-change-set.json",
            canonical_document_bytes(candidate_change_set),
        ),
        "authority": _reference(
            "review-authority-context",
            "review-authority-context.json",
            canonical_document_bytes(authority_context),
        ),
        "operator_intent": _reference(
            "operator-intent", "execution-request.json", execution_request_bytes
        ),
    }
    requirement_dispositions = [
        {
            **target,
            "disposition": "CONFORMS",
            "summary": "The implementation and executable oracle satisfy this requirement.",
            "evidence": [evidence["build_input"], evidence["implementation"]],
            "finding_ids": [],
        }
        for target in subject["review_targets"]["requirements"]
    ]
    architecture_dispositions = [
        {
            **target,
            "disposition": "CONFORMS",
            "summary": "The focused implementation preserves the ratified boundary.",
            "evidence": [evidence["build_input"], evidence["change_set"]],
            "finding_ids": [],
        }
        for target in subject["review_targets"]["architecture_items"]
    ]
    operational_maturity_dispositions = [
        {
            **target,
            "disposition": "CONFORMS",
            "summary": "The retained test and observation prove the ratified oracle item.",
            "evidence": [
                evidence["build_input"],
                evidence["tests"],
                evidence["observations"],
            ],
            "finding_ids": [],
        }
        for target in subject["review_targets"]["operational_maturity_items"]
    ]
    probe_body = {
        "obligation_id": "addition-obligation",
        "verifier_id": "validator-test-execution-v1",
        "effect_digest": effect_digest,
        "test_result": {
            "test_id": "addition",
            "assertion_digest": assertion_digest,
            "output_digest": output_digest,
        },
        "failure_mode": "The implementation could preserve the baseline subtraction defect.",
        "attempt": "Execute the retained acceptance oracle with representative operands.",
        "expected_result": "The oracle reports that 2 + 3 equals 5.",
        "observed_result": "The retained observation records a passing addition assertion.",
        "outcome": "PASSED",
        "evidence": [evidence["tests"], evidence["observations"]],
        "finding_ids": [],
    }
    challenge_body = {
        "hypothesis": "The candidate does not implement the operator's requested addition.",
        "attempt": "Compare exact operator intent with the candidate implementation.",
        "observed_result": "The candidate returns the sum of both operands.",
        "outcome": "REFUTED",
        "evidence": [evidence["operator_intent"], evidence["implementation"]],
        "finding_ids": [],
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
                "evidence": (
                    [
                        evidence["operator_intent"],
                        evidence["build_input"],
                        evidence["implementation"],
                    ]
                    if dimension == "intent-conformance"
                    else [evidence["implementation"], evidence["build_input"]]
                ),
            }
            for dimension in REQUIRED_REVIEW_DIMENSIONS
        ],
        "requirement_dispositions": requirement_dispositions,
        "architecture_dispositions": architecture_dispositions,
        "operational_maturity_dispositions": operational_maturity_dispositions,
        "failure_mode_probes": [{"probe_id": digest_obj(probe_body), **probe_body}],
        "clean_claim_challenges": [{"challenge_id": digest_obj(challenge_body), **challenge_body}],
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
                evidence["baseline"],
                evidence["change_set"],
                evidence["authority"],
                evidence["operator_intent"],
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


@pytest.mark.parametrize(
    "field",
    (
        "requirement_dispositions",
        "architecture_dispositions",
        "operational_maturity_dispositions",
    ),
)
def test_review_refuses_omitted_ratified_item_disposition(
    tmp_path: Path,
    field: str,
) -> None:
    subject, report, observations, paths = _fixture(tmp_path)
    report[field] = []

    with pytest.raises(AdversarialReviewError, match="validation failed|dispositions"):
        _verify(tmp_path, subject, report, observations, paths)


@pytest.mark.parametrize(
    ("field", "error"),
    (
        ("failure_mode_probes", "cover every exact observed"),
        ("clean_claim_challenges", "re-derive as INCOMPLETE"),
    ),
)
def test_vacuous_clean_claim_is_refused(tmp_path: Path, field: str, error: str) -> None:
    subject, report, observations, paths = _fixture(tmp_path)
    report[field] = []

    with pytest.raises(AdversarialReviewError, match=error):
        _verify(tmp_path, subject, report, observations, paths)


def test_intent_lens_must_cite_operator_request_and_ratified_requirements(
    tmp_path: Path,
) -> None:
    subject, report, observations, paths = _fixture(tmp_path)
    report["dimensions"][0]["evidence"] = [report["dimensions"][0]["evidence"][-1]]

    with pytest.raises(AdversarialReviewError, match="exact operator intent"):
        _verify(tmp_path, subject, report, observations, paths)


def test_item_conformance_requires_produced_behavior_not_only_a_test_definition(
    tmp_path: Path,
) -> None:
    subject, report, observations, paths = _fixture(tmp_path)
    report["requirement_dispositions"][0]["evidence"] = [
        report["requirement_dispositions"][0]["evidence"][0],
        report["failure_mode_probes"][0]["evidence"][0],
    ]

    with pytest.raises(AdversarialReviewError, match="produced behavior"):
        _verify(tmp_path, subject, report, observations, paths)


def test_probe_must_bind_an_exact_observed_test_result(tmp_path: Path) -> None:
    subject, report, observations, paths = _fixture(tmp_path)
    probe = report["failure_mode_probes"][0]
    probe["test_result"]["output_digest"] = _digest("unobserved-output")
    body = {key: value for key, value in probe.items() if key != "probe_id"}
    probe["probe_id"] = digest_obj(body)

    with pytest.raises(AdversarialReviewError, match="unobserved acceptance test result"):
        _verify(tmp_path, subject, report, observations, paths)


def test_executed_test_probe_requires_its_exact_test_evidence(tmp_path: Path) -> None:
    subject, report, observations, paths = _fixture(tmp_path)
    probe = report["failure_mode_probes"][0]
    probe["evidence"] = [probe["evidence"][1]]
    probe["probe_id"] = digest_obj(
        {key: value for key, value in probe.items() if key != "probe_id"}
    )

    with pytest.raises(AdversarialReviewError, match="selected test oracle"):
        _verify(tmp_path, subject, report, observations, paths)


def test_non_test_probe_binds_exact_effect_without_fake_test_evidence(tmp_path: Path) -> None:
    subject, report, observations, paths = _fixture(tmp_path)
    observed_effect = observations["results"][0]
    observed_effect["verifier_id"] = "validator-exact-value-v1"
    observed_effect["test_results"] = []
    observed_effect["effect_digest"] = _digest("exact-value-effect")
    observations_bytes = canonical_document_bytes(observations)
    paths["observations"].write_bytes(observations_bytes)
    observations_ref = _reference(
        "acceptance-observations",
        "acceptance-obligation-observations.json",
        observations_bytes,
    )
    report["acceptance_observations_digest"] = digest_obj(observations)
    probe = report["failure_mode_probes"][0]
    retained_observations_ref = probe["evidence"][1]
    retained_observations_ref.clear()
    retained_observations_ref.update(observations_ref)
    probe.update(
        {
            "verifier_id": observed_effect["verifier_id"],
            "effect_digest": observed_effect["effect_digest"],
            "test_result": None,
            "evidence": [observations_ref],
        }
    )
    probe["probe_id"] = digest_obj(
        {key: value for key, value in probe.items() if key != "probe_id"}
    )

    verified = _verify(tmp_path, subject, report, observations, paths)

    assert verified.passed is True


def test_failure_mode_probes_must_cover_every_observed_effect(tmp_path: Path) -> None:
    subject, report, observations, paths = _fixture(tmp_path)
    second_effect = copy.deepcopy(observations["results"][0])
    second_effect["obligation_id"] = "second-obligation"
    second_effect["effect_digest"] = _digest("second-observed-effect")
    observations["results"].append(second_effect)
    observations_bytes = canonical_document_bytes(observations)
    paths["observations"].write_bytes(observations_bytes)
    observations_ref = _reference(
        "acceptance-observations",
        "acceptance-obligation-observations.json",
        observations_bytes,
    )
    retained_observations_ref = report["failure_mode_probes"][0]["evidence"][1]
    retained_observations_ref.clear()
    retained_observations_ref.update(observations_ref)
    report["acceptance_observations_digest"] = digest_obj(observations)
    probe = report["failure_mode_probes"][0]
    probe["probe_id"] = digest_obj(
        {key: value for key, value in probe.items() if key != "probe_id"}
    )

    with pytest.raises(AdversarialReviewError, match="cover every exact observed"):
        _verify(tmp_path, subject, report, observations, paths)


def test_operational_violation_accepts_test_adequacy_finding_and_refuses_other_lens(
    tmp_path: Path,
) -> None:
    subject, report, observations, paths = _fixture(tmp_path)
    disposition = report["operational_maturity_dispositions"][0]
    identity = {
        "dimension_id": "test-adequacy",
        "severity": "should-fix",
        "statement": "The oracle misses a ratified failure boundary.",
        "consequence": "A required failure can escape validation.",
        "evidence": disposition["evidence"],
    }
    finding_id = digest_obj(identity)
    report["findings"] = [{"finding_id": finding_id, **identity}]
    disposition["disposition"] = "VIOLATES"
    disposition["finding_ids"] = [finding_id]
    report["verdict"] = "CHANGES_REQUESTED"

    verified = _verify(tmp_path, subject, report, observations, paths)
    assert verified.verdict == "CHANGES_REQUESTED"

    subject, report, observations, paths = _fixture(tmp_path / "wrong-lens")
    disposition = report["operational_maturity_dispositions"][0]
    identity = {
        "dimension_id": "redundancy",
        "severity": "should-fix",
        "statement": "The oracle misses a ratified failure boundary.",
        "consequence": "A required failure can escape validation.",
        "evidence": disposition["evidence"],
    }
    finding_id = digest_obj(identity)
    report["findings"] = [{"finding_id": finding_id, **identity}]
    disposition["disposition"] = "VIOLATES"
    disposition["finding_ids"] = [finding_id]
    report["verdict"] = "CHANGES_REQUESTED"

    with pytest.raises(AdversarialReviewError, match="wrong review dimension"):
        _verify(tmp_path, subject, report, observations, paths)


def test_review_refuses_operator_intent_substituted_after_checkpoint(tmp_path: Path) -> None:
    subject, report, observations, paths = _fixture(tmp_path)
    entry = subject["operator_intent"]["execution_request"]
    alternate = json.loads(base64.b64decode(entry["content_base64"]))
    alternate["requested_outcome"] = "Substituted outcome."
    alternate_bytes = canonical_document_bytes(alternate)
    alternate_digest = digest_obj(alternate)
    entry.update(
        {
            "declared_digest": alternate_digest,
            "content_digest": digest_bytes(alternate_bytes),
            "content_base64": base64.b64encode(alternate_bytes).decode("ascii"),
            "content_utf8": alternate_bytes.decode("utf-8"),
        }
    )
    subject["operator_intent"]["execution_request_digest"] = alternate_digest
    subject["operator_intent"]["execution_request_source_digest"] = digest_bytes(alternate_bytes)
    report["subject_digest"] = digest_obj(subject)

    with pytest.raises(AdversarialReviewError, match="externally anchored checkpoint"):
        _verify(tmp_path, subject, report, observations, paths)


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
        }
    ]
    report["verdict"] = "BLOCK"

    verified = _verify(tmp_path, subject, report, observations, paths)

    assert verified.passed is False
    assert verified.verdict == "BLOCK"


def test_report_cannot_self_attest_a_finding_refutation(tmp_path: Path) -> None:
    subject, report, observations, paths = _fixture(tmp_path)
    evidence = report["dimensions"][0]["evidence"]
    identity = {
        "dimension_id": "intent-conformance",
        "severity": "blocking",
        "statement": "The required outcome is absent.",
        "consequence": "The user receives the wrong behavior.",
        "evidence": evidence,
    }
    report["findings"] = [
        {
            "finding_id": digest_obj(identity),
            **identity,
            "refutation": {"state": "REFUTED", "summary": "I disagree with myself."},
        }
    ]
    report["verdict"] = "CLEAN_QUALIFIED"

    with pytest.raises(AdversarialReviewError, match="validation failed|refutation"):
        _verify(tmp_path, subject, report, observations, paths)


@pytest.mark.parametrize("field", ("base_source_snapshot", "authority_context"))
def test_review_refuses_embedded_baseline_or_authority_mutation(
    tmp_path: Path,
    field: str,
) -> None:
    subject, report, observations, paths = _fixture(tmp_path)
    mutated = copy.deepcopy(subject)
    if field == "base_source_snapshot":
        mutated[field]["files"][0]["content_base64"] = "eA=="
    else:
        mutated[field]["configuration_sources"][0]["content_base64"] = "eA=="
    report["subject_digest"] = digest_obj(mutated)

    with pytest.raises(AdversarialReviewError, match="digest|changed|content"):
        _verify(tmp_path, mutated, report, observations, paths)


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


@pytest.mark.parametrize("swapped_artifact", ("subject", "report"))
def test_retention_refuses_a_destination_swapped_after_successful_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    swapped_artifact: str,
) -> None:
    subject, report, observations, paths = _fixture(tmp_path)
    verified = _verify(tmp_path, subject, report, observations, paths)
    original_link = os.link

    def swap_after_link(
        source: object,
        destination: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        original_link(source, destination, *args, **kwargs)
        destination_path = Path(destination)  # type: ignore[arg-type]
        should_swap = (
            swapped_artifact == "subject" and destination_path.name == "subject.json"
        ) or (
            swapped_artifact == "report"
            and destination_path.name == f"{verified.report_digest.removeprefix('sha256:')}.json"
        )
        if should_swap:
            destination_path.unlink()
            destination_path.write_bytes(b"{}\n")

    monkeypatch.setattr(adversarial_review_module.os, "link", swap_after_link)

    with pytest.raises(AdversarialReviewError):
        retain_validator_adversarial_review(tmp_path / "runs", "run-1", verified)
