#!/usr/bin/env python3
"""Host a provider's own session server in a lane's terminal: Muse (MSP) and Antigravity (ACP).

The lane wrapper runs this program where it would run the provider's terminal UI. It starts
the provider's stdio server (``muse serve`` or ``agy acp --stdio``) on the operator's own
sign-in and settings, speaks the provider's published protocol to it, and reports to the lane
through the same event journal a terminal session's hooks write. So the wrapper's custody is
unchanged: the launch hand-over recorded before spawn, one unaccounted message at a time,
nothing ever given twice.

Invariants this program upholds:

- Identity. An existing session is re-entered with ``session/resume`` and the kept id only,
  and the answer must name that id. Anything else (an error, another id, no id, a timeout, the
  server ending) sends no prompt and ends this program with exit 70. ``session/load`` is
  never sent: Antigravity accepts an unknown id through it, and its own resume error
  suggests it. A new session is never made in place of a failed resume.
- Reports are of this session only. A frame settles something only if it names this
  session's id exactly; one that names another, or none, settles nothing.
- A message is reported received only on a report that is that submission's own: Muse's
  ``turn/started`` for the message's own command id; for Antigravity, whose updates name
  no prompt, the session's echo of the message carrying its mark, or else the answer to
  that very ``session/prompt`` request. Its end is a separate report with the provider's
  own terminal word, and a failed or cancelled turn is never called done.
- No decision is made for the operator. A permission or approval request is shown in the
  pane and held; a person answers it there, once, for that request only. No choice that
  lasts for a session or is saved to the operator's policy is ever sent, and nothing is
  approved because nobody answered.

What this is not: the central policy guard. Neither provider asks about every action
(existing grants and permissive modes are not asked about) and both skip a hook that fails,
so a launch that requires that guard or custody is refused before this program is started
(``docs/HARNESS.md``).
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import pathlib
import re
import secrets
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import native_session

SCHEMA = "factory-hosted-session/1"
#: The lane's dialect names, and the protocol each provider's server speaks.
PROTOCOL = {"muse": "msp", "agy": "acp"}
#: Variables that would bill a provider to an API key instead of the operator's sign-in.
#: None of them reaches either provider's server, whichever provider it is.
API_ENV = ("META_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "AGY_LLM_GATEWAY_API_KEY")
MUSE_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
#: Refused for a hosted Antigravity lane: print mode, a prompt or conversation of someone
#: else's choosing, and anything that would write a log of the operator's settings.
BANNED_AGY = ("-p", "--print", "--prompt", "--output-format", "--input-format",
              "--json-schema", "--print-timeout", "-i", "--prompt-interactive", "-c",
              "--continue", "--log-file", "--cli_log_file", "--login")
HANDSHAKE_S = float(os.environ.get("FACTORY_HOSTED_HANDSHAKE_S", "60"))
FRAME_LOG_LIMIT = 16 * 1024 * 1024
#: How much pane text may wait for the terminal before the oldest is dropped.
PANE_LIMIT = 1024 * 1024
UUID = re.compile(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


class Refused(Exception):
    """The session cannot be hosted as asked; nothing was prompted."""


class GuardRequired(ValueError):
    """A guard or custody is required of this start, and a hosted session has neither."""


#: What requires a guard or custody of a lane: a flag that is set, or a binding that is named.
GUARD_FLAGS = ("FACTORY_REQUIRE_SIGNET", "FACTORY_REQUIRE_AUTHOR_CUSTODY")
GUARD_BINDINGS = ("FACTORY_SIGNET_CONTRACT", "FACTORY_SIGNET_DIGEST",
                  "FACTORY_AUTHOR_CUSTODY_CONFIG", "FACTORY_AUTHOR_CUSTODY_DIGEST")


def refuse_guarded(environ: Mapping[str, str]) -> None:
    """Refuse a hosted start that a guard or custody is required of.

    This is the authority for that refusal: every way of starting a hosted session comes
    through ``plan`` or ``run``, and both call it before anything is spawned or recorded.
    The launcher's own refusal only says the same thing earlier. Fail closed: a flag set to
    anything but empty or ``0`` is a requirement.
    """

    named = [name for name in GUARD_FLAGS if environ.get(name, "") not in ("", "0")]
    named += [name for name in GUARD_BINDINGS if environ.get(name)]
    if named:
        raise GuardRequired(
            "a guard or author custody is required (" + ", ".join(named) + "), and neither is "
            "qualified for a hosted Muse or Antigravity session: no gate on every tool action "
            "and no read isolation is established for it. Nothing was started")


def uuid7() -> str:
    """A time-ordered id of the form Muse requires for its command and session ids."""

    stamp = int(time.time() * 1000) & ((1 << 48) - 1)
    value = (stamp << 80) | (secrets.randbits(80))
    value = (value & ~(0xF << 76)) | (0x7 << 76)
    value = (value & ~(0x3 << 62)) | (0x2 << 62)
    return str(uuid.UUID(int=value))


def _dict(value: object) -> dict:
    """A frame's member as an object, or an empty one: a frame is never trusted for shape."""

    return value if isinstance(value, dict) else {}


