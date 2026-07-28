from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory_core.manifest import digest_obj
from factory_core.provenance import (
    PHASE_ARCHITECTURE,
    PHASE_OPERATIONAL_MATURITY,
    PHASE_PRODUCT_SPECIFICATION,
    IntentItem,
    PhaseArtifact,
    ProvenanceClaim,
)
from factory_runtime.evidence_plane import (
    ChecklistJournal,
    DeterminismRecord,
    EvidenceBundleAssembler,
    EvidencePlaneError,
    SurfaceEvidence,
)
from factory_runtime.state import RunState, RunStore

TARGET = "sha256:" + ("1" * 64)
SOURCE = "sha256:" + ("2" * 64)
CANDIDATE = "sha256:" + ("3" * 64)
TESTS = "sha256:" + ("4" * 64)


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
    store.create("run-1", target_digest=TARGET, source_digest=SOURCE, actor="validator")
    artifacts = (
        _phase(PHASE_PRODUCT_SPECIFICATION, "product"),
        _phase(PHASE_ARCHITECTURE, "architecture"),
        _phase(PHASE_OPERATIONAL_MATURITY, "operations"),
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
            artifact_digests={artifact.phase: artifact.content_digest},
        )
    store.transition("run-1", RunState.BUILDING, actor="validator")
    store.transition(
        "run-1",
        RunState.VALIDATING,
        actor="validator",
        artifact_digests={
            "candidate": CANDIDATE,
            "acceptance-tests": TESTS,
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
        surface_evidence=(
            _surface(criticality="standard", adequate=False, evidence=False),
        ),
        determinism_records=(_determinism(criticality="standard"),),
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
        )
