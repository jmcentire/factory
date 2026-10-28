"""Synthetic policy responses exercise durable custody; installed policies stay untouched."""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import threading
import time

import pytest

from harness import lane_custody
from harness import policy_custody as policy
from tests.test_lane_custody import HOME, plan_document, request
from tests.test_orchestrator_channel import assessment, resident_root
from tests.test_signet_hooks import fixed, response


@pytest.fixture
def fixture(tmp_path):
    root = resident_root(tmp_path)
    path, digest, _ = fixed(tmp_path)
    document = plan_document(tmp_path)
    document['policy'] = {
        'client': 'claude', 'contract': str(path), 'contract_digest': digest,
        'root': str(root), 'prerequisite': None,
        'sources': {name: hashlib.sha256((HOME / name).read_bytes()).hexdigest()
                    for name in ('harness/policy_custody.py', 'harness/signet_hooks.py')}}
    plan = lane_custody.Plan.parse(document)
    receipts = tmp_path / 'receipts'
    receipts.mkdir()
    return plan, receipts, root


def tool_request(plan, identifier='first', text='synthetic-private-input'):
    return {'kind': 'policy', 'request_id': identifier, 'plan_digest': plan.digest,
            'payload': {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash',
                        'tool_input': {'command': text}}}


def evaluator(monkeypatch, decision='deny', reason='synthetic refusal', revision='policy-1'):
    calls = []

    def evaluate(client, payload, fixed):
        calls.append(payload)
        return response(decision, permissionDecisionReason=reason), {'policy_revision': revision}

    monkeypatch.setattr(policy.signet_hooks, 'evaluate_with_health', evaluate)
    return calls


def test_denial_holds_changed_calls_and_fixed_operations_without_payload_logging(
        fixture, monkeypatch):
    plan, receipts, _ = fixture
    calls = evaluator(monkeypatch)
    first = policy.evaluate(plan, tool_request(plan), receipts)
    assert first['output']['hookSpecificOutput']['permissionDecision'] == 'deny'
    changed_calls = evaluator(monkeypatch, 'allow')
    changed = policy.evaluate(plan, tool_request(plan, 'changed', 'different spelling'), receipts)
    assert changed['output']['hookSpecificOutput']['permissionDecision'] == 'deny'
    assert len(calls) == 1
    assert not changed_calls
    with pytest.raises(ValueError, match='policy hold'):
        lane_custody.execute(plan, request(plan), receipts)
    assert not (receipts / 'request-1.log').exists()
    journal = ''.join(p.read_text() for p in receipts.glob('*.json'))
    assert 'synthetic-private-input' not in journal
    assert 'synthetic refusal' not in journal
    assert len(policy.holds(receipts)) == 1


@pytest.mark.parametrize('matching', [True, False])
@pytest.mark.parametrize('reason', ['Use Kindex first',
                                  'Signet policy context must be received: search before edit',
                                  'Unrelated refusal'])
def test_only_exact_owner_prerequisite_allows_genuine_recovery(
        fixture, monkeypatch, matching, reason):
    plan, receipts, _ = fixture
    plan.document['policy']['prerequisite'] = {'reason': [
        'Use Kindex first', 'Signet policy context must be received: search before edit'],
                                             'policy_revision': 'policy-1'}
    evaluator(monkeypatch, reason=reason,
              revision='policy-1' if matching else 'changed-policy')
    result = policy.evaluate(plan, tool_request(plan), receipts)
    assert result['output']['hookSpecificOutput']['permissionDecision'] == 'deny'
    calls = evaluator(monkeypatch, 'allow')
    recovery = tool_request(plan, 'recovery')
    recovery['payload'].update(tool_name='mcp__kindex__search', tool_input={'query': 'rules'})
    next_result = policy.evaluate(plan, recovery, receipts)
    recoverable = matching and reason != 'Unrelated refusal'
    assert len(calls) == int(recoverable)
    assert next_result['output']['hookSpecificOutput']['permissionDecision'] == (
        'allow' if recoverable else 'deny')


