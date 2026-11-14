#!/usr/bin/env python3
"""agents — one registry of the agent harnesses a Factory seat can run, so any harness can hold
any seat.

The factory names no model and no vendor. What it needs from a harness is a shape: how to start
it in a working directory with a named model and reasoning effort, how to hand it the prompt, how
to resume the same session, which variables it reads, how to give a lane its own scoped Kindex,
and how to read the final message, session id and cost back out of its output.

Claude Code is never a headless turn (founder, 2026-11-13). Its entry builds the arguments of
the operator's own interactive session, which ``native_session.py`` hosts on a terminal: no
print mode, no API key, and nothing that replaces or discards the operator's settings, hooks,
plugins or MCP servers. The lane's scoped Kindex is added beside them.

Each harness is data here; launchers (``tmux_lane.sh``, ``factory.sh``,
``tmux_lane_message.sh``) ask the registry instead of carrying per-vendor branches, and a new
harness is one entry.

Each model runs in its own vendor's harness where one exists (it was trained there): Claude in
Claude Code, GPT in Codex, Gemini in Antigravity, Muse in Muse Code, Grok in Cursor's agent CLI,
and GLM in Claude Code pointed at the local Ollama server (``ollama launch claude``'s shape).

Billing (founder direction, 2026-10-16): a seat runs on the operator's signed-in plan, never on a
per-use API key, unless the operator launches it metered. ``api_env`` names the variables that
would switch a harness to per-use billing; a ``plan`` lane's environment never admits them, its
preflight proves a plan sign-in (``lane_agent.py``), and a harness that announces its billing
source in its first streamed event is stopped by the lane wrapper if that source is an API key.

Claude Code is subscription-only (founder, 2026-11-13): it has no metered launch at all
(``Harness.metered`` is False). Asking for one is refused where the request is made, in
``admitted_env``, so no lane home, seat file, credential list or turn record is ever prepared
for it and none of its API variables is ever copied anywhere. Its ``api_env`` remains the list
of names the wrapper unsets and refuses. ``claude-ollama`` is a different entry: it names its
loopback server on purpose and is unaffected.

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

_HARNESS_ROOT = str(Path(__file__).resolve().parent)
if _HARNESS_ROOT not in sys.path:
    sys.path.insert(0, _HARNESS_ROOT)
import signet_hooks  # noqa: E402

BILLINGS = ("plan", "metered")
TURN_RECORD = "factory-lane-turn/1"
# The address Ollama serves its Anthropic-compatible Messages API on; claude-ollama talks to
# nothing else, so no Anthropic key can leave the machine through it.
OLLAMA_BASE_URL = "http://127.0.0.1:11434"


@dataclass(frozen=True)
class Turn:
    """A native invocation: argv, prompt transport, and where a final message lands.

    Persistent invocations keep stdin open for frames from ``persistent_input``.
    """

    argv: tuple[str, ...]
    # "stdin" | "argv" (appended as the last argument) | "terminal" (typed into the native
    # interactive session by the wrapper that owns its terminal; never an argument)
    prompt_via: str
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
    # The Factory can continue this lane's session after its turn ended, by a lane message
    # (which is what a wrap-up or a review pause does). It says what the Factory does, not
    # whether the program has a resume flag: `turn(resume=...)` builds that flag regardless.
    resumable: bool
    api_env: tuple[str, ...] = ()  # variables that switch the harness to per-use API billing
    plan: bool = True  # the harness has a signed-in plan path
    metered: bool = True  # the Factory may launch it on per-use billing; False: plan only
    # Whether a lane's memory is its own. "lane-only": the lane sees its scoped Kindex and no
    # MCP server of the operator's (a private configuration; the operator's settings are not
    # loaded). "beside-operator": the operator's settings and MCP servers are intact and the
    # lane's Kindex is added beside them, so any memory server the operator configured is
    # visible to the lane too. Independence of Coder and Tester then rests on their separate
    # inputs and clones, not on memory, and a launch says so.
    memory: str = "lane-only"
    # How this provider's interactive program reports its lifecycle to the lane wrapper
    # (native_session.DIALECTS), or "" when it is not hosted as a native session.
    dialect: str = ""
    # What the provider's own program asks the operator when a lane starts, and what the
    # Factory does and does not approve for them. Printed and recorded at launch, so a
    # question waiting in a lane's window is expected rather than discovered.
    start_requires: str = ""
    # A line this provider's lanes need in their protocol prompt, or "".
    protocol_note: str = ""
    # Why this provider's lanes are not yet its own interactive program with the operator's
    # configuration intact, or are but have not been accepted for unattended work; "" only
    # when neither is open. Stated where the registry is read, so the
    # limit is a recorded blocker and never a silent difference.
    native_blocker: str = ""

    def turn(self, *, model: str, effort: str, cwd: Path, resume: str = "",
             last_message_file: str = "", max_turns: int = 400,
             extra: Sequence[str] = ()) -> Turn:
        raise NotImplementedError

    def persistent_turn(self, *, model: str, effort: str, cwd: Path, resume: str = "",
                        extra: Sequence[str] = ()) -> Turn:
        """Keep one native process alive for successive messages.

        Claude Code's is its interactive session, unchanged (``prompt_via`` is ``terminal``).
        Agy's is structured stdin. Other harnesses need their own qualified transport before
        they can opt in.
        """

        if self.name not in ("claude", "claude-ollama", "cursor-agent", "agy"):
            raise ValueError(f"{self.name} does not support persistent native input")
        built = self.turn(model=model, effort=effort, cwd=cwd, resume=resume, extra=extra)
        if built.prompt_via == "terminal":
            return built
        # agy's --print requires a value even when input arrives through the stream.
        argv = (*built.argv[:-1], "--input-format", "stream-json", "--print", "")
        return Turn(argv, "stdin")

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
        if billing == "metered" and not self.metered:
            raise ValueError(
                f"{self.name} runs only on the operator's signed-in subscription: it has no "
                "metered launch and is never given an API key")
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


def persistent_input(agent_name: str, text: str, message_id: str) -> dict[str, object]:
    """Encode literal user text; callers serialize each frame as one JSON line.

    Agy does not acknowledge an input ID, so its caller must allow only one in-flight
    message at a time. Claude Code has no stream frame: its input is typed into its terminal.
    """

    if agent_name == "agy":
        return {"event": "user", "message": {"content": text}}
    raise ValueError(f"{agent_name} does not support persistent stream input")


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
        # The interactive program and nothing that changes what it is: the operator's
        # assigned model and effort when they named one, and the conversation to continue.
        # No permission argument. What a session may do without asking is the operator's
        # choice, made where they make it natively: their launcher (``cl`` passes its own
        # permissive flags) and their Claude Code settings. The registry imposes neither
        # ``bypassPermissions`` nor any other mode on top of that. An operator who selects
        # the permissive mode explicitly (FACTORY_LANE_SANDBOX=off) gets Claude Code's own
        # flag for it from the launcher of the lane or seat, not from here. ``max_turns``
        # bounds print mode only and has no meaning here.
        argv = ["claude"]
        argv += ["--model", model] if model else []
        argv += ["--effort", effort] if effort else []
        argv += ["--resume", resume] if resume else []
        argv += list(extra)
        return Turn(tuple(argv), "terminal")

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
        # The operator's configuration directory, settings, hooks, plugins, CLAUDE.md and MCP
        # servers all stay exactly as they are. The lane's scoped Kindex is added beside them.
        config = _write(home / "mcp.json", json.dumps({"mcpServers": _mcp_servers(server)}))
        return LaneSetup(argv=("--mcp-config", str(config)))


class ClaudeOllama(ClaudeCode):
    """Claude Code on a model the local Ollama server serves (GLM and other open models), the way
    ``ollama launch claude`` runs it: the same interactive program, pointed at the loopback
    server by ``ANTHROPIC_BASE_URL`` with Ollama's fixed bearer word. No Anthropic API key is
    set or read, and nothing can leave the machine through it. Billing is the Ollama plan."""

    def billing_in(self, line: str) -> str:
        return ""  # always the loopback Ollama server: lane_setup fixes the base URL

    def lane_setup(self, home: Path, *, model: str, server: Mapping[str, object] | None,
                   operator_home: Path, environ: Mapping[str, str]) -> LaneSetup:
        config = _write(home / "mcp.json", json.dumps({"mcpServers": _mcp_servers(server)}))
        return LaneSetup(env=self.seat_env(model), argv=("--mcp-config", str(config)))

    def seat_env(self, model: str) -> Mapping[str, str]:
        if not model:
            raise ValueError("claude-ollama needs a model (an Ollama model, e.g. glm-5.3:cloud)")
        return {
                "ANTHROPIC_BASE_URL": OLLAMA_BASE_URL,
                # Ollama's bearer word for its loopback server. Never the API key variable:
                # that one is not set for Claude Code under any launch.
                "ANTHROPIC_AUTH_TOKEN": "ollama",
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


#: The lane's Kindex as Cursor knows it. Not "kindex": an operator who has their own server of
#: that name keeps it, and the lane's permission rule must not be able to match theirs.
CURSOR_LANE_SERVER = signet_hooks.CURSOR_LANE_KINDEX


class CursorAgent(Harness):
    """Cursor's agent CLI, interactive (Grok and the other models Cursor serves): the
    operator's own sign-in, rules, hooks and MCP servers, all untouched and all still loaded.
    The model name carries the reasoning effort (``grok-4.7-xhigh``).

    The lane's hooks and its Kindex reach it the one lane-scoped way Cursor offers: the
    project files ``.cursor/hooks.json`` and ``.cursor/mcp.json`` in the lane's own clone,
    written and removed by ``native_session.cursor_plan`` and never over a target's own
    file. The interface was proven against the interactive program by the independent
    tester on 2026-11-13, and the tester then ran this adapter against real Cursor
    (cursor-agent 2026.11.13): at ``aa8870b`` a lane started by ``tmux_lane.sh`` took its
    first prompt and a second message sent by ``tmux_lane_message.sh``, reported
    ``observed`` with matching text in the same session; at ``aae71ef`` the model called
    the lane server's ``scope_info``. Not exercised live: resume, a Cursor coordinator
    seat, and a required Signet guard.

    Permissions are the operator's own. With their configuration loaded, Cursor's blanket
    flags would reach everything they have configured, so none is passed: not ``--force``
    (run everything unless denied), not ``--approve-mcps`` (approve every MCP server), and
    not ``--trust``, which Cursor documents as headless-only and so grants nothing here.
    The one thing added is the documented project rule ``Mcp(<lane server>:*)`` in
    ``.cursor/cli.json``, for the lane's own Kindex server, which is named apart from any
    server of the operator's so the rule can match nothing else."""

    def turn(self, *, model: str, effort: str, cwd: Path, resume: str = "",
             last_message_file: str = "", max_turns: int = 400,
             extra: Sequence[str] = ()) -> Turn:
        # The interactive program and no approval of anything: see the class note.
        argv = ["agent", "--workspace", str(cwd)]
        argv += ["--model", model] if model else []
        argv += [f"--resume={resume}"] if resume else []
        argv += list(extra)
        return Turn(tuple(argv), "terminal")

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
        # The operator's HOME, ~/.cursor and keychain are the lane's too, exactly as they
        # are. The lane's Kindex is left here for the wrapper, which places it in the lane
        # clone's project MCP file for as long as the session runs.
        lane = _mcp_servers(server).get("kindex")
        servers = {CURSOR_LANE_SERVER: lane} if lane is not None else {}
        _write(home / "cursor-mcp.json", json.dumps({"mcpServers": servers}))
        if servers:
            # Cursor's documented per-server rule (cli/reference/permissions): the lane's
            # own server's tools, and nothing of the operator's. Cursor's schema for this
            # file requires both lists; real Cursor (2026.11.13) refuses to start on a
            # project file without `deny`. The empty list denies nothing.
            _write(home / "cursor-cli.json", json.dumps(
                {"permissions": {"allow": [f"Mcp({CURSOR_LANE_SERVER}:*)"], "deny": []}}))
        return LaneSetup()


