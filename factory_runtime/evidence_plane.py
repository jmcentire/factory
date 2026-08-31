"""Append-as-observed checklist evidence and reproducible change-evidence bundles."""

from __future__ import annotations

import hmac
import json
import os
import re
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from factory_core.checklist import (
    ChecklistItemResult,
    ChecklistReport,
    verify_checklist,
)
from factory_core.correction import (
    LANE_CORRECTION,
    LANES,
    CorrectionRecord,
    CorrectionReport,
    verify_correction,
)
from factory_core.criticality import BASE_REQUIRED_EVIDENCE_IDS, ResolvedSurface
from factory_core.evidence import EvidenceIntegrity
from factory_core.independence import (
    IndependenceRecord,
    IndependenceReport,
    verify_independence,
)
from factory_core.manifest import Ledger, LedgerEntry, SegregationPolicy, digest_bytes, digest_obj
from factory_core.monitors import Monitor, MonitorSetReport, verify_monitor_set
from factory_core.provenance import (
    IntentBackreference,
    PhaseArtifact,
    ProvenanceBundle,
    ProvenanceClaim,
    ProvenanceReport,
)
from factory_runtime.authority import AuthorityPolicy
from factory_runtime.durability import load_chain_key
from factory_runtime.generation import GenerationError, verify_prepared_generation
from factory_runtime.schema import DocumentValidationError, validate_document
from factory_runtime.snapshot import SnapshotError, tree_digest, verify_frozen_tree
from factory_runtime.state import RunState, RunStore
from factory_runtime.state_admission import StateAdmissionError, read_stable_regular_bytes
from factory_runtime.tessera import TesseraCli, TesseraVerificationError


class EvidencePlaneError(ValueError):
    """Evidence could not be re-derived from its authoritative sources."""


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ATTEMPT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
EVIDENCE_VERIFICATION_RECEIPT_VERSION = "factory-evidence-verification-receipt/1"
TESSERA_EVIDENCE_VERIFIER_ID = "factory-tessera-evidence-verifier/1"
PREVIEW_VALIDATED_ARTIFACT_KEYS = (
    "acceptance-obligation-report",
    "validator-review-subject",
    "validator-adversarial-review",
    "base-source-snapshot",
    "candidate-change-set",
    "validator-review-authority-context",
    "validator-review-observations-source",
    "validator-execution-manifest",
    "validator-execution-configuration",
    "validator-execution-environment",
    "validator-execution-snapshot",
)
PREVIEW_ADMISSION_ARTIFACT_KEYS = (
    "candidate",
    "acceptance-tests",
    "coder-output-snapshot",
    "tester-output-snapshot",
    *PREVIEW_VALIDATED_ARTIFACT_KEYS,
)


def build_preview_admission(
    *,
    run_schema_version: str,
    run_id: str,
    generation: int,
    validating_ledger_head: str,
    authority_genesis_digest: str,
    implementer_identity: str,
    tester_identity: str,
    verifier_identity: str,
    artifact_digests: Mapping[str, str],
) -> dict[str, Any]:
    """Build the exact non-circular PREVIEW subject authenticated by the Validator."""

    if run_schema_version != "factory-run/5":
        raise EvidencePlaneError("preview admission subject requires factory-run/5")
    if not _ATTEMPT_ID.fullmatch(run_id):
        raise EvidencePlaneError("preview admission subject has an invalid run id")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise EvidencePlaneError("preview admission subject has an invalid generation")
    for label, value in (
        ("validating ledger head", validating_ledger_head),
        ("authority genesis digest", authority_genesis_digest),
    ):
        if not _DIGEST.fullmatch(value):
            raise EvidencePlaneError(f"preview admission {label} is not a content address")
    identities = {
        "implementer": implementer_identity.strip(),
        "tester": tester_identity.strip(),
        "verifier": verifier_identity.strip(),
    }
    if any(not identity for identity in identities.values()) or len(set(identities.values())) != 3:
        raise EvidencePlaneError(
            "preview admission requires distinct Coder, Tester, and Validator identities"
        )
    supplied = dict(artifact_digests)
    if set(supplied) != set(PREVIEW_ADMISSION_ARTIFACT_KEYS):
        missing = sorted(set(PREVIEW_ADMISSION_ARTIFACT_KEYS) - set(supplied))
        extra = sorted(set(supplied) - set(PREVIEW_ADMISSION_ARTIFACT_KEYS))
        raise EvidencePlaneError(
            "preview admission artifact map is incomplete or open: "
            f"missing={missing}, extra={extra}"
        )
    for key, value in supplied.items():
        if not _DIGEST.fullmatch(value):
            raise EvidencePlaneError(
                f"preview admission artifact {key!r} is not a content address"
            )
    return {
        "run_schema_version": run_schema_version,
        "run_id": run_id,
        "generation": generation,
        "source": "validating",
        "destination": "preview",
        "validating_ledger_head": validating_ledger_head,
        "authority_genesis_digest": authority_genesis_digest,
        "identities": identities,
        "artifact_digests": {key: supplied[key] for key in PREVIEW_ADMISSION_ARTIFACT_KEYS},
    }


