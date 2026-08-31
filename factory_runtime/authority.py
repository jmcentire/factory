"""Pinned genesis and subject-bound authority receipt verification."""

from __future__ import annotations

import hmac
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from factory_core.manifest import SegregationPolicy
from factory_runtime.schema import validate_document
from factory_runtime.tessera import TesseraCli, TesseraVerificationError, VerifiedEnvelope

_PUBLIC_KEY = re.compile(r"^[0-9a-f]{64}$")

ACTION_CAPABILITY: Mapping[str, str] = {
    "authorize-target-resolution": "factory:authorize-target-resolution",
    "authorize-change": "factory:authorize-change",
    "ratify-product-specification": "factory:ratify-product-specification",
    "ratify-architecture": "factory:ratify-architecture",
    "ratify-operational-maturity": "factory:ratify-operational-maturity",
    "ratify-acceptance-obligation-catalog": (
        "factory:ratify-acceptance-obligation-catalog"
    ),
    "ratify-test-change-authorization": "factory:ratify-test-change-authorization",
    "approve-promotion": "factory:approve-promotion",
    "activate-policy": "factory:activate-policy",
}


class AuthorityVerificationError(ValueError):
    """The externally signed authority chain did not authorize the requested action."""


@dataclass(frozen=True)
class Principal:
    identity: str
    kind: str
    public_key: str
    capabilities: frozenset[str]


@dataclass(frozen=True)
class AuthorityPolicy:
    repository_id: str
    policy_id: str
    root_public_key: str
    principals: Mapping[str, Principal]
    bootstrap_enabled: bool
    bootstrap_scope: frozenset[str]
    genesis_digest: str
    # 2.2: optional founder-root-signed commitment to the chain-key root material
    # (sha256 of .chain-root.key bytes). Empty on pre-2.2 geneses.
    chain_root_commitment: str = ""

    def principal(self, identity: str) -> Principal | None:
        return self.principals.get(identity)

    def segregation_policy(self) -> SegregationPolicy:
        """Project the signed genesis roster onto the core's identity-resolution shape.

        Enrollment is positive and denial wins: an enrolled human resolves as a human, and every
        enrolled agent identity is excluded so it can never resolve as one. Any identity the
        genesis does not enroll resolves as nothing at all.
        """

        humans = frozenset(
            principal.identity
            for principal in self.principals.values()
            if principal.kind == "human"
        )
        return SegregationPolicy(
            human_ids=humans,
            human_aliases={identity.lower(): identity for identity in humans},
            excluded_service_identities=frozenset(
                principal.identity
                for principal in self.principals.values()
                if principal.kind != "human"
            ),
        )


@dataclass(frozen=True)
class VerifiedReceipt:
    receipt_id: str
    run_id: str
    action: str
    subject_digest: str
    signer_identity: str
    capabilities: frozenset[str]
    issued_at: int
    expires_at: int
    nonce: str
    envelope: VerifiedEnvelope


def load_genesis(
    envelope_path: str | Path,
    *,
    trusted_root_public_key: str,
    tessera: TesseraCli,
) -> AuthorityPolicy:
    """Verify a genesis signed by the externally pinned founder root."""

    if not _PUBLIC_KEY.fullmatch(trusted_root_public_key):
        raise AuthorityVerificationError("trusted root public key must be 64 lowercase hex chars")
    try:
        envelope = tessera.verify_json(
            envelope_path,
            trusted_public_keys=(trusted_root_public_key,),
            expected_kind="factory-genesis",
        )
    except TesseraVerificationError as exc:
        raise AuthorityVerificationError(str(exc)) from exc
    validate_document("genesis", envelope.payload)
    root_public_key = str(envelope.payload["root_public_key"])
    if not hmac.compare_digest(root_public_key, trusted_root_public_key):
        raise AuthorityVerificationError(
            "genesis payload root does not match the externally pinned signer"
        )

    principals: dict[str, Principal] = {}
    for raw_principal in envelope.payload["principals"]:
        if not isinstance(raw_principal, Mapping):
            raise AuthorityVerificationError("genesis principal must be an object")
        principal = Principal(
            identity=str(raw_principal["identity"]),
            kind=str(raw_principal["kind"]),
            public_key=str(raw_principal["public_key"]),
            capabilities=frozenset(str(value) for value in raw_principal["capabilities"]),
        )
        if principal.identity in principals:
            raise AuthorityVerificationError(
                f"duplicate genesis principal identity: {principal.identity}"
            )
        if any(
            hmac.compare_digest(principal.public_key, existing.public_key)
            for existing in principals.values()
        ):
            raise AuthorityVerificationError(
                f"genesis reuses a signing key across principals: {principal.identity}"
            )
        principals[principal.identity] = principal

    root_principals = [
        principal
        for principal in principals.values()
        if hmac.compare_digest(principal.public_key, root_public_key)
    ]
    if len(root_principals) != 1 or root_principals[0].kind != "human":
        raise AuthorityVerificationError(
            "genesis root key must belong to exactly one enrolled human principal"
        )
    bootstrap = envelope.payload["bootstrap"]
    if not isinstance(bootstrap, Mapping):
        raise AuthorityVerificationError("genesis bootstrap policy must be an object")
    return AuthorityPolicy(
        repository_id=str(envelope.payload["repository_id"]),
        policy_id=str(envelope.payload["policy_id"]),
        root_public_key=root_public_key,
        principals=principals,
        bootstrap_enabled=bool(bootstrap["enabled"]),
        bootstrap_scope=frozenset(str(value) for value in bootstrap["scope"]),
        genesis_digest=envelope.payload_digest,
        chain_root_commitment=str(envelope.payload.get("chain_root_commitment", "")),
    )