@pytest.mark.parametrize('case', ['question-only', 'unreviewed', 'wrong-request',
                                  'substantive', 'extra-question'])
def test_reviewed_question_recovery_preserves_author_admission_block(fixture, monkeypatch, case):
    from harness.attention_gate import BlockingEventPending, check_lane_admission
    from harness.lane_dialogue import record_question

    plan, receipts, root = fixture
    evaluator(monkeypatch)
    policy.evaluate(plan, tool_request(plan), receipts)
    question = 'Where does the comment stay after a split?'
    record_question(root, 'coder', question)
    notice = json.loads((receipts / 'policy-first.notice.json').read_text())
    pending = policy.holds(receipts)[0]
    reviewed = f"Review policy-first request {pending['request_digest']} for recovery."
    if case == 'wrong-request':
        reviewed = 'Review policy-first request ' + '0' * 64
    report = policy.orchestrator_channel.record_assessment(root, assessment(
        notice['cursor'], decision='block', planning_mode='clarify',
        specification_questions=[question] + (['An unrelated decision?']
                                             if case == 'extra-question' else []),
        aligned=case != 'substantive',
        summary=reviewed if case != 'unreviewed' else 'Unrelated review.'))
    with pytest.raises(BlockingEventPending):
        check_lane_admission(root, 'coder')
    if case != 'question-only':
        with pytest.raises(ValueError, match='does not dispose'):
            policy.release(plan, receipts, 'policy-first', report['assessment_digest'])
        assert policy.holds(receipts)
        return
    terminal = (receipts / 'policy-first.terminal.json').read_bytes()
    policy.release(plan, receipts, 'policy-first', report['assessment_digest'])
    assert not policy.holds(receipts)
    assert (receipts / 'policy-first.terminal.json').read_bytes() == terminal
    with pytest.raises(BlockingEventPending):
        check_lane_admission(root, 'coder')
    calls = evaluator(monkeypatch, 'allow')
    recovery = tool_request(plan, 'fresh-kindex')
    recovery['payload'].update(tool_name='mcp__kindex__search', tool_input={'query': 'rules'})
    policy.evaluate(plan, recovery, receipts)
    assert calls == [recovery['payload']]


def accept(root, cursor):
    return policy.orchestrator_channel.record_assessment(root, assessment(cursor))


def test_release_requires_current_accepted_assessment_and_retains_history(fixture, monkeypatch):
    plan, receipts, root = fixture
    evaluator(monkeypatch)
    policy.evaluate(plan, tool_request(plan), receipts)
    with pytest.raises(policy.orchestrator_channel.OrchestratorChannelError):
        policy.release(plan, receipts, 'policy-first', 'unreviewed')
    notice = json.loads((receipts / 'policy-first.notice.json').read_text())
    accept(root, notice['cursor'])
    row = policy.orchestrator_channel._read_jsonl(root / 'orchestrator/reports.jsonl')[-1]
    with pytest.raises(ValueError, match='does not dispose'):
        policy.release(plan, receipts, 'policy-first', 'wrong')
    policy.release(plan, receipts, 'policy-first', row['assessment_digest'])
    assert not policy.holds(receipts)
    assert (receipts / 'policy-first.terminal.json').exists()
    calls = evaluator(monkeypatch, 'allow')
    policy.evaluate(plan, tool_request(plan, 'after-review'), receipts)
    assert len(calls) == 1
    completed = lane_custody.execute(plan, request(plan), receipts)
    assert completed['status'] == 'completed' and completed['returncode'] == 0


def test_allowed_policy_does_not_block_fixed_operation_or_allow_namespace_collision(
        fixture, monkeypatch):
    plan, receipts, _ = fixture
    evaluator(monkeypatch, 'allow')
    policy.evaluate(plan, tool_request(plan), receipts)
    assert lane_custody.execute(plan, request(plan), receipts)['returncode'] == 0
    with pytest.raises(ValueError, match='admitted plan'):
        lane_custody.execute(plan, request(plan, 'policy-forged'), receipts)


