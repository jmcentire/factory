"""A persistent lane's turn end wakes the Validator once; only a lane that is really waiting is
spared the stall rule.

Regression (2026-11-17): a replay's Validator sat idle 20.6 minutes after a Cursor Tester
finished. Cursor's stop report carries no text, so its ``__LANE_DONE__`` line is never seen,
and nothing told the Validator the lane had ended its turn.

Review of the first fix: exempting every lane whose phase looked idle from the stall rule was
unsafe. A stop report means the session ended a response, not that it will not continue
(harness/persistent_lane.py), so a session that continued and then hung was never stopped,
and a dead lane whose record still said "waiting" was watched forever.

Contract under test (the Validator's brief for fix/wake-validator-at-lane-turn-end):

1. A persistent lane's reported turn ends are its record's integer ``stop_reports``, else its
   integer ``turns``. When that count exceeds the noticed count while the phase is
   ``stop-reported``, ``waiting-for-input``, ``idle`` or (a stream turn that failed)
   ``failed``, one ``lane-turn-ended`` wake and one Validator inbox message from ``host``
   (``FACTORY_LANE_TURN_ENDED <lane>: ...``) are given, and the count is noticed. A count seen
   in any other phase is not noticed. A notice for a lane that is not waiting for input (a stop
   report, a failed turn) and printed no done line this turn names the stall deadline in the
   launch's whole minutes.
2. The first arm of a launch announces a turn that ended after the launch, and a re-arm over
   an active watch state keeps the noticed count it stored. A new life, a record older than
   the launch, and a watch state from before the change take the current count as noticed. A
   lower count is a new session.
3. An undeliverable inbox message is recorded once and retried at every check.
4. The stall clock restarts at the record's turn timestamps. A lane is exempt from the stall
   stop, the review pause and the wrap-up only when it printed a done line in the current turn
   and its turn ended (not failed), or when it says it is waiting for input and the same check
   saw its processes alive. The caps always apply.

The real ``LaneWatch`` runs against a synthetic run root. Doubles sit only at its declared
seams, as in tests/test_lane_controls.py: the clock, the tmux runner, the process table, the
stop and the delivery callables. The tmux double answers each format it is asked for from one
modelled pane (window ``@1``, pane pid ``4242``, alive or dead); the process double reports
that pane's tree when the lane is alive. The stop double does what a delivered SIGTERM does:
the pane dies and its processes leave the table. The tmux and delivery recorders are evidence
only of what the watcher itself asked for (T17).
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from harness import lane_agent, lane_watchdog

T0 = 1_791_000_000.0
READ_ALLOWANCE_S = 900
STALL_WINDOW_S = 1200
# The unchanged stall boundary for a lane that has not committed: the window runs from the end
# of the read allowance (the rule tests/test_lane_controls.py documents for every watched lane).
STALL_AT = READ_ALLOWANCE_S + STALL_WINDOW_S
TARGET = "r1:tester"
PANE_PID = 4242
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


def _tree() -> dict[int, dict[str, Any]]:
    """The pane's live process tree: its shell and the agent under it."""
    return {
        PANE_PID: {"pid": PANE_PID, "ppid": 1, "started": "pane-start", "state": "S"},
        PANE_PID + 1: {"pid": PANE_PID + 1, "ppid": PANE_PID, "started": "agent-start",
                       "state": "S"},
    }


