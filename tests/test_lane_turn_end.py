"""A persistent lane's turn end wakes the Validator once; only a lane that is really waiting is
spared the stall rule.

Regression (2026-11-17): a replay's Validator sat idle 20.6 minutes after a Cursor Tester
finished. Cursor's stop report carries no text, so its ``__LANE_DONE__`` line is never seen,
and nothing told the Validator the lane had ended its turn.

Review of the first fix: exempting every lane whose phase looked idle from the stall rule was
unsafe. A stop report means the session ended a response, not that it will not continue
(harness/persistent_lane.py), so a session that continued and then hung was never stopped,
and a dead lane whose record still said "waiting" was watched forever.

Contract under test (the Validator's brief for fix/wake-validator-at-lane-turn-end, as
amended after review):

1. A persistent lane's reported turn ends are its record's integer ``stop_reports``, else its
   integer ``turns``. When that count exceeds the noticed count while the phase is
   ``stop-reported``, ``waiting-for-input``, ``idle`` or (a stream turn that failed)
   ``failed``, one ``lane-turn-ended`` wake and one Validator inbox message from ``host``
   (``FACTORY_LANE_TURN_ENDED <lane>: ...``) are given, and the count is noticed. A count seen
   in any other phase is not noticed. The notice names the stall deadline in the launch's
   whole minutes exactly when the stall exemption (5) does not hold.
2. Which turn a done line counts for comes from what the session's wrapper writes to the lane
   log. A stream wrapper marks each turn's input (``persistent-turn-start`` with the record's
   ``turn_started_at``) before its output; a response counts for the turn of the last mark
   before it, kept across checks and into a new life on the same log offset, cleared by a new
   offset or a truncated or replaced log; a response with no mark counts for no turn. A native
   terminal wrapper's ``native-stop-report`` row names the turn current when the stop arrived
   and whether it is ``bound`` to it (by generation, else: not the turn's first stop, or a
   first stop of a prompt that was not queued and does not end a continuation); its done line
   counts for that turn only when bound. A lane with no session record counts its done line
   as before. On a verified exit only a done line of the current turn makes the lane ``done``.
3. A slot's first watch has told no turn ends. Every later arm (a restart, a new life, a re-arm
   after a final status) carries the noticed count; a watch state without one takes the
   current count as noticed. A lower count is a new session.
4. Notices, each with its wake, are queued and on record in the watch state before anything
   is told: an undeliverable message or wake is retried at every check, whatever the watch
   status and into a new life, and given in order, exactly once, even across a watcher that
   died mid-delivery. A check waits on the events log's lock, and on the inbox's, for one
   ``run_record.SUPERVISOR_WAIT_S`` allowance each in all, never the watcher; a failed message
   is recorded once, through the run's event writer; a write that failed after its row was
   committed is not repeated, however much the inbox grew since. Each notice says when its
   turn end was seen. ``LaneWatch.run`` keeps delivering owed notices after a final verdict,
   for at most ``TURN_NOTICE_DRAIN_S``.
5. The stall clock restarts at the record's turn timestamps and at the check that announced a
   turn end. A lane is spared the stall stop, the review pause and the wrap-up only while (a)
   a done line counts for its current turn, its turn ended (not failed) and its session is not
   known dead, or (b) it says it waits for input and this check saw its session's own worker
   and wrapper alive. Time spent waiting is not work a review is owed for, in this life or the
   next (a new life carries the held time up to its start). The caps always apply.

The real ``LaneWatch`` runs against a synthetic run root. Doubles sit only at its declared
seams, as in tests/test_lane_controls.py: the clock, the tmux runner, the process table, the
stop and the delivery callables. Every persistent agent is launched with ``terminal: false``
(harness/lane_agent.py), as here. The tmux double answers each format it is asked for from one
modelled pane (window ``@1``, pane pid ``4242``, alive or dead); the process double reports the
pane's tree: its shell, the session wrapper and the worker under it, whose identities the
session record carries as persistent_lane.py writes them. The stop double does what a
delivered SIGTERM does: the pane dies and its processes leave the table. The tmux and delivery
recorders are evidence only of what the watcher itself asked for (T17). Section 8 runs the
real session wrappers with synthetic native programs.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from harness import attention_gate, lane_agent, lane_watchdog, run_record

T0 = 1_791_000_000.0
READ_ALLOWANCE_S = 900
STALL_WINDOW_S = 1200
# The unchanged stall boundary for a lane that has not committed: the window runs from the end
# of the read allowance (the rule tests/test_lane_controls.py documents for every watched lane).
STALL_AT = READ_ALLOWANCE_S + STALL_WINDOW_S
TARGET = "r1:tester"
PANE_PID = 4242
# The session's own processes, as persistent_lane.py records them (``ps`` lstart strings).
WRAPPER = {"pid": 5000, "started": "Tue Nov 17 09:59:58 2026"}
WORKER = {"pid": 5001, "started": "Tue Nov 17 09:59:59 2026"}
ORPHAN_PID = 6000
# A real process table is never empty: init and the watcher's own processes are always in it.
INIT = {1: {"pid": 1, "ppid": 0, "started": "Tue Nov 17 08:00:00 2026", "state": "S"}}
PREFIX = "FACTORY_LANE_TURN_ENDED "
WAITING_WORDS = "its session reported it is waiting for input"
STOP_WORDS = "its session reported the end of a response and may still continue on its own"
FAILED_WORDS = "its session reported the turn failed"
SAID = (WAITING_WORDS, STOP_WORDS, FAILED_WORDS)
DONE_SEEN = "its done line was seen"
DONE_UNSEEN = "no done line was seen"
CLOSING = ("A turn's end is not acceptance of its work. Evaluate it, then continue the lane or "
           "close it.")
DEADLINE_HEAD = "unless it commits or is continued"
DEADLINE = DEADLINE_HEAD + ", it is stopped as stalled {} min after this turn end"
ALLOWANCE_DEADLINE = (DEADLINE_HEAD
                      + ", it is stopped as stalled {} min after its read allowance ends ({})")
DONE_LINE = "Report written.\n__LANE_DONE__ tester commits=0"
RESUMABLE = {"runs_dir": "/runs", "wrap_s": 300}
REVIEWED = {"runs_dir": "/runs", "review_every_s": 1080}
# Input into a pane: any of these tmux commands would type or paste into a session.
TYPING = {"send-keys", "send", "paste-buffer", "pasteb", "load-buffer", "loadb", "set-buffer",
          "setb"}
DROP = object()  # a record field the session wrapper did not write


class Clock:
    def __init__(self) -> None:
        self.value = T0

    def __call__(self) -> float:
        return self.value


def _commit(repo: Path, at: float, name: str) -> None:
    (repo / name).write_text(name, encoding="utf-8")
    stamp = f"@{int(at)} +0000"
    env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(repo), "add", name], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", name], check=True, env=env)


def _process(identity: dict[str, Any], ppid: int, state: str = "S") -> dict[str, Any]:
    return {"pid": identity["pid"], "ppid": ppid, "started": identity["started"], "state": state}


def _tree() -> dict[int, dict[str, Any]]:
    """The pane's live process tree: its shell, the session wrapper and the worker under it."""
    shell = {"pid": PANE_PID, "started": "Tue Nov 17 09:59:57 2026"}
    return {
        **INIT,
        PANE_PID: _process(shell, 1),
        WRAPPER["pid"]: _process(WRAPPER, PANE_PID),
        WORKER["pid"]: _process(WORKER, WRAPPER["pid"], "Sl"),
    }


