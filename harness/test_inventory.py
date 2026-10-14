#!/usr/bin/env python3
"""test_inventory — the tests a runner actually collects, in one runner-neutral format.

Every runner-specific parse lives here, so the gates that consume an inventory
(``discovery_check.py``) validate one documented format instead of reading console lines.

  test_inventory.py pytest|vitest|deno [-- <runner command prefix>]

Run it in the tree to inventory. The prefix launches the runner (default ``python3 -m pytest``,
``npx vitest``, ``deno test``); the adapter appends its own collect-only flags, so nothing runs:

  pytest  ``--collect-only --verbosity=-1``  node ids ``<module>::<name>``
  vitest  ``list --json``                    ``[{"name", "file"}]``
  deno    ``--filter /^$/ --reporter=junit``  every module loads, every test is reported skipped

Exit 0 prints exactly one JSON object on stdout::

  {"schema_version": "factory-test-inventory/1",
   "tests": [{"module": "<tree-relative POSIX path>", "name": "<test name>"}, ...]}

A module appears only through a collected test, so a module with no tests is absent. Any runner
failure (non-zero exit, or output the adapter cannot parse) exits 70 with no inventory: a
diagnostic that names a module is never an inventory. Stdlib only.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree

SCHEMA = "factory-test-inventory/1"
PREFIXES = {
    "pytest": ["python3", "-m", "pytest"],
    "vitest": ["npx", "vitest"],
    "deno": ["deno", "test"],
}


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


def _run(command: list[str], tree: pathlib.Path,
         ok: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(command, cwd=tree, stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, check=False)
    except OSError as error:
        raise InventoryError(f"the runner did not start: {error}") from None
    if completed.returncode not in ok:
        raise InventoryError(f"the runner exited {completed.returncode}: "
                             f"{(completed.stderr or completed.stdout).strip()[-400:]}")
    return completed


def pytest_tests(tree: pathlib.Path, prefix: list[str]) -> list[dict[str, str]]:
    tests = []
    # pytest exits 5 when it collects nothing: an empty inventory, not a failure.
    collected = _run([*prefix, "--collect-only", "--verbosity=-1"], tree, ok=(0, 5))
    for line in collected.stdout.splitlines():
        if not line.strip():
            break  # the node ids end at the first blank line; the summary follows
        module, separator, name = line.partition("::")
        if not separator:
            raise InventoryError(f"pytest printed a line that is not a node id: {line!r}")
        tests.append({"module": _module(tree, module), "name": name})
    return tests


def vitest_tests(tree: pathlib.Path, prefix: list[str]) -> list[dict[str, str]]:
    listed = _run([*prefix, "list", "--json"], tree)
    try:
        return [{"module": _module(tree, entry["file"]), "name": entry["name"]}
                for entry in json.loads(listed.stdout)]
    except (json.JSONDecodeError, TypeError, KeyError):
        # vitest 5 exits 0 with an error line instead of JSON, e.g. for a file with no tests.
        diagnostic = (listed.stderr or listed.stdout).strip()[:400]
        raise InventoryError(f"vitest list --json printed no test list: {diagnostic}") from None


def deno_tests(tree: pathlib.Path, prefix: list[str]) -> list[dict[str, str]]:
    with tempfile.TemporaryDirectory() as scratch:
        report = pathlib.Path(scratch) / "junit.xml"
        _run([*prefix, "--filter", "/^$/", "--reporter=junit", f"--junit-path={report}"], tree)
        try:
            cases = ElementTree.parse(report).getroot().iter("testcase")
            return [{"module": _module(tree, case.attrib["classname"]),
                     "name": case.attrib["name"]} for case in cases]
        except (OSError, ElementTree.ParseError, KeyError) as error:
            raise InventoryError(f"deno wrote no readable junit report: {error}") from None


ADAPTERS = {"pytest": pytest_tests, "vitest": vitest_tests, "deno": deno_tests}


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in ADAPTERS or arguments[1:2] not in ([], ["--"]):
        print(f"usage: test_inventory.py {'|'.join(ADAPTERS)} [-- <runner command prefix>]",
              file=sys.stderr)
        return 64
    runner, prefix = arguments[0], arguments[2:] or PREFIXES[arguments[0]]
    try:
        tests = ADAPTERS[runner](pathlib.Path.cwd(), prefix)
    except InventoryError as error:
        print(f"test-inventory: {error}", file=sys.stderr)
        return 70
    print(json.dumps({"schema_version": SCHEMA, "tests": tests}, sort_keys=True,
                     separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
