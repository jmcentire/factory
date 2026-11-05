"""Builtin qualification is enforced at generation admission and frozen replay.

The ledger setup uses the existing explicit store-level intake/ratification fixtures;
these are not a replacement for real signing integration. Catalogs and qualification
observations come from an actually executed prepare-target invocation.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import factory_core
from factory_core.build_plan import (
    BuildPlan,
    BuildPlanBundle,
    BuildStep,
    OracleLink,
    PatternCatalog,
    verify_build_plan,
)
from factory_core.manifest import digest_obj
from factory_core.provenance import ProvenanceBundle
from factory_core.target import load_target_manifest
from factory_runtime.cli import main
from factory_runtime.generation import (
    GenerationError,
    GenerationPreparer,
    build_input_document,
    verify_prepared_generation,
)
from factory_runtime.snapshot import freeze_blob
from factory_runtime.state import RunProjection, RunState, RunStore
from tests.conftest import create_intake_run, ratification_receipts
from tests.test_generation_prepare_forcing import SOURCE, _Clock, _phase


@dataclasses.dataclass
class Inputs:
    root: Path
    store: RunStore
    target_path: Path
    catalog_path: Path
    plan_path: Path
    artifacts: tuple[Any, ...]


def _json_bytes(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


def _inputs(root: Path, capsys: pytest.CaptureFixture[str], falsification: str = "") -> Inputs:
    root.mkdir(parents=True, exist_ok=True)
    output = root / "target-inputs"
    assert (
        main(
            [
                "prepare-target",
                "--target-id",
                "forcing",
                "--repository-url",
                "https://example.invalid/acme/widget.git",
                "--ref",
                "main",
                "--output-dir",
                str(output),
                "--compliance-rules-path",
                "compliance/rules.json",
                "--max-attempts",
                "1",
            ]
        )
        == 0
    )
    capsys.readouterr()
    target_path = output / "target-manifest.toml"
    catalog_path = output / "pattern-catalog.json"
    catalog_document = json.loads(catalog_path.read_bytes())
    original_digest = PatternCatalog.from_dict(catalog_document).content_digest
    if falsification:
        report = catalog_document["patterns"][0]["mechanism"]["qualification_evidence"]
        if falsification == "false-probe":
            report["observations"][0]["passed"] = False
        elif falsification == "false-negative":
            negative = next(row for row in report["observations"] if "observed_exception" in row)
            negative["observed_exception"] = "NoException"
        elif falsification == "extra-field":
            report["unexecuted_attestation"] = "not emitted by qualification"
        else:
            raise AssertionError(f"Unknown falsification: {falsification}")
        catalog_document["patterns"][0]["qualification_evidence_digest"] = digest_obj(report)
        catalog_path.write_bytes(_json_bytes(catalog_document))
        new_digest = PatternCatalog.from_dict(catalog_document).content_digest
        target_path.write_text(target_path.read_text().replace(original_digest, new_digest))
    catalog = PatternCatalog.from_dict(catalog_document)
    target = load_target_manifest(target_path)
    runs = root / "runs"
    runs.mkdir()
    store = RunStore(runs, clock=_Clock())
    create_intake_run(
        store,
        run_id="run-1",
        target_digest=target.source_digest,
        source_digest=SOURCE,
    )
    phases = (
        ("product-specification", RunState.PRODUCT_SPECIFICATION_RATIFIED),
        ("architecture", RunState.ARCHITECTURE_RATIFIED),
        ("operational-maturity", RunState.OPERATIONAL_MATURITY_RATIFIED),
    )
    artifacts = tuple(_phase(phase, phase) for phase, _ in phases)
    for artifact, (_, state) in zip(artifacts, phases, strict=True):
        evidence = (
            runs
            / "run-1"
            / "evidence"
            / artifact.phase
            / artifact.content_digest.removeprefix("sha256:")
        )
        evidence.mkdir(parents=True)
        (evidence / "artifact.json").write_bytes(_json_bytes(artifact.body()))
        store.transition(
            "run-1",
            state,
            actor="validator",
            artifact_digests={
                artifact.phase: artifact.content_digest,
                **ratification_receipts(artifact.phase),
            },
        )
    product, architecture, operations = artifacts
    pattern = catalog.patterns[0]
    build_input = build_input_document("run-1", target.source_digest, artifacts)
    plan = BuildPlan(
        plan_id="builtin-forcing",
        version="1",
        run_id="run-1",
        target_digest=target.source_digest,
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
                configuration={"artifact_directory": "artifact"},
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
    plan_path.write_bytes(_json_bytes(plan.body()))
    return Inputs(root, store, target_path, catalog_path, plan_path, artifacts)


def _prepare(inputs: Inputs):
    return GenerationPreparer(inputs.store.root).prepare(
        "run-1",
        target_manifest_path=inputs.target_path,
        pattern_catalog_path=inputs.catalog_path,
        build_plan_path=inputs.plan_path,
    )


def _frozen_projection(inputs: Inputs) -> RunProjection:
    """Build a hash-consistent replay fixture without bypassing the replay consumer.

    The pure build-plan verifier establishes structural IR validity independently of
    builtin qualification. This lets a forged, rehashed qualification reach the actual
    frozen-generation semantic boundary instead of failing an unrelated hash check.
    """
    target = load_target_manifest(inputs.target_path)
    catalog = PatternCatalog.from_dict(json.loads(inputs.catalog_path.read_bytes()))
    plan = BuildPlan.from_dict(json.loads(inputs.plan_path.read_bytes()))
    build_input = build_input_document("run-1", target.source_digest, inputs.artifacts)
    report = verify_build_plan(
        BuildPlanBundle(
            catalog=catalog,
            trusted_catalog_digest=catalog.content_digest,
            plan=plan,
            provenance=ProvenanceBundle(
                artifacts=inputs.artifacts,
                claims=(),
                trusted_artifact_digests={
                    artifact.artifact_id: artifact.content_digest for artifact in inputs.artifacts
                },
            ),
            expected_run_id="run-1",
            expected_target_digest=target.source_digest,
            expected_build_input_digest=digest_obj(build_input),
        )
    )
    assert report.ready, report.issues
    run_root = inputs.store.root / "run-1"
    generation_root = run_root / "evidence" / "generation"

    def retain(label: str, data: bytes):
        return freeze_blob(
            generation_root,
            durable_through=run_root,
            label=label,
            data=data,
        )

    target_blob = retain("target-manifest", inputs.target_path.read_bytes())
    catalog_blob = retain("pattern-catalog", inputs.catalog_path.read_bytes())
    plan_blob = retain("build-plan", inputs.plan_path.read_bytes())
    input_blob = retain("build-input", _json_bytes(build_input))
    readiness = {
        "schema_version": "factory-generation-readiness/2",
        "run_id": "run-1",
        "attempt_number": 1,
        "attempt_limit": 1,
        "target_digest": target.source_digest,
        "pattern_catalog_digest": catalog.content_digest,
        "pattern_catalog_source_digest": catalog_blob.digest,
        "build_plan_digest": plan.content_digest,
        "build_plan_source_digest": plan_blob.digest,
        "build_input_digest": input_blob.digest,
        "report": report.to_dict(),
    }
    readiness_blob = retain("generation-readiness", _json_bytes(readiness))
    return dataclasses.replace(
        inputs.store.load("run-1"),
        build_attempt_count=1,
        build_attempt_limit=1,
        generation_artifact_digests={
            "target-manifest-source": target_blob.digest,
            "pattern-catalog": catalog.content_digest,
            "pattern-catalog-source": catalog_blob.digest,
            "build-plan": plan.content_digest,
            "build-plan-source": plan_blob.digest,
            "build-input": input_blob.digest,
            "generation-readiness": readiness_blob.digest,
        },
    )


def test_builtin_generation_preparation_and_frozen_replay_accept_current_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = _inputs(tmp_path / "input", capsys)
    prepared = _prepare(inputs)
    assert prepared.report.ready
    bound = dataclasses.replace(
        inputs.store.load("run-1"),
        build_attempt_count=1,
        build_attempt_limit=1,
        generation_artifact_digests=prepared.artifact_digests,
    )
    target, catalog, _, _ = verify_prepared_generation(inputs.store.root, bound)
    assert target.source_digest == prepared.target.source_digest
    assert catalog.content_digest == prepared.catalog.content_digest
    fixture_inputs = _inputs(tmp_path / "fixture-control", capsys)
    assert verify_prepared_generation(fixture_inputs.store.root, _frozen_projection(fixture_inputs))


@pytest.mark.parametrize("falsification", ["false-probe", "false-negative", "extra-field"])
def test_prepare_refuses_forged_builtin_qualification_before_building(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], falsification: str
) -> None:
    inputs = _inputs(tmp_path, capsys, falsification)
    with pytest.raises(GenerationError):
        _prepare(inputs)
    generation_root = inputs.store.root / "run-1" / "evidence" / "generation"
    assert not (generation_root / "pattern-catalog").exists()


@pytest.mark.parametrize("falsification", ["false-probe", "false-negative", "extra-field"])
def test_frozen_generation_replay_refuses_forged_rehashed_builtin_qualification(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], falsification: str
) -> None:
    inputs = _inputs(tmp_path, capsys, falsification)
    projection = _frozen_projection(inputs)
    with pytest.raises(GenerationError):
        verify_prepared_generation(inputs.store.root, projection)


_CHILD = r"""
import json, sys
from factory_runtime.generation import (
    GenerationError, GenerationPreparer, verify_prepared_generation,
)
from factory_runtime.state import RunProjection
root, target, catalog, plan, projection_file, action, drift = sys.argv[1:]
if drift == "python-runtime":
    sys.version += " external-runtime-drift"
