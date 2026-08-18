#!/usr/bin/env python3
"""Run one advisory model process with code-owned time and output ceilings."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO, cast


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--stdin", required=True)
    parser.add_argument("--stdin-mode", choices=("prompt", "closed"), default="prompt")
    parser.add_argument("--stdout", required=True)
    parser.add_argument("--stderr", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--wall-seconds", required=True, type=float)
    parser.add_argument("--max-input-bytes", required=True, type=int)
    parser.add_argument("--max-output-bytes", required=True, type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def _read_stable_regular_input(path: Path, max_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("advisory stdin must be a regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw = stream.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError("advisory stdin exceeds its byte ceiling")
            stream.seek(0)
            confirmed = stream.read(max_bytes + 1)
            after = os.fstat(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    def identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
    if raw != confirmed or identity(before) != identity(after) or before.st_size != len(raw):
        raise ValueError("advisory stdin changed while it was admitted")
    return raw


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


def _write_json_once(path: Path, document: dict[str, object]) -> None:
    _write_once(
        path,
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n",
    )


def _process_group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_group(pgid: int, signum: signal.Signals) -> None:
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        pass


def _stop_tree(process: subprocess.Popen[bytes]) -> None:
    # The client is a session/process-group leader. Its group survives when the
    # principal exits, so pre-exited parents cannot hide TERM-tolerant children by
    # re-parenting them before the ceiling fires.
    _signal_group(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + 0.5
    while _process_group_exists(process.pid) and time.monotonic() < deadline:
        time.sleep(min(0.05, deadline - time.monotonic()))
    process.poll()  # reap a terminated leader so an empty group is removed before KILL
    _signal_group(process.pid, signal.SIGKILL)
    if process.poll() is None:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("advisory principal survived SIGKILL") from exc


@contextlib.contextmanager
def _capture_termination_signals(received: list[int]) -> Iterator[None]:
    def record_signal(signum: int, _frame: object) -> None:
        received.append(signum)

    handled = (signal.SIGTERM, signal.SIGINT)
    previous = {signum: signal.signal(signum, record_signal) for signum in handled}
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def supervise(
    command: list[str],
    *,
    cwd: Path,
    stdin_path: Path,
    stdout_path: Path,
    stderr_path: Path,
    receipt_path: Path,
    stdin_mode: str,
    wall_seconds: float,
    max_input_bytes: int,
    max_output_bytes: int,
) -> int:
    if not command:
        raise ValueError("an advisory command is required")
    if wall_seconds <= 0 or max_input_bytes <= 0 or max_output_bytes <= 0:
        raise ValueError("advisory ceilings must be positive")
    if cwd.is_symlink() or not cwd.is_dir():
        raise ValueError("advisory cwd must be a real directory")

    stdout = bytearray()
    stderr = bytearray()
    termination_reason = ""
    output_truncated = False
    started = time.monotonic()
    received_signal: list[int] = []

    input_bytes = (
        _read_stable_regular_input(stdin_path, max_input_bytes)
        if stdin_mode == "prompt"
        else b""
    )
    input_stream: BinaryIO
    if stdin_mode == "prompt":
        input_stream = tempfile.TemporaryFile(mode="w+b")
        input_stream.write(input_bytes)
        input_stream.flush()
        input_stream.seek(0)
    else:
        input_stream = open(os.devnull, "rb")
    with _capture_termination_signals(received_signal), input_stream as prompt:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=prompt,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
        )
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        streams = ((process.stdout, stdout), (process.stderr, stderr))
        for stream, destination in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, destination)
        try:
            while True:
                process.poll()  # reap an exited leader before checking whether its group remains
                group_exists = _process_group_exists(process.pid)
                if not selector.get_map() and not group_exists:
                    break
                if received_signal:
                    signal_name = signal.Signals(received_signal[0]).name
                    termination_reason = f"advisory supervisor interrupted by {signal_name}"
                    _stop_tree(process)
                    break
                if time.monotonic() - started >= wall_seconds:
                    termination_reason = "advisory wall-time ceiling exceeded"
                    _stop_tree(process)
                    break
                if not selector.get_map():
                    time.sleep(0.05)
                    continue
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
                        output_truncated = True
                        _stop_tree(process)
                        break
                    destination.extend(chunk)
                if termination_reason:
                    break
            if process.poll() is None or _process_group_exists(process.pid):
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
    stdout_bytes = bytes(stdout)
    stderr_bytes = bytes(stderr)
    _write_once(stdout_path, stdout_bytes)
    _write_once(stderr_path, stderr_bytes)
    if termination_reason == "advisory output ceiling exceeded":
        supervisor_exit_code = 74
    elif termination_reason:
        supervisor_exit_code = 124
    else:
        supervisor_exit_code = returncode if 0 <= returncode <= 255 else 1
    _write_json_once(
        receipt_path,
        {
            "schema_version": "factory-advisory-supervisor-receipt/1",
            "input_admitted": True,
            "stdin_mode": stdin_mode,
            "input_digest": "sha256:" + hashlib.sha256(input_bytes).hexdigest(),
            "input_byte_count": len(input_bytes),
            "stdout_digest": "sha256:" + hashlib.sha256(stdout_bytes).hexdigest(),
            "stdout_byte_count": len(stdout_bytes),
            "stderr_digest": "sha256:" + hashlib.sha256(stderr_bytes).hexdigest(),
            "stderr_byte_count": len(stderr_bytes),
            "combined_output_truncated": output_truncated,
            "termination_reason": termination_reason,
            "client_returncode": returncode,
            "supervisor_exit_code": supervisor_exit_code,
        },
    )
    return supervisor_exit_code


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
            receipt_path=Path(arguments.receipt),
            stdin_mode=arguments.stdin_mode,
            wall_seconds=arguments.wall_seconds,
            max_input_bytes=arguments.max_input_bytes,
            max_output_bytes=arguments.max_output_bytes,
        )
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        message = f"advisory supervisor refused: {exc}\n"
        try:
            if not Path(arguments.stdout).exists():
                _write_once(Path(arguments.stdout), b"")
            if not Path(arguments.stderr).exists():
                _write_once(Path(arguments.stderr), message.encode())
            if not Path(arguments.receipt).exists():
                _write_json_once(
                    Path(arguments.receipt),
                    {
                        "schema_version": "factory-advisory-supervisor-receipt/1",
                        "input_admitted": False,
                        "stdin_mode": arguments.stdin_mode,
                        "input_digest": "sha256:" + hashlib.sha256(b"").hexdigest(),
                        "input_byte_count": 0,
                        "stdout_digest": "sha256:" + hashlib.sha256(b"").hexdigest(),
                        "stdout_byte_count": 0,
                        "stderr_digest": "sha256:" + hashlib.sha256(message.encode()).hexdigest(),
                        "stderr_byte_count": len(message.encode()),
                        "combined_output_truncated": False,
                        "termination_reason": "supervisor-refused",
                        "client_returncode": None,
                        "supervisor_exit_code": 70,
                    },
                )
        except OSError:
            pass
        print(message, file=sys.stderr, end="")
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
