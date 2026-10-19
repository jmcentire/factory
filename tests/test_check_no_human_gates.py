"""The ship gate that keeps false human gates out of the factory (P-0001, founder 2026-10-18)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_no_human_gates import REPO_ROOT, scan, scan_text

GATE = REPO_ROOT / "scripts" / "check_no_human_gates.py"


@pytest.mark.parametrize(
    "planted",
    [
        "HALT stands until a human clears it and re-seats the Validator.",
        "Only the human clears the stop.",
        "Ask the founder for approval before you merge.",
        "The Validator waits for the founder to ratify every row.",
        "Page the human when a seat sits on a dialog.",
        "Reply: just type go and I will continue.",
        "This step requires founder approval.",
        "A green-now failure routes to the human.",
        "Promotion needs a human sign-off.",
        "The Orchestrator may halt the Validator.",
        "This is a founder-only step.",
    ],
)
def test_the_gate_fires_on_a_planted_human_gate(tmp_path: Path, planted: str) -> None:
    """The founder had demanded these gone before, and prose did not hold. A planted false
    gate in any scanned directory fails the build, with the founder's words in the message."""
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "planted.md").write_text(f"# Planted\n\n{planted}\n", encoding="utf-8")
    assert [finding.line for finding in scan(tmp_path)] == [3]
    finished = subprocess.run(
        [sys.executable, str(GATE), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert finished.returncode == 1
    assert "prompts/planted.md:3" in finished.stderr
    assert "P-0001" in finished.stderr and "I ALREADY FUCKING ASKED YOU TO DO IT" in finished.stderr


@pytest.mark.parametrize(
    "rule",
    [
        "Nothing waits on the founder.",
        "The Validator never asks the founder to sign in, click, run a command, or type go.",
        "A block never waits on the founder.",
        "Only a genuine impasse is sent to the founder.  <!-- gate-exempt: the impasse rule -->",
    ],
)
def test_the_rule_itself_is_not_a_gate(rule: str) -> None:
    """A phrase stated as what never happens, or a reasoned exemption, is the rule."""
    assert scan_text("prompts/rule.md", rule + "\n") == []


def test_archival_records_and_the_product_paging_standard_are_not_scanned(tmp_path: Path) -> None:
    for relative in ("docs/proposals/old.md", "docs/practices/incident-response.md"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Page the human on a SEV1.\n", encoding="utf-8")
    assert scan(tmp_path) == []


def test_the_factory_carries_no_human_gate() -> None:
    findings = scan()
    assert findings == [], "\n".join(finding.render() for finding in findings)
