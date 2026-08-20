"""Two-stage target authority, authorized-change intake, and phase ratification.

The runtime does not infer authority from a ticket, branch, or chat transcript. A canonical
request enters only with a subject-bound human receipt. Each phase artifact then enters only
with distinct human and Validator receipts over the exact artifact digest. Receipt nonces are
consumed in the authoritative run ledger so replay is detectable when the run is re-derived.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory_core.manifest import (
    SegregationPolicy,
    digest_bytes,
    digest_obj,
)
from factory_core.provenance import REQUIRED_PHASES, IntentBackreference, PhaseArtifact
from factory_core.target import TargetManifestError, load_target_manifest
from factory_runtime.authority import (
    AuthorityPolicy,
    AuthorityVerificationError,
    VerifiedReceipt,
    verify_receipt,
)
from factory_runtime.durability import DurabilityError, fsync_directory_chain
from factory_runtime.evidence_plane import TesseraEvidenceEnvelopeVerifier
from factory_runtime.schema import DocumentValidationError, validate_document
from factory_runtime.state import RunProjection, RunState, RunStore
from factory_runtime.target_state import (
    TargetResolutionError,
    TargetResolver,
    normalize_repository_url,
    normalize_subpath,
    verify_target_state,
)
from factory_runtime.tessera import TesseraCli, TesseraVerificationError, VerifiedEnvelope

_MAX_REPAIR_BRIEF_BYTES = 65_536

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


@dataclass(frozen=True)
class VerifiedRepairBrief:
    """A repair envelope plus the exact bytes verified from its retained path."""

    envelope: VerifiedEnvelope
    content: bytes


def _read_json_object(path: str | Path) -> tuple[dict[str, Any], bytes]:
    source = Path(path)
    if source.is_symlink():
        raise WorkflowError(f"refusing symlink JSON document: {source}")
    try:
        raw = source.read_bytes()
        document = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"unreadable JSON document {source}: {exc}") from exc
    if not isinstance(document, Mapping):
        raise WorkflowError(f"JSON document must be an object: {source}")
    return dict(document), raw


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sync_identical_evidence(path: Path, content: bytes) -> bool:
    """Verify and durably retain one already-installed exact evidence file."""

    if path.is_symlink():
        return False
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size != len(content):
            return False
        chunks: list[bytes] = []
        remaining = len(content) + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        if b"".join(chunks) != content or identity != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            return False
        os.fsync(descriptor)
        installed = os.lstat(path)
        if stat.S_ISLNK(installed.st_mode) or (
            installed.st_dev,
            installed.st_ino,
        ) != (after.st_dev, after.st_ino):
            return False
    except OSError as exc:
        raise WorkflowError(f"could not durably verify existing evidence {path}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _fsync_directory(path.parent)
    return True


def _sync_evidence_chain(path: Path, durable_root: Path | None) -> None:
    if durable_root is None:
        return
    try:
        fsync_directory_chain(path.parent, through=durable_root)
    except DurabilityError as exc:
        raise WorkflowError(str(exc)) from exc


def _write_once(
    path: Path,
    content: bytes,
    *,
    durable_root: Path | None = None,
) -> None:
    """Atomically create immutable evidence, accepting only an identical prior write.

    The final install uses a same-filesystem hard link, whose no-replace behavior closes the
    check-then-replace race that ``os.replace`` would leave open.
    """

    if path.is_symlink():
        raise WorkflowError(f"refusing symlink evidence path: {path}")
    if path.exists():
        if _sync_identical_evidence(path, content):
            _sync_evidence_chain(path, durable_root)
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
            _fsync_directory(path.parent)
            _sync_evidence_chain(path, durable_root)
        except FileExistsError:
            if _sync_identical_evidence(path, content):
                _sync_evidence_chain(path, durable_root)
                return
            raise WorkflowError(f"refusing to replace non-identical evidence: {path}") from None
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _canonical_bytes(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _verified_envelope_bytes(receipt: VerifiedReceipt) -> bytes:
    """Read the exact verified envelope and refuse a verify-to-copy swap."""

    return _verified_tessera_bytes(receipt.envelope)


def _verified_tessera_bytes(envelope: VerifiedEnvelope) -> bytes:
    """Read exact already-verified Tessera bytes and refuse a verify-to-copy swap."""

    if envelope.path.is_symlink():
        raise WorkflowError("verified receipt envelope may not be a symlink")
    try:
        content = envelope.path.read_bytes()
    except OSError as exc:
        raise WorkflowError(f"verified receipt envelope became unreadable: {exc}") from exc
    if digest_bytes(content) != envelope.envelope_digest:
        raise WorkflowError("verified receipt envelope changed before evidence persistence")
    return content


def _retained_intent_backreferences(
    root: Path,
    run_id: str,
    phase_artifact_digests: Mapping[str, str],
) -> frozenset[IntentBackreference]:
    """Resolve exact canonical intent pointers from the run-retained phase artifacts."""

    if set(phase_artifact_digests) != set(REQUIRED_PHASES):
        raise WorkflowError("repair brief requires the exact three ratified phase artifacts")
    resolved: set[IntentBackreference] = set()
    for phase in REQUIRED_PHASES:
        expected_digest = str(phase_artifact_digests[phase])
        path = (
            root
            / run_id
            / "evidence"
            / phase
            / expected_digest.removeprefix("sha256:")
            / "artifact.json"
        )
        raw, _ = _read_json_object(path)
        try:
            validate_document("phase-artifact", raw)
        except DocumentValidationError as exc:
            raise WorkflowError(str(exc)) from exc
        artifact = PhaseArtifact.from_dict(raw)
        if artifact.phase != phase or artifact.content_digest != expected_digest:
            raise WorkflowError(f"retained {phase} artifact differs from the run ledger")
        resolved.update(artifact.backreference(item) for item in artifact.items)
    return frozenset(resolved)


def _latest_build_attempt_id(entries: tuple[Mapping[str, Any], ...]) -> str:
    for entry in reversed(entries):
        if entry.get("to_state") != RunState.BUILDING:
            continue
        payload = entry.get("payload")
        if isinstance(payload, Mapping):
            attempt_id = str(payload.get("attempt_id", ""))
            if attempt_id:
                return attempt_id
    return ""


def _causal_validator_identity(entry: Mapping[str, Any], *, context: str) -> str:
    """Return the failed attempt's Validator only when all three lane identities are proven."""

    payload = entry.get("payload")
    if (
        entry.get("to_state") != RunState.BLOCKED
        or entry.get("from_state") not in {RunState.BUILDING, RunState.VALIDATING}
        or not isinstance(payload, Mapping)
        or payload.get("reason") == "repair-brief-recorded"
    ):
        raise WorkflowError(f"{context} is not a causal failed build attempt")
    identities = {
        "Coder": str(entry.get("implementer_identity", "")).strip(),
        "Tester": str(payload.get("tester_identity", "")).strip(),
        "Validator": str(entry.get("verifier_identity", "")).strip(),
    }
    missing = [role for role, identity in identities.items() if not identity]
    if missing:
        raise WorkflowError(f"{context} omits causal identity role(s): " + ", ".join(missing))
    if len(set(identities.values())) != 3:
        raise WorkflowError(f"{context} Coder, Tester, and Validator are not distinct")
    return identities["Validator"]


