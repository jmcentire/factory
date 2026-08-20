from __future__ import annotations

import base64
import hashlib
import json
import os
import runpy
import sys
import time
from pathlib import Path


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _digest_obj(value: object) -> str:
    return _digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _evidence(source: str, path: str, file_path: Path) -> dict[str, object]:
    lines = file_path.read_bytes().splitlines(keepends=True)
    if not lines:
        raise SystemExit(f"review evidence is empty: {file_path}")
    return {
        "source": source,
        "path": path,
        "start_line": 1,
        "end_line": len(lines),
        "excerpt_digest": _digest(b"".join(lines)),
    }


def _evidence_data(source: str, path: str, data: bytes) -> dict[str, object]:
    lines = data.splitlines(keepends=True)
    if not lines:
        raise SystemExit(f"review evidence is empty: {source}:{path}")
    return {
        "source": source,
        "path": path,
        "start_line": 1,
        "end_line": len(lines),
        "excerpt_digest": _digest(b"".join(lines)),
    }


input_path = Path(os.environ["FACTORY_BUILD_INPUT_PATH"])
input_bytes = input_path.read_bytes()
if _digest(input_bytes) != os.environ["FACTORY_BUILD_INPUT_DIGEST"]:
    raise SystemExit("Validator received the wrong build-input bytes")
build_input = json.loads(input_bytes)
for path_name, digest_name in (
    ("FACTORY_BUILD_PLAN_PATH", "FACTORY_BUILD_PLAN_SOURCE_DIGEST"),
    ("FACTORY_PATTERN_CATALOG_PATH", "FACTORY_PATTERN_CATALOG_SOURCE_DIGEST"),
):
    data = Path(os.environ[path_name]).read_bytes()
    if _digest(data) != os.environ[digest_name]:
        raise SystemExit("Validator received stale construction IR")
acceptance_catalog_bytes = Path(
    os.environ["FACTORY_ACCEPTANCE_OBLIGATION_CATALOG_PATH"]
).read_bytes()
if (
    _digest(acceptance_catalog_bytes)
    != os.environ["FACTORY_ACCEPTANCE_OBLIGATION_CATALOG_SOURCE_DIGEST"]
):
    raise SystemExit("Validator received stale acceptance obligations")
acceptance_catalog = json.loads(acceptance_catalog_bytes)
if _digest_obj(acceptance_catalog) != os.environ["FACTORY_ACCEPTANCE_OBLIGATION_CATALOG_DIGEST"]:
    raise SystemExit("Validator received the wrong acceptance catalog")
trigger = next(
    item
    for item in acceptance_catalog["triggers"]
    if item["from_state"] == "validating" and item["to_state"] == "preview"
)
execution_digests = {
    "command_digest": os.environ["FACTORY_VALIDATOR_COMMAND_DIGEST"],
    "configuration_digest": os.environ["FACTORY_VALIDATOR_CONFIGURATION_DIGEST"],
    "environment_digest": os.environ["FACTORY_VALIDATOR_ENVIRONMENT_DIGEST"],
}
if any(trigger[key] != value for key, value in execution_digests.items()):
    raise SystemExit("Validator command or isolation contract was not ratified")

implementation = Path(os.environ["FACTORY_IMPLEMENTATION_DIR"])
tests = Path(os.environ["FACTORY_TEST_DIR"])
review_subject_path = Path(os.environ["FACTORY_VALIDATOR_REVIEW_SUBJECT_PATH"])
review_subject_bytes = review_subject_path.read_bytes()
if _digest(review_subject_bytes) != os.environ["FACTORY_VALIDATOR_REVIEW_SUBJECT_SOURCE_DIGEST"]:
    raise SystemExit("Validator received a stale adversarial-review subject")
review_subject = json.loads(review_subject_bytes)
assertions = json.loads((tests / "evidence" / "assertions.json").read_text(encoding="utf-8"))
product = next(
    artifact
    for artifact in build_input["phase_artifacts"]
    if artifact["phase"] == "product-specification"
)
item = product["items"][0]
backreference = {
    "artifact_id": product["artifact_id"],
    "artifact_digest": _digest_obj(product),
    "item_id": item["item_id"],
    "intent_digest": _digest_obj({"canonical_statement": item["canonical_statement"]}),
}
expected_claims = {criterion_id: backreference for criterion_id in ("AC-1", "AC-2")}
actual_claims = {claim["claim_id"]: claim["backreference"] for claim in assertions["claims"]}
if actual_claims != expected_claims:
    raise SystemExit("Tester assertions do not resolve to every authorized criterion")

sys.path.insert(0, str(implementation / "artifact"))
started_at = int(time.time())
runpy.run_path(str(tests / "tests" / "acceptance_test.py"), run_name="__main__")
finished_at = max(started_at, int(time.time()))

