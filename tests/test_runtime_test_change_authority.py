from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import factory_runtime.test_change_authority as authority_module
from factory_core.manifest import digest_bytes, digest_obj
from factory_core.provenance import (
    PHASE_ARCHITECTURE,
    PHASE_OPERATIONAL_MATURITY,
    PHASE_PRODUCT_SPECIFICATION,
    IntentBackreference,
    IntentItem,
    PhaseArtifact,
)
from factory_core.test_disposition import (
    TestAssertionBinding as _TestAssertionBinding,
)
from factory_core.test_disposition import (
    TestChangeAuthorization as _TestChangeAuthorization,
)
from factory_core.test_disposition import (
    TestSelection as _TestSelection,
)
from factory_runtime.authority import AuthorityPolicy, Principal
from factory_runtime.state import RunState, RunStore
from factory_runtime.tessera import TesseraVerificationError, VerifiedEnvelope
from factory_runtime.test_change_authority import (
    HUMAN_RECEIPT_KEY,
    VALIDATOR_RECEIPT_KEY,
    verify_and_retain_test_change_authorization,
)
from factory_runtime.test_change_authority import (
    TestChangeAuthorityError as _TestChangeAuthorityError,
)
from tests.conftest import create_intake_run, ratification_receipts

ROOT_KEY = "a" * 64
VALIDATOR_KEY = "b" * 64
SOURCE = digest_obj({"source": "test-change-authority"})
TARGET = digest_obj({"target": "test-change-authority"})


class _Tessera:
    def __init__(self) -> None:
        self.envelopes: list[VerifiedEnvelope] = []

    def add(
        self,
        path: Path,
        payload: dict[str, Any],
        *,
        key: str,
    ) -> Path:
        path.write_text(json.dumps({"fixture": payload}), encoding="utf-8")
        self.envelopes.append(
            VerifiedEnvelope(
                kind="factory-authority-receipt",
                payload=payload,
                payload_digest=digest_obj(payload),
                public_key=key,
                envelope_digest=digest_bytes(path.read_bytes()),
                path=path,
            )
        )
        return path

    def verify_json(
        self,
        envelope_path: str | Path,
        *,
        trusted_public_keys: tuple[str, ...] = (),
        expected_kind: str | None = None,
        expected_payload_digest: str | None = None,
    ) -> VerifiedEnvelope:
        path = Path(envelope_path)
        document = json.loads(path.read_text(encoding="utf-8"))
        payload = document.get("fixture")
        envelope = next(
            (
                replace(candidate, path=path)
                for candidate in self.envelopes
                if candidate.payload == payload
                and candidate.envelope_digest == digest_bytes(path.read_bytes())
            ),
            None,
        )
        if envelope is None:
            raise TesseraVerificationError("unknown or modified fixture envelope")
        if trusted_public_keys and envelope.public_key not in trusted_public_keys:
            raise TesseraVerificationError("untrusted fixture signer")
        if expected_kind is not None and envelope.kind != expected_kind:
            raise TesseraVerificationError("wrong fixture kind")
        if (
            expected_payload_digest is not None
            and envelope.payload_digest != expected_payload_digest
        ):
            raise TesseraVerificationError("wrong fixture payload")
        return envelope


def _policy() -> AuthorityPolicy:
    capability = "factory:ratify-test-change-authorization"
    return AuthorityPolicy(
        repository_id="factory",
        policy_id="factory-authority/1",
        root_public_key=ROOT_KEY,
        principals={
            "human:founder": Principal(
                identity="human:founder",
                kind="human",
                public_key=ROOT_KEY,
                capabilities=frozenset({capability}),
            ),
            "agent:validator": Principal(
                identity="agent:validator",
                kind="agent",
                public_key=VALIDATOR_KEY,
                capabilities=frozenset({capability}),
            ),
        },
        bootstrap_enabled=False,
        bootstrap_scope=frozenset(),
        genesis_digest=digest_obj({"genesis": "test-change-authority"}),
    )


