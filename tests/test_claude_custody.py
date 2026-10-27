"""Exercise the actual generated module; no model, operator policy or credentials."""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "harness"))
import claude_custody  # noqa: E402
import signet_hooks  # noqa: E402


def prepare(tmp_path):
    python = pathlib.Path(sys.executable)
    contract = {"schema": signet_hooks.SCHEMA, "evaluator": str(python),
                "evaluator_sha256": hashlib.sha256(python.read_bytes()).hexdigest(),
                "owner_home": str(tmp_path), "owner_user": "fixture",
                "project": str(tmp_path), "session": "synthetic-session"}
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract))
    return path, signet_hooks.digest(contract), python


def generated(tmp_path):
    path, digest, python = prepare(tmp_path)
    return claude_custody.files(path, digest, python,
        custody_socket="/tmp/synthetic-custody.sock", custody_digest="a" * 64)


def node(tmp_path, body):
    executable = shutil.which("node")
    if not executable:
        pytest.skip("native module fixture requires Node; provider acceptance remains separate")
    files = generated(tmp_path)
    module = tmp_path / "custody.mjs"
    module.write_text(files["hooks/custody.js"])
    runner = tmp_path / "fixture.mjs"
    runner.write_text("import assert from 'node:assert/strict';\n"
        "import {register} from './custody.mjs';\n"
        "let handler, failure;\n"
        "register((event, fn) => {assert.equal(event, 'classic.PreToolUse'); handler=fn;"
        "return {catch(fn) {failure=fn;}};});\n"
        "const reply=(permissionDecision, extra={}) => ({exitCode:0, stdout:JSON.stringify({"
        "hookSpecificOutput:{hookEventName:'PreToolUse',permissionDecision,...extra}})});\n"
        + body)
    result = subprocess.run([executable, str(runner)], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return result


def test_native_short_circuit_requires_custody_before_owner(tmp_path):
    node(tmp_path, """
let held=false, receipts=[], executed=[], nativeCalls=[];
const $={process:{async run(argv, options) {
  assert(argv.includes('--custody-socket'));
  assert(!argv.some(x=>x.includes('synthetic-private-input')));
  const call=JSON.parse(options.stdin);
  assert.equal(call.hook_event_name,'PreToolUse');
  if(held) return reply('deny',{permissionDecisionReason:'retained hold'});
  receipts.push(call.tool_name);
  if(call.tool_name==='Bash') {
    held=true;return reply('deny',{permissionDecisionReason:'fixture denied'});
  }
  return reply('allow');
}}};
// The native owner bypasses ordinary settings hooks when it rejects the call.
const native=async(e, next)=>{nativeCalls.push(e.tool);return e.tool==='Bash' ?
  {deny:'native fixture denial'} : next(e);};
const effect=async(e)=>{
 executed.push(e.tool);return {additionalContext:['native redaction retained']};
};
await native({tool:'Bash'},e=>handler($,e,effect));
await native({tool:'Kindex'},e=>handler($,e,effect));
assert.deepEqual(executed,['Kindex']); // exact pre-fix ordering defect
assert.equal(held,false);
held=false;receipts=[];executed=[];nativeCalls=[];
const route=e=>handler($,e,x=>native(x,effect));
await route({tool:'Kindex',query:'relevant context'});
assert.equal((await route({tool:'Bash',command:'synthetic-private-input'})).deny,'fixture denied');
assert.equal((await route({tool:'Kindex',query:'different request'})).deny,'retained hold');
assert.deepEqual(receipts,['Kindex','Bash']);
assert.deepEqual(executed,['Kindex']);
assert.deepEqual(nativeCalls,['Kindex']);
""")


@pytest.mark.parametrize("fault", ["throw", "timeout", "exit", "invalid", "missing", "unknown",
                                   "extra", "badInput", "badContext"])
def test_failure_never_reaches_owner_or_tool(tmp_path, fault):
    node(tmp_path, "const fault=" + json.dumps(fault) + ";\n" + """
let nextCalls=0;
const $={process:{async run(){
 if(fault==='throw'||fault==='timeout') throw Error('sensitive fixture');
 if(fault==='exit') return {exitCode:1,stdout:'sensitive fixture'};
 if(fault==='invalid') return {exitCode:0,stdout:'not-json'};
 if(fault==='missing') return {exitCode:0,stdout:'{}'};
 if(fault==='unknown') return reply('unknown');
 if(fault==='extra') return reply('allow',{unexpected:true});
 if(fault==='badInput') return reply('allow',{updatedInput:[]});
 return reply('allow',{additionalContext:42});
}}};
const result=await handler($,{tool:'Bash',command:'harmless'},async()=>{nextCalls++;});
assert.equal(nextCalls,0);assert.equal(typeof result.deny,'string');
assert(!JSON.stringify(result).includes('sensitive fixture'));
assert.equal(typeof failure().deny,'string');
""")


def test_allow_preserves_native_denial_context_and_input_replacement(tmp_path):
    node(tmp_path, """
const $={process:{async run(){return reply('allow',{additionalContext:'policy context',
 updatedInput:{command:'redacted',tool:'forged',tool_use_id:'forged'}});}}};
let forwarded;
const result=await handler($,{tool:'Bash',tool_use_id:'real',command:'sensitive'},async e=>{
 forwarded=e;return {deny:'native denial',additionalContext:['native context']};
});
assert.deepEqual(forwarded,{command:'redacted',tool:'Bash',tool_use_id:'real'});
assert.equal(result.deny,'native denial');
assert.deepEqual(result.additionalContext,['native context','policy context']);
""")


@pytest.mark.parametrize("decision", ["deny", "ask"])
def test_no_silent_approval_of_policy_decisions(tmp_path, decision):
    node(tmp_path, "const decision=" + json.dumps(decision) + ";\n" + """
const $={process:{async run(){
 return reply(decision,{permissionDecisionReason:'scoped decision'});
}}};
const result=await handler($,{tool:'Read'},async()=>{throw Error('must not execute');});
assert.deepEqual(result,{[decision]:'scoped decision'});
""")


def test_generator_requires_exact_owner_custody_and_executable(tmp_path):
    path, digest, python = prepare(tmp_path)
    kwargs = {"custody_socket": "/tmp/synthetic.sock", "custody_digest": "a" * 64}
    for change in ({"owner_plugin": "unqualified"}, {"custody_socket": "relative"},
                   {"custody_digest": ""}):
        with pytest.raises(ValueError):
            claude_custody.files(path, digest, python, **{**kwargs, **change})
    with pytest.raises(ValueError, match="interpreter"):
        claude_custody.files(path, digest, tmp_path, **kwargs)
    files = claude_custody.files(path, digest, python, **kwargs)
    manifest = json.loads(files[".claude-plugin/plugin.json"])
    assert manifest["dependencies"] == ["signet-eval-functions@skills-dir"]
    assert set(files) == {".claude-plugin/plugin.json", "hooks/hooks.json", "hooks/custody.js"}
    assert "__FACTORY_CUSTODY_ARGV__" not in files["hooks/custody.js"]


def test_cli_will_not_replace_existing_plugin(tmp_path):
    path, digest, python = prepare(tmp_path)
    root = tmp_path / "existing"
    root.mkdir()
    marker = root / "retain"
    marker.write_text("original")
    result = subprocess.run([str(python), claude_custody.__file__, "--contract", str(path),
        "--digest", digest, "--python", str(python), "--custody-socket", "/tmp/fixture.sock",
        "--custody-digest", "a" * 64, "--output", str(root)], capture_output=True, text=True)
    assert result.returncode != 0
    assert marker.read_text() == "original" and list(root.iterdir()) == [marker]


def test_installed_cli_module_contract_without_model_or_real_process_call(tmp_path):
    cli = shutil.which("claude")
    if not cli:
        pytest.skip("installed Claude module fixture requires its CLI; no provider claim")
    root = tmp_path / "fixture-plugin"
    for name, content in generated(tmp_path).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    fixture = pathlib.Path(__file__).parent / "fixtures/custody-native.test.ts"
    (root / "custody.test.ts").write_text(fixture.read_text())
    # Official test kit stubs every process call; does not start a conversation or log in.
    result = subprocess.run([cli, "plugin", "test", str(root)], capture_output=True,
                            text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 pass" in result.stdout + result.stderr
    assert "0 fail" in result.stdout + result.stderr
