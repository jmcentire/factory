"""Existing tests change only with artifact supersession plus an exact human impact ruling."""

from __future__ import annotations

from dataclasses import replace

from factory_core.manifest import digest_obj
from factory_core.provenance import (
    CLAIM_TEST_ASSERTION,
    PHASE_ARCHITECTURE,
    PHASE_OPERATIONAL_MATURITY,
    PHASE_PRODUCT_SPECIFICATION,
    IntentBackreference,
    IntentItem,
    PhaseArtifact,
    ProvenanceBundle,
    ProvenanceClaim,
)
from factory_core.test_disposition import (
    TEST_ACTION_FIX_IMPLEMENTATION,
    TEST_ACTION_ROUTE_HUMAN,
    TEST_ACTION_UPDATE,
    ExistingTestFailure,
    dispose_existing_test_failure,
)
from factory_core.test_disposition import (
    TestAssertionBinding as _TestAssertionBinding,
)
from factory_core.test_disposition import (
    TestChangeAuthorization as _TestChangeAuthorization,
)
from factory_core.test_disposition import (
    TestSelection as _TestSelection,
)

RUN_ID = "run-1"


def _old_reference() -> IntentBackreference:
    return IntentBackreference(
        artifact_id="product-v1",
        artifact_digest=digest_obj({"old-artifact": "v1"}),
        item_id="behavior",
        intent_digest=digest_obj({"canonical_statement": "The old behavior."}),
    )


def _artifact(
    artifact_id: str,
    phase: str,
    item_id: str,
    statement: str,
    *,
    supersedes: tuple[IntentBackreference, ...] = (),
) -> PhaseArtifact:
    return PhaseArtifact(
        artifact_id=artifact_id,
        phase=phase,
        version="2",
        source_digest=digest_obj({"source": artifact_id}),
        human_ratifier="human",
        validator_ratifier="validator",
        items=(
            IntentItem(
                item_id=item_id,
                canonical_statement=statement,
                supersedes=supersedes,
            ),
        ),
    )


def _bundle(product_items: tuple[IntentItem, ...]) -> ProvenanceBundle:
    product = PhaseArtifact(
        artifact_id="product-v2",
        phase=PHASE_PRODUCT_SPECIFICATION,
        version="2",
        source_digest=digest_obj({"source": "product-v2"}),
        human_ratifier="human",
        validator_ratifier="validator",
        items=product_items,
    )
    architecture = _artifact("architecture-v2", PHASE_ARCHITECTURE, "owner", "One owner.")
    operations = _artifact(
        "operations-v2",
        PHASE_OPERATIONAL_MATURITY,
        "failure",
        "The failure is denied.",
    )
    artifacts = (product, architecture, operations)
    claim = ProvenanceClaim(
        claim_id="assertion-current",
        kind=CLAIM_TEST_ASSERTION,
        backreference=product.backreference(product.items[0]),
    )
    return ProvenanceBundle(
        artifacts=artifacts,
        claims=(claim,),
        trusted_artifact_digests={
            artifact.artifact_id: artifact.content_digest for artifact in artifacts
        },
    )


def _failure(test_id: str, behavior: IntentBackreference) -> ExistingTestFailure:
    return ExistingTestFailure(
        run_id=RUN_ID,
        test_id=test_id,
        assertion_digest=digest_obj({"assertion": test_id}),
        asserted_phase=PHASE_PRODUCT_SPECIFICATION,
        asserted_behavior=behavior,
    )


def _authorization(
    bundle: ProvenanceBundle,
    failure: ExistingTestFailure,
    new_behavior: IntentBackreference,
) -> _TestChangeAuthorization:
    assert failure.asserted_behavior is not None
    return _TestChangeAuthorization(
        authorization_id="change-tests-1",
        version="1",
        run_id=RUN_ID,
        human_authorizer="human:founder",
        ruling="change-expected-behavior",
        expected_change_statement=next(
            item.canonical_statement
            for artifact in bundle.artifacts
            for item in artifact.items
            if artifact.backreference(item) == new_behavior
        )
        if any(
            artifact.backreference(item) == new_behavior
            for artifact in bundle.artifacts
            for item in artifact.items
        )
        else "An invented behavior that has no phase authority.",
        phase_artifact_digests={
            artifact.phase: artifact.content_digest for artifact in bundle.artifacts
        },
        old_behavior=failure.asserted_behavior,
        new_behavior=new_behavior,
        selection=_TestSelection(
            members=(
                _TestAssertionBinding(
                    test_id=failure.test_id,
                    assertion_digest=failure.assertion_digest,
                ),
            )
        ),
    )


