from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from harness.agreement_contract import (
    CONTRACT_SCHEMA as AGREEMENT_CONTRACT_SCHEMA,
)
from harness.agreement_contract import (
    INVENTORY_SCHEMA as AGREEMENT_INVENTORY_SCHEMA,
)
from harness.agreement_contract import derive_regions
from harness.phase_compiler import update as update_phase_artifacts
from harness.phase_compiler import verify as verify_phase_artifacts
from harness.run_guidance import (
    ADMISSION_SCHEMA,
    APPLICATION_SCHEMA,
    CLASSIFICATION_REVIEW_SCHEMA,
    CONTRACT_SCHEMA,
    EVIDENCE_SCHEMA,
    FINDING_RESOLUTION_SCHEMA,
    OBSERVATION_SCHEMA,
    SELECTION_SCHEMA,
    RunGuidanceError,
    admit,
    assessment_state,
    load_plan,
    projection,
    update_artifacts,
    verify_evidence,
    verify_plan,
)
from tests.test_harness_scripts import _write_closed_semantic_union


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def write_canonical(path: Path, value: object) -> str:
    raw = canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return digest(raw)


def reference(root: Path, path: Path) -> dict[str, str]:
    return {"path": path.relative_to(root).as_posix(), "digest": digest(path.read_bytes())}


def selected_documents(tmp_path: Path) -> tuple[Path, dict[str, Path], dict[str, object]]:
    sources = {
        "infra-recipe": tmp_path / "infra-recipe.md",
        "release-loop": tmp_path / "release-loop.md",
        "security-standard": tmp_path / "security-standard.md",
    }
    sources["infra-recipe"].write_text("# Entrypoint recipe\nProvide an executable main.\n")
    sources["release-loop"].write_text("# Release loop\nRe-run package smoke before release.\n")
    sources["security-standard"].write_text("# Security standard\nReject unsigned inputs.\n")
    documents: list[dict[str, object]] = [
        {
            "source_name": "infra-recipe",
            "source_digest": digest(sources["infra-recipe"].read_bytes()),
            "kind": "recipe",
            "obligations": [
                {
                    "obligation_id": "G-ENTRYPOINT",
                    "text": "The build exposes a named main module and executable entrypoint.",
                    "subject_class": "constructional",
                    "classification_basis": (
                        "This constrains assembly shape rather than user-observable behavior."
                    ),
                    "roles": ["validator", "orchestrator", "coder"],
                    "authority_targets": ["architecture", "testing-strategy"],
                }
            ],
        },
        {
            "source_name": "release-loop",
            "source_digest": digest(sources["release-loop"].read_bytes()),
            "kind": "loop",
            "obligations": [
                {
                    "obligation_id": "G-PACKAGE-SMOKE",
                    "text": "Run the clean-install package smoke before release.",
                    "subject_class": "procedural",
                    "classification_basis": (
                        "This constrains the release process and names a checkpoint."
                    ),
                    "roles": ["validator", "orchestrator", "coder"],
                    "authority_targets": ["architecture", "testing-strategy"],
                }
            ],
        },
        {
            "source_name": "security-standard",
            "source_digest": digest(sources["security-standard"].read_bytes()),
            "kind": "standard",
            "obligations": [
                {
                    "obligation_id": "G-UNSIGNED-REFUSAL",
                    "text": "Unsigned inputs are refused before any protected effect.",
                    "subject_class": "behavioral",
                    "classification_basis": (
                        "A caller can observe refusal and absence of the protected effect."
                    ),
                    "roles": ["validator", "orchestrator", "coder", "tester"],
                    "authority_targets": ["product-specification", "testing-strategy"],
                }
            ],
        },
    ]
    selector = {
        "schema_version": SELECTION_SCHEMA,
        "run_id": "guide-r1",
        "generation": 1,
        "documents": documents,
    }
    selector_path = tmp_path / "selection.json"
    write_canonical(selector_path, selector)
    return selector_path, sources, selector


