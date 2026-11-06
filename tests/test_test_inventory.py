"""test_inventory turns each runner's own collection into one runner-neutral document."""

from __future__ import annotations

import json
import shutil
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
    # The second, preloaded collection reports which registrations are ignored; none here.
    deno = _fake(tmp_path, f"""
assert "--filter" in sys.argv and "/^$/" in sys.argv, sys.argv
junit = [a for a in sys.argv if a.startswith("--junit-path=")]
if junit:
    open(junit[0][len("--junit-path="):], "w").write({junit!r})
""")
    assert _inventory(tmp_path, "deno", "--", sys.executable, deno, "test", capsys=capsys) == [
        {"module": "tests/a_test.ts", "name": "R7.1 refuses x"}]
    listed = json.dumps([{"name": "cap > R7.1 refuses x",
                          "file": str(tmp_path.resolve() / "tests" / "a.test.ts")}])
    vitest = _fake(tmp_path, f"assert sys.argv[1:] == ['list', '--json']\nprint({listed!r})\n")
    assert _inventory(tmp_path, "vitest", "--", sys.executable, vitest, capsys=capsys) == [
        {"module": "tests/a.test.ts", "name": "cap > R7.1 refuses x"}]


def _document(tree: Path, *argv: str, capsys: pytest.CaptureFixture[str]) -> dict:
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(tree)
        assert test_inventory.main(list(argv)) == 0
    return json.loads(capsys.readouterr().out)


def test_a_skipped_pytest_test_is_not_a_runnable_test(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A skipped test cannot fail, so it is no evidence: skip, a true skipif, xfail(run=False)
    and unittest.skip leave ``tests`` for ``skipped``; a false skipif stays runnable."""

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_cap.py").write_text(
        "import sys, unittest\nimport pytest\n\n\n"
        '@pytest.mark.skip(reason="later")\ndef test_cap_R7_1():\n    pass\n\n\n'
        '@pytest.mark.skipif(sys.version_info > (3,), reason="always")\n'
        "def test_cap_R7_2():\n    pass\n\n\n"
        '@pytest.mark.xfail(run=False)\ndef test_cap_R7_3():\n    pass\n\n\n'
        "class Legacy(unittest.TestCase):\n"
        '    @unittest.skip("later")\n    def test_cap_R7_4(self):\n        pass\n\n\n'
        '@pytest.mark.skipif(sys.version_info < (3,), reason="never")\n'
        "def test_cap_R7_5():\n    pass\n")
    document = _document(tmp_path, "pytest", "--", sys.executable, "-m", "pytest",
                         "-p", "no:cacheprovider", capsys=capsys)

    assert document["tests"] == [{"module": "tests/test_cap.py", "name": "test_cap_R7_5"}]
    assert sorted(test["name"] for test in document["skipped"]) == [
        "Legacy::test_cap_R7_4", "test_cap_R7_1", "test_cap_R7_2", "test_cap_R7_3"]


def test_an_ignored_deno_test_is_not_a_runnable_test(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Deno reports every test skipped under the empty filter; the preloaded collection's
    ignore records move ignored registrations out of ``tests``."""

    junit = ('<testsuites><testsuite name="./tests/a_test.ts">'
             '<testcase name="R7.1 runs" classname="./tests/a_test.ts"><skipped/></testcase>'
             '<testcase name="R7.2 ignored" classname="./tests/a_test.ts"><skipped/></testcase>'
             "</testsuite></testsuites>")
    site = (tmp_path.resolve() / "tests" / "a_test.ts").as_uri()
    records = [{"name": "R7.1 runs", "ignore": False, "site": f"at {site}:1:6"},
               {"name": "R7.2 ignored", "ignore": True, "site": f"at {site}:2:6"}]
    deno = _fake(tmp_path, f"""
import json, os
junit = [a for a in sys.argv if a.startswith("--junit-path=")]
if junit:
    open(junit[0][len("--junit-path="):], "w").write({junit!r})
else:
    assert any(a.startswith("--preload=") for a in sys.argv), sys.argv
    with open(os.environ["FACTORY_INVENTORY_IGNORED"], "a") as sink:
        for row in {records!r}:
            sink.write(json.dumps(row) + "\\n")
""")
    document = _document(tmp_path, "deno", "--", sys.executable, deno, "test", capsys=capsys)

    assert document["tests"] == [{"module": "tests/a_test.ts", "name": "R7.1 runs"}]
    assert document["skipped"] == [{"module": "tests/a_test.ts", "name": "R7.2 ignored"}]


@pytest.mark.skipif(shutil.which("deno") is None, reason="needs the deno runtime")
def test_real_deno_ignore_forms_are_not_runnable_tests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "a_test.ts").write_text(
        'Deno.test("R7.1 runs", () => {});\n'
        'Deno.test({ name: "R7.2 ignored", ignore: true, fn: () => {} });\n'
        'Deno.test.ignore("R7.3 ignored", () => {});\n'
        'Deno.test("R7.4 option", { ignore: 1 + 1 === 2 }, () => {});\n'
        'Deno.test({ name: "R7.5 runs", ignore: 1 + 1 === 3, fn: () => {} });\n')
    document = _document(tmp_path, "deno", capsys=capsys)

    assert [test["name"] for test in document["tests"]] == ["R7.1 runs", "R7.5 runs"]
    assert {test["module"] for test in document["tests"]} == {"tests/a_test.ts"}
    assert [test["name"] for test in document["skipped"]] == [
        "R7.2 ignored", "R7.3 ignored", "R7.4 option"]


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