def _value(args: list[str], index: int, flag: str) -> str:
    if index + 1 >= len(args):
        raise ValueError(f"{flag} needs a value")
    return args[index + 1]


def translate(dialect: str, arguments: Sequence[str]) -> dict[str, Any]:
    """Read the registry's terminal-shaped arguments as settings of a hosted session.

    Only what a hosted session can honour is taken. Anything else is refused by name, so no
    option is dropped quietly and none is passed to a server that would read it otherwise.
    """

    args = list(arguments)
    found: dict[str, Any] = {"resume": "", "model": "", "effort": "", "permissive": False,
                             "server": [], "workspace": ""}
    index = 0
    if dialect == "muse":
        if args[:1] == ["resume"]:
            found["resume"] = _value(args, 0, "resume")
            index = 2
        while index < len(args):
            part = args[index]
            if part == "--workspace":
                found["workspace"] = _value(args, index, part)
            elif part == "--model":
                found["model"] = _value(args, index, part)
            elif part in ("--reasoning-effort", "--effort"):
                found["effort"] = _value(args, index, part)
                if found["effort"] not in MUSE_EFFORTS:
                    raise ValueError(f"Muse has no reasoning effort `{found['effort']}`")
            elif part == "--provider":
                found["server"] += [part, _value(args, index, part)]
            elif part in ("-c", "--config"):
                setting = _value(args, index, part)
                if re.match(r"\s*hooks\b", setting):
                    raise ValueError("a hosted Muse session refuses a per-run `hooks` "
                                     "setting: it replaces the operator's own hooks")
                found["server"] += ["-c", setting]
            elif part == "--yolo":
                # The operator's explicit permissive selection (FACTORY_LANE_SANDBOX=off),
                # as the launcher already passes it. Never a default.
                found["permissive"] = True
                index += 1
                continue
            else:
                raise ValueError(f"a hosted Muse session does not take `{part}`")
            index += 2
        found["server"] = ["serve", *(["--model", found["model"]] if found["model"] else []),
                           *found["server"]]
    else:
        while index < len(args):
            part = args[index]
            if native_session._flag(part) in BANNED_AGY:
                raise ValueError(f"a hosted Antigravity session refuses `{part}`")
            if part == "--model":
                found["model"] = _value(args, index, part)
            elif part == "--effort":
                found["effort"] = _value(args, index, part)
            elif part == "--conversation":
                found["resume"] = _value(args, index, part)
            elif part == "--dangerously-skip-permissions":
                found["permissive"] = True  # the operator's explicit selection, as above
                index += 1
                continue
            else:
                raise ValueError(f"a hosted Antigravity session does not take `{part}`")
            index += 2
        found["server"] = ["acp", "--stdio",
                           *(["--dangerously-skip-permissions"] if found["permissive"] else [])]
    if found["resume"] and not UUID.fullmatch(found["resume"]):
        raise ValueError("the session id to resume is missing or malformed")
    return found


def record_path(directory: pathlib.Path, slot: str) -> pathlib.Path:
    return directory / f"{slot}-hosted.json"


def plan(arguments: Sequence[str], *, dialect: str, executable: str,
         directory: pathlib.Path, slot: str, workspace: pathlib.Path, prompt: str,
         python: str = sys.executable, message_id: str = "",
         api_env: Sequence[str] = (),
         environ: Mapping[str, str] | None = None) -> native_session.Plan:
    """A start of a hosted session, as the lane wrapper's terminal driver takes any start.

    The program the wrapper runs is this file; the provider's server is its child. The
    first message, or the message that restarts an ended session (``message_id``), is this
    program's launch argument, marked as a terminal session's would be, and the plan says
    so in ``launched`` so the wrapper records the hand-over before anything is spawned.
    Nothing is written into the lane's repository.
    """

    refuse_guarded(os.environ if environ is None else environ)
    settings = translate(dialect, arguments)
    resumed = bool(settings["resume"])
    if resumed and not message_id:
        raise ValueError("a hosted session is restarted only by the message that restarts "
                         "it; a resumed start with nothing to give is refused")
    if message_id and (not resumed or not native_session.MESSAGE_ID.fullmatch(message_id)):
        raise ValueError(
            "a launch message id names the message that restarts a resumed session, and "
            "nothing else: give the kept session as the provider's own resume arguments "
            "after the executable (`resume <id>` for Muse, `--conversation <id>` for "
            "Antigravity), as the registry builds them")
    if "\0" in prompt:
        raise ValueError("the message holds a NUL byte and cannot be a launch argument")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    journal = directory / f"{slot}-native-events.jsonl"
    if journal.is_symlink() or record_path(directory, slot).is_symlink():
        raise ValueError("symlinked hosted-session file")
    try:
        offset = journal.stat().st_size
    except FileNotFoundError:
        offset = 0
    if resumed:
        mark = native_session.message_token(settings["resume"], message_id)
    else:
        mark = native_session.message_token(uuid.uuid4().hex, "initial")
    given = native_session.marked(prompt, mark)
    launched = {"token": mark, "typed_sha256": native_session.text_digest(given),
                "body_sha256": native_session.text_digest(prompt),
                **({"message_id": message_id} if message_id else {})}
    protocol = PROTOCOL[dialect]
    command = [python, str(pathlib.Path(__file__).resolve()), "run",
               "--protocol", protocol, "--journal", str(journal),
               "--record", str(record_path(directory, slot)),
               "--workspace", str(pathlib.Path(settings["workspace"] or workspace).resolve())]
    for name in sorted({*API_ENV, *api_env}):
        command += ["--unset", name]
    if resumed:
        command += ["--resume", settings["resume"]]
    if settings["effort"]:
        command += ["--effort", settings["effort"]]
    if settings["model"] and protocol == "acp":
        command += ["--model", settings["model"]]  # Muse's server takes it as its own option
    if settings["permissive"]:
        command += ["--permissive"]
    servers = directory / "agy-mcp.json"
    if protocol == "acp" and servers.exists():
        command += ["--mcp-file", str(servers)]
    command += ["--message", given, "--", executable, *settings["server"]]
    name = pathlib.Path(executable).name
    return native_session.Plan(
        tuple(command), settings["resume"], resumed, journal,
        native_session.Launcher(name, "executable", path=executable), offset, None, launched)


