"""Regression: closure depends on the exact session, not a successful format command."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


def test_exact_session_presence_and_absence() -> None:
    """Owner-directed lifecycle invariant; the old display-message check stays falsely green."""
    tmux = shutil.which("tmux")
    if tmux is None:
        pytest.skip("installed tmux required")
    helper = Path(__file__).resolve().parents[1] / "harness" / "tmux_resource.sh"
    with tempfile.TemporaryDirectory(prefix="tmux-exact-", dir="/tmp") as directory:
        socket = str(Path(directory) / "s")
        env = {**os.environ, "TMUX": f"{socket},0,0"}

        def command(*args: str) -> None:
            subprocess.run([tmux, "-S", socket, *args], check=True, capture_output=True)

        def present(name: str) -> bool:
            result = subprocess.run(
                ["bash", "-c", 'source "$1"; factory_tmux_resource_exists tmux-session "$2"',
                 "check", str(helper), name], env=env, capture_output=True,
            )
            assert result.returncode in (0, 1), result.stderr
            return result.returncode == 0

        command("-f", "/dev/null", "new-session", "-d", "-s", "run-other", "sleep 60")
        try:
            assert present("run-other")
            assert not present("run")  # A prefix must not select the other session.
            assert not present("missing")
            command("new-session", "-d", "-s", "run", "sleep 60")
            assert present("run")
            command("kill-session", "-t", "=run")
            assert not present("run")
            assert present("run-other")  # Checking/closing run leaves its neighbor alone.
        finally:
            command("kill-server")
