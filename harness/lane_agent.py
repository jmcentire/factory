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
  codex-model  the model selection a ``codex exec`` lane carries over from the operator's own
             ``$CODEX_HOME/config.toml`` when no model was named: the model, then one
             ``key=value`` override per field. Lanes run with ``--ignore-user-config`` so the
             operator's MCP servers, sandbox and approval settings stay out of them, and without
             this a lane would run Codex's built-in default instead of the operator's model.
  environment  the lane's dependency-cache variables and runtime directories, validated, as
             NUL-terminated fields for tmux_lane.sh and tmux_lane_message.sh: the Codex shell
             policy that sets them, the lane PATH, the launch record, the prompt notice and
             the shell-quoted NAME=VALUE assignments.
  run        run one lane process. It tees the output to the lane log (a terminal agent keeps
             the terminal instead), and when the process ends, including when the watchdog
             stops it with SIGTERM, appends one exit record to the lane's exits journal.

Agents are named here once, with whether each is metered by construction; tests hold this list
and ``tmux_lane.sh``'s case list equal. No model is named here: a lane runs the model the
operator names (``--model``), or else the one the agent's own CLI configuration selects.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import shlex
import signal
import stat
import subprocess
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

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
    # Bills per use whatever the operator says: a lane on it refuses to launch without a
    # per-round spend cap (tmux_lane.sh). The operator marks any other launch with --metered.
    metered: bool = False


AGENTS: Mapping[str, Agent] = {
    # `codex exec`, non-interactive: the default for author lanes.
    "codex": Agent("codex", "codex", "stdin", False, "codex-login"),
    # The Codex TUI, for an operator who wants to watch or steer a lane by hand.
    "codex-interactive": Agent("codex-interactive", "codex", "argv", True, "codex-login"),
    # `codex exec --oss` on a model the local Ollama server already has.
    "codex-ollama": Agent("codex-ollama", "codex", "stdin", False, "ollama-model", metered=True),
    # Cursor's agent CLI in print mode: `agent -p [--model <m>] --force --trust --workspace <lane>`.
    "cursor-agent": Agent("cursor-agent", "agent", "argv", False, "cursor-status"),
    # Harnesses whose headless turn comes from the registry (harness/agents.py): any of them may
    # hold any seat. Sign-in is the harness's own; the check proves the CLI runs headless and
    # that a credential it names is present (by variable or its own config), never its value.
    "claude": Agent("claude", "claude", "argv", False, "registry"),
    "agy": Agent("agy", "agy", "argv", False, "registry"),
    "prime-agent": Agent("prime-agent", "prime-agent", "argv", False, "registry"),
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
    if chosen.auth == "registry":
        _status([chosen.executable, "--version"], timeout=timeout, runner=runner,
                remediation=f"install {chosen.executable} and sign it in as this user",
                label=chosen.name)
        return "registry-cli-runs"
    # codex-ollama: the local Ollama server answers for the model (harness/model_availability.sh
    # has already run `ollama show`); Codex's --oss mode uses no ChatGPT sign-in. ollama.com
    # sign-in for a :cloud model has no non-interactive status command to check.
    return "ollama-model-available"


# -- codex model ---------------------------------------------------------------------------------

# The keys of a Codex config.toml that choose the model, and nothing else. The rest of the
# operator's config (MCP servers, sandbox, approvals, hooks) stays out of a lane.
CODEX_MODEL_KEYS = (
    "model",
    "model_provider",
    "model_reasoning_effort",
    "model_reasoning_summary",
    "model_verbosity",
)


def _toml_value(value: object) -> str:
    """A TOML literal for one value, as ``codex -c key=value`` parses it."""

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)  # a JSON string is a valid TOML basic string
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(
            f"{json.dumps(str(key))} = {_toml_value(item)}" for key, item in value.items()
        ) + "}"
    raise ValueError(f"cannot carry a {type(value).__name__} Codex setting into a lane")


