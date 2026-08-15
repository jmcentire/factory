from factory_core import (
    BuildPlan,
    BuildPlanBundle,
    BuildStep,
    IntentItem,
    OracleLink,
    PatternCatalog,
    PatternDefinition,
    PhaseArtifact,
    ProvenanceBundle,
    digest_obj,
    verify_build_plan,
)
from factory_core.provenance import (
    PHASE_ARCHITECTURE,
    PHASE_OPERATIONAL_MATURITY,
    PHASE_PRODUCT_SPECIFICATION,
)


def _d(value):
    return digest_obj(value)


def _make_bundle(catalog_patterns, step_pattern_id, step_pattern_digest):
    catalog = PatternCatalog("cat-1", "1", tuple(catalog_patterns))
    catalog_digest = catalog.content_digest

    i1 = IntentItem("i1", "Product requirement 1")
    i2 = IntentItem("i2", "Architecture requirement 1")
    i3 = IntentItem("i3", "Operational requirement 1")

    prod = PhaseArtifact(
        "prod-1",
        PHASE_PRODUCT_SPECIFICATION,
        "1",
        _d("ps"),
        "human-a",
        "validator-a",
        (i1,),
    )
    arch = PhaseArtifact(
        "arch-1",
        PHASE_ARCHITECTURE,
        "1",
        _d("as"),
        "human-b",
        "validator-b",
        (i2,),
    )
    ops = PhaseArtifact(
        "ops-1",
        PHASE_OPERATIONAL_MATURITY,
        "1",
        _d("os"),
        "human-c",
        "validator-c",
        (i3,),
    )

    step = BuildStep(
        "step-1",
        step_pattern_id,
        step_pattern_digest,
        {"k": "v"},
        (prod.backreference(i1), arch.backreference(i2)),
    )

    oracle = OracleLink(prod.backreference(i1), ops.backreference(i3))

    plan = BuildPlan(
        "plan-1",
        "1",
        "run-1",
        _d("target"),
        "regenerate",
        3,
        _d("build-input"),
        catalog_digest,
        {
            PHASE_PRODUCT_SPECIFICATION: prod.content_digest,
            PHASE_ARCHITECTURE: arch.content_digest,
            PHASE_OPERATIONAL_MATURITY: ops.content_digest,
        },
        (step,),
        (oracle,),
    )

    provenance = ProvenanceBundle(
        artifacts=(prod, arch, ops),
        claims=(),
        trusted_artifact_digests={
            artifact.artifact_id: artifact.content_digest for artifact in (prod, arch, ops)
        },
    )

    bundle = BuildPlanBundle(
        catalog,
        catalog_digest,
        plan,
        provenance,
        "run-1",
        _d("target"),
        _d("build-input"),
    )

    return bundle, step


def test_first_pattern_ready_with_unrelated_second():
    """PRIMER.md: selecting the first pattern remains ready and includes the
    step id in verified_step_ids when an unrelated second pattern is last."""
    p1 = PatternDefinition("p1", "1", _d("artifact-a"), _d("qual-a"), {"kind": "mechanism-a"})
    p2 = PatternDefinition("p2", "1", _d("artifact-b"), _d("qual-b"), {"kind": "mechanism-b"})

    bundle, step = _make_bundle([p1, p2], "p1", p1.content_digest)
    report = verify_build_plan(bundle)

    assert report.ready is True
    assert step.step_id in report.verified_step_ids


def test_reversed_catalog_order_preserves_result():
    """PRIMER.md: reversing catalog order preserves the same result after
    recomputing content-addressed plan bindings."""
    p1 = PatternDefinition("p1", "1", _d("artifact-a"), _d("qual-a"), {"kind": "mechanism-a"})
    p2 = PatternDefinition("p2", "1", _d("artifact-b"), _d("qual-b"), {"kind": "mechanism-b"})

    bundle_fwd, step = _make_bundle([p1, p2], "p1", p1.content_digest)
    report_fwd = verify_build_plan(bundle_fwd)

    bundle_rev, _ = _make_bundle([p2, p1], "p1", p1.content_digest)
    report_rev = verify_build_plan(bundle_rev)

    assert report_fwd.ready == report_rev.ready
    assert step.step_id in report_rev.verified_step_ids


def test_unknown_pattern_id_fails():
    """PRIMER.md: an unknown pattern id must fail visibly."""
    p1 = PatternDefinition("p1", "1", _d("artifact-a"), _d("qual-a"), {"kind": "mechanism-a"})
    p2 = PatternDefinition("p2", "1", _d("artifact-b"), _d("qual-b"), {"kind": "mechanism-b"})

    bundle, _ = _make_bundle([p1, p2], "p-unknown", p1.content_digest)
    report = verify_build_plan(bundle)

    assert report.ready is False


def test_wrong_pattern_digest_fails():
    """PRIMER.md: a step using the other pattern's digest fails visibly."""
    p1 = PatternDefinition("p1", "1", _d("artifact-a"), _d("qual-a"), {"kind": "mechanism-a"})
    p2 = PatternDefinition("p2", "1", _d("artifact-b"), _d("qual-b"), {"kind": "mechanism-b"})

    bundle, _ = _make_bundle([p1, p2], "p1", p2.content_digest)
    report = verify_build_plan(bundle)

    assert report.ready is False
