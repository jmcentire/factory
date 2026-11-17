"""A persistent lane that ends a turn and waits wakes the Validator, and is never stall-stopped.

Regression (2026-11-17): a replay's Validator sat idle 20.6 minutes after a Cursor Tester
finished. Cursor's stop report carries no text, so its ``__LANE_DONE__`` line is never seen;
nothing told the Validator the lane was waiting, and the watcher stall-stopped the finished
lane once its window passed, losing its session.

Contract under test (the Validator's brief for fix/wake-validator-at-lane-turn-end): a
persistent lane's turn-end count is its ``<slot>-persistent.json`` integer ``stop_reports``,
or else its integer ``turns``. A rise in that count while the session waits (phase ``idle``,
``stop-reported`` or ``waiting-for-input``) gives exactly one notice: a ``lane-turn-ended``
wake and one Validator inbox message from ``host`` beginning ``FACTORY_LANE_TURN_ENDED
<lane>: ``. A waiting persistent lane is not stopped by the stall rule; caps still apply.

The real ``LaneWatch`` runs against a synthetic run root. Doubles sit only at its declared
seams, as in tests/test_lane_controls.py: the clock, the tmux runner (window ``@1``, pane
reported dead so a requested stop verifies), the stop and delivery callables, and an empty
process table. The tmux and delivery recorders are evidence only of what the watcher itself
asked for (T17): that it typed nothing.
"""

from __future__ import annotations

import datetime
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from harness import lane_agent, lane_watchdog

T0 = 1_791_000_000.0
READ_ALLOWANCE_S = 900
STALL_WINDOW_S = 1200
# The stall boundary for a lane with no commits: the window runs from the end of the read
# allowance (the rule tests/test_lane_controls.py documents for every watched lane).
STALL_AT = READ_ALLOWANCE_S + STALL_WINDOW_S
PREFIX = "FACTORY_LANE_TURN_ENDED "
WAITING = ("idle", "stop-reported", "waiting-for-input")
# Input into a pane: any of these tmux commands would type or paste into a session.
TYPING = {"send-keys", "send", "paste-buffer", "pasteb", "load-buffer", "loadb", "set-buffer",
          "setb"}


class Clock:
    def __init__(self) -> None:
        self.value = T0

    def __call__(self) -> float:
        return self.value