def codex_model_selection(codex_home: pathlib.Path) -> tuple[str, list[str]]:
    """The model the operator's Codex config selects, and the ``-c`` overrides that select it.

    ``("", [])`` when the config names none: the lane then runs whatever Codex itself defaults
    to, which is also what the operator's own ``codex`` would run. A legacy ``profile`` key
    layers that profile's model keys over the top-level ones, as Codex does."""

    path = codex_home / "config.toml"
    if not path.is_file():
        return "", []
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"{path} is not readable TOML: {error}") from None
    chosen = {key: document[key] for key in CODEX_MODEL_KEYS if key in document}
    profile = document.get("profile")
    profiles = document.get("profiles")
    if isinstance(profile, str) and isinstance(profiles, dict) and isinstance(
        profiles.get(profile), dict
    ):
        chosen.update({key: profiles[profile][key] for key in CODEX_MODEL_KEYS
                       if key in profiles[profile]})
    overrides = [f"{key}={_toml_value(value)}" for key, value in chosen.items()]
    provider = chosen.get("model_provider")
    providers = document.get("model_providers")
    if isinstance(provider, str) and isinstance(providers, dict) and isinstance(
        providers.get(provider), dict
    ):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", provider):
            raise ValueError(f"Codex model_provider {provider!r} is not a bare TOML key")
        overrides.append(f"model_providers.{provider}={_toml_value(providers[provider])}")
    model = chosen.get("model", "")
    return (model if isinstance(model, str) else ""), overrides


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
    env_file: pathlib.Path | None = None,
) -> int:
    """Run one lane process to its end and record that end.

    ``env_file`` (owner-only ``NAME=value`` lines) adds a harness's named credentials to the
    child's environment, so they never appear on a command line or in the tmux launch string.
    """

    argv = list(command)
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        raise ValueError("lane command is empty")
    if prompt is not None:
        argv.append(_read_prompt(prompt, MAX_ARGV_PROMPT))
    child_env = dict(os.environ)
    if env_file is not None:
        if env_file.stat().st_mode & 0o077:
            raise ValueError(f"credential file {env_file} is readable by others")
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.isidentifier():
                child_env[name] = value
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
            process = subprocess.Popen(argv, env=child_env)
            rc = process.wait()
        else:
            process = subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=child_env,
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


# -- lane environment ----------------------------------------------------------------------------

# Codex runs lane shell commands under shell_environment_policy inherit="core", which keeps only
# HOME, PATH, USER and the like: a DENO_DIR in Codex's own environment never reaches `deno`
# (dogfood #31/#61). The cache variables are therefore set in the policy itself, on launch and on
# every resume, and the runtime directories go first on PATH. Every value is an existing
# directory the lane cannot write: a read-only store, never a credential.
CORE_SHELL_POLICY = 'shell_environment_policy={inherit="core",ignore_default_excludes=false}'
RESERVED_ENV = frozenset({"HOME", "USER", "PATH", "TMPDIR", "TERM", "SHELL", "LANG", "CODEX_HOME",
                          "FACTORY_RUNS_DIR", "HARNESS_RUN_ROOT"})


@dataclass(frozen=True)
class LaneEnvironment:
    cache_env: dict[str, str]
    runtime_dirs: tuple[str, ...]

    def shell_policy(self) -> str:
        if not self.cache_env:
            return CORE_SHELL_POLICY
        pairs = ",".join(f"{name}={json.dumps(value, ensure_ascii=False)}"
                         for name, value in sorted(self.cache_env.items()))
        return CORE_SHELL_POLICY[:-1] + f",set={{{pairs}}}}}"

    def path(self, base: str) -> str:
        return os.pathsep.join([*self.runtime_dirs, base]) if self.runtime_dirs else base

    def record(self) -> dict[str, object]:
        return {"cache_env": dict(sorted(self.cache_env.items())),
                "runtime_dirs": list(self.runtime_dirs)}

    def assignments(self) -> str:
        return " ".join(shlex.quote(f"{name}={value}")
                        for name, value in sorted(self.cache_env.items()))

    def notice(self) -> str:
        lines = [f"- {name}={value} is set for your commands; it is a read-only, pre-fetched "
                 "store: resolve dependencies from it offline (for Deno, --cached-only)."
                 for name, value in sorted(self.cache_env.items())]
        lines += [f"- {directory} is first on PATH: use the runtime there, not a shim."
                  for directory in self.runtime_dirs]
        return "\n".join(lines)


