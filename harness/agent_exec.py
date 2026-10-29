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
import time

TIMED_OUT = 124


def run(argv: list[str], *, timeout_s: float, stdin_file: pathlib.Path | None,
        log: pathlib.Path | None) -> int:
    cancelled = 0

    def cancel(signum: int, _frame: object) -> None:
        nonlocal cancelled
        cancelled = signum

    previous = {sig: signal.signal(sig, cancel) for sig in (signal.SIGTERM, signal.SIGINT)}
    stdin = None
    stdout = None
    process = None
    try:
        stdin = stdin_file.open("rb") if stdin_file else None
        stdout = log.open("ab") if log else None
        process = subprocess.Popen(argv, stdin=stdin or subprocess.DEVNULL, stdout=stdout,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + timeout_s
        while not cancelled:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                return process.wait(timeout=min(remaining, 0.1))
            except subprocess.TimeoutExpired:
                continue
        if cancelled:
            return 128 + cancelled
        print(f"agent-exec: stopped after {timeout_s / 60:g} min: {argv[0]}", file=sys.stderr)
        return TIMED_OUT
    finally:
        try:
            if process is not None and process.returncode is None:
                stop_group(process)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            if stdin:
                stdin.close()
            if stdout:
                stdout.close()


def stop_group(process: subprocess.Popen[bytes]) -> None:
    """Stop only our unreaped session leader's group, then reap it.

    Keeping the child unreaped pins its PID until the final signal, including when it exits
    during cleanup. Waiting before the group kill would allow PID/group reuse and could leave
    TERM-resistant descendants running after their parent exited.
    """

    if process.returncode is not None:
        return
    try:
        if os.getpgid(process.pid) != process.pid or os.getsid(process.pid) != process.pid:
            raise RuntimeError("agent-exec: child no longer owns its launched session")
    except ProcessLookupError:
        # macOS no longer exposes a zombie leader's session; its unreaped PID still pins it.
        pass
    try:
        for signum in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, signum)
            except ProcessLookupError:
                break
            except PermissionError:
                # macOS reports EPERM for a group containing only zombies. Do not suppress
                # a permission failure while any live member remains.
                rows = subprocess.check_output(
                    ["ps", "-axo", "pgid=,stat="], text=True, timeout=5,
                )
                if any(group == str(process.pid) and not state.startswith("Z")
                       for group, state in (row.split() for row in rows.splitlines())):
                    raise
                break
            if signum == signal.SIGTERM:
                time.sleep(1)
    finally:
        process.wait(timeout=5)


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
