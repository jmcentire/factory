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
import hashlib
import json
import os
import pathlib
import re
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

_HARNESS_ROOT = str(pathlib.Path(__file__).resolve().parent)
if _HARNESS_ROOT not in sys.path:
    sys.path.insert(0, _HARNESS_ROOT)

import agents  # noqa: E402  (harness/agents.py, the harness registry)

EXIT_SCHEMA = "factory-lane-exit/1"
REFUSED = 77
# A plan lane whose harness announced API billing is stopped at once with this code.
BILLING_REFUSED = 78
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
    # hold any seat. Each runs on the operator's signed-in plan unless launched --metered, and its
    # check proves that sign-in the way the harness itself reports it, never a credential value.
    "claude": Agent("claude", "claude", "argv", False, "claude-plan"),
    # Claude Code on a model the local Ollama server serves (GLM): the Ollama plan.
    "claude-ollama": Agent("claude-ollama", "claude", "argv", False, "ollama-server"),
    "agy": Agent("agy", "agy", "argv", False, "agy-plan"),
    "muse": Agent("muse", "muse", "argv", False, "muse-plan"),
    # prime-agent bills OpenRouter per use by construction; it has no plan to sign in to.
    "prime-agent": Agent("prime-agent", "prime-agent", "argv", False, "registry", metered=True),
}


def agent(name: str) -> Agent:
    try:
        return AGENTS[name]
    except KeyError:
        raise PreflightRefused(
            f"unknown lane agent {name!r}", f"choose one of: {', '.join(AGENTS)}"
        ) from None


# -- model families and seats --------------------------------------------------------------------

# The Coder and the Tester run different model families wherever the operator has configured more
# than one (SOFTWARE-FACTORY.md "Across the lanes"; DOCTRINE-KERNEL E1, I19). A named model says
# its family by its name, and a name no prefix recognizes says nothing (a custom provider can serve
# anything under any name). With no model named, an agent that runs only its own vendor's models
# says it, unless the operator's Codex config routes it to another provider.
MODEL_FAMILIES = (
    ("claude", "anthropic"), ("opus", "anthropic"), ("sonnet", "anthropic"),
    ("haiku", "anthropic"), ("fable", "anthropic"),
    ("gpt", "openai"), ("codex", "openai"), ("sol", "openai"),
    ("gemini", "google"), ("argon", "google"),
    ("grok", "xai"), ("glm", "zhipu"), ("qwen", "alibaba"), ("deepseek", "deepseek"),
    ("kimi", "moonshot"), ("llama", "meta"), ("muse", "meta"),
)
AGENT_FAMILIES = {
    "codex": "openai", "codex-interactive": "openai", "claude": "anthropic", "agy": "google",
    "muse": "meta",
}
SEATS = "lane-seats.json"
SEATED_ROLES = {"coder": "tester", "tester": "coder"}


def model_family(agent_name: str, model: str, *, custom_provider: bool = False) -> str:
    """The family a lane's model belongs to, or "" when nothing says reliably."""
    if model:
        name = model.lower().rsplit("/", 1)[-1]
        return next((family for prefix, family in MODEL_FAMILIES if name.startswith(prefix)), "")
    return "" if custom_provider else AGENT_FAMILIES.get(agent_name, "")


def lane_family(agent_name: str, model: str, codex_home: pathlib.Path | None) -> str:
    """The family of what the lane will actually run. A Codex lane with no named model runs the
    operator's Codex config, model and provider both, so that config decides."""
    if model or agent_name not in ("codex", "codex-interactive") or codex_home is None:
        return model_family(agent_name, model)
    configured, overrides = codex_model_selection(codex_home)
    custom = any(o.startswith("model_provider=") and o != 'model_provider="openai"'
                 for o in overrides)
    return model_family(agent_name, configured, custom_provider=custom)


def _started(pid: object) -> str:
    """The start time of a running process, or "" when there is none. A PID alone can be reused
    by an unrelated process; a PID with its start time names one process."""
    if not isinstance(pid, (int, str)):
        return ""
    try:
        number = int(pid)
    except ValueError:
        return ""
    if number <= 0:
        return ""
    # ps renders the start time in the caller's timezone and locale; pin both, so every launcher
    # reads the same text for the same process.
    probe = subprocess.run(["ps", "-o", "lstart=", "-p", str(number)], capture_output=True,
                           text=True, check=False,
                           env={**os.environ, "TZ": "UTC", "LC_ALL": "C", "LANG": "C"})
    return probe.stdout.strip() if probe.returncode == 0 else ""


