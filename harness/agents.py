#!/usr/bin/env python3
"""agents — one registry of the agent harnesses a Factory seat can run, so any harness can hold
any seat.

The factory names no model and no vendor. What it needs from a harness is a shape: how to start a
headless turn in a working directory with a named model and reasoning effort, how to hand it the
prompt, how to resume the same session for the next turn, which variables it reads, how to give a
lane its own scoped Kindex and nothing of the operator's, and how to read the final message,
session id and cost back out of its output. Each harness is data here; launchers
(``tmux_lane.sh``, ``factory.sh``, ``tmux_lane_message.sh``) ask the registry instead of carrying
per-vendor branches, and a new harness is one entry.

Each model runs in its own vendor's harness where one exists (it was trained there): Claude in
Claude Code, GPT in Codex, Gemini in Antigravity, Muse in Muse Code, Grok in Cursor's agent CLI,
and GLM in Claude Code pointed at the local Ollama server (``ollama launch claude``'s shape).

Billing (founder direction, 2026-10-16): a seat runs on the operator's signed-in plan, never on a
per-use API key, unless the operator launches it metered. ``api_env`` names the variables that
would switch a harness to per-use billing; a ``plan`` lane's environment never admits them, its
preflight proves a plan sign-in (``lane_agent.py``), and a harness that announces its billing
source in its first streamed event is stopped by the lane wrapper if that source is an API key.

Invariant: an entry builds argv only — no shell, no string interpolation — and never puts a
credential value on a command line; credentials pass by variable *name* so a launcher that clears
the environment admits exactly those, through an owner-only file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

BILLINGS = ("plan", "metered")
TURN_RECORD = "factory-lane-turn/1"
# The address Ollama serves its Anthropic-compatible Messages API on; claude-ollama talks to
# nothing else, so no Anthropic key can leave the machine through it.
OLLAMA_BASE_URL = "http://127.0.0.1:11434"


@dataclass(frozen=True)
class Turn:
    """One headless turn: the argv, how the prompt travels, and where a final message lands."""

    argv: tuple[str, ...]
    prompt_via: str  # "stdin" | "argv" (appended as the last argument)
    last_message_file: str = ""


@dataclass(frozen=True)
class Outcome:
    text: str
    session: str
    cost_usd: float
    tokens: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class LaneSetup:
    """What a lane needs beyond its turn: variables for its owner-only env file, and arguments
    every turn of it carries (launch and resume alike)."""

    env: Mapping[str, str] = field(default_factory=dict)
    argv: tuple[str, ...] = ()


@dataclass(frozen=True)
class Harness:
    name: str
    executable: str
    credential_env: tuple[str, ...]  # non-billing variables a cleared environment admits
    resumable: bool
    api_env: tuple[str, ...] = ()  # variables that switch the harness to per-use API billing
    plan: bool = True  # the harness has a signed-in plan path

    def turn(self, *, model: str, effort: str, cwd: Path, resume: str = "",
             last_message_file: str = "", max_turns: int = 400,
             extra: Sequence[str] = ()) -> Turn:
        raise NotImplementedError

    def outcome(self, stdout: str, last_message: str = "") -> Outcome:
        raise NotImplementedError

    def noise(self, line: str) -> bool:
        """A streaming-progress line the transcript need not keep (outcome never reads it)."""

        return False

    def session_in(self, line: str) -> str:
        """The session id a streamed output line announces, or "". The lane wrapper records the
        first one, so a turn the watchdog ends mid-flight can still be resumed."""

        return ""

    def billing_in(self, line: str) -> str:
        """``plan`` or ``api`` when a streamed line states how the turn is billed, else ""."""

        return ""

    def admitted_env(self, billing: str) -> tuple[str, ...]:
        if billing not in BILLINGS:
            raise ValueError(f"billing must be one of {BILLINGS}, not {billing!r}")
        if billing == "plan" and not self.plan:
            raise ValueError(f"{self.name} has no signed-in plan path; launch it metered")
        return self.credential_env + (self.api_env if billing == "metered" else ())

    def lane_setup(self, home: Path, *, model: str, server: Mapping[str, object] | None,
                   operator_home: Path, environ: Mapping[str, str]) -> LaneSetup:
        """Build this lane's private configuration under ``home`` so the harness sees the lane's
        scoped Kindex (``server``) and none of the operator's MCP servers, rules or hooks."""

        return LaneSetup()

    def seat_env(self, model: str) -> Mapping[str, str]:
        """Fixed variables a resident seat (Validator, Orchestrator) of this harness needs. A
        seat keeps the operator's own configuration and Kindex; only billing is constrained."""

        return {}


