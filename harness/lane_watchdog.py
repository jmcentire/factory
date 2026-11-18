#!/usr/bin/env python3
"""lane_watchdog — the progress watchdog every tmux author lane runs beside.

``harness/watchdog.py`` watches the run (the signal deadline); this watches one lane.
``tmux_lane.sh`` starts it in a ``watch-<lane>`` window beside every lane it launches and
refuses the launch when it cannot; ``tmux_lane_message.sh`` starts a new life of it when it
resumes a lane. Its inputs are the lane's launch row, never the environment.

msg-r2 lost about $1,200 of metered credit to a Tester model that read for hours without
committing, and hours more to lanes that finished and sat idle because nothing noticed
(docs/practices/lessons-msg-r2-2026-10.md §2-§3). A launcher that leaves those to the
Validator's attention has no control at all, so the watcher, not a person, does three things:

- **Stall stop.** After a read allowance (default 15 minutes) a lane must commit within the
  stall window (default 20 minutes), measured from the allowance's end or its last commit,
  whichever is later. A lane with no commit in that window is stopped (SIGTERM to its pane's
  individually verified process tree), a ``refusal-lane-stall`` event is recorded, and the operator
  is woken. A lane
  waiting on its own unanswered FACTORY_QUESTION is waiting on the Validator, not stalled (the
  LIVE gate), so the window is held open while a question is pending.
- **Cap stop.** A metered lane runs under a spend cap. None of the agents we run exposes a live
  spend figure, so the enforced proxy is wall-clock time: spend is estimated as
  ``usd_per_hour x active time`` (the rate declared at launch) and the lane is stopped
  (``refusal-lane-cap``) when the estimate reaches the cap or the declared wall-clock cap
  passes, whichever comes first.
- **Turn-end wake.** A persistent lane's session reports each turn's end (a terminal session's
  stop report, a stream session's completed or failed turn). Each one is told to the Validator
  through its inbox (``lane-turn-ended``), whether or not a done line was seen: Cursor's stop
  report carries no text, so a Cursor Tester that finished was told to nobody (2026-11-17: the
  Validator sat idle 20.6 min until its own monitor timed out). The notice waits until the
  session's phase says the turn ended; notices are queued and retried at each check, whatever
  the watch's status, until written. A turn's end also restarts the stall window. A session
  that says it is waiting for input (Claude Code's idle prompt, a stream session's idle), while
  its own wrapper and worker are seen running, is waiting for the Validator, not stalled: the
  stall rule, review pause and wrap-up leave it alone, and a review that fell due meanwhile is
  not owed; the caps still apply. A stop report alone is not that word (the session may
  continue past it), and Cursor, Codex and Antigravity sessions send no other, so such a lane is
  stall-stopped a full window after its last turn end unless it printed a done line in that
  turn, and its notice says so.
- **Completion wake.** An agent response may claim ``__LANE_DONE__ <slot> commits=<n>``.
  Only a matching successful wrapper exit receipt, a confirmed dead pane and absence of the
  observed process tree make that claim terminal. Tool output is not a response, and an exit
  without a claim is only ``exited``. This is not proof against children escaping between polls.

Two more apply to a lane whose session can be resumed (a Codex exec thread, or the session a
registry harness streams: ``RESUMABLE_AGENTS``), because both end the live turn and continue the
same session through ``tmux_lane_message.sh``:

- **Wrap-up before the cap.** ``wrap_s`` before the wall-clock cap the live turn is interrupted
  and a generated ``FACTORY_WRAP_UP`` notice resumes the thread, so the lane spends its last
  minutes leaving the work building and committed instead of being stopped mid-edit. The cap stop
  still follows at the cap. The notice is delivered by ``tmux_lane_message.sh`` started in its own
  session with the environment the launch recorded (``<slot>-delivery.env``, owner-only), because
  the watcher's own environment is scrubbed; a lane with no such record keeps the plain cap stop.
  A notice that does not start a new life within five minutes ends the lane as capped.
- **Progress review pause.** Every ``review_every_s`` of active work a Coder lane is paused (its
  turn ended, its pane kept) and the Validator is woken (``lane-review-due``) to freeze and review
  it against the task specification and resume it with a ``FACTORY_REVIEW``. Paused time does
  not count toward the cap, and the stall window carries across the pause rather than starting
  over; a review still missing after ``review_wait_s`` wakes the Validator again
  (``lane-review-overdue``). A new round launched over a paused lane closes it as
  ``superseded`` (``close``).

``remaining`` prints a lane's time left on its cap; the lane protocol tells the lane the command.

Commits are read from the lane repository's reflog *file*, never by running git: the repository
is agent-owned once the agent starts, and host git there would execute the agent's
configuration. Every lane's end appends one row to ``tmux-lanes/lane-output.jsonl`` (commits,
time to first commit, wall time, spend, commits per dollar; ``lane_watchdog.py report``). A
qualification probe (``tmux_lane.sh <run> <role> qualify``) also appends its pass or fail to
the runs directory's ``lane-qualifications.jsonl``, which a metered launch requires.

Waking the operator writes one row to the run's ``events.jsonl`` (``wake: true``) and one to
``tmux-lanes/wake.jsonl`` (the file a Validator's monitor tails), prints a ``FACTORY_WAKE`` line
in the watcher's window, and flashes a tmux message to the run's session.
"""

from __future__ import annotations

import argparse
import datetime
import decimal
import json
import os
import pathlib
import re
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from typing import Any


class LaneWatchError(RuntimeError):
    pass


def _load_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


LANE_WATCH_SCHEMA = "factory-lane-watch/1"
LANE_OUTPUT_SCHEMA = "factory-lane-output/1"
QUALIFICATION_SCHEMA = "factory-lane-qualification/1"
LANE_FINAL = frozenset({"done", "exited", "stalled", "capped", "superseded"})
DEFAULT_READ_ALLOWANCE_S = 15 * 60
DEFAULT_STALL_WINDOW_S = 20 * 60
DEFAULT_POLL_S = 30
# How long a watcher whose lane is finished stays to deliver turn-end notices still owed.
TURN_NOTICE_DRAIN_S = 60 * 60
# Wall-clock time beyond awake time between two checks is the machine asleep, not the lane idle.
SLEEP_TOLERANCE_S = 60.0
DEFAULT_REVIEW_WAIT_S = 15 * 60
# A paused review or a wrap-up notice needs room to be useful: no review is taken this close to
# the wrap-up, and an undelivered wrap-up ends the lane after this long.
REVIEW_HEADROOM_S = 5 * 60
WRAP_DELIVERY_S = 5 * 60
# An unanswered review is a stuck required effect: the overdue signal re-emits with its age every
# review wait, and after this many waits the pause emits expired once. The lane stays paused, so a
# late review still resumes it; nothing about the lane is destroyed.
REVIEW_EXPIRY_WAITS = 4
# Lanes whose ended turn this watcher may continue with a lane message (wrap-up, review pause).
# The agent registry owns that fact (harness/agents.py, `describe_lane`); this is the watcher's
# own copy, kept because the watcher imports nothing but the standard library, and it names
# exactly the lane agents the registry reports as resumable by the Factory. `cursor-agent`,
# `muse` and `agy` are not: this watcher never ends a session of any of them in order to
# continue it, and a launch offers no wrap-up or review pause for a lane the registry does
# not report resumable. (`cursor-agent` is what the registry calls restartable: a lane
# message to a Cursor session that has already ended restarts it. That is the message
# command's doing, after an end this watcher did not cause for that purpose.) It is a
# statement about what the Factory does, not about those programs, whose own resume argument
# the registry still builds.
RESUMABLE_AGENTS = frozenset({"codex", "codex-ollama", "codex-interactive", "claude",
                              "claude-ollama", "prime-agent"})
# The count ends the marker: some harnesses run the next message straight on with no newline
# ("commits=21Top finding"), and that is still a completion.
_DONE = re.compile(r"^\W*__LANE_DONE__\s+(\S+)\s+commits=\d+(?!\d)")
_REFLOG = re.compile(r"^([0-9a-f]{40,64}) ([0-9a-f]{40,64}) [^\t]*> (\d+) [+-]\d{4}\t(.*)$")
_MAX_REFLOG_BYTES = 4 * 1024 * 1024
_MAX_LOG_SCAN_BYTES = 1024 * 1024


def _bounded_tail(path: pathlib.Path, ceiling: int, offset: int = 0) -> tuple[bytes, int]:
    """Read at most ``ceiling`` bytes of a regular, non-symlinked file from ``offset`` (or its
    last ``ceiling`` bytes when ``offset`` is negative). Missing reads as empty."""

    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return b"", max(offset, 0)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            return b"", max(offset, 0)
        start = max(metadata.st_size - ceiling, 0) if offset < 0 else min(offset, metadata.st_size)
        os.lseek(descriptor, start, os.SEEK_SET)
        raw = os.read(descriptor, ceiling)
        return raw, start + len(raw)
    finally:
        os.close(descriptor)


