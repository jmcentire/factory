"""Disposable author processes exercise actual broker identity and durable holds."""
from __future__ import annotations

import hashlib
import json
import pathlib
import platform
import shlex
import subprocess
import sys
import tempfile
import time

import pytest

from harness import author_custody as author
from harness import lane_custody, policy_custody, signet_hooks
from harness.validator_boundary import Boundary
from tests.test_orchestrator_channel import resident_root

HOME = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def prepared(tmp_path):
    with tempfile.TemporaryDirectory(prefix="fac-author-", dir="/private/tmp") as short:
        control = tmp_path / "control"
        control.mkdir()
        repo = tmp_path / "author"
        repo.mkdir()
        peer = tmp_path / "peer"
        peer.mkdir()
        root = resident_root(tmp_path)
        evaluator = control / "synthetic-evaluator"
        evaluator.write_text(f"#!{sys.executable}\n" + '''
import json,sys
if sys.argv[1:]==['integration','describe']:
 print(json.dumps({'owner':'signet-eval','protocol_version':1,'active':True,
                   'disabled':False,'policy_revision':'fixture-policy'}))
else:
 request=json.load(sys.stdin)
 denied=request['tool_input'].get('command')=='synthetic-denial'
 print(json.dumps({'hookSpecificOutput':{'hookEventName':'PreToolUse',
    'permissionDecision':'deny' if denied else 'allow',
    'permissionDecisionReason':'synthetic decision'}}))
''')
        evaluator.chmod(0o700)

        def create(role="coder", client="codex"):
            guard = {"schema": signet_hooks.SCHEMA, "evaluator": str(evaluator),
                "evaluator_sha256": hashlib.sha256(evaluator.read_bytes()).hexdigest(),
                "owner_home": str(tmp_path), "owner_user": "fixture-owner",
                "project": str(repo), "session": f"fixture-{role}"}
            guard_path = control / f"{role}-signet.json"
            guard_path.write_text(json.dumps(guard))
            boundary = Boundary.parse({"schema": "factory-seat-boundary/1", "role": role,
                "denied_reads": [str(peer)], "protected_writes": [str(control), short,
                    *(str(HOME/name) for name in ("harness", "factory_core", "factory_runtime"))],
                "allowed_sockets": [str(pathlib.Path(short) / f"{role}.sock")]})
            boundary_path = control / f"{role}-boundary.json"
            boundary_path.write_text(json.dumps(boundary.document()))
            record = {"schema": "factory-lane-boundary/1", "role": role,
                      "contract": str(boundary_path), "digest": boundary.digest}
            config = {"schema": author.SCHEMA, "role": role, "client": client,
                "factory_home": str(HOME), "python": sys.executable, "repository": str(repo),
                "boundary": record, "state": str(control / f"{role}-state"),
                "socket": str(pathlib.Path(short) / f"{role}.sock"),
                "sources": {name:hashlib.sha256((HOME/name).read_bytes()).hexdigest()
                            for name in author.SOURCES},
                "policy": {"client": client, "contract": str(guard_path),
                    "contract_digest": signet_hooks.digest(guard), "root": str(root),
                    "prerequisite": None,
                    "sources": {name: hashlib.sha256((HOME/name).read_bytes()).hexdigest()
                        for name in ('harness/policy_custody.py', 'harness/signet_hooks.py')}}}
            path = control / f"{role}-custody.json"
            path.write_text(json.dumps(config))
            return path, lane_custody._digest(config), config, boundary
        yield create


