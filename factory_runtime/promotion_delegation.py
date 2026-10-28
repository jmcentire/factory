"""Project one explicit owner delegation onto exact run policy/profile subjects.

This is not service-identity enrollment or a global role grant. A real owner
receipt, pinned to the existing genesis, must bind the run, delegate and both
reviewed digests. Raw promotion-input allowlists confer no authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from factory_core.criticality import CriticalityProfile
from factory_core.manifest import SegregationPolicy, digest_obj
from factory_core.promotion import PromotionRequest
from factory_runtime.authority import (
    ACTION_CAPABILITY,
    AuthorityPolicy,
    AuthorityVerificationError,
    verify_receipt,
)
from factory_runtime.tessera import TesseraCli


def delegation_subject(
    run_id: str, identity: str, policy_digest: str, profile_digest: str
) -> dict[str, object]:
    return {
        "schema_version": "factory-promotion-delegation/1",
        "run_id": run_id,
        "identity": identity,
        "actions": ["sign-derived-tool-policy", "classify-reviewed-surfaces"],
        "policy_digest": policy_digest,
        "profile_digest": profile_digest,
    }


def apply_delegation(
    run_root: Path,
    raw: Mapping[str, object],
    request: PromotionRequest,
    profile: CriticalityProfile,
    policy: SegregationPolicy,
    *,
    authority: AuthorityPolicy,
    tessera: TesseraCli,
) -> SegregationPolicy:
    """Verify positive authority before admitting either exact delegated role."""
    identity = str(raw.get("identity", ""))
    principal = authority.principal(identity)
    if (
        principal is None
        or principal.kind != "agent"
        or identity != request.verifier
        or ACTION_CAPABILITY["ratify-architecture"] not in principal.capabilities
    ):
        raise AuthorityVerificationError("delegation requires the enrolled run Validator")
    if request.tool_policy is None or request.tool_policy.policy.run_id != run_root.name:
        raise AuthorityVerificationError("delegation tool policy belongs to a different run")
    receipt = (run_root / str(raw.get("receipt", ""))).resolve()
    if not receipt.is_relative_to(run_root.resolve()) or not receipt.is_file():
        raise AuthorityVerificationError("delegation receipt must be a file within this run")
    policy_digest = request.tool_policy.policy.content_digest
    profile_digest = profile.content_digest
    verified = verify_receipt(
        receipt,
        policy=authority,
        expected_action="activate-policy",
        expected_subject_digest=digest_obj(
            delegation_subject(run_root.name, identity, policy_digest, profile_digest)
        ),
        expected_run_id=run_root.name,
        tessera=tessera,
    )
    owner = authority.principal(verified.signer_identity)
    if owner is None or owner.kind != "human":
        raise AuthorityVerificationError("delegation must record the owner's authority")
    return replace(
        policy,
        delegated_policy_signers=frozenset({
            (run_root.name, policy_digest, identity, verified.issued_at, verified.expires_at)
        }),
        delegated_classification_deciders=frozenset({
            (profile_digest, identity, verified.issued_at, verified.expires_at)
        }),
    )