def review_for(
    root: Path,
    selection_digest: str,
    obligation_id: str,
    application_row: dict[str, object],
) -> dict[str, str]:
    path = root / "evidence" / "guidance" / "reviews" / f"{obligation_id}.json"
    write_canonical(
        path,
        {
            "schema_version": CLASSIFICATION_REVIEW_SCHEMA,
            "run_id": "guide-r1",
            "generation": 1,
            "selection_digest": selection_digest,
            "obligation_id": obligation_id,
            "application_subject_digest": digest(canonical(application_row)),
            "reviewer": "independent-reviewer",
            "reviewer_family": "different-model-family",
            "independent_of_validator": True,
            "classification_upheld": True,
            "application_upheld": True,
            "basis": "The class follows the observable subject, and the selected route fits it.",
        },
    )
    return reference(root, path)


def guidance_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    root = tmp_path / "run"
    root.mkdir()
    selector_path, sources, _selector = selected_documents(tmp_path)
    config = [f"factory-run-guidance={selector_path}"] + [
        f"{name}={path}" for name, path in sources.items()
    ]
    config_digests = [
        f"{entry.split('=', 1)[0]}={digest(Path(entry.split('=', 1)[1]).read_bytes())}"
        for entry in config
    ]
    admission = admit(
        root,
        run_id="guide-r1",
        generation=1,
        config_sources=config,
        config_digests=config_digests,
    )
    assert admission["schema_version"] == CONTRACT_SCHEMA
    assert admission["state"] == "pending-application"
    write_canonical(
        root / "harness.json",
        {
            "schema_version": "factory-harness/2",
            "run_id": "guide-r1",
            "validator_agent": "codex",
            "guidance_contract_version": CONTRACT_SCHEMA,
            "guidance_generation": 1,
            "guidance_state": "pending-application",
            "guidance_selection_digest": admission["selection_digest"],
            "guidance_source_digests": admission["source_digests"],
            "agreement_contract_version": "factory-agreement-contract/1",
            "agreement_requirement_region_families": ["authored-product", "run-guidance"],
        },
    )
    artifacts = root / "artifacts"
    (artifacts / "guidance").mkdir(parents=True)
    (artifacts / "product-specification.md").write_text(
        "# Product Specification\n\n- **R1.1** Existing behavior remains available.\n"
    )
    (artifacts / "architecture.md").write_text("# Architecture\n")
    (artifacts / "testing-strategy.md").write_text("# Testing Strategy\n")
    selection_digest = str(admission["selection_digest"])
    applications: list[dict[str, object]] = [
        {
            "obligation_id": "G-ENTRYPOINT",
            "disposition": "applied",
            "basis": "The selected infrastructure recipe applies to this executable package.",
            "acceptance_obligation_ids": [],
            "process_checkpoint_ids": [],
            "construction_requirement_ids": ["build-entrypoint"],
        },
        {
            "obligation_id": "G-PACKAGE-SMOKE",
            "disposition": "applied",
            "basis": "This run publishes an installable package.",
            "acceptance_obligation_ids": [],
            "process_checkpoint_ids": ["pre-release-package-smoke"],
            "construction_requirement_ids": [],
        },
        {
            "obligation_id": "G-UNSIGNED-REFUSAL",
            "disposition": "applied",
            "basis": "The protected input boundary is in the authorized change surface.",
            "acceptance_obligation_ids": ["accept-unsigned-input-refusal"],
            "process_checkpoint_ids": [],
            "construction_requirement_ids": [],
        },
    ]
    for row in applications:
        obligation_id = str(row["obligation_id"])
        row["independent_review"] = review_for(
            root,
            selection_digest,
            obligation_id,
            row,
        )
    application = {
        "schema_version": APPLICATION_SCHEMA,
        "run_id": "guide-r1",
        "generation": 1,
        "selection_digest": selection_digest,
        "obligations": applications,
    }
    write_canonical(artifacts / "guidance" / "application.json", application)
    update_artifacts(root, artifacts)
    return root, artifacts, admission


