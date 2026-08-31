"""prepare()-level forcing tests (verification round-4 D1/D2).

Round 4 proved the knob enforcement was one deletable line: every knob test
imported the private validator directly, so unwiring ``issues.extend(...)``
from ``prepare()`` left the whole suite green, and no test had ever fired
``target-manifest-run-digest-mismatch``. These tests drive ``prepare()``
itself — the refuters verified this shape is green at HEAD and red under the
unwiring mutation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory_core.build_plan import (
    BuildPlan,
    BuildStep,
    OracleLink,
    PatternCatalog,
    PatternDefinition,
)
from factory_core.manifest import digest_obj
from factory_core.provenance import (
    PHASE_ARCHITECTURE,
    PHASE_OPERATIONAL_MATURITY,
    PHASE_PRODUCT_SPECIFICATION,
    IntentItem,
    PhaseArtifact,
)
from factory_core.target import load_target_manifest
from factory_runtime.generation import (
    GenerationError,
    GenerationPreparer,
    build_input_document,
)
from factory_runtime.state import RunState, RunStore
from tests.conftest import create_intake_run, ratification_receipts

SOURCE = "sha256:" + "5" * 64


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


def _target_toml(catalog_digest: str, *, with_signal: bool) -> str:
    lines = [
        'schema_version = "factory-target-manifest/1"',
        'target_id = "synthetic-prepare-forcing"',
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
        f'pattern_catalog_digest = "{catalog_digest}"',
        "max_attempts = 1",
        'construction_modes = ["regenerate"]',
    ]
    if with_signal:
        lines += [
            "",
            "[build.signal]",
            "signal_pass_deadline = 1",
            "signal_pass_warn = 1",
            "signal_wall_clock_cap_hours = 24",
        ]
    return "\n".join(lines) + "\n"


class _Clock:
    def __init__(self) -> None:
        self.value = 100

    def __call__(self) -> int:
        self.value += 1
        return self.value


def _prepared_inputs(root: Path, *, with_signal: bool) -> tuple[RunStore, Path, Path, Path]:
    store = RunStore(root, clock=_Clock())
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
        _target_toml(catalog.content_digest, with_signal=with_signal), encoding="utf-8"
    )
    target = load_target_manifest(target_path)
    create_intake_run(
        store,
        run_id="run-1",
        target_digest=target.content_digest,
        source_digest=SOURCE,
        target_manifest_source_digest=target.source_digest,
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
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "artifact.json").write_text(
            json.dumps(artifact.body(), sort_keys=True), encoding="utf-8"
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
        phase_artifact_digests={a.phase: a.content_digest for a in artifacts},
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
    return store, target_path, catalog_path, plan_path


def _prepare(root: Path, target_path: Path, catalog_path: Path, plan_path: Path):
    return GenerationPreparer(root).prepare(
        "run-1",
        target_manifest_path=target_path,
        pattern_catalog_path=catalog_path,
        build_plan_path=plan_path,
    )


def test_prepare_accepts_a_declared_signal_target(tmp_path: Path) -> None:
    store, target_path, catalog_path, plan_path = _prepared_inputs(
        tmp_path, with_signal=True
    )
    prepared = _prepare(tmp_path, target_path, catalog_path, plan_path)
    assert prepared.report.ready


def test_prepare_refuses_a_knobless_target(tmp_path: Path) -> None:
    """Round-4 D1: 'mandatory at readiness' must be a prepare()-level refusal,
    not a deletable extend call — this test is red under the unwiring mutation."""
    store, target_path, catalog_path, plan_path = _prepared_inputs(
        tmp_path, with_signal=False
    )
    with pytest.raises(GenerationError) as excinfo:
        _prepare(tmp_path, target_path, catalog_path, plan_path)
    assert "signal-knobs-undeclared" in str(excinfo.value)


def test_prepare_refuses_a_resigned_manifest(tmp_path: Path) -> None:
    """Round-4 D2: the freeze headline shown to fire — an edited/re-signed
    manifest (deadline raised, digest changed) refuses at re-prepare with
    target-manifest-run-digest-mismatch."""
    store, target_path, catalog_path, plan_path = _prepared_inputs(
        tmp_path, with_signal=True
    )
    assert _prepare(tmp_path, target_path, catalog_path, plan_path).report.ready
    edited = target_path.read_text(encoding="utf-8").replace(
        "signal_pass_deadline = 1", "signal_pass_deadline = 2"
    )
    target_path.write_text(edited, encoding="utf-8")
    with pytest.raises(GenerationError) as excinfo:
        _prepare(tmp_path, target_path, catalog_path, plan_path)
    assert "target-manifest-run-digest-mismatch" in str(excinfo.value)
