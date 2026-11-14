"""Independent delayed native shutdown custody; synthetic native CLI only."""
import json
import os
import subprocess
import time

from tests.test_native_claude_contract import (
    execute,
    launch,
    read_events,
    status,
    wait_for,
)
from tests.test_native_claude_contract import (
    native_case as _native_case,
)

native_case = _native_case

def test_restart_waits_or_refuses_while_previous_owned_wrapper_is_stopping(native_case):
    class RedactedEnv(dict):
        def __repr__(self):
            return "<fixture environment redacted>"
    native_case['env'] = RedactedEnv(native_case['env'])
    release_old = native_case['runs'] / 'release-old-native'
    for name in ('cl', 'claude'):
        path = native_case['bin_dir'] / name
        recorder = path.read_text()
        hook = ("import signal, time\n"
                "def slow_shutdown(signum, frame):\n"
                f"    while not os.path.exists({str(release_old)!r}): time.sleep(.01)\n"
                "    raise SystemExit(0)\n"
                "signal.signal(signal.SIGTERM, slow_shutdown)\n"
                "signal.signal(signal.SIGHUP, slow_shutdown)\n")
        recorder = recorder.replace("import json, os, sys, subprocess, tty\n",
                                    "import json, os, sys, subprocess, tty\n" + hook)
        path.write_text(recorder)
    launch(native_case)
    before = wait_for(lambda: (row if (row := status(native_case)).get('phase')
                              == 'stop-reported' else None))
    root = native_case['runs'] / 'native-acceptance'
    original = json.loads((root / 'orchestrator-process.json').read_text())
    old_pid = original['wrapper']['pid']
    command = subprocess.run(['ps', '-ww', '-p', str(old_pid), '-o', 'command='],
                             capture_output=True, text=True).stdout
    assert str(root) in command and 'lane_agent.py' in command
    record = json.loads(execute(native_case['base'] + ['status'] + native_case['common'],
                                native_case['env']).stdout)
    child = None
    try:
        execute(['tmux', '-L', record['tmux_socket'], 'kill-server'], native_case['env'])
        wait_for(lambda: execute(['tmux', '-L', record['tmux_socket'], 'has-session',
                                  '-t', '=native-acceptance'], native_case['env']).returncode != 0)
        boundary = len(read_events(native_case['log']))
        child = subprocess.Popen(native_case['base'] + ['start'] + native_case['common'],
                                 env=native_case['env'], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        # The real prior wrapper waits for its slow synthetic native child.
        # A start may wait/refuse, but cannot overlap replacement native seats.
        deadline = time.monotonic() + 1.2
        while time.monotonic() < deadline and child.poll() is None:
            owned_start = subprocess.run(
                ['ps', '-p', str(old_pid), '-o', 'lstart='],
                capture_output=True, text=True, env={**os.environ, 'TZ': 'UTC'}).stdout.strip()
            assert ' '.join(owned_start.split()) == ' '.join(
                original['wrapper']['started'].split())
            events = read_events(native_case['log'])[boundary:]
            if any('--resume' in e.get('argv', []) for e in events):
                old_state = subprocess.run(['ps', '-p', str(old_pid), '-o', 'stat='],
                                           capture_output=True, text=True).stdout.strip()
                assert not old_state, ('replacement native child overlapped owned wrapper: '
                                       + old_state)
            time.sleep(.02)
        if child.poll() is not None:
            stdout, stderr = child.communicate()
            assert child.returncode != 0, stdout + stderr
        release_old.touch()
        def old_gone():
            cmd = subprocess.run(['ps', '-ww', '-p', str(old_pid), '-o', 'command='],
                                 capture_output=True, text=True).stdout
            return str(root) not in cmd
        wait_for(old_gone)
        if child.poll() is None:
            stdout, stderr = child.communicate(timeout=30)
            assert child.returncode == 0, stdout + stderr
        else:
            resumed = execute(native_case['base'] + ['start'] + native_case['common'],
                              native_case['env'])
            assert resumed.returncode == 0, resumed.stdout + resumed.stderr
        after = status(native_case)
        assert after['session_id'] == before['session_id']
        assert after['worker']['pid'] != before['worker']['pid']
        assert after['phase'] != 'exited'
        initial = [e for e in read_events(native_case['log']) if e.get('input', '').startswith(
            'Act as Factory validator.')]
        assert len(initial) == 1
    finally:
        release_old.touch()
        if child is not None and child.poll() is None:
            child.terminate()
            child.communicate(timeout=10)
