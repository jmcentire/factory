"""Executable build/validate/evidence slice over the three-role runtime."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from factory_core.correction import CorrectionRecord
from factory_core.independence import IndependenceRecord
from factory_core.manifest import digest_bytes
from factory_core.monitors import Monitor
from factory_core.provenance import ProvenanceClaim
from factory_runtime.acceptance_obligations import (
    REPORT_ARTIFACT_KEY,
    AcceptanceObligationCatalog,
    AcceptanceObligationError,
    derive_acceptance_obligation_report,
    load_retained_acceptance_catalog,
    retain_acceptance_obligation_report,
    validator_execution_digests,
    verify_and_retain_acceptance_catalog,
    verify_retained_validator_execution,
)
from factory_runtime.adversarial_review import (
    VerifiedAdversarialReview,
    build_review_authority_context,
    build_validator_review_subject,
    load_canonical_review_report,
    retain_validator_adversarial_review,
    verify_validator_adversarial_review,
)
from factory_runtime.authority import human_public_keys
from factory_runtime.candidate_diff import CandidateDiffError, build_candidate_review_context
from factory_runtime.durability import DurabilityError, fsync_directory_chain
from factory_runtime.evidence_plane import (
    ChecklistJournal,
    DeterminismRecord,
    EvidenceBundleAssembler,
    EvidenceBundleReport,
    SurfaceEvidence,
)
from factory_runtime.generation import GenerationError, GenerationPreparer
from factory_runtime.lanes import (
    IsolatedBuildLoop,
    LaneExecution,
    LaneRole,
    ValidationExecution,
)
from factory_runtime.preflight import run_preflight
from factory_runtime.resume import (
    ResumeVerification,
    _stable_stream_digest,
    verify_resume_checkpoint,
)
from factory_runtime.snapshot import FrozenTree, SnapshotError, tree_digest
from factory_runtime.state import RunProjection, RunState
from factory_runtime.state_admission import StateAdmissionError, read_stable_regular_bytes
from factory_runtime.tessera import VerifiedEnvelope
from factory_runtime.test_change_authority import (
    HUMAN_RECEIPT_KEY,
    TEST_CHANGE_AUTHORIZATION_KEY,
    VALIDATOR_RECEIPT_KEY,
    TestChangeAuthorityError,
    verify_and_retain_test_change_authorization,
)
from factory_runtime.workflow import FactoryWorkflow, WorkflowError

BUILD_CHECKLIST = (
    "lane-isolation",
    "coder-output",
    "tester-output",
    "acceptance-tests",
    "adversarial-review",
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
    acceptance_report: Mapping[str, object] | None
    acceptance_report_digest: str
    resume_verification: ResumeVerification
    adversarial_review: Mapping[str, object] | None = None
    adversarial_review_digest: str = ""

    @property
    def passed(self) -> bool:
        return (
            self.execution.passed
            and self.evidence_report is not None
            and self.evidence_report.mechanically_satisfied
            and self.evidence_envelope is not None
            and self.acceptance_report is not None
            and bool(self.acceptance_report_digest)
            and self.adversarial_review is not None
            and bool(self.adversarial_review_digest)
            and self.projection.state == RunState.PREVIEW
        )

    @property
    def repair_signal(self) -> str:
        return "pass" if self.passed else "fail"


def digest_artifact_tree(root: str | Path) -> str:
    """Content-address a regular-file tree without following links."""

    try:
        return tree_digest(root)
    except SnapshotError as exc:
        raise OrchestrationError(str(exc)) from exc


def _claims(path: Path) -> tuple[ProvenanceClaim, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrchestrationError(f"Tester assertion manifest is unreadable: {exc}") from exc
    raw_claims = raw.get("claims") if isinstance(raw, Mapping) else None
    if not isinstance(raw_claims, list):
        raise OrchestrationError("Tester assertion manifest has no claims array")
    claims = tuple(
        ProvenanceClaim.from_dict(claim) for claim in raw_claims if isinstance(claim, Mapping)
    )
    if len(claims) != len(raw_claims) or not claims:
        raise OrchestrationError("Tester assertion manifest contains malformed or no claims")
    return claims


def _object(path: Path, *, label: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise OrchestrationError(f"{label} is missing, not regular, or symlinked")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrchestrationError(f"{label} is unreadable: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise OrchestrationError(f"{label} must be a JSON object")
    return {str(key): value for key, value in raw.items()}


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
        candidate_digest: str = "",
        tests_digest: str = "",
    ) -> None:
        """Make an internal refusal recoverable without exposing lane/test internals."""

        current = self.workflow.store.load(run_id)
        if current.state not in {RunState.BUILDING, RunState.VALIDATING}:
            return
        self.workflow.store.transition(
            run_id,
            RunState.BLOCKED,
            actor="validator",
            artifact_digests=(
                {
                    "candidate": candidate_digest,
                    "acceptance-tests": tests_digest,
                }
                if candidate_digest and tests_digest
                else None
            ),
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
        target_manifest_path: str | Path,
        pattern_catalog_path: str | Path,
        build_plan_path: str | Path,
        acceptance_catalog_path: str | Path,
        acceptance_catalog_human_receipt_path: str | Path,
        acceptance_catalog_validator_receipt_path: str | Path,
        coder_command: Sequence[str],
        tester_command: Sequence[str],
        validator_command: Sequence[str],
        coder_trusted_paths: Sequence[str | Path],
        tester_trusted_paths: Sequence[str | Path],
        validator_trusted_paths: Sequence[str | Path],
        resume_checkpoint_path: str | Path,
        expected_resume_checkpoint_digest: str,
        genesis_path: str | Path,
        resume_configuration_sources: Mapping[str, str | Path],
        implementer_identity: str,
        tester_identity: str,
        verifier_identity: str,
        verifier_key_path: str | Path,
        surface_evidence: Sequence[SurfaceEvidence],
        determinism_records: Sequence[DeterminismRecord],
        lane: str,
        independence: IndependenceRecord,
        prebuilt_author_outputs: Mapping[LaneRole, str | Path] | None = None,
        monitors: Sequence[Monitor] = (),
        monitor_declared_unit_count: int = 0,
        correction: CorrectionRecord | None = None,
        repair_brief_path: str | Path | None = None,
        changed_existing_tests: Sequence[str] = (),
        test_change_authorization_path: str | Path | None = None,
        test_change_human_receipt_path: str | Path | None = None,
        test_change_validator_receipt_path: str | Path | None = None,
        candidate_runtime_path: str | Path | None = None,
        candidate_launch: Sequence[str] = (),
        candidate_loopback: Sequence[Mapping[str, object]] = (),
    ) -> BuildOutcome:
        if not _ATTEMPT_ID.fullmatch(attempt_id):
            raise OrchestrationError(
                "attempt_id must start with an alphanumeric and contain only letters, "
                "numbers, dot, underscore, or dash"
            )
        # Feasibility preflight (plan §1.1): the configuration-determined NO
        # fires HERE — after the attempt-id regex, before catalog parse, resume
        # verification, retention, and prepare — so a refused dispatch leaves
        # zero new files under the run root (forcing-tested; §1.1d made the
        # readiness path refusal-side-effect-free). Only the input groups
        # available at dispatch run; the CLI door covers the rest, tri-state.
        from factory_core.build_plan import BuildPlan
        from factory_core.target import TargetManifestError, load_target_manifest

        try:
            preflight_target = load_target_manifest(target_manifest_path)
        except (OSError, TargetManifestError) as exc:
            raise OrchestrationError(f"preflight: target manifest unreadable: {exc}") from exc
        try:
            preflight_plan_attempts: int | None = BuildPlan.from_dict(
                _object(Path(build_plan_path), label="build plan")
            ).max_build_attempts
        except (OrchestrationError, ValueError, KeyError, TypeError):
            # A malformed plan dies with its exact shape error at prepare();
            # the preflight only refuses on facts it could actually read.
            preflight_plan_attempts = None
        # 4.1d: the signal-deadline condition is a host refusal at THIS admission
        # door — the next BUILDING/model admission, never only a pager. The
        # BUILDING row of the full transition table will absorb it when that
        # migration reaches it; until then this preflight IS the admission.
        from factory_runtime.preflight import probe_signal_deadline

        preflight_report = run_preflight(
            target_build=dict(preflight_target.build),
            plan_max_build_attempts=preflight_plan_attempts,
            signal_deadline=probe_signal_deadline(self.workflow.root, run_id),
        )
        if not preflight_report.go:
            raise OrchestrationError(
                "preflight refused: "
                + "; ".join(
                    f"{finding.code}:{finding.subject}"
                    for finding in preflight_report.hard_no
                )
            )
        # Freeze the proposed catalog subject before opening the mutable run root.  The external
        # checkpoint must name this exact digest even on first activation; later authority and
        # provenance checks decide whether those bytes may actually become active.
        try:
            proposed_catalog = AcceptanceObligationCatalog.from_dict(
                _object(
                    Path(acceptance_catalog_path),
                    label="acceptance-obligation catalog",
                )
            )
        except AcceptanceObligationError as exc:
            raise OrchestrationError(str(exc)) from exc
        resume = verify_resume_checkpoint(
            resume_checkpoint_path,
            expected_checkpoint_digest=expected_resume_checkpoint_digest,
            runs_root=self.workflow.root,
            run_id=run_id,
            genesis_path=genesis_path,
            trusted_root_public_key=self.workflow.policy.root_public_key,
            tessera=self.workflow.tessera,
            configuration_sources=resume_configuration_sources,
            expected_acceptance_obligation_catalog_digest=(proposed_catalog.content_digest),
        )
        current = self.workflow.store.load(run_id)
        if current.ledger_head != resume.current_run_ledger_head:
            raise OrchestrationError(
                "run advanced after external resume verification; retry from a fresh checkpoint"
            )
        if current.state not in {
            RunState.OPERATIONAL_MATURITY_RATIFIED,
            RunState.BLOCKED,
        }:
            raise OrchestrationError(
                "build requires ratified invariant documents or a recoverable blocked attempt"
            )
        repair_brief_bytes: bytes | None = None
        repair_artifacts: dict[str, str] = {}
        if current.state == RunState.BLOCKED:
            if repair_brief_path is None:
                raise OrchestrationError("a blocked retry requires its signed repair brief")
            try:
                verified_repair = self.workflow.verify_recorded_repair_brief(
                    run_id,
                    envelope_path=repair_brief_path,
                    validator_identity=verifier_identity,
                    expected_attempt_id=attempt_id,
                )
                repair_envelope = verified_repair.envelope
                repair_brief_bytes = verified_repair.content
            except WorkflowError as exc:
                raise OrchestrationError(str(exc)) from exc
            repair_artifacts = {
                "repair-brief": repair_envelope.payload_digest,
                "repair-brief-envelope": repair_envelope.envelope_digest,
            }
        elif repair_brief_path is not None:
            raise OrchestrationError("an initial build may not inject a repair brief")
        verifier = self.workflow.policy.principal(verifier_identity)
        if verifier is None or verifier.kind != "agent":
            raise OrchestrationError("Validator verifier identity is not an enrolled agent")
        implementer = self.workflow.policy.principal(implementer_identity)
        tester = self.workflow.policy.principal(tester_identity)
        if implementer is None or implementer.kind != "agent":
            raise OrchestrationError("Coder implementer identity is not an enrolled agent")
        if tester is None or tester.kind != "agent":
            raise OrchestrationError("Tester identity is not an enrolled agent")
        if len({implementer_identity, tester_identity, verifier_identity}) != 3:
            raise OrchestrationError("Coder, Tester, and Validator identities must be distinct")
        if len({implementer.public_key, tester.public_key, verifier.public_key}) != 3:
            raise OrchestrationError("Coder, Tester, and Validator must not share signing keys")

        activating_catalog = current.state == RunState.OPERATIONAL_MATURITY_RATIFIED
        try:
            if activating_catalog:
                stored_catalog = verify_and_retain_acceptance_catalog(
                    self.workflow.root,
                    run_id,
                    catalog_path=acceptance_catalog_path,
                    human_receipt_path=acceptance_catalog_human_receipt_path,
                    validator_receipt_path=acceptance_catalog_validator_receipt_path,
                    policy=self.workflow.policy,
                    tessera=self.workflow.tessera,
                )
                acceptance_catalog = stored_catalog.catalog
                catalog_activation_artifacts = dict(stored_catalog.artifact_digests)
                # 4.1b: only the human AUTHORITY nonce is consumed; the Validator
                # attribution carries no replay ceremony. A phase-derived catalog re-cites the
                # already-consumed operational-maturity receipts, so it records no new nonce.
                catalog_activation_nonces = (
                    [stored_catalog.human_receipt.nonce]
                    if stored_catalog.consumes_new_nonces
                    else []
                )
                catalog_phase_derived = not stored_catalog.consumes_new_nonces
            else:
                acceptance_catalog = load_retained_acceptance_catalog(
                    self.workflow.root,
                    run_id,
                    expected_digest=current.acceptance_obligation_catalog_digest,
                )
                supplied_catalog = AcceptanceObligationCatalog.from_dict(
                    _object(
                        Path(acceptance_catalog_path),
                        label="acceptance-obligation catalog",
                    )
                )
                if supplied_catalog.content_digest != acceptance_catalog.content_digest:
                    raise AcceptanceObligationError(
                        "retry supplied a different acceptance-obligation catalog"
                    )
                catalog_activation_artifacts = {}
                catalog_activation_nonces = []
                catalog_phase_derived = False
        except AcceptanceObligationError as exc:
            raise OrchestrationError(str(exc)) from exc
        if acceptance_catalog.content_digest != resume.acceptance_obligation_catalog_digest:
            raise OrchestrationError(
                "ratified acceptance-obligation catalog differs from the external checkpoint"
            )

        changed_test_ids = tuple(str(test_id) for test_id in changed_existing_tests)
        test_change_paths = (
            test_change_authorization_path,
            test_change_human_receipt_path,
            test_change_validator_receipt_path,
        )
        if changed_test_ids and not all(path is not None for path in test_change_paths):
            raise OrchestrationError(
                "changed existing tests require authorization plus human and Validator receipts"
            )
        if not changed_test_ids and any(path is not None for path in test_change_paths):
            raise OrchestrationError(
                "test-change authority cannot be supplied without changed_existing_tests"
            )
        test_change_artifacts: dict[str, str] = {}
        test_change_nonces: list[str] = []
        test_change_directory: Path | None = None
        if changed_test_ids:
            assert test_change_authorization_path is not None
            assert test_change_human_receipt_path is not None
            assert test_change_validator_receipt_path is not None
            try:
                stored_test_change = verify_and_retain_test_change_authorization(
                    self.workflow.root,
                    run_id,
                    authorization_path=test_change_authorization_path,
                    human_receipt_path=test_change_human_receipt_path,
                    validator_receipt_path=test_change_validator_receipt_path,
                    changed_existing_tests=changed_test_ids,
                    policy=self.workflow.policy,
                    tessera=self.workflow.tessera,
                    additional_consumed_nonces=catalog_activation_nonces,
                )
            except TestChangeAuthorityError as exc:
                raise OrchestrationError(str(exc)) from exc
            test_change_artifacts = dict(stored_test_change.artifact_digests)
            test_change_nonces = list(stored_test_change.authority_nonces)
            test_change_directory = stored_test_change.directory

        retained_acceptance_catalog_path = (
            self.workflow.root
            / run_id
            / "evidence"
            / "acceptance-obligation-catalogs"
            / acceptance_catalog.content_digest.removeprefix("sha256:")
            / "catalog.json"
        )
        command_digest, configuration_digest, environment_digest = validator_execution_digests(
            validator_command,
            trusted_paths=validator_trusted_paths,
        )
        trigger = acceptance_catalog.select("validating", "preview")
        expected_execution = {
            "command_digest": command_digest,
            "configuration_digest": configuration_digest,
            "environment_digest": environment_digest,
        }
        for field, expected in expected_execution.items():
            if trigger[field] != expected:
                raise OrchestrationError(
                    f"acceptance-obligation catalog does not authorize Validator {field}"
                )

        try:
            prepared = GenerationPreparer(self.workflow.root).prepare(
                run_id,
                target_manifest_path=target_manifest_path,
                pattern_catalog_path=pattern_catalog_path,
                build_plan_path=build_plan_path,
            )
        except GenerationError as exc:
            raise OrchestrationError(str(exc)) from exc
        if prepared.plan.max_build_attempts > int(acceptance_catalog.document["max_review_rounds"]):
            raise OrchestrationError(
                "build plan attempt limit exceeds the ratified acceptance review limit"
            )

        attempt_root = self.workflow.root / run_id / "evidence" / "build-attempts" / attempt_id
        if attempt_root.exists():
            raise OrchestrationError(f"refusing to reuse build attempt: {attempt_id}")
        self.workflow.store.transition(
            run_id,
            RunState.BUILDING,
            actor="validator",
            artifact_digests={
                **prepared.artifact_digests,
                "resume-checkpoint": resume.checkpoint_digest,
                **repair_artifacts,
                **catalog_activation_artifacts,
                **test_change_artifacts,
            },
            payload={
                "attempt_id": attempt_id,
                "attempt_number": prepared.attempt_number,
                "attempt_limit": prepared.plan.max_build_attempts,
                "construction_mode": prepared.plan.construction_mode,
                "resume_checkpoint_id": resume.checkpoint_id,
                "anchored_run_ledger_head": resume.anchored_run_ledger_head,
                "anchored_run_ledger_length": resume.anchored_run_ledger_length,
                "changed_existing_tests": list(changed_test_ids),
                **(
                    {"catalog_authority_basis": "phase-ratification:operational-maturity"}
                    if catalog_phase_derived
                    else {}
                ),
                **(
                    {
                        "authority_receipt_nonces": [
                            *catalog_activation_nonces,
                            *test_change_nonces,
                        ]
                    }
                    if catalog_activation_nonces or test_change_nonces
                    else {}
                ),
            },
            implementer_identity=implementer_identity,
            verifier_identity=verifier_identity,
        )
        loop = IsolatedBuildLoop(attempt_root)
        candidate_digest = ""
        tests_digest = ""
        coder_snapshot_digest = ""
        tester_snapshot_digest = ""
        validator_execution_snapshot_digest = ""
        review_subject: Mapping[str, object] | None = None
        journal: ChecklistJournal | None = None

        def enter_validation(
            coder: LaneExecution,
            tester: LaneExecution,
            coder_snapshot: FrozenTree,
            tester_snapshot: FrozenTree,
        ) -> Mapping[str, object]:
            nonlocal candidate_digest, tests_digest, journal
            nonlocal coder_snapshot_digest, tester_snapshot_digest, review_subject
            nonlocal validator_execution_snapshot_digest
            candidate_digest = digest_artifact_tree(coder_snapshot.files_directory / "artifact")
            tests_digest = digest_artifact_tree(tester_snapshot.files_directory / "tests")
            coder_snapshot_digest = coder_snapshot.digest
            tester_snapshot_digest = tester_snapshot.digest
            validator_execution_snapshot_digest = verify_retained_validator_execution(
                self.workflow.root / run_id,
                attempt_id=attempt_id,
                command_digest=command_digest,
                configuration_digest=configuration_digest,
                environment_digest=environment_digest,
            )
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
                detail="Coder output was frozen before Validator review",
                actor="validator",
                observations={
                    "candidate_digest": candidate_digest,
                    "snapshot_digest": coder_snapshot_digest,
                },
            )
            journal.record(
                "tester-output",
                passed=True,
                detail="Tester output was frozen before Validator review",
                actor="validator",
                observations={
                    "tests_digest": tests_digest,
                    "snapshot_digest": tester_snapshot_digest,
                },
            )
            validation_projection = self.workflow.store.transition(
                run_id,
                RunState.VALIDATING,
                actor="validator",
                artifact_digests={
                    "candidate": candidate_digest,
                    "acceptance-tests": tests_digest,
                    "coder-output-snapshot": coder_snapshot_digest,
                    "tester-output-snapshot": tester_snapshot_digest,
                    "validator-execution-manifest": command_digest,
                    "validator-execution-configuration": configuration_digest,
                    "validator-execution-environment": environment_digest,
                    "validator-execution-snapshot": validator_execution_snapshot_digest,
                },
                payload={"tester_identity": tester_identity},
                implementer_identity=implementer_identity,
                verifier_identity=verifier_identity,
            )
            try:
                base_source_snapshot, candidate_change_set = build_candidate_review_context(
                    target_state=validation_projection.target_state,
                    candidate_root=coder_snapshot.files_directory / "artifact",
                    candidate_digest=candidate_digest,
                    construction_mode=prepared.plan.construction_mode,
                )
                checkpoint_bytes = read_stable_regular_bytes(
                    resume_checkpoint_path,
                    label="Validator review resume checkpoint",
                    max_bytes=4 * 1024 * 1024,
                )
                execution_request_bytes = read_stable_regular_bytes(
                    self.workflow.root / run_id / "evidence" / "intake" / "execution-request.json",
                    label="Validator review Stage-E execution request",
                    max_bytes=4 * 1024 * 1024,
                )
                configuration_sources = {}
                configuration_trees = {}
                configuration_large_files = {}
                for name, source in sorted(resume_configuration_sources.items()):
                    source_path = Path(source)
                    if not source_path.is_symlink() and source_path.is_dir():
                        # Sealed author outputs are directory sources; their review
                        # identity is the same deterministic tree digest the resume
                        # checkpoint bound, not a byte read.
                        configuration_trees[name] = tree_digest(source_path)
                        continue
                    if (
                        not source_path.is_symlink()
                        and source_path.is_file()
                        and source_path.stat().st_size > 4 * 1024 * 1024
                    ):
                        # A qualified lane executable binds by the same streaming
                        # digest the resume checkpoint derived, never embedded bytes.
                        configuration_large_files[name] = _stable_stream_digest(
                            source_path, name
                        )
                        continue
                    configuration_sources[name] = read_stable_regular_bytes(
                        source,
                        label=f"Validator review configuration source {name!r}",
                        max_bytes=4 * 1024 * 1024,
                    )
                test_change_sources: dict[str, bytes] = {}
                if test_change_directory is not None:
                    test_change_sources = {
                        TEST_CHANGE_AUTHORIZATION_KEY: read_stable_regular_bytes(
                            test_change_directory / "authorization.json",
                            label="retained test-change authorization",
                            max_bytes=4 * 1024 * 1024,
                        ),
                        HUMAN_RECEIPT_KEY: read_stable_regular_bytes(
                            test_change_directory / "human-receipt.tessera.json",
                            label="retained human test-change receipt",
                            max_bytes=4 * 1024 * 1024,
                        ),
                        VALIDATOR_RECEIPT_KEY: read_stable_regular_bytes(
                            test_change_directory / "validator-receipt.tessera.json",
                            label="retained Validator test-change receipt",
                            max_bytes=4 * 1024 * 1024,
                        ),
                    }
                authority_context = build_review_authority_context(
                    resume_checkpoint_digest=resume.checkpoint_digest,
                    resume_checkpoint_source_digest=resume.checkpoint_source_digest,
                    resume_checkpoint_bytes=checkpoint_bytes,
                    configuration_sources=configuration_sources,
                    expected_configuration_digests=resume.configuration_digests,
                    changed_existing_tests=changed_test_ids,
                    test_change_artifacts=test_change_artifacts,
                    test_change_sources=test_change_sources,
                    configuration_trees=configuration_trees,
                    configuration_large_files=configuration_large_files,
                )
            except (CandidateDiffError, StateAdmissionError) as exc:
                raise OrchestrationError(str(exc)) from exc
            review_subject = build_validator_review_subject(
                run_id=run_id,
                generation=validation_projection.generation,
                target_digest=validation_projection.target_digest,
                target_state_digest=validation_projection.target_state_digest,
                resolved_commit=str(validation_projection.target_state.get("resolved_commit", "")),
                resolved_tree=str(validation_projection.target_state.get("resolved_tree", "")),
                reviewer_identity=verifier_identity,
                base_source_snapshot=base_source_snapshot,
                candidate_change_set=candidate_change_set,
                authority_context=authority_context,
                execution_request_bytes=execution_request_bytes,
                build_input=prepared.build_input,
                build_input_digest=str(prepared.artifact_digests["build-input"]),
                pattern_catalog_digest=str(prepared.artifact_digests["pattern-catalog"]),
                pattern_catalog_source_digest=str(
                    prepared.artifact_digests["pattern-catalog-source"]
                ),
                build_plan_digest=str(prepared.artifact_digests["build-plan"]),
                build_plan_source_digest=str(prepared.artifact_digests["build-plan-source"]),
                phase_artifact_digests=validation_projection.phase_artifact_digests,
                acceptance_obligation_catalog_digest=acceptance_catalog.content_digest,
                acceptance_obligation_catalog_source_digest=digest_bytes(
                    retained_acceptance_catalog_path.read_bytes()
                ),
                candidate_digest=candidate_digest,
                acceptance_tests_digest=tests_digest,
                coder_output_snapshot_digest=coder_snapshot_digest,
                tester_output_snapshot_digest=tester_snapshot_digest,
                command_digest=command_digest,
                configuration_digest=configuration_digest,
                environment_digest=environment_digest,
            )
            return review_subject

        try:
            execution = loop.execute(
                build_input_path=prepared.build_input_path,
                coder_command=coder_command,
                tester_command=tester_command,
                validator_command=validator_command,
                coder_trusted_paths=coder_trusted_paths,
                tester_trusted_paths=tester_trusted_paths,
                validator_trusted_paths=validator_trusted_paths,
                prebuilt_author_outputs=prebuilt_author_outputs,
                build_plan_path=prepared.build_plan_path,
                pattern_catalog_path=prepared.pattern_catalog_path,
                acceptance_catalog_path=retained_acceptance_catalog_path,
                review_snapshot_store=(
                    self.workflow.root / run_id / "evidence" / "review-snapshots"
                ),
                review_snapshot_durable_through=self.workflow.root / run_id,
                repair_brief_bytes=repair_brief_bytes,
                candidate_runtime_path=candidate_runtime_path,
                candidate_launch=candidate_launch,
                candidate_loopback=candidate_loopback,
                before_validation=enter_validation,
            )
        except Exception as exc:
            self._record_exception_as_blocked(
                run_id,
                exc=exc,
                tester_identity=tester_identity,
                # The causal lane identities are known and truthful even when the
                # failure precedes any lane output; omitting them left the blocked
                # entry unrecoverable by the repair-brief machinery, which requires
                # all three roles on the causal failed attempt.
                implementer_identity=implementer_identity,
                verifier_identity=verifier_identity,
                candidate_digest=candidate_digest,
                tests_digest=tests_digest,
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
            return BuildOutcome(
                candidate_digest="",
                tests_digest="",
                execution=execution,
                projection=projection,
                evidence_report=None,
                evidence_envelope=None,
                acceptance_report=None,
                acceptance_report_digest="",
                resume_verification=resume,
            )
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
                candidate_digest=candidate_digest,
                tests_digest=tests_digest,
                execution=execution,
                projection=projection,
                evidence_report=None,
                evidence_envelope=None,
                acceptance_report=None,
                acceptance_report_digest="",
                resume_verification=resume,
            )

        product_acceptance_report: Mapping[str, object] | None = None
        product_acceptance_report_digest = ""
        verified_review: VerifiedAdversarialReview | None = None
        review_artifacts: Mapping[str, str] = {}
        try:
            if execution.coder_snapshot is None or execution.tester_snapshot is None:
                raise OrchestrationError("immutable author review snapshots are missing")
            if review_subject is None:
                raise OrchestrationError("Validator adversarial-review subject is missing")
            validation_projection = self.workflow.store.load(run_id)
            trusted_acceptance_evidence = {
                "candidate": candidate_digest,
                "acceptance-tests": tests_digest,
                "coder-output-snapshot": coder_snapshot_digest,
                "tester-output-snapshot": tester_snapshot_digest,
            }
            observations_path = (
                execution.validator.output_directory / "acceptance-obligation-observations.json"
            )
            observations = _object(
                observations_path,
                label="Validator acceptance-obligation observations",
            )
            product_acceptance_report = derive_acceptance_obligation_report(
                acceptance_catalog,
                observations=observations,
                run_id=run_id,
                generation=validation_projection.generation,
                source=str(RunState.VALIDATING),
                destination=str(RunState.PREVIEW),
                target_state_digest=validation_projection.target_state_digest,
                resolved_commit=str(validation_projection.target_state.get("resolved_commit", "")),
                resolved_tree=str(validation_projection.target_state.get("resolved_tree", "")),
                phase_artifact_digests=validation_projection.phase_artifact_digests,
                candidate_digest=candidate_digest,
                acceptance_tests_digest=tests_digest,
                command_digest=command_digest,
                configuration_digest=configuration_digest,
                environment_digest=environment_digest,
                trusted_evidence_digests=trusted_acceptance_evidence,
            )
            product_acceptance_report_digest = retain_acceptance_obligation_report(
                self.workflow.root,
                run_id,
                product_acceptance_report,
            )
            review_report = load_canonical_review_report(
                execution.validator.output_directory / "validator-adversarial-review.json"
            )
            verified_review = verify_validator_adversarial_review(
                review_report,
                subject=review_subject,
                reviewer_identity=verifier_identity,
                acceptance_observations=observations,
                implementation_root=execution.coder_snapshot.files_directory / "artifact",
                tests_root=execution.tester_snapshot.files_directory / "tests",
                build_input_path=prepared.build_input_path,
                pattern_catalog_path=prepared.pattern_catalog_path,
                build_plan_path=prepared.build_plan_path,
                acceptance_catalog_path=retained_acceptance_catalog_path,
                acceptance_observations_path=observations_path,
            )
            review_artifacts = retain_validator_adversarial_review(
                self.workflow.root,
                run_id,
                verified_review,
            )
            journal.record(
                "adversarial-review",
                passed=verified_review.passed,
                detail=(
                    "Validator adversarial review completed with no surviving finding"
                    if verified_review.passed
                    else f"Validator adversarial review returned {verified_review.verdict}"
                ),
                actor="validator",
                observations={
                    **dict(review_artifacts),
                    "verdict": verified_review.verdict,
                },
            )
            if not verified_review.passed:
                projection = self.workflow.store.transition(
                    run_id,
                    RunState.BLOCKED,
                    actor="validator",
                    artifact_digests={
                        "candidate": candidate_digest,
                        "acceptance-tests": tests_digest,
                        REPORT_ARTIFACT_KEY: product_acceptance_report_digest,
                        **dict(review_artifacts),
                    },
                    payload={
                        "reason": "validator-adversarial-review-failed",
                        "review_verdict": verified_review.verdict,
                        "repair_signal": "fail",
                        "tester_identity": tester_identity,
                    },
                    implementer_identity=implementer_identity,
                    verifier_identity=verifier_identity,
                )
                return BuildOutcome(
                    candidate_digest=candidate_digest,
                    tests_digest=tests_digest,
                    execution=execution,
                    projection=projection,
                    evidence_report=None,
                    evidence_envelope=None,
                    acceptance_report=product_acceptance_report,
                    acceptance_report_digest=product_acceptance_report_digest,
                    resume_verification=resume,
                    adversarial_review=verified_review.report,
                    adversarial_review_digest=verified_review.report_digest,
                )
            claims = _claims(
                execution.tester_snapshot.files_directory / "evidence" / "assertions.json"
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
                validated_artifact_digests={
                    REPORT_ARTIFACT_KEY: product_acceptance_report_digest,
                    **dict(review_artifacts),
                    "validator-execution-manifest": command_digest,
                    "validator-execution-configuration": configuration_digest,
                    "validator-execution-environment": environment_digest,
                    "validator-execution-snapshot": validator_execution_snapshot_digest,
                },
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
                    artifact_digests={
                        "candidate": candidate_digest,
                        "acceptance-tests": tests_digest,
                        **dict(review_artifacts),
                    },
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
                    candidate_digest=candidate_digest,
                    tests_digest=tests_digest,
                    execution=execution,
                    projection=projection,
                    evidence_report=report,
                    evidence_envelope=None,
                    acceptance_report=product_acceptance_report,
                    acceptance_report_digest=product_acceptance_report_digest,
                    resume_verification=resume,
                    adversarial_review=verified_review.report,
                    adversarial_review_digest=verified_review.report_digest,
                )

            bundle_path = attempt_root / "evidence-bundle.tessera.json"
            envelope = self.workflow.tessera.wrap_json(
                report.document,
                kind="factory-evidence-bundle",
                key_path=verifier_key_path,
                output_path=bundle_path,
            
                forbidden_signer_public_keys=human_public_keys(self.workflow.policy),
            )
            try:
                fsync_directory_chain(bundle_path.parent, through=self.workflow.root)
            except DurabilityError as exc:
                raise OrchestrationError(str(exc)) from exc
            # 4.1b: the host-minted-then-host-verified bundle signer check is
            # deleted — the host signed this envelope one call above, so verifying
            # its own signature proved nothing; the signature is attribution and
            # the enrolled-key threading above already refuses human keys.
            projection = self.workflow.store.transition(
                run_id,
                RunState.PREVIEW,
                actor="validator",
                artifact_digests={
                    "candidate": candidate_digest,
                    "acceptance-tests": tests_digest,
                    REPORT_ARTIFACT_KEY: product_acceptance_report_digest,
                    **dict(review_artifacts),
                    "validator-execution-manifest": command_digest,
                    "validator-execution-configuration": configuration_digest,
                    "validator-execution-environment": environment_digest,
                    "validator-execution-snapshot": validator_execution_snapshot_digest,
                    "evidence-bundle": envelope.payload_digest,
                    "evidence-envelope": envelope.envelope_digest,
                },
                payload={
                    "repair_signal": "pass",
                    "standard_gate_issues": list(report.gate_issues),
                    "reports": list(report.reports),
                    "tester_identity": tester_identity,
                    "command_digest": command_digest,
                    "configuration_digest": configuration_digest,
                    "environment_digest": environment_digest,
                    "test_family": trigger["trigger_id"],
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
                candidate_digest=candidate_digest,
                tests_digest=tests_digest,
            )
            raise
        return BuildOutcome(
            candidate_digest=candidate_digest,
            tests_digest=tests_digest,
            execution=execution,
            projection=projection,
            evidence_report=report,
            evidence_envelope=envelope,
            acceptance_report=product_acceptance_report,
            acceptance_report_digest=product_acceptance_report_digest,
            resume_verification=resume,
            adversarial_review=(verified_review.report if verified_review is not None else None),
            adversarial_review_digest=(
                verified_review.report_digest if verified_review is not None else ""
            ),
        )