try:
    if action == "prepare":
        prepared = GenerationPreparer(root).prepare(
            "run-1", target_manifest_path=target, pattern_catalog_path=catalog, build_plan_path=plan
        )
        assert prepared.report.ready
    else:
        projection = RunProjection.from_dict(json.load(open(projection_file)))
        verify_prepared_generation(root, projection)
except GenerationError as exc:
    print(json.dumps({"refused": True, "reason": str(exc)}))
else:
    print(json.dumps({"refused": False}))
"""


def _assert_drift_refusal(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    action: str,
    drift: str,
) -> None:
    inputs = _inputs(tmp_path / "input", capsys)
    projection = _frozen_projection(inputs)
    projection_path = tmp_path / "projection.json"
    projection_path.write_bytes(_json_bytes(projection.to_dict()))
    repository = Path(factory_core.__file__).resolve().parents[1]
    isolated = tmp_path / "isolated-factory"
    isolated.mkdir()
    for package in ("factory_core", "factory_runtime"):
        shutil.copytree(
            repository / package,
            isolated / package,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    arguments = [
        sys.executable,
        "-c",
        _CHILD,
        str(inputs.store.root),
        str(inputs.target_path),
        str(inputs.catalog_path),
        str(inputs.plan_path),
        str(projection_path),
        action,
    ]
    environment = {
        **os.environ,
        "PYTHONPATH": str(isolated),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    control = subprocess.run(
        [*arguments, "none"],
        cwd=isolated,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert control.returncode == 0, control.stderr
    assert json.loads(control.stdout)["refused"] is False
    if drift == "construction-source":
        source = isolated / "factory_runtime" / "candidate_diff.py"
        source.write_bytes(source.read_bytes() + b"\n# externally altered source\n")
    refused = subprocess.run(
        [*arguments, drift],
        cwd=isolated,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert refused.returncode == 0, refused.stderr
    assert json.loads(refused.stdout)["refused"] is True


@pytest.mark.parametrize("drift", ["python-runtime", "construction-source"])
def test_generation_prepare_refuses_stale_builtin_qualification(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    drift: str,
) -> None:
    _assert_drift_refusal(tmp_path, capsys, action="prepare", drift=drift)


@pytest.mark.parametrize("drift", ["python-runtime", "construction-source"])
def test_frozen_generation_replay_refuses_stale_builtin_qualification(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    drift: str,
) -> None:
    _assert_drift_refusal(tmp_path, capsys, action="replay", drift=drift)