def read_commits(repository: pathlib.Path, since: float) -> list[dict[str, Any]]:
    """The commits made in ``repository`` at or after ``since``, from ``.git/logs/HEAD``.

    No git process runs: the bytes are parsed. A symlinked ``.git`` path reads as no commits,
    so a lane cannot point its watcher at another repository's history."""

    git = repository / ".git"
    reflog = git / "logs" / "HEAD"
    if any(path.is_symlink() for path in (git, git / "logs", reflog)):
        return []
    raw, _ = _bounded_tail(reflog, _MAX_REFLOG_BYTES, -1)
    floor = int(since)
    commits: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        matched = _REFLOG.match(line)
        if matched is None or not matched.group(4).startswith("commit"):
            continue
        stamp = int(matched.group(3))
        if stamp >= floor:
            commits.append(
                {"sha": matched.group(2), "ts": stamp, "subject": matched.group(4)[:200]}
            )
    return commits


#: The phases that are a persistent session's own word that it waits for input
#: (persistent_lane.py): a stream session's ``idle``, a terminal session's idle prompt.
WAITING_PHASES = ("idle", "waiting-for-input")
#: ... and with a terminal session's stop report, the phases at which a turn ended. A stop report
#: alone is not proof that the session waits: it may continue past it (tmux_lane_message.sh
#: does not take it for idle either).
STOPPED_PHASES = (*WAITING_PHASES, "stop-reported")
#: Every phase a reported turn end leaves: a stream session's failed turn ends one too.
TURN_END_PHASES = (*STOPPED_PHASES, "failed")


def _turn_ends(native: Mapping[str, Any]) -> int | None:
    """How many turn ends a persistent session has reported: a terminal session's stop reports,
    or a stream session's completed turns. None for a lane with no persistent session."""
    for key in ("stop_reports", "turns"):
        value = native.get(key)
        if type(value) is int:
            return value
    return None


def _response_text(value: object) -> str | None:
    """Read known agent response fields, never arbitrary nested tool/prompt strings."""
    if not isinstance(value, dict):
        return None
    if value.get("type") == "result":
        text = value.get("result") if not value.get("is_error") else ""
    elif value.get("type") == "native-stop-report":
        # What a native terminal session said last when it reported a stop
        # (persistent_lane.run_terminal). A source for a claim, like every branch here.
        text = value.get("last_assistant_message")
    elif value.get("type") == "item.completed":
        item = value.get("item")
        if not isinstance(item, dict) or item.get("type") != "agent_message":
            return None
        text = item.get("text")
    elif value.get("payload_type") == "run.terminal.completed":
        payload = value.get("payload")
        text = payload.get("text") if isinstance(payload, dict) else None
    elif value.get("event") == "step_update":
        step = value.get("step_update")
        if not isinstance(step, dict) or step.get("step_type") != "agent_response":
            return None
        text = step.get("text_delta")
    elif value.get("type") == "message_end":
        message = value.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            return None
        parts = message.get("content")
        return "".join(p["text"] for p in parts if isinstance(p, dict)
                       and p.get("type") == "text" and isinstance(p.get("text"), str)) \
            if isinstance(parts, list) else ""
    else:
        return None
    return text if isinstance(text, str) else ""


def _done_in(text: str, *, slot: str = "") -> bool:
    return _claim_in(text, slot=slot) is True


def _claim_in(text: str, *, slot: str = "") -> bool | None:
    """A completion *claim*, from typed response fields.

    A claim alone never proves completion. Unknown event shapes fail closed; recursively
    searching event strings let a tool's fixture stop supervision of a still-running author.
    """
    return _claim_row(text, slot=slot)[0]


# The row persistent_lane.py's stream wrapper writes to the lane log as each turn starts.
_TURN_MARK = "persistent-turn-start"


def _claim_row(text: str, *, slot: str = "", turn: object = None
               ) -> tuple[bool | None, dict[str, Any] | None, object, object]:
    """``_claim_in``'s claim, with the event of the response it was read from, the turn
    (``turn_started_at``) marked before that response, and the turn marked last. ``turn`` is
    the one marked last before ``text``."""

    def claimed(line: str) -> bool:
        match = _DONE.match(line)
        return match is not None and (not slot or match.group(1) == slot)

    latest: bool | None = None
    event: dict[str, Any] | None = None
    marked = turn
    for line in text.splitlines():
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            latest, event = False, None  # an unreadable event cannot preserve an earlier claim
            continue
        if isinstance(value, dict) and value.get("type") == _TURN_MARK:
            started = value.get("turn_started_at")
            turn = started if isinstance(started, (int, float)) else None
            continue
        response = _response_text(value)
        if response is not None:
            latest, event = any(claimed(part) for part in response.splitlines()), value
            marked = turn
    return latest, event, marked, turn


def _epoch(stamp: object) -> float:
    if isinstance(stamp, (int, float)):
        return float(stamp)
    return datetime.datetime.fromisoformat(str(stamp)).timestamp()


def _iso(epoch: float) -> str:
    return datetime.datetime.fromtimestamp(epoch, datetime.UTC).isoformat(timespec="seconds")


def _read_rows(path: pathlib.Path) -> list[dict[str, Any]]:
    if path.is_symlink():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _append_row(path: pathlib.Path, row: Mapping[str, object]) -> None:
    payload = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _tmux(*arguments: str) -> subprocess.CompletedProcess[str]:
    socket = os.environ.get("FACTORY_TMUX_SOCKET")
    command = ["tmux", *(["-L", socket] if socket else []), *arguments]
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=15)


def process_snapshot() -> dict[int, dict[str, Any]] | None:
    """Read identities without command lines; failure is unknown, not an empty process table."""
    try:
        result = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,lstart=,stat=,time="], capture_output=True,
            text=True, check=True, timeout=10, env={**os.environ, "LC_ALL": "C", "TZ": "UTC"})
    except (OSError, subprocess.SubprocessError):
        return None
    rows: dict[int, dict[str, Any]] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 9 or not fields[0].isdigit() or not fields[1].isdigit():
            return None
        pid = int(fields[0])
        rows[pid] = {"pid": pid, "ppid": int(fields[1]), "started": " ".join(fields[2:7]),
                     "state": fields[7], "cpu": fields[8]}
    return rows or None


#: How long a new start waits for the previous owner of a slot to finish stopping. A wrapper
#: that was signalled passes the signal on, waits up to 5s for its program, kills it and waits
#: 5s more, then writes its exit record: this is a little more than that.
PREVIOUS_OWNER_WAIT_S = 15.0


def previous_owner(receipt: pathlib.Path,
                   snapshot: Mapping[int, Mapping[str, Any]] | None) -> list[dict[str, Any]] | None:
    """The processes a slot's process receipt names that are still running, each exactly that
    process: the recorded PID with the recorded start time, and not a zombie. ``[]`` when
    there is no receipt or none of them is running; ``None`` when it cannot be told (the
    receipt is unreadable or is not a receipt, or the process table could not be read).

    Why a start asks. A slot's exits journal is one file for every life of the slot, and its
    rows name no process. A wrapper writes its row last, after its program has ended, which
    can be seconds after its tmux window is gone. A new life started in that time would have
    the old life's exit row land beside its own start and be taken for it. Nothing here
    signals anything: the previous owner is only looked at.
    """

    if receipt.is_symlink():
        return None
    try:
        document = json.loads(receipt.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict) or document.get("schema") != "factory-worker-process/1":
        return None
    if snapshot is None:
        return None
    alive = []
    for role in ("wrapper", "worker"):
        identity = document.get(role)
        if (not isinstance(identity, dict) or type(identity.get("pid")) is not int
                or not isinstance(identity.get("started"), str)):
            return None
        row = snapshot.get(identity["pid"])
        if (row is not None and row.get("started") == identity["started"]
                and not str(row.get("state", "")).startswith("Z")):
            alive.append({"role": role, "pid": identity["pid"], "started": identity["started"]})
    return alive


def await_previous_owner(receipt: pathlib.Path, wait_s: float = PREVIOUS_OWNER_WAIT_S,
                         poll_s: float = 0.2) -> list[dict[str, Any]] | None:
    """Wait, for at most ``wait_s``, until the slot's previous owner has stopped. Returns what
    ``previous_owner`` last saw: ``[]`` when it is gone, the processes still running when the
    wait ran out, ``None`` when it cannot be told."""

    deadline = time.monotonic() + max(0.0, wait_s)
    while True:
        seen = previous_owner(receipt, process_snapshot())
        if not seen or time.monotonic() >= deadline:
            return seen
        time.sleep(poll_s)


