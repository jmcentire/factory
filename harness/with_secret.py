#!/usr/bin/env python3
"""Run one command with secrets in its environment, and never print a secret's value.

    with_secret.py [--secrets <dir>] ENV_NAME[=FILE] [...] -- <command> [args...]

Each secret is read from ``<dir>/FILE`` (FILE defaults to ENV_NAME; ``<dir>`` defaults to
``$HARNESS_SECRETS`` or ``~/.harness/secrets``, the lane_env.sh convention) and set as ENV_NAME
in that one child's environment only. ENV_NAME may be any name execve accepts, including ones
a shell cannot assign, such as npm's ``npm_config_//host/:_authToken``.

The failure this replaces: a token typed as a shell assignment the shell rejected was echoed back
in full in the error, into the seat transcript and the model API. Here the value never enters
argv, a shell, or a message. Every line the child writes to stdout or stderr has each secret's
exact bytes replaced with ``[REDACTED:ENV_NAME]`` before it is passed on; the exit status is the
child's. Redaction matches exact bytes only: a child that prints a secret transformed (encoded,
split across lines) is not caught, which is why the tripwire still scans transcripts after.
Errors name secrets, never their values. Prove a new use with a dummy value first.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import threading
from typing import IO

_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_USAGE = "usage: with_secret.py [--secrets <dir>] ENV_NAME[=FILE] ... -- <command> [args...]"


def _fail(message: str, code: int) -> int:
    print(f"with_secret: {message}", file=sys.stderr)
    return code


def _redact(source: IO[bytes], sink: IO[bytes], secrets: list[tuple[bytes, bytes]]) -> None:
    for line in iter(source.readline, b""):
        for value, mark in secrets:
            line = line.replace(value, mark)
        sink.write(line)
        sink.flush()


def main(argv: list[str]) -> int:
    if "--" not in argv:
        return _fail(_USAGE, 64)
    split = argv.index("--")
    specs, command = argv[:split], argv[split + 1 :]
    directory = pathlib.Path(
        os.environ.get("HARNESS_SECRETS") or pathlib.Path.home() / ".harness" / "secrets"
    )
    if specs[:1] == ["--secrets"] and len(specs) > 1:
        directory, specs = pathlib.Path(specs[1]), specs[2:]
    if not specs or not command:
        return _fail(_USAGE, 64)
    environment = dict(os.environ)
    secrets: list[tuple[bytes, bytes]] = []
    for spec in specs:
        name, _, file = spec.partition("=")
        file = file or name
        if not name or "\0" in name or not _FILE.fullmatch(file):
            return _fail(f"invalid secret spec for {name or '(empty)'!r}", 64)
        try:
            value = (directory / file).read_text(encoding="utf-8").rstrip("\r\n")
        except (OSError, UnicodeDecodeError):
            return _fail(f"secret {file} is missing or unreadable in {directory}", 78)
        if not value or "\n" in value or "\0" in value:
            return _fail(f"secret {file} must be one non-empty line", 78)
        environment[name] = value
        secrets.append((value.encode(), f"[REDACTED:{name}]".encode()))
    # Longest first, so a secret containing another is redacted whole.
    secrets.sort(key=lambda pair: len(pair[0]), reverse=True)
    try:
        child = subprocess.Popen(
            command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except OSError as exc:
        return _fail(f"cannot run {command[0]}: {exc.strerror}", 127)
    assert child.stdout is not None and child.stderr is not None
    pumps = [
        threading.Thread(target=_redact, args=(child.stdout, sys.stdout.buffer, secrets)),
        threading.Thread(target=_redact, args=(child.stderr, sys.stderr.buffer, secrets)),
    ]
    for pump in pumps:
        pump.start()
    status = child.wait()
    for pump in pumps:
        pump.join()
    return status if status >= 0 else 128 - status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
