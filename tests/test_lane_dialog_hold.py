"""Before any stall stop the watcher assesses the lane: hung, working, waiting or uncertain.

Code owner's review of #124 (2026-11-18): a timeout is a reason to investigate, not proof of a
hang; a failed pane read is uncertainty; a commit is no evidence that a dialog was answered; a
healthy session is kept while what it needs is resolved, and no host prompt is answered.
Regression (2026-11-17): a Tester sat 30 min at Cursor's command approval and was stopped here as
stalled, losing its work. The real ``LaneWatch`` runs with doubles only at its seams.
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from harness import lane_watchdog

T0 = 1_791_000_000.0
STALL_WINDOW_S = 1200
PAST = 900 + STALL_WINDOW_S + 1  # the stall window has run out
TARGET = "r1:tester"
INBOX = "validator/inbox.jsonl"
TYPING = {"send-keys", "send", "send-prefix", "paste-buffer", "pasteb"}


def payload(*lines: str) -> str:
    return "\n".join(lines) + "\n"



# Screens. Cursor's three identifying rows are quoted from the 2026-11-17 lane; the rest of every
# dialog is reconstructed for the fixture.
WORK = payload("Reading TASK.md", "Ran 12 tests in 0.41s", "OK")


def cursor_dialog(command: str, allowlist: str = "sha256sum, wc") -> str:
    return payload(f"  $ {command}", " Run this command?", f" Not in allowlist: {allowlist}",
                   " → Run (once) (y)", "   Skip (esc or n)", "   Run Everything (shift+tab)")




CURSOR_APPROVAL = WORK + cursor_dialog("sha256sum dist/factory-0.8.30.tar.gz | wc -c")


Call = tuple[str, ...]


def is_lookup(args: Call) -> bool:
    return args[0] == "list-windows"


def _targets_lane(args: Call) -> bool:
    target = next(((args[i + 1] if a == "-t" else a[2:]) for i, a in enumerate(args)
                   if a.startswith("-t") and i + (a == "-t") < len(args)), "")
    return target.split(":")[-1] == "@7"


def _prints(args: Call) -> bool:
    return any(a.startswith("-") and "p" in a[1:] for a in args[1:])



class Tmux:
    """The lane's tmux at ``LaneWatch``'s seam, answering as tmux does: the lane's window is @7,
    whose ``capture-pane -p`` prints ``screen``; its pane is dead once ``dead``. ``aimed`` raises
    from a check's n-th ``list-windows``; ``on_capture`` runs once inside the next capture."""

    def __init__(self, screen: str) -> None:
        self.screen = screen
        self.capture_rc = 0
        self.capture_fault: BaseException | None = None
        self.aimed: tuple[int, BaseException] | None = None
        self.on_capture: Callable[[], None] | None = None
        self.fired = 0
        self.dead = False
        self.pane_pid = "0"
        self.windows = "tester @7\nwatch-tester @8\n"
        self.calls: list[Call] = []
        self.checks: list[list[Call]] = [[]]

    def __call__(self, *args: str) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        self.checks[-1].append(args)
        if self.aimed is not None and is_lookup(args):
            ordinal, error = self.aimed
            if sum(map(is_lookup, self.checks[-1])) == ordinal:
                self.fired += 1
                raise error
        out, rc = ("1\n" if self.dead else "0\n"), 0
        if is_lookup(args):
            out = self.windows
        elif "#{pane_pid}" in args:
            out = f"{self.pane_pid}\n"
        elif args[0] == "capture-pane":
            if self.capture_fault is not None:
                raise self.capture_fault
            if self.on_capture is not None and _targets_lane(args):
                hook, self.on_capture = self.on_capture, None
                hook()
            out = (self.screen if _targets_lane(args) else WORK) if _prints(args) else ""
            rc = self.capture_rc
        elif args[0] in {"display-message", "display"} and "#{pane_dead}" in args:
            out = (out if _targets_lane(args) else "0\n") if _prints(args) else ""
        return subprocess.CompletedProcess(args, rc, out, "")

    def typed(self) -> list[Call]:
        return [call for call in self.calls if call[0] in TYPING]


