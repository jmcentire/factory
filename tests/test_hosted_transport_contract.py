"""Independent Slice 1 public transport contract; fake RPC providers, never models.

Oracle: /tmp/factory-hosted-contract.md, approved hosted-only scope. These tests
intentionally cannot qualify Signet enforcement; required security must refuse.
"""

import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

H = Path(__file__).resolve().parents[1] / "harness"
pytest_plugins = ["tests.test_native_claude_contract"]

SID = "12345678-1234-4234-8234-123456789abc"
FAKE = r"""
import json, os, pathlib, sys, time

folder = pathlib.Path(__file__).parent
agent = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]


def log(**r):
    with (folder / "rpc.jsonl").open("a") as f:
        f.write(json.dumps(r) + "\n")


if "--help" in args:
    print("serve acp --stdio --model --mode --sandbox --provider --config")
    sys.exit()
if "--version" in args:
    print("1.5.9-synthetic")
    sys.exit()
log(
    spawn=True,
    argv=args,
    home=os.environ.get("HOME"),
    keys=[
        k
        for k in ["META_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "AGY_LLM_GATEWAY_API_KEY"]
        if os.environ.get(k)
    ],
)
assert not {"-p", "--print", "--prompt", "exec"}.intersection(args)
assert args and args[0] == ("acp" if agent == "agy" else "serve"), args
sid = "12345678-1234-4234-8234-123456789abc"


def emit(r):
    print(json.dumps(dict(jsonrpc="2.0", **r)), flush=True)


def answer(r, result):
    emit(dict(id=r["id"], result=result))


def notify(method, params):
    emit(dict(method=method, params=params))


for line in sys.stdin:
    r = json.loads(line)
    m = r.get("method")
    p = r.get("params", {})
    log(request=r)
    if "id" not in r:
        continue
    if m == "initialize":
        answer(
            r,
            dict(
                protocolVersion=1,
                agentInfo={"name": "synthetic", "version": "1"},
                agentCapabilities={"loadSession": True, "sessionCapabilities": {"resume": {}}},
                serverInfo={"name": "synthetic", "version": "1"},
                capabilities={},
            ),
        )
    elif m in ["session/new", "session/start"]:
        sid = p.get("sessionId", sid)
        answer(
            r,
            {"sessionId": sid}
            if agent == "agy"
            else {
                "session": {
                    "sessionId": sid,
                    "status": "idle",
                    "providerId": "echo",
                    "turnCount": 0,
                },
                "viewCursor": "",
            },
        )
    elif m == "session/resume":
        sid = p["sessionId"]
        mode = (folder / "resume-mode").read_text() if (folder / "resume-mode").exists() else "ok"
        if mode == "missing":
            emit(
                dict(
                    id=r["id"],
                    error={
                        "code": -32603 if agent == "agy" else -32020,
                        "message": "missing session; try session/load",
                    },
                )
            )
            continue
        if mode == "mismatch":
            sid = "99999999-9999-4999-8999-999999999999"
        answer(
            r,
            {"sessionId": sid}
            if agent == "agy"
            else {
                "session": {
                    "sessionId": sid,
                    "status": "idle",
                    "providerId": "echo",
                    "turnCount": 1,
                },
                "viewCursor": "",
                "items": [],
            },
        )
    elif m in ["session/prompt", "turn/start"]:
        log(prompt=p)
        if (folder / "crash-before-ack").exists():
            sys.exit(19)
        if agent == "agy":
            notify(
                "session/update",
                {
                    "sessionId": sid,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "__LANE_DONE__"},
                    },
                },
            )
            answer(r, {"stopReason": "end_turn"})
        else:
            tid = "turn-" + str(r["id"])
            answer(
                r,
                {
                    "sessionId": sid,
                    "turnId": tid,
                    "accepted": True,
                    "disposition": "started",
                    "startedNewTurn": True,
                },
            )
            notify("turn/started", {"sessionId": sid, "turnId": tid})
            notify(
                "item/updated",
                {
                    "sessionId": sid,
                    "turnId": tid,
                    "item": {"id": tid + "-text", "type": "agentMessage", "text": "__LANE_DONE__"},
                },
            )
            notify("turn/completed", {"sessionId": sid, "turnId": tid, "terminal": "completed"})
        if (folder / "exit-after-completion").exists():
            sys.exit(0)
    elif m == "session/load":
        raise AssertionError("forbidden fallback")
    else:
        answer(r, {})
"""


