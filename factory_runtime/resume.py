"""Externally anchored resume verification for executable Factory dispatch.

The checkpoint bytes and their expected digest are consumed before the mutable run root is
opened.  A locally adjacent checkpoint is therefore not self-authenticating: callers must obtain
the digest from independent custody and freeze it for the process/session.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory_core.manifest import Ledger, LedgerIntegrityError, digest_bytes, digest_obj
from factory_runtime.authority import (
    AuthorityPolicy,
    AuthorityVerificationError,
    load_genesis,
    verify_receipt,
)
from factory_runtime.durability import load_chain_key
from factory_runtime.evidence_plane import TesseraEvidenceEnvelopeVerifier
from factory_runtime.resources import ResourceLedger, ResourceLedgerError
from factory_runtime.schema import DocumentValidationError, validate_document
from factory_runtime.state import RunState, RunStateError, RunStore
from factory_runtime.snapshot import SnapshotError, tree_digest
from factory_runtime.state_admission import StateAdmissionError, read_stable_regular_bytes
from factory_runtime.target_state import TargetResolutionError, verify_target_state
from factory_runtime.tessera import TesseraCli, TesseraVerificationError

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_DOCUMENT_BYTES = 1_048_576


class ResumeVerificationError(ValueError):
    """A resume checkpoint did not anchor the exact retained executable state."""


@dataclass(frozen=True)
class ResumeVerification:
    """Fresh verification result bound to the current, race-checkable run head."""

    run_id: str
    checkpoint_digest: str
    checkpoint_source_digest: str
    checkpoint_id: str
    anchored_run_ledger_head: str
    anchored_run_ledger_length: int
    current_run_ledger_head: str
    current_run_ledger_length: int
    current_resource_ledger_head: str
    current_resource_ledger_length: int
    acceptance_obligation_catalog_digest: str
    configuration_digests: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        """Return the stable, security-relevant resume evidence surface."""

        return {
            "run_id": self.run_id,
            "checkpoint_digest": self.checkpoint_digest,
            "checkpoint_source_digest": self.checkpoint_source_digest,
            "checkpoint_id": self.checkpoint_id,
            "anchored_run_ledger_head": self.anchored_run_ledger_head,
            "anchored_run_ledger_length": self.anchored_run_ledger_length,
            "current_run_ledger_head": self.current_run_ledger_head,
            "current_run_ledger_length": self.current_run_ledger_length,
            "current_resource_ledger_head": self.current_resource_ledger_head,
            "current_resource_ledger_length": self.current_resource_ledger_length,
            "acceptance_obligation_catalog_digest": (
                self.acceptance_obligation_catalog_digest
            ),
            "configuration_digests": dict(self.configuration_digests),
        }

    def state_admission_dict(self) -> dict[str, Any]:
        """Return only resume facts that condition the model's admitted state.

        Resource-ledger state is deliberately absent. Dispatch creates and dispositions
        run-owned resources after the model capsule is frozen, while the broker independently
        re-derives the current resource ledger before resolving any operation. Including that
        moving operational ledger in model context would make every normal dispatch stale without
        adding authority or confinement.
        """

        document = self.to_dict()
        del document["current_resource_ledger_head"]
        del document["current_resource_ledger_length"]
        return document


@dataclass(frozen=True)
class _BoundState:
    policy: AuthorityPolicy
    projection: Any
    genesis_envelope_digest: str
    target_resolution_request_digest: str
    target_resolution_receipt_digest: str
    execution_request_digest: str
    execution_receipt_digest: str
    run_entries: tuple[Mapping[str, Any], ...]
    resource_entries: tuple[Mapping[str, Any], ...]
    resource_seal_digest: str
    acceptance_obligation_catalog_digest: str
    configuration_digests: Mapping[str, str]


def _read_object(path: str | Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = read_stable_regular_bytes(
            path,
            label=label,
            max_bytes=_MAX_DOCUMENT_BYTES,
        )
    except StateAdmissionError as exc:
        raise ResumeVerificationError(str(exc)) from exc
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ResumeVerificationError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ResumeVerificationError(f"{label} must be a JSON object")
    return document, raw


def _configuration_digests(sources: Mapping[str, str | Path]) -> dict[str, str]:
    if not sources:
        raise ResumeVerificationError(
            "at least one externally named configuration source is required"
        )
    result: dict[str, str] = {}
    for name, raw_path in sorted(sources.items()):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name):
            raise ResumeVerificationError(f"invalid configuration source name: {name!r}")
        path = Path(raw_path)
        if not path.is_symlink() and path.is_dir():
            # Sealed author outputs are directories: an attempt configuration must
            # name them as sources, and the external checkpoint must pin them just
            # as exactly as any file. The content address of a directory source is
            # its deterministic tree digest, re-derived identically at derive and
            # verify time.
            try:
                result[name] = tree_digest(path)
            except SnapshotError as exc:
                raise ResumeVerificationError(
                    f"configuration source {name!r} tree is invalid: {exc}"
                ) from exc
            continue
        try:
            raw = read_stable_regular_bytes(
                raw_path,
                label=f"configuration source {name!r}",
                max_bytes=_MAX_DOCUMENT_BYTES,
            )
        except StateAdmissionError as exc:
            if "exceeds" not in str(exc):
                raise ResumeVerificationError(str(exc)) from exc
            # A qualified lane executable (for example the Validator's exact
            # interpreter) is legitimate checkpoint configuration and larger than
            # a document. Stream-digest it with the same stability guarantee:
            # the file identity must not change across the read.
            result[name] = _stable_stream_digest(path, name)
            continue
        result[name] = digest_bytes(raw)
    return result


def _stable_stream_digest(path: Path, name: str) -> str:
    import hashlib

    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        )
    except OSError as exc:
        raise ResumeVerificationError(
            f"configuration source {name!r} is unavailable: {exc}"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ResumeVerificationError(f"configuration source {name!r} is not regular")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in identity):
            raise ResumeVerificationError(
                f"configuration source {name!r} changed during the read"
            )
        return "sha256:" + digest.hexdigest()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _entries(path: Path, *, label: str) -> tuple[Mapping[str, Any], ...]:
    try:
        return tuple(
            Ledger(str(path), chain_key=load_chain_key(path)).verified_entries()
        )
    except LedgerIntegrityError as exc:
        raise ResumeVerificationError(f"{label} verification failed: {exc}") from exc


def _entry_time(entry: Mapping[str, Any], *, label: str) -> int:
    try:
        value = int(str(entry["created_at"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ResumeVerificationError(f"{label} has no canonical creation time") from exc
    if value < 1:
        raise ResumeVerificationError(f"{label} has an invalid creation time")
    return value


def _artifact_map(entry: Mapping[str, Any], *, label: str) -> Mapping[str, Any]:
    value = entry.get("artifact_digests")
    if not isinstance(value, Mapping):
        raise ResumeVerificationError(f"{label} has no artifact digest map")
    return value


def _verify_authority_receipt(
    path: Path,
    *,
    policy: AuthorityPolicy,
    action: str,
    subject_digest: str,
    run_id: str,
    consumed_at: int,
    expected_envelope_digest: str,
    tessera: TesseraCli,
) -> str:
    try:
        receipt = verify_receipt(
            path,
            policy=policy,
            expected_action=action,
            expected_subject_digest=subject_digest,
            expected_run_id=run_id,
            tessera=tessera,
            clock=lambda: consumed_at,
        )
    except (AuthorityVerificationError, DocumentValidationError) as exc:
        raise ResumeVerificationError(f"retained {action} receipt is invalid: {exc}") from exc
    if receipt.envelope.envelope_digest != expected_envelope_digest:
        raise ResumeVerificationError(f"retained {action} envelope differs from the run ledger")
    return receipt.envelope.envelope_digest


def _derive_bound_state(
    runs_root: str | Path,
    run_id: str,
    *,
    genesis_path: str | Path,
    trusted_root_public_key: str,
    tessera: TesseraCli,
    configuration_sources: Mapping[str, str | Path],
    expected_acceptance_obligation_catalog_digest: str | None = None,
) -> _BoundState:
    try:
        genesis = tessera.verify_json(
            genesis_path,
            trusted_public_keys=(trusted_root_public_key,),
            expected_kind="factory-genesis",
        )
        policy = load_genesis(
            genesis_path,
            trusted_root_public_key=trusted_root_public_key,
            tessera=tessera,
        )
    except (TesseraVerificationError, AuthorityVerificationError, DocumentValidationError) as exc:
        raise ResumeVerificationError(f"authority genesis is invalid: {exc}") from exc

    root = Path(runs_root)
    run_dir = root / run_id
    try:
        projection = RunStore(
            root,
            preview_evidence_verifier=TesseraEvidenceEnvelopeVerifier(
                tessera=tessera,
                authority_policy=policy,
            ),
        ).load(run_id)
    except RunStateError as exc:
        raise ResumeVerificationError(str(exc)) from exc
    if projection.state in {
        RunState.TARGET_RESOLUTION_AUTHORIZED,
        RunState.TARGET_RESOLVED,
    }:
        raise ResumeVerificationError("resume dispatch requires retained Stage-E intake authority")
    if policy.repository_id != projection.target_state.get("repository_id"):
        raise ResumeVerificationError("genesis repository differs from retained target-state")
    if Path(str(projection.target_state.get("control_root", ""))).resolve() != run_dir.resolve():
        raise ResumeVerificationError("retained target-state control root was substituted")
    active_catalog_digest = str(projection.acceptance_obligation_catalog_digest)
    catalog_digest = (
        active_catalog_digest
        if expected_acceptance_obligation_catalog_digest is None
        else expected_acceptance_obligation_catalog_digest
    )
    if catalog_digest and not _DIGEST.fullmatch(catalog_digest):
        raise ResumeVerificationError("acceptance-obligation catalog digest is not canonical")
    if active_catalog_digest and catalog_digest != active_catalog_digest:
        raise ResumeVerificationError(
            "externally expected acceptance-obligation catalog differs from the active catalog"
        )
    retained_target_state, _ = _read_object(
        run_dir / "evidence" / "target-resolution" / "target-state.json",
        label="retained target-state",
    )
    try:
        verify_target_state(retained_target_state, expected_digest=projection.target_state_digest)
    except TargetResolutionError as exc:
        raise ResumeVerificationError(str(exc)) from exc

    run_entries = _entries(run_dir / "ledger.jsonl", label="run ledger")
    if not run_entries or run_entries[-1].get("entry_hash") != projection.ledger_head:
        raise ResumeVerificationError("run projection is not bound to the verified lifecycle head")
    stage_r = run_entries[0]
    intake_matches = [entry for entry in run_entries if entry.get("to_state") == RunState.INTAKE]
    if len(intake_matches) != 1:
        raise ResumeVerificationError("run must contain exactly one Stage-E intake entry")
    stage_e = intake_matches[0]
    stage_r_artifacts = _artifact_map(stage_r, label="Stage-R entry")
    stage_e_artifacts = _artifact_map(stage_e, label="Stage-E entry")
    if stage_r_artifacts.get("authority-genesis") != genesis.payload_digest:
        raise ResumeVerificationError("Stage-R genesis differs from the externally pinned genesis")
    if stage_e_artifacts.get("authority-genesis") != genesis.payload_digest:
        raise ResumeVerificationError("Stage-E genesis differs from the externally pinned genesis")

    resolution_dir = run_dir / "evidence" / "target-resolution"
    stage_r_request, _ = _read_object(
        resolution_dir / "target-resolution-request.json",
        label="retained Stage-R request",
    )
    validate_document("target-resolution-request", stage_r_request)
    stage_r_request_digest = digest_obj(stage_r_request)
    if stage_r_request_digest != stage_r_artifacts.get("target-resolution-request"):
        raise ResumeVerificationError("retained Stage-R request differs from the run ledger")
    stage_r_receipt_digest = _verify_authority_receipt(
        resolution_dir / "target-resolution-receipt.tessera.json",
        policy=policy,
        action="authorize-target-resolution",
        subject_digest=stage_r_request_digest,
        run_id=run_id,
        consumed_at=_entry_time(stage_r, label="Stage-R entry"),
        expected_envelope_digest=str(stage_r_artifacts.get("target-resolution-receipt", "")),
        tessera=tessera,
    )

    intake_dir = run_dir / "evidence" / "intake"
    stage_e_request, _ = _read_object(
        intake_dir / "execution-request.json",
        label="retained Stage-E request",
    )
    validate_document("execution-request", stage_e_request)
    stage_e_request_digest = digest_obj(stage_e_request)
    if stage_e_request_digest != stage_e_artifacts.get("execution-request"):
        raise ResumeVerificationError("retained Stage-E request differs from the run ledger")
    stage_e_receipt_digest = _verify_authority_receipt(
        intake_dir / "execution-receipt.tessera.json",
        policy=policy,
        action="authorize-change",
        subject_digest=stage_e_request_digest,
        run_id=run_id,
        consumed_at=_entry_time(stage_e, label="Stage-E entry"),
        expected_envelope_digest=str(stage_e_artifacts.get("execution-receipt", "")),
        tessera=tessera,
    )

    resources = ResourceLedger(run_dir, run_id)
    try:
        resources.records()
        resource_entries = _entries(resources.path, label="resource ledger")
        seal = resources.terminal_seal()
    except ResourceLedgerError as exc:
        raise ResumeVerificationError(str(exc)) from exc
    if not resource_entries:
        raise ResumeVerificationError("resume dispatch requires a non-empty resource ledger")
    return _BoundState(
        policy=policy,
        projection=projection,
        genesis_envelope_digest=genesis.envelope_digest,
        target_resolution_request_digest=stage_r_request_digest,
        target_resolution_receipt_digest=stage_r_receipt_digest,
        execution_request_digest=stage_e_request_digest,
        execution_receipt_digest=stage_e_receipt_digest,
        run_entries=run_entries,
        resource_entries=resource_entries,
        resource_seal_digest=str(seal["seal_digest"]) if seal else "",
        acceptance_obligation_catalog_digest=catalog_digest,
        configuration_digests=_configuration_digests(configuration_sources),
    )


def derive_resume_checkpoint(
    runs_root: str | Path,
    run_id: str,
    *,
    checkpoint_id: str,
    previous_checkpoint_digest: str,
    genesis_path: str | Path,
    trusted_root_public_key: str,
    tessera: TesseraCli,
    configuration_sources: Mapping[str, str | Path],
    acceptance_obligation_catalog_digest: str | None = None,
    retention: Mapping[str, Any],
    clock: Callable[[], int],
) -> dict[str, Any]:
    """Derive bytes for independent custody; deriving does not itself create an anchor."""

    bound = _derive_bound_state(
        runs_root,
        run_id,
        genesis_path=genesis_path,
        trusted_root_public_key=trusted_root_public_key,
        tessera=tessera,
        configuration_sources=configuration_sources,
        expected_acceptance_obligation_catalog_digest=(
            acceptance_obligation_catalog_digest
        ),
    )
    document = {
        "schema_version": "factory-resume-checkpoint/1",
        "checkpoint_id": checkpoint_id,
        "previous_checkpoint_digest": previous_checkpoint_digest,
        "repository_id": bound.policy.repository_id,
        "run_id": run_id,
        "trusted_root_public_key": trusted_root_public_key,
        "genesis_envelope_digest": bound.genesis_envelope_digest,
        "genesis_payload_digest": bound.policy.genesis_digest,
        "target_resolution_request_digest": bound.target_resolution_request_digest,
        "target_resolution_receipt_digest": bound.target_resolution_receipt_digest,
        "execution_request_digest": bound.execution_request_digest,
        "execution_receipt_digest": bound.execution_receipt_digest,
        "target_manifest_digest": bound.projection.target_digest,
        "target_state_digest": bound.projection.target_state_digest,
        "source_digest": bound.projection.source_digest,
        "generation": bound.projection.generation,
        "acceptance_obligation_catalog_digest": (
            bound.acceptance_obligation_catalog_digest
        ),
        "run_ledger_head": str(bound.run_entries[-1]["entry_hash"]),
        "run_ledger_length": len(bound.run_entries),
        "resource_ledger_head": str(bound.resource_entries[-1]["entry_hash"]),
        "resource_ledger_length": len(bound.resource_entries),
        "resource_seal_digest": bound.resource_seal_digest,
        "configuration_digests": dict(bound.configuration_digests),
        "retention": dict(retention),
        "issued_at": int(clock()),
    }
    try:
        validate_document("resume-checkpoint", document)
    except DocumentValidationError as exc:
        raise ResumeVerificationError(str(exc)) from exc
    mode = document["retention"]["mode"]
    retain_until = int(document["retention"]["retain_until"])
    if (mode == "retain-until") != (retain_until > document["issued_at"]):
        raise ResumeVerificationError(
            "retain-until requires a future retain_until; other retention modes require zero"
        )
    return document


def verify_resume_checkpoint(
    checkpoint_path: str | Path,
    *,
    expected_checkpoint_digest: str,
    runs_root: str | Path,
    run_id: str,
    genesis_path: str | Path,
    trusted_root_public_key: str,
    tessera: TesseraCli,
    configuration_sources: Mapping[str, str | Path],
    expected_acceptance_obligation_catalog_digest: str | None = None,
    accepted_previous_checkpoint_digests: Sequence[str] = (),
) -> ResumeVerification:
    """Verify an independently pinned checkpoint before admitting mutable-root state."""

    # Ordering is intentional: freeze and validate the external subject before opening runs_root.
    checkpoint, checkpoint_bytes = _read_object(checkpoint_path, label="resume checkpoint")
    if not _DIGEST.fullmatch(expected_checkpoint_digest):
        raise ResumeVerificationError("expected checkpoint digest is not canonical")
    checkpoint_digest = digest_obj(checkpoint)
    if checkpoint_digest != expected_checkpoint_digest:
        raise ResumeVerificationError("resume checkpoint differs from the externally pinned digest")
    try:
        validate_document("resume-checkpoint", checkpoint)
    except DocumentValidationError as exc:
        raise ResumeVerificationError(str(exc)) from exc
    if checkpoint["run_id"] != run_id:
        raise ResumeVerificationError("resume checkpoint belongs to another run")
    if checkpoint["trusted_root_public_key"] != trusted_root_public_key:
        raise ResumeVerificationError("resume checkpoint substitutes the trusted root")
    previous = str(checkpoint["previous_checkpoint_digest"])
    accepted = tuple(accepted_previous_checkpoint_digests)
    if len(accepted) != len(set(accepted)) or any(not _DIGEST.fullmatch(item) for item in accepted):
        raise ResumeVerificationError("accepted predecessor digests are not canonical and unique")
    if previous:
        if not accepted or previous not in set(accepted):
            raise ResumeVerificationError("resume checkpoint forks from an unaccepted predecessor")
    elif accepted:
        raise ResumeVerificationError("genesis resume checkpoint cannot claim a predecessor")

    bound = _derive_bound_state(
        runs_root,
        run_id,
        genesis_path=genesis_path,
        trusted_root_public_key=trusted_root_public_key,
        tessera=tessera,
        configuration_sources=configuration_sources,
        expected_acceptance_obligation_catalog_digest=(
            expected_acceptance_obligation_catalog_digest
        ),
    )
    exact = {
        "repository_id": bound.policy.repository_id,
        "genesis_envelope_digest": bound.genesis_envelope_digest,
        "genesis_payload_digest": bound.policy.genesis_digest,
        "target_resolution_request_digest": bound.target_resolution_request_digest,
        "target_resolution_receipt_digest": bound.target_resolution_receipt_digest,
        "execution_request_digest": bound.execution_request_digest,
        "execution_receipt_digest": bound.execution_receipt_digest,
        "target_manifest_digest": bound.projection.target_digest,
        "target_state_digest": bound.projection.target_state_digest,
        "source_digest": bound.projection.source_digest,
        "generation": bound.projection.generation,
        "acceptance_obligation_catalog_digest": (
            bound.acceptance_obligation_catalog_digest
        ),
        "configuration_digests": dict(bound.configuration_digests),
    }
    for field, expected in exact.items():
        if checkpoint[field] != expected:
            raise ResumeVerificationError(f"resume checkpoint has stale or substituted {field}")

    run_length = int(checkpoint["run_ledger_length"])
    resource_length = int(checkpoint["resource_ledger_length"])
    if run_length > len(bound.run_entries) or resource_length > len(bound.resource_entries):
        raise ResumeVerificationError("mutable ledgers rolled back behind the external checkpoint")
    if bound.run_entries[run_length - 1].get("entry_hash") != checkpoint["run_ledger_head"]:
        raise ResumeVerificationError("run ledger forked before the external checkpoint")
    if (
        bound.resource_entries[resource_length - 1].get("entry_hash")
        != checkpoint["resource_ledger_head"]
    ):
        raise ResumeVerificationError("resource ledger forked before the external checkpoint")
    if checkpoint["resource_seal_digest"]:
        if bound.resource_seal_digest != checkpoint["resource_seal_digest"]:
            raise ResumeVerificationError("terminal resource seal differs from the checkpoint")
        if resource_length != len(bound.resource_entries):
            raise ResumeVerificationError("sealed resource ledger advanced after its checkpoint")

    return ResumeVerification(
        run_id=run_id,
        checkpoint_digest=checkpoint_digest,
        checkpoint_source_digest=digest_bytes(checkpoint_bytes),
        checkpoint_id=str(checkpoint["checkpoint_id"]),
        anchored_run_ledger_head=str(checkpoint["run_ledger_head"]),
        anchored_run_ledger_length=run_length,
        current_run_ledger_head=str(bound.run_entries[-1]["entry_hash"]),
        current_run_ledger_length=len(bound.run_entries),
        current_resource_ledger_head=str(bound.resource_entries[-1]["entry_hash"]),
        current_resource_ledger_length=len(bound.resource_entries),
        acceptance_obligation_catalog_digest=bound.acceptance_obligation_catalog_digest,
        configuration_digests=dict(bound.configuration_digests),
    )
