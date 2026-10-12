#!/usr/bin/env python3
"""strategy_plan — what the agreed Testing Strategy binds the Tester lane to, checked by machine.

- **The Tester starts with every oracle input the Strategy cites, byte for byte** (dogfood #36,
  #65). The Strategy named oracle inputs its Tester projection never carried, and three of seven
  copies that it did carry had been reformatted by a formatter. A Strategy line
  ``oracle-input: <path in the Tester's tree> sha256:<digest of the pinned upstream bytes>``
  binds one input; ``oracle-inputs`` refuses a Tester repository where the file is missing, not
  a regular file, or holds other bytes.

Lines may sit in a Markdown list (``- oracle-input: ...``). Stdlib only, no package imports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

ORACLE_INPUT = re.compile(
    r"^\s*(?:[-*]\s+)?oracle-input:\s*(?P<path>\S+)\s+sha256:(?P<digest>[0-9a-f]{64})\s*$"
)
ORACLE_INPUT_LINE = re.compile(r"^\s*(?:[-*]\s+)?oracle-input:")


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


def missing_oracle_inputs(inputs: dict[str, str], repo: pathlib.Path) -> list[str]:
    missing = []
    for relative, digest in sorted(inputs.items()):
        path = repo / relative
        if path.is_symlink() or not path.is_file():
            missing.append(f"{relative} (absent)")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            missing.append(f"{relative} (bytes differ from the pinned sha256:{digest[:12]})")
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="check the Tester lane against the Strategy")
    commands = parser.add_subparsers(dest="command", required=True)
    inputs = commands.add_parser("oracle-inputs")
    inputs.add_argument("--strategy", type=pathlib.Path, required=True)
    inputs.add_argument("--repo", type=pathlib.Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        lines = _lines(arguments.strategy)
        cited = oracle_inputs(lines)
        missing = missing_oracle_inputs(cited, arguments.repo)
        if missing:
            print("strategy-plan: the Tester's tree lacks the authentic bytes of oracle "
                  f"inputs the Strategy cites: {'; '.join(missing)}", file=sys.stderr)
            return 70
        print(json.dumps({"oracle_inputs": cited}, sort_keys=True, separators=(",", ":")))
        return 0
    except PlanError as error:
        print(f"strategy-plan: {error}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
