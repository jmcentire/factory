from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import factory_runtime.catalog_activation as activation
from factory_core.manifest import digest_obj
from factory_runtime.acceptance_obligations import (
    AcceptanceObligationCatalog,
    validator_execution_digests,
)
from factory_runtime.cli import _parser
from factory_runtime.state import RunState


def _projection() -> SimpleNamespace:
    return SimpleNamespace(
        state=RunState.OPERATIONAL_MATURITY_RATIFIED,
        generation=1,
        target_state_digest="sha256:" + "a" * 64,
        phase_artifact_digests={
            "product-specification": "sha256:" + "1" * 64,
            "architecture": "sha256:" + "2" * 64,
            "operational-maturity": "sha256:" + "3" * 64,
        },
    )


def _catalog(projection: SimpleNamespace) -> dict[str, object]:
    command, configuration, environment = validator_execution_digests((sys.executable,))
    return {
        "schema_version": "factory-acceptance-obligation-catalog/1",
        "catalog_id": "catalog-proposal-test",
        "version": "1",
        "run_id": "run-1",
        "generation": projection.generation,
        "target_state_digest": projection.target_state_digest,
        "phase_artifact_digests": dict(projection.phase_artifact_digests),
        "human_ratifier": "human:founder",
        "validator_ratifier": "agent:validator",
        "max_review_rounds": 1,
        "triggers": [
            {
                "trigger_id": "validating-to-preview",
                "from_state": "validating",
                "to_state": "preview",
                "command_digest": command,
                "configuration_digest": configuration,
                "environment_digest": environment,
                "obligations": [
                    {
                        "obligation_id": "catalog-proposal-test",
                        "criterion": "The designated acceptance check succeeds.",
                        "verifier_id": "validator-test-execution-v1",
                        "intent_backreferences": [
                            {
                                "artifact_id": "product-specification",
                                "artifact_digest": projection.phase_artifact_digests[
                                    "product-specification"
                                ],
                                "item_id": "criterion-1",
                                "intent_digest": "sha256:" + "b" * 64,
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
                                "test_id": "catalog-proposal-test",
                                "assertion_digest": digest_obj({"expected": "success"}),
                            }
                        ],
                    }
                ],
            }
        ],
    }


def _proposal(projection: SimpleNamespace, source: Path) -> dict[str, object]:
    return {
        "schema_version": activation.SCHEMA_VERSION,
        "run_id": "run-1",
        "generation": projection.generation,
        "target_state_digest": projection.target_state_digest,
        "phase_artifact_digests": dict(projection.phase_artifact_digests),
        "proposal_nonce": "catalog-proposal-nonce-0001",
        "catalog": _catalog(projection),
        "target_runtime_profile": {
            "mode": "native-two-profile",
            "candidate_launch": [sys.executable, "server.py"],
            "test_entrypoint": [sys.executable, "-m", "pytest"],
            "runtime_read_paths": [str(source / "runtime")],
            "readiness": {
                "entrypoint": [sys.executable, "ready.py"],
                "timeout_seconds": 15,
                "interval_seconds": 1,
                "max_attempts": 15,
            },
            "loopback": [{"protocol": "tcp", "operations": ["bind", "connect"], "count": 1}],
        },
    }


@pytest.fixture
def proposal_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    projection = _projection()
    (tmp_path / "runs").mkdir()
    source = tmp_path / "target-source"
    (source / "runtime").mkdir(parents=True)

    class FakeStore:
        def __init__(self, root):
            self.root = Path(root)

        def load(self, run_id):
            assert run_id == "run-1"
            return projection

        def consumed_authority_nonces(self, run_id):
            assert run_id == "run-1"
            return set()

    monkeypatch.setattr(activation, "RunStore", FakeStore)
    monkeypatch.setattr(activation, "_source_root", lambda *_: source)
    return projection, source


def _write_canonical(path: Path, document: dict[str, object]) -> None:
    path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")