class Lane:
    """One watched author lane in a synthetic run root; ``tester`` is its slot."""

    def __init__(self, tmp_path: Path, *, record: dict[str, Any] | None,
                 terminal: bool = True, agent: str = "cursor-agent",
                 lane: str | None = "tester.r2", caps: dict[str, object] | None = None,
                 watch: dict[str, object] | None = None, thread: bool = False,
                 alive: bool = True) -> None:
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
        # The modelled pane: what tmux and the process table report about it.
        self.window = True
        self.pane_dead = not alive
        self.snapshot_available = True
        self.table: dict[int, dict[str, Any]] = _tree() if alive else {}
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
        self.pane_dead = True
        self.table = {}
        return f"stopped {target}"

    def _deliver(self, argv: list[str]) -> str:
        self.delivered.append(argv)
        return "scheduled"

    # -- the lane ------------------------------------------------------------------------------

    def report(self, **fields: Any) -> None:
        """What the persistent session wrapper records: phase, counters and turn timestamps."""
        record = dict(self.record or {
            "schema": "factory-persistent-lane/2", "root": str(self.root.resolve()),
            "slot": "tester", "agent": self.agent, "turn_started_at": T0 + 10,
        })
        record.update(fields)
        record = {key: value for key, value in record.items() if value is not DROP}
        self.record = record
        self.record_path.write_text(json.dumps(record), encoding="utf-8")

    def respond(self, text: str) -> None:
        """One typed agent response in the lane log (a non-terminal lane's done line)."""
        with self.log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "result", "result": text}) + "\n")

    def tool_output(self, text: str) -> None:
        """Tool output in the lane log: not a response, so it neither claims nor retracts."""
        with self.log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "tool_result", "content": text}) + "\n")

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


# A session of each kind: (agent, terminal launch, its counter, its turn-end timestamp).
CURSOR = ("cursor-agent", True, "stop_reports", "stop_reported_at")
CLAUDE = ("claude", True, "stop_reports", "stop_reported_at")
STREAM = ("agy", False, "turns", "turn_completed_at")
RESUMABLE_STREAM = ("codex-ollama", False, "turns", "turn_completed_at")
CLAUDE_STREAM = ("claude", False, "turns", "turn_completed_at")


def _session(tmp_path: Path, kind: tuple[str, bool, str, str], phase: str, count: int,
             **options: Any) -> Lane:
    agent, terminal, counter, _ = kind
    return Lane(tmp_path, record={"phase": phase, counter: count}, agent=agent,
                terminal=terminal, **options)


def _ends_turn(watched: Lane, kind: tuple[str, bool, str, str], phase: str, count: int,
               at: float) -> None:
    _, _, counter, stamp = kind
    watched.report(phase=phase, **{counter: count, stamp: T0 + at})


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
    assert DEADLINE.format(20) in text
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
    tmp_path: Path, kind: tuple[str, bool, str, str], phase: str, words: str, deadline: bool,
) -> None:
    """Falsifier: one wording for every phase (a stop report told as waiting for input invites
    the Validator to type into a session that may still be working; the reverse hides that a
    lane is ready); a failed stream turn not announced, or told as a finished one; a stream
    session's ``turns`` not read as its count; the stall deadline given to a lane the stall
    rule spares (waiting for input) or withheld from one it will stop."""

    mid = "working" if kind is STREAM else "prompt-observed"
    watched = _session(tmp_path, kind, mid, 0)
    assert watched.at(60) == "watching" and watched.notices() == 0
    _ends_turn(watched, kind, phase, 1, at=100)
    assert watched.at(120) == "watching"
    assert watched.notices() == 1
    [text] = watched.texts()
    assert words in text
    assert [said for said in SAID if said != words and said in text] == []
    assert DONE_UNSEEN in text and "0 commit(s)" in text and text.rstrip().endswith(CLOSING)
    if deadline:
        assert DEADLINE.format(20) in text
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

    watched = _session(tmp_path, CURSOR, "prompt-observed", 0,
                       watch={"stall_window_s": window})
    assert watched.at(60) == "watching"
    _ends_turn(watched, CURSOR, "stop-reported", 1, at=100)
    assert watched.at(120) == "watching"
    [text] = watched.texts()
    assert DEADLINE.format(minutes) in text