@pytest.mark.skipif(platform.system() != "Darwin", reason="installed kernel author boundary")
@pytest.mark.parametrize(("role", "client"), [("coder", "codex"), ("tester", "cursor-agent")])
def test_actual_author_identity_persistent_denial_and_relaunch_refusal(prepared, role, client):
    path, digest, config, boundary = prepared(role, client)
    settings = signet_hooks.configuration(client, pathlib.Path(config['policy']['contract']),
        config['policy']['contract_digest'], pathlib.Path(sys.executable),
        author_config=str(path), author_digest=digest)
    handler = (settings['hooks']['PreToolUse'][0]['hooks'][0] if client == 'codex'
               else settings['hooks']['beforeShellExecution'][0])
    command = shlex.split(handler['command'])
    script = '''import json,subprocess,sys
command=json.loads(sys.argv[1]);client=sys.argv[2]
for text in ['synthetic-denial','different-permitted-operation']:
 payload=({'hook_event_name':'PreToolUse','tool_name':'Bash','tool_input':{'command':text}}
          if client=='codex' else {'hook_event_name':'beforeShellExecution','command':text})
 result=subprocess.run(command,input=json.dumps(payload),text=True,capture_output=True,check=True)
 value=json.loads(result.stdout)
 decision=value.get('permission',value.get('hookSpecificOutput',{}).get('permissionDecision'))
 assert decision=='deny',value
print('held')
'''
    child = boundary.invocation([sys.executable, '-c', script, json.dumps(command), client],
                                cwd=pathlib.Path(config['repository']))
    assert author.run(path, digest, child) == 0
    receipts = pathlib.Path(config['state']) / 'receipts'
    assert len(policy_custody.holds(receipts)) == 1
    assert len(list(receipts.glob('*.started.json'))) == 1
    assert not pathlib.Path(config['socket']).exists()
    with pytest.raises(ValueError, match='hold'):
        author.run(path, digest, [sys.executable, '-c', 'raise AssertionError("must not run")'])
    record = author.read_binding(config, digest)
    assert record.document['author_role'] == role
    assert record.document['operations'] == {}
    assert record.document['policy']['client'] == client


@pytest.mark.skipif(platform.system() != "Darwin", reason="boundary admission")
@pytest.mark.parametrize('change', ['client', 'role', 'boundary', 'source', 'digest'])
def test_missing_wrong_or_changed_author_binding_refuses(prepared, change):
    path, digest, config, _ = prepared()
    if change == 'client':
        config['client'] = 'cursor-agent'
    elif change == 'role':
        config['role'] = 'validator'
    elif change == 'boundary':
        config['boundary'] = None
    elif change == 'source':
        config['sources']['harness/author_custody.py'] = '0'*64
    else:
        digest = '0'*64
    if change != 'digest':
        path.write_text(json.dumps(config))
        digest = lane_custody._digest(config)
    with pytest.raises(ValueError):
        author.configuration(path, digest)


def test_missing_required_config_never_means_direct_evaluation(tmp_path):
    with pytest.raises(ValueError, match='missing'):
        author.select({'FACTORY_REQUIRE_AUTHOR_CUSTODY': '1'}, 'coder', 'codex', tmp_path, None)


@pytest.mark.skipif(platform.system() != "Darwin", reason="boundary admission")
def test_author_plan_has_no_execution_operations_and_rejects_validator_role(prepared):
    path, digest, config, _ = prepared()
    plan = {'schema': lane_custody.SCHEMA, 'seat': {'pid': 999999,'started':'fixture'},
            'factory_home':str(HOME),'python':sys.executable,'cwd':str(HOME),
            'operations':{},'author_role':'coder','policy':config['policy']}
    lane_custody.Plan.parse(plan)
    for update in ({'author_role':'validator'}, {'operations':{'execute':{}}}):
        with pytest.raises(ValueError):
            lane_custody.Plan.parse({**plan, **update})
    with pytest.raises(ValueError, match='combined'):
        signet_hooks.configuration('codex', pathlib.Path(config['policy']['contract']),
            config['policy']['contract_digest'], pathlib.Path(sys.executable),
            author_config=str(path), author_digest=digest, custody_socket='/tmp/validator.sock')


@pytest.mark.skipif(platform.system() != "Darwin", reason="kernel custody admission")
def test_failed_broker_start_never_executes_author(prepared, monkeypatch):
    path, digest, config, _ = prepared()
    marker = pathlib.Path(config['repository']) / 'must-not-run'
    original = author.subprocess.Popen

    def spawn(command, *args, **kwargs):
        if 'serve' in command:
            command = [sys.executable, '-c', 'raise SystemExit(9)']
        return original(command, *args, **kwargs)

    monkeypatch.setattr(author.subprocess, 'Popen', spawn)
    with pytest.raises(ValueError):
        author.run(path, digest, [sys.executable, '-c',
            'import pathlib,sys; pathlib.Path(sys.argv[1]).touch()', str(marker)])
    assert not marker.exists()
    exits = list(pathlib.Path(config['state']).glob('exit-*.json'))
    assert exits and json.loads(exits[0].read_text())['stop_verified']


