"""Provenance-of-intent tests over synthetic, target-agnostic phase artifacts."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from factory_core.manifest import digest_obj
from factory_core.provenance import (
    CLAIM_CONSTRAINT,
    CLAIM_REQUIREMENT,
    CLAIM_TASK,
    CLAIM_TEST_ASSERTION,
    PHASE_ARCHITECTURE,
    PHASE_OPERATIONAL_MATURITY,
    PHASE_PRODUCT_SPECIFICATION,
    PROVENANCE_GAP_PREFIXES,
    IntentBackreference,
    IntentItem,
    PhaseArtifact,
    ProvenanceBundle,
    ProvenanceClaim,
    provenance_issue_is_gap,
    verify_intent_provenance,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "factory_core" / "provenance.py"
DENYLIST_TOKENS = tuple(
    json.loads((REPO_ROOT / "core_purity_denylist.json").read_text(encoding="utf-8")).get(
        "tokens", []
    )
)


def _artifact(artifact_id: str, phase: str, item_id: str, statement: str) -> PhaseArtifact:
    return PhaseArtifact(
        artifact_id=artifact_id,
        phase=phase,
        version="1",
        source_digest=digest_obj({"verbatim": f"source for {artifact_id}"}),
        human_ratifier="human-1",
        validator_ratifier="validator-1",
        items=(IntentItem(item_id=item_id, canonical_statement=statement),),
    )


def _bundle() -> tuple[PhaseArtifact, ...]:
    return (
        _artifact(
            "phase-1-v1",
            PHASE_PRODUCT_SPECIFICATION,
            "behavior-1",
            "A submitted record is observable through the public interface.",
        ),
        _artifact(
            "phase-2-v1",
            PHASE_ARCHITECTURE,
            "owner-1",
            "Component alpha is the sole authoritative owner of the record.",
        ),
        _artifact(
            "phase-3-v1",
            PHASE_OPERATIONAL_MATURITY,
            "failure-1",
            "An unavailable authority denies the mutation.",
        ),
    )


def _trust(artifacts: tuple[PhaseArtifact, ...]) -> dict[str, str]:
    return {artifact.artifact_id: artifact.content_digest for artifact in artifacts}


def _claim(
    claim_id: str,
    kind: str,
    artifact: PhaseArtifact,
    item: IntentItem | None = None,
) -> ProvenanceClaim:
    selected = item or artifact.items[0]
    return ProvenanceClaim(
        claim_id=claim_id,
        kind=kind,
        backreference=IntentBackreference(
            artifact_id=artifact.artifact_id,
            artifact_digest=artifact.content_digest,
            item_id=selected.item_id,
            intent_digest=selected.intent_digest,
        ),
    )


def test_all_claim_kinds_resolve_to_canonical_statements() -> None:
    artifacts = _bundle()
    claims = (
        _claim("req-1", CLAIM_REQUIREMENT, artifacts[0]),
        _claim("constraint-1", CLAIM_CONSTRAINT, artifacts[1]),
        _claim("task-1", CLAIM_TASK, artifacts[1]),
        _claim("assertion-1", CLAIM_TEST_ASSERTION, artifacts[2]),
    )

    report = verify_intent_provenance(artifacts, claims, _trust(artifacts))

    assert report.satisfied is True, report.issues
    assert report.issues == ()
    assert [claim.claim_id for claim in report.resolved_claims] == [
        "req-1",
        "constraint-1",
        "task-1",
        "assertion-1",
    ]
    assert (
        report.resolved_claims[-1].canonical_statement == artifacts[2].items[0].canonical_statement
    )


def test_missing_phase_fails_closed() -> None:
    artifacts = _bundle()[:2]
    report = verify_intent_provenance(artifacts, (), _trust(artifacts))
    assert report.satisfied is False
    assert f"phase-missing:{PHASE_OPERATIONAL_MATURITY}" in report.issues


def test_empty_artifact_or_claim_set_is_not_vacuously_valid() -> None:
    artifacts = _bundle()
    empty_operational = replace(artifacts[2], items=())
    bundle = (artifacts[0], artifacts[1], empty_operational)

    report = verify_intent_provenance(bundle, (), _trust(bundle))

    assert report.satisfied is False
    assert "artifact-items-empty:phase-3-v1" in report.issues
    assert "claims-empty" in report.issues


def test_untrusted_or_mutated_artifact_fails_closed() -> None:
    artifacts = _bundle()
    trust = _trust(artifacts)

    untrusted = verify_intent_provenance(artifacts, (), {})
    assert untrusted.satisfied is False
    assert "artifact-untrusted:phase-1-v1" in untrusted.issues

    mutated_product = replace(
        artifacts[0],
        items=(
            IntentItem(
                item_id="behavior-1",
                canonical_statement="A different statement was substituted.",
            ),
        ),
    )
    mutated_bundle = (mutated_product, artifacts[1], artifacts[2])
    mutated = verify_intent_provenance(mutated_bundle, (), trust)
    assert mutated.satisfied is False
    assert "artifact-digest-mismatch:phase-1-v1" in mutated.issues


def test_bundle_copies_and_freezes_the_external_trust_map() -> None:
    artifacts = _bundle()
    trusted = _trust(artifacts)
    bundle = ProvenanceBundle(
        artifacts=artifacts,
        claims=(_claim("req-1", CLAIM_REQUIREMENT, artifacts[0]),),
        trusted_artifact_digests=trusted,
    )
    trusted.clear()

    assert bundle.verify().satisfied is True
    with pytest.raises(TypeError):
        bundle.trusted_artifact_digests["phase-1-v1"] = "sha256:" + ("0" * 64)  # type: ignore[index]


def test_missing_or_unresolvable_backreference_fails_closed() -> None:
    artifacts = _bundle()
    claims = (
        ProvenanceClaim(claim_id="no-ref", kind=CLAIM_TASK, backreference=None),
        ProvenanceClaim(
            claim_id="no-artifact",
            kind=CLAIM_TASK,
            backreference=IntentBackreference(
                artifact_id="absent",
                artifact_digest=digest_obj({"artifact": "absent"}),
                item_id="item",
                intent_digest=digest_obj({"canonical_statement": "x"}),
            ),
        ),
        ProvenanceClaim(
            claim_id="no-item",
            kind=CLAIM_TEST_ASSERTION,
            backreference=IntentBackreference(
                artifact_id=artifacts[0].artifact_id,
                artifact_digest=artifacts[0].content_digest,
                item_id="absent",
                intent_digest=digest_obj({"canonical_statement": "x"}),
            ),
        ),
    )

    report = verify_intent_provenance(artifacts, claims, _trust(artifacts))

    assert report.satisfied is False
    assert "backreference-missing:no-ref" in report.issues
    assert "artifact-unresolved:no-artifact:absent" in report.issues
    assert "item-unresolved:no-item:phase-1-v1:absent" in report.issues


def test_missing_links_are_classifiable_without_weakening_integrity_failures() -> None:
    artifacts = _bundle()
    missing = ProvenanceClaim(claim_id="no-ref", kind=CLAIM_TASK, backreference=None)
    unresolved = ProvenanceClaim(
        claim_id="no-item",
        kind=CLAIM_TASK,
        backreference=IntentBackreference(
            artifact_id=artifacts[0].artifact_id,
            artifact_digest=artifacts[0].content_digest,
            item_id="absent",
            intent_digest=digest_obj({"canonical_statement": "x"}),
        ),
    )

    report = verify_intent_provenance(artifacts, (missing, unresolved), _trust(artifacts))

    missing_issue = "backreference-missing:no-ref"
    unresolved_issue = "item-unresolved:no-item:phase-1-v1:absent"
    assert missing_issue in report.issues
    assert unresolved_issue in report.issues
    assert provenance_issue_is_gap(missing_issue) is True
    assert provenance_issue_is_gap(unresolved_issue) is False
    assert "backreference-missing" in PROVENANCE_GAP_PREFIXES


def test_empty_backreference_fields_are_reported_as_gaps_but_bad_digest_is_not() -> None:
    artifacts = _bundle()
    claims = (
        ProvenanceClaim(
            claim_id="no-artifact-id",
            kind=CLAIM_TASK,
            backreference=IntentBackreference(
                artifact_id="",
                artifact_digest=digest_obj({"artifact": "missing-id"}),
                item_id="item",
                intent_digest=digest_obj({"canonical_statement": "x"}),
            ),
        ),
        ProvenanceClaim(
            claim_id="no-digest",
            kind=CLAIM_TASK,
            backreference=IntentBackreference(
                artifact_id=artifacts[0].artifact_id,
                artifact_digest=artifacts[0].content_digest,
                item_id=artifacts[0].items[0].item_id,
                intent_digest="",
            ),
        ),
        ProvenanceClaim(
            claim_id="no-artifact-digest",
            kind=CLAIM_TASK,
            backreference=IntentBackreference(
                artifact_id=artifacts[0].artifact_id,
                artifact_digest="",
                item_id=artifacts[0].items[0].item_id,
                intent_digest=artifacts[0].items[0].intent_digest,
            ),
        ),
        ProvenanceClaim(
            claim_id="bad-digest",
            kind=CLAIM_TASK,
            backreference=IntentBackreference(
                artifact_id=artifacts[0].artifact_id,
                artifact_digest=artifacts[0].content_digest,
                item_id=artifacts[0].items[0].item_id,
                intent_digest="sha256:not-a-real-digest",
            ),
        ),
    )

    report = verify_intent_provenance(artifacts, claims, _trust(artifacts))

    assert "backreference-artifact-id-missing:no-artifact-id" in report.issues
    assert any(issue.startswith("intent-digest-missing:no-digest:") for issue in report.issues)
    assert "backreference-artifact-digest-missing:no-artifact-digest" in report.issues
    assert any(issue.startswith("intent-digest-mismatch:bad-digest:") for issue in report.issues)
    assert provenance_issue_is_gap("backreference-artifact-id-missing:no-artifact-id")
    assert provenance_issue_is_gap(
        f"intent-digest-missing:no-digest:{artifacts[0].artifact_id}:behavior-1"
    )
    assert provenance_issue_is_gap(
        "backreference-artifact-digest-missing:no-artifact-digest"
    )
    assert not provenance_issue_is_gap(
        f"intent-digest-mismatch:bad-digest:{artifacts[0].artifact_id}:behavior-1"
    )


def test_item_id_cannot_be_replayed_after_the_statement_changes() -> None:
    artifacts = _bundle()
    item = artifacts[0].items[0]
    claim = _claim("req-1", CLAIM_REQUIREMENT, artifacts[0], item)
    wrong_ref = replace(
        claim,
        backreference=replace(
            claim.backreference,  # type: ignore[arg-type]
            intent_digest=digest_obj({"canonical_statement": "not the ratified statement"}),
        ),
    )

    report = verify_intent_provenance(artifacts, (wrong_ref,), _trust(artifacts))

    assert report.satisfied is False
    assert "intent-digest-mismatch:req-1:phase-1-v1:behavior-1" in report.issues
    assert report.resolved_claims == ()


def test_new_signed_artifact_version_invalidates_all_old_derived_references() -> None:
    artifacts = _bundle()
    old_claim = _claim("req-1", CLAIM_REQUIREMENT, artifacts[0])
    amended_product = replace(artifacts[0], version="2")
    amended = (amended_product, artifacts[1], artifacts[2])

    report = verify_intent_provenance(amended, (old_claim,), _trust(amended))

    assert report.satisfied is False
    assert "backreference-artifact-digest-mismatch:req-1:phase-1-v1" in report.issues
    assert amended_product.items[0].intent_digest == artifacts[0].items[0].intent_digest


def test_duplicate_phases_items_and_claims_are_not_accepted() -> None:
    artifacts = _bundle()
    duplicate_item_artifact = replace(
        artifacts[0],
        items=(artifacts[0].items[0], artifacts[0].items[0]),
    )
    duplicate_phase_artifact = _artifact(
        "phase-1-v2",
        PHASE_PRODUCT_SPECIFICATION,
        "behavior-2",
        "A second active product artifact must not coexist in one run.",
    )
    invalid_bundle = (
        duplicate_item_artifact,
        duplicate_phase_artifact,
        artifacts[1],
        artifacts[2],
    )
    trust = _trust(invalid_bundle)
    claim = _claim("req-1", CLAIM_REQUIREMENT, duplicate_item_artifact)

    report = verify_intent_provenance(invalid_bundle, (claim, claim), trust)

    assert report.satisfied is False
    assert "item-id-duplicate:phase-1-v1:behavior-1" in report.issues
    assert f"phase-duplicate:{PHASE_PRODUCT_SPECIFICATION}:2" in report.issues
    assert "claim-id-duplicate:req-1" in report.issues


def test_from_dict_loads_the_canonical_schema() -> None:
    artifact = PhaseArtifact.from_dict(
        {
            "artifact_id": "phase-1-v1",
            "phase": PHASE_PRODUCT_SPECIFICATION,
            "version": "1",
            "source_digest": digest_obj({"verbatim": "source"}),
            "human_ratifier": "human-1",
            "validator_ratifier": "validator-1",
            "items": [{"item_id": "item-1", "canonical_statement": "The exact statement."}],
        }
    )
    claim = ProvenanceClaim.from_dict(
        {
            "claim_id": "claim-1",
            "kind": CLAIM_REQUIREMENT,
            "backreference": {
                "artifact_id": artifact.artifact_id,
                "artifact_digest": artifact.content_digest,
                "item_id": artifact.items[0].item_id,
                "intent_digest": artifact.items[0].intent_digest,
            },
        }
    )

    report = verify_intent_provenance(
        (artifact,),
        (claim,),
        {artifact.artifact_id: artifact.content_digest},
    )

    assert report.satisfied is False  # the other two required phase artifacts are absent
    assert report.resolved_claims[0].canonical_statement == "The exact statement."


def test_module_names_nothing_target_specific() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    assert not [token for token in DENYLIST_TOKENS if token.lower() in source]
