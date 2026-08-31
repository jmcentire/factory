"""Fail-closed generation readiness over target ABI, phase authority, and build IR."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from factory_core.build_plan import (
    BuildPlan,
    BuildPlanBundle,
    BuildPlanReport,
    PatternCatalog,
    verify_build_plan,
)
from factory_core.manifest import digest_bytes, digest_obj
from factory_core.provenance import PhaseArtifact, ProvenanceBundle
from factory_core.target import TargetManifest, TargetManifestError, load_target_manifest
from factory_runtime.schema import DocumentValidationError, validate_document
from factory_runtime.snapshot import FrozenBlob, freeze_blob, verify_frozen_blob
from factory_runtime.state import GENERATION_ARTIFACT_KEYS, RunProjection, RunState, RunStore


class GenerationError(ValueError):
    """Generation could not start from a complete, current, immutable authority tuple."""


@dataclass(frozen=True)
class PreparedGeneration:
    target: TargetManifest
    catalog: PatternCatalog
    plan: BuildPlan
    report: BuildPlanReport
    build_input: Mapping[str, Any]
    build_input_path: Path
    pattern_catalog_path: Path
    build_plan_path: Path
    artifact_digests: Mapping[str, str]
    attempt_number: int


def _canonical_bytes(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_input_document(
    run_id: str,
    target_digest: str,
    artifacts: tuple[PhaseArtifact, ...],
) -> dict[str, Any]:
    """Compile exact ratified phase bytes into the only document shared by both author lanes."""

    return {
        "schema_version": "factory-build-input/1",
        "run_id": run_id,
        "target_digest": target_digest,
        "phase_artifacts": [artifact.body() for artifact in artifacts],
    }


def _read_object(path: str | Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    source = Path(path)
    try:
        data = source.read_bytes()
        raw = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenerationError(f"{label} is unreadable: {source}: {exc}") from exc
    if not isinstance(raw, dict):
        raise GenerationError(f"{label} must be a JSON object: {source}")
    return raw, data


def _load_target_exact(
    path: str | Path, *, expected_source_digest: str = ""
) -> tuple[TargetManifest, bytes]:
    """Load one stable target byte string and prove the parser consumed those exact bytes."""

    source = Path(path)
    try:
        before = source.read_bytes()
        target = load_target_manifest(source)
        after = source.read_bytes()
    except (OSError, TargetManifestError) as exc:
        raise GenerationError(f"target manifest is unreadable: {source}: {exc}") from exc
    source_digest = digest_bytes(before)
    if before != after or target.source_digest != source_digest:
        raise GenerationError(f"target manifest changed while it was being loaded: {source}")
    if expected_source_digest and source_digest != expected_source_digest:
        raise GenerationError("consumed target manifest bytes do not match the frozen address")
    return target, before


def _validate_object(name: str, raw: Mapping[str, Any]) -> None:
    try:
        validate_document(name, raw)
    except DocumentValidationError as exc:
        raise GenerationError(str(exc)) from exc


def _phase_artifacts(
    runs_root: Path,
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
            raise GenerationError(f"run has no ratified {phase} artifact")
        path = (
            runs_root
            / run_id
            / "evidence"
            / phase
            / digest.removeprefix("sha256:")
            / "artifact.json"
        )
        raw, _ = _read_object(path, label=f"ratified {phase} artifact")
        _validate_object("phase-artifact", raw)
        artifact = PhaseArtifact.from_dict(raw)
        if artifact.phase != phase or artifact.content_digest != digest:
            raise GenerationError(f"ratified phase artifact does not match the run ledger: {phase}")
        artifacts.append(artifact)
    return tuple(artifacts)


def _signal_knob_issues(
    build: Mapping[str, object],
    frozen_attempt_limit: int | None,
) -> tuple[str, ...]:
    """Validate the target ABI's signal-deadline knobs (remediation plan §0.4a).

    The three knobs live inside the target manifest, so they are frozen into the
    generation tuple transitively: any knob edit changes the manifest digest and
    fires target-manifest-run-digest-mismatch. The named raised-after-start issue
    here additionally catches a re-signed ABI whose deadline exceeds the attempt
    ceiling frozen at the first attempt — a mid-run re-sign that only raises the
    deadline must fail the comparison and disarm nothing. The finer per-axis
    comparison (each knob its own named axis) lands with Phase 3's replacement of
    whole-tuple equality. Declaration is mandatory at readiness — configurable
    never means disable-able — while schema-level requirement waits for the
    factory-target-manifest/2 bump (refuse-at-parse is Phase 2.1's earliest
    firing point).
    """

    signal = build.get("signal")
    if not isinstance(signal, Mapping):
        return ("signal-knobs-undeclared",)
    deadline = signal.get("signal_pass_deadline")
    warn = signal.get("signal_pass_warn")
    cap = signal.get("signal_wall_clock_cap_hours")
    if not (
        isinstance(deadline, int)
        and not isinstance(deadline, bool)
        and deadline >= 1
        and isinstance(warn, int)
        and not isinstance(warn, bool)
        and warn >= 1
        and isinstance(cap, (int, float))
        and not isinstance(cap, bool)
        and cap > 0
    ):
        return ("signal-knobs-invalid",)
    issues: list[str] = []
    max_attempts = build.get("max_attempts")
    if (
        isinstance(max_attempts, int)
        and not isinstance(max_attempts, bool)
        and deadline > max_attempts
    ):
        issues.append("signal-pass-deadline-exceeds-max-attempts")
    if warn > deadline:
        issues.append("signal-warn-exceeds-deadline")
    if frozen_attempt_limit and deadline > frozen_attempt_limit:
        issues.append("deadline-knob-raised-after-start")
    return tuple(issues)


class GenerationPreparer:
    """Compile and freeze one per-attempt input tuple before any author lane starts."""

    def __init__(self, runs_root: str | Path) -> None:
        self.runs_root = Path(runs_root)
        self.store = RunStore(self.runs_root)

    def prepare(
        self,
        run_id: str,
        *,
        target_manifest_path: str | Path,
        pattern_catalog_path: str | Path,
        build_plan_path: str | Path,
    ) -> PreparedGeneration:
        projection = self.store.load(run_id)
        if projection.state not in {
            RunState.OPERATIONAL_MATURITY_RATIFIED,
            RunState.BLOCKED,
        }:
            raise GenerationError(
                "generation readiness requires ratified invariant documents or a blocked attempt"
            )

        target, target_bytes = _load_target_exact(target_manifest_path)
        catalog_raw, catalog_bytes = _read_object(
            pattern_catalog_path,
            label="pattern catalog",
        )
        plan_raw, plan_bytes = _read_object(build_plan_path, label="build plan")
        _validate_object("pattern-catalog", catalog_raw)
        _validate_object("build-plan", plan_raw)
        catalog = PatternCatalog.from_dict(catalog_raw)
        plan = BuildPlan.from_dict(plan_raw)
        artifacts = _phase_artifacts(
            self.runs_root,
            run_id,
            projection.phase_artifact_digests,
        )
        build_input = build_input_document(run_id, projection.target_digest, artifacts)
        build_input_bytes = _canonical_bytes(build_input)
        build_input_digest = digest_obj(build_input)
        provenance = ProvenanceBundle(
            artifacts=artifacts,
            claims=(),
            trusted_artifact_digests={
                artifact.artifact_id: artifact.content_digest for artifact in artifacts
            },
        )
        report = verify_build_plan(
            BuildPlanBundle(
                catalog=catalog,
                trusted_catalog_digest=str(target.build.get("pattern_catalog_digest", "")),
                plan=plan,
                provenance=provenance,
                expected_run_id=run_id,
                expected_target_digest=projection.target_digest,
                expected_build_input_digest=build_input_digest,
            )
        )

        issues = list(report.issues)
        if target.content_digest != projection.target_digest:
            issues.append("target-manifest-run-digest-mismatch")
        target_attempt_limit = target.build.get("max_attempts")
        if (
            isinstance(target_attempt_limit, bool)
            or not isinstance(target_attempt_limit, int)
            or plan.max_build_attempts > target_attempt_limit
        ):
            issues.append("build-plan-attempt-limit-exceeds-target-abi")
        modes = target.build.get("construction_modes")
        if not isinstance(modes, list) or plan.construction_mode not in modes:
            issues.append("build-plan-construction-mode-not-authorized-by-target")
        attempt_number = projection.build_attempt_count + 1
        if attempt_number > plan.max_build_attempts:
            issues.append("build-plan-attempt-limit-exhausted")
        if (
            projection.build_attempt_limit
            and plan.max_build_attempts > projection.build_attempt_limit
        ):
            issues.append("build-plan-attempt-limit-raised-after-start")
        issues.extend(_signal_knob_issues(target.build, projection.build_attempt_limit))
        unique_issues = tuple(dict.fromkeys(issues))
        report = replace(report, ready=not unique_issues, issues=unique_issues)

        run_root = self.runs_root / run_id
        generation_root = run_root / "evidence" / "generation"
        target_snapshot = freeze_blob(
            generation_root,
            durable_through=run_root,
            label="target-manifest",
            data=target_bytes,
        )
        catalog_snapshot = freeze_blob(
            generation_root,
            durable_through=run_root,
            label="pattern-catalog",
            data=catalog_bytes,
        )
        plan_snapshot = freeze_blob(
            generation_root,
            durable_through=run_root,
            label="build-plan",
            data=plan_bytes,
        )
        input_snapshot = freeze_blob(
            generation_root,
            durable_through=run_root,
            label="build-input",
            data=build_input_bytes,
        )
        readiness_document = {
            "schema_version": "factory-generation-readiness/1",
            "run_id": run_id,
            "attempt_number": attempt_number,
            "attempt_limit": plan.max_build_attempts,
            "target_digest": target.content_digest,
            "target_manifest_source_digest": target_snapshot.digest,
            "pattern_catalog_digest": catalog.content_digest,
            "pattern_catalog_source_digest": catalog_snapshot.digest,
            "build_plan_digest": plan.content_digest,
            "build_plan_source_digest": plan_snapshot.digest,
            "build_input_digest": input_snapshot.digest,
            "report": report.to_dict(),
        }
        readiness_snapshot = freeze_blob(
            generation_root,
            durable_through=run_root,
            label="generation-readiness",
            data=_canonical_bytes(readiness_document),
        )
        artifact_digests = {
            "target-manifest-source": target_snapshot.digest,
            "pattern-catalog": catalog.content_digest,
            "pattern-catalog-source": catalog_snapshot.digest,
            "build-plan": plan.content_digest,
            "build-plan-source": plan_snapshot.digest,
            "build-input": input_snapshot.digest,
            "generation-readiness": readiness_snapshot.digest,
        }
        if set(artifact_digests) != set(GENERATION_ARTIFACT_KEYS):
            raise GenerationError("internal generation artifact tuple is incomplete")
        if input_snapshot.digest != build_input_digest:
            raise GenerationError("canonical build input address is inconsistent")
        if not report.ready:
            raise GenerationError("generation readiness refused: " + ", ".join(report.issues))
        return PreparedGeneration(
            target=target,
            catalog=catalog,
            plan=plan,
            report=report,
            build_input=build_input,
            build_input_path=input_snapshot.payload_path,
            pattern_catalog_path=catalog_snapshot.payload_path,
            build_plan_path=plan_snapshot.payload_path,
            artifact_digests=artifact_digests,
            attempt_number=attempt_number,
        )


def _generation_blob(
    runs_root: Path,
    run_id: str,
    label: str,
    digest: str,
) -> FrozenBlob:
    directory = (
        runs_root / run_id / "evidence" / "generation" / label / digest.removeprefix("sha256:")
    )
    return verify_frozen_blob(directory, expected_digest=digest, label=label)


def verify_prepared_generation(
    runs_root: str | Path,
    projection: RunProjection,
) -> tuple[TargetManifest, PatternCatalog, BuildPlan, Mapping[str, Any]]:
    """Re-derive all frozen generation artifacts before evidence is allowed to cite them."""

    generation = dict(projection.generation_artifact_digests)
    if set(generation) != set(GENERATION_ARTIFACT_KEYS):
        raise GenerationError("run projection has an incomplete generation artifact tuple")
    root = Path(runs_root)
    target_blob = _generation_blob(
        root,
        projection.run_id,
        "target-manifest",
        generation["target-manifest-source"],
    )
    catalog_blob = _generation_blob(
        root,
        projection.run_id,
        "pattern-catalog",
        generation["pattern-catalog-source"],
    )
    plan_blob = _generation_blob(
        root,
        projection.run_id,
        "build-plan",
        generation["build-plan-source"],
    )
    input_blob = _generation_blob(
        root,
        projection.run_id,
        "build-input",
        generation["build-input"],
    )
    readiness_blob = _generation_blob(
        root,
        projection.run_id,
        "generation-readiness",
        generation["generation-readiness"],
    )
    target, _ = _load_target_exact(
        target_blob.payload_path,
        expected_source_digest=target_blob.digest,
    )
    if target.content_digest != projection.target_digest:
        raise GenerationError("frozen target manifest does not match the run target")
    catalog_raw, catalog_bytes = _read_object(
        catalog_blob.payload_path,
        label="frozen pattern catalog",
    )
    plan_raw, plan_bytes = _read_object(plan_blob.payload_path, label="frozen build plan")
    input_raw, input_bytes = _read_object(input_blob.payload_path, label="frozen build input")
    readiness_raw, readiness_bytes = _read_object(
        readiness_blob.payload_path,
        label="frozen generation readiness",
    )
    for label, data, blob in (
        ("pattern catalog", catalog_bytes, catalog_blob),
        ("build plan", plan_bytes, plan_blob),
        ("build input", input_bytes, input_blob),
        ("generation readiness", readiness_bytes, readiness_blob),
    ):
        if digest_bytes(data) != blob.digest:
            raise GenerationError(f"consumed frozen {label} bytes do not match their address")
    _validate_object("pattern-catalog", catalog_raw)
    _validate_object("build-plan", plan_raw)
    catalog = PatternCatalog.from_dict(catalog_raw)
    plan = BuildPlan.from_dict(plan_raw)
    if catalog.content_digest != generation["pattern-catalog"]:
        raise GenerationError("frozen pattern catalog canonical address mismatch")
    if plan.content_digest != generation["build-plan"]:
        raise GenerationError("frozen build plan canonical address mismatch")
    if digest_bytes(_canonical_bytes(input_raw)) != generation["build-input"]:
        raise GenerationError("frozen build input is not canonically encoded")
    artifacts = _phase_artifacts(
        root,
        projection.run_id,
        projection.phase_artifact_digests,
    )
    expected_input = build_input_document(
        projection.run_id,
        projection.target_digest,
        artifacts,
    )
    if input_raw != expected_input:
        raise GenerationError("frozen build input does not equal current phase authority")
    provenance = ProvenanceBundle(
        artifacts=artifacts,
        claims=(),
        trusted_artifact_digests={
            artifact.artifact_id: artifact.content_digest for artifact in artifacts
        },
    )
    report = verify_build_plan(
        BuildPlanBundle(
            catalog=catalog,
            trusted_catalog_digest=str(target.build.get("pattern_catalog_digest", "")),
            plan=plan,
            provenance=provenance,
            expected_run_id=projection.run_id,
            expected_target_digest=projection.target_digest,
            expected_build_input_digest=generation["build-input"],
        )
    )
    if not report.ready:
        raise GenerationError("frozen build plan no longer verifies: " + ", ".join(report.issues))
    target_attempt_limit = target.build.get("max_attempts")
    modes = target.build.get("construction_modes")
    if (
        isinstance(target_attempt_limit, bool)
        or not isinstance(target_attempt_limit, int)
        or plan.max_build_attempts > target_attempt_limit
        or not isinstance(modes, list)
        or plan.construction_mode not in modes
    ):
        raise GenerationError("frozen build plan exceeds the target operational ABI")
    readiness_report = readiness_raw.get("report")
    if not isinstance(readiness_report, Mapping) or readiness_report.get("ready") is not True:
        raise GenerationError("frozen generation readiness report is not affirmative")
    if readiness_report != report.to_dict():
        raise GenerationError("frozen readiness report does not match re-derived verification")
    if (
        readiness_raw.get("attempt_number") != projection.build_attempt_count
        or readiness_raw.get("attempt_limit") != projection.build_attempt_limit
    ):
        raise GenerationError("frozen readiness attempt bound does not match the run ledger")
    cited = {
        "target-manifest-source": readiness_raw.get("target_manifest_source_digest"),
        "pattern-catalog": readiness_raw.get("pattern_catalog_digest"),
        "pattern-catalog-source": readiness_raw.get("pattern_catalog_source_digest"),
        "build-plan": readiness_raw.get("build_plan_digest"),
        "build-plan-source": readiness_raw.get("build_plan_source_digest"),
        "build-input": readiness_raw.get("build_input_digest"),
        "generation-readiness": readiness_blob.digest,
    }
    if cited != generation:
        raise GenerationError("generation readiness report does not bind the ledger tuple")
    return target, catalog, plan, input_raw
