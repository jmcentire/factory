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