@pytest.mark.skipif(platform.system() != "Darwin", reason="actual process binding")
def test_foreign_process_cannot_use_author_broker_and_observed_orphan_stops(prepared):
    path, digest, config, _ = prepared()
    marker = pathlib.Path(config['repository']) / 'child-id'
    command = [sys.executable, '-c', """import subprocess,sys,time,pathlib
p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
pathlib.Path(sys.argv[1]).write_text(str(p.pid))
time.sleep(1)
""", str(marker)]
    wrapper = subprocess.Popen([sys.executable, str(HOME/'harness/author_custody.py'),
        '--config', str(path), '--digest', digest, '--', *command])
    try:
        deadline = time.monotonic()+8
        while not marker.exists() and wrapper.poll() is None and time.monotonic()<deadline:
            time.sleep(.02)
        assert marker.exists()
        payload={'hook_event_name':'PreToolUse','tool_name':'Bash','tool_input':{'command':'ok'}}
        with pytest.raises(ValueError, match='refused'):
            author.evaluate(path, digest, 'codex', payload,
                pathlib.Path(config['policy']['contract']), config['policy']['contract_digest'])
        assert wrapper.wait(timeout=10)==0
        assert not list((pathlib.Path(config['state'])/'receipts').glob('*.started.json'))
        end=json.loads(next(pathlib.Path(config['state']).glob('exit-*.json')).read_text())
        assert end['stop_verified'] and not end['survivors']
        assert int(marker.read_text()) in [i['pid'] for i in end['observed_identities']]
    finally:
        if wrapper.poll() is None:
            wrapper.terminate()
            wrapper.wait(timeout=8)


@pytest.mark.skipif(platform.system() != "Darwin", reason="kernel admission")
def test_resume_uses_recorded_config_and_refuses_prior_unknown_exit(prepared, monkeypatch):
    path,digest,config,_=prepared()
    root=pathlib.Path(config['policy']['root'])
    journal=root/'tmux-lanes/coder-launch.jsonl'
    journal.parent.mkdir()
    journal.write_text(json.dumps({'status':'active','author_custody':
        {'config':str(path),'digest':digest}})+'\n')
    monkeypatch.setenv('FACTORY_AUTHOR_CUSTODY_CONFIG','/ambient-replacement')
    value=author.retained(root,'coder','coder','codex',
                         pathlib.Path(config['repository']),config['boundary'])
    assert value=={'config':str(path),'digest':digest}
    state=pathlib.Path(config['state'])
    state.mkdir(mode=0o700)
    lane_custody._save_once(state/'owner.json',{'role':'coder','client':'codex',
        'repository':config['repository'],'root':config['policy']['root']})
    lane_custody._save_once(state/'current.json',{'token':'a'*32})
    with pytest.raises((ValueError,OSError)):
        author.run(path,digest,[sys.executable,'-c','raise AssertionError("must not execute")'])
    lane_custody._save_once(state/('exit-'+'a'*32+'.json'),{'stop_verified':False})
    with pytest.raises(ValueError,match='unverified'):
        author.run(path,digest,[sys.executable,'-c','raise AssertionError("must not execute")'])


def test_reused_process_identity_is_never_signalled(monkeypatch):
    original={'pid':54321,'started':'original'}
    table={54321:{'pid':54321,'started':'replacement','ppid':1,'state':'S'}}
    monkeypatch.setattr(author.lane_watchdog,'process_snapshot',lambda:table)
    signals=[]
    def send(identity,number):
        return author.lane_watchdog.signal_identity_original(
            identity,number,processes=lambda:table,send=lambda *x:signals.append(x))
    monkeypatch.setattr(author.lane_watchdog,'signal_identity_original',
                        author.lane_watchdog.signal_identity,raising=False)
    monkeypatch.setattr(author.lane_watchdog,'signal_identity',send)
    assert author.finish_processes([original],[])['stop_verified']
    assert not signals


@pytest.mark.skipif(platform.system() != "Darwin", reason="kernel admission")
def test_registry_guard_configuration_binds_author_without_inheriting_environment(prepared):
    from harness import agents
    path,digest,config,_=prepared('tester','cursor-agent')
    base=path.parent/'client'
    setup=agents.prepare_lane('cursor-agent',billing='plan',home=base,model='fixture',
        kindex_record={'mcp_server':{'command':'kindex-lite','args':['--repo',config['repository']]}},
        env_file=base/'environment',environ={'HOME':str(path.parent),
            'FACTORY_SIGNET_CONTRACT':config['policy']['contract'],
            'FACTORY_SIGNET_DIGEST':config['policy']['contract_digest'],
            'FACTORY_AUTHOR_CUSTODY_CONFIG':str(path),'FACTORY_AUTHOR_CUSTODY_DIGEST':digest})
    setting=json.loads((pathlib.Path(setup.env['CURSOR_CONFIG_DIR'])/'hooks.json').read_text())
    for event in ('preToolUse','beforeShellExecution','beforeMCPExecution'):
        hook=setting['hooks'][event][0]
        assert hook['failClosed'] and '--author-config' in hook['command']
        assert str(path) in hook['command'] and digest in hook['command']
    assert 'FACTORY_AUTHOR_CUSTODY_CONFIG' not in (base/'environment').read_text()


