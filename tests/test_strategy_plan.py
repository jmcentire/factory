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


def test_the_tester_freezes_only_when_every_critical_planned_case_is_traced(
    tmp_path: Path,
) -> None:
    strategy = _strategy(tmp_path, "planned-case: R16.11-a", "planned-case: R7.1 Critical",
                         "- planned-case: N13 Standard", "test-modules: tests/*_test.ts")
    files = tmp_path / "files"
    (files / "tests").mkdir(parents=True)
    test = files / "tests" / "cap_test.ts"
    test.write_text('Deno.test("R16.11-a: request cap", () => {});\n'
                    "// R7.1.2 is a different case\n")
    (files / "spec").mkdir()
    (files / "spec" / "testing-strategy.md").write_text(strategy.read_text())  # not a test
    check = ["traceability", "--strategy", str(strategy), "--files", str(files)]
    assert strategy_plan.main(check) == 70
    test.write_text(test.read_text() + 'Deno.test("R7.1: 413 over the cap", () => {});\n')
    assert strategy_plan.main(check) == 0
    lines = strategy.read_text().splitlines()
    assert strategy_plan.untraced(
        strategy_plan.planned_cases(lines), files, strategy_plan.test_module_globs(lines)
    ) == {"Critical": [], "Standard": ["N13"], "Cosmetic": []}


def test_a_case_named_only_outside_a_test_module_is_not_covered(tmp_path: Path) -> None:
    """Review of #73: a README, an inventory or a comment naming every case is not a test."""

    strategy = _strategy(tmp_path, "planned-case: R7.1", "test-modules: tests/*_test.ts")
    files = tmp_path / "files"
    (files / "tests").mkdir(parents=True)
    (files / "README.md").write_text("Covers R7.1.\n")
    (files / "tests" / "COVERAGE.txt").write_text("R7.1: done\n")
    test = files / "tests" / "cap_test.ts"
    test.write_text("// R7.1: todo\n  # R7.1\nDeno.test(\"request cap\", () => {});\n")
    check = ["traceability", "--strategy", str(strategy), "--files", str(files)]
    assert strategy_plan.main(check) == 70
    test.write_text(test.read_text() + 'Deno.test("R7.1: 413 over the cap", () => {});\n')
    assert strategy_plan.main(check) == 0


def test_planned_cases_without_a_test_module_glob_are_refused(tmp_path: Path) -> None:
    strategy = _strategy(tmp_path, "planned-case: R7.1")
    (tmp_path / "files").mkdir()
    assert strategy_plan.main(["traceability", "--strategy", str(strategy),
                               "--files", str(tmp_path / "files")]) == 70


def test_traceability_is_a_source_reference_and_untested_needs_a_collected_test(
    tmp_path: Path,
) -> None:
    """Review of #73: a constant or trailing comment traces a case but tests nothing."""

    strategy = _strategy(tmp_path, "planned-case: R7.1", "test-modules: tests/*_test.ts")
    files = tmp_path / "files"
    (files / "tests").mkdir(parents=True)
    (files / "tests" / "cap_test.ts").write_text('const planned = "R7.1"; // R7.1\n')
    check = ["traceability", "--strategy", str(strategy), "--files", str(files)]
    assert strategy_plan.main(check) == 0
    cases = strategy_plan.planned_cases(strategy.read_text().splitlines())
    assert strategy_plan.untested(cases, [])["Critical"] == ["R7.1"]
    assert strategy_plan.untested(cases, ["cap > R7.10 is another case"])["Critical"] == ["R7.1"]
    assert strategy_plan.untested(cases, ["cap > R7.1: 413 over the cap"])["Critical"] == []
