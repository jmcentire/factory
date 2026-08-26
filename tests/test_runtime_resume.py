from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

import factory_runtime.resume as resume_module
from factory_core.manifest import digest_obj
from factory_runtime.authority import AuthorityPolicy, load_genesis
from factory_runtime.resume import (
    ResumeVerificationError,
    derive_resume_checkpoint,
    verify_resume_checkpoint,
)
from factory_runtime.state import RunState, RunStateError
from factory_runtime.workflow import FactoryWorkflow
from tests.conftest import SYNTHETIC_CATALOG, SYNTHETIC_TARGET, ratification_receipts
from tests.test_runtime_workflow import (
    ROOT_KEY,
    VALIDATOR_KEY,
    _execution_request,
    _object_source,
    _receipt,
    _resolution_request,
    _Tessera,
)


def _genesis() -> dict[str, Any]:
    return {
        "schema_version": "factory-genesis/1",
        "repository_id": "factory",
        "policy_id": "factory-authority/1",
        "root_public_key": ROOT_KEY,
        "principals": [
            {
                "identity": "human:founder",
                "kind": "human",
                "public_key": ROOT_KEY,
                "capabilities": [
                    "factory:authorize-target-resolution",
                    "factory:authorize-change",
                    "factory:ratify-product-specification",
                    "factory:ratify-architecture",
                    "factory:ratify-operational-maturity",
                    "factory:approve-promotion",
                    "factory:activate-policy",
                ],
            },
            {
                "identity": "agent:validator",
                "kind": "agent",
                "public_key": VALIDATOR_KEY,
                "capabilities": [
                    "factory:ratify-product-specification",
                    "factory:ratify-architecture",
                    "factory:ratify-operational-maturity",
                ],
            },
        ],
        "bootstrap": {
            "enabled": True,
            "scope": [
                "authorize-target-resolution",
                "authorize-change",
                "activate-policy",
            ],
            "deactivates_when": "the first non-bootstrap policy activation receipt is consumed",
        },
        "issued_at": 100,
    }


def _authorized_run(tmp_path: Path) -> tuple[FactoryWorkflow, _Tessera, Path, Path]:
    tessera = _Tessera()
    genesis_path = tessera.add(
        tmp_path / "genesis.tessera.json",
        _genesis(),
        key=ROOT_KEY,
        kind="factory-genesis",
    )
    policy = load_genesis(
        genesis_path,
        trusted_root_public_key=ROOT_KEY,
        tessera=tessera,  # type: ignore[arg-type]
    )
    workflow = FactoryWorkflow(
        tmp_path / "runs",
        authority_policy=policy,
        tessera=tessera,  # type: ignore[arg-type]
        clock=lambda: 150,
    )
    resolution_path, resolution = _resolution_request(tmp_path)
    resolution_receipt = tessera.add(
        tmp_path / "resolution.tessera.json",
        _receipt(
            receipt_id="resolution-1",
            action="authorize-target-resolution",
            subject_digest=digest_obj(resolution),
            signer="human:founder",
            nonce="resolution-nonce-001",
        ),
        key=ROOT_KEY,
        kind="factory-authority-receipt",
    )
    workflow.authorize_target_resolution(
        "run-1",
        manifest_path=SYNTHETIC_TARGET,
        request_path=resolution_path,
        receipt_path=resolution_receipt,
        pattern_catalog_path=SYNTHETIC_CATALOG,
    )
    workflow.resolve_target("run-1", object_source=_object_source(tmp_path))
    execution_path, execution = _execution_request(tmp_path, workflow)
    execution_receipt = tessera.add(
        tmp_path / "execution.tessera.json",
        _receipt(
            receipt_id="execution-1",
            action="authorize-change",
            subject_digest=digest_obj(execution),
            signer="human:founder",
            nonce="execution-nonce-001",
        ),
        key=ROOT_KEY,
        kind="factory-authority-receipt",
    )
    workflow.authorize_change(
        "run-1",
        request_path=execution_path,
        receipt_path=execution_receipt,
    )
    config = tmp_path / "runner-config.json"
    config.write_text('{"runner":"codex"}\n', encoding="utf-8")
    return workflow, tessera, genesis_path, config


def _checkpoint(
    tmp_path: Path,
    workflow: FactoryWorkflow,
    tessera: _Tessera,
    genesis_path: Path,
    config: Path,
    *,
    previous_checkpoint_digest: str = "",
    acceptance_obligation_catalog_digest: str | None = None,
) -> tuple[Path, str]:
    document = derive_resume_checkpoint(
        workflow.root,
        "run-1",
        checkpoint_id="checkpoint-1",
        previous_checkpoint_digest=previous_checkpoint_digest,
        genesis_path=genesis_path,
        trusted_root_public_key=ROOT_KEY,
        tessera=tessera,  # type: ignore[arg-type]
        configuration_sources={"runner": config},
        acceptance_obligation_catalog_digest=(
            acceptance_obligation_catalog_digest
        ),
        retention={
            "policy_id": "factory-retention-1",
            "mode": "retain-indefinitely",
            "retain_until": 0,
            "metadata_classes": [
                "authority-envelopes",
                "lifecycle-ledger",
                "resource-ledger",
            ],
            "erasure_authority": "human:founder",
        },
        clock=lambda: 151,
    )
    path = tmp_path / "external" / "checkpoint.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path, digest_obj(document)


