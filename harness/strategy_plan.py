#!/usr/bin/env python3
"""strategy_plan — what the agreed Testing Strategy binds the Tester lane to, checked by machine.

Two invariants, each found by hand mid-run when nothing checked them:

- **The Tester starts with every oracle input the Strategy cites, byte for byte** (dogfood #36,
  #65). The Strategy named oracle inputs its Tester projection never carried, and three of seven
  copies that it did carry had been reformatted by a formatter. A Strategy line
  ``oracle-input: <path in the Tester's tree> sha256:<digest of the pinned upstream bytes>``
  binds one input; ``oracle-inputs`` refuses a Tester repository where the file is missing, not
  a regular file, or holds other bytes.
- **The Tester freezes only when every Critical planned case has a test** (dogfood #73). A
  Tester declared DONE with 12 Critical planned cases unimplemented. A Strategy line
  ``planned-case: <case id> [Critical|Standard|Cosmetic]`` (no class means Critical: an
  unclassified surface is Critical) names one case; ``coverage`` refuses a frozen Tester
  export that names no Critical case id in any file, and reports every other gap.

Lines may sit in a Markdown list (``- oracle-input: ...``). Stdlib only, no package imports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import stat
import sys

ORACLE_INPUT = re.compile(
    r"^\s*(?:[-*]\s+)?oracle-input:\s*(?P<path>\S+)\s+sha256:(?P<digest>[0-9a-f]{64})\s*$"
)
PLANNED_CASE = re.compile(
    r"^\s*(?:[-*]\s+)?planned-case:\s*(?P<id>\S+)(?:\s+(?P<class>Critical|Standard|Cosmetic))?\s*$"
)
ORACLE_INPUT_LINE = re.compile(r"^\s*(?:[-*]\s+)?oracle-input:")
PLANNED_CASE_LINE = re.compile(r"^\s*(?:[-*]\s+)?planned-case:")


class PlanError(RuntimeError):
    pass


def _lines(strategy: pathlib.Path) -> list[str]:
    try:
        return strategy.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise PlanError(f"the testing strategy cannot be read: {error}") from None


def oracle_inputs(lines: list[str]) -> dict[str, str]:
    inputs: dict[str, str] = {}
    for number, line in enumerate(lines, 1):
        found = ORACLE_INPUT.match(line)
        if found is None:
            if ORACLE_INPUT_LINE.match(line):
                raise PlanError(
                    f"strategy line {number} is not 'oracle-input: <path> sha256:<hex>'"
                )
            continue
        path = pathlib.PurePosixPath(found["path"])
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise PlanError(f"strategy line {number}: unsafe oracle-input path {found['path']!r}")
        inputs[path.as_posix()] = found["digest"]
    return inputs


def planned_cases(lines: list[str]) -> dict[str, str]:
    cases: dict[str, str] = {}
    for number, line in enumerate(lines, 1):
        found = PLANNED_CASE.match(line)
        if found is None:
            if PLANNED_CASE_LINE.match(line):
                raise PlanError(f"strategy line {number} is not 'planned-case: <id> [class]'")
            continue
        cases[found["id"]] = found["class"] or "Critical"
    return cases


_NOFOLLOW = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)


def _contained_digest(repo: pathlib.Path, relative: str) -> str | None:
    """sha256 of the regular file at ``relative`` inside ``repo``, opened one component at a time
    with no-follow, so a linked ancestor cannot point outside the projection; None if absent."""

    parts = pathlib.PurePosixPath(relative).parts
    descriptors = [os.open(repo, _NOFOLLOW | os.O_DIRECTORY)]
    try:
        for part in parts[:-1]:
            descriptors.append(os.open(part, _NOFOLLOW | os.O_DIRECTORY, dir_fd=descriptors[-1]))
        descriptors.append(os.open(parts[-1], _NOFOLLOW, dir_fd=descriptors[-1]))
        if not stat.S_ISREG(os.fstat(descriptors[-1]).st_mode):
            return None
        digest = hashlib.sha256()
        while chunk := os.read(descriptors[-1], 1 << 16):
            digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        # ELOOP/ENOTDIR on a link, ENOENT when missing: neither is the Tester's own file.
        return None
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def missing_oracle_inputs(inputs: dict[str, str], repo: pathlib.Path) -> list[str]:
    missing = []
    for relative, digest in sorted(inputs.items()):
        actual = _contained_digest(repo, relative)
        if actual is None:
            missing.append(f"{relative} (absent)")  # or reached through a link
        elif actual != digest:
            missing.append(f"{relative} (bytes differ from the pinned sha256:{digest[:12]})")
    return missing


def uncovered(cases: dict[str, str], files: pathlib.Path) -> dict[str, list[str]]:
    # A copy of the Strategy in the Tester's tree names every case; it is not a test.
    # ponytail: any other file naming a case id counts as covering it; a declared test-module
    # glob would tighten this if a spec copy starts masking gaps.
    texts = (path.read_text(encoding="utf-8", errors="replace")
             for path in sorted(files.rglob("*")) if path.is_file() and not path.is_symlink())
    corpus = "\n".join(text for text in texts
                       if not any(PLANNED_CASE_LINE.match(line) for line in text.splitlines()))
    gaps: dict[str, list[str]] = {"Critical": [], "Standard": [], "Cosmetic": []}
    for case, criticality in cases.items():
        if not re.search(rf"(?<![\w.-]){re.escape(case)}(?![\w-])(?!\.\w)", corpus):
            gaps[criticality].append(case)
    return gaps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="check the Tester lane against the Strategy")
    commands = parser.add_subparsers(dest="command", required=True)
    inputs = commands.add_parser("oracle-inputs")
    inputs.add_argument("--strategy", type=pathlib.Path, required=True)
    inputs.add_argument("--repo", type=pathlib.Path, required=True)
    coverage = commands.add_parser("coverage")
    coverage.add_argument("--strategy", type=pathlib.Path, required=True)
    coverage.add_argument("--files", type=pathlib.Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        lines = _lines(arguments.strategy)
        if arguments.command == "oracle-inputs":
            cited = oracle_inputs(lines)
            missing = missing_oracle_inputs(cited, arguments.repo)
            if missing:
                print("strategy-plan: the Tester's tree lacks the authentic bytes of oracle "
                      f"inputs the Strategy cites: {'; '.join(missing)}", file=sys.stderr)
                return 70
            print(json.dumps({"oracle_inputs": cited}, sort_keys=True, separators=(",", ":")))
            return 0
        gaps = uncovered(planned_cases(lines), arguments.files)
        print(json.dumps({"plan_gaps": gaps}, sort_keys=True, separators=(",", ":")))
        if gaps["Critical"]:
            print("strategy-plan: no test names these Critical planned cases: "
                  f"{', '.join(gaps['Critical'])}", file=sys.stderr)
            return 70
        return 0
    except PlanError as error:
        print(f"strategy-plan: {error}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
