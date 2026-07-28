"""Authorized-change intake and invariant-document ratification.

The runtime does not infer authority from a ticket, branch, or chat transcript. A canonical
request enters only with a subject-bound human receipt. Each phase artifact then enters only
with distinct human and Validator receipts over the exact artifact digest. Receipt nonces are
consumed in the authoritative run ledger so replay is detectable when the run is re-derived.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory_core.manifest import (
    SegregationPolicy,
    digest_bytes,
    digest_obj,
)
from factory_core.provenance import PhaseArtifact
from factory_runtime.authority import (
    AuthorityPolicy,
    AuthorityVerificationError,
    VerifiedReceipt,
    verify_receipt,
)
from factory_runtime.schema import DocumentValidationError, validate_document
from factory_runtime.state import RunProjection, RunState, RunStore
from factory_runtime.tessera import TesseraCli

_PHASE_ACTIONS: Mapping[str, tuple[str, RunState]] = {
    "product-specification": (
        "ratify-product-specification",
        RunState.PRODUCT_SPECIFICATION_RATIFIED,
    ),
    "architecture": (
        "ratify-architecture",
        RunState.ARCHITECTURE_RATIFIED,
    ),
    "operational-maturity": (
        "ratify-operational-maturity",
        RunState.OPERATIONAL_MATURITY_RATIFIED,
    ),
}


class WorkflowError(ValueError):
    """An intake or ratification could not be authorized without guessing."""


@dataclass(frozen=True)
class StoredRatification:
    """A phase artifact plus the evidence that authorized its exact bytes."""

    artifact: PhaseArtifact
    artifact_digest: str
    human_receipt: VerifiedReceipt
    validator_receipt: VerifiedReceipt
    directory: Path
    projection: RunProjection


def _read_json_object(path: str | Path) -> tuple[dict[str, Any], bytes]:
    source = Path(path)
    try:
        raw = source.read_bytes()
        document = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"unreadable JSON document {source}: {exc}") from exc
    if not isinstance(document, Mapping):
        raise WorkflowError(f"JSON document must be an object: {source}")
    return dict(document), raw


def _write_once(path: Path, content: bytes) -> None:
    """Atomically create immutable evidence, accepting only an identical prior write.

    The final install uses a same-filesystem hard link, whose no-replace behavior closes the
    check-then-replace race that ``os.replace`` would leave open.
    """

    if path.is_symlink():
        raise WorkflowError(f"refusing symlink evidence path: {path}")
    if path.exists():
        if path.is_file() and path.read_bytes() == content:
            return
        raise WorkflowError(f"refusing to replace non-identical evidence: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".evidence-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if not path.is_symlink() and path.is_file() and path.read_bytes() == content:
                return
            raise WorkflowError(f"refusing to replace non-identical evidence: {path}") from None
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _canonical_bytes(document: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    )


def _verified_envelope_bytes(receipt: VerifiedReceipt) -> bytes:
    """Read the exact verified envelope and refuse a verify-to-copy swap."""

    try:
        content = receipt.envelope.path.read_bytes()
    except OSError as exc:
        raise WorkflowError(f"verified receipt envelope became unreadable: {exc}") from exc
    if digest_bytes(content) != receipt.envelope.envelope_digest:
        raise WorkflowError("verified receipt envelope changed before evidence persistence")
    return content


def _segregation_policy(policy: AuthorityPolicy) -> SegregationPolicy:
    humans = frozenset(
        principal.identity
        for principal in policy.principals.values()
        if principal.kind == "human"
    )
    excluded = frozenset(
        principal.identity
        for principal in policy.principals.values()
        if principal.kind != "human"
    )
    return SegregationPolicy(
        human_ids=humans,
        excluded_service_identities=excluded,
        require_signature=True,
        allowlist_digest=policy.genesis_digest,
    )


class FactoryWorkflow:
    """High-level authority boundary over the persisted run state machine."""

    def __init__(
        self,
        root: str | Path,
        *,
        authority_policy: AuthorityPolicy,
        tessera: TesseraCli,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self.root = Path(root)
        self.policy = authority_policy
        self.tessera = tessera
        self.store = RunStore(self.root, clock=clock)
        self._clock = clock

    def authorize_change(
        self,
        run_id: str,
        *,
        target_digest: str,
        request_path: str | Path,
        receipt_path: str | Path,
        actor: str = "validator",
    ) -> RunProjection:
        """Create intake only after a human authorizes the exact canonical request."""

        request, _ = _read_json_object(request_path)
        try:
            validate_document("authorization-request", request)
        except DocumentValidationError as exc:
            raise WorkflowError(str(exc)) from exc
        if request["run_id"] != run_id:
            raise WorkflowError("authorization request belongs to a different Factory run")
        if request["repository_id"] != self.policy.repository_id:
            raise WorkflowError("authorization request belongs to a different repository")
        if request["target_digest"] != target_digest:
            raise WorkflowError("authorization request binds a different target digest")
        verbatim = str(request["verbatim_request"])
        source_digest = digest_bytes(verbatim.encode("utf-8"))
        if source_digest != request["verbatim_request_digest"]:
            raise WorkflowError("authorization request verbatim digest does not re-derive")
        request_digest = digest_obj(request)
        receipt = verify_receipt(
            receipt_path,
            policy=self.policy,
            expected_action="authorize-change",
            expected_subject_digest=request_digest,
            expected_run_id=run_id,
            tessera=self.tessera,
            clock=self._clock,
        )
        principal = self.policy.principal(receipt.signer_identity)
        if principal is None or principal.kind != "human":
            raise AuthorityVerificationError("only an enrolled human may authorize a change")
        if self.policy.bootstrap_enabled and "authorize-change" not in self.policy.bootstrap_scope:
            raise AuthorityVerificationError(
                "bootstrap policy does not permit authorized-change intake"
            )

        evidence_dir = self.root / run_id / "evidence" / "intake"
        _write_once(evidence_dir / "authorization-request.json", _canonical_bytes(request))
        _write_once(
            evidence_dir / "authorization-receipt.tessera.json",
            _verified_envelope_bytes(receipt),
        )
        return self.store.create(
            run_id,
            target_digest=target_digest,
            source_digest=source_digest,
            actor=actor,
            artifact_digests={
                "authorization-request": request_digest,
                "authorization-receipt": receipt.envelope.envelope_digest,
                "authority-genesis": self.policy.genesis_digest,
            },
            payload={
                "authorization_request_id": request["request_id"],
                "authorization_receipt_id": receipt.receipt_id,
                "authority_receipt_nonces": [receipt.nonce],
            },
            approver_identity=receipt.signer_identity,
            policy=_segregation_policy(self.policy),
        )

    def ratify_phase(
        self,
        run_id: str,
        *,
        artifact_path: str | Path,
        human_receipt_path: str | Path,
        validator_receipt_path: str | Path,
        actor: str = "validator",
    ) -> StoredRatification:
        """Ratify one invariant document with independent exact-subject receipts."""

        raw_artifact, _ = _read_json_object(artifact_path)
        try:
            validate_document("phase-artifact", raw_artifact)
        except DocumentValidationError as exc:
            raise WorkflowError(str(exc)) from exc
        artifact = PhaseArtifact.from_dict(raw_artifact)
        current = self.store.load(run_id)
        if artifact.source_digest != current.source_digest:
            raise WorkflowError(
                "phase artifact source digest does not match the authorized verbatim source"
            )
        action_and_state = _PHASE_ACTIONS.get(artifact.phase)
        if action_and_state is None:
            raise WorkflowError(f"unsupported phase artifact: {artifact.phase!r}")
        if artifact.human_ratifier == artifact.validator_ratifier:
            raise WorkflowError("human and Validator ratifiers must be distinct identities")
        item_ids = [item.item_id for item in artifact.items]
        if len(item_ids) != len(set(item_ids)):
            raise WorkflowError("phase artifact contains duplicate item ids")

        action, destination = action_and_state
        artifact_digest = artifact.content_digest
        consumed = self.store.consumed_authority_nonces(run_id)
        human_receipt = verify_receipt(
            human_receipt_path,
            policy=self.policy,
            expected_action=action,
            expected_subject_digest=artifact_digest,
            expected_run_id=run_id,
            expected_signer_identity=artifact.human_ratifier,
            tessera=self.tessera,
            clock=self._clock,
            consumed_nonces=tuple(consumed),
        )
        human = self.policy.principal(human_receipt.signer_identity)
        if human is None or human.kind != "human":
            raise AuthorityVerificationError("phase human ratifier is not an enrolled human")
        validator_receipt = verify_receipt(
            validator_receipt_path,
            policy=self.policy,
            expected_action=action,
            expected_subject_digest=artifact_digest,
            expected_run_id=run_id,
            expected_signer_identity=artifact.validator_ratifier,
            tessera=self.tessera,
            clock=self._clock,
            consumed_nonces=tuple((*consumed, human_receipt.nonce)),
        )
        validator = self.policy.principal(validator_receipt.signer_identity)
        if validator is None or validator.kind == "human":
            raise AuthorityVerificationError(
                "phase Validator ratifier must be an enrolled non-human principal"
            )

        directory = (
            self.root
            / run_id
            / "evidence"
            / artifact.phase
            / artifact_digest.removeprefix("sha256:")
        )
        _write_once(directory / "artifact.json", _canonical_bytes(raw_artifact))
        _write_once(
            directory / "human-receipt.tessera.json",
            _verified_envelope_bytes(human_receipt),
        )
        _write_once(
            directory / "validator-receipt.tessera.json",
            _verified_envelope_bytes(validator_receipt),
        )

        projection = self.store.transition(
            run_id,
            destination,
            actor=actor,
            artifact_digests={
                artifact.phase: artifact_digest,
                f"{artifact.phase}:human-receipt": human_receipt.envelope.envelope_digest,
                f"{artifact.phase}:validator-receipt": (
                    validator_receipt.envelope.envelope_digest
                ),
            },
            payload={
                "artifact_id": artifact.artifact_id,
                "human_receipt_id": human_receipt.receipt_id,
                "validator_receipt_id": validator_receipt.receipt_id,
                "authority_receipt_nonces": [
                    human_receipt.nonce,
                    validator_receipt.nonce,
                ],
            },
            verifier_identity=validator_receipt.signer_identity,
            approver_identity=human_receipt.signer_identity,
            policy=_segregation_policy(self.policy),
        )
        return StoredRatification(
            artifact=artifact,
            artifact_digest=artifact_digest,
            human_receipt=human_receipt,
            validator_receipt=validator_receipt,
            directory=directory,
            projection=projection,
        )
