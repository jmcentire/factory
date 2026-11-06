#!/usr/bin/env python3
"""test_inventory — the tests a runner actually collects, in one runner-neutral format.

Every runner-specific parse lives here, so the gates that consume an inventory
(``discovery_check.py``) validate one documented format instead of reading console lines.

  test_inventory.py pytest|vitest|deno|node [-- <runner command prefix>]

Run it in the tree to inventory. The prefix launches the runner (default ``python3 -m pytest``,
``npx vitest``, ``deno test``, ``node --test``); the adapter appends its own collect-only flags,
so nothing runs, except under node, which has no such mode:

  pytest  ``--collect-only --verbosity=-1``  node ids ``<module>::<name>``
  vitest  ``list --json``                    ``[{"name", "file"}]``
  deno    ``--filter /^$/ --reporter=junit``  every module loads, every test is reported skipped
  node    ``--test-reporter=<events>``       inserted after ``--test``; the suite runs (below)

node: name filters cannot stand in for collection (Node 20 reports filtered tests skipped,
overwriting their own skip; Node 22+ drops them), so the adapter runs the suite once and reads
each test's final result. Pass or fail is runnable; skip or todo is skipped; a suite is never a
test; names join suites and parent tests with `` > `` as vitest does. A test belongs to its
top-level test's file, which must be a file Node ran (per-file isolation, the default). A file
that fails outside any test is a runner failure. The command must not set ``--test-reporter``.

Exit 0 prints exactly one JSON object on stdout::

  {"schema_version": "factory-test-inventory/1",
   "tests": [{"module": "<tree-relative POSIX path>", "name": "<test name>"}, ...],
   "skipped": [{"module": ..., "name": ...}, ...]}

``tests`` holds only tests the runner would run. A test it is told never to run is listed in
``skipped`` and is no evidence: a skipped test cannot fail, so it covers nothing.

- pytest: a plugin the adapter loads evaluates each item's ``skip``/``skipif`` marks (conditions
  included), ``xfail(run=False)`` and ``unittest.skip``, as pytest would before running it.
- deno: under the empty filter every test is reported skipped, so a second collection under a
  preload records which registrations are ignored (``Deno.test.ignore``, or an ``ignore`` option
  that is truthy when evaluated).
- vitest: ``list --json`` does not report skip modifiers, so a vitest inventory cannot tell a
  skipped test from a runnable one; the judge's own run still reports it skipped.

A module appears only through a collected test, so a module with no tests is absent. Any runner
failure (non-zero exit, or output the adapter cannot parse) exits 70 with no inventory: a
diagnostic that names a module is never an inventory. Stdlib only.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree

SCHEMA = "factory-test-inventory/1"
PREFIXES = {
    "pytest": ["python3", "-m", "pytest"],
    "vitest": ["npx", "vitest"],
    "deno": ["deno", "test"],
    "node": ["node", "--test"],
}

Test = dict[str, str]
Inventory = tuple[list[Test], list[Test]]  # (runnable, skipped)

# Loaded into the collecting pytest by name (-p). It evaluates the marks pytest's own skipping
# plugin evaluates before running an item, so "skipped" means what the run would do. An evaluation
# error propagates: pytest exits non-zero and no inventory is printed, rather than counting a test
# whose skip could not be decided.
_PYTEST_PLUGIN = """
import json, os
from _pytest.skipping import evaluate_skip_marks, evaluate_xfail_marks


def pytest_collection_finish(session):
    rows = []
    for item in session.items:
        xfail = evaluate_xfail_marks(item)
        skipped = (
            evaluate_skip_marks(item) is not None
            or (xfail is not None and not xfail.run)
            or bool(getattr(getattr(item, "obj", None), "__unittest_skip__", False))
            or bool(getattr(getattr(item, "cls", None), "__unittest_skip__", False))
        )
        rows.append({"nodeid": item.nodeid, "skipped": skipped})
    with open(os.environ["FACTORY_INVENTORY_SKIPS"], "w", encoding="utf-8") as sink:
        json.dump(rows, sink)
