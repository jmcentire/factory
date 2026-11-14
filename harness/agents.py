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
    # Nothing is typed into this program's session after its launch: a later message is kept
    # queued and left for a person to enter. Set where the program has no observed report of
    # a point at which typed input is safe.
    withhold_followups: bool = False
    # What the provider's own program asks the operator when a lane starts, and what the
    # Factory does and does not approve for them. Printed and recorded at launch, so a
    # question waiting in a lane's window is expected rather than discovered.
    start_requires: str = ""
    # A line this provider's lanes need in their protocol prompt, or "".
    protocol_note: str = ""
    # An ended session of this program is restarted by a lane message, on its own
    # conversation, while `resumable` stays false: the restart of a session that ended was
    # shown, and the watcher ending a live one to pause it or wrap it up was not. So the
    # watcher offers no pause for such a lane and never ends one to continue it; only a
    # message to a session that has already ended restarts it. `describe` prints
    # `restartable`.
    restart_by_message: bool = False
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

        For Claude Code, Cursor, Muse, Antigravity and interactive Codex it is the
        interactive session itself,
        unchanged (``prompt_via`` is ``terminal``). Any other harness needs its own
        qualified transport before it can opt in; none has a stream transport any more.
        """

        if self.name not in ("claude", "claude-ollama", "cursor-agent", "muse", "agy",
                             "codex-interactive"):
            raise ValueError(f"{self.name} does not support persistent native input")
        built = self.turn(model=model, effort=effort, cwd=cwd, resume=resume, extra=extra)
        if built.prompt_via != "terminal":
            raise ValueError(f"{self.name} has no native terminal turn")
        return built

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

    No harness has a stream transport any more: Antigravity's was its print mode fed over
    stdin, which is never used. Every resident session is the program's own interactive one,
    and what reaches it is typed or given at launch.
    """

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


class CodexInteractive(Harness):
    """Codex's own interactive session, hosted on a terminal the lane wrapper owns: the
    operator's sign-in, configuration, hooks and MCP servers, all untouched and all still
    loaded. Never ``codex exec``. This entry is the lane agents ``codex`` (the default author
    lane) and ``codex-interactive``; only ``codex-ollama`` still runs ``codex exec``.

    What decides the shape was observed on codex-cli 0.167.0 on the operator's ChatGPT
    sign-in, 2026-11-13: the first message given at launch is reported by Codex's own
    prompt hook; a message given with ``codex queue`` to the running session is reported
    the same way, with nothing typed; project hooks in ``.codex/hooks.json`` run when the
    folder and the hooks are trusted. ``--no-daemon`` keeps the session off the operator's
    shared background server."""

    def turn(self, *, model: str, effort: str, cwd: Path, resume: str = "",
             last_message_file: str = "", max_turns: int = 400,
             extra: Sequence[str] = ()) -> Turn:
        argv = ["codex", *(["resume"] if resume else []),
                "--no-daemon", "--no-alt-screen", "-C", str(cwd)]
        argv += ["-m", model] if model else []
        argv += ["-c", f"model_reasoning_effort={json.dumps(effort)}"] if effort else []
        argv += list(extra)
        # `codex resume [OPTIONS] [SESSION_ID] [PROMPT]`: the id after the options, which is
        # the form that was run (2026-11-14) and the one `native_session.codex_plan` reads.
        argv += [resume] if resume else []
        return Turn(tuple(argv), "terminal")


