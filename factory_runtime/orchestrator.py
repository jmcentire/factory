"""Executable build/validate/evidence slice over the three-role runtime."""

from __future__ import annotations

import json
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from factory_core.correction import CorrectionRecord
from factory_core.independence import IndependenceRecord
from factory_core.manifest import digest_bytes, digest_obj
from factory_core.monitors import Monitor
from factory_core.provenance import ProvenanceClaim
from factory_runtime.evidence_plane import (
    ChecklistJournal,
    DeterminismRecord,
    EvidenceBundleAssembler,
    EvidenceBundleReport,
    SurfaceEvidence,
)
from factory_runtime.lanes import (
    IsolatedBuildLoop,
    LaneExecution,
    ValidationExecution,
)
from factory_runtime.state import RunProjection, RunState
from factory_runtime.tessera import VerifiedEnvelope
from factory_runtime.workflow import FactoryWorkflow

BUILD_CHECKLIST = (
    "lane-isolation",
    "coder-output",
    "tester-output",
    "acceptance-tests",
)
_ATTEMPT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class OrchestrationError(RuntimeError):
    """The executable build slice could not advance without weakening a gate."""


@dataclass(frozen=True)
class BuildOutcome:
    candidate_digest: str
    tests_digest: str
    execution: ValidationExecution
    projection: RunProjection
    evidence_report: EvidenceBundleReport | None
    evidence_envelope: VerifiedEnvelope | None

    @property
    def passed(self) -> bool:
        return (
            self.execution.passed
            and self.evidence_report is not None
            and self.evidence_report.mechanically_satisfied
            and self.evidence_envelope is not None
            and self.projection.state == RunState.PREVIEW
        )

    @property
    def repair_signal(self) -> str:
        return "pass" if self.passed else "fail"


