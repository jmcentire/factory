"""Forcing tests for harness/lane_watchdog.py target addressing."""

from __future__ import annotations


def test_window_id_never_lets_tmux_fall_back_to_another_window() -> None:
    """tmux answers for the current window when a name is missing; resolve names to ids."""
    import subprocess

    from harness.lane_watchdog import stop_pane, window_id

    calls: list[tuple[str, ...]] = []

    def tmux(*arguments: str) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        out = "coder-a @6\nwatch-coder-a @7\n" if arguments[0] == "list-windows" else "0 0\n"
        return subprocess.CompletedProcess(list(arguments), 0, out, "")

    assert window_id("msg-r2:coder-a", tmux) == "@6"
    assert window_id("msg-r2:coder-b", tmux) is None
    assert window_id("msg-r2:@6", tmux) == "msg-r2:@6"
    calls.clear()
    assert "not found" in stop_pane("msg-r2:coder-b", tmux)
    assert all(call[0] == "list-windows" for call in calls)  # never addressed by name


def test_identity_signal_never_targets_reused_or_unknown_process():
    import signal

    import pytest

    from harness.lane_watchdog import signal_identity

    identity = {'pid': 90001, 'started': 'old'}
    sent = []
    table = {90001: {'pid': 90001, 'ppid': 1, 'started': 'new', 'state': 'S'}}
    assert signal_identity(identity, signal.SIGSTOP, processes=lambda: table,
                           send=lambda *args: sent.append(args)) == 'exited'
    assert not sent
    with pytest.raises(OSError, match='unavailable'):
        signal_identity(identity, signal.SIGSTOP, processes=lambda: None,
                        send=lambda *args: sent.append(args))
    assert not sent


def test_stop_pane_refuses_changed_recorded_identity(monkeypatch):
    import subprocess

    from harness import lane_watchdog

    table = {101: {'pid': 101, 'ppid': 1, 'started': 'new', 'state': 'S'}}
    monkeypatch.setattr(lane_watchdog, 'process_snapshot', lambda: table)
    def tmux(*a):
        return subprocess.CompletedProcess(
            a, 0, 'coder @1\n' if a[0] == 'list-windows' else '101 0\n', '')
    import pytest

    with pytest.raises(OSError, match='recorded identity'):
        lane_watchdog.stop_pane('run:coder', tmux, expected={'pid': 101, 'started': 'old'})


def test_dead_pane_stop_includes_previously_observed_orphans(monkeypatch):
    import signal
    import subprocess

    from harness import lane_watchdog

    table = {102: {'pid': 102, 'ppid': 1, 'started': 'orphan', 'state': 'S'}}
    known = [{'pid': 101, 'started': 'root'}, {'pid': 102, 'started': 'orphan'}]
    sent = []
    monkeypatch.setattr(lane_watchdog, 'process_snapshot', lambda: table)
    monkeypatch.setattr(lane_watchdog, 'signal_identity', lambda i, s: sent.append((i, s)))

    def tmux(*a):
        return subprocess.CompletedProcess(
            a, 0, 'coder @1\n' if a[0] == 'list-windows' else '101 1\n', '')

    lane_watchdog.stop_pane('run:coder', tmux, expected=known[0], known=known)
    assert (known[1], signal.SIGTERM) in sent
