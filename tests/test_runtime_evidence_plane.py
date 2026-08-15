from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from factory_core.build_plan import (
    BuildPlan,
    BuildStep,
    OracleLink,
    PatternCatalog,
    PatternDefinition,
)
from factory_core.correction import (
    BASELINE_RESULT_FAILED,
    BASELINE_RESULT_PASSED,
    CONTROL_GREEN_NOW,
    CONTROL_RED_NOW,
    FAILURE_RELATION_DEFECT,
    LANE_CAPABILITY,
    LANE_CORRECTION,
    REPRODUCTION_REPRODUCED,
    ControlObservation,
    CorrectionRecord,
    ReproductionRecord,
)
from factory_core.evidence import EvidenceIntegrity
from factory_core.independence import (
    INDEPENDENCE_MODERATE,
    INDEPENDENCE_STRONGER,
    INDEPENDENCE_WEAKEST,
    ROLE_CODER,
    ROLE_TESTER,
    ROLE_VALIDATOR,
    STRUCTURAL_MODE_ISOLATED,
    AgentIdentity,
    IndependenceRecord,
    StructuralModeRecord,
)
from factory_core.manifest import SegregationPolicy, digest_bytes, digest_obj
from factory_core.monitors import (
    MONITOR_AUTHORSHIP_GENERATED,
    MONITOR_AUTHORSHIP_HUMAN,
    MONITOR_DERIVATION_SPECIFICATION,
    Monitor,
)
from factory_core.provenance import (
    PHASE_ARCHITECTURE,
    PHASE_OPERATIONAL_MATURITY,
    PHASE_PRODUCT_SPECIFICATION,
    IntentItem,
    PhaseArtifact,
    ProvenanceClaim,
)
from factory_core.target import load_target_manifest
from factory_runtime.evidence_plane import (
    ChecklistJournal,
    DeterminismRecord,
    EvidenceBundleAssembler,
    EvidencePlaneError,
    SurfaceEvidence,
)
from factory_runtime.generation import GenerationPreparer, build_input_document
from factory_runtime.schema import DocumentValidationError
from factory_runtime.snapshot import freeze_tree, tree_digest
from factory_runtime.state import RunState, RunStore
from tests.conftest import ratification_receipts

TARGET = "sha256:" + ("1" * 64)
SOURCE = "sha256:" + ("2" * 64)
CANDIDATE = digest_obj(
    {
        "files": [
            {
                "path": "candidate.txt",
                "mode": 0o444,
                "digest": digest_bytes(b"candidate"),
            }
        ]
    }
)
TESTS = digest_obj(
    {
        "files": [
            {
                "path": "acceptance.txt",
                "mode": 0o444,
                "digest": digest_bytes(b"acceptance"),
            }
        ]
    }
)


class _Clock:
    def __init__(self) -> None:
        self.value = 100

    def __call__(self) -> int:
        self.value += 1
        return self.value


def _phase(phase: str, artifact_id: str) -> PhaseArtifact:
    return PhaseArtifact(
        artifact_id=artifact_id,
        phase=phase,
        version="1",
        source_digest=SOURCE,
        human_ratifier="human:founder",
        validator_ratifier="agent:validator",
        items=(
            IntentItem(
                item_id=f"{phase}:1",
                canonical_statement=f"The {phase} invariant is authoritative.",
            ),
        ),
    )


