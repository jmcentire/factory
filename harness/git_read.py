#!/usr/bin/env python3
"""Read-only git for a coordinator seat: ``git_read.py -C PATH SUBCOMMAND [ARGS...]``.

An Antigravity seat's allowlist names commands by literal prefix, with no wildcard, so it
cannot name `git -C <path> log` for the run's candidate checkout and lane clones, whose paths
are chosen during the run. This one program is allowed instead and runs only reads. A
repository's own configuration cannot make it run anything: external diff drivers,
textconv, fsmonitor and hooks are switched off, and an option that writes a file is refused.
"""
from __future__ import annotations

import subprocess
import sys

READS = {"log", "show", "diff", "status", "rev-parse", "merge-base", "branch", "ls-files"}
# Diff output can run a repository's configured external diff or textconv program.
DIFFS = {"log", "show", "diff"}
SAFE = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "diff.external=",
        "--no-pager"]


def command(argv: list[str]) -> list[str]:
    """The git command to run, or ValueError naming why it is not a read."""

    if len(argv) < 3 or argv[0] != "-C":
        raise ValueError("usage: git_read.py -C PATH SUBCOMMAND [ARGS...]")
    path, subcommand, args = argv[1], argv[2], argv[3:]
    if subcommand not in READS:
        raise ValueError(f"`{subcommand}` is not one of: {', '.join(sorted(READS))}")
    if subcommand == "branch" and "--list" not in args:
        raise ValueError("branch is read only with --list")
    if any(arg.startswith(("--output", "--ext-diff", "--textconv")) for arg in args):
        raise ValueError("options that write a file or run a diff program are refused")
    extra = ["--no-ext-diff", "--no-textconv"] if subcommand in DIFFS else []
    return ["git", *SAFE, "-C", path, subcommand, *extra, *args]


def main(argv: list[str]) -> int:
    try:
        built = command(argv)
    except ValueError as error:
        print(f"git_read.py: {error}", file=sys.stderr)
        return 64
    return subprocess.run(built).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