def process_tree(snapshot: Mapping[int, Mapping[str, Any]],
                 identities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extend only matching recorded identities; keep orphans and never adopt a reused PID."""
    known = {(r["pid"], r["started"]): dict(r) for r in identities}
    live = {pid for pid, started in known if pid in snapshot
            and snapshot[pid]["started"] == started}
    while True:
        children = {pid for pid, row in snapshot.items() if row["ppid"] in live} - live
        if not children:
            break
        for pid in children:
            row = snapshot[pid]
            known[(pid, row["started"])] = {"pid": pid, "started": row["started"]}
        live.update(children)
    return list(known.values())


def signal_identity(identity: Mapping[str, Any], signum: int, *,
                    processes: Callable[[], dict[int, dict[str, Any]] | None] = process_snapshot,
                    send: Callable[[int, int], None] = os.kill) -> str:
    """Revalidate just before an individual signal; never signal a group or guessed PID.

    POSIX PID signalling still has a check-to-signal race: this is not a kernel process handle.
    Unknown observation refuses. A changed identity is exited, not a new signalling target.
    """
    table = processes()
    if table is None:
        raise OSError("process identity observation unavailable")
    pid = identity["pid"]
    row = table.get(pid)
    if row is None or row["started"] != identity["started"] or row["state"].startswith("Z"):
        return "exited"
    if pid <= 1 or pid == os.getpid():
        raise OSError("refusing self/system process signal")
    try:
        send(pid, signum)
    except ProcessLookupError:
        return "exited"
    return "sent"


def window_id(target: str,
              tmux: Callable[..., subprocess.CompletedProcess[str]] = _tmux) -> str | None:
    """The unique ``@id`` of ``run:window``, or None when no window has that exact name.

    tmux resolves a window name it cannot find to the session's current window, even with an
    exact-match ``=`` prefix, so a watcher addressing a vanished lane by name would read or stop
    another lane's pane. Targets already given as an id or index pass through.
    """
    session, sep, window = target.partition(":")
    if not sep or window.startswith("@") or window.isdigit():
        return target
    listed = tmux("list-windows", "-t", f"={session}", "-F", "#{window_name} #{window_id}")
    if listed.returncode != 0:
        return None
    for line in listed.stdout.splitlines():
        name, _, ident = line.partition(" ")
        if name == window.lstrip("="):
            return ident
    return None


def stop_pane(target: str, tmux: Callable[..., subprocess.CompletedProcess[str]] = _tmux, *,
              expected: Mapping[str, Any] | None = None,
              known: list[dict[str, Any]] | None = None) -> str:
    """SIGTERM only observed identities in the pane's tree; exit requires later observation."""

    resolved = window_id(target, tmux)
    if resolved is None:
        return f"window {target} not found; nothing to stop"
    shown = tmux("display-message", "-p", "-t", resolved, "#{pane_pid} #{pane_dead}")
    fields = shown.stdout.split()
    if shown.returncode != 0 or len(fields) != 2 or not fields[0].isdigit():
        return f"pane {target} not found; nothing to stop"
    if fields[1] == "1" and not known:
        return f"pane {target} had already exited"
    pid = int(fields[0])
    table = process_snapshot()
    if table is None or (fields[1] != "1" and pid not in table):
        raise OSError("pane process identity is unavailable")
    if fields[1] != "1" and expected is not None and (expected.get("pid") != pid
                                 or expected.get("started") != table[pid]["started"]):
        raise OSError("pane process no longer matches recorded identity")
    roots = list(known or [])
    if fields[1] != "1":
        roots.append({"pid": pid, "started": table[pid]["started"]})
    identities = process_tree(table, roots)
    # Refuse a replaced pane, even if its old process is still alive.
    again = tmux("display-message", "-p", "-t", resolved, "#{pane_pid} #{pane_dead}")
    if again.returncode or again.stdout.split() != fields:
        raise OSError("pane identity changed before stop")
    for identity in identities:
        signal_identity(identity, signal.SIGTERM)
    return f"SIGTERM requested for {len(identities)} observed identities of {target}"


def _delivery_env(path: pathlib.Path) -> dict[str, str] | None:
    """The environment the launch recorded for lane messages, or None when it is missing, a link,
    not a regular file or readable by anyone but its owner."""

    try:
        metadata = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
        return None
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep and name.isidentifier():
            env[name] = value
    return env


def spawn_delivery(argv: list[str], env_file: pathlib.Path, log: pathlib.Path) -> str:
    """Start a lane message in its own session, with the launch's recorded environment, its output
    in ``log``. The delivery resumes the lane and replaces this watcher's window, so it must not die
    with the watcher; and the watcher's scrubbed environment cannot load the run's context."""

    env = _delivery_env(env_file)
    if env is None:
        return "not started: the launch recorded no usable delivery environment"
    try:
        descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND
                             | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            process = subprocess.Popen(
                argv, env=env, stdin=subprocess.DEVNULL, stdout=descriptor,
                stderr=subprocess.STDOUT, start_new_session=True, cwd=str(env_file.parent),
            )
        finally:
            os.close(descriptor)
    except OSError as error:
        return f"not started: {error.strerror or error}"
    return f"started (pid {process.pid}, output in {log.name})"


class LaneWatch:
    """One lane's watcher. ``check`` is one observation; ``LANE_FINAL`` verdicts end it. A paused
    or wrapping lane keeps its watcher until the resumed turn starts a new life."""

    def __init__(
        self,
        *,
        root: pathlib.Path,
        slot: str,
        now: Callable[[], float] = time.time,
        stop: Callable[[str], str] = stop_pane,
        tmux: Callable[..., subprocess.CompletedProcess[str]] = _tmux,
        echo: Callable[[str], None] = print,
        deliver: Callable[[list[str]], str] | None = None,
        processes: Callable[[], dict[int, dict[str, Any]] | None] = process_snapshot,
        awake: Callable[[], float] | None = None,
    ) -> None:
        self.root = pathlib.Path(root)
        # A clock that stops while the machine sleeps (time.monotonic); None counts wall time only.
        self.awake = awake
        self.lanes = self.root / "tmux-lanes"
        self.slot = slot
        self.now = now
        self.stop = stop
        self.tmux = tmux
        self.echo = echo
        self.processes = processes
        self.deliver = deliver or (lambda argv: spawn_delivery(
            argv, self.lanes / f"{slot}-delivery.env", self.lanes / f"{slot}-wrapup.log"))
        self.journal = self.lanes / f"{slot}-launch.jsonl"
        self.state_path = self.lanes / f"{slot}-watch.json"
        self._wait_by: dict[str, float] = {}

    # -- inputs ------------------------------------------------------------------------------

    def launch(self) -> dict[str, Any]:
        active = [row for row in _read_rows(self.journal) if row.get("status") == "active"]
        if len(active) != 1:
            raise LaneWatchError(f"{self.journal} holds no single active launch to watch")
        return active[0]

    def _pending_question(self, role: str) -> bool:
        harness = str(pathlib.Path(__file__).resolve().parent)
        if harness not in sys.path:
            sys.path.insert(0, harness)
        try:
            from lane_dialogue import pending_questions

            return bool(pending_questions(self.root, role))
        except Exception:  # noqa: BLE001 - an unreadable journal must not disarm the watch
            return False

    def _scan_output(self, launch: Mapping[str, Any], state: dict[str, Any]
                     ) -> tuple[bool | None, dict[str, Any] | None, object]:
        log = pathlib.Path(str(launch.get("lane_log", "")))
        raw, offset = _bounded_tail(log, _MAX_LOG_SCAN_BYTES, int(state.get("log_offset", 0)))
        # Retain an offset, not a partial event: tool output can contain private author code
        # and must never be copied into the operator's watcher-status metadata.
        state.pop("log_fragment", None)
        start = offset - len(raw)
        if start < int(state.get("log_offset", 0)):
            state["completion_claim"] = False  # the log was truncated/replaced
            state["log_turn"] = None
        newline = raw.rfind(b"\n")
        if newline < 0:
            text = ""
            discard = bool(state.get("discard_log_fragment")) or len(raw) >= _MAX_LOG_SCAN_BYTES
            state["discard_log_fragment"] = discard
            if discard:
                state["completion_claim"] = False
            state["log_offset"] = offset if discard else start
        else:
            complete = raw[:newline + 1]
            if state.get("discard_log_fragment"):
                # An oversized event is unknown, not a suffix masquerading as a new event.
                complete = complete.partition(b"\n")[2]
                state["completion_claim"] = False
            state["discard_log_fragment"] = False
            state["log_offset"] = start + newline + 1
            text = complete.decode("utf-8", errors="replace")
        try:
            state["log_drained"] = int(state["log_offset"]) >= log.stat().st_size \
                and not state["discard_log_fragment"]
        except OSError:
            state["log_drained"] = False
        if launch.get("terminal"):
            # Screen text cannot distinguish the assistant from a displayed tool fixture.
            # Its wrapper can prove exit, but cannot certify an agent completion claim.
            return None, None, None
        claimed, event, marked, state["log_turn"] = _claim_row(
            text, slot=self.slot, turn=state.get("log_turn"))
        return claimed, event, marked

    def _exits(self, launch: Mapping[str, Any]) -> list[dict[str, Any]]:
        return _read_rows(pathlib.Path(str(launch.get("lane_exits", ""))))

    def _pane_exited(self, launch: Mapping[str, Any]) -> bool:
        """Require positive tmux evidence; lookup failure is not proof of process exit."""
        resolved = window_id(str(launch.get("tmux_target", "")), self.tmux)
        if resolved is None:
            return False
        shown = self.tmux("display-message", "-p", "-t", resolved, "#{pane_dead}")
        return shown.returncode == 0 and shown.stdout.strip() == "1"

    def _observe_processes(self, launch: Mapping[str, Any], state: dict[str, Any]) -> bool:
        """Retain observed descendants even after reparenting; never signal based on this list.

        This is observed-tree evidence, not confinement: a child that forks and detaches between
        polls may be unseen. A reused PID is a different identity, never a surviving lane child.
        """
        snapshot = self.processes()
        if snapshot is None:
            state["process_observation"] = "unavailable"
            return False
        resolved = window_id(str(launch.get("tmux_target", "")), self.tmux)
        if resolved is None or state.get("observed_window_id", resolved) != resolved:
            state["process_observation"] = "pane unavailable or replaced"
            return False
        state["observed_window_id"] = resolved
        known = list(state.get("process_identities", []))
        if not known:
            shown = self.tmux("display-message", "-p", "-t", resolved, "#{pane_pid}")
            if shown.returncode != 0:
                return False
            pane_pid = shown.stdout.strip()
            if pane_pid and not pane_pid.isdigit():
                return False
            # tmux may clear pane_pid after an early exit. Only positive dead-pane
            # evidence below can admit that absence; a live/unknown pane still refuses.
            root = snapshot.get(int(pane_pid)) if pane_pid else None
            if root is not None:
                identity = {"pid": root["pid"], "started": root["started"]}
                state["pane_identity"] = identity
                known.append(identity)
            elif not self._pane_exited(launch):
                return False
        live = {row["pid"] for row in known if row["pid"] in snapshot
                and snapshot[row["pid"]]["started"] == row["started"]}
        while True:
            children = {pid for pid, row in snapshot.items() if row["ppid"] in live} - live
            if not children:
                break
            known.extend({"pid": pid, "started": snapshot[pid]["started"]} for pid in children)
            live.update(children)
        state["process_identities"] = known
        state["surviving_processes"] = [row for row in known if row["pid"] in live
                                       and snapshot[row["pid"]]["started"] == row["started"]
                                       and not snapshot[row["pid"]]["state"].startswith("Z")]
        state["process_observation"] = "observed"
        state["process_observed_ts"] = self.now()
        return True

    def _exit_verified(self, launch: Mapping[str, Any], state: dict[str, Any]) -> bool:
        observed = self._observe_processes(launch, state)
        self._write_state(state)
        return observed and not state.get("surviving_processes") and self._pane_exited(launch)

    def _native_turn(self) -> dict[str, Any]:
        """A persistent lane's native turn state (persistent_lane.py), or {} for any other lane."""
        path = self.lanes / f"{self.slot}-persistent.json"
        return {} if path.is_symlink() else _load_json(path)

    def _notice_turn_end(self, launch: Mapping[str, Any], state: dict[str, Any],
                         native: Mapping[str, Any], summary: str, waiting: bool,
                         allowance_end: float) -> None:
        """Tell the Validator each turn end a persistent lane's session reported.

        One notice per new count of turn ends, sent once the session's phase says the turn
        ended; a count seen while the session is in another phase waits for that. The count is
        noticed only when the notice is made. Notices are queued in order, each with its wake,
        and what could not be done is retried at each check (``_deliver_turn_notices``). The
        time this check saw the turn end restarts the stall window: the session's own
        ``stop_reported_at`` is set only for the first stop after a prompt. The notice gives
        the stall deadline unless ``waiting``, the stall rule's own exemption, holds: a window
        from the turn's end, or from ``allowance_end`` when the turn ended before the lane's read
        allowance did.
        """
        reported = _turn_ends(native)
        if reported is None:
            return
        noticed = state.get("turn_ends_noticed")
        if type(noticed) is not int or reported < noticed:
            state["turn_ends_noticed"] = reported  # an older watch record, or a new session
            return
        phase = native.get("phase")
        if reported == noticed or phase not in TURN_END_PHASES:
            return
        state["turn_ends_noticed"] = reported
        lane = str(launch.get("lane") or self.slot)
        claimed = self._claim_fresh(state, native)
        done = ("its done line was seen" if claimed else
                "no done line was seen in what its session reported (a Cursor stop report "
                "carries no text): read its window")
        ended = ("its session reported it is waiting for input" if phase in WAITING_PHASES else
                 "its session reported the turn failed" if phase == "failed" else
                 "its session reported the end of a response and may still continue on its own")
        detail = f"{ended} (seen {_iso(self.now())}); {done}; {summary}"
        if not waiting:
            window = float((launch.get("watch") or {}).get("stall_window_s",
                                                           DEFAULT_STALL_WINDOW_S))
            start = ("this turn end" if allowance_end <= self.now() else
                     f"its read allowance ends ({_iso(allowance_end)})")
            detail += (f"; unless it commits or is continued, it is stopped as stalled "
                       f"{window / 60:.0f} min after {start}")
        state["turn_end_seen_at"] = self.now()
        state.setdefault("turn_notices", []).append({
            "text": (f"FACTORY_LANE_TURN_ENDED {lane}: {detail}. A turn's end is not acceptance "
                     "of its work. Evaluate it, then continue the lane or close it."),
            "lane": lane, "failed": False, "wake": detail})
        self._deliver_turn_notices(launch, state)

    def _deliver_turn_notices(self, launch: Mapping[str, Any], state: dict[str, Any]) -> None:
        """Make each pending turn-end notice: its wake, then its inbox message, oldest first.

        Notices outlive the watch's life and status, so none is dropped. They are on record,
        with the inbox's end, before any is attempted, so a check that dies mid-way, or whose
        write failed after its row was committed (the directory sync), does not tell one twice.
        What could not be done is retried at the next check. A check waits on each lock (the
        event log's, the inbox's) for at most ``SUPERVISOR_WAIT_S`` in all, however many notices
        it makes, and each message not delivered is recorded once as undelivered.
        """
        notices = state.get("turn_notices")
        if not isinstance(notices, list) or not notices:
            return
        harness = str(pathlib.Path(__file__).resolve().parent)
        if harness not in sys.path:
            sys.path.insert(0, harness)
        import run_record
        from attention_gate import AttentionGateError

        for notice in notices:
            if "inbox_from" not in notice:
                notice["inbox_from"] = run_record.inbox_end(self.root, "validator")
        self._write_state(state)
        for notice in notices:
            if notice.get("wake") is None or getattr(self, "_events_failed", False):
                continue
            try:
                self._wake(launch, "lane-turn-ended", str(notice["wake"]), refusal=False,
                           wait_s=self._wait_left("events"))
            except (OSError, AttentionGateError):
                self._events_failed = True  # the event log stayed locked: woken at a later check
            else:
                notice["wake"] = None
        for notice in notices:
            if notice.get("sent") or getattr(self, "_inbox_failed", ""):
                continue  # in order: none after one that failed
            text = str(notice.get("text"))
            try:
                if not run_record.delivered(self.root, "validator", "host", text,
                                            since=int(notice["inbox_from"])):
                    run_record.message(self.root, "validator", "host", text,
                                       wait_s=self._wait_left("inbox"))
            except (OSError, ValueError) as error:
                self._inbox_failed = str(error) or type(error).__name__
            else:
                notice["sent"] = True
        failed = getattr(self, "_inbox_failed", "")
        if failed:
            self._undelivered(notices, failed)
        notices[:] = [notice for notice in notices
                      if not notice.get("sent") or notice.get("wake") is not None]

    def _wait_left(self, sink: str) -> float:
        """What is left of this check's one wait on ``sink``'s lock: ``SUPERVISOR_WAIT_S`` in all
        from the first write that waits on it, however many writes it makes."""
        import run_record

        by = self._wait_by.setdefault(sink, time.monotonic() + run_record.SUPERVISOR_WAIT_S)
        return max(0.0, by - time.monotonic())

    def _undelivered(self, notices: list[dict[str, Any]], error: str) -> None:
        """Record each message not delivered once as undelivered, through the run's event
        writer, within what is left of the check's wait on its lock."""
        from attention_gate import AttentionGateError, _append_jsonl

        for notice in notices:
            if notice.get("failed") or notice.get("sent") or getattr(self, "_events_failed", False):
                continue
            try:
                _append_jsonl(self.root / "events.jsonl", {
                    "ts": _iso(self.now()), "kind": "lane-turn-notice-undelivered",
                    "class": "lane", "source": "watchdog.py lane",
                    "detail": f"{notice.get('lane')}: {error}"[:500], "wake": False,
                }, wait_s=self._wait_left("events"))
            except (OSError, AttentionGateError):
                self._events_failed = True  # recorded at a later check
            else:
                notice["failed"] = True

    @staticmethod
    def _claim_turn(response: Mapping[str, Any] | None, marked: object) -> object:
        """The turn a done line is credited to, by its ``turn_started_at``; None for no turn.

        What the session's wrapper wrote to the lane log says (persistent_lane.py). A native
        session's stop report names the turn it ended and whether it can only be that turn's;
        one that cannot counts for no turn. Any other response counts for the turn marked
        before it in the log. A lane with no session record marks no turns and has none, so
        its done line counts.
        """
        if isinstance(response, Mapping) and response.get("type") == "native-stop-report":
            started = response.get("turn_started_at")
            return started if (response.get("bound") is True
                               and isinstance(started, (int, float))) else None
        return marked

    @staticmethod
    def _claim_fresh(state: Mapping[str, Any], native: Mapping[str, Any]) -> bool:
        """A done line counts for the turn it was printed in, not for the session's later turns."""
        return bool(state.get("completion_claim")) and (
            state.get("claim_turn_started_at") == native.get("turn_started_at"))

    def _session(self, native: Mapping[str, Any]) -> str:
        """``alive`` or ``dead`` on positive evidence that the session's own wrapper and worker
        run or do not; ``unknown`` when the record or the process table cannot tell.

        The identities are the persistent record's (persistent_lane.py checks the same ones);
        another process left in the lane's tree, such as an orphaned test run, is not the
        session."""
        identities = [native.get(key) for key in ("worker", "wrapper")]
        if not all(isinstance(identity, dict) and type(identity.get("pid")) is int
                   for identity in identities):
            return "unknown"
        snapshot = self.processes()
        if not snapshot:
            return "unknown"
        for identity in identities:
            row = snapshot.get(identity["pid"])  # type: ignore[index]
            if (row is None or row.get("started") != identity.get("started")  # type: ignore[union-attr]
                    or str(row.get("state", "Z")).startswith("Z")):
                return "dead"
        return "alive"

    def _resumable(self, launch: Mapping[str, Any]) -> bool:
        """A turn this watcher ends can be continued: a session was retained (a Codex thread, or
        a registry harness's session), and the launch recorded where the runs live and the
        environment a lane message needs."""

        thread = self.lanes / f"{self.slot}-thread-id"
        return (
            launch.get("agent") in RESUMABLE_AGENTS
            and not launch.get("terminal")
            and bool((launch.get("watch") or {}).get("runs_dir"))
            and thread.is_file()
            and not thread.is_symlink()
            and _delivery_env(self.lanes / f"{self.slot}-delivery.env") is not None
        )

    def _pane_live(self, launch: Mapping[str, Any]) -> bool:
        resolved = window_id(str(launch.get("tmux_target", "")), self.tmux)
        if resolved is None:
            return False
        shown = self.tmux("display-message", "-p", "-t", resolved, "#{pane_dead}")
        return shown.returncode == 0 and shown.stdout.strip() == "0"

    # -- state -------------------------------------------------------------------------------

    def _write_state(self, state: Mapping[str, Any]) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.state_path)

    def arm(self, *, new_life: bool = False, log_offset: int | None = None) -> dict[str, Any]:
        """Start this watcher's life, or continue it after the watcher itself restarted.

        ``new_life`` is a resumed lane turn (tmux_lane_message.sh): the earlier life's totals
        carry over, so caps count all of the lane's active time, and only exits after this
        point end the new life. ``log_offset`` is where the lane log stood when the turn was
        resumed: the new life reads only what that turn writes, never the earlier turn's done
        line that the previous life had not yet scanned."""

        launch = self.launch()
        prior = _load_json(self.state_path)
        current = self.now()
        if prior.get("status") == "stopping" and new_life:
            raise LaneWatchError("cannot resume a lane whose requested stop is unverified")
        if prior.get("status") in {"watching", "paused", "wrapping", "stopping"} and not new_life:
            return prior
        adopted = self._owed_elsewhere(launch)
        # A turn resumed after this watcher's own pause or wrap-up continues the same work: the
        # stall window carries on from where it stood, with no fresh read allowance.
        continued = prior.get("status") in {"paused", "wrapping"}
        earlier = float(prior.get("active_s", 0.0))
        if prior.get("status") == "watching":
            earlier = float(prior.get("active_s_before", 0.0)) + max(
                0.0, current - float(prior.get("life_started", current))
            )
        state: dict[str, Any] = {
            "schema_version": LANE_WATCH_SCHEMA,
            "slot": self.slot,
            "launch_ts": _epoch(launch["ts"]),
            "life_started": current,
            "active_s_before": earlier,
            "lives": int(prior.get("lives", 0)) + 1,
            # A first launch can finish before its watcher starts. Those exits belong
            # to this life; only resumed/rearmed lives exclude earlier receipts.
            "exits_baseline": len(self._exits(launch)) if new_life or prior else 0,
            "log_offset": (
                int(log_offset)
                if new_life and log_offset is not None
                else int(prior.get("log_offset", 0))
            ),
            # The turn marked last before that offset (``_claim_row``), where it is kept.
            "log_turn": (None if new_life and log_offset is not None
                         else prior.get("log_turn")),
            "commits": list(prior.get("commits", [])),
            "hold_ts": None,
            "reviews": int(prior.get("reviews", 0)),
            "review_held_s": LaneWatch._held_until(prior, earlier),
            "wrapup_sent": bool(prior.get("wrapup_sent", False)),
            "continued": continued,
            "completion_claim": False,
            "turn_ends_noticed": self._turn_end_baseline(prior),
            "turn_notices": [*(notice for _, _, owed in adopted for notice in owed),
                             *(notice for notice in prior.get("turn_notices", [])
                               if isinstance(notice, dict))],
            "stall_carry_s": float(prior.get("stall_carry_s", 0.0)) if continued else 0.0,
            "status": "watching",
        }
        self._write_state(state)
        # Only once this record holds them: a check that dies in between leaves them in both,
        # and the inbox's own record keeps either from telling one twice.
        for state_path, other, owed in adopted:
            other["turn_notices"] = [notice for notice in other["turn_notices"]
                                     if notice not in owed]
            other["turn_notices_to"] = self.slot
            temporary = state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(other, indent=1, sort_keys=True) + "\n",
                                 encoding="utf-8")
            temporary.replace(state_path)
        return state

    def _owed_elsewhere(self, launch: Mapping[str, Any]
                        ) -> list[tuple[pathlib.Path, dict[str, Any], list[dict[str, Any]]]]:
        """The turn-end notices this lane's finished rounds still owe, which a new life takes
        over (``arm``): each round's record, and its notices for this lane.

        A new round replaces the lane's watcher (tmux_lane.sh), which may still have been
        delivering notices after its round finished (``run``). They move with what was already
        done for each, so none is stranded and none is told twice: a message already in the
        inbox is found from its retry offset. The finished round's record then keeps none and
        names the slot that took them."""
        lane = launch.get("lane")
        adopted: list[tuple[pathlib.Path, dict[str, Any], list[dict[str, Any]]]] = []
        if not isinstance(lane, str) or not lane:
            return adopted
        for state_path in sorted(self.lanes.glob("*-watch.json")):
            if state_path == self.state_path:
                continue
            state = _load_json(state_path)
            notices = state.get("turn_notices")
            if state.get("status") not in LANE_FINAL or not isinstance(notices, list):
                continue
            owed = [notice for notice in notices
                    if isinstance(notice, dict) and notice.get("lane") == lane]
            if owed:
                adopted.append((state_path, state, owed))
        return adopted

    def _turn_end_baseline(self, prior: Mapping[str, Any]) -> int:
        """The turn ends already told when a life starts.

        A later life carries the count its predecessor told, so a turn that a resumed session
        ends before this watcher arms is still told. A slot's first watch has told none: its
        session can end a turn before the watcher arms (the launch is recorded only after the
        session starts). A watch record from before turn-end notices takes what is there as
        told."""
        noticed = prior.get("turn_ends_noticed")
        if type(noticed) is int:
            return noticed
        if not prior:
            return 0
        return _turn_ends(self._native_turn()) or 0

    # -- effects -----------------------------------------------------------------------------

    def _wake(self, launch: Mapping[str, Any], kind: str, detail: str, *, refusal: bool,
              wait_s: float | None = None) -> None:
        """Record a wake for the run. With ``wait_s``, an event log another writer holds longer
        than that raises AttentionGateError before any of the wake is done."""
        harness = str(pathlib.Path(__file__).resolve().parent)
        if harness not in sys.path:
            sys.path.insert(0, harness)
        from attention_gate import _append_jsonl, append_refusal_event

        message = f"{self.slot}: {detail}"
        if refusal:
            append_refusal_event(self.root, kind=kind, source="watchdog.py lane",
                                 detail=message, exit_code=70)
        else:
            _append_jsonl(self.root / "events.jsonl", {
                "ts": _iso(self.now()), "kind": kind, "class": "lane",
                "source": "watchdog.py lane", "detail": message[:4096], "wake": True,
            }, wait_s=wait_s)
        _append_row(self.lanes / "wake.jsonl", {
            "ts": _iso(self.now()), "kind": kind, "slot": self.slot, "detail": detail[:4096],
        })
        self.echo(f"FACTORY_WAKE {kind} {message}")
        session = str(launch.get("tmux_target", "")).split(":", 1)[0]
        if session:
            try:
                self.tmux("display-message", "-t", session, f"FACTORY_WAKE {kind} {message}"[:200])
            except (OSError, subprocess.SubprocessError):
                pass

    def _finish(self, launch: Mapping[str, Any], state: dict[str, Any], status: str,
                active: float, spend: decimal.Decimal | None) -> dict[str, Any]:
        state["status"] = status
        state["ended_ts"] = self.now()
        state["active_s"] = active
        commits = state["commits"]
        first = (min(c["ts"] for c in commits) - state["launch_ts"]) if commits else None
        output: dict[str, Any] = {
            "schema_version": LANE_OUTPUT_SCHEMA,
            "ts": _iso(self.now()),
            "slot": self.slot,
            "lane": launch.get("lane"),
            "role": launch.get("role"),
            "agent": launch.get("agent"),
            "model": launch.get("model"),
            "outcome": status,
            "commits": len(commits),
            "time_to_first_commit_s": first,
            "wall_s": round(active, 1),
            "spend_usd": None if spend is None else str(spend.quantize(decimal.Decimal("0.01"))),
            "spend_basis": "declared-usd-per-hour-x-wall-clock" if spend is not None
            else "no-declared-rate",
            "commits_per_usd": (
                None if not spend else round(len(commits) / float(spend), 3)
            ),
        }
        _append_row(self.lanes / "lane-output.jsonl", output)
        qualify = launch.get("qualify")
        if isinstance(qualify, Mapping):
            reasons = []
            minimum = int(qualify.get("min_commits", 1))
            limit = float(qualify.get("first_commit_s", DEFAULT_READ_ALLOWANCE_S))
            if len(commits) < minimum:
                reasons.append(f"{len(commits)} commit(s), below the {minimum} required")
            if first is None or first > limit:
                reasons.append(
                    "no commit" if first is None
                    else f"first commit after {first:.0f}s, past the {limit:.0f}s limit"
                )
            verdict = "fail" if reasons else "pass"
            ledger = pathlib.Path(str(qualify.get("ledger") or self.lanes / "qualifications.jsonl"))
            _append_row(ledger, {
                "schema_version": QUALIFICATION_SCHEMA, "ts": _iso(self.now()),
                "slot": self.slot, "role": launch.get("role"), "agent": launch.get("agent"),
                "model": launch.get("model"), "verdict": verdict, "reasons": reasons,
                "outcome": status, "commit_count": len(commits),
                "time_to_first_commit_s": first, "wall_s": round(active, 1),
                "spend_usd": output["spend_usd"],
            })
            state["qualification"] = verdict
            output["qualification"] = verdict
        self._write_state(state)
        return output

    # -- the check ---------------------------------------------------------------------------

    def _request_end(self, launch: Mapping[str, Any], state: dict[str, Any], status: str,
                     kind: str, detail: str) -> str:
        # Persist intent before signalling: a crash must not erase the required stop or make
        # a subsequent question/completion claim reopen this lane. No repeat blind signalling.
        self._observe_processes(launch, state)
        state.update(status="stopping", stop_outcome=status, stop_kind=kind,
                     stop_detail=detail, stop_requested_ts=self.now())
        self._write_state(state)
        try:
            target = str(launch.get("tmux_target", ""))
            if self.stop is stop_pane:
                if not state.get("pane_identity"):
                    raise OSError("no recorded pane identity; refusing signal")
                response = stop_pane(target, self.tmux, expected=state["pane_identity"],
                                     known=state.get("process_identities", []))
            else:
                response = self.stop(target)
        except (OSError, subprocess.SubprocessError) as error:
            response = f"stop failed: {error}"
        state["stop_response"] = response
        self._write_state(state)
        self._wake(launch, "lane-stop-requested", f"{detail}; {response}; exit unverified",
                   refusal=False)
        return self._stopping(launch, state)

    def _stopping(self, launch: Mapping[str, Any], state: dict[str, Any]) -> str:
        """A sent signal is not an acknowledged stop. Keep watching until exit is observed."""
        if not self._exit_verified(launch, state):
            return "stopping"
        active = float(state["active_s_before"]) + max(
            0.0, self.now() - float(state["life_started"]))
        rate = (launch.get("caps") or {}).get("usd_per_hour")
        spend = decimal.Decimal(str(rate)) * decimal.Decimal(str(active)) / decimal.Decimal(3600) \
            if rate is not None else None
        state["stop_verified_ts"] = self.now()
        status = str(state["stop_outcome"])
        if status in {"paused", "wrapping"}:
            state.update(status=status, active_s=active)
            if status == "paused":
                state.update(paused_ts=self.now(), reviews=int(state.get("reviews", 0)) + 1,
                             review_overdue_count=0, review_expired=False)
                self._write_state(state)
                run = launch.get("run_id", "<run>")
                self._wake(launch, "lane-review-due", f"exit verified; {state['stop_detail']}; "
                           f"tmux_lane_message.sh {run} validator {self.slot} review "
                           "--verdict on-track|redirect --review-file <file>",
                           refusal=False)
            else:
                # Publish the verified state before delivery can resume this session. A crash
                # before/inside delivery leaves an unresumed wrap-up, never a fabricated send.
                state["wrapup_ts"] = self.now()
                self._write_state(state)
                started = self.deliver(list(state["wrapup_argv"]))
                self._wake(launch, "lane-wrap-up", f"exit verified; wrap-up {started}",
                           refusal=False)
                if started.startswith("not started"):
                    self._finish(launch, state, "capped", active, spend)
                    return "capped"
            return status
        output = self._finish(launch, state, status, active, spend)
        self._wake(launch, str(state["stop_kind"]),
                   f"lane exit verified: {state['stop_detail']}"
                   + (f"; qualification {output['qualification']}" if "qualification" in output
                      else ""), refusal=True)
        return status

    def check(self) -> str:
        launch = self.launch()
        state = _load_json(self.state_path)
        self._inbox_failed, self._events_failed = "", False
        self._wait_by = {}
        if state.get("turn_notices"):
            self._deliver_turn_notices(launch, state)
            self._write_state(state)
        if state.get("status") in LANE_FINAL:
            return str(state["status"])
        if state.get("status") == "stopping":
            return self._stopping(launch, state)
        if state.get("status") == "paused":
            return self._paused(launch, state)
        if state.get("status") == "wrapping":
            return self._wrapping(launch, state)
        if state.get("status") != "watching":
            state = self.arm()
        self._observe_processes(launch, state)
        current = self.now()
        if self.awake is not None:
            awake = self.awake()
            if state.get("last_check") is not None and state.get("last_awake") is not None:
                asleep = (current - float(state["last_check"])) - (
                    awake - float(state["last_awake"]))
                if asleep > SLEEP_TOLERANCE_S:
                    # Neither work nor stall: caps and the stall clock both skip it.
                    state["life_started"] = float(state["life_started"]) + asleep
                    state["hold_ts"] = current
            state["last_check"], state["last_awake"] = current, awake
        active = float(state["active_s_before"]) + max(0.0, current - float(state["life_started"]))
        watch = launch.get("watch") or {}
        caps = launch.get("caps") or {}
        allowance = float(watch.get("read_allowance_s", DEFAULT_READ_ALLOWANCE_S))
        window = float(watch.get("stall_window_s", DEFAULT_STALL_WINDOW_S))

        known = {commit["sha"] for commit in state["commits"]}
        for commit in read_commits(pathlib.Path(str(launch["repository"])), state["launch_ts"]):
            if commit["sha"] not in known:
                state["commits"].append(commit)
                known.add(commit["sha"])
        pending = self._pending_question(str(launch.get("lane") or launch.get("role", "")))
        if pending:
            state["hold_ts"] = current
        claimed, response, marked = self._scan_output(launch, state)
        native = self._native_turn()
        if claimed is not None:
            state["completion_claim"] = claimed
            if claimed:
                state["claim_turn_started_at"] = self._claim_turn(response, marked)
        exits = [row for row in self._exits(launch)[int(state["exits_baseline"]):]
                 if row.get("schema_version") == "factory-lane-exit/1"
                 and row.get("slot") == self.slot and type(row.get("rc")) is int
                 and type(row.get("stopped_by_signal")) is bool]

        rate = caps.get("usd_per_hour")
        spend = (
            decimal.Decimal(str(rate)) * decimal.Decimal(str(active)) / decimal.Decimal(3600)
            if rate is not None else None
        )
        summary = (
            f"{len(state['commits'])} commit(s), {active / 60:.1f} min"
            + (f", ~${spend:.2f} spent" if spend is not None else "")
        )
        if exits and state.get("log_drained") and self._exit_verified(launch, state):
            successful = exits[-1]["rc"] == 0 and not exits[-1]["stopped_by_signal"]
            status = "done" if self._claim_fresh(state, native) and successful else "exited"
            rc = f" rc={exits[-1]['rc']}"
            output = self._finish(launch, state, status, active, spend)
            self._wake(
                launch, f"lane-{status}",
                f"lane {status}{rc}: {summary}"
                + (f"; qualification {output['qualification']}" if "qualification" in output
                   else ""),
                refusal=False,
            )
            return status

        # The stall clock: time since the read allowance ended (none on a continued turn), the last
        # commit or the last pending question, plus what a pause carried over from before it.
        last_commit = max((float(c["ts"]) for c in state["commits"]), default=0.0)
        anchor = float(state["life_started"]) + (0.0 if state.get("continued") else allowance)
        # A persistent lane at its input boundary is waiting for the Validator, not stalled:
        # stopping it would only lose its session. The done line it printed in this turn says so
        # (as before), unless its session is known to have died; so does its session's own
        # report that it waits, while its session is seen running. A stop report alone does
        # not: the session may continue past it, so it only restarts the window below. A pause
        # or wrap-up has nothing to interrupt while it waits, and the time it waits is not work
        # a review is owed for: the Validator was told of the turn's end.
        session = (self._session(native) if native.get("phase") in STOPPED_PHASES
                   else "unknown")
        waiting = ((self._claim_fresh(state, native) and native.get("phase") in STOPPED_PHASES
                    and session != "dead")
                   or (native.get("phase") in WAITING_PHASES and session == "alive"))
        self._notice_turn_end(launch, state, native, summary, waiting, anchor)
        # A persistent lane's new native turn starts its own window, and so does its report of
        # a turn's end (persistent_lane.py): the Validator is told of it and has a full window.
        marks = [native.get(key) for key in ("turn_started_at", "stop_reported_at",
                                             "turn_completed_at")]
        marks.append(state.get("turn_end_seen_at"))
        turn = max((float(mark) for mark in marks if isinstance(mark, (int, float))), default=0.0)
        clock = max(anchor, last_commit, float(state["hold_ts"] or 0.0), turn)
        idle = current - clock + (float(state.get("stall_carry_s", 0.0)) if clock == anchor
                                  else 0.0)
        wall_cap = caps.get("wall_cap_s")
        wrap_s = float(watch.get("wrap_s") or 0)
        review_s = float(watch.get("review_every_s") or 0)
        self._held_for_reviews(state, active, "waiting", waiting)
        resumable = self._resumable(launch) and not waiting
        if (
            resumable and wall_cap is not None and wrap_s > 0 and not state.get("wrapup_sent")
            and float(wall_cap) - wrap_s <= active < float(wall_cap)
        ):
            return self._wrap_up(launch, state, current, active, float(wall_cap) - active,
                                 idle, summary)
        if (
            resumable and review_s > 0 and idle <= window
            and active - float(state.get("review_held_s", 0.0))
            >= review_s * (int(state.get("reviews", 0)) + 1)
            and (wall_cap is None or float(wall_cap) - active > wrap_s + REVIEW_HEADROOM_S)
        ):
            return self._pause_for_review(launch, state, current, active, idle, summary)

        reason = ""
        spend_cap = caps.get("spend_cap_usd")
        if wall_cap is not None and active >= float(wall_cap):
            reason = f"wall-clock cap of {float(wall_cap) / 60:.0f} min reached"
        elif spend is not None and spend_cap is not None and spend >= decimal.Decimal(
            str(spend_cap)
        ):
            reason = f"spend cap of ${spend_cap} reached (declared rate ${rate}/h)"
        if reason:
            return self._request_end(launch, state, "capped", "refusal-lane-cap",
                                     f"{reason}; {summary}")

        if not pending and not waiting and idle > window:
            return self._request_end(
                launch, state, "stalled", "refusal-lane-stall",
                f"no commit for {idle / 60:.0f} min past its {allowance / 60:.0f} min "
                f"read allowance and {window / 60:.0f} min window; {summary}")
        self._write_state(state)
        return "watching"

    # -- pausing and wrapping up: the turn ends, the thread is continued by a lane message ---------

    @staticmethod
    def _held_for_reviews(state: dict[str, Any], active: float, reason: str,
                          held: bool) -> None:
        """Time a lane is held by someone else (at a host dialog, waiting for the Validator) is
        not work a progress review is owed for: the review rule counts active time less
        ``review_held_s``. The active time from a check at which any reason held the lane to
        the next check is added to it once, however many reasons there are; ``held`` says
        whether ``reason`` holds it at this check."""
        if state.get("review_held_active") != active:
            state["review_held_s"] = LaneWatch._held_until(state, active)
            state["review_held_active"], state["review_held_by"] = active, {}
        state["review_held_by"][reason] = held

    @staticmethod
    def _held_until(state: Mapping[str, Any], active: float) -> float:
        """``review_held_s`` up to ``active``: with the time since the last check, when any
        reason held the lane at it. A new life carries it, as the next check would."""
        held = float(state.get("review_held_s", 0.0))
        last = state.get("review_held_active")
        if isinstance(last, (int, float)) and any((state.get("review_held_by") or {}).values()):
            held += max(0.0, active - float(last))
        return held

    def _pause_for_review(self, launch: Mapping[str, Any], state: dict[str, Any], current: float,
                          active: float, idle: float, summary: str) -> str:
        state["stall_carry_s"] = idle
        return self._request_end(launch, state, "paused", "lane-review-due",
                                 f"progress review required; {summary}")

    def _paused(self, launch: Mapping[str, Any], state: dict[str, Any]) -> str:
        wait = float((launch.get("watch") or {}).get("review_wait_s", DEFAULT_REVIEW_WAIT_S))
        waited = self.now() - float(state.get("paused_ts", self.now()))
        if not state.get("stop_verified_ts") or not self._exit_verified(launch, state):
            # Legacy/unverified or subsequently revived seats cannot turn a mandatory review
            # into permission to keep working. Hold delivery and surface uncertainty.
            state.update(status="stopping", stop_outcome="paused",
                         stop_kind="lane-review-due", stop_detail="review exit unverified")
            self._write_state(state)
            self._wake(launch, "lane-review-unverified", "review exit evidence no longer holds",
                       refusal=True)
            return "stopping"
        if state.get("review_expired"):
            return "paused"
        expiry = wait * REVIEW_EXPIRY_WAITS
        if waited > expiry:
            state["review_expired"] = True
            self._write_state(state)
            self._wake(
                launch, "lane-review-expired",
                f"paused {waited / 60:.0f} min with no progress review, past its "
                f"{expiry / 60:.0f} min expiry; the lane stays paused and does no work until a "
                "review resumes it",
                refusal=False,
            )
            return "paused"
        overdue = int(state.get("review_overdue_count", 0))
        if waited > wait * (overdue + 1):
            state["review_overdue_count"] = overdue + 1
            self._write_state(state)
            self._wake(
                launch, "lane-review-overdue",
                f"paused {waited / 60:.0f} min waiting for the Validator's progress review "
                f"(overdue notice {overdue + 1}; expires at {expiry / 60:.0f} min); the lane does "
                "no work until a review resumes it",
                refusal=False,
            )
        return "paused"

    def _wrap_up(self, launch: Mapping[str, Any], state: dict[str, Any], current: float,
                 active: float, remaining: float, idle: float, summary: str) -> str:
        minutes = max(1, round(remaining / 60))
        script = pathlib.Path(__file__).resolve().parent / "tmux_lane_message.sh"
        state["wrapup_argv"] = [
            str(script), str(launch.get("run_id", "")), "watchdog", self.slot, "wrapup",
            "--minutes", str(minutes), "--wait-idle", "30",
            "--runs", str((launch.get("watch") or {}).get("runs_dir", "")),
        ]
        state["wrapup_sent"] = True
        state["stall_carry_s"] = idle
        return self._request_end(launch, state, "wrapping", "lane-wrap-up", summary)

    def _wrapping(self, launch: Mapping[str, Any], state: dict[str, Any]) -> str:
        if self.now() - float(state.get("wrapup_ts", self.now())) <= WRAP_DELIVERY_S:
            return "wrapping"
        # Whatever happened to the notice, nothing runs unwatched past this point.
        return self._request_end(
            launch, state, "capped", "refusal-lane-cap",
            f"its wrap-up notice did not resume it within {WRAP_DELIVERY_S} s; "
            f"{len(state['commits'])} commit(s)")

    def run(self, sleep: Callable[[float], None] = time.sleep, *, new_life: bool = False,
            launch_wait_s: float = 120.0, log_offset: int | None = None) -> str:
        # tmux_lane.sh starts this window before it records the launch active; wait for it.
        waited = 0.0
        while True:
            try:
                self.launch()
                break
            except LaneWatchError:
                if waited >= launch_wait_s:
                    raise
                sleep(2.0)
                waited += 2.0
        self.arm(new_life=new_life, log_offset=log_offset)
        poll = float((self.launch().get("watch") or {}).get("poll_s", DEFAULT_POLL_S))
        # A final verdict ends supervision, not the turn-end notices still owed: each check
        # retries them first. The watcher stays for them up to TURN_NOTICE_DRAIN_S, or until a
        # new round of the lane replaces it and takes them over (``_owed_elsewhere``).
        drained = 0.0
        while True:
            verdict = self.check()
            if verdict in LANE_FINAL and (drained >= TURN_NOTICE_DRAIN_S
                                          or not _load_json(self.state_path).get("turn_notices")):
                return verdict
            sleep(poll)
            if verdict in LANE_FINAL:
                drained += poll