class Lane:
    """One Tester lane as tmux_lane.sh records it (by default an interactive Cursor session), its
    watcher armed at T0. ``thread=True`` records the resumable thread a review or wrap-up needs."""

    def __init__(self, tmp_path: Path, tmux: Tmux, *, agent: str = "cursor-agent",
                 prompt_via: str | None = "terminal", lane: str | None = "tester",
                 caps: dict[str, object] | None = None, watch: dict[str, object] | None = None,
                 thread: bool = False, table: dict[int, dict[str, str | int]] | None = None,
                 ) -> None:
        self.root, self.tmux, self.now, self.table = tmp_path / "run", tmux, T0, table or {}
        self.lanes = self.root / "tmux-lanes"
        self.lanes.mkdir(parents=True)
        self.repo = tmp_path / "clone"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        (self.lanes / "tester-lane.log").touch()
        row: dict[str, object] = {
            "status": "active", "ts": datetime.datetime.fromtimestamp(T0, datetime.UTC)
            .isoformat(), "run_id": "r1", "role": "tester", "lane": lane, "slot": "tester",
            "agent": agent, "model": "fixture-model", "terminal": False, "prompt_via": prompt_via,
            "repository": str(self.repo), "lane_log": str(self.lanes / "tester-lane.log"),
            "lane_exits": str(self.lanes / "tester-exits.jsonl"), "tmux_target": TARGET,
            "watch": {"read_allowance_s": 900, "stall_window_s": STALL_WINDOW_S, "poll_s": 30,
                      **(watch or {})},
            "caps": caps or {}, "qualify": None}
        (self.lanes / "tester-launch.jsonl").write_text(
            json.dumps({key: value for key, value in row.items()
                        if value is not None or key not in {"lane", "prompt_via"}}) + "\n")
        if thread:
            (self.lanes / "tester-thread-id").write_text("12345678-1234-4234-8234-123456789abc\n")
            (self.lanes / "tester-delivery.env").write_text("PATH=/usr/bin:/bin\n")
            (self.lanes / "tester-delivery.env").chmod(0o600)
        self.stops: list[str] = []
        self.said: list[str] = []
        self.delivered: list[list[str]] = []
        self.dies_at: str | None = None
        self.died: list[str] = []
        self.restart()
        self.watch.arm()

    def restart(self) -> None:
        """A fresh watcher, not armed again: all it knows is what the run's files hold."""
        self.watch = lane_watchdog.LaneWatch(
            root=self.root, slot="tester", now=lambda: self.now, stop=self._stop,
            tmux=self.tmux, echo=self._echo, deliver=self._deliver,
            processes=lambda: {pid: dict(row) for pid, row in self.table.items()})

    def _echo(self, line: str) -> None:
        self.said.append(line)
        if self.dies_at is not None and line.startswith(self.dies_at):
            self.dies_at = None
            self.died.append("died")
            raise SystemExit("fixture: the watcher died")

    def _stop(self, target: str) -> str:
        self.stops.append(target)
        self.tmux.dead = True
        return f"stopped {target}"

    def _deliver(self, argv: list[str]) -> str:
        self.delivered.append(argv)
        return "scheduled"

    def resume(self, seconds: float) -> None:
        """A review pause ends: the pane runs again and the watcher starts a new life."""
        self.now = T0 + seconds
        self.tmux.dead = False
        self.watch.arm(new_life=True)

    def at(self, seconds: float) -> str:
        self.now = T0 + seconds
        self.tmux.checks.append([])
        return self.watch.check()

    def rows(self, name: str) -> list[dict[str, object]]:
        path = self.root / name
        return [json.loads(row) for row in path.read_text().splitlines()] if path.is_file() else []

    def kinds(self) -> list[str]:
        return [str(row["kind"]) for row in self.rows("events.jsonl")]

    def notices(self) -> list[str]:
        return [str(row["text"]) for row in self.rows(INBOX)
                if str(row.get("text", "")).startswith("FACTORY_LANE_STATUS")]

    def wakes(self, kind: str) -> int:
        return [row["kind"] for row in self.rows("tmux-lanes/wake.jsonl")].count(kind)

    def dialog(self) -> object:
        return json.loads((self.lanes / "tester-watch.json").read_text()).get("dialog")


