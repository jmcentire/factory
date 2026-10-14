"""test_inventory turns each runner's own collection into one runner-neutral document."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from harness import test_inventory


def _inventory(tree: Path, *argv: str, capsys: pytest.CaptureFixture[str]) -> list[dict]:
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(tree)
        assert test_inventory.main(list(argv)) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["schema_version"] == "factory-test-inventory/1"
    return document["tests"]


def _fake(tree: Path, body: str) -> str:
    script = tree / "fake_runner.py"
    script.write_text("import sys\n" + body)
    return str(script)


def test_pytest_node_ids_become_module_and_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts = "-q"\n')
    (tmp_path / "tests" / "test_cap.py").write_text(
        'import pytest\n\n\n@pytest.mark.parametrize("case", ["R7.1"])\n'
        "def test_cap(case):\n    pass\n")
    (tmp_path / "tests" / "test_empty.py").write_text('planned = "R7.2"\n')
    pytest_command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"]

    assert _inventory(tmp_path, "pytest", "--", *pytest_command, capsys=capsys) == [
        {"module": "tests/test_cap.py", "name": "test_cap[R7.1]"}]
    empty = _inventory(tmp_path, "pytest", "--", *pytest_command, "tests/test_empty.py",
                       capsys=capsys)
    assert empty == []


def test_deno_junit_and_vitest_json_become_module_and_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    junit = ('<testsuites><testsuite name="./tests/a_test.ts">'
             '<testcase name="R7.1 refuses x" classname="./tests/a_test.ts"><skipped/></testcase>'
             "</testsuite></testsuites>")
    deno = _fake(tmp_path, f"""
assert "--filter" in sys.argv and "/^$/" in sys.argv, sys.argv
path = next(a for a in sys.argv if a.startswith("--junit-path="))[len("--junit-path="):]
open(path, "w").write({junit!r})
""")
    assert _inventory(tmp_path, "deno", "--", sys.executable, deno, "test", capsys=capsys) == [
        {"module": "tests/a_test.ts", "name": "R7.1 refuses x"}]
    listed = json.dumps([{"name": "cap > R7.1 refuses x",
                          "file": str(tmp_path.resolve() / "tests" / "a.test.ts")}])
    vitest = _fake(tmp_path, f"assert sys.argv[1:] == ['list', '--json']\nprint({listed!r})\n")
    assert _inventory(tmp_path, "vitest", "--", sys.executable, vitest, capsys=capsys) == [
        {"module": "tests/a.test.ts", "name": "cap > R7.1 refuses x"}]


@pytest.mark.parametrize("runner, body", [
    ("vitest", "print('Error: No test suite found in file tests/b.test.ts')\n"),
    ("vitest", "print('[]'); sys.exit(1)\n"),
    ("deno", "print('Check file:///tree/tests/a_test.ts')\n"),
    ("vitest", "print('[{\"name\": \"x\", \"file\": \"/elsewhere/a.test.ts\"}]')\n"),
])
def test_a_runner_failure_or_unparseable_output_prints_no_inventory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], runner: str, body: str
) -> None:
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(tmp_path)
        assert test_inventory.main([runner, "--", sys.executable, _fake(tmp_path, body)]) == 70
    assert capsys.readouterr().out == ""