def test_missing_custody_and_conflicting_fixed_pin_refuse_canonical_launch(tmp_path):
    from tests.test_harness_scripts import _ignite_and_launcher
    _,_,tmux_log,launch=_ignite_and_launcher(tmp_path,'Require author custody.',{
        'FACTORY_REQUIRE_AUTHOR_CUSTODY':'1'})
    result=launch('coder')
    assert result.returncode==77 and 'author custody' in result.stderr
    assert not any('-n coder' in row for row in tmux_log.read_text().splitlines())


@pytest.mark.skipif(platform.system() != "Darwin", reason="kernel custody admission")
def test_evaluator_failure_persists_uncertain_hold(prepared, monkeypatch):
    path,digest,config,_=prepared()
    plan=lane_custody.Plan.parse({'schema':lane_custody.SCHEMA,
        'seat':{'pid':54321,'started':'fixture'},'factory_home':str(HOME),
        'python':sys.executable,'cwd':str(HOME),'operations':{},'author_role':'coder',
        'policy':config['policy']})
    receipts=path.parent/'uncertain-receipts'
    receipts.mkdir()
    def fail(*args):
        raise ValueError('synthetic health failure')
    monkeypatch.setattr(policy_custody.signet_hooks,'evaluate_with_health',fail)
    from tests.test_policy_custody import tool_request
    with pytest.raises(ValueError):
        policy_custody.evaluate(plan,tool_request(plan),receipts)
    assert policy_custody.holds(receipts)[0]['outcome']=='uncertain'
    denied=policy_custody.evaluate(plan,tool_request(plan,'changed'),receipts)
    assert denied['output']['hookSpecificOutput']['permissionDecision']=='deny'


@pytest.mark.skipif(platform.system() != "Darwin", reason="kernel binding validation")
def test_cross_run_and_cross_role_state_are_rejected(prepared):
    path,digest,config,_=prepared()
    env={'FACTORY_AUTHOR_CUSTODY_CONFIG':str(path),'FACTORY_AUTHOR_CUSTODY_DIGEST':digest}
    with pytest.raises(ValueError,match='does not match'):
        author.select(env,'coder','codex',pathlib.Path(config['repository']),
                      config['boundary'],path.parent/'different-run')
    state=pathlib.Path(config['state'])
    state.mkdir(mode=0o700)
    lane_custody._save_once(state/'owner.json',{'role':'validator','client':'claude',
        'repository':config['repository'],'root':config['policy']['root']})
    with pytest.raises(ValueError,match='different role'):
        author.run(path,digest,[sys.executable,'-c','raise AssertionError("never execute")'])


@pytest.mark.skipif(platform.system() != "Darwin", reason="kernel binding validation")
def test_author_release_checks_its_role_and_preserves_denial(prepared, monkeypatch):
    from tests.test_policy_custody import accept, evaluator, tool_request
    path,digest,config,_=prepared('tester','cursor-agent')
    # Use canonical Cursor preToolUse input, not another client's payload.
    plan=lane_custody.Plan.parse({'schema':lane_custody.SCHEMA,
        'seat':{'pid':54321,'started':'fixture'},'factory_home':str(HOME),
        'python':sys.executable,'cwd':str(HOME),'operations':{},'author_role':'tester',
        'policy':config['policy']})
    receipts=path.parent/'release-receipts'
    receipts.mkdir()
    evaluator(monkeypatch)
    request=tool_request(plan)
    request['payload']['hook_event_name']='preToolUse'
    policy_custody.evaluate(plan,request,receipts)
    notice=json.loads((receipts/'policy-first.notice.json').read_text())
    root=pathlib.Path(config['policy']['root'])
    accept(root,notice['cursor'])
    report=policy_custody.orchestrator_channel._read_jsonl(root/'orchestrator/reports.jsonl')[-1]
    roles=[]
    monkeypatch.setattr(policy_custody,'check_lane_admission',lambda r,role:roles.append(role))
    policy_custody.release(plan,receipts,'policy-first',report['assessment_digest'])
    assert roles==['tester'] and not policy_custody.holds(receipts)
    assert (receipts/'policy-first.terminal.json').exists()


