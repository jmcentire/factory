#!/usr/bin/env python3
"""CLI entrypoint for host Git over a repository an agent could have written.

``factory_runtime/host_git.py`` states the threat and the invariant. Shell harness code calls
this instead of ``git -C <lane or candidate>``: ``resolve`` and ``materialize`` read exactly one
commit from raw objects, and ``run`` runs any other Git command under the closed environment.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import sys

from factory_runtime.host_git import (
    HostGitError,
    MaterializedCommit,
    materialize_commit,
    resolve_commit_tree,
    run_host_git,
)

# A judged checkout is the target's whole tree; the ceilings stop a runaway, not a real repo.
MAX_FILES = 200_000
MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    resolve = commands.add_parser("resolve", help="print the tree of exactly one commit")
    resolve.add_argument("--repository", type=pathlib.Path, required=True)
    resolve.add_argument("--commit", required=True)
    materialize = commands.add_parser(
        "materialize", help="write exactly one commit's tree into a new directory"
    )
    materialize.add_argument("--repository", type=pathlib.Path, required=True)
    materialize.add_argument("--commit", required=True)
    materialize.add_argument("--destination", type=pathlib.Path, required=True)
    materialize.add_argument("--admit-symlinks", action="store_true")
    run = commands.add_parser("run", help="run one Git command under the closed environment")
    run.add_argument("--repository", type=pathlib.Path, required=True)
    run.add_argument("arguments", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "resolve":
            print(resolve_commit_tree(arguments.repository, arguments.commit))
        elif arguments.command == "materialize":
            result: MaterializedCommit = materialize_commit(
                arguments.repository,
                arguments.commit,
                arguments.destination,
                admit_symlinks=arguments.admit_symlinks,
                max_files=MAX_FILES,
                max_total_bytes=MAX_TOTAL_BYTES,
            )
            print(json.dumps(dataclasses.asdict(result), sort_keys=True))
        else:
            git_arguments = list(arguments.arguments)
            if git_arguments[:1] == ["--"]:
                git_arguments = git_arguments[1:]
            sys.stdout.buffer.write(run_host_git(arguments.repository, git_arguments))
    except HostGitError as error:
        print(f"host-git: {error}", file=sys.stderr)
        return 70
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
