#!/usr/bin/env python3
"""discovery_check — the integrated tree's test runner finds every test the Tester froze.

The invariant: **Phase C does not start on a tree whose runner cannot see the Tester's suite.**
In run c the Coder's projection hid tests/, so the Coder excluded tests/ in deno.json; the
integrated tree's ``deno test`` then reported "No test modules found" at Phase C, and the shipped
repository's own test task would have too (dogfood #42). After both lanes are frozen and the
tree is integrated, this runs an inventory command in that tree and refuses when it exits
non-zero, prints anything but one ``factory-test-inventory/1`` document (``test_inventory.py``
documents the format and holds every runner-specific parse), or when any test module in the
Tester's latest freeze has no collected test in it. Console lines are never read: a diagnostic
that names a module is not an inventory.

It also checks the planned tests (dogfood #73): every Critical ``planned-case`` id in
``--strategy`` must appear in the name of a collected test from one of the Tester's modules.
A module that only mentions the id (a constant, a comment) has no test that carries it.

  discovery_check.py --root <run> --tree <integrated tree> --pattern <glob> --strategy <md>
                     [--slot tester] [--timeout-minutes 10]
                     -- <inventory command, e.g. python3 harness/test_inventory.py deno>

``--pattern`` names the Tester's test modules inside its frozen export (``fnmatch``, where
``*`` also crosses ``/``: ``tests/*_test.ts``). A pattern that matches nothing is refused, so the
check cannot pass vacuously. Exit 0 prints a JSON receipt; exit 70 names what the runner missed:
a configuration conflict to route as an integration edit, never a reason to skip the suite.
Stdlib only.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import pathlib
import subprocess
import sys

_HARNESS_ROOT = str(pathlib.Path(__file__).resolve().parent)
if _HARNESS_ROOT not in sys.path:
    sys.path.insert(0, _HARNESS_ROOT)

import strategy_plan  # noqa: E402

FREEZE_SCHEMA = "factory-tmux-lane-freeze/1"
INVENTORY_SCHEMA = "factory-test-inventory/1"


class DiscoveryError(RuntimeError):
    pass


def frozen_files(root: pathlib.Path, slot: str) -> pathlib.Path:
    journal = root / "tmux-lanes" / f"{slot}-launch.jsonl"
    try:
        rows = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    except (OSError, json.JSONDecodeError) as error:
        raise DiscoveryError(f"no readable launch journal for {slot}: {error}") from None
    freezes = [row for row in rows if row.get("schema_version") == FREEZE_SCHEMA]
    if not freezes or rows[-1] is not freezes[-1]:
        raise DiscoveryError(f"{slot} is not frozen; freeze both lanes before the check")
    return pathlib.Path(freezes[-1]["export"]["files_directory"])


def test_modules(files: pathlib.Path, pattern: str) -> list[str]:
    modules = sorted(path.relative_to(files).as_posix() for path in files.rglob("*")
                     if path.is_file() and fnmatch.fnmatch(path.relative_to(files).as_posix(),
                                                          pattern))
    if not modules:
        raise DiscoveryError(f"--pattern {pattern!r} matches no file in the Tester's freeze")
    return modules


def inventory(output: str) -> list[dict[str, str]]:
    """The collected tests of one ``factory-test-inventory/1`` document; anything else refuses."""

    try:
        document = json.loads(output)
    except json.JSONDecodeError:
        document = None
    tests = document.get("tests") if isinstance(document, dict) else None
    if (not isinstance(document, dict) or document.get("schema_version") != INVENTORY_SCHEMA
            or not isinstance(tests, list)
            or not all(isinstance(test, dict) and set(test) == {"module", "name"}
                       and all(isinstance(value, str) for value in test.values())
                       for test in tests)):
        raise DiscoveryError(f"the inventory command did not print one {INVENTORY_SCHEMA} "
                             f"document: {output.strip()[:200]!r}")
    return tests


def collected(tree: pathlib.Path, command: list[str], timeout_s: float) -> list[dict[str, str]]:
    try:
        completed = subprocess.run(command, cwd=tree, stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, timeout=timeout_s,
                                   check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DiscoveryError(f"the inventory command did not complete: {error}") from None
    if completed.returncode != 0:
        raise DiscoveryError(f"the inventory command exited {completed.returncode}: "
                             f"{(completed.stderr or completed.stdout).strip()[-400:]}")
    return inventory(completed.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="the runner discovers the Tester's modules")
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--tree", type=pathlib.Path, required=True)
    parser.add_argument("--pattern", required=True)
    parser.add_argument("--strategy", type=pathlib.Path, required=True)
    parser.add_argument("--slot", default="tester")
    parser.add_argument("--timeout-minutes", type=float, default=10.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    command = arguments.command[1:] if arguments.command[:1] == ["--"] else arguments.command
    if not command:
        parser.error("needs the inventory command after --")
    try:
        cases = strategy_plan.planned_cases(strategy_plan._lines(arguments.strategy))
        modules = test_modules(frozen_files(arguments.root, arguments.slot), arguments.pattern)
        tests = collected(arguments.tree, command, arguments.timeout_minutes * 60)
    except (DiscoveryError, strategy_plan.PlanError) as error:
        print(f"discovery-check: {error}", file=sys.stderr)
        return 70
    listed = {test["module"] for test in tests}
    missing = [module for module in modules if module not in listed]
    if missing:
        print(f"discovery-check: the runner does not list {len(missing)} of the Tester's "
              f"{len(modules)} modules: {', '.join(missing)}. Fix the integrated tree's test "
              "configuration as an integration edit before Phase C.", file=sys.stderr)
        return 70
    gaps = strategy_plan.untested(
        cases, [test["name"] for test in tests if test["module"] in modules])
    if gaps["Critical"]:
        print("discovery-check: no collected test of the Tester's names these Critical planned "
              f"cases: {', '.join(gaps['Critical'])}. Send them back to the Tester.",
              file=sys.stderr)
        return 70
    print(json.dumps({"schema_version": "factory-discovery-check/1", "slot": arguments.slot,
                      "pattern": arguments.pattern, "command": command,
                      "modules": modules, "untested": gaps},
                     sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