def queue(python: str, record: pathlib.Path, session: str, text: str) -> list[str]:
    """The command that gives a later message to the running host; nothing is typed."""

    return [python, str(pathlib.Path(__file__).resolve()), "send", "--record", str(record),
            "--session", session, "--message", text]


def _store(path: pathlib.Path, value: Mapping[str, Any]) -> None:
    handle, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    except BaseException:
        pathlib.Path(name).unlink(missing_ok=True)
        raise


def _started(pid: int) -> str:
    import lane_watchdog

    snapshot = lane_watchdog.process_snapshot()
    entry = (snapshot or {}).get(pid)
    return str(entry.get("started") or "") if isinstance(entry, dict) else ""


class Host:
    """One provider server and the one session this lane has in it."""

    def __init__(self, args: argparse.Namespace, provider: list[str]) -> None:
        self.args = args
        self.protocol = args.protocol
        self.journal = args.journal
        self.record = args.record
        self.workspace = str(args.workspace)
        self.session = ""
        self.buffer = b""
        self.next_id = 0
        self.answers: dict[int, dict] = {}
        self.frames_logged = 0
        self.frame_log = self.record.with_name(self.record.name[:-5] + "-frames.jsonl")
        self.commands = self.record.with_name(self.record.name[:-5] + "-commands.json")
        # Muse: turn id -> the message given under it. Antigravity: the one prompt in flight.
        self.turns: dict[str, dict] = {}
        self.flight: dict | None = None
        self.backlog: collections.deque[str] = collections.deque()
        self.items: dict[str, str] = {}  # Muse view item id -> kind
        self.streamed: set[str] = set()  # Muse view items whose text was shown as it came
        self.asking: dict | None = None
        self.foreign = 0
        self.ended = False
        self.pane = bytearray()
        os.set_blocking(sys.stdout.fileno(), False)
        environment = {key: value for key, value in os.environ.items()
                       if key not in set(args.unset)}
        self.child = subprocess.Popen(provider, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, env=environment,
                                      cwd=self.workspace)

    # -- the pane and the retained record ------------------------------------------------

    def show(self, text: str) -> None:
        """Queue text for the pane. The pane is a courtesy and never holds the session up:
        the wrapper that drains this terminal is at times waiting on this very program (while
        it hands a message over), so a write that waited for the terminal would wait for
        itself. What does not fit is dropped from the front; the lane's record is elsewhere."""

        self.pane.extend(text.encode("utf-8", "replace"))
        if len(self.pane) > PANE_LIMIT:
            del self.pane[:len(self.pane) - PANE_LIMIT]
        self.drain()

    def drain(self) -> None:
        while self.pane:
            try:
                written = os.write(sys.stdout.fileno(), bytes(self.pane[:65536]))
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                self.pane.clear()  # no terminal any more; nothing to show it to
                return
            del self.pane[:written]

    def say(self, text: str) -> None:
        self.show(text if text.endswith("\n") else text + "\n")

    def keep(self, direction: str, frame: object) -> None:
        if self.frames_logged >= FRAME_LOG_LIMIT:
            return
        line = json.dumps({"at": round(time.time(), 3), "dir": direction,
                           "frame": json.dumps(frame)[:16384]}) + "\n"
        self.frames_logged += len(line)
        descriptor = os.open(self.frame_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND
                             | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.write(descriptor, line.encode())
        finally:
            os.close(descriptor)

    def report(self, event: str, **fields: object) -> None:
        native_session.record_hook(self.journal, {
            "hook_event_name": event, "session_id": self.session, **fields}, "hosted")

    # -- the wire ------------------------------------------------------------------------

    def write(self, frame: dict) -> None:
        self.keep("out", frame)
        assert self.child.stdin is not None
        self.child.stdin.write((json.dumps(frame) + "\n").encode())
        self.child.stdin.flush()

    def request(self, method: str, params: dict) -> int:
        self.next_id += 1
        self.write({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params})
        return self.next_id

    def pump(self, wait: float) -> None:
        """Read what the server has written, for at most ``wait`` seconds, and act on it."""

        assert self.child.stdout is not None and self.child.stderr is not None
        readable, _, _ = select.select([self.child.stdout, self.child.stderr], [], [], wait)
        if self.child.stderr in readable:
            said = os.read(self.child.stderr.fileno(), 65536)
            if said:
                self.keep("err", said.decode("utf-8", "replace")[:4000])
        if self.child.stdout in readable:
            chunk = os.read(self.child.stdout.fileno(), 1 << 20)
            if not chunk:
                self.ended = True
                return
            self.buffer += chunk
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    frame = json.loads(line)
                except ValueError:
                    self.keep("in-unparsed", line.decode("utf-8", "replace")[:2000])
                    continue
                if isinstance(frame, dict):
                    self.keep("in", frame)
                    self.take(frame)

    def call(self, method: str, params: dict, wait: float = HANDSHAKE_S) -> dict:
        """One request and its answer. An error, a timeout or the server ending raises."""

        identifier = self.request(method, params)
        deadline = time.monotonic() + wait
        while identifier not in self.answers:
            if self.ended or self.child.poll() is not None:
                raise Refused(f"the server ended before answering {method}")
            if time.monotonic() >= deadline:
                raise Refused(f"the server did not answer {method} within {wait:.0f}s")
            self.pump(0.2)
        answer = self.answers.pop(identifier)
        if "error" in answer or not isinstance(answer.get("result"), dict):
            error = _dict(answer.get("error"))
            raise Refused(f"{method} was refused by the server: "
                          f"{error.get('code')} {str(error.get('message'))[:300]}")
        return answer["result"]

    def take(self, frame: dict) -> None:
        if "method" not in frame:
            if isinstance(frame.get("id"), int):
                if self.flight is not None and frame["id"] == self.flight["request"]:
                    self.prompt_answered(frame)
                else:
                    self.answers[frame["id"]] = frame
            return
        method, params = frame["method"], frame.get("params")
        params = params if isinstance(params, dict) else {}
        if params.get("sessionId") != self.session or not self.session:
            # Only a frame that names this lane's session, exactly, is this session's. One
            # that names another, names none, or names it as anything but that string
            # settles nothing here. A request among them is not granted: it gets the
            # protocol's own word for "no decision".
            self.foreign += 1
            if "id" in frame:
                self.write({"jsonrpc": "2.0", "id": frame["id"],
                            **({"result": {"outcome": {"outcome": "cancelled"}}}
                               if method == "session/request_permission" else
                               {"error": {"code": -32601, "message": "not this session"}})})
            return
        if self.protocol == "msp":
            self.take_msp(frame, method, params)
        else:
            self.take_acp(frame, method, params)

    # -- Muse (MSP) ----------------------------------------------------------------------

    def take_msp(self, frame: dict, method: str, params: dict) -> None:
        if method == "turn/started":
            # This message's own turn: the command id given with it, or the turn id Muse's
            # acknowledgement of it named. Nothing else is taken for its start.
            named = params.get("commandId")
            turn = self.turns.get(named) if isinstance(named, str) else None
            if turn is None and isinstance(params.get("turnId"), str):
                turn = next((t for t in self.turns.values()
                             if t.get("turn") == params["turnId"]), None)
            if turn is not None and not turn["observed"]:
                turn["observed"] = True
                turn["turn"] = str(params.get("turnId"))
                self.turns[turn["turn"]] = turn
                self.report("UserPromptSubmit", prompt=turn["text"], turn_id=turn["turn"])
                self.say(f"\n[turn started] {turn['text'].splitlines()[0][:120]}")
        elif method == "item/started":
            item = _dict(params.get("item"))
            self.items[str(item.get("itemId"))] = str(item.get("kind"))
        elif method == "item/delta":
            if (self.items.get(str(params.get("itemId"))) == "agentMessage"
                    and params.get("field") == "text" and isinstance(params.get("delta"), str)):
                self.streamed.add(str(params.get("itemId")))
                self.show(params["delta"])
        elif method == "item/completed":
            item = _dict(params.get("item"))
            turn = self.turns.get(str(item.get("turnId")))
            if item.get("kind") == "agentMessage" and turn is not None \
                    and isinstance(item.get("text"), str) and item["text"].strip():
                turn["reply"] = item["text"]
                if str(item.get("itemId")) not in self.streamed:
                    # Its text never came as it was written, only now, whole: show it now.
                    self.say("\n" + item["text"])
            elif item.get("kind") not in ("agentMessage", "userMessage", None):
                self.say(f"\n[{item.get('kind')}] {str(item.get('status') or '')}")
        elif method == "turn/completed":
            turn = self.turns.get(str(params.get("turnId")))
            if turn is not None and not turn.get("ended"):
                turn["ended"] = True
                terminal = str(params.get("terminal") or "unknown")
                self.report("Stop", turn_id=turn.get("turn") or str(params.get("turnId")),
                            last_assistant_message=turn.get("reply", ""), status=terminal)
                self.say(f"\n[turn {terminal}]")
        elif method == "approval/request" and "id" in frame:
            # The answer to the request is only that it was shown; the decision is separate.
            self.write({"jsonrpc": "2.0", "id": frame["id"], "result": {}})
            self.ask({"kind": "approval", "id": str(params.get("approvalId")),
                      "requirement": params.get("currentRequirementId"),
                      "choices": params.get("availableChoices"),
                      "what": f"{_dict(params.get('subject')).get('kind')} "
                              f"{params.get('toolName')}: {str(params.get('rawArgs'))[:400]}"})
        elif method == "approval/resolved":
            if self.asking and self.asking["id"] == str(params.get("approvalId")):
                self.asking = None
                self.say("[approval settled]")
        elif "id" in frame:
            # A request this client does not serve is said to be unserved, never satisfied.
            self.write({"jsonrpc": "2.0", "id": frame["id"],
                        "error": {"code": -32601, "message": "not served by this client"}})

    def known_commands(self) -> dict[str, str]:
        """The command ids already given out, by message. Absent means none yet. A record
        that is there and cannot be trusted is refused: with it lost, a message already
        given could be given again under a fresh id, which is the one thing it prevents."""

        if self.commands.is_symlink():
            raise Refused("the retained command-id record is a symlink; nothing was sent")
        try:
            raw = self.commands.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise Refused(f"the retained command-id record cannot be read ({error}); "
                          "nothing was sent") from None
        try:
            known = json.loads(raw)
        except ValueError:
            known = None
        if not isinstance(known, dict) or not all(
                isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key)
                and isinstance(value, str) and UUID.fullmatch(value)
                for key, value in known.items()):
            raise Refused("the retained command-id record is damaged; nothing was sent, and "
                          "no new command id is made in its place")
        return known

    def give_msp(self, text: str) -> dict:
        """Give one message as a turn. The command id is the message's own and is on disk
        before it is sent, so the same message is never given under a second id."""

        key = hashlib.sha256(text.encode()).hexdigest()
        known = self.known_commands()
        command = known.get(key) or uuid7()
        if key not in known:
            known[key] = command
            _store(self.commands, known)
        self.turns.setdefault(command, {"text": text, "observed": False})
        result = self.call("turn/start", {
            "sessionId": self.session, "commandId": command, "ifBusy": "queue",
            "input": [{"type": "text", "text": text}],
            **({"reasoningEffort": self.args.effort} if self.args.effort else {})}, 30)
        if result.get("status") != "accepted" or not isinstance(result.get("turnId"), str):
            raise Refused(f"turn/start was not accepted: {json.dumps(result)[:200]}")
        self.turns[command].setdefault("turn", result["turnId"])
        self.turns.setdefault(result["turnId"], self.turns[command])
        return result

    # -- Antigravity (ACP) ---------------------------------------------------------------

    def take_acp(self, frame: dict, method: str, params: dict) -> None:
        if method == "session/update":
            update = _dict(params.get("update"))
            if self.flight is not None:
                content = _dict(update.get("content"))
                # An update carries no word of which prompt it belongs to, so none of them
                # is this message's receipt by merely arriving: a late frame of the turn
                # before, a mode or option notice, anything. The one update that is this
                # submission's own is the session's echo of the user's message carrying
                # this message's mark. Otherwise the receipt is the answer to this very
                # request (``prompt_answered``).
                if update.get("sessionUpdate") == "user_message_chunk" \
                        and isinstance(content.get("text"), str) \
                        and self.flight["mark"] in native_session.TOKEN.findall(content["text"]):
                    self.prompt_seen()
                if update.get("sessionUpdate") == "agent_message_chunk" \
                        and isinstance(content.get("text"), str):
                    self.flight["reply"].append(content["text"])
                    self.show(content["text"])
                elif update.get("sessionUpdate") in ("tool_call", "tool_call_update"):
                    self.say(f"\n[{update.get('sessionUpdate')}] "
                             f"{str(update.get('title') or update.get('toolCallId') or '')[:120]}"
                             f" {str(update.get('status') or '')}")
        elif method == "session/request_permission" and "id" in frame:
            call = _dict(params.get("toolCall"))
            self.ask({"kind": "permission", "id": frame["id"],
                      "choices": params.get("options"),
                      "what": f"{call.get('kind') or 'tool'} "
                              f"{str(call.get('title') or call.get('toolCallId') or '')[:400]}"})
        elif "id" in frame:
            self.write({"jsonrpc": "2.0", "id": frame["id"],
                        "error": {"code": -32601, "message": "not served by this client"}})

    def prompt_seen(self) -> None:
        if self.flight is not None and not self.flight["observed"]:
            self.flight["observed"] = True
            self.report("UserPromptSubmit", prompt=self.flight["text"],
                        turn_id=self.flight["turn"])

    def prompt_answered(self, frame: dict) -> None:
        flight = self.flight
        assert flight is not None
        result = frame.get("result") if isinstance(frame.get("result"), dict) else None
        if result is None or not isinstance(result.get("stopReason"), str):
            if not flight["observed"]:
                # Refused or answered in a shape that is not an answer: not a receipt.
                self.flight = None
                raise Refused("session/prompt was not accepted: "
                              + json.dumps(frame.get("error") or frame.get("result"))[:300])
            status = "error"
        else:
            self.prompt_seen()  # answered without any update: the answer is the receipt
            status = result["stopReason"]
        self.flight = None
        self.report("Stop", turn_id=flight["turn"],
                    last_assistant_message="".join(flight["reply"]), status=status)
        self.say(f"\n[turn {status}]")
        self.next_acp()

    def next_acp(self) -> None:
        """Send the next message, only when no prompt is in flight and its answer is in."""

        if self.flight is None and self.backlog:
            text = self.backlog.popleft()
            self.say(f"\n[prompt] {text.splitlines()[0][:120]}")
            marks = native_session.TOKEN.findall(text)
            self.flight = {"text": text, "turn": str(uuid.uuid4()), "observed": False,
                           "mark": marks[-1] if marks else "",
                           "reply": [], "request": self.request("session/prompt", {
                               "sessionId": self.session,
                               "prompt": [{"type": "text", "text": text}]})}

    # -- a question to the operator ------------------------------------------------------

    def ask(self, question: dict) -> None:
        self.asking = question
        self.report("Notification", notification_type="permission_prompt")
        self.say(f"\n[asking the operator] {question['what']}\n"
                 "  press a to allow this once, d to deny it. Nothing is decided for you, "
                 "and no choice that lasts beyond this request is offered here.")

    def decide(self, key: str) -> None:
        question = self.asking
        if question is None or key not in ("a", "d"):
            return
        choices = [c for c in (question.get("choices") or []) if isinstance(c, dict)]
        if question["kind"] == "permission":
            wanted = "allow_once" if key == "a" else "reject_once"
            chosen = next((c for c in choices if c.get("kind") == wanted), None)
            if chosen is None and key == "a":
                self.say("  this request offers no allow-once choice; d denies it")
                return
            self.asking = None
            self.write({"jsonrpc": "2.0", "id": question["id"], "result": {"outcome": (
                {"outcome": "selected", "optionId": chosen.get("optionId")}
                if chosen is not None else {"outcome": "cancelled"})}})
        else:
            wanted = "approved" if key == "a" else "denied"
            chosen = next((c for c in choices if c.get("decision") == wanted
                           and c.get("scope") == "once"), None)
            if chosen is None:
                self.say(f"  this request offers no once-only `{wanted}` choice")
                return
            self.request("approval/decide", {
                "sessionId": self.session, "approvalId": question["id"],
                "requirementId": question["requirement"], "choiceId": chosen.get("choiceId"),
                "commandId": uuid7()})
        self.say("  allowed once" if key == "a" else "  denied")

    # -- the session ---------------------------------------------------------------------

    def open(self) -> None:
        """Handshake, then the one session: a new one, or the kept one and no other."""

        resume = self.args.resume
        if self.protocol == "msp":
            self.call("initialize", {"clientInfo": {"name": "factory_lane", "version": "1"},
                                     "capabilities": {"userInputDialogs": False}})
            self.write({"jsonrpc": "2.0", "method": "initialized"})
            if resume:
                result = self.call("session/resume", {
                    "sessionId": resume, "commandId": uuid7(), "excludeItems": True})
            else:
                result = self.call("session/start", {
                    "sessionId": uuid7(), "commandId": uuid7(),
                    "workspaceRoot": self.workspace,
                    **({"approvalMode": "allowAll"} if self.args.permissive else {})})
            session = _dict(result.get("session"))
            named = session.get("sessionId")
        else:
            opened = self.call("initialize", {
                "protocolVersion": 1, "clientCapabilities": {},
                "clientInfo": {"name": "factory-lane", "version": "1"}})
            servers = []
            if self.args.mcp_file is not None:
                found = json.loads(self.args.mcp_file.read_text(encoding="utf-8"))
                for name, entry in (found.get("mcpServers") or {}).items():
                    servers.append({
                        "name": "lane-kindex" if name == "kindex" else name,
                        "command": entry["command"], "args": list(entry.get("args") or []),
                        "env": [{"name": key, "value": value}
                                for key, value in (entry.get("env") or {}).items()]})
            if resume:
                offered = _dict(opened.get("agentCapabilities"))
                if "resume" not in _dict(offered.get("sessionCapabilities")):
                    raise Refused("this server does not offer session/resume; an existing "
                                  "session is re-entered no other way")
                result = self.call("session/resume", {
                    "sessionId": resume, "cwd": self.workspace, "mcpServers": servers})
            else:
                result = self.call("session/new", {"cwd": self.workspace,
                                                   "mcpServers": servers})
            named = result.get("sessionId")
        if not isinstance(named, str) or not UUID.fullmatch(named):
            raise Refused("the server named no session")
        if resume and named != resume:
            raise Refused(f"asked to resume {resume} and the server answered for {named}; "
                          "nothing was prompted")
        self.session = named
        if self.protocol == "acp":
            self.choose(result)
        elif resume and self.args.permissive:
            self.call("session/setApprovalMode", {
                "sessionId": named, "commandId": uuid7(), "mode": "allowAll"})

    def choose(self, opened: dict) -> None:
        """Antigravity: put the lane's stated model and effort in force, or refuse. A model
        that was asked for and is not the one in force is never run under its name."""

        options = {str(o.get("id")): o for o in (opened.get("configOptions") or [])
                   if isinstance(o, dict)}

        def offered(name: str) -> list[str]:
            return [str(v.get("value")) for v in (options.get(name, {}).get("options") or [])
                    if isinstance(v, dict)]

        def now(name: str) -> str:
            return str(options.get(name, {}).get("currentValue") or "")

        wanted: dict[str, str] = {}
        model, effort = self.args.model, self.args.effort
        if model and model != now("model") and model != f"{now('model')}-{now('thought_level')}":
            if model in offered("model"):
                wanted["model"] = model
            else:
                pairs = [(m, t) for m in offered("model") for t in offered("thought_level")
                         if model == f"{m}-{t}"]
                if len(pairs) != 1 or (effort and effort != pairs[0][1]):
                    raise Refused(f"this server does not offer the model `{model}` "
                                  f"(offered: {', '.join(offered('model')) or 'none named'})")
                wanted["model"], wanted["thought_level"] = pairs[0]
        if effort and wanted.get("thought_level", now("thought_level")) != effort:
            if effort not in offered("thought_level"):
                raise Refused(f"this server does not offer the effort `{effort}` "
                              f"(offered: {', '.join(offered('thought_level')) or 'none named'})")
            wanted["thought_level"] = effort
        for name, value in wanted.items():
            answer = self.call("session/set_config_option", {
                "sessionId": self.session, "configId": name, "value": value})
            # The server's own word that it is now in force. An answer that does not say so
            # (no options returned, or another value) is not taken for one.
            current = {str(o.get("id")): o.get("currentValue")
                       for o in (answer.get("configOptions") or []) if isinstance(o, dict)}
            if current.get(name) != value:
                raise Refused(f"asked for {name} `{value}` and the server's answer does not "
                              f"show it in force (it shows `{current.get(name)}`); nothing "
                              "was prompted")

    def serve(self) -> int:
        self.open()
        self.report("SessionStart", source="resume" if self.args.resume else "startup")
        secret = secrets.token_hex(24)
        directory = tempfile.mkdtemp(prefix="factory-hosted-", dir="/tmp")
        address = directory + "/h.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(address)
            server.listen(4)
            _store(self.record, {
                "schema": SCHEMA, "protocol": self.protocol, "session_id": self.session,
                "pid": os.getpid(), "started": _started(os.getpid()), "socket": address,
                "secret": secret})
            self.say(f"[hosted {self.protocol} session {self.session}] "
                     + ("resumed" if self.args.resume else "new"))
            self.give(self.args.message)
            try:
                return self.loop(server, secret)
            finally:
                pathlib.Path(address).unlink(missing_ok=True)
                try:
                    os.rmdir(directory)
                except OSError:
                    pass

    def give(self, text: str) -> str:
        if self.protocol == "msp":
            result = self.give_msp(text)
            return (f"accepted ({result.get('disposition')}) as turn {result.get('turnId')} "
                    f"for session {self.session}")
        self.backlog.append(text)
        self.next_acp()
        return f"taken for session {self.session}"

    def loop(self, server: socket.socket, secret: str) -> int:
        keys = sys.stdin.fileno() if sys.stdin.isatty() else None
        while not self.ended and self.child.poll() is None:
            watched = [server.fileno(), *([keys] if keys is not None else [])]
            self.drain()
            readable, _, _ = select.select(watched, [], [], 0)
            if keys in readable:
                try:
                    typed = os.read(keys, 64).decode("utf-8", "replace")
                except (BlockingIOError, InterruptedError):
                    typed = ""
                for key in typed.lower():
                    self.decide(key)
            if server.fileno() in readable:
                with server.accept()[0] as connection:
                    connection.settimeout(5)
                    try:
                        raw = bytearray()
                        while not raw.endswith(b"\n"):
                            part = connection.recv(65536)
                            if not part or len(raw) > 8 * 1024 * 1024:
                                raise ValueError("incomplete request")
                            raw.extend(part)
                        asked = json.loads(raw)
                        if asked.get("secret") != secret or asked.get("session") != self.session:
                            raise ValueError("not this host's session")
                        if not isinstance(asked.get("text"), str) or not asked["text"]:
                            raise ValueError("no message")
                        answer = {"ok": True, "said": self.give(asked["text"])}
                    except (OSError, ValueError, Refused) as error:
                        answer = {"ok": False, "said": str(error)[:300]}
                    try:
                        connection.sendall(json.dumps(answer).encode() + b"\n")
                    except OSError:
                        pass
            self.pump(0.2)
        self.report("SessionEnd", reason="the provider's server ended")
        return self.child.wait()


