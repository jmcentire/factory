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
    row = "| **Tester** | Validator only | The spec | The tests | Nothing |"
    doctrine.write_text(
        source.replace(
            row,
            "| **Reviewer** | Validator only | The spec | The tests | Nothing |",
        ),
        encoding="utf-8",
    )

    errors = check_repository(root)

    assert any(error.startswith("role-map-mismatch:") for error in errors)


def test_mutated_role_channel_is_detected_structurally(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    doctrine = root / "docs" / "SOFTWARE-FACTORY.md"
    source = doctrine.read_text(encoding="utf-8")
    row = "| **Coder** | Validator only | The spec | The implementation | Nothing it is judged by |"
    doctrine.write_text(
        source.replace(
            row,
            "| **Coder** | Nobody | The spec | The implementation | Nothing it is judged by |",
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


def test_new_active_doc_cannot_escape_the_stale_commitment_scan(tmp_path: Path) -> None:
    root = _copy_guard_surface(tmp_path)
    new_surface = root / "docs" / "new-operating-guide.md"
    new_surface.write_text(
        "# New guide\n\nThe seven non-negotiables govern this process.\n",
        encoding="utf-8",
    )

    errors = check_repository(root)

    assert (
        "stale-commitment:docs/new-operating-guide.md:the seven non-negotiables" in errors
    )