def _ratified_run(tmp_path: Path) -> tuple[RunStore, IntentBackreference, IntentBackreference]:
    store = RunStore(tmp_path, clock=lambda: 100)
    create_intake_run(
        store,
        run_id="run-1",
        target_digest=TARGET,
        source_digest=SOURCE,
    )
    old = IntentBackreference(
        artifact_id="product-v1",
        artifact_digest=digest_obj({"product": "v1"}),
        item_id="behavior",
        intent_digest=digest_obj({"canonical_statement": "The old behavior."}),
    )
    product = PhaseArtifact(
        artifact_id="product-v2",
        phase=PHASE_PRODUCT_SPECIFICATION,
        version="2",
        source_digest=SOURCE,
        human_ratifier="human:founder",
        validator_ratifier="agent:validator",
        items=(
            IntentItem(
                item_id="behavior-v2",
                canonical_statement="The replacement behavior.",
                supersedes=(old,),
            ),
        ),
    )
    architecture = PhaseArtifact(
        artifact_id="architecture-v1",
        phase=PHASE_ARCHITECTURE,
        version="1",
        source_digest=SOURCE,
        human_ratifier="human:founder",
        validator_ratifier="agent:validator",
        items=(IntentItem("architecture", "The architecture remains fixed."),),
    )
    operations = PhaseArtifact(
        artifact_id="operations-v1",
        phase=PHASE_OPERATIONAL_MATURITY,
        version="1",
        source_digest=SOURCE,
        human_ratifier="human:founder",
        validator_ratifier="agent:validator",
        items=(IntentItem("tests", "The exact tests remain required."),),
    )
    for artifact, destination in (
        (product, RunState.PRODUCT_SPECIFICATION_RATIFIED),
        (architecture, RunState.ARCHITECTURE_RATIFIED),
        (operations, RunState.OPERATIONAL_MATURITY_RATIFIED),
    ):
        directory = (
            tmp_path
            / "run-1"
            / "evidence"
            / artifact.phase
            / artifact.content_digest.removeprefix("sha256:")
        )
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "artifact.json").write_text(
            json.dumps(artifact.body(), sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        store.transition(
            "run-1",
            destination,
            actor="validator",
            artifact_digests={
                artifact.phase: artifact.content_digest,
                **ratification_receipts(artifact.phase),
            },
        )
    return store, old, product.backreference(product.items[0])


def _authorization(
    store: RunStore,
    old: IntentBackreference,
    new: IntentBackreference,
) -> _TestChangeAuthorization:
    projection = store.load("run-1")
    return _TestChangeAuthorization(
        authorization_id="test-change-1",
        version="1",
        run_id="run-1",
        generation=projection.generation,
        target_state_digest=projection.target_state_digest,
        human_authorizer="human:founder",
        validator_ratifier="agent:validator",
        ruling="change-expected-behavior",
        expected_change_statement="The replacement behavior.",
        phase_artifact_digests=projection.phase_artifact_digests,
        old_behavior=old,
        new_behavior=new,
        selection=_TestSelection(
            family_id="",
            members=(
                _TestAssertionBinding(
                    test_id="tests/test_contract.py::test_old_expectation",
                    assertion_digest=digest_obj({"assertion": "old expectation"}),
                ),
            ),
        ),
    )


def _receipt(
    authorization: _TestChangeAuthorization,
    *,
    signer: str,
    nonce: str,
) -> dict[str, Any]:
    return {
        "schema_version": "factory-authority-receipt/1",
        "receipt_id": nonce,
        "run_id": "run-1",
        "repository_id": "factory",
        "action": "ratify-test-change-authorization",
        "subject_digest": authorization.content_digest,
        "signer_identity": signer,
        "capabilities": ["factory:ratify-test-change-authorization"],
        "issued_at": 100,
        "expires_at": 200,
        "nonce": nonce,
    }


def test_exact_dual_authority_is_retained_and_addressed(tmp_path: Path) -> None:
    store, old, new = _ratified_run(tmp_path)
    authorization = _authorization(store, old, new)
    authorization_path = tmp_path / "authorization.json"
    authorization_path.write_text(json.dumps(authorization.body()), encoding="utf-8")
    tessera = _Tessera()
    human_path = tessera.add(
        tmp_path / "human.tessera.json",
        _receipt(authorization, signer="human:founder", nonce="human-test-change-nonce"),
        key=ROOT_KEY,
    )
    validator_path = tessera.add(
        tmp_path / "validator.tessera.json",
        _receipt(
            authorization,
            signer="agent:validator",
            nonce="validator-test-change-nonce",
        ),
        key=VALIDATOR_KEY,
    )

    stored = verify_and_retain_test_change_authorization(
        tmp_path,
        "run-1",
        authorization_path=authorization_path,
        human_receipt_path=human_path,
        validator_receipt_path=validator_path,
        changed_existing_tests=["tests/test_contract.py::test_old_expectation"],
        policy=_policy(),
        tessera=tessera,  # type: ignore[arg-type]
        clock=lambda: 150,
    )

    assert stored.artifact_digests["test-change-authorization"] == (authorization.content_digest)
    assert (
        stored.artifact_digests[HUMAN_RECEIPT_KEY] != stored.artifact_digests[VALIDATOR_RECEIPT_KEY]
    )
    assert json.loads((stored.directory / "authorization.json").read_text()) == (
        authorization.body()
    )
    reservation = json.loads((stored.directory / "nonce-reservations.json").read_text())
    assert reservation["authorization_digest"] == authorization.content_digest
    assert reservation["nonces"] == [
        "human-test-change-nonce",
        "validator-test-change-nonce",
    ]


def test_membership_or_signer_substitution_is_denied(tmp_path: Path) -> None:
    store, old, new = _ratified_run(tmp_path)
    authorization = _authorization(store, old, new)
    authorization_path = tmp_path / "authorization.json"
    authorization_path.write_text(json.dumps(authorization.body()), encoding="utf-8")
    tessera = _Tessera()
    human_path = tessera.add(
        tmp_path / "human.tessera.json",
        _receipt(authorization, signer="human:founder", nonce="human-test-change-nonce"),
        key=ROOT_KEY,
    )
    wrong_validator_path = tessera.add(
        tmp_path / "validator.tessera.json",
        _receipt(
            authorization,
            signer="agent:validator",
            nonce="validator-test-change-nonce",
        ),
        key=ROOT_KEY,
    )

    with pytest.raises(_TestChangeAuthorityError, match="exact membership"):
        verify_and_retain_test_change_authorization(
            tmp_path,
            "run-1",
            authorization_path=authorization_path,
            human_receipt_path=human_path,
            validator_receipt_path=wrong_validator_path,
            changed_existing_tests=["tests/test_contract.py::some_other_test"],
            policy=_policy(),
            tessera=tessera,  # type: ignore[arg-type]
            clock=lambda: 150,
        )

    with pytest.raises(_TestChangeAuthorityError, match="does not own the signing key"):
        verify_and_retain_test_change_authorization(
            tmp_path,
            "run-1",
            authorization_path=authorization_path,
            human_receipt_path=human_path,
            validator_receipt_path=wrong_validator_path,
            changed_existing_tests=["tests/test_contract.py::test_old_expectation"],
            policy=_policy(),
            tessera=tessera,  # type: ignore[arg-type]
            clock=lambda: 150,
        )


def test_validator_must_be_an_enrolled_agent_not_a_service(tmp_path: Path) -> None:
    store, old, new = _ratified_run(tmp_path)
    authorization = _authorization(store, old, new)
    authorization_path = tmp_path / "authorization.json"
    authorization_path.write_text(json.dumps(authorization.body()), encoding="utf-8")
    tessera = _Tessera()
    human_path = tessera.add(
        tmp_path / "human.tessera.json",
        _receipt(authorization, signer="human:founder", nonce="human-test-change-nonce"),
        key=ROOT_KEY,
    )
    validator_path = tessera.add(
        tmp_path / "validator.tessera.json",
        _receipt(
            authorization,
            signer="agent:validator",
            nonce="validator-test-change-nonce",
        ),
        key=VALIDATOR_KEY,
    )
    policy = _policy()
    service_policy = replace(
        policy,
        principals={
            **policy.principals,
            "agent:validator": replace(policy.principals["agent:validator"], kind="service"),
        },
    )

    with pytest.raises(_TestChangeAuthorityError, match="not an enrolled agent"):
        verify_and_retain_test_change_authorization(
            tmp_path,
            "run-1",
            authorization_path=authorization_path,
            human_receipt_path=human_path,
            validator_receipt_path=validator_path,
            changed_existing_tests=["tests/test_contract.py::test_old_expectation"],
            policy=service_policy,
            tessera=tessera,  # type: ignore[arg-type]
            clock=lambda: 150,
        )


def test_published_bundle_reserves_both_nonces_before_ledger_activation(tmp_path: Path) -> None:
    store, old, new = _ratified_run(tmp_path)
    first = _authorization(store, old, new)
    second = replace(first, authorization_id="test-change-2")
    tessera = _Tessera()

    def paths_for(authorization: _TestChangeAuthorization, prefix: str) -> tuple[Path, Path, Path]:
        authorization_path = tmp_path / f"{prefix}-authorization.json"
        authorization_path.write_text(json.dumps(authorization.body()), encoding="utf-8")
        human_path = tessera.add(
            tmp_path / f"{prefix}-human.tessera.json",
            _receipt(authorization, signer="human:founder", nonce="shared-human-nonce"),
            key=ROOT_KEY,
        )
        validator_path = tessera.add(
            tmp_path / f"{prefix}-validator.tessera.json",
            _receipt(authorization, signer="agent:validator", nonce="shared-validator-nonce"),
            key=VALIDATOR_KEY,
        )
        return authorization_path, human_path, validator_path

    first_paths = paths_for(first, "first")
    second_paths = paths_for(second, "second")
    verify_and_retain_test_change_authorization(
        tmp_path,
        "run-1",
        authorization_path=first_paths[0],
        human_receipt_path=first_paths[1],
        validator_receipt_path=first_paths[2],
        changed_existing_tests=["tests/test_contract.py::test_old_expectation"],
        policy=_policy(),
        tessera=tessera,  # type: ignore[arg-type]
        clock=lambda: 150,
    )

    with pytest.raises(_TestChangeAuthorityError, match="nonce has already been consumed"):
        verify_and_retain_test_change_authorization(
            tmp_path,
            "run-1",
            authorization_path=second_paths[0],
            human_receipt_path=second_paths[1],
            validator_receipt_path=second_paths[2],
            changed_existing_tests=["tests/test_contract.py::test_old_expectation"],
            policy=_policy(),
            tessera=tessera,  # type: ignore[arg-type]
            clock=lambda: 150,
        )


def test_bundle_publish_is_all_or_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    final = tmp_path / "run-1" / "evidence" / "test-change-authorizations" / ("a" * 64)
    real_write = authority_module._write_new_file
    calls = 0

    def fail_second_write(path: Path, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise authority_module.TestChangeAuthorityError("injected partial write")
        real_write(path, content)

    monkeypatch.setattr(authority_module, "_write_new_file", fail_second_write)
    with pytest.raises(_TestChangeAuthorityError, match="injected partial write"):
        authority_module._retain_bundle_atomically(
            run_dir=tmp_path / "run-1",
            final_directory=final,
            files={"one.json": b"one\n", "two.json": b"two\n"},
        )

    assert not final.exists()
    staging = tmp_path / "run-1" / ".staging" / "test-change-authority"
    assert list(staging.iterdir()) == []
