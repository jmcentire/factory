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
    # The first message, when it was given to the program as its launch argument instead of
    # being typed later: its mark and the digests of what was given. None: nothing was.
    launched: Mapping[str, str] | None = None


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
#: Cursor's project permission file: the one place a rule for the lane's own server can go.
CURSOR_CLI = ".cursor/cli.json"
#: Banned for Cursor: its print mode.
BANNED_CURSOR_FLAGS = ("-p", "--print", "--output-format")


CURSOR_PROJECT_FILES = (CURSOR_HOOKS, CURSOR_MCP, CURSOR_CLI)
#: Text only a lane's own generated file carries: the observation hook's command, and the
#: lane server's name. A target project's own Cursor files hold neither.
_LANE_MARKS = {CURSOR_HOOKS: (b"native_session.py", b"--dialect"),
               CURSOR_MCP: (b"lane-kindex",),
               CURSOR_CLI: (b"Mcp(lane-kindex:",)}


#: Muse Code runs a project's hooks from this file, beside the operator's own, once the
#: workspace is trusted (observed 2026-11-13 on Muse Code 1.4.20 with its echo provider, by
#: me and independently by the tester). It is the lane's for as long as its session runs.
MUSE_HOOKS = ".muse/hooks.json"
MUSE_PROJECT_FILES = (MUSE_HOOKS,)
_LANE_MARKS[MUSE_HOOKS] = (b"native_session.py", b"--dialect")
#: Antigravity loads a plugin it finds in a trusted workspace's ``.agents/plugins/<name>/``:
#: its ``hooks.json`` beside the operator's hooks, and the servers in its ``mcp_config.json``
#: beside the operator's (observed 2026-11-13 on agy 1.5.8, by me and independently by the
#: tester, with no ``plugins.json`` and nothing installed). The directory is the lane's for as
#: long as its session runs.
AGY_PLUGIN = ".agents/plugins/lane"
AGY_MANIFEST = f"{AGY_PLUGIN}/plugin.json"
AGY_HOOKS = f"{AGY_PLUGIN}/hooks.json"
AGY_MCP = f"{AGY_PLUGIN}/mcp_config.json"
AGY_PROJECT_FILES = (AGY_MANIFEST, AGY_HOOKS, AGY_MCP)
_LANE_MARKS[AGY_HOOKS] = (b"native_session.py", b"--dialect")
#: Codex runs a trusted project's hooks from this file beside the operator's own (observed
#: 2026-11-13 on codex-cli 0.167.0, interactive, on the operator's ChatGPT sign-in). It is
#: the lane's for as long as its session runs.
CODEX_HOOKS = ".codex/hooks.json"
CODEX_PROJECT_FILES = (CODEX_HOOKS,)
_LANE_MARKS[CODEX_HOOKS] = (b"native_session.py", b"--dialect")
#: The project files a lane of each agent writes into its own clone, and what to call them.
PROJECT_FILES: Mapping[str, tuple[str, ...]] = {"cursor-agent": CURSOR_PROJECT_FILES,
                                                "muse": MUSE_PROJECT_FILES,
                                                "agy": AGY_PROJECT_FILES,
                                                "codex-interactive": CODEX_PROJECT_FILES}
PROJECT_LABEL = {"cursor-agent": "Cursor", "muse": "Muse", "agy": "Antigravity",
                 "codex-interactive": "Codex"}
#: Directories a lane owns outright: anything else in one is the target project's, and the
#: lane does not add its files beside it.
PROJECT_OWNED_DIRS: Mapping[str, tuple[str, ...]] = {"agy": (AGY_PLUGIN,)}


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def project_collisions(workspace: pathlib.Path,
                       relatives: Sequence[str] = CURSOR_PROJECT_FILES,
                       owned: Sequence[str] = ()) -> list[str]:
    """What in ``workspace`` already occupies the paths a lane would write, in words.

    For a launcher to ask before it reserves anything: a path that exists here is the target
    project's own file (a lane's own leftovers are cleared first, by their manifest), and a
    link on the way is not written through. ``install_project_files`` makes the same
    refusal for a caller that did not ask.
    """

    root = workspace.resolve()
    found: list[str] = []
    for relative in relatives:
        target = root / relative
        parent = target.parent
        if parent.is_symlink():
            reason = f"{parent.relative_to(root)} is a link"
        elif target.is_symlink():
            reason = f"{relative} is a link"
        elif target.exists():
            reason = f"{relative} already exists and is the target project's own file"
        else:
            continue
        if reason not in found:
            found.append(reason)
    for directory in owned:
        held = root / directory
        if held.is_symlink():
            found.append(f"{directory} is a link")
        elif held.is_dir():
            names = {pathlib.PurePosixPath(relative).name for relative in relatives
                     if relative.startswith(directory + "/")}
            other = sorted(entry.name for entry in held.iterdir() if entry.name not in names)
            if other:
                found.append(f"{directory} already exists and holds the target project's own "
                             f"{', '.join(other[:5])}")
    return found


