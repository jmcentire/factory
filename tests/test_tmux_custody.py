"""Real tmux pause acceptance. Only private-server disposable counters are controlled."""
from __future__ import annotations

import datetime
import importlib
import json
import pathlib
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import pytest

import harness.orchestrator_channel  # noqa: F401

s = importlib.import_module('seat_supervisor')
w = importlib.import_module('lane_watchdog')
F = pathlib.Path(__file__).resolve().parents[1]


def wait_for(predicate, seconds=6):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.1)
    raise AssertionError('bounded fixture observation timed out')


class Fixture:
    def __init__(self, root, monkeypatch):
        self.root = root
        (root/'orchestrator').mkdir()
        (root/'tmux-lanes').mkdir()
        (root/'harness.json').write_text(json.dumps({'validator_agent': 'kickoff'}))
        self.socket = str(pathlib.Path(tempfile.mkdtemp(prefix='fvc-test-'))/'tmux.sock')
        self.identities = []
        self.workers = {}
        self.panes = {}
        self.events = []
        self.script = root/'counter.py'
        self.script.write_text(
            'import pathlib,sys,time\np=pathlib.Path(sys.argv[1])\n'
            'for n in range(2000):\n with p.open("a") as f: f.write(str(n)+"\\n")\n'
            ' time.sleep(.05)\n')
        self.monkeypatch = monkeypatch
        monkeypatch.setattr(s.escalate, 'resolve', lambda *a, **kw: self.events.append(kw))

    def tmux(self, *args, check=True):
        return subprocess.run(['tmux', '-S', self.socket, *args], check=check,
                              capture_output=True, text=True, timeout=10)

    def launch(self, name, *, wrapped=True):
        session, window = ('outside', 'seat') if name == 'external' else ('fixture', name)
        command = [sys.executable, str(self.script), str(self.root/f'{name}.counter')]
        log = self.root/'tmux-lanes'/f'{name}.log'
        if wrapped:
            command = [sys.executable, str(F/'harness/lane_agent.py'), 'run', '--slot', name,
                       '--log', str(log), '--exits', str(log)+'.exits', '--', *command]
        args = (['-f', '/dev/null', 'new-session', '-d', '-s', session] if name in
                ('validator', 'external') else ['new-window', '-d', '-t', '=fixture'])
        out = self.tmux(*args, '-n', window, '-P', '-F', '#{pane_pid} #{pane_id}',
                        'exec '+shlex.join(command)).stdout.split()
        pid, pane = int(out[0]), out[1]
        table = w.process_snapshot()
        assert table is not None and pid in table
        root_identity = {'pid': pid, 'started': table[pid]['started']}
        self.identities.append(root_identity)
        self.panes[name] = (pane, root_identity)
        if wrapped:
            path = self.root/'tmux-lanes'/f'{name}-process.json'
            wait_for(lambda: path.exists() and
                     bool(json.loads(path.read_text())['worker']['started']))
            identity = json.loads(path.read_text())['worker']
            self.identities.append(identity)
        else:
            identity = root_identity
        self.workers[name] = identity
        wait_for(lambda: self.count(name) is not None)
        server = self.tmux('display-message', '-p', '#{pid}').stdout.strip()
        self.monkeypatch.setenv('TMUX', f'{self.socket},{server},0')
        addressed = subprocess.check_output(['tmux', 'display-message', '-p', '#{socket_path}'],
                                           text=True).strip()
        assert addressed == self.socket
        return identity

    def count(self, name):
        path = self.root/f'{name}.counter'
        if not path.exists():
            return None
        lines = path.read_text().splitlines()
        return int(lines[-1]) if lines else None

    def cli(self, command, *args, codes=(0,)):
        result = subprocess.run([sys.executable, str(F/'harness/seat_supervisor.py'), command,
                                 '--root', str(self.root), '--session', 'fixture', *args],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode in codes, (result.stdout, result.stderr)
        return result

    def bind(self):
        worker = self.workers['external']
        return self.cli('bind-validator', '--pid', str(worker['pid']),
                        '--started', worker['started'], '--pane', self.panes['external'][0])

    def tick(self):
        result = s.tick(self.root, s.Seats('fixture'), s.Journal(self.root),
                        ttl=datetime.timedelta(seconds=1),
                        notify=lambda *a, **kw: self.events.append(kw))
        with (self.root/'fixture-observations.jsonl').open('a') as out:
            out.write(json.dumps(result)+'\n')
        return result

    def setup_roles(self):
        self.launch('validator', wrapped=False)  # inbox mirror, not executing Validator
        self.launch('external')
        self.launch('coder')
        self.launch('tester')
        self.bind()

    def control(self, action):
        return self.cli('control', '--seat', 'validator', '--action', action,
                        '--rule', 'fixture', '--evidence', 'synthetic only',
                        '--correction', 'wait for explicit fixture resume', codes=(0, 75))

    def cleanup(self):
        self.tmux('kill-server', check=False)
        # Only recorded identities from this fixture, revalidated immediately before signal.
        for identity in self.identities:
            w.signal_identity(identity, signal.SIGKILL)
        def gone():
            table = w.process_snapshot()
            return table is not None and not any(i['pid'] in table and
                table[i['pid']]['started'] == i['started'] and
                not table[i['pid']]['state'].startswith('Z') for i in self.identities)
        wait_for(gone)
        (self.root/'cleanup.json').write_text(json.dumps({'verified': True,
                                                        'identities': self.identities}))


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    if shutil.which('tmux') is None:
        pytest.skip('tmux is not installed')
    # A sandbox denial is a failure here, never a successful activation or silent skip.
    assert w.process_snapshot() is not None
    value = Fixture(tmp_path, monkeypatch)
    try:
        yield value
    finally:
        value.cleanup()


def test_real_tmux_root_auto_resume_is_reproduced_and_binding_refused(fixture):
    fixture.launch('validator', wrapped=False)
    worker = fixture.launch('external', wrapped=False)
    before = fixture.count('external')
    w.signal_identity(worker, signal.SIGSTOP)
    wait_for(lambda: fixture.count('external') > before + 5)
    result = fixture.cli('bind-validator', '--pid', str(worker['pid']), '--started',
                         worker['started'], '--pane', fixture.panes['external'][0], codes=(64,))
    assert 'pane-root' in result.stderr


def test_real_tmux_child_stop_resume_and_lag_have_sustained_no_progress(fixture):
    fixture.setup_roles()
    fixture.cli('check-validator')
    stop = json.loads(fixture.control('stop').stdout)
    assert not stop['acknowledged']  # cannot certify pause from one transient snapshot
    wait_for(lambda: set(fixture.tick()['paused']) == {'validator', 'coder', 'tester'})
    before = {name: fixture.count(name) for name in fixture.workers}
    for _ in range(10):
        time.sleep(.1)
        assert set(fixture.tick()['paused']) == {'validator', 'coder', 'tester'}
        assert all(fixture.count(n) == before[n] for n in ('external', 'coder', 'tester'))
    assert fixture.count('validator') > before['validator']  # mirror keeps running
    fixture.cli('check-validator', codes=(64,))
    fixture.control('resume')
    wait_for(lambda: not fixture.tick()['holds'])
    fixture.cli('check-validator')
    wait_for(lambda: all(fixture.count(n) > before[n] for n in fixture.workers))
    old = (datetime.datetime.now(datetime.UTC)-datetime.timedelta(seconds=5)).isoformat()
    (fixture.root/'orchestrator/activity.jsonl').write_text(
        json.dumps({'cursor': 1, 'ts': old})+'\n')
    wait_for(lambda: set(fixture.tick()['paused']) == {'validator', 'coder', 'tester'})
    before = fixture.count('external')
    time.sleep(.3)
    assert fixture.count('external') == before
    # Synthetic input to deterministic supervision, not a claimed genuine AG assessment.
    (fixture.root/'orchestrator/reports.jsonl').write_text(
        json.dumps({'assessment': {'through_cursor': 1}})+'\n')
    wait_for(lambda: not fixture.tick()['holds'])
    wait_for(lambda: fixture.count('external') > before)


def test_real_tmux_failed_signal_retains_hold_without_blind_retry(fixture, monkeypatch):
    fixture.setup_roles()
    sent = []

    def denied(identity, sig):
        sent.append((identity, sig))
        raise PermissionError('synthetic permission failure')

    monkeypatch.setattr(s, 'signal_identity', denied)
    seats = s.Seats('fixture')
    result = s.control(fixture.root, seats, seat='validator', action='stop',
                       rule='fixture', evidence='synthetic', correction='hold')
    assert not result['acknowledged'] and len(sent) == 3
    for _ in range(3):
        assert fixture.tick()['holds']
    assert len(sent) == 3
    assert not fixture.tick()['paused']


def test_real_tmux_stale_identity_never_signals_the_live_replacement(fixture):
    fixture.setup_roles()
    path = fixture.root/'orchestrator/validator-process.json'
    binding = json.loads(path.read_text())
    binding['identity']['started'] = 'stale identity fixture'
    path.write_text(json.dumps(binding))
    before = fixture.count('external')
    result = json.loads(fixture.control('stop').stdout)
    assert not result['acknowledged'] and result['identity_error']
    wait_for(lambda: fixture.count('external') > before + 5)
    assert fixture.tick()['holds']['validator']


def test_real_tmux_terminal_exit_is_never_acknowledged_as_resumable_pause(fixture):
    fixture.setup_roles()
    fixture.control('stop')
    wait_for(lambda: set(fixture.tick()['paused']) == {'validator', 'coder', 'tester'})
    identity = fixture.workers['external']
    w.signal_identity(identity, signal.SIGKILL)  # explicit terminal fixture cleanup, not pause
    wait_for(lambda: identity['pid'] not in w.process_snapshot())
    result = json.loads(fixture.control('resume').stdout)
    assert not result['acknowledged'] and result['identity_error']
    assert result['acknowledgements']['validator']['state'] == 'exited'
    seats = s.Seats('fixture')
    seats.configure(fixture.root)
    assert 'not paused' in seats.transition('validator', identity['pid'], False)['error']
    assert fixture.tick()['holds']


def test_real_tmux_rebind_preserves_holds_until_explicit_validation_resume(fixture):
    from tests.test_orchestrator_channel import assessment
    channel = importlib.import_module('orchestrator_channel')
    fixture.setup_roles()
    fixture.control('stop')
    wait_for(lambda: set(fixture.tick()['paused']) == {'validator', 'coder', 'tester'})
    old = fixture.workers['external']
    w.signal_identity(old, signal.SIGKILL)  # only a recorded disposable counter
    wait_for(lambda: old['pid'] not in w.process_snapshot())
    new = fixture.launch('replacement')
    w.signal_identity(new, signal.SIGSTOP)
    wait_for(lambda: 'T' in w.process_snapshot()[new['pid']]['state'])
    metadata = json.loads((fixture.root/'harness.json').read_text())
    metadata.update(orchestrator_mode='resident-monitoring', status='open')
    (fixture.root/'harness.json').write_text(json.dumps(metadata))
    cursor = channel.append_activity(fixture.root, kind='deterministic_signal', source='dispatcher',
                                     detail='synthetic paused process replacement')
    channel.record_assessment(fixture.root, assessment(cursor))
    digest = channel._read_jsonl(fixture.root/'orchestrator/reports.jsonl')[-1]['assessment_digest']
    result = fixture.cli('rebind-validator', '--pid', str(new['pid']), '--started', new['started'],
                         '--pane', fixture.panes['replacement'][0],
                         '--previous-pid', str(old['pid']),
                         '--previous-started', old['started'], '--assessment-digest', digest)
    receipt = json.loads(pathlib.Path(json.loads(result.stdout)['receipt']).read_text())
    assert receipt['previous_binding']['identity'] == old
    assert 'stop:validator-rebind-validation' in s.load_state(fixture.root)['validator']
    wait_for(lambda: set(fixture.tick()['paused']) == {'validator', 'coder', 'tester'})
    before = {name: fixture.count(name) for name in ('replacement', 'coder', 'tester')}
    time.sleep(1.1)
    assert all(fixture.count(name) == count for name, count in before.items())
    fixture.cli('check-validator', codes=(64,))
    fixture.control('resume')
    wait_for(lambda: not fixture.tick()['holds'])
    fixture.cli('check-validator')
    wait_for(lambda: all(fixture.count(name) > count for name, count in before.items()))
