from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"
MAKEFILE = Path(__file__).parents[1] / "Makefile"


def test_factory_ci_checkouts_fetch_acceptance_tags() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    factory_checkouts = text.split("      - name: Check out Factory\n")[1:]

    assert len(factory_checkouts) == 2
    for remainder in factory_checkouts:
        checkout = remainder.split("\n      - name:", 1)[0]
        assert "          fetch-depth: 0\n" in checkout
        assert "          fetch-tags: true\n" in checkout


def test_both_ci_platforms_run_every_tessera_gated_test_file() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert workflow.count("        run: make test-tessera\n") == 2

    makefile = MAKEFILE.read_text(encoding="utf-8")
    target = makefile.split("test-tessera:", 1)[1].split("\nlint:", 1)[0]
    assert {
        "tests/test_tessera_cli_integration.py",
        "tests/test_build_and_validate_cli.py",
        "tests/test_repair_ceremony.py",
        "tests/test_trust_root.py",
    } <= set(target.split())