def commit(lane: Lane, at: float) -> None:
    name = f"commit-{at}"
    (lane.repo / name).write_text(name, encoding="utf-8")
    stamp = f"@{int(T0 + at)} +0000"
    env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    for argv in (["add", name], ["commit", "-q", "-m", name]):
        subprocess.run(["git", "-C", str(lane.repo), *argv], check=True, env=env)




def test_a_lane_at_a_dialog_is_kept_told_once_and_never_answered(tmp_path: Path) -> None:
    lane = Lane(tmp_path, Tmux(CURSOR_APPROVAL))
    for seconds in (60, PAST, PAST + 600, 3 * 3600):
        assert lane.at(seconds) == "watching"
    assert lane.stops == [] and lane.tmux.typed() == []
    assert len(lane.notices()) == 1 and lane.wakes("lane-waiting") == 1
    assert "sha256sum" not in lane.notices()[0]  # names the wait, never the screen's text


def test_a_wait_of_four_hours_is_told_again_never_stopped(tmp_path: Path) -> None:
    lane = Lane(tmp_path, Tmux(CURSOR_APPROVAL))
    lane.at(PAST)
    assert lane.at(PAST + lane_watchdog.REASSESS_S + 60) == "watching"
    assert lane.stops == [] and len(lane.notices()) == 2 and lane.wakes("lane-waiting") == 2


def test_a_commit_while_the_dialog_shows_does_not_end_the_wait(tmp_path: Path) -> None:
    # A background command can commit while the foreground session still waits at its dialog.
    lane = Lane(tmp_path, Tmux(CURSOR_APPROVAL))
    lane.at(PAST)
    commit(lane, PAST + 30)
    for seconds in (PAST + 60, PAST + STALL_WINDOW_S + 600, PAST + 2 * STALL_WINDOW_S + 900):
        assert lane.at(seconds) == "watching"
    assert lane.stops == [] and lane.tmux.typed() == []


@pytest.mark.parametrize("fault", ["timeout", "rc", "lookup"])
def test_a_failed_pane_read_is_uncertain_and_never_stops_the_lane(
    tmp_path: Path, fault: str,
) -> None:
    tmux = Tmux(WORK)
    if fault == "timeout":
        tmux.capture_fault = subprocess.TimeoutExpired(["tmux", "capture-pane"], 5)
    elif fault == "rc":
        tmux.capture_rc = 1
    lane = Lane(tmp_path, tmux)
    if fault == "lookup":
        tmux.aimed = (1, subprocess.TimeoutExpired(["tmux", "list-windows"], 5))
    for seconds in (PAST, PAST + 600, PAST + 3 * 3600):
        assert lane.at(seconds) == "watching"
        if fault == "lookup":
            tmux.aimed = (1, subprocess.TimeoutExpired(["tmux", "list-windows"], 5))
    assert lane.stops == []
    assert len(lane.notices()) == 1 and lane.wakes("lane-uncertain") == 1


def test_a_lane_whose_screen_keeps_changing_is_working_and_kept(tmp_path: Path) -> None:
    tmux = Tmux(WORK)
    lane = Lane(tmp_path, tmux)
    for name, seconds in zip(("alpha", "beta", "gamma", "delta"),
                             (PAST - 60, PAST, PAST + 300, PAST + 600), strict=True):
        tmux.screen = WORK + payload(f"compiled module {name}")
        assert lane.at(seconds) == "watching"
    assert lane.stops == [] and len(lane.notices()) == 1
    assert lane.wakes("lane-waiting") == lane.wakes("lane-uncertain") == 0


def test_the_wall_cap_still_stops_a_lane_at_a_dialog(tmp_path: Path) -> None:
    lane = Lane(tmp_path, Tmux(CURSOR_APPROVAL), caps={"wall_cap_s": 3600})
    assert lane.at(PAST) == "watching"
    assert lane.at(3601) == "capped"
    assert lane.stops == [TARGET] and lane.tmux.typed() == []


def test_a_cleared_dialog_ends_the_wait_and_the_next_one_is_told_again(tmp_path: Path) -> None:
    tmux = Tmux(CURSOR_APPROVAL)
    lane = Lane(tmp_path, tmux)
    lane.at(PAST)
    tmux.screen = WORK + payload("Hashes match.")
    lane.at(PAST + 60)
    tmux.screen = WORK + cursor_dialog("rm -rf build/tmp && mv out build", "rm, mv")
    # The cleared read re-anchors the window; the next dialog is told when it runs out again.
    assert lane.at(PAST + 120) == "watching"
    assert lane.at(PAST + 60 + STALL_WINDOW_S + 1) == "watching"
    assert lane.wakes("lane-waiting") == 2 and lane.stops == []


