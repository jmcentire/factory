"""Independent Slice 1 public transport contract; fake RPC providers, never models.

Oracle: tests/contracts/hosted_transport.md, approved hosted-only scope. These tests
intentionally cannot qualify Signet enforcement; required security must refuse.
"""

import hashlib
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
import json, os, pathlib, sys, time, threading, queue

folder = pathlib.Path(__file__).parent
agent = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]


def log(**r):
    with (folder / "rpc.jsonl").open("a") as f:
        f.write(json.dumps(r) + "\n")


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
if "--help" in args:
    print("serve acp --stdio --model --mode --sandbox --provider --config")
    sys.exit()
if "--version" in args:
    print("1.5.9-synthetic")
    sys.exit()
assert not {"-p", "--print", "--prompt", "exec"}.intersection(args)
assert args and args[0] == ("acp" if agent == "agy" else "serve"), args
sid = "12345678-1234-4234-8234-123456789abc"


def emit(r):
    with output_lock:
        print(json.dumps(dict(jsonrpc="2.0", **r)), flush=True)


def answer(r, result):
    emit(dict(id=r["id"], result=result))


def notify(method, params):
    emit(dict(method=method, params=params))


prompt_count = 0
active = False
incoming = queue.Queue()
turns = queue.Queue()
output_lock = threading.Lock()


def read_wire():
    for line in sys.stdin:
        frame = json.loads(line)
        log(wire_received=frame)
        incoming.put(frame)
    incoming.put(None)


def run_turns():
    global active
    while True:
        tid, session, hold, command_id, text = turns.get()
        notify("turn/started", {"sessionId": session, "turnId": tid, "commandId": command_id})
        notify(
            "item/completed",
            {
                "sessionId": session,
                "item": {
                    "itemId": tid + "-user",
                    "kind": "userMessage",
                    "turnId": tid,
                    "revision": 1,
                    "status": "completed",
                    "recordedAt": "2026-11-14T09:00:00Z",
                    "text": text,
                    "commandId": command_id,
                },
            },
        )
        log(turn_started=tid)
        if hold:
            log(active=True)
            while not (folder / "release-turn").exists():
                time.sleep(0.02)
        notify(
            "item/completed",
            {
                "sessionId": session,
                "turnId": tid,
                "item": {
                    "itemId": tid + "-text",
                    "kind": "agentMessage",
                    "turnId": tid,
                    "revision": 1,
                    "status": "completed",
                    "recordedAt": "2026-11-14T09:00:00Z",
                    "text": "__LANE_DONE__",
                },
            },
        )
        notify("turn/completed", {"sessionId": session, "turnId": tid, "terminal": "completed"})
        log(turn_completed=tid)
        turns.task_done()
        active = turns.unfinished_tasks > 0


