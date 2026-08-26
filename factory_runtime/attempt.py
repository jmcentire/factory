"""Checkpoint-bound, Factory-owned execution of immutable attempts.

The campaign layer receives this executor, never a host command.  The typed
invocation is deliberately the same shape as the established orchestrator
entrypoint; no target-specific wrapper has a chance to reinterpret a repair
brief or manufacture terminal state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from factory_core.correction import CorrectionRecord
from factory_core.independence import IndependenceRecord
from factory_core.monitors import Monitor
from factory_runtime.campaign import CampaignAttemptOutcome
from factory_runtime.evidence_plane import DeterminismRecord, SurfaceEvidence
from factory_runtime.orchestrator import FactoryOrchestrator
from factory_runtime.workflow import FactoryWorkflow


@dataclass(frozen=True)
class FactoryAttemptInvocation:
    """All typed, checkpoint-verifiable inputs to one immutable attempt."""

    target_manifest_path: Path
    pattern_catalog_path: Path
    build_plan_path: Path
    acceptance_catalog_path: Path
    acceptance_catalog_human_receipt_path: Path
    acceptance_catalog_validator_receipt_path: Path
    coder_command: tuple[str, ...]
    tester_command: tuple[str, ...]
    validator_command: tuple[str, ...]
    coder_trusted_paths: tuple[Path, ...]
    tester_trusted_paths: tuple[Path, ...]
    validator_trusted_paths: tuple[Path, ...]
    resume_checkpoint_path: Path
    expected_resume_checkpoint_digest: str
    genesis_path: Path
    resume_configuration_sources: Mapping[str, Path]
    implementer_identity: str
    tester_identity: str
    verifier_identity: str
    verifier_key_path: Path
    surface_evidence: tuple[SurfaceEvidence, ...]
    determinism_records: tuple[DeterminismRecord, ...]
    lane: str
    independence: IndependenceRecord
    monitors: tuple[Monitor, ...] = ()
    monitor_declared_unit_count: int = 0
    correction: CorrectionRecord | None = None
    changed_existing_tests: tuple[str, ...] = ()
    test_change_authorization_path: Path | None = None
    test_change_human_receipt_path: Path | None = None
    test_change_validator_receipt_path: Path | None = None


class FactoryAttemptExecutor:
    """The sole Factory adapter from a campaign retry to orchestration."""

    def __init__(
        self,
        workflow: FactoryWorkflow,
        *,
        invocation: FactoryAttemptInvocation,
        orchestrator: FactoryOrchestrator | None = None,
    ) -> None:
        self.workflow = workflow
        self.invocation = invocation
        self.orchestrator = orchestrator or FactoryOrchestrator(workflow)

    def execute(
        self,
        run_id: str,
        *,
        attempt_id: str,
        repair_brief_path: Path | None,
    ) -> CampaignAttemptOutcome:
        values = self.invocation
        outcome = self.orchestrator.build_and_validate(
            run_id,
            attempt_id=attempt_id,
            target_manifest_path=values.target_manifest_path,
            pattern_catalog_path=values.pattern_catalog_path,
            build_plan_path=values.build_plan_path,
            acceptance_catalog_path=values.acceptance_catalog_path,
            acceptance_catalog_human_receipt_path=(
                values.acceptance_catalog_human_receipt_path
            ),
            acceptance_catalog_validator_receipt_path=(
                values.acceptance_catalog_validator_receipt_path
            ),
            coder_command=values.coder_command,
            tester_command=values.tester_command,
            validator_command=values.validator_command,
            coder_trusted_paths=values.coder_trusted_paths,
            tester_trusted_paths=values.tester_trusted_paths,
            validator_trusted_paths=values.validator_trusted_paths,
            resume_checkpoint_path=values.resume_checkpoint_path,
            expected_resume_checkpoint_digest=values.expected_resume_checkpoint_digest,
            genesis_path=values.genesis_path,
            resume_configuration_sources=values.resume_configuration_sources,
            implementer_identity=values.implementer_identity,
            tester_identity=values.tester_identity,
            verifier_identity=values.verifier_identity,
            verifier_key_path=values.verifier_key_path,
            surface_evidence=values.surface_evidence,
            determinism_records=values.determinism_records,
            lane=values.lane,
            independence=values.independence,
            monitors=values.monitors,
            monitor_declared_unit_count=values.monitor_declared_unit_count,
            correction=values.correction,
            repair_brief_path=repair_brief_path,
            changed_existing_tests=values.changed_existing_tests,
            test_change_authorization_path=values.test_change_authorization_path,
            test_change_human_receipt_path=values.test_change_human_receipt_path,
            test_change_validator_receipt_path=values.test_change_validator_receipt_path,
        )
        return CampaignAttemptOutcome(
            attempt_id=attempt_id,
            candidate_digest=outcome.candidate_digest,
            tests_digest=outcome.tests_digest,
            projection=outcome.projection,
            _passed=outcome.passed,
        )
