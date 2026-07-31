"""Graded independence, per-agent verifier identity, and the structural-depth trade."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from factory_core.evidence import EvidenceIntegrity
from factory_core.independence import (
    INDEPENDENCE_MODERATE,
    INDEPENDENCE_STRONGER,
    INDEPENDENCE_STRONGEST,
    INDEPENDENCE_TIERS,
    INDEPENDENCE_WEAK,
    INDEPENDENCE_WEAKEST,
    ROLE_CODER,
    ROLE_TESTER,
    ROLE_VALIDATOR,
    STRUCTURAL_MODE_IMPLEMENTATION_INFORMED,
    STRUCTURAL_MODE_ISOLATED,
    AgentIdentity,
    IndependenceRecord,
    StructuralModeRecord,
    derive_independence_tier,
    tier_rank,
    verify_independence,
)
from factory_core.manifest import digest_obj
from factory_core.provenance import IntentBackreference, IntentItem, PhaseArtifact

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATHS = (
    REPO_ROOT / "factory_core" / "independence.py",
    REPO_ROOT / "factory_core" / "monitors.py",
    REPO_ROOT / "factory_core" / "triage.py",
    REPO_ROOT / "factory_core" / "correction.py",
)
DENYLIST_TOKENS = tuple(
    json.loads((REPO_ROOT / "core_purity_denylist.json").read_text(encoding="utf-8")).get(
        "tokens", []
    )
)


def _evidence(body: dict[str, Any]) -> EvidenceIntegrity:
    return EvidenceIntegrity(body=body, claimed_digest=digest_obj(body))


def _agents(
    *,
    coder_family: str = "family-a",
    tester_family: str = "family-b",
    roles: tuple[str, ...] = (ROLE_CODER, ROLE_TESTER, ROLE_VALIDATOR),
) -> tuple[AgentIdentity, ...]:
    families = {
        ROLE_CODER: coder_family,
        ROLE_TESTER: tester_family,
        ROLE_VALIDATOR: "family-c",
    }
    return tuple(
        AgentIdentity(
            role=role,
            model_family=families[role],
            model_version="2026-07",
            directive_version=f"{role}-directive-3",
        )
        for role in roles
    )


def _record(**overrides: Any) -> IndependenceRecord:
    values: dict[str, Any] = {
        "agents": _agents(),
        "shared_context": False,
        "channel_open": False,
        "claimed_tier": INDEPENDENCE_STRONGER,
        "structural_mode": _isolated_mode(),
    }
    values.update(overrides)
    return IndependenceRecord(**values)


def _isolated_mode(*, note: str = "Structural depth was not purchased.") -> StructuralModeRecord:
    record = StructuralModeRecord(mode=STRUCTURAL_MODE_ISOLATED, decision_package_note=note)
    return replace(record, mutation_evidence=_evidence(record.authority_body()))


def _resolved_contract() -> tuple[IntentBackreference, ...]:
    artifact = PhaseArtifact(
        artifact_id="phase-2",
        phase="architecture",
        version="1",
        source_digest=digest_obj({"verbatim": "source"}),
        human_ratifier="human-1",
        validator_ratifier="validator-1",
        items=(IntentItem(item_id="interface", canonical_statement="The interface is fixed."),),
    )
    return (artifact.backreference(artifact.items[0]),)


def test_tier_ladder_is_derived_from_the_recorded_arrangement() -> None:
    same_family = {"coder_family": "family-a", "tester_family": "family-a"}

    weakest = _record(shared_context=True, channel_open=True)
    weak = _record(shared_context=False, channel_open=True)
    moderate = _record(agents=_agents(**same_family), shared_context=False, channel_open=False)
    stronger = _record(shared_context=False, channel_open=False)
    strongest = _record(mechanism_ids=("schema-validator",))

    assert derive_independence_tier(weakest) == INDEPENDENCE_WEAKEST
    assert derive_independence_tier(weak) == INDEPENDENCE_WEAK
    assert derive_independence_tier(moderate) == INDEPENDENCE_MODERATE
    assert derive_independence_tier(stronger) == INDEPENDENCE_STRONGER
    assert derive_independence_tier(strongest) == INDEPENDENCE_STRONGEST
    assert tuple(sorted(INDEPENDENCE_TIERS, key=tier_rank)) == INDEPENDENCE_TIERS


def test_unrecorded_arrangement_derives_the_weakest_tier() -> None:
    # Fail closed: an absent fact is not evidence of separation.
    assert derive_independence_tier(IndependenceRecord()) == INDEPENDENCE_WEAKEST


def test_an_unrecorded_model_family_cannot_demonstrate_diversity() -> None:
    record = _record(
        agents=_agents(roles=(ROLE_CODER, ROLE_VALIDATOR)),
        claimed_tier=INDEPENDENCE_MODERATE,
    )

    assert derive_independence_tier(record) == INDEPENDENCE_MODERATE


def test_a_tier_claim_above_the_derived_tier_is_a_false_verdict() -> None:
    overclaimed = verify_independence(
        _record(
            agents=_agents(coder_family="family-a", tester_family="family-a"),
            claimed_tier=INDEPENDENCE_STRONGER,
        )
    )
    understated = verify_independence(_record(claimed_tier=INDEPENDENCE_MODERATE))

    assert (
        f"independence-tier-overclaimed:{INDEPENDENCE_STRONGER}:{INDEPENDENCE_MODERATE}"
        in overclaimed.integrity_issues
    )
    assert overclaimed.satisfied is False
    # Understating is honest, so it is recorded rather than refused.
    assert understated.integrity_issues == ()
    assert any(
        report.startswith("independence-tier-understated") for report in understated.reports
    )


def test_missing_record_tier_or_unknown_tier_are_distinguished() -> None:
    absent = verify_independence(None)
    unclaimed = verify_independence(_record(claimed_tier=""))
    unknown = verify_independence(_record(claimed_tier="airtight"))

    assert absent.gaps == ("independence-record-missing",)
    assert "independence-tier-unclaimed" in unclaimed.gaps
    assert "independence-tier-unknown:airtight" in unknown.integrity_issues


def test_every_producing_and_judging_agent_records_model_and_directive_version() -> None:
    report = verify_independence(
        _record(
            agents=(
                AgentIdentity(role=ROLE_CODER, model_family="family-a"),
                AgentIdentity(
                    role=ROLE_TESTER,
                    model_family="family-b",
                    model_version="2026-07",
                    directive_version="tester-directive-3",
                ),
            )
        )
    )

    assert "independence-model-version-unrecorded:coder" in report.gaps
    assert "independence-directive-version-unrecorded:coder" in report.gaps
    # The judging agent counts: a verdict whose model nobody recorded cannot be requalified.
    assert "independence-agent-missing:validator" in report.gaps


def test_duplicate_agent_role_is_an_integrity_failure() -> None:
    report = verify_independence(_record(agents=_agents() + _agents()[:1]))

    assert "independence-agent-duplicate:coder" in report.integrity_issues


def test_an_open_coder_tester_channel_is_negative_evidence_for_every_class() -> None:
    report = verify_independence(_record(channel_open=True, claimed_tier=INDEPENDENCE_WEAK))

    assert "independence-coder-tester-channel-open" in report.failures
    assert report.satisfied is False


def test_structural_mode_requires_a_resolved_signed_contract() -> None:
    resolved = _resolved_contract()
    anchored = verify_independence(
        _record(
            structural_mode=StructuralModeRecord(
                mode=STRUCTURAL_MODE_IMPLEMENTATION_INFORMED,
                contract_backreference=resolved[0],
            )
        ),
        resolved_backreferences=resolved,
    )
    unanchored = verify_independence(
        _record(
            structural_mode=StructuralModeRecord(mode=STRUCTURAL_MODE_IMPLEMENTATION_INFORMED)
        ),
        resolved_backreferences=resolved,
    )
    unresolved = verify_independence(
        _record(
            structural_mode=StructuralModeRecord(
                mode=STRUCTURAL_MODE_IMPLEMENTATION_INFORMED,
                contract_backreference=replace(resolved[0], item_id="absent"),
            )
        ),
        resolved_backreferences=resolved,
    )

    assert anchored.integrity_issues == ()
    assert "structural-depth-purchased-against-signed-contract" in anchored.reports
    assert "structural-mode-without-signed-contract" in unanchored.integrity_issues
    assert "structural-mode-contract-unresolved" in unresolved.integrity_issues


def test_an_unverifiable_contract_reference_is_an_absence_when_authority_is_unavailable() -> None:
    resolved = _resolved_contract()
    report = verify_independence(
        _record(
            structural_mode=StructuralModeRecord(
                mode=STRUCTURAL_MODE_IMPLEMENTATION_INFORMED,
                contract_backreference=resolved[0],
            )
        ),
        authority_available=False,
    )

    assert "structural-mode-contract-authority-unavailable" in report.gaps
    assert report.integrity_issues == ()


def test_forgoing_structural_depth_owes_mutation_evidence_and_a_stated_note() -> None:
    complete = verify_independence(_record())
    without_evidence = verify_independence(
        _record(
            structural_mode=StructuralModeRecord(
                mode=STRUCTURAL_MODE_ISOLATED,
                decision_package_note="Depth not purchased.",
            )
        )
    )
    without_note = verify_independence(_record(structural_mode=_isolated_mode(note="")))
    unrecorded = verify_independence(_record(structural_mode=None))
    unknown_mode = verify_independence(_record(structural_mode=StructuralModeRecord(mode="skim")))

    assert complete.satisfied is True
    assert "structural-depth-not-purchased" in complete.reports
    assert "structural-depth-mutation-evidence-missing" in without_evidence.gaps
    assert "structural-depth-note-missing" in without_note.gaps
    assert "structural-mode-unrecorded" in unrecorded.gaps
    assert "structural-mode-unknown:skim" in unknown_mode.integrity_issues


def test_mutation_evidence_must_bind_its_own_subject() -> None:
    record = StructuralModeRecord(
        mode=STRUCTURAL_MODE_ISOLATED,
        decision_package_note="Depth not purchased.",
    )
    wrong_subject = replace(
        record,
        mutation_evidence=_evidence({"structural_mode": STRUCTURAL_MODE_ISOLATED}),
    )
    tampered = replace(
        record,
        mutation_evidence=EvidenceIntegrity(
            body=record.authority_body(),
            claimed_digest=digest_obj({"structural_mode": "other"}),
        ),
    )

    subject = verify_independence(_record(structural_mode=wrong_subject))
    digest = verify_independence(_record(structural_mode=tampered))

    assert "structural-depth-mutation-evidence-subject-mismatch" in subject.integrity_issues
    assert "structural-depth-mutation-evidence-digest-mismatch" in digest.integrity_issues


def test_record_round_trips_through_dicts() -> None:
    record = _record()

    restored = IndependenceRecord.from_dict(json.loads(json.dumps(record.to_dict())))

    assert restored == record
    assert verify_independence(restored).satisfied is True


def test_new_modules_name_nothing_target_specific() -> None:
    for path in MODULE_PATHS:
        runs = {
            run for run in re.split(r"[^a-z0-9]+", path.read_text(encoding="utf-8").lower()) if run
        }
        assert not [token for token in DENYLIST_TOKENS if token in runs], path
