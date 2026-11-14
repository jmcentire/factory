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

if __name__ == "__main__":
    # As a program this is trusted and runs outside any boundary, in the lane's clone. Only
    # absolute import-path entries are kept: a relative one (the empty entry, or the
    # placeholder an editable install leaves) is read against the working directory, where
    # a lane can write. (Imported, the importing program's path is its own business.)
    # It is started isolated (``-I -S``: no site start-up, its own directory not implied),
    # so its own directory is named here.
    sys.path[:] = [os.path.dirname(os.path.abspath(__file__)),
                   *(entry for entry in sys.path if os.path.isabs(entry))]

import native_session  # noqa: E402
import validator_boundary  # noqa: E402

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
    """Something is required of this start that a hosted session does not have (exit 77)."""


class BoundaryUnusable(ValueError):
    """A boundary was handed to this start and cannot be relied on as handed (exit 70)."""


#: What requires a Signet guard or author custody of a lane: a flag that is set, or a binding
#: that is named. Neither is qualified for any hosted session.
GUARD_FLAGS = ("FACTORY_REQUIRE_SIGNET", "FACTORY_REQUIRE_AUTHOR_CUSTODY")
GUARD_BINDINGS = ("FACTORY_SIGNET_CONTRACT", "FACTORY_SIGNET_DIGEST",
                  "FACTORY_AUTHOR_CUSTODY_CONFIG", "FACTORY_AUTHOR_CUSTODY_DIGEST")
#: What requires the kernel boundary of a lane.
BOUNDARY_FLAGS = ("FACTORY_REQUIRE_BOUNDARY",)
BOUNDARY_BINDINGS = ("FACTORY_BOUNDARY_CONTRACT", "FACTORY_BOUNDARY_DIGEST")
#: The hosted providers that may run inside the kernel boundary. Antigravity only. Muse is
#: not: its own shell sandbox cannot be applied inside the boundary (macOS forbids a second
#: sandbox), and Muse with its sandbox off is not adopted, because the boundary leaves IP
#: egress open and so would not keep Muse's own network policy for shell commands.
BOUNDED_DIALECTS = ("agy",)


def refuse_guarded(environ: Mapping[str, str], *, dialect: str = "",
                   boundary: bool = False, starting: bool = True) -> None:
    """Refuse a hosted start that is required to have what a hosted session does not.

    This is the authority for that refusal: every way of starting a hosted session comes
    through ``plan`` or ``run``, and both call it before anything is spawned or recorded.
    The launcher's own refusal only says the same thing earlier. Fail closed: a flag set to
    anything but empty or ``0`` is a requirement, and so is any named binding.

    - A Signet guard or author custody: refused for every hosted session, with or without a
      boundary. The kernel boundary confines paths and sockets; it decides nothing per
      action and is no substitute for either.
    - The kernel boundary: refused for a provider not in ``BOUNDED_DIALECTS``. For one that
      is, a start (``starting``) that is required to be bounded and was handed no boundary
      record is refused: a session is never started unbounded where a boundary is required.

    ``boundary`` says the caller was handed a boundary record for this start.
    """

    named = [name for name in GUARD_FLAGS if environ.get(name, "") not in ("", "0")]
    named += [name for name in GUARD_BINDINGS if environ.get(name)]
    if named:
        raise GuardRequired(
            "a guard or author custody is required (" + ", ".join(named) + "), and neither "
            "is qualified for a hosted Muse or Antigravity session: no gate on every tool "
            "action is established for it, and the kernel boundary is not one. Nothing was "
            "started")
    asked = [name for name in BOUNDARY_FLAGS if environ.get(name, "") not in ("", "0")]
    asked += [name for name in BOUNDARY_BINDINGS if environ.get(name)]
    if boundary:
        asked.append("a boundary record")
    if not asked:
        return
    if dialect not in BOUNDED_DIALECTS:
        raise GuardRequired(
            "a kernel boundary is required (" + ", ".join(asked) + "), and this hosted "
            "session is refused under one. For Muse: its own shell sandbox cannot be "
            "applied inside the boundary (macOS forbids a second sandbox), and running it "
            "with that sandbox off is not adopted, because the boundary leaves IP egress "
            "open and would not keep Muse's own network policy for shell commands. Nothing "
            "was started")
    if starting and not boundary:
        raise GuardRequired(
            "a kernel boundary is required (" + ", ".join(asked) + ") and no boundary "
            "record was handed to this start; a hosted session is never started unbounded "
            "where one is required. Nothing was started")


