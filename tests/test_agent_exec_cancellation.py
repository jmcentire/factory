"""Cancellation uses real owned processes, including TERM-resistant descendants."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from harness import agent_exec

WRAPPER = Path(agent_exec.__file__).resolve()


def wait_file(path: Path) -> str:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return path.read_text()
        time.sleep(0.01)
    raise AssertionError(f"child never became ready: {path}")


def alive(pid: int) -> bool:
    result = subprocess.run(["ps", "-p", str(pid), "-o", "stat="],
                            capture_output=True, text=True, check=False)
    return bool(result.stdout.strip()) and not result.stdout.strip().startswith("Z")


@pytest.mark.parametrize("timeout", [False, True])
def test_cancellation_and_timeout_stop_owned_group_only(tmp_path: Path, timeout: bool) -> None:
    child_file, grandchild_file = tmp_path / "child", tmp_path / "grandchild"
    grandchild = (
        "import os,signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"Path({str(grandchild_file)!r}).write_text(str(os.getpid())); time.sleep(30)"
    )
    child = (
        "import os,subprocess,sys,time; from pathlib import Path; "
        f"subprocess.Popen([sys.executable,'-c',{grandchild!r}]); "
        f"Path({str(child_file)!r}).write_text(str(os.getpid())); time.sleep(30)"
    )
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                 start_new_session=True)
    wrapper = subprocess.Popen([sys.executable, str(WRAPPER), "--timeout-minutes",
                                "0.02" if timeout else "1", "--", sys.executable, "-c", child])
    child_pid = grandchild_pid = None
    try:
        child_pid = int(wait_file(child_file))
        grandchild_pid = int(wait_file(grandchild_file))
        if not timeout:
            wrapper.terminate()
        result = wrapper.wait(timeout=8)
        assert not alive(child_pid)
        assert not alive(grandchild_pid)
        assert result == (124 if timeout else 143)
        assert unrelated.poll() is None
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
        wrapper.wait()
        for pid in (child_pid, grandchild_pid):
            if pid is not None and alive(pid):
                os.kill(pid, signal.SIGKILL)
        unrelated.terminate()
        unrelated.wait()


def test_signal_during_launch_is_retained_until_child_is_owned(tmp_path: Path) -> None:
    # The signal arrives within Popen, before run() has received the process object.
    script = tmp_path / "launch.py"
    script.write_text('''import os, signal, subprocess
from harness import agent_exec
original = subprocess.Popen
def launch(*args, **kwargs):
    child = original(*args, **kwargs)
    os.kill(os.getpid(), signal.SIGTERM)
    return child
subprocess.Popen = launch
raise SystemExit(agent_exec.run(["sleep", "30"], timeout_s=30, stdin_file=None, log=None))
''')
    assert subprocess.run([sys.executable, str(script)], timeout=8, check=False).returncode == 143


def test_already_reaped_child_and_launch_failure_restore_handlers() -> None:
    handlers = [signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)]
    child = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    child.wait()
    agent_exec.stop_group(child)
    with pytest.raises(FileNotFoundError):
        agent_exec.run(["/missing-agent-executable"], timeout_s=1, stdin_file=None, log=None)
    assert [signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)] == handlers


def test_cleanup_refuses_a_process_that_does_not_own_its_session() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        with pytest.raises(RuntimeError, match="no longer owns"):
            agent_exec.stop_group(child)
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait()