def _ratified_run(root: Path) -> tuple[RunStore, tuple[PhaseArtifact, ...]]:
    clock = _Clock()
    store = RunStore(root, clock=clock)
    artifacts = (
        _phase(PHASE_PRODUCT_SPECIFICATION, "product"),
        _phase(PHASE_ARCHITECTURE, "architecture"),
        _phase(PHASE_OPERATIONAL_MATURITY, "operations"),
    )
    pattern = PatternDefinition(
        pattern_id="module",
        version="1",
        artifact_digest=digest_obj({"pattern": "module"}),
        qualification_evidence_digest=digest_obj({"qualified": "module"}),
        mechanism={"kind": "module"},
    )
    catalog = PatternCatalog("catalog", "1", (pattern,))
    catalog_path = root / "pattern-catalog.json"
    catalog_path.write_text(json.dumps(catalog.body()), encoding="utf-8")
    target_path = root / "target.toml"
    target_path.write_text(
        "\n".join(
            (
                'schema_version = "factory-target-manifest/1"',
                'target_id = "synthetic-evidence"',
                "[repo]",
                'url = "https://example.invalid/repo.git"',
                'ref = "main"',
                "[adapters]",
                'repo = "readonly_git"',
                'knowledge = "kin_reader"',
                'compliance = "rules_json"',
                'idp = "oidc"',
                'artifact_sink = "local_fs"',
                "[compliance]",
                'rules_path = "compliance/rules.json"',
                "[build]",
                f'pattern_catalog_digest = "{catalog.content_digest}"',
                "max_attempts = 1",
                'construction_modes = ["regenerate", "brownfield"]',
                "",
            )
        ),
        encoding="utf-8",
    )
    target = load_target_manifest(target_path)
    store.create(
        "run-1",
        target_digest=target.content_digest,
        source_digest=SOURCE,
        actor="validator",
    )
    states = (
        RunState.PRODUCT_SPECIFICATION_RATIFIED,
        RunState.ARCHITECTURE_RATIFIED,
        RunState.OPERATIONAL_MATURITY_RATIFIED,
    )
    for artifact, state in zip(artifacts, states, strict=True):
        directory = (
            root
            / "run-1"
            / "evidence"
            / artifact.phase
            / artifact.content_digest.removeprefix("sha256:")
        )
        directory.mkdir(parents=True)
        (directory / "artifact.json").write_text(
            json.dumps(artifact.body(), sort_keys=True),
            encoding="utf-8",
        )
        store.transition(
            "run-1",
            state,
            actor="validator",
            artifact_digests={
                artifact.phase: artifact.content_digest,
                **ratification_receipts(artifact.phase),
            },
        )
    build_input = build_input_document("run-1", target.content_digest, artifacts)
    product, architecture, operations = artifacts
    plan = BuildPlan(
        plan_id="plan-1",
        version="1",
        run_id="run-1",
        target_digest=target.content_digest,
        construction_mode="regenerate",
        max_build_attempts=1,
        build_input_digest=digest_obj(build_input),
        pattern_catalog_digest=catalog.content_digest,
        phase_artifact_digests={artifact.phase: artifact.content_digest for artifact in artifacts},
        steps=(
            BuildStep(
                step_id="construct",
                pattern_id=pattern.pattern_id,
                pattern_digest=pattern.content_digest,
                configuration={"module": "candidate.txt"},
                intent_backreferences=(
                    product.backreference(product.items[0]),
                    architecture.backreference(architecture.items[0]),
                ),
            ),
        ),
        oracle_links=(
            OracleLink(
                product.backreference(product.items[0]),
                operations.backreference(operations.items[0]),
            ),
            OracleLink(
                architecture.backreference(architecture.items[0]),
                operations.backreference(operations.items[0]),
            ),
        ),
    )
    plan_path = root / "build-plan.json"
    plan_path.write_text(json.dumps(plan.body()), encoding="utf-8")
    prepared = GenerationPreparer(root).prepare(
        "run-1",
        target_manifest_path=target_path,
        pattern_catalog_path=catalog_path,
        build_plan_path=plan_path,
    )
    store.transition(
        "run-1",
        RunState.BUILDING,
        actor="validator",
        artifact_digests=prepared.artifact_digests,
        payload={"attempt_number": 1, "attempt_limit": 1},
    )
    coder_output = root / "coder-output"
    tester_output = root / "tester-output"
    (coder_output / "artifact").mkdir(parents=True)
    (tester_output / "tests").mkdir(parents=True)
    candidate_file = coder_output / "artifact" / "candidate.txt"
    tests_file = tester_output / "tests" / "acceptance.txt"
    candidate_file.write_bytes(b"candidate")
    tests_file.write_bytes(b"acceptance")
    candidate_file.chmod(0o444)
    tests_file.chmod(0o444)
    review_root = root / "run-1" / "evidence" / "review-snapshots"
    coder_snapshot = freeze_tree(coder_output, review_root)
    tester_snapshot = freeze_tree(tester_output, review_root)
    assert tree_digest(coder_snapshot.files_directory / "artifact") == CANDIDATE
    assert tree_digest(tester_snapshot.files_directory / "tests") == TESTS
    store.transition(
        "run-1",
        RunState.VALIDATING,
        actor="validator",
        artifact_digests={
            "candidate": CANDIDATE,
            "acceptance-tests": TESTS,
            "coder-output-snapshot": coder_snapshot.digest,
            "tester-output-snapshot": tester_snapshot.digest,
        },
    )
    return store, artifacts


