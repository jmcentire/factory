#!/usr/bin/env python3
"""native_session — host a provider's own interactive program on a terminal the wrapper owns.

Founder direction, 2026-11-13: Claude Code is never run in print mode, never billed to an API
key, never has its own state files rewritten, and never has the operator's settings suppressed.
A resident Claude seat is therefore the operator's own interactive session: started through the
operator's launcher, on a real terminal, with the operator's sign-in and configuration.

Invariants this module upholds:

* The program runs on a pseudo-terminal whose master only the wrapper that started it holds, so
  typed input can reach that exact process and no other. The pane's terminal is relayed both
  ways, so an operator attached to the window uses the real program.
* Nothing here reads or writes a provider's own files. Lifecycle facts arrive only from the
  program's supported hook interface, added with ``--settings`` beside the operator's settings.
* Every argument this module adds is additive. ``BANNED_CLAUDE_FLAGS`` are refused, never added.
* API-billing variables are removed from the program's environment; none is ever set.
"""

from __future__ import annotations

import argparse
import datetime
import fcntl
import hashlib
import json
import os
import pathlib
import pty
import re
import select
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import termios
import time
import tty
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

#: Print mode and every flag that replaces or discards the operator's own configuration.
BANNED_CLAUDE_FLAGS = (
    "-p", "--print", "--output-format", "--input-format", "--replay-user-messages",
    "--max-turns", "--setting-sources", "--strict-mcp-config", "--bare",
)
#: Variables that move Claude Code from the signed-in subscription to another billing route.
CLAUDE_API_ENV = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
)
#: The operator names their launcher here: an executable, or a function their login shell
#: defines. Unset: ``cl`` when it is one of those, otherwise the ``claude`` executable.
LAUNCHER_ENV = "FACTORY_CLAUDE_LAUNCHER"
DEFAULT_LAUNCHER = "cl"
#: Native lifecycle events the hook settings subscribe to.
HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "Notification", "SessionEnd")
#: Notification types that mean the session is asking the operator something on screen.
#: Keys typed then would answer that question, so nothing is typed while one is the last report.
ASKING = ("permission_prompt", "elicitation_dialog", "elicitation_url_dialog", "agent_needs_input")
#: The one report that the session is waiting for input: Claude Code sends it about a minute
#: after it finished responding, if nothing was typed since.
WAITING = "idle_prompt"
#: Claude Code's permission arguments. The Factory passes one only on the operator's explicit
#: selection, and never over a launcher that is the operator's own function, which makes
#: that choice itself (``cl`` passes its own permissive flags).
PERMISSION_FLAGS = ("--dangerously-skip-permissions", "--allow-dangerously-skip-permissions")
PASTE_START, PASTE_END = b"\x1b[200~", b"\x1b[201~"
EVENT_LIMIT = 4 * 1024 * 1024
MESSAGE_LIMIT = 256 * 1024


#: The mark typed after every Factory message. The session's prompt report is matched to a
#: message by this mark, because how the program reports pasted text (verbatim, wrapped or as
#: a placeholder) is its own business; the mark is typed as plain keys, outside the paste.
TOKEN = re.compile(r"\[factory-message:([0-9a-f]{16})\]")


def message_token(session: str, identifier: str) -> str:
    return hashlib.sha256(f"{session}:{identifier}".encode()).hexdigest()[:16]


def trailer(token: str) -> bytes:
    """The mark as typed: plain characters after the paste, on the message's last line."""

    return f" [factory-message:{token}]".encode()


def marked(text: str, token: str) -> str:
    """The whole input as the program holds it when nothing transforms the paste."""

    return paste_body(text) + f" [factory-message:{token}]"


def text_digest(text: str) -> str:
    """The identity of a typed message, insensitive to line-ending and edge whitespace changes
    a terminal paste makes."""

    normal = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return "sha256:" + hashlib.sha256(normal.encode("utf-8")).hexdigest()


# -- launcher ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Launcher:
    """How the operator's Claude Code starts: an executable run directly, or a shell function
    their login shell defines. ``via`` is ``executable`` or ``login-shell``."""

    name: str
    via: str
    path: str = ""
    shell: str = ""

    def command(self, arguments: Sequence[str]) -> list[str]:
        if self.via == "executable":
            return [self.path, *arguments]
        # The login shell sources the operator's profile itself; this module never opens it.
        # The billing variables go after the profile has run and before the launcher does.
        script = "unset " + " ".join(CLAUDE_API_ENV) + '; "$0" "$@"'
        return [self.shell, "-l", "-c", script, self.name, *arguments]

    def record(self) -> dict[str, str]:
        return {"name": self.name, "via": self.via}