@pytest.mark.parametrize(("kind", "mid", "phase"), [
    (("agy", False, "stop_reports", "stop_reported_at"), "prompt-observed", "stop-reported"),
    (STREAM, "working", "failed"),
], ids=["stop-reported", "failed"])
def test_the_deadline_is_left_out_only_while_a_done_line_was_read_this_turn(
    tmp_path: Path, kind: tuple[str, bool, str, str], mid: str, phase: str,
) -> None:
    """Falsifier: the deadline given although a done line was read this turn (or withheld
    although none was), or a done line from an earlier turn withholding it. The first launch is
    synthetic: a non-terminal lane (whose done line counts) reporting a stop."""

    _, _, counter, _ = kind
    watched = _session(tmp_path, kind, mid, 0)
    watched.respond(DONE_LINE)
    assert watched.at(60) == "watching"
    _ends_turn(watched, kind, phase, 1, at=100)
    assert watched.at(120) == "watching"
    [first] = watched.texts()
    assert DONE_SEEN in first and DEADLINE_HEAD not in first

    watched.report(phase=mid, turn_started_at=T0 + 600)
    watched.tool_output("ran the suite")
    assert watched.at(630) == "watching"
    _ends_turn(watched, kind, phase, 2, at=700)
    assert watched.at(720) == "watching"
    assert watched.notices() == 2
    later = watched.texts()[1]
    assert DONE_UNSEEN in later and DEADLINE.format(20) in later


@pytest.mark.parametrize("read", ["before-the-turn-end", "with-the-turn-end"])
def test_a_done_line_read_in_the_current_turn_is_reported(tmp_path: Path, read: str) -> None:
    """Falsifier: the notice saying no done line was seen when the lane did claim done (the
    Validator would chase a lane that finished), including the usual stream case where the
    response and the completed turn reach the watcher in the same check."""

    watched = _session(tmp_path, STREAM, "working", 0)
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

    watched = _session(tmp_path, STREAM, "working", 0)
    watched.respond(DONE_LINE)
    assert watched.at(60) == "watching"
    _ends_turn(watched, STREAM, "idle", 1, at=100)
    assert watched.at(120) == "watching"
    assert DONE_SEEN in watched.texts()[0]

    watched.report(phase="working", turn_started_at=T0 + 600, turn_completed_at=None)
    watched.tool_output("ran the suite")
    assert watched.at(630) == "watching"
    _ends_turn(watched, STREAM, "idle", 2, at=700)
    assert watched.at(720) == "watching"
    assert watched.notices() == 2
    later = watched.texts()[1]
    assert DONE_UNSEEN in later and DONE_SEEN not in later


def test_a_terminal_lanes_logged_text_is_not_a_done_line(tmp_path: Path) -> None:
    """Falsifier: a terminal session's logged screen text taken as its done claim (a terminal
    event's author cannot be established; tests/test_lane_controls.py holds completion to the
    same rule)."""

    watched = _session(tmp_path, CURSOR, "prompt-observed", 0)
    watched.respond(DONE_LINE)
    assert watched.at(60) == "watching"
    _ends_turn(watched, CURSOR, "stop-reported", 1, at=100)
    assert watched.at(120) == "watching"
    [text] = watched.texts()
    assert DONE_UNSEEN in text and DONE_SEEN not in text


@pytest.mark.parametrize(("kind", "busy", "settled"), [
    (CURSOR, "prompt-observed", "stop-reported"),
    (CLAUDE, "started", "waiting-for-input"),
    (CLAUDE, "asking-operator", "waiting-for-input"),
    (CLAUDE, "starting", "stop-reported"),
    (STREAM, "working", "idle"),
], ids=["prompt-observed", "started", "asking-operator", "starting", "working"])
def test_a_count_seen_mid_turn_is_announced_when_the_session_settles(
    tmp_path: Path, kind: tuple[str, bool, str, str], busy: str, settled: str,
) -> None:
    """Falsifier: announcing a turn end while the session is busy again (the Validator would
    act on a lane that is mid-turn); or marking the count noticed then, so the session settling
    at that count is never announced (the turn end is lost: the original 20.6-minute idle)."""

    _, _, counter, _ = kind
    watched = _session(tmp_path, kind, "working" if kind is STREAM else "prompt-observed", 0)
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