def _end(child: subprocess.Popen) -> None:
    """End the provider's server: its input closed first, which is its own clean ending."""

    if child.poll() is not None:
        return
    try:
        assert child.stdin is not None
        child.stdin.close()
        child.wait(10)
    except (OSError, subprocess.TimeoutExpired):
        child.send_signal(signal.SIGTERM)
        try:
            child.wait(5)
        except subprocess.TimeoutExpired:
            child.send_signal(signal.SIGKILL)


def run(args: argparse.Namespace, provider: list[str]) -> int:
    try:
        refuse_guarded(os.environ)
    except GuardRequired as error:
        print(f"hosted-session: refused: {error}", flush=True)
        return 77
    host = None
    restore = None
    if sys.stdin.isatty():
        import termios
        import tty

        restore = termios.tcgetattr(sys.stdin.fileno())
        tty.setcbreak(sys.stdin.fileno())

    def stop(signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, stop)
    try:
        host = Host(args, provider)
        return host.serve()
    except Refused as error:
        # Before any session was reported, this is the whole of what the lane will know.
        os.set_blocking(sys.stdout.fileno(), True)
        print(f"hosted-session: refused: {error}", flush=True)
        try:
            _store(args.record.with_name(args.record.name[:-5] + "-refusal.json"),
                   {"schema": SCHEMA, "at": time.time(), "refused": str(error),
                    "resume": args.resume})
        except OSError:
            pass
        return 70
    except KeyboardInterrupt:
        return 143
    finally:
        if restore is not None:
            import termios

            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, restore)
        if host is not None:
            _end(host.child)


