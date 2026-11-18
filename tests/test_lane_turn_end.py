"""A persistent lane's turn end is told to the Validator once, through the reusable input path,
and a lane waiting on the Validator is not stall-stopped; its caps still stop it.

Regression (2026-11-17): a replay's Validator sat idle 20.6 minutes after a Cursor Tester
finished, because Cursor's stop report carries no text and nothing told the Validator.

The real ``LaneWatch`` runs against a synthetic run root, with doubles only at its declared
seams (clock, tmux, process table, stop, delivery), as in tests/test_lane_controls.py."""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from harness import lane_agent, lane_dialogue, lane_watchdog

T0 = 1_791_000_000.0

READ_ALLOWANCE_S = 900

STALL_WINDOW_S = 1200

STALL_AT = READ_ALLOWANCE_S + STALL_WINDOW_S

TARGET = "r1:tester"

PANE_PID = 4242

WRAPPER = {"pid": 5000, "started": "Tue Nov 17 09:59:58 2026"}

WORKER = {"pid": 5001, "started": "Tue Nov 17 09:59:59 2026"}

ORPHAN_PID = 6000

INIT = {1: {"pid": 1, "ppid": 0, "started": "Tue Nov 17 08:00:00 2026", "state": "S"}}

PREFIX = "FACTORY_LANE_TURN_ENDED "

DONE_LINE = "Report written.\n__LANE_DONE__ tester commits=0"

RESUMABLE = {"runs_dir": "/runs", "wrap_s": 300}

REVIEWED = {"runs_dir": "/runs", "review_every_s": 1080}

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
                 alive: bool = True, identities: bool = True, slot: str = "tester",
                 root: Path | None = None) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.slot = slot
        self.root = root or tmp_path / "run"  # another lane's run root, when given
        self.lanes = self.root / "tmux-lanes"
        self.lanes.mkdir(parents=True, exist_ok=True)
        self.repo = tmp_path / "clone"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        self.log = self.lanes / f"{slot}-lane.log"
        self.log.touch()
        self.exits = self.lanes / f"{slot}-exits.jsonl"
        self.agent = agent
        self.identities = identities
        row: dict[str, object] = {
            "status": "active",
            "ts": datetime.datetime.fromtimestamp(T0, datetime.UTC).isoformat(),
            "run_id": "r1", "role": "tester", "slot": slot,
            "agent": agent, "model": "fixture-model", "terminal": terminal,
            "repository": str(self.repo), "lane_log": str(self.log),
            "lane_exits": str(self.exits), "tmux_target": TARGET,
            "watch": {"read_allowance_s": READ_ALLOWANCE_S, "stall_window_s": STALL_WINDOW_S,
                      "poll_s": 30, **(watch or {})},
            "caps": caps or {}, "qualify": None,
        }
        if lane is not None:
            row["lane"] = lane
        (self.lanes / f"{slot}-launch.jsonl").write_text(json.dumps(row) + "\n")
        if thread:
            (self.lanes / f"{slot}-thread-id").write_text(
                "12345678-1234-4234-8234-123456789abc\n")
            delivery = self.lanes / f"{slot}-delivery.env"
            delivery.write_text("PATH=/usr/bin:/bin\n")
            delivery.chmod(0o600)
        # The modelled pane and process table.
        self.window = True
        self.pane_dead = not alive
        self.snapshot_available = True
        self.stop_kills = True
        self.table: dict[int, dict[str, Any]] = _tree() if alive else dict(INIT)
        self.record_path = self.lanes / f"{slot}-persistent.json"
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
            root=self.root, slot=self.slot, now=self.clock, stop=self._stop, tmux=self._tmux,
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
            "slot": self.slot, "agent": self.agent, "turn_started_at": T0 - 5,
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


WAITING_LANES: dict[str, tuple[Kind, str, bool, bool]] = {
    "claude-waiting-for-input-alive": (CLAUDE, "waiting-for-input", True, False),
    "claude-stream-idle-alive": (CLAUDE_STREAM, "idle", True, False),
    "codex-stream-idle-alive": (CODEX_STREAM, "idle", True, False),
    "codex-stream-idle-done-unobserved": (CODEX_STREAM, "idle", False, True),
    "claude-stop-reported-done-alive": (CLAUDE, "stop-reported", True, True),
}

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


@pytest.mark.parametrize("name", list(WAITING_LANES))
def test_a_waiting_lane_is_not_paused_for_review(tmp_path: Path, name: str) -> None:
    """Falsifier: a review pause ending the session of a lane that is waiting for the Validator
    (there is no live turn to pause)."""

    watched = _waiting(tmp_path, name, watch=REVIEWED, thread=True)
    for seconds in (1079, 1080, 1110, 2160, 3240):
        assert watched.at(seconds) == "watching"
    assert watched.stops == []
    assert watched.events("lane-review-due") == []


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