def _claim(artifact: PhaseArtifact) -> ProvenanceClaim:
    return ProvenanceClaim(
        claim_id="acceptance:1",
        kind="test-assertion",
        backreference=artifact.backreference(artifact.items[0]),
    )


def _journal(root: Path) -> ChecklistJournal:
    journal = ChecklistJournal(
        root / "run-1" / "evidence" / "checklist.jsonl",
        subject_digest=CANDIDATE,
        clock=_Clock(),
    )
    journal.record(
        "build",
        passed=True,
        detail="candidate built",
        actor="validator",
        observations={"command": "synthetic-build"},
    )
    journal.record(
        "tests",
        passed=True,
        detail="acceptance tests passed",
        actor="validator",
        observations={"test_count": 2},
    )
    return journal


def _surface(
    *,
    criticality: str = "critical",
    adequate: bool = True,
    evidence: bool = True,
) -> SurfaceEvidence:
    return SurfaceEvidence(
        surface_id="control-plane",
        criticality=criticality,
        oracle_adequate=adequate,
        required_evidence_ids=(("tests",) if evidence else ("unavailable",)),
        evidence_digests={},
    )


def _determinism(
    *,
    criticality: str = "critical",
    deterministic: bool = True,
    flakes: int = 0,
    retries: int = 0,
) -> DeterminismRecord:
    return DeterminismRecord(
        surface_id="control-plane",
        criticality=criticality,
        deterministic=deterministic,
        flake_count=flakes,
        automatic_retry_count=retries,
    )


def _policy() -> SegregationPolicy:
    return SegregationPolicy(
        human_ids=frozenset({"human:founder"}),
        human_aliases={"human:founder": "human:founder"},
        excluded_service_identities=frozenset({"agent:*"}),
    )


def _evidence(body: dict[str, Any]) -> EvidenceIntegrity:
    return EvidenceIntegrity(body=body, claimed_digest=digest_obj(body))


def _independence(
    *,
    tester_family: str = "family-b",
    claimed_tier: str = INDEPENDENCE_STRONGER,
) -> IndependenceRecord:
    structural = StructuralModeRecord(
        mode=STRUCTURAL_MODE_ISOLATED,
        decision_package_note="No signed interface contract anchored the oracle.",
    )
    return IndependenceRecord(
        agents=(
            AgentIdentity(
                role=ROLE_CODER,
                model_family="family-a",
                model_version="2026-07",
                directive_version="coder-3",
            ),
            AgentIdentity(
                role=ROLE_TESTER,
                model_family=tester_family,
                model_version="2026-07",
                directive_version="tester-3",
            ),
            AgentIdentity(
                role=ROLE_VALIDATOR,
                model_family="family-c",
                model_version="2026-07",
                directive_version="validator-3",
            ),
        ),
        shared_context=False,
        channel_open=False,
        claimed_tier=claimed_tier,
        structural_mode=replace(
            structural,
            mutation_evidence=_evidence(structural.authority_body()),
        ),
    )


