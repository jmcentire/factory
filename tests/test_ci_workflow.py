from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"


def test_factory_ci_checkouts_fetch_acceptance_tags() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    factory_checkouts = text.split("      - name: Check out Factory\n")[1:]

    assert len(factory_checkouts) == 2
    for remainder in factory_checkouts:
        checkout = remainder.split("\n      - name:", 1)[0]
        assert "          fetch-depth: 0\n" in checkout
        assert "          fetch-tags: true\n" in checkout