def close_superseded(root: pathlib.Path, lane: str, by: str,
                     now: Callable[[], float] = time.time, *,
                     tmux: Callable[..., subprocess.CompletedProcess[str]] = _tmux,
                     processes: Callable[[], dict[int, dict[str, Any]] | None] = process_snapshot,
                     ) -> list[str]:
    """Close every slot of ``lane`` left paused or wrapping when a new round replaces its window and
    watcher, so the slot still ends with its output row instead of reading as paused forever."""

    closed = []
    lanes = pathlib.Path(root) / "tmux-lanes"
    for state_path in sorted(lanes.glob("*-watch.json")):
        state = _load_json(state_path)
        slot = str(state.get("slot", ""))
        if state.get("status") not in {"paused", "wrapping", "stopping"} or not slot or slot == by:
            continue
        watch = LaneWatch(root=root, slot=slot, now=now, tmux=tmux, processes=processes)
        try:
            launch = watch.launch()
        except LaneWatchError:
            continue
        if (launch.get("lane") or launch.get("role")) != lane:
            continue
        if state.get("status") == "stopping" or not watch._exit_verified(launch, state):
            raise LaneWatchError(f"cannot supersede {slot}: stop/exit is unverified")
        active = float(state.get("active_s", 0.0))
        rate = (launch.get("caps") or {}).get("usd_per_hour")
        spend = (
            decimal.Decimal(str(rate)) * decimal.Decimal(str(active)) / decimal.Decimal(3600)
            if rate is not None else None
        )
        state["superseded_by"] = by
        watch._finish(launch, state, "superseded", active, spend)
        closed.append(slot)
    return closed