class Antigravity(Harness):
    """Google's Antigravity CLI, interactive: the operator's own sign-in, settings, hooks,
    plugins and MCP servers, all untouched and all still loaded. Never ``--print``.

    The shape follows what was observed of agy 1.5.8 in a trusted throwaway workspace, with
    prompts its own hook stopped before any model call (2026-11-13, by this adapter's author
    and independently by the tester):

    - The lane's hooks and its Kindex are a plugin in the lane's own clone,
      ``.agents/plugins/lane/``, written and removed by ``native_session.agy_plan``.
      Antigravity found it there by itself and ran its hooks beside the operator's. Nothing
      is added with ``agy mcp add`` or ``agy plugin install``: both write the operator's
      configuration.
    - No private HOME and no links to the operator's state.
    - Nothing is imposed on permissions: no ``--dangerously-skip-permissions`` (the launcher
      adds it only for ``FACTORY_LANE_SANDBOX=off``).
    - ``--conversation <id>`` continues the same conversation and is built whenever an id
      is given."""

    def turn(self, *, model: str, effort: str, cwd: Path, resume: str = "",
             last_message_file: str = "", max_turns: int = 400,
             extra: Sequence[str] = ()) -> Turn:
        # The lane wrapper runs it in the lane's clone, which is its workspace.
        argv = ["agy"]
        argv += ["--model", model] if model else []
        argv += ["--effort", effort] if effort else []
        argv += ["--conversation", resume] if resume else []
        argv += list(extra)
        return Turn(tuple(argv), "terminal")

    def lane_setup(self, home: Path, *, model: str, server: Mapping[str, object] | None,
                   operator_home: Path, environ: Mapping[str, str]) -> LaneSetup:
        # The lane's Kindex as the plugin's own server file, for agy_plan to place in the
        # clone. Antigravity names a plugin's server <plugin>_<server> (its changelog), so
        # it cannot be taken for a server of the operator's.
        _write(home / "agy-mcp.json", json.dumps({"mcpServers": _mcp_servers(server)}))
        return LaneSetup()


#: The name every native lane gives its own Kindex server, apart from any server of the
#: operator's (which is usually named ``kindex``), so nothing addressed to one reaches the other.
LANE_SERVER = signet_hooks.CURSOR_LANE_KINDEX


