"""Authority-bound implementation IR compiled from the three phase artifacts.

Product, architecture, and testing/monitoring artifacts remain the only sources of intent.
Patterns are pre-qualified construction mechanisms and a build plan is disposable per-run IR.
The verifier makes stale bindings, omissions, and invented dependencies fail before generation;
it does not pretend hashes can judge semantic sufficiency.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from factory_core.manifest import digest_obj, verify_digest
from factory_core.provenance import (
    CLAIM_TASK,
    CLAIM_TEST_ASSERTION,
    PHASE_ARCHITECTURE,
    PHASE_OPERATIONAL_MATURITY,
    PHASE_PRODUCT_SPECIFICATION,
    REQUIRED_PHASES,
    IntentBackreference,
    PhaseArtifact,
    ProvenanceBundle,
    ProvenanceClaim,
    verify_intent_provenance,
)

CONSTRUCTION_REGENERATE = "regenerate"
CONSTRUCTION_BROWNFIELD = "brownfield"
CONSTRUCTION_MODES = (CONSTRUCTION_REGENERATE, CONSTRUCTION_BROWNFIELD)
PATTERN_CATALOG_SCHEMA_VERSION = "factory-pattern-catalog/1"
BUILD_PLAN_SCHEMA_VERSION = "factory-build-plan/1"

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _is_digest(value: str) -> bool:
    return bool(_DIGEST.fullmatch(value))


def _mapping_items(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value)


def _freeze_json(value: Any) -> Any:
    """Copy JSON-shaped data into immutable containers so content addresses stay stable."""

    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    """Return ordinary JSON containers for schemas and canonical serialization."""

    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True)
class PatternDefinition:
    """One reusable mechanism whose qualification and implementation are content-addressed."""

    pattern_id: str
    version: str
    artifact_digest: str
    qualification_evidence_digest: str
    mechanism: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "mechanism", _freeze_json(self.mechanism))

    def body(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "version": self.version,
            "artifact_digest": self.artifact_digest,
            "qualification_evidence_digest": self.qualification_evidence_digest,
            "mechanism": _thaw_json(self.mechanism),
        }

    @property
    def content_digest(self) -> str:
        return digest_obj(self.body())

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> PatternDefinition:
        mechanism = raw.get("mechanism")
        return cls(
            pattern_id=str(raw.get("pattern_id", "")),
            version=str(raw.get("version", "")),
            artifact_digest=str(raw.get("artifact_digest", "")),
            qualification_evidence_digest=str(raw.get("qualification_evidence_digest", "")),
            mechanism=(dict(mechanism) if isinstance(mechanism, Mapping) else {}),
        )


@dataclass(frozen=True)
class PatternCatalog:
    """A versioned construction catalog whose trust digest comes from target data."""

    catalog_id: str
    version: str
    patterns: tuple[PatternDefinition, ...] = ()

    def body(self) -> dict[str, Any]:
        return {
            "schema_version": PATTERN_CATALOG_SCHEMA_VERSION,
            "catalog_id": self.catalog_id,
            "version": self.version,
            "patterns": [
                item.body() for item in sorted(self.patterns, key=lambda row: row.pattern_id)
            ],
        }

    @property
    def content_digest(self) -> str:
        return digest_obj(self.body())

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> PatternCatalog:
        return cls(
            catalog_id=str(raw.get("catalog_id", "")),
            version=str(raw.get("version", "")),
            patterns=tuple(
                PatternDefinition.from_dict(item) for item in _mapping_items(raw.get("patterns"))
            ),
        )


@dataclass(frozen=True)
class BuildStep:
    """One instantiated recipe carrying configuration and authority backreferences.

    ``configuration`` supplies the per-run values a qualified pattern needs. It is derived IR,
    not a new statement of intent: every step still points at the exact phase items that
    authorize it, and the plan is discarded whenever any of those artifacts changes.
    """

    step_id: str
    pattern_id: str
    pattern_digest: str
    configuration: Mapping[str, Any] = field(default_factory=dict)
    intent_backreferences: tuple[IntentBackreference, ...] = ()
    depends_on: tuple[str, ...] = ()
    # Phase 1.5 additive joins, emitted only when declared so retained plan bytes
    # re-derive unchanged: the ratified verbs this step delivers toward __DONE__, and
    # the characterization probes this step promises toward adequacy criteria.
    delivers_verbs: tuple[str, ...] = ()
    promises_probes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "configuration", _freeze_json(self.configuration))

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "pattern_id": self.pattern_id,
            "pattern_digest": self.pattern_digest,
            "configuration": _thaw_json(self.configuration),
            "intent_backreferences": [item.to_dict() for item in self.intent_backreferences],
            "depends_on": list(self.depends_on),
            **({"delivers_verbs": sorted(self.delivers_verbs)} if self.delivers_verbs else {}),
            **(
                {"promises_probes": sorted(self.promises_probes)}
                if self.promises_probes
                else {}
            ),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> BuildStep:
        return cls(
            step_id=str(raw.get("step_id", "")),
            pattern_id=str(raw.get("pattern_id", "")),
            pattern_digest=str(raw.get("pattern_digest", "")),
            configuration=(
                dict(raw["configuration"]) if isinstance(raw.get("configuration"), Mapping) else {}
            ),
            intent_backreferences=tuple(
                IntentBackreference.from_dict(item)
                for item in _mapping_items(raw.get("intent_backreferences"))
            ),
            depends_on=_strings(raw.get("depends_on")),
            delivers_verbs=_strings(raw.get("delivers_verbs")),
            promises_probes=_strings(raw.get("promises_probes")),
        )


@dataclass(frozen=True)
class OracleLink:
    """Derived trace from a product/architecture expectation to an operational oracle."""

    expectation: IntentBackreference
    oracle: IntentBackreference

    def to_dict(self) -> dict[str, Any]:
        return {
            "expectation": self.expectation.to_dict(),
            "oracle": self.oracle.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> OracleLink:
        expectation = raw.get("expectation")
        oracle = raw.get("oracle")
        return cls(
            expectation=IntentBackreference.from_dict(
                expectation if isinstance(expectation, Mapping) else {}
            ),
            oracle=IntentBackreference.from_dict(oracle if isinstance(oracle, Mapping) else {}),
        )


@dataclass(frozen=True)
class BuildPlan:
    """Disposable implementation IR bound to one run, target, input, and authority version."""

    plan_id: str
    version: str
    run_id: str
    target_digest: str
    construction_mode: str
    max_build_attempts: int
    build_input_digest: str
    pattern_catalog_digest: str
    phase_artifact_digests: Mapping[str, str] = field(default_factory=dict)
    steps: tuple[BuildStep, ...] = ()
    oracle_links: tuple[OracleLink, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "phase_artifact_digests",
            MappingProxyType(dict(self.phase_artifact_digests)),
        )

    def body(self) -> dict[str, Any]:
        return {
            "schema_version": BUILD_PLAN_SCHEMA_VERSION,
            "plan_id": self.plan_id,
            "version": self.version,
            "run_id": self.run_id,
            "target_digest": self.target_digest,
            "construction_mode": self.construction_mode,
            "max_build_attempts": self.max_build_attempts,
            "build_input_digest": self.build_input_digest,
            "pattern_catalog_digest": self.pattern_catalog_digest,
            "phase_artifact_digests": dict(sorted(self.phase_artifact_digests.items())),
            "steps": [item.to_dict() for item in sorted(self.steps, key=lambda row: row.step_id)],
            "oracle_links": [
                item.to_dict()
                for item in sorted(
                    self.oracle_links,
                    key=lambda row: (
                        row.expectation.artifact_id,
                        row.expectation.item_id,
                        row.oracle.artifact_id,
                        row.oracle.item_id,
                    ),
                )
            ],
        }

    @property
    def content_digest(self) -> str:
        return digest_obj(self.body())

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> BuildPlan:
        phases = raw.get("phase_artifact_digests")
        attempt_limit = raw.get("max_build_attempts")
        return cls(
            plan_id=str(raw.get("plan_id", "")),
            version=str(raw.get("version", "")),
            run_id=str(raw.get("run_id", "")),
            target_digest=str(raw.get("target_digest", "")),
            construction_mode=str(raw.get("construction_mode", "")),
            max_build_attempts=(
                attempt_limit
                if isinstance(attempt_limit, int) and not isinstance(attempt_limit, bool)
                else 0
            ),
            build_input_digest=str(raw.get("build_input_digest", "")),
            pattern_catalog_digest=str(raw.get("pattern_catalog_digest", "")),
            phase_artifact_digests=(
                {str(key): str(value) for key, value in phases.items()}
                if isinstance(phases, Mapping)
                else {}
            ),
            steps=tuple(BuildStep.from_dict(item) for item in _mapping_items(raw.get("steps"))),
            oracle_links=tuple(
                OracleLink.from_dict(item) for item in _mapping_items(raw.get("oracle_links"))
            ),
        )


@dataclass(frozen=True)
class BuildPlanBundle:
    catalog: PatternCatalog
    trusted_catalog_digest: str
    plan: BuildPlan
    provenance: ProvenanceBundle
    expected_run_id: str
    expected_target_digest: str
    expected_build_input_digest: str


@dataclass(frozen=True)
class BuildPlanReport:
    ready: bool
    catalog_digest: str
    plan_digest: str
    phase_artifact_digests: Mapping[str, str]
    verified_step_ids: tuple[str, ...]
    covered_expectation_count: int
    verified_oracle_link_count: int
    issues: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "catalog_digest": self.catalog_digest,
            "plan_digest": self.plan_digest,
            "phase_artifact_digests": dict(sorted(self.phase_artifact_digests.items())),
            "verified_step_ids": list(self.verified_step_ids),
            "covered_expectation_count": self.covered_expectation_count,
            "verified_oracle_link_count": self.verified_oracle_link_count,
            "issues": list(self.issues),
        }


def _cycle_nodes(steps: Sequence[BuildStep], known: set[str]) -> tuple[str, ...]:
    dependencies = {
        step.step_id: tuple(item for item in step.depends_on if item in known)
        for step in steps
        if step.step_id in known
    }
    visiting: set[str] = set()
    visited: set[str] = set()
    cycles: set[str] = set()

    def visit(step_id: str) -> None:
        if step_id in visited:
            return
        if step_id in visiting:
            cycles.add(step_id)
            return
        visiting.add(step_id)
        for dependency in dependencies.get(step_id, ()):
            if dependency in visiting:
                cycles.update((step_id, dependency))
            else:
                visit(dependency)
        visiting.remove(step_id)
        visited.add(step_id)

    for step_id in sorted(dependencies):
        visit(step_id)
    return tuple(sorted(cycles))


def _claims(plan: BuildPlan) -> tuple[ProvenanceClaim, ...]:
    claims: list[ProvenanceClaim] = []
    for step_index, step in enumerate(plan.steps):
        claims.extend(
            ProvenanceClaim(f"build-step:{step_index}:{index}", CLAIM_TASK, reference)
            for index, reference in enumerate(step.intent_backreferences)
        )
    for index, link in enumerate(plan.oracle_links):
        claims.extend(
            (
                ProvenanceClaim(f"oracle-expectation:{index}", CLAIM_TASK, link.expectation),
                ProvenanceClaim(f"oracle-evidence:{index}", CLAIM_TEST_ASSERTION, link.oracle),
            )
        )
    return tuple(claims)


def verify_build_plan(bundle: BuildPlanBundle) -> BuildPlanReport:
    """Verify completeness and freshness without granting the IR authority of its own."""

    catalog = bundle.catalog
    plan = bundle.plan
    issues: list[str] = []
    if not catalog.catalog_id.strip():
        issues.append("pattern-catalog-id-missing")
    if not catalog.version.strip():
        issues.append("pattern-catalog-version-missing")
    if not catalog.patterns:
        issues.append("pattern-catalog-empty")
    if not verify_digest(catalog.body(), bundle.trusted_catalog_digest):
        issues.append("pattern-catalog-untrusted")

    patterns: dict[str, PatternDefinition] = {}
    for pattern in catalog.patterns:
        pattern_id = pattern.pattern_id.strip()
        if not pattern_id:
            issues.append("pattern-id-missing")
            continue
        if pattern_id in patterns:
            issues.append(f"pattern-id-duplicate:{pattern_id}")
            continue
        patterns[pattern_id] = pattern
        if not pattern.version.strip():
            issues.append(f"pattern-version-missing:{pattern_id}")
        if not _is_digest(pattern.artifact_digest):
            issues.append(f"pattern-artifact-digest-invalid:{pattern_id}")
        if not _is_digest(pattern.qualification_evidence_digest):
            issues.append(f"pattern-qualification-evidence-digest-invalid:{pattern_id}")
        if not pattern.mechanism:
            issues.append(f"pattern-mechanism-empty:{pattern_id}")

    if not plan.plan_id.strip():
        issues.append("build-plan-id-missing")
    if not plan.version.strip():
        issues.append("build-plan-version-missing")
    if plan.run_id != bundle.expected_run_id or not plan.run_id.strip():
        issues.append("build-plan-run-id-mismatch")
    if plan.target_digest != bundle.expected_target_digest or not _is_digest(plan.target_digest):
        issues.append("build-plan-target-digest-mismatch")
    if plan.construction_mode not in CONSTRUCTION_MODES:
        issues.append(f"build-plan-construction-mode-invalid:{plan.construction_mode}")
    if plan.max_build_attempts < 1:
        issues.append("build-plan-attempt-limit-invalid")
    if plan.build_input_digest != bundle.expected_build_input_digest or not _is_digest(
        plan.build_input_digest
    ):
        issues.append("build-plan-input-digest-mismatch")
    if plan.pattern_catalog_digest != catalog.content_digest:
        issues.append("build-plan-pattern-catalog-mismatch")

    artifacts: dict[str, PhaseArtifact] = {}
    current_phase_digests: dict[str, str] = {}
    for artifact in bundle.provenance.artifacts:
        if artifact.phase in REQUIRED_PHASES and artifact.phase not in artifacts:
            artifacts[artifact.phase] = artifact
            current_phase_digests[artifact.phase] = artifact.content_digest
    if dict(plan.phase_artifact_digests) != current_phase_digests:
        issues.append("build-plan-phase-artifacts-mismatch")
    if set(plan.phase_artifact_digests) != set(REQUIRED_PHASES):
        issues.append("build-plan-phase-set-incomplete")
    provenance_report = verify_intent_provenance(
        bundle.provenance.artifacts,
        _claims(plan),
        bundle.provenance.trusted_artifact_digests,
    )
    issues.extend(f"build-plan-provenance:{item}" for item in provenance_report.issues)

    if not plan.steps:
        issues.append("build-plan-steps-empty")
    declared_ids = {step.step_id for step in plan.steps}
    step_index: dict[str, BuildStep] = {}
    verified_steps: list[str] = []
    for step in plan.steps:
        step_id = step.step_id.strip()
        if not step_id:
            issues.append("build-step-id-missing")
            continue
        if step_id in step_index:
            issues.append(f"build-step-id-duplicate:{step_id}")
            continue
        step_index[step_id] = step
        selected_pattern = patterns.get(step.pattern_id)
        if selected_pattern is None:
            issues.append(f"build-step-pattern-unknown:{step_id}:{step.pattern_id}")
        elif step.pattern_digest != selected_pattern.content_digest:
            issues.append(f"build-step-pattern-digest-mismatch:{step_id}")
        if not step.intent_backreferences:
            issues.append(f"build-step-intent-empty:{step_id}")
        if not step.configuration:
            issues.append(f"build-step-configuration-empty:{step_id}")
        if len(step.intent_backreferences) != len(set(step.intent_backreferences)):
            issues.append(f"build-step-intent-duplicate:{step_id}")
        if len(step.depends_on) != len(set(step.depends_on)):
            issues.append(f"build-step-dependency-duplicate:{step_id}")
        for dependency in step.depends_on:
            if dependency == step_id:
                issues.append(f"build-step-dependency-self:{step_id}")
            elif dependency not in declared_ids:
                issues.append(f"build-step-dependency-unknown:{step_id}:{dependency}")
        if selected_pattern is not None and step.pattern_digest == selected_pattern.content_digest:
            verified_steps.append(step_id)
    for step_id in _cycle_nodes(plan.steps, set(step_index)):
        issues.append(f"build-step-dependency-cycle:{step_id}")

    upstream: set[IntentBackreference] = set()
    required_expectations: set[IntentBackreference] = set()
    operational: set[IntentBackreference] = set()
    for phase, artifact in artifacts.items():
        references = {artifact.backreference(item) for item in artifact.items}
        if phase == PHASE_PRODUCT_SPECIFICATION:
            upstream.update(references)
            required_expectations.update(references)
        elif phase == PHASE_ARCHITECTURE:
            upstream.update(references)
        elif phase == PHASE_OPERATIONAL_MATURITY:
            operational.update(references)
    implemented = {
        reference
        for step in plan.steps
        for reference in step.intent_backreferences
        if reference in upstream
    }
    for missing in sorted(upstream - implemented, key=lambda row: (row.artifact_id, row.item_id)):
        issues.append(
            f"build-plan-implementation-coverage-missing:{missing.artifact_id}:{missing.item_id}"
        )

    linked_expectations: set[IntentBackreference] = set()
    linked_oracles: set[IntentBackreference] = set()
    pairs: set[tuple[IntentBackreference, IntentBackreference]] = set()
    verified_links = 0
    if not plan.oracle_links:
        issues.append("build-plan-oracle-links-empty")
    for link in plan.oracle_links:
        pair = (link.expectation, link.oracle)
        if pair in pairs:
            issues.append("build-plan-oracle-link-duplicate")
            continue
        pairs.add(pair)
        valid = True
        if link.expectation not in upstream:
            issues.append("build-plan-oracle-expectation-not-upstream")
            valid = False
        if link.oracle not in operational:
            issues.append("build-plan-oracle-not-operational")
            valid = False
        if valid:
            linked_expectations.add(link.expectation)
            linked_oracles.add(link.oracle)
            verified_links += 1
    for missing in sorted(
        required_expectations - linked_expectations,
        key=lambda row: (row.artifact_id, row.item_id),
    ):
        issues.append(f"build-plan-oracle-coverage-missing:{missing.artifact_id}:{missing.item_id}")
    for unused in sorted(
        operational - linked_oracles,
        key=lambda row: (row.artifact_id, row.item_id),
    ):
        issues.append(f"build-plan-operational-item-unused:{unused.artifact_id}:{unused.item_id}")

    unique = tuple(dict.fromkeys(issues))
    return BuildPlanReport(
        ready=not unique,
        catalog_digest=catalog.content_digest,
        plan_digest=plan.content_digest,
        phase_artifact_digests=current_phase_digests,
        verified_step_ids=tuple(sorted(set(verified_steps))),
        covered_expectation_count=len(linked_expectations),
        verified_oracle_link_count=verified_links,
        issues=unique,
    )