def send(args: argparse.Namespace) -> int:
    """Give one message to the host that owns this lane's session, and to no other."""

    try:
        known = json.loads(args.record.read_text(encoding="utf-8"))
        if known.get("schema") != SCHEMA or known.get("session_id") != args.session:
            raise ValueError("the hosted-session record is not this session's")
        if not known.get("started") or _started(int(known["pid"])) != known["started"]:
            raise ValueError("the host that owned this session is not running")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(40)
            client.connect(known["socket"])
            client.sendall(json.dumps({"secret": known["secret"], "session": args.session,
                                       "text": args.message}).encode() + b"\n")
            raw = bytearray()
            while not raw.endswith(b"\n"):
                part = client.recv(65536)
                if not part:
                    raise ValueError("the host closed without answering")
                raw.extend(part)
        answer = json.loads(raw)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"hosted-session: {error}", file=sys.stderr)
        return 70
    print(str(answer.get("said") or ""))
    return 0 if answer.get("ok") is True else 70


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    runner = sub.add_parser("run", help="host one session; the provider's server command "
                            "follows --")
    runner.add_argument("--protocol", choices=sorted(PROTOCOL.values()), required=True)
    runner.add_argument("--journal", type=pathlib.Path, required=True)
    runner.add_argument("--record", type=pathlib.Path, required=True)
    runner.add_argument("--workspace", type=pathlib.Path, required=True)
    runner.add_argument("--resume", default="")
    runner.add_argument("--model", default="")
    runner.add_argument("--effort", default="")
    runner.add_argument("--permissive", action="store_true")
    runner.add_argument("--mcp-file", type=pathlib.Path)
    runner.add_argument("--unset", action="append", default=[])
    runner.add_argument("--message", required=True)
    runner.add_argument("provider", nargs=argparse.REMAINDER)
    sender = sub.add_parser("send", help="give a later message to the running host")
    sender.add_argument("--record", type=pathlib.Path, required=True)
    sender.add_argument("--session", required=True)
    sender.add_argument("--message", required=True)
    args = parser.parse_args(argv)
    if args.command == "send":
        return send(args)
    provider = args.provider[1:] if args.provider[:1] == ["--"] else args.provider
    if not provider or not args.record.name.endswith(".json"):
        parser.error("the provider's server command is required after --")
    return run(args, provider)


if __name__ == "__main__":
    raise SystemExit(main())