def test_proposal_is_canonical_data_outside_the_run_root(
    tmp_path: Path, proposal_environment
) -> None:
    projection, source = proposal_environment
    source_input = tmp_path / "input.json"
    output = tmp_path / "proposal.json"
    _write_canonical(source_input, _proposal(projection, source))

    digest = activation.create_catalog_proposal(source_input, output, runs_root=tmp_path / "runs")

    assert digest == digest_obj(json.loads(output.read_text()))
    assert not (tmp_path / "runs" / "run-1").exists()
    assert output.exists()


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda value: value.__setitem__("run_id", "other-run"), "names no retained"),
        (lambda value: value.__setitem__("unexpected", "provider"), "unsupported schema"),
        (
            lambda value: value["target_runtime_profile"].__setitem__(
                "environment", {"TOKEN": "x"}
            ),
            "invalid",
        ),
    ],
)
def test_proposal_refuses_cross_run_extra_fields_and_environment(
    tmp_path: Path, proposal_environment, mutate, message: str
) -> None:
    projection, source = proposal_environment
    document = _proposal(projection, source)
    mutate(document)
    with pytest.raises(activation.CatalogProposalError, match=message):
        activation.validate_catalog_proposal(document, runs_root=tmp_path / "runs")


def test_activation_binds_dual_receipts_to_exact_proposal_and_retains_only_after_validation(
    tmp_path: Path, proposal_environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    projection, source = proposal_environment
    document = _proposal(projection, source)
    proposal = tmp_path / "proposal.json"
    _write_canonical(proposal, document)
    retained = tmp_path / "runs" / "run-1" / "evidence" / "acceptance-obligation-catalogs" / "x"
    retained.mkdir(parents=True)
    seen: dict[str, object] = {}

    def retain(*args, **kwargs):
        seen.update(kwargs)
        catalog = AcceptanceObligationCatalog.from_dict(kwargs["catalog_document"])
        return SimpleNamespace(catalog=catalog, directory=retained)

    monkeypatch.setattr(activation, "verify_and_retain_acceptance_catalog", retain)
    result = activation.activate_catalog_proposal(
        proposal,
        human_receipt_path=tmp_path / "human.tessera.json",
        validator_receipt_path=tmp_path / "validator.tessera.json",
        runs_root=tmp_path / "runs",
        policy=SimpleNamespace(),
        tessera=SimpleNamespace(),
    )

    assert seen["receipt_subject_digest"] == digest_obj(document)
    assert result.stored_catalog.catalog.content_digest == AcceptanceObligationCatalog.from_dict(
        document["catalog"]
    ).content_digest
    assert (retained / "proposal.json").exists()
    assert (retained / "native-runtime-profile.json").exists()


def test_activation_refuses_tampered_proposal_before_retention(
    tmp_path: Path, proposal_environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    projection, source = proposal_environment
    document = _proposal(projection, source)
    document["proposal_nonce"] = "short"
    proposal = tmp_path / "proposal.json"
    _write_canonical(proposal, document)
    called = False

    def retain(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not retain")

    monkeypatch.setattr(activation, "verify_and_retain_acceptance_catalog", retain)
    with pytest.raises(activation.CatalogProposalError, match="nonce"):
        activation.activate_catalog_proposal(
            proposal,
            human_receipt_path=tmp_path / "human",
            validator_receipt_path=tmp_path / "validator",
            runs_root=tmp_path / "runs",
            policy=SimpleNamespace(),
            tessera=SimpleNamespace(),
        )
    assert not called


def test_catalog_commands_expose_no_execution_or_ambient_configuration() -> None:
    parser = _parser()
    proposal = parser.parse_args(
        [
            "propose-acceptance-catalog",
            "--runs",
            "/runs",
            "--proposal-input",
            "/operator/proposal-input.json",
            "--proposal-output",
            "/operator/proposal.json",
        ]
    )
    activation_args = parser.parse_args(
        [
            "activate-acceptance-catalog",
            "--runs",
            "/runs",
            "--proposal",
            "/operator/proposal.json",
            "--human-receipt",
            "/operator/human.tessera.json",
            "--validator-receipt",
            "/operator/validator.tessera.json",
            "--genesis",
            "/operator/genesis.tessera.json",
            "--root-public-key",
            "a" * 64,
        ]
    )

    assert proposal.command == "propose-acceptance-catalog"
    assert activation_args.command == "activate-acceptance-catalog"
    assert not hasattr(proposal, "coder_command_arg")
    assert not hasattr(activation_args, "candidate_launch")
