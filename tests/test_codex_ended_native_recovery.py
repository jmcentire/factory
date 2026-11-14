"""Independent public native Codex recovery; synthetic provider, no model calls."""

import json
import os
import signal
import sys

import pytest

from tests.test_native_claude_contract import FACTORY, execute, launch, read_events, wait_for

pytest_plugins = ["tests.test_native_claude_contract"]

PROVIDER = r"""
import json, os, pathlib, subprocess, sys, time
args = sys.argv[1:]
folder = pathlib.Path(__file__).parent
log = folder/'codex-events.jsonl'
def record(**data):
    with log.open('a') as out: out.write(json.dumps(data)+'\n')
if args == ['--version']: print('codex-cli 0.162.0-test'); raise SystemExit
if args == ['login', 'status']: print('Logged in using ChatGPT'); raise SystemExit
if '--help' in args:
    print('--ignore-user-config --ignore-rules --strict-config --json --thread '
          '--no-alt-screen --no-daemon --add-dir --model --message'); raise SystemExit
if args and args[0] == 'queue':
    record(queue=args)
    print('Queued message for session', args[args.index('--thread')+1]); raise SystemExit
record(pid=os.getpid(), argv=args, home=os.environ.get('CODEX_HOME'),
       tty=os.isatty(0) and os.isatty(1))
assert 'exec' not in args
settings = json.loads((pathlib.Path.cwd()/'.codex/hooks.json').read_text())
session = '12345678-1234-4234-8234-123456789abc'
if args and args[0] == 'resume': assert session in args
# Real native resume does not emit start until supplied a prompt.
if '--' not in args:
    time.sleep(20); raise SystemExit
prompt = args[args.index('--')+1]
record(prompt=prompt)
if args[0]=='resume' and (folder/'uncertain-resume').exists(): raise SystemExit(19)
def entries(node, event):
    if isinstance(node, dict):
        for k,v in node.items():
            if k == event: yield v
            else: yield from entries(v,event)
    elif isinstance(node,list):
        for v in node: yield from entries(v,event)
def commands(node):
    if isinstance(node,dict):
        if node.get('type') == 'command': yield node['command']
        else:
            for v in node.values(): yield from commands(v)
    elif isinstance(node,list):
        for v in node: yield from commands(v)
def report(kind, **extra):
    body=dict(hook_event_name=kind,session_id=session,model='synthetic-model',permission_mode='default',**extra)
    for item in entries(settings,kind):
        for cmd in commands(item):
            subprocess.run(cmd,shell=True,input=json.dumps(body),text=True,check=True,timeout=5)
report('SessionStart',source='resume' if args[0]=='resume' else 'startup')
if args[0]=='resume':
    # A successful spawn or SessionStart is not delivery observation.
    while not (folder/'release-receipt').exists(): time.sleep(.02)
report('UserPromptSubmit',prompt=prompt,turn_id='synthetic-turn')
report('Stop',turn_id='synthetic-turn',stop_hook_active=False,last_assistant_message='done')
if args[0]!='resume':
    report('SessionEnd',reason='fixture-exit'); raise SystemExit
while True: time.sleep(.1)
"""


@pytest.fixture
def owned_codex(native_case):
    yield native_case
    for event in read_events(native_case["bin_dir"] / "codex-events.jsonl"):
        if "pid" in event:
            try:
                os.kill(event["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize("uncertain", [False, True])
def test_ended_native_codex_resumes_marked_followup_without_replaying_initial(
    owned_codex, tmp_path, uncertain
):
    case = owned_codex
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
    case["env"]["CODEX_HOME"] = str(tmp_path / "native-codex-profile")
    launch(case)
    provider = case["bin_dir"] / "codex"
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
            "codex-interactive",
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
    log = case["bin_dir"] / "codex-events.jsonl"
    initial = next(e for e in read_events(log) if "argv" in e)
    initial_prompt = next(e["prompt"] for e in read_events(log) if "prompt" in e)
    if uncertain:
        (case["bin_dir"] / "uncertain-resume").touch()
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
    if not uncertain:
        assert sent.returncode in (0, 75), sent.stdout + sent.stderr
    resumed = wait_for(
        lambda: next((e for e in read_events(log) if e.get("argv", [None])[0] == "resume"), None)
    )
    assert resumed["tty"] and resumed["home"] == initial["home"]
    assert session in resumed["argv"] and "exec" not in resumed["argv"]
    model_flag = "--model" if "--model" in resumed["argv"] else "-m"
    assert resumed["argv"][resumed["argv"].index(model_flag) + 1] == "synthetic-model"
    assert (
        resumed["argv"][1 : resumed["argv"].index(session)]
        == initial["argv"][: initial["argv"].index("--")]
    )
    assert "--no-daemon" in resumed["argv"] and "--no-alt-screen" in resumed["argv"]
    followup = resumed["argv"][resumed["argv"].index("--") + 1]
    assert "[factory-message:" in followup and followup != initial_prompt
    assert state()["session_id"] == session
    assert all(v["state"] != "observed" for k, v in state()["deliveries"].items() if k != "initial")
    assert sum(e.get("prompt") == initial_prompt for e in read_events(log)) == 1
    if uncertain:
        # Provider consumed the launch argument then vanished before all native
        # receipts. Another public status must not replay that uncertain ID.
        (case["bin_dir"] / "uncertain-resume").unlink()
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
        assert sum(e.get("prompt") == followup for e in read_events(log)) == 1
        assert sum(e.get("prompt") == initial_prompt for e in read_events(log)) == 1
        assert all(
            v["state"] != "observed" for k, v in state()["deliveries"].items() if k != "initial"
        )
        return
    (case["bin_dir"] / "release-receipt").touch()
    wait_for(
        lambda: any(
            v["state"] == "observed" for k, v in state()["deliveries"].items() if k != "initial"
        )
    )
    # The resumed native process remains the owner for subsequent provider-queue
    # continuation; acceptance by queue alone must not invent hook observation.
    queued = execute(
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
    assert queued.returncode in (0, 75), queued.stdout + queued.stderr
    queue_call = wait_for(lambda: next((e for e in read_events(log) if "queue" in e), None))
    argv = queue_call["queue"]
    assert argv[argv.index("--thread") + 1] == session
    assert "[factory-message:" in argv[argv.index("--message") + 1]
    assert len([e for e in read_events(log) if e.get("argv", [None])[0] == "resume"]) == 1
    assert sum(e.get("prompt") == initial_prompt for e in read_events(log)) == 1
