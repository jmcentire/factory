"""Correcting quoted protocol data never answers a real question or resumes work."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from harness.lane_dialogue import (
    LaneDialogueError,
    correct_tool_echo,
    pending_questions,
    plan_message,
    record_question,
)
from tests.test_harness_scripts import load_dispatcher
from tests.test_orchestrator_channel import assessment, resident_root
from tests.test_validator_rebind import handoff, perform  # noqa: F401 - shared process fixture


def echo(root: Path) -> tuple[dict, Path]:
    stream = root / 'tmux-lanes/coder.qualify-1-codex-events.jsonl'
    stream.parent.mkdir(exist_ok=True)
    raw = json.dumps({'type': 'item.completed', 'item': {
        'id': 'item_1', 'type': 'command_execution',
        'aggregated_output': 'Ask `FACTORY_QUESTION: <question>` instead of guessing.\n',
        'exit_code': 0, 'status': 'completed'}})
    stream.write_text(raw + '\n')
    match = re.search(r'\bFACTORY_QUESTION:\s*(\S.*)$', raw)
    assert match
    question, _ = record_question(root, 'coder', match.group(1))
    return question, stream


def reviewed(root: Path) -> str:
    import orchestrator_channel as channel
    cursor = channel.append_activity(root, kind='deterministic_signal', source='dispatcher',
                                     detail='review exact retained placeholder evidence')
    report = channel.record_assessment(root, assessment(cursor, planning_mode='clarify',
        decision='block', specification_questions=[q['text'] for q in pending_questions(root)]))
    return report['assessment_digest']


def test_correction_preserves_history_and_real_question_without_fake_delivery(tmp_path):
    root = resident_root(tmp_path)
    q, stream = echo(root)
    real, _ = record_question(root, 'tester', 'Should a stale edit return 409?')
    digest = reviewed(root)
    journal = root / 'dialogue/journal.jsonl'
    before = journal.read_bytes()
    result = correct_tool_echo(root, q['question_id'], stream.name, digest)
    assert journal.read_bytes().startswith(before)
    assert result['record_type'] == 'question-corrected'
    assert 'aggregated_output' not in json.dumps(result)
    assert [q['question_id'] for q in pending_questions(root)] == [real['question_id']]
    assert not any('message-' in row['record_type'] for row in
                   map(json.loads, journal.read_text().splitlines()))
    assert correct_tool_echo(root, q['question_id'], stream.name, digest) == result
    assert len(journal.read_text().splitlines()) == 3
    with pytest.raises(LaneDialogueError, match='corrected'):
        plan_message(root, sender='validator', lane='coder', message_kind='spec-answer',
                     text='No invented answer', basis='fixture', authority='ratified-spec',
                     question_id=q['question_id'])


@pytest.mark.parametrize('defect', ['wrong-review', 'real-question', 'wrong-lane', 'traversal',
    'symlink', 'duplicate-event', 'not-tool', 'author-placeholder', 'incomplete', 'planned-answer',
    'malformed', 'changed-after-correction'])
def test_correction_refuses_unproven_or_genuine_question(tmp_path, defect):
    root = resident_root(tmp_path)
    q, stream = echo(root)
    digest = reviewed(root)
    source = stream.name
    if defect == 'wrong-review':
        digest = 'sha256:' + '0' * 64
    elif defect == 'real-question':
        q, _ = record_question(root, 'coder', 'What does <question> mean in the docs?')
        digest = reviewed(root)
    elif defect == 'wrong-lane':
        source = 'tester.qualify-1-codex-events.jsonl'
    elif defect == 'traversal':
        source = '../' + stream.name
    elif defect == 'symlink':
        target = stream.with_suffix('.retained')
        stream.rename(target)
        stream.symlink_to(target)
    elif defect == 'duplicate-event':
        stream.write_text(stream.read_text() * 2)
    elif defect == 'not-tool':
        stream.write_text(stream.read_text().replace('command_execution', 'agent_message'))
    elif defect == 'author-placeholder':
        with stream.open('a') as output:
            output.write(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message',
                'text': 'FACTORY_QUESTION: Is <question> literal?'}}) + '\n')
    elif defect == 'incomplete':
        stream.write_text(stream.read_text().rstrip('\n'))
    elif defect == 'malformed':
        stream.write_text(stream.read_text() + 'not JSON\n')
    elif defect == 'changed-after-correction':
        correct_tool_echo(root, q['question_id'], source, digest)
        stream.write_text(stream.read_text() + '{}\n')
    elif defect == 'planned-answer':
        plan_message(root, sender='validator', lane='coder', message_kind='spec-answer',
                     text='Retain planned semantics', basis='fixture', authority='ratified-spec',
                     question_id=q['question_id'])
    before = (root / 'dialogue/journal.jsonl').read_bytes()
    with pytest.raises((LaneDialogueError, OSError)):
        correct_tool_echo(root, q['question_id'], source, digest)
    assert (root / 'dialogue/journal.jsonl').read_bytes() == before


def test_tampered_correction_receipt_fails_closed(tmp_path):
    root = resident_root(tmp_path)
    q, stream = echo(root)
    correct_tool_echo(root, q['question_id'], stream.name, reviewed(root))
    journal = root / 'dialogue/journal.jsonl'
    rows = list(map(json.loads, journal.read_text().splitlines()))
    rows[-1]['question_digest'] = 'sha256:' + '0' * 64
    journal.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    with pytest.raises(LaneDialogueError, match='correction receipt'):
        pending_questions(root)


def test_dispatcher_ignores_tool_json_and_quoted_examples_but_keeps_direct_question(tmp_path):
    mod = load_dispatcher()
    root = resident_root(tmp_path)
    q, stream = echo(root)
    # Exercise classification independently of existing legacy journal data.
    dispatcher = mod.Dispatcher('fixture', root, 30)
    dispatcher.check_window('coder', stream.read_text())
    dispatcher.check_window('coder', 'The docs say `FACTORY_QUESTION: <question>` instead.')
    assert len(pending_questions(root)) == 1
    dispatcher.check_window('coder', 'FACTORY_QUESTION: Should an empty write be rejected?')
    assert [x['text'] for x in pending_questions(root)] == [
        q['text'], 'Should an empty write be rejected?']


def test_correction_does_not_release_rebind_or_process_holds(handoff):  # noqa: F811
    import orchestrator_channel as channel

    from tests.test_seat_supervisor import supervisor
    root, _, old, _, _ = handoff
    q, stream = echo(root)
    blocked = reviewed(root)
    correct_tool_echo(root, q['question_id'], stream.name, blocked)
    with pytest.raises(supervisor.SupervisorError, match='accepted supervision disposition'):
        perform(handoff, assessment_digest=blocked)
    assert supervisor._read_control(root / 'orchestrator/validator-process.json') == old
    cursor = channel.append_activity(root, kind='deterministic_signal', source='dispatcher',
                                     detail='inspect append-only correction, review held handoff')
    accepted = channel.record_assessment(root, assessment(cursor))
    perform(handoff, assessment_digest=accepted['assessment_digest'])
    assert set(supervisor.load_state(root)['validator']) == {
        'stop:prior-incident', 'stop:validator-rebind-validation'}
    assert supervisor.load_state(root)['coder'] == ['validator-stopped']


def test_genuine_pending_question_still_prevents_noop_after_correction(tmp_path):
    import orchestrator_channel as channel
    root = resident_root(tmp_path)
    q, stream = echo(root)
    record_question(root, 'coder', 'Which API route owns edits?')
    correct_tool_echo(root, q['question_id'], stream.name, reviewed(root))
    cursor = channel.append_activity(root, kind='deterministic_signal', source='dispatcher',
                                     detail='real question still needs its answer')
    with pytest.raises(channel.OrchestratorChannelError, match='pending'):
        channel.record_assessment(root, assessment(cursor))