threading.Thread(target=read_wire, daemon=True).start()
threading.Thread(target=run_turns, daemon=True).start()
while True:
    r = incoming.get()
    if r is None:
        break
    m = r.get("method")
    p = r.get("params", {})
    log(request=r)
    if "id" not in r:
        assert m in {"initialized", "notifications/initialized", "session/cancel"}, m
        continue
    if m is None:
        assert r["id"] == "permission-1" and ("result" in r or "error" in r), r
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
        assert m == ("session/new" if agent == "agy" else "session/start")
        if agent == "agy":
            assert isinstance(p["cwd"], str) and isinstance(p["mcpServers"], list)
        else:
            import uuid

            assert uuid.UUID(p["commandId"]).version == 7
        sid = p.get("sessionId", sid)
        identity = {"sessionId": sid}
        if agent == "agy" and (folder / "identity-mode").exists():
            mode = (folder / "identity-mode").read_text()
            identity = (
                {} if mode == "missing" else {"sessionId": 42 if mode == "nonstring" else "foreign"}
            )
        if agent == "agy" and (folder / "config-mismatch").exists():
            identity["configOptions"] = [
                {
                    "id": "model",
                    "name": "Model",
                    "category": "model",
                    "type": "select",
                    "currentValue": "old-model",
                    "options": [
                        {"value": "old-model", "name": "Old"},
                        {"value": "wanted-model", "name": "Wanted"},
                    ],
                },
                {
                    "id": "thought_level",
                    "name": "Thought level",
                    "category": "thought_level",
                    "type": "select",
                    "currentValue": "low",
                    "options": [{"value": "low", "name": "Low"}, {"value": "high", "name": "High"}],
                },
            ]
        answer(
            r,
            identity
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
        identity = {"sessionId": sid}
        if agent == "agy" and (folder / "identity-mode").exists():
            mode = (folder / "identity-mode").read_text()
            identity = (
                {} if mode == "missing" else {"sessionId": 42 if mode == "nonstring" else "foreign"}
            )
        if agent == "agy" and (folder / "config-mismatch").exists():
            identity["configOptions"] = [
                {
                    "id": "model",
                    "name": "Model",
                    "category": "model",
                    "type": "select",
                    "currentValue": "old-model",
                    "options": [
                        {"value": "old-model", "name": "Old"},
                        {"value": "wanted-model", "name": "Wanted"},
                    ],
                },
                {
                    "id": "thought_level",
                    "name": "Thought level",
                    "category": "thought_level",
                    "type": "select",
                    "currentValue": "low",
                    "options": [{"value": "low", "name": "Low"}, {"value": "high", "name": "High"}],
                },
            ]
        answer(
            r,
            identity
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
        assert m == ("session/prompt" if agent == "agy" else "turn/start")
        assert p["sessionId"] == sid
        content = p["prompt" if agent == "agy" else "input"]
        assert isinstance(content, list) and content
        assert all(x.get("type") == "text" and isinstance(x.get("text"), str) for x in content)
        if agent == "muse":
            import uuid

            assert uuid.UUID(p["commandId"]).version == 7 and p["ifBusy"] == "queue"
        prompt_count += 1
        snapshots = {
            str(q.relative_to(folder.parent)): q.read_text() for q in folder.parent.rglob("*.json")
        }
        log(prompt=p, durable_before_write=snapshots)
        if (folder / "permission").exists():
            params = {
                "sessionId": sid,
                "toolCall": {"toolCallId": "unknown-tool", "title": "unknown arbitrary effect"},
                "options": [
                    {"optionId": "always", "name": "Always", "kind": "allow_always"},
                    {"optionId": "once", "name": "Once", "kind": "allow_once"},
                    {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
                ],
            }
            if agent == "muse":
                params = {
                    "sessionId": sid,
                    "approvalId": "approval-1",
                    "toolName": "unknown-tool",
                    "rawArgs": "{}",
                    "subject": {"kind": "future-effect"},
                    "availableChoices": [
                        {"choiceId": "always", "label": "Always", "scope": "localPersistent"},
                        {"choiceId": "reject", "label": "Reject"},
                    ],
                    "currentRequirementId": {"approvalId": "approval-1", "sourceIndex": 0},
                    "turnId": "turn-1",
                    "itemId": "tool-1",
                    "taskId": "task-1",
                    "toolCallId": "call-1",
                    "judgeEscalated": False,
                    "protectedWrite": False,
                    "sourceRange": {},
                    "viewCursor": "",
                }
            emit(
                {
                    "id": "permission-1",
                    "method": "session/request_permission"
                    if agent == "agy"
                    else "approval/request",
                    "params": params,
                }
            )
            continue

        if agent == "agy" and (folder / "reject-update").exists():
            mode = (folder / "reject-update").read_text()
            update = {"sessionUpdate": "current_mode_update", "currentModeId": "default"}
            if mode == "config":
                update = {"sessionUpdate": "config_option_update", "configOptions": []}
            if mode == "foreign":
                update = {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "old answer"},
                }
            params = {"sessionId": sid, "update": update}
            if mode == "foreign":
                params["sessionId"] = "foreign"
            if mode == "missing":
                params.pop("sessionId")
            if mode == "nonstring":
                params["sessionId"] = 42
            notify("session/update", params)
            emit({"id": r["id"], "error": {"code": -32603, "message": "prompt rejected"}})
            log(rejected=True)
            continue
        if (folder / "crash-before-ack").exists():
            sys.exit(19)
        if agent == "agy":
            if (folder / "hold-turn").exists() and prompt_count == 1:
                log(active=True)
                while not (folder / "release-turn").exists():
                    time.sleep(0.02)
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
                    "status": "accepted",
                    "commandId": p["commandId"],
                    "disposition": "queued" if active else "started",
                    "startedNewTurn": not active,
                },
            )
            active = True
            turns.put(
                (
                    tid,
                    sid,
                    (folder / "hold-turn").exists() and prompt_count == 1,
                    p["commandId"],
                    "".join(x["text"] for x in content),
                )
            )
        if (folder / "exit-after-completion").exists():
            if agent == "muse":
                turns.join()
            sys.exit(0)
    elif m == "session/load":
        raise AssertionError("forbidden fallback")
    elif m == "session/set_mode":
        assert agent == "agy" and p["sessionId"] == sid and isinstance(p["modeId"], str)
        answer(r, {})
    elif m == "session/set_config_option":
        assert agent == "agy" and p["sessionId"] == sid and isinstance(p["configId"], str)
        assert "value" in p
        if (folder / "config-revert").exists():
            for opt in identity.get("configOptions", []):
                if opt["id"] == p["configId"]:
                    opt["currentValue"] = p["value"]
                if p["configId"] == "thought_level" and opt["id"] == "model":
                    opt["currentValue"] = "old-model"
        answer(r, {"configOptions": identity.get("configOptions", [])})
    else:
        raise AssertionError("unsupported synthetic protocol request: " + json.dumps(r))
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
    env["PATH"] = str(folder) + os.pathsep + env["PATH"]
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
                sid = (folder / "coder-thread-id").read_text().strip()
                cmd.extend(["resume", sid] if self.agent == "muse" else ["--conversation", sid])
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
    try:
        foreign = lane.send(mid, "99999999-9999-4999-8999-999999999999")
    except ValueError:
        foreign = {"state": "refused"}
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