def _read_only_directory(value: str, label: str) -> str:
    if not os.path.isabs(value) or os.pathsep in value or any(ord(c) < 32 for c in value):
        raise ValueError(f"{label} must be an absolute path without {os.pathsep!r}: {value!r}")
    if not os.path.isdir(value):
        raise ValueError(f"{label} is not an existing directory: {value}")
    # A link anywhere on the path, or inside the store, lets the bytes change after admission.
    prefix = os.sep
    for part in pathlib.PurePath(value).parts[1:]:
        prefix = os.path.join(prefix, part)
        if os.path.islink(prefix):
            raise ValueError(f"{label} path has a symlink at {prefix}; give its real path: {value}")
    # ponytail: a same-user lane can still chmod the store back; this proves the state at launch
    # and resume only. A content manifest bound across launch and resume would close that.
    for directory, names, files in os.walk(value, followlinks=False):
        for entry in [directory, *(os.path.join(directory, name) for name in names + files)]:
            mode = os.lstat(entry).st_mode
            if stat.S_ISLNK(mode):
                raise ValueError(f"{label} holds a symlink, {entry}; vendor real files: {value}")
            if mode & 0o222:
                raise ValueError(
                    f"{label} is writable at {entry}; make the store read-only (chmod -R a-w)"
                )
    return value


def lane_environment(cache_env: Sequence[str], runtime_dirs: Sequence[str]) -> LaneEnvironment:
    variables: dict[str, str] = {}
    for item in cache_env:
        name, separator, value = item.partition("=")
        if not separator or not name.isidentifier() or not name.isascii():
            raise ValueError(f"--cache-env must be NAME=DIR: {item!r}")
        if name in RESERVED_ENV or name in variables:
            raise ValueError(f"--cache-env may not set {name} (reserved or repeated)")
        variables[name] = _read_only_directory(value, f"--cache-env {name}")
    directories = tuple(_read_only_directory(value, "--runtime-dir") for value in runtime_dirs)
    return LaneEnvironment(variables, directories)


# -- CLI -----------------------------------------------------------------------------------------


def _nul(*fields: str) -> None:
    sys.stdout.write("".join(field + "\0" for field in fields))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="tmux lane agents")
    commands = parser.add_subparsers(dest="command", required=True)
    describe = commands.add_parser("describe")
    describe.add_argument("--agent", required=True)
    selection = commands.add_parser("codex-model")
    selection.add_argument("--codex-home", type=pathlib.Path, required=True)
    check = commands.add_parser("preflight")
    check.add_argument("--agent", required=True)
    check.add_argument("--timeout", type=float, default=20.0)
    environment = commands.add_parser("environment")
    environment.add_argument("--path", required=True)
    environment.add_argument("--cache-env", action="append", default=[])
    environment.add_argument("--runtime-dir", action="append", default=[])
    environment.add_argument("--record", help="a launch's lane_env record, for a resume")
    runner = commands.add_parser("run")
    runner.add_argument("--slot", required=True)
    runner.add_argument("--log", type=pathlib.Path, required=True)
    runner.add_argument("--exits", type=pathlib.Path, required=True)
    runner.add_argument("--prompt-arg", type=pathlib.Path)
    runner.add_argument("--terminal", action="store_true")
    runner.add_argument("--env-file", type=pathlib.Path)
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
        if arguments.command == "codex-model":
            model, overrides = codex_model_selection(arguments.codex_home)
            _nul(model, *overrides)
            return 0
        if arguments.command == "environment":
            cache_env, runtime_dirs = arguments.cache_env, arguments.runtime_dir
            if arguments.record:
                record = json.loads(arguments.record)
                cache_env = [f"{name}={value}" for name, value in record["cache_env"].items()]
                runtime_dirs = record["runtime_dirs"]
            lane = lane_environment(cache_env, runtime_dirs)
            _nul(lane.shell_policy(), lane.path(arguments.path),
                 json.dumps(lane.record(), sort_keys=True, separators=(",", ":")), lane.notice(),
                 lane.assignments())
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
            env_file=arguments.env_file,
        )
    except PreflightRefused as refused:
        print(f"lane-agent: {refused}", file=sys.stderr)
        print(f"lane-agent: remediation: {refused.remediation}", file=sys.stderr)
        return REFUSED
    except (OSError, ValueError, UnicodeDecodeError, KeyError, TypeError) as error:
        print(f"lane-agent: {error}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
