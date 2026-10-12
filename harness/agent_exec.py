#!/usr/bin/env python3
"""agent_exec — run a non-interactive agent CLI (``codex exec``, ``claude -p``) bounded.

The invariant: **a non-interactive agent call never waits on a terminal and never runs
unbounded.** In run e a cross-family review launched as ``codex exec`` with stdin left open sat
on "Reading additional input from stdin..." for 27 minutes while the Validator reported it as
running (dogfood #55). Here stdin is ``/dev/null`` (or the named prompt file), the call has a
wall-clock cap, and on the cap the whole process group is stopped and the exit is 124, the
code GNU ``timeout`` uses, which macOS does not ship.

  agent_exec.py --timeout-minutes <n> [--stdin-file <prompt>] [--log <file>] -- <argv...>

Output goes to ``--log`` (appended) or to this process's stdout. Stdlib only.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import signal
import subprocess
import sys

TIMED_OUT = 124


def run(argv: list[str], *, timeout_s: float, stdin_file: pathlib.Path | None,
        log: pathlib.Path | None) -> int:
    stdin = stdin_file.open("rb") if stdin_file else subprocess.DEVNULL
    stdout = log.open("ab") if log else None
    try:
        process = subprocess.Popen(argv, stdin=stdin, stdout=stdout, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            return process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            print(f"agent-exec: stopped after {timeout_s / 60:g} min: {argv[0]}", file=sys.stderr)
            return TIMED_OUT
    finally:
        if stdin_file:
            stdin.close()  # type: ignore[union-attr]
        if stdout:
            stdout.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="run an agent CLI with stdin closed and a cap")
    parser.add_argument("--timeout-minutes", type=float, required=True)
    parser.add_argument("--stdin-file", type=pathlib.Path)
    parser.add_argument("--log", type=pathlib.Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    command = arguments.command[1:] if arguments.command[:1] == ["--"] else arguments.command
    if not command or arguments.timeout_minutes <= 0:
        parser.error("needs a positive --timeout-minutes and a command after --")
    return run(command, timeout_s=arguments.timeout_minutes * 60,
               stdin_file=arguments.stdin_file, log=arguments.log)


if __name__ == "__main__":
    raise SystemExit(main())