@pytest.mark.parametrize("offset", [0, 10], ids=["at-the-launch", "after-the-launch"])
def test_a_turn_that_ended_before_the_watcher_armed_is_announced(
    tmp_path: Path, offset: int,
) -> None:
    """Falsifier: a newly armed watch taking the count it found as noticed, so a lane whose
    turn ended before the watcher started (a fast Tester) is told to nobody; a strict ``>``
    where the contract says ``turn_started_at`` at or after the launch."""

    watched = Lane(tmp_path, record={"phase": "stop-reported", "stop_reports": 1,
                                     "turn_started_at": T0 + offset,
                                     "stop_reported_at": T0 + offset + 5})
    assert watched.at(30) == "watching"
    assert watched.notices() == 1
    assert watched.at(60) == "watching" and watched.notices() == 1


@pytest.mark.parametrize("started", [T0 - 1, DROP], ids=["older-than-the-launch", "missing"])
def test_a_record_from_before_the_launch_is_taken_as_noticed(
    tmp_path: Path, started: object,
) -> None:
    """Falsifier: announcing turns another launch's session ended (a stale wake per launch)."""

    watched = Lane(tmp_path, record={"phase": "stop-reported", "stop_reports": 3,
                                     "turn_started_at": started})
    assert watched.at(30) == "watching"
    assert watched.notices() == 0
    watched.report(stop_reports=4)
    assert watched.at(60) == "watching"
    assert watched.notices() == 1


def test_a_restarted_watcher_never_repeats_a_notice(tmp_path: Path) -> None:
    """Falsifier: the first-arm rule applied to every arm, so a restarted watcher over a
    record of this launch announces again a turn end it already announced."""

    watched = _session(tmp_path, CURSOR, "prompt-observed", 0)
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
    """A re-arm over an active watch state keeps the noticed count it stored.

    Falsifier: a re-arm taking the count at arm as noticed, so the turn end that happened while
    the watcher was down is told to nobody (the original 20.6-minute idle); or announcing it
    more than once."""

    watched = _session(tmp_path, CURSOR, "prompt-observed", 0)
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


def test_a_new_life_takes_the_count_at_arm_as_noticed(tmp_path: Path) -> None:
    """Falsifier: a new life of the watcher (a resumed turn) announcing the turn ends of the
    life before it, or inheriting nothing and announcing them all."""

    watched = Lane(tmp_path, record={"phase": "stop-reported", "stop_reports": 3})
    assert watched.at(60) == "watching"
    assert watched.notices() == 1  # first arm, record of this launch: announced
    watched.report(stop_reports=5)
    assert watched.at(120) == "watching"
    assert watched.notices() == 2

    lane_agent.record_exit(watched.exits, slot="tester", rc=1, stopped=False)
    watched.pane_dead, watched.table = True, {}
    assert watched.at(180) == "exited"
    watched.report(stop_reports=8, turn_started_at=T0 + 290)
    watched.pane_dead, watched.table = False, _tree()  # the resumed session
    watched.clock.value = T0 + 300
    watched.watch.arm(new_life=True)
    assert watched.at(360) == "watching"
    assert watched.notices() == 2
    watched.report(stop_reports=9)
    assert watched.at(420) == "watching"
    assert watched.notices() == 3


def test_a_watch_state_from_before_the_change_takes_the_current_count_as_noticed(
    tmp_path: Path,
) -> None:
    """Falsifier: an upgraded watcher reading an absent noticed count as zero and waking the
    Validator for every turn the lane ever ended.

    The pre-change state is the current state without its record of the noticed count. That
    record is found by value (a count no other watch field plausibly holds) rather than by a
    field name, which the contract does not give."""

    sentinel = 4271
    watched = Lane(tmp_path, record={"phase": "stop-reported", "stop_reports": sentinel,
                                     "turn_started_at": T0 - 60})
    assert watched.at(60) == "watching"
    assert watched.notices() == 0

    def downgrade() -> None:
        state = json.loads(watched.watch.state_path.read_text())
        old, dropped = _strip(state, sentinel)
        assert dropped, "the watch state records no noticed turn-end count"
        watched.watch.state_path.write_text(json.dumps(old))

    downgrade()
    assert watched.at(90) == "watching"  # the same watcher reading the old state
    assert watched.notices() == 0

    downgrade()
    watched.report(stop_reports=sentinel + 2)
    watched.restart()  # an upgraded watcher over the old state
    assert watched.at(120) == "watching"
    assert watched.notices() == 0
    watched.report(stop_reports=sentinel + 3)
    assert watched.at(180) == "watching"
    assert watched.notices() == 1