def _last_json(text: str) -> dict[str, object]:
    for line in reversed(text.strip().splitlines()):
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(doc, dict):
            return doc
    return {}


def _json_line(line: str) -> dict[str, object]:
    if not line.lstrip().startswith("{"):
        return {}
    try:
        doc = json.loads(line)
    except json.JSONDecodeError:
        return {}
    return doc if isinstance(doc, dict) else {}


def _int(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _server(server: Mapping[str, object]) -> tuple[str, list[str], dict[str, str]]:
    command = server.get("command")
    args = server.get("args") or []
    env = server.get("env") or {}
    if not isinstance(command, str) or not command or not isinstance(args, list) \
            or not all(isinstance(part, str) for part in args) or not isinstance(env, dict):
        raise ValueError("the lane Kindex record's mcp_server is malformed")
    return command, list(args), {str(key): str(value) for key, value in env.items()}


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def _link(link: Path, target: Path) -> None:
    """A lane-home link to one piece of the operator's sign-in. Replaced on relaunch."""

    link.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        raise ValueError(f"{link} exists and is not a link; refusing to replace it")
    link.symlink_to(target)


def _keychain_home(home: Path, operator_home: Path) -> Path:
    """A private HOME whose ``Library`` is the operator's, so a harness that keeps its sign-in
    in the login keychain (cursor-agent, agy) still finds it while every dot-file it reads for
    configuration is the lane's own."""

    private = home / "home"
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    library = operator_home / "Library"
    if library.is_dir():
        _link(private / "Library", library)
    return private


def _mcp_servers(server: Mapping[str, object] | None) -> dict[str, object]:
    if server is None:
        return {}
    command, args, env = _server(server)
    return {"kindex": {"command": command, "args": args, "env": env}}


class ClaudeCode(Harness):
    def turn(self, *, model: str, effort: str, cwd: Path, resume: str = "",
             last_message_file: str = "", max_turns: int = 400,
             extra: Sequence[str] = ()) -> Turn:
        argv = ["claude", "-p", "--output-format", "stream-json", "--verbose",
                "--permission-mode", "bypassPermissions", "--max-turns", str(max_turns)]
        argv += ["--model", model] if model else []
        argv += ["--effort", effort] if effort else []
        argv += ["--resume", resume] if resume else []
        argv += list(extra)
        return Turn(tuple(argv), "argv")

    def outcome(self, stdout: str, last_message: str = "") -> Outcome:
        doc = _last_json(stdout)
        usage = doc.get("usage") if isinstance(doc.get("usage"), dict) else {}
        assert isinstance(usage, dict)
        return Outcome(
            text=str(doc.get("result") or ""), session=str(doc.get("session_id") or ""),
            cost_usd=float(str(doc.get("total_cost_usd") or 0.0)),
            tokens={"input": _int(usage.get("input_tokens"))
                    + _int(usage.get("cache_creation_input_tokens")),
                    "cached": _int(usage.get("cache_read_input_tokens")),
                    "output": _int(usage.get("output_tokens"))},
        )

    def session_in(self, line: str) -> str:
        doc = _json_line(line)
        if doc.get("type") == "system" and doc.get("subtype") == "init":
            return str(doc.get("session_id") or "")
        return ""

    def billing_in(self, line: str) -> str:
        doc = _json_line(line)
        if doc.get("type") != "system" or doc.get("subtype") != "init" \
                or "apiKeySource" not in doc:
            return ""
        return "plan" if doc.get("apiKeySource") in (None, "none") else "api"

    def lane_setup(self, home: Path, *, model: str, server: Mapping[str, object] | None,
                   operator_home: Path, environ: Mapping[str, str]) -> LaneSetup:
        # Claude Code keeps its plan sign-in in the keychain under a name derived from its config
        # directory, so the directory stays the operator's. User settings, hooks, plugins and the
        # operator's CLAUDE.md stay out (--setting-sources), and so does every MCP server but the
        # lane's (--strict-mcp-config).
        config = _write(home / "mcp.json", json.dumps({"mcpServers": _mcp_servers(server)}))
        return LaneSetup(argv=("--mcp-config", str(config), "--strict-mcp-config",
                               "--setting-sources", "project,local"))


class ClaudeOllama(ClaudeCode):
    """Claude Code on a model the local Ollama server serves (GLM and other open models), the way
    ``ollama launch claude`` runs it. ``--bare`` keeps the operator's keychain, hooks, plugins and
    CLAUDE.md out, and makes the harness take its key from the environment, which here is a fixed
    placeholder valid only against the loopback Ollama server. Billing is the Ollama plan."""

    def turn(self, *, model: str, effort: str, cwd: Path, resume: str = "",
             last_message_file: str = "", max_turns: int = 400,
             extra: Sequence[str] = ()) -> Turn:
        built = super().turn(model=model, effort=effort, cwd=cwd, resume=resume,
                             max_turns=max_turns, extra=extra)
        return Turn(built.argv[:2] + ("--bare",) + built.argv[2:], built.prompt_via)

    def billing_in(self, line: str) -> str:
        return ""  # always the loopback Ollama server: lane_setup fixes the base URL

    def lane_setup(self, home: Path, *, model: str, server: Mapping[str, object] | None,
                   operator_home: Path, environ: Mapping[str, str]) -> LaneSetup:
        config = _write(home / "mcp.json", json.dumps({"mcpServers": _mcp_servers(server)}))
        return LaneSetup(env=self.seat_env(model),
                         argv=("--mcp-config", str(config), "--strict-mcp-config"))

    def seat_env(self, model: str) -> Mapping[str, str]:
        if not model:
            raise ValueError("claude-ollama needs a model (an Ollama model, e.g. glm-5.3:cloud)")
        return {
                "ANTHROPIC_BASE_URL": OLLAMA_BASE_URL,
                "ANTHROPIC_API_KEY": "ollama",
                # Claude Code's own background calls name a Claude tier; route each to the model.
                "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
                "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
                "CLAUDE_CODE_SUBAGENT_MODEL": model,
                "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
                "CLAUDE_CODE_AUTO_MODE_SERVER": "0",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }


class Codex(Harness):
    def turn(self, *, model: str, effort: str, cwd: Path, resume: str = "",
             last_message_file: str = "", max_turns: int = 400,
             extra: Sequence[str] = ()) -> Turn:
        common = ["--json", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox"]
        common += ["-o", last_message_file] if last_message_file else []
        common += ["-m", model] if model else []
        common += ["-c", f"model_reasoning_effort={json.dumps(effort)}"] if effort else []
        common += list(extra)
        if resume:
            argv = ["codex", "exec", "resume", *common, resume, "-"]
        else:
            argv = ["codex", "exec", *common, "-C", str(cwd), "-"]
        return Turn(tuple(argv), "stdin", last_message_file)

    def outcome(self, stdout: str, last_message: str = "") -> Outcome:
        session, tokens = "", {"input": 0, "cached": 0, "output": 0}
        for line in stdout.splitlines():
            event = _json_line(line)
            if event.get("type") == "thread.started" and event.get("thread_id"):
                session = str(event["thread_id"])
            usage = event.get("usage")
            if event.get("type") == "turn.completed" and isinstance(usage, dict):
                tokens["input"] += _int(usage.get("input_tokens"))
                tokens["cached"] += _int(usage.get("cached_input_tokens"))
                tokens["output"] += _int(usage.get("output_tokens"))
        return Outcome(text=last_message, session=session, cost_usd=0.0, tokens=tokens)

    def session_in(self, line: str) -> str:
        event = _json_line(line)
        if event.get("type") == "thread.started":
            return str(event.get("thread_id") or "")
        return ""


class Antigravity(Harness):
    def turn(self, *, model: str, effort: str, cwd: Path, resume: str = "",
             last_message_file: str = "", max_turns: int = 400,
             extra: Sequence[str] = ()) -> Turn:
        argv = ["agy", "--output-format", "stream-json", "--dangerously-skip-permissions",
                "--add-dir", str(cwd)]
        argv += ["--model", model] if model else []
        argv += ["--effort", effort] if effort else []
        argv += ["--conversation", resume] if resume else []
        argv += list(extra)
        argv += ["--print"]  # the prompt follows as the last argument
        return Turn(tuple(argv), "argv")

    def outcome(self, stdout: str, last_message: str = "") -> Outcome:
        session, said = "", []
        tokens = {"input": 0, "cached": 0, "output": 0}
        for line in stdout.splitlines():
            event = _json_line(line)
            if not event:
                continue
            if "response" in event:  # --output-format json: one document
                usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
                assert isinstance(usage, dict)
                return Outcome(
                    text=str(event.get("response") or ""),
                    session=str(event.get("conversation_id") or ""), cost_usd=0.0,
                    tokens={"input": _int(usage.get("input_tokens")),
                            "cached": _int(usage.get("cache_read_tokens")),
                            "output": _int(usage.get("output_tokens"))})
            session = session or self.session_in(line)
            step = event.get("step_update")
            if isinstance(step, dict) and step.get("step_type") == "agent_response":
                said.append(str(step.get("text_delta") or ""))
                usage = step.get("usage")
                if isinstance(usage, dict):
                    tokens["input"] += _int(usage.get("input_tokens"))
                    tokens["cached"] += _int(usage.get("cache_read_tokens"))
                    tokens["output"] += _int(usage.get("output_tokens"))
        return Outcome(text="".join(said), session=session, cost_usd=0.0, tokens=tokens)

    def session_in(self, line: str) -> str:
        event = _json_line(line)
        if event.get("event") == "init":
            return str(event.get("conversation_id") or "")
        return ""

    def lane_setup(self, home: Path, *, model: str, server: Mapping[str, object] | None,
                   operator_home: Path, environ: Mapping[str, str]) -> LaneSetup:
        # agy reads its MCP servers from ~/.gemini/config/mcp_config.json and migrates
        # ~/.gemini/antigravity-cli/mcp_config.json into it once. The lane's HOME links every
        # piece of the operator's antigravity-cli state (the sign-in token among them) except
        # the MCP configuration, writes the lane's own in both places, and marks the migration
        # done. The operator's GEMINI.md, hooks and skills are not linked.
        private = _keychain_home(home, operator_home)
        source = operator_home / ".gemini" / "antigravity-cli"
        state = private / ".gemini" / "antigravity-cli"
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        own = {"mcp_config.json", "mcp_oauth_tokens.json", "mcp"}
        if source.is_dir():
            for entry in source.iterdir():
                if entry.name not in own:
                    _link(state / entry.name, entry)
        document = json.dumps({"mcpServers": _mcp_servers(server)})
        _write(state / "mcp_config.json", document)
        _write(private / ".gemini" / "config" / "mcp_config.json", document)
        _write(private / ".gemini" / "config" / ".migrated", "")
        return LaneSetup(env={"HOME": str(private)})


class Muse(Harness):
    """Meta's Muse Code. ``muse exec --json`` streams session events; the first carries the
    session id, and ``--session-id`` continues it. ``META_API_KEY`` overrides the account sign-in,
    so a plan lane never admits it."""

    def turn(self, *, model: str, effort: str, cwd: Path, resume: str = "",
             last_message_file: str = "", max_turns: int = 400,
             extra: Sequence[str] = ()) -> Turn:
        argv = ["muse", "exec", "--json", "--workspace", str(cwd), "--yolo",
                "--disable-request-user-input", "--no-foreign-personal-context"]
        argv += ["--model", model] if model else []
        argv += ["--reasoning-effort", effort] if effort else []
        argv += ["--session-id", resume] if resume else []
        argv += list(extra)
        return Turn(tuple(argv), "argv")

    def outcome(self, stdout: str, last_message: str = "") -> Outcome:
        session, text = "", ""
        for line in stdout.splitlines():
            event = _json_line(line)
            if not event:
                continue
            session = session or self.session_in(line)
            payload = event.get("payload")
            if str(event.get("payload_type", "")).startswith("run.terminal") \
                    and isinstance(payload, dict):
                text = str(payload.get("text") or text)
        return Outcome(text=text, session=session, cost_usd=0.0)

    def session_in(self, line: str) -> str:
        stream = _json_line(line).get("stream")
        if isinstance(stream, dict) and stream.get("kind") == "session":
            return str(stream.get("id") or "")
        return ""

    def lane_setup(self, home: Path, *, model: str, server: Mapping[str, object] | None,
                   operator_home: Path, environ: Mapping[str, str]) -> LaneSetup:
        # Muse reads $XDG_CONFIG_HOME/muse: the lane's holds a link to the operator's sign-in and
        # a settings file naming only the lane's Kindex. Muse passes an MCP child a small env
        # allowlist plus the literal env map, and never expands ${VAR}.
        base = Path(environ.get("XDG_CONFIG_HOME") or operator_home / ".config")
        config = home / "xdg"
        auth = base / "muse" / "auth.json"
        if auth.is_file():
            _link(config / "muse" / "auth.json", auth)
        servers: dict[str, object] = {}
        if server is not None:
            command, args, env = _server(server)
            servers["kindex"] = {"type": "stdio", "command": command, "args": args, "env": env,
                                 "mode": "optional"}
        _write(config / "muse" / "settings.json", json.dumps(
            {"schema_version": 1, "provider": "meta", "mcpServers": servers}))
        return LaneSetup(env={"XDG_CONFIG_HOME": str(config)})


class PrimeAgent(Harness):
    # JSON mode re-emits the whole growing message on every streamed token; kept, one long
    # session's log grows quadratically into gigabytes. outcome() reads only message_end.
    def noise(self, line: str) -> bool:
        return re.search(r'"type"\s*:\s*"message_update"', line[:160]) is not None

    def turn(self, *, model: str, effort: str, cwd: Path, resume: str = "",
             last_message_file: str = "", max_turns: int = 400,
             extra: Sequence[str] = ()) -> Turn:
        argv = ["prime-agent", "--print", "--mode", "json", "--cwd", str(cwd)]
        provider, _, name = model.partition("/") if model.count("/") >= 2 else ("", "", model)
        argv += ["--provider", provider] if provider else []
        argv += ["--model", name] if name else []
        argv += ["--thinking", effort] if effort else []
        argv += ["--resume", resume] if resume else []
        argv += list(extra)
        argv += ["--"]
        return Turn(tuple(argv), "argv")

    def outcome(self, stdout: str, last_message: str = "") -> Outcome:
        session, text, cost = "", "", 0.0
        tokens = {"input": 0, "cached": 0, "output": 0}
        for line in stdout.splitlines():
            event = _json_line(line)
            if event.get("type") == "session" and event.get("id"):
                session = str(event["id"])
            message = event.get("message")
            if event.get("type") == "message_end" and isinstance(message, dict) \
                    and message.get("role") == "assistant":
                usage = message.get("usage") or {}
                if isinstance(usage, dict):
                    total = (usage.get("cost") or {}).get("total") \
                        if isinstance(usage.get("cost"), dict) else 0
                    cost += float(total or 0)
                    tokens["input"] += _int(usage.get("input"))
                    tokens["cached"] += _int(usage.get("cacheRead"))
                    tokens["output"] += _int(usage.get("output"))
                parts = message.get("content") or []
                said = "".join(p.get("text", "") for p in parts
                               if isinstance(p, dict) and p.get("type") == "text")
                text = said or text
        return Outcome(text=text, session=session, cost_usd=cost, tokens=tokens)

    def session_in(self, line: str) -> str:
        event = _json_line(line)
        return str(event.get("id") or "") if event.get("type") == "session" else ""


class CursorAgent(Harness):
    """Cursor's agent CLI (Grok and the other models Cursor serves). The model name carries the
    reasoning effort (``grok-4.7-xhigh``). Its stream's init event names the billing source:
    ``login`` is the plan."""

    def turn(self, *, model: str, effort: str, cwd: Path, resume: str = "",
             last_message_file: str = "", max_turns: int = 400,
             extra: Sequence[str] = ()) -> Turn:
        argv = ["agent", "-p", "--output-format", "stream-json", "--force", "--trust",
                "--approve-mcps", "--workspace", str(cwd)]
        argv += ["--model", model] if model else []
        argv += ["--resume", resume] if resume else []
        argv += list(extra)
        return Turn(tuple(argv), "argv")

    def outcome(self, stdout: str, last_message: str = "") -> Outcome:
        doc = _last_json(stdout)
        if doc.get("type") != "result":
            return Outcome(text=stdout.strip()[-20_000:], session="", cost_usd=0.0)
        usage = doc.get("usage") if isinstance(doc.get("usage"), dict) else {}
        assert isinstance(usage, dict)
        return Outcome(
            text=str(doc.get("result") or ""), session=str(doc.get("session_id") or ""),
            cost_usd=0.0,
            tokens={"input": _int(usage.get("inputTokens")),
                    "cached": _int(usage.get("cacheReadTokens")),
                    "output": _int(usage.get("outputTokens"))},
        )

    def session_in(self, line: str) -> str:
        doc = _json_line(line)
        if doc.get("type") == "system" and doc.get("subtype") == "init":
            return str(doc.get("session_id") or "")
        return ""

    def billing_in(self, line: str) -> str:
        doc = _json_line(line)
        if doc.get("type") != "system" or doc.get("subtype") != "init" \
                or "apiKeySource" not in doc:
            return ""
        return "plan" if doc.get("apiKeySource") == "login" else "api"

    def lane_setup(self, home: Path, *, model: str, server: Mapping[str, object] | None,
                   operator_home: Path, environ: Mapping[str, str]) -> LaneSetup:
        # cursor-agent reads ~/.cursor/mcp.json (and the operator's rules and hooks) from HOME;
        # its sign-in is in the keychain. The lane's HOME has only its own mcp.json.
        private = _keychain_home(home, operator_home)
        config = private / ".cursor"
        _write(config / "mcp.json", json.dumps({"mcpServers": _mcp_servers(server)}))
        return LaneSetup(env={"HOME": str(private), "CURSOR_CONFIG_DIR": str(config)})


HARNESSES: Mapping[str, Harness] = {
    "claude": ClaudeCode("claude", "claude", ("CLAUDE_CONFIG_DIR", "IS_SANDBOX"), True,
                         api_env=("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                                  "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                                  "CLAUDE_CODE_USE_VERTEX")),
    "claude-ollama": ClaudeOllama("claude-ollama", "claude", ("IS_SANDBOX",), True),
    "codex": Codex("codex", "codex", ("CODEX_HOME",), True, api_env=("OPENAI_API_KEY",)),
    "agy": Antigravity("agy", "agy", (), True,
                       api_env=("GEMINI_API_KEY", "GOOGLE_API_KEY", "AGY_LLM_GATEWAY_API_KEY")),
    "muse": Muse("muse", "muse", (), True, api_env=("META_API_KEY",)),
    "prime-agent": PrimeAgent("prime-agent", "prime-agent", (), True,
                              api_env=("OPENROUTER_API_KEY",), plan=False),
    "cursor-agent": CursorAgent("cursor-agent", "agent", (), True, api_env=("CURSOR_API_KEY",)),
}


def harness(name: str) -> Harness:
    try:
        return HARNESSES[name]
    except KeyError:
        raise SystemExit(f"agents: unknown harness {name!r} ({', '.join(HARNESSES)})") from None


def write_env_file(path: Path, values: Mapping[str, str]) -> None:
    """The owner-only ``NAME=value`` file ``lane_agent.py run --env-file`` loads."""

    lines = []
    for name, value in values.items():
        if not name.isidentifier() or "\n" in value or "\0" in value:
            raise ValueError(f"variable {name!r} cannot travel in an env file")
        lines.append(f"{name}={value}\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                         | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write("".join(lines))
    path.chmod(0o600)


def prepare_lane(name: str, *, billing: str, home: Path, model: str,
                 kindex_record: Mapping[str, object] | None, env_file: Path,
                 environ: Mapping[str, str]) -> LaneSetup:
    """Build a lane's private configuration and its env file; return what every turn carries."""

    chosen = harness(name)
    admitted = chosen.admitted_env(billing)
    if billing == "plan":
        present = [variable for variable in chosen.api_env if environ.get(variable)]
        # Not an error: the cleared environment drops them. Said so the operator knows.
        for variable in present:
            print(f"agents: {variable} is set in the launching shell; this plan lane does not "
                  "admit it", file=sys.stderr)
    server: Mapping[str, object] | None = None
    if kindex_record is not None:
        found = kindex_record.get("mcp_server")
        if not isinstance(found, Mapping):
            raise ValueError("the lane Kindex record has no mcp_server")
        server = found
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    setup = chosen.lane_setup(home, model=model, server=server,
                              operator_home=Path(environ.get("HOME") or Path.home()),
                              environ=environ)
    values = {variable: environ[variable] for variable in admitted if environ.get(variable)}
    values.update(setup.env)
    write_env_file(env_file, values)
    return setup


def prepare_seat(name: str, *, billing: str, model: str, env_file: Path,
                 environ: Mapping[str, str]) -> None:
    """The owner-only env file a resident seat's wrapper loads: admitted variables and the
    harness's fixed seat variables, nothing else from the launching shell."""

    chosen = harness(name)
    values = {variable: environ[variable] for variable in chosen.admitted_env(billing)
              if environ.get(variable)}
    values.update(chosen.seat_env(model))
    write_env_file(env_file, values)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="the registered harnesses")
    describe = sub.add_parser("describe", help="resumable and plan, NUL-separated")
    describe.add_argument("--agent", required=True)
    turn = sub.add_parser("turn", help="print one headless turn's argv, NUL-separated")
    turn.add_argument("--agent", required=True)
    turn.add_argument("--model", default="")
    turn.add_argument("--effort", default="")
    turn.add_argument("--cwd", required=True, type=Path)
    turn.add_argument("--resume", default="")
    turn.add_argument("--extra-arg", action="append", default=[])
    env = sub.add_parser("credential-env", help="print the admitted variable names, one a line")
    env.add_argument("--agent", required=True)
    env.add_argument("--billing", choices=BILLINGS, default="plan")
    setup = sub.add_parser("lane-setup", help="build a lane's private configuration and env "
                           "file; print the arguments every turn carries, NUL-separated")
    setup.add_argument("--agent", required=True)
    setup.add_argument("--billing", choices=BILLINGS, required=True)
    setup.add_argument("--home", required=True, type=Path)
    setup.add_argument("--model", default="")
    setup.add_argument("--kindex-record", default="")
    setup.add_argument("--env-file", required=True, type=Path)
    api = sub.add_parser("api-env", help="the variables that would switch a harness to per-use "
                         "API billing, one a line (a plan seat unsets them)")
    api.add_argument("--agent", required=True)
    seat = sub.add_parser("seat-env", help="write a resident seat's owner-only env file")
    seat.add_argument("--agent", required=True)
    seat.add_argument("--billing", choices=BILLINGS, default="plan")
    seat.add_argument("--model", default="")
    seat.add_argument("--env-file", required=True, type=Path)
    record = sub.add_parser("turn-record", help="write the owner-only record a lane message "
                            "reads to resume this lane's session the way it was launched")
    record.add_argument("--output", required=True, type=Path)
    record.add_argument("--agent", required=True)
    record.add_argument("--model", default="")
    record.add_argument("--effort", default="")
    record.add_argument("--billing", choices=BILLINGS, required=True)
    record.add_argument("--env-file", required=True)
    record.add_argument("--setup", required=True, type=Path,
                        help="lane-setup's NUL-separated output")
    args = parser.parse_args(argv)
    try:
        if args.command == "turn-record":
            harness(args.agent)
            extra = [part for part in args.setup.read_text(encoding="utf-8").split("\0") if part]
            # The record holds no secret; the env file it names does, owner-only.
            descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                                 | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": TURN_RECORD, "agent": args.agent,
                           "model": args.model, "effort": args.effort, "billing": args.billing,
                           "env_file": args.env_file, "extra": extra}, handle)
            return 0
        if args.command == "api-env":
            print("\n".join(harness(args.agent).api_env))
            return 0
        if args.command == "seat-env":
            prepare_seat(args.agent, billing=args.billing, model=args.model,
                         env_file=args.env_file, environ=os.environ)
            return 0
        if args.command == "list":
            print("\n".join(HARNESSES))
        elif args.command == "describe":
            chosen = harness(args.agent)
            sys.stdout.write(f"{'resumable' if chosen.resumable else 'single'}\0"
                             f"{'plan' if chosen.plan else 'metered-only'}\0")
        elif args.command == "credential-env":
            print("\n".join(harness(args.agent).admitted_env(args.billing)))
        elif args.command == "lane-setup":
            kindex = json.loads(args.kindex_record) if args.kindex_record else None
            built = prepare_lane(args.agent, billing=args.billing, home=args.home,
                                 model=args.model, kindex_record=kindex,
                                 env_file=args.env_file, environ=os.environ)
            sys.stdout.write("".join(part + "\0" for part in built.argv))
        else:
            made = harness(args.agent).turn(model=args.model, effort=args.effort, cwd=args.cwd,
                                            resume=args.resume, extra=args.extra_arg)
            sys.stdout.write("\0".join((made.prompt_via, *made.argv)) + "\0")
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"agents: {error}", file=sys.stderr)
        return 70
    return 0


if __name__ == "__main__":
    sys.exit(main())
