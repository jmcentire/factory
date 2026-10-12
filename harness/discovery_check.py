#!/usr/bin/env python3
"""discovery_check — the integrated tree's test runner finds every test the Tester froze.

The invariant: **Phase C does not start on a tree whose runner cannot see the Tester's suite.**
In run c the Coder's projection hid tests/, so the Coder excluded tests/ in deno.json; the
integrated tree's ``deno test`` then reported "No test modules found" at Phase C, and the shipped
repository's own test task would have too (dogfood #42). After both lanes are frozen and the
tree is integrated, this runs the runner's own listing command in that tree and refuses when any
test module in the Tester's latest freeze is missing from its output.

  discovery_check.py --root <run> --tree <integrated tree> --pattern <glob> [--slot tester]
                     [--timeout-minutes 10] -- <listing command, e.g. deno test --no-run>

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

FREEZE_SCHEMA = "factory-tmux-lane-freeze/1"


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


def listing(tree: pathlib.Path, command: list[str], timeout_s: float) -> str:
    try:
        completed = subprocess.run(command, cwd=tree, stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, timeout=timeout_s,
                                   check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DiscoveryError(f"the listing command did not complete: {error}") from None
    return completed.stdout + completed.stderr


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="the runner discovers the Tester's modules")
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--tree", type=pathlib.Path, required=True)
    parser.add_argument("--pattern", required=True)
    parser.add_argument("--slot", default="tester")
    parser.add_argument("--timeout-minutes", type=float, default=10.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    command = arguments.command[1:] if arguments.command[:1] == ["--"] else arguments.command
    if not command:
        parser.error("needs the runner's listing command after --")
    try:
        modules = test_modules(frozen_files(arguments.root, arguments.slot), arguments.pattern)
        output = listing(arguments.tree, command, arguments.timeout_minutes * 60)
    except DiscoveryError as error:
        print(f"discovery-check: {error}", file=sys.stderr)
        return 70
    missing = [module for module in modules if module not in output]
    if missing:
        print(f"discovery-check: the runner does not list {len(missing)} of the Tester's "
              f"{len(modules)} modules: {', '.join(missing)}. Fix the integrated tree's test "
              "configuration as an integration edit before Phase C.", file=sys.stderr)
        return 70
    print(json.dumps({"schema_version": "factory-discovery-check/1", "slot": arguments.slot,
                      "pattern": arguments.pattern, "command": command,
                      "modules": modules}, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