@pytest.mark.parametrize("new_session", ["settled", "busy-first"])
def test_a_lower_count_is_a_new_session_and_resets_the_noticed_count(
    tmp_path: Path, new_session: str,
) -> None:
    """Falsifier: a new session's lower count announced as a turn end, or its later turn ends
    ignored until they pass the old session's count."""

    watched = Lane(tmp_path, record={"phase": "idle", "turns": 5}, terminal=False, agent="agy")
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


# -- 3. an undeliverable notice --------------------------------------------------------------


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
    validator = watched.root / "validator"
    if blocked == "symlink":  # run_record.message refuses with ValueError
        validator.symlink_to(elsewhere, target_is_directory=True)
    elif blocked == "file":  # creating the inbox directory fails with OSError
        validator.write_text("", encoding="utf-8")
    else:  # the inbox is not a regular file: the append fails with OSError
        (validator / "inbox.jsonl").mkdir(parents=True)

    expected, outcomes = [], []
    for seconds, fields in [(60, {}), (120, {"phase": "stop-reported", "stop_reports": 1}),
                            (180, {}), (240, {}), (300, {})]:
        for lane, results in ((healthy, expected), (watched, outcomes)):
            if fields:
                lane.report(**fields)
            results.append(lane.at(seconds))
    assert outcomes == expected == ["watching"] * 5
    assert healthy.notices() == 1  # the twin shows a notice was due

    assert len(watched.events("lane-turn-notice-undelivered")) == 1
    assert len(watched.events("lane-turn-ended")) == 1
    assert len(watched.wakes("lane-turn-ended")) == 1
    assert watched.inbox() == []
    assert list(elsewhere.iterdir()) == []
    assert watched.stops == []
    watched.assert_nothing_typed()

    if blocked == "symlink":
        validator.unlink()
    elif blocked == "file":
        validator.unlink()
    else:
        (validator / "inbox.jsonl").rmdir()
    assert watched.at(360) == "watching"
    [message] = watched.inbox()
    assert message["sender"] == "host" and message["text"].startswith(f"{PREFIX}tester.r2: ")
    assert STOP_WORDS in message["text"]
    for seconds in (420, 480):
        assert watched.at(seconds) == "watching"
    assert len(watched.inbox()) == 1
    assert len(watched.events("lane-turn-notice-undelivered")) == 1
    assert len(watched.events("lane-turn-ended")) == 1
    assert len(watched.wakes("lane-turn-ended")) == 1
    assert list(elsewhere.iterdir()) == []


# -- 4. the stall rule -----------------------------------------------------------------------


@pytest.mark.parametrize("alive", [True, False], ids=["processes-alive", "processes-gone"])
def test_a_cursor_lane_is_stall_stopped_a_window_after_its_last_stop_report(
    tmp_path: Path, alive: bool,
) -> None:
    """Falsifier: a stop report taken as waiting (the session may continue past it and hang,
    and is then never stopped); live processes taken as waiting without the session saying so;
    the stop report not restarting the window (a lane that just answered stopped at main's
    boundary)."""

    watched = _session(tmp_path, CURSOR, "prompt-observed", 0, alive=alive)
    _commit(watched.repo, T0 + 300, "a")
    assert watched.at(600) == "watching"
    _ends_turn(watched, CURSOR, "stop-reported", 1, at=1500)
    assert watched.at(1530) == "watching"
    assert watched.at(STALL_AT + 1) == "watching"  # main's boundary: max(900, 300) + 1200
    assert watched.at(1500 + STALL_WINDOW_S - 1) == "watching"
    assert watched.stops == [] and watched.stalls() == 0
    assert watched.at(1500 + STALL_WINDOW_S + 1) == "stalled"
    assert watched.stops == [TARGET]
    assert watched.stalls() == 1
    assert watched.at(9000) == "stalled" and watched.stops == [TARGET]