@dataclass(frozen=True)
class EvidenceVerificationReceipt:
    """Deterministic audit record of one externally authenticated evidence envelope.

    The receipt is deliberately not an authority token.  ``RunStore`` persists it so a replay can
    require the configured verifier to reproduce the same result from the exact retained bytes.
    A caller-supplied or ledger-only receipt is never sufficient.
    """

    schema_version: str
    verifier_id: str
    authority_genesis_digest: str
    signer_identity: str
    signer_public_key: str
    envelope_digest: str
    payload_digest: str

    def to_dict(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "verifier_id": self.verifier_id,
            "authority_genesis_digest": self.authority_genesis_digest,
            "signer_identity": self.signer_identity,
            "signer_public_key": self.signer_public_key,
            "envelope_digest": self.envelope_digest,
            "payload_digest": self.payload_digest,
        }


@dataclass(frozen=True)
class VerifiedEvidenceEnvelope:
    """Exact payload plus the receipt produced by an authenticated verifier."""

    payload: Mapping[str, Any]
    receipt: EvidenceVerificationReceipt


class EvidenceEnvelopeVerifier(Protocol):
    """Host-supplied cryptographic boundary required for PREVIEW admission and replay."""

    def verify(
        self,
        envelope_path: Path,
        *,
        expected_kind: str,
        expected_payload_digest: str,
        expected_envelope_digest: str,
        expected_signer_identity: str,
        expected_authority_genesis_digest: str,
    ) -> VerifiedEvidenceEnvelope: ...