def _verify(
    workflow: FactoryWorkflow,
    tessera: _Tessera,
    genesis_path: Path,
    config: Path,
    checkpoint_path: Path,
    checkpoint_digest: str,
    *,
    expected_acceptance_obligation_catalog_digest: str | None = None,
    accepted_previous_checkpoint_digests: tuple[str, ...] = (),
) -> Any:
    return verify_resume_checkpoint(
        checkpoint_path,
        expected_checkpoint_digest=checkpoint_digest,
        runs_root=workflow.root,
        run_id="run-1",
        genesis_path=genesis_path,
        trusted_root_public_key=ROOT_KEY,
        tessera=tessera,  # type: ignore[arg-type]
        configuration_sources={"runner": config},
        expected_acceptance_obligation_catalog_digest=(
            expected_acceptance_obligation_catalog_digest
        ),
        accepted_previous_checkpoint_digests=accepted_previous_checkpoint_digests,
    )


def test_checkpoint_reverifies_authority_and_allows_only_append_only_extension(
    tmp_path: Path,
) -> None:
    workflow, tessera, genesis_path, config = _authorized_run(tmp_path)
    checkpoint_path, checkpoint_digest = _checkpoint(
        tmp_path, workflow, tessera, genesis_path, config
    )
    result = _verify(
        workflow, tessera, genesis_path, config, checkpoint_path, checkpoint_digest
    )
    assert result.current_run_ledger_length == result.anchored_run_ledger_length
    admitted = result.state_admission_dict()
    assert admitted["current_run_ledger_head"] == result.current_run_ledger_head
    assert admitted["checkpoint_source_digest"].startswith("sha256:")
    assert "current_resource_ledger_head" not in admitted
    assert "current_resource_ledger_length" not in admitted

    workflow.store.transition(
        "run-1",
        RunState.PRODUCT_SPECIFICATION_RATIFIED,
        actor="validator",
        artifact_digests={
            "product-specification": "sha256:" + "e" * 64,
            **ratification_receipts("product-specification"),
        },
    )
    extended = _verify(
        workflow, tessera, genesis_path, config, checkpoint_path, checkpoint_digest
    )
    assert extended.current_run_ledger_length == result.current_run_ledger_length + 1
    assert extended.anchored_run_ledger_head == result.anchored_run_ledger_head


def test_bound_state_replay_constructs_an_authenticated_evidence_verifier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = AuthorityPolicy(
        repository_id="factory",
        policy_id="factory-authority/1",
        root_public_key=ROOT_KEY,
        principals={},
        bootstrap_enabled=False,
        bootstrap_scope=frozenset(),
        genesis_digest="sha256:" + ("a" * 64),
    )

    class _Genesis:
        payload_digest = policy.genesis_digest
        envelope_digest = "sha256:" + ("b" * 64)

    class _VerifierTessera:
        def verify_json(self, *_args: object, **_kwargs: object) -> _Genesis:
            return _Genesis()

    observed: list[object] = []

    class _StoppingStore:
        def __init__(
            self,
            _root: str | Path,
            *,
            preview_evidence_verifier: object,
        ) -> None:
            observed.append(preview_evidence_verifier)

        def load(self, _run_id: str) -> object:
            raise RunStateError("stop after verifier construction")

    tessera = _VerifierTessera()
    monkeypatch.setattr(resume_module, "load_genesis", lambda *_args, **_kwargs: policy)
    monkeypatch.setattr(resume_module, "RunStore", _StoppingStore)

    with pytest.raises(ResumeVerificationError, match="stop after verifier construction"):
        resume_module._derive_bound_state(
            tmp_path,
            "run-1",
            genesis_path=tmp_path / "genesis.tessera.json",
            trusted_root_public_key=ROOT_KEY,
            tessera=tessera,  # type: ignore[arg-type]
            configuration_sources={"runner": tmp_path / "runner.json"},
        )

    assert len(observed) == 1
    verifier = observed[0]
    assert isinstance(verifier, resume_module.TesseraEvidenceEnvelopeVerifier)
    assert verifier._tessera is tessera
    assert verifier._policy is policy


def test_checkpoint_binds_independently_expected_acceptance_catalog(tmp_path: Path) -> None:
    workflow, tessera, genesis_path, config = _authorized_run(tmp_path)
    catalog_digest = digest_obj({"catalog": "ratified-v1"})
    checkpoint_path, checkpoint_digest = _checkpoint(
        tmp_path,
        workflow,
        tessera,
        genesis_path,
        config,
        acceptance_obligation_catalog_digest=catalog_digest,
    )

    verified = _verify(
        workflow,
        tessera,
        genesis_path,
        config,
        checkpoint_path,
        checkpoint_digest,
        expected_acceptance_obligation_catalog_digest=catalog_digest,
    )
    assert verified.acceptance_obligation_catalog_digest == catalog_digest

    with pytest.raises(ResumeVerificationError, match="acceptance_obligation_catalog_digest"):
        _verify(
            workflow,
            tessera,
            genesis_path,
            config,
            checkpoint_path,
            checkpoint_digest,
            expected_acceptance_obligation_catalog_digest=digest_obj(
                {"catalog": "substituted"}
            ),
        )