def test_pending_unknown_permission_is_visible_and_never_autoapproved(hosted):
    lane = hosted
    (lane.folder / "permission").touch()
    p = lane.start()
    until(lambda: lane.state().get("phase") == "asking-operator", p, lane.root / "wrapper-0.log")
    mid = lane.plan("must not pass unanswered operator boundary")
    result = lane.send(mid)
    assert result.get("state") != "observed"
    time.sleep(0.15)
    messages = [r["request"] for r in lane.rows() if "request" in r]
    assert len([r for r in lane.rows() if "prompt" in r]) == 1
    assert not any(r.get("method") == "approval/decide" for r in messages)
    for r in messages:
        if r.get("id") == "permission-1" and "result" in r:
            assert not any(
                x in json.dumps(r["result"])
                for x in ["allow_once", "allow_always", "localPersistent", '"always"', '"once"']
            )


def test_active_turn_respects_native_queue_or_acp_busy_boundary(hosted):
    lane = hosted
    (lane.folder / "hold-turn").touch()
    p = lane.start()
    until(lambda: any(r.get("active") for r in lane.rows()), p, lane.root / "wrapper-0.log")
    mid = lane.plan("followup while prior turn remains active")
    result = lane.send(mid)
    time.sleep(0.1)
    prompts = [r["prompt"] for r in lane.rows() if "prompt" in r]
    if lane.agent == "agy":
        assert len(prompts) == 1, "ACP must not send second prompt before first response"
        wire_prompts = [
            x for x in lane.rows() if x.get("wire_received", {}).get("method") == "session/prompt"
        ]
        assert len(wire_prompts) == 1, "second ACP write was buffered during unresolved first turn"
        assert result.get("state") != "observed"
    else:
        until(
            lambda: len([r for r in lane.rows() if "prompt" in r]) == 2,
            p,
            lane.root / "wrapper-0.log",
        )
        queued = [r["prompt"] for r in lane.rows() if "prompt" in r][-1]
        assert queued["ifBusy"] == "queue"
        assert len([x for x in lane.rows() if "turn_started" in x]) == 1
        assert not any("turn_completed" in x for x in lane.rows())
    (lane.folder / "release-turn").touch()
    until(lambda: lane.send(mid).get("state") == "observed", p, lane.root / "wrapper-0.log")
    assert len([r for r in lane.rows() if "prompt" in r]) == 2