class TesseraEvidenceEnvelopeVerifier:
    """Bind real Tessera validation to the signer enrolled by the active authority genesis."""

    def __init__(self, *, tessera: TesseraCli, authority_policy: AuthorityPolicy) -> None:
        self._tessera = tessera
        self._policy = authority_policy

    def verify(
        self,
        envelope_path: Path,
        *,
        expected_kind: str,
        expected_payload_digest: str,
        expected_envelope_digest: str,
        expected_signer_identity: str,
        expected_authority_genesis_digest: str,
    ) -> VerifiedEvidenceEnvelope:
        if not expected_signer_identity.strip():
            raise EvidencePlaneError("evidence envelope has no Validator signer identity")
        if not hmac.compare_digest(
            self._policy.genesis_digest,
            expected_authority_genesis_digest,
        ):
            raise EvidencePlaneError(
                "evidence verifier authority policy differs from the run authority genesis"
            )
        # 4.1b (fourth demotion site): the preview evidence envelope's Validator
        # signature is ATTRIBUTION, not authority — the signature and enrolled
        # principal are verified so provenance is meaningful, but promotion
        # authority lives in the host-verified evidence and the human seats.
        principal = self._policy.principal(expected_signer_identity)
        if principal is None or principal.kind != "agent":
            raise EvidencePlaneError(
                "evidence envelope attribution is not an enrolled agent principal"
            )
        verification_copy = ""
        try:
            retained = read_stable_regular_bytes(
                envelope_path,
                label="retained signed evidence bundle",
                max_bytes=16 * 1024 * 1024,
            )
            retained_digest = digest_bytes(retained)
            if not hmac.compare_digest(retained_digest, expected_envelope_digest):
                raise EvidencePlaneError(
                    "retained evidence envelope differs from its ledger address"
                )
            descriptor, verification_copy = tempfile.mkstemp(
                prefix=".factory-evidence-verification-",
                suffix=".tessera.json",
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(retained)
                handle.flush()
                os.fsync(handle.fileno())
            verified = self._tessera.verify_json(
                verification_copy,
                trusted_public_keys=(principal.public_key,),
                expected_kind=expected_kind,
                expected_payload_digest=expected_payload_digest,
            )
            retained_after = read_stable_regular_bytes(
                envelope_path,
                label="retained signed evidence bundle",
                max_bytes=16 * 1024 * 1024,
            )
        except (OSError, TesseraVerificationError, StateAdmissionError) as exc:
            raise EvidencePlaneError(f"Tessera refused retained evidence: {exc}") from exc
        finally:
            if verification_copy and os.path.exists(verification_copy):
                os.unlink(verification_copy)
        if not hmac.compare_digest(verified.envelope_digest, expected_envelope_digest):
            raise EvidencePlaneError("retained evidence envelope differs from its ledger address")
        if not hmac.compare_digest(digest_bytes(retained_after), verified.envelope_digest):
            raise EvidencePlaneError("retained evidence envelope changed during verification")
        if not hmac.compare_digest(verified.public_key, principal.public_key):
            raise EvidencePlaneError(
                "evidence envelope signer identity does not own the Tessera signing key"
            )
        return VerifiedEvidenceEnvelope(
            payload=dict(verified.payload),
            receipt=EvidenceVerificationReceipt(
                schema_version=EVIDENCE_VERIFICATION_RECEIPT_VERSION,
                verifier_id=TESSERA_EVIDENCE_VERIFIER_ID,
                authority_genesis_digest=self._policy.genesis_digest,
                signer_identity=expected_signer_identity,
                signer_public_key=verified.public_key,
                envelope_digest=verified.envelope_digest,
                payload_digest=verified.payload_digest,
            ),
        )


def verify_retained_evidence_bundle(
    run_dir: str | Path,
    *,
    attempt_id: str,
    payload_digest: str,
    envelope_digest: str,
    run_id: str,
    target_digest: str,
    source_digest: str,
    candidate_digest: str,
    acceptance_tests_digest: str,
    generation_artifacts: Mapping[str, str],
    coder_snapshot_digest: str,
    tester_snapshot_digest: str,
    attempt_number: int,
    attempt_limit: int,
    validating_ledger_head: str,
    expected_preview_admission: Mapping[str, Any] | None,
    verifier_identity: str,
    authority_genesis_digest: str,
    verifier: EvidenceEnvelopeVerifier,
) -> VerifiedEvidenceEnvelope:
    """Reopen the exact signed bundle and rederive its authoritative subject bindings.

    Both admission and replay invoke the same explicit verifier.  The verifier authenticates the
    exact retained bytes and binds their public key to the named Validator under the run's
    externally verified authority genesis; this function then rederives the complete semantic
    subject.  A shaped signature or a prior orchestration-side check is never sufficient.
    """

    if not _ATTEMPT_ID.fullmatch(attempt_id):
        raise EvidencePlaneError("evidence bundle has an invalid attempt id")
    for label, value in (
        ("evidence bundle digest", payload_digest),
        ("evidence envelope digest", envelope_digest),
    ):
        if not _DIGEST.fullmatch(value):
            raise EvidencePlaneError(f"{label} is not a canonical content address")
    path = Path(run_dir) / "evidence" / "build-attempts" / attempt_id / (
        "evidence-bundle.tessera.json"
    )
    verified = verifier.verify(
        path,
        expected_kind="factory-evidence-bundle",
        expected_payload_digest=payload_digest,
        expected_envelope_digest=envelope_digest,
        expected_signer_identity=verifier_identity,
        expected_authority_genesis_digest=authority_genesis_digest,
    )
    payload = dict(verified.payload)
    if digest_obj(payload) != payload_digest:
        raise EvidencePlaneError("retained evidence bundle payload does not rederive its address")
    try:
        validate_document("evidence-bundle", payload)
    except DocumentValidationError as exc:
        raise EvidencePlaneError(f"retained evidence bundle payload is invalid: {exc}") from exc
    expected = {
        "run_id": run_id,
        "target_digest": target_digest,
        "source_digest": source_digest,
        "candidate_digest": candidate_digest,
        "acceptance_tests_digest": acceptance_tests_digest,
        "generation_artifacts": dict(generation_artifacts),
        "review_snapshots": {
            "coder-output": coder_snapshot_digest,
            "tester-output": tester_snapshot_digest,
        },
        "build_attempt": {"number": attempt_number, "limit": attempt_limit},
        "ledger_head": validating_ledger_head,
    }
    schema_version = payload.get("schema_version")
    if expected_preview_admission is None:
        if schema_version != "factory-evidence-bundle/2":
            raise EvidencePlaneError("released run/4 replay requires evidence bundle/2")
    else:
        if schema_version != "factory-evidence-bundle/3":
            raise EvidencePlaneError("factory-run/5 PREVIEW requires evidence bundle/3")
        expected["preview_admission"] = dict(expected_preview_admission)
    for field, expected_value in expected.items():
        if payload.get(field) != expected_value:
            raise EvidencePlaneError(
                f"retained evidence bundle has stale or substituted {field}"
            )
    return VerifiedEvidenceEnvelope(payload=payload, receipt=verified.receipt)


@dataclass(frozen=True)
class SurfaceEvidence:
    surface_id: str
    criticality: str
    oracle_adequate: bool
    required_evidence_ids: tuple[str, ...]
    evidence_digests: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "criticality": self.criticality,
            "oracle_adequate": self.oracle_adequate,
            "required_evidence_ids": list(self.required_evidence_ids),
            "evidence_digests": dict(self.evidence_digests),
        }


