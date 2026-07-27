"""Existing-test failure disposition is based on signed supersession, never preference."""

from __future__ import annotations

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


def test_signed_supersession_authorizes_updating_the_existing_test() -> None:
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

    decision = dispose_existing_test_failure(
        ExistingTestFailure(test_id="acceptance-old", asserted_behavior=old),
        bundle,
    )

    assert decision.action == TEST_ACTION_UPDATE
    assert decision.superseding_backreference == bundle.artifacts[0].backreference(
        bundle.artifacts[0].items[0]
    )


def test_behavior_still_authorized_means_fix_the_implementation() -> None:
    item = IntentItem(item_id="behavior", canonical_statement="The behavior remains required.")
    bundle = _bundle((item,))
    current = bundle.artifacts[0].backreference(item)

    decision = dispose_existing_test_failure(
        ExistingTestFailure(test_id="acceptance-current", asserted_behavior=current),
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
        intent_digest=digest_obj(
            {"canonical_statement": "The behavior remains required."}
        ),
    )
    item = IntentItem(
        item_id="behavior",
        canonical_statement="The behavior remains required.",
    )
    bundle = _bundle((item,))

    decision = dispose_existing_test_failure(
        ExistingTestFailure(test_id="acceptance-old-version", asserted_behavior=old),
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
        ExistingTestFailure(test_id="acceptance-ambiguous", asserted_behavior=_old_reference()),
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
        ExistingTestFailure(test_id="acceptance-conflict", asserted_behavior=old),
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
        ExistingTestFailure(test_id="acceptance-conflict", asserted_behavior=old),
        bundle,
    )

    assert decision.action == TEST_ACTION_ROUTE_HUMAN
    assert (
        decision.reason
        == "signed-artifacts-both-retain-and-supersede-asserted-behavior"
    )


def test_invalid_phase_authority_never_justifies_a_code_or_test_edit() -> None:
    item = IntentItem(item_id="behavior", canonical_statement="The behavior remains required.")
    bundle = _bundle((item,))
    invalid = ProvenanceBundle(
        artifacts=bundle.artifacts,
        claims=bundle.claims,
        trusted_artifact_digests={},
    )

    decision = dispose_existing_test_failure(
        ExistingTestFailure(
            test_id="acceptance-invalid-authority",
            asserted_behavior=bundle.artifacts[0].backreference(item),
        ),
        invalid,
    )

    assert decision.action == TEST_ACTION_ROUTE_HUMAN
    assert decision.reason.startswith("phase-artifact-provenance-invalid:")