# Review of the reworked #124 (2026-11-18).

def test_an_uncapped_lane_at_a_dialog_is_stopped_at_the_hold_limit(tmp_path: Path) -> None:
    """A plan-billed lane has no wall or spend cap, so the stall stop is its only bound: a wait
    may not keep it forever."""
    lane = Lane(tmp_path, Tmux(CURSOR_APPROVAL))
    assert lane.at(PAST) == "watching"
    assert lane.at(900 + lane_watchdog.HOLD_LIMIT_S + 1) in {"stalled", "stopping"}
    assert lane.stops == [TARGET] and lane.tmux.typed() == []
    assert "hold limit" in lane.notices()[-1]


def test_a_spinner_over_a_stalled_stream_is_hung_with_one_wake(tmp_path: Path) -> None:
    """A TUI stuck on a stalled stream redraws a spinner and an elapsed-time counter with a
    little CPU: not work. Its real stall wakes the operator once, by the stop."""
    tmux = Tmux(WORK)
    tmux.pane_pid = "4242"
    lane = Lane(tmp_path, tmux, table={4242: {"pid": 4242, "ppid": 1, "state": "S",
                                              "started": "Tue Nov 17 09:59:57 2026"}})
    for n, seconds in enumerate((PAST, PAST + 60, PAST + 120, PAST + 60 + 300)):
        tmux.screen = WORK + payload(f"{'⠋⠙⠹⠸'[n]} Generating… ({seconds}s · esc to interrupt)")
        lane.table[4242]["cpu"] = f"00:00:{n:02d}"  # 1 s a minute or less: a redraw
        lane.at(seconds)
    assert lane.stops == [TARGET]
    assert lane.wakes("lane-uncertain") == 0 and len(lane.notices()) == 1


@pytest.mark.parametrize("gone", ["pane-dead", "window-gone"])
def test_a_dialog_on_a_pane_that_died_or_went_away_is_no_longer_a_wait(
    tmp_path: Path, gone: str,
) -> None:
    """The dialog was rewritten only by a live read, so a lane whose pane died or whose window
    went away stayed waiting, and carried the dialog into its next life."""
    tmux = Tmux(CURSOR_APPROVAL)
    lane = Lane(tmp_path, tmux)
    lane.at(PAST)
    assert lane.dialog() is not None
    if gone == "pane-dead":
        tmux.dead = True
    else:
        tmux.windows = "validator @9\n"
    lane.at(PAST + 60)
    assert lane.dialog() is None
    lane.resume(PAST + 90)
    assert lane.dialog() is None


def test_a_lane_flipping_between_working_and_uncertain_is_told_once(tmp_path: Path) -> None:
    tmux = Tmux(WORK)
    lane = Lane(tmp_path, tmux)
    for rc, word, seconds in ((0, "alpha", PAST), (0, "beta", PAST + 30),
                              (1, "gamma", PAST + 30 + STALL_WINDOW_S + 1),
                              (0, "delta", PAST + 60 + STALL_WINDOW_S + 1),
                              (0, "epsilon", PAST + 90 + STALL_WINDOW_S + 1)):
        tmux.capture_rc, tmux.screen = rc, WORK + payload(f"compiled module {word}")
        assert lane.at(seconds) == "watching"
    assert len(lane.notices()) == 1 and lane.wakes("lane-uncertain") == 1


def test_dialog_words_above_the_pane_bottom_are_not_a_dialog(tmp_path: Path) -> None:
    """Detection scanned the last 15 rows, so a lane whose output said "Do you want to proceed"
    a few rows up was held as at a dialog."""
    screen = WORK + payload("Do you want to proceed with the migration?",
                            *(f"applied step {word}" for word in ("one", "two", "three",
                                                                  "four", "five")))
    lane = Lane(tmp_path, Tmux(screen))
    assert lane.at(PAST) == "watching"
    assert lane.dialog() is None and lane.wakes("lane-waiting") == 0