"""

# Preloaded into a second, filtered Deno collection. Wrapping Deno.test moves the location Deno
# reports, so that run is read only for which registrations are ignored; the junit run without the
# preload stays the inventory. Each record names the registering module from the caller's frame.
_DENO_PRELOAD = """
const sink = Deno.env.get("FACTORY_INVENTORY_IGNORED");
const register = Deno.test;
const site = () => (new Error().stack ?? "").split("\\n")[3] ?? "";
function note(args, forced, where) {
  const [first, second] = args;
  let name = "", ignore = forced;
  if (typeof first === "string") {
    name = first;
    if (second !== null && typeof second === "object") ignore = ignore || second.ignore;
  } else if (typeof first === "function") {
    name = first.name;
  } else if (first !== null && typeof first === "object") {
    name = first.name ?? (typeof second === "function" ? second.name : "");
    ignore = ignore || first.ignore;
  }
  Deno.writeTextFileSync(
    sink, JSON.stringify({ name, ignore: Boolean(ignore), site: where }) + "\\n", { append: true });
}
function test(...args) { note(args, false, site()); return register(...args); }
test.ignore = (...args) => { note(args, true, site()); return register.ignore(...args); };
test.only = (...args) => { note(args, false, site()); return register.only(...args); };
Deno.test = test;
"""
_DENO_SITE = re.compile(r"(file://[^\s()]+?):\d+:\d+\)?\s*$")


# Supplied to ``node --test`` as its reporter. Only final results are read: ``test:pass`` and
# ``test:fail`` arrive once per test and suite, children before their parent, one file after
# another. The end line proves the stream was not cut short.
_NODE_REPORTER = """
export default async function* inventory(source) {
  for await (const { type, data } of source) {
    // Each test file Node runs in its own process is enqueued at the top level under its path.
    if (type === "test:enqueue" && data.nesting === 0) {
      yield JSON.stringify({ enqueued: data.name, file: data.file ?? null }) + "\\n";
    }
    if (type !== "test:pass" && type !== "test:fail") continue;
    yield JSON.stringify({
      name: data.name, nesting: data.nesting, file: data.file ?? null,
      failed: type === "test:fail", suite: data.details?.type === "suite",
      // Node marks skip and todo by presence: an empty reason still skips.
      skip: data.skip !== undefined && data.skip !== false,
      todo: data.todo !== undefined && data.todo !== false,
    }) + "\\n";
  }
  yield JSON.stringify({ end: true }) + "\\n";
}
"""


class InventoryError(RuntimeError):
    pass


def _module(tree: pathlib.Path, path: str) -> str:
    path = path.removeprefix("file://")
    if os.path.isabs(path):
        path = os.path.relpath(os.path.realpath(path), os.path.realpath(tree))
    module = pathlib.PurePosixPath(path).as_posix().removeprefix("./")
    if module.startswith("../"):
        raise InventoryError(f"the runner collected a test outside the tree: {path}")
    return module


def _run(command: list[str], tree: pathlib.Path, ok: tuple[int, ...] = (0,),
         env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(command, cwd=tree, stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, check=False,
                                   env={**os.environ, **env} if env else None)
    except OSError as error:
        raise InventoryError(f"the runner did not start: {error}") from None
    if completed.returncode not in ok:
        raise InventoryError(f"the runner exited {completed.returncode}: "
                             f"{(completed.stderr or completed.stdout).strip()[-400:]}")
    return completed


def _pytest_skips(report: pathlib.Path) -> dict[str, bool]:
    try:
        return {str(row["nodeid"]): bool(row["skipped"])
                for row in json.loads(report.read_text(encoding="utf-8"))}
    except (OSError, json.JSONDecodeError, TypeError, KeyError) as error:
        raise InventoryError(f"pytest did not report which tests are skipped: {error}") from None


def pytest_tests(tree: pathlib.Path, prefix: list[str]) -> Inventory:
    with tempfile.TemporaryDirectory() as directory:
        scratch = pathlib.Path(directory)
        (scratch / "factory_inventory_skips.py").write_text(_PYTEST_PLUGIN, encoding="utf-8")
        report = scratch / "skips.json"
        python_path = os.pathsep.join(filter(None, [directory, os.environ.get("PYTHONPATH")]))
        # pytest exits 5 when it collects nothing: an empty inventory, not a failure.
        collected = _run([*prefix, "-p", "factory_inventory_skips", "--collect-only",
                          "--verbosity=-1"], tree, ok=(0, 5),
                         env={"PYTHONPATH": python_path, "FACTORY_INVENTORY_SKIPS": str(report)})
        skips = _pytest_skips(report) if collected.returncode == 0 else {}
    runnable: list[Test] = []
    skipped: list[Test] = []
    for line in collected.stdout.splitlines():
        if not line.strip():
            break  # the node ids end at the first blank line; the summary follows
        module, separator, name = line.partition("::")
        if not separator:
            raise InventoryError(f"pytest printed a line that is not a node id: {line!r}")
        if line not in skips:
            raise InventoryError(f"pytest listed {line!r} but its skip marks were not evaluated")
        test = {"module": _module(tree, module), "name": name}
        (skipped if skips[line] else runnable).append(test)
    return runnable, skipped


def vitest_tests(tree: pathlib.Path, prefix: list[str]) -> Inventory:
    listed = _run([*prefix, "list", "--json"], tree)
    try:
        return [{"module": _module(tree, entry["file"]), "name": entry["name"]}
                for entry in json.loads(listed.stdout)], []
    except (json.JSONDecodeError, TypeError, KeyError):
        # vitest 5 exits 0 with an error line instead of JSON, e.g. for a file with no tests.
        diagnostic = (listed.stderr or listed.stdout).strip()[:400]
        raise InventoryError(f"vitest list --json printed no test list: {diagnostic}") from None


def _deno_ignored(tree: pathlib.Path, prefix: list[str],
                  scratch: pathlib.Path) -> set[tuple[str | None, str]]:
    """(module, name) of every ignored registration. The module is None when the registering
    frame is outside the tree (a helper library registered it); None matches the name anywhere."""

    preload, sink = scratch / "inventory_preload.js", scratch / "ignored.jsonl"
    preload.write_text(_DENO_PRELOAD, encoding="utf-8")
    sink.touch()
    _run([*prefix, f"--preload={preload}", "--allow-env=FACTORY_INVENTORY_IGNORED",
          f"--allow-write={sink}", "--filter", "/^$/", "--reporter=dot"], tree,
         env={"FACTORY_INVENTORY_IGNORED": str(sink)})
    try:
        rows = [json.loads(line) for line in sink.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    except (OSError, json.JSONDecodeError) as error:
        raise InventoryError(f"deno did not report which tests are ignored: {error}") from None
    ignored: set[tuple[str | None, str]] = set()
    for row in rows:
        if not isinstance(row, dict) or not row.get("ignore"):
            continue
        site = _DENO_SITE.search(str(row.get("site", "")))
        try:
            module: str | None = _module(tree, site[1]) if site else None
        except InventoryError:
            module = None
        ignored.add((module, str(row.get("name", ""))))
    return ignored


def deno_tests(tree: pathlib.Path, prefix: list[str]) -> Inventory:
    with tempfile.TemporaryDirectory() as directory:
        scratch = pathlib.Path(directory)
        report = scratch / "junit.xml"
        _run([*prefix, "--filter", "/^$/", "--reporter=junit", f"--junit-path={report}"], tree)
        try:
            cases = [{"module": _module(tree, case.attrib["classname"]),
                      "name": case.attrib["name"]}
                     for case in ElementTree.parse(report).getroot().iter("testcase")]
        except (OSError, ElementTree.ParseError, KeyError) as error:
            raise InventoryError(f"deno wrote no readable junit report: {error}") from None
        ignored = _deno_ignored(tree, prefix, scratch) if cases else set()
    runnable: list[Test] = []
    skipped: list[Test] = []
    for case in cases:
        marked = {(case["module"], case["name"]), (None, case["name"])} & ignored
        (skipped if marked else runnable).append(case)
    return runnable, skipped


def _node_rows(tree: pathlib.Path, prefix: list[str]) -> list[dict]:
    if "--test" not in prefix:
        raise InventoryError("the node:test command must pass --test (node --test, tsx --test)")
    if any(word.startswith("--test-reporter") for word in prefix):
        raise InventoryError("leave --test-reporter out of the command; the inventory sets its own")
    with tempfile.TemporaryDirectory() as directory:
        reporter = pathlib.Path(directory, "inventory_reporter.mjs")
        events = pathlib.Path(directory, "events.jsonl")
        reporter.write_text(_NODE_REPORTER, encoding="utf-8")
        at = prefix.index("--test") + 1
        # node --test exits 1 when a test fails; a failing test is still a collected test.
        ran = _run([*prefix[:at], f"--test-reporter={reporter.as_uri()}",
                    f"--test-reporter-destination={events}", *prefix[at:]], tree, ok=(0, 1))
        diagnostic = (ran.stderr or ran.stdout).strip()[-400:]
        try:
            rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()
                    if line.strip()]
        except (OSError, json.JSONDecodeError) as error:
            raise InventoryError(f"node --test wrote no readable test results ({error}): "
                                 f"{diagnostic}") from None
    if not rows or rows[-1] != {"end": True}:
        raise InventoryError(f"node --test did not finish reporting: {diagnostic}")
    if ran.returncode != 0 and not any(row.get("failed") for row in rows):
        raise InventoryError(f"node --test exited {ran.returncode} with no failing test: "
                             f"{diagnostic}")
    return rows[:-1]


def node_tests(tree: pathlib.Path, prefix: list[str]) -> Inventory:
    runnable: list[Test] = []
    skipped: list[Test] = []
    # Results still waiting for their parent: (nesting, [(name, runnable)]).
    waiting: list[tuple[int, list[tuple[str, bool]]]] = []
    ran: set[str] = set()
    for row in _node_rows(tree, prefix):
        if "enqueued" in row:
            if isinstance(row["enqueued"], str) and isinstance(row["file"], str) and \
                    os.path.realpath(tree / row["enqueued"]) == os.path.realpath(row["file"]):
                ran.add(os.path.realpath(row["file"]))
            continue
        name, nesting, file = row.get("name"), row.get("nesting"), row.get("file")
        if not (isinstance(name, str) and isinstance(nesting, int) and isinstance(file, str)):
            raise InventoryError(f"node --test reported a result with no name or file: {row}")
        file = file.removeprefix("file://")
        below: list[tuple[str, bool]] = []
        while waiting and waiting[-1][0] > nesting:
            below = waiting.pop()[1] + below
        # The runner reports a file that ran no test as one top-level test named by its path.
        if nesting == 0 and not below and os.path.realpath(tree / name) == os.path.realpath(file):
            if row.get("failed"):
                raise InventoryError(f"node --test could not run {_module(tree, file)}: it failed "
                                     "outside any test; run it to see why")
            continue
        runs = not (row.get("skip") or row.get("todo"))
        # A suite is not a test; a skipped suite whose tests never registered is listed skipped.
        own = ([] if below or runs else [(name, False)]) if row.get("suite") else [(name, runs)]
        tests = own + [(f"{name} > {child}", child_runs) for child, child_runs in below]
        if nesting > 0:
            waiting.append((nesting, tests))
            continue
        if os.path.realpath(file) not in ran:
            raise InventoryError(f"node --test credited top-level test {name!r} to "
                                 f"{_module(tree, file)}, which is not a test file it ran (a "
                                 "shared helper registered it, or isolation is not per file)")
        module = _module(tree, file)
        for test, test_runs in tests:
            (runnable if test_runs else skipped).append({"module": module, "name": test})
    if waiting:
        raise InventoryError("node --test reported results with no enclosing top-level test: "
                             f"{[name for _nesting, tests in waiting for name, _ in tests][:5]}")
    return runnable, skipped


ADAPTERS = {"pytest": pytest_tests, "vitest": vitest_tests, "deno": deno_tests,
            "node": node_tests}


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in ADAPTERS or arguments[1:2] not in ([], ["--"]):
        print(f"usage: test_inventory.py {'|'.join(ADAPTERS)} [-- <runner command prefix>]",
              file=sys.stderr)
        return 64
    runner, prefix = arguments[0], arguments[2:] or PREFIXES[arguments[0]]
    try:
        tests, skipped = ADAPTERS[runner](pathlib.Path.cwd(), prefix)
    except InventoryError as error:
        print(f"test-inventory: {error}", file=sys.stderr)
        return 70
    print(json.dumps({"schema_version": SCHEMA, "tests": tests, "skipped": skipped},
                     sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
