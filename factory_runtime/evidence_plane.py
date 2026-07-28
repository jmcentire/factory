"""Append-as-observed checklist evidence and reproducible change-evidence bundles."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from factory_core.checklist import (
    ChecklistItemResult,
    ChecklistReport,
    verify_checklist,
)
from factory_core.evidence import EvidenceIntegrity
from factory_core.manifest import Ledger, LedgerEntry, digest_obj
from factory_core.provenance import (
    PhaseArtifact,
    ProvenanceBundle,
    ProvenanceClaim,
    ProvenanceReport,
)
from factory_runtime.schema import validate_document
from factory_runtime.state import RunState, RunStore


class EvidencePlaneError(ValueError):
    """Evidence could not be re-derived from its authoritative sources."""


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

    @property
    def mechanically_satisfied(self) -> bool:
        return (
            self.provenance.satisfied
            and self.checklist.satisfied
            and not self.blocking_issues
        )


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
        self._ledger = Ledger(str(self.path))

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
            raw_result = (
                payload.get("checklist_result") if isinstance(payload, Mapping) else None
            )
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
    """Re-derive the manifest from run ledger, invariant documents, and item evidence."""

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
    ) -> EvidenceBundleReport:
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
            raise EvidencePlaneError(
                "validating transition has no acceptance-test artifact digest"
            )
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
        blocking = tuple(dict.fromkeys((*evidence_binding_issues, *blocking)))
        document = {
            "schema_version": "factory-evidence-bundle/1",
            "run_id": run_id,
            "target_digest": projection.target_digest,
            "source_digest": projection.source_digest,
            "candidate_digest": candidate_digest,
            "acceptance_tests_digest": acceptance_tests_digest,
            "ledger_head": projection.ledger_head,
            "phase_artifacts": [artifact.body() for artifact in artifacts],
            "trusted_artifact_digests": trusted,
            "claims": [claim.to_dict() for claim in claims],
            "checklist_results": [result.to_dict() for result in checklist_results],
            "surface_evidence": [record.to_dict() for record in surface_rows],
            "determinism_records": [record.to_dict() for record in determinism_rows],
        }
        validate_document("evidence-bundle", document)
        return EvidenceBundleReport(
            document=document,
            provenance=provenance_report,
            checklist=checklist_report,
            blocking_issues=blocking,
            gate_issues=gates,
            reports=reports,
        )

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
                    issues.append(
                        f"surface-evidence-mismatch:{surface.surface_id}:{evidence_id}"
                    )
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
                if (
                    not record.deterministic
                    or record.flake_count
                    or record.automatic_retry_count
                ):
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
