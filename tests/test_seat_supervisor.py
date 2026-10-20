"""The seats work only while the Orchestrator judges them; its stop always carries a correction."""

from __future__ import annotations

import datetime
import importlib
import json
import signal
from pathlib import Path

import pytest

import harness.orchestrator_channel  # noqa: F401 - establish adjacent harness imports

supervisor = importlib.import_module("seat_supervisor")
escalate = importlib.import_module("escalate")

T0 = datetime.datetime(2026, 10, 20, 12, 0, tzinfo=datetime.UTC)


class FakeSeats:
    def __init__(self, panes: dict[str, int]) -> None:
        self._panes = panes
        self.signals: list[tuple[int, int]] = []
        self.delivered: list[tuple[str, str]] = []

    def panes(self) -> dict[str, int]:
        return dict(self._panes)

    def signal(self, pid: int, signum: int) -> None:
        self.signals.append((pid, signum))

    def deliver(self, seat: str, text: str) -> None:
        self.delivered.append((seat, text))


def run_root(tmp_path: Path) -> Path:
    root = tmp_path / "demo-run"
    (root / "orchestrator").mkdir(parents=True)
    return root


def activity(root: Path, cursor: int, at: datetime.datetime) -> None:
    with (root / "orchestrator" / "activity.jsonl").open("a") as handle:
        handle.write(json.dumps({"cursor": cursor, "ts": at.isoformat()}) + "\n")


def assessed(root: Path, through: int) -> None:
    with (root / "orchestrator" / "reports.jsonl").open("a") as handle:
        handle.write(json.dumps({"assessment": {"through_cursor": through}}) + "\n")


def tick(root, seats, journal, at, notified):
    return supervisor.tick(
        root,
        seats,
        journal,
        ttl=datetime.timedelta(minutes=15),
        now=lambda: at,
        notify=lambda *a, **k: notified.append(k),
    )


def test_current_judgment_leaves_every_seat_working(tmp_path: Path) -> None:
    root, seats, notified = (
        run_root(tmp_path),
        FakeSeats({"validator": 10, "coder": 20, "tester": 30}),
        [],
    )
    activity(root, 1, T0)
    assessed(root, 1)
    result = tick(root, seats, supervisor.Journal(root), T0 + datetime.timedelta(hours=2), notified)
    assert result["lagging"] is False and seats.signals == [] and notified == []


def test_unassessed_activity_past_the_lease_pauses_every_seat_and_alerts_once(
    tmp_path: Path,
) -> None:
    root, seats, notified = (
        run_root(tmp_path),
        FakeSeats({"validator": 10, "coder": 20, "tester": 30}),
        [],
    )
    journal = supervisor.Journal(root)
    activity(root, 1, T0)
    assert (
        tick(root, seats, journal, T0 + datetime.timedelta(minutes=10), notified)["lagging"]
        is False
    )
    tick(root, seats, journal, T0 + datetime.timedelta(minutes=16), notified)
    assert sorted(seats.signals) == [
        (10, signal.SIGSTOP),
        (20, signal.SIGSTOP),
        (30, signal.SIGSTOP),
    ]
    assert [n["level"] for n in notified] == ["critical"] and notified[0]["key"] == supervisor.DOWN
    tick(root, seats, journal, T0 + datetime.timedelta(minutes=20), notified)
    assert len(seats.signals) == 3 and len(notified) == 1, (
        "a paused run is not paused or alerted twice"
    )


def test_the_orchestrator_catching_up_resumes_the_seats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(escalate, "resolve", lambda *a, **k: None)
    root, seats, notified = run_root(tmp_path), FakeSeats({"validator": 10, "coder": 20}), []
    journal = supervisor.Journal(root)
    activity(root, 1, T0)
    tick(root, seats, journal, T0 + datetime.timedelta(minutes=30), notified)
    assessed(root, 1)
    seats.signals.clear()
    tick(root, seats, journal, T0 + datetime.timedelta(minutes=31), notified)
    assert sorted(seats.signals) == [(10, signal.SIGCONT), (20, signal.SIGCONT)]
    assert supervisor.load_state(root) == {}


def test_the_orchestrator_is_never_a_paused_seat(tmp_path: Path) -> None:
    root, seats, notified = run_root(tmp_path), FakeSeats({"coder": 20}), []
    activity(root, 1, T0)
    tick(root, seats, supervisor.Journal(root), T0 + datetime.timedelta(hours=1), notified)
    assert seats.signals == [(20, signal.SIGSTOP)]


def test_stop_without_a_correction_is_refused(tmp_path: Path) -> None:
    root, seats = run_root(tmp_path), FakeSeats({"validator": 10})
    with pytest.raises(supervisor.SupervisorError, match="exact correction"):
        supervisor.control(
            root,
            seats,
            seat="validator",
            action="stop",
            rule="fresh_session_for_resumable",
            evidence="launched tester-l.r7",
            correction="",
        )
    assert seats.signals == []