def test_followup_uncertain_write_is_durable_and_not_replayed(hosted):
    lane = hosted
    p = lane.start()
    until(
        lambda: lane.state().get("deliveries", {}).get("initial", {}).get("state") == "observed",
        p,
        lane.root / "wrapper-0.log",
    )
    until(
        lambda: "__LANE_DONE__" in (lane.root / "wrapper-0.log").read_text(),
        p,
        lane.root / "wrapper-0.log",
    )
    first = next(r for r in lane.rows() if "prompt" in r)
    assert "tmux-lanes/coder-launch-intent.json" in first["durable_before_write"]
    (lane.folder / "crash-before-ack").touch()
    mid = lane.plan("uncertain followup cannot be silently replayed")
    lane.send(mid)
    until(
        lambda: len([r for r in lane.rows() if "prompt" in r]) == 2,
        diagnostic=lane.root / "wrapper-0.log",
    )
    p.wait(timeout=10)
    follow = next(r for r in reversed(lane.rows()) if "prompt" in r)
    snapshot = json.loads(follow["durable_before_write"]["tmux-lanes/coder-persistent.json"])
    handover = snapshot["deliveries"][mid]
    assert handover["state"] in {"submitted", "unknown"}
    assert snapshot["session_id"] == follow["prompt"]["sessionId"]
    # A dialogue plan alone does not establish that this exact delivery was handed over.
    if lane.agent == "muse":
        typed = "".join(x["text"] for x in follow["prompt"]["input"])
        digest = hashlib.sha256(typed.encode()).hexdigest()
        assert handover["typed_sha256"] == "sha256:" + digest
        commands = json.loads(
            follow["durable_before_write"]["tmux-lanes/coder-hosted-commands.json"]
        )
        assert commands[digest] == follow["prompt"]["commandId"]
    (lane.folder / "crash-before-ack").unlink()
    # Public delivery retry may refuse or report unknown; neither may resubmit.
    try:
        result = lane.send(mid)
    except ValueError:
        result = {"state": "refused"}
    assert result.get("state") != "observed"
    assert len([r for r in lane.rows() if "prompt" in r]) == 2


@pytest.mark.parametrize("mode", ["config", "mode", "foreign", "missing", "nonstring"])
def test_acp_unrelated_update_then_rejection_is_not_prompt_receipt(hosted, mode):
    lane = hosted
    if lane.agent != "agy":
        pytest.skip("ACP-specific event contract")
    (lane.folder / "reject-update").write_text(mode)
    p = lane.start()
    until(lambda: any(r.get("rejected") for r in lane.rows()), p, lane.root / "wrapper-0.log")
    time.sleep(0.15)
    assert lane.state().get("deliveries", {}).get("initial", {}).get("state") != "observed"


@pytest.mark.parametrize("mode", ["missing", "nonstring"])
def test_acp_invalid_new_identity_refuses_before_prompt(hosted, mode):
    lane = hosted
    if lane.agent != "agy":
        pytest.skip("ACP-specific result contract")
    (lane.folder / "identity-mode").write_text(mode)
    p = lane.start()
    assert p.wait(timeout=10) != 0
    assert not any("prompt" in r for r in lane.rows())