def test_admission_retains_exact_checkpoint_selected_sources(tmp_path: Path) -> None:
    root, _artifacts, admission = guidance_fixture(tmp_path)
    retained = json.loads((root / "guidance" / "admission.json").read_text())

    assert retained["schema_version"] == ADMISSION_SCHEMA
    assert retained["selection_digest"] == admission["selection_digest"]
    assert [row["source_name"] for row in retained["sources"]] == [
        "infra-recipe",
        "release-loop",
        "security-standard",
    ]
    source = root / retained["sources"][0]["retained_path"]
    source.write_text("substituted bytes\n")
    with pytest.raises(RunGuidanceError, match="retained guidance source changed"):
        load_plan(root, root / "artifacts")


def test_admission_refuses_post_checkpoint_source_substitution(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    selector_path, sources, _selector = selected_documents(tmp_path)
    config = [f"factory-run-guidance={selector_path}"] + [
        f"{name}={path}" for name, path in sources.items()
    ]
    config_digests = [
        f"{entry.split('=', 1)[0]}={digest(Path(entry.split('=', 1)[1]).read_bytes())}"
        for entry in config
    ]
    sources["infra-recipe"].write_text("substituted with a matching-looking recipe\n")

    with pytest.raises(RunGuidanceError, match="changed after resume verification"):
        admit(
            root,
            run_id="guide-r1",
            generation=1,
            config_sources=config,
            config_digests=config_digests,
        )


def test_application_membership_is_exact(tmp_path: Path) -> None:
    root, artifacts, _admission = guidance_fixture(tmp_path)
    application_path = artifacts / "guidance" / "application.json"
    application = json.loads(application_path.read_text())
    application["obligations"].pop()
    write_canonical(application_path, application)

    with pytest.raises(RunGuidanceError, match="membership or order"):
        load_plan(root, artifacts)


def test_application_review_is_bound_to_the_exact_row(tmp_path: Path) -> None:
    root, artifacts, _admission = guidance_fixture(tmp_path)
    application_path = artifacts / "guidance" / "application.json"
    application = json.loads(application_path.read_text())
    application["obligations"][0]["basis"] = "A substituted applicability basis."
    write_canonical(application_path, application)

    with pytest.raises(RunGuidanceError, match="did not uphold the routing"):
        load_plan(root, artifacts)


def test_constructional_obligation_cannot_route_as_behavior(tmp_path: Path) -> None:
    root, artifacts, _admission = guidance_fixture(tmp_path)
    application_path = artifacts / "guidance" / "application.json"
    application = json.loads(application_path.read_text())
    row = application["obligations"][0]
    row["construction_requirement_ids"] = []
    row["acceptance_obligation_ids"] = ["pretend-behavior"]
    write_canonical(application_path, application)

    with pytest.raises(RunGuidanceError, match="architecture conformance requirements"):
        load_plan(root, artifacts)


def test_selection_refuses_nonbehavioral_tester_role(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    selector_path, sources, selector = selected_documents(tmp_path)
    selector["documents"][0]["obligations"][0]["roles"].append("tester")
    write_canonical(selector_path, selector)
    config = [f"factory-run-guidance={selector_path}"] + [
        f"{name}={path}" for name, path in sources.items()
    ]
    config_digests = [
        f"{entry.split('=', 1)[0]}={digest(Path(entry.split('=', 1)[1]).read_bytes())}"
        for entry in config
    ]

    with pytest.raises(RunGuidanceError, match="independent Tester"):
        admit(
            root,
            run_id="guide-r1",
            generation=1,
            config_sources=config,
            config_digests=config_digests,
        )


def test_tester_projection_receives_behavior_not_recipe_or_loop(tmp_path: Path) -> None:
    root, artifacts, _admission = guidance_fixture(tmp_path)
    plan = verify_plan(root, artifacts)
    assert plan is not None

    tester_ids = [row["obligation_id"] for row in projection(plan, "tester")["obligations"]]
    coder_ids = [row["obligation_id"] for row in projection(plan, "coder")["obligations"]]

    assert tester_ids == ["G-UNSIGNED-REFUSAL"]
    assert coder_ids == ["G-ENTRYPOINT", "G-PACKAGE-SMOKE", "G-UNSIGNED-REFUSAL"]


def test_generated_authorities_are_exact_and_do_not_make_recipe_product_intent(
    tmp_path: Path,
) -> None:
    root, artifacts, _admission = guidance_fixture(tmp_path)

    product = (artifacts / "product-specification.md").read_text()
    architecture = (artifacts / "architecture.md").read_text()
    testing = (artifacts / "testing-strategy.md").read_text()
    assert "- **G-UNSIGNED-REFUSAL**" in product
    assert "G-ENTRYPOINT" not in product
    assert "G-ENTRYPOINT" in architecture
    assert "G-UNSIGNED-REFUSAL" not in architecture
    assert "architecture-conformance" in testing

    (artifacts / "architecture.md").write_text(
        architecture.replace("routing-verified", "compliant")
    )
    with pytest.raises(RunGuidanceError, match="differs from fresh derivation"):
        verify_plan(root, artifacts)


def write_complete_evidence(root: Path, artifacts: Path, candidate_sha: str) -> None:
    plan = verify_plan(root, artifacts)
    assert plan is not None
    refs: dict[str, dict[str, str]] = {}
    for obligation_id in plan.selected.obligations:
        raw_path = root / "evidence" / "guidance" / "results" / f"{obligation_id}.txt"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(f"exact candidate {candidate_sha}: {obligation_id} observed\n")
        observation_path = (
            root / "evidence" / "guidance" / "observations" / f"{obligation_id}.json"
        )
        write_canonical(
            observation_path,
            {
                "schema_version": OBSERVATION_SCHEMA,
                "run_id": "guide-r1",
                "generation": 1,
                "selection_digest": plan.selected.selection_digest,
                "application_digest": plan.application_digest,
                "obligation_id": obligation_id,
                "candidate_sha": candidate_sha,
                "verifier": "validator-codex",
                "method": (
                    "inspection" if obligation_id == "G-ENTRYPOINT" else "test"
                ),
                "passed": True,
                "basis": "The retained raw output demonstrates the named obligation.",
                "raw_evidence": [reference(root, raw_path)],
            },
        )
        refs[obligation_id] = reference(root, observation_path)
    write_canonical(
        artifacts / "guidance" / "evidence.json",
        {
            "schema_version": EVIDENCE_SCHEMA,
            "run_id": "guide-r1",
            "generation": 1,
            "selection_digest": plan.selected.selection_digest,
            "application_digest": plan.application_digest,
            "candidate_sha": candidate_sha,
            "results": [
                {
                    "obligation_id": "G-ENTRYPOINT",
                    "status": "evidence-complete",
                    "closure_class": "refused",
                    "basis": "The exact-candidate entrypoint conformance check passed.",
                    "evidence": [refs["G-ENTRYPOINT"]],
                },
                {
                    "obligation_id": "G-PACKAGE-SMOKE",
                    "status": "evidence-complete",
                    "closure_class": "refused",
                    "basis": "The exact clean-install package checkpoint passed.",
                    "evidence": [refs["G-PACKAGE-SMOKE"]],
                },
                {
                    "obligation_id": "G-UNSIGNED-REFUSAL",
                    "status": "evidence-complete",
                    "closure_class": "refused",
                    "basis": "The exact behavioral acceptance obligation refused the input.",
                    "evidence": [refs["G-UNSIGNED-REFUSAL"]],
                },
            ],
            "findings": [],
        },
    )


def test_evidence_membership_drives_state_and_binds_candidate(tmp_path: Path) -> None:
    root, artifacts, _admission = guidance_fixture(tmp_path)
    candidate = "a" * 40
    assert assessment_state(root, artifacts)["state"] == "routing-verified"
    write_complete_evidence(root, artifacts, candidate)

    evidence = verify_evidence(root, artifacts, candidate_sha=candidate)

    assert evidence is not None
    assert assessment_state(root, artifacts)["state"] == "evidence-complete"
    with pytest.raises(RunGuidanceError, match="stale or belongs"):
        verify_evidence(root, artifacts, candidate_sha="b" * 40)


def test_missing_applied_evidence_cannot_claim_complete(tmp_path: Path) -> None:
    root, artifacts, _admission = guidance_fixture(tmp_path)
    candidate = "a" * 40
    write_complete_evidence(root, artifacts, candidate)
    evidence_path = artifacts / "guidance" / "evidence.json"
    evidence = json.loads(evidence_path.read_text())
    evidence["results"][1]["evidence"] = []
    write_canonical(evidence_path, evidence)

    with pytest.raises(RunGuidanceError, match="wrong evidence membership"):
        verify_evidence(root, artifacts, candidate_sha=candidate)
    assert assessment_state(root, artifacts)["state"] == "noncompliant"


def test_observation_cannot_be_reused_for_another_candidate(tmp_path: Path) -> None:
    root, artifacts, _admission = guidance_fixture(tmp_path)
    candidate = "a" * 40
    write_complete_evidence(root, artifacts, candidate)
    evidence_path = artifacts / "guidance" / "evidence.json"
    evidence = json.loads(evidence_path.read_text())
    evidence["candidate_sha"] = "b" * 40
    write_canonical(evidence_path, evidence)

    with pytest.raises(RunGuidanceError, match="belongs to another subject"):
        verify_evidence(root, artifacts, candidate_sha="b" * 40)


def test_finding_resolution_cannot_be_reused_for_another_candidate(
    tmp_path: Path,
) -> None:
    root, artifacts, _admission = guidance_fixture(tmp_path)
    first_candidate = "a" * 40
    second_candidate = "b" * 40
    write_complete_evidence(root, artifacts, first_candidate)
    plan = verify_plan(root, artifacts)
    assert plan is not None
    raw_path = root / "evidence" / "guidance" / "results" / "resolved-finding.txt"
    raw_path.write_text("The compliance defect no longer reproduces.\n", encoding="utf-8")
    resolution_path = (
        root / "evidence" / "guidance" / "resolutions" / "GUIDE-FINDING-1.json"
    )
    write_canonical(
        resolution_path,
        {
            "schema_version": FINDING_RESOLUTION_SCHEMA,
            "run_id": "guide-r1",
            "generation": 1,
            "selection_digest": plan.selected.selection_digest,
            "application_digest": plan.application_digest,
            "finding_id": "GUIDE-FINDING-1",
            "candidate_sha": first_candidate,
            "verifier": "validator-codex",
            "method": "test",
            "resolved": True,
            "basis": "The exact-candidate regression no longer reproduces.",
            "raw_evidence": [reference(root, raw_path)],
        },
    )
    evidence_path = artifacts / "guidance" / "evidence.json"
    evidence = json.loads(evidence_path.read_text())
    evidence["findings"] = [
        {
            "finding_id": "GUIDE-FINDING-1",
            "severity": "standard",
            "status": "resolved",
            "basis": "The selected standard was initially violated.",
            "resolution_evidence": reference(root, resolution_path),
        }
    ]
    write_canonical(evidence_path, evidence)
    assert verify_evidence(root, artifacts, candidate_sha=first_candidate) is not None

    evidence["candidate_sha"] = second_candidate
    for result in evidence["results"]:
        observation_reference = result["evidence"][0]
        observation_path = root / observation_reference["path"]
        observation = json.loads(observation_path.read_text())
        observation["candidate_sha"] = second_candidate
        write_canonical(observation_path, observation)
        result["evidence"][0] = reference(root, observation_path)
    write_canonical(evidence_path, evidence)

    with pytest.raises(RunGuidanceError, match="resolution is stale"):
        verify_evidence(root, artifacts, candidate_sha=second_candidate)


def test_no_selection_is_an_explicit_none_path(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    result = admit(
        root,
        run_id="guide-r1",
        generation=1,
        config_sources=[],
        config_digests=[],
    )
    write_canonical(
        root / "harness.json",
        {
            "schema_version": "factory-harness/2",
            "run_id": "guide-r1",
            "guidance_contract_version": CONTRACT_SCHEMA,
            "guidance_generation": 1,
            "guidance_state": "none",
            "guidance_selection_digest": None,
            "guidance_source_digests": {},
        },
    )

    assert result["state"] == "none"
    assert assessment_state(root, root / "artifacts")["state"] == "none"


def test_phase_compiler_reorders_and_verifies_semantics_guidance_then_agreement(
    tmp_path: Path,
) -> None:
    root, artifacts, _admission = guidance_fixture(tmp_path)
    spec = artifacts / "product-specification.md"
    _write_closed_semantic_union(artifacts, spec)
    assert spec.read_text().index("FACTORY-RUN-GUIDANCE:BEGIN") < spec.read_text().index(
        "FACTORY-SEMANTIC-UNION:BEGIN"
    )
    regions = derive_regions(spec, ["authored-product", "run-guidance"])
    inventory_source = root / "evidence" / "agreement" / "participant-inventory.txt"
    inventory_source.parent.mkdir(parents=True, exist_ok=True)
    inventory_source.write_text("generated route inventory\n")
    inventory_reference = reference(root, inventory_source)
    inventory = {
        "schema_version": AGREEMENT_INVENTORY_SCHEMA,
        "run_id": "guide-r1",
        "requirement_regions": [
            {
                "family": region.family,
                "region_digest": region.region_digest,
                "requirements": [
                    {
                        "requirement_id": requirement_id,
                        "criticality": "critical",
                        "derivation": "mechanical",
                        "derivation_evidence": inventory_reference,
                        "basis": (
                            "Generated route inventory retained at "
                            f"{inventory_reference['digest']}."
                        ),
                        "limitations": [],
                        "participants": [f"path-{requirement_id.lower().replace('.', '-')}"] ,
                    }
                    for requirement_id, _body in region.requirements
                ],
            }
            for region in regions
        ],
    }
    inventory_digest = write_canonical(
        artifacts / "agreement" / "participant-inventory.json", inventory
    )
    entries = [
        {
            "requirement_id": requirement_id,
            "single_path_basis": (
                f"Mechanical inventory {inventory_reference['digest']} names one participant."
            ),
            "shared_authority": None,
            "semantic_residue": None,
            "agreement_oracle": None,
            "producer_mismatch": None,
            "consumer_mismatch": None,
            "axes": [],
        }
        for region in regions
        for requirement_id, _body in region.requirements
    ]
    write_canonical(
        artifacts / "agreement" / "contract.json",
        {
            "schema_version": AGREEMENT_CONTRACT_SCHEMA,
            "run_id": "guide-r1",
            "participant_inventory_digest": inventory_digest,
            "entries": entries,
        },
    )

    update_phase_artifacts(root, artifacts)
    verify_phase_artifacts(root, artifacts)

    product = spec.read_text()
    testing = (artifacts / "testing-strategy.md").read_text()
    assert product.index("FACTORY-SEMANTIC-UNION:BEGIN") < product.index(
        "FACTORY-RUN-GUIDANCE:BEGIN"
    )
    assert testing.index("FACTORY-RUN-GUIDANCE-TESTING:BEGIN") < testing.index(
        "FACTORY-AGREEMENT-CONTRACT:BEGIN"
    )