def _monitor(
    artifacts: Sequence[PhaseArtifact],
    *,
    authorship: str = MONITOR_AUTHORSHIP_HUMAN,
    author: str = "human:founder",
) -> Monitor:
    product = artifacts[0]
    return Monitor(
        monitor_id="monitor-control-plane",
        surface_id="control-plane",
        derivation=MONITOR_DERIVATION_SPECIFICATION,
        authorship=authorship,
        author_identity=author,
        backreference=product.backreference(product.items[0]),
        actionable_conclusion="Page the control-plane owner with the unmet invariant.",
        notifies_human=True,
    )


def _correction() -> CorrectionRecord:
    reproduction = ReproductionRecord(
        defect_id="defect-1",
        result=REPRODUCTION_REPRODUCED,
        environment_id="ephemeral-1",
        disposable_environment=True,
        recorded_before_repair=True,
    )
    return CorrectionRecord(
        defect_id="defect-1",
        baseline_available=True,
        controls=(
            ControlObservation(
                test_id="forces-the-defect",
                declared_role=CONTROL_RED_NOW,
                baseline_result=BASELINE_RESULT_FAILED,
                failure_relation=FAILURE_RELATION_DEFECT,
            ),
            ControlObservation(
                test_id="guards-unrelated-behavior",
                declared_role=CONTROL_GREEN_NOW,
                baseline_result=BASELINE_RESULT_PASSED,
            ),
        ),
        reproduction=replace(
            reproduction,
            evidence=_evidence(reproduction.authority_body()),
        ),
    )


def _records(artifacts: Sequence[PhaseArtifact], **overrides: Any) -> dict[str, Any]:
    """The records the bundle must carry: lane, independence, and the monitor set."""

    values: dict[str, Any] = {
        "lane": LANE_CAPABILITY,
        "independence": _independence(),
        "monitors": (_monitor(artifacts),),
        "policy": _policy(),
    }
    values.update(overrides)
    return values


def test_checklist_is_hash_chained_and_missing_items_stay_visible(tmp_path: Path) -> None:
    journal = ChecklistJournal(
        tmp_path / "checklist.jsonl",
        subject_digest=CANDIDATE,
        clock=_Clock(),
    )
    journal.record(
        "build",
        passed=True,
        detail="built",
        actor="validator",
    )

    report = journal.report(("build", "tests"))
    assert report.satisfied is False
    assert report.satisfied_item_ids == ("build",)
    assert report.gaps == ("checklist-item-missing:tests",)

    path = tmp_path / "checklist.jsonl"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["payload"]["checklist_result"]["passed"] = False
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(EvidencePlaneError, match="verification failed"):
        journal.results()


def test_bundle_rederives_phase_artifacts_provenance_checklist_and_surface_policy(
    tmp_path: Path,
) -> None:
    _, artifacts = _ratified_run(tmp_path)
    report = EvidenceBundleAssembler(tmp_path).assemble(
        "run-1",
        candidate_digest=CANDIDATE,
        claims=(_claim(artifacts[0]),),
        checklist_journal=_journal(tmp_path),
        required_checklist_item_ids=("build", "tests"),
        surface_evidence=(_surface(),),
        determinism_records=(_determinism(),),
        **_records(artifacts),
    )

    assert report.mechanically_satisfied is True
    assert report.blocking_issues == ()
    assert report.gate_issues == ()
    assert report.document["ledger_head"]
    assert report.document["acceptance_tests_digest"] == TESTS


def test_critical_gap_or_flake_blocks_while_standard_gap_gates(tmp_path: Path) -> None:
    _, artifacts = _ratified_run(tmp_path)
    assembler = EvidenceBundleAssembler(tmp_path)
    journal = _journal(tmp_path)

    critical = assembler.assemble(
        "run-1",
        candidate_digest=CANDIDATE,
        claims=(_claim(artifacts[0]),),
        checklist_journal=journal,
        required_checklist_item_ids=("build", "tests"),
        surface_evidence=(_surface(adequate=False, evidence=False),),
        determinism_records=(_determinism(deterministic=False, flakes=1, retries=1),),
        **_records(artifacts),
    )
    assert "critical-evidence-gap:control-plane" in critical.blocking_issues
    assert "critical-nondeterminism:control-plane" in critical.blocking_issues
    assert critical.mechanically_satisfied is False

    standard = assembler.assemble(
        "run-1",
        candidate_digest=CANDIDATE,
        claims=(_claim(artifacts[0]),),
        checklist_journal=journal,
        required_checklist_item_ids=("build", "tests"),
        surface_evidence=(_surface(criticality="standard", adequate=False, evidence=False),),
        determinism_records=(_determinism(criticality="standard"),),
        **_records(artifacts),
    )
    assert standard.blocking_issues == ()
    assert standard.gate_issues == ("standard-evidence-gap:control-plane",)