def _node_fake(tree: Path, rows: list[dict], exit_code: int = 0, end: bool = True) -> str:
    """A node --test stand-in that writes the inventory reporter's lines to its destination."""

    lines = [json.dumps(row) for row in rows] + ([json.dumps({"end": True})] if end else [])
    return _fake(tree, f"""
assert sys.argv[1] == "--test", sys.argv
assert sys.argv[2].startswith("--test-reporter=file://"), sys.argv
destination = sys.argv[3].removeprefix("--test-reporter-destination=")
open(destination, "w").write({chr(10).join(lines)!r} + "\\n")
sys.exit({exit_code})
""")


def _result(name: str, nesting: int, file: Path, **flags: bool) -> dict:
    return {"name": name, "nesting": nesting, "file": str(file), "failed": False,
            "suite": False, "skip": False, "todo": False, **flags}


def test_node_results_become_tests_named_through_their_suites(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """node:test reports children before their parent. Suites are not tests, a failing test is
    still collected, skip and todo are not runnable, and a file that ran no test is not one."""

    test_file = tmp_path.resolve() / "test" / "cap.test.ts"
    empty_file = tmp_path.resolve() / "test" / "helpers.test.ts"
    rows = [
        _result("R7.1 refuses x", 1, test_file),
        _result("R7.2 later", 1, test_file, skip=True),
        _result("R7.3 todo", 1, test_file, todo=True),
        _result("R7.4 deep", 2, test_file, failed=True),
        _result("inner", 1, test_file, suite=True),
        _result("skipped suite", 1, test_file, suite=True, skip=True),
        _result("cap", 0, test_file, suite=True),
        _result("R7.5 subtest", 1, test_file),
        _result("parent", 0, test_file),
        _result("test/helpers.test.ts", 0, empty_file),
    ]
    runner = _node_fake(tmp_path, rows, exit_code=1)
    document = _document(tmp_path, "node", "--", sys.executable, runner, "--test", "test",
                         capsys=capsys)

    module = "test/cap.test.ts"
    assert document["tests"] == [{"module": module, "name": name} for name in (
        "cap > R7.1 refuses x", "cap > inner > R7.4 deep", "parent", "parent > R7.5 subtest")]
    assert document["skipped"] == [{"module": module, "name": name} for name in (
        "cap > R7.2 later", "cap > R7.3 todo", "cap > skipped suite")]


@pytest.mark.parametrize("rows, exit_code, end, argv", [
    # A file that failed outside any test did not load.
    ([_result("test/a.test.ts", 0, Path("test/a.test.ts"), failed=True)], 1, True, ["--test"]),
    # The reporter never finished.
    ([_result("R7.1", 0, Path("test/a.test.ts"))], 0, False, ["--test"]),
    # Exit 1 with no test run is a runner failure (Node 20: "Could not find" a glob).
    ([], 1, True, ["--test"]),
    ([_result("R7.1", 0, Path("test/a.test.ts"))], 7, True, ["--test"]),
    ([_result("R7.1", 0, Path("test/a.test.ts"))], 0, True, []),
    ([_result("R7.1", 0, Path("test/a.test.ts"))], 0, True, ["--test", "--test-reporter=tap"]),
])
def test_a_node_runner_failure_prints_no_inventory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], rows: list[dict], exit_code: int,
    end: bool, argv: list[str],
) -> None:
    rows = [{**row, "file": str(tmp_path.resolve() / row["file"])} for row in rows]
    runner = _node_fake(tmp_path, rows, exit_code=exit_code, end=end)
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(tmp_path)
        assert test_inventory.main(["node", "--", sys.executable, runner, *argv]) == 70
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("script, prefix", [
    ("tsx --test 'src/**/*.test.ts'", ["tsx", "--test", "src/**/*.test.ts"]),
    ("node --import tsx --test src", ["node", "--import", "tsx", "--test", "src"]),
    ("npm run build && node --test dist", ["node", "--test"]),
    ("vitest run", ["node", "--test"]),
])
def test_the_node_prefix_is_the_repositorys_plain_test_script(
    tmp_path: Path, script: str, prefix: list[str]
) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": script}}))
    assert test_inventory.node_prefix(tmp_path) == prefix
    (tmp_path / "node_modules" / ".bin").mkdir(parents=True)
    (tmp_path / "node_modules" / ".bin" / "tsx").write_text("")
    if prefix[0] == "tsx":
        assert test_inventory.node_prefix(tmp_path)[0] == str(tmp_path / "node_modules/.bin/tsx")


@pytest.mark.skipif(shutil.which("node") is None, reason="needs the node runtime")
def test_real_node_test_results_are_an_inventory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "test").mkdir()
    (tmp_path / "test" / "cap.test.mjs").write_text(
        'import { describe, it, test } from "node:test";\n'
        'describe("cap", () => {\n'
        '  it("R7.1 runs", () => {});\n'
        '  it.skip("R7.2 skipped", () => {});\n'
        '  it("R7.3 option", { skip: "later" }, () => {});\n'
        '  it.todo("R7.4 todo");\n'
        '  describe("inner", () => { it("R7.5 fails", () => { throw new Error("x"); }); });\n'
        "});\n"
        'test("parent", async (t) => { await t.test("R7.6 subtest", () => {}); });\n')
    document = _document(tmp_path, "node", "--", "node", "--test", "test/cap.test.mjs",
                         capsys=capsys)

    assert [test["name"] for test in document["tests"]] == [
        "cap > R7.1 runs", "cap > inner > R7.5 fails", "parent", "parent > R7.6 subtest"]
    assert [test["name"] for test in document["skipped"]] == [
        "cap > R7.2 skipped", "cap > R7.3 option", "cap > R7.4 todo"]
    assert {test["module"] for test in document["tests"]} == {"test/cap.test.mjs"}