# What stands between each remaining provider and the Claude arrangement (its own
# interactive program, the operator's configuration intact, the lane Kindex added beside it,
# and receipt reported by the program itself). "Established" below means read from the named
# source on 2026-11-13; none of it has been run in a lane. Until a blocker is cleared, that
# provider's lanes keep the print-mode start and private configuration below: converting
# without these would cost the lane its Kindex and any way to send it a message, which is
# worse than the defect.
_TO_SHOW = (
    " To clear: one live interactive session showing a start report, a report carrying a "
    "typed prompt, a stop report, and the lane Kindex loading from the mechanism named.")
_BLOCKERS = {
    "agy": "Antigravity. Established (its own `agy changelog`): hooks.json events "
           "SessionStart, PreInvocation (input carries lastUserInput), Stop (input carries "
           "continuation_active); hook input carries hookEventName and cwd; follow-up "
           "messages are queued until the turn ends by default; plugins may bundle MCP "
           "servers and a workspace may list plugins in .agents/plugins.json. Not "
           "established: that a project .agents/hooks.json and a workspace plugin load "
           "without an edit to the operator's configuration or a trust prompt." + _TO_SHOW,
    # The lane is Cursor's own interactive agent with the operator's configuration, and the
    # independent tester has run it against real Cursor. What follows is what is still open.
    "cursor-agent": "Cursor: hosted as its own interactive agent. The independent tester ran "
                    "this adapter against real Cursor (cursor-agent 2026.11.13). At aa8870b a "
                    "lane started by tmux_lane.sh took its first prompt and a second message "
                    "sent by tmux_lane_message.sh, reported `observed` with matching text in "
                    "the same session; the generated permission file was accepted and "
                    "lane-kindex was listed ready beside the operator's servers. At aae71ef "
                    "the model called the lane server's scope_info, read-only. Still open: "
                    "unattended resume (no lane command resumes a session, and a resumed "
                    "session reported nothing until prompted); a Cursor coordinator seat, "
                    "which has not been exercised; a required Signet guard, not shown "
                    "enforcing end to end; and what the lane's Kindex returns for a seeded "
                    "search.",
    "muse": "Muse. Established (probed live on muse 1.4.6, 2026-10-12, and its own help): "
            "events sessionStart, userPromptSubmit, stop, notification, sessionEnd; hooks by "
            "per-run `-c hooks=<json>` or project .muse/hooks.json (off until the workspace "
            "is trusted); project MCP file <repository>/.mcp.json. Not established: whether "
            "`-c hooks=` adds to or replaces the operator's hooks (clashing keys refuse the "
            "run), and the userPromptSubmit payload. Its sign-in has no status command, so "
            "the preflight still reads whether a sign-in is stored." + _TO_SHOW,
    "prime-agent": "Prime: not installed on the machine this was assessed on; its interactive "
                   "program and any report or MCP interface are unassessed.",
    # Lane agents `codex` and `codex-ollama`. Their configuration defect is fixed (nothing of
    # the operator's Codex configuration is ignored); their mode is not.
    "codex": "Codex: this lane runs `codex exec`, the non-interactive mode, not the "
             "interactive program. Established (`codex --help` here, and this harness's own "
             "use): the interactive program is `codex [PROMPT]` (lane agent "
             "codex-interactive), `codex resume [SESSION_ID]` continues a session, `codex "
             "queue` queues a message for an existing session, and hooks given by `-c` are "
             "skipped until the operator trusts them. Not established: any report from the "
             "interactive program that it started, received a prompt or stopped, and that "
             "`codex queue` reaches a live interactive session, so a codex-interactive lane "
             "can be neither messaged nor resumed by the Factory and cannot replace exec "
             "for unattended work." + _TO_SHOW,
}
#: Lane agents that share a registry entry, and the blocker each really has. A lane agent's
#: truth is its own: `codex-interactive` is already the interactive program with the
#: operator's configuration (no blocker in this sense, though the Factory cannot message or
#: resume it), while `codex` and `codex-ollama` still run `codex exec`.
#: Each maps to (registry entry, blocker or None for the entry's own, resumable by the Factory).
LANE_VARIANTS: Mapping[str, tuple[str, str | None, bool]] = {
    "codex-interactive": ("codex", "", False),
    "codex-ollama": ("codex", None, True),
}


