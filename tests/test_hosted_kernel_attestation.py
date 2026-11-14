"""Independent acceptance from published hosted kernel public interface.

Only synthetic ACP servers; no model, account, provider configuration or source.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from harness.validator_boundary import Boundary

H = Path(__file__).resolve().parents[1] / 'harness'


def call(args, env=None):
    clean = {k: v for k, v in os.environ.items() if not k.startswith('FACTORY_')}
    clean.update(env or {})
    return subprocess.run([sys.executable, str(H / 'protocol_host.py'), *args],
                          env=clean, capture_output=True, text=True, timeout=30)


@pytest.fixture
def kernel_probe():
    with tempfile.TemporaryDirectory(prefix='f-kp-', dir='/tmp') as temp:
        root = Path(temp).resolve()
        control = root / 'control'
        control.mkdir()
        workspace = root / 'workspace'
        workspace.mkdir()
        events = workspace / 'events.jsonl'
        server = workspace / 'server'
        # System interpreter is separate from Factory's protected virtualenv.
        server.write_text('''#!/usr/bin/python3
import json,os,sys
from pathlib import Path
log=Path(__file__).parent/'events.jsonl'
def record(x):
    with log.open('a') as f:f.write(json.dumps(x)+'\\n')
record({'spawn':os.getpid()})
for line in sys.stdin:
    r=json.loads(line);record({'request':r})
    if 'id' not in r:continue
    method=r['method']
    if method=='initialize':
        result={'protocolVersion':1,'agentCapabilities':{'loadSession':False},'authMethods':[]}
    elif method=='session/resume':
        result={'sessionId':r['params']['sessionId']}
    else:
        print(json.dumps({'jsonrpc':'2.0','id':r['id'],'error':{'code':-32601,'message':'unexpected method'}}),flush=True);continue
    print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':result}),flush=True)
''')
        server.chmod(0o755)
        roots = call(['trust-roots', '--control', str(control)])
        assert roots.returncode == 0, roots.stdout + roots.stderr
        plan = json.loads(roots.stdout)
        assert set(('deny_read','deny_write','out_of_reach_already','socket')) <= plan.keys()
        yield root, control, workspace, events, server, plan


def policy(case, omit=None):
    root, control, workspace, events, server, plan = case
    reads = list(dict.fromkeys([*plan['deny_read'], *plan['deny_write']]))
    if omit == 'control':
        reads = [p for p in reads if Path(p).resolve() != control]
    elif omit == 'code':
        code = H.parent.resolve()
        reads = [p for p in reads if not code.is_relative_to(Path(p).resolve())]
    b = Boundary.parse({'schema':'factory-seat-boundary/1','role':'coder',
                        'denied_reads':reads,'protected_writes':[str(control)],'allowed_sockets':[]})
    path = control / 'boundary.json'
    path.write_text(json.dumps(b.document()))
    return b, path


def probe(case, boundary, path, resume=None):
    _,control,workspace,_,server,_ = case
    binding={'schema':'factory-lane-boundary/1','role':'coder','contract':str(path),'digest':boundary.digest}
    args=['probe','--protocol','acp','--record',str(control/'coder-hosted.json'),
          '--workspace',str(workspace),'--boundary-record',json.dumps(binding),
          '--boundary-role','coder','--control',str(control)]
    if resume:args += ['--resume',resume]
    return call([*args,'--',str(server),'acp','--stdio'])


def rows(case):
    p=case[3]
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


@pytest.mark.parametrize('omit',['control','code'])
def test_incomplete_kernel_coverage_refuses_before_provider(kernel_probe, omit):
    b,path=policy(kernel_probe,omit)
    result=probe(kernel_probe,b,path)
    assert result.returncode == 70, result.stdout+result.stderr
    assert not rows(kernel_probe), 'incomplete profile spawned the provider'
    assert not (kernel_probe[1]/'coder-hosted.json').exists()


@pytest.mark.parametrize('resume',[None,'11111111-2222-4333-8444-555555555555'])
def test_complete_profile_attests_actual_child_without_prompt(kernel_probe,resume):
    b,path=policy(kernel_probe)
    result=probe(kernel_probe,b,path,resume)
    assert result.returncode == 0,result.stdout+result.stderr
    output=json.loads(result.stdout)
    assert output['initialized'] is True
    assert output['prompts_sent'] == output['sessions_made'] == 0
    assert output['session_id'] == resume
    assert output['resumed'] is bool(resume)
    receipt=output['confined']
    spawned=[r['spawn'] for r in rows(kernel_probe) if 'spawn' in r]
    assert spawned == [receipt['pid']]
    assert receipt['started']
    assert receipt['contract'] == str(path)
    assert receipt['digest'] == b.digest and receipt['role'] == 'coder'
    assert all(receipt['denied'][kind] > 0 for kind in ('read','write','connect'))
    methods=[r['request']['method'] for r in rows(kernel_probe) if 'request' in r]
    assert methods == ['initialize'] + (['session/resume'] if resume else [])
    assert not (kernel_probe[1]/'coder-hosted.json').exists(), 'probe must not persist session'
