"""The integrated tree's runner must list every test module the Tester froze (dogfood #42)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness import discovery_check, strategy_plan

HARNESS = Path(__file__).resolve().parents[1] / "harness"


def _git(tree: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(tree), "-c", "user.name=t", "-c", "user.email=t@t", *arguments],
        check=True, capture_output=True, text=True).stdout.strip()


def _commit(tree: Path) -> str:
    """Commit everything in ``tree`` (a fresh repository on first call) and return HEAD."""

    if not (tree / ".git").exists():
        _git(tree, "init", "--quiet")
    (tree / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
    _git(tree, "add", "-A")
    _git(tree, "commit", "--quiet", "--allow-empty", "-m", "candidate")
    return _git(tree, "rev-parse", "HEAD")


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


def _strategy(directory: Path, *lines: str) -> Path:
    strategy = directory / "testing-strategy.md"
    strategy.write_text("".join(line + "\n" for line in lines))
    return strategy


def _inventory(*tests: tuple[str, str]) -> str:
    return json.dumps({"schema_version": discovery_check.INVENTORY_SCHEMA,
                       "tests": [{"module": module, "name": name} for module, name in tests]})


def _integrated(tmp_path: Path) -> tuple[Path, str]:
    tree = tmp_path / "integrated"
    tree.mkdir()
    (tree / "README.md").write_text("candidate\n")
    return tree, _commit(tree)


def _check(root: Path, tree: Path, stdout: str, pattern: str = "tests/*_test.ts",
           stderr: str = "", status: int = 0, strategy: Path | None = None,
           candidate: str | None = None) -> int:
    # The fake runner's output lives outside the tree, so the candidate checkout stays clean.
    out, err = tree.parent / "runner.out", tree.parent / "runner.err"
    out.write_text(stdout)
    err.write_text(stderr)
    strategy = strategy or _strategy(tree.parent, "test-modules: tests/*_test.ts")
    return discovery_check.main([
        "--root", str(root), "--tree", str(tree),
        "--candidate-sha", candidate or _git(tree, "rev-parse", "HEAD"),
        "--pattern", pattern, "--strategy", str(strategy), "--",
        "sh", "-c", f"cat {out}; cat {err} >&2; exit {status}"])


def test_a_tree_whose_runner_misses_the_testers_modules_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, (tree, _) = _frozen_tester(tmp_path), _integrated(tmp_path)
    assert _check(root, tree, _inventory()) == 70
    assert "tests/export_test.ts" in capsys.readouterr().err
    assert _check(root, tree, _inventory(("tests/export_test.ts", "exports a CSV"))) == 0
    assert json.loads(capsys.readouterr().out)["modules"] == ["tests/export_test.ts"]


def test_console_lines_and_failing_runners_are_not_an_inventory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review of #42: only a documented inventory counts, never a line that names a module."""

    root, (tree, _) = _frozen_tester(tmp_path), _integrated(tmp_path)
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
    root, (tree, _) = _frozen_tester(tmp_path), _integrated(tmp_path)
    found = _inventory(("tests/export_test.ts", "exports a CSV"))
    assert _check(root, tree, found, pattern="spec/*_test.ts") == 70
    journal = root / "tmux-lanes" / "tester-launch.jsonl"
    journal.write_text(journal.read_text() + json.dumps(
        {"status": "active", "role": "tester"}) + "\n")
    assert _check(root, tree, found) == 70


