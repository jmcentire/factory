#!/usr/bin/env python3
"""Start run-owned processes on a ledger, and signal only what the ledger recorded.

    spawn.py start  --root <run> --name <label> -- <command> [args...]
    spawn.py signal --root <run> --name <label> [--signal TERM]
    spawn.py sweep  --root <run> [--signal TERM]

Run e's Validator chose a SIGTERM target with ``pgrep -f "deno run --frozen"``, signalled a
process that was not its child, and retried on the wrong result (HALT 1287); runs also left
orphaned processes behind twice. A process a run starts is therefore started here: in its own
process group, with its pid, pgid and start time appended to ``<run>/spawns.jsonl`` and its
output in ``<run>/spawns/<label>.log``. ``signal`` addresses only that recorded group, and only
while the pid still has the recorded start time, so a reused pid is refused, never signalled.
``sweep`` signals every recorded group still alive: the end-of-step orphan sweep. Nothing here
ever searches the process table by name or pattern.
"""

from __future__ import annotations

import argparse
import datetime
import fcntl
import json
import os
import pathlib
import re
import signal
import subprocess
import sys

_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _started(pid: int) -> str | None:
    """The kernel's start time for pid, or None when no such process exists."""
    result = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or None


def _ledger(root: pathlib.Path) -> list[dict[str, object]]:
    try:
        lines = (root / "spawns.jsonl").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in lines if line.strip()]


def start(root: pathlib.Path, name: str, command: list[str]) -> int:
    (root / "spawns").mkdir(exist_ok=True)
    # The label check, the launch and the ledger row happen under one lock, so two concurrent
    # starts cannot both pass the check and record two groups under one label.
    with open(root / "spawns.jsonl", "a+", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        if any(json.loads(line)["name"] == name for line in stream.read().splitlines()
               if line.strip()):
            print(f"spawn: {name} is already recorded; choose a new label", file=sys.stderr)
            return 65
        with open(root / "spawns" / f"{name}.log", "ab") as log:
            child = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        stream.write(json.dumps({
            "name": name, "pid": child.pid, "pgid": child.pid, "argv": command,
            "started": _started(child.pid),
            "ts": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        }, sort_keys=True, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(child.pid)
    return 0


def _signal(row: dict[str, object], signum: int) -> str:
    pid, pgid = int(str(row["pid"])), int(str(row["pgid"]))
    current = _started(pid)
    if current is None:
        # ponytail: a group whose leader exited is not signalled, since its pgid cannot be told
        # apart from a reused one; record each long-lived child with its own start if that bites.
        return "exited"
    if current != row["started"]:
        return "refused: pid now belongs to another process"
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        return "exited"
    return f"signalled group {pgid}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="spawn.py")
    parser.add_argument("action", choices=("start", "signal", "sweep"))
    parser.add_argument("--root", required=True, type=pathlib.Path)
    parser.add_argument("--name")
    parser.add_argument("--signal", default="TERM")
    split = argv.index("--") if "--" in argv else len(argv)
    args = parser.parse_args(argv[:split])
    command = argv[split + 1 :]
    if not args.root.is_dir():
        parser.error(f"run root does not exist: {args.root}")
    try:
        signum = signal.Signals[f"SIG{args.signal.upper().removeprefix('SIG')}"]
    except KeyError:
        parser.error(f"unknown signal: {args.signal}")
    if args.action == "start":
        if not args.name or not _LABEL.fullmatch(args.name) or not command:
            parser.error("start needs --name <label> -- <command>")
        return start(args.root, args.name, command)
    rows = _ledger(args.root)
    if args.action == "signal":
        matches = [row for row in rows if row["name"] == args.name]
        if not matches:
            print(f"spawn: {args.name} is not in this run's spawn ledger; nothing signalled",
                  file=sys.stderr)
            return 66
        outcome = _signal(matches[0], signum)
        print(f"{args.name}: {outcome}")
        return 75 if outcome.startswith("refused") else 0
    refused = False
    for row in rows:
        outcome = _signal(row, signum)
        refused |= outcome.startswith("refused")
        print(f"{row['name']}: {outcome}")
    return 75 if refused else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
