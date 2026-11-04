"""Planted policy losses must fail independently of the old document's size/ceremonies."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scripts.check_doctrine_sync import PRINCIPLES, RULES, check_repository

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def surface(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "docs/SOFTWARE-FACTORY.md", root / "docs/SOFTWARE-FACTORY.md")
    shutil.copytree(REPO_ROOT / "prompts", root / "prompts")
    assert check_repository(root) == ()
    return root


def replace(root: Path, before: str, after: str) -> None:
    path = root / "docs/SOFTWARE-FACTORY.md"
    source = path.read_text()
    assert before in source, "a mutation that changes no input cannot test this guard"
    path.write_text(source.replace(before, after))


def test_current_repository_doctrine_is_structurally_consistent() -> None:
    assert check_repository(REPO_ROOT) == ()


def test_retained_policy_inventory_cannot_silently_shrink() -> None:
    # Independent policy inventory, not derived from what the checker happens to contain.
    expected = """scope no-manual-signatures attestation-intent
    concerns no-ceremony model-choice construction-authority tester-input
    communication orchestrator no-lag-stop input-isolation scoped-memory self-reference independence
    assignments capability platform-permissions critical-evidence risk-axes side-effects risk-owner
    cited-evidence frozen-review reproduction unavailable-reproduction negative-control
    positive-control
    regression over-constraint determinism test-disposition no-test-weakening monitor-provenance
    monitor-density monitor-authorship truthful-authorship monitor-triage monitor-state
    notifications
    recoverable-evidence model-record honest-model-record independence-record
    coordination no-shell-input
    delivery durability honesty""".split()
    assert {name for name, _ in RULES} == set(expected)
    assert len(RULES) == len(expected)
    assert len(PRINCIPLES) == 8


@pytest.mark.parametrize(("name", "requirement"), RULES, ids=[name for name, _ in RULES])
def test_loss_of_each_retained_rule_is_detected(surface: Path, name: str, requirement: str) -> None:
    replace(surface, requirement, "This requirement was removed.")
    assert f"doctrine-rule-missing:{name}" in check_repository(surface)


@pytest.mark.parametrize("principle", PRINCIPLES)
def test_eight_principles_remain_protected(surface: Path, principle: str) -> None:
    replace(surface, principle, "removed principle")
    assert f"principle-missing:{principle}" in check_repository(surface)


@pytest.mark.parametrize(
    ("role", "filename"),
    [
        ("Validator", "validate.md"),
        ("Orchestrator", "orchestrate.md"),
        ("Coder", "engineer.md"),
        ("Tester", "test.md"),
    ],
)
def test_missing_role_directive_is_detected(surface: Path, role: str, filename: str) -> None:
    (surface / "prompts" / filename).unlink()
    assert f"missing-role-prompt:{role}" in check_repository(surface)


def test_role_name_elsewhere_cannot_hide_lost_role_ownership(surface: Path) -> None:
    replace(surface, "| Tester | Independently", "| Reviewer | Independently")
    assert "role-map-mismatch" in check_repository(surface)


def test_coder_cannot_become_its_own_judge(surface: Path) -> None:
    replace(surface, "Implementation against the task and public contract.", "Accept its own work.")
    assert "role-ownership-mismatch:Coder" in check_repository(surface)


def test_coder_tester_direct_channel_is_detected(surface: Path) -> None:
    replace(surface, "| Validator only |", "| Validator and peer author |")
    errors = check_repository(surface)
    assert "role-channel-mismatch:Coder" in errors
    assert "role-channel-mismatch:Tester" in errors


@pytest.mark.parametrize("role", ["Critical", "Standard", "Cosmetic"])
def test_criticality_class_cannot_disappear(surface: Path, role: str) -> None:
    replace(surface, f"| {role} |", "| Unclassified |")
    assert "criticality-map-mismatch" in check_repository(surface)


def test_critical_missing_evidence_cannot_be_waived(surface: Path) -> None:
    replace(
        surface, "No waiver can substitute for missing critical evidence.", "A waiver is enough."
    )
    assert "critical-gap-must-block" in check_repository(surface)


def test_unknown_consequences_cannot_default_to_cosmetic(surface: Path) -> None:
    replace(
        surface,
        "Unclassified behavior is treated as critical until examined.",
        "Unknown behavior is cosmetic.",
    )
    assert "unclassified-critical-default-missing" in check_repository(surface)


@pytest.mark.parametrize(
    "filename",
    [
        "docs/new.md",
        "prompts/new.md",
        "new.md",
        "factory_runtime/new.py",
        "harness/new.py",
        "scripts/new.py",
    ],
)
def test_new_active_surface_cannot_escape_stale_commitment_scan(
    surface: Path, filename: str
) -> None:
    path = surface / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# The Orchestrator is invoked, not resident.\n")
    assert f"stale-commitment:{filename}:invoked, not resident" in check_repository(surface)


def test_canonical_doc_is_also_scanned_for_contradictory_commitments(surface: Path) -> None:
    path = surface / "docs/SOFTWARE-FACTORY.md"
    path.write_text(path.read_text() + "\nThe Orchestrator is invoked, not resident.\n")
    assert any(
        error.startswith("stale-commitment:docs/SOFTWARE-FACTORY.md:")
        for error in check_repository(surface)
    )


def test_missing_canonical_contract_is_not_green(surface: Path) -> None:
    (surface / "docs/SOFTWARE-FACTORY.md").unlink()
    assert check_repository(surface) == ("missing-canonical:docs/SOFTWARE-FACTORY.md",)