def test_bundle_rejects_unbound_candidate_and_fabricated_surface_evidence(
    tmp_path: Path,
) -> None:
    _, artifacts = _ratified_run(tmp_path)
    assembler = EvidenceBundleAssembler(tmp_path)
    journal = _journal(tmp_path)

    with pytest.raises(EvidencePlaneError, match="candidate digest"):
        assembler.assemble(
            "run-1",
            candidate_digest="sha256:" + ("9" * 64),
            claims=(_claim(artifacts[0]),),
            checklist_journal=journal,
            required_checklist_item_ids=("build", "tests"),
            surface_evidence=(_surface(),),
            determinism_records=(_determinism(),),
            **_records(artifacts),
        )

    fabricated = SurfaceEvidence(
        surface_id="control-plane",
        criticality="critical",
        oracle_adequate=True,
        required_evidence_ids=("tests",),
        evidence_digests={"tests": digest_obj({"fabricated": True})},
    )
    report = assembler.assemble(
        "run-1",
        candidate_digest=CANDIDATE,
        claims=(_claim(artifacts[0]),),
        checklist_journal=journal,
        required_checklist_item_ids=("build", "tests"),
        surface_evidence=(fabricated,),
        determinism_records=(_determinism(),),
        **_records(artifacts),
    )
    assert "surface-evidence-mismatch:control-plane:tests" in report.blocking_issues
    assert report.mechanically_satisfied is False


def test_bundle_refuses_phase_bytes_that_no_longer_match_the_run_ledger(
    tmp_path: Path,
) -> None:
    _, artifacts = _ratified_run(tmp_path)
    product = artifacts[0]
    path = (
        tmp_path
        / "run-1"
        / "evidence"
        / product.phase
        / product.content_digest.removeprefix("sha256:")
        / "artifact.json"
    )
    body = json.loads(path.read_text())
    body["version"] = "forged"
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(EvidencePlaneError, match="does not match"):
        EvidenceBundleAssembler(tmp_path).assemble(
            "run-1",
            candidate_digest=CANDIDATE,
            claims=(_claim(product),),
            checklist_journal=_journal(tmp_path),
            required_checklist_item_ids=("build", "tests"),
            surface_evidence=(_surface(),),
            determinism_records=(_determinism(),),
            **_records(artifacts),
        )


def test_bundle_refuses_an_undeclared_or_unknown_lane(tmp_path: Path) -> None:
    _, artifacts = _ratified_run(tmp_path)
    assembler = EvidenceBundleAssembler(tmp_path)

    for lane in ("", "hotfix"):
        with pytest.raises(EvidencePlaneError, match="lane must be declared"):
            assembler.assemble(
                "run-1",
                candidate_digest=CANDIDATE,
                claims=(_claim(artifacts[0]),),
                checklist_journal=_journal(tmp_path),
                required_checklist_item_ids=("build", "tests"),
                surface_evidence=(_surface(),),
                determinism_records=(_determinism(),),
                **_records(artifacts, lane=lane),
            )