def test_crash_is_held_and_notice_recovery_requires_new_review(fixture, monkeypatch):
    plan, receipts, root = fixture

    def fail(*args):
        raise ValueError('synthetic evaluation failure')

    monkeypatch.setattr(policy.signet_hooks, 'evaluate_with_health', fail)
    with pytest.raises(ValueError, match='evaluation failure'):
        policy.evaluate(plan, tool_request(plan), receipts)
    assert policy.holds(receipts)[0]['outcome'] == 'uncertain'
    with pytest.raises(ValueError, match='await current assessment'):
        policy.release(plan, receipts, 'policy-first', 'unreviewed')
    notice = json.loads((receipts / 'policy-first.notice.json').read_text())
    accept(root, notice['cursor'])
    row = policy.orchestrator_channel._read_jsonl(root / 'orchestrator/reports.jsonl')[-1]
    policy.release(plan, receipts, 'policy-first', row['assessment_digest'])
    assert not policy.holds(receipts)


@pytest.mark.parametrize('tamper', ['source', 'receipt', 'symlink', 'release-socket'])
def test_invalid_evidence_or_release_route_fails_closed(fixture, monkeypatch, tamper):
    plan, receipts, _ = fixture
    evaluator(monkeypatch)
    if tamper == 'source':
        plan.document['policy']['sources']['harness/policy_custody.py'] = '0' * 64
        with pytest.raises(ValueError, match='implementation changed'):
            policy.evaluate(plan, tool_request(plan), receipts)
    elif tamper == 'release-socket':
        with pytest.raises(ValueError, match='invalid policy custody request'):
            policy.evaluate(plan, {**tool_request(plan), 'kind': 'release'}, receipts)
    else:
        start = receipts / 'policy-forged.started.json'
        if tamper == 'receipt':
            start.write_text('{}')
        else:
            start.symlink_to(receipts / 'absent')
        with pytest.raises((ValueError, OSError)):
            policy.evaluate(plan, tool_request(plan), receipts)


def test_disconnected_custody_never_falls_back_to_local_evaluator(fixture, monkeypatch):
    plan, receipts, _ = fixture
    calls = evaluator(monkeypatch, 'allow')
    with pytest.raises(OSError):
        policy.signet_hooks.evaluate_host('claude', tool_request(plan)['payload'],
                                         str(receipts / 'absent.sock'), plan.digest)
    assert not calls


def test_generated_binding_is_explicit_and_requires_both_fields(fixture):
    plan, _, _ = fixture
    binding = plan.document['policy']
    args = ('claude', pathlib.Path(binding['contract']), binding['contract_digest'],
            pathlib.Path(plan.document['python']))
    with pytest.raises(ValueError, match='complete host binding'):
        policy.signet_hooks.configuration(*args, custody_socket='/tmp/fixture.sock')
    result = policy.signet_hooks.configuration(*args, custody_socket='/tmp/fixture.sock',
                                              custody_digest=plan.digest)
    assert '--custody-digest' in result['hooks']['PreToolUse'][0]['hooks'][0]['command']


@pytest.mark.parametrize('decision', ['block', 'no-op'])
def test_block_or_stale_assessment_cannot_release(fixture, monkeypatch, decision):
    plan, receipts, root = fixture
    evaluator(monkeypatch)
    policy.evaluate(plan, tool_request(plan), receipts)
    cursor = json.loads((receipts / 'policy-first.notice.json').read_text())['cursor']
    policy.orchestrator_channel.record_assessment(root, assessment(cursor, decision=decision))
    row = policy.orchestrator_channel._read_jsonl(root / 'orchestrator/reports.jsonl')[-1]
    if decision == 'no-op':
        policy.orchestrator_channel.append_activity(
            root, kind='cadence', source='dispatcher', detail='new unreviewed activity')
    with pytest.raises((ValueError, RuntimeError)):
        policy.release(plan, receipts, 'policy-first', row['assessment_digest'])
    assert policy.holds(receipts)