def trust_roots(launch_dir: pathlib.Path | None = None) -> list[pathlib.Path]:
    """Every place the trusted side loads code from when it starts: this checkout, the
    interpreter's own trees, every entry of the import path as this interpreter actually
    has it (site-packages, anything a ``.pth`` file or ``PYTHONPATH`` added), the user's
    site directory where it is in use (whether or not it exists yet), and ``launch_dir``
    when a caller has one that its helpers import from. (The lane launchers have none:
    their helpers never import by way of the working directory, ``harness/trusted_startup``.)

    A provider that could write into any of them, or create one that is missing, could run
    code outside the boundary the next time the trusted side starts, so each must be out of
    its reach.
    """

    import site

    roots = {pathlib.Path(__file__).resolve().parent.parent,
             pathlib.Path(os.path.realpath(sys.executable)).parent}
    for value in (sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix,
                  *site.getsitepackages(), *(entry for entry in sys.path if entry)):
        roots.add(pathlib.Path(value).resolve())
    if site.ENABLE_USER_SITE:
        roots.add(pathlib.Path(site.getusersitepackages()).resolve())
    if launch_dir is not None:
        roots.add(pathlib.Path(launch_dir).resolve())
    return sorted(roots)


#: Python flags for a trusted program started in a lane's clone: isolated mode and no site.
ISOLATED = ("-I", "-S")


def _isolated(command: list[str]) -> list[str]:
    """The boundary program's own command, started isolated. It is trusted code that runs
    outside the boundary, in the lane's clone, until it becomes the confined command."""

    if command[:1] == [sys.executable] and command[1:2] != ["-I"]:
        return [command[0], *ISOLATED, *command[1:]]
    return command