def test_bundle_records_the_derived_tier_and_refuses_an_overclaim(tmp_path: Path) -> None:
    _, artifacts = _ratified_run(tmp_path)
    assembler = EvidenceBundleAssembler(tmp_path)

    honest = assembler.assemble(
        "run-1",
        candidate_digest=CANDIDATE,
        claims=(_claim(artifacts[0]),),
        checklist_journal=_journal(tmp_path),
        required_checklist_item_ids=("build", "tests"),
        surface_evidence=(_surface(),),
        determinism_records=(_determinism(),),
        **_records(artifacts),
    )
    overclaimed = assembler.assemble(
        "run-1",
        candidate_digest=CANDIDATE,
        claims=(_claim(artifacts[0]),),
        checklist_journal=_journal(tmp_path),
        required_checklist_item_ids=("build", "tests"),
        surface_evidence=(_surface(),),
        determinism_records=(_determinism(),),
        **_records(
            artifacts,
            independence=_independence(tester_family="family-a"),
        ),
    )

    assert honest.document["independence"]["derived_tier"] == INDEPENDENCE_STRONGER
    assert honest.document["independence"]["agents"][0]["model_version"] == "2026-07"
    assert honest.document["independence"]["agents"][0]["directive_version"] == "coder-3"
    assert (
        f"independence-integrity:independence-tier-overclaimed:"
        f"{INDEPENDENCE_STRONGER}:{INDEPENDENCE_MODERATE}" in overclaimed.blocking_issues
    )
    assert overclaimed.mechanically_satisfied is False


def test_an_unrecorded_independence_arrangement_cannot_produce_a_bundle(tmp_path: Path) -> None:
    _, artifacts = _ratified_run(tmp_path)

    # The bundle is the record, and the closed schema refuses to write one that omits the model,
    # directive version, or tier: an unrecorded arrangement is not a weaker record, it is an
    # unusable one.
    with pytest.raises(DocumentValidationError, match="independence"):
        EvidenceBundleAssembler(tmp_path).assemble(
            "run-1",
            candidate_digest=CANDIDATE,
            claims=(_claim(artifacts[0]),),
            checklist_journal=_journal(tmp_path),
            required_checklist_item_ids=("build", "tests"),
            surface_evidence=(_surface(),),
            determinism_records=(_determinism(),),
            **_records(artifacts, independence=IndependenceRecord()),
        )


def test_a_recorded_but_unisolated_arrangement_blocks_the_bundle(tmp_path: Path) -> None:
    _, artifacts = _ratified_run(tmp_path)
    leaky = replace(
        _independence(claimed_tier=INDEPENDENCE_WEAKEST),
        shared_context=True,
        channel_open=True,
    )

    report = EvidenceBundleAssembler(tmp_path).assemble(
        "run-1",
        candidate_digest=CANDIDATE,
        claims=(_claim(artifacts[0]),),
        checklist_journal=_journal(tmp_path),
        required_checklist_item_ids=("build", "tests"),
        surface_evidence=(_surface(),),
        determinism_records=(_determinism(),),
        **_records(artifacts, independence=leaky),
    )

    # Schema-valid and honestly claimed, but the arrangement itself violates the separation.
    assert report.document["independence"]["derived_tier"] == INDEPENDENCE_WEAKEST
    assert "independence-failure:independence-coder-tester-channel-open" in report.blocking_issues
    assert report.mechanically_satisfied is False


def test_monitor_coverage_is_class_disposed_at_the_bundle_boundary(tmp_path: Path) -> None:
    _, artifacts = _ratified_run(tmp_path)
    assembler = EvidenceBundleAssembler(tmp_path)
    journal = _journal(tmp_path)

    def _uncovered(criticality: str) -> object:
        return assembler.assemble(
            "run-1",
            candidate_digest=CANDIDATE,
            claims=(_claim(artifacts[0]),),
            checklist_journal=journal,
            required_checklist_item_ids=("build", "tests"),
            surface_evidence=(_surface(criticality=criticality),),
            determinism_records=(_determinism(criticality=criticality),),
            **_records(artifacts, monitors=()),
        )

    critical = _uncovered("critical")
    standard = _uncovered("standard")
    cosmetic = _uncovered("cosmetic")

    assert "critical-gap:monitor-coverage-missing:control-plane" in critical.blocking_issues
    assert "standard-gap:monitor-coverage-missing:control-plane" in standard.gate_issues
    assert "cosmetic-gap:monitor-coverage-missing:control-plane" in cosmetic.reports
    assert standard.blocking_issues == () and cosmetic.blocking_issues == ()