@pytest.mark.parametrize("option,value", [("--model", "wanted-model"), ("--effort", "high")])
def test_acp_unselected_requested_configuration_refuses_before_prompt(hosted, option, value):
    lane = hosted
    if lane.agent != "agy":
        pytest.skip("ACP-specific configuration contract")
    (lane.folder / "config-mismatch").touch()
    # Native invocation settings are public provider args, not private config.
    lane.command.extend([option, value])
    p = lane.start()
    assert p.wait(timeout=10) != 0
    requests = [r["request"] for r in lane.rows() if "request" in r]
    assert any(r.get("method") == "session/set_config_option" for r in requests)
    assert not any("prompt" in r for r in lane.rows())


@pytest.mark.parametrize("damage", ["corrupt", "unreadable"])
def test_muse_damaged_retained_command_ledger_never_allocates_fresh_command(hosted, damage):
    lane = hosted
    if lane.agent != "muse":
        pytest.skip("MSP-specific durable command contract")
    p = lane.start()
    until(
        lambda: lane.state().get("deliveries", {}).get("initial", {}).get("state") == "observed", p
    )
    ledger = lane.folder / "coder-hosted-commands.json"
    assert ledger.is_file(), "public retained command receipt missing"
    original = ledger.read_bytes()
    if damage == "corrupt":
        ledger.write_text("{not-json")
    else:
        ledger.unlink()
        ledger.mkdir()  # deterministically unreadable as a file, including privileged test runners
    before = len([r for r in lane.rows() if "prompt" in r])
    mid = lane.plan("cannot allocate replacement id after ledger damage")
    try:
        try:
            result = lane.send(mid)
        except (ValueError, OSError):
            result = {"state": "refused"}
        time.sleep(0.15)
        assert result.get("state") != "observed"
        assert len([r for r in lane.rows() if "prompt" in r]) == before
        assert ledger.is_dir() if damage == "unreadable" else ledger.read_text() == "{not-json"
    finally:
        if ledger.is_dir():
            ledger.rmdir()
        ledger.write_bytes(original)


def test_acp_later_effort_selection_cannot_revert_requested_model(hosted):
    lane = hosted
    if lane.agent != "agy":
        pytest.skip("ACP configuration transaction")
    (lane.folder / "config-mismatch").touch()
    (lane.folder / "config-revert").touch()
    lane.command.extend(["--model", "wanted-model", "--effort", "high"])
    p = lane.start()
    assert p.wait(timeout=10) != 0
    requests = [r["request"] for r in lane.rows() if "request" in r]
    configs = [
        r["params"]["configId"] for r in requests if r.get("method") == "session/set_config_option"
    ]
    assert configs == ["model", "thought_level"]
    assert not any("prompt" in r for r in lane.rows())


def test_direct_protocol_host_strips_api_routing_without_caller_unset(hosted):
    lane = hosted
    (lane.folder / "exit-after-completion").touch()
    executable = lane.folder / lane.agent
    command = [
        sys.executable,
        str(H / "protocol_host.py"),
        "run",
        "--protocol",
        "acp" if lane.agent == "agy" else "msp",
        "--journal",
        str(lane.root / "direct-journal.jsonl"),
        "--record",
        str(lane.root / "direct-host.json"),
        "--workspace",
        str(lane.project),
        "--message",
        "synthetic direct entry",
        "--",
        str(executable),
        "acp" if lane.agent == "agy" else "serve",
    ]
    # This deliberately omits --unset: each public entry must enforce subscription routing.
    result = subprocess.run(
        command, cwd=lane.project, env=lane.env, capture_output=True, text=True, timeout=10
    )
    spawns = [r for r in lane.rows() if r.get("spawn")]
    assert spawns, result.stdout + result.stderr
    assert all(not r["keys"] for r in spawns)
