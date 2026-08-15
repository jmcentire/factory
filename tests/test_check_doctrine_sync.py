"""Structural doctrine-parity guard tests, including scan sensitivity."""

from __future__ import annotations

import shutil
from pathlib import Path

from scripts.check_doctrine_sync import check_repository

REPO_ROOT = Path(__file__).resolve().parent.parent


def _copy_guard_surface(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for filename in ("README.md", "CLAUDE.md", "AGENTS.md"):
        shutil.copy2(REPO_ROOT / filename, root / filename)
    shutil.copytree(REPO_ROOT / "docs", root / "docs")
    shutil.copytree(REPO_ROOT / "factory_core", root / "factory_core")
    return root


def test_current_repository_doctrine_is_structurally_consistent() -> None:
    assert check_repository(REPO_ROOT) == ()


def test_missing_role_directive_is_detected_structurally(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    doctrine = root / "docs" / "SOFTWARE-FACTORY.md"
    source = doctrine.read_text(encoding="utf-8")
    doctrine.write_text(
        source.replace("## Directive — Tester", "## Tester notes"),
        encoding="utf-8",
    )

    errors = check_repository(root)

    assert any(error.startswith("role-directives-mismatch:") for error in errors)


def test_mutated_role_map_is_detected_even_when_name_remains_elsewhere(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    doctrine = root / "docs" / "SOFTWARE-FACTORY.md"
    source = doctrine.read_text(encoding="utf-8")
    row = "| **Tester** | Validator only | Ratified build input only | The tests | Nothing |"
    doctrine.write_text(
        source.replace(
            row,
            "| **Reviewer** | Validator only | Ratified build input only | The tests | Nothing |",
        ),
        encoding="utf-8",
    )

    errors = check_repository(root)

    assert any(error.startswith("role-map-mismatch:") for error in errors)


def test_mutated_role_channel_is_detected_structurally(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    doctrine = root / "docs" / "SOFTWARE-FACTORY.md"
    source = doctrine.read_text(encoding="utf-8")
    row = (
        "| **Coder** | Validator only | Ratified build input + derived construction IR | "
        "The implementation | Nothing it is judged by |"
    )
    doctrine.write_text(
        source.replace(
            row,
            "| **Coder** | Nobody | Ratified build input + derived construction IR | "
            "The implementation | Nothing it is judged by |",
        ),
        encoding="utf-8",
    )

    errors = check_repository(root)

    assert any(error.startswith("role-channel-map-mismatch:") for error in errors)


def test_old_writer_to_judge_contradiction_cannot_return(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    doctrine = root / "docs" / "SOFTWARE-FACTORY.md"
    source = doctrine.read_text(encoding="utf-8")
    doctrine.write_text(
        source.replace(
            "does not control the judge, cannot negotiate the\n"
            "> verdict, and cannot talk to the Tester.",
            "does not control the judge, and cannot talk to them.",
        ),
        encoding="utf-8",
    )

    errors = check_repository(root)

    assert "core-doctrine-communication-mismatch" in errors


def test_missing_criticality_class_is_detected_structurally(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    doctrine = root / "docs" / "SOFTWARE-FACTORY.md"
    source = doctrine.read_text(encoding="utf-8")
    cosmetic_row = (
        "| **Cosmetic** | Presentation, copy, layout, non-functional display where being wrong "
        "costs an aesthetic defect and nothing else | Best available | Report and promote |"
    )
    doctrine.write_text(source.replace(cosmetic_row, ""), encoding="utf-8")

    errors = check_repository(root)

    assert any(error.startswith("criticality-map-mismatch:") for error in errors)


def test_oracle_criticality_matrix_cannot_silently_drift(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    doctrine = root / "docs" / "SOFTWARE-FACTORY.md"
    source = doctrine.read_text(encoding="utf-8")
    doctrine.write_text(
        source.replace(
            "| **Critical** | Promote after mandatory specialist review | **Block** |",
            "| **Critical** | Promote after mandatory specialist review | Gate |",
        ),
        encoding="utf-8",
    )

    errors = check_repository(root)

    assert "criticality-gate-matrix-mismatch" not in errors
    assert "criticality-critical-gap-must-block" in errors


def test_invariant_artifact_authority_cannot_be_replaced_by_a_ticket(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    doctrine = root / "docs" / "SOFTWARE-FACTORY.md"
    source = doctrine.read_text(encoding="utf-8")
    doctrine.write_text(
        source.replace(
            "> **Nothing outside these three authorizes a requirement.**",
            "> **A ticket may authorize a requirement.**",
        ),
        encoding="utf-8",
    )

    errors = check_repository(root)

    assert any(error.startswith("invariant-document-rule-missing:") for error in errors)


def test_recipe_book_cannot_be_promoted_into_behavioral_authority(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    doctrine = root / "docs" / "SOFTWARE-FACTORY.md"
    source = doctrine.read_text(encoding="utf-8")
    rule = "it may not contain free behavioral authority."
    assert rule in source
    doctrine.write_text(
        source.replace(rule, "it may add behavior when a standard pattern needs it."),
        encoding="utf-8",
    )

    errors = check_repository(root)

    assert any(error.startswith("construction-ir-rule-missing:") for error in errors)


def test_tester_projection_cannot_receive_construction_ir(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    doctrine = root / "docs" / "SOFTWARE-FACTORY.md"
    source = doctrine.read_text(encoding="utf-8")
    rule = "You never read the pattern catalog or build plan."
    assert rule in source
    doctrine.write_text(
        source.replace(rule, "You may inspect the pattern catalog when useful."),
        encoding="utf-8",
    )

    errors = check_repository(root)

    assert "tester-construction-ir-isolation-missing" in errors


def test_missing_tool_tier_is_detected_structurally(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    doctrine = root / "docs" / "SOFTWARE-FACTORY.md"
    source = doctrine.read_text(encoding="utf-8")
    row = (
        "| **Verboten** | Not available | **Absent from the grant.** "
        "Not present and forbidden — not present. |"
    )
    doctrine.write_text(source.replace(row, ""), encoding="utf-8")

    errors = check_repository(root)

    assert any(error.startswith("tool-tier-map-mismatch:") for error in errors)


def test_checklist_item_cannot_be_satisfied_by_recollection(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    doctrine = root / "docs" / "SOFTWARE-FACTORY.md"
    source = doctrine.read_text(encoding="utf-8")
    doctrine.write_text(
        source.replace(
            "an item is satisfied only by cited evidence.",
            "an item may be satisfied by recollection.",
        ),
        encoding="utf-8",
    )

    errors = check_repository(root)

    assert "checklist-cited-evidence-rule-missing" in errors


def test_the_red_guard_rule_cannot_be_softened_back_into_an_expectation(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    doctrine = root / "docs" / "SOFTWARE-FACTORY.md"
    source = doctrine.read_text(encoding="utf-8")
    doctrine.write_text(
        source.replace(
            "**A green guard that comes back red is not a forcing test.**",
            "A green guard that comes back red usually means the spec moved.",
        ),
        encoding="utf-8",
    )

    errors = check_repository(root)

    assert "control-rule-missing:A green guard that comes back red is not a forcing test." in errors


def test_signed_supersession_alone_cannot_authorize_a_test_change(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    doctrine = root / "docs" / "SOFTWARE-FACTORY.md"
    source = doctrine.read_text(encoding="utf-8")
    rule = "firm affirmative human\nruling over its impact"
    assert rule in source
    doctrine.write_text(
        source.replace(rule, "signed superseding item\nwithout a separate impact ruling"),
        encoding="utf-8",
    )

    errors = check_repository(root)

    assert "existing-test-disposition-missing:firm affirmative human ruling" in errors


def test_the_operational_control_names_cannot_be_dropped(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    doctrine = root / "docs" / "SOFTWARE-FACTORY.md"
    source = doctrine.read_text(encoding="utf-8")
    row = (
        "| **Negative** | **red-now** | The spec is not too weak | New tests must **fail** "
        "against current broken main, at least one failing on the defect | The spec did not "
        "catch the bug — rejected |"
    )
    assert row in source
    doctrine.write_text(
        source.replace(
            row,
            "| **Negative** | The spec is not too weak | New tests must **fail** against "
            "current broken main | The spec did not catch the bug — rejected |",
        ),
        encoding="utf-8",
    )

    errors = check_repository(root)

    assert "control-rule-missing:red-now" in errors


def test_the_independence_tier_ladder_cannot_collapse_back_to_a_binary(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    doctrine = root / "docs" / "SOFTWARE-FACTORY.md"
    source = doctrine.read_text(encoding="utf-8")
    row = (
        "| **Moderate** | Same model, no shared context, **no channel** | Tuning to the oracle. "
        "Still shares the frame. |"
    )
    assert row in source
    doctrine.write_text(source.replace(row, ""), encoding="utf-8")

    errors = check_repository(root)

    assert any(error.startswith("independence-tier-map-mismatch:") for error in errors)


def test_a_monitor_may_not_become_authorized_without_resolution(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    doctrine = root / "docs" / "SOFTWARE-FACTORY.md"
    source = doctrine.read_text(encoding="utf-8")
    doctrine.write_text(
        source.replace(
            "**A monitor whose backreference does\nnot resolve is an unauthorized assertion "
            "about production.**",
            "A monitor should usually cite the criterion it watches.",
        ),
        encoding="utf-8",
    )

    errors = check_repository(root)

    assert any(error.startswith("monitor-rule-missing:") for error in errors)


def test_the_triage_silencing_prohibition_cannot_be_removed(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    doctrine = root / "docs" / "SOFTWARE-FACTORY.md"
    source = doctrine.read_text(encoding="utf-8")
    doctrine.write_text(
        source.replace(
            "> **An agent that evaluates an alert may not delete or weaken the monitor that "
            "produced it.**",
            "> An agent that evaluates an alert may tune the monitor that produced it.",
        ),
        encoding="utf-8",
    )

    errors = check_repository(root)

    assert any(error.startswith("triage-rule-missing:") for error in errors)


def test_the_reproduction_requirement_cannot_become_optional(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    doctrine = root / "docs" / "SOFTWARE-FACTORY.md"
    source = doctrine.read_text(encoding="utf-8")
    doctrine.write_text(
        source.replace(
            "**A defect is reproduced in a disposable environment before any repair is written, "
            "and the\nreproduction is recorded.**",
            "A defect is usually reproduced before a repair is written.",
        ),
        encoding="utf-8",
    )

    errors = check_repository(root)

    assert any(error.startswith("reproduction-rule-missing:") for error in errors)


def test_per_agent_model_recording_cannot_leave_the_evidence_plane(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    doctrine = root / "docs" / "SOFTWARE-FACTORY.md"
    source = doctrine.read_text(encoding="utf-8")
    doctrine.write_text(
        source.replace(
            "- **The model and version of every agent that produced or judged the change**, the",
            "- The agents involved, and the",
        ),
        encoding="utf-8",
    )

    errors = check_repository(root)

    assert any(error.startswith("evidence-plane-record-missing:") for error in errors)


def test_new_active_doc_cannot_escape_the_stale_commitment_scan(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    new_surface = root / "docs" / "new-operating-guide.md"
    new_surface.write_text(
        "# New guide\n\nThe seven non-negotiables govern this process.\n",
        encoding="utf-8",
    )

    errors = check_repository(root)

    assert "stale-commitment:docs/new-operating-guide.md:the seven non-negotiables" in errors