def _within(path: pathlib.Path, roots: Sequence[pathlib.Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


#: How many entries of an uncovered tree are examined before it is called too large to
#: establish, which counts as within reach.
REACH_LIMIT = 100000


def _reachable(root: pathlib.Path) -> str | None:
    """A path at or under ``root`` that this user could change, or None if there is none.

    The whole tree is looked at, not its top: a tree another user owns can hold a directory
    this one may write. A path that does not exist is within reach if the nearest directory
    that does exist is. A symlink that leaves the tree is followed one step, to what it
    names. What cannot be examined, and a tree too large to examine, count as within reach.
    """

    uid = os.getuid()

    def mine(path: str) -> bool:
        try:
            status = os.lstat(path)
        except OSError:
            return True
        return status.st_uid == uid or (not os.path.islink(path) and os.access(path, os.W_OK))

    top = str(root)
    while not os.path.lexists(top):
        parent = os.path.dirname(top)
        if parent == top:
            return top
        top = parent
    if top != str(root):
        return top if mine(top) else None
    if mine(top):
        return top
    pending, seen = [top], 0
    while pending:
        current = pending.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            return current
        for entry in entries:
            seen += 1
            if seen > REACH_LIMIT:
                return f"{root} (too large to establish)"
            if mine(entry.path):
                return entry.path
            if entry.is_symlink():
                target = os.path.realpath(entry.path)
                if not _within(pathlib.Path(target), [root]) and mine(target):
                    return target
            elif entry.is_dir(follow_symlinks=False):
                pending.append(entry.path)
    return None


def confinement(record: object, role: str, workspace: pathlib.Path,
                control: Sequence[pathlib.Path], launch_dir: pathlib.Path | None = None,
                ) -> tuple[dict, validator_boundary.Boundary]:
    """Check a boundary before anything is spawned under it, and that it protects what the
    trusted side depends on. Returns the validated record and the parsed boundary.

    The contract and digest are validated as every bounded lane's are
    (``validator_boundary.validate_lane_record``). Beyond that, a hosted session adds a
    trusted program outside the boundary, so the contract must itself put out of the
    provider's reach, and is refused if it does not:

    - every directory in ``control`` (the lane's journals, the host's record with its socket
      secret, its command ids, its socket): under a root the boundary denies reading, which
      it also denies writing;
    - every place in ``trust_roots`` (with ``launch_dir``, when a launcher asks) that this
      user could change anywhere within: under a root the boundary denies writing.

    Nothing is assumed from how a contract was provisioned: one that leaves any of these
    out is refused here, by name.
    """

    try:
        checked = validator_boundary.validate_lane_record(record, role, workspace)
        if checked is None:
            raise validator_boundary.BoundaryError("no boundary record")
        with open(checked["contract"], encoding="utf-8") as stream:
            boundary = validator_boundary.Boundary.parse(json.load(stream))
    except (validator_boundary.BoundaryError, OSError, ValueError, TypeError,
            KeyError) as error:
        raise BoundaryUnusable(f"the kernel boundary cannot be used: {error}") from None
    unwritable = (*boundary.denied_reads, *boundary.protected_writes)
    for path in dict.fromkeys(pathlib.Path(item).resolve() for item in control):
        if not _within(path, boundary.denied_reads):
            raise BoundaryUnusable(
                f"the kernel boundary does not keep the lane's control records from the "
                f"provider: {path} is not under a root it denies reading")
    for root in trust_roots(launch_dir):
        if _within(root, unwritable):
            continue
        found = _reachable(root)
        if found is not None:
            raise BoundaryUnusable(
                f"the kernel boundary does not protect what the trusted host runs from: "
                f"{root} is not under a root it denies writing, and {found} can be changed "
                "by this user (`protocol_host.py trust-roots` lists what a contract must "
                "cover)")
    return checked, boundary


def confined_argv(argv: Sequence[str], record: object, role: str,
                  workspace: pathlib.Path, control: Sequence[pathlib.Path] = ()) -> list[str]:
    """One run of the provider's program inside the boundary, for the runs that are not its
    session server (its account check, its version, its help). Every run of the provider's
    program for a bounded lane goes inside: its start-up is the provider's code too.

    The boundary is held to the same things here as for the server (``confinement``): a
    contract that validates and yet leaves the trusted side's code, or the control
    directories named, within the provider's reach is refused before the command is built.
    No caller is relied on to have checked."""

    confinement(record, role, workspace, control)
    try:
        return _isolated(validator_boundary.model_command(list(argv), record, role, workspace))
    except (validator_boundary.BoundaryError, OSError, ValueError, TypeError,
            KeyError) as error:
        raise BoundaryUnusable(f"the kernel boundary cannot be used: {error}") from None


#: Run inside the boundary, as the very process that becomes the provider's server, before
#: that program exists: it tries what the boundary must stop, says what the kernel
#: answered, and then becomes the server. It is a shell script on the trusted host's command
#: line, run by the system's own shell and tools: nothing of it is in a file the provider
#: could reach, and it needs nothing of the Factory's (so a contract may deny the provider
#: the Factory's checkout and interpreter outright).
#: Arguments: the working directory; a count and that many directories to list; a count
#: and that many directories to create a file in; a count and that many sockets to connect
#: to; ``--``; the provider's command.
#: Listing and creating count as denied only on the kernel's own "Operation not permitted".
#: A connection counts as denied when it is not made: the kernel refuses that one without a
#: word the tools pass on, so the host first shows, from outside, that the same socket can
#: be connected to (``Host``), which leaves the boundary as the only difference.
_ATTEST = r"""
cwd=$1; shift
out="factory_confinement $$"
verdict() { case "$1" in *"Operation not permitted"*) echo denied ;; *) echo error ;; esac; }
n=$1; shift; i=0
while [ "$i" -lt "$n" ]; do
  p=$1; shift
  if e=$(/bin/ls "$p" 2>&1 >/dev/null); then a=allowed; else a=$(verdict "$e"); fi
  out="$out r$i=$a"; i=$((i+1))
done
n=$1; shift; i=0
while [ "$i" -lt "$n" ]; do
  p=$1; shift; f="$p/.factory-confinement-probe-$$"
  if e=$( ( : > "$f" ) 2>&1 ); then /bin/unlink "$f"; a=allowed; else a=$(verdict "$e"); fi
  out="$out w$i=$a"; i=$((i+1))
done
n=$1; shift; i=0
while [ "$i" -lt "$n" ]; do
  p=$1; shift
  if ( cd "${p%/*}" && /usr/bin/nc -w 2 -U "${p##*/}" </dev/null ) >/dev/null 2>&1; then a=allowed
  else a=denied; fi
  out="$out c$i=$a"; i=$((i+1))
done
shift
cd "$cwd" || exit 126
printf '%s\n' "$out"
exec "$@"
"""


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
         environ: Mapping[str, str] | None = None,
         boundary_record: object = None, boundary_role: str = "",
         control: Sequence[pathlib.Path] = ()) -> native_session.Plan:
    """A start of a hosted session, as the lane wrapper's terminal driver takes any start.

    The program the wrapper runs is this file; the provider's server is its child. The
    first message, or the message that restarts an ended session (``message_id``), is this
    program's launch argument, marked as a terminal session's would be, and the plan says
    so in ``launched`` so the wrapper records the hand-over before anything is spawned.
    Nothing is written into the lane's repository.
    """

    refuse_guarded(os.environ if environ is None else environ, dialect=dialect,
                   boundary=boundary_record is not None)
    if boundary_record is not None:
        # Before anything is recorded: the boundary handed to this start is one the lane can
        # be held to, for the host's own files and for the wrapper's (``control``).
        confinement(boundary_record, boundary_role, workspace, [directory, *control])
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
    # The host is a trusted program started in the lane's clone. It is started isolated:
    # no site start-up (so no ``.pth`` entry and no ``sitecustomize`` looked for), no
    # variable honoured, nothing imported by way of the clone. It needs the standard
    # library and this directory, nothing else.
    command = [python, *ISOLATED, str(pathlib.Path(__file__).resolve()), "run",
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
    if boundary_record is not None:
        command += ["--boundary-record", json.dumps(boundary_record, sort_keys=True),
                    "--boundary-role", boundary_role]
        for extra in control:
            command += ["--control", str(extra)]
    command += ["--message", given, "--", executable, *settings["server"]]
    name = pathlib.Path(executable).name
    return native_session.Plan(
        tuple(command), settings["resume"], resumed, journal,
        native_session.Launcher(name, "executable", path=executable), offset, None, launched)


def queue(python: str, record: pathlib.Path, session: str, text: str) -> list[str]:
    """The command that gives a later message to the running host; nothing is typed."""

    return [python, *ISOLATED, str(pathlib.Path(__file__).resolve()), "send",
            "--record", str(record), "--session", session, "--message", text]


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


def _listen(address: str) -> socket.socket:
    """Listen on a socket in its own directory. Bound by its name from inside that
    directory, so a long control path is no obstacle (a socket address is short)."""

    directory, name = os.path.split(address)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    back = os.getcwd()
    try:
        os.chdir(directory)
        try:
            os.unlink(name)  # left by an earlier host of this slot that has ended
        except FileNotFoundError:
            pass
        server.bind(name)
    except OSError:
        server.close()
        raise
    finally:
        os.chdir(back)
    server.listen(4)
    return server


def _connect(address: str, wait: float) -> socket.socket:
    directory, name = os.path.split(address)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(wait)
    back = os.getcwd()
    try:
        os.chdir(directory)
        client.connect(name)
    except OSError:
        client.close()
        raise
    finally:
        os.chdir(back)
    return client


def confined_command(provider: Sequence[str], checked: dict, role: str,
                     workspace: pathlib.Path, boundary: validator_boundary.Boundary,
                     control: Sequence[pathlib.Path], address: str) -> tuple[list[str], dict]:
    """The provider's server command inside the boundary, with the attestation stage in
    front of it, and what that stage is to try: every root of the contract that is a
    directory, the lane's control directories, and the host's own socket."""

    held = [pathlib.Path(item).resolve() for item in control]
    spec = {
        "read": sorted({str(path) for path in (*boundary.denied_reads, *held) if path.is_dir()}),
        "write": sorted({str(path) for path in (*boundary.denied_reads,
                                               *boundary.protected_writes, *held)
                         if path.is_dir()}),
        "connect": [address],
    }
    for tool in ("/bin/sh", "/bin/ls", "/bin/unlink", "/usr/bin/nc"):
        if not os.access(tool, os.X_OK):
            raise BoundaryUnusable(f"the boundary cannot be attested here: {tool} is missing")
    inner = ["/bin/sh", "-c", _ATTEST, "factory-confinement", str(workspace)]
    for kind in ("read", "write", "connect"):
        inner += [str(len(spec[kind])), *spec[kind]]
    inner += ["--", *provider]
    return _isolated(validator_boundary.model_command(inner, checked, role, workspace)), spec


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
        # The provider's server never has an API-routing variable, whoever started this
        # program and with whatever arguments: that is what keeps it on the operator's
        # sign-in. ``--unset`` only adds names to these; it cannot take any away.
        withheld = {*API_ENV, *args.unset}
        environment = {key: value for key, value in os.environ.items() if key not in withheld}
        self.secret = secrets.token_hex(24)
        self.confined: dict | None = None
        self.socket_dir = ""
        record = getattr(args, "boundary_record", None)
        command = list(provider)
        if record is None:
            self.pin(None)
            self.socket_dir = tempfile.mkdtemp(prefix="factory-hosted-", dir="/tmp")
            self.address = self.socket_dir + "/h.sock"
        else:
            # This program stays outside the boundary; only the provider's server goes in.
            # What it depends on (its records, its socket, the code it runs) is checked
            # against the contract before the socket exists and before anything is spawned.
            control = [self.record.parent, self.journal.parent,
                       *getattr(args, "control", [])]
            checked, boundary = confinement(record, args.boundary_role,
                                            pathlib.Path(self.workspace), control)
            self.pin(checked["digest"])
            self.address = str(self.record.with_name(self.record.name[:-5] + ".sock"))
            command, spec = confined_command(provider, checked, args.boundary_role,
                                             pathlib.Path(self.workspace), boundary, control,
                                             self.address)
        # The socket later messages arrive on exists before the provider does. And it
        # answers: shown here from outside the boundary, so that the confined process not
        # reaching it says something about the boundary and not about the socket.
        self.server = _listen(self.address)
        with _connect(self.address, 5):
            self.server.accept()[0].close()
        self.child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, env=environment,
                                      cwd=self.workspace)
        if record is not None:
            try:
                self.attest(checked, spec)
            except BaseException:
                _end(self.child)
                self.server.close()
                raise

    def pin(self, digest: str | None) -> None:
        """A restart of a lane keeps exactly the policy its session was started under: the
        same boundary, or none, by the lane's own retained record of that session. The
        record is this host's, out of the provider's reach when there is a boundary. With
        no such record, a damaged one, one that is another session's, or one that names
        another policy, the restart is refused before anything is started: a missing
        record is not read as "no boundary".

        A probe is not a lane's continuation and has no record to keep to."""

        if not self.args.resume or getattr(self.args, "probing", False):
            return
        if self.record.is_symlink():
            raise Refused("the record of how this session was hosted is a symlink; the "
                          "restart is refused")
        try:
            raw = self.record.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise Refused(f"there is no record of how this session was hosted "
                          f"({self.record.name}); a lane is continued only from its own "
                          "retained record, so the restart is refused") from None
        except OSError as error:
            raise Refused(f"the record of how this session was hosted cannot be read "
                          f"({error}); the restart is refused") from None
        try:
            prior = json.loads(raw)
        except ValueError:
            prior = None
        held = prior.get("confined") if isinstance(prior, dict) else None
        if (not isinstance(prior, dict) or prior.get("schema") != SCHEMA
                or prior.get("protocol") != self.protocol
                or ("confined" in prior and not (
                    isinstance(held, dict) and isinstance(held.get("digest"), str)
                    and held["digest"]))):
            raise Refused("the record of how this session was hosted is damaged; the restart "
                          "is refused")
        if prior.get("session_id") != self.args.resume:
            raise Refused("the record of how this lane was hosted is of another session than "
                          "the one to resume; the restart is refused")
        before = held["digest"] if isinstance(held, dict) else None
        if before != digest:
            def named(value: str | None) -> str:
                return f"under the boundary {value[:16]}" if value else "with no boundary"
            raise Refused(f"this session was hosted {named(before)} and this restart is "
                          f"{named(digest)}; a restart keeps exactly the policy the session "
                          "was started under")

    def attest(self, checked: dict, spec: dict) -> None:
        """Take the confined process's own account of the boundary, before the provider's
        server exists in it, and hold it to that. No account, another process's account, or
        an account of anything the kernel allowed, and there is no confined provider."""

        assert self.child.stdout is not None and self.child.stderr is not None
        deadline = time.monotonic() + HANDSHAKE_S
        while b"\n" not in self.buffer:
            if time.monotonic() >= deadline:
                raise Refused("the confined process gave no account of its boundary in time")
            readable, _, _ = select.select([self.child.stdout], [], [], 0.2)
            if readable:
                chunk = os.read(self.child.stdout.fileno(), 65536)
                if chunk:
                    self.buffer += chunk
                    continue
            if self.child.poll() is not None:
                said = self.child.stderr.read().decode("utf-8", "replace").strip()[-300:]
                raise Refused(f"the boundary did not start the provider "
                              f"(exit {self.child.returncode}): {said}")
        line, self.buffer = self.buffer.split(b"\n", 1)
        words = line.decode("utf-8", "replace").split()
        if words[:1] != ["factory_confinement"] or len(words) < 2:
            raise Refused("the first thing the confined process said was not its account of "
                          "the boundary")
        if words[1] != str(self.child.pid):
            raise Refused("the account of the boundary is not from the process that was started")
        answers = dict(word.split("=", 1) for word in words[2:] if "=" in word)
        for kind, letter in (("read", "r"), ("write", "w"), ("connect", "c")):
            for index, path in enumerate(spec[kind]):
                if answers.get(f"{letter}{index}") != "denied":
                    raise Refused(f"the kernel boundary is not in force for the provider: "
                                  f"{kind} of {path} was "
                                  f"{answers.get(f'{letter}{index}')!r}, not denied")
        started = _started(self.child.pid)
        if not started:
            raise Refused("the confined provider's start time could not be read")
        self.confined = {
            "pid": self.child.pid, "started": started, "contract": checked["contract"],
            "digest": checked["digest"], "role": checked["role"],
            "denied": {kind: len(spec[kind]) for kind in ("read", "write", "connect")}}
        self.keep("confined", self.confined)

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

    def open(self, create: bool = True) -> None:
        """Handshake, then the one session: a new one, or the kept one and no other. With
        ``create`` false nothing is made: a handshake, and a resume if an id was given."""

        resume = self.args.resume
        if self.protocol == "msp":
            self.call("initialize", {"clientInfo": {"name": "factory_lane", "version": "1"},
                                     "capabilities": {"userInputDialogs": False}})
            self.write({"jsonrpc": "2.0", "method": "initialized"})
            if not resume and not create:
                return
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
            if not resume and not create:
                return
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

        # What must be in force when this returns: option id -> value.
        required: dict[str, str] = {}
        model, effort = self.args.model, self.args.effort
        if model:
            if model == now("model") or model in offered("model"):
                required["model"] = model
            elif model == f"{now('model')}-{now('thought_level')}":
                required["model"], required["thought_level"] = now("model"), now("thought_level")
            else:
                pairs = [(m, t) for m in offered("model") for t in offered("thought_level")
                         if model == f"{m}-{t}"]
                if len(pairs) != 1:
                    raise Refused(f"this server does not offer the model `{model}` "
                                  f"(offered: {', '.join(offered('model')) or 'none named'})")
                required["model"], required["thought_level"] = pairs[0]
        if effort:
            if required.get("thought_level", effort) != effort:
                raise Refused(f"the model `{model}` names the effort "
                              f"`{required['thought_level']}` and the lane asks for `{effort}`")
            if effort != now("thought_level") and effort not in offered("thought_level"):
                raise Refused(f"this server does not offer the effort `{effort}` "
                              f"(offered: {', '.join(offered('thought_level')) or 'none named'})")
            required["thought_level"] = effort
        current = {name: now(name) for name in options}
        for name, value in required.items():
            if current.get(name) == value:
                continue
            answer = self.call("session/set_config_option", {
                "sessionId": self.session, "configId": name, "value": value})
            # The server's own account of every option after this change, which replaces
            # what was known: a later change may have undone an earlier one.
            current = {str(o.get("id")): str(o.get("currentValue") or "")
                       for o in (answer.get("configOptions") or []) if isinstance(o, dict)}
        # The whole of what was asked for, together, by the server's last word. An answer
        # that does not show a value in force (no options returned, another value, or one
        # selection reset by a later one) is not taken for it.
        missing = {name: current.get(name) for name, value in required.items()
                   if current.get(name) != value}
        if missing:
            raise Refused("asked for " + ", ".join(f"{k} `{required[k]}`" for k in missing)
                          + " and the server's final options do not show it in force ("
                          + ", ".join(f"{k} `{v}`" for k, v in missing.items())
                          + "); nothing was prompted")

    def serve(self) -> int:
        self.open()
        self.report("SessionStart", source="resume" if self.args.resume else "startup")
        with self.server:
            _store(self.record, {
                "schema": SCHEMA, "protocol": self.protocol, "session_id": self.session,
                "pid": os.getpid(), "started": _started(os.getpid()), "socket": self.address,
                "secret": self.secret,
                # The provider's server as it runs inside the boundary: the exact process,
                # the contract and its digest. Absent: this session has no boundary.
                **({"confined": self.confined} if self.confined is not None else {})})
            self.say(f"[hosted {self.protocol} session {self.session}] "
                     + ("resumed" if self.args.resume else "new")
                     + (f", provider confined under boundary {self.confined['digest'][:16]}"
                        if self.confined is not None else ""))
            self.give(self.args.message)
            try:
                return self.loop(self.server, self.secret)
            finally:
                pathlib.Path(self.address).unlink(missing_ok=True)
                if self.socket_dir:
                    try:
                        os.rmdir(self.socket_dir)
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


def _refusal(args: argparse.Namespace) -> int | None:
    """77 and its reason on stdout if this start is refused as such, else None."""

    dialect = {protocol: name for name, protocol in PROTOCOL.items()}[args.protocol]
    try:
        refuse_guarded(os.environ, dialect=dialect,
                       boundary=getattr(args, "boundary_record", None) is not None)
    except GuardRequired as error:
        print(f"hosted-session: refused: {error}", flush=True)
        return 77
    return None


def run(args: argparse.Namespace, provider: list[str]) -> int:
    refused = _refusal(args)
    if refused is not None:
        return refused
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
    except (Refused, BoundaryUnusable) as error:
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


def probe(args: argparse.Namespace, provider: list[str]) -> int:
    """A handshake, and a resume if an id is given, and nothing else: no session is made
    and no prompt is sent. With a boundary, the provider's server runs inside it exactly as
    for a lane, and its attestation is printed. For qualifying a real server with no model
    turn; a lane is never started this way."""

    refused = _refusal(args)
    if refused is not None:
        return refused
    host = None
    try:
        host = Host(args, provider)
        host.open(create=False)
        result: dict[str, Any] = {
            "initialized": True, "session_id": host.session or None,
            "resumed": bool(args.resume), "confined": host.confined, "prompts_sent": 0,
            "sessions_made": 0}
        code = 0
    except (Refused, BoundaryUnusable) as error:
        result, code = {"refused": str(error)}, 70
    finally:
        if host is not None:
            _end(host.child)
            host.server.close()
            pathlib.Path(host.address).unlink(missing_ok=True)
            if host.socket_dir:
                try:
                    os.rmdir(host.socket_dir)
                except OSError:
                    pass
    os.set_blocking(sys.stdout.fileno(), True)
    print(json.dumps(result, sort_keys=True), flush=True)
    return code


def send(args: argparse.Namespace) -> int:
    """Give one message to the host that owns this lane's session, and to no other."""

    try:
        known = json.loads(args.record.read_text(encoding="utf-8"))
        if known.get("schema") != SCHEMA or known.get("session_id") != args.session:
            raise ValueError("the hosted-session record is not this session's")
        if not known.get("started") or _started(int(known["pid"])) != known["started"]:
            raise ValueError("the host that owned this session is not running")
        with _connect(known["socket"], 40) as client:
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


def cover(args: argparse.Namespace) -> int:
    """Say what a boundary contract must cover for a hosted lane whose control directories
    are those given: nothing is guessed by the caller and nothing is random. The host's
    socket is ``<slot>-hosted.sock`` in the lane's own control directory."""

    reach = {str(root): _reachable(root) for root in trust_roots()}
    print(json.dumps({
        "deny_read": [str(pathlib.Path(item).resolve()) for item in args.control],
        "deny_write": [root for root, found in reach.items() if found is not None],
        "out_of_reach_already": [root for root, found in reach.items() if found is None],
        "socket": "<slot>-hosted.sock in the lane's control directory; never listed in "
                  "allowed_sockets",
    }, indent=1, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def bounded(command: argparse.ArgumentParser) -> None:
        command.add_argument("--boundary-record", type=json.loads, default=None,
                             help="the lane's boundary record; the provider's server then "
                                  "runs inside that boundary and this program outside it")
        command.add_argument("--boundary-role", choices=("coder", "tester"), default="")
        command.add_argument("--control", action="append", default=[], type=pathlib.Path,
                             help="a further directory of the lane's control records that "
                                  "the boundary must keep from the provider (repeatable)")

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
    bounded(runner)
    runner.add_argument("provider", nargs=argparse.REMAINDER)
    prober = sub.add_parser("probe", help="handshake (and resume, with --resume) only: no "
                            "session made, no prompt sent; prints one JSON object")
    prober.add_argument("--protocol", choices=sorted(PROTOCOL.values()), required=True)
    prober.add_argument("--record", type=pathlib.Path, required=True,
                        help="where a lane's host record would be; names the control "
                             "directory. Nothing is written to it")
    prober.add_argument("--workspace", type=pathlib.Path, required=True)
    prober.add_argument("--resume", default="")
    prober.add_argument("--unset", action="append", default=[])
    bounded(prober)
    prober.add_argument("provider", nargs=argparse.REMAINDER)
    sender = sub.add_parser("send", help="give a later message to the running host")
    sender.add_argument("--record", type=pathlib.Path, required=True)
    sender.add_argument("--session", required=True)
    sender.add_argument("--message", required=True)
    covering = sub.add_parser("trust-roots", help="print what a boundary contract must "
                              "cover for a hosted lane with these control directories")
    covering.add_argument("--control", action="append", default=[], type=pathlib.Path)
    checking = sub.add_parser("check-boundary", help="say whether a boundary record can be "
                              "relied on for a hosted lane with these control directories: "
                              "exit 0, or 70 with the reason; nothing is started")
    checking.add_argument("--workspace", type=pathlib.Path, required=True)
    bounded(checking)
    confining = sub.add_parser("confined-run", help="run one command of the provider's "
                               "program inside the lane's boundary (its account check, "
                               "version or help); the command follows --")
    confining.add_argument("--workspace", type=pathlib.Path, required=True)
    bounded(confining)
    confining.add_argument("provider", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command == "send":
        return send(args)
    if args.command == "trust-roots":
        return cover(args)
    if args.command == "check-boundary":
        try:
            confinement(args.boundary_record, args.boundary_role, args.workspace, args.control)
        except BoundaryUnusable as error:
            print(f"hosted-session: {error}", file=sys.stderr)
            return 70
        return 0
    if args.command == "confined-run":
        command = args.provider[1:] if args.provider[:1] == ["--"] else args.provider
        if not command or args.boundary_record is None:
            parser.error("confined-run needs --boundary-record and a command after --")
        try:
            wrapped = confined_argv(command, args.boundary_record, args.boundary_role,
                                    args.workspace, args.control)
        except BoundaryUnusable as error:
            print(f"hosted-session: {error}", file=sys.stderr)
            return 70
        os.chdir(args.workspace)
        os.execv(wrapped[0], wrapped)
    # A start that is refused as such is refused first, whatever else is wrong with the
    # command line: nothing about a malformed request turns a refusal into a usage error.
    refused = _refusal(args)
    if refused is not None:
        return refused
    provider = args.provider[1:] if args.provider[:1] == ["--"] else args.provider
    if not args.record.name.endswith(".json"):
        parser.error("--record names the host's record file and must end in .json "
                     "(its sibling files are named from it)")
    if not provider:
        parser.error("the provider's server command is required after --")
    if args.command == "probe":
        args.probing = True
        args.journal = args.record.with_name("probe-events.jsonl")
        args.message, args.model, args.effort = "", "", ""
        args.permissive, args.mcp_file = False, None
        return probe(args, provider)
    return run(args, provider)


if __name__ == "__main__":
    raise SystemExit(main())