def verify_receipt(
    envelope_path: str | Path,
    *,
    policy: AuthorityPolicy,
    expected_action: str,
    expected_subject_digest: str,
    expected_run_id: str,
    expected_signer_identity: str | None = None,
    tessera: TesseraCli,
    clock: Callable[[], int] | None = None,
    consumed_nonces: Sequence[str] = (),
) -> VerifiedReceipt:
    """Verify signer enrollment, capability, exact subject, expiry, and replay nonce."""

    if expected_action not in ACTION_CAPABILITY:
        raise AuthorityVerificationError(f"unsupported authority action: {expected_action}")
    trusted_keys = tuple(principal.public_key for principal in policy.principals.values())
    try:
        envelope = tessera.verify_json(
            envelope_path,
            trusted_public_keys=trusted_keys,
            expected_kind="factory-authority-receipt",
        )
    except TesseraVerificationError as exc:
        raise AuthorityVerificationError(str(exc)) from exc
    validate_document("authority-receipt", envelope.payload)
    payload = envelope.payload
    run_id = str(payload["run_id"])
    if not hmac.compare_digest(run_id, expected_run_id):
        raise AuthorityVerificationError("receipt belongs to a different Factory run")
    repository_id = str(payload["repository_id"])
    if not hmac.compare_digest(repository_id, policy.repository_id):
        raise AuthorityVerificationError("receipt belongs to a different repository")
    action = str(payload["action"])
    if not hmac.compare_digest(action, expected_action):
        raise AuthorityVerificationError(
            f"receipt action mismatch: expected {expected_action!r}, got {action!r}"
        )
    subject_digest = str(payload["subject_digest"])
    if not hmac.compare_digest(subject_digest, expected_subject_digest):
        raise AuthorityVerificationError("receipt authorizes a different subject digest")
    signer_identity = str(payload["signer_identity"])
    if expected_signer_identity is not None and not hmac.compare_digest(
        signer_identity,
        expected_signer_identity,
    ):
        raise AuthorityVerificationError("receipt signer identity does not match the artifact")
    principal = policy.principal(signer_identity)
    if principal is None:
        raise AuthorityVerificationError(f"receipt signer is not enrolled: {signer_identity}")
    if not hmac.compare_digest(principal.public_key, envelope.public_key):
        raise AuthorityVerificationError("receipt signer label does not own the signing key")

    capabilities = frozenset(str(value) for value in payload["capabilities"])
    required = ACTION_CAPABILITY[action]
    if required not in capabilities or required not in principal.capabilities:
        raise AuthorityVerificationError(
            f"receipt signer lacks required capability: {required}"
        )
    nonce = str(payload["nonce"])
    if nonce in frozenset(consumed_nonces):
        raise AuthorityVerificationError("receipt nonce has already been consumed")
    issued_at = int(payload["issued_at"])
    expires_at = int(payload["expires_at"])
    now = (clock or (lambda: int(time.time())))()
    if issued_at > now:
        raise AuthorityVerificationError("receipt was issued in the future")
    if expires_at <= issued_at:
        raise AuthorityVerificationError("receipt expiry is not after issuance")
    if expires_at < now:
        raise AuthorityVerificationError("receipt has expired")

    return VerifiedReceipt(
        receipt_id=str(payload["receipt_id"]),
        run_id=run_id,
        action=action,
        subject_digest=subject_digest,
        signer_identity=signer_identity,
        capabilities=capabilities,
        issued_at=issued_at,
        expires_at=expires_at,
        nonce=nonce,
        envelope=envelope,
    )