def test_a_generated_monitor_on_a_critical_surface_blocks_the_bundle(tmp_path: Path) -> None:
    _, artifacts = _ratified_run(tmp_path)

    report = EvidenceBundleAssembler(tmp_path).assemble(
        "run-1",
        candidate_digest=CANDIDATE,
        claims=(_claim(artifacts[0]),),
        checklist_journal=_journal(tmp_path),
        required_checklist_item_ids=("build", "tests"),
        surface_evidence=(_surface(),),
        determinism_records=(_determinism(),),
        **_records(
            artifacts,
            monitors=(_monitor(artifacts, authorship=MONITOR_AUTHORSHIP_GENERATED),),
        ),
    )

    assert (
        "critical-gap:critical-monitor-not-human-authored:monitor-control-plane:control-plane"
        in report.blocking_issues
    )


def test_monitor_authorship_resolves_against_the_signed_roster(tmp_path: Path) -> None:
    _, artifacts = _ratified_run(tmp_path)

    report = EvidenceBundleAssembler(tmp_path).assemble(
        "run-1",
        candidate_digest=CANDIDATE,
        claims=(_claim(artifacts[0]),),
        checklist_journal=_journal(tmp_path),
        required_checklist_item_ids=("build", "tests"),
        surface_evidence=(_surface(),),
        determinism_records=(_determinism(),),
        **_records(artifacts, monitors=(_monitor(artifacts, author="agent:validator"),)),
    )

    # An enrolled agent identity never resolves as the human author of a critical monitor.
    assert (
        "monitor-integrity:monitor-author-not-enrolled-human:monitor-control-plane"
        in report.blocking_issues
    )


def test_the_correction_lane_bundle_carries_its_controls_and_reproduction(
    tmp_path: Path,
) -> None:
    _, artifacts = _ratified_run(tmp_path)
    assembler = EvidenceBundleAssembler(tmp_path)
    journal = _journal(tmp_path)

    complete = assembler.assemble(
        "run-1",
        candidate_digest=CANDIDATE,
        claims=(_claim(artifacts[0]),),
        checklist_journal=journal,
        required_checklist_item_ids=("build", "tests"),
        surface_evidence=(_surface(),),
        determinism_records=(_determinism(),),
        **_records(artifacts, lane=LANE_CORRECTION, correction=_correction()),
    )
    without_record = assembler.assemble(
        "run-1",
        candidate_digest=CANDIDATE,
        claims=(_claim(artifacts[0]),),
        checklist_journal=journal,
        required_checklist_item_ids=("build", "tests"),
        surface_evidence=(_surface(),),
        determinism_records=(_determinism(),),
        **_records(artifacts, lane=LANE_CORRECTION),
    )

    assert complete.mechanically_satisfied is True
    assert complete.document["correction"]["defect_id"] == "defect-1"
    assert complete.correction is not None and complete.correction.satisfied is True
    assert (
        "critical-gap:correction-gap:correction-record-missing:control-plane"
        in without_record.blocking_issues
    )


def test_a_capability_bundle_reports_a_stray_correction_record(tmp_path: Path) -> None:
    _, artifacts = _ratified_run(tmp_path)

    report = EvidenceBundleAssembler(tmp_path).assemble(
        "run-1",
        candidate_digest=CANDIDATE,
        claims=(_claim(artifacts[0]),),
        checklist_journal=_journal(tmp_path),
        required_checklist_item_ids=("build", "tests"),
        surface_evidence=(_surface(),),
        determinism_records=(_determinism(),),
        **_records(artifacts, lane=LANE_CAPABILITY, correction=_correction()),
    )

    assert "correction-record-outside-correction-lane" in report.reports
    assert report.correction is None
    assert report.mechanically_satisfied is True