class Muse(Harness):
    """Meta's Muse Code, interactive: the operator's own sign-in, settings, hooks and MCP
    servers, all untouched and all still loaded. Never ``muse exec``.

    What decides the shape was observed on Muse Code 1.4.20 with its echo provider (no model,
    no sign-in), by this adapter's author and independently by the tester, 2026-11-13:

    - The lane's Kindex is one more server for this run, ``-c mcpServers.<lane server>=...``.
      Muse adds it beside the operator's servers; it refuses the whole map and changes no
      file. The server is named apart from any of the operator's. In that form Muse refuses
      a ``type`` key ("unknown key") and any literal ``env`` value ("secret leaves need
      exactly one whole-value ``${VAR}``"), so the server's variables, which are paths and
      not secrets, are set by ``/usr/bin/env`` in front of its command and reach only it.
    - The lane's hooks are the project file ``.muse/hooks.json`` in the lane's own clone,
      written and removed by ``native_session.muse_plan``. A per-run ``-c hooks=`` is never
      used: it replaces the operator's hooks instead of adding to them.
    - Muse asks its own workspace-trust question; no ``--trust-workspace`` is passed.
    - Nothing is imposed on permissions: no ``--yolo`` (the launcher adds it only for
      ``FACTORY_LANE_SANDBOX=off``), no ``--disable-request-user-input``.
    - ``resume <id>`` continues the same session and is built whenever a session id is given.

    ``META_API_KEY`` overrides the account sign-in, so a plan lane never admits it."""

    def turn(self, *, model: str, effort: str, cwd: Path, resume: str = "",
             last_message_file: str = "", max_turns: int = 400,
             extra: Sequence[str] = ()) -> Turn:
        argv = ["muse", *(["resume", resume] if resume else []), "--workspace", str(cwd)]
        argv += ["--model", model] if model else []
        argv += ["--reasoning-effort", effort] if effort else []
        argv += list(extra)
        return Turn(tuple(argv), "terminal")

    def lane_setup(self, home: Path, *, model: str, server: Mapping[str, object] | None,
                   operator_home: Path, environ: Mapping[str, str]) -> LaneSetup:
        # Nothing of the operator's configuration is replaced or linked.
        if server is None:
            return LaneSetup()
        command, args, env = _server(server)
        if any(not name.isidentifier() for name in env):
            raise ValueError("the lane Kindex record names a variable that is not a name")
        if env:
            command, args = "/usr/bin/env", [*(f"{name}={value}" for name, value in env.items()),
                                             command, *args]
        entry = {"command": command, "args": args, "mode": "optional"}
        return LaneSetup(argv=("-c", f"mcpServers.{LANE_SERVER}={json.dumps(entry)}"))


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
CURSOR_LANE_SERVER = LANE_SERVER


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
    # The lane is Antigravity's own interactive session with the operator's configuration.
    # What follows is what is open, not conversion.
    "agy": "Antigravity: hosted as its own interactive session. Two kinds of evidence, both on "
           "agy 1.5.8, 2026-11-13. Lifecycle probes with no model turn (the probe's own hook "
           "stopped each prompt before the model; by the adapter's author and independently "
           "by the tester): a plugin in a trusted workspace loaded by itself, its hooks ran "
           "beside the operator's, its server started beside the operator's, a typed marked "
           "message was reported in full. Actual executions of this adapter through the "
           "lane wrapper (author only, twice, on the operator's subscription, hooks not "
           "stopping the model): the first message given by --prompt-interactive was "
           "reported and matched, one stop report followed with terminationReason "
           "NO_TOOL_CALL, the plugin was removed at exit. The model's reply was not read or "
           "checked. Open. First, nothing with a tool call has been run, and those stop "
           "reports carried no model text, so a lane's done line is not seen from its "
           "reports. Second, later messages are not typed: "
           "Antigravity's Stop report carries fullyIdle and continuationActive, but what "
           "they say when a Stop hook continues the turn was not observed, so no report is "
           "taken as a safe moment; a later message is kept `queued` with the reason and "
           "left for a person to enter, and a review, an answer or a wrap-up notice does "
           "not reach an Antigravity lane unattended. When a person enters exactly that "
           "message followed by its own mark (the lane writes that one entry to a file) and "
           "Antigravity's own prompt report says so, it is recorded as received and later "
           "messages are accepted. The words without the mark settle nothing, because a "
           "report of the same words cannot be told from an earlier one repeated; none of "
           "Antigravity's counters is taken as proof of a fresh submission. (The settlement "
           "is the same code as Muse's and was run against Muse's echo provider only; "
           "against Antigravity only its report's mapping was checked.) Third, no lane "
           "command restarts an "
           "ended session, so wrap-up and review pauses are not offered. Fourth, there is "
           "no Signet guard and a launch that requires one is refused: a user shell action "
           "typed in the session ran with no hook called. This user-shell route did not "
           "exercise PreToolUse; model-tool hook failure behavior remains unverified. "
           "Fifth, the lane's server is named by Antigravity "
           "(<plugin>_<server>, per its changelog); that name and a model calling it were "
           "not observed.",
    # The lane is Cursor's own interactive agent with the operator's configuration, and the
    # independent tester has run it against real Cursor. What follows is what is still open.
    "cursor-agent": "Cursor: hosted as its own interactive agent. The independent tester ran "
                    "this adapter against real Cursor (cursor-agent 2026.11.13). At aa8870b a "
                    "lane started by tmux_lane.sh took its first prompt and a second message "
                    "sent by tmux_lane_message.sh, reported `observed` with matching text in "
                    "the same session; the generated permission file was accepted and "
                    "lane-kindex was listed ready beside the operator's servers. At aae71ef "
                    "the model called the lane server's scope_info, read-only. Restarting an "
                    "ended session is built and partly shown. Observed of Cursor itself "
                    "(cursor-agent 2026.11.13, 2026-11-14, four one-word turns in a workspace "
                    "Cursor already trusted, by the adapter's author; the hook capture was "
                    "read independently): `agent --resume=<id>` given nothing reported "
                    "nothing; given a message after `--` it reported that message at once on "
                    "the same conversation id, byte for byte, with no start report; a "
                    "message then typed into it was reported too. So a lane message to an "
                    "ended session restarts it that way, the message recorded as handed "
                    "over before the program exists and never handed over twice. That path "
                    "was run through tmux_lane.sh and tmux_lane_message.sh with a stand-in "
                    "program that calls no model, and once against Cursor itself "
                    "(2026-11-14, a new synthetic workspace, no API key, no blanket "
                    "option): Cursor asked its trust question once, at first launch, and "
                    "one shell command was approved once; after a clean end a lane message "
                    "restarted the same conversation id with no question asked and was "
                    "reported exactly; a follow-up typed into the restarted session was "
                    "reported; three prompt reports, three generation ids, each bound to "
                    "one message. That is all that is claimed: a Cursor lane is "
                    "`restartable` (a message restarts a session that has ended), not "
                    "`resumable`. The watcher ending a live Cursor session to pause it or "
                    "wrap it up, and continuing it, was not run, so no pause is offered "
                    "for a Cursor lane and the watcher never ends one for that. A "
                    "restarted program that ends before reporting leaves that one message "
                    "`unknown`, and another message is needed to restart the session. Not "
                    "run against Cursor, and not claimed: a restart after a crash or after "
                    "the session was ended by a signal; a restart whose program fails; a "
                    "restart in a workspace Cursor was not asked to trust at launch; a "
                    "required Signet guard on a first or a restarted session, not shown "
                    "enforcing end to end. Also open: a Cursor coordinator seat, which has "
                    "not been exercised, and what the lane's Kindex returns for a seeded "
                    "search.",
    # The lane is Muse's own interactive session with the operator's configuration. What is
    # open is acceptance and what an echo run cannot show, not conversion.
    "muse": "Muse: hosted as its own interactive session from an interface observed on Muse "
            "Code 1.4.20 with its echo provider (2026-11-13, by the adapter's author and "
            "independently by the tester): start, a first message given at launch, typed "
            "follow-up messages, turn-end reports, lane hooks beside the operator's, a "
            "per-run lane server beside the operator's. Not yet accepted, and three things "
            "are open. First, everything a signed-in model run would show and echo cannot: "
            "a model calling the lane's Kindex and a model's own tool call. There is no "
            "Signet guard, and a launch that requires one is refused, for a reason that was "
            "observed (1.4.20, echo, a user shell action, 2026-11-13): PreToolUse does fire, "
            "also under --yolo, and an explicit deny or exit 2 blocks the tool; but the tool "
            "runs when the hook exits 1, prints something that is not JSON, times out or "
            "cannot be started. No supported fail-closed hook setting has been qualified; "
            "tested failClosed/required entries were disabled. This tested hook-only "
            "configuration cannot provide fail-closed enforcement across those failures. "
            "Second, unattended resume: a resumed "
            "session reports nothing until a prompt is typed, and Muse refuses a prompt "
            "beside `resume`, so no lane command restarts an ended session and wrap-up and "
            "review pauses are not offered. Third, no report of Muse asking the operator a "
            "question was observed, so a lane waiting on one is not shown as such. Fourth, "
            "and the largest: later messages are not typed into a Muse session. The "
            "independent tester showed (ef8fdd7, /tmp/factory-muse-ef/RESULT.md) that a "
            "message typed after Muse's stop report, while an operator Stop hook was still "
            "deciding, was taken into the continued earlier turn with no prompt report of "
            "its own. Muse reports no point at which typed input is safe, so a later "
            "message is kept `queued` with the reason and left for a person to enter in "
            "the lane's window: a review, an answer or a wrap-up notice does not reach a "
            "Muse lane unattended. When a person enters exactly that message followed by "
            "its own mark (the lane writes that one entry to a file) and Muse's own prompt "
            "report says so, it is recorded as received and later messages are accepted. "
            "The words without the mark settle nothing, because a report of the same words "
            "cannot be told from an earlier one repeated (run against Muse Code 1.4.20 "
            "with its echo provider, 2026-11-14, with two messages of the same words: the "
            "words alone, the first message's entry repeated, and a mark that was nobody's "
            "all left the second held; its own entry settled it).",
    "prime-agent": "Prime: not installed on the machine this was assessed on; its interactive "
                   "program and any report or MCP interface are unassessed.",
    # The registry entry `codex` is `codex exec`. The only lane agent still on it is
    # `codex-ollama`; the lane agent `codex` is the interactive adapter (LANE_VARIANTS).
    "codex": "Codex on a local Ollama model (`codex-ollama`): this lane runs `codex exec "
             "--oss`, the non-interactive mode, not the interactive program. It is not "
             "converted because Codex's interactive program on a local model has not been "
             "run: nothing is known of its hooks, its queue or its resume there. The lane "
             "agent `codex` does not run `codex exec`: it is Codex's own interactive "
             "session, hosted (the adapter also named `codex-interactive`)." + _TO_SHOW,
    # The lane agent `codex-interactive`: Codex's own interactive session, hosted. What is
    # open is stated, not hidden.
    "codex-interactive": "Codex interactive: hosted as its own interactive session from an "
                         "interface observed on codex-cli 0.167.0 on the operator's ChatGPT "
                         "sign-in (2026-11-13, by the adapter's author, three one-word "
                         "turns): the first message given at launch was reported by "
                         "Codex's prompt hook, a message given with `codex queue` between "
                         "turns was reported the same way with nothing typed, and each "
                         "stop report carried the reply. Not accepted. Open. First, Codex "
                         "skips a hook it has not been told to trust: without that trust "
                         "(given by the operator through Codex's own review, or for one "
                         "run by the caller's explicit permissive choice) the lane reports "
                         "nothing and stays `starting`. Second, a message queued while "
                         "the model is in the middle of a turn was not observed; it stays "
                         "`submitted` until Codex reports it. (One queued while a Stop hook "
                         "was deciding was reported as its own prompt when the hook "
                         "returned: 2026-11-14, one observation.) Third, restarting an "
                         "ended session is built and only partly shown. Observed of Codex "
                         "itself (2026-11-14, four one-word turns): `codex resume "
                         "<options> <id>` keeps the session id; given nothing it reports "
                         "nothing and stopped on Codex's own update question; given a "
                         "message after `--` it reported its start and that message at "
                         "once. So a lane message to an ended session restarts it that "
                         "way, the message recorded as handed over before the program "
                         "exists and never handed over twice. That path was run through "
                         "tmux_lane.sh and tmux_lane_message.sh with a stand-in program "
                         "that calls no model, and once against Codex itself (2026-11-14, "
                         "codex-cli 0.167.0, a synthetic folder Codex already trusted, "
                         "FACTORY_LANE_SANDBOX=off): the lane read its inputs with shell "
                         "commands and answered; after a clean end a lane message "
                         "restarted the same session id and was reported once; two "
                         "follow-ups went through the queue and were each reported once. "
                         "Not run: a restart after a crash, a watcher's pause or wrap-up "
                         "of a Codex session, or a second machine. A restarted program "
                         "that ends before reporting leaves that one message `unknown`, "
                         "and another message is needed to restart the session. Fourth, "
                         "the model ran shell commands and called the lane's Kindex in "
                         "that one lane, but no tool hook of the Factory's and no Signet "
                         "guard was in place, so nothing about either is established.",
}
#: Lane agents that are answered for by another registry entry. The lane agent `codex`, the
#: default author lane, is the hosted interactive adapter `codex-interactive`: the launcher
#: runs that and records that name. `codex-ollama` is the registry entry `codex`, which is
#: `codex exec`, and is the only lane agent still on it.
#: Each maps to (registry entry, blocker or None for the entry's own, resumable by the Factory).
LANE_VARIANTS: Mapping[str, tuple[str, str | None, bool]] = {
    "codex": ("codex-interactive", None, True),
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
    # `codex exec`, with the operator's whole configuration. Only the lane agent `codex-ollama`
    # still runs on this entry; the lane agent `codex` is `codex-interactive` (LANE_VARIANTS).
    "codex": Codex("codex", "codex", ("CODEX_HOME",), True, api_env=("OPENAI_API_KEY",),
                   memory="beside-operator", native_blocker=_BLOCKERS["codex"]),
    # Not resumable by the Factory yet, for the reasons its blocker gives. Antigravity's own
    # resume is untouched: `turn(resume=<id>)` builds `--conversation <id>`.
    # Resumable by the Factory: a lane message to an ended session restarts it with Codex's
    # own resume and that message as its launch argument (tmux_lane_message.sh). What of that
    # was run against Codex itself, and what only against a stand-in, is in its blocker.
    "codex-interactive": CodexInteractive(
        "codex-interactive", "codex", ("CODEX_HOME",), True, api_env=("OPENAI_API_KEY",),
        memory="beside-operator", dialect="codex",
        native_blocker=_BLOCKERS["codex-interactive"],
        start_requires=(
            "Codex asks its own folder-trust question in the lane's window for a clone it "
            "has not been told to trust, and says it saves the answer; the Factory does not "
            "answer it. Codex also skips this lane's report hooks (the clone's "
            ".codex/hooks.json) until they are trusted: review them with Codex's /hooks in "
            "the lane's window, or launch with FACTORY_LANE_SANDBOX=off, which is the "
            "explicit per-run choice that passes Codex's own "
            "--dangerously-bypass-hook-trust beside its approval and sandbox bypass. "
            "Without one of those the lane works but reports nothing and stays `starting`.")),
    "agy": Antigravity(
        "agy", "agy", (), False,
        api_env=("GEMINI_API_KEY", "GOOGLE_API_KEY", "AGY_LLM_GATEWAY_API_KEY"),
        memory="beside-operator", dialect="agy", native_blocker=_BLOCKERS["agy"],
        withhold_followups=True,
        protocol_note=(
            "- Your Kindex is the MCP server this lane's own plugin provides (Antigravity "
            "lists it as\n  lane_kindex). Do not call the server named kindex: it is not "
            "this lane's memory. The\n  directory .agents/plugins/lane is this lane's "
            "runtime configuration, not the work: do not\n  edit it, and never add it to a "
            "commit (not with git add -f either).\n"),
        start_requires=(
            "Antigravity asks its own workspace-trust question in the lane's window for a "
            "clone it has not been told to trust, and the Factory does not answer it. The "
            "lane's first message is given to Antigravity at launch. Nothing is typed into "
            "an Antigravity session after that: a later message to the lane is kept queued, "
            "and you enter it in the lane's window yourself, exactly as its entry file "
            "gives it (the message followed by its mark); the lane records it as received "
            "when Antigravity reports that entry.")),
    # Not resumable by the Factory yet, for the reason its blocker gives. Muse's own resume is
    # untouched: `turn(resume=<id>)` builds `resume <id>`, the same session and never a new one.
    "muse": Muse(
        "muse", "muse", (), False, api_env=("META_API_KEY",),
        memory="beside-operator", dialect="muse", native_blocker=_BLOCKERS["muse"],
        withhold_followups=True,
        protocol_note=(
            f"- Your Kindex is the MCP server named {LANE_SERVER}. It is this lane's own. "
            "Do not call the\n  server named kindex: it is not this lane's memory. The file "
            ".muse/hooks.json is this\n  lane's runtime configuration, not the work: do not "
            "edit it, and never add it to a commit\n  (not with git add -f either).\n"),
        start_requires=(
            "Muse asks its own workspace-trust question in the lane's window for a clone it "
            "has not been told to trust, and the Factory does not answer it or pass "
            "--trust-workspace. The lane's first message is given to Muse at launch and is "
            "submitted when that question is answered. Nothing is typed into a Muse "
            "session after that: a later message to the lane is kept queued, and you enter "
            "it in the lane's window yourself, exactly as its entry file gives it (the "
            "message followed by its mark); the lane records it as received when Muse "
            "reports that entry.")),
    "prime-agent": PrimeAgent("prime-agent", "prime-agent", (), True,
                              api_env=("OPENROUTER_API_KEY",), plan=False,
                              native_blocker=_BLOCKERS["prime-agent"]),
    # Restartable, not resumable. A lane message to a session that has ended restarts it
    # with Cursor's own `--resume=<id>` and that message as its launch argument, because a
    # resumed session given nothing reports nothing (tmux_lane_message.sh,
    # native_session.cursor_plan); that was run against Cursor itself after a clean end.
    # The watcher ending a live Cursor session and continuing it (a review pause, a wrap-up)
    # was not run, so `resumable` stays false and no pause is offered. A message to a
    # running session is typed into it, as before.
    "cursor-agent": CursorAgent(
        "cursor-agent", "agent", (), False, api_env=("CURSOR_API_KEY",),
        restart_by_message=True,
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
    describe = sub.add_parser("describe", help="resumable | restartable | single, plan, "
                              "memory isolation, the "
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
            # resumable: the watcher may end and continue it, and a message restarts an
            # ended one. restartable: only the second. single: neither.
            word = ("resumable" if resumable
                    else "restartable" if chosen.restart_by_message else "single")
            sys.stdout.write(f"{word}\0"
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