def test_supersession_still_requires_an_affirmative_human_test_impact_ruling() -> None:
    old = _old_reference()
    bundle = _bundle(
        (
            IntentItem(
                item_id="behavior-v2",
                canonical_statement="The deliberately changed behavior.",
                supersedes=(old,),
            ),
        )
    )

    failure = _failure("acceptance-old", old)
    new_behavior = bundle.artifacts[0].backreference(bundle.artifacts[0].items[0])

    without_ruling = dispose_existing_test_failure(failure, bundle)
    authorization = _authorization(bundle, failure, new_behavior)
    decision = dispose_existing_test_failure(
        failure,
        bundle,
        authorization=authorization,
        trusted_authorization_digest=authorization.content_digest,
    )

    assert without_ruling.action == TEST_ACTION_ROUTE_HUMAN
    assert without_ruling.reason == "human-test-change-authorization-required"
    assert decision.action == TEST_ACTION_UPDATE
    assert decision.superseding_backreference == new_behavior
    assert decision.test_change_authorization_digest == authorization.content_digest


def test_behavior_still_authorized_means_fix_the_implementation() -> None:
    item = IntentItem(item_id="behavior", canonical_statement="The behavior remains required.")
    bundle = _bundle((item,))
    current = bundle.artifacts[0].backreference(item)

    decision = dispose_existing_test_failure(
        _failure("acceptance-current", current),
        bundle,
    )

    assert decision.action == TEST_ACTION_FIX_IMPLEMENTATION
    assert decision.reason == "signed-artifacts-retain-asserted-behavior"
    assert decision.current_backreference == current


def test_unrelated_artifact_amendment_rebinds_same_behavior_and_fixes_code() -> None:
    old = IntentBackreference(
        artifact_id="product-v1",
        artifact_digest=digest_obj({"product": "v1"}),
        item_id="behavior",
        intent_digest=digest_obj({"canonical_statement": "The behavior remains required."}),
    )
    item = IntentItem(
        item_id="behavior",
        canonical_statement="The behavior remains required.",
    )
    bundle = _bundle((item,))

    decision = dispose_existing_test_failure(
        _failure("acceptance-old-version", old),
        bundle,
    )

    assert decision.action == TEST_ACTION_FIX_IMPLEMENTATION
    assert decision.reason == "new-artifact-version-retains-exact-asserted-behavior"
    assert decision.current_backreference == bundle.artifacts[0].backreference(item)


def test_artifact_silence_routes_to_the_human() -> None:
    bundle = _bundle(
        (IntentItem(item_id="different", canonical_statement="An unrelated behavior."),)
    )

    decision = dispose_existing_test_failure(
        _failure("acceptance-ambiguous", _old_reference()),
        bundle,
    )

    assert decision.action == TEST_ACTION_ROUTE_HUMAN
    assert decision.reason == "signed-artifacts-silent-on-asserted-behavior"


def test_multiple_superseding_items_are_ambiguous_not_selected_by_order() -> None:
    old = _old_reference()
    bundle = _bundle(
        (
            IntentItem(item_id="one", canonical_statement="First replacement.", supersedes=(old,)),
            IntentItem(item_id="two", canonical_statement="Second replacement.", supersedes=(old,)),
        )
    )

    decision = dispose_existing_test_failure(
        _failure("acceptance-conflict", old),
        bundle,
    )

    assert decision.action == TEST_ACTION_ROUTE_HUMAN
    assert decision.reason == "multiple-signed-items-supersede-asserted-behavior"