def lane_file_in_candidate(relative: str, data: bytes, manifests: Sequence[pathlib.Path] = (),
                           *, marks: bool = True) -> str:
    """Why ``data`` at ``relative`` in a candidate is a lane's own project file, or "".

    By what it contains (the lane's hook command, the lane server's name), or by being
    byte-for-byte a file some lane's manifest recorded writing. These words can also
    appear in a project's own configuration, so a caller asks this only about a file that
    is new or changed since the run began, never about one the target already held, and
    passes ``marks=False`` for a path the target had before, where only a recorded digest
    is evidence.
    """

    wanted = _LANE_MARKS.get(relative) if marks else None
    if wanted and all(mark in data for mark in wanted):
        return "it holds a lane's own hook command or lane server"
    digest = _sha(data)
    for manifest in manifests:
        try:
            if digest in _manifest(manifest).values():
                return f"it is the file recorded in {manifest.name}"
        except (OSError, ValueError):
            continue
    return ""


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
                          manifest: pathlib.Path,
                          reads: str = "lane hooks and the lane's Kindex") -> None:
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
        ancestors = [ancestor for ancestor in target.parents if workspace in ancestor.parents]
        if (any(ancestor.is_symlink() for ancestor in ancestors) or target.is_symlink()
                or workspace not in target.resolve().parents):
            raise ValueError(f"{relative} in the lane's repository is a link; refusing to write "
                             "through it")
        if target.exists():
            mine = recorded.get(str(target))
            if mine is None or _sha(target.read_bytes()) != mine:
                raise ValueError(
                    f"the lane's repository already has {relative}, which is the target "
                    "project's own file and is not edited or replaced. This provider reads "
                    f"{reads} only from that path, so the lane "
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
        # Outermost first, so each directory this start creates is recorded as created.
        for ancestor in reversed([a for a in target.parents if workspace in a.parents]):
            if not ancestor.exists():
                ancestor.mkdir()
                planned[str(ancestor) + "/"] = "directory"
        temporary = target.with_name(f".{target.name}.factory-{os.getpid()}")
        temporary.write_bytes(data)
        os.replace(temporary, target)
    _write_private(manifest, json.dumps({**recorded, **planned}, sort_keys=True))


def remove_project_files(manifest: pathlib.Path) -> list[str]:
    """Remove exactly the files this lane wrote and still holds unchanged; report the rest.

    A recorded file whose bytes changed is left where it is and named in the result: someone
    else wrote it since, and it is theirs to keep.

    Nothing is reached through a link. Every recorded path was canonical when it was written
    (no link anywhere above it), so if the directory it sits in no longer resolves to
    itself, some directory on the way has since been replaced by a link, and what the path
    now names is somewhere else: possibly a file outside the lane's repository that happens
    to hold the same bytes. Such an entry is neither read nor removed. It is kept in the
    record and named in the result, for a person to look at.
    """

    recorded = _manifest(manifest)
    kept: dict[str, str] = {}
    left: list[str] = []

    def moved(path: pathlib.Path) -> bool:
        try:
            return path.parent.resolve(strict=True) != path.parent
        except OSError:
            return False  # the directory is gone, and so is anything that was in it

    for name, digest in sorted(recorded.items(), reverse=True):  # files before directories
        path = pathlib.Path(name)
        if moved(path):
            kept[name] = digest
            left.append(name)
            continue
        if digest == "directory":
            if path.is_symlink():
                kept[name] = digest  # replaced by a link: not this lane's directory any more
                left.append(name)
                continue
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
    (``cursor-mcp.json``) and its one permission rule (``cursor-cli.json``) go into the
    project files Cursor reads, beside the operator's own user-level hooks, servers and
    permissions, which are untouched and still apply. Cursor chooses its
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
    for source, relative in (("cursor-mcp.json", CURSOR_MCP), ("cursor-cli.json", CURSOR_CLI)):
        if (directory / source).exists():
            files[relative] = (directory / source).read_bytes()
    manifest = directory / f"{slot}-project-files.json"
    install_project_files(workspace, files, manifest)
    try:
        offset = journal.stat().st_size
    except FileNotFoundError:
        offset = 0
    name = pathlib.Path(executable).name
    return Plan((executable, *args), session, resumed, journal,
                Launcher(name, "executable", path=executable), offset, manifest)


#: Muse subcommands a lane's session may begin with. Everything else Muse offers as a
#: subcommand is another program (its headless `exec`, its servers), never a lane's session.
_MUSE_SESSION_WORDS = ("resume",)


def muse_plan(arguments: Sequence[str], *, executable: str, directory: pathlib.Path,
              slot: str, workspace: pathlib.Path, prompt: str,
              python: str = sys.executable) -> Plan:
    """A native start of Muse Code's interactive session.

    ``arguments`` exclude the executable. What was observed of Muse Code 1.4.20 on its echo
    provider decides the shape, and nothing here goes beyond it:

    - The lane's observation hooks go into ``.muse/hooks.json`` in the lane's own clone,
      which Muse runs beside the operator's own hooks. A per-run ``-c hooks=...`` would
      replace the operator's hooks, so it is refused here in any argument.
    - Muse reports a session's start only when its first prompt is submitted, so the first
      message is given as the launch argument (after ``--``, so its text is never read as an
      option), with its mark. Later messages are typed.
    - Muse chooses its own session id; its start report names it.
    - ``resume <id>`` continues that same session. Muse refuses a prompt beside ``resume``
      and a resumed session reports nothing until one is typed, so a resumed session is
      given no launch argument and stays ``starting`` until it reports.
    """

    args = _split_values(list(arguments), ("--config",))
    if args and not args[0].startswith("-") and args[0] not in _MUSE_SESSION_WORDS:
        raise ValueError(f"native Muse session refuses the `{args[0]}` subcommand; a lane is "
                         "Muse's interactive session")
    for index, part in enumerate(args):
        value = ""
        if part in ("-c", "--config") and index + 1 < len(args):
            value = args[index + 1]
        elif part.startswith("-c") and not part.startswith("--") and len(part) > 2:
            value = part[2:]
        if re.match(r"\s*hooks\b", value):
            raise ValueError("native Muse session refuses a per-run `hooks` setting: it "
                             "replaces the operator's own hooks")
    session = ""
    resumed = args[:1] == ["resume"]
    if resumed:
        session = args[1] if len(args) > 1 else ""
        try:
            uuid.UUID(session)
        except ValueError:
            raise ValueError("native session id missing or malformed") from None
    if "\0" in prompt:
        raise ValueError("the first message holds a NUL byte and cannot be a launch argument")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    journal = directory / f"{slot}-native-events.jsonl"
    if journal.is_symlink():
        raise ValueError("symlinked native event journal")
    command = shlex.join([python, str(pathlib.Path(__file__).resolve()), "hook",
                          "--dialect", "muse", "--journal", str(journal)])
    # The one file shape observed to load: an outer "hooks" object keyed by Claude Code's
    # event names. The same object without the outer key is silently ignored by Muse.
    hooks = {event: [{"matcher": "*", "hooks": [{"type": "command", "command": command}]}]
             for event in HOOK_EVENTS}
    manifest = directory / f"{slot}-project-files.json"
    install_project_files(
        workspace, {MUSE_HOOKS: json.dumps({"hooks": hooks}, sort_keys=True).encode()},
        manifest, reads="a lane's hooks, beside the operator's,")
    try:
        offset = journal.stat().st_size
    except FileNotFoundError:
        offset = 0
    launched = None
    if not resumed:
        # A mark of this start's own: the session id it would otherwise derive from is not
        # known until Muse reports it.
        mark = message_token(uuid.uuid4().hex, "initial")
        given = marked(prompt, mark)
        args += ["--", given]
        launched = {"token": mark, "typed_sha256": text_digest(given),
                    "body_sha256": text_digest(prompt)}
    name = pathlib.Path(executable).name
    return Plan((executable, *args), session, resumed, journal,
                Launcher(name, "executable", path=executable), offset, manifest, launched)


#: Refused for Antigravity: its print mode, a launch prompt of someone else's (the lane
#: gives the first message itself), and "the most recent conversation", which names none.
BANNED_AGY_FLAGS = ("-p", "--print", "--prompt", "--output-format", "--input-format",
                    "--json-schema", "--print-timeout", "-i", "--prompt-interactive",
                    "-c", "--continue")


def agy_plan(arguments: Sequence[str], *, executable: str, directory: pathlib.Path,
             slot: str, workspace: pathlib.Path, prompt: str,
             python: str = sys.executable) -> Plan:
    """A native start of Antigravity's interactive session.

    ``arguments`` exclude the executable. The shape follows what was observed of agy 1.5.8
    with a prompt its own hook stopped before any model call, and goes no further:

    - The lane's hooks and its Kindex are a plugin in the lane's own clone,
      ``.agents/plugins/lane/``, which Antigravity loads beside the operator's hooks and
      servers once the workspace is trusted. Nothing of the operator's is written; no
      ``agy mcp add`` and no ``agy plugin install``, which write the operator's files.
    - Antigravity reports a session's start with its first prompt, so the first message is
      given at launch (``--prompt-interactive``, the interactive program's own option),
      with its mark. That option at launch was not among the observations; the typed form
      was.
    - Antigravity chooses its own conversation id; its start report names it.
    - ``--conversation <id>`` continues that conversation and gets no launch message.
    """

    args = _split_values(list(arguments), ("--conversation",))
    banned = sorted({_flag(part) for part in args if _flag(part) in BANNED_AGY_FLAGS})
    if banned:
        raise ValueError("native Antigravity session refuses " + ", ".join(banned))
    session = ""
    resumed = "--conversation" in args
    if resumed:
        index = args.index("--conversation")
        session = args[index + 1] if index + 1 < len(args) else ""
        try:
            uuid.UUID(session)
        except ValueError:
            raise ValueError("native conversation id missing or malformed") from None
    if "\0" in prompt:
        raise ValueError("the first message holds a NUL byte and cannot be a launch argument")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    journal = directory / f"{slot}-native-events.jsonl"
    if journal.is_symlink():
        raise ValueError("symlinked native event journal")
    manifest = directory / f"{slot}-project-files.json"
    recorded = set(_manifest(manifest))
    plugin = workspace.resolve() / AGY_PLUGIN
    if plugin.is_dir() and not plugin.is_symlink():
        foreign = sorted(entry.name for entry in plugin.iterdir()
                         if str(entry) not in recorded)
        if foreign:
            raise ValueError(
                f"the lane's repository already has {AGY_PLUGIN} holding "
                f"{', '.join(foreign[:5])}, which is the target project's own plugin and is "
                "not edited or added to, so the lane cannot run as a native session in this "
                "repository")
    command = shlex.join([python, str(pathlib.Path(__file__).resolve()), "hook",
                          "--dialect", "agy", "--journal", str(journal)])
    # The shape Antigravity's own validator accepts and its loader ran: a named hook set,
    # then event, then a list of command handlers.
    hooks = {"lane": {event: [{"type": "command", "command": command}]
                      for event in AGY_EVENTS}}
    servers: dict[str, object] = {}
    source = directory / "agy-mcp.json"
    if source.exists():
        document = json.loads(source.read_text(encoding="utf-8"))
        found = document.get("mcpServers") if isinstance(document, dict) else None
        if not isinstance(found, dict):
            raise ValueError("the lane's Antigravity server file is malformed")
        servers = found
    files = {
        AGY_MANIFEST: json.dumps({
            "name": "lane", "version": "1",
            "description": "Factory lane: its report hooks and its own Kindex. Written for "
                           "this session and removed when it ends."}, sort_keys=True).encode(),
        AGY_HOOKS: json.dumps(hooks, sort_keys=True).encode(),
        AGY_MCP: json.dumps({"mcpServers": servers}, sort_keys=True).encode(),
    }
    install_project_files(workspace, files, manifest,
                          reads="a lane's plugin (its hooks and its Kindex)")
    try:
        offset = journal.stat().st_size
    except FileNotFoundError:
        offset = 0
    launched = None
    if not resumed:
        mark = message_token(uuid.uuid4().hex, "initial")
        given = marked(prompt, mark)
        args += ["--prompt-interactive", given]
        launched = {"token": mark, "typed_sha256": text_digest(given),
                    "body_sha256": text_digest(prompt)}
    name = pathlib.Path(executable).name
    return Plan((executable, *args), session, resumed, journal,
                Launcher(name, "executable", path=executable), offset, manifest, launched)


#: Codex subcommands a lane's session may begin with. Its others are different programs
#: (the non-interactive `exec` and `review`, its servers), never a lane's session.
_CODEX_SESSION_WORDS = ("resume",)


def codex_plan(arguments: Sequence[str], *, executable: str, directory: pathlib.Path,
               slot: str, workspace: pathlib.Path, prompt: str,
               python: str = sys.executable) -> Plan:
    """A native start of Codex's interactive session.

    ``arguments`` exclude the executable. The shape follows what was observed of codex-cli
    0.167.0 on 2026-11-13 and goes no further:

    - The lane's report hooks go into ``.codex/hooks.json`` in the lane's own clone, which
      Codex runs beside the operator's hooks when the folder is trusted and the hooks are.
      Neither trust is given here: Codex asks about the folder in the lane's window, and it
      skips a hook it has not been told to trust unless the caller passed Codex's own
      per-run option for that.
    - The first message is the launch argument (after ``--``), with its mark; Codex reported
      it as the session's first prompt.
    - Codex chooses its own session id. Its hooks also report sessions of its own that this
      lane did not start (a memory-writing agent was seen), so the lane's session is the one
      whose prompt report carries the launch mark, not the first to report a start.
    - ``resume <id>`` continues that session and is given no launch message here.
    """

    args = list(arguments)
    if args and not args[0].startswith("-") and args[0] not in _CODEX_SESSION_WORDS:
        raise ValueError(f"native Codex session refuses the `{args[0]}` subcommand; a lane is "
                         "Codex's interactive session")
    session = ""
    resumed = args[:1] == ["resume"]
    if resumed:
        session = args[1] if len(args) > 1 else ""
        try:
            uuid.UUID(session)
        except ValueError:
            raise ValueError("native session id missing or malformed") from None
    if "\0" in prompt:
        raise ValueError("the first message holds a NUL byte and cannot be a launch argument")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    journal = directory / f"{slot}-native-events.jsonl"
    if journal.is_symlink():
        raise ValueError("symlinked native event journal")
    command = shlex.join([python, str(pathlib.Path(__file__).resolve()), "hook",
                          "--dialect", "codex", "--journal", str(journal)])
    hooks = {event: [{"hooks": [{"type": "command", "command": command}]}]
             for event in CODEX_EVENTS}
    manifest = directory / f"{slot}-project-files.json"
    install_project_files(
        workspace, {CODEX_HOOKS: json.dumps({"hooks": hooks}, sort_keys=True).encode()},
        manifest, reads="a lane's hooks, beside the operator's,")
    try:
        offset = journal.stat().st_size
    except FileNotFoundError:
        offset = 0
    launched = None
    if not resumed:
        mark = message_token(uuid.uuid4().hex, "initial")
        given = marked(prompt, mark)
        args += ["--", given]
        launched = {"token": mark, "typed_sha256": text_digest(given),
                    "body_sha256": text_digest(prompt)}
    name = pathlib.Path(executable).name
    return Plan((executable, *args), session, resumed, journal,
                Launcher(name, "executable", path=executable), offset, manifest, launched)


def codex_queue(executable: str, session: str, text: str) -> list[str]:
    """Codex's own command for giving a message to an existing session: nothing is typed.

    Observed: it answers "Queued message <id> for thread <id>" and the running session then
    reports the message as a prompt of its own. Sent while the session was between turns;
    what happens to one queued during a turn was not observed.
    """

    return [executable, "queue", "--thread", session, "--message", text]


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
#: Muse Code's hooks use Claude Code's event names and nearly its payload (observed, as
#: above): ``session_id``, ``prompt``, ``last_assistant_message``, ``stop_hook_active``, and a
#: ``turn_id`` that a prompt's report and that turn's stop report share.
DIALECTS = ("claude", "cursor", "muse", "agy", "codex")
#: Codex's hooks use Claude Code's event names and nearly its payload (observed, as above):
#: ``session_id``, ``prompt``, ``last_assistant_message``, ``stop_hook_active`` and a
#: ``turn_id`` a prompt's report and its turn's stop report share.
CODEX_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd")
#: Antigravity's hook events (observed, as above), under the names the journal uses. Its
#: payload is camelCase: ``hookEventName``, ``conversationId``, ``lastUserInput``.
#: ``PreInvocation`` runs before every model call, each time carrying the latest user
#: message, so it is the prompt report; a turn with several model calls sends several.
AGY_EVENTS = {"SessionStart": "SessionStart", "PreInvocation": "UserPromptSubmit",
              "Stop": "Stop"}


def record_hook(journal: pathlib.Path, payload: Mapping[str, object],
                dialect: str = "claude") -> None:
    """Append one native lifecycle fact. Prompt text is kept as a digest, never verbatim."""

    event = payload.get("hook_event_name")
    session = payload.get("session_id")
    if dialect == "cursor":
        event = CURSOR_EVENTS.get(str(event))
        session = payload.get("conversation_id")
    if dialect == "agy":
        event = AGY_EVENTS.get(str(payload.get("hookEventName")))
        session = payload.get("conversationId")
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
    if dialect in ("muse", "codex") and isinstance(payload.get("turn_id"), str):
        row["generation"] = payload["turn_id"]
    if dialect == "agy" and event == "UserPromptSubmit":
        # One user message is reported once per model call. The number is kept so a reader
        # can tell the calls apart; the driver counts a repeated message once.
        payload = {**payload, "prompt": payload.get("lastUserInput")}
        if isinstance(payload.get("invocationNum"), int):
            row["invocation"] = payload["invocationNum"]
        else:
            row["invocation"] = -1
    if dialect == "agy" and event == "Stop":
        # Antigravity's own words about this stop, kept as it gave them. None of them is
        # taken to mean the session is ready for input.
        for name, key in (("fully_idle", "fullyIdle"),
                          ("continuation_active", "continuationActive")):
            if isinstance(payload.get(key), bool):
                row[name] = payload[key]
        reason = payload.get("terminationReason")
        if isinstance(reason, str):
            row["termination_reason"] = reason[:200]
        # A real turn's stop report carried no model text under any name (observed), so the
        # journal has none for Antigravity and a lane's done line is not seen from it.
    if event == "UserPromptSubmit" and isinstance(payload.get("prompt"), str):
        reported = str(payload["prompt"])
        row["prompt_sha256"] = text_digest(reported)
        row["tokens"] = TOKEN.findall(reported)[:16]
    if event == "SessionStart" and isinstance(payload.get("source"), str):
        row["source"] = payload["source"]
    if event == "Stop" and dialect in ("claude", "muse", "codex"):
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
    check = sub.add_parser("check-project", help="say what in a workspace already occupies "
                           "the project paths a lane of --agent would write; exit 70 if "
                           "anything")
    check.add_argument("--workspace", type=pathlib.Path, required=True)
    check.add_argument("--agent", choices=sorted(PROJECT_FILES), default="cursor-agent")
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
        if args.dialect in ("cursor", "agy"):
            # Cursor reads a JSON reply; the empty object changes nothing (the form proven).
            # Antigravity reads one too: the empty object names no decision, no injected
            # step and no termination. Claude Code adds a prompt hook's output to the
            # model's context, so it gets none.
            sys.stdout.write("{}")
        return 0
    if args.command == "check-project":
        try:
            found = project_collisions(args.workspace, PROJECT_FILES[args.agent],
                                       PROJECT_OWNED_DIRS.get(args.agent, ()))
        except OSError as error:
            print(f"native-session: {error}", file=sys.stderr)
            return 70
        if found:
            print("; ".join(found))
            return 70
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