@dataclass(frozen=True)
class DeterminismRecord:
    surface_id: str
    criticality: str
    deterministic: bool
    flake_count: int
    automatic_retry_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "criticality": self.criticality,
            "deterministic": self.deterministic,
            "flake_count": self.flake_count,
            "automatic_retry_count": self.automatic_retry_count,
        }


@dataclass(frozen=True)
class EvidenceBundleReport:
    document: Mapping[str, Any]
    provenance: ProvenanceReport
    checklist: ChecklistReport
    blocking_issues: tuple[str, ...]
    gate_issues: tuple[str, ...]
    reports: tuple[str, ...]
    independence: IndependenceReport | None = None
    monitors: MonitorSetReport | None = None
    correction: CorrectionReport | None = None

    @property
    def mechanically_satisfied(self) -> bool:
        return self.provenance.satisfied and self.checklist.satisfied and not self.blocking_issues


class ChecklistJournal:
    """Hash-chained checklist records persisted at the moment each observation is made."""

    def __init__(
        self,
        path: str | Path,
        *,
        subject_digest: str,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self.path = Path(path)
        self.subject_digest = subject_digest
        self._clock = clock or (lambda: int(time.time()))
        self._ledger = Ledger(str(self.path), chain_key=load_chain_key(self.path))

    def record(
        self,
        item_id: str,
        *,
        passed: bool,
        detail: str,
        actor: str,
        observations: Mapping[str, Any] | None = None,
    ) -> ChecklistItemResult:
        """Persist one independently bound result; no end-of-run reconstruction."""

        ok, chain_detail = self._ledger.verify_chain()
        if not ok:
            raise EvidencePlaneError(
                f"refusing to append to a damaged checklist journal: {chain_detail}"
            )
        unsigned = ChecklistItemResult(
            id=item_id,
            passed=passed,
            detail=detail,
            recorded_at=self._clock(),
        )
        body = {
            **dict(observations or {}),
            **unsigned.authority_body(self.subject_digest),
        }
        result = replace(
            unsigned,
            evidence=EvidenceIntegrity(body=body, claimed_digest=digest_obj(body)),
        )
        assert result.evidence is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ledger.append(
            LedgerEntry(
                capability_id=f"checklist:{self.subject_digest}",
                from_state=str(len(self._ledger.entries())),
                to_state=item_id,
                artifact_digests={
                    "subject": self.subject_digest,
                    "item-evidence": result.evidence.claimed_digest,
                },
                payload={"checklist_result": result.to_dict()},
                actor=actor,
                created_at=str(result.recorded_at),
            )
        )
        return result

    def results(self) -> tuple[ChecklistItemResult, ...]:
        ok, detail = self._ledger.verify_chain()
        if not ok:
            raise EvidencePlaneError(f"checklist journal verification failed: {detail}")
        results: list[ChecklistItemResult] = []
        for index, entry in enumerate(self._ledger.entries()):
            digests = entry.get("artifact_digests")
            if not isinstance(digests, Mapping):
                raise EvidencePlaneError(f"checklist entry {index} has no digest map")
            if digests.get("subject") != self.subject_digest:
                raise EvidencePlaneError(f"checklist entry {index} binds another subject")
            payload = entry.get("payload")
            raw_result = payload.get("checklist_result") if isinstance(payload, Mapping) else None
            if not isinstance(raw_result, Mapping):
                raise EvidencePlaneError(f"checklist entry {index} has no result")
            result = ChecklistItemResult.from_dict(raw_result)
            if result.evidence is None or (
                result.evidence.claimed_digest != digests.get("item-evidence")
            ):
                raise EvidencePlaneError(
                    f"checklist entry {index} evidence address does not match its ledger record"
                )
            results.append(result)
        return tuple(results)

    def report(self, required_item_ids: Sequence[str]) -> ChecklistReport:
        return verify_checklist(
            required_item_ids,
            self.results(),
            self.subject_digest,
        )


class EvidenceBundleAssembler:
    """Re-derive the manifest from run ledger, invariant documents, and item evidence.

    The bundle *is* the record, so the facts the doctrine says the record carries are required to
    write one rather than disposed of by surface class: the declared lane, the independence tier
    with each agent's model and directive version, and the monitor set. An absent record here is
    not a gap in the evidence — it is a bundle that cannot be assembled.
    """

    def __init__(self, runs_root: str | Path) -> None:
        self.runs_root = Path(runs_root)
        self.store = RunStore(self.runs_root)

    def assemble(
        self,
        run_id: str,
        *,
        candidate_digest: str,
        claims: Sequence[ProvenanceClaim],
        checklist_journal: ChecklistJournal,
        required_checklist_item_ids: Sequence[str],
        surface_evidence: Sequence[SurfaceEvidence],
        determinism_records: Sequence[DeterminismRecord],
        lane: str,
        independence: IndependenceRecord,
        validated_artifact_digests: Mapping[str, str],
        monitors: Sequence[Monitor] = (),
        monitor_declared_unit_count: int = 0,
        correction: CorrectionRecord | None = None,
        policy: SegregationPolicy | None = None,
    ) -> EvidenceBundleReport:
        if lane not in LANES:
            raise EvidencePlaneError(
                f"the run lane must be declared as one of {LANES}, not {lane!r}: "
                "a capability and a correction do not have the same oracle available"
            )
        projection = self.store.load(run_id)
        if projection.state != RunState.VALIDATING:
            raise EvidencePlaneError(
                "evidence bundle assembly requires the authoritative validating state"
            )
        current_digests = self.store.current_artifact_digests(run_id)
        ledger_candidate = str(current_digests.get("candidate", ""))
        acceptance_tests_digest = str(current_digests.get("acceptance-tests", ""))
        if ledger_candidate != candidate_digest:
            raise EvidencePlaneError(
                "candidate digest does not match the authoritative validating transition"
            )
        if not acceptance_tests_digest:
            raise EvidencePlaneError("validating transition has no acceptance-test artifact digest")
        coder_snapshot_digest = str(current_digests.get("coder-output-snapshot", ""))
        tester_snapshot_digest = str(current_digests.get("tester-output-snapshot", ""))
        if not coder_snapshot_digest or not tester_snapshot_digest:
            raise EvidencePlaneError(
                "validating transition has no immutable review snapshot digests"
            )
        validated_artifacts = dict(validated_artifact_digests)
        if set(validated_artifacts) != set(PREVIEW_VALIDATED_ARTIFACT_KEYS):
            raise EvidencePlaneError(
                "evidence bundle requires the complete closed set of PREVIEW validation artifacts"
            )
        review_root = self.runs_root / run_id / "evidence" / "review-snapshots"
        try:
            coder_snapshot = verify_frozen_tree(
                review_root / coder_snapshot_digest.removeprefix("sha256:"),
                expected_digest=coder_snapshot_digest,
            )
            tester_snapshot = verify_frozen_tree(
                review_root / tester_snapshot_digest.removeprefix("sha256:"),
                expected_digest=tester_snapshot_digest,
            )
            if tree_digest(coder_snapshot.files_directory / "artifact") != candidate_digest:
                raise EvidencePlaneError(
                    "frozen Coder snapshot does not contain the ledger candidate"
                )
            if tree_digest(tester_snapshot.files_directory / "tests") != acceptance_tests_digest:
                raise EvidencePlaneError(
                    "frozen Tester snapshot does not contain the ledger acceptance tests"
                )
            verify_prepared_generation(self.runs_root, projection)
        except (SnapshotError, GenerationError) as exc:
            raise EvidencePlaneError(
                f"immutable build evidence failed verification: {exc}"
            ) from exc
        artifacts = self._phase_artifacts(run_id, projection.phase_artifact_digests)
        trusted = {artifact.artifact_id: artifact.content_digest for artifact in artifacts}
        provenance_bundle = ProvenanceBundle(
            artifacts=artifacts,
            claims=tuple(claims),
            trusted_artifact_digests=trusted,
        )
        provenance_report = provenance_bundle.verify()
        checklist_results = checklist_journal.results()
        checklist_report = verify_checklist(
            required_checklist_item_ids,
            checklist_results,
            candidate_digest,
        )
        if checklist_journal.subject_digest != candidate_digest:
            raise EvidencePlaneError("checklist journal belongs to another candidate")

        surface_rows, evidence_binding_issues = self._bind_surface_evidence(
            tuple(surface_evidence),
            checklist_results,
        )
        determinism_rows = tuple(determinism_records)
        blocking, gates, reports = self._evaluate_surfaces(surface_rows, determinism_rows)

        (
            independence_report,
            monitor_report,
            correction_report,
            record_findings,
        ) = self._evaluate_records(
            surface_rows,
            provenance_report,
            lane=lane,
            independence=independence,
            monitors=tuple(monitors),
            monitor_declared_unit_count=monitor_declared_unit_count,
            correction=correction,
            policy=policy or SegregationPolicy(),
        )
        record_blocking, record_gates, record_reports = record_findings
        blocking = tuple(dict.fromkeys((*evidence_binding_issues, *blocking, *record_blocking)))
        gates = tuple(dict.fromkeys((*gates, *record_gates)))
        reports = tuple(dict.fromkeys((*reports, *record_reports)))
        ledger_entries = self.store.verified_ledger_entries(run_id)
        genesis_artifacts = ledger_entries[0].get("artifact_digests")
        validating_entry = ledger_entries[-1]
        validating_payload = validating_entry.get("payload")
        if not isinstance(genesis_artifacts, Mapping) or not isinstance(
            validating_payload, Mapping
        ):
            raise EvidencePlaneError(
                "evidence bundle cannot derive authority or validating identities"
            )
        preview_admission = build_preview_admission(
            run_schema_version=projection.schema_version,
            run_id=run_id,
            generation=projection.generation,
            validating_ledger_head=projection.ledger_head,
            authority_genesis_digest=str(genesis_artifacts.get("authority-genesis", "")),
            implementer_identity=str(validating_entry.get("implementer_identity", "")),
            tester_identity=str(validating_payload.get("tester_identity", "")),
            verifier_identity=str(validating_entry.get("verifier_identity", "")),
            artifact_digests={
                "candidate": candidate_digest,
                "acceptance-tests": acceptance_tests_digest,
                "coder-output-snapshot": coder_snapshot_digest,
                "tester-output-snapshot": tester_snapshot_digest,
                **validated_artifacts,
            },
        )
        document = {
            "schema_version": "factory-evidence-bundle/3",
            "run_id": run_id,
            "lane": lane,
            "target_digest": projection.target_digest,
            "source_digest": projection.source_digest,
            "candidate_digest": candidate_digest,
            "acceptance_tests_digest": acceptance_tests_digest,
            "generation_artifacts": dict(projection.generation_artifact_digests),
            "review_snapshots": {
                "coder-output": coder_snapshot_digest,
                "tester-output": tester_snapshot_digest,
            },
            "build_attempt": {
                "number": projection.build_attempt_count,
                "limit": projection.build_attempt_limit,
            },
            "ledger_head": projection.ledger_head,
            "phase_artifacts": [artifact.body() for artifact in artifacts],
            "trusted_artifact_digests": trusted,
            "preview_admission": preview_admission,
            "claims": [claim.to_dict() for claim in claims],
            "checklist_results": [result.to_dict() for result in checklist_results],
            "surface_evidence": [record.to_dict() for record in surface_rows],
            "determinism_records": [record.to_dict() for record in determinism_rows],
            "independence": {
                **independence.to_dict(),
                "derived_tier": independence_report.derived_tier,
            },
            "monitors": [monitor.to_dict() for monitor in monitors],
            "monitor_declared_unit_count": monitor_declared_unit_count,
        }
        if correction is not None:
            document["correction"] = correction.to_dict()
        validate_document("evidence-bundle", document)
        return EvidenceBundleReport(
            document=document,
            provenance=provenance_report,
            checklist=checklist_report,
            blocking_issues=blocking,
            gate_issues=gates,
            reports=reports,
            independence=independence_report,
            monitors=monitor_report,
            correction=correction_report,
        )

    def _evaluate_records(
        self,
        surfaces: Sequence[SurfaceEvidence],
        provenance_report: ProvenanceReport,
        *,
        lane: str,
        independence: IndependenceRecord,
        monitors: Sequence[Monitor],
        monitor_declared_unit_count: int,
        correction: CorrectionRecord | None,
        policy: SegregationPolicy,
    ) -> tuple[
        IndependenceReport,
        MonitorSetReport,
        CorrectionReport | None,
        tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]],
    ]:
        """Verify the independence, monitor, and correction records this bundle must carry.

        Monitor and correction absences are disposed by the surface class exactly as an evidence
        gap is; integrity failures and negative findings block every class. The independence
        record is different in kind — the bundle cannot claim a tier it did not record — so its
        absences block here rather than being disposed.
        """

        resolved_references = tuple(
            IntentBackreference(
                artifact_id=claim.artifact_id,
                artifact_digest=claim.artifact_digest,
                item_id=claim.item_id,
                intent_digest=claim.intent_digest,
            )
            for claim in provenance_report.resolved_claims
        )
        authority_available = provenance_report.satisfied

        blocking: list[str] = []
        gates: list[str] = []
        reports: list[str] = []

        independence_report = verify_independence(
            independence,
            resolved_backreferences=resolved_references,
            authority_available=authority_available,
        )
        blocking.extend(f"independence-unrecorded:{code}" for code in independence_report.gaps)
        blocking.extend(f"independence-failure:{code}" for code in independence_report.failures)
        blocking.extend(
            f"independence-integrity:{code}" for code in independence_report.integrity_issues
        )
        reports.extend(independence_report.reports)
        reports.append(f"independence-tier-derived:{independence_report.derived_tier}")

        resolved_surfaces = tuple(
            ResolvedSurface(
                surface_id=surface.surface_id,
                component_id="",
                declared_criticality=surface.criticality,
                effective_criticality=surface.criticality,
                decided_by="",
                wrong_cost="",
                required_evidence_ids=BASE_REQUIRED_EVIDENCE_IDS,
                standard_flake_budget=0,
                side_effect_surface_ids=(),
            )
            for surface in surfaces
        )
        monitor_report = verify_monitor_set(
            monitors,
            resolved_surfaces,
            policy,
            resolved_backreferences=resolved_references,
            authority_available=authority_available,
            declared_unit_count=monitor_declared_unit_count,
        )
        blocking.extend(f"monitor-integrity:{code}" for code in monitor_report.integrity_issues)
        criticality_by_surface = {surface.surface_id: surface.criticality for surface in surfaces}
        for surface_id, code in monitor_report.surface_gaps:
            self._dispose(
                criticality_by_surface.get(surface_id, "critical"),
                f"{code}:{surface_id}",
                blocking,
                gates,
                reports,
            )
        reports.extend(monitor_report.reports)

        correction_report: CorrectionReport | None = None
        if lane == LANE_CORRECTION:
            correction_report = verify_correction(correction)
            for code in correction_report.gaps:
                for surface in surfaces:
                    self._dispose(
                        surface.criticality,
                        f"correction-gap:{code}:{surface.surface_id}",
                        blocking,
                        gates,
                        reports,
                    )
            blocking.extend(f"correction-failure:{code}" for code in correction_report.failures)
            blocking.extend(
                f"correction-integrity:{code}" for code in correction_report.integrity_issues
            )
            gates.extend(f"correction-review:{code}" for code in correction_report.gate_reasons)
            reports.extend(f"correction:{code}" for code in correction_report.reports)
        elif correction is not None:
            reports.append("correction-record-outside-correction-lane")

        return (
            independence_report,
            monitor_report,
            correction_report,
            (
                tuple(dict.fromkeys(blocking)),
                tuple(dict.fromkeys(gates)),
                tuple(dict.fromkeys(reports)),
            ),
        )

    @staticmethod
    def _dispose(
        criticality: str,
        code: str,
        blocking: list[str],
        gates: list[str],
        reports: list[str],
    ) -> None:
        """Class-dispose one absence: Critical blocks, Standard gates, Cosmetic reports."""

        if criticality == "critical":
            blocking.append(f"critical-gap:{code}")
        elif criticality == "standard":
            gates.append(f"standard-gap:{code}")
        else:
            reports.append(f"cosmetic-gap:{code}")

    def _bind_surface_evidence(
        self,
        surfaces: Sequence[SurfaceEvidence],
        checklist_results: Sequence[ChecklistItemResult],
    ) -> tuple[tuple[SurfaceEvidence, ...], tuple[str, ...]]:
        """Resolve surface citations only from passed, candidate-bound checklist evidence.

        Callers may name evidence ids, but cannot make a digest authoritative by supplying a
        plausible-looking hash. Any supplied address must match the journal exactly.
        """

        available: dict[str, str] = {}
        for result in checklist_results:
            if result.passed is True and result.evidence is not None:
                available[result.id] = result.evidence.claimed_digest

        issues: list[str] = []
        bound: list[SurfaceEvidence] = []
        for surface in surfaces:
            explicit = dict(surface.evidence_digests)
            for evidence_id, claimed_digest in explicit.items():
                authoritative = available.get(evidence_id)
                if authoritative is None:
                    issues.append(
                        f"surface-evidence-unresolvable:{surface.surface_id}:{evidence_id}"
                    )
                elif claimed_digest != authoritative:
                    issues.append(f"surface-evidence-mismatch:{surface.surface_id}:{evidence_id}")
            resolved = {
                evidence_id: available[evidence_id]
                for evidence_id in surface.required_evidence_ids
                if evidence_id in available
            }
            bound.append(replace(surface, evidence_digests=resolved))
        return tuple(bound), tuple(dict.fromkeys(issues))

    def _phase_artifacts(
        self,
        run_id: str,
        phase_digests: Mapping[str, str],
    ) -> tuple[PhaseArtifact, ...]:
        artifacts: list[PhaseArtifact] = []
        for phase in (
            "product-specification",
            "architecture",
            "operational-maturity",
        ):
            digest = phase_digests.get(phase)
            if digest is None:
                raise EvidencePlaneError(f"run has no ratified {phase} artifact")
            path = (
                self.runs_root
                / run_id
                / "evidence"
                / phase
                / digest.removeprefix("sha256:")
                / "artifact.json"
            )
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise EvidencePlaneError(
                    f"ratified phase artifact is unreadable: {path}: {exc}"
                ) from exc
            if not isinstance(raw, Mapping):
                raise EvidencePlaneError(f"ratified phase artifact is not an object: {path}")
            validate_document("phase-artifact", raw)
            artifact = PhaseArtifact.from_dict(raw)
            if artifact.phase != phase or artifact.content_digest != digest:
                raise EvidencePlaneError(
                    f"ratified phase artifact does not match the run ledger: {phase}"
                )
            artifacts.append(artifact)
        return tuple(artifacts)

    def _evaluate_surfaces(
        self,
        surfaces: Sequence[SurfaceEvidence],
        determinism: Sequence[DeterminismRecord],
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        blocking: list[str] = []
        gates: list[str] = []
        reports: list[str] = []
        surface_index = {record.surface_id: record for record in surfaces}
        determinism_index = {record.surface_id: record for record in determinism}
        if len(surface_index) != len(surfaces):
            blocking.append("surface-evidence-duplicate")
        if len(determinism_index) != len(determinism):
            blocking.append("determinism-record-duplicate")
        if not surface_index:
            blocking.append("surface-evidence-missing")
        if set(surface_index) != set(determinism_index):
            blocking.append("surface-determinism-parity-mismatch")

        for surface_id, surface in surface_index.items():
            record = determinism_index.get(surface_id)
            if record is None:
                continue
            if record.criticality != surface.criticality:
                blocking.append(f"surface-criticality-mismatch:{surface_id}")
            missing_evidence = sorted(
                set(surface.required_evidence_ids) - set(surface.evidence_digests)
            )
            gap = not surface.oracle_adequate or bool(missing_evidence)
            if surface.criticality == "critical":
                if gap:
                    blocking.append(f"critical-evidence-gap:{surface_id}")
                if not record.deterministic or record.flake_count or record.automatic_retry_count:
                    blocking.append(f"critical-nondeterminism:{surface_id}")
            elif surface.criticality == "standard" and gap:
                gates.append(f"standard-evidence-gap:{surface_id}")
            elif surface.criticality == "cosmetic" and gap:
                reports.append(f"cosmetic-evidence-gap:{surface_id}")
        return (
            tuple(dict.fromkeys(blocking)),
            tuple(dict.fromkeys(gates)),
            tuple(dict.fromkeys(reports)),
        )