def digest_artifact_tree(root: str | Path) -> str:
    """Content-address a regular-file tree without following links."""

    directory = Path(root)
    if not directory.is_dir():
        raise OrchestrationError(f"candidate artifact directory is missing: {directory}")
    files: list[dict[str, object]] = []
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory).as_posix()
        if path.is_symlink():
            raise OrchestrationError(f"candidate artifact contains a symlink: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise OrchestrationError(f"candidate artifact is not a regular file: {relative}")
        mode = stat.S_IMODE(path.stat().st_mode)
        files.append(
            {
                "path": relative,
                "mode": mode,
                "digest": digest_bytes(path.read_bytes()),
            }
        )
    if not files:
        raise OrchestrationError("candidate artifact tree is empty")
    return digest_obj({"files": files})


def _claims(path: Path) -> tuple[ProvenanceClaim, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrchestrationError(f"Tester assertion manifest is unreadable: {exc}") from exc
    raw_claims = raw.get("claims") if isinstance(raw, Mapping) else None
    if not isinstance(raw_claims, list):
        raise OrchestrationError("Tester assertion manifest has no claims array")
    claims = tuple(
        ProvenanceClaim.from_dict(claim)
        for claim in raw_claims
        if isinstance(claim, Mapping)
    )
    if len(claims) != len(raw_claims) or not claims:
        raise OrchestrationError("Tester assertion manifest contains malformed or no claims")
    return claims


class FactoryOrchestrator:
    """Drive one clean Coder/Tester attempt through Validator evidence to preview."""

    def __init__(self, workflow: FactoryWorkflow) -> None:
        self.workflow = workflow

    def _record_exception_as_blocked(
        self,
        run_id: str,
        *,
        exc: Exception,
        tester_identity: str,
        implementer_identity: str = "",
        verifier_identity: str = "",
    ) -> None:
        """Make an internal refusal recoverable without exposing lane/test internals."""

        current = self.workflow.store.load(run_id)
        if current.state not in {RunState.BUILDING, RunState.VALIDATING}:
            return
        self.workflow.store.transition(
            run_id,
            RunState.BLOCKED,
            actor="validator",
            payload={
                "reason": "orchestration-error",
                "error_type": type(exc).__name__,
                "repair_signal": "fail",
                "tester_identity": tester_identity,
            },
            implementer_identity=implementer_identity,
            verifier_identity=verifier_identity,
        )

    def build_and_validate(
        self,
        run_id: str,
        *,
        attempt_id: str,
        spec_path: str | Path,
        coder_command: Sequence[str],
        tester_command: Sequence[str],
        validator_command: Sequence[str],
        coder_trusted_paths: Sequence[str | Path],
        tester_trusted_paths: Sequence[str | Path],
        validator_trusted_paths: Sequence[str | Path],
        implementer_identity: str,
        tester_identity: str,
        verifier_identity: str,
        verifier_key_path: str | Path,
        surface_evidence: Sequence[SurfaceEvidence],
        determinism_records: Sequence[DeterminismRecord],
        lane: str,
        independence: IndependenceRecord,
        monitors: Sequence[Monitor] = (),
        monitor_declared_unit_count: int = 0,
        correction: CorrectionRecord | None = None,
    ) -> BuildOutcome:
        if not _ATTEMPT_ID.fullmatch(attempt_id):
            raise OrchestrationError(
                "attempt_id must start with an alphanumeric and contain only letters, "
                "numbers, dot, underscore, or dash"
            )
        current = self.workflow.store.load(run_id)
        if current.state not in {
            RunState.OPERATIONAL_MATURITY_RATIFIED,
            RunState.BLOCKED,
        }:
            raise OrchestrationError(
                "build requires ratified invariant documents or a recoverable blocked attempt"
            )
        verifier = self.workflow.policy.principal(verifier_identity)
        if verifier is None or verifier.kind == "human":
            raise OrchestrationError("Validator verifier identity is not an enrolled agent")
        implementer = self.workflow.policy.principal(implementer_identity)
        tester = self.workflow.policy.principal(tester_identity)
        if implementer is None or implementer.kind == "human":
            raise OrchestrationError("Coder implementer identity is not an enrolled agent")
        if tester is None or tester.kind == "human":
            raise OrchestrationError("Tester identity is not an enrolled agent")
        if len({implementer_identity, tester_identity, verifier_identity}) != 3:
            raise OrchestrationError("Coder, Tester, and Validator identities must be distinct")
        if len({implementer.public_key, tester.public_key, verifier.public_key}) != 3:
            raise OrchestrationError(
                "Coder, Tester, and Validator must not share signing keys"
            )

        attempt_root = (
            self.workflow.root
            / run_id
            / "evidence"
            / "build-attempts"
            / attempt_id
        )
        if attempt_root.exists():
            raise OrchestrationError(f"refusing to reuse build attempt: {attempt_id}")
        self.workflow.store.transition(
            run_id,
            RunState.BUILDING,
            actor="validator",
            artifact_digests={"combined-spec": digest_bytes(Path(spec_path).read_bytes())},
        )
        loop = IsolatedBuildLoop(attempt_root)
        candidate_digest = ""
        tests_digest = ""
        journal: ChecklistJournal | None = None

        def enter_validation(
            coder: LaneExecution,
            tester: LaneExecution,
        ) -> None:
            nonlocal candidate_digest, tests_digest, journal
            candidate_digest = digest_artifact_tree(coder.output_directory / "artifact")
            tests_digest = digest_artifact_tree(tester.output_directory / "tests")
            journal = ChecklistJournal(
                attempt_root / "checklist.jsonl",
                subject_digest=candidate_digest,
            )
            journal.record(
                "lane-isolation",
                passed=True,
                detail="read, write, and network denial probes passed",
                actor="validator",
                observations=asdict(loop.sandbox.qualify(attempt_root / "requalification")),
            )
            journal.record(
                "coder-output",
                passed=True,
                detail="Coder produced a non-empty regular-file artifact tree",
                actor="validator",
                observations={"candidate_digest": candidate_digest},
            )
            journal.record(
                "tester-output",
                passed=True,
                detail="Tester produced a non-empty regular-file acceptance-test tree",
                actor="validator",
                observations={"tests_digest": tests_digest},
            )
            self.workflow.store.transition(
                run_id,
                RunState.VALIDATING,
                actor="validator",
                artifact_digests={
                    "candidate": candidate_digest,
                    "acceptance-tests": tests_digest,
                },
                payload={"tester_identity": tester_identity},
                implementer_identity=implementer_identity,
            )

        try:
            execution = loop.execute(
                spec_path=spec_path,
                coder_command=coder_command,
                tester_command=tester_command,
                validator_command=validator_command,
                coder_trusted_paths=coder_trusted_paths,
                tester_trusted_paths=tester_trusted_paths,
                validator_trusted_paths=validator_trusted_paths,
                before_validation=enter_validation,
            )
        except Exception as exc:
            self._record_exception_as_blocked(
                run_id,
                exc=exc,
                tester_identity=tester_identity,
                implementer_identity=(
                    implementer_identity if candidate_digest else ""
                ),
            )
            raise
        if not execution.coder.succeeded or not execution.tester.succeeded:
            projection = self.workflow.store.transition(
                run_id,
                RunState.BLOCKED,
                actor="validator",
                payload={
                    "reason": "author-lane-failed",
                    "repair_signal": "fail",
                    "tester_identity": tester_identity,
                },
            )
            return BuildOutcome("", "", execution, projection, None, None)
        if journal is None or not candidate_digest or not tests_digest:
            raise OrchestrationError("validation entered without candidate evidence")

        journal.record(
            "acceptance-tests",
            passed=execution.validator.succeeded,
            detail=(
                "Validator executed the Tester suite successfully"
                if execution.validator.succeeded
                else "Validator observed an acceptance-test failure"
            ),
            actor="validator",
            observations={"repair_signal": execution.repair_signal},
        )
        if not execution.validator.succeeded:
            projection = self.workflow.store.transition(
                run_id,
                RunState.BLOCKED,
                actor="validator",
                artifact_digests={
                    "candidate": candidate_digest,
                    "acceptance-tests": tests_digest,
                },
                payload={
                    "reason": "acceptance-tests-failed",
                    "repair_signal": "fail",
                    "tester_identity": tester_identity,
                },
                implementer_identity=implementer_identity,
                verifier_identity=verifier_identity,
            )
            return BuildOutcome(
                candidate_digest,
                tests_digest,
                execution,
                projection,
                None,
                None,
            )

        try:
            claims = _claims(
                execution.tester.output_directory / "evidence" / "assertions.json"
            )
            report = EvidenceBundleAssembler(self.workflow.root).assemble(
                run_id,
                candidate_digest=candidate_digest,
                claims=claims,
                checklist_journal=journal,
                required_checklist_item_ids=BUILD_CHECKLIST,
                surface_evidence=surface_evidence,
                determinism_records=determinism_records,
                lane=lane,
                independence=independence,
                monitors=monitors,
                monitor_declared_unit_count=monitor_declared_unit_count,
                correction=correction,
                # Monitor authorship resolves against the signed genesis roster, so
                # "human-authored" is an enrolled human rather than a label.
                policy=self.workflow.policy.segregation_policy(),
            )
            if not report.mechanically_satisfied:
                projection = self.workflow.store.transition(
                    run_id,
                    RunState.BLOCKED,
                    actor="validator",
                    payload={
                        "reason": "mechanical-evidence-gate-failed",
                        "repair_signal": "fail",
                        "blocking_issues": list(report.blocking_issues),
                        "provenance_issues": list(report.provenance.issues),
                        "checklist_failures": list(report.checklist.failures),
                        "checklist_gaps": list(report.checklist.gaps),
                        "tester_identity": tester_identity,
                    },
                    implementer_identity=implementer_identity,
                    verifier_identity=verifier_identity,
                )
                return BuildOutcome(
                    candidate_digest,
                    tests_digest,
                    execution,
                    projection,
                    report,
                    None,
                )

            bundle_path = attempt_root / "evidence-bundle.tessera.json"
            envelope = self.workflow.tessera.wrap_json(
                report.document,
                kind="factory-evidence-bundle",
                key_path=verifier_key_path,
                output_path=bundle_path,
            )
            if envelope.public_key != verifier.public_key:
                raise OrchestrationError(
                    "evidence bundle signer does not own the Validator verifier identity"
                )
            projection = self.workflow.store.transition(
                run_id,
                RunState.PREVIEW,
                actor="validator",
                artifact_digests={
                    "candidate": candidate_digest,
                    "acceptance-tests": tests_digest,
                    "evidence-bundle": envelope.payload_digest,
                    "evidence-envelope": envelope.envelope_digest,
                },
                payload={
                    "repair_signal": "pass",
                    "standard_gate_issues": list(report.gate_issues),
                    "reports": list(report.reports),
                    "tester_identity": tester_identity,
                },
                implementer_identity=implementer_identity,
                verifier_identity=verifier_identity,
            )
        except Exception as exc:
            self._record_exception_as_blocked(
                run_id,
                exc=exc,
                tester_identity=tester_identity,
                implementer_identity=implementer_identity,
                verifier_identity=verifier_identity,
            )
            raise
        return BuildOutcome(
            candidate_digest,
            tests_digest,
            execution,
            projection,
            report,
            envelope,
        )