@pytest.mark.parametrize(("kind", "phase", "field"), [
    (CURSOR, "prompt-observed", "turn_started_at"),
    (STREAM, "working", "turn_started_at"),
    (STREAM, "idle", "turn_completed_at"),
    (STREAM, "failed", "turn_completed_at"),
], ids=["terminal-turn-started", "stream-turn-started", "stream-turn-completed",
        "stream-turn-failed"])
def test_the_stall_window_restarts_at_the_records_turn_timestamps(
    tmp_path: Path, kind: tuple[str, bool, str, str], phase: str, field: str,
) -> None:
    """Falsifier: the record's ``turn_started_at`` or ``turn_completed_at`` ignored, so a lane
    given new work after a quiet spell is stopped minutes into its new turn; a failed turn
    taken as waiting although its processes are alive. (The idle stream lane here has no live
    processes observed, so it is not waiting.)"""

    _, _, counter, _ = kind
    watched = _session(tmp_path, kind, phase, 0, alive=phase != "idle")
    assert watched.at(600) == "watching"
    watched.report(**{field: T0 + 1500, counter: 1 if field == "turn_completed_at" else 0})
    assert watched.at(1530) == "watching"
    assert watched.at(STALL_AT + 1) == "watching"
    assert watched.at(1500 + STALL_WINDOW_S - 1) == "watching"
    assert watched.stops == []
    assert watched.at(1500 + STALL_WINDOW_S + 1) == "stalled"
    assert watched.stops == [TARGET] and watched.stalls() == 1


@pytest.mark.parametrize(("kind", "phase"), [(CLAUDE, "waiting-for-input"), (STREAM, "idle")],
                         ids=["claude-waiting-for-input", "stream-idle"])
def test_a_lane_waiting_for_input_with_live_processes_is_not_stall_stopped(
    tmp_path: Path, kind: tuple[str, bool, str, str], phase: str,
) -> None:
    """Falsifier: the stall rule stopping a finished lane that waits for the Validator with no
    done line seen (the Cursor-Tester case, for a session that does say it is waiting)."""

    watched = _session(tmp_path, kind, "working" if kind is STREAM else "prompt-observed", 0)
    assert watched.at(600) == "watching"
    _ends_turn(watched, kind, phase, 1, at=700)
    for seconds in (STALL_AT + 1, 700 + STALL_WINDOW_S + 1, STALL_AT + 3 * 3600):
        assert watched.at(seconds) == "watching"
    assert watched.stops == [] and watched.stalls() == 0


@pytest.mark.parametrize("unobserved", [
    "snapshot-unavailable", "pane-dead", "window-gone", "pane-pid-absent",
    "alive-only-at-an-earlier-check",
])
def test_a_lane_waiting_for_input_whose_processes_are_not_seen_is_stall_stopped(
    tmp_path: Path, unobserved: str,
) -> None:
    """Falsifier: a record that says waiting trusted without this check seeing the session
    alive, so a dead lane with a stale record (or one the watcher cannot see) is watched
    forever; liveness remembered from an earlier check."""

    watched = _session(tmp_path, CLAUDE, "waiting-for-input", 1)
    watched.report(stop_reported_at=T0 + 10)

    def hide() -> None:
        if unobserved in {"snapshot-unavailable", "alive-only-at-an-earlier-check"}:
            watched.snapshot_available = False
        elif unobserved == "pane-dead":
            watched.pane_dead, watched.table = True, {}
        elif unobserved == "window-gone":
            watched.window, watched.table = False, {}
        else:  # the pane is alive, but its pid is not in the table
            watched.table = {7: {"pid": 7, "ppid": 1, "started": "other", "state": "S"}}

    if unobserved != "alive-only-at-an-earlier-check":
        hide()
    assert watched.at(600) == "watching"
    assert watched.at(1200) == "watching"
    if unobserved == "alive-only-at-an-earlier-check":
        hide()
    assert watched.at(STALL_AT - 1) == "watching"
    assert watched.stops == []
    outcome = watched.at(STALL_AT + 1)
    assert watched.stops == [TARGET]
    assert outcome in {"stalled", "stopping"}
    # Once the stopped lane is observable it is finished as stalled, once.
    watched.snapshot_available, watched.window = True, True
    watched.pane_dead, watched.table = True, {}
    assert watched.at(STALL_AT + 31) == "stalled"
    assert watched.stops == [TARGET] and watched.stalls() == 1