class Lane:
    """One watched author lane in a synthetic run root; ``tester`` is its slot."""

    def __init__(self, tmp_path: Path, *, record: dict[str, Any] | None,
                 agent: str = "cursor-agent", terminal: bool = False,
                 lane: str | None = "tester.r2", caps: dict[str, object] | None = None,
                 watch: dict[str, object] | None = None, thread: bool = False,
                 alive: bool = True, identities: bool = True) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.root = tmp_path / "run"
        self.lanes = self.root / "tmux-lanes"
        self.lanes.mkdir(parents=True)
        self.repo = tmp_path / "clone"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        self.log = self.lanes / "tester-lane.log"
        self.log.touch()
        self.exits = self.lanes / "tester-exits.jsonl"
        self.agent = agent
        self.identities = identities
        row: dict[str, object] = {
            "status": "active",
            "ts": datetime.datetime.fromtimestamp(T0, datetime.UTC).isoformat(),
            "run_id": "r1", "role": "tester", "slot": "tester",
            "agent": agent, "model": "fixture-model", "terminal": terminal,
            "repository": str(self.repo), "lane_log": str(self.log),
            "lane_exits": str(self.exits), "tmux_target": TARGET,
            "watch": {"read_allowance_s": READ_ALLOWANCE_S, "stall_window_s": STALL_WINDOW_S,
                      "poll_s": 30, **(watch or {})},
            "caps": caps or {}, "qualify": None,
        }
        if lane is not None:
            row["lane"] = lane
        (self.lanes / "tester-launch.jsonl").write_text(json.dumps(row) + "\n")
        if thread:
            (self.lanes / "tester-thread-id").write_text(
                "12345678-1234-4234-8234-123456789abc\n")
            delivery = self.lanes / "tester-delivery.env"
            delivery.write_text("PATH=/usr/bin:/bin\n")
            delivery.chmod(0o600)
        # The modelled pane and process table.
        self.window = True
        self.pane_dead = not alive
        self.snapshot_available = True
        self.stop_kills = True
        self.table: dict[int, dict[str, Any]] = _tree() if alive else dict(INIT)
        self.record_path = self.lanes / "tester-persistent.json"
        self.record: dict[str, Any] | None = None
        if record is not None:
            self.report(**record)
        self.clock = Clock()
        self.stops: list[str] = []
        self.tmux_calls: list[tuple[str, ...]] = []
        self.delivered: list[list[str]] = []
        self.said: list[str] = []
        self.watch = self._watch()
        self.watch.arm()

    # -- seams ---------------------------------------------------------------------------------

    def _watch(self) -> lane_watchdog.LaneWatch:
        return lane_watchdog.LaneWatch(
            root=self.root, slot="tester", now=self.clock, stop=self._stop, tmux=self._tmux,
            echo=self.said.append, deliver=self._deliver, processes=self._processes,
        )

    def _value(self, name: str) -> str:
        values = {"window_name": "tester", "window_id": "@1", "pane_id": "%1",
                  "pane_pid": str(PANE_PID), "pane_dead": "1" if self.pane_dead else "0"}
        return values.get(name, "")

    def _tmux(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        self.tmux_calls.append(arguments)
        if arguments and arguments[0] == "list-windows":
            out = "tester @1\n" if self.window else "validator @9\n"
            return subprocess.CompletedProcess(arguments, 0, out, "")
        formats = [argument for argument in arguments if "#{" in argument]
        if not formats:  # a flashed message, a kill: nothing to print
            return subprocess.CompletedProcess(arguments, 0, "", "")
        if not self.window:
            return subprocess.CompletedProcess(arguments, 1, "", "can't find window: @1")
        out = re.sub(r"#\{(\w+)\}", lambda match: self._value(match.group(1)), formats[-1])
        return subprocess.CompletedProcess(arguments, 0, out + "\n", "")

    def _processes(self) -> dict[int, dict[str, Any]] | None:
        if not self.snapshot_available:
            return None
        return {pid: dict(process) for pid, process in self.table.items()}

    def _stop(self, target: str) -> str:
        """A delivered SIGTERM: the pane dies and its processes leave the table."""
        self.stops.append(target)
        if self.stop_kills:
            self.pane_dead = True
            self.table = dict(INIT)
        return f"stopped {target}"

    def _deliver(self, argv: list[str]) -> str:
        self.delivered.append(argv)
        return "scheduled"

    # -- the lane ------------------------------------------------------------------------------

    def report(self, **fields: Any) -> None:
        """What the persistent session wrapper records: phase, counters, turn timestamps and
        (when ``identities``) its own and its worker's process identities. Its session started
        before the launch row was written."""
        base: dict[str, Any] = {
            "schema": "factory-persistent-lane/2", "root": str(self.root.resolve()),
            "slot": "tester", "agent": self.agent, "turn_started_at": T0 - 5,
        }
        if self.identities:
            base.update(wrapper=dict(WRAPPER), worker=dict(WORKER))
        record = dict(self.record or base)
        record.update(fields)
        record = {key: value for key, value in record.items() if value is not DROP}
        self.record = record
        self.record_path.write_text(json.dumps(record), encoding="utf-8")

    def _log_row(self, row: dict[str, Any]) -> None:
        with self.log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n")

    def respond(self, text: str) -> None:
        """One typed agent response in the lane log (a done line, when it carries one)."""
        self._log_row({"type": "result", "result": text})

    def agent_message(self, text: str) -> None:
        """A Codex agent message, which may come before its turn ends."""
        self._log_row({"type": "item.completed", "item": {"type": "agent_message", "text": text}})

    def mark(self, at: float) -> None:
        """What the stream wrapper writes as a turn's input is sent, before any of its output:
        the record's ``turn_started_at`` for that turn."""
        self._log_row({"type": "persistent-turn-start", "turn_started_at": T0 + at})

    def stop_report(self, text: str, *, bound: bool = True, turn: float | None = None) -> None:
        """A native stop report row as the terminal wrapper writes it: the record's current turn
        (or ``T0 + turn``) when the stop arrived, and whether the stop is bound to that turn."""
        assert self.record is not None
        started = self.record["turn_started_at"] if turn is None else T0 + turn
        self._log_row({"type": "native-stop-report", "turn_started_at": started,
                       "bound": bound, "stop_hook_active": False,
                       "last_assistant_message": text,
                       "session_id": "12345678-1234-4234-8234-123456789abc",
                       "source": "native-stop-hook"})

    def tool_output(self, text: str) -> None:
        """Tool output in the lane log: not a response, so it neither claims nor retracts."""
        with self.log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "tool_result", "content": text}) + "\n")

    def unobserve(self, how: str) -> None:
        """Make this check unable to see the session's own processes alive, one way."""
        if how == "snapshot-unavailable":
            self.snapshot_available = False
        elif how == "session-gone":  # the pane's shell is left
            self.table.pop(WRAPPER["pid"], None)
            self.table.pop(WORKER["pid"], None)
        elif how == "orphan-in-pane":  # a test run the session started outlives it
            self.unobserve("session-gone")
            self.table[ORPHAN_PID] = {"pid": ORPHAN_PID, "ppid": PANE_PID,
                                      "started": "Tue Nov 17 10:05:00 2026", "state": "R"}
        elif how == "wrapper-gone":
            self.table.pop(WRAPPER["pid"], None)
            self.table[WORKER["pid"]]["ppid"] = 1
        elif how == "worker-restarted":  # the pid now belongs to another process
            self.table[WORKER["pid"]]["started"] = "Tue Nov 17 11:00:00 2026"
        elif how == "worker-zombie":
            self.table[WORKER["pid"]]["state"] = "Z"
        elif how == "pane-dead":
            self.pane_dead, self.table = True, dict(INIT)
        else:
            raise AssertionError(how)

    def revive(self) -> None:
        """A resumed session: the pane and its tree run again."""
        self.pane_dead, self.table, self.snapshot_available = False, _tree(), True

    def kill(self) -> None:
        """The session and its pane are gone, and the table shows it."""
        self.pane_dead, self.table, self.snapshot_available = True, dict(INIT), True

    def restart(self) -> None:
        """A watcher process started afresh over the slot's existing watch state."""
        self.watch = self._watch()
        self.watch.arm()

    def at(self, seconds: float) -> str:
        self.clock.value = T0 + seconds
        return self.watch.check()

    # -- evidence ------------------------------------------------------------------------------

    def _rows(self, path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def events(self, kind: str) -> list[dict[str, Any]]:
        return [row for row in self._rows(self.root / "events.jsonl") if row.get("kind") == kind]

    def wakes(self, kind: str) -> list[dict[str, Any]]:
        return [row for row in self._rows(self.lanes / "wake.jsonl") if row.get("kind") == kind]

    def inbox(self) -> list[dict[str, Any]]:
        rows = self._rows(self.root / "validator" / "inbox.jsonl")
        return [row for row in rows if str(row.get("text", "")).startswith(PREFIX)]

    def texts(self) -> list[str]:
        return [str(row["text"]) for row in self.inbox()]

    def notices(self) -> int:
        """Notices given so far; each one is a wake event, a wake row and one inbox message."""
        events, wakes, inbox = (
            self.events("lane-turn-ended"), self.wakes("lane-turn-ended"), self.inbox())
        assert len(events) == len(wakes) == len(inbox), (events, wakes, inbox)
        return len(inbox)

    def undelivered(self) -> int:
        return len(self.events("lane-turn-notice-undelivered"))

    def stalls(self) -> int:
        return len(self.events("refusal-lane-stall"))

    def assert_nothing_typed(self) -> None:
        typed = [call for call in self.tmux_calls if call and call[0] in TYPING]
        assert typed == [] and self.delivered == []


def _strip(value: Any, sentinel: int) -> tuple[Any, int]:
    """``value`` without any mapping entry equal to ``sentinel``, and how many were dropped."""
    if isinstance(value, dict):
        kept: dict[str, Any] = {}
        dropped = 0
        for key, item in value.items():
            if type(item) is int and item == sentinel:
                dropped += 1
                continue
            kept[key], inner = _strip(item, sentinel)
            dropped += inner
        return kept, dropped
    if isinstance(value, list):
        pairs = [_strip(item, sentinel) for item in value]
        return [item for item, _ in pairs], sum(inner for _, inner in pairs)
    return value, 0


# A session of each kind: (agent, its counter, its turn-end timestamp, its mid-turn phase).
# Cursor and Claude Code are native terminal sessions (stop reports); Antigravity, Codex and
# Claude here are stream sessions (completed turns). Claude and Codex launches are resumable.
Kind = tuple[str, str, str, str]
CURSOR: Kind = ("cursor-agent", "stop_reports", "stop_reported_at", "prompt-observed")
CLAUDE: Kind = ("claude", "stop_reports", "stop_reported_at", "prompt-observed")
STREAM: Kind = ("agy", "turns", "turn_completed_at", "working")
CLAUDE_STREAM: Kind = ("claude", "turns", "turn_completed_at", "working")
CODEX_STREAM: Kind = ("codex-ollama", "turns", "turn_completed_at", "working")


def _stream(kind: Kind) -> bool:
    return kind[1] == "turns"


def _session(tmp_path: Path, kind: Kind, phase: str | None, count: int, *,
             marked: bool = True, **options: Any) -> Lane:
    """A lane of ``kind`` whose record shows ``phase`` (its mid-turn phase when None). A stream
    session's wrapper marks its first turn's input in the lane log (unless not ``marked``)."""
    agent, counter, _, mid = kind
    watched = Lane(tmp_path, record={"phase": phase or mid, counter: count}, agent=agent,
                   **options)
    if _stream(kind) and marked:
        watched.mark(-5)
    return watched


def _ends_turn(watched: Lane, kind: Kind, phase: str, count: int, at: float, *,
               done: bool = False, bound: bool = True) -> None:
    """The session ends a turn; with ``done`` its last response carries the done line (a stream
    response, or the text of the native stop report that ends the turn)."""
    _, counter, stamp, _ = kind
    if done and _stream(kind):
        watched.respond(DONE_LINE)
    elif done:
        watched.stop_report(DONE_LINE, bound=bound)
    watched.report(phase=phase, **{counter: count, stamp: T0 + at})


def _new_turn(watched: Lane, kind: Kind, at: float) -> None:
    """A new prompt reaches the session (persistent_lane clears the turn-end stamp); a stream
    wrapper marks the new turn's input in the lane log."""
    _, _, stamp, mid = kind
    watched.report(phase=mid, turn_started_at=T0 + at, **{stamp: None})
    if _stream(kind):
        watched.mark(at)


def _block(watched: Lane, how: str, elsewhere: Path) -> None:
    validator = watched.root / "validator"
    if how == "symlink":  # run_record.message refuses with ValueError
        validator.symlink_to(elsewhere, target_is_directory=True)
    elif how == "file":  # creating the inbox directory fails with OSError
        validator.write_text("", encoding="utf-8")
    else:  # the inbox is not a regular file: the append fails with OSError
        (validator / "inbox.jsonl").mkdir(parents=True)


def _unblock(watched: Lane, how: str) -> None:
    validator = watched.root / "validator"
    if how == "inbox-directory":
        (validator / "inbox.jsonl").rmdir()
    else:
        validator.unlink()


# -- 1. the notice ---------------------------------------------------------------------------


@pytest.mark.parametrize(("lane", "named"), [("tester.r2", "tester.r2"), (None, "tester")])
def test_a_cursor_stop_report_wakes_the_validator_once(
    tmp_path: Path, lane: str | None, named: str,
) -> None:
    """Falsifier: no notice for a Cursor stop report (the 20.6-minute idle Validator); a notice
    on every check of the same count; the lane misnamed; a notice claiming a done line Cursor
    never showed, calling a stop report waiting for input, sending the Validator to
    tmux_lane_message.sh, omitting the commits, the stall deadline, or that a turn's end is
    not acceptance; the notice delivered by typing into a pane."""

    watched = Lane(tmp_path, record={"phase": "prompt-observed", "stop_reports": 0}, lane=lane)
    _commit(watched.repo, T0 + 30, "first")
    assert watched.at(60) == "watching"
    assert watched.notices() == 0

    watched.report(phase="stop-reported", stop_reports=1, stop_reported_at=T0 + 100)
    assert watched.at(120) == "watching"
    assert watched.notices() == 1
    [event] = watched.events("lane-turn-ended")
    assert event["wake"] is True
    [wake] = watched.wakes("lane-turn-ended")
    assert wake["slot"] == "tester"
    [message] = watched.inbox()
    assert message["sender"] == "host"
    text = message["text"]
    assert text.startswith(f"{PREFIX}{named}: ")
    assert STOP_WORDS in text and WAITING_WORDS not in text
    assert DONE_UNSEEN in text and DONE_SEEN not in text
    assert "1 commit(s)" in text
    assert _deadline(100) in text
    assert text.rstrip().endswith(CLOSING)
    assert "tmux_lane_message.sh" not in text

    for seconds in (180, 240, 300):  # the same count again
        assert watched.at(seconds) == "watching"
    assert watched.notices() == 1
    watched.assert_nothing_typed()


@pytest.mark.parametrize(("kind", "phase", "words", "deadline"), [
    (CURSOR, "stop-reported", STOP_WORDS, True),
    (CLAUDE, "stop-reported", STOP_WORDS, True),
    (CLAUDE, "waiting-for-input", WAITING_WORDS, False),
    (STREAM, "idle", WAITING_WORDS, False),
    (STREAM, "failed", FAILED_WORDS, True),
], ids=["cursor-stop", "claude-stop", "claude-waiting", "stream-idle", "stream-failed"])
def test_the_notice_says_what_the_session_reported(
    tmp_path: Path, kind: Kind, phase: str, words: str, deadline: bool,
) -> None:
    """Falsifier: one wording for every phase (a stop report told as waiting for input invites
    the Validator to type into a session that may still be working; the reverse hides that a
    lane is ready); a failed stream turn not announced, or told as a finished one; a stream
    session's ``turns`` not read as its count; the stall deadline given to a lane the stall
    rule spares (waiting for input) or withheld from one it will stop."""

    watched = _session(tmp_path, kind, None, 0)
    assert watched.at(60) == "watching" and watched.notices() == 0
    _ends_turn(watched, kind, phase, 1, at=100)
    assert watched.at(120) == "watching"
    assert watched.notices() == 1
    [text] = watched.texts()
    assert words in text
    assert [said for said in SAID if said != words and said in text] == []
    assert DONE_UNSEEN in text and "0 commit(s)" in text and text.rstrip().endswith(CLOSING)
    if deadline:
        assert _deadline(100) in text
    else:
        assert DEADLINE_HEAD not in text
    assert watched.at(150) == "watching" and watched.at(180) == "watching"
    assert watched.notices() == 1


@pytest.mark.parametrize(("window", "minutes"), [(1200, 20), (1550, 26), (1510, 25)])
def test_the_deadline_names_the_launchs_stall_window_in_whole_minutes(
    tmp_path: Path, window: int, minutes: int,
) -> None:
    """Falsifier: the default window named instead of the launch's; minutes truncated (1550 s
    is 25.8 min: 26) or rounded up (1510 s is 25.2 min: 25)."""

    watched = _session(tmp_path, CURSOR, None, 0, watch={"stall_window_s": window})
    assert watched.at(60) == "watching"
    _ends_turn(watched, CURSOR, "stop-reported", 1, at=100)
    assert watched.at(120) == "watching"
    [text] = watched.texts()
    assert _deadline(100, minutes) in text


# (kind, phase it ends in, its done line: none, bound or unbound, how this check sees its
# session) -> whether the notice gives the stall deadline (the stall rule would stop it).
DEADLINE_CASES: dict[str, tuple[Kind, str, str, str, bool]] = {
    "stop-done-alive": (CLAUDE, "stop-reported", "bound", "alive", False),
    "stop-done-unknown": (CLAUDE, "stop-reported", "bound", "no-identities", False),
    "stop-unbound-done-alive": (CLAUDE, "stop-reported", "unbound", "alive", True),
    "stop-alive": (CLAUDE, "stop-reported", "none", "alive", True),
    "stop-done-dead": (CLAUDE, "stop-reported", "bound", "session-gone", True),
    "failed-done-alive": (STREAM, "failed", "bound", "alive", True),
    "failed-alive": (STREAM, "failed", "none", "alive", True),
    "waiting-alive": (CLAUDE, "waiting-for-input", "none", "alive", False),
    "waiting-unknown": (CLAUDE, "waiting-for-input", "none", "snapshot-unavailable", True),
    "waiting-no-identities": (CLAUDE, "waiting-for-input", "none", "no-identities", True),
    "waiting-dead": (CLAUDE, "waiting-for-input", "none", "session-gone", True),
    "waiting-done-unknown": (CLAUDE, "waiting-for-input", "bound", "snapshot-unavailable",
                             False),
    "waiting-done-dead": (CLAUDE, "waiting-for-input", "bound", "worker-zombie", True),
    "idle-alive": (STREAM, "idle", "none", "alive", False),
    "idle-unknown": (STREAM, "idle", "none", "snapshot-unavailable", True),
    "idle-done-no-identities": (STREAM, "idle", "bound", "no-identities", False),
    "idle-done-dead": (STREAM, "idle", "bound", "wrapper-gone", True),
}


@pytest.mark.parametrize("case", list(DEADLINE_CASES))
def test_the_deadline_is_given_exactly_when_the_stall_rule_would_stop_the_lane(
    tmp_path: Path, case: str,
) -> None:
    """The notice names the deadline exactly when the stall exemption does not hold: a done
    line counted for this turn with the turn ended (not failed) and the session not known dead,
    or a waiting phase with the session seen alive. Each case then runs to the stall boundary,
    so the notice and the stall rule are checked against each other. Falsifier: a failed turn
    or a waiting lane whose session is dead or unseen told it has no deadline (and then
    stopped), or a lane the rule spares told it will be stopped."""

    kind, phase, done, seen, deadline = DEADLINE_CASES[case]
    watched = _session(tmp_path, kind, None, 0, identities=seen != "no-identities")
    assert watched.at(60) == "watching"
    if seen not in {"alive", "no-identities"}:
        watched.unobserve(seen)
    _ends_turn(watched, kind, phase, 1, at=100, done=done != "none", bound=done == "bound")
    assert watched.at(120) == "watching"
    [text] = watched.texts()
    assert (DONE_SEEN in text) is (done == "bound")
    if deadline:
        assert _deadline(100) in text
    else:
        assert DEADLINE_HEAD not in text
    assert watched.at(STALL_AT - 1) == "watching"
    assert watched.stops == []
    outcome = watched.at(STALL_AT + 1)
    if deadline:
        assert outcome in {"stalled", "stopping"} and watched.stops == [TARGET]
    else:
        assert outcome == "watching" and watched.stops == []


@pytest.mark.parametrize(("ended", "announced", "boundary"), [
    (100, 120, STALL_AT), (READ_ALLOWANCE_S, READ_ALLOWANCE_S, STALL_AT),
    (1000, 1020, 1020 + STALL_WINDOW_S),
], ids=["inside-the-allowance", "at-its-end", "after-it"])
def test_the_deadline_counts_from_the_read_allowances_end_for_a_turn_that_ended_inside_it(
    tmp_path: Path, ended: float, announced: float, boundary: float,
) -> None:
    """A turn that ends inside the read allowance is stopped a window after the allowance ends,
    and its notice says so, with the allowance's end; one that ends at or after the allowance's
    end is stopped a window after its turn end. Each runs to its stall boundary. Falsifier: a
    notice of a turn inside the allowance promising a stop 20 min after the turn end (the lane
    runs on to 2100 s), or the allowance named for a turn after it."""

    watched = _session(tmp_path, CLAUDE, None, 0)
    assert watched.at(60) == "watching"
    _ends_turn(watched, CLAUDE, "stop-reported", 1, at=ended)
    assert watched.at(announced) == "watching"
    [text] = watched.texts()
    if ended < READ_ALLOWANCE_S:
        assert ALLOWANCE_DEADLINE.format(20, _seen(READ_ALLOWANCE_S)) in text
        assert "after this turn end" not in text
    else:
        assert DEADLINE.format(20) in text and "read allowance" not in text
    _stalls_at_the_boundary(watched, boundary)


@pytest.mark.parametrize("continued", ["after-a-review-pause", "after-a-wrap-up"])
def test_a_continued_life_names_its_deadline_from_the_turn_end(
    tmp_path: Path, continued: str,
) -> None:
    """A life continued after a review pause or a wrap-up has no read allowance, so a turn that
    ends soon after it is told the deadline from its turn end (and, after the pause, is stopped
    a window after it). Falsifier: the continued life given a fresh allowance, so the notice
    names an allowance's end and the lane runs on past the deadline it was told."""

    if continued == "after-a-review-pause":
        watched = _session(tmp_path, CLAUDE, None, 0, thread=True,
                           watch={"runs_dir": "/runs", "review_every_s": 1500})
        for seconds in (60, 600, 1200, 1470):
            assert watched.at(seconds) == "watching"
        assert watched.at(1500) == "paused"
        resumed = 1600
    else:
        watched = _session(tmp_path, CLAUDE, None, 0, thread=True, watch=RESUMABLE,
                           caps={"wall_cap_s": 4000})
        for commit, seconds in ((1000, 1100), (2000, 2100), (3000, 3100)):
            _commit(watched.repo, T0 + commit, f"c{commit}")
            assert watched.at(seconds) == "watching"
        assert watched.at(3700) == "wrapping"
        resumed = 3750
    assert watched.stops == [TARGET]
    watched.revive()
    _new_turn(watched, CLAUDE, at=resumed)
    watched.clock.value = T0 + resumed
    watched.watch.arm(new_life=True)
    _ends_turn(watched, CLAUDE, "stop-reported", 1, at=resumed + 100)
    assert watched.at(resumed + 120) == "watching"
    [text] = watched.texts()
    assert DEADLINE.format(20) in text and "read allowance" not in text
    if continued == "after-a-review-pause":
        boundary = resumed + 120 + STALL_WINDOW_S
        assert watched.at(boundary - 1) == "watching"
        assert watched.stops == [TARGET]
        assert watched.at(boundary + 1) in {"stalled", "stopping"}
        assert watched.stops == [TARGET, TARGET] and watched.stalls() == 1


@pytest.mark.parametrize("read", ["before-the-turn-end", "with-the-turn-end"])
def test_a_done_line_read_in_the_current_turn_is_reported(tmp_path: Path, read: str) -> None:
    """Falsifier: the notice saying no done line was seen when the lane did claim done (the
    Validator would chase a lane that finished), including the usual stream case where the
    response and the completed turn reach the watcher in the same check."""

    watched = _session(tmp_path, STREAM, None, 0)
    if read == "before-the-turn-end":
        watched.respond(DONE_LINE)
    assert watched.at(60) == "watching"
    assert watched.notices() == 0  # a claim is not a turn end
    if read == "with-the-turn-end":
        watched.respond(DONE_LINE)
    _ends_turn(watched, STREAM, "idle", 1, at=100)
    assert watched.at(120) == "watching"
    assert watched.notices() == 1
    [text] = watched.texts()
    assert text.startswith(f"{PREFIX}tester.r2: ")
    assert DONE_SEEN in text and DONE_UNSEEN not in text
    assert WAITING_WORDS in text and DEADLINE_HEAD not in text


def test_a_done_line_from_an_earlier_turn_does_not_speak_for_a_later_one(tmp_path: Path) -> None:
    """Falsifier: a done claim that outlives its turn, so a later turn that printed none is
    reported as done (the Validator accepts a lane that never finished its new work). The
    later turn writes only tool output, which neither claims nor retracts, so only the turn
    boundary can end the claim."""

    watched = _session(tmp_path, STREAM, None, 0)
    watched.respond(DONE_LINE)
    assert watched.at(60) == "watching"
    _ends_turn(watched, STREAM, "idle", 1, at=100)
    assert watched.at(120) == "watching"
    assert DONE_SEEN in watched.texts()[0]

    _new_turn(watched, STREAM, at=600)
    watched.tool_output("ran the suite")
    assert watched.at(630) == "watching"
    _ends_turn(watched, STREAM, "idle", 2, at=700)
    assert watched.at(720) == "watching"
    assert watched.notices() == 2
    later = watched.texts()[1]
    assert DONE_UNSEEN in later and DONE_SEEN not in later


def _stalls_at_the_boundary(watched: Lane, seconds: float = STALL_AT) -> None:
    assert watched.at(seconds - 1) == "watching"
    assert watched.stops == []
    assert watched.at(seconds + 1) in {"stalled", "stopping"}
    assert watched.stops == [TARGET]


def _spared_past_the_boundary(watched: Lane, seconds: float = STALL_AT) -> None:
    for at in (seconds + 1, seconds + 3600):
        assert watched.at(at) == "watching"
    assert watched.stops == [] and watched.stalls() == 0


# Which turn a stream session's done line counts for: the turn whose wrapper mark comes last
# before it in the lane log. Each lane below is unobserved (no recorded identities), so only a
# done line counted for its current turn spares it the stall stop.


def test_a_turn_that_starts_and_claims_done_between_two_checks_is_credited(
    tmp_path: Path,
) -> None:
    """Between two checks turn 1 ends, turn 2 starts (its mark), prints its done line and ends.
    Falsifier (agy's review): the done line lost because a new turn began since the previous
    check, so the notice says no done line and the finished lane is stopped as stalled."""

    watched = _session(tmp_path, CLAUDE_STREAM, None, 0, identities=False)
    assert watched.at(60) == "watching"
    _ends_turn(watched, CLAUDE_STREAM, "idle", 1, at=100)
    _new_turn(watched, CLAUDE_STREAM, at=150)
    _ends_turn(watched, CLAUDE_STREAM, "idle", 2, at=200, done=True)
    assert watched.at(210) == "watching"
    [text] = watched.texts()
    assert DONE_SEEN in text and DEADLINE_HEAD not in text
    _spared_past_the_boundary(watched)


@pytest.mark.parametrize("read", ["in-one-check", "turn-k-before-k-plus-1-began"])
def test_turn_ks_done_line_read_after_turn_k_plus_1_began_counts_for_k_never_k_plus_1(
    tmp_path: Path, read: str,
) -> None:
    """Turn 1 prints its done line and ends; turn 2 starts and ends without one. Falsifier
    (Codex's review): turn 1's done line, read after turn 2 began, credited to turn 2, so its
    notice says done and the lane that never finished its new work is spared the stall stop."""

    watched = _session(tmp_path, CLAUDE_STREAM, None, 0, identities=False)
    assert watched.at(60) == "watching"
    _ends_turn(watched, CLAUDE_STREAM, "idle", 1, at=100, done=True)
    if read == "turn-k-before-k-plus-1-began":
        assert watched.at(120) == "watching"
        assert DONE_SEEN in watched.texts()[0]
    _new_turn(watched, CLAUDE_STREAM, at=150)
    assert watched.at(180) == "watching"
    _ends_turn(watched, CLAUDE_STREAM, "idle", 2, at=200)
    assert watched.at(210) == "watching"
    later = watched.texts()[-1]
    assert DONE_UNSEEN in later and DONE_SEEN not in later
    assert _deadline(200) in later
    _stalls_at_the_boundary(watched)


@pytest.mark.parametrize("respond", ["agent-message", "result"])
def test_a_done_line_printed_mid_turn_counts_for_that_turn(tmp_path: Path, respond: str) -> None:
    """A Codex ``item.completed`` agent message carries the done line before its turn ends, and
    a check reads it mid-turn. Falsifier: a done line counted only when read with its turn end,
    so the turn's notice says no done line and the finished lane is stopped."""

    watched = _session(tmp_path, CODEX_STREAM, None, 0, identities=False)
    assert watched.at(60) == "watching"
    _new_turn(watched, CODEX_STREAM, at=90)
    assert watched.at(120) == "watching"
    if respond == "agent-message":
        watched.agent_message(DONE_LINE)
    else:
        watched.respond(DONE_LINE)
    assert watched.at(150) == "watching" and watched.notices() == 0
    watched.tool_output("ran the suite")
    _ends_turn(watched, CODEX_STREAM, "idle", 1, at=200)
    assert watched.at(210) == "watching"
    [text] = watched.texts()
    assert DONE_SEEN in text and DEADLINE_HEAD not in text
    _spared_past_the_boundary(watched)


@pytest.mark.parametrize("kind", [CLAUDE_STREAM, CLAUDE], ids=["stream", "terminal"])
def test_a_response_with_no_turn_mark_before_it_counts_for_no_turn(
    tmp_path: Path, kind: Kind,
) -> None:
    """A persistent session's response with no wrapper mark before it in the log (a terminal
    session's wrapper writes none: its done lines come in stop reports). Falsifier: such a
    response credited to the current turn."""

    watched = _session(tmp_path, kind, None, 0, identities=False, marked=False)
    watched.respond(DONE_LINE)
    assert watched.at(60) == "watching"
    _ends_turn(watched, kind, "idle" if _stream(kind) else "stop-reported", 1, at=100)
    assert watched.at(120) == "watching"
    [text] = watched.texts()
    assert DONE_UNSEEN in text and _deadline(100) in text
    _stalls_at_the_boundary(watched)


@pytest.mark.parametrize("arm", ["check", "restart", "new-life"])
def test_the_last_turn_mark_is_kept_across_checks_and_arms_that_keep_the_log_offset(
    tmp_path: Path, arm: str,
) -> None:
    """The turn's mark is read at one check and its done line at a later one, across a restart
    or a new life of the watcher that keeps its log offset. Falsifier: the mark forgotten between
    checks or lives, so the done line counts for no turn."""

    watched = _session(tmp_path, CLAUDE_STREAM, None, 0, identities=False)
    assert watched.at(60) == "watching"
    _new_turn(watched, CLAUDE_STREAM, at=90)
    assert watched.at(120) == "watching"
    watched.clock.value = T0 + 130
    if arm == "restart":
        watched.restart()
    elif arm == "new-life":
        watched.watch.arm(new_life=True)
    _ends_turn(watched, CLAUDE_STREAM, "idle", 1, at=200, done=True)
    assert watched.at(210) == "watching"
    [text] = watched.texts()
    assert DONE_SEEN in text and DEADLINE_HEAD not in text
    _spared_past_the_boundary(watched)


@pytest.mark.parametrize("change", ["new-offset", "truncated", "replaced"])
def test_a_new_log_offset_or_a_truncated_or_replaced_log_clears_the_last_turn_mark(
    tmp_path: Path, change: str,
) -> None:
    """The turn's mark was read; then the watcher's new life is given a new log offset, or the
    log is truncated or replaced, and a done line follows with no mark before it. Falsifier: a
    mark from another log position (or another file) crediting the done line to this turn."""

    watched = _session(tmp_path, CLAUDE_STREAM, None, 0, identities=False)
    assert watched.at(60) == "watching"
    _new_turn(watched, CLAUDE_STREAM, at=90)
    watched.respond("Working on it.")
    assert watched.at(120) == "watching"
    if change == "new-offset":  # past output this watcher never read
        watched.respond("More output.")
        watched.clock.value = T0 + 130
        watched.watch.arm(new_life=True, log_offset=watched.log.stat().st_size)
    elif change == "truncated":
        watched.log.write_text("")
    else:
        replacement = watched.log.with_name("replacement.log")
        replacement.write_text("")
        os.replace(replacement, watched.log)
    _ends_turn(watched, CLAUDE_STREAM, "idle", 1, at=200, done=True)
    assert watched.at(210) == "watching"
    [text] = watched.texts()
    renewed = READ_ALLOWANCE_S + (130 if change == "new-offset" else 0)
    assert DONE_UNSEEN in text and _deadline(200, allowance_end=renewed) in text
    # A new life's read allowance starts at its arm.
    _stalls_at_the_boundary(watched, STALL_AT + (130 if change == "new-offset" else 0))


@pytest.mark.parametrize("end", ["stall-boundary", "clean-exit"])
@pytest.mark.parametrize("log", ["replaced", "truncated", "appended"])
def test_a_done_line_in_a_replaced_log_before_any_mark_counts_for_no_turn(
    tmp_path: Path, log: str, end: str,
) -> None:
    """One turn throughout: the record keeps its ``turn_started_at``, and the turn's mark is in
    the original log, read at a check. The log is then replaced (or truncated in place) by a
    shorter one with no mark, and a check sees it shrink; only then is a done-line response
    written to the new log, and the turn ends. Falsifier: the mark kept over the replaced log,
    so the done line counts for the record's unchanged turn: the notice says done, the lane is
    spared the stall stop, and a clean exit is ``done``. Control (``appended``): the same done
    line written to the original log is the turn's."""

    watched = _session(tmp_path, CLAUDE_STREAM, None, 0, identities=False)
    watched.tool_output("ran the suite: 412 passed, 3 skipped, 0 failed in 61.2 s")
    watched.respond("Working on it; the report is next.")
    assert watched.at(60) == "watching"  # the turn's mark has been read
    original = watched.log.stat().st_size
    if log == "replaced":
        replacement = watched.log.with_name("replacement.log")
        replacement.write_text(json.dumps({"type": "tool_result", "content": "restarted"})
                               + "\n", encoding="utf-8")
        os.replace(replacement, watched.log)
    elif log == "truncated":
        watched.log.write_text("", encoding="utf-8")
    if log != "appended":
        assert watched.log.stat().st_size < original
        assert b"persistent-turn-start" not in watched.log.read_bytes()
    assert watched.at(80) == "watching"  # this check sees the log shrink (or not)
    watched.respond(DONE_LINE)  # written to the new log, with no mark before it
    assert watched.record is not None and watched.record["turn_started_at"] == T0 - 5
    watched.report(phase="idle", turns=1, turn_completed_at=T0 + 100)  # the same turn ends
    assert watched.record["turn_started_at"] == T0 - 5
    assert watched.at(120) == "watching"
    [text] = watched.texts()
    credited = log == "appended"
    assert (DONE_SEEN in text) is credited and (DONE_UNSEEN in text) is not credited
    assert (_deadline(100) in text) is not credited
    if end == "stall-boundary":
        if credited:
            _spared_past_the_boundary(watched)
        else:
            _stalls_at_the_boundary(watched)
    else:
        lane_agent.record_exit(watched.exits, slot="tester", rc=0, stopped=False)
        watched.kill()
        assert watched.at(150) == ("done" if credited else "exited")
        assert watched.stops == []


def test_a_lane_without_a_session_record_counts_its_done_line_as_before(tmp_path: Path) -> None:
    """A lane that is not persistent has no turns and no marks. Falsifier: its done line
    counted for no turn, so its verified exit is reported as an exit, not done."""

    watched = Lane(tmp_path, record=None)
    assert watched.at(60) == "watching"
    watched.respond(DONE_LINE)
    assert watched.at(120) == "watching"
    lane_agent.record_exit(watched.exits, slot="tester", rc=0, stopped=False)
    watched.kill()
    assert watched.at(150) == "done"
    assert watched.stops == []


# Which turn a native terminal session's done line counts for: the turn its stop report row
# names, and only when the wrapper bound that stop to it.


@pytest.mark.parametrize(("stop", "credited"), [
    ("bound-this-turn", True),
    ("unbound-this-turn", False),
    ("bound-earlier-turn", False),
])
def test_a_stop_reports_done_line_counts_only_when_bound_and_only_for_its_turn(
    tmp_path: Path, stop: str, credited: bool,
) -> None:
    """Falsifier: an unbound stop's done line (a response the wrapper could not tie to this
    turn) or one bound to an earlier turn sparing the current turn the stall stop."""

    watched = _session(tmp_path, CLAUDE, None, 0, identities=False)
    assert watched.at(60) == "watching"
    _new_turn(watched, CLAUDE, at=90)
    watched.stop_report(DONE_LINE, bound=stop != "unbound-this-turn",
                        turn=-5 if stop == "bound-earlier-turn" else None)
    watched.report(phase="stop-reported", stop_reports=1, stop_reported_at=T0 + 100)
    assert watched.at(120) == "watching"
    [text] = watched.texts()
    assert (DONE_SEEN in text) is credited
    assert (DEADLINE_HEAD in text) is not credited
    if credited:
        _spared_past_the_boundary(watched)
    else:
        _stalls_at_the_boundary(watched)


def test_a_stop_that_arrives_after_a_queued_prompt_does_not_exempt_the_queued_turn(
    tmp_path: Path,
) -> None:
    """Codex's case: prompt B is reported while A still runs, then A's stop arrives carrying
    A's done line; the wrapper records it unbound, against B's turn. B's own stop is bound.
    Falsifier: A's done line sparing B (stopped never, though B never said done)."""

    watched = _session(tmp_path, CLAUDE, None, 0, identities=False)
    assert watched.at(60) == "watching"
    _new_turn(watched, CLAUDE, at=90)  # A
    assert watched.at(120) == "watching"
    _new_turn(watched, CLAUDE, at=150)  # B, queued behind A
    watched.stop_report(DONE_LINE, bound=False)  # A's stop, under B's turn
    watched.report(phase="stop-reported", stop_reports=1, stop_reported_at=T0 + 160)
    assert watched.at(180) == "watching"
    [first] = watched.texts()
    assert DONE_UNSEEN in first and _deadline(160) in first
    assert watched.at(1200) == "watching"

    watched.stop_report("B: not finished yet.")  # B's own stop, bound, no done line
    watched.report(stop_reports=2, stop_reported_at=T0 + 1300)
    assert watched.at(1320) == "watching"
    second = watched.texts()[-1]
    assert DONE_UNSEEN in second and _deadline(1300) in second
    _stalls_at_the_boundary(watched, 1320 + STALL_WINDOW_S)


@pytest.mark.parametrize(("kind", "busy", "settled"), [
    (CURSOR, "prompt-observed", "stop-reported"),
    (CLAUDE, "started", "waiting-for-input"),
    (CLAUDE, "asking-operator", "waiting-for-input"),
    (CLAUDE, "starting", "stop-reported"),
    (STREAM, "working", "idle"),
], ids=["prompt-observed", "started", "asking-operator", "starting", "working"])
def test_a_count_seen_mid_turn_is_announced_when_the_session_settles(
    tmp_path: Path, kind: Kind, busy: str, settled: str,
) -> None:
    """Falsifier: announcing a turn end while the session is busy again (the Validator would
    act on a lane that is mid-turn); or marking the count noticed then, so the session settling
    at that count is never announced (the turn end is lost: the original 20.6-minute idle)."""

    _, counter, _, _ = kind
    watched = _session(tmp_path, kind, None, 0)
    assert watched.at(30) == "watching"
    watched.report(phase=busy, **{counter: 1})
    assert watched.at(60) == "watching"
    assert watched.at(90) == "watching"
    assert watched.notices() == 0
    watched.report(phase=settled)
    assert watched.at(120) == "watching"
    assert watched.notices() == 1
    assert watched.at(150) == "watching" and watched.notices() == 1


def test_a_burst_of_turn_ends_is_one_notice(tmp_path: Path) -> None:
    """Falsifier: one notice per turn end instead of one per check that finds new ends (a
    stack of stale wakes), whether the ends arrive between two checks or while the session is
    busy."""

    watched = _session(tmp_path, CURSOR, "stop-reported", 0)
    assert watched.at(60) == "watching" and watched.notices() == 0
    watched.report(stop_reports=3)  # three turn ends between checks
    assert watched.at(120) == "watching"
    assert watched.notices() == 1
    assert watched.at(180) == "watching" and watched.notices() == 1

    watched.report(phase="prompt-observed", stop_reports=4)
    assert watched.at(240) == "watching"
    watched.report(phase="prompt-observed", stop_reports=5)
    assert watched.at(300) == "watching"
    watched.report(phase="stop-reported", stop_reports=6)
    assert watched.at(360) == "watching"
    assert watched.notices() == 2


@pytest.mark.parametrize(("record", "raise_field"), [
    ({"phase": "stop-reported", "stop_reports": 0, "turns": 0}, "turns"),
    ({"phase": "idle", "stop_reports": None, "turns": 0}, "stop_reports"),
], ids=["stop-reports-beside-turns", "non-integer-stop-reports"])
def test_the_count_is_stop_reports_if_an_integer_else_turns(
    tmp_path: Path, record: dict[str, Any], raise_field: str,
) -> None:
    """Falsifier: reading ``turns`` before an integer ``stop_reports``; or giving up on a
    record whose ``stop_reports`` is not an integer although ``turns`` is."""

    watched = Lane(tmp_path, record=record)
    assert watched.at(30) == "watching" and watched.notices() == 0
    if raise_field == "turns":
        watched.report(turns=4)  # not this session's count
        assert watched.at(60) == "watching"
        assert watched.notices() == 0
        watched.report(stop_reports=1)
    else:
        watched.report(stop_reports="7")  # still not an integer
        assert watched.at(60) == "watching"
        assert watched.notices() == 0
        watched.report(turns=1)
    assert watched.at(120) == "watching"
    assert watched.notices() == 1


@pytest.mark.parametrize("record", [
    None,
    {"phase": "stop-reported"},
    {"phase": "stop-reported", "stop_reports": "3", "turns": "3"},
    {"phase": "idle", "turns": 2.0},
], ids=["no-persistent-record", "no-counts", "string-counts", "float-turns"])
def test_a_lane_without_an_integer_count_is_never_announced(
    tmp_path: Path, record: dict[str, Any] | None,
) -> None:
    """Falsifier: a lane with no persistent session (or no count) woken as if it ended a turn,
    or a malformed count coerced into one."""

    watched = Lane(tmp_path, record=record)
    for seconds in (60, 120, 600, 1200):
        assert watched.at(seconds) == "watching"
    assert watched.events("lane-turn-ended") == [] and watched.wakes("lane-turn-ended") == []
    assert watched.inbox() == []


# -- 2. the baseline -------------------------------------------------------------------------


@pytest.mark.parametrize("started", [T0 - 3600, T0, T0 + 10, DROP],
                         ids=["long-before-the-launch", "at-the-launch", "after-the-launch",
                              "missing"])
def test_a_slots_first_watch_announces_a_turn_that_ended_before_it_armed(
    tmp_path: Path, started: object,
) -> None:
    """The launch row is written after the session starts, so the record's timestamps say
    nothing about what this slot was told. Falsifier: a first watch taking the count it found
    as noticed (a fast Tester's turn end told to nobody), for any ``turn_started_at``."""

    watched = Lane(tmp_path, record={"phase": "stop-reported", "stop_reports": 1,
                                     "turn_started_at": started})
    assert watched.at(30) == "watching"
    assert watched.notices() == 1
    assert watched.at(60) == "watching" and watched.notices() == 1
    watched.report(stop_reports=2)
    assert watched.at(90) == "watching"
    assert watched.notices() == 2


def test_a_restarted_watcher_never_repeats_a_notice(tmp_path: Path) -> None:
    """Falsifier: the first-watch rule applied to every arm, so a restarted watcher announces
    again a turn end it already announced."""

    watched = _session(tmp_path, CURSOR, None, 0)
    assert watched.at(60) == "watching"
    _ends_turn(watched, CURSOR, "stop-reported", 1, at=100)
    assert watched.at(120) == "watching" and watched.notices() == 1
    watched.restart()
    assert watched.at(150) == "watching"
    assert watched.at(180) == "watching"
    assert watched.notices() == 1
    watched.report(stop_reports=2)  # the next turn end, while this watcher runs
    assert watched.at(240) == "watching"
    assert watched.notices() == 2


def test_a_rearm_announces_a_turn_end_missed_while_the_watcher_was_down(tmp_path: Path) -> None:
    """A re-arm keeps the noticed count the watch state stored.

    Falsifier: a re-arm taking the count at arm as noticed, so the turn end that happened while
    the watcher was down is told to nobody (the original 20.6-minute idle); or announcing it
    more than once."""

    watched = _session(tmp_path, CURSOR, None, 0)
    assert watched.at(60) == "watching"
    _ends_turn(watched, CURSOR, "stop-reported", 1, at=100)
    assert watched.at(120) == "watching" and watched.notices() == 1
    watched.report(stop_reports=2)  # a turn end while the watcher is down
    watched.restart()
    assert watched.at(180) == "watching"
    assert watched.notices() == 2
    assert watched.at(210) == "watching" and watched.notices() == 2
    watched.report(stop_reports=3)
    assert watched.at(240) == "watching"
    assert watched.notices() == 3


@pytest.mark.parametrize("arms", ["new-life", "rearm-after-final-then-new-life"])
def test_a_new_life_carries_the_noticed_count(tmp_path: Path, arms: str) -> None:
    """Falsifier: a new life (or a re-arm after a final status) taking the count at arm as
    noticed, so a turn end the resumed session reported before the new life armed is told to
    nobody; or a new life announcing again the ends the earlier life announced."""

    watched = _session(tmp_path, CURSOR, "stop-reported", 3)
    assert watched.at(60) == "watching"
    assert watched.notices() == 1
    watched.report(stop_reports=5)
    assert watched.at(120) == "watching"
    assert watched.notices() == 2

    lane_agent.record_exit(watched.exits, slot="tester", rc=1, stopped=False)
    watched.kill()
    assert watched.at(180) == "exited"
    watched.report(stop_reports=8, turn_started_at=T0 + 240, stop_reported_at=T0 + 260)
    if arms == "rearm-after-final-then-new-life":
        watched.clock.value = T0 + 270
        watched.restart()
    watched.revive()
    watched.clock.value = T0 + 300
    watched.watch.arm(new_life=True)
    assert watched.at(360) == "watching"
    assert watched.notices() == 3  # the ends reported before the new life armed, once
    assert watched.at(390) == "watching" and watched.notices() == 3
    watched.report(stop_reports=9)
    assert watched.at(420) == "watching"
    assert watched.notices() == 4


def test_a_watch_state_from_before_the_change_takes_the_current_count_as_noticed(
    tmp_path: Path,
) -> None:
    """Falsifier: an upgraded watcher reading an absent noticed count as zero and waking the
    Validator for every turn the lane ever ended.

    The pre-change state is the current state without its record of the noticed count. That
    record is found by value (a count no other watch field plausibly holds) rather than by a
    field name, which the contract does not give."""

    sentinel = 4271
    watched = _session(tmp_path, CURSOR, "stop-reported", sentinel)
    assert watched.at(60) == "watching"
    assert watched.notices() == 1  # a first watch: announced

    def downgrade() -> None:
        state = json.loads(watched.watch.state_path.read_text())
        old, dropped = _strip(state, sentinel)
        assert dropped, "the watch state records no noticed turn-end count"
        watched.watch.state_path.write_text(json.dumps(old))

    downgrade()
    assert watched.at(90) == "watching"  # the same watcher reading the old state
    assert watched.notices() == 1

    downgrade()
    watched.report(stop_reports=sentinel + 2)
    watched.restart()  # an upgraded watcher over the old state
    assert watched.at(120) == "watching"
    assert watched.notices() == 1
    watched.report(stop_reports=sentinel + 3)
    assert watched.at(180) == "watching"
    assert watched.notices() == 2


@pytest.mark.parametrize("new_session", ["settled", "busy-first"])
def test_a_lower_count_is_a_new_session_and_resets_the_noticed_count(
    tmp_path: Path, new_session: str,
) -> None:
    """Falsifier: a new session's lower count announced as a turn end, or its later turn ends
    ignored until they pass the old session's count."""

    watched = _session(tmp_path, STREAM, "idle", 5)
    assert watched.at(60) == "watching"
    assert watched.notices() == 1
    if new_session == "settled":
        watched.report(turns=1, turn_started_at=T0 + 100, turn_completed_at=T0 + 110)
        assert watched.at(120) == "watching"
        assert watched.notices() == 1
        watched.report(turns=2, turn_completed_at=T0 + 170)
    else:
        watched.report(phase="working", turns=0, turn_started_at=T0 + 100,
                       turn_completed_at=None)
        assert watched.at(120) == "watching"
        assert watched.notices() == 1
        watched.report(phase="idle", turns=1, turn_completed_at=T0 + 170)
    assert watched.at(180) == "watching"
    assert watched.notices() == 2


# -- 3. the notice queue ---------------------------------------------------------------------


@pytest.mark.parametrize("blocked", ["symlink", "file", "inbox-directory"])
def test_an_undeliverable_notice_is_recorded_once_and_retried_until_delivered(
    tmp_path: Path, blocked: str,
) -> None:
    """Falsifier: an inbox failure crashing the watcher (leaving the lane unwatched), changing
    its verdict, writing through a planted symlink, failing silently, recording the failure at
    every check, re-waking at every retry, or never retrying (the notice is lost for good)."""

    healthy = Lane(tmp_path / "healthy", record={"phase": "prompt-observed", "stop_reports": 0})
    watched = Lane(tmp_path / "blocked", record={"phase": "prompt-observed", "stop_reports": 0})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _block(watched, blocked, elsewhere)

    expected, outcomes = [], []
    for seconds, fields in [(60, {}), (120, {"phase": "stop-reported", "stop_reports": 1}),
                            (180, {}), (240, {}), (300, {})]:
        for lane, results in ((healthy, expected), (watched, outcomes)):
            if fields:
                lane.report(**fields)
            results.append(lane.at(seconds))
    assert outcomes == expected == ["watching"] * 5
    assert healthy.notices() == 1  # the twin shows a notice was due

    assert watched.undelivered() == 1
    assert len(watched.events("lane-turn-ended")) == 1
    assert len(watched.wakes("lane-turn-ended")) == 1
    assert watched.inbox() == []
    assert list(elsewhere.iterdir()) == []
    assert watched.stops == []
    watched.assert_nothing_typed()

    _unblock(watched, blocked)
    assert watched.at(360) == "watching"
    [message] = watched.inbox()
    assert message["sender"] == "host" and message["text"].startswith(f"{PREFIX}tester.r2: ")
    assert STOP_WORDS in message["text"]
    for seconds in (420, 480):
        assert watched.at(seconds) == "watching"
    assert len(watched.inbox()) == 1
    assert watched.undelivered() == 1
    assert len(watched.events("lane-turn-ended")) == 1
    assert len(watched.wakes("lane-turn-ended")) == 1
    assert list(elsewhere.iterdir()) == []


def test_notices_queued_in_an_outage_are_all_delivered_oldest_first(tmp_path: Path) -> None:
    """Falsifier: a pending notice replaced by the next one (a turn end lost), the queue
    delivered out of order, or one undelivered row for the whole outage instead of one per
    notice."""

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    watched = _session(tmp_path / "lane", CLAUDE, None, 0)
    _block(watched, "symlink", elsewhere)
    assert watched.at(30) == "watching"
    _ends_turn(watched, CLAUDE, "stop-reported", 1, at=40)
    assert watched.at(60) == "watching"
    _new_turn(watched, CLAUDE, at=70)
    assert watched.at(90) == "watching"
    _ends_turn(watched, CLAUDE, "waiting-for-input", 2, at=100)
    assert watched.at(120) == "watching"
    assert watched.undelivered() == 2
    assert len(watched.wakes("lane-turn-ended")) == 2
    assert watched.inbox() == []

    _unblock(watched, "symlink")
    assert watched.at(150) == "watching"
    first, second = watched.texts()
    assert STOP_WORDS in first and WAITING_WORDS not in first
    assert WAITING_WORDS in second and STOP_WORDS not in second
    assert watched.at(180) == "watching"
    assert watched.notices() == 2 and watched.undelivered() == 2
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("status", ["stalled", "exited", "stopping", "paused", "wrapping",
                                    "new-life"])
def test_a_pending_notice_is_retried_whatever_the_watch_status(
    tmp_path: Path, status: str,
) -> None:
    """Falsifier: retrying only while the lane is watched, so a notice the inbox refused is
    lost once the lane is stopped, paused, wrapping up, finished or resumed (exactly when the
    Validator most needs it)."""

    options: dict[str, Any] = {
        "stopping": {"caps": {"wall_cap_s": 300}},
        "paused": {"watch": REVIEWED, "thread": True},
        "wrapping": {"caps": {"wall_cap_s": 1800}, "watch": RESUMABLE, "thread": True},
    }.get(status, {})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    watched = _session(tmp_path / "lane", CLAUDE, None, 0, **options)
    _block(watched, "symlink", elsewhere)
    assert watched.at(30) == "watching"
    _ends_turn(watched, CLAUDE, "stop-reported", 1, at=40)  # a stop report: not waiting
    assert watched.at(60) == "watching"
    assert watched.undelivered() == 1 and watched.inbox() == []

    if status == "stalled":
        reached = STALL_AT + 1
    elif status == "exited":
        lane_agent.record_exit(watched.exits, slot="tester", rc=1, stopped=False)
        watched.kill()
        reached = 90
    elif status == "stopping":
        watched.stop_kills = False
        reached = 300
    elif status == "paused":
        reached = 1080
    elif status == "wrapping":
        reached = 1500
    else:
        lane_agent.record_exit(watched.exits, slot="tester", rc=1, stopped=False)
        watched.kill()
        assert watched.at(90) == "exited"
        watched.revive()
        watched.clock.value = T0 + 120
        watched.watch.arm(new_life=True)
        reached = 150
    expected = "watching" if status == "new-life" else status
    assert watched.at(reached) == expected
    assert watched.inbox() == []

    _unblock(watched, "symlink")
    assert watched.at(reached + 30) == expected
    [message] = watched.inbox()
    assert STOP_WORDS in message["text"]
    assert watched.at(reached + 60) == expected
    assert len(watched.inbox()) == 1
    assert watched.undelivered() == 1
    assert len(watched.wakes("lane-turn-ended")) == 1


def _deadline(ended: float, minutes: int = 20, allowance_end: float = READ_ALLOWANCE_S) -> str:
    """The notice's deadline for a turn that ended ``ended`` s after the launch: counted from
    the read allowance's end when it ended inside the allowance, else from the turn end."""
    if ended < allowance_end:
        return ALLOWANCE_DEADLINE.format(minutes, _seen(allowance_end))
    return DEADLINE.format(minutes)


def _seen(seconds: float) -> str:
    """The announcing check's time as the notice gives it: the watch clock, as event ``ts``."""
    return datetime.datetime.fromtimestamp(T0 + seconds, datetime.UTC).isoformat()


# Holds the inbox lock from another process until its stdin closes (or ``hold_s`` passes, so a
# watcher that blocks on the lock is released and fails on time instead of hanging the suite).
HOLDER = (
    "import fcntl, os, select, sys\n"
    "fd = os.open(sys.argv[1], os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX)\n"
    "print('locked', flush=True)\n"
    "select.select([sys.stdin], [], [], float(sys.argv[2]))\n"
)


class HeldLock:
    """Another writer holding ``fcntl.flock(LOCK_EX)`` on a file (an inbox, the events log)."""

    def __init__(self, path: Path, hold_s: float = 40.0) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.process = subprocess.Popen(
            [sys.executable, "-I", "-c", HOLDER, str(path), str(hold_s)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        assert self.process.stdout is not None
        assert self.process.stdout.readline().strip() == "locked"

    def release(self) -> None:
        if self.process.poll() is None:
            assert self.process.stdin is not None
            self.process.stdin.close()
        self.process.wait(timeout=30)


def _timed_check(watched: Lane, seconds: float) -> tuple[str, float]:
    started = time.monotonic()
    outcome = watched.at(seconds)
    return outcome, time.monotonic() - started


def test_a_held_inbox_lock_delays_the_notices_but_never_the_watcher(tmp_path: Path) -> None:
    """Falsifier: a check that blocks on another writer's inbox lock (the watcher stops
    watching: no stall stop, no cap, for as long as the lock is held), or waits on it more
    than once (about 5 s each) when a notice is already queued and another is announced in the
    same check; a check that gives up without recording the notices it could not deliver,
    records a notice again at every check, or loses, repeats or reorders the queued notices
    once the lock is released. The first notice is queued by another failure (a symlinked
    inbox), so the lock meets a queue at once."""

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    watched = _session(tmp_path / "lane", CLAUDE, None, 0)
    _block(watched, "symlink", elsewhere)
    assert watched.at(30) == "watching"
    _ends_turn(watched, CLAUDE, "stop-reported", 1, at=40)
    assert watched.at(60) == "watching"
    assert watched.undelivered() == 1
    _unblock(watched, "symlink")

    holder = HeldLock(watched.root / "validator" / "inbox.jsonl")
    try:
        for count, phase, at in ((2, "waiting-for-input", 120), (3, "stop-reported", 180)):
            _new_turn(watched, CLAUDE, at=at - 30)
            _ends_turn(watched, CLAUDE, phase, count, at=at - 10)
            # One queued notice (two at 180) and one announced now: one wait, not one each.
            outcome, took = _timed_check(watched, at)
            assert outcome == "watching" and took < 8, took
            assert watched.undelivered() == count and watched.texts() == []
        assert len(watched.wakes("lane-turn-ended")) == 3
    finally:
        holder.release()

    assert watched.at(210) == "watching"
    texts = watched.texts()
    assert len(texts) == 3
    for text, words, seen in zip(texts, (STOP_WORDS, WAITING_WORDS, STOP_WORDS),
                                 (60, 120, 180), strict=True):
        assert words in text and f"(seen {_seen(seen)})" in text
    assert watched.at(240) == "watching"
    assert len(watched.texts()) == 3 and watched.undelivered() == 3
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("wait", ["default", "explicit-none"])
def test_a_message_without_a_wait_still_waits_for_the_inbox_lock(
    tmp_path: Path, wait: str,
) -> None:
    """Falsifier: the watcher's give-up applied to every sender, so a Validator's or
    founder's message is refused while another writer appends."""

    root = tmp_path / "run"
    inbox = root / "validator" / "inbox.jsonl"
    holder = HeldLock(inbox)
    errors: list[BaseException] = []

    def send() -> None:
        try:
            if wait == "default":
                run_record.message(root, "validator", "founder", "Use the second clock.")
            else:
                run_record.message(root, "validator", "founder", "Use the second clock.",
                                   wait_s=None)
        except BaseException as error:  # noqa: BLE001 - reported below
            errors.append(error)

    sender = threading.Thread(target=send, daemon=True)
    try:
        sender.start()
        sender.join(timeout=2.0)
        assert sender.is_alive(), errors  # still waiting for the lock
        assert "Use the second clock." not in inbox.read_text(encoding="utf-8")
    finally:
        holder.release()
    sender.join(timeout=15)
    assert not sender.is_alive() and errors == []
    rows = [json.loads(line) for line in inbox.read_text(encoding="utf-8").splitlines()]
    assert [row["text"] for row in rows] == ["Use the second clock."]


def test_a_notice_whose_write_failed_after_it_was_committed_is_not_delivered_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falsifier: a retry that rewrites a notice whose row reached the inbox before the write
    reported failure (the Validator told the same turn end twice), or a queue that stalls
    behind it so later notices are never delivered."""

    watched = _session(tmp_path, CLAUDE, None, 0)
    assert watched.at(30) == "watching"
    _ends_turn(watched, CLAUDE, "stop-reported", 1, at=40)
    assert watched.at(60) == "watching"
    assert len(watched.texts()) == 1  # the watcher's inbox writer is loaded now

    # The writer the watcher uses, whichever import name it took (the watcher loads the
    # harness modules by their own names): the real append, then a failure, once.
    fired: list[str] = []
    modules = {id(module): module for name, module in list(sys.modules.items())
               if name in {"run_record", "harness.run_record"}}
    for module in modules.values():
        real = module.append_record

        def late(*args: Any, _real: Any = real, **kwargs: Any) -> Any:
            result = _real(*args, **kwargs)
            target = Path(args[0] if args else kwargs["path"])
            if not fired and target.name == "inbox.jsonl":
                fired.append(str(target))
                raise OSError("the disk went away after the row was written")
            return result

        monkeypatch.setattr(module, "append_record", late)

    _new_turn(watched, CLAUDE, at=70)
    assert watched.at(90) == "watching"
    _ends_turn(watched, CLAUDE, "stop-reported", 2, at=100)
    assert watched.at(120) == "watching"
    assert fired, "the failure was never injected into the watcher's inbox writer"
    for seconds in (150, 180):
        assert watched.at(seconds) == "watching"
    texts = watched.texts()
    assert len(texts) == 2
    assert f"(seen {_seen(120)})" in texts[1]

    _new_turn(watched, CLAUDE, at=200)
    assert watched.at(210) == "watching"
    _ends_turn(watched, CLAUDE, "waiting-for-input", 3, at=220)
    assert watched.at(240) == "watching"
    texts = watched.texts()
    assert len(texts) == 3 and WAITING_WORDS in texts[2]
    assert len(set(texts)) == 3


def test_delivered_finds_only_an_exact_recent_message_from_that_sender(tmp_path: Path) -> None:
    """``run_record.delivered`` is what a retry asks. Falsifier: it matching another sender,
    another recipient, a prefix or a near text (a notice taken as delivered when it was not),
    missing a text that needed escaping, failing on an absent inbox, or reading an unbounded
    inbox (only the last 64 KiB is read)."""

    root = tmp_path / "run"
    root.mkdir()
    text = 'FACTORY_LANE_TURN_ENDED tester: it said "done"\nand \\stopped (seen now)'
    assert not run_record.delivered(root, "validator", "host", text)
    run_record.message(root, "validator", "host", text)
    assert run_record.delivered(root, "validator", "host", text)
    assert not run_record.delivered(root, "validator", "founder", text)
    assert not run_record.delivered(root, "orchestrator", "host", text)
    assert not run_record.delivered(root, "validator", "host", text[:-1])
    assert not run_record.delivered(root, "validator", "host", text + " ")
    assert not run_record.delivered(root, "validator", "host", "FACTORY_LANE_TURN_ENDED")

    filler = "x" * 1000
    for index in range(90):  # about 90 KiB of later rows
        run_record.message(root, "validator", "host", f"{filler} {index}")
    assert run_record.delivered(root, "validator", "host", f"{filler} 89")
    assert not run_record.delivered(root, "validator", "host", text)


def test_a_notice_says_when_its_turn_end_was_seen(tmp_path: Path) -> None:
    """Falsifier: no seen time (two notices for like turn ends read the same, and a retry's
    exact-text check would take the second for the first); the time of delivery given instead
    of the announcing check's; a time in another form than the events'."""

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    watched = _session(tmp_path / "lane", CURSOR, None, 0)
    _block(watched, "symlink", elsewhere)
    assert watched.at(60) == "watching"
    _ends_turn(watched, CURSOR, "stop-reported", 1, at=100)
    assert watched.at(120) == "watching"
    _new_turn(watched, CURSOR, at=130)
    assert watched.at(150) == "watching"
    _ends_turn(watched, CURSOR, "stop-reported", 2, at=160)
    assert watched.at(180) == "watching"
    _unblock(watched, "symlink")
    assert watched.at(360) == "watching"

    first, second = watched.texts()
    assert f" (seen {_seen(120)})" in first and _seen(180) not in first
    assert f" (seen {_seen(180)})" in second and _seen(120) not in second
    assert _seen(360) not in first + second
    assert [row["ts"] for row in watched.events("lane-turn-ended")] == [_seen(120), _seen(180)]
    assert first != second


def _seen_order(rows: list[dict[str, Any]]) -> list[str]:
    """The seen times the wake rows carry, in the order they were written."""
    return [match.group(1) for row in rows
            for match in [re.search(r"\(seen ([^)]+)\)", str(row.get("detail", "")))] if match]


def test_a_locked_events_log_queues_the_wake_but_not_the_message(tmp_path: Path) -> None:
    """Falsifier: a check that blocks on another writer of the events log, drops the wake it
    could not write, wakes again for a turn end already woken, or wakes out of order; the
    Validator's message held back with the wake."""

    watched = _session(tmp_path / "lane", CLAUDE, None, 0)
    assert watched.at(30) == "watching"
    holder = HeldLock(watched.root / "events.jsonl")
    try:
        for count, phase, at in ((1, "stop-reported", 60), (2, "waiting-for-input", 120)):
            if count > 1:
                _new_turn(watched, CLAUDE, at=at - 50)
            _ends_turn(watched, CLAUDE, phase, count, at=at - 20)
            outcome, took = _timed_check(watched, at)
            assert outcome == "watching" and took < 8, took
            assert watched.events("lane-turn-ended") == []
            assert watched.wakes("lane-turn-ended") == []
            assert len(watched.texts()) == count  # the message does not wait for the wake
    finally:
        holder.release()

    assert watched.at(150) == "watching"
    expected = [_seen(60), _seen(120)]
    assert _seen_order(watched.events("lane-turn-ended")) == expected
    assert _seen_order(watched.wakes("lane-turn-ended")) == expected
    first, second = watched.texts()
    assert f"(seen {_seen(60)})" in first and f"(seen {_seen(120)})" in second
    assert watched.at(180) == "watching"
    assert len(watched.events("lane-turn-ended")) == len(watched.wakes("lane-turn-ended")) == 2
    assert len(watched.texts()) == 2


@pytest.mark.parametrize("status", ["exited", "stalled", "paused", "new-life"])
def test_a_queued_wake_is_given_whatever_the_watch_status(tmp_path: Path, status: str) -> None:
    """Falsifier: a wake queued behind a locked events log dropped once the lane is finished,
    stopped for a stall, paused, or watched by a new life, or given twice."""

    options: dict[str, Any] = {"watch": REVIEWED, "thread": True} if status == "paused" else {}
    watched = _session(tmp_path / "lane", CLAUDE, None, 0, **options)
    assert watched.at(30) == "watching"
    holder = HeldLock(watched.root / "events.jsonl")
    try:
        _ends_turn(watched, CLAUDE, "stop-reported", 1, at=40)
        outcome, took = _timed_check(watched, 60)
        assert outcome == "watching" and took < 8, took
        assert watched.wakes("lane-turn-ended") == []
        if status == "new-life":  # a resumed turn's watcher takes over the queue
            watched.clock.value = T0 + 90
            watched.watch.arm(new_life=True)
    finally:
        holder.release()

    if status == "exited":
        lane_agent.record_exit(watched.exits, slot="tester", rc=1, stopped=False)
        watched.kill()
        reached = 90
    elif status == "stalled":
        reached = STALL_AT + 1
    elif status == "paused":
        reached = 1080
    else:
        reached = 120
    expected = "watching" if status == "new-life" else status
    for seconds in (reached, reached + 30, reached + 60):
        assert watched.at(seconds) == expected
    assert _seen_order(watched.events("lane-turn-ended")) == [_seen(60)]
    assert _seen_order(watched.wakes("lane-turn-ended")) == [_seen(60)]
    assert len(watched.texts()) == 1


def test_with_the_events_log_and_the_inbox_locked_a_check_is_still_bounded(
    tmp_path: Path,
) -> None:
    """Falsifier: a check that waits on both locks for each notice (or without bound); a
    turn end whose wake or message is lost, doubled or reordered once both are released; a
    failed message recorded more than once. (Whether a failure that cleared with both locks
    is recorded at all, the contract does not say.)"""

    watched = _session(tmp_path / "lane", CLAUDE, None, 0)
    assert watched.at(30) == "watching"
    events = HeldLock(watched.root / "events.jsonl")
    inbox = HeldLock(watched.root / "validator" / "inbox.jsonl")
    try:
        for count, phase, at in ((1, "stop-reported", 60), (2, "waiting-for-input", 120)):
            if count > 1:
                _new_turn(watched, CLAUDE, at=at - 50)
            _ends_turn(watched, CLAUDE, phase, count, at=at - 20)
            outcome, took = _timed_check(watched, at)
            assert outcome == "watching" and took < 13, took
            assert watched.wakes("lane-turn-ended") == [] and watched.texts() == []
    finally:
        events.release()
        inbox.release()

    assert watched.at(150) == "watching"
    expected = [_seen(60), _seen(120)]
    assert _seen_order(watched.events("lane-turn-ended")) == expected
    assert _seen_order(watched.wakes("lane-turn-ended")) == expected
    first, second = watched.texts()
    assert STOP_WORDS in first and f"(seen {_seen(60)})" in first
    assert WAITING_WORDS in second and f"(seen {_seen(120)})" in second
    assert watched.at(180) == "watching"
    assert len(watched.wakes("lane-turn-ended")) == 2 and len(watched.texts()) == 2
    assert watched.undelivered() <= 2


def test_undelivered_rows_wait_for_the_events_log_and_are_recorded_once(tmp_path: Path) -> None:
    """Falsifier: a ``lane-turn-notice-undelivered`` row written around the events log's lock,
    or waited for without bound, or lost while the lock is held, or recorded at every
    check once it is released."""

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    watched = _session(tmp_path / "lane", CLAUDE, None, 0)
    _block(watched, "symlink", elsewhere)
    assert watched.at(30) == "watching"
    holder = HeldLock(watched.root / "events.jsonl")
    try:
        for count, phase, at in ((1, "stop-reported", 60), (2, "waiting-for-input", 120)):
            if count > 1:
                _new_turn(watched, CLAUDE, at=at - 50)
            _ends_turn(watched, CLAUDE, phase, count, at=at - 20)
            outcome, took = _timed_check(watched, at)
            assert outcome == "watching" and took < 13, took
            assert watched.undelivered() == 0 and watched.wakes("lane-turn-ended") == []
    finally:
        holder.release()

    assert watched.at(150) == "watching"
    assert watched.undelivered() == 2
    assert len(watched.wakes("lane-turn-ended")) == 2 and watched.texts() == []
    assert watched.at(180) == "watching"
    assert watched.undelivered() == 2
    _unblock(watched, "symlink")
    assert watched.at(210) == "watching"
    first, second = watched.texts()
    assert STOP_WORDS in first and WAITING_WORDS in second
    assert watched.at(240) == "watching"
    assert watched.undelivered() == 2 and len(watched.texts()) == 2
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("hook", ["wake-echo", "failing-message"])
def test_an_undelivered_row_waits_on_the_events_log_only_so_long(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hook: str,
) -> None:
    """The turn end's wake is written; then, before the inbox write fails, another process
    takes the events log's lock, so the undelivered row is the write that meets it. The lock
    is taken from the wake's echo line (with a symlinked inbox) or from a failing
    ``run_record.message`` on the watcher's own module. Falsifier: the undelivered row waiting
    on the lock without bound (the watcher stops watching), written around the lock, lost, or
    written again at every check; the message lost or doubled once the inbox works."""

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    watched = _session(tmp_path / "lane", CLAUDE, None, 0)
    events = watched.root / "events.jsonl"
    holders: list[HeldLock] = []
    broken = [True]

    def lock_events() -> None:
        if not holders:  # returns once the other process holds the lock
            holders.append(HeldLock(events))

    assert watched.at(30) == "watching"
    if hook == "wake-echo":
        _block(watched, "symlink", elsewhere)

        def echo(line: str) -> None:
            watched.said.append(line)
            if line.startswith("FACTORY_WAKE lane-turn-ended"):
                lock_events()

        watched.watch.echo = echo
        count, at = 1, 60
    else:
        _ends_turn(watched, CLAUDE, "stop-reported", 1, at=40)
        assert watched.at(60) == "watching"
        assert len(watched.texts()) == 1  # the watcher's inbox writer is loaded now

        def make(real: Any, fired: list[str]) -> Any:
            def failing(*args: Any, **kwargs: Any) -> Any:
                if broken[0]:
                    fired.append("message")
                    lock_events()
                    raise OSError("the inbox cannot be written")
                return real(*args, **kwargs)
            return failing

        _on_the_watchers_writer("message", monkeypatch, make)
        _new_turn(watched, CLAUDE, at=70)
        assert watched.at(90) == "watching"
        count, at = 2, 120

    try:
        _ends_turn(watched, CLAUDE, "waiting-for-input", count, at=at - 20)
        outcome, took = _timed_check(watched, at)
        assert holders, "the events log was never locked between the wake and the inbox write"
        assert outcome == "watching" and took < 8, took
        assert _seen(at) in _seen_order(watched.wakes("lane-turn-ended"))  # the wake went first
        assert watched.undelivered() == 0
    finally:
        for holder in holders:
            holder.release()

    assert watched.at(at + 30) == "watching"
    assert watched.undelivered() == 1
    assert watched.at(at + 60) == "watching"
    assert watched.undelivered() == 1
    assert len(watched.texts()) == count - 1
    if hook == "wake-echo":
        _unblock(watched, "symlink")
    else:
        broken[0] = False
    assert watched.at(at + 90) == "watching"
    texts = watched.texts()
    assert len(texts) == count and f"(seen {_seen(at)})" in texts[-1]
    assert watched.at(at + 120) == "watching"
    assert len(watched.texts()) == count and watched.undelivered() == 1
    assert _seen_order(watched.wakes("lane-turn-ended")).count(_seen(at)) == 1
    assert list(elsewhere.iterdir()) == []


FLICKER = (
    "import fcntl, os, select, sys\n"
    "path, hold = sys.argv[1], float(sys.argv[2])\n"
    "fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX)\n"
    "print('locked', flush=True)\n"
    "while not select.select([sys.stdin], [], [], hold)[0]:\n"
    "    size = os.stat(path).st_size\n"
    "    fcntl.flock(fd, fcntl.LOCK_UN)\n"
    "    while os.stat(path).st_size == size:\n"
    "        if select.select([sys.stdin], [], [], 0.001)[0]:\n"
    "            sys.exit(0)\n"
    "    fcntl.flock(fd, fcntl.LOCK_EX)\n"
)


class FlickeringLock(HeldLock):
    """Another process that holds the lock, releases it just before each 5 s timeout, and
    takes it again as soon as one write has landed."""

    def __init__(self, path: Path, hold_s: float = 4.5) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.process = subprocess.Popen(
            [sys.executable, "-I", "-c", FLICKER, str(path), str(hold_s)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        assert self.process.stdout is not None
        assert self.process.stdout.readline().strip() == "locked"


@pytest.mark.parametrize("locks", ["events-log", "inbox", "both"])
def test_owed_notices_wait_on_a_flickering_lock_one_allowance_per_check(
    tmp_path: Path, locks: str,
) -> None:
    """Codex's case: three notices are owed, and another process lets each write through just
    before its timeout and takes the lock again before the next. A check waits on each lock for
    one allowance in all. Falsifier: an allowance per write (three wakes or three messages take
    about 13.5 s, and the watcher stalls for as long), or notices lost, doubled or reordered."""

    events_down, inbox_down = locks in {"events-log", "both"}, locks in {"inbox", "both"}
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    watched = _session(tmp_path / "lane", CLAUDE, None, 0)
    events = watched.root / "events.jsonl"
    inbox = watched.root / "validator" / "inbox.jsonl"
    assert watched.at(30) == "watching"
    # An outage queues three notices: the events log is not a file, the inbox is not writable.
    kept = events.read_bytes() if events.exists() else b""
    if events_down:
        events.unlink(missing_ok=True)
        events.mkdir()
    if inbox_down:
        _block(watched, "symlink", elsewhere)
    for count, at in ((1, 60), (2, 120), (3, 180)):
        _ends_turn(watched, CLAUDE, "stop-reported", count, at=at - 10)
        assert watched.at(at) == "watching"
    if events_down:
        events.rmdir()
        events.write_bytes(kept)
    if inbox_down:
        _unblock(watched, "symlink")
    owed = [_seen(60), _seen(120), _seen(180)]
    assert len(watched.texts()) == (0 if inbox_down else 3)
    assert len(watched.wakes("lane-turn-ended")) == (0 if events_down else 3)

    holders = [FlickeringLock(path) for path, down in ((events, events_down),
                                                       (inbox, inbox_down)) if down]
    try:
        outcome, took = _timed_check(watched, 240)
        assert outcome == "watching"
        assert took < (13 if locks == "both" else 8), took
    finally:
        for holder in holders:
            holder.release()
    for seconds in (270, 300):
        assert watched.at(seconds) == "watching"
    assert _seen_order(watched.wakes("lane-turn-ended")) == owed
    assert _seen_order(watched.events("lane-turn-ended")) == owed
    assert [re.search(r"\(seen ([^)]+)\)", text)[1] for text in watched.texts()] == owed
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize(("inbox_hold_s", "delivered", "bound"), [
    (6.5, True, 9.0),
    (9.5, False, 10.5),
], ids=["within-its-own-allowance", "beyond-its-own-allowance"])
def test_the_events_log_and_the_inbox_each_have_their_own_allowance(
    tmp_path: Path, inbox_hold_s: float, delivered: bool, bound: float,
) -> None:
    """At a turn end another process holds the events log's lock about 3 s into the check and
    the inbox's lock about ``inbox_hold_s``. The wake gets through after about 3 s; the message
    then needs about 3.5 s more, within the inbox's own 5 s, and is delivered at that check.
    Falsifier: one allowance shared by both locks (about 2 s left for the inbox, so the message
    fails). Control: an inbox held about 6.5 s past the wake is beyond its own allowance, so the
    message waits for the next check (the allowance is bounded, not merely separate)."""

    watched = _session(tmp_path, CLAUDE, None, 0)
    assert watched.at(30) == "watching"
    _ends_turn(watched, CLAUDE, "stop-reported", 1, at=40)
    inbox = watched.root / "validator" / "inbox.jsonl"
    # The inbox holder starts first, so its hold, counted from the check, is if anything shorter.
    holders = [HeldLock(inbox, hold_s=inbox_hold_s)]
    try:
        holders.append(HeldLock(watched.root / "events.jsonl", hold_s=3.0))
        outcome, took = _timed_check(watched, 60)
        assert outcome == "watching"
        assert took < bound, took
        assert _seen_order(watched.wakes("lane-turn-ended")) == [_seen(60)]
        if delivered:
            [text] = watched.texts()
            assert f"(seen {_seen(60)})" in text
            assert watched.undelivered() == 0
        else:
            assert watched.texts() == [] and watched.undelivered() == 1
    finally:
        for holder in holders:
            holder.release()
    assert watched.at(90) == "watching"
    assert watched.notices() == 1 and watched.undelivered() == (0 if delivered else 1)


@pytest.mark.parametrize("lock", ["inbox", "events-log"])
def test_each_check_on_the_same_watcher_has_a_fresh_allowance_per_lock(
    tmp_path: Path, lock: str,
) -> None:
    """Production runs every check on one ``LaneWatch``. Check 1: the lock is held through its
    whole allowance, so the notice (its message, or its wake) is not given. Check 2, 30 s later
    on the same object: the lock is held about 2 s and released. Falsifier: the allowance spent
    once per watcher rather than per check, so check 2 starts with nothing left and fails."""

    watched = _session(tmp_path, CLAUDE, None, 0)
    watch = watched.watch
    assert watched.at(30) == "watching"
    _ends_turn(watched, CLAUDE, "stop-reported", 1, at=40)
    path = (watched.root / "validator" / "inbox.jsonl" if lock == "inbox"
            else watched.root / "events.jsonl")
    holder = HeldLock(path)
    try:
        outcome, took = _timed_check(watched, 60)
        assert outcome == "watching" and took < 8, took
        if lock == "inbox":
            assert watched.texts() == [] and watched.undelivered() == 1
        else:
            assert watched.wakes("lane-turn-ended") == [] and len(watched.texts()) == 1
    finally:
        holder.release()

    holder = HeldLock(path, hold_s=2.0)
    try:
        outcome, took = _timed_check(watched, 90)
    finally:
        holder.release()
    assert watched.watch is watch  # both checks ran on one watcher
    assert outcome == "watching" and took < 7, took
    assert watched.notices() == 1
    assert [f"(seen {_seen(60)})" in text for text in watched.texts()] == [True]
    assert _seen_order(watched.wakes("lane-turn-ended")) == [_seen(60)]
    assert watched.undelivered() == (1 if lock == "inbox" else 0)


def test_the_event_writer_gives_up_on_a_held_lock_only_when_asked(tmp_path: Path) -> None:
    """Falsifier: ``wait_s`` ignored (a supervisor blocks on another writer), a partial or late
    row written after giving up, or the default writer giving up too."""

    path = tmp_path / "run" / "events.jsonl"
    path.parent.mkdir()
    path.write_text('{"kind":"before"}\n', encoding="utf-8")
    path.chmod(0o600)
    holder = HeldLock(path)
    errors: list[BaseException] = []

    def patient() -> None:
        try:
            attention_gate._append_jsonl(path, {"kind": "patient"})
        except BaseException as error:  # reported below
            errors.append(error)

    writer = threading.Thread(target=patient, daemon=True)
    try:
        started = time.monotonic()
        with pytest.raises(attention_gate.AttentionGateError):
            attention_gate._append_jsonl(path, {"kind": "hasty"}, wait_s=1.0)
        took = time.monotonic() - started
        assert 0.9 <= took < 4, took
        assert path.read_text(encoding="utf-8") == '{"kind":"before"}\n'
        writer.start()
        writer.join(timeout=2.0)
        assert writer.is_alive(), errors
        assert path.read_text(encoding="utf-8") == '{"kind":"before"}\n'
    finally:
        holder.release()
    writer.join(timeout=15)
    assert not writer.is_alive() and errors == []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["kind"] for row in rows] == ["before", "patient"]


def _on_the_watchers_writer(name: str, monkeypatch: pytest.MonkeyPatch,
                            make: Any) -> list[str]:
    """Replace ``name`` on each loaded run_record module (the watcher loads the harness
    modules by their own names) with ``make(real, fired)``; returns the list that records
    each injected failure."""

    fired: list[str] = []
    modules = {id(module): module for key, module in list(sys.modules.items())
               if key in {"run_record", "harness.run_record"}}
    for module in modules.values():
        monkeypatch.setattr(module, name, make(getattr(module, name), fired))
    return fired


@pytest.mark.parametrize("dies", ["before-the-write", "after-the-row-is-committed"])
@pytest.mark.parametrize("successor", ["fresh-check", "rearmed"])
def test_a_watcher_that_dies_mid_delivery_gives_each_notice_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dies: str, successor: str,
) -> None:
    """The queued notice and its noticed count are on record before anything is told.
    Falsifier: state written after the delivery, so the next watcher announces the turn end
    again (a second message, or one with a new seen time) or never; a row committed just before
    the death written again; the wake lost. (The wake is not held to exactly once here: the
    contract says "each notice", and a wake written just before the death is given again.)"""

    watched = _session(tmp_path, CLAUDE, None, 0)
    assert watched.at(30) == "watching"
    _ends_turn(watched, CLAUDE, "stop-reported", 1, at=40)
    assert watched.at(60) == "watching"
    assert len(watched.texts()) == 1  # the watcher's inbox writer is loaded now

    if dies == "before-the-write":
        def make(real: Any, fired: list[str]) -> Any:
            def dying(*args: Any, **kwargs: Any) -> Any:
                if not fired:
                    fired.append("message")
                    raise SystemExit("the watchdog was killed")
                return real(*args, **kwargs)
            return dying
        fired = _on_the_watchers_writer("message", monkeypatch, make)
    else:
        def make(real: Any, fired: list[str]) -> Any:
            def dying(*args: Any, **kwargs: Any) -> Any:
                result = real(*args, **kwargs)
                target = Path(args[0] if args else kwargs["path"])
                if not fired and target.name == "inbox.jsonl":
                    fired.append(str(target))
                    raise SystemExit("the watchdog was killed")
                return result
            return dying
        fired = _on_the_watchers_writer("append_record", monkeypatch, make)

    _new_turn(watched, CLAUDE, at=70)
    assert watched.at(90) == "watching"
    _ends_turn(watched, CLAUDE, "waiting-for-input", 2, at=100)
    try:
        watched.at(120)
    except SystemExit:
        pass
    assert fired, "the death was never injected into the watcher's inbox writer"

    watched.watch = watched._watch()  # the next watcher process
    if successor == "rearmed":
        watched.watch.arm()
    for seconds in (150, 180):
        assert watched.at(seconds) == "watching"
    first, second = watched.texts()
    assert WAITING_WORDS in second and f"(seen {_seen(120)})" in second
    assert len(watched.texts()) == 2
    for rows in (watched.wakes("lane-turn-ended"), watched.events("lane-turn-ended")):
        assert set(_seen_order(rows)) == {_seen(60), _seen(120)}
        assert _seen_order(rows)[0] == _seen(60)


def test_a_committed_notice_is_found_however_much_the_inbox_grew_before_the_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falsifier: the retry searching only the inbox's last 64 KiB, so a notice whose row was
    committed before the write failed, and then buried under other senders' messages, is
    written again."""

    watched = _session(tmp_path, CLAUDE, None, 0)
    assert watched.at(30) == "watching"
    _ends_turn(watched, CLAUDE, "stop-reported", 1, at=40)
    assert watched.at(60) == "watching"
    assert len(watched.texts()) == 1

    def make(real: Any, fired: list[str]) -> Any:
        def late(*args: Any, **kwargs: Any) -> Any:
            result = real(*args, **kwargs)
            target = Path(args[0] if args else kwargs["path"])
            if not fired and target.name == "inbox.jsonl":
                fired.append(str(target))
                raise OSError("the disk went away after the row was written")
            return result
        return late

    fired = _on_the_watchers_writer("append_record", monkeypatch, make)
    _new_turn(watched, CLAUDE, at=70)
    assert watched.at(90) == "watching"
    _ends_turn(watched, CLAUDE, "waiting-for-input", 2, at=100)
    assert watched.at(120) == "watching"
    assert fired, "the failure was never injected into the watcher's inbox writer"
    filler = "y" * 1000
    for index in range(80):  # about 80 KiB from the founder before the retry
        run_record.message(watched.root, "validator", "founder", f"{filler} {index}")
    for seconds in (150, 180):
        assert watched.at(seconds) == "watching"
    texts = watched.texts()
    assert len(texts) == 2 and f"(seen {_seen(120)})" in texts[1]
    _new_turn(watched, CLAUDE, at=200)
    assert watched.at(210) == "watching"
    _ends_turn(watched, CLAUDE, "stop-reported", 3, at=220)
    assert watched.at(240) == "watching"
    assert len(watched.texts()) == 3


def test_inbox_end_is_the_inboxs_size(tmp_path: Path) -> None:
    """Falsifier: an offset that is not the inbox's size (a retry would search from the wrong
    place), or an error for an absent inbox."""

    root = tmp_path / "run"
    root.mkdir()
    assert run_record.inbox_end(root, "validator") == 0
    run_record.message(root, "validator", "host", "first")
    inbox = root / "validator" / "inbox.jsonl"
    assert run_record.inbox_end(root, "validator") == inbox.stat().st_size > 0
    run_record.message(root, "validator", "host", "second")
    assert run_record.inbox_end(root, "validator") == inbox.stat().st_size
    assert run_record.inbox_end(root, "orchestrator") == 0


@pytest.mark.parametrize("shape", ["folder", "symlinked-file", "symlinked-directory", "fifo"])
def test_inbox_end_is_zero_for_an_inbox_that_is_not_a_regular_file_in_a_real_directory(
    tmp_path: Path, shape: str,
) -> None:
    """Falsifier (agy's review of #124): a link followed, or a folder's own size taken, as the
    offset a message will be written at (a retry would then search from past its row)."""

    root = tmp_path / "run"
    root.mkdir()
    real = tmp_path / "real"
    real.mkdir()
    (real / "inbox.jsonl").write_text("x" * 5000)
    validator = root / "validator"
    if shape == "symlinked-directory":
        validator.symlink_to(real, target_is_directory=True)
    else:
        validator.mkdir()
        inbox = validator / "inbox.jsonl"
        if shape == "folder":
            inbox.mkdir()
            for index in range(50):
                (inbox / f"{index:03d}-{'n' * 100}").touch()
            assert inbox.stat().st_size > 0
        elif shape == "symlinked-file":
            inbox.symlink_to(real / "inbox.jsonl")
        else:
            os.mkfifo(inbox)
    assert run_record.inbox_end(root, "validator") == 0


def _filled_folder(path: Path) -> int:
    """A folder where the inbox should be, made large; returns its own size."""
    path.mkdir(parents=True)
    for index in range(200):
        (path / f"{index:03d}-{'n' * 200}").touch()
    return path.stat().st_size


@pytest.mark.parametrize("after_commit", ["the-write-fails", "the-watchdog-dies"])
def test_a_notice_first_tried_on_a_folder_inbox_is_found_once_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_commit: str,
) -> None:
    """Agy's case: the inbox is a folder at the notice's first attempt, so the offset saved for
    its retry is taken then. Later the inbox is a regular file, the row is committed at offset
    0, and the write fails (or the watchdog dies) after the commit; other messages then grow
    the inbox past the folder's size. Falsifier: the folder's own size saved as the offset, so
    the retry searches from past the committed row and sends the notice twice."""

    watched = _session(tmp_path, CLAUDE, None, 0)
    assert watched.at(30) == "watching"
    _ends_turn(watched, CLAUDE, "stop-reported", 1, at=40)
    assert watched.at(60) == "watching"
    assert len(watched.texts()) == 1  # the watcher's inbox writer is loaded now
    inbox = watched.root / "validator" / "inbox.jsonl"
    os.replace(inbox, inbox.with_name("earlier.jsonl"))
    folder_size = _filled_folder(inbox)
    assert folder_size > 0

    _new_turn(watched, CLAUDE, at=70)
    assert watched.at(90) == "watching"
    _ends_turn(watched, CLAUDE, "waiting-for-input", 2, at=100)
    assert watched.at(120) == "watching"  # the first attempt meets the folder
    assert watched.texts() == [] and watched.undelivered() == 1

    import shutil
    shutil.rmtree(inbox)  # a regular inbox from now on, written from its start

    def make(real: Any, fired: list[str]) -> Any:
        def late(*args: Any, **kwargs: Any) -> Any:
            result = real(*args, **kwargs)
            target = Path(args[0] if args else kwargs["path"])
            if not fired and target.name == "inbox.jsonl":
                fired.append(str(target))
                if after_commit == "the-watchdog-dies":
                    raise SystemExit("the watchdog was killed")
                raise OSError("the disk went away after the row was written")
            return result
        return late

    fired = _on_the_watchers_writer("append_record", monkeypatch, make)
    try:
        watched.at(150)
    except SystemExit:
        watched.watch = watched._watch()  # the next watcher process
    assert fired, "the failure was never injected into the watcher's inbox writer"
    [committed] = watched.texts()
    assert f"(seen {_seen(120)})" in committed
    assert json.loads(inbox.read_text().splitlines()[0])["text"] == committed  # at offset 0

    filler = "z" * 1000
    index = 0
    while inbox.stat().st_size <= folder_size + 4096:  # past the folder's size, and then some
        run_record.message(watched.root, "validator", "founder", f"{filler} {index}")
        index += 1
    for seconds in (180, 210, 240):
        assert watched.at(seconds) == "watching"
    assert watched.texts() == [committed]
    assert watched.undelivered() == 1


def test_a_message_with_a_wait_bounds_its_log_append_too(
    tmp_path: Path, capfd: pytest.CaptureFixture[str],
) -> None:
    """Falsifier (Codex's review of #124): ``wait_s`` bounding the inbox but not the optional
    log append, so a caller that must not block waits on the log's lock without bound; or the
    inbox row lost because the log was held."""

    root = tmp_path / "run"
    root.mkdir()
    log = tmp_path / "messages.log"
    holder = HeldLock(log, hold_s=20)
    try:
        start = time.monotonic()
        run_record.message(root, "validator", "host", "while the log is held",
                           log=str(log), wait_s=1.0)
        took = time.monotonic() - start
    finally:
        holder.release()
    assert took < 3.0, took
    rows = [json.loads(line) for line in
            (root / "validator" / "inbox.jsonl").read_text().splitlines()]
    assert [row["text"] for row in rows] == ["while the log is held"]
    assert log.read_bytes() == b""  # the append was skipped, not made around the lock
    assert capfd.readouterr().err.strip()

    run_record.message(root, "validator", "host", "with the log free", log=str(log),
                       wait_s=1.0)
    assert log.stat().st_size > 0  # positive control: a free log is appended to
    assert len((root / "validator" / "inbox.jsonl").read_text().splitlines()) == 2


def test_delivered_since_an_offset_finds_the_message_however_much_follows(
    tmp_path: Path,
) -> None:
    """Falsifier: ``since`` ignored (the last 64 KiB searched instead, so a buried row is
    missed), a row before the offset taken for this attempt's, or an offset past a now
    shorter inbox (rotated, truncated) finding nothing."""

    root = tmp_path / "run"
    root.mkdir()
    run_record.message(root, "validator", "host", "notice A")
    offset = run_record.inbox_end(root, "validator")
    run_record.message(root, "validator", "host", "notice B")
    filler = "z" * 1000
    for index in range(100):  # about 100 KiB after it
        run_record.message(root, "validator", "founder", f"{filler} {index}")
    assert run_record.delivered(root, "validator", "host", "notice B", since=offset)
    assert not run_record.delivered(root, "validator", "host", "notice B")
    assert not run_record.delivered(root, "validator", "host", "notice A", since=offset)
    assert not run_record.delivered(root, "validator", "founder", "notice B", since=offset)
    assert run_record.delivered(root, "validator", "founder", f"{filler} 99", since=offset)
    past = run_record.inbox_end(root, "validator") + 4096
    assert run_record.delivered(root, "validator", "founder", f"{filler} 99", since=past)
    assert not run_record.delivered(root, "validator", "host", "notice B", since=past)


@pytest.mark.parametrize("since", [None, 0], ids=["no-offset", "offset"])
@pytest.mark.parametrize("shape", ["symlink", "fifo"])
def test_delivered_neither_follows_a_symlink_nor_blocks_on_a_fifo(
    tmp_path: Path, shape: str, since: int | None,
) -> None:
    """Falsifier: a planted symlink's target read as the inbox (a notice taken as delivered
    that the Validator never got), or a FIFO opened for reading, which blocks the watcher
    until something writes to it."""

    root = tmp_path / "run"
    inbox = root / "validator" / "inbox.jsonl"
    inbox.parent.mkdir(parents=True, mode=0o700)
    text = "FACTORY_LANE_TURN_ENDED tester.r2: planted"
    if shape == "symlink":
        decoy = tmp_path / "decoy"
        decoy.mkdir()
        run_record.message(decoy, "validator", "host", text)
        inbox.symlink_to(decoy / "validator" / "inbox.jsonl")
    else:
        os.mkfifo(inbox)
    answers: list[bool] = []
    errors: list[BaseException] = []

    def ask() -> None:
        try:
            answers.append(run_record.delivered(root, "validator", "host", text, since=since))
        except BaseException as error:  # reported below
            errors.append(error)

    asker = threading.Thread(target=ask, daemon=True)
    asker.start()
    asker.join(timeout=5)
    try:
        assert not asker.is_alive(), "delivered() blocked"
        assert errors == [] and answers == [False]
    finally:
        if shape == "fifo" and asker.is_alive():  # free a reader stuck in open()
            os.close(os.open(inbox, os.O_WRONLY | os.O_NONBLOCK))


# -- 4. the stall rule -----------------------------------------------------------------------


@pytest.mark.parametrize("alive", [True, False], ids=["session-alive", "session-gone"])
def test_a_cursor_lane_is_stall_stopped_a_window_after_its_turn_end(
    tmp_path: Path, alive: bool,
) -> None:
    """Falsifier: a stop report taken as waiting (the session may continue past it and hang,
    and is then never stopped); a live session taken as waiting without saying so; the turn end
    not restarting the window (a lane that just answered stopped at main's boundary)."""

    watched = _session(tmp_path, CURSOR, None, 0, alive=alive)
    _commit(watched.repo, T0 + 300, "a")
    assert watched.at(600) == "watching"
    _ends_turn(watched, CURSOR, "stop-reported", 1, at=1500)
    assert watched.at(1500) == "watching"
    assert watched.notices() == 1
    assert watched.at(STALL_AT + 1) == "watching"  # main's boundary: max(900, 300) + 1200
    assert watched.at(1500 + STALL_WINDOW_S - 1) == "watching"
    assert watched.stops == [] and watched.stalls() == 0
    assert watched.at(1500 + STALL_WINDOW_S + 1) == "stalled"
    assert watched.stops == [TARGET]
    assert watched.stalls() == 1
    assert watched.at(9000) == "stalled" and watched.stops == [TARGET]


@pytest.mark.parametrize("shape", ["second-stop-in-one-prompt", "stop-after-asking-operator"])
def test_the_stall_window_restarts_at_the_check_that_announced_a_turn_end(
    tmp_path: Path, shape: str,
) -> None:
    """persistent_lane sets ``stop_reported_at`` only on the first stop after a prompt, so a
    second stop in the same prompt, or a stop after a question to the operator, leaves no
    newer timestamp. Falsifier: the window measured from the record alone, so the notice's
    deadline ("stopped as stalled 20 min after this turn end") is false and the lane is stopped
    early."""

    watched = _session(tmp_path, CLAUDE, None, 0)
    assert watched.at(600) == "watching"
    if shape == "second-stop-in-one-prompt":
        _ends_turn(watched, CLAUDE, "stop-reported", 1, at=1000)
        assert watched.at(1000) == "watching"
        watched.report(stop_reports=2)  # the session continued past its stop, and stopped again
    else:
        watched.report(phase="asking-operator")
        assert watched.at(1000) == "watching"
        watched.report(phase="stop-reported", stop_reports=1)  # stop_reported_at stays unset
    announced = 1800
    assert watched.at(announced) == "watching"
    assert DEADLINE.format(20) in watched.texts()[-1]
    assert watched.at(STALL_AT + 1) == "watching"
    assert watched.at(1000 + STALL_WINDOW_S + 1) == "watching"
    assert watched.at(announced + STALL_WINDOW_S - 1) == "watching"
    assert watched.stops == []
    assert watched.at(announced + STALL_WINDOW_S + 1) == "stalled"
    assert watched.stops == [TARGET] and watched.stalls() == 1


@pytest.mark.parametrize(("kind", "phase", "field", "identities"), [
    (CURSOR, "prompt-observed", "turn_started_at", True),
    (STREAM, "working", "turn_started_at", True),
    (STREAM, "idle", "turn_completed_at", False),
    (STREAM, "failed", "turn_completed_at", True),
], ids=["terminal-turn-started", "stream-turn-started", "stream-turn-completed",
        "stream-turn-failed"])
def test_the_stall_window_restarts_at_the_records_turn_timestamps(
    tmp_path: Path, kind: Kind, phase: str, field: str, identities: bool,
) -> None:
    """Falsifier: the record's ``turn_started_at`` or ``turn_completed_at`` ignored, so a lane
    given new work after a quiet spell is stopped minutes into its new turn; a failed turn
    taken as waiting although its session is alive. (The idle stream lane records no session
    identities, so it is not seen alive and is not waiting.)"""

    _, counter, _, _ = kind
    watched = _session(tmp_path, kind, phase, 0, identities=identities)
    assert watched.at(600) == "watching"
    watched.report(**{field: T0 + 1500, counter: 1 if field == "turn_completed_at" else 0})
    assert watched.at(1500) == "watching"
    assert watched.at(STALL_AT + 1) == "watching"
    assert watched.at(1500 + STALL_WINDOW_S - 1) == "watching"
    assert watched.stops == []
    assert watched.at(1500 + STALL_WINDOW_S + 1) == "stalled"
    assert watched.stops == [TARGET] and watched.stalls() == 1


@pytest.mark.parametrize("count", [DROP, "1", None], ids=["missing", "string", "null"])
@pytest.mark.parametrize(("kind", "phase", "identities"), [
    (CURSOR, "stop-reported", True), (STREAM, "idle", False),
], ids=["stop-reported_at", "turn_completed_at"])
def test_the_sessions_turn_end_timestamp_restarts_the_window_without_a_count(
    tmp_path: Path, kind: Kind, phase: str, identities: bool, count: object,
) -> None:
    """A record with a fresh ``stop_reported_at`` (or ``turn_completed_at``) but no integer
    count makes no notice, so no announcing check can restart the window; the timestamp,
    written between two checks, must. Falsifier: ``stop_reported_at`` or ``turn_completed_at``
    dropped from the window's marks (the lane stopped at main's boundary, minutes after its
    session ended a turn). The idle stream lane records no identities, so it is not waiting."""

    _, counter, stamp, mid = kind
    watched = Lane(tmp_path, record={"phase": mid}, agent=kind[0], identities=identities)
    assert watched.at(600) == "watching"
    assert watched.at(1490) == "watching"
    watched.report(phase=phase, **{stamp: T0 + 1500, counter: count})
    assert watched.at(1530) == "watching"
    assert watched.at(STALL_AT + 1) == "watching"
    assert watched.at(1500 + STALL_WINDOW_S - 1) == "watching"
    assert watched.stops == [] and watched.notices() == 0
    assert watched.at(1500 + STALL_WINDOW_S + 1) == "stalled"
    assert watched.stops == [TARGET] and watched.stalls() == 1


@pytest.mark.parametrize(("kind", "phase"), [(CLAUDE, "waiting-for-input"), (STREAM, "idle")],
                         ids=["claude-waiting-for-input", "stream-idle"])
def test_a_lane_waiting_for_input_with_its_session_alive_is_not_stall_stopped(
    tmp_path: Path, kind: Kind, phase: str,
) -> None:
    """Falsifier: the stall rule stopping a finished lane that waits for the Validator with no
    done line seen (the Cursor-Tester case, for a session that does say it is waiting)."""

    watched = _session(tmp_path, kind, None, 0)
    assert watched.at(600) == "watching"
    _ends_turn(watched, kind, phase, 1, at=700)
    for seconds in (700, STALL_AT + 1, 700 + STALL_WINDOW_S + 1, STALL_AT + 3 * 3600):
        assert watched.at(seconds) == "watching"
    assert watched.stops == [] and watched.stalls() == 0


@pytest.mark.parametrize("unobserved", [
    "no-identities", "snapshot-unavailable", "session-gone", "orphan-in-pane", "wrapper-gone",
    "worker-restarted", "worker-zombie", "pane-dead", "alive-only-at-an-earlier-check",
])
def test_a_lane_waiting_for_input_whose_session_is_not_seen_alive_is_stall_stopped(
    tmp_path: Path, unobserved: str,
) -> None:
    """Falsifier: a record that says waiting trusted without this check seeing the session's
    own worker and wrapper alive, so a dead lane with a stale record (or one the watcher cannot
    see) is watched forever; any process in the pane (an orphaned test run) taken for the
    session; a reused pid or a zombie taken as alive; liveness remembered from an earlier
    check."""

    watched = _session(tmp_path, CLAUDE, "waiting-for-input", 1,
                       identities=unobserved != "no-identities")
    watched.report(stop_reported_at=T0 - 5)
    early = unobserved not in {"no-identities", "alive-only-at-an-earlier-check"}
    if early:
        watched.unobserve(unobserved)
    assert watched.at(600) == "watching"
    assert watched.at(1200) == "watching"
    if unobserved == "alive-only-at-an-earlier-check":
        watched.unobserve("snapshot-unavailable")
    assert watched.at(STALL_AT - 1) == "watching"
    assert watched.stops == []
    outcome = watched.at(STALL_AT + 1)
    assert watched.stops == [TARGET]
    assert outcome in {"stalled", "stopping"}
    # Once the stopped lane is observable it is finished as stalled, once.
    watched.kill()
    assert watched.at(STALL_AT + 31) == "stalled"
    assert watched.stops == [TARGET] and watched.stalls() == 1


@pytest.mark.parametrize("seen", ["alive", "no-identities", "snapshot-unavailable"])
@pytest.mark.parametrize(("kind", "phase"), [
    (STREAM, "idle"), (CLAUDE, "stop-reported"), (CLAUDE, "waiting-for-input"),
], ids=["idle", "stop-reported", "waiting-for-input"])
def test_a_lane_that_printed_its_done_line_this_turn_and_ended_it_is_not_stall_stopped(
    tmp_path: Path, kind: Kind, phase: str, seen: str,
) -> None:
    """Falsifier: a lane that said it is done stopped while it waits for the Validator
    (DIS-2032), when its last report is a stop, or when its session cannot be seen (no recorded
    identities, no process table: main's rule, which tests/test_lane_controls.py encodes)."""

    watched = _session(tmp_path, kind, None, 0, identities=seen != "no-identities")
    if seen == "snapshot-unavailable":
        watched.unobserve(seen)
    assert watched.at(600) == "watching"
    _ends_turn(watched, kind, phase, 1, at=700, done=True)
    for seconds in (700, STALL_AT + 1, STALL_AT + 3 * 3600):
        assert watched.at(seconds) == "watching"
    assert watched.stops == [] and watched.stalls() == 0


@pytest.mark.parametrize("dead", ["session-gone", "wrapper-gone", "worker-restarted",
                                  "worker-zombie", "pane-dead"])
def test_a_done_line_does_not_exempt_a_session_seen_dead(tmp_path: Path, dead: str) -> None:
    """Falsifier: a done line keeping a lane watched forever although the process table shows
    its recorded session gone, replaced or a zombie."""

    watched = _session(tmp_path, STREAM, None, 0)
    watched.respond(DONE_LINE)
    assert watched.at(600) == "watching"
    _ends_turn(watched, STREAM, "idle", 1, at=700)
    assert watched.at(700) == "watching"
    watched.unobserve(dead)
    assert watched.at(STALL_AT - 1) == "watching"
    assert watched.stops == []
    assert watched.at(STALL_AT + 1) == "stalled"
    assert watched.stops == [TARGET] and watched.stalls() == 1


@pytest.mark.parametrize(("phase", "turns", "completed"), [
    ("working", 0, None),
    ("failed", 1, 1500),
])
def test_a_done_line_does_not_exempt_a_turn_that_has_not_ended_or_failed(
    tmp_path: Path, phase: str, turns: int, completed: int | None,
) -> None:
    """Falsifier: a done line exempting a lane whose session went on working after printing
    it, or whose turn then failed (stopped a window after the failure, not spared), with its
    session alive."""

    watched = _session(tmp_path, STREAM, None, 0)
    watched.respond(DONE_LINE)
    assert watched.at(600) == "watching"
    if completed is None:
        watched.report(phase=phase, turns=turns)
        boundary = STALL_AT
    else:
        assert watched.at(completed - 10) == "watching"
        watched.report(phase=phase, turns=turns, turn_completed_at=T0 + completed)
        assert watched.at(completed) == "watching"
        boundary = completed + STALL_WINDOW_S
    assert watched.at(boundary - 1) == "watching"
    assert watched.stops == []
    assert watched.at(boundary + 1) == "stalled"
    assert watched.stops == [TARGET] and watched.stalls() == 1


def test_a_done_line_from_an_earlier_turn_does_not_exempt_a_later_one(tmp_path: Path) -> None:
    """Falsifier: a done claim outliving its turn, so a lane given new work that never prints
    another done line (and is not seen alive: no recorded identities) is never stall-stopped."""

    watched = _session(tmp_path, STREAM, None, 0, identities=False)
    watched.respond(DONE_LINE)
    assert watched.at(200) == "watching"
    _ends_turn(watched, STREAM, "idle", 1, at=250)
    assert watched.at(250) == "watching"
    assert watched.at(STALL_AT + 1) == "watching"  # done this turn: waiting
    assert watched.at(2900) == "watching"

    _new_turn(watched, STREAM, at=3000)
    watched.tool_output("ran the suite")
    assert watched.at(3030) == "watching"
    _ends_turn(watched, STREAM, "idle", 2, at=3100)
    assert watched.at(3100) == "watching"
    assert watched.at(3100 + STALL_WINDOW_S - 1) == "watching"
    assert watched.stops == []
    assert watched.at(3100 + STALL_WINDOW_S + 1) == "stalled"
    assert watched.stops == [TARGET] and watched.stalls() == 1


@pytest.mark.parametrize("record", [
    {"phase": "prompt-observed", "stop_reports": 1},
    {"phase": "asking-operator", "stop_reports": 1},
    {"phase": "working", "turns": 1},
    None,
], ids=["prompt-observed", "asking-operator", "working", "no-persistent-record"])
def test_a_lane_mid_turn_or_without_a_persistent_record_is_stall_stopped_as_before(
    tmp_path: Path, record: dict[str, Any] | None,
) -> None:
    """Positive control: the same lane, busy or not persistent, with its session alive, is
    stopped at the unchanged boundary. Falsifier: the exemption widened to every lane, or to
    any lane whose session is alive."""

    watched = Lane(tmp_path, record=record)
    assert watched.at(STALL_AT - 1) == "watching"
    assert watched.stops == []
    assert watched.at(STALL_AT + 1) == "stalled"
    assert watched.stops == [TARGET]
    assert watched.stalls() == 1


# -- 5. review pause, wrap-up and caps -------------------------------------------------------

# A waiting lane of each kind: launch, the record it reaches, how its session is seen, and
# whether it printed a done line in that turn. All are resumable launches, so the watcher would
# otherwise offer them a review pause and a wrap-up (their positive controls below).
WAITING_LANES: dict[str, tuple[Kind, str, bool, bool]] = {
    "claude-waiting-for-input-alive": (CLAUDE, "waiting-for-input", True, False),
    "claude-stream-idle-alive": (CLAUDE_STREAM, "idle", True, False),
    "codex-stream-idle-alive": (CODEX_STREAM, "idle", True, False),
    "codex-stream-idle-done-unobserved": (CODEX_STREAM, "idle", False, True),
    "claude-stop-reported-done-alive": (CLAUDE, "stop-reported", True, True),
}
BUSY_LANES: dict[str, Kind] = {"claude-prompt-observed": CLAUDE, "codex-working": CODEX_STREAM}


def _waiting(tmp_path: Path, name: str, **options: Any) -> Lane:
    kind, phase, identities, done = WAITING_LANES[name]
    watched = _session(tmp_path, kind, None, 0, identities=identities, **options)
    _commit(watched.repo, T0 + 100, "a")
    assert watched.at(600) == "watching"
    _ends_turn(watched, kind, phase, 1, at=700, done=done)
    assert watched.at(730) == "watching"
    return watched


def _settle(watched: Lane, seconds: float, final: str) -> None:
    """The stop is requested at ``seconds`` and the lane finishes as ``final`` once it is seen
    to have stopped (at once, or at the next check)."""

    outcome = watched.at(seconds)
    assert watched.stops == [TARGET]
    if outcome == "stopping":
        outcome = watched.at(seconds + 30)
    assert outcome == final


@pytest.mark.parametrize("name", list(WAITING_LANES))
def test_a_waiting_lane_gets_no_wrap_up_and_is_capped_at_the_cap(
    tmp_path: Path, name: str,
) -> None:
    """Falsifier: a wrap-up interrupting a lane that is waiting for the Validator (its turn is
    already over: the interrupt and the generated notice would start unrequested work)."""

    watched = _waiting(tmp_path, name, caps={"wall_cap_s": 1800}, watch=RESUMABLE, thread=True)
    for seconds in (1499, 1500, 1530, 1799):
        assert watched.at(seconds) == "watching"
    assert watched.delivered == [] and watched.stops == []
    assert watched.events("lane-wrap-up") == []
    _settle(watched, 1800, "capped")
    assert watched.delivered == []


@pytest.mark.parametrize("name", list(BUSY_LANES))
def test_a_busy_resumable_lane_still_gets_its_wrap_up(tmp_path: Path, name: str) -> None:
    """Positive control for the test above (and proof its launches are resumable)."""

    watched = _session(tmp_path, BUSY_LANES[name], None, 0, caps={"wall_cap_s": 1800},
                       watch=RESUMABLE, thread=True)
    _commit(watched.repo, T0 + 100, "a")
    assert watched.at(1499) == "watching" and watched.delivered == []
    assert watched.at(1500) == "wrapping"
    assert watched.stops == [TARGET]
    [argv] = watched.delivered
    assert argv[1:5] == ["r1", "watchdog", "tester", "wrapup"]
    assert len(watched.events("lane-wrap-up")) == 1


@pytest.mark.parametrize("name", list(WAITING_LANES))
def test_a_waiting_lane_is_not_paused_for_review(tmp_path: Path, name: str) -> None:
    """Falsifier: a review pause ending the session of a lane that is waiting for the Validator
    (there is no live turn to pause)."""

    watched = _waiting(tmp_path, name, watch=REVIEWED, thread=True)
    for seconds in (1079, 1080, 1110, 2160, 3240):
        assert watched.at(seconds) == "watching"
    assert watched.stops == []
    assert watched.events("lane-review-due") == []


@pytest.mark.parametrize("name", list(BUSY_LANES))
def test_a_busy_resumable_lane_is_still_paused_for_review(tmp_path: Path, name: str) -> None:
    """Positive control for the test above."""

    watched = _session(tmp_path, BUSY_LANES[name], None, 0, watch=REVIEWED, thread=True)
    _commit(watched.repo, T0 + 100, "a")
    _commit(watched.repo, T0 + 1000, "b")
    assert watched.at(1079) == "watching"
    assert watched.at(1080) == "paused"
    assert watched.stops == [TARGET]
    assert len(watched.events("lane-review-due")) == 1


def test_a_review_that_fell_due_while_the_lane_waited_is_not_owed_afterwards(
    tmp_path: Path,
) -> None:
    """Review every 900 s; the lane waits from 180 s to 1080 s of active time and is then
    continued, so its next review is at 1800 s. Falsifier: the review that fell due at 900 s
    paused the lane the moment it was continued (interrupting the work the Validator just
    gave it); or the schedule restarted at the continuation (no review until 1980 s)."""

    watched = _session(tmp_path, CLAUDE, None, 0,
                       watch={"runs_dir": "/runs", "review_every_s": 900}, thread=True)
    assert watched.at(170) == "watching"
    _ends_turn(watched, CLAUDE, "waiting-for-input", 1, at=180)
    for seconds in (180, 900, 930, 1000):
        assert watched.at(seconds) == "watching"
    assert watched.stops == [] and watched.events("lane-review-due") == []

    _new_turn(watched, CLAUDE, at=1080)  # the Validator continues the lane
    for seconds in (1080, 1110, 1500, 1799):
        assert watched.at(seconds) == "watching"
    assert watched.stops == [] and watched.events("lane-review-due") == []
    assert watched.at(1800) == "paused"
    assert watched.stops == [TARGET]
    assert len(watched.events("lane-review-due")) == 1


def test_waiting_time_is_not_work_a_review_is_owed_for(tmp_path: Path) -> None:
    """Review every 1200 s, checks every 30 s; the lane waits from 60 s and is continued at
    1180 s, so 1140 s of its active time was waiting and its first review is due at about
    2340 s. Falsifier: the review owed at 1200 s of active time regardless (the old rule
    paused there, 20 s into the new work), or waiting time counted otherwise."""

    watched = _session(tmp_path, CLAUDE, None, 0,
                       watch={"runs_dir": "/runs", "review_every_s": 1200}, thread=True)
    for seconds in range(30, 2341, 30):
        if seconds == 60:
            _ends_turn(watched, CLAUDE, "waiting-for-input", 1, at=45)
        if seconds == 1200:
            _new_turn(watched, CLAUDE, at=1180)  # the Validator continues the lane
        expected = "paused" if seconds == 2340 else "watching"
        assert watched.at(seconds) == expected, seconds
    assert watched.stops == [TARGET]
    assert len(watched.events("lane-review-due")) == 1


def test_waiting_time_carries_into_a_new_life_after_a_review(tmp_path: Path) -> None:
    """Review every 600 s; 300 s of waiting puts the first pause at 900 s of active time, and
    the reviewed lane's next life owes its second review at 1500 s of active time (1200 s of
    work), not at 1200 s. Falsifier: the new life forgetting the waiting time."""

    watched = _session(tmp_path, CLAUDE, None, 0,
                       watch={"runs_dir": "/runs", "review_every_s": 600}, thread=True)
    for seconds in range(30, 901, 30):
        if seconds == 60:
            _ends_turn(watched, CLAUDE, "waiting-for-input", 1, at=45)
        if seconds == 360:
            _new_turn(watched, CLAUDE, at=345)
        expected = "paused" if seconds == 900 else "watching"
        assert watched.at(seconds) == expected, seconds

    # The Validator reviews the lane and continues it: a new life of the watcher.
    watched.revive()
    _new_turn(watched, CLAUDE, at=1500)
    watched.clock.value = T0 + 1500
    watched.watch.arm(new_life=True)
    for seconds in range(1530, 2101, 30):  # active time 930 s .. 1500 s
        expected = "paused" if seconds == 2100 else "watching"
        assert watched.at(seconds) == expected, seconds
    assert len(watched.events("lane-review-due")) == 2


def test_a_new_life_counts_the_time_since_a_held_last_check_as_held(tmp_path: Path) -> None:
    """Review every 600 s, checks every 30 s. The lane waits for input from 45 s; its
    watcher's last check (held) is at 300 s, and its next life starts at 1200 s with new work.
    The time to 1200 s was spent waiting, so the first review falls due about 600 s of work
    later (near 1750 s). Falsifier: the gap between the last held check and the new life
    counted as work, so the lane is paused for review at once (1230 s)."""

    watched = _session(tmp_path, CLAUDE, None, 0,
                       watch={"runs_dir": "/runs", "review_every_s": 600}, thread=True)
    for seconds in range(30, 301, 30):
        if seconds == 60:
            _ends_turn(watched, CLAUDE, "waiting-for-input", 1, at=45)
        assert watched.at(seconds) == "watching", seconds
    watched.clock.value = T0 + 1200
    _new_turn(watched, CLAUDE, at=1200)  # the Validator continues the lane: a new life
    watched.watch.arm(new_life=True)
    paused = None
    for seconds in range(1230, 2401, 30):
        outcome = watched.at(seconds)
        if outcome != "watching":
            assert outcome == "paused"
            paused = seconds
            break
    assert paused is not None and 1680 <= paused <= 1800, paused
    assert len(watched.events("lane-review-due")) == 1


@pytest.mark.parametrize("caps", [
    {"wall_cap_s": 1800},
    {"spend_cap_usd": "1", "usd_per_hour": "2"},
], ids=["wall-clock", "spend"])
@pytest.mark.parametrize("name", list(WAITING_LANES))
def test_a_waiting_lane_is_still_capped(
    tmp_path: Path, name: str, caps: dict[str, object],
) -> None:
    """Falsifier: the waiting exemption also lifting the wall-clock or spend cap."""

    watched = _waiting(tmp_path, name, caps=caps)
    assert watched.at(1799) == "watching"
    assert watched.stops == []
    _settle(watched, 1800, "capped")
    assert len(watched.events("refusal-lane-cap")) == 1


# -- 6. the exit -----------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "claim", "outcome"), [
    (CLAUDE_STREAM, "this-turn", "done"),
    (CLAUDE_STREAM, "earlier-turn", "exited"),
    (CLAUDE_STREAM, "turn-k-read-after-k-plus-1-began", "exited"),
    (CLAUDE_STREAM, "no-mark-before-it", "exited"),
    (CLAUDE, "this-turn", "done"),
    (CLAUDE, "unbound-stop", "exited"),
    (CLAUDE, "earlier-turn", "exited"),
], ids=["stream-this-turn", "stream-earlier-turn", "stream-turn-k-read-after-k-plus-1",
        "stream-no-mark", "terminal-this-turn", "terminal-unbound-stop",
        "terminal-earlier-turn"])
def test_a_persistent_lane_exits_done_only_on_a_done_line_of_its_current_turn(
    tmp_path: Path, kind: Kind, claim: str, outcome: str,
) -> None:
    """Falsifier: a verified exit reported as done on the strength of an earlier turn's done
    line (read before or after the next turn began), a response with no turn mark before it,
    or a stop the wrapper did not bind to the turn, so the Validator takes as finished a lane
    whose last turn never said so."""

    settled = "idle" if _stream(kind) else "stop-reported"
    watched = _session(tmp_path, kind, None, 0, identities=False,
                       marked=claim != "no-mark-before-it")
    assert watched.at(60) == "watching"
    if claim == "no-mark-before-it":
        watched.respond(DONE_LINE)
    _ends_turn(watched, kind, settled, 1, at=100, done=claim in {"this-turn", "earlier-turn",
                                                                 "unbound-stop"},
               bound=claim != "unbound-stop")
    if claim == "turn-k-read-after-k-plus-1-began":
        watched.respond(DONE_LINE)  # turn 1's last response, read with turn 2's mark
        _new_turn(watched, kind, at=150)
        _ends_turn(watched, kind, settled, 2, at=200)
    assert watched.at(210) == "watching"
    if claim == "earlier-turn":
        _new_turn(watched, kind, at=240)
        watched.tool_output("ran the suite")
        assert watched.at(250) == "watching"
        _ends_turn(watched, kind, settled, 2, at=260)
        assert watched.at(270) == "watching"
    lane_agent.record_exit(watched.exits, slot="tester", rc=0, stopped=False)
    watched.kill()
    assert watched.at(300) == outcome
    assert watched.stops == []


# -- 7. the watcher's run after a final verdict ----------------------------------------------


def _run(watched: Lane, script: dict[float, Any]) -> tuple[str, list[float]]:
    """``LaneWatch.run`` with its injectable sleep: each sleep advances the clock, and what
    ``script`` says happens at a time happens before the check at that time."""

    polls: list[float] = []
    pending = dict(script)

    def sleep(seconds: float) -> None:
        watched.clock.value += seconds
        polls.append(watched.clock.value - T0)
        for at in sorted(at for at in pending if watched.clock.value - T0 >= at):
            pending.pop(at)()

    watched.clock.value = T0
    verdict = watched.watch.run(sleep=sleep, launch_wait_s=0)
    return verdict, polls


def _exits(watched: Lane) -> Any:
    def exit_() -> None:
        lane_agent.record_exit(watched.exits, slot="tester", rc=0, stopped=False)
        watched.kill()
    return exit_


def test_run_returns_its_final_verdict_at_once_when_no_notice_is_owed(tmp_path: Path) -> None:
    """Falsifier: a watcher that polls on after its verdict with nothing to deliver."""

    watched = _session(tmp_path, CLAUDE, None, 0)
    verdict, polls = _run(watched, {
        60: lambda: _ends_turn(watched, CLAUDE, "stop-reported", 1, at=50),
        150: _exits(watched),
    })
    assert verdict == "exited"
    assert polls[-1] == 150  # the check at 150 s gave the verdict; no poll after it
    assert watched.notices() == 1


def test_run_delivers_a_notice_still_owed_after_its_final_verdict(tmp_path: Path) -> None:
    """The turn ends, the inbox cannot be written, the lane exits (a final verdict), and the
    inbox is mended 15 minutes later. Falsifier: the watcher returning at its verdict, so the
    turn end is never told; or the notice given twice."""

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    watched = _session(tmp_path / "lane", CLAUDE, None, 0)
    _block(watched, "symlink", elsewhere)
    verdict, polls = _run(watched, {
        60: lambda: _ends_turn(watched, CLAUDE, "stop-reported", 1, at=50),
        150: _exits(watched),
        1050: lambda: _unblock(watched, "symlink"),
    })
    assert verdict == "exited"
    assert polls[-1] == 1050  # returned once the owed notice was delivered
    [text] = watched.texts()
    assert f"(seen {_seen(60)})" in text
    assert watched.undelivered() == 1 and watched.notices() == 1
    assert list(elsewhere.iterdir()) == []


def test_run_gives_up_on_owed_notices_an_hour_after_its_final_verdict(tmp_path: Path) -> None:
    """Falsifier: a watcher that never returns while a notice cannot be delivered, or one that
    gives up before the drain time."""

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    watched = _session(tmp_path / "lane", CLAUDE, None, 0)
    _block(watched, "symlink", elsewhere)
    verdict, polls = _run(watched, {
        60: lambda: _ends_turn(watched, CLAUDE, "stop-reported", 1, at=50),
        150: _exits(watched),
    })
    assert verdict == "exited"
    assert lane_watchdog.TURN_NOTICE_DRAIN_S == 3600
    assert 150 + 3600 <= polls[-1] < 150 + 3600 + 30
    assert watched.texts() == [] and watched.undelivered() == 1
    assert list(elsewhere.iterdir()) == []


# -- 8. what the session wrappers write -------------------------------------------------------
#
# The real lane wrapper (harness/lane_agent.py) hosts a synthetic native program, as in
# tests/test_persistent_lane.py and tests/test_native_codex_queue_contract.py. The program only
# runs the wrapper's own hook commands with the reports each test asks for, in order; no model
# runs and nothing here reads the wrapper's code.

NATIVE = r"""
import json, os, pathlib, select, subprocess, sys, tty
folder = pathlib.Path(__file__).parent
args = sys.argv[1:]
if '--settings' in args:
    settings = json.loads(pathlib.Path(args[args.index('--settings') + 1]).read_text())
    flag = '--resume' if '--resume' in args else '--session-id'
    session = args[args.index(flag) + 1]
else:
    settings = json.loads((pathlib.Path.cwd() / '.codex/hooks.json').read_text())
    session = '12345678-1234-4234-8234-123456789abc'
(folder / 'session').write_text(session)
def commands(node, event, inside=False):
    if isinstance(node, dict):
        if inside and node.get('type') == 'command':
            yield node['command']
            return
        for key, value in node.items():
            yield from commands(value, event, inside or key == event)
    elif isinstance(node, list):
        for item in node:
            yield from commands(item, event, inside)
def report(event, **fields):
    payload = dict(hook_event_name=event, session_id=session, cwd=os.getcwd(), **fields)
    hooked, dialect = event, ''
    if event == 'PreInvocation':  # Antigravity's prompt report, through the same hook
        payload = dict(hookEventName=event, conversationId=session, **fields)
        hooked, dialect = 'UserPromptSubmit', ' --dialect agy'
    for command in commands(settings, hooked):
        subprocess.run(command + dialect, shell=True, input=json.dumps(payload), text=True,
                       check=True, timeout=10)
tty.setraw(0)
report('SessionStart', source='startup')
if '--settings' not in args:  # Codex binds its session by the launch prompt's own report
    report('UserPromptSubmit', turn_id='turn-initial', prompt=args[args.index('--') + 1])
    report('Stop', turn_id='turn-initial', stop_hook_active=False,
           last_assistant_message='initial reply')
processed = 0
while not (folder / 'exit').exists():
    if select.select([0], [], [], .02)[0]:
        if not os.read(0, 4096): break
    requests = folder / 'reports.jsonl'
    rows = requests.read_text().splitlines() if requests.exists() else []
    for row in rows[processed:]:
        payload = json.loads(row)
        report(payload.pop('event'), **payload)
        with (folder / 'reported').open('a') as out: out.write('.')
    processed = len(rows)
"""


class Native:
    """One real wrapper hosting the synthetic program: ``report`` makes the program report
    hook events, ``stops`` reads the wrapper's stop report rows from the lane log."""

    def __init__(self, tmp_path: Path, dialect: str) -> None:
        from tests.test_native_codex_queue_contract import WRAPPER

        harness = Path(__file__).resolve().parents[1] / "harness"
        self.root = tmp_path / "run"
        self.folder = self.root / "tmux-lanes"
        self.folder.mkdir(parents=True)
        (self.root / "harness.json").write_text(
            json.dumps({"run_id": "r", "tmux_socket": "fixture"}))
        project = tmp_path / "project"
        project.mkdir()
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        executable = self.folder / ("codex" if dialect == "codex" else "cl")
        executable.write_text(f"#!{sys.executable}\n" + NATIVE)
        executable.chmod(0o755)
        (self.root / "prompt").write_text("initial literal native message")
        home = tmp_path / "operator"
        home.mkdir()
        env = {**os.environ, "HOME": str(home), "CODEX_HOME": str(home / ".codex"),
               "PATH": str(self.folder) + os.pathsep + os.environ["PATH"]}
        if dialect == "codex":
            command = [sys.executable, "-c", WRAPPER, str(harness), str(self.root),
                       str(executable)]
        else:
            command = [sys.executable, str(harness / "lane_agent.py"), "run", "--slot", "coder",
                       "--log", str(self.folder / "coder-log"),
                       "--exits", str(self.folder / "coder-exits.jsonl"),
                       "--prompt-arg", str(self.root / "prompt"),
                       "--session-agent", "claude",
                       "--session-file", str(self.folder / "coder-thread-id"),
                       "--persistent", "--run-root", str(self.root), "--", str(executable)]
        self.output = (self.root / "wrapper-output").open("w")
        self.child = subprocess.Popen(command, cwd=project, env=env, stdout=self.output,
                                      stderr=subprocess.STDOUT)
        self.sent = 0
        settled = "stop-reported" if dialect == "codex" else "started"
        self.until(lambda: self.raw().get("phase") == settled, "the session start")

    def until(self, condition: Any, what: str, timeout: float = 10) -> None:
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                output = (self.root / "wrapper-output").read_text(errors="replace")[-2000:]
                raise AssertionError(f"no {what}: {self.raw()} {output}")
            time.sleep(0.02)

    def raw(self) -> dict[str, Any]:
        path = self.folder / "coder-persistent.json"
        try:
            return dict(json.loads(path.read_text()))
        except (OSError, ValueError):
            return {}

    def stops(self) -> list[dict[str, Any]]:
        path = self.folder / "coder-log"
        rows = []
        for line in (path.read_bytes().splitlines() if path.exists() else []):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("type") == "native-stop-report":
                rows.append(row)
        return rows

    def report(self, event: str, **fields: Any) -> dict[str, Any]:
        """The program reports one hook event; returns the record once the wrapper took it in
        (a stop: its stop report row is in the log)."""
        before = len(self.stops())
        reports = self.raw().get("stop_reports", 0)
        with (self.folder / "reports.jsonl").open("a") as out:
            out.write(json.dumps({"event": event, **fields}) + "\n")
        self.sent += 1
        reported = self.folder / "reported"
        self.until(lambda: reported.exists() and len(reported.read_text()) >= self.sent,
                   f"report of {event}")
        if event == "Stop":  # its row in the log and the record's count, in either order
            self.until(lambda: len(self.stops()) > before, "stop report row")
            self.until(lambda: self.raw().get("stop_reports", 0) > reports, "stop count")
        else:
            time.sleep(0.2)  # the wrapper reads its journal on its own time
        return self.raw()

    def prompt(self, text: str, **fields: Any) -> dict[str, Any]:
        before = self.raw().get("turn_started_at")
        self.report("UserPromptSubmit", prompt=text, **fields)
        self.until(lambda: self.raw().get("turn_started_at") != before, "new turn")
        return self.raw()

    def stop(self, text: str, **fields: Any) -> dict[str, Any]:
        self.report("Stop", stop_hook_active=fields.pop("stop_hook_active", False),
                    last_assistant_message=text, **fields)
        return self.stops()[-1]

    def close(self) -> None:
        (self.folder / "exit").touch()
        try:
            self.child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.child.terminate()
            self.child.wait(timeout=5)
        self.output.close()


@pytest.fixture(params=["codex", "claude"])
def native(tmp_path: Path, request: pytest.FixtureRequest) -> Any:
    lane = Native(tmp_path, request.param)
    lane.dialect = request.param  # type: ignore[attr-defined]
    try:
        yield lane
    finally:
        lane.close()


def _turn_fields(record: dict[str, Any]) -> tuple[Any, Any, Any]:
    return record.get("turn_stops"), record.get("turn_queued"), record.get("turn_generation")


def test_a_new_session_record_starts_its_turn_fields_empty(tmp_path: Path) -> None:
    """A Claude Code session that has started and reported no prompt. Falsifier: a new record
    without the turn fields, or starting them otherwise."""

    native = Native(tmp_path, "claude")
    try:
        assert _turn_fields(native.raw()) == (0, False, None)
    finally:
        native.close()


def test_each_stop_report_row_names_the_current_turn_and_counts_the_turns_stops(
    native: Native,
) -> None:
    """Falsifier: a stop row without the turn it arrived in, or naming another turn; the
    record's stop count not reset by a prompt report."""

    record = native.prompt("first")
    assert _turn_fields(record)[:2] == (0, False)
    first = native.stop("one")
    assert first["turn_started_at"] == record["turn_started_at"]
    assert native.raw()["turn_stops"] == 1
    second = native.stop("two")
    assert second["turn_started_at"] == record["turn_started_at"]
    assert native.raw()["turn_stops"] == 2
    later = native.prompt("second")
    assert later["turn_started_at"] != record["turn_started_at"]
    assert _turn_fields(later)[:2] == (0, False)
    assert native.stop("three")["turn_started_at"] == later["turn_started_at"]


def test_codexs_case_a_stop_from_before_a_queued_prompt_is_unbound(native: Native) -> None:
    """Prompt B is reported while A still runs, then A's stop arrives with A's done line.
    Falsifier: A's stop bound to B's turn (so A's done line would exempt B), or B's own stop
    left unbound. With generations (Codex) the stops are told apart by them; without
    (Claude Code), B was queued, so its first stop is unbound and its next bound."""

    generation = native.dialect == "codex"  # type: ignore[attr-defined]
    a = native.prompt("A", **({"turn_id": "turn-a"} if generation else {}))
    assert _turn_fields(a) == (0, False, "turn-a" if generation else None)
    b = native.prompt("B", **({"turn_id": "turn-b"} if generation else {}))
    assert _turn_fields(b) == (0, True, "turn-b" if generation else None)
    stop_a = native.stop(DONE_LINE, **({"turn_id": "turn-a"} if generation else {}))
    assert stop_a["bound"] is False
    assert stop_a["turn_started_at"] == b["turn_started_at"]
    assert DONE_LINE in stop_a["last_assistant_message"]
    stop_b = native.stop("B is done.", **({"turn_id": "turn-b"} if generation else {}))
    assert stop_b["bound"] is True and stop_b["turn_started_at"] == b["turn_started_at"]
    assert native.raw()["turn_stops"] == 2


@pytest.mark.parametrize(("prompt_id", "stop_ids", "bound"), [
    ("g1", ["g1", "g1"], [True, True]),
    ("g1", ["g0", "g1"], [False, True]),
    ("g1", ["g0", "g0"], [False, False]),
    ("g1", [None, None], [True, True]),
    (None, ["g1", "g1"], [True, True]),
], ids=["same", "earlier-then-same", "earlier-twice", "stop-without", "prompt-without"])
def test_a_stop_with_a_generation_is_bound_by_it_only_when_its_prompt_had_one(
    tmp_path: Path, prompt_id: str | None, stop_ids: list[str | None], bound: list[bool],
) -> None:
    """Falsifier: a generation ignored (an earlier turn's stop bound to this one), or required
    of a prompt or stop that carries none (a stop of an unqueued turn left unbound)."""

    native = Native(tmp_path, "codex")
    try:
        record = native.prompt("work", **({"turn_id": prompt_id} if prompt_id else {}))
        assert record["turn_generation"] == prompt_id
        rows = [native.stop(f"stop {n}", **({"turn_id": stop} if stop else {}))
                for n, stop in enumerate(stop_ids)]
        assert [row["bound"] for row in rows] == bound
    finally:
        native.close()


def test_without_generations_a_first_stop_is_bound_unless_queued_or_continued(
    native: Native,
) -> None:
    """Falsifier: a first stop after a prompt queued behind a running turn bound to it; a stop
    that ends a continuation (``stop_hook_active``) bound as the turn's own first stop; a
    later stop of the turn left unbound."""

    native.prompt("plain")
    assert [native.stop("1")["bound"], native.stop("2")["bound"]] == [True, True]

    native.prompt("running")
    queued = native.prompt("queued behind it")
    assert queued["turn_queued"] is True
    assert [native.stop("1")["bound"], native.stop("2")["bound"]] == [False, True]

    after = native.prompt("after a stop")
    assert after["turn_queued"] is False
    rows = [native.stop("1", stop_hook_active=True), native.stop("2", stop_hook_active=True)]
    assert [row["bound"] for row in rows] == [False, True]


def test_a_prompt_reported_while_the_operator_is_asked_is_queued(tmp_path: Path) -> None:
    """Claude Code reports a permission question (``asking-operator``) mid-turn, then a new
    prompt. Falsifier: the new turn's first stop bound, though it may end the questioned turn."""

    native = Native(tmp_path, "claude")
    try:
        native.prompt("running")
        native.report("Notification", notification_type="permission_prompt")
        native.until(lambda: native.raw().get("phase") == "asking-operator", "the question")
        queued = native.prompt("queued behind the question")
        assert queued["turn_queued"] is True
        assert [native.stop("1")["bound"], native.stop("2")["bound"]] == [False, True]
    finally:
        native.close()


def test_an_antigravity_repeat_of_the_same_prompt_resets_nothing(tmp_path: Path) -> None:
    """Antigravity reports the latest user message before every model call, numbered. The
    same message again (same digest, with its invocation number) is not a new prompt.
    Falsifier: a repeat starting a new turn (so the turn's later stop counts as its first and
    the stop count, the queued mark or the turn timestamp are reset)."""

    native = Native(tmp_path, "claude")
    try:
        native.report("PreInvocation", lastUserInput="same words", invocationNum=1)
        native.until(lambda: native.raw().get("phase") == "prompt-observed", "the prompt")
        first = native.raw()
        assert native.stop("one")["bound"] is True
        native.report("PreInvocation", lastUserInput="same words", invocationNum=2)
        again = native.raw()
        assert again["turn_started_at"] == first["turn_started_at"]
        assert _turn_fields(again) == (1, False, None)
        assert native.stop("two")["turn_started_at"] == first["turn_started_at"]
        assert native.raw()["turn_stops"] == 2
        later = native.prompt("other words")  # a different message is a new prompt
        assert later["turn_started_at"] != first["turn_started_at"]
        assert later["turn_stops"] == 0
    finally:
        native.close()


def test_the_stream_wrapper_marks_no_turn_whose_input_cannot_be_encoded(tmp_path: Path) -> None:
    """With the registry's real encoder (no agent has a stream transport) the first input
    cannot be encoded and is never sent. Falsifier: a turn mark in the lane log for an input
    that never reached the session."""

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))
    import persistent_lane

    root = tmp_path / "run"
    folder = root / "tmux-lanes"
    folder.mkdir(parents=True)
    (root / "harness.json").write_text(json.dumps({"run_id": "r", "tmux_socket": "fixture"}))
    child = subprocess.Popen([sys.executable, "-I", "-c", "import sys; sys.stdin.read()"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)

    def started(pid: int) -> str:
        return " ".join(lane_agent._started(pid).split())

    (folder / "coder-process.json").write_text(json.dumps({
        "schema": "factory-worker-process/1", "slot": "coder", "status": "running",
        "wrapper": {"pid": os.getpid(), "started": started(os.getpid())},
        "worker": {"pid": child.pid, "started": started(child.pid)}}))
    emitted: list[bytes] = []
    try:
        with pytest.raises(ValueError, match="does not support persistent stream input"):
            persistent_lane.run(child, root=root, slot="coder", agent="agy", prompt="initial",
                                session_file=folder / "thread", emit=emitted.append)
    finally:
        child.kill()
        child.wait(timeout=10)
    assert not any(b"persistent-turn-start" in line for line in emitted), emitted


def test_the_stream_wrapper_marks_a_turn_before_its_input_and_output(tmp_path: Path) -> None:
    """``persistent_lane.run`` writes the turn mark, with the record's ``turn_started_at``, to
    the lane log before the turn's input is sent and before any of its output. No registered
    agent has a stream transport (harness/agents.py), so the registry's frame encoder is
    stubbed at its seam. Falsifier: no mark, a mark after output, or one naming another turn."""

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))
    import agents
    import persistent_lane

    root = tmp_path / "run"
    folder = root / "tmux-lanes"
    folder.mkdir(parents=True)
    (root / "harness.json").write_text(json.dumps({"run_id": "r", "tmux_socket": "fixture"}))
    program = (
        "import json, sys\n"
        "print(json.dumps({'type': 'output-before-input'}), flush=True)\n"
        "for line in sys.stdin:\n"
        "    print(json.dumps({'type': 'input', 'line': line}), flush=True)\n"
    )
    child = subprocess.Popen([sys.executable, "-I", "-c", program], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def started(pid: int) -> str:
        return " ".join(lane_agent._started(pid).split())

    (folder / "coder-process.json").write_text(json.dumps({
        "schema": "factory-worker-process/1", "slot": "coder", "status": "running",
        "wrapper": {"pid": os.getpid(), "started": started(os.getpid())},
        "worker": {"pid": child.pid, "started": started(child.pid)}}))
    emitted: list[bytes] = []
    original = agents.persistent_input
    agents.persistent_input = lambda agent, text, message_id: {"type": "user", "text": text}
    worker = threading.Thread(target=lambda: _quietly(persistent_lane.run, child, root=root,
                                                      slot="coder", agent="agy",
                                                      prompt="initial",
                                                      session_file=folder / "thread",
                                                      emit=emitted.append), daemon=True)
    try:
        worker.start()
        deadline = time.monotonic() + 10
        while not any(b'"input"' in line for line in emitted):
            assert time.monotonic() < deadline, emitted
            time.sleep(0.02)
        rows = [json.loads(line) for line in emitted]
        assert rows[0]["type"] == "persistent-turn-start"
        record = json.loads((folder / "coder-persistent.json").read_text())
        assert rows[0]["turn_started_at"] == record["turn_started_at"]
        assert [row["type"] for row in rows].count("persistent-turn-start") == 1
        assert rows.index(rows[0]) < [row["type"] for row in rows].index("output-before-input")
    finally:
        agents.persistent_input = original
        child.kill()
        worker.join(10)


def _quietly(function: Any, *args: Any, **kwargs: Any) -> None:
    try:
        function(*args, **kwargs)
    except Exception:  # noqa: BLE001 - the driver's end once its child is killed
        pass
