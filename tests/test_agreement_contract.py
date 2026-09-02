from __future__ import annotations

import difflib
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness.agreement_contract import (
    AXES,
    CONTRACT_SCHEMA,
    EVIDENCE_SCHEMA,
    INVENTORY_SCHEMA,
    AgreementContractError,
    derive_regions,
    update_strategy,
    verify_evidence,
    verify_plan,
)

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "harness" / "agreement_probe.py"


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def write_canonical(path: Path, value: object) -> str:
    raw = canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return digest(raw)


def cross_entry(requirement_id: str) -> dict[str, object]:
    return {
        "requirement_id": requirement_id,
        "single_path_basis": None,
        "shared_authority": "The paths consume one signed decision record.",
        "semantic_residue": "Each path can still interpret the decision differently.",
        "agreement_oracle": "Exercise both paths against the same real boundary and compare.",
        "producer_mismatch": "Perturb the producer projection while retaining consumer behavior.",
        "consumer_mismatch": "Perturb the consumer projection while retaining producer behavior.",
        "axes": [
            {
                "axis": axis,
                "disposition": "not-applicable",
                "basis": f"The fixture has no independent {axis} state.",
                "plan": None,
            }
            for axis in AXES
        ],
    }


def agreement_fixture(
    tmp_path: Path,
    requirements: list[tuple[str, list[str], str]],
) -> tuple[Path, Path]:
    root = tmp_path / "run"
    artifacts = root / "artifacts"
    agreement = artifacts / "agreement"
    agreement.mkdir(parents=True)
    spec_lines = ["# Product Specification", ""]
    for requirement_id, _participants, description in requirements:
        spec_lines.extend([f"- **{requirement_id}** {description}", ""])
    (artifacts / "product-specification.md").write_text("\n".join(spec_lines))
    (artifacts / "testing-strategy.md").write_text("# Testing Strategy\n")
    write_canonical(
        root / "harness.json",
        {
            "schema_version": "factory-harness/2",
            "run_id": "res-r1",
            "agreement_contract_version": CONTRACT_SCHEMA,
            "agreement_requirement_region_families": ["authored-product"],
        },
    )
    regions = derive_regions(artifacts / "product-specification.md", ["authored-product"])
    evidence = root / "evidence" / "agreement" / "participant-inventory.txt"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("mechanical route and call-site inventory\n")
    evidence_ref = {
        "path": evidence.relative_to(root).as_posix(),
        "digest": digest(evidence.read_bytes()),
    }
    inventory = {
        "schema_version": INVENTORY_SCHEMA,
        "run_id": "res-r1",
        "requirement_regions": [
            {
                "family": "authored-product",
                "region_digest": regions[0].region_digest,
                "requirements": [
                    {
                        "requirement_id": requirement_id,
                        "criticality": "critical",
                        "derivation": "mechanical",
                        "derivation_evidence": evidence_ref,
                        "basis": (
                            f"Route/call-site enumeration retained as {evidence_ref['digest']}."
                        ),
                        "limitations": [],
                        "participants": participants,
                    }
                    for requirement_id, participants, _description in requirements
                ],
            }
        ],
    }
    inventory_digest = write_canonical(agreement / "participant-inventory.json", inventory)
    entries = []
    for requirement_id, participants, _description in requirements:
        if len(participants) >= 2:
            entries.append(cross_entry(requirement_id))
        else:
            entries.append(
                {
                    "requirement_id": requirement_id,
                    "single_path_basis": (
                        f"The mechanical inventory {evidence_ref['digest']} names one participant."
                    ),
                    "shared_authority": None,
                    "semantic_residue": None,
                    "agreement_oracle": None,
                    "producer_mismatch": None,
                    "consumer_mismatch": None,
                    "axes": [],
                }
            )
    write_canonical(
        agreement / "contract.json",
        {
            "schema_version": CONTRACT_SCHEMA,
            "run_id": "res-r1",
            "participant_inventory_digest": inventory_digest,
            "entries": entries,
        },
    )
    update_strategy(root, artifacts)
    return root, artifacts


def test_res_r1_forcing_fixture_closes_each_observed_relationship(tmp_path: Path) -> None:
    root, artifacts = agreement_fixture(
        tmp_path,
        [
            ("R1.1", ["hold", "quote"], "Quote and hold use one availability decision."),
            (
                "R2.1",
                ["change-guest", "create-contact", "erase"],
                "Storage, update, and erasure use one guest identity.",
            ),
            (
                "R3.1",
                ["confirm", "expired-metric", "release"],
                "Release, confirmation, and expiry telemetry share one lifecycle.",
            ),
        ],
    )

    plan = verify_plan(root, artifacts)

    assert plan is not None
    assert list(plan.contract_entries) == ["R1.1", "R2.1", "R3.1"]
    assert all(len(item["participants"]) >= 2 for item in plan.inventory_items.values())
    rendered = (artifacts / "testing-strategy.md").read_text()
    assert rendered.count("`cross-path`") == 3
    assert "hold" in rendered and "quote" in rendered


def test_participant_removal_stales_the_inventory(tmp_path: Path) -> None:
    root, artifacts = agreement_fixture(
        tmp_path,
        [("R1.1", ["hold", "quote"], "Quote and hold agree.")],
    )
    inventory_path = artifacts / "agreement" / "participant-inventory.json"
    inventory = json.loads(inventory_path.read_text())
    inventory["requirement_regions"][0]["requirements"][0]["participants"] = ["quote"]
    write_canonical(inventory_path, inventory)

    with pytest.raises(AgreementContractError, match="stale participant inventory"):
        verify_plan(root, artifacts)