@pytest.mark.parametrize(("phase", "counter"), [("idle", "turns"),
                                               ("stop-reported", "stop_reports")])
def test_a_lane_that_printed_its_done_line_this_turn_and_ended_it_is_not_stall_stopped(
    tmp_path: Path, phase: str, counter: str,
) -> None:
    """Falsifier: a lane that said it is done stopped while it waits for the Validator
    (DIS-2032), even when its processes are not seen and its last report is a stop."""

    watched = Lane(tmp_path, record={"phase": "working", counter: 0}, terminal=False,
                   agent="agy", alive=False)
    watched.respond(DONE_LINE)
    assert watched.at(600) == "watching"
    watched.report(phase=phase, **{counter: 1})
    for seconds in (STALL_AT + 1, STALL_AT + 3 * 3600):
        assert watched.at(seconds) == "watching"
    assert watched.stops == [] and watched.stalls() == 0


@pytest.mark.parametrize(("phase", "turns", "completed"), [
    ("working", 0, None),
    ("failed", 1, 1500),
])
def test_a_done_line_does_not_exempt_a_turn_that_has_not_ended_or_failed(
    tmp_path: Path, phase: str, turns: int, completed: int | None,
) -> None:
    """Falsifier: a done line exempting a lane whose session went on working after printing
    it, or whose turn then failed (stopped a window after the failure, not spared),
    whatever its processes."""

    watched = Lane(tmp_path, record={"phase": "working", "turns": 0}, terminal=False,
                   agent="agy")
    watched.respond(DONE_LINE)
    assert watched.at(600) == "watching"
    if completed is None:
        watched.report(phase=phase, turns=turns)
        boundary = STALL_AT
    else:
        assert watched.at(completed - 10) == "watching"
        watched.report(phase=phase, turns=turns, turn_completed_at=T0 + completed)
        assert watched.at(completed + 30) == "watching"
        boundary = completed + STALL_WINDOW_S
    assert watched.at(boundary - 1) == "watching"
    assert watched.stops == []
    assert watched.at(boundary + 1) == "stalled"
    assert watched.stops == [TARGET] and watched.stalls() == 1


def test_a_done_line_from_an_earlier_turn_does_not_exempt_a_later_one(tmp_path: Path) -> None:
    """Falsifier: a done claim outliving its turn, so a lane given new work that never prints
    another done line (and is not seen alive) is never stall-stopped."""

    watched = Lane(tmp_path, record={"phase": "working", "turns": 0}, terminal=False,
                   agent="agy", alive=False)
    watched.respond(DONE_LINE)
    assert watched.at(200) == "watching"
    watched.report(phase="idle", turns=1, turn_completed_at=T0 + 250)
    assert watched.at(STALL_AT + 1) == "watching"  # done this turn: waiting
    assert watched.at(2900) == "watching"

    watched.report(phase="working", turn_started_at=T0 + 3000, turn_completed_at=None)
    watched.tool_output("ran the suite")
    assert watched.at(3030) == "watching"
    watched.report(phase="idle", turns=2, turn_completed_at=T0 + 3100)
    assert watched.at(3100 + STALL_WINDOW_S - 1) == "watching"
    assert watched.stops == []
    assert watched.at(3100 + STALL_WINDOW_S + 1) == "stalled"
    assert watched.stops == [TARGET] and watched.stalls() == 1


