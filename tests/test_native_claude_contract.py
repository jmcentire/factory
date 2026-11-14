"""Independent black-box acceptance for Jeremy's native Claude requirements.

Only public Factory commands are invoked. The executable fixture observes the
process boundary; it does not emulate an LLM or prove real Claude compatibility.
"""
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

FACTORY = Path(os.environ.get('FACTORY_ACCEPTANCE_ROOT', Path(__file__).parents[1]))


def execute(argv, env, *, cwd=None):
    return subprocess.run(argv, cwd=cwd or FACTORY, env=env, text=True,
                          capture_output=True, timeout=30)


def manifest(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def native_case(tmp_path):
    home = tmp_path / 'home'
    profile = home / 'selected account with spaces'
    profile.mkdir(parents=True)
    (home / '.claude').mkdir()
    (home / '.claude.json').write_text('{"sentinel":"default trust unchanged"}')
    (home / '.claude' / 'settings.json').write_text('{"sentinel":"user tools and style"}')
    (profile / '.claude.json').write_text('{"sentinel":"selected trust unchanged"}')
    (profile / 'settings.json').write_text('{"sentinel":"selected native settings"}')
    watched = [home / '.claude', profile]
    before = {str(p): manifest(p) for p in watched}
    default_before = (home / '.claude.json').read_bytes()
    target = tmp_path / 'target'
    target.mkdir()
    env = {**os.environ, 'HOME': str(home), 'CLAUDE_CONFIG_DIR': str(profile),
           'FACTORY_VALIDATOR_AGENT': 'claude', 'FACTORY_ORCHESTRATOR_AGENT': 'claude',
           'FACTORY_PYTHON': sys.executable, 'ANTHROPIC_API_KEY': 'not-a-real-key',
           'FACTORY_CONFIG': str(tmp_path / 'no-config.toml')}
    Path(env['FACTORY_CONFIG']).write_text('')
    for name in ('FACTORY_VALIDATOR_MODEL', 'FACTORY_ORCHESTRATOR_MODEL',
                 'FACTORY_CODER_MODEL', 'FACTORY_TESTER_MODEL'):
        env.pop(name, None)
    for command in ([ 'git', 'init', '-q'],
                    ['git', 'config', 'user.name', 'Acceptance'],
                    ['git', 'config', 'user.email', 'acceptance@example.invalid']):
        assert execute(command, env, cwd=target).returncode == 0
    (target / 'README.md').write_text('Fixture public specification only.\n')
    assert execute(['git', 'add', 'README.md'], env, cwd=target).returncode == 0
    assert execute(['git', 'commit', '-qm', 'fixture'], env, cwd=target).returncode == 0
    task = tmp_path / 'task.txt'
    task.write_text('Retain an interactive native conversation; change no target files.\n')
    log = tmp_path / 'native-events.jsonl'
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    recorder = f'''#!{sys.executable}
import json, os, sys, subprocess, tty
with open({str(log)!r}, 'a') as f:
    f.write(json.dumps({{'argv':sys.argv[1:], 'launcher':os.path.basename(sys.argv[0]),
                        'stdin_tty':sys.stdin.isatty(),
                        'stdout_tty':sys.stdout.isatty(), 'pid':os.getpid(),
                        'profile':os.environ.get('CLAUDE_CONFIG_DIR'),
                        'home':os.environ.get('HOME'),
                        'api_key_present':bool(os.environ.get('ANTHROPIC_API_KEY'))}})+'\\n')
if sys.argv[1:] == ['--version']:
    print('2.1.268 (Claude Code)')
    raise SystemExit(0)
if sys.argv[1:] == ['--help']:
    print('--name --dangerously-skip-permissions --session-id --resume --settings '
          '--add-dir --model --effort --permission-mode --mcp-config')
    raise SystemExit(0)
if sys.argv[1:] == ['auth','status','--json']:
    print(json.dumps({{'loggedIn':True,'authMethod':'claude.ai','apiKeySource':None}}))
else:
    args = sys.argv[1:]
    session_flag = '--session-id' if '--session-id' in args else '--resume'
    session_id = args[args.index(session_flag) + 1] if session_flag in args else 'missing'
    settings = json.load(open(args[args.index('--settings') + 1])) if '--settings' in args else {{}}
    def native_event(kind, prompt=None):
        body = {{'hook_event_name':kind, 'session_id':session_id}}
        if prompt is not None:
            body['prompt'] = prompt
        for group in settings.get('hooks', {{}}).get(kind, []):
            for hook in group.get('hooks', []):
                subprocess.run(hook['command'], shell=True, input=json.dumps(body), text=True,
                               capture_output=True, timeout=5)
    if sys.stdin.isatty():
        tty.setraw(sys.stdin.fileno())
    native_event('SessionStart')
    print('NATIVE_READY', flush=True)
    pending = ''
    while True:
        chunk = os.read(sys.stdin.fileno(), 4096)
        if not chunk:
            break
        with open({str(log)!r}, 'a') as f:
            f.write(json.dumps({{'raw':chunk.decode(),'pid':os.getpid()}})+'\\n')
        pending += chunk.decode()
        while '\\r' in pending:
            if pending.startswith('\\x1b[200~'):
                end = pending.find('\\x1b[201~')
                enter = pending.find('\\r', end) if end >= 0 else -1
                if enter < 0:
                    break
                line = pending[6:end] + pending[end+6:enter]
                pending = pending[enter+1:]
            else:
                line, pending = pending.split('\\r', 1)
            quiet = os.path.exists({str(tmp_path / 'suppress-ack')!r})
            if not quiet:
                native_event('UserPromptSubmit', line)
            with open({str(log)!r}, 'a') as f:
                f.write(json.dumps({{'input':line,'pid':os.getpid()}})+'\\n')
            print('NATIVE_REPLY '+line, flush=True)
            if not quiet:
                native_event('Stop')
'''
    (bin_dir / 'cl').write_text(recorder)
    (bin_dir / 'cl').chmod(0o755)
    # A direct claude invocation is a different launcher and must be observable.
    (bin_dir / 'claude').write_text(recorder)
    (bin_dir / 'claude').chmod(0o755)
    # The recorder never invokes Kindex. Supply only the executable-discovery
    # dependency so this launch-boundary test needs no real MCP installation.
    kindex = bin_dir / 'kindex-lite'
    kindex.write_text(f'#!{sys.executable}\n' + """
import json, sys, os
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request:
        continue
    method = request.get('method')
    if method == 'initialize':
        result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'boundary-fixture', 'version': '0.48.1'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': name, 'inputSchema': {'type': 'object'}}
                            for name in ['kinbase_sync', 'kinbase_explain',
                                         'search', 'add', 'scope_info']]}
    elif method == 'tools/call' and request.get('params', {}).get('name') == 'scope_info':
        result = {'structuredContent': {'ok': True, 'kinbase_submissions_allowed': False,
                                       'repo': os.getcwd(),
                                       'data_dir': os.path.join(os.getcwd(), '.kin/local')}}
    else:
        raise SystemExit('Boundary fixture unsupported request: ' + json.dumps(request))
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)
""")
    kindex.chmod(0o755)
    kin = bin_dir / 'kin'
    kin.write_text('#!/bin/sh\ncase \"$1\" in --version) echo \"kin 0.48.1\";; esac\nexit 0\n')
    kin.chmod(0o755)
    env['FACTORY_KINDEX_LITE_BIN'] = str(kindex)
    env['PATH'] = str(bin_dir) + os.pathsep + env['PATH']
    runs = tmp_path / 'runs'
    base = [sys.executable, str(FACTORY / 'harness' / 'run_record.py')]
    common = ['--runs', str(runs), '--run', 'native-acceptance']
    result = execute(base + ['create'] + common + ['--repo', str(target),
                                                '--task-file', str(task)], env)
    assert result.returncode == 0, result.stderr
    case = dict(env=env, base=base, common=common, log=log, profile=profile,
                home=home, watched=watched, before=before,
                default_before=default_before, bin_dir=bin_dir, runs=runs,
                suppress_ack=tmp_path / 'suppress-ack')
    yield case
    status = execute(base + ['status'] + common, env)
    if status.returncode == 0:
        record = json.loads(status.stdout)
        if Path(record['repository']).resolve() == target.resolve():
            execute(['tmux', '-L', record['tmux_socket'], 'kill-server'], env)
    # Stop only test-owned process IDs if they remain; no machine-wide tmux kill.
    if log.exists():
        for pid in {item['pid'] for item in read_events(log) if 'pid' in item}:
            command = subprocess.run(['ps', '-ww', '-p', str(pid), '-o', 'command='],
                                     capture_output=True, text=True).stdout
            if not any(str(bin_dir / name) in command
                       for name in ('cl', 'native-recorder')):
                continue
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                pass


def read_events(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def start(case):
    result = execute(case['base'] + ['start'] + case['common'], case['env'])
    assert result.returncode == 0, result.stdout + result.stderr


def launch(case):
    start(case)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        events = [e for e in read_events(case['log']) if 'argv' in e
                  and e['argv'] != ['auth', 'status', '--json']]
        if len(events) >= 2:
            return events
        time.sleep(0.05)
    pytest.fail('Factory reported start but no selected native launcher was observed')


@pytest.mark.parametrize('launcher_kind', ['executable', 'shell-function'])
def test_coordinator_uses_selected_interactive_native_launcher(native_case, launcher_kind):
    if launcher_kind == 'shell-function':
        executable = native_case['bin_dir'] / 'cl'
        hidden = native_case['bin_dir'] / 'native-recorder'
        executable.rename(hidden)
        # This is a synthetic test profile, never Jeremy's real profile.
        (native_case['home'] / '.bash_profile').write_text(
            'cl() { exec ' + shlex.quote(str(hidden)) + ' "$@"; }\n')
        native_case['env']['SHELL'] = '/bin/bash'
    events = launch(native_case)
    for event in events:
        expected = 'cl' if launcher_kind == 'executable' else 'native-recorder'
        assert event['launcher'] == expected, event
        assert event['stdin_tty'] and event['stdout_tty'], event
        assert not event['api_key_present'], event
        assert event['home'] == str(native_case['home'])
        assert event['profile'] == str(native_case['profile'])
        forbidden = {'-p', '--print', '--strict-mcp-config', '--setting-sources',
                     '--permission-mode', '--dangerously-skip-permissions'}
        assert not (forbidden & set(event['argv'])), event


def test_launch_does_not_rewrite_native_profile_or_settings(native_case):
    launch(native_case)
    assert {str(p): manifest(p) for p in native_case['watched']} == native_case['before']
    assert (native_case['home'] / '.claude.json').read_bytes() == native_case['default_before']


def status(case, slot='validator'):
    result = execute([sys.executable, str(FACTORY / 'harness' / 'persistent_lane.py'),
                      'status', '--root', str(case['runs'] / 'native-acceptance'),
                      '--slot', slot], case['env'])
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def wait_for(predicate):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    pytest.fail('Expected observable native-session event did not arrive')


def test_native_conversation_and_restart_keep_identity(native_case, tmp_path):
    launch(native_case)
    before = wait_for(lambda: (s if (s := status(native_case)).get('phase')
                              == 'stop-reported' else None))
    session = before['session_id']
    pid = before['worker']['pid']
    message = tmp_path / 'message.txt'
    message.write_text('Acceptance continuation marker')
    result = execute(native_case['base'] + ['message'] + native_case['common']
                     + ['--to', 'validator', '--message-file', str(message)], native_case['env'])
    assert result.returncode in (0, 75), result.stdout + result.stderr
    wait_for(lambda: any('Acceptance continuation marker' in e.get('input', '')
                        and e.get('pid') == pid for e in read_events(native_case['log'])))
    after = status(native_case)
    assert after['session_id'] == session
    assert after['worker']['pid'] == pid
    assert after['transport'] == 'native-terminal'
    prior_ids = set(after['deliveries']) - set(before['deliveries'])
    assert len(prior_ids) == 1
    message_id = prior_ids.pop()
    # Terminate only the verified fixture process, then use Factory's public restart.
    command = subprocess.run(['ps', '-ww', '-p', str(pid), '-o', 'command='],
                             capture_output=True, text=True).stdout
    assert str(native_case['bin_dir'] / 'cl') in command
    os.kill(pid, 15)
    def dead():
        result = execute([sys.executable, str(FACTORY / 'harness' / 'persistent_lane.py'),
                          'status', '--root', str(native_case['runs'] / 'native-acceptance'),
                          '--slot', 'validator'], native_case['env'])
        return result.returncode == 70 or json.loads(result.stdout).get('phase') == 'exited'
    wait_for(dead)
    run_status = execute(native_case['base'] + ['status'] + native_case['common'],
                         native_case['env'])
    run = json.loads(run_status.stdout)
    assert Path(run['repository']).parent == native_case['runs'].parent
    execute(['tmux', '-L', run['tmux_socket'], 'kill-server'], native_case['env'])
    start(native_case)
    resumed = wait_for(lambda: next((e for e in read_events(native_case['log'])
                                   if '--resume' in e.get('argv', [])
                                   and session in e['argv']), None))
    assert resumed['stdin_tty'] and resumed['stdout_tty']
    assert not resumed['api_key_present']
    assert '-p' not in resumed['argv'] and '--print' not in resumed['argv']
    repeat = execute([sys.executable, str(FACTORY / 'harness' / 'persistent_lane.py'),
                      'deliver', '--root', str(native_case['runs'] / 'native-acceptance'),
                      '--slot', 'validator', '--message-id', message_id,
                      '--thread', session], native_case['env'])
    assert repeat.returncode in (0, 70, 75), repeat.stdout + repeat.stderr
    # Native receipt is not model success; an uncertain old delivery must stay retained.
    retained = status(native_case)['deliveries'][message_id]
    assert retained['state'] in ('unknown', 'observed')
    assert len([e for e in read_events(native_case['log'])
                if 'Acceptance continuation marker' in e.get('input', '')]) == 1


def test_submission_without_native_ack_is_not_completion(native_case, tmp_path):
    launch(native_case)
    before = wait_for(lambda: (s if (s := status(native_case)).get('phase')
                              == 'stop-reported' else None))
    native_case['suppress_ack'].touch()
    message = tmp_path / 'unacknowledged.txt'
    marker = 'Acceptance unacknowledged marker'
    message.write_text(marker)
    result = execute(native_case['base'] + ['message'] + native_case['common']
                     + ['--to', 'validator', '--message-file', str(message)], native_case['env'])
    assert result.returncode in (0, 75), result.stdout + result.stderr
    wait_for(lambda: any(marker in e.get('input', '') for e in read_events(native_case['log'])))
    after = status(native_case)
    deliveries = {k: v for k, v in after['deliveries'].items() if k not in before['deliveries']}
    assert len(deliveries) == 1, after
    message_id, delivery = next(iter(deliveries.items()))
    assert delivery['state'] == 'submitted'
    assert after['stop_reports'] == before['stop_reports']
    repeat = execute([sys.executable, str(FACTORY / 'harness' / 'persistent_lane.py'),
                      'deliver', '--root', str(native_case['runs'] / 'native-acceptance'),
                      '--slot', 'validator', '--message-id', message_id,
                      '--thread', before['session_id']], native_case['env'])
    assert repeat.returncode == 75, repeat.stdout + repeat.stderr
    assert json.loads(repeat.stdout)['state'] == 'submitted'
    assert status(native_case)['stop_reports'] == before['stop_reports']
    received = [e for e in read_events(native_case['log']) if marker in e.get('input', '')]
    assert len(received) == 1


def test_install_reinstall_preserves_user_files(native_case, tmp_path):
    commands = native_case['profile'] / 'commands'
    commands.mkdir()
    custom = commands / 'validate.md'
    custom.write_text('My own customized command; keep it byte-for-byte.\n')
    before = manifest(native_case['home'])
    bindir = tmp_path / 'installed-bin'
    env = dict(native_case['env'])
    env['PATH'] = str(bindir) + os.pathsep + env['PATH']
    argv = [sys.executable, str(FACTORY / 'scripts' / 'install.py'), 'install',
            '--root', str(FACTORY), '--venv', str(Path(sys.executable).parent.parent),
            '--bindir', str(bindir), '--commands-dir', str(commands)]
    result = execute(argv, env)
    assert result.returncode == 0, result.stdout + result.stderr
    installed = manifest(native_case['home'])
    assert all(installed.get(path) == digest for path, digest in before.items())
    extra = set(installed) - set(before)
    assert extra
    assert all(Path(path).parent == commands.relative_to(native_case['home']) for path in extra)
    assert (bindir / 'factory').is_file()
    result = execute(argv, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert manifest(native_case['home']) == installed
    assert execute([str(bindir / 'factory'), '--help'], env).returncode == 0


def test_author_launch_respects_native_launcher(native_case, tmp_path):
    native_case['env'].pop('ANTHROPIC_API_KEY', None)
    launch(native_case)
    lane = tmp_path / 'author-repository'
    prepared = execute([sys.executable, str(FACTORY / 'harness' / 'role_inputs.py'),
                        'prepare', '--runs', str(native_case['runs']), '--run', 'native-acceptance',
                        '--role', 'coder', '--repo', str(lane)], native_case['env'])
    assert prepared.returncode == 0, prepared.stderr
    lane = Path(json.loads(prepared.stdout)['repository'])
    brief = tmp_path / 'author-brief.txt'
    brief.write_text('Observe native behavior only; change no target files.\n')
    before = len(read_events(native_case['log']))
    launched = execute(['bash', str(FACTORY / 'harness' / 'tmux_lane.sh'),
                        'native-acceptance', 'coder', 'launch', '--runs', str(native_case['runs']),
                        '--repo', str(lane), '--prompt', str(brief), '--agent', 'claude'],
                       native_case['env'])
    assert launched.returncode == 0, launched.stdout + launched.stderr
    events = wait_for(lambda: [e for e in read_events(native_case['log'])[before:]
                              if '--session-id' in e.get('argv', [])])
    for event in events:
        assert event['launcher'] == 'cl'
        assert event['stdin_tty'] and event['stdout_tty']
        assert not event['api_key_present']
        assert event['home'] == str(native_case['home'])
        assert event['profile'] == str(native_case['profile'])
        forbidden = {'-p', '--print', '--strict-mcp-config', '--setting-sources',
                     '--permission-mode', '--dangerously-skip-permissions'}
        assert not forbidden & set(event['argv'])
    # A preflight must examine the same selected launcher/account as execution.
    probes = [e for e in read_events(native_case['log'])[before:] if 'argv' in e
              and e['argv'] == ['auth', 'status', '--json']]
    assert all(e['launcher'] == 'cl' for e in probes), probes


def test_hook_observations_do_not_claim_model_completion(native_case):
    # This boundary double reports events but runs no model. Parallel user hooks
    # may reject a prompt or continue after Stop; neither is an outcome receipt.
    launch(native_case)
    observed = wait_for(lambda: (s if (s := status(native_case)).get('stop_reports', 0)
                                else None))
    assert observed['phase'] == 'stop-reported'
    assert 'turns' not in observed
    assert 'completed_work_s' not in observed
    assert all(d['state'] not in ('completed', 'succeeded', 'acknowledged')
               for d in observed['deliveries'].values())


@pytest.mark.parametrize('corruption', ['invalid-json', 'invalid-deliveries'])
def test_corrupt_saved_state_refuses_restart_without_retyping(native_case, corruption):
    launch(native_case)
    running = wait_for(lambda: (s if (s := status(native_case)).get('stop_reports', 0)
                               else None))
    run_result = execute(native_case['base'] + ['status'] + native_case['common'],
                         native_case['env'])
    run = json.loads(run_result.stdout)
    assert Path(run['repository']).parent == native_case['runs'].parent
    assert execute(['tmux', '-L', run['tmux_socket'], 'kill-server'],
                   native_case['env']).returncode == 0
    for identity in ('worker', 'wrapper'):
        pid = running[identity]['pid']
        wait_for(lambda pid=pid: subprocess.run(['ps', '-p', str(pid), '-o', 'pid='],
                                       capture_output=True).returncode != 0)
    root = native_case['runs'] / 'native-acceptance'
    # Test-owned public state artifacts only. Corrupt both coordinator receipts.
    for slot in ('validator', 'orchestrator'):
        receipt = root / f'{slot}-persistent.json'
        if corruption == 'invalid-json':
            receipt.write_text('{broken receipt')
        else:
            saved = json.loads(receipt.read_text())
            saved['deliveries'] = ['not-a-receipt-map']
            receipt.write_text(json.dumps(saved))
    launches_before = len([e for e in read_events(native_case['log']) if 'argv' in e])
    result = execute(native_case['base'] + ['start'] + native_case['common'], native_case['env'])
    # Starting a tmux wrapper may return before the child checks state. Query the
    # public lane status as a synchronous barrier; never infer success from start.
    checked = execute([sys.executable, str(FACTORY / 'harness' / 'persistent_lane.py'),
                       'status', '--root', str(root), '--slot', 'validator'], native_case['env'])
    assert checked.returncode != 0, checked.stdout
    assert result.returncode != 0 or checked.returncode != 0
    assert len([e for e in read_events(native_case['log']) if 'argv' in e]) == launches_before


def test_user_launcher_permission_choice_is_preserved(native_case):
    executable = native_case['bin_dir'] / 'cl'
    recorder = native_case['bin_dir'] / 'native-recorder'
    executable.rename(recorder)
    (native_case['home'] / '.bash_profile').write_text(
        'cl() { exec ' + shlex.quote(str(recorder))
        + ' --dangerously-skip-permissions "$@"; }\n')
    native_case['env']['SHELL'] = '/bin/bash'
    for event in launch(native_case):
        assert event['launcher'] == 'native-recorder'
        assert event['argv'].count('--dangerously-skip-permissions') == 1
        assert '--permission-mode' not in event['argv']
        assert not event['api_key_present']


def test_explicit_sandbox_off_selects_native_permission_flag(native_case):
    native_case['env']['FACTORY_LANE_SANDBOX'] = 'off'
    for event in launch(native_case):
        assert event['argv'].count('--dangerously-skip-permissions') == 1
        assert '--permission-mode' not in event['argv']


def test_explicit_api_key_env_file_refuses_native_session(native_case, tmp_path):
    launch(native_case)
    running = wait_for(lambda: (s if (s := status(native_case)).get('stop_reports') else None))
    run = json.loads(execute(native_case['base'] + ['status'] + native_case['common'],
                             native_case['env']).stdout)
    execute(['tmux', '-L', run['tmux_socket'], 'kill-server'], native_case['env'])
    for identity in ('worker', 'wrapper'):
        pid = running[identity]['pid']
        wait_for(lambda pid=pid: subprocess.run(['ps', '-p', str(pid), '-o', 'pid='],
                                               capture_output=True).returncode != 0)
    prior_launches = len([e for e in read_events(native_case['log']) if 'argv' in e])
    env_file = tmp_path / 'explicit.env'
    env_file.write_text('ANTHROPIC_API_KEY=synthetic-explicit-forbidden-key\n')
    env_file.chmod(0o600)
    prompt = tmp_path / 'explicit-prompt.txt'
    prompt.write_text('Synthetic harmless prompt')
    env = dict(native_case['env'])
    env.pop('ANTHROPIC_API_KEY', None)
    result = execute([sys.executable, str(FACTORY / 'harness' / 'lane_agent.py'), 'run',
                      '--slot', 'validator', '--log', str(tmp_path / 'lane.log'),
                      '--exits', str(tmp_path / 'exits.jsonl'), '--env-file', str(env_file),
                      '--session-agent', 'claude', '--session-file',
                      str(native_case['runs'] / 'native-acceptance' / 'validator-session-id'),
                      '--prompt-arg', str(prompt),
                      '--plan-only', '--persistent', '--run-root',
                      str(native_case['runs'] / 'native-acceptance'), '--',
                      str(native_case['bin_dir'] / 'cl'), '--resume', running['session_id']], env)
    assert result.returncode != 0, result.stdout + result.stderr
    log = tmp_path / 'lane.log'
    diagnostic = result.stderr + (log.read_text() if log.exists() else '')
    assert 'ANTHROPIC_API_KEY' in diagnostic, diagnostic
    assert len([e for e in read_events(native_case['log']) if 'argv' in e]) == prior_launches
    assert 'synthetic-explicit-forbidden-key' not in result.stdout + result.stderr
