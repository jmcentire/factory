#!/usr/bin/env python3
"""Run one advisory model process with code-owned time and output ceilings."""

from __future__ import annotations

import argparse
import os
import selectors
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import BinaryIO, cast


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--stdin", required=True)
    parser.add_argument("--stdout", required=True)
    parser.add_argument("--stderr", required=True)
    parser.add_argument("--wall-seconds", required=True, type=float)
    parser.add_argument("--max-output-bytes", required=True, type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def _open_regular_input(path: Path) -> BinaryIO:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError("advisory stdin must be a regular file")
    return os.fdopen(descriptor, "rb")


def _write_once(path: Path, content: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _descendants(pid: int) -> list[int]:
    """Snapshot descendants deepest-first without adding a runtime dependency."""

    try:
        completed = subprocess.run(
            ["/bin/ps", "-axo", "pid=,ppid="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    children: dict[int, list[int]] = {}
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            child, parent = map(int, fields)
        except ValueError:
            continue
        children.setdefault(parent, []).append(child)
    ordered: list[int] = []
    frontier = [pid]
    while frontier:
        parent = frontier.pop()
        direct = children.get(parent, [])
        frontier.extend(direct)
        ordered.extend(direct)
    ordered.reverse()
    return ordered


def _signal_tree(process: subprocess.Popen[bytes], signum: signal.Signals) -> None:
    for pid in [*_descendants(process.pid), process.pid]:
        try:
            os.kill(pid, signum)
        except ProcessLookupError:
            pass


def _stop_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    _signal_tree(process, signal.SIGTERM)
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        _signal_tree(process, signal.SIGKILL)
        process.wait(timeout=2)


def supervise(
    command: list[str],
    *,
    cwd: Path,
    stdin_path: Path,
    stdout_path: Path,
    stderr_path: Path,
    wall_seconds: float,
    max_output_bytes: int,
) -> int:
    if not command:
        raise ValueError("an advisory command is required")
    if wall_seconds <= 0 or max_output_bytes <= 0:
        raise ValueError("advisory ceilings must be positive")
    if cwd.is_symlink() or not cwd.is_dir():
        raise ValueError("advisory cwd must be a real directory")

    stdout = bytearray()
    stderr = bytearray()
    termination_reason = ""
    started = time.monotonic()
    with _open_regular_input(stdin_path) as prompt:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=prompt,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
        )
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        streams = ((process.stdout, stdout), (process.stderr, stderr))
        for stream, destination in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, destination)
        try:
            while selector.get_map():
                if time.monotonic() - started >= wall_seconds:
                    termination_reason = "advisory wall-time ceiling exceeded"
                    _stop_tree(process)
                    break
                for key, _ in selector.select(timeout=0.1):
                    stream = cast(BinaryIO, key.fileobj)
                    destination = key.data
                    try:
                        chunk = os.read(stream.fileno(), 8192)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    remaining = max_output_bytes - len(stdout) - len(stderr)
                    if remaining <= 0 or len(chunk) > remaining:
                        destination.extend(chunk[: max(0, remaining)])
                        termination_reason = "advisory output ceiling exceeded"
                        _stop_tree(process)
                        break
                    destination.extend(chunk)
                if termination_reason:
                    break
            if process.poll() is None:
                _stop_tree(process)
            returncode = process.wait(timeout=2)
        finally:
            selector.close()
            for stream, _ in streams:
                stream.close()

    if termination_reason:
        marker = f"\n[{termination_reason}]\n".encode()
        remaining = max_output_bytes - len(stdout) - len(stderr)
        if remaining > 0:
            stderr.extend(marker[:remaining])
    _write_once(stdout_path, bytes(stdout))
    _write_once(stderr_path, bytes(stderr))
    if termination_reason == "advisory output ceiling exceeded":
        return 74
    if termination_reason:
        return 124
    return returncode if 0 <= returncode <= 255 else 1


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    command = list(arguments.command)
    if command and command[0] == "--":
        command = command[1:]
    try:
        return supervise(
            command,
            cwd=Path(arguments.cwd),
            stdin_path=Path(arguments.stdin),
            stdout_path=Path(arguments.stdout),
            stderr_path=Path(arguments.stderr),
            wall_seconds=arguments.wall_seconds,
            max_output_bytes=arguments.max_output_bytes,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        message = f"advisory supervisor refused: {exc}\n"
        try:
            if not Path(arguments.stdout).exists():
                _write_once(Path(arguments.stdout), b"")
            if not Path(arguments.stderr).exists():
                _write_once(Path(arguments.stderr), message.encode())
        except OSError:
            pass
        print(message, file=sys.stderr, end="")
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
