"""Independent public native Cursor recovery; synthetic provider, no model calls."""

import json
import os
import signal
import subprocess
import sys

import pytest

from tests.test_native_claude_contract import FACTORY, execute, launch, read_events, wait_for

pytest_plugins = ["tests.test_native_claude_contract"]

PROVIDER = r"""
import hashlib, json, os, pathlib, subprocess, sys, time, tty
folder = pathlib.Path(__file__).parent
log = folder/'cursor-events.jsonl'
args=sys.argv[1:]
def record(**data):
    with log.open('a') as out: out.write(json.dumps(data)+'\n')
if args==['--version']: print('2026.11.13-synthetic'); raise SystemExit
if '--help' in args:
    print('--model --workspace --resume --sandbox --output-format --approve-mcps'); raise SystemExit
if args and args[0]=='status':
    print(json.dumps({'status':'authenticated','isAuthenticated':True})); raise SystemExit
assert '-p' not in args and '--print' not in args
assert os.isatty(0) and os.isatty(1)
workspace=pathlib.Path(args[args.index('--workspace')+1])
settings=json.loads((workspace/'.cursor/hooks.json').read_text())
record(pid=os.getpid(),argv=args,home=os.environ.get('HOME'),path=os.environ.get('PATH'),
       config={name:hashlib.sha256((workspace/'.cursor'/name).read_bytes()).hexdigest()
               for name in ['hooks.json','mcp.json','cli.json']})
session='12345678-1234-4234-8234-123456789abc'
resumed=any(a.startswith('--resume') for a in args)
if resumed:
    assert ('--resume='+session in args or
            ('--resume' in args and args[args.index('--resume')+1]==session))
def commands(node):
    if isinstance(node,dict):
        if 'command' in node: yield node['command']
        else:
            for value in node.values(): yield from commands(value)
    elif isinstance(node,list):
        for value in node: yield from commands(value)
generation=0
def report(event,prompt=None):
    payload={'hook_event_name':event,'conversation_id':session,'session_id':session,'generation_id':'gen-'+str(generation),
             'model':'synthetic-model','workspace_roots':[str(workspace)],'status':'completed'}
    if prompt is not None: payload['prompt']=prompt
    for command in commands(settings['hooks'].get(event,[])):
        subprocess.run(command,shell=True,input=json.dumps(payload),text=True,
                       capture_output=True,check=True,timeout=5)
def consume(prompt):
    global generation
    generation += 1
    record(prompt=prompt)
    report('beforeSubmitPrompt',prompt)
    report('stop')
tty.setraw(0)
if resumed:
    # Public Cursor behavior: resumed launch may omit sessionStart entirely.
    assert '--' in args, 'resumed native prompt must be delimited'
    prompt=args[args.index('--')+1]
    record(startup_prompt=prompt)
    if (folder/'uncertain-resume').exists(): raise SystemExit(19)
    while not (folder/'release-receipt').exists(): time.sleep(.02)
    consume(prompt)
else:
    report('sessionStart')
pending=''
while True:
    chunk=os.read(0,4096)
    if not chunk: break
    pending += chunk.decode()
    while '\r' in pending:
        if pending.startswith('\x1b[200~'):
            end=pending.find('\x1b[201~'); enter=pending.find('\r',end) if end>=0 else -1
            if enter<0: break
            prompt=pending[6:end]+pending[end+6:enter]; pending=pending[enter+1:]
        else: prompt,pending=pending.split('\r',1)
        consume(prompt)
        if not resumed:
            report('sessionEnd'); raise SystemExit
"""


