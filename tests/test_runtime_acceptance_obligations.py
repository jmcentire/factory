from __future__ import annotations

import os
import stat
import sys
from copy import deepcopy
from pathlib import Path

import pytest

import factory_runtime.acceptance_obligations as obligations_module
from factory_core.manifest import digest_obj
from factory_runtime.acceptance_obligations import (
    AcceptanceObligationCatalog,
    AcceptanceObligationError,
    capture_validator_execution,
    derive_acceptance_obligation_report,
    validator_execution_digests,
    verify_acceptance_obligation_report,
)

DIGEST_A = "sha256:" + ("a" * 64)
DIGEST_B = "sha256:" + ("b" * 64)
DIGEST_C = "sha256:" + ("c" * 64)
DIGEST_D = "sha256:" + ("d" * 64)
COMMAND = (sys.executable, "--exact")
COMMAND_DIGEST, CONFIGURATION_DIGEST, ENVIRONMENT_DIGEST = validator_execution_digests(COMMAND)
PHASES = {
    "product-specification": "sha256:" + ("1" * 64),
    "architecture": "sha256:" + ("2" * 64),
    "operational-maturity": "sha256:" + ("3" * 64),
}
ASSERTION = digest_obj({"test_id": "acceptance-1", "expected": "works"})


def test_identical_acceptance_evidence_is_fsynced_before_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "acceptance-obligations" / ("a" * 64) / "catalog.json"
    path.parent.mkdir(parents=True)
    content = b'{"catalog":"exact"}\n'
    path.write_bytes(content)
    real_fsync = os.fsync
    synced: list[tuple[str, int]] = []

    def track_fsync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        synced.append(("file" if stat.S_ISREG(metadata.st_mode) else "directory", metadata.st_ino))
        real_fsync(descriptor)

    monkeypatch.setattr(obligations_module.os, "fsync", track_fsync)

    obligations_module._write_once_or_identical(path, content)

    assert ("file", path.stat().st_ino) in synced
    assert ("directory", path.parent.stat().st_ino) in synced
    assert ("directory", path.parent.parent.stat().st_ino) in synced


def test_validator_execution_identity_changes_when_same_path_bytes_are_replaced(
    tmp_path: Path,
) -> None:
    runner = tmp_path / "validator.py"
    runner.write_text("print('ratified')\n", encoding="utf-8")
    command = (sys.executable, str(runner))

    first = validator_execution_digests(command, trusted_paths=(runner,))
    replacement = tmp_path / "replacement.py"
    replacement.write_text("print('substituted')\n", encoding="utf-8")
    replacement.replace(runner)
    second = validator_execution_digests(command, trusted_paths=(runner,))

    assert first[0] != second[0]
    assert first[1] != second[1]
    assert first[2] == second[2]


def test_validator_execution_identity_binds_trusted_input_tree_bytes(tmp_path: Path) -> None:
    trusted = tmp_path / "validator-runtime"
    trusted.mkdir()
    module = trusted / "review_policy.py"
    module.write_text("POLICY = 'ratified'\n", encoding="utf-8")
    command = (sys.executable, "-c", "raise SystemExit(0)")

    first = validator_execution_digests(command, trusted_paths=(trusted,))
    module.write_text("POLICY = 'substituted'\n", encoding="utf-8")
    second = validator_execution_digests(command, trusted_paths=(trusted,))

    assert first[0] != second[0]
    assert first[1] != second[1]


def test_validator_execution_configuration_binds_the_closed_stdin_launch_abi(
    tmp_path: Path,
) -> None:
    validator = tmp_path / "validator.py"
    validator.write_text("raise SystemExit(0)\n", encoding="utf-8")
    capture = capture_validator_execution(
        (sys.executable, str(validator), "review"),
        trusted_paths=(validator,),
    )
    launch_contract = {
        "schema_version": "factory-validator-launch/1",
        "launch_mode": "python-source-stdin/1",
        "runtime_tcb": "current-factory-python/1",
        "validator_abi": "standalone-python-source/1",
        "argv_0": "-",
        "file": "<stdin>",
        "stdin_after_source": "eof",
        "script_directory_on_sys_path": False,
        "interpreter_flags": "forbidden",
        "additional_path_bindings": "forbidden",
    }

    assert capture.configuration_digest == digest_obj(
        {
            "schema_version": "factory-validator-configuration/3",
            "runner": "isolated-build-loop/3",
            "launch_contract": launch_contract,
            "command_digest": capture.command_digest,
            "execution_identity_digest": capture.identity_digest,
            "snapshot_tree_digest": capture.document["snapshot_tree_digest"],
        }
    )
    assert capture.environment_digest == digest_obj(
        {
            "schema_version": "factory-validator-environment/4",
            "ambient_environment": "closed",
            "network": "loopback-connect-candidate-range",
            "candidate_endpoint": "host-supervised-loopback-block",
            "launch_contract": launch_contract,
            "read_scope": [
                "build-input",
                "build-plan",
                "pattern-catalog",
                "acceptance-obligation-catalog",
                "coder-output-snapshot",
                "tester-output-snapshot",
                "validator-execution-snapshot",
            ],
            "write_scope": ["validator-work", "validator-output"],
        }
    )