def test_multiple_participants_cannot_be_declared_single_path(tmp_path: Path) -> None:
    root, artifacts = agreement_fixture(
        tmp_path,
        [("R1.1", ["hold", "quote"], "Quote and hold agree.")],
    )
    contract_path = artifacts / "agreement" / "contract.json"
    contract = json.loads(contract_path.read_text())
    entry = contract["entries"][0]
    entry["single_path_basis"] = "Treat the paths separately."
    for key in (
        "shared_authority",
        "semantic_residue",
        "agreement_oracle",
        "producer_mismatch",
        "consumer_mismatch",
    ):
        entry[key] = None
    entry["axes"] = []
    write_canonical(contract_path, contract)

    with pytest.raises(AgreementContractError, match="cannot be downgraded"):
        verify_plan(root, artifacts)


def test_critical_requirement_refuses_bounded_manual_inventory(tmp_path: Path) -> None:
    root, artifacts = agreement_fixture(
        tmp_path,
        [("R1.1", ["hold", "quote"], "Quote and hold agree.")],
    )
    inventory_path = artifacts / "agreement" / "participant-inventory.json"
    inventory = json.loads(inventory_path.read_text())
    item = inventory["requirement_regions"][0]["requirements"][0]
    item["derivation"] = "bounded-manual"
    item["limitations"] = ["No generated call graph was available."]
    write_canonical(inventory_path, inventory)

    with pytest.raises(AgreementContractError, match="cannot clear Critical"):
        verify_plan(root, artifacts)


def test_absent_ignition_field_preserves_legacy_run_semantics(tmp_path: Path) -> None:
    root = tmp_path / "run"
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True)
    write_canonical(root / "harness.json", {"schema_version": "factory-harness/2", "run_id": "old"})

    assert verify_plan(root, artifacts) is None


def create_probe_candidate(tmp_path: Path) -> tuple[Path, str]:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=candidate, check=True)
    (candidate / "impl.py").write_text(
        "def quote(value):\n    return value\n\ndef hold(value):\n    return value\n"
    )
    (candidate / "local_test.py").write_text(
        "from impl import hold, quote\n"
        "assert isinstance(quote(1), int)\n"
        "assert isinstance(hold(1), int)\n"
    )
    (candidate / "agreement_test.py").write_text(
        "from impl import hold, quote\nassert quote(1) == hold(1)\n"
    )
    subprocess.run(["git", "add", "."], cwd=candidate, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "candidate",
        ],
        cwd=candidate,
        check=True,
    )
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=candidate, text=True).strip()
    return candidate, sha


def run_probe(
    tmp_path: Path,
    root: Path,
    candidate: Path,
    sha: str,
    direction: str,
) -> dict[str, object]:
    local_command = tmp_path / "local-command.json"
    agreement_command = tmp_path / "agreement-command.json"
    write_canonical(local_command, [sys.executable, "local_test.py"])
    write_canonical(agreement_command, [sys.executable, "agreement_test.py"])
    function = "quote" if direction == "producer" else "hold"
    increment = "1" if direction == "producer" else "2"
    patch = tmp_path / f"{direction}.patch"
    original = (candidate / "impl.py").read_text()
    changed = original.replace(
        f"def {function}(value):\n    return value",
        f"def {function}(value):\n    return value + {increment}",
    )
    patch.write_text(
        "diff --git a/impl.py b/impl.py\n"
        + "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                changed.splitlines(keepends=True),
                fromfile="a/impl.py",
                tofile="b/impl.py",
            )
        )
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(PROBE),
            "--root",
            str(root),
            "--run-id",
            "res-r1",
            "--requirement-id",
            "R1.1",
            "--direction",
            direction,
            "--candidate",
            str(candidate),
            "--candidate-sha",
            sha,
            "--mutation-patch",
            str(patch),
            "--local-command",
            str(local_command),
            "--agreement-command",
            str(agreement_command),
            "--local-suite",
            "local_test.py",
            "--agreement-oracle",
            "agreement_test.py",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_two_direction_probe_evidence_binds_candidate_and_oracles(tmp_path: Path) -> None:
    root, artifacts = agreement_fixture(
        tmp_path,
        [("R1.1", ["hold", "quote"], "Quote and hold agree.")],
    )
    candidate, sha = create_probe_candidate(tmp_path)
    producer = run_probe(tmp_path, root, candidate, sha, "producer")
    consumer = run_probe(tmp_path, root, candidate, sha, "consumer")
    assert producer["local_suite_digest"] == consumer["local_suite_digest"]
    assert producer["agreement_oracle_digest"] == consumer["agreement_oracle_digest"]
    plan = verify_plan(root, artifacts)
    assert plan is not None
    write_canonical(
        artifacts / "agreement" / "evidence.json",
        {
            "schema_version": EVIDENCE_SCHEMA,
            "run_id": "res-r1",
            "agreement_contract_digest": plan.contract_digest,
            "candidate_sha": sha,
            "results": [
                {
                    "requirement_id": "R1.1",
                    "disposition": "witnessed",
                    "closure_class": "refused",
                    "local_suite_digest": producer["local_suite_digest"],
                    "agreement_oracle_digest": producer["agreement_oracle_digest"],
                    "producer_witness": producer["witness"],
                    "consumer_witness": consumer["witness"],
                    "independent_review": None,
                }
            ],
        },
    )

    assert verify_evidence(root, artifacts, candidate_sha=sha) is not None
    with pytest.raises(AgreementContractError, match="stale or belongs"):
        verify_evidence(root, artifacts, candidate_sha="f" * 40)