@pytest.fixture
def owned_cursor(native_case):
    native_case["message_processes"] = []
    yield native_case
    for process in native_case["message_processes"]:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=5)
    for event in read_events(native_case["bin_dir"] / "cursor-events.jsonl"):
        if "pid" in event:
            try:
                os.kill(event["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize("uncertain", [False, True])
def test_ended_native_cursor_resumes_marked_followup_without_replaying_initial(
    owned_cursor, tmp_path, uncertain
):
    case = owned_cursor
    allowed = {
        "HOME",
        "PATH",
        "SHELL",
        "TMPDIR",
        "TERM",
        "LANG",
        "CLAUDE_CONFIG_DIR",
        "FACTORY_VALIDATOR_AGENT",
        "FACTORY_ORCHESTRATOR_AGENT",
        "FACTORY_PYTHON",
        "FACTORY_CONFIG",
        "FACTORY_KINDEX_LITE_BIN",
    }
    case["env"] = {k: v for k, v in case["env"].items() if k in allowed}
    launch(case)
    provider = case["bin_dir"] / "agent"
    provider.write_text(f"#!{sys.executable}\n" + PROVIDER)
    provider.chmod(0o755)
    prepared = execute(
        [
            sys.executable,
            str(FACTORY / "harness/role_inputs.py"),
            "prepare",
            "--runs",
            str(case["runs"]),
            "--run",
            "native-acceptance",
            "--role",
            "coder",
            "--repo",
            str(tmp_path / "author"),
        ],
        case["env"],
    )
    assert prepared.returncode == 0, prepared.stderr
    repo = json.loads(prepared.stdout)["repository"]
    brief = tmp_path / "brief"
    brief.write_text("INITIAL_NOT_TO_REPLAY")
    env = {**case["env"], "FACTORY_LANE_SANDBOX": "off"}
    result = execute(
        [
            "bash",
            str(FACTORY / "harness/tmux_lane.sh"),
            "native-acceptance",
            "coder",
            "launch",
            "--runs",
            str(case["runs"]),
            "--repo",
            repo,
            "--prompt",
            str(brief),
            "--agent",
            "cursor-agent",
            "--model",
            "synthetic-model",
        ],
        env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    state_file = case["runs"] / "native-acceptance/tmux-lanes/coder-persistent.json"

    def state():
        return json.loads(state_file.read_text()) if state_file.exists() else {}

    before = wait_for(lambda: s if (s := state()).get("phase") == "exited" else None)
    assert before["deliveries"]["initial"]["state"] == "observed"
    session = before["session_id"]
    log = case["bin_dir"] / "cursor-events.jsonl"
    initial = next(e for e in read_events(log) if "argv" in e)
    initial_prompt = next(e["prompt"] for e in read_events(log) if "prompt" in e)
    if uncertain:
        (case["bin_dir"] / "uncertain-resume").touch()
    sent = subprocess.Popen(
        [
            "bash",
            str(FACTORY / "harness/tmux_lane_message.sh"),
            "native-acceptance",
            "validator",
            "coder",
            "status",
            "--runs",
            str(case["runs"]),
        ],
        env=env,
        cwd=FACTORY,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    case["message_processes"].append(sent)
    resumed = wait_for(
        lambda: next(
            (
                e
                for e in read_events(log)
                if any(arg.startswith("--resume") for arg in e.get("argv", []))
            ),
            None,
        )
    )
    assert resumed["home"] == initial["home"]
    assert resumed["config"] == initial["config"]
    assert resumed["argv"][resumed["argv"].index("--model") + 1] == "synthetic-model"
    assert not {"-p", "--print"} & set(resumed["argv"])
    assert session in " ".join(resumed["argv"])
    assert "--sandbox" in resumed["argv"]
    followup = resumed["argv"][resumed["argv"].index("--") + 1]
    assert "[factory-message:" in followup and followup != initial_prompt
    assert sum(e.get("prompt") == initial_prompt for e in read_events(log)) == 1
    assert all(v["state"] != "observed" for k, v in state()["deliveries"].items() if k != "initial")
    if uncertain:
        output, error = sent.communicate(timeout=15)
        assert sent.returncode != 0, output + error
        (case["bin_dir"] / "uncertain-resume").unlink()
        (case["bin_dir"] / "release-receipt").touch()
        execute(
            [
                "bash",
                str(FACTORY / "harness/tmux_lane_message.sh"),
                "native-acceptance",
                "validator",
                "coder",
                "status",
                "--runs",
                str(case["runs"]),
            ],
            env,
        )
        assert sum(e.get("startup_prompt") == followup for e in read_events(log)) == 1
        return
    (case["bin_dir"] / "release-receipt").touch()
    output, error = sent.communicate(timeout=15)
    assert sent.returncode in (0, 75), output + error
    wait_for(
        lambda: any(
            v["state"] == "observed" for k, v in state()["deliveries"].items() if k != "initial"
        )
    )
    assert state()["session_id"] == session
    sent = execute(
        [
            "bash",
            str(FACTORY / "harness/tmux_lane_message.sh"),
            "native-acceptance",
            "validator",
            "coder",
            "status",
            "--runs",
            str(case["runs"]),
        ],
        env,
    )
    assert sent.returncode in (0, 75), sent.stdout + sent.stderr
    wait_for(lambda: len([e for e in read_events(log) if "prompt" in e]) >= 3)
    assert len([e for e in read_events(log) if "argv" in e]) == 2
    assert sum(e.get("prompt") == initial_prompt for e in read_events(log)) == 1