def test_the_check_runs_only_on_the_exact_unchanged_candidate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The receipt names one commit, so the tree must be that commit with nothing added."""

    root, (tree, candidate) = _frozen_tester(tmp_path), _integrated(tmp_path)
    found = _inventory(("tests/export_test.ts", "exports a CSV"))
    assert _check(root, tree, found, candidate="0" * 40) == 70
    assert "not candidate" in capsys.readouterr().err
    (tree / "pytest.ini").write_text("[pytest]\n")
    assert _check(root, tree, found, candidate=candidate) == 70
    assert "uncommitted or untracked" in capsys.readouterr().err
    assert not discovery_check.receipt_path(root, "tester", candidate).exists()


def _pytest_tester(tmp_path: Path, body: str) -> tuple[Path, Path, Path, list[str]]:
    """A Tester freeze whose export is also the integrated pytest tree."""

    tree = tmp_path / "integrated"
    (tree / "tests").mkdir(parents=True)
    module = tree / "tests" / "test_cap.py"
    module.write_text(body)
    root = tmp_path / "run"
    (root / "tmux-lanes").mkdir(parents=True)
    (root / "tmux-lanes" / "tester-launch.jsonl").write_text(json.dumps(
        {"schema_version": discovery_check.FREEZE_SCHEMA,
         "export": {"files_directory": str(tree)}}) + "\n")
    strategy = _strategy(tmp_path, "planned-case: R7.1", "planned-case: N2 Standard",
                         "test-modules: tests/test_*.py")
    check = ["--root", str(root), "--tree", str(tree), "--pattern", "tests/test_*.py",
             "--strategy", str(strategy), "--", sys.executable, str(HARNESS / "test_inventory.py"),
             "pytest", "--", sys.executable, "-m", "pytest", "-p", "no:cacheprovider"]
    return root, tree, module, check


def test_a_critical_planned_case_with_no_collected_test_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review of #73: a module that only mentions a case id (a constant, a trailing comment)
    passes traceability but has no collected test carrying the id, so it is refused here.
    The inventory comes from real pytest through test_inventory.py."""

    root, tree, module, check = _pytest_tester(
        tmp_path, 'planned = "R7.1"  # R7.1\n\n\ndef test_unrelated():\n    pass\n')
    lines = (tmp_path / "testing-strategy.md").read_text().splitlines()
    assert strategy_plan.untraced(strategy_plan.planned_cases(lines), tree,
                                  strategy_plan.test_module_globs(lines))["Critical"] == []

    assert discovery_check.main(["--candidate-sha", _commit(tree), *check]) == 70
    assert "Critical planned cases: R7.1" in capsys.readouterr().err
    module.write_text(module.read_text() + '\n\nimport pytest\n\n\n'
                      '@pytest.mark.parametrize("case", ["R7.1"])\n'
                      'def test_cap(case):\n    pass\n')
    assert discovery_check.main(["--candidate-sha", _commit(tree), *check]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["untested"] == {"Critical": [], "Standard": ["N2"], "Cosmetic": []}


def test_a_skipped_test_does_not_cover_a_critical_planned_case(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A skipped test cannot fail, so a Critical case named only by one is untested."""

    root, tree, module, check = _pytest_tester(
        tmp_path, "import pytest\n\n\ndef test_other():\n    pass\n\n\n"
        '@pytest.mark.skip(reason="later")\n@pytest.mark.parametrize("case", ["R7.1"])\n'
        "def test_cap(case):\n    pass\n")

    assert discovery_check.main(["--candidate-sha", _commit(tree), *check]) == 70
    assert "R7.1 named only by skipped or ignored tests" in capsys.readouterr().err
    module.write_text(module.read_text().replace('@pytest.mark.skip(reason="later")\n', ""))
    assert discovery_check.main(["--candidate-sha", _commit(tree), *check]) == 0


def test_a_module_whose_tests_are_all_ignored_is_not_discovered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, (tree, _) = _frozen_tester(tmp_path), _integrated(tmp_path)
    ignored = json.dumps({"schema_version": discovery_check.INVENTORY_SCHEMA, "tests": [],
                          "skipped": [{"module": "tests/export_test.ts", "name": "R3.1"}]})
    assert _check(root, tree, ignored) == 70
    assert "no runnable test" in capsys.readouterr().err


# --- endgame's requirement: no verdict without a receipt for the exact candidate ----------


def _ratified(tmp_path: Path, *lines: str) -> Path:
    artifacts = tmp_path / "run" / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    strategy = _strategy(artifacts, *lines)
    (artifacts / "testing-strategy.md.digest").write_text("ratified\n")
    return strategy


def _require(root: Path, candidate: str, strategy: Path) -> int:
    return discovery_check.main(["require", "--root", str(root), "--candidate-sha", candidate,
                                 "--strategy", str(strategy)])


def test_a_verdict_needs_a_receipt_for_the_exact_candidate_strategy_and_freeze(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, (tree, candidate) = _frozen_tester(tmp_path), _integrated(tmp_path)
    strategy = _ratified(tmp_path, "test-modules: tests/*_test.ts")
    found = _inventory(("tests/export_test.ts", "exports a CSV"))

    assert _require(root, candidate, strategy) == 70
    assert "no discovery receipt for tester" in capsys.readouterr().err
    assert _check(root, tree, found, strategy=strategy) == 0
    assert _require(root, candidate, strategy) == 0
    assert _require(root, "1" * 40, strategy) == 70
    capsys.readouterr()

    strategy.write_text(strategy.read_text() + "planned-case: R9.9 Standard\n")
    assert _require(root, candidate, strategy) == 70
    assert "strategy_sha256" in capsys.readouterr().err
    assert _check(root, tree, found, strategy=strategy) == 0

    journal = root / "tmux-lanes" / "tester-launch.jsonl"
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    rows += [{"status": "active", "role": "tester"},
             {**rows[-1], "ts": "refrozen"}]
    journal.write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert _require(root, candidate, strategy) == 70
    assert "freeze_sha256" in capsys.readouterr().err


def test_a_verdict_needs_the_strategy_ratified_and_a_freeze_when_it_declares_modules(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, (tree, candidate) = _frozen_tester(tmp_path), _integrated(tmp_path)
    strategy = _ratified(tmp_path, "test-modules: tests/*_test.ts")
    assert _check(root, tree, _inventory(("tests/export_test.ts", "x")), strategy=strategy) == 0
    (strategy.parent / "testing-strategy.md.digest").unlink()
    assert _require(root, candidate, strategy) == 70
    assert "not ratified" in capsys.readouterr().err

    (root / "tmux-lanes" / "tester-launch.jsonl").unlink()
    assert _require(root, candidate, strategy) == 70
    assert "no Tester slot froze" in capsys.readouterr().err
    strategy.write_text("no planned tests in this run\n")
    assert _require(root, candidate, strategy) == 0