def until(predicate, child=None, diagnostic=None):
    end = time.monotonic() + 7
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        if child is not None and child.poll() is not None:
            break
        time.sleep(0.03)
    pytest.fail(
        "public outcome missing: "
        + (diagnostic.read_text() if diagnostic and diagnostic.exists() else "")
    )


@pytest.fixture(params=["agy", "muse"])
def hosted(tmp_path, monkeypatch, request):
    monkeypatch.syspath_prepend(str(H))
    driver = importlib.import_module("persistent_lane")
    dialogue = importlib.import_module("lane_dialogue")
    root = tmp_path / "run"
    folder = root / "tmux-lanes"
    folder.mkdir(parents=True)
    project = tmp_path / "workspace"
    project.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    config = home / "settings.json"
    config.write_text('{"sentinel":"operator policy untouched"}')
    executable = folder / request.param
    executable.write_text(f"#!{sys.executable}\n" + FAKE)
    executable.chmod(0o755)
    prompt = root / "prompt"
    prompt.write_text("-literal initial\nno shell $(false)")
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "SHELL", "TMPDIR", "TERM", "LANG"}}
    env.update(HOME=str(home), FACTORY_PYTHON=sys.executable)
    for key in ["META_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "AGY_LLM_GATEWAY_API_KEY"]:
        env[key] = "synthetic-not-a-key"
    command = [
        sys.executable,
        str(H / "lane_agent.py"),
        "run",
        "--slot",
        "coder",
        "--log",
        str(folder / "coder-log"),
        "--exits",
        str(folder / "coder-exits.jsonl"),
        "--prompt-arg",
        str(prompt),
        "--session-agent",
        request.param,
        "--session-file",
        str(folder / "coder-thread-id"),
        "--persistent",
        "--run-root",
        str(root),
        "--",
        str(executable),
    ]
    children = []
    streams = []

    class Lane:
        agent = request.param

        def start(self, extra=None, message=None):
            stream = (root / f"wrapper-{len(children)}.log").open("w")
            streams.append(stream)
            cmd = list(command)
            if message:
                mid, text = message
                path = root / "followup"
                path.write_text(text)
                cmd[cmd.index("--prompt-arg") + 1] = str(path)
                cmd[cmd.index("--") : cmd.index("--")] = ["--launch-message-id", mid]
            p = subprocess.Popen(
                cmd,
                cwd=project,
                env={**env, **(extra or {})},
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
            children.append(p)
            return p

        def rows(self):
            p = folder / "rpc.jsonl"
            return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

        def state(self):
            p = folder / "coder-persistent.json"
            return json.loads(p.read_text()) if p.exists() else {}

        def plan(self, text):
            return dialogue.plan_message(
                root,
                sender="validator",
                lane="coder",
                message_kind="status-probe",
                text=text,
                basis="independent hosted transport contract",
                authority="runtime-protocol",
            )["message_id"]

        def send(self, mid, thread=None):
            return driver.request(root, "coder", mid, thread or self.state()["session_id"])

    lane = Lane()
    lane.folder = folder
    lane.root = root
    lane.project = project
    lane.home = home
    lane.command = command
    lane.env = env
    yield lane
    for p in children:
        if p.poll() is None:
            p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
    for stream in streams:
        stream.close()
    assert config.read_text() == '{"sentinel":"operator policy untouched"}'


def test_signet_required_refuses_before_any_provider_spawn(hosted):
    lane = hosted
    p = lane.start({"FACTORY_REQUIRE_SIGNET": "1"})
    assert p.wait(timeout=10) == 77, (lane.root / "wrapper-0.log").read_text()
    assert not lane.rows(), "Signet unqualified must refuse even preflight provider spawn"


def test_hosted_initial_and_live_followup_bind_exact_identity_without_pollution(hosted):
    lane = hosted
    p = lane.start()
    until(
        lambda: lane.state().get("deliveries", {}).get("initial", {}).get("state") == "observed",
        p,
        lane.root / "wrapper-0.log",
    )
    sid = lane.state()["session_id"]
    before = lane.rows()
    assert len([x for x in before if x.get("prompt")]) == 1
    assert all(not r["keys"] and r["home"] == str(lane.home) for r in before if r.get("spawn"))
    mid = lane.plan("-followup\nverbatim body")
    foreign = lane.send(mid, "99999999-9999-4999-8999-999999999999")
    assert foreign.get("state") != "observed"
    assert len([x for x in lane.rows() if x.get("prompt")]) == 1
    lane.send(mid)
    until(lambda: lane.send(mid).get("state") == "observed", p, lane.root / "wrapper-0.log")
    prompts = [x["prompt"] for x in lane.rows() if "prompt" in x]
    assert len(prompts) == 2
    assert prompts[-1]["sessionId"] == sid
    assert "-followup\\nverbatim body" in json.dumps(prompts[-1])
    for _ in range(2):
        assert lane.send(mid)["state"] == "observed"
    assert len([x for x in lane.rows() if "prompt" in x]) == 2
    if lane.agent == "muse":
        import uuid

        assert uuid.UUID(prompts[-1]["commandId"]).version == 7
        assert prompts[-1]["ifBusy"] == "queue"
    assert not (lane.project / ".muse").exists()
    assert not (lane.project / ".agents/plugins/lane").exists()
    assert not any(x.get("request", {}).get("method") == "session/load" for x in lane.rows())


def test_uncertain_initial_handover_never_replays(hosted):
    lane = hosted
    (lane.folder / "crash-before-ack").touch()
    p = lane.start()
    until(lambda: any("prompt" in r for r in lane.rows()), p, lane.root / "wrapper-0.log")
    p.wait(timeout=10)
    (lane.folder / "crash-before-ack").unlink()
    second = lane.start()
    second.wait(timeout=10)
    assert len([r for r in lane.rows() if "prompt" in r]) == 1
    assert lane.state().get("deliveries", {}).get("initial", {}).get("state") != "observed"


@pytest.mark.parametrize("mode", ["ok", "missing", "mismatch"])
def test_ended_hosted_resume_is_strict_and_never_loads(hosted, mode):
    lane = hosted
    (lane.folder / "exit-after-completion").touch()
    first = lane.start()
    until(lambda: any("prompt" in r for r in lane.rows()), first, lane.root / "wrapper-0.log")
    first.wait(timeout=10)
    sid = (lane.folder / "coder-thread-id").read_text().strip()
    (lane.folder / "resume-mode").write_text(mode)
    text = "-leading followup\nsecond line"
    mid = lane.plan(text)
    second = lane.start(message=(mid, text))
    code = second.wait(timeout=10)
    requests = [r["request"] for r in lane.rows() if "request" in r]
    resumes = [r for r in requests if r.get("method") == "session/resume"]
    assert len(resumes) == 1
    assert resumes[0]["params"]["sessionId"] == sid
    assert not any(r.get("method") == "session/load" for r in requests)
    prompts = [r["prompt"] for r in lane.rows() if "prompt" in r]
    if mode == "ok":
        assert code == 0, (lane.root / "wrapper-1.log").read_text()
        assert len(prompts) == 2
        assert prompts[-1]["sessionId"] == sid
        assert "-leading followup\\nsecond line" in json.dumps(prompts[-1])
        assert "[factory-message:" in json.dumps(prompts[-1])
        assert "literal initial" not in json.dumps(prompts[-1])
    else:
        assert code == 70, (lane.root / "wrapper-1.log").read_text()
        assert len(prompts) == 1, "failed identity recovery must never submit a prompt"


@pytest.mark.parametrize("agent", ["agy", "muse"])
def test_public_author_custody_refuses_hosted_provider_before_spawn(native_case, tmp_path, agent):
    from tests.test_native_claude_contract import execute, launch

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
    case = native_case
    case["env"] = {k: v for k, v in case["env"].items() if k in allowed}
    launch(case)
    provider = case["bin_dir"] / agent
    provider.write_text(f"#!{sys.executable}\n" + FAKE)
    provider.chmod(0o755)
    prepared = execute(
        [
            sys.executable,
            str(H / "role_inputs.py"),
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
    prompt = tmp_path / "hosted-brief"
    prompt.write_text("synthetic protected author")
    result = execute(
        [
            "bash",
            str(H / "tmux_lane.sh"),
            "native-acceptance",
            "coder",
            "launch",
            "--runs",
            str(case["runs"]),
            "--repo",
            repo,
            "--prompt",
            str(prompt),
            "--agent",
            agent,
        ],
        case["env"],
    )
    assert result.returncode == 77, result.stdout + result.stderr
    assert not (case["bin_dir"] / "rpc.jsonl").exists()