class Lane:
    """One watched author lane in a synthetic run root; ``tester`` is its slot."""

    def __init__(self, tmp_path: Path, *, record: dict[str, Any] | None,
                 terminal: bool = True, agent: str = "cursor-agent",
                 lane: str | None = "tester.r2", caps: dict[str, object] | None = None) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.root = tmp_path / "run"
        self.lanes = self.root / "tmux-lanes"
        self.lanes.mkdir(parents=True)
        self.repo = tmp_path / "clone"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        self.log = self.lanes / "tester-lane.log"
        self.log.touch()
        self.exits = self.lanes / "tester-exits.jsonl"
        row: dict[str, object] = {
            "status": "active",
            "ts": datetime.datetime.fromtimestamp(T0, datetime.UTC).isoformat(),
            "run_id": "r1", "role": "tester", "slot": "tester",
            "agent": agent, "model": "fixture-model", "terminal": terminal,
            "repository": str(self.repo), "lane_log": str(self.log),
            "lane_exits": str(self.exits), "tmux_target": "r1:tester",
            "watch": {"read_allowance_s": READ_ALLOWANCE_S, "stall_window_s": STALL_WINDOW_S,
                      "poll_s": 30},
            "caps": caps or {}, "qualify": None,
        }
        if lane is not None:
            row["lane"] = lane
        (self.lanes / "tester-launch.jsonl").write_text(json.dumps(row) + "\n")
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

    def _watch(self) -> lane_watchdog.LaneWatch:
        return lane_watchdog.LaneWatch(
            root=self.root, slot="tester", now=self.clock, stop=self._stop, tmux=self._tmux,
            echo=self.said.append, deliver=self._deliver, processes=lambda: {},
        )

    def _tmux(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        self.tmux_calls.append(arguments)
        out = "tester @1\n" if arguments and arguments[0] == "list-windows" else "1\n"
        return subprocess.CompletedProcess(arguments, 0, out, "")

    def _stop(self, target: str) -> str:
        self.stops.append(target)
        return f"stopped {target}"

    def _deliver(self, argv: list[str]) -> str:
        self.delivered.append(argv)
        return "scheduled"

    def report(self, **fields: Any) -> None:
        """What the persistent session wrapper records: phase and its turn-end counters."""
        record = dict(self.record or {"slot": "tester", "turn_started_at": T0 + 10})
        record.update(fields)
        self.record = record
        self.record_path.write_text(json.dumps(record), encoding="utf-8")

    def restart(self) -> None:
        """A watcher process started afresh over the slot's existing watch state."""
        self.watch = self._watch()
        self.watch.arm()

    def at(self, seconds: float) -> str:
        self.clock.value = T0 + seconds
        return self.watch.check()

    def _rows(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def events(self, kind: str) -> list[dict[str, Any]]:
        return [row for row in self._rows(self.root / "events.jsonl") if row.get("kind") == kind]

    def wakes(self, kind: str) -> list[dict[str, Any]]:
        return [row for row in self._rows(self.lanes / "wake.jsonl") if row.get("kind") == kind]

    def inbox(self) -> list[dict[str, Any]]:
        rows = self._rows(self.root / "validator" / "inbox.jsonl")
        return [row for row in rows if str(row.get("text", "")).startswith(PREFIX)]

    def notices(self) -> int:
        """Notices given so far; each one is a wake event, a wake row and one inbox message."""
        events, wakes, inbox = (
            self.events("lane-turn-ended"), self.wakes("lane-turn-ended"), self.inbox())
        assert len(events) == len(wakes) == len(inbox), (events, wakes, inbox)
        return len(inbox)

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


# -- notice ------------------------------------------------------------------------------------


@pytest.mark.parametrize(("lane", "named"), [("tester.r2", "tester.r2"), (None, "tester")])
def test_a_cursor_lane_that_ends_a_turn_wakes_the_validator_once(
    tmp_path: Path, lane: str | None, named: str,
) -> None:
    """Falsifier: no notice for a Cursor stop report (the 20.6-minute idle Validator); a notice
    on every poll of the same count; a notice claiming a done line Cursor never showed; the
    notice reaching the Validator only by typing into a pane."""

    watched = Lane(tmp_path, record={"phase": "prompt-observed", "stop_reports": 0}, lane=lane)
    assert watched.at(60) == "watching"
    assert watched.notices() == 0

    watched.report(phase="stop-reported", stop_reports=1)
    assert watched.at(120) == "watching"
    assert watched.notices() == 1
    [event] = watched.events("lane-turn-ended")
    assert event["class"] == "lane" and event["wake"] is True
    [wake] = watched.wakes("lane-turn-ended")
    assert wake["slot"] == "tester"
    [message] = watched.inbox()
    assert message["sender"] == "host"
    assert message["text"].startswith(f"{PREFIX}{named}: ")
    assert "no done line was seen" in message["text"]
    assert "its done line was seen" not in message["text"]

    assert watched.at(180) == "watching"  # the same count again
    assert watched.at(240) == "watching"
    assert watched.notices() == 1
    watched.assert_nothing_typed()


def test_a_stream_lane_whose_done_line_was_seen_says_so(tmp_path: Path) -> None:
    """Falsifier: the stream count (``turns``) ignored, or the notice saying no done line was
    seen when the lane did claim done (the Validator would chase a lane that finished)."""

    watched = Lane(tmp_path, record={"phase": "working", "turns": 0}, terminal=False,
                   agent="claude")
    watched.log.write_text(json.dumps(
        {"type": "result", "result": "Report written.\n__LANE_DONE__ tester commits=0"}) + "\n")
    assert watched.at(60) == "watching"  # the claim is read; the turn has not ended yet
    assert watched.notices() == 0

    watched.report(phase="idle", turns=1)
    assert watched.at(120) == "watching"
    assert watched.notices() == 1
    [message] = watched.inbox()
    assert message["text"].startswith(f"{PREFIX}tester.r2: ")
    assert "its done line was seen" in message["text"]
    assert "no done line was seen" not in message["text"]
    assert watched.at(180) == "watching" and watched.notices() == 1


@pytest.mark.parametrize("phase", ["prompt-observed", "working", "asking-operator"])
def test_a_turn_end_already_followed_by_a_new_turn_is_noticed_but_not_announced(
    tmp_path: Path, phase: str,
) -> None:
    """Falsifier: announcing a turn end when the session is already busy again (the Validator
    would act on a lane that is mid-turn); or holding the count back so the turn end is
    announced later, against a turn it does not belong to."""

    watched = Lane(tmp_path, record={"phase": "stop-reported", "stop_reports": 0})
    watched.report(phase=phase, stop_reports=1)
    assert watched.at(60) == "watching"
    assert watched.notices() == 0
    # The count was taken as noticed: the session settling at the same count is not a new end.
    watched.report(phase="waiting-for-input")
    assert watched.at(120) == "watching"
    assert watched.notices() == 0
    # The next turn end while waiting is announced.
    watched.report(phase="stop-reported", stop_reports=2)
    assert watched.at(180) == "watching"
    assert watched.notices() == 1


def test_a_terminal_lanes_stop_reports_are_its_count_even_beside_turns(tmp_path: Path) -> None:
    """Falsifier: reading ``turns`` before ``stop_reports`` for a record carrying both."""

    watched = Lane(tmp_path, record={"phase": "stop-reported", "stop_reports": 0, "turns": 0})
    watched.report(turns=4)
    assert watched.at(60) == "watching"
    assert watched.notices() == 0
    watched.report(stop_reports=1)
    assert watched.at(120) == "watching"
    assert watched.notices() == 1


# -- baseline ----------------------------------------------------------------------------------


def test_turn_ends_before_arming_are_not_announced_and_a_burst_is_one_notice(
    tmp_path: Path,
) -> None:
    """Falsifier: a newly armed watch announcing turns that ended before it (a stale wake per
    delivery); one notice per turn end rather than one per check that sees new ends; a new
    life inheriting the earlier life's noticed count."""

    watched = Lane(tmp_path, record={"phase": "stop-reported", "stop_reports": 3})
    assert watched.at(60) == "watching"
    assert watched.notices() == 0
    watched.report(stop_reports=5)  # two turn ends between checks
    assert watched.at(120) == "watching"
    assert watched.notices() == 1

    lane_agent.record_exit(watched.exits, slot="tester", rc=1, stopped=False)
    assert watched.at(180) == "exited"
    watched.report(stop_reports=8)
    watched.clock.value = T0 + 300
    watched.watch.arm(new_life=True)
    assert watched.at(360) == "watching"
    assert watched.notices() == 1  # the new life takes 8 as already noticed
    watched.report(stop_reports=9)
    assert watched.at(420) == "watching"
    assert watched.notices() == 2


def test_a_watch_state_from_before_the_change_takes_the_current_count_as_noticed(
    tmp_path: Path,
) -> None:
    """Falsifier: an upgraded watcher reading an absent noticed count as zero and waking the
    Validator for every turn the lane ever ended.

    The pre-change state is the current state without its record of the noticed count. That
    record is found by value (a count no other watch field plausibly holds) rather than by a
    field name, which the contract does not give."""

    sentinel = 4271
    watched = Lane(tmp_path, record={"phase": "stop-reported", "stop_reports": sentinel})
    assert watched.at(60) == "watching"
    state = json.loads(watched.watch.state_path.read_text())
    old, dropped = _strip(state, sentinel)
    assert dropped, "the watch state records no noticed turn-end count"
    watched.watch.state_path.write_text(json.dumps(old))

    watched.report(stop_reports=sentinel + 2)
    watched.restart()
    assert watched.at(120) == "watching"
    assert watched.notices() == 0
    watched.report(stop_reports=sentinel + 3)
    assert watched.at(180) == "watching"
    assert watched.notices() == 1


def test_a_lower_count_is_a_new_session_and_resets_the_noticed_count(tmp_path: Path) -> None:
    """Falsifier: a new session's lower count announced as a turn end, or its later turn ends
    ignored until they pass the old session's count."""

    watched = Lane(tmp_path, record={"phase": "idle", "turns": 5}, terminal=False,
                   agent="claude")
    watched.report(turns=1)
    assert watched.at(60) == "watching"
    assert watched.notices() == 0
    watched.report(turns=2)
    assert watched.at(120) == "watching"
    assert watched.notices() == 1


# -- undelivered -------------------------------------------------------------------------------


@pytest.mark.parametrize("blocked", ["symlink", "not-a-directory"])
def test_an_undeliverable_notice_is_recorded_and_the_watch_carries_on(
    tmp_path: Path, blocked: str,
) -> None:
    """Falsifier: an inbox failure crashing the watcher (leaving the lane unwatched), changing
    its verdict, writing through a planted symlink, or failing silently."""

    steps = [(60, {}), (120, {"phase": "stop-reported", "stop_reports": 1}), (180, {})]
    healthy = Lane(tmp_path / "healthy", record={"phase": "prompt-observed", "stop_reports": 0})
    watched = Lane(tmp_path / "blocked", record={"phase": "prompt-observed", "stop_reports": 0})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    if blocked == "symlink":  # run_record.message refuses with ValueError
        (watched.root / "validator").symlink_to(elsewhere, target_is_directory=True)
    else:  # creating the inbox directory fails with OSError
        (watched.root / "validator").write_text("", encoding="utf-8")

    expected, outcomes = [], []
    for seconds, fields in steps:
        for lane, results in ((healthy, expected), (watched, outcomes)):
            if fields:
                lane.report(**fields)
            results.append(lane.at(seconds))
    assert outcomes == expected == ["watching", "watching", "watching"]
    assert healthy.notices() == 1  # the twin shows a notice was due

    [undelivered] = watched.events("lane-turn-notice-undelivered")
    assert undelivered["wake"] is False
    assert list(elsewhere.iterdir()) == []
    assert not (watched.root / "validator" / "inbox.jsonl").exists()
    assert watched.stops == []
    watched.assert_nothing_typed()


# -- waiting is not a stall --------------------------------------------------------------------


@pytest.mark.parametrize("phase", WAITING)
def test_a_waiting_persistent_lane_is_not_stall_stopped(tmp_path: Path, phase: str) -> None:
    """Falsifier: the stall rule stopping a finished lane that waits for the Validator, with no
    done line seen (the Cursor Tester whose session was lost)."""

    watched = Lane(tmp_path, record={"phase": phase, "stop_reports": 1})
    assert watched.at(STALL_AT - 1) == "watching"
    assert watched.at(STALL_AT + 1) == "watching"
    assert watched.at(STALL_AT + 3 * 3600) == "watching"
    assert watched.stops == []
    assert watched.events("refusal-lane-stall") == []


@pytest.mark.parametrize("record", [
    {"phase": "prompt-observed", "stop_reports": 1},
    {"phase": "working", "turns": 1},
    None,
], ids=["prompt-observed", "working", "no-persistent-record"])
def test_a_lane_mid_turn_or_without_a_persistent_record_is_stall_stopped_as_before(
    tmp_path: Path, record: dict[str, Any] | None,
) -> None:
    """Positive control for the test above: the same lane, busy or not persistent, is stopped
    at the unchanged boundary. Falsifier: the waiting exemption widened to every lane."""

    watched = Lane(tmp_path, record=record)
    assert watched.at(STALL_AT - 1) == "watching"
    assert watched.stops == []
    assert watched.at(STALL_AT + 1) == "stalled"
    assert watched.stops == ["r1:tester"]
    assert len(watched.events("refusal-lane-stall")) == 1


@pytest.mark.parametrize("caps", [
    {"wall_cap_s": 1800},
    {"spend_cap_usd": "1", "usd_per_hour": "2"},
], ids=["wall-clock", "spend"])
def test_a_waiting_persistent_lane_is_still_capped(
    tmp_path: Path, caps: dict[str, object],
) -> None:
    """Falsifier: the waiting exemption also lifting the wall-clock or spend cap."""

    watched = Lane(tmp_path, record={"phase": "stop-reported", "stop_reports": 1}, caps=caps)
    assert watched.at(1799) == "watching"
    assert watched.at(1800) == "capped"
    assert watched.stops == ["r1:tester"]