def test_first_use_acceptance_report_fsyncs_evidence_chain_through_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    report = {"catalog_digest": DIGEST_A, "satisfied": True}
    real_fsync = os.fsync
    synced: list[tuple[str, int]] = []

    def track_fsync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        synced.append(("file" if stat.S_ISREG(metadata.st_mode) else "directory", metadata.st_ino))
        real_fsync(descriptor)

    monkeypatch.setattr(obligations_module.os, "fsync", track_fsync)

    obligations_module.retain_acceptance_obligation_report(tmp_path, "run-1", report)
    digest_dir = run_dir / "evidence" / "acceptance-obligation-reports" / ("a" * 64)

    for directory in (
        digest_dir,
        digest_dir.parent,
        digest_dir.parent.parent,
        run_dir,
    ):
        assert ("directory", directory.stat().st_ino) in synced


def _catalog_document() -> dict[str, object]:
    return {
        "schema_version": "factory-acceptance-obligation-catalog/1",
        "catalog_id": "acceptance-catalog",
        "version": "1",
        "run_id": "run-1",
        "generation": 1,
        "target_state_digest": DIGEST_A,
        "phase_artifact_digests": dict(PHASES),
        "human_ratifier": "human:founder",
        "validator_ratifier": "agent:validator",
        "max_review_rounds": 2,
        "triggers": [
            {
                "trigger_id": "validating-to-preview",
                "from_state": "validating",
                "to_state": "preview",
                "command_digest": COMMAND_DIGEST,
                "configuration_digest": CONFIGURATION_DIGEST,
                "environment_digest": ENVIRONMENT_DIGEST,
                "obligations": [
                    {
                        "obligation_id": "acceptance-1",
                        "criterion": "The exact ratified behavior works.",
                        "verifier_id": "validator-test-execution-v1",
                        "intent_backreferences": [
                            {
                                "artifact_id": "product-1",
                                "artifact_digest": PHASES["product-specification"],
                                "item_id": "criterion-1",
                                "intent_digest": DIGEST_B,
                            }
                        ],
                        "required_evidence_ids": [
                            "candidate",
                            "acceptance-tests",
                            "coder-output-snapshot",
                            "tester-output-snapshot",
                        ],
                        "test_assertions": [
                            {
                                "test_id": "acceptance-1",
                                "assertion_digest": ASSERTION,
                            }
                        ],
                    }
                ],
            }
        ],
    }


def _trusted() -> dict[str, str]:
    return {
        "candidate": DIGEST_B,
        "acceptance-tests": DIGEST_C,
        "coder-output-snapshot": DIGEST_D,
        "tester-output-snapshot": "sha256:" + ("e" * 64),
    }


def _observations(catalog: AcceptanceObligationCatalog) -> dict[str, object]:
    trusted = _trusted()
    test_result = {
        "test_id": "acceptance-1",
        "assertion_digest": ASSERTION,
        "exit_status": 0,
        "output_digest": digest_obj(
            {
                "test_id": "acceptance-1",
                "assertion_digest": ASSERTION,
                "exit_status": 0,
                "candidate_digest": DIGEST_B,
                "acceptance_tests_digest": DIGEST_C,
                "command_digest": COMMAND_DIGEST,
            }
        ),
    }
    effect_body = {
        "obligation_id": "acceptance-1",
        "verifier_id": "validator-test-execution-v1",
        "candidate_digest": DIGEST_B,
        "acceptance_tests_digest": DIGEST_C,
        "command_digest": COMMAND_DIGEST,
        "configuration_digest": CONFIGURATION_DIGEST,
        "environment_digest": ENVIRONMENT_DIGEST,
        "started_at": 100,
        "finished_at": 101,
        "evidence_digests": trusted,
        "test_results": [test_result],
    }
    return {
        "schema_version": "factory-acceptance-obligation-observations/1",
        "run_id": "run-1",
        "generation": 1,
        "catalog_digest": catalog.content_digest,
        "trigger_id": "validating-to-preview",
        "candidate_digest": DIGEST_B,
        "acceptance_tests_digest": DIGEST_C,
        "command_digest": COMMAND_DIGEST,
        "configuration_digest": CONFIGURATION_DIGEST,
        "environment_digest": ENVIRONMENT_DIGEST,
        "started_at": 100,
        "finished_at": 101,
        "results": [
            {
                "obligation_id": "acceptance-1",
                "verifier_id": "validator-test-execution-v1",
                "passed": True,
                "evidence_digests": trusted,
                "test_results": [test_result],
                "effect_digest": digest_obj(effect_body),
            }
        ],
    }