def test_non_genesis_checkpoint_requires_an_explicitly_accepted_predecessor(
    tmp_path: Path,
) -> None:
    workflow, tessera, genesis_path, config = _authorized_run(tmp_path)
    predecessor = digest_obj({"checkpoint": "prior"})
    checkpoint_path, checkpoint_digest = _checkpoint(
        tmp_path,
        workflow,
        tessera,
        genesis_path,
        config,
        previous_checkpoint_digest=predecessor,
    )

    with pytest.raises(ResumeVerificationError, match="unaccepted predecessor"):
        _verify(
            workflow, tessera, genesis_path, config, checkpoint_path, checkpoint_digest
        )
    _verify(
        workflow,
        tessera,
        genesis_path,
        config,
        checkpoint_path,
        checkpoint_digest,
        accepted_previous_checkpoint_digests=(predecessor,),
    )


def test_checkpoint_bytes_root_and_configuration_are_not_substitutable(tmp_path: Path) -> None:
    workflow, tessera, genesis_path, config = _authorized_run(tmp_path)
    checkpoint_path, checkpoint_digest = _checkpoint(
        tmp_path, workflow, tessera, genesis_path, config
    )
    document = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    document["run_id"] = "run-2"
    checkpoint_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ResumeVerificationError, match="externally pinned digest"):
        _verify(workflow, tessera, genesis_path, config, checkpoint_path, checkpoint_digest)

    checkpoint_path, checkpoint_digest = _checkpoint(
        tmp_path / "fresh", workflow, tessera, genesis_path, config
    )
    with pytest.raises(ResumeVerificationError, match="substitutes the trusted root"):
        verify_resume_checkpoint(
            checkpoint_path,
            expected_checkpoint_digest=checkpoint_digest,
            runs_root=workflow.root,
            run_id="run-1",
            genesis_path=genesis_path,
            trusted_root_public_key="f" * 64,
            tessera=tessera,  # type: ignore[arg-type]
            configuration_sources={"runner": config},
        )
    config.write_text('{"runner":"other"}\n', encoding="utf-8")
    with pytest.raises(ResumeVerificationError, match="configuration_digests"):
        _verify(workflow, tessera, genesis_path, config, checkpoint_path, checkpoint_digest)


def test_whole_control_root_copy_and_ledger_rollback_are_denied(tmp_path: Path) -> None:
    workflow, tessera, genesis_path, config = _authorized_run(tmp_path)
    workflow.store.transition(
        "run-1",
        RunState.PRODUCT_SPECIFICATION_RATIFIED,
        actor="validator",
        artifact_digests={
            "product-specification": "sha256:" + "e" * 64,
            **ratification_receipts("product-specification"),
        },
    )
    checkpoint_path, checkpoint_digest = _checkpoint(
        tmp_path, workflow, tessera, genesis_path, config
    )

    copied_runs = tmp_path / "copied-runs"
    shutil.copytree(workflow.root, copied_runs)
    with pytest.raises(ResumeVerificationError, match="control root was substituted"):
        verify_resume_checkpoint(
            checkpoint_path,
            expected_checkpoint_digest=checkpoint_digest,
            runs_root=copied_runs,
            run_id="run-1",
            genesis_path=genesis_path,
            trusted_root_public_key=ROOT_KEY,
            tessera=tessera,  # type: ignore[arg-type]
            configuration_sources={"runner": config},
        )

    ledger = workflow.root / "run-1" / "ledger.jsonl"
    lines = ledger.read_text(encoding="utf-8").splitlines()
    ledger.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    workflow.store.rebuild_projection("run-1")
    with pytest.raises(ResumeVerificationError, match="rolled back"):
        _verify(workflow, tessera, genesis_path, config, checkpoint_path, checkpoint_digest)


def test_retained_receipt_tampering_is_denied_even_with_unchanged_checkpoint(
    tmp_path: Path,
) -> None:
    workflow, tessera, genesis_path, config = _authorized_run(tmp_path)
    checkpoint_path, checkpoint_digest = _checkpoint(
        tmp_path, workflow, tessera, genesis_path, config
    )
    receipt = (
        workflow.root
        / "run-1"
        / "evidence"
        / "intake"
        / "execution-receipt.tessera.json"
    )
    receipt.write_text('{"tampered":true}\n', encoding="utf-8")
    with pytest.raises(ResumeVerificationError, match="retained authorize-change receipt"):
        _verify(workflow, tessera, genesis_path, config, checkpoint_path, checkpoint_digest)
