from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from factory_core.manifest import digest_obj
from factory_runtime.authority import (
    AuthorityVerificationError,
    load_genesis,
    verify_receipt,
)
from factory_runtime.tessera import VerifiedEnvelope

ROOT_KEY = "a" * 64
VALIDATOR_KEY = "b" * 64
SUBJECT = "sha256:" + ("c" * 64)


class _Tessera:
    def __init__(self, envelopes: dict[str, VerifiedEnvelope]) -> None:
        self.envelopes = envelopes

    def verify_json(
        self,
        envelope_path: str | Path,
        *,
        trusted_public_keys: tuple[str, ...] = (),
        expected_kind: str | None = None,
        expected_payload_digest: str | None = None,
    ) -> VerifiedEnvelope:
        del expected_payload_digest
        envelope = self.envelopes[str(envelope_path)]
        if trusted_public_keys and envelope.public_key not in trusted_public_keys:
            raise AssertionError("test fixture supplied an untrusted signer")
        if expected_kind is not None and envelope.kind != expected_kind:
            raise AssertionError("test fixture supplied the wrong kind")
        return envelope


def _envelope(path: str, payload: dict[str, Any], *, key: str, kind: str) -> VerifiedEnvelope:
    return VerifiedEnvelope(
        kind=kind,
        payload=payload,
        payload_digest=digest_obj(payload),
        public_key=key,
        envelope_digest=digest_obj({"envelope": payload, "key": key}),
        path=Path(path),
    )


def _genesis_payload() -> dict[str, Any]:
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


def _receipt_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "schema_version": "factory-authority-receipt/1",
        "receipt_id": "receipt-1",
        "run_id": "run-1",
        "repository_id": "factory",
        "action": "ratify-product-specification",
        "subject_digest": SUBJECT,
        "signer_identity": "human:founder",
        "capabilities": ["factory:ratify-product-specification"],
        "issued_at": 100,
        "expires_at": 200,
        "nonce": "nonce-0000000001",
    }
    payload.update(overrides)
    return payload


def _policy_and_tessera() -> tuple[Any, _Tessera]:
    genesis = _genesis_payload()
    tessera = _Tessera(
        {
            "genesis": _envelope(
                "genesis",
                genesis,
                key=ROOT_KEY,
                kind="factory-genesis",
            )
        }
    )
    return (
        load_genesis(
            "genesis",
            trusted_root_public_key=ROOT_KEY,
            tessera=tessera,  # type: ignore[arg-type]
        ),
        tessera,
    )


def test_genesis_requires_the_externally_pinned_root() -> None:
    policy, _ = _policy_and_tessera()

    assert policy.repository_id == "factory"
    assert policy.root_public_key == ROOT_KEY
    assert policy.principal("agent:validator") is not None


def test_genesis_rejects_key_reuse_between_principals() -> None:
    genesis = _genesis_payload()
    genesis["principals"][1]["public_key"] = ROOT_KEY
    tessera = _Tessera(
        {
            "genesis": _envelope(
                "genesis",
                genesis,
                key=ROOT_KEY,
                kind="factory-genesis",
            )
        }
    )

    with pytest.raises(AuthorityVerificationError, match="reuses a signing key"):
        load_genesis(
            "genesis",
            trusted_root_public_key=ROOT_KEY,
            tessera=tessera,  # type: ignore[arg-type]
        )


def test_receipt_is_bound_to_action_subject_signer_capability_expiry_and_nonce() -> None:
    policy, tessera = _policy_and_tessera()
    receipt = _receipt_payload()
    tessera.envelopes["receipt"] = _envelope(
        "receipt",
        receipt,
        key=ROOT_KEY,
        kind="factory-authority-receipt",
    )

    verified = verify_receipt(
        "receipt",
        policy=policy,
        expected_action="ratify-product-specification",
        expected_subject_digest=SUBJECT,
        expected_run_id="run-1",
        expected_signer_identity="human:founder",
        tessera=tessera,  # type: ignore[arg-type]
        clock=lambda: 150,
    )
    assert verified.receipt_id == "receipt-1"

    with pytest.raises(AuthorityVerificationError, match="different subject"):
        verify_receipt(
            "receipt",
            policy=policy,
            expected_action="ratify-product-specification",
            expected_subject_digest="sha256:" + ("d" * 64),
            expected_run_id="run-1",
            tessera=tessera,  # type: ignore[arg-type]
            clock=lambda: 150,
        )
    with pytest.raises(AuthorityVerificationError, match="different Factory run"):
        verify_receipt(
            "receipt",
            policy=policy,
            expected_action="ratify-product-specification",
            expected_subject_digest=SUBJECT,
            expected_run_id="run-2",
            tessera=tessera,  # type: ignore[arg-type]
            clock=lambda: 150,
        )
    with pytest.raises(AuthorityVerificationError, match="already been consumed"):
        verify_receipt(
            "receipt",
            policy=policy,
            expected_action="ratify-product-specification",
            expected_subject_digest=SUBJECT,
            expected_run_id="run-1",
            tessera=tessera,  # type: ignore[arg-type]
            clock=lambda: 150,
            consumed_nonces=("nonce-0000000001",),
        )


def test_expired_receipt_is_refused() -> None:
    policy, tessera = _policy_and_tessera()
    receipt = _receipt_payload(expires_at=120)
    tessera.envelopes["receipt"] = _envelope(
        "receipt",
        receipt,
        key=ROOT_KEY,
        kind="factory-authority-receipt",
    )

    with pytest.raises(AuthorityVerificationError, match="expired"):
        verify_receipt(
            "receipt",
            policy=policy,
            expected_action="ratify-product-specification",
            expected_subject_digest=SUBJECT,
            expected_run_id="run-1",
            tessera=tessera,  # type: ignore[arg-type]
            clock=lambda: 150,
        )