def _segregation_policy(policy: AuthorityPolicy) -> SegregationPolicy:
    humans = frozenset(
        principal.identity for principal in policy.principals.values() if principal.kind == "human"
    )
    excluded = frozenset(
        principal.identity for principal in policy.principals.values() if principal.kind != "human"
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
        self.store = RunStore(
            self.root,
            clock=clock,
            preview_evidence_verifier=TesseraEvidenceEnvelopeVerifier(
                tessera=tessera,
                authority_policy=authority_policy,
            ),
        )
        self._clock = clock

    def _require_current_authority_genesis(
        self,
        run_id: str,
        *,
        expected_ledger_head: str,
    ) -> None:
        """Bind every post-creation authority operation to this run's founder genesis."""

        entries = self.store.verified_ledger_entries(run_id)
        genesis_artifacts = entries[0].get("artifact_digests") if entries else None
        if not isinstance(genesis_artifacts, Mapping):
            raise WorkflowError("Stage R has no authority genesis")
        if entries[-1].get("entry_hash") != expected_ledger_head:
            raise WorkflowError("run ledger changed while authority genesis was checked")
        if genesis_artifacts.get("authority-genesis") != self.policy.genesis_digest:
            raise WorkflowError("current authority genesis differs from Stage R")

    def authorize_target_resolution(
        self,
        run_id: str,
        *,
        manifest_path: str | Path,
        request_path: str | Path,
        receipt_path: str | Path,
        actor: str = "validator",
    ) -> RunProjection:
        """Create Stage R before any repository contact or source inspection."""

        request, _ = _read_json_object(request_path)
        try:
            validate_document("target-resolution-request", request)
            if Path(manifest_path).is_symlink():
                raise WorkflowError("target manifest may not be a symlink")
            manifest = load_target_manifest(manifest_path)
            normalized_url = normalize_repository_url(str(manifest.repo["url"]))
            subpath = normalize_subpath(str(manifest.repo.get("subpath", "")))
        except (DocumentValidationError, TargetManifestError, TargetResolutionError) as exc:
            raise WorkflowError(str(exc)) from exc
        if request["run_id"] != run_id:
            raise WorkflowError("target-resolution request belongs to a different Factory run")
        if request["repository_id"] != self.policy.repository_id:
            raise WorkflowError("target-resolution request belongs to a different repository")
        if request["generation"] != 1:
            raise WorkflowError("new target-resolution requests must begin at generation 1")
        if request["target_manifest_digest"] != manifest.content_digest:
            raise WorkflowError("target-resolution request binds another target manifest")
        if request["target_manifest_source_digest"] != manifest.source_digest:
            raise WorkflowError("target-resolution request binds other manifest bytes")
        if request["normalized_url"] != normalized_url:
            raise WorkflowError("target-resolution request binds another repository URL")
        if request["requested_ref"] != str(manifest.repo["ref"]):
            raise WorkflowError("target-resolution request binds another ref")
        if request["subpath"] != subpath:
            raise WorkflowError("target-resolution request binds another subpath")
        request_digest = digest_obj(request)
        receipt = verify_receipt(
            receipt_path,
            policy=self.policy,
            expected_action="authorize-target-resolution",
            expected_subject_digest=request_digest,
            expected_run_id=run_id,
            tessera=self.tessera,
            clock=self._clock,
        )
        principal = self.policy.principal(receipt.signer_identity)
        if principal is None or principal.kind != "human":
            raise AuthorityVerificationError(
                "only an enrolled human may authorize target resolution"
            )
        if receipt.nonce != request["nonce"]:
            raise AuthorityVerificationError(
                "target-resolution receipt nonce differs from the signed request"
            )
        if receipt.expires_at != request["expires_at"]:
            raise AuthorityVerificationError(
                "target-resolution receipt expiry differs from the signed request"
            )
        now = (self._clock or (lambda: int(time.time())))()
        if request["created_at"] > now:
            raise AuthorityVerificationError("target-resolution request was created in the future")
        if request["expires_at"] <= request["created_at"]:
            raise AuthorityVerificationError(
                "target-resolution request expiry is not after creation"
            )
        if self.policy.bootstrap_enabled and (
            "authorize-target-resolution" not in self.policy.bootstrap_scope
        ):
            raise AuthorityVerificationError("bootstrap policy does not permit target resolution")

        evidence_dir = self.root / run_id / "evidence" / "target-resolution"
        manifest_bytes = Path(manifest_path).read_bytes()
        if digest_bytes(manifest_bytes) != manifest.source_digest:
            raise WorkflowError("target manifest changed before evidence persistence")
        _write_once(
            evidence_dir / "target-manifest.toml",
            manifest_bytes,
            durable_root=self.root,
        )
        _write_once(
            evidence_dir / "target-resolution-request.json",
            _canonical_bytes(request),
            durable_root=self.root,
        )
        _write_once(
            evidence_dir / "target-resolution-receipt.tessera.json",
            _verified_envelope_bytes(receipt),
            durable_root=self.root,
        )
        return self.store.create(
            run_id,
            target_digest=manifest.content_digest,
            actor=actor,
            artifact_digests={
                "target-manifest-source": manifest.source_digest,
                "target-resolution-request": request_digest,
                "target-resolution-receipt": receipt.envelope.envelope_digest,
                "authority-genesis": self.policy.genesis_digest,
            },
            payload={
                "target_resolution_request_id": request["request_id"],
                "target_resolution_receipt_id": receipt.receipt_id,
                "authority_receipt_nonces": [receipt.nonce],
            },
            approver_identity=receipt.signer_identity,
            policy=_segregation_policy(self.policy),
        )

    def resolve_target(
        self,
        run_id: str,
        *,
        object_source: str | Path | None = None,
        actor: str = "target-resolver",
    ) -> RunProjection:
        """Resolve only the retained Stage-R subject into a run-owned checkout."""

        current = self.store.load(run_id)
        if current.state != RunState.TARGET_RESOLUTION_AUTHORIZED:
            raise WorkflowError("target resolution requires target-resolution-authorized state")
        evidence_dir = self.root / run_id / "evidence" / "target-resolution"
        manifest_path = evidence_dir / "target-manifest.toml"
        request_path = evidence_dir / "target-resolution-request.json"
        receipt_path = evidence_dir / "target-resolution-receipt.tessera.json"
        if manifest_path.is_symlink():
            raise WorkflowError("retained target manifest may not be a symlink")
        try:
            manifest = load_target_manifest(manifest_path)
            request, _ = _read_json_object(request_path)
            validate_document("target-resolution-request", request)
        except (TargetManifestError, DocumentValidationError) as exc:
            raise WorkflowError(str(exc)) from exc
        self._require_current_authority_genesis(
            run_id,
            expected_ledger_head=current.ledger_head,
        )
        artifacts = self.store.current_artifact_digests(run_id)
        if manifest.content_digest != current.target_digest:
            raise WorkflowError("retained target manifest differs from the run subject")
        if manifest.source_digest != artifacts.get("target-manifest-source"):
            raise WorkflowError("retained target manifest bytes differ from Stage R")
        if digest_obj(request) != artifacts.get("target-resolution-request"):
            raise WorkflowError("retained target-resolution request differs from Stage R")
        now = (self._clock or (lambda: int(time.time())))()
        if request["expires_at"] <= now:
            raise WorkflowError("target-resolution authority expired before repository contact")
        try:
            receipt = verify_receipt(
                receipt_path,
                policy=self.policy,
                expected_action="authorize-target-resolution",
                expected_subject_digest=digest_obj(request),
                expected_run_id=run_id,
                tessera=self.tessera,
                clock=self._clock,
            )
        except AuthorityVerificationError as exc:
            raise WorkflowError(f"retained target-resolution authority is invalid: {exc}") from exc
        if receipt.envelope.envelope_digest != artifacts.get("target-resolution-receipt"):
            raise WorkflowError("retained target-resolution receipt differs from Stage R")
        principal = self.policy.principal(receipt.signer_identity)
        if principal is None or principal.kind != "human":
            raise WorkflowError("retained target-resolution authority is not human")
        if receipt.nonce != request["nonce"] or receipt.expires_at != request["expires_at"]:
            raise WorkflowError("retained target-resolution receipt differs from its request")
        resolver = TargetResolver(
            self.root / run_id,
            run_id,
            repository_id=self.policy.repository_id,
            generation=current.generation,
            clock=self._clock,
        )
        try:
            target_state = resolver.resolve(
                manifest=manifest,
                request=request,
                object_source=object_source,
            )
        except TargetResolutionError as exc:
            raise WorkflowError(str(exc)) from exc
        _write_once(
            evidence_dir / "target-state.json",
            _canonical_bytes(target_state),
            durable_root=self.root,
        )
        return self.store.record_target_state(
            run_id,
            target_state=target_state,
            actor=actor,
            artifact_digests={
                "resource-ledger": str(target_state["resource_ledger_head"]),
            },
            payload={"observation_method": target_state["observation_method"]},
        )

    def authorize_change(
        self,
        run_id: str,
        *,
        request_path: str | Path,
        receipt_path: str | Path,
        actor: str = "validator",
    ) -> RunProjection:
        """Create intake only after a human authorizes the exact resolved target state."""

        current = self.store.load(run_id)
        if current.state != RunState.TARGET_RESOLVED:
            raise WorkflowError("authorized-change intake requires target-resolved state")
        self._require_current_authority_genesis(
            run_id,
            expected_ledger_head=current.ledger_head,
        )
        request, _ = _read_json_object(request_path)
        try:
            validate_document("execution-request", request)
        except DocumentValidationError as exc:
            raise WorkflowError(str(exc)) from exc
        if request["run_id"] != run_id:
            raise WorkflowError("execution request belongs to a different Factory run")
        if request["repository_id"] != self.policy.repository_id:
            raise WorkflowError("execution request belongs to a different repository")
        if request["generation"] != current.generation:
            raise WorkflowError("execution request binds a different run generation")
        if request["target_manifest_digest"] != current.target_digest:
            raise WorkflowError("execution request binds a different target manifest")
        if request["target_state_digest"] != current.target_state_digest:
            raise WorkflowError("execution request binds a different target-state")
        if request["resolved_commit"] != current.target_state.get("resolved_commit"):
            raise WorkflowError("execution request binds a different resolved commit")
        target_state_path = (
            self.root / run_id / "evidence" / "target-resolution" / "target-state.json"
        )
        retained_target_state, _ = _read_json_object(target_state_path)
        if digest_obj(retained_target_state) != current.target_state_digest:
            raise WorkflowError("retained target-state bytes differ from the run ledger")
        try:
            verify_target_state(
                retained_target_state,
                expected_digest=current.target_state_digest,
            )
        except TargetResolutionError as exc:
            raise WorkflowError(str(exc)) from exc
        verbatim = str(request["verbatim_request"])
        source_digest = digest_bytes(verbatim.encode("utf-8"))
        if source_digest != request["verbatim_request_digest"]:
            raise WorkflowError("execution request verbatim digest does not re-derive")
        request_digest = digest_obj(request)
        receipt = verify_receipt(
            receipt_path,
            policy=self.policy,
            expected_action="authorize-change",
            expected_subject_digest=request_digest,
            expected_run_id=run_id,
            tessera=self.tessera,
            clock=self._clock,
            consumed_nonces=tuple(self.store.consumed_authority_nonces(run_id)),
        )
        principal = self.policy.principal(receipt.signer_identity)
        if principal is None or principal.kind != "human":
            raise AuthorityVerificationError("only an enrolled human may authorize a change")
        if self.policy.bootstrap_enabled and "authorize-change" not in self.policy.bootstrap_scope:
            raise AuthorityVerificationError(
                "bootstrap policy does not permit authorized-change intake"
            )

        evidence_dir = self.root / run_id / "evidence" / "intake"
        _write_once(
            evidence_dir / "execution-request.json",
            _canonical_bytes(request),
            durable_root=self.root,
        )
        _write_once(
            evidence_dir / "execution-receipt.tessera.json",
            _verified_envelope_bytes(receipt),
            durable_root=self.root,
        )
        return self.store.authorize_intake(
            run_id,
            source_digest=source_digest,
            actor=actor,
            artifact_digests={
                "execution-request": request_digest,
                "execution-receipt": receipt.envelope.envelope_digest,
                "authority-genesis": self.policy.genesis_digest,
            },
            payload={
                "execution_request_id": request["request_id"],
                "execution_receipt_id": receipt.receipt_id,
                "authority_receipt_nonces": [receipt.nonce],
            },
            approver_identity=receipt.signer_identity,
            policy=_segregation_policy(self.policy),
        )

    def record_repair_brief(
        self,
        run_id: str,
        *,
        expected_ledger_head: str,
        brief_digest: str,
        envelope: VerifiedEnvelope,
        validator_identity: str,
    ) -> RunProjection:
        """Record a signed Validator diagnosis without changing phase authority.

        A Repair Brief is derived operational guidance, never a new or amended
        requirement.  It is therefore bound to the exact blocked ledger head
        and the already-ratified phase artifact digests before it is retained.
        """

        current = self.store.load(run_id)
        if current.state != RunState.BLOCKED:
            raise WorkflowError("repair brief requires a blocked run")
        self._require_current_authority_genesis(
            run_id,
            expected_ledger_head=current.ledger_head,
        )
        if current.ledger_head != expected_ledger_head:
            raise WorkflowError("repair brief predecessor ledger head changed")
        entries = self.store.verified_ledger_entries(run_id)
        if not entries or entries[-1].get("entry_hash") != expected_ledger_head:
            raise WorkflowError("repair brief predecessor ledger head is not current")
        causal_validator = _causal_validator_identity(
            entries[-1],
            context="repair brief predecessor",
        )
        if validator_identity != causal_validator:
            raise WorkflowError(
                "repair brief signer must be the Validator of the causal failed attempt"
            )
        principal = self.policy.principal(validator_identity)
        if principal is None or principal.kind != "agent":
            raise WorkflowError("repair brief signer must be an enrolled Validator agent")
        if envelope.public_key != principal.public_key:
            raise WorkflowError("repair brief envelope signer does not own Validator identity")
        if envelope.kind != "factory-repair-brief":
            raise WorkflowError("repair brief envelope has the wrong kind")
        if envelope.payload_digest != brief_digest:
            raise WorkflowError("repair brief envelope binds a different document")
        payload = envelope.payload
        try:
            validate_document("repair-brief", payload)
        except DocumentValidationError as exc:
            raise WorkflowError(str(exc)) from exc
        if digest_obj(dict(payload)) != brief_digest:
            raise WorkflowError("repair brief digest does not re-derive from its payload")
        if payload.get("run_id") != run_id:
            raise WorkflowError("repair brief belongs to a different run")
        if payload.get("predecessor_ledger_head") != expected_ledger_head:
            raise WorkflowError("repair brief does not bind the blocked ledger head")
        if payload.get("phase_artifact_digests") != dict(current.phase_artifact_digests):
            raise WorkflowError("repair brief changes or omits ratified phase authority")
        current_artifacts = self.store.current_artifact_digests(run_id)
        for payload_key, artifact_key in (
            ("candidate_digest", "candidate"),
            ("oracle_digest", "acceptance-tests"),
        ):
            retained = str(current_artifacts.get(artifact_key, ""))
            if not retained:
                raise WorkflowError(
                    "repair brief requires a retained candidate and acceptance-test subject"
                )
            if payload.get(payload_key) != retained:
                raise WorkflowError(
                    f"repair brief {payload_key} differs from the blocked attempt ledger"
                )
        if payload.get("failed_attempt_id") != _latest_build_attempt_id(entries):
            raise WorkflowError("repair brief does not name the causal build attempt")
        raw_references = payload.get("intent_backreferences")
        if not isinstance(raw_references, list):
            raise WorkflowError("repair brief intent_backreferences must be an array")
        references = tuple(
            IntentBackreference.from_dict(item)
            for item in raw_references
            if isinstance(item, Mapping)
        )
        if len(references) != len(raw_references) or len(references) != len(set(references)):
            raise WorkflowError("repair brief intent backreferences are malformed or repeated")
        resolved = _retained_intent_backreferences(
            self.root,
            run_id,
            current.phase_artifact_digests,
        )
        unresolved = [reference for reference in references if reference not in resolved]
        if unresolved:
            raise WorkflowError("repair brief contains an unresolved intent backreference")
        if payload.get("authorized_attempt_id") in self.store.build_attempt_ids(run_id):
            raise WorkflowError("repair brief authorizes a previously committed attempt id")

        directory = self.root / run_id / "evidence" / "repair-briefs"
        stem = envelope.payload_digest.removeprefix("sha256:")
        _write_once(
            directory / f"{stem}.tessera.json",
            _verified_tessera_bytes(envelope),
            durable_root=self.root,
        )
        return self.store.transition(
            run_id,
            RunState.BLOCKED,
            actor="repair-supervisor",
            artifact_digests={
                "repair-brief": brief_digest,
                "repair-brief-envelope": envelope.envelope_digest,
            },
            payload={
                "reason": "repair-brief-recorded",
                "predecessor_ledger_head": expected_ledger_head,
                "repair_brief_digest": brief_digest,
                "repair_brief_envelope_digest": envelope.envelope_digest,
                "repair_signal": "retry",
                "authorized_attempt_id": payload["authorized_attempt_id"],
                "failure_signature": payload["failure_signature"],
                "authority_receipt_nonces": [],
            },
            verifier_identity=validator_identity,
        )

    def verify_recorded_repair_brief(
        self,
        run_id: str,
        *,
        envelope_path: str | Path,
        validator_identity: str,
        expected_attempt_id: str,
    ) -> VerifiedRepairBrief:
        """Verify the exact latest repair event before its Coder-visible retry executes."""

        path = Path(envelope_path)
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise WorkflowError(f"recorded repair brief is unreadable: {exc}") from exc
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_REPAIR_BRIEF_BYTES:
            raise WorkflowError("recorded repair brief is not regular or exceeds its byte ceiling")
        current = self.store.load(run_id)
        if current.state != RunState.BLOCKED:
            raise WorkflowError("recorded repair brief requires a blocked run")
        self._require_current_authority_genesis(
            run_id,
            expected_ledger_head=current.ledger_head,
        )
        principal = self.policy.principal(validator_identity)
        if principal is None or principal.kind != "agent":
            raise WorkflowError("repair brief verifier must be an enrolled Validator agent")
        try:
            envelope = self.tessera.verify_json(
                path,
                trusted_public_keys=(principal.public_key,),
                expected_kind="factory-repair-brief",
            )
            validate_document("repair-brief", envelope.payload)
        except (TesseraVerificationError, DocumentValidationError) as exc:
            raise WorkflowError(str(exc)) from exc
        exact_bytes = _verified_tessera_bytes(envelope)
        if len(exact_bytes) > _MAX_REPAIR_BRIEF_BYTES:
            raise WorkflowError("recorded repair brief exceeds its byte ceiling")
        canonical = (
            self.root
            / run_id
            / "evidence"
            / "repair-briefs"
            / f"{envelope.payload_digest.removeprefix('sha256:')}.tessera.json"
        )
        try:
            if path.resolve(strict=True) != canonical.resolve(strict=True):
                raise WorkflowError("repair brief path is not the retained canonical evidence")
        except OSError as exc:
            raise WorkflowError(f"repair brief canonical path is unreadable: {exc}") from exc

        entries = self.store.verified_ledger_entries(run_id)
        if len(entries) < 2:
            raise WorkflowError("recorded repair brief has no causal blocked predecessor")
        event = entries[-1]
        predecessor = entries[-2]
        event_payload = event.get("payload")
        event_artifacts = event.get("artifact_digests")
        predecessor_artifacts = predecessor.get("artifact_digests")
        if not isinstance(event_payload, Mapping) or not isinstance(event_artifacts, Mapping):
            raise WorkflowError("recorded repair event is structurally incomplete")
        if not isinstance(predecessor_artifacts, Mapping):
            raise WorkflowError("recorded repair predecessor has no artifact bindings")
        causal_validator = _causal_validator_identity(
            predecessor,
            context="recorded repair predecessor",
        )
        if validator_identity != causal_validator:
            raise WorkflowError(
                "repair brief verifier must be the Validator of the causal failed attempt"
            )
        if (
            event.get("from_state") != RunState.BLOCKED
            or event.get("to_state") != RunState.BLOCKED
            or event_payload.get("reason") != "repair-brief-recorded"
        ):
            raise WorkflowError("latest ledger event is not a repair-brief authorization")
        if event_payload.get("predecessor_ledger_head") != predecessor.get("entry_hash"):
            raise WorkflowError("recorded repair event does not bind its causal predecessor")
        if event.get("verifier_identity") != causal_validator:
            raise WorkflowError("recorded repair event names a different Validator")
        if event_artifacts.get("repair-brief") != envelope.payload_digest:
            raise WorkflowError("recorded repair event binds a different brief payload")
        if event_artifacts.get("repair-brief-envelope") != envelope.envelope_digest:
            raise WorkflowError("recorded repair event binds different signed bytes")
        payload = envelope.payload
        if payload.get("run_id") != run_id:
            raise WorkflowError("recorded repair brief belongs to another run")
        if payload.get("predecessor_ledger_head") != predecessor.get("entry_hash"):
            raise WorkflowError("recorded repair brief does not bind its blocked predecessor")
        if payload.get("phase_artifact_digests") != dict(current.phase_artifact_digests):
            raise WorkflowError("recorded repair brief differs from current phase authority")
        if payload.get("failed_attempt_id") != _latest_build_attempt_id(entries[:-1]):
            raise WorkflowError("recorded repair brief does not name the causal build attempt")
        if payload.get("authorized_attempt_id") != expected_attempt_id:
            raise WorkflowError("recorded repair brief authorizes a different next attempt")
        if event_payload.get("authorized_attempt_id") != payload.get("authorized_attempt_id"):
            raise WorkflowError("recorded repair event authorizes a different next attempt")
        if event_payload.get("failure_signature") != payload.get("failure_signature"):
            raise WorkflowError("recorded repair event binds a different failure signature")
        for payload_key, artifact_key in (
            ("candidate_digest", "candidate"),
            ("oracle_digest", "acceptance-tests"),
        ):
            if payload.get(payload_key) != predecessor_artifacts.get(artifact_key):
                raise WorkflowError(
                    f"recorded repair brief {payload_key} differs from its failed subject"
                )
        raw_references = payload.get("intent_backreferences")
        reference_items = raw_references if isinstance(raw_references, list) else []
        references = tuple(
            IntentBackreference.from_dict(item)
            for item in reference_items
            if isinstance(item, Mapping)
        )
        if not isinstance(raw_references, list) or len(references) != len(raw_references):
            raise WorkflowError("recorded repair brief intent backreferences are malformed")
        resolved = _retained_intent_backreferences(
            self.root,
            run_id,
            current.phase_artifact_digests,
        )
        if len(references) != len(set(references)) or any(
            reference not in resolved for reference in references
        ):
            raise WorkflowError("recorded repair brief has repeated or unresolved authority")
        return VerifiedRepairBrief(envelope=envelope, content=exact_bytes)

    def recover_or_verify_repair_brief(
        self,
        run_id: str,
        *,
        envelope_path: str | Path,
        validator_identity: str,
        expected_attempt_id: str,
    ) -> VerifiedRepairBrief:
        """Resume from either a ledgered brief or an authenticated pre-ledger orphan.

        The canonical envelope is published before its BLOCKED-to-BLOCKED authority event.  If
        the process stops in that narrow window, this method revalidates the exact canonical
        bytes against the causal Validator and admits the previously unledgered event.  It never
        treats an orphan file as authority by itself.
        """

        entries = self.store.verified_ledger_entries(run_id)
        latest_payload = entries[-1].get("payload") if entries else None
        if isinstance(latest_payload, Mapping) and latest_payload.get("reason") == (
            "repair-brief-recorded"
        ):
            return self.verify_recorded_repair_brief(
                run_id,
                envelope_path=envelope_path,
                validator_identity=validator_identity,
                expected_attempt_id=expected_attempt_id,
            )

        path = Path(envelope_path)
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise WorkflowError(f"repair recovery envelope is unreadable: {exc}") from exc
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_REPAIR_BRIEF_BYTES:
            raise WorkflowError(
                "repair recovery envelope is not regular or exceeds its byte ceiling"
            )
        current = self.store.load(run_id)
        if current.state != RunState.BLOCKED:
            raise WorkflowError("repair recovery requires a blocked run")
        self._require_current_authority_genesis(
            run_id,
            expected_ledger_head=current.ledger_head,
        )
        principal = self.policy.principal(validator_identity)
        if principal is None or principal.kind != "agent":
            raise WorkflowError("repair recovery signer must be an enrolled Validator agent")
        try:
            envelope = self.tessera.verify_json(
                path,
                trusted_public_keys=(principal.public_key,),
                expected_kind="factory-repair-brief",
            )
            validate_document("repair-brief", envelope.payload)
        except (TesseraVerificationError, DocumentValidationError) as exc:
            raise WorkflowError(str(exc)) from exc
        exact_bytes = _verified_tessera_bytes(envelope)
        if len(exact_bytes) > _MAX_REPAIR_BRIEF_BYTES:
            raise WorkflowError("repair recovery envelope exceeds its byte ceiling")
        canonical = (
            self.root
            / run_id
            / "evidence"
            / "repair-briefs"
            / f"{envelope.payload_digest.removeprefix('sha256:')}.tessera.json"
        )
        try:
            if path.resolve(strict=True) != canonical.resolve(strict=True):
                raise WorkflowError("repair recovery path is not the canonical evidence address")
        except OSError as exc:
            raise WorkflowError(f"repair recovery canonical path is unreadable: {exc}") from exc
        if envelope.payload.get("authorized_attempt_id") != expected_attempt_id:
            raise WorkflowError("repair recovery envelope authorizes a different next attempt")

        self.record_repair_brief(
            run_id,
            expected_ledger_head=current.ledger_head,
            brief_digest=envelope.payload_digest,
            envelope=envelope,
            validator_identity=validator_identity,
        )
        return self.verify_recorded_repair_brief(
            run_id,
            envelope_path=path,
            validator_identity=validator_identity,
            expected_attempt_id=expected_attempt_id,
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
        self._require_current_authority_genesis(
            run_id,
            expected_ledger_head=current.ledger_head,
        )
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
        if validator is None or validator.kind != "agent":
            raise AuthorityVerificationError(
                "phase Validator ratifier must be an enrolled agent principal"
            )

        directory = (
            self.root
            / run_id
            / "evidence"
            / artifact.phase
            / artifact_digest.removeprefix("sha256:")
        )
        _write_once(
            directory / "artifact.json",
            _canonical_bytes(raw_artifact),
            durable_root=self.root,
        )
        _write_once(
            directory / "human-receipt.tessera.json",
            _verified_envelope_bytes(human_receipt),
            durable_root=self.root,
        )
        _write_once(
            directory / "validator-receipt.tessera.json",
            _verified_envelope_bytes(validator_receipt),
            durable_root=self.root,
        )

        projection = self.store.transition(
            run_id,
            destination,
            actor=actor,
            artifact_digests={
                artifact.phase: artifact_digest,
                f"{artifact.phase}:human-receipt": human_receipt.envelope.envelope_digest,
                f"{artifact.phase}:validator-receipt": (validator_receipt.envelope.envelope_digest),
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