@pytest.mark.parametrize(("record", "terminal"), [
    ({"phase": "prompt-observed", "stop_reports": 1}, True),
    ({"phase": "asking-operator", "stop_reports": 1}, True),
    ({"phase": "working", "turns": 1}, False),
    (None, True),
], ids=["prompt-observed", "asking-operator", "working", "no-persistent-record"])
def test_a_lane_mid_turn_or_without_a_persistent_record_is_stall_stopped_as_before(
    tmp_path: Path, record: dict[str, Any] | None, terminal: bool,
) -> None:
    """Positive control: the same lane, busy or not persistent, with its processes alive, is
    stopped at the unchanged boundary. Falsifier: the exemption widened to every lane, or to
    any lane whose processes are alive."""

    watched = Lane(tmp_path, record=record, terminal=terminal)
    assert watched.at(STALL_AT - 1) == "watching"
    assert watched.stops == []
    assert watched.at(STALL_AT + 1) == "stalled"
    assert watched.stops == [TARGET]
    assert watched.stalls() == 1


# -- 5. review pause, wrap-up and caps -------------------------------------------------------

# A waiting lane of each kind: launch, the record it reaches, whether its processes are seen,
# and whether it printed a done line in that turn. The watcher offers a review pause and a
# wrap-up only to a resumable launch, which a native terminal Claude session is not (its
# positive control does not pause), so those two rules are driven through stream launches;
# the last entry there is synthetic (a stream session reports ``idle``, not
# ``waiting-for-input``) and exercises the other waiting phase on a resumable launch.
RESUMABLE_WAITING = {
    "claude-stream-idle-alive": (CLAUDE_STREAM, "idle", True, False),
    "codex-stream-idle-alive": (RESUMABLE_STREAM, "idle", True, False),
    "codex-stream-idle-done-line": (RESUMABLE_STREAM, "idle", False, True),
    "claude-stream-waiting-for-input-alive": (CLAUDE_STREAM, "waiting-for-input", True, False),
}
WAITING_LANES = {
    "claude-terminal-waiting-for-input": (CLAUDE, "waiting-for-input", True, False),
    **RESUMABLE_WAITING,
}
BUSY_LANES = {
    "claude-stream-working": (CLAUDE_STREAM, "working"),
    "codex-stream-working": (RESUMABLE_STREAM, "working"),
}


def _waiting(tmp_path: Path, name: str, **options: Any) -> Lane:
    kind, phase, alive, done = WAITING_LANES[name]
    watched = _session(tmp_path, kind, "prompt-observed" if kind[1] else "working", 0,
                       alive=alive, **options)
    _commit(watched.repo, T0 + 100, "a")
    if done:
        watched.respond(DONE_LINE)
    assert watched.at(600) == "watching"
    _ends_turn(watched, kind, phase, 1, at=700)
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


@pytest.mark.parametrize("name", list(RESUMABLE_WAITING))
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

    kind, phase = BUSY_LANES[name]
    watched = _session(tmp_path, kind, phase, 0, caps={"wall_cap_s": 1800}, watch=RESUMABLE,
                       thread=True)
    _commit(watched.repo, T0 + 100, "a")
    assert watched.at(1499) == "watching" and watched.delivered == []
    assert watched.at(1500) == "wrapping"
    assert watched.stops == [TARGET]
    [argv] = watched.delivered
    assert argv[1:5] == ["r1", "watchdog", "tester", "wrapup"]
    assert len(watched.events("lane-wrap-up")) == 1


@pytest.mark.parametrize("name", list(RESUMABLE_WAITING))
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

    kind, phase = BUSY_LANES[name]
    watched = _session(tmp_path, kind, phase, 0, watch=REVIEWED, thread=True)
    _commit(watched.repo, T0 + 100, "a")
    _commit(watched.repo, T0 + 1000, "b")
    assert watched.at(1079) == "watching"
    assert watched.at(1080) == "paused"
    assert watched.stops == [TARGET]
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