def remaining(root: pathlib.Path, slot: str, now: Callable[[], float] = time.time) -> str:
    """The lane's time left on its wall-clock cap, counted as the watchdog counts it: active time
    across resumed turns, paused time excluded."""

    watch = LaneWatch(root=root, slot=slot, now=now)
    caps = watch.launch().get("caps") or {}
    cap = caps.get("wall_cap_s")
    if cap is None:
        return "no wall-clock cap on this lane"
    state = _load_json(watch.state_path)
    active = float(state.get("active_s", 0.0))
    if state.get("status") in {"watching", "stopping"}:
        active = float(state.get("active_s_before", 0.0)) + max(
            0.0, now() - float(state.get("life_started", now())))
    left = max(0, int(float(cap) - active))
    return (f"time left: {left // 60}m {left % 60:02d}s of this lane's "
            f"{float(cap) / 60:.0f}m cap; the watchdog stops the lane at the cap")


def lane_report(root: pathlib.Path) -> list[dict[str, Any]]:
    """Every finished lane's output row, then each lane still being watched, live."""

    lanes = pathlib.Path(root) / "tmux-lanes"
    rows = _read_rows(lanes / "lane-output.jsonl")
    for state_path in sorted(lanes.glob("*-watch.json")):
        state = _load_json(state_path)
        if state.get("status") in {"watching", "paused", "wrapping", "stopping"}:
            rows.append({
                "slot": state.get("slot"), "outcome": state.get("status"),
                "commits": len(state.get("commits", [])),
            })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="lane progress watchdog")
    commands = parser.add_subparsers(dest="command", required=True)
    lane = commands.add_parser("lane", help="watch one launched lane until it ends")
    lane.add_argument("--root", type=pathlib.Path, required=True)
    lane.add_argument("--slot", required=True)
    lane.add_argument("--log-offset", type=int, default=None,
                      help="with --new-life: the lane log's size when the turn was resumed")
    lane.add_argument("--new-life", action="store_true",
                      help="a resumed lane turn: carry totals, watch only what follows")
    report = commands.add_parser("report", help="each lane's commits, wall time and spend")
    report.add_argument("--root", type=pathlib.Path, required=True)
    commits = commands.add_parser(
        "commits",
        help="a lane repository's commit count and latest commit, read from its reflog file; "
        "status.sh uses this because host git there would run the agent's configuration",
    )
    commits.add_argument("--repository", type=pathlib.Path, required=True)
    close = commands.add_parser(
        "close", help="close a lane's paused or wrapping slots that a new round supersedes")
    close.add_argument("--root", type=pathlib.Path, required=True)
    close.add_argument("--lane", required=True)
    close.add_argument("--by", required=True, help="the slot of the new round")
    clock = commands.add_parser("remaining", help="a lane's time left on its wall-clock cap")
    clock.add_argument("--root", type=pathlib.Path, required=True)
    clock.add_argument("--slot", required=True)
    owner = commands.add_parser(
        "previous-owner",
        help="wait until the processes a slot's process receipt names have stopped; exit 0 "
             "when they have (or there is no receipt), 75 when one is still running, 70 when "
             "it cannot be told. Nothing is signalled")
    owner.add_argument("--receipt", type=pathlib.Path, required=True)
    owner.add_argument("--wait-seconds", type=float, default=PREVIOUS_OWNER_WAIT_S)
    arguments = parser.parse_args(argv)
    if arguments.command == "previous-owner":
        seen = await_previous_owner(arguments.receipt, arguments.wait_seconds)
        if seen is None:
            print(f"lane-watchdog: cannot tell whether the previous owner of "
                  f"{arguments.receipt.name} has stopped (unreadable receipt or process "
                  "table)", file=sys.stderr)
            return 70
        if seen:
            print("lane-watchdog: still running: " + ", ".join(
                f"{row['role']} pid {row['pid']}" for row in seen), file=sys.stderr)
            return 75
        return 0
    if arguments.command == "close":
        try:
            for slot in close_superseded(arguments.root, arguments.lane, arguments.by):
                print(f"lane-watchdog: {slot} superseded by {arguments.by}")
        except LaneWatchError as error:
            print(f"lane-watchdog: {error}", file=sys.stderr)
            return 70
        return 0
    if arguments.command == "remaining":
        try:
            print(remaining(arguments.root, arguments.slot))
        except LaneWatchError as error:
            print(f"lane-watchdog: {error}", file=sys.stderr)
            return 70
        return 0
    if arguments.command == "report":
        for row in lane_report(arguments.root):
            print(json.dumps(row, sort_keys=True))
        return 0
    if arguments.command == "commits":
        made = read_commits(arguments.repository, 0)
        latest = made[-1] if made else None
        print(json.dumps({"count": len(made), "latest": latest}, sort_keys=True))
        return 0
    try:
        verdict = LaneWatch(root=arguments.root, slot=arguments.slot,
                            awake=time.monotonic).run(
            new_life=arguments.new_life, log_offset=arguments.log_offset
        )
    except LaneWatchError as error:
        print(f"lane-watchdog: {error}", file=sys.stderr)
        return 70
    print(f"lane-watchdog: {arguments.slot} {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