def _launching(entry: dict[str, object] | None) -> bool:
    return bool(entry and entry.get("status") == "pending" and entry.get("pid_start")
                and _started(entry.get("pid")) == entry.get("pid_start"))


def _settled(entry: dict[str, object] | None) -> dict[str, object] | None:
    """A pending seat whose launch is gone falls back to the seat it would have replaced."""
    if entry and entry.get("status") == "pending" and not _launching(entry):
        return entry.get("previous")  # type: ignore[return-value]
    return entry


def _seed_from_launches(root: pathlib.Path) -> dict[str, object]:
    """A run launched before the seat file existed: each role's seat is its latest active launch
    (not a qualify probe), by recorded time and then journal order. Two latest launches at the same
    time with different families, or a launch whose record cannot say its family, seed an unknown
    family, so the next claim needs a gap rather than a guess."""
    latest: dict[str, list[dict[str, object]]] = {}
    for journal in sorted(root.glob("*-launch.jsonl")):
        for line in journal.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            role = row.get("role")
            if row.get("status") != "active" or row.get("mode") != "launch" \
                    or role not in SEATED_ROLES:
                continue
            ts = str(row.get("ts", ""))
            best = latest.get(role)
            if not best or ts > str(best[0].get("ts", "")):
                latest[role] = [row]
            elif ts == str(best[0].get("ts", "")):
                best.append(row)
    seats: dict[str, object] = {}
    for role, rows in latest.items():
        families = set()
        for row in rows:
            agent_name, model = str(row.get("agent") or ""), str(row.get("model") or "")
            # codex-interactive runs the operator's Codex config, which its record does not carry.
            config = row.get("model_config")
            overrides = [str(o) for o in config] if isinstance(config, list) else []
            custom = (agent_name == "codex-interactive" and not model) or any(
                o.startswith("model_provider=") and o != 'model_provider="openai"'
                for o in overrides)
            families.add(model_family(agent_name, model, custom_provider=custom))
        row = rows[-1]
        family = families.pop() if len(families) == 1 else ""
        seats[role] = {"slot": row.get("slot"), "agent": row.get("agent"),
                       "model": row.get("model"), "family": family or None,
                       "status": "active", "seeded": True, "ts": row.get("ts"),
                       "previous": None}
    return seats