def _derive(
    catalog: AcceptanceObligationCatalog,
    observations: dict[str, object],
) -> dict[str, object]:
    return derive_acceptance_obligation_report(
        catalog,
        observations=observations,
        run_id="run-1",
        generation=1,
        source="validating",
        destination="preview",
        target_state_digest=DIGEST_A,
        resolved_commit="a" * 40,
        resolved_tree="b" * 40,
        phase_artifact_digests=PHASES,
        candidate_digest=DIGEST_B,
        acceptance_tests_digest=DIGEST_C,
        command_digest=COMMAND_DIGEST,
        configuration_digest=CONFIGURATION_DIGEST,
        environment_digest=ENVIRONMENT_DIGEST,
        trusted_evidence_digests=_trusted(),
    )


def test_exact_catalog_and_observations_rederive_a_self_verifying_report() -> None:
    catalog = AcceptanceObligationCatalog.from_dict(_catalog_document())
    report = _derive(catalog, _observations(catalog))

    verify_acceptance_obligation_report(
        catalog,
        report,
        run_id="run-1",
        generation=1,
        source="validating",
        destination="preview",
        target_state_digest=DIGEST_A,
        resolved_commit="a" * 40,
        resolved_tree="b" * 40,
        phase_artifact_digests=PHASES,
        candidate_digest=DIGEST_B,
        acceptance_tests_digest=DIGEST_C,
        command_digest=COMMAND_DIGEST,
        configuration_digest=CONFIGURATION_DIGEST,
        environment_digest=ENVIRONMENT_DIGEST,
        trusted_evidence_digests=_trusted(),
    )
    assert report["satisfied"] is True
    assert report["observations_digest"] == digest_obj(report["observations"])


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value["triggers"][0].update({"from_state": "building"}),
            "must define validating -> preview",
        ),
        (
            lambda value: value["triggers"].append(deepcopy(value["triggers"][0])),
            "trigger ids must be unique",
        ),
        (
            lambda value: value["triggers"][0]["obligations"][0]["required_evidence_ids"].append(
                "operator-claim"
            ),
            "unsupported evidence ids",
        ),
    ],
)
def test_catalog_refuses_missing_ambiguous_or_untrusted_obligations(
    mutation: object,
    message: str,
) -> None:
    document = _catalog_document()
    mutation(document)  # type: ignore[operator]
    with pytest.raises(AcceptanceObligationError, match=message):
        AcceptanceObligationCatalog.from_dict(document)


def test_unknown_selector_and_unratified_validator_command_fail_closed() -> None:
    catalog = AcceptanceObligationCatalog.from_dict(_catalog_document())
    with pytest.raises(AcceptanceObligationError, match="unknown"):
        catalog.select("building", "preview")
    with pytest.raises(AcceptanceObligationError, match="does not authorize"):
        derive_acceptance_obligation_report(
            catalog,
            observations=_observations(catalog),
            run_id="run-1",
            generation=1,
            source="validating",
            destination="preview",
            target_state_digest=DIGEST_A,
            resolved_commit="a" * 40,
            resolved_tree="b" * 40,
            phase_artifact_digests=PHASES,
            candidate_digest=DIGEST_B,
            acceptance_tests_digest=DIGEST_C,
            command_digest=DIGEST_D,
            configuration_digest=CONFIGURATION_DIGEST,
            environment_digest=ENVIRONMENT_DIGEST,
            trusted_evidence_digests=_trusted(),
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda value: value["results"][0]["evidence_digests"].pop("candidate"),
            "evidence membership",
        ),
        (
            lambda value: value["results"][0]["test_results"][0].update(
                {"assertion_digest": DIGEST_D}
            ),
            "exact ratified test selection",
        ),
        (
            lambda value: value["results"][0]["test_results"][0].update(
                {"output_digest": DIGEST_D}
            ),
            "output receipt",
        ),
        (
            lambda value: value["results"][0].update({"effect_digest": DIGEST_D}),
            "effect digest",
        ),
    ],
)
def test_observation_membership_and_effect_mutations_are_denied(
    mutate: object,
    message: str,
) -> None:
    catalog = AcceptanceObligationCatalog.from_dict(_catalog_document())
    observations = _observations(catalog)
    mutate(observations)  # type: ignore[operator]
    with pytest.raises(AcceptanceObligationError, match=message):
        _derive(catalog, observations)


def test_report_tamper_is_not_a_valid_receipt_even_when_schema_valid() -> None:
    catalog = AcceptanceObligationCatalog.from_dict(_catalog_document())
    report = _derive(catalog, _observations(catalog))
    report["resolved_tree"] = "c" * 40

    with pytest.raises(AcceptanceObligationError, match="fresh derivation"):
        verify_acceptance_obligation_report(
            catalog,
            report,
            run_id="run-1",
            generation=1,
            source="validating",
            destination="preview",
            target_state_digest=DIGEST_A,
            resolved_commit="a" * 40,
            resolved_tree="b" * 40,
            phase_artifact_digests=PHASES,
            candidate_digest=DIGEST_B,
            acceptance_tests_digest=DIGEST_C,
            command_digest=COMMAND_DIGEST,
            configuration_digest=CONFIGURATION_DIGEST,
            environment_digest=ENVIRONMENT_DIGEST,
            trusted_evidence_digests=_trusted(),
        )