@pytest.mark.skipif(platform.system() != "Darwin", reason="canonical kernel launch binding")
def test_canonical_launch_and_resume_retain_author_broker(prepared, tmp_path, monkeypatch):
    from tests import test_harness_scripts as scripts
    from tests.test_harness_scripts import HARNESS, _ignite_and_launcher, run
    environment = {}
    original = scripts.factory_ignition_env
    def capture(*args, **kwargs):
        env, log = original(*args, **kwargs)
        environment.update(env)
        return env, log
    monkeypatch.setattr(scripts, 'factory_ignition_env', capture)
    setup=tmp_path/'canonical'
    setup.mkdir()
    root,operator,tmux_log,launch=_ignite_and_launcher(setup,'Keep per-author custody.')
    path,_,config,_=prepared()
    repository=setup/'standalone-coder'
    repository.mkdir()
    config['repository']=str(repository)
    config['policy']['root']=str(root)
    guard_path=pathlib.Path(config['policy']['contract'])
    guard=json.loads(guard_path.read_text())
    guard['project']=str(repository)
    guard_path.write_text(json.dumps(guard))
    config['policy']['contract_digest']=signet_hooks.digest(guard)
    path.write_text(json.dumps(config))
    digest=lane_custody._digest(config)
    # A CLI fixture supports only read-only hooks/list; no provider/model is invoked.
    # Locate the existing fixture binary by its known setup helper's PATH.
    env=environment
    stub=pathlib.Path(env['PATH'].split(':')[0])/'codex'
    stub.write_text(f'#!{sys.executable}\n'+'''import json,os,sys,tomllib
if '--version' in sys.argv: print('fixture');raise SystemExit
if 'login' in sys.argv and 'status' in sys.argv: raise SystemExit(0)
if '--help' in sys.argv:
 print('--ignore-user-config --ignore-rules --strict-config --json --thread '
       '--no-alt-screen --add-dir --model')
 raise SystemExit(0)
if 'app-server' not in sys.argv: raise SystemExit('no model fixture permitted')
override=next(x for x in sys.argv if x.startswith('hooks.PreToolUse='))
handler=tomllib.loads(override)['hooks']['PreToolUse'][0]['hooks'][0]
for line in sys.stdin:
 request=json.loads(line)
 if request['method']=='initialize': result={}
 elif request['method']=='hooks/list':
  result={'data':[{'cwd':os.getcwd(),'errors':[],'hooks':[{'eventName':'preToolUse',
   'command':handler['command'],'source':'sessionFlags','enabled':True,
   'trustStatus':'trusted','timeoutSec':25,'currentHash':'sha256:'+'a'*64}]}]}
 else: continue
 print(json.dumps({'id':request['id'],'result':result}),flush=True)
''')
    stub.chmod(0o700)
    boundary=config['boundary']
    result=launch('coder','--boundary-contract',boundary['contract'],
        '--boundary-digest',boundary['digest'],'--signet-contract',str(guard_path),
        '--signet-digest',config['policy']['contract_digest'],
        '--author-custody-contract',str(path),'--author-custody-digest',digest)
    assert result.returncode==0,result.stdout+result.stderr
    journal=root/'tmux-lanes/coder-launch.jsonl'
    rows=[json.loads(line) for line in journal.read_text().splitlines()]
    assert rows[-1]['author_custody']=={'config':str(path),'digest':digest}
    command=next(row for row in tmux_log.read_text().splitlines() if '-n coder' in row)
    assert 'author_custody.py' in command and '--shell-command' in command
    assert '--author-config' in command and digest in command
    (root/'tmux-lanes/coder-thread-id').write_text('12345678-1234-4234-8234-123456789abc')
    env.update(FACTORY_AUTHOR_CUSTODY_CONFIG='/ignored-ambient',
               FACTORY_AUTHOR_CUSTODY_DIGEST='0'*64)
    result=run(['bash',str(HARNESS/'tmux_lane_message.sh'),'r1','validator','coder','status',
                '--runs',str(root.parent)],operator,env)
    assert result.returncode==0,result.stdout+result.stderr
    resumed=next(row for row in tmux_log.read_text().splitlines() if 'respawn-pane' in row)
    assert 'author_custody.py' in resumed and digest in resumed
    assert '/ignored-ambient' not in resumed