def _locked_seats(root: pathlib.Path):
    import contextlib
    import fcntl

    @contextlib.contextmanager
    def held():
        root.mkdir(parents=True, exist_ok=True)
        with open(root / (SEATS + ".lock"), "a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = root / SEATS
            seats = (json.loads(path.read_text(encoding="utf-8")) if path.is_file()
                     else _seed_from_launches(root))
            yield seats
            tmp = root / (SEATS + ".tmp")
            tmp.write_text(json.dumps(seats, sort_keys=True, indent=1) + "\n", encoding="utf-8")
            os.replace(tmp, path)

    return held()


def claim_seat(root: pathlib.Path, *, role: str, slot: str, agent_name: str, model: str,
               codex_home: pathlib.Path | None, gap: str, pid: int) -> dict[str, object]:
    """Check this lane's family against the other seat and take this role's seat, atomically.
    One seated launch runs at a time: while either seat is pending in a live launch, every other
    claim is refused, so no lane is admitted against a launch that may still fail. Raises
    SystemExit when the pair would share a family, or a family is unknown, with no gap."""
    family = lane_family(agent_name, model, codex_home)
    other = SEATED_ROLES[role]
    with _locked_seats(root) as seats:
        for name in SEATED_ROLES:
            if _launching(seats.get(name)):
                busy = seats[name]
                raise SystemExit(
                    f"tmux-lane: the {name} launch {busy.get('slot')} is still starting; launch "
                    "again once it has started or failed"
                )
            seats[name] = _settled(seats.get(name))
        peer = seats.get(other)
        theirs = (peer or {}).get("family") or ""
        record: dict[str, object] = {"family": family or None}
        if peer is None:
            tier = "no-peer" if family else "unknown-family"
        else:
            record.update(other_role=other, other_slot=peer.get("slot"),
                          other_family=theirs or None)
            tier = ("unknown-family" if not (family and theirs)
                    else "cross-family" if family != theirs else "same-family")
        record["tier"] = tier
        if tier in ("same-family", "unknown-family"):
            if not gap:
                mine = family or "an unknown model family"
                raise SystemExit(
                    f"tmux-lane: the {role} lane would run {mine}"
                    + (f" and the {other} lane runs {theirs or 'an unknown model family'}"
                       if peer is not None else "")
                    + "; the Coder and the Tester run different, known model families. Launch "
                    "with --agent/--model from another family, or, only when no other family is "
                    "configured, set FACTORY_LANE_INDEPENDENCE_GAP to the reason so the gap is "
                    "recorded"
                )
            record["independence_gap"] = gap
        seats[role] = {"slot": slot, "agent": agent_name, "model": model,
                       "family": family or None, "status": "pending", "pid": pid,
                       "pid_start": _started(pid), "ts": _now(), "previous": seats.get(role)}
    return record


def confirm_seat(root: pathlib.Path, *, role: str, slot: str) -> None:
    """The lane is running: its pending seat becomes the role's seat."""
    with _locked_seats(root) as seats:
        entry = seats.get(role)
        if not entry or entry.get("slot") != slot or entry.get("status") != "pending":
            raise SystemExit(f"tmux-lane: the {role} seat is not {slot}'s to confirm")
        entry.update(status="active", previous=None)


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
    billing: str = "plan",
) -> str:
    """Return how sign-in was established, or raise PreflightRefused with the exact fix. A plan
    lane (the default) must be signed in to the operator's plan, not to a per-use API key."""

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
        if billing == "plan" and "api key" in (result.stdout + result.stderr).lower():
            raise PreflightRefused(
                f"Codex is signed in with an API key for CODEX_HOME={home}, not the ChatGPT plan",
                f"CODEX_HOME={quoted} codex login --device-auth   (or launch the lane --metered)",
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
    if chosen.auth == "claude-plan":
        remediation = "claude auth login   (once, as this user, with the Claude subscription)"
        result = _status([chosen.executable, "auth", "status", "--json"], timeout=timeout,
                         runner=runner, remediation=remediation, label="Claude Code")
        try:
            document = json.loads(result.stdout)
        except json.JSONDecodeError:
            document = None
        if not isinstance(document, dict) or document.get("loggedIn") is not True:
            raise PreflightRefused("Claude Code is not signed in", remediation)
        on_key = document.get("authMethod") in ("api_key", None) or document.get("apiKeySource")
        if billing == "plan" and on_key:
            raise PreflightRefused(
                f"Claude Code would bill an API key ({document.get('apiKeySource')}), "
                "not the plan",
                "unset the key for the lane, or launch it --metered with a spend cap",
            )
        return f"claude-plan:{document.get('authMethod')}"
    if chosen.auth == "ollama-server":
        remediation = ("start the Ollama server (`ollama serve`) and `ollama signin` for "
                       ":cloud models")
        result = _status([chosen.executable, "--version"], timeout=timeout, runner=runner,
                         remediation="install Claude Code", label="Claude Code")
        if result.returncode != 0:
            raise PreflightRefused("Claude Code is not runnable", "install Claude Code")
        try:
            with urllib.request.urlopen(f"{agents.OLLAMA_BASE_URL}/api/version",
                                        timeout=timeout) as response:
                json.load(response)
        except (OSError, ValueError) as error:
            raise PreflightRefused(f"the Ollama server does not answer ({error})",
                                   remediation) from None
        return "ollama-server-answers"
    if chosen.auth == "agy-plan":
        remediation = "agy   (run it once interactively, as this user, to sign in)"
        result = _status([chosen.executable, "models"], timeout=timeout, runner=runner,
                         remediation=remediation, label="agy")
        shown = result.stdout + result.stderr
        if result.returncode != 0 or "sign in" in shown.lower():
            raise PreflightRefused("agy is not signed in", remediation)
        return "agy-models"
    if chosen.auth == "muse-plan":
        remediation = "muse login   (once, as this user; META_API_KEY would bill the API instead)"
        if environ.get("META_API_KEY"):
            raise PreflightRefused("META_API_KEY would override Muse's account sign-in",
                                   "unset it for the lane, or launch it --metered")
        base = pathlib.Path(environ.get("XDG_CONFIG_HOME")
                            or pathlib.Path(environ.get("HOME", "~")) / ".config")
        auth = base / "muse" / "auth.json"
        try:
            stored = json.loads(auth.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stored = None
        if not isinstance(stored, dict) or not stored.get("providers"):
            raise PreflightRefused(f"Muse has no stored sign-in at {auth}", remediation)
        _status([chosen.executable, "--version"], timeout=timeout, runner=runner,
                remediation="install Muse Code", label="muse")
        return "muse-auth-stored"
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


def _record_session(path: pathlib.Path, session: str) -> None:
    """Write a resumable session id owner-only, atomically, never through a symlink."""

    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._:-]{7,127}", session):
        return
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                         | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(session + "\n")
    os.replace(temporary, path)


#: Where an argv harness's oversized turn is placed: the lane's own untracked .git directory,
#: which its sandbox can read and its commits never carry.
BRIEF_DIRECTORY = "factory-brief"


def argv_prompt(prompt: pathlib.Path, workdir: pathlib.Path) -> str:
    """The argument an argv harness receives: the turn itself, or a pointer to its bytes.

    A turn larger than one argv element can hold is never refused and never sent to another
    agent (founder, 2026-10-18: prompt size is verified on delegation and avoided by reference).
    Its bytes go, unchanged and content-addressed, into ``<lane>/.git/factory-brief/``, and the
    harness is told to read that file in full and check its sha256 first.
    """
    text = _read_prompt(prompt, 64 * 1024 * 1024)
    raw = text.encode("utf-8")
    if len(raw) <= MAX_ARGV_PROMPT:
        return text
    git_dir = workdir / ".git"
    if not git_dir.is_dir() or git_dir.is_symlink():
        raise ValueError(
            f"prompt is {len(raw)} bytes and {workdir} has no .git directory to hold it"
        )
    digest = hashlib.sha256(raw).hexdigest()
    directory = git_dir / BRIEF_DIRECTORY
    directory.mkdir(mode=0o700, exist_ok=True)
    brief = directory / f"turn-{digest[:16]}.txt"
    descriptor = os.open(
        brief, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return (
        f"This turn is the file {brief} ({len(raw)} bytes, sha256:{digest}). Read the whole "
        "file now, every line, and check its sha256 before doing anything else. It governs "
        "this lane exactly as if it had been given here. Do not edit or delete it."
    )


def run_lane(
    command: Sequence[str],
    *,
    slot: str,
    log: pathlib.Path,
    exits: pathlib.Path,
    prompt: pathlib.Path | None = None,
    terminal: bool = False,
    env_file: pathlib.Path | None = None,
    session_agent: str = "",
    session_file: pathlib.Path | None = None,
    plan_only: bool = False,
) -> int:
    """Run one lane process to its end and record that end.

    ``env_file`` (owner-only ``NAME=value`` lines) adds a harness's named credentials to the
    child's environment, so they never appear on a command line or in the tmux launch string.

    With ``session_agent``, the first session id the harness streams is written to
    ``session_file`` the moment it appears, so a turn the watchdog ends mid-flight can be resumed.
    With ``plan_only``, a harness that announces per-use API billing in its stream is stopped at
    once and the run ends ``BILLING_REFUSED``: a plan lane never spends on an API key.
    """

    argv = list(command)
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        raise ValueError("lane command is empty")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", slot):
        raise ValueError("invalid process receipt slot")
    if prompt is not None:
        argv.append(argv_prompt(prompt, pathlib.Path.cwd()))
    child_env = dict(os.environ)
    if env_file is not None:
        if env_file.stat().st_mode & 0o077:
            raise ValueError(f"credential file {env_file} is readable by others")
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.isidentifier():
                child_env[name] = value
    stopped = False
    refused_billing = ""
    session_seen = False
    chosen = agents.harness(session_agent) if session_agent else None
    process: subprocess.Popen[bytes] | None = None

    def record_worker(status: str) -> None:
        # The tmux pane owns this wrapper; pause the child doing the work, never the pane root
        # (tmux automatically SIGCONTs that root). No command line or credential is retained.
        assert process is not None
        path = log.parent / f"{slot}-process.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        wrapper_start = " ".join(_started(os.getpid()).split())
        worker_start = " ".join(_started(process.pid).split())
        receipt = {"schema": "factory-worker-process/1", "slot": slot, "status": status,
                   "wrapper": {"pid": os.getpid(), "started": wrapper_start},
                   "worker": {"pid": process.pid, "started": worker_start}}
        if not wrapper_start or not worker_start:
            if process.poll() is not None:
                return  # a completed short-lived command needs its exit receipt, not a pause
            raise OSError("cannot publish worker process identity")
        fd, temporary = tempfile.mkstemp(prefix=".worker-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as out:
                json.dump(receipt, out, sort_keys=True)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _watch(raw: bytes) -> None:
        nonlocal session_seen, refused_billing
        if chosen is None:
            return
        line = raw.decode("utf-8", errors="replace")
        if session_file is not None and not session_seen:
            session = chosen.session_in(line)
            if session:
                _record_session(session_file, session)
                session_seen = True
        if plan_only and not refused_billing and chosen.billing_in(line) == "api":
            refused_billing = chosen.name
            if process is not None and process.poll() is None:
                process.terminate()

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
            record_worker("running")
            rc = process.wait()
        else:
            process = subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=child_env,
            )
            record_worker("running")
            assert process.stdout is not None
            for raw in process.stdout:
                sys.stdout.buffer.write(raw)
                sys.stdout.buffer.flush()
                _append(log, raw)
                _watch(raw)
            rc = process.wait()
    except OSError as error:
        if process is not None and process.poll() is None:
            # Receipt failure must not leave an untracked model running. This is terminal
            # cleanup of our own Popen child, not the supervisor's resumable pause operation.
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        _append(log, f"lane-agent: could not start {argv[0]}: {error}\n".encode())
        rc = 127
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    if refused_billing:
        _append(log, (f"lane-agent: {refused_billing} started on per-use API billing in a plan "
                      "lane; stopped. Unset the key or launch the lane --metered.\n").encode())
        rc = BILLING_REFUSED
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
    family = commands.add_parser("family")
    family.add_argument("--agent", required=True)
    family.add_argument("--model", default="")
    family.add_argument("--codex-home", type=pathlib.Path)
    seat = commands.add_parser("seat")
    seat.add_argument("seat_action", choices=("claim", "confirm"))
    seat.add_argument("--root", type=pathlib.Path, required=True)
    seat.add_argument("--role", choices=tuple(SEATED_ROLES), required=True)
    seat.add_argument("--slot", required=True)
    seat.add_argument("--agent", default="")
    seat.add_argument("--model", default="")
    seat.add_argument("--codex-home", type=pathlib.Path)
    seat.add_argument("--gap", default="")
    seat.add_argument("--pid", type=int, default=0)
    selection = commands.add_parser("codex-model")
    selection.add_argument("--codex-home", type=pathlib.Path, required=True)
    check = commands.add_parser("preflight")
    check.add_argument("--agent", required=True)
    check.add_argument("--timeout", type=float, default=20.0)
    check.add_argument("--billing", choices=("plan", "metered"), default="plan")
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
    runner.add_argument("--session-agent", default="")
    runner.add_argument("--session-file", type=pathlib.Path)
    runner.add_argument("--plan-only", action="store_true")
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
        if arguments.command == "family":
            agent(arguments.agent)
            print(lane_family(arguments.agent, arguments.model, arguments.codex_home))
            return 0
        if arguments.command == "seat":
            if arguments.seat_action == "confirm":
                confirm_seat(arguments.root, role=arguments.role, slot=arguments.slot)
                return 0
            agent(arguments.agent)
            record = claim_seat(arguments.root, role=arguments.role, slot=arguments.slot,
                                agent_name=arguments.agent, model=arguments.model,
                                codex_home=arguments.codex_home, gap=arguments.gap,
                                pid=arguments.pid or os.getppid())
            print(json.dumps(record, sort_keys=True, separators=(",", ":")))
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
            how = preflight(arguments.agent, environ=os.environ, timeout=arguments.timeout,
                            billing=arguments.billing)
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
            session_agent=arguments.session_agent,
            session_file=arguments.session_file,
            plan_only=arguments.plan_only,
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
