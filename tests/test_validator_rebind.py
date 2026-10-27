"""A process restart cannot discard Validator custody or turn a hold into permission."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.test_orchestrator_channel import assessment
from tests.test_seat_supervisor import kickoff_root, supervisor


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    import orchestrator_channel as channel
    root = kickoff_root(tmp_path)
    meta = json.loads((root / 'harness.json').read_text())
    meta.update(orchestrator_mode='resident-monitoring', status='open')
    (root / 'harness.json').write_text(json.dumps(meta))
    table = {
        101: {'pid': 101, 'ppid': 1, 'started': 'pane', 'state': 'S', 'cpu': '0:00.00'},
        102: {'pid': 102, 'ppid': 101, 'started': 'old', 'state': 'T', 'cpu': '0:00.00'},
        103: {'pid': 103, 'ppid': 102, 'started': 'old-child', 'state': 'T', 'cpu': '0:00.00'},
        104: {'pid': 104, 'ppid': 101, 'started': 'new', 'state': 'T', 'cpu': '0:00.00'}}
    monkeypatch.setattr(supervisor, 'process_snapshot', lambda: table)
    monkeypatch.setattr(supervisor.subprocess, 'run', lambda *a, **kw:
                        subprocess.CompletedProcess(a, 0, '101\n', ''))
    old = supervisor.bind_validator(root, 'fixture', 102, 'old', '%1')
    custody = {'identity': old['identity'], 'identities': [old['identity'],
                {'pid': 103, 'started': 'old-child'}], 'requested': 'stop',
                'state': 'stopped', 'acknowledged': True, 'attempts': {}}
    supervisor._store(root / 'orchestrator/seat-processes.json', {'validator': custody})
    supervisor.save_state(root, {'validator': ['stop:prior-incident'],
                                 'coder': ['validator-stopped']})
    cursor = channel.append_activity(root, kind='deterministic_signal', source='dispatcher',
                                     detail='scoped maintenance handoff; no productive resume')
    channel.record_assessment(root, assessment(cursor))
    digest = channel._read_jsonl(root / 'orchestrator/reports.jsonl')[-1]['assessment_digest']
    del table[102], table[103]
    return root, table, old, custody, digest


def perform(handoff, **changes):
    root, _, _, _, digest = handoff
    args = dict(session='fixture', pid=104, started='new', pane='%1',
                previous_pid=102, previous_started='old', assessment_digest=digest)
    return supervisor.rebind_validator(root, **(args | changes))


def test_handoff_retains_old_custody_all_holds_and_no_resume(handoff):
    root, _, old, custody, _ = handoff
    result = perform(handoff)
    receipt = supervisor._read_control(Path(result['receipt']))
    assert receipt['previous_binding'] == old and receipt['previous_custody'] == custody
    records = supervisor._read_control(root / 'orchestrator/seat-processes.json')
    assert records['validator']['identity'] == {'pid': 104, 'started': 'new'}
    assert records['validator']['state'] == 'pending'
    assert not records['validator']['acknowledged']
    state = supervisor.load_state(root)
    assert set(state['validator']) == {'stop:prior-incident', 'stop:validator-rebind-validation'}
    assert state['coder'] == ['validator-stopped']
    assert perform(handoff)['replayed']
    assert len(list((root / 'orchestrator').glob('validator-rebind-*.json'))) == 1


@pytest.mark.parametrize('defect', ['old-live', 'orphan', 'running', 'wrong-start',
                                  'wrong-prior', 'wrong-session', 'wrong-assessment', 'unknown',
                                  'running-child', 'new-pid-reused'])
def test_unqualified_handoff_never_mutates_binding(handoff, monkeypatch, defect):
    root, table, old, _, _ = handoff
    changes = {}
    if defect in ('old-live', 'orphan'):
        pid = 102 if defect == 'old-live' else 103
        table[pid] = {'pid': pid, 'ppid': 1, 'started': 'old' if pid == 102 else 'old-child',
                      'state': 'T', 'cpu': '0:00.00'}
    elif defect == 'running':
        table[104]['state'] = 'S'
    elif defect == 'wrong-start':
        changes['started'] = 'reused'
    elif defect == 'wrong-prior':
        changes['previous_started'] = 'different'
    elif defect == 'wrong-session':
        changes['session'] = 'other'
    elif defect == 'wrong-assessment':
        changes['assessment_digest'] = 'wrong'
    elif defect == 'unknown':
        monkeypatch.setattr(supervisor, 'process_snapshot', lambda: None)
    elif defect == 'running-child':
        table[105] = {'pid': 105, 'ppid': 104, 'started': 'new-child',
                      'state': 'S', 'cpu': '0:00.00'}
    elif defect == 'new-pid-reused':
        table[104]['started'] = 'different-process'
    with pytest.raises(supervisor.SupervisorError):
        perform(handoff, **changes)
    assert supervisor._read_control(root / 'orchestrator/validator-process.json') == old
    assert not list((root / 'orchestrator').glob('validator-rebind-*.json'))


@pytest.mark.parametrize('crash_at', ['state', 'custody', 'binding'])
def test_exact_retry_finishes_interrupted_handoff_without_losing_receipt(handoff, monkeypatch,
                                                                       crash_at):
    root, _, old, _, _ = handoff
    original = supervisor._store
    target = {'state': 'seat-state.json', 'custody': 'seat-processes.json',
              'binding': 'validator-process.json'}[crash_at]

    def fail(path, value):
        if path.name == target:
            raise OSError('synthetic interrupted persistence')
        original(path, value)

    monkeypatch.setattr(supervisor, '_store', fail)
    with pytest.raises(OSError):
        perform(handoff)
    retained = list((root / 'orchestrator').glob('validator-rebind-*.json'))
    assert len(retained) == 1
    before = retained[0].read_bytes()
    assert supervisor._read_control(root / 'orchestrator/validator-process.json') == old
    monkeypatch.setattr(supervisor, '_store', original)
    # Recovery can be newly reviewed after a cadence tick without changing the immutable
    # original receipt or inventing a second handoff subject.
    import orchestrator_channel as channel
    cursor = channel.append_activity(root, kind='cadence', source='dispatcher',
                                     detail='review the interrupted handoff')
    channel.record_assessment(root, assessment(cursor))
    digest = channel._read_jsonl(root / 'orchestrator/reports.jsonl')[-1]['assessment_digest']
    assert not perform(handoff, assessment_digest=digest)['replayed']
    assert retained[0].read_bytes() == before


def test_pid_reuse_does_not_signal_or_adopt_unrelated_prior_identity(handoff, monkeypatch):
    _, table, _, _, _ = handoff
    table[102] = {'pid': 102, 'ppid': 1, 'started': 'unrelated', 'state': 'S', 'cpu': '0:00.00'}
    calls = []
    monkeypatch.setattr(supervisor, 'signal_identity', lambda *a, **k: calls.append(a))
    assert not perform(handoff)['replayed']
    assert not calls


@pytest.mark.parametrize('state', ['unassessed', 'block'])
def test_new_or_blocking_supervision_prevents_handoff(handoff, state):
    import orchestrator_channel as channel
    root, _, old, _, _ = handoff
    cursor = channel.append_activity(root, kind='cadence', source='dispatcher',
                                     detail='new supervision evidence')
    changes = {}
    if state == 'block':
        report = assessment(cursor)
        report['decision'] = 'block'
        channel.record_assessment(root, report)
        changes['assessment_digest'] = channel._read_jsonl(
            root / 'orchestrator/reports.jsonl')[-1]['assessment_digest']
    with pytest.raises(supervisor.SupervisorError):
        perform(handoff, **changes)
    assert supervisor._read_control(root / 'orchestrator/validator-process.json') == old
    assert not list((root / 'orchestrator').glob('validator-rebind-*.json'))