def test_stop_pauses_the_seat_and_queues_the_correction_until_resume(tmp_path: Path) -> None:
    root, seats = run_root(tmp_path), FakeSeats({"validator": 10})
    supervisor.control(
        root,
        seats,
        seat="validator",
        action="stop",
        rule="paid_overage",
        evidence="Codex window 100%, credits falling 3,600/h",
        correction="Stop the Codex Orchestrator and reseat it on a plan model now.",
    )
    assert seats.signals == [(10, signal.SIGSTOP)]
    [(seat, text)] = seats.delivered
    assert seat == "validator" and "Rule: paid_overage" in text and "reseat it" in text
    assert supervisor.load_state(root) == {"validator": ["stop:paid_overage"]}
    supervisor.control(
        root, seats, seat="validator", action="resume", rule="", evidence="", correction=""
    )
    assert seats.signals[-1] == (10, signal.SIGCONT)
    rows = [
        json.loads(line)
        for line in (root / "orchestrator" / "seat-control.jsonl").read_text().splitlines()
    ]
    assert [r["action"] for r in rows] == ["stop", "resume"]


def test_resume_does_not_override_a_lagging_orchestrator(tmp_path: Path) -> None:
    root, seats, notified = run_root(tmp_path), FakeSeats({"validator": 10}), []
    activity(root, 1, T0)
    tick(root, seats, supervisor.Journal(root), T0 + datetime.timedelta(hours=1), notified)
    supervisor.control(
        root, seats, seat="validator", action="resume", rule="", evidence="", correction=""
    )
    assert seats.signals == [(10, signal.SIGSTOP)], "a seat resumed while judgment is still missing"


def test_correct_delivers_without_pausing(tmp_path: Path) -> None:
    root, seats = run_root(tmp_path), FakeSeats({"validator": 10, "tester": 30})
    supervisor.control(
        root,
        seats,
        seat="tester",
        action="correct",
        rule="founder_action_validator_can_do",
        evidence="asked the founder to approve the deploy he requested",
        correction="Deploy; the founder asked for it on 2026-10-16.",
    )
    assert seats.signals == [] and seats.delivered[0][0] == "tester"


def test_only_live_role_seats_are_controlled(tmp_path: Path) -> None:
    root, seats = run_root(tmp_path), FakeSeats({"validator": 10})
    for seat in ("orchestrator", "coder"):
        with pytest.raises(supervisor.SupervisorError):
            supervisor.control(
                root, seats, seat=seat, action="correct", rule="r", evidence="e", correction="c"
            )


def kickoff_root(tmp_path: Path) -> Path:
    root = run_root(tmp_path)
    (root / "harness.json").write_text(json.dumps({"validator_agent": "kickoff"}))
    return root


def test_stopping_the_kickoff_validator_holds_every_lane_and_never_freezes_the_session(
    tmp_path: Path,
) -> None:
    root, seats = kickoff_root(tmp_path), FakeSeats({"validator": 10, "coder": 20, "tester": 30})
    supervisor.control(
        root,
        seats,
        seat="validator",
        action="stop",
        rule="no fresh session",
        evidence="launched coder round 4 over a live session",
        correction="resume coder's own session",
    )
    assert sorted(seats.signals) == [(20, signal.SIGSTOP), (30, signal.SIGSTOP)]
    assert seats.delivered and seats.delivered[0][0] == "validator"
    assert "resume coder's own session" in seats.delivered[0][1]
    seats.signals.clear()
    supervisor.control(
        root, seats, seat="validator", action="resume", rule="", evidence="", correction=""
    )
    assert sorted(seats.signals) == [(20, signal.SIGCONT), (30, signal.SIGCONT)]
    assert supervisor.load_state(root) == {}


def test_a_lane_launched_while_the_kickoff_validator_is_stopped_is_held(tmp_path: Path) -> None:
    root, notified = kickoff_root(tmp_path), []
    seats = FakeSeats({"validator": 10, "coder": 20})
    supervisor.control(
        root, seats, seat="validator", action="stop", rule="r", evidence="e", correction="c"
    )
    seats._panes["tester"] = 30
    seats.signals.clear()
    tick(root, seats, supervisor.Journal(root), T0, notified)
    assert seats.signals == [(30, signal.SIGSTOP)]


def test_a_lagging_orchestrator_pauses_lanes_and_tells_the_kickoff_validator(
    tmp_path: Path,
) -> None:
    root, notified = kickoff_root(tmp_path), []
    seats = FakeSeats({"validator": 10, "coder": 20})
    activity(root, 1, T0)
    tick(root, seats, supervisor.Journal(root), T0 + datetime.timedelta(minutes=20), notified)
    assert seats.signals == [(20, signal.SIGSTOP)]
    assert seats.delivered and seats.delivered[0][0] == "validator"
    assert "unassessed" in seats.delivered[0][1]
