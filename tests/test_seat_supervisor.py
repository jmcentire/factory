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
        self.modes = {}

    def configure(self, root):
        self.root = root

    def external(self, *, require_live=True):
        return {"pid": 11, "started": "external-validator"}

    def transition(self, seat, pid, stopped):
        mode = signal.SIGSTOP if stopped else signal.SIGCONT
        if self.modes.get(pid) != mode:
            self.signal(pid, mode)
            self.modes[pid] = mode
        return {"acknowledged": True, "state": "stopped" if stopped else "running"}

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


def test_stopping_the_kickoff_validator_controls_the_external_process_and_holds_lanes(
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
    assert sorted(seats.signals) == [
        (11, signal.SIGSTOP), (20, signal.SIGSTOP), (30, signal.SIGSTOP)]
    assert seats.delivered and seats.delivered[0][0] == "validator"
    assert "resume coder's own session" in seats.delivered[0][1]
    seats.signals.clear()
    supervisor.control(
        root, seats, seat="validator", action="resume", rule="", evidence="", correction=""
    )
    assert seats.signals == [(11, signal.SIGCONT)]
    tick(root, seats, supervisor.Journal(root), T0, [])
    assert sorted(seats.signals) == [
        (11, signal.SIGCONT), (20, signal.SIGCONT), (30, signal.SIGCONT)]
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
    assert sorted(seats.signals) == [(11, signal.SIGSTOP), (20, signal.SIGSTOP)]
    assert seats.delivered and seats.delivered[0][0] == "validator"
    assert "unassessed" in seats.delivered[0][1]


def test_signal_is_pending_until_state_ack_and_survives_restart(tmp_path, monkeypatch):
    root = run_root(tmp_path)
    seats = supervisor.Seats('fixture')
    seats.configure(root)
    table = {101: {'pid': 101, 'ppid': 1, 'started': 'root', 'state': 'S'},
             102: {'pid': 102, 'ppid': 101, 'started': 'child', 'state': 'S'}}
    sent = []
    monkeypatch.setattr(supervisor, 'process_snapshot', lambda: table)
    monkeypatch.setattr(supervisor, 'signal_identity', lambda identity, sig:
                        sent.append((dict(identity), sig)) or 'sent')
    first = seats.transition('coder', 101, True)
    assert not first['acknowledged'] and first['state'] == 'pending'
    assert len(sent) == 2
    # An attempted resume cannot clear the unacknowledged stop.
    assert not seats.transition('coder', 101, False)['acknowledged']
    assert len(sent) == 2
    table[101]['state'] = table[102]['state'] = 'T'
    replacement = supervisor.Seats('fixture')
    replacement.configure(root)
    assert replacement.transition('coder', 101, True)['acknowledged']
    assert len(sent) == 2  # no repeat signals after watcher restart
    resumed = replacement.transition('coder', 101, False)
    assert not resumed['acknowledged'] and len(sent) == 4
    table[101]['state'] = table[102]['state'] = 'S'
    assert replacement.transition('coder', 101, False)['acknowledged']
    assert len(sent) == 4


def test_stop_failure_and_unknown_observation_remain_pending(tmp_path, monkeypatch):
    root = run_root(tmp_path)
    seats = supervisor.Seats('fixture')
    seats.configure(root)
    table = {101: {'pid': 101, 'ppid': 1, 'started': 'root', 'state': 'S'}}
    monkeypatch.setattr(supervisor, 'process_snapshot', lambda: table)
    calls = []

    def denied(identity, sig):
        calls.append(identity)
        raise PermissionError('fixture denial')

    monkeypatch.setattr(supervisor, 'signal_identity', denied)
    assert not seats.transition('coder', 101, True)['acknowledged']
    assert not seats.transition('coder', 101, True)['acknowledged']
    assert len(calls) == 1
    monkeypatch.setattr(supervisor, 'process_snapshot', lambda: None)
    assert not seats.transition('coder', 101, True)['acknowledged']
    assert len(calls) == 1


def test_kickoff_missing_binding_never_signals_the_mirror(tmp_path):
    root = kickoff_root(tmp_path)
    seats = FakeSeats({'validator': 10, 'coder': 20})

    def missing(**kwargs):
        raise supervisor.SupervisorError('binding absent')

    seats.external = missing
    result = supervisor.control(root, seats, seat='validator', action='stop',
                                rule='r', evidence='e', correction='c')
    assert not result['acknowledged'] and result['identity_error'] == 'binding absent'
    assert seats.signals == [(20, signal.SIGSTOP)]
    assert supervisor.UNBOUND in supervisor.load_state(root)['validator']


def test_control_delivery_failure_preserves_hold_and_signal_receipt(tmp_path):
    root = run_root(tmp_path)
    seats = FakeSeats({'validator': 10})

    def fail(*args):
        raise OSError('delivery unavailable')

    seats.deliver = fail
    result = supervisor.control(root, seats, seat='validator', action='stop',
                                rule='r', evidence='e', correction='c')
    assert result['delivery_error'] == 'delivery unavailable'
    assert supervisor.load_state(root)['validator'] == ['stop:r']
    assert seats.signals == [(10, signal.SIGSTOP)]


def test_binding_rejects_wrong_start_wrong_pane_and_overwrite(tmp_path, monkeypatch):
    import subprocess

    root = kickoff_root(tmp_path)
    table = {101: {'pid': 101, 'ppid': 1, 'started': 'root', 'state': 'S'},
             102: {'pid': 102, 'ppid': 101, 'started': 'validator', 'state': 'S'},
             103: {'pid': 103, 'ppid': 1, 'started': 'unrelated', 'state': 'S'}}
    monkeypatch.setattr(supervisor, 'process_snapshot', lambda: table)
    monkeypatch.setattr(supervisor.subprocess, 'run', lambda *a, **kw:
                        subprocess.CompletedProcess(a, 0, '101\n', ''))
    with pytest.raises(supervisor.SupervisorError, match='not current'):
        supervisor.bind_validator(root, 'fixture', 102, 'old', '%1')
    with pytest.raises(supervisor.SupervisorError, match='not in the named pane'):
        supervisor.bind_validator(root, 'fixture', 103, 'unrelated', '%1')
    bound = supervisor.bind_validator(root, 'fixture', 102, 'validator', '%1')
    assert bound['identity']['pid'] == 102
    with pytest.raises(supervisor.SupervisorError, match='refusing overwrite'):
        supervisor.bind_validator(root, 'fixture', 101, 'root', '%1')
    seats = supervisor.Seats('fixture')
    seats.configure(root)
    assert seats.external() == bound['identity']
    table[102]['started'] = 'reused'
    with pytest.raises(supervisor.SupervisorError, match='absent or changed'):
        seats.external()


def test_real_disposable_process_stop_and_resume_acknowledgements(tmp_path):
    import subprocess
    import sys
    import time

    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(90)'])
    root = run_root(tmp_path)
    seats = supervisor.Seats('fixture-only')
    seats.configure(root)
    try:
        for stopped in (True, False):
            for _ in range(40):
                result = seats.transition('validator', child.pid, stopped)
                if result['acknowledged']:
                    break
                time.sleep(.05)
            assert result['acknowledged'], result
            assert result['state'] == ('stopped' if stopped else 'running')
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_observed_orphan_remains_controlled_without_adopting_reused_root(tmp_path, monkeypatch):
    root = run_root(tmp_path)
    seats = supervisor.Seats('fixture')
    seats.configure(root)
    table = {101: {'pid': 101, 'ppid': 1, 'started': 'root', 'state': 'S'},
             102: {'pid': 102, 'ppid': 101, 'started': 'child', 'state': 'S'}}
    sent = []
    monkeypatch.setattr(supervisor, 'process_snapshot', lambda: table)
    monkeypatch.setattr(supervisor, 'signal_identity', lambda identity, sig:
                        sent.append((dict(identity), sig)) or 'sent')
    assert not seats.transition('coder', 101, True)['acknowledged']
    table[101]['started'] = 'unrelated-new-process'
    table[102]['ppid'] = 1
    assert not seats.transition('coder', 101, True)['acknowledged']
    assert len(sent) == 2
    table[102]['state'] = 'T'
    result = seats.transition('coder', 101, True)
    assert result['acknowledged'] and result['state'] == 'stopped'
    assert len(sent) == 2


def test_admission_rejects_stopped_validator_and_reused_pane(tmp_path, monkeypatch):
    import subprocess

    root = kickoff_root(tmp_path)
    table = {101: {'pid': 101, 'ppid': 1, 'started': 'pane', 'state': 'S'},
             102: {'pid': 102, 'ppid': 101, 'started': 'validator', 'state': 'S'}}
    monkeypatch.setattr(supervisor, 'process_snapshot', lambda: table)
    monkeypatch.setattr(supervisor.subprocess, 'run', lambda *a, **kw:
                        subprocess.CompletedProcess(a, 0, '101\n', ''))
    supervisor.bind_validator(root, 'fixture', 102, 'validator', '%1')
    args = ['check-validator', '--root', str(root), '--session', 'fixture']
    assert supervisor.main(args) == 0
    table[102]['state'] = 'T'
    assert supervisor.main(args) == 64
    table[102]['state'] = 'S'
    table[101]['started'] = 'reused-pane'
    assert supervisor.main(args) == 64
    (root / 'harness.json').write_text('{broken')
    assert supervisor.main(args) == 64


def test_inventory_failure_retains_known_custody_and_never_reports_resumed(tmp_path, monkeypatch):
    root = run_root(tmp_path)
    seats = supervisor.Seats('fixture')
    seats.configure(root)
    table = {101: {'pid': 101, 'ppid': 1, 'started': 'coder', 'state': 'T'}}
    monkeypatch.setattr(supervisor, 'process_snapshot', lambda: table)
    sent = []
    monkeypatch.setattr(supervisor, 'signal_identity', lambda i, s: sent.append((i, s)) or 'sent')
    assert seats.transition('coder', 101, True)['acknowledged']

    def unavailable():
        raise supervisor.SupervisorError('inventory unavailable')

    seats.panes = unavailable
    result = tick(root, seats, supervisor.Journal(root), T0, [])
    assert result['identity_error'] == 'inventory unavailable'
    assert supervisor.UNBOUND in result['holds']['coder']
    assert len(sent) == 1
    events = (root / 'events.jsonl').read_text()
    assert 'seats-resumed' not in events
    receipts = [json.loads(line) for line in
                (root / 'orchestrator' / 'seat-process-events.jsonl').read_text().splitlines()]
    assert receipts[0]['seat'] == 'coder' and receipts[0]['state'] == 'stopped'


def test_control_cli_returns_pending_instead_of_success(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, 'control', lambda *a, **kw: {'acknowledged': False})
    assert supervisor.main(['control', '--root', str(tmp_path), '--session', 'fixture',
                            '--seat', 'validator', '--action', 'stop']) == 75
