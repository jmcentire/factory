from __future__ import annotations

from dataclasses import replace

import pytest

from factory_core.build_plan import (
    BuildPlan,
    BuildPlanBundle,
    BuildStep,
    OracleLink,
    PatternCatalog,
    PatternDefinition,
    verify_build_plan,
)
from factory_core.manifest import digest_obj
from factory_core.provenance import (
    PHASE_ARCHITECTURE,
    PHASE_OPERATIONAL_MATURITY,
    PHASE_PRODUCT_SPECIFICATION,
    IntentItem,
    PhaseArtifact,
    ProvenanceBundle,
)
from factory_runtime.schema import DocumentValidationError, validate_document

RUN_ID = "run-1"
TARGET = digest_obj({"target": "synthetic"})
BUILD_INPUT = digest_obj({"input": "ratified-phases"})


def _artifact(phase: str, *items: tuple[str, str]) -> PhaseArtifact:
    return PhaseArtifact(
        artifact_id=f"synthetic-{phase}",
        phase=phase,
        version="1",
        source_digest=digest_obj({"source": phase}),
        human_ratifier="human:founder",
        validator_ratifier="agent:validator",
        items=tuple(
            IntentItem(item_id=item_id, canonical_statement=statement)
            for item_id, statement in items
        ),
    )


def _authority() -> tuple[PhaseArtifact, ...]:
    return (
        _artifact(
            PHASE_PRODUCT_SPECIFICATION,
            ("product:add", "The product adds integers."),
            ("product:error", "Invalid inputs fail visibly."),
        ),
        _artifact(
            PHASE_ARCHITECTURE,
            ("architecture:module", "The operation is exposed through one module."),
        ),
        _artifact(
            PHASE_OPERATIONAL_MATURITY,
            ("test:add", "Acceptance examples assert integer addition."),
            ("test:error", "Acceptance coverage asserts visible invalid-input failure."),
        ),
    )


def _catalog() -> PatternCatalog:
    return PatternCatalog(
        catalog_id="qualified-patterns",
        version="1",
        patterns=(
            PatternDefinition(
                pattern_id="python-function",
                version="1",
                artifact_digest=digest_obj({"artifact": "python-function-v1"}),
                qualification_evidence_digest=digest_obj({"qualification": "python-function-v1"}),
                mechanism={
                    "kind": "source-generator",
                    "required_configuration": ["module", "function", "operation"],
                },
            ),
        ),
    )


def _plan(
    artifacts: tuple[PhaseArtifact, ...],
    catalog: PatternCatalog,
) -> BuildPlan:
    product, architecture, operations = artifacts
    pattern = catalog.patterns[0]
    upstream = tuple(
        artifact.backreference(item)
        for artifact in (product, architecture)
        for item in artifact.items
    )
    add_oracle = operations.backreference(operations.items[0])
    error_oracle = operations.backreference(operations.items[1])
    return BuildPlan(
        plan_id="plan-1",
        version="1",
        run_id=RUN_ID,
        target_digest=TARGET,
        construction_mode="regenerate",
        max_build_attempts=2,
        build_input_digest=BUILD_INPUT,
        pattern_catalog_digest=catalog.content_digest,
        phase_artifact_digests={artifact.phase: artifact.content_digest for artifact in artifacts},
        steps=(
            BuildStep(
                step_id="generate-module",
                pattern_id=pattern.pattern_id,
                pattern_digest=pattern.content_digest,
                configuration={
                    "module": "calculator.py",
                    "function": "add",
                    "operation": "integer-addition",
                },
                intent_backreferences=upstream,
            ),
        ),
        oracle_links=(
            OracleLink(upstream[0], add_oracle),
            OracleLink(upstream[1], error_oracle),
        ),
    )


def _bundle(
    artifacts: tuple[PhaseArtifact, ...],
    catalog: PatternCatalog,
    plan: BuildPlan,
    *,
    trusted_catalog_digest: str | None = None,
) -> BuildPlanBundle:
    return BuildPlanBundle(
        catalog=catalog,
        trusted_catalog_digest=trusted_catalog_digest or catalog.content_digest,
        plan=plan,
        provenance=ProvenanceBundle(
            artifacts=artifacts,
            claims=(),
            trusted_artifact_digests={
                artifact.artifact_id: artifact.content_digest for artifact in artifacts
            },
        ),
        expected_run_id=RUN_ID,
        expected_target_digest=TARGET,
        expected_build_input_digest=BUILD_INPUT,
    )


