#!/usr/bin/env python3
"""lane_agent — the agents a tmux author lane can run, their sign-in preflight, and the wrapper
every lane process runs under.

The invariant: **a lane is never started on an agent that would sit on a login screen, and every
lane process leaves an exit record the lane watchdog reads without trusting the agent's output.**
The msg-r2 run lost hours to an author agent that fell back to an interactive sign-in, and lanes
that finished sat idle because nothing recorded that they had ended
(docs/practices/lessons-msg-r2-2026-10.md §3-§4).

  describe   one agent's launch shape as NUL-terminated fields, for tmux_lane.sh: executable,
             prompt delivery (stdin or argv), whether it needs a terminal, and whether it is
             metered by construction.
  preflight  check one agent's sign-in non-interactively: stdin closed, bounded time, run under
             the exact scrubbed environment the lane gets. Exit 0 when signed in; exit 77 with
             the exact remediation command when it is not, or when the check itself waits for
             input (that is the login screen the lane would have sat on).
  run        run one lane process. It tees the output to the lane log (a terminal agent keeps
             the terminal instead), and when the process ends, including when the watchdog
             stops it with SIGTERM, appends one exit record to the lane's exits journal.

Agents are named here once. ``factory_runtime/model_profiles.py`` holds which roles may bind
which agents (LAUNCHABLE) and which agents are metered by construction (METERED_AGENTS);
tests hold the two lists and ``tmux_lane.sh``'s case list equal.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import shlex
import signal
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from factory_runtime.model_profiles import METERED_AGENTS  # noqa: E402 - repository root above

EXIT_SCHEMA = "factory-lane-exit/1"
REFUSED = 77
# A prompt passed as one argv element must fit the smallest per-argument limit we run on
# (Linux MAX_ARG_STRLEN is 128 KiB); a larger prompt is refused, never truncated.
MAX_ARGV_PROMPT = 128 * 1024 - 1
# macOS sun_path is 104 bytes including the terminator. An interactive Codex whose IPC socket
# path is longer cannot bind it and falls back to its login onboarding (the msg-r2 failure).
MAX_SOCKET_PATH = 103
CODEX_SOCKET = "ipc/ipc.sock"


class PreflightRefused(RuntimeError):
    """The agent is not signed in, or its sign-in check would wait for a person."""

    def __init__(self, message: str, remediation: str) -> None:
        super().__init__(message)
        self.remediation = remediation


@dataclass(frozen=True)
class Agent:
    name: str
    executable: str
    prompt_via: str  # "stdin" (the Codex session wrapper feeds it) or "argv" (last argument)
    terminal: bool  # a TUI that needs the pane's terminal; its output is not teed
    auth: str  # which sign-in check applies

    @property
    def metered(self) -> bool:
        return self.name in METERED_AGENTS


AGENTS: Mapping[str, Agent] = {
    # `codex exec`, non-interactive: the default for author lanes.
    "codex": Agent("codex", "codex", "stdin", False, "codex-login"),
    # The Codex TUI, for an operator who wants to watch or steer a lane by hand.
    "codex-interactive": Agent("codex-interactive", "codex", "argv", True, "codex-login"),
    # `codex exec --oss` on a model the local Ollama server already has.
    "codex-ollama": Agent("codex-ollama", "codex", "stdin", False, "ollama-model"),
    # Cursor's agent CLI in print mode: `agent -p --model <m> --force --trust --workspace <lane>`.
    "cursor-agent": Agent("cursor-agent", "agent", "argv", False, "cursor-status"),
}


def agent(name: str) -> Agent:
    try:
        return AGENTS[name]
    except KeyError:
        raise PreflightRefused(
            f"unknown lane agent {name!r}", f"choose one of: {', '.join(AGENTS)}"
        ) from None


# -- preflight -----------------------------------------------------------------------------------


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _status(argv: Sequence[str], *, timeout: float, runner: Runner, remediation: str,
            label: str) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            list(argv),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise PreflightRefused(
            f"the {label} sign-in check did not answer within {timeout:g}s with stdin closed: "
            "it is waiting for an interactive step, so the lane would sit on a login screen",
            remediation,
        ) from None
    except OSError as error:
        raise PreflightRefused(
            f"the {label} CLI could not be run ({error})",
            f"install {argv[0]} and put it on PATH",
        ) from None


def preflight(
    name: str,
    *,
    environ: Mapping[str, str],
    timeout: float = 20.0,
    runner: Runner = subprocess.run,
) -> str:
    """Return how sign-in was established, or raise PreflightRefused with the exact fix."""

    chosen = agent(name)
    if chosen.auth == "codex-login":
        home = environ.get("CODEX_HOME") or str(pathlib.Path(environ.get("HOME", "~")) / ".codex")
        quoted = shlex.quote(home)
        remediation = (
            f"CODEX_HOME={quoted} codex login --device-auth   "
            f"(or, with an API key: printenv OPENAI_API_KEY | CODEX_HOME={quoted} "
            "codex login --with-api-key)"
        )
        if chosen.terminal:
            socket = pathlib.Path(home) / CODEX_SOCKET
            if len(str(socket).encode("utf-8")) > MAX_SOCKET_PATH:
                raise PreflightRefused(
                    f"interactive Codex cannot bind {socket} ({len(str(socket))} bytes; the "
                    f"limit is {MAX_SOCKET_PATH}) and would fall back to its login screen",
                    "use --agent codex (codex exec), or set CODEX_HOME to a shorter path",
                )
        result = _status(
            [chosen.executable, "login", "status"],
            timeout=timeout, runner=runner, remediation=remediation, label="Codex",
        )
        if result.returncode != 0:
            said = (result.stdout + result.stderr).strip().splitlines()
            raise PreflightRefused(
                f"Codex is not signed in for CODEX_HOME={home}"
                + (f" ({said[0][:120]})" if said else ""),
                remediation,
            )
        return "codex-login-status"
    if chosen.auth == "cursor-status":
        remediation = "agent login   (once, as this user; or put CURSOR_API_KEY in agent's config)"
        result = _status(
            [chosen.executable, "status", "--format", "json"],
            timeout=timeout, runner=runner, remediation=remediation, label="cursor-agent",
        )
        try:
            document: object = json.loads(result.stdout)
        except json.JSONDecodeError:
            document = None
        if not isinstance(document, dict) or result.returncode != 0:
            raise PreflightRefused(
                "cursor-agent status did not report a readable sign-in state", remediation
            )
        if document.get("isAuthenticated") is not True:
            raise PreflightRefused(
                f"cursor-agent is not signed in (status: {str(document.get('status'))[:40]})",
                remediation,
            )
        return "cursor-agent-status"
    # codex-ollama: the local Ollama server answers for the model (harness/model_availability.sh
    # has already run `ollama show`); Codex's --oss mode uses no ChatGPT sign-in. ollama.com
    # sign-in for a :cloud model has no non-interactive status command to check.
    return "ollama-model-available"


# -- run -----------------------------------------------------------------------------------------


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def _read_prompt(path: pathlib.Path, ceiling: int) -> str:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= ceiling:
            raise ValueError(
                f"prompt {path} is not a non-empty regular file of at most {ceiling} bytes"
            )
        raw = b""
        while chunk := os.read(descriptor, 1024 * 1024):
            raw += chunk
    finally:
        os.close(descriptor)
    text = raw.decode("utf-8")
    if "\0" in text:
        raise ValueError("prompt contains a NUL byte")
    return text


def _append(path: pathlib.Path, data: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        written = 0
        while written < len(data):
            count = os.write(descriptor, data[written:])
            if count < 1:
                raise OSError("lane log append made no progress")
            written += count
    finally:
        os.close(descriptor)


def record_exit(exits: pathlib.Path, *, slot: str, rc: int, stopped: bool) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_version": EXIT_SCHEMA,
        "ts": _now(),
        "slot": slot,
        "rc": rc,
        "stopped_by_signal": stopped,
    }
    _append(exits, (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode())
    return row


def run_lane(
    command: Sequence[str],
    *,
    slot: str,
    log: pathlib.Path,
    exits: pathlib.Path,
    prompt: pathlib.Path | None = None,
    terminal: bool = False,
) -> int:
    """Run one lane process to its end and record that end."""

    argv = list(command)
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        raise ValueError("lane command is empty")
    if prompt is not None:
        argv.append(_read_prompt(prompt, MAX_ARGV_PROMPT))
    stopped = False
    process: subprocess.Popen[bytes] | None = None

    def _stop(signum: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True
        if process is not None and process.poll() is None:
            process.send_signal(signum)

    previous = {
        signum: signal.signal(signum, _stop) for signum in (signal.SIGTERM, signal.SIGHUP)
    }
    try:
        if terminal:
            process = subprocess.Popen(argv)
            rc = process.wait()
        else:
            process = subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
            assert process.stdout is not None
            for raw in process.stdout:
                sys.stdout.buffer.write(raw)
                sys.stdout.buffer.flush()
                _append(log, raw)
            rc = process.wait()
    except OSError as error:
        _append(log, f"lane-agent: could not start {argv[0]}: {error}\n".encode())
        rc = 127
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    marker = f"__LANE_EXIT__ {slot} rc={rc}\n"
    _append(log, marker.encode())
    record_exit(exits, slot=slot, rc=rc, stopped=stopped)
    sys.stdout.write(marker)
    sys.stdout.flush()
    return rc


# -- CLI -----------------------------------------------------------------------------------------


def _nul(*fields: str) -> None:
    sys.stdout.write("".join(field + "\0" for field in fields))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="tmux lane agents")
    commands = parser.add_subparsers(dest="command", required=True)
    describe = commands.add_parser("describe")
    describe.add_argument("--agent", required=True)
    check = commands.add_parser("preflight")
    check.add_argument("--agent", required=True)
    check.add_argument("--timeout", type=float, default=20.0)
    runner = commands.add_parser("run")
    runner.add_argument("--slot", required=True)
    runner.add_argument("--log", type=pathlib.Path, required=True)
    runner.add_argument("--exits", type=pathlib.Path, required=True)
    runner.add_argument("--prompt-arg", type=pathlib.Path)
    runner.add_argument("--terminal", action="store_true")
    runner.add_argument("lane_command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "describe":
            chosen = agent(arguments.agent)
            _nul(
                chosen.executable,
                chosen.prompt_via,
                "terminal" if chosen.terminal else "headless",
                "metered" if chosen.metered else "unmetered",
            )
            return 0
        if arguments.command == "preflight":
            how = preflight(arguments.agent, environ=os.environ, timeout=arguments.timeout)
            print(how)
            return 0
        return run_lane(
            arguments.lane_command,
            slot=arguments.slot,
            log=arguments.log,
            exits=arguments.exits,
            prompt=arguments.prompt_arg,
            terminal=arguments.terminal,
        )
    except PreflightRefused as refused:
        print(f"lane-agent: {refused}", file=sys.stderr)
        print(f"lane-agent: remediation: {refused.remediation}", file=sys.stderr)
        return REFUSED
    except (OSError, ValueError, UnicodeDecodeError) as error:
        print(f"lane-agent: {error}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