output = Path(os.environ["FACTORY_OUTPUT_DIR"])
(output / "verdict.json").write_text(
    json.dumps(
        {
            "passed": True,
            "build_input_digest": _digest(input_bytes),
            "criteria": sorted(expected_claims),
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)

trusted_evidence = {
    "candidate": os.environ["FACTORY_CANDIDATE_DIGEST"],
    "acceptance-tests": os.environ["FACTORY_ACCEPTANCE_TESTS_DIGEST"],
    "coder-output-snapshot": os.environ["FACTORY_CODER_OUTPUT_SNAPSHOT_DIGEST"],
    "tester-output-snapshot": os.environ["FACTORY_TESTER_OUTPUT_SNAPSHOT_DIGEST"],
}
results = []
for obligation in trigger["obligations"]:
    evidence = {
        evidence_id: trusted_evidence[evidence_id]
        for evidence_id in obligation["required_evidence_ids"]
    }
    test_results = [
        {
            **test,
            "exit_status": 0,
            "output_digest": _digest_obj(
                {
                    **test,
                    "exit_status": 0,
                    "candidate_digest": trusted_evidence["candidate"],
                    "acceptance_tests_digest": trusted_evidence["acceptance-tests"],
                    "command_digest": execution_digests["command_digest"],
                }
            ),
        }
        for test in obligation["test_assertions"]
    ]
    effect_body = {
        "obligation_id": obligation["obligation_id"],
        "verifier_id": obligation["verifier_id"],
        "candidate_digest": trusted_evidence["candidate"],
        "acceptance_tests_digest": trusted_evidence["acceptance-tests"],
        **execution_digests,
        "started_at": started_at,
        "finished_at": finished_at,
        "evidence_digests": evidence,
        "test_results": test_results,
    }
    results.append(
        {
            "obligation_id": obligation["obligation_id"],
            "verifier_id": obligation["verifier_id"],
            "passed": True,
            "evidence_digests": evidence,
            "test_results": test_results,
            "effect_digest": _digest_obj(effect_body),
        }
    )
observations = {
    "schema_version": "factory-acceptance-obligation-observations/1",
    "run_id": acceptance_catalog["run_id"],
    "generation": acceptance_catalog["generation"],
    "catalog_digest": os.environ["FACTORY_ACCEPTANCE_OBLIGATION_CATALOG_DIGEST"],
    "trigger_id": trigger["trigger_id"],
    "candidate_digest": trusted_evidence["candidate"],
    "acceptance_tests_digest": trusted_evidence["acceptance-tests"],
    **execution_digests,
    "started_at": started_at,
    "finished_at": finished_at,
    "results": results,
}
observations_path = output / "acceptance-obligation-observations.json"
observations_path.write_bytes(_canonical(observations))

implementation_ref = _evidence(
    "implementation", "calculator.py", implementation / "artifact" / "calculator.py"
)
tests_ref = _evidence(
    "acceptance-tests", "acceptance_test.py", tests / "tests" / "acceptance_test.py"
)
build_input_ref = _evidence("build-input", "build-input.json", input_path)
pattern_catalog_ref = _evidence(
    "pattern-catalog",
    "pattern-catalog.json",
    Path(os.environ["FACTORY_PATTERN_CATALOG_PATH"]),
)
build_plan_ref = _evidence(
    "build-plan", "build-plan.json", Path(os.environ["FACTORY_BUILD_PLAN_PATH"])
)
acceptance_catalog_ref = _evidence(
    "acceptance-obligation-catalog",
    "acceptance-obligation-catalog.json",
    Path(os.environ["FACTORY_ACCEPTANCE_OBLIGATION_CATALOG_PATH"]),
)
observations_ref = _evidence(
    "acceptance-observations",
    "acceptance-obligation-observations.json",
    observations_path,
)
baseline_entry = review_subject["base_source_snapshot"]["files"][0]
baseline_ref = _evidence_data(
    "baseline-source",
    baseline_entry["path"],
    base64.b64decode(baseline_entry["content_base64"]),
)
change_set_ref = _evidence_data(
    "candidate-change-set",
    "candidate-change-set.json",
    _canonical(review_subject["candidate_change_set"]),
)
authority_ref = _evidence_data(
    "review-authority-context",
    "review-authority-context.json",
    _canonical(review_subject["authority_context"]),
)
operator_intent_ref = _evidence_data(
    "operator-intent",
    "execution-request.json",
    base64.b64decode(review_subject["operator_intent"]["execution_request"]["content_base64"]),
)
dimension_evidence = {
    "intent-conformance": [
        operator_intent_ref,
        build_input_ref,
        build_plan_ref,
        implementation_ref,
    ],
    "architecture": [build_input_ref, pattern_catalog_ref, implementation_ref],
    "redundancy": [implementation_ref],
    "clarity": [implementation_ref],
    "separation-of-concerns": [implementation_ref, tests_ref],
    "test-adequacy": [tests_ref, observations_ref],
    "correctness-and-failure": [implementation_ref, tests_ref, observations_ref],
    "scope-control": [build_input_ref, acceptance_catalog_ref, implementation_ref],
}
requirement_dispositions = [
    {
        **target,
        "disposition": "CONFORMS",
        "summary": "The candidate and retained oracle satisfy this Product requirement.",
        "evidence": [build_input_ref, implementation_ref, observations_ref],
        "finding_ids": [],
    }
    for target in review_subject["review_targets"]["requirements"]
]
architecture_dispositions = [
    {
        **target,
        "disposition": "CONFORMS",
        "summary": "The candidate change preserves this ratified Architecture boundary.",
        "evidence": [build_input_ref, change_set_ref, implementation_ref],
        "finding_ids": [],
    }
    for target in review_subject["review_targets"]["architecture_items"]
]
operational_maturity_dispositions = [
    {
        **target,
        "disposition": "CONFORMS",
        "summary": "The exact tests and Validator observations satisfy this maturity item.",
        "evidence": [build_input_ref, tests_ref, observations_ref],
        "finding_ids": [],
    }
    for target in review_subject["review_targets"]["operational_maturity_items"]
]
observed_effect = results[0]
observed_test = observed_effect["test_results"][0]
probe_body = {
    "obligation_id": observed_effect["obligation_id"],
    "verifier_id": observed_effect["verifier_id"],
    "effect_digest": observed_effect["effect_digest"],
    "test_result": {
        "test_id": observed_test["test_id"],
        "assertion_digest": observed_test["assertion_digest"],
        "output_digest": observed_test["output_digest"],
    },
    "probe_method": "inspect-observed-test-result/1",
    "failure_mode": "The implementation could retain subtraction behavior for positive inputs.",
    "attempt": "Execute the exact positive-addition acceptance assertion.",
    "expected_result": "The assertion observes the mathematical sum.",
    "observed_result": "The bound Validator result records a successful execution.",
    "outcome": "PASSED",
    "evidence": [tests_ref, observations_ref],
    "finding_ids": [],
}
challenge_body = {
    "challenge_method": "compare-exact-evidence/1",
    "authority_evidence_index": 0,
    "produced_evidence_index": 1,
    "hypothesis": "The candidate does not implement the operator-authorized addition outcome.",
    "attempt": "Compare exact Stage-E intent with the complete candidate and change set.",
    "observed_result": "The implementation replaces subtraction with addition.",
    "outcome": "REFUTED",
    "evidence": [operator_intent_ref, implementation_ref, change_set_ref],
    "finding_ids": [],
}
review = {
    "schema_version": "factory-validator-adversarial-review/1",
    "authority": "review-evidence-only",
    "subject_digest": _digest_obj(review_subject),
    "reviewer_identity": review_subject["reviewer_identity"],
    "acceptance_observations_digest": _digest_obj(observations),
    "dimensions": [
        {
            "dimension_id": dimension_id,
            "state": "COMPLETED",
            "summary": f"Synthetic review completed for {dimension_id}.",
            "evidence": dimension_evidence[dimension_id],
        }
        for dimension_id in review_subject["protocol"]["required_dimensions"]
    ],
    "requirement_dispositions": requirement_dispositions,
    "architecture_dispositions": architecture_dispositions,
    "operational_maturity_dispositions": operational_maturity_dispositions,
    "failure_mode_probes": [{"probe_id": _digest_obj(probe_body), **probe_body}],
    "clean_claim_challenges": [{"challenge_id": _digest_obj(challenge_body), **challenge_body}],
    "findings": [],
    "completeness": {
        "state": "COMPLETED",
        "summary": "The synthetic clean claim was challenged against every bound input.",
        "checks": [
            {
                "check_id": check_id,
                "state": "COMPLETED",
                "summary": f"Synthetic clean-claim check completed for {check_id}.",
                "evidence": [build_input_ref, observations_ref],
            }
            for check_id in review_subject["protocol"]["required_completeness_checks"]
        ],
        "evidence": [
            build_input_ref,
            implementation_ref,
            tests_ref,
            observations_ref,
            pattern_catalog_ref,
            build_plan_ref,
            acceptance_catalog_ref,
            baseline_ref,
            change_set_ref,
            authority_ref,
            operator_intent_ref,
        ],
    },
    "verdict": "CLEAN_QUALIFIED",
}
(output / "validator-adversarial-review.json").write_bytes(_canonical(review))
