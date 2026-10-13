"""The Tester lane is checked against the agreed Testing Strategy (dogfood #36, #65, #73)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from harness import strategy_plan

UPSTREAM = b"route: /site\n# keep this comment exactly\n"
DIGEST = hashlib.sha256(UPSTREAM).hexdigest()


def _strategy(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "testing-strategy.md"
    path.write_text("# Testing strategy\n" + "".join(line + "\n" for line in lines))
    return path


def test_a_cited_oracle_input_must_be_in_the_testers_tree_as_its_pinned_bytes(
    tmp_path: Path,
) -> None:
    strategy = _strategy(tmp_path, f"- oracle-input: tools/gateway/site-route.yaml sha256:{DIGEST}")
    repo = tmp_path / "tester"
    (repo / "tools" / "gateway").mkdir(parents=True)
    check = ["oracle-inputs", "--strategy", str(strategy), "--repo", str(repo)]
    assert strategy_plan.main(check) == 70  # never projected (#36)
    copy = repo / "tools" / "gateway" / "site-route.yaml"
    copy.write_bytes(b"route: /site\n# keep this comment  exactly\n")
    assert strategy_plan.main(check) == 70  # a formatter's copy (#65)
    copy.write_bytes(UPSTREAM)
    assert strategy_plan.main(check) == 0


def test_an_oracle_input_reached_through_a_linked_ancestor_is_refused(tmp_path: Path) -> None:
    """Review of #36/#65: a linked directory on the path escapes the Tester's projection even
    when the bytes outside currently match."""

    strategy = _strategy(tmp_path, f"oracle-input: tools/gateway/site-route.yaml sha256:{DIGEST}")
    outside = tmp_path / "outside" / "gateway"
    outside.mkdir(parents=True)
    (outside / "site-route.yaml").write_bytes(UPSTREAM)
    repo = tmp_path / "tester"
    repo.mkdir()
    (repo / "tools").symlink_to(tmp_path / "outside")
    check = ["oracle-inputs", "--strategy", str(strategy), "--repo", str(repo)]
    assert strategy_plan.main(check) == 70
    (repo / "tools").unlink()
    (repo / "tools" / "gateway").mkdir(parents=True)
    (repo / "tools" / "gateway" / "site-route.yaml").symlink_to(outside / "site-route.yaml")
    assert strategy_plan.main(check) == 70


def test_a_malformed_binding_line_is_refused_not_skipped(tmp_path: Path) -> None:
    strategy = _strategy(tmp_path, "oracle-input: tools/gateway/site-route.yaml")
    assert strategy_plan.main(["oracle-inputs", "--strategy", str(strategy),
                               "--repo", str(tmp_path)]) == 70


def test_the_tester_freezes_only_when_every_critical_planned_case_has_a_test(
    tmp_path: Path,
) -> None:
    strategy = _strategy(tmp_path, "planned-case: R16.11-a", "planned-case: R7.1 Critical",
                         "- planned-case: N13 Standard")
    files = tmp_path / "files"
    (files / "tests").mkdir(parents=True)
    test = files / "tests" / "cap_test.ts"
    test.write_text('Deno.test("R16.11-a: request cap", () => {});\n'
                    "// R7.1.2 is a different case\n")
    (files / "spec").mkdir()
    (files / "spec" / "testing-strategy.md").write_text(strategy.read_text())  # not a test
    check = ["coverage", "--strategy", str(strategy), "--files", str(files)]
    assert strategy_plan.main(check) == 70
    test.write_text(test.read_text() + 'Deno.test("R7.1: 413 over the cap", () => {});\n')
    assert strategy_plan.main(check) == 0
    assert strategy_plan.uncovered(
        strategy_plan.planned_cases(strategy.read_text().splitlines()), files
    ) == {"Critical": [], "Standard": ["N13"], "Cosmetic": []}