def test_recipe_book_is_complete_current_schema_valid_and_content_addressed() -> None:
    artifacts = _authority()
    catalog = _catalog()
    plan = _plan(artifacts, catalog)

    report = verify_build_plan(_bundle(artifacts, catalog, plan))

    assert report.ready is True
    assert report.issues == ()
    assert report.verified_step_ids == ("generate-module",)
    assert report.covered_expectation_count == 2
    assert report.verified_oracle_link_count == 2
    validate_document("pattern-catalog", catalog.body())
    validate_document("build-plan", plan.body())


def test_recipe_and_step_configuration_are_immutable_copies() -> None:
    mechanism = {"kind": "generator", "fields": ["module"]}
    configuration = {"module": "calculator.py", "options": ["typed"]}
    pattern = PatternDefinition(
        pattern_id="immutable",
        version="1",
        artifact_digest=digest_obj({"artifact": 1}),
        qualification_evidence_digest=digest_obj({"evidence": 1}),
        mechanism=mechanism,
    )
    step = BuildStep(
        step_id="immutable",
        pattern_id="immutable",
        pattern_digest=pattern.content_digest,
        configuration=configuration,
    )
    pattern_digest = pattern.content_digest
    step_body = step.to_dict()

    mechanism["fields"].append("function")
    configuration["options"].append("formatted")

    assert pattern.content_digest == pattern_digest
    assert pattern.body()["mechanism"]["fields"] == ["module"]
    assert step.to_dict() == step_body


@pytest.mark.parametrize(
    ("mutation", "issue"),
    (
        (lambda plan: replace(plan, run_id="other"), "build-plan-run-id-mismatch"),
        (
            lambda plan: replace(plan, target_digest=digest_obj({"target": "other"})),
            "build-plan-target-digest-mismatch",
        ),
        (
            lambda plan: replace(plan, build_input_digest=digest_obj({"input": "stale"})),
            "build-plan-input-digest-mismatch",
        ),
        (lambda plan: replace(plan, max_build_attempts=0), "build-plan-attempt-limit-invalid"),
    ),
)
def test_stale_or_unbounded_recipe_book_is_refused(mutation: object, issue: str) -> None:
    artifacts = _authority()
    catalog = _catalog()
    plan = mutation(_plan(artifacts, catalog))  # type: ignore[operator]

    report = verify_build_plan(_bundle(artifacts, catalog, plan))

    assert report.ready is False
    assert issue in report.issues


def test_all_authority_needs_construction_and_each_product_expectation_needs_an_oracle() -> None:
    artifacts = _authority()
    catalog = _catalog()
    plan = _plan(artifacts, catalog)
    step = replace(
        plan.steps[0],
        intent_backreferences=plan.steps[0].intent_backreferences[:-1],
    )
    incomplete = replace(plan, steps=(step,), oracle_links=plan.oracle_links[:-1])

    report = verify_build_plan(_bundle(artifacts, catalog, incomplete))

    assert report.ready is False
    assert any(
        item.startswith("build-plan-implementation-coverage-missing:") for item in report.issues
    )
    assert any(item.startswith("build-plan-oracle-coverage-missing:") for item in report.issues)


def test_unknown_pattern_and_dependency_cycle_are_refused() -> None:
    artifacts = _authority()
    catalog = _catalog()
    plan = _plan(artifacts, catalog)
    first = replace(
        plan.steps[0],
        step_id="first",
        pattern_id="unknown",
        depends_on=("second",),
    )
    second = replace(plan.steps[0], step_id="second", depends_on=("first",))
    invalid = replace(plan, steps=(first, second))

    report = verify_build_plan(_bundle(artifacts, catalog, invalid))

    assert report.ready is False
    assert "build-step-pattern-unknown:first:unknown" in report.issues
    assert "build-step-dependency-cycle:first" in report.issues
    assert "build-step-dependency-cycle:second" in report.issues


def test_catalog_trust_is_external_and_schema_refuses_unconfigured_steps() -> None:
    artifacts = _authority()
    catalog = _catalog()
    plan = _plan(artifacts, catalog)
    report = verify_build_plan(
        _bundle(
            artifacts,
            catalog,
            plan,
            trusted_catalog_digest=digest_obj({"catalog": "untrusted"}),
        )
    )
    assert report.ready is False
    assert "pattern-catalog-untrusted" in report.issues

    malformed = plan.body()
    del malformed["steps"][0]["configuration"]
    with pytest.raises(DocumentValidationError, match="configuration"):
        validate_document("build-plan", malformed)
