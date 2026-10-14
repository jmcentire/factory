"""The integrated tree's runner must list every test module the Tester froze (dogfood #42)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from harness import discovery_check, strategy_plan

HARNESS = Path(__file__).resolve().parents[1] / "harness"


def _frozen_tester(tmp_path: Path) -> Path:
    files = tmp_path / "snapshot" / "files"
    (files / "tests").mkdir(parents=True)
    (files / "tests" / "export_test.ts").write_text("// R3.1\n")
    (files / "contracts").mkdir()
    (files / "contracts" / "api.yaml").write_text("openapi: 3.1.0\n")
    root = tmp_path / "run"
    (root / "tmux-lanes").mkdir(parents=True)
    rows = [{"status": "active", "role": "tester"},
            {"schema_version": discovery_check.FREEZE_SCHEMA,
             "export": {"files_directory": str(files)}}]
    (root / "tmux-lanes" / "tester-launch.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows))
    return root


def _strategy(tmp_path: Path, *lines: str) -> Path:
    strategy = tmp_path / "testing-strategy.md"
    strategy.write_text("".join(line + "\n" for line in lines))
    return strategy


def _inventory(*tests: tuple[str, str]) -> str:
    return json.dumps({"schema_version": discovery_check.INVENTORY_SCHEMA,
                       "tests": [{"module": module, "name": name} for module, name in tests]})


def _check(root: Path, tree: Path, stdout: str, pattern: str = "tests/*_test.ts",
           stderr: str = "", status: int = 0, strategy: Path | None = None) -> int:
    (tree / ".out").write_text(stdout)
    (tree / ".err").write_text(stderr)
    strategy = strategy or _strategy(tree, "test-modules: tests/*_test.ts")
    return discovery_check.main([
        "--root", str(root), "--tree", str(tree), "--pattern", pattern,
        "--strategy", str(strategy), "--", "sh", "-c", f"cat .out; cat .err >&2; exit {status}"])


def test_a_tree_whose_runner_misses_the_testers_modules_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, tree = _frozen_tester(tmp_path), tmp_path / "integrated"
    tree.mkdir()
    assert _check(root, tree, _inventory()) == 70
    assert "tests/export_test.ts" in capsys.readouterr().err
    assert _check(root, tree, _inventory(("tests/export_test.ts", "exports a CSV"))) == 0
    assert json.loads(capsys.readouterr().out)["modules"] == ["tests/export_test.ts"]


def test_console_lines_and_failing_runners_are_not_an_inventory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review of #42: only a documented inventory counts, never a line that names a module."""

    root, tree = _frozen_tester(tmp_path), tmp_path / "integrated"
    tree.mkdir()
    for listing in ("tests/export_test.ts", "Check tests/export_test.ts",
                    "Skipping tests/export_test.ts"):
        assert _check(root, tree, listing) == 70
        assert _check(root, tree, "", stderr=listing) == 70
    assert "did not print one factory-test-inventory/1" in capsys.readouterr().err
    found = _inventory(("tests/export_test.ts", "exports a CSV"))
    assert _check(root, tree, found, status=1) == 70
    assert "exited 1" in capsys.readouterr().err
    assert _check(root, tree, found.replace("factory-test-inventory/1", "other/1")) == 70
    assert _check(root, tree, found.replace('"name"', '"title"')) == 70


def test_the_check_cannot_pass_vacuously_or_before_freeze(tmp_path: Path) -> None:
    root, tree = _frozen_tester(tmp_path), tmp_path / "integrated"
    tree.mkdir()
    found = _inventory(("tests/export_test.ts", "exports a CSV"))
    assert _check(root, tree, found, pattern="spec/*_test.ts") == 70
    journal = root / "tmux-lanes" / "tester-launch.jsonl"
    journal.write_text(journal.read_text() + json.dumps(
        {"status": "active", "role": "tester"}) + "\n")
    assert _check(root, tree, found) == 70


def test_a_critical_planned_case_with_no_collected_test_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review of #73: a module that only mentions a case id (a constant, a trailing comment)
    passes traceability but has no collected test carrying the id, so it is refused here.
    The inventory comes from real pytest through test_inventory.py."""

    tree = tmp_path / "integrated"
    (tree / "tests").mkdir(parents=True)
    module = tree / "tests" / "test_cap.py"
    module.write_text('planned = "R7.1"  # R7.1\n\n\ndef test_unrelated():\n    pass\n')
    root = tmp_path / "run"
    (root / "tmux-lanes").mkdir(parents=True)
    (root / "tmux-lanes" / "tester-launch.jsonl").write_text(json.dumps(
        {"schema_version": discovery_check.FREEZE_SCHEMA,
         "export": {"files_directory": str(tree)}}) + "\n")
    strategy = _strategy(tmp_path, "planned-case: R7.1", "planned-case: N2 Standard",
                         "test-modules: tests/test_*.py")
    lines = strategy.read_text().splitlines()
    assert strategy_plan.untraced(strategy_plan.planned_cases(lines), tree,
                                  strategy_plan.test_module_globs(lines))["Critical"] == []
    check = ["--root", str(root), "--tree", str(tree), "--pattern", "tests/test_*.py",
             "--strategy", str(strategy), "--", sys.executable, str(HARNESS / "test_inventory.py"),
             "pytest", "--", sys.executable, "-m", "pytest", "-p", "no:cacheprovider"]

    assert discovery_check.main(check) == 70
    assert "Critical planned cases: R7.1" in capsys.readouterr().err
    module.write_text(module.read_text() + '\n\nimport pytest\n\n\n'
                      '@pytest.mark.parametrize("case", ["R7.1"])\n'
                      'def test_cap(case):\n    pass\n')
    assert discovery_check.main(check) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["untested"] == {"Critical": [], "Standard": ["N2"], "Cosmetic": []}