def test_a_new_wrappers_first_turn_end_before_the_first_check_is_told(tmp_path: Path) -> None:
    """Review: each session wrapper counts its turns from 0, so a count carried from the last
    wrapper hid a resumed session's first turn end (the 20-minute idle Validator again)."""

    watched = _session(tmp_path, STREAM, "idle", 1)
    assert watched.at(60) == "watching" and watched.notices() == 1
    watched.report(wrapper={"pid": 5002, "started": "Tue Nov 17 10:30:00 2026"}, turns=1,
                   turn_started_at=T0 + 100, turn_completed_at=T0 + 110)
    assert watched.at(120) == "watching" and watched.notices() == 2
    assert watched.at(150) == "watching" and watched.notices() == 2


def _status_notices(watched: Lane) -> list[str]:
    rows = watched._rows(watched.root / "validator" / "inbox.jsonl")
    return [str(row["text"]) for row in rows if str(row["text"]).startswith("FACTORY_LANE_STATUS")]


@pytest.mark.parametrize("held_by", ["question", *WAITING_LANES])
def test_a_long_hold_is_diagnosed_and_escalated_each_period_and_never_stopped(
    tmp_path: Path, held_by: str,
) -> None:
    """Jeremy, on merged #125: elapsed time should trigger diagnosis and escalation, not
    termination as stalled regardless of lane status."""

    if held_by == "question":
        watched = Lane(tmp_path, record=None, lane=None)
        lane_dialogue.record_question(watched.root, "tester", "Which stay types are valid?")
    else:
        watched = _waiting(tmp_path, held_by)
    period = lane_watchdog.HOLD_ESCALATE_S
    for seconds in (period, READ_ALLOWANCE_S + period + 1, READ_ALLOWANCE_S + period + 60,
                    READ_ALLOWANCE_S + 2 * period + 1, READ_ALLOWANCE_S + 2 * period + 60):
        assert watched.at(seconds) == "watching"
    assert watched.stops == [] and watched.stalls() == 0
    assert len(watched.wakes("lane-hold-escalated")) == 2
    first = _status_notices(watched)[0]
    assert ("FACTORY_QUESTION has no answer" if held_by == "question" else "its session reports"
            ) in first and "elapsed time alone stops no lane" in first


def _ledger_unwritable(watched: Lane) -> Path:
    """The Validator's notice record cannot be written: every post fails until it is removed."""
    ledger = watched.root / "validator" / "notices.jsonl"
    ledger.mkdir(parents=True)
    return ledger


def test_a_turn_end_whose_notice_failed_is_told_after_the_next_turn_ends(tmp_path: Path) -> None:
    """Jeremy, on merged #125: the count advanced before the notice was posted, and the next turn
    end replaced the one unposted event. Both are told, in order, once the record is writable."""

    watched = _session(tmp_path, STREAM, None, 0)
    assert watched.at(60) == "watching"
    ledger = _ledger_unwritable(watched)
    _ends_turn(watched, STREAM, "idle", 1, at=100)
    assert watched.at(120) == "watching"
    watched.report(phase="working", turn_started_at=T0 + 130, turn_completed_at=None)
    assert watched.at(150) == "watching"
    _ends_turn(watched, STREAM, "idle", 2, at=170)
    assert watched.at(180) == "watching" and watched.notices() == 0
    ledger.rmdir()
    assert watched.at(210) == "watching"
    assert watched.notices() == 2 and len(watched.wakes("lane-turn-ended")) == 2
    assert watched.at(240) == "watching" and watched.notices() == 2


@pytest.mark.parametrize("arm", ["new-life", "rearm-after-exit"])
def test_a_turn_end_whose_notice_failed_is_told_after_a_rearm(tmp_path: Path, arm: str) -> None:
    """Jeremy, on merged #125: a rearm rebuilt the watch state without the unposted event."""

    watched = _session(tmp_path, STREAM, None, 0)
    assert watched.at(60) == "watching"
    ledger = _ledger_unwritable(watched)
    _ends_turn(watched, STREAM, "idle", 1, at=100)
    assert watched.at(120) == "watching" and watched.notices() == 0
    if arm == "rearm-after-exit":
        lane_agent.record_exit(watched.exits, slot="tester", rc=1, stopped=False)
        watched.kill()
        assert watched.at(150) == "exited"
        watched.revive()
    watched.clock.value = T0 + 160
    watched.watch.arm(new_life=arm == "new-life")
    ledger.rmdir()
    assert watched.at(180) == "watching"
    assert watched.notices() == 1 and len(watched.wakes("lane-turn-ended")) == 1


@pytest.mark.parametrize("then", ["record-gone", "new-turn-then-record-gone"])
def test_a_turn_end_on_record_holds_through_an_unreadable_record_for_one_window_only(
    tmp_path: Path, then: str,
) -> None:
    """Review: a recorded turn end was never cleared, so a session record that went missing or
    corrupt kept the lane waiting from then on."""

    watched = _waiting(tmp_path, "claude-stream-idle-alive")
    if then == "new-turn-then-record-gone":
        watched.report(phase="working")
        assert watched.at(800) == "watching"
    watched.record_path.unlink()
    if then == "record-gone":
        assert watched.at(STALL_AT + 1) == "watching"
        _settle(watched, STALL_AT + STALL_WINDOW_S + 2, "stalled")
    else:
        _settle(watched, STALL_AT + 1, "stalled")