def test_retained_and_superseded_behavior_is_a_specification_conflict() -> None:
    old = _old_reference()
    bundle = _bundle(
        (
            IntentItem(
                item_id=old.item_id,
                canonical_statement="The old behavior.",
            ),
            IntentItem(
                item_id="replacement",
                canonical_statement="The replacement behavior.",
                supersedes=(old,),
            ),
        )
    )

    decision = dispose_existing_test_failure(
        _failure("acceptance-conflict", old),
        bundle,
    )

    assert decision.action == TEST_ACTION_ROUTE_HUMAN
    assert decision.reason == "signed-artifacts-both-retain-and-supersede-asserted-behavior"


def test_invalid_phase_authority_never_justifies_a_code_or_test_edit() -> None:
    item = IntentItem(item_id="behavior", canonical_statement="The behavior remains required.")
    bundle = _bundle((item,))
    invalid = ProvenanceBundle(
        artifacts=bundle.artifacts,
        claims=bundle.claims,
        trusted_artifact_digests={},
    )

    decision = dispose_existing_test_failure(
        _failure(
            "acceptance-invalid-authority",
            bundle.artifacts[0].backreference(item),
        ),
        invalid,
    )

    assert decision.action == TEST_ACTION_ROUTE_HUMAN
    assert decision.reason.startswith("phase-artifact-provenance-invalid:")


def test_a_human_ruling_cannot_invert_behavior_the_artifacts_still_retain() -> None:
    item = IntentItem(item_id="behavior", canonical_statement="The behavior remains required.")
    bundle = _bundle((item,))
    current = bundle.artifacts[0].backreference(item)
    failure = _failure("acceptance-current", current)
    forged_new = IntentBackreference(
        artifact_id="invented",
        artifact_digest=digest_obj({"invented": "artifact"}),
        item_id="opposite",
        intent_digest=digest_obj({"canonical_statement": "The opposite."}),
    )
    authorization = _authorization(bundle, failure, forged_new)

    decision = dispose_existing_test_failure(
        failure,
        bundle,
        authorization=authorization,
        trusted_authorization_digest=authorization.content_digest,
    )

    assert decision.action == TEST_ACTION_FIX_IMPLEMENTATION
    assert decision.reason == "signed-artifacts-retain-asserted-behavior"


def test_human_change_statement_must_equal_the_unique_signed_replacement() -> None:
    old = _old_reference()
    bundle = _bundle(
        (
            IntentItem(
                item_id="behavior-v2",
                canonical_statement="The signed replacement behavior.",
                supersedes=(old,),
            ),
        )
    )
    failure = _failure("acceptance-old", old)
    replacement = bundle.artifacts[0].backreference(bundle.artifacts[0].items[0])
    authorization = replace(
        _authorization(bundle, failure, replacement),
        expected_change_statement="The opposite of the signed replacement.",
    )

    decision = dispose_existing_test_failure(
        failure,
        bundle,
        authorization=authorization,
        trusted_authorization_digest=authorization.content_digest,
    )

    assert decision.action == TEST_ACTION_ROUTE_HUMAN
    assert decision.reason == "test-change-authorization-change-statement-mismatch"


def test_another_phase_cannot_supersede_the_behavior_a_test_asserts() -> None:
    old = _old_reference()
    product = _artifact(
        "product-v2",
        PHASE_PRODUCT_SPECIFICATION,
        "different",
        "An unrelated product behavior.",
    )
    architecture = _artifact(
        "architecture-v2",
        PHASE_ARCHITECTURE,
        "replacement",
        "A cross-phase replacement claim.",
        supersedes=(old,),
    )
    operations = _artifact(
        "operations-v2",
        PHASE_OPERATIONAL_MATURITY,
        "failure",
        "The failure is denied.",
    )
    artifacts = (product, architecture, operations)
    bundle = ProvenanceBundle(
        artifacts=artifacts,
        claims=(
            ProvenanceClaim(
                claim_id="assertion-current",
                kind=CLAIM_TEST_ASSERTION,
                backreference=product.backreference(product.items[0]),
            ),
        ),
        trusted_artifact_digests={
            artifact.artifact_id: artifact.content_digest for artifact in artifacts
        },
    )

    decision = dispose_existing_test_failure(
        _failure("acceptance-old", old),
        bundle,
    )

    assert decision.action == TEST_ACTION_ROUTE_HUMAN
    assert decision.reason == "signed-artifacts-silent-on-asserted-behavior"
