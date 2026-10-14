#!/usr/bin/env python3
"""strategy_plan — what the agreed Testing Strategy binds the Tester lane to, checked by machine.

Two invariants, each found by hand mid-run when nothing checked them:

- **The Tester starts with every oracle input the Strategy cites, byte for byte** (dogfood #36,
  #65). The Strategy named oracle inputs its Tester projection never carried, and three of seven
  copies that it did carry had been reformatted by a formatter. A Strategy line
  ``oracle-input: <path in the Tester's tree> sha256:<digest of the pinned upstream bytes>``
  binds one input; ``oracle-inputs`` refuses a Tester repository where the file is missing, not
  a regular file, or holds other bytes.
- **Every Critical planned case has a test** (dogfood #73). A Tester declared DONE with 12
  Critical planned cases unimplemented. A Strategy line
  ``planned-case: <case id> [Critical|Standard|Cosmetic]`` (no class means Critical: an
  unclassified surface is Critical) names one case, and ``test-modules: <glob>`` (repeatable;
  ``fnmatch``, ``*`` crosses ``/``; the same glob ``discovery_check.py --pattern`` takes) names
  the Tester's test modules. Two checks read them:

  - ``traceability`` (at the Tester's freeze, before any runner can import the tests) is a
    source-reference check only: it refuses a frozen export in which a Critical case id appears
    in no test module outside a full-line comment. A README or inventory naming a case does not
    count, but a string constant or trailing comment does, so it proves no test exists.
  - ``untested`` (called by ``discovery_check.py --strategy`` on the integrated tree) is the
    planned-test check: a case is tested only when its id appears in the name of a runnable
    (not skipped or ignored) test the runner collected (``test_inventory.py``) from one of the
    Tester's modules.

Lines may sit in a Markdown list (``- oracle-input: ...``). Stdlib only, no package imports.
"""

from __future__ import annotations

import argparse
import fnmatch
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
TEST_MODULES = re.compile(r"^\s*(?:[-*]\s+)?test-modules:\s*(?P<glob>\S+)\s*$")
COMMENT_LINE = re.compile(r"^\s*(?://|#|/?\*|--|;|<!--)")


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


def test_module_globs(lines: list[str]) -> list[str]:
    return [found["glob"] for line in lines if (found := TEST_MODULES.match(line))]


def names_case(case: str, text: str) -> bool:
    """``case`` appears as a whole id: R7.1 is not named by R7.10, R7.1.2 or R7.1-b."""

    return re.search(rf"(?<![\w.-]){re.escape(case)}(?![\w-])(?!\.\w)", text) is not None


def _gaps(cases: dict[str, str], text: str) -> dict[str, list[str]]:
    gaps: dict[str, list[str]] = {"Critical": [], "Standard": [], "Cosmetic": []}
    for case, criticality in cases.items():
        if not names_case(case, text):
            gaps[criticality].append(case)
    return gaps


def untested(cases: dict[str, str], test_names: list[str]) -> dict[str, list[str]]:
    """Cases whose id is in no collected test's name, by class."""

    return _gaps(cases, "\n".join(test_names))


def untraced(cases: dict[str, str], files: pathlib.Path,
             globs: list[str]) -> dict[str, list[str]]:
    if cases and not globs:
        raise PlanError("the strategy names planned cases but no 'test-modules: <glob>' line, "
                        "so no file can be told to be a test")
    modules = [path for path in sorted(files.rglob("*"))
               if path.is_file() and not path.is_symlink()
               and any(fnmatch.fnmatch(path.relative_to(files).as_posix(), g) for g in globs)]
    # ponytail: full-line comments are dropped by their leading marker; a trailing comment on a
    # code line still counts. A per-language tokenizer would close that if it masks gaps.
    corpus = "\n".join(line for path in modules
                       for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
                       if not COMMENT_LINE.match(line))
    return _gaps(cases, corpus)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="check the Tester lane against the Strategy")
    commands = parser.add_subparsers(dest="command", required=True)
    inputs = commands.add_parser("oracle-inputs")
    inputs.add_argument("--strategy", type=pathlib.Path, required=True)
    inputs.add_argument("--repo", type=pathlib.Path, required=True)
    traceability = commands.add_parser("traceability")
    traceability.add_argument("--strategy", type=pathlib.Path, required=True)
    traceability.add_argument("--files", type=pathlib.Path, required=True)
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
        gaps = untraced(planned_cases(lines), arguments.files, test_module_globs(lines))
        print(json.dumps({"traceability_gaps": gaps}, sort_keys=True, separators=(",", ":")))
        if gaps["Critical"]:
            print("strategy-plan: no test module mentions these Critical planned cases: "
                  f"{', '.join(gaps['Critical'])}", file=sys.stderr)
            return 70
        return 0
    except PlanError as error:
        print(f"strategy-plan: {error}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