def describe_lane(name: str) -> tuple[Harness, str, bool]:
    """The registry entry a lane agent runs on, that lane agent's own native blocker, and
    whether the Factory can continue its session."""

    if name not in LANE_VARIANTS:
        chosen = harness(name)
        return chosen, chosen.native_blocker, chosen.resumable
    base, blocker, resumable = LANE_VARIANTS[name]
    chosen = harness(base)
    return chosen, chosen.native_blocker if blocker is None else blocker, resumable

HARNESSES: Mapping[str, Harness] = {
    "claude": ClaudeCode("claude", "claude", ("CLAUDE_CONFIG_DIR", "IS_SANDBOX"), True,
                         api_env=("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                                  "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                                  "CLAUDE_CODE_USE_VERTEX"), memory="beside-operator",
                         dialect="claude",
                         start_requires=(
                             "Claude Code asks its own folder-trust question for a directory "
                             "it has not seen; answer it in the lane's window. Until the "
                             "session reports starting it is `starting` and nothing is typed "
                             "to it."),
                         metered=False),
    "claude-ollama": ClaudeOllama("claude-ollama", "claude", ("IS_SANDBOX",), True,
                                  memory="beside-operator", dialect="claude"),
    # Codex lanes are launched by tmux_lane.sh, with the operator's whole configuration, and
    # still by `codex exec` (see LANE_VARIANTS for the interactive lane agent).
    "codex": Codex("codex", "codex", ("CODEX_HOME",), True, api_env=("OPENAI_API_KEY",),
                   memory="beside-operator", native_blocker=_BLOCKERS["codex"]),
    "agy": Antigravity("agy", "agy", (), True,
                       api_env=("GEMINI_API_KEY", "GOOGLE_API_KEY", "AGY_LLM_GATEWAY_API_KEY"),
                       native_blocker=_BLOCKERS["agy"]),
    "muse": Muse("muse", "muse", (), True, api_env=("META_API_KEY",),
                 native_blocker=_BLOCKERS["muse"]),
    "prime-agent": PrimeAgent("prime-agent", "prime-agent", (), True,
                              api_env=("OPENROUTER_API_KEY",), plan=False,
                              native_blocker=_BLOCKERS["prime-agent"]),
    # Not resumable by the Factory yet: no lane command restarts an ended Cursor session, and
    # a resumed session reported nothing until it was given a prompt. Cursor's own resume is
    # untouched: `turn(resume=<id>)` builds `--resume=<id>`, the same conversation and never a
    # new one, and a lane message to a running session is typed into that session.
    "cursor-agent": CursorAgent(
        "cursor-agent", "agent", (), False, api_env=("CURSOR_API_KEY",),
        memory="beside-operator", dialect="cursor", native_blocker=_BLOCKERS["cursor-agent"],
        protocol_note=(
            f"- Your Kindex is the MCP server named {CURSOR_LANE_SERVER}. It is this lane's own. "
            "Do not call the\n  server named kindex: it is not this lane's memory. The files "
            ".cursor/hooks.json,\n  .cursor/mcp.json and .cursor/cli.json are this lane's "
            "runtime configuration, not the work:\n  do not edit them, and never add them to "
            "a commit (not with git add -f either).\n"),
        start_requires=(
            "Cursor asks its own questions in the lane's window and the Factory answers none "
            "of them: whether to trust the workspace, and whether to approve the lane's MCP "
            f"server `{CURSOR_LANE_SERVER}` (`agent mcp enable {CURSOR_LANE_SERVER}`, run in "
            "the lane's clone, approves that one server). What the lane may then run without "
            "asking is your own Cursor permission configuration; the Factory adds one rule, "
            f"`Mcp({CURSOR_LANE_SERVER}:*)`, and passes no --force, --approve-mcps or --trust. "
            "Until the session reports starting it is `starting` and nothing is typed to it.")),
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
    guard_path = environ.get("FACTORY_SIGNET_CONTRACT", "")
    guard_digest = environ.get("FACTORY_SIGNET_DIGEST", "")
    if bool(guard_path) != bool(guard_digest) or (
            (environ.get("FACTORY_REQUIRE_SIGNET") == "1"
             or environ.get("FACTORY_REQUIRE_AUTHOR_CUSTODY") == "1"
             or environ.get("FACTORY_AUTHOR_CUSTODY_CONFIG")
             or environ.get("FACTORY_AUTHOR_CUSTODY_DIGEST")) and not guard_path):
        raise ValueError("required Signet hook contract and digest are missing")
    if guard_path:
        settings = signet_hooks.configuration(name, Path(guard_path), guard_digest,
                                             Path(sys.executable),
            author_config=environ.get("FACTORY_AUTHOR_CUSTODY_CONFIG"),
            author_digest=environ.get("FACTORY_AUTHOR_CUSTODY_DIGEST"))
        if name == "claude":
            filename = _write(home / "signet-settings.json", json.dumps(settings))
            setup = LaneSetup(env=setup.env, argv=(*setup.argv, "--settings", str(filename)))
        elif name == "cursor-agent":
            # Joined to the lane's project hook file by the wrapper, beside the operator's.
            _write(home / "cursor-hooks.json", json.dumps(settings))
        else:
            raise ValueError("required Signet client adapter is not qualified")
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
    describe = sub.add_parser("describe", help="resumable, plan, memory isolation, the "
                              "native blocker (or empty), what a start asks of the "
                              "operator (or empty) and the lane protocol note (or empty), "
                              "NUL-separated")
    describe.add_argument("--agent", required=True)
    turn = sub.add_parser("turn", help="print one headless turn's argv, NUL-separated")
    turn.add_argument("--agent", required=True)
    turn.add_argument("--model", default="")
    turn.add_argument("--effort", default="")
    turn.add_argument("--cwd", required=True, type=Path)
    turn.add_argument("--resume", default="")
    turn.add_argument("--persistent", action="store_true",
                      help="keep the native process open for structured stdin messages")
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
    record.add_argument("--persistent", action="store_true")
    record.add_argument("--billing", choices=BILLINGS, required=True)
    record.add_argument("--env-file", required=True)
    record.add_argument("--setup", required=True, type=Path,
                        help="lane-setup's NUL-separated output")
    args = parser.parse_args(argv)
    try:
        if args.command == "turn-record":
            chosen = harness(args.agent)
            chosen.admitted_env(args.billing)  # a billing this harness has no launch for
            if args.persistent:
                chosen.persistent_turn(model=args.model, effort=args.effort, cwd=Path.cwd())
            extra = [part for part in args.setup.read_text(encoding="utf-8").split("\0") if part]
            document = {"schema_version": TURN_RECORD, "agent": args.agent,
                        "model": args.model, "effort": args.effort, "billing": args.billing,
                        "env_file": args.env_file, "extra": extra}
            if args.persistent:
                document["persistent"] = True
            # The record holds no secret; the env file it names does, owner-only.
            descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                                 | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle)
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
            # A lane agent name (codex-interactive, codex-ollama) answers for itself.
            chosen, blocker, resumable = describe_lane(args.agent)
            sys.stdout.write(f"{'resumable' if resumable else 'single'}\0"
                             f"{'plan' if chosen.plan else 'metered-only'}\0"
                             f"memory:{chosen.memory}\0{blocker}\0{chosen.start_requires}\0"
                             f"{chosen.protocol_note}\0")
        elif args.command == "credential-env":
            print("\n".join(harness(args.agent).admitted_env(args.billing)))
        elif args.command == "lane-setup":
            kindex = json.loads(args.kindex_record) if args.kindex_record else None
            built = prepare_lane(args.agent, billing=args.billing, home=args.home,
                                 model=args.model, kindex_record=kindex,
                                 env_file=args.env_file, environ=os.environ)
            sys.stdout.write("".join(part + "\0" for part in built.argv))
        else:
            chosen = harness(args.agent)
            build = chosen.persistent_turn if args.persistent else chosen.turn
            made = build(model=args.model, effort=args.effort, cwd=args.cwd,
                         resume=args.resume, extra=args.extra_arg)
            sys.stdout.write("\0".join((made.prompt_via, *made.argv)) + "\0")
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"agents: {error}", file=sys.stderr)
        return 70
    return 0


if __name__ == "__main__":
    sys.exit(main())