def test_cadence_noop_cannot_override_unresolved_earlier_block(fixture, monkeypatch):
    plan, receipts, root = fixture
    evaluator(monkeypatch)
    policy.evaluate(plan, tool_request(plan), receipts)
    cursor = json.loads((receipts / 'policy-first.notice.json').read_text())['cursor']
    policy.orchestrator_channel.record_assessment(root, assessment(cursor, decision='block'))
    next_cursor = policy.orchestrator_channel.append_activity(
        root, kind='cadence', source='dispatcher', detail='hold remains; no new model work')
    accept(root, next_cursor)
    row = policy.orchestrator_channel._read_jsonl(root / 'orchestrator/reports.jsonl')[-1]
    from attention_gate import BlockingEventPending
    with pytest.raises(BlockingEventPending):
        policy.release(plan, receipts, 'policy-first', row['assessment_digest'])
    assert policy.holds(receipts)


def test_real_socket_round_trip_retains_denial_across_fresh_hook_processes(fixture, monkeypatch):
    plan, receipts, _ = fixture
    # The child sends two separate connections through the actual hook adapter. The server
    # validates its OS-supplied PID, and the second reworded request never reaches Signet.
    code = '''import json,sys
sys.path.insert(0,sys.argv[1])
import signet_hooks
config=json.loads(sys.stdin.readline())
for index,spelling in enumerate(('synthetic original', 'synthetic reworded')):
 if index: sys.stdin.readline()
 payload={'hook_event_name':'PreToolUse','tool_name':'Bash','tool_input':{'command':spelling}}
 print(json.dumps(signet_hooks.evaluate_host('claude',payload,config['socket'],config['digest'])),
       flush=True)
'''
    child = subprocess.Popen([sys.executable, '-c', code, str(HOME / 'harness')],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    calls = evaluator(monkeypatch)
    errors = []
    try:
        table = lane_custody.lane_watchdog.process_snapshot()
        assert table is not None and child.pid in table
        plan.document['seat'] = {'pid': child.pid, 'started': table[child.pid]['started']}
        receipts.chmod(0o700)
        with tempfile.TemporaryDirectory(prefix='f-policy-', dir='/tmp') as directory:
            address = pathlib.Path(directory) / 'fixture.sock'

            def server():
                try:
                    lane_custody.serve(plan, address, receipts, once=True)
                except Exception as error:
                    errors.append(error)

            # Keep one socket server for both calls by invoking the second child request
            # only after the first endpoint has been re-established.
            worker = threading.Thread(target=server, daemon=True)
            worker.start()
            deadline = time.monotonic() + 3
            while not address.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            assert address.exists()
            assert child.stdin is not None and child.stdout is not None
            child.stdin.write(json.dumps({'socket': str(address), 'digest': plan.digest}) + '\n')
            child.stdin.flush()
            import select
            assert select.select([child.stdout], [], [], 10)[0]
            first = child.stdout.readline()
            worker.join(timeout=3)
            assert not worker.is_alive() and not errors
            worker = threading.Thread(target=server, daemon=True)
            worker.start()
            deadline = time.monotonic() + 3
            while not address.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            assert address.exists()
            output, error = child.communicate('continue\n', timeout=10)
            worker.join(timeout=3)
            assert child.returncode == 0, error
            assert not worker.is_alive() and not errors
            rows = [json.loads(line) for line in (first + output).splitlines()]
            assert len(rows) == 2
            assert all(row['hookSpecificOutput']['permissionDecision'] == 'deny' for row in rows)
            assert len(calls) == 1 and policy.holds(receipts)
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=3)