#: Asks the login shell what a name is. bash answers ``type -t``; zsh answers ``whence -w``.
#: Exit 0: a function or a command file. Exit 3: an alias. Anything else: not defined, or a
#: shell that can say neither (then only an executable on PATH can be the launcher).
_KIND_SCRIPT = (
    'kind=$(type -t "$0" 2>/dev/null) || kind=$(whence -w "$0" 2>/dev/null) || kind=; '
    'case "$kind" in function|file|*": function"|*": command"|*": hashed") exit 0 ;; '
    'alias|*": alias") exit 3 ;; *) exit 1 ;; esac'
)
#: The program the default launcher may stand in for.
CLAUDE_PROGRAM = "claude"


def _shell_kind(shell: str, name: str, environ: Mapping[str, str], timeout: float) -> str:
    """``function``, ``alias`` or ``""`` for ``name`` in the operator's login shell."""

    try:
        probe = subprocess.run(
            [shell, "-l", "-c", _KIND_SCRIPT, name], env=dict(environ),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return {0: "function", 3: "alias"}.get(probe.returncode, "")


def resolve_launcher(environ: Mapping[str, str], *, executable: str = CLAUDE_PROGRAM,
                     timeout: float = 30.0) -> Launcher:
    """Choose the launcher. A named one that cannot be run is an error, never a substitute.

    A launcher is an executable on PATH or a function the login shell defines. A shell alias
    is not one: an alias is expanded only where a shell reads it as a command word, which a
    launcher given arguments by another program never is.

    The default launcher stands in for Claude Code and nothing else: a caller that asks for
    a program not named ``claude`` gets that program, run directly. Naming a launcher in
    ``FACTORY_CLAUDE_LAUNCHER`` is the operator's explicit choice and always applies.
    """

    named = environ.get(LAUNCHER_ENV, "").strip()
    wanted = named or DEFAULT_LAUNCHER
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", wanted):
        raise ValueError(f"{LAUNCHER_ENV} must be one command name or path, not {wanted!r}")
    # Resolution depends on the environment it was given and nothing else: never this
    # process's own PATH (shutil.which would fall back to it), and never a login shell
    # unless that environment says whose it is. A lane's or seat's environment always
    # carries both; a caller that passes neither gets only what is on the default path.
    search = environ.get("PATH") or os.defpath

    def direct() -> Launcher:
        own = shutil.which(executable, path=search)
        if not own:
            raise ValueError(f"{executable} is not on PATH")
        return Launcher(pathlib.Path(executable).name, "executable",
                        path=str(pathlib.Path(own).absolute()))

    if not named and pathlib.Path(executable).name != CLAUDE_PROGRAM:
        return direct()
    found = shutil.which(wanted, path=search)
    if found:
        return Launcher(pathlib.Path(wanted).name, "executable",
                        path=str(pathlib.Path(found).absolute()))
    shell = environ.get("SHELL") or "/bin/sh"
    kind = ""
    if "/" not in wanted and environ.get("HOME") and shutil.which(shell, path=search):
        kind = _shell_kind(shell, wanted, environ, timeout)
    if kind == "function":
        return Launcher(wanted, "login-shell", shell=shell)
    if named:
        raise ValueError(
            f"{LAUNCHER_ENV}={named} is a shell alias in {shell}, which cannot be given "
            "arguments by another program; name an executable or a shell function"
            if kind == "alias" else
            f"{LAUNCHER_ENV}={named} is neither an executable on PATH nor a function the "
            f"login shell {shell} defines")
    return direct()


# -- arguments and hooks -------------------------------------------------------------------------

def hook_settings(journal: pathlib.Path, python: str) -> dict[str, object]:
    """Settings that add one observation hook per lifecycle event and nothing else."""

    command = shlex.join(
        [python, str(pathlib.Path(__file__).resolve()), "hook", "--journal", str(journal)])
    entry = [{"hooks": [{"type": "command", "command": command, "timeout": 10}]}]
    return {"hooks": {event: entry for event in HOOK_EVENTS}}


def merge_settings(base: Mapping[str, object], added: Mapping[str, object]) -> dict[str, object]:
    """``added`` hooks join ``base`` hooks event by event; every other ``base`` key is kept."""

    merged = dict(base)
    existing = base.get("hooks")
    hooks: dict[str, object] = dict(existing) if isinstance(existing, dict) else {}
    extra = added.get("hooks")
    for event, entries in (extra.items() if isinstance(extra, dict) else ()):
        present = hooks.get(event)
        hooks[event] = [*(present if isinstance(present, list) else []), *entries]
    merged["hooks"] = hooks
    return merged


def _write_private(path: pathlib.Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                         | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)


def _flag(part: str) -> str:
    """The option an argument names, whether written ``--flag`` or ``--flag=value``."""

    return part.split("=", 1)[0] if part.startswith("--") else part


def banned_flags(arguments: Sequence[str]) -> list[str]:
    """Every banned option among ``arguments``, in each spelling the program accepts:
    ``--flag``, ``--flag=value``, ``-p``, and ``-p`` inside a short-option cluster."""

    found = set()
    for part in arguments:
        if _flag(part) in BANNED_CLAUDE_FLAGS:
            found.add(_flag(part))
        elif re.fullmatch(r"-[A-Za-z]*p[A-Za-z]*", part):
            found.add("-p")
    return sorted(found)


@dataclass(frozen=True)
class Plan:
    """One native start: the full command, the session it binds, and where its events land."""

    argv: tuple[str, ...]
    session_id: str
    resumed: bool
    journal: pathlib.Path
    launcher: Launcher
    # Where this start's events begin in the journal: taken before the program exists, so
    # nothing it reports, however fast, falls before it; rows an earlier process of the same
    # seat wrote fall before it and are never read as this start's.
    journal_offset: int = 0
    # The record of files this start wrote into the lane's own repository (Cursor's project
    # configuration), so exactly those are removed when the session ends; None: it wrote none.
    manifest: pathlib.Path | None = None


def plan(arguments: Sequence[str], *, launcher: Launcher, directory: pathlib.Path, slot: str,
         name: str, python: str = sys.executable) -> Plan:
    """Turn the registry's Claude arguments into a native start.

    ``arguments`` exclude the executable. A ``--resume <id>`` among them continues that
    conversation; otherwise a new session id is chosen here, so identity never has to be read
    back out of the program's own files.
    """

    args = list(arguments)
    banned = banned_flags(args)
    if banned:
        raise ValueError("native Claude session refuses " + ", ".join(banned))
    if launcher.via == "login-shell":
        # The operator's own launcher carries their permission choice; nothing is added over
        # it, not even an explicitly selected flag: the launcher is the more specific choice.
        kept: list[str] = []
        skip = False
        for part in args:
            if skip:
                skip = False
            elif part == "--permission-mode":
                skip = True
            elif _flag(part) not in (*PERMISSION_FLAGS, "--permission-mode"):
                kept.append(part)
        args = kept
    args = _split_values(args, ("--resume", "--settings", "--session-id"))
    if "--session-id" in args:
        raise ValueError("the session id of a native Claude session is chosen here, not passed")
    resumed = "--resume" in args
    if resumed:
        if args.index("--resume") + 1 >= len(args):
            raise ValueError("native conversation id missing or malformed")
        session = args[args.index("--resume") + 1]
    else:
        session = str(uuid.uuid4())
        args += ["--session-id", session]
    try:
        uuid.UUID(session)
    except ValueError:
        raise ValueError("native conversation id missing or malformed") from None
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    journal = directory / f"{slot}-native-events.jsonl"
    if journal.is_symlink():
        raise ValueError("symlinked native event journal")
    settings = hook_settings(journal, python)
    if "--settings" in args:
        # One --settings reaches the program: fold the lane's existing file into ours.
        index = args.index("--settings")
        existing = json.loads(pathlib.Path(args[index + 1]).read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            raise ValueError("lane settings file is not an object")
        settings = merge_settings(existing, settings)
        del args[index:index + 2]
    target = directory / f"{slot}-native-settings.json"
    _write_private(target, json.dumps(settings, sort_keys=True))
    # First, and in one word: a launcher that reads its first argument as a session name still
    # names the session for its seat (--name=... given twice resolves to this value).
    args = [f"--name={name}", *args, "--settings", str(target)]
    try:
        offset = journal.stat().st_size
    except FileNotFoundError:
        offset = 0
    return Plan(tuple(launcher.command(args)), session, resumed, journal, launcher, offset)


def _split_values(arguments: Sequence[str], flags: Sequence[str]) -> list[str]:
    """Rewrite ``--flag=value`` as ``--flag value`` for the named flags."""

    result: list[str] = []
    for part in arguments:
        name, joined, value = part.partition("=")
        result += [name, value] if joined and name in flags else [part]
    return result


# -- files a provider reads only from the project ------------------------------------------------

#: Cursor reads lane-scoped hooks and MCP servers from these two files in its workspace and
#: from nowhere else a lane may write. They are the lane's for as long as its session runs.
CURSOR_HOOKS = ".cursor/hooks.json"
CURSOR_MCP = ".cursor/mcp.json"
#: Banned for Cursor: its print mode.
BANNED_CURSOR_FLAGS = ("-p", "--print", "--output-format")


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _manifest(path: pathlib.Path) -> dict[str, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
        raise ValueError(f"{path.name} is malformed; refusing to guess which project files "
                         "this lane wrote")
    return value


def install_project_files(workspace: pathlib.Path, files: Mapping[str, bytes],
                          manifest: pathlib.Path) -> None:
    """Write lane configuration into the lane's own repository without touching the target's.

    A path that already exists is the target project's file unless this lane's manifest
    recorded writing exactly those bytes; a target's file is never edited, merged into or
    replaced, so its presence refuses the start. Every file written is recorded with its
    digest before it is written, listed in ``.git/info/exclude`` so no commit carries it,
    and removed by ``remove_project_files``. Nothing outside ``workspace`` is written.
    """

    workspace = workspace.resolve()
    recorded = _manifest(manifest)
    planned: dict[str, str] = {}
    for relative, data in files.items():
        target = workspace / relative
        parent = target.parent
        if parent.is_symlink() or target.is_symlink() or workspace not in target.resolve().parents:
            raise ValueError(f"{relative} in the lane's repository is a link; refusing to write "
                             "through it")
        if target.exists():
            mine = recorded.get(str(target))
            if mine is None or _sha(target.read_bytes()) != mine:
                raise ValueError(
                    f"the lane's repository already has {relative}, which is the target "
                    "project's own file and is not edited or replaced. This provider reads "
                    "lane hooks and the lane's Kindex only from that path, so the lane "
                    "cannot run as a native session in this repository")
        planned[str(target)] = _sha(data)
    _write_private(manifest, json.dumps({**recorded, **planned}, sort_keys=True))
    exclude = workspace / ".git" / "info" / "exclude"
    if (workspace / ".git").is_dir() and not (workspace / ".git").is_symlink():
        exclude.parent.mkdir(exist_ok=True)
        present = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        missing = [f"/{relative}" for relative in files
                   if f"/{relative}" not in present.splitlines()]
        if missing:
            with exclude.open("a", encoding="utf-8") as stream:
                stream.write(("" if present.endswith("\n") or not present else "\n")
                             + "".join(line + "\n" for line in missing))
    for relative, data in files.items():
        target = workspace / relative
        created = not target.parent.exists()
        target.parent.mkdir(exist_ok=True)
        if created:
            planned[str(target.parent) + "/"] = "directory"
        temporary = target.with_name(f".{target.name}.factory-{os.getpid()}")
        temporary.write_bytes(data)
        os.replace(temporary, target)
    _write_private(manifest, json.dumps({**recorded, **planned}, sort_keys=True))


def remove_project_files(manifest: pathlib.Path) -> list[str]:
    """Remove exactly the files this lane wrote and still holds unchanged; report the rest.

    A recorded file whose bytes changed is left where it is and named in the result: someone
    else wrote it since, and it is theirs to keep.
    """

    recorded = _manifest(manifest)
    kept: dict[str, str] = {}
    left: list[str] = []
    for name, digest in sorted(recorded.items(), reverse=True):  # files before directories
        path = pathlib.Path(name)
        if digest == "directory":
            try:
                path.rmdir()  # only if this lane's files were all it held
            except OSError:
                # Still holding a file this lane wrote and left: remember the directory,
                # so it goes when that file does. Otherwise it holds the target's files.
                if any(other.startswith(name) for other in kept):
                    kept[name] = digest
            continue
        if path.is_symlink() or not path.exists():
            continue
        if _sha(path.read_bytes()) != digest:
            kept[name] = digest
            left.append(name)
            continue
        path.unlink()
    if recorded:
        _write_private(manifest, json.dumps(kept, sort_keys=True))
    return left


def cursor_plan(arguments: Sequence[str], *, executable: str, directory: pathlib.Path,
                slot: str, workspace: pathlib.Path, python: str = sys.executable) -> Plan:
    """A native start of Cursor's interactive agent.

    ``arguments`` exclude the executable. The lane's observation hooks, any required hook
    file the registry left in ``directory`` (``cursor-hooks.json``) and the lane's Kindex
    (``cursor-mcp.json``) go into the two project files Cursor reads, beside the operator's
    own user-level hooks and servers, which are untouched and still load. Cursor chooses its
    own conversation id, so it is known only when the session reports starting, unless
    ``--resume=<id>`` names one.
    """

    args = _split_values(list(arguments), ("--resume",))
    banned = sorted({_flag(part) for part in args if _flag(part) in BANNED_CURSOR_FLAGS})
    if banned:
        raise ValueError("native Cursor session refuses " + ", ".join(banned))
    session = ""
    resumed = "--resume" in args
    if resumed:
        index = args.index("--resume")
        session = args[index + 1] if index + 1 < len(args) else ""
        try:
            uuid.UUID(session)
        except ValueError:
            raise ValueError("native conversation id missing or malformed") from None
        # The one spelling proven against the interactive program.
        args[index:index + 2] = [f"--resume={session}"]
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    journal = directory / f"{slot}-native-events.jsonl"
    if journal.is_symlink():
        raise ValueError("symlinked native event journal")
    command = shlex.join([python, str(pathlib.Path(__file__).resolve()), "hook",
                          "--dialect", "cursor", "--journal", str(journal)])
    hooks: dict[str, list[object]] = {event: [{"command": command}] for event in CURSOR_EVENTS}
    extra = directory / "cursor-hooks.json"
    if extra.exists():
        document = json.loads(extra.read_text(encoding="utf-8"))
        required = document.get("hooks") if isinstance(document, dict) else None
        if not isinstance(required, dict):
            raise ValueError("required Cursor hook file is malformed")
        for event, entries in required.items():
            if not isinstance(entries, list):
                raise ValueError("required Cursor hook file is malformed")
            hooks[event] = [*entries, *hooks.get(event, [])]
    files = {CURSOR_HOOKS: json.dumps({"version": 1, "hooks": hooks}, sort_keys=True).encode()}
    servers = directory / "cursor-mcp.json"
    if servers.exists():
        files[CURSOR_MCP] = servers.read_bytes()
    manifest = directory / f"{slot}-project-files.json"
    install_project_files(workspace, files, manifest)
    try:
        offset = journal.stat().st_size
    except FileNotFoundError:
        offset = 0
    name = pathlib.Path(executable).name
    return Plan((executable, *args), session, resumed, journal,
                Launcher(name, "executable", path=executable), offset, manifest)


def child_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """The program's environment: the wrapper's, without any API-billing variable."""

    return {key: value for key, value in environ.items() if key not in CLAUDE_API_ENV}


# -- hook journal --------------------------------------------------------------------------------

#: Cursor's hook events, as its interactive CLI sent them from a project .cursor/hooks.json
#: (independent proof, 2026-11-13, cursor-agent 2026.11.05 and 2026.11.13), under the names
#: the journal uses. Its conversation id is its session id and is what ``--resume=`` takes;
#: ``generation_id`` is one prompt's, and that prompt's stop report carries the same one.
CURSOR_EVENTS = {"sessionStart": "SessionStart", "beforeSubmitPrompt": "UserPromptSubmit",
                 "stop": "Stop", "sessionEnd": "SessionEnd"}
DIALECTS = ("claude", "cursor")


def record_hook(journal: pathlib.Path, payload: Mapping[str, object],
                dialect: str = "claude") -> None:
    """Append one native lifecycle fact. Prompt text is kept as a digest, never verbatim."""

    event = payload.get("hook_event_name")
    session = payload.get("session_id")
    if dialect == "cursor":
        event = CURSOR_EVENTS.get(str(event))
        session = payload.get("conversation_id")
    if event not in HOOK_EVENTS:
        return
    row: dict[str, object] = {
        "ts": datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds"),
        "event": event,
        "session_id": str(session or ""),
    }
    if dialect == "cursor":
        if isinstance(payload.get("generation_id"), str):
            row["generation"] = payload["generation_id"]
        if event == "Stop" and isinstance(payload.get("status"), str):
            row["status"] = payload["status"]
    if event == "UserPromptSubmit" and isinstance(payload.get("prompt"), str):
        reported = str(payload["prompt"])
        row["prompt_sha256"] = text_digest(reported)
        row["tokens"] = TOKEN.findall(reported)[:16]
    if event == "SessionStart" and isinstance(payload.get("source"), str):
        row["source"] = payload["source"]
    if event == "Stop" and dialect == "claude":
        # True when this stop ends a continuation another stop hook asked for.
        row["stop_hook_active"] = payload.get("stop_hook_active") is True
    if event == "Stop":
        if isinstance(payload.get("last_assistant_message"), str):
            row["last_assistant_message"] = str(payload["last_assistant_message"])[:MESSAGE_LIMIT]
    if event == "Notification":
        kind = payload.get("notification_type")
        if kind != WAITING and kind not in ASKING:
            return  # no other notification says anything about the session's input
        row["notification_type"] = kind
    if event == "SessionEnd" and isinstance(payload.get("reason"), str):
        row["reason"] = payload["reason"]
    data = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor = os.open(journal, os.O_WRONLY | os.O_CREAT | os.O_APPEND
                         | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        os.write(descriptor, data)
    finally:
        os.close(descriptor)


class Journal:
    """Read whole rows appended since ``offset``, then since the last read."""

    def __init__(self, path: pathlib.Path, offset: int = 0) -> None:
        self.path = path
        self.offset = offset

    def read(self) -> list[dict[str, object]]:
        try:
            descriptor = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            return []
        with os.fdopen(descriptor, "rb") as stream:
            stream.seek(self.offset)
            raw = stream.read(EVENT_LIMIT)
        rows: list[dict[str, object]] = []
        while b"\n" in raw:
            line, raw = raw.split(b"\n", 1)
            self.offset += len(line) + 1
            try:
                row = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows


def session_started(journal: pathlib.Path, session: str) -> bool:
    """Whether the program itself ever reported starting this conversation."""

    return any(row.get("event") == "SessionStart" and row.get("session_id") == session
               for row in Journal(journal).read())


# -- terminal ------------------------------------------------------------------------------------

def _controlling_terminal() -> None:
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


class Terminal:
    """One program on a pseudo-terminal this object owns, relayed to the wrapper's terminal."""

    def __init__(self, argv: Sequence[str], *, env: Mapping[str, str],
                 cwd: str | None = None) -> None:
        self.inbound = 0 if os.isatty(0) else -1
        # Everything the program writes is relayed to the wrapper's own output, whatever
        # that is: a pane, a pipe or a file. A program that refuses to start says why on
        # its terminal, and that must not be lost because nobody was attached.
        self.outbound = 1
        self.sized_by = 1 if os.isatty(1) else -1
        try:
            os.fstat(self.outbound)
        except OSError:
            self.outbound = -1  # the wrapper has no output to relay to
        # Output waiting to be relayed. The relay never blocks this wrapper: it writes only
        # when the output is ready for it, and holds at most RELAY_LIMIT bytes meanwhile.
        self._relay = bytearray()
        self.relay_dropped = 0
        self._drained = 0  # bytes read from the program's terminal so far
        self.master, slave = pty.openpty()
        self._resize(slave)
        try:
            # Single-threaded wrapper: the hook only makes the new session's terminal controlling.
            self.process = subprocess.Popen(
                list(argv), stdin=slave, stdout=slave, stderr=slave, env=dict(env), cwd=cwd,
                start_new_session=True, preexec_fn=_controlling_terminal)
        except BaseException:
            os.close(self.master)
            raise
        finally:
            os.close(slave)
        os.set_blocking(self.master, False)
        self._saved: list[object] | None = None
        self._pending = b""
        self._open = True
        if self.inbound >= 0:
            self._saved = termios.tcgetattr(self.inbound)
            tty.setraw(self.inbound)
        self._previous_winch = signal.signal(signal.SIGWINCH, lambda *_: self._resize(self.master))

    def _resize(self, descriptor: int) -> None:
        size = struct.pack("HHHH", 50, 200, 0, 0)
        if self.sized_by >= 0:
            try:
                size = fcntl.ioctl(self.sized_by, termios.TIOCGWINSZ, size)
            except OSError:
                pass
        try:
            fcntl.ioctl(descriptor, termios.TIOCSWINSZ, size)
        except OSError:
            pass

    def readers(self) -> list[int]:
        return [fd for fd in (self.master if self._open else -1, self.inbound) if fd >= 0]

    def writers(self) -> list[int]:
        waiting = [self.master] if self._pending and self._open else []
        if self._relay and self.outbound >= 0:
            waiting.append(self.outbound)
        return waiting

    def _queue_relay(self, data: bytes) -> None:
        if self.outbound < 0:
            return
        self._relay += data
        over = len(self._relay) - RELAY_LIMIT
        if over > 0:
            # A reader that has fallen this far behind loses the oldest output. The program,
            # its hooks and this wrapper's control channel are never made to wait for it.
            del self._relay[:over]
            self.relay_dropped += over

    def _flush_relay(self) -> None:
        """Write what the output will take now, and no more.

        The guarantee is exactly this wide: for a pipe that only this wrapper writes (a
        lane's own output, a test's capture), a write of at most PIPE_BUF bytes after select
        reports room does not block. A descriptor shared with another writer, or a terminal
        whose driver stalls between the two calls, can still make one such write wait. The
        descriptor is not switched to non-blocking to close that gap, because that flag
        belongs to every holder of the descriptor and would outlive a wrapper that is killed.
        """

        budget = 64 * 1024
        while self._relay and self.outbound >= 0 and budget > 0:
            if not select.select([], [self.outbound], [], 0)[1]:
                return
            try:
                sent = os.write(self.outbound, bytes(self._relay[:select.PIPE_BUF]))
            except BlockingIOError:
                return
            except OSError:
                self.outbound = -1  # the wrapper's output is gone; keep the program
                self._relay.clear()
                return
            del self._relay[:sent]
            budget -= sent

    def type(self, data: bytes) -> None:
        """Queue bytes for the program's terminal input; ``service`` writes them."""

        self._pending += data

    def typed(self) -> bool:
        return not self._pending

    def service(self, readable: Sequence[int], writable: Sequence[int],
                output: Callable[[bytes], None] | None = None) -> None:
        if self.master in readable and self._open:
            try:
                data = os.read(self.master, 65536)
            except BlockingIOError:
                data = None
            except OSError:
                data = b""  # the program closed its terminal
            if data == b"":
                self._open = False
            elif data:
                self._drained += len(data)
                self._queue_relay(data)
                if output is not None:
                    output(data)
        if self.outbound >= 0 and self.outbound in writable:
            self._flush_relay()
        if self.inbound >= 0 and self.inbound in readable:
            try:
                keys = os.read(self.inbound, 4096)
            except OSError:
                keys = b""
            if keys:
                self._pending += keys
            else:
                self.inbound = -1  # the pane's input closed; the program keeps running
        if self.master in writable and self._pending and self._open:
            try:
                sent = os.write(self.master, self._pending[:1024])
            except BlockingIOError:
                sent = 0
            except OSError:
                self._open, sent = False, 0
            self._pending = self._pending[sent:]

    def drain(self, output: Callable[[bytes], None] | None = None,
              read_s: float = 1.0, flush_s: float = 1.0) -> None:
        """Read what the program left on its terminal as it ended, then give the relay time
        to deliver it. Both loops are bounded, in time and in bytes: a process the program
        left behind may hold the terminal open and write to it for ever, and a reader may
        never read. ``read_s`` and ``flush_s`` are the budgets these loops schedule
        themselves by, not a wall-clock promise: one write to an output that is shared or
        stalls (see ``_flush_relay``), or the ``output`` callback's own work, such as
        writing the lane log, adds whatever it takes on top.
        """

        deadline = time.monotonic() + read_s
        budget = DRAIN_LIMIT
        while self._open and budget > 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready = select.select([self.master], [], [], min(0.2, remaining))[0]
            if not ready:
                break  # quiet: everything it wrote before ending has been read
            before = self._drained
            self.service(ready, [], output)
            budget -= self._drained - before
        deadline = time.monotonic() + flush_s
        while self._relay and self.outbound >= 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [self.outbound], [], remaining)[1]:
                break
            self._flush_relay()

    def close(self) -> None:
        signal.signal(signal.SIGWINCH, self._previous_winch)
        if self._saved is not None and self.inbound >= 0:
            try:
                # At once: waiting for the terminal's pending output to drain could wait on a
                # reader, and the settings being restored do not depend on it.
                termios.tcsetattr(self.inbound, termios.TCSANOW, self._saved)  # type: ignore[arg-type]
            except termios.error:
                pass
        try:
            os.close(self.master)
        except OSError:
            pass


def write_bounded(descriptor: int, data: bytes, timeout: float = 1.0) -> bool:
    """Write ``data`` to an output that may never be read, without waiting on it for long.

    For what a wrapper says on its own output once a native session has run: that output
    may be a pipe nobody reads, already full of the program's relayed screen. Each write is
    made only when the output is ready, and the whole attempt gives up after ``timeout``.
    False means not all of it was written. Whatever must survive is written durably
    elsewhere first; this is the copy for whoever is watching.
    """

    view = memoryview(data)
    deadline = time.monotonic() + timeout
    while view:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            if not select.select([], [descriptor], [], remaining)[1]:
                return False
            sent = os.write(descriptor, view[:select.PIPE_BUF])
        except (OSError, ValueError):
            return False
        view = view[sent:]
    return True


def paste(text: str) -> bytes:
    """A message as one bracketed paste, so its newlines are text and not submissions."""

    return PASTE_START + paste_body(text).encode("utf-8") + PASTE_END


def paste_body(text: str) -> str:
    """The text a paste carries: one newline convention, no escape byte, no trailing newline
    (the mark follows on the same line)."""

    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\x1b", "").rstrip("\n")


#: The largest message typed in full. A longer one is placed, unchanged, in a file beside the
#: session's journal and the session is told to read it: a terminal is not a bulk channel.
TYPED_LIMIT = 16 * 1024


def typed_text(text: str, directory: pathlib.Path, slot: str) -> str:
    """What is actually typed for ``text``: the text itself, or a pointer to its exact bytes."""

    raw = text.encode("utf-8")
    if len(raw) <= TYPED_LIMIT:
        return text
    digest = hashlib.sha256(raw).hexdigest()
    target = directory / f"{slot}-message-{digest[:16]}.txt"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                         | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return (f"This message is the file {target} ({len(raw)} bytes, sha256:{digest}). Read the "
            "whole file now, every line, and check its sha256 before doing anything else. It "
            "governs this lane exactly as if it had been given here. Do not edit or delete it.")


#: How much relayed output may wait for a slow reader before the oldest is dropped.
RELAY_LIMIT = 1024 * 1024
#: How much is read from the terminal of a program that has ended, at most.
DRAIN_LIMIT = 4 * 1024 * 1024
#: How much of a program's output before its session starts is remembered, from its
#: beginning, so that if it ends without starting, what it said first can be kept. It is the
#: program's raw terminal output and may hold whatever the program chose to show there (an
#: account name, a sign-in prompt), so it is written nowhere unless the program ended
#: without a session, and then only to the lane's owner-only log.
STARTUP_KEEP = 256 * 1024
SUBMIT = b"\r"
#: After the program reports its session started, before the first typing; and between the end
#: of a paste and the Enter that submits it. Both are time for the program's own input handling.
SETTLE_S = float(os.environ.get("FACTORY_NATIVE_SETTLE_S", "1.5"))
SUBMIT_GAP_S = float(os.environ.get("FACTORY_NATIVE_SUBMIT_GAP_S", "0.5"))


# -- command line --------------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    hook = sub.add_parser("hook", help="append the lifecycle event on stdin to a journal")
    hook.add_argument("--journal", type=pathlib.Path, required=True)
    hook.add_argument("--dialect", choices=DIALECTS, default="claude")
    clean = sub.add_parser("clean-project", help="remove the project files a lane wrote, by "
                           "its manifest; prints any it left because they had changed")
    clean.add_argument("--manifest", type=pathlib.Path, required=True)
    sub.add_parser("launcher", help="print how Claude Code starts here: name, NUL, via, NUL")
    args = parser.parse_args(argv)
    if args.command == "hook":
        # A hook must never disturb the session it observes: no output, always success.
        try:
            payload = json.loads(sys.stdin.buffer.read(8 * 1024 * 1024))
            if isinstance(payload, dict):
                record_hook(args.journal, payload, args.dialect)
        except (OSError, ValueError):
            pass
        if args.dialect == "cursor":
            # Cursor reads a JSON reply; the empty object changes nothing (the form proven).
            # Claude Code adds a prompt hook's output to the model's context, so it gets none.
            sys.stdout.write("{}")
        return 0
    if args.command == "clean-project":
        try:
            left = remove_project_files(args.manifest)
        except (OSError, ValueError) as error:
            print(f"native-session: {error}", file=sys.stderr)
            return 70
        for name in left:
            print(name)
        return 0
    try:
        chosen = resolve_launcher(os.environ)
    except ValueError as error:
        print(f"native-session: {error}", file=sys.stderr)
        return 70
    sys.stdout.write(f"{chosen.name}\0{chosen.via}\0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
