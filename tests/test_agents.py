"""Any harness in any seat: the registry, plan-only billing, operator configuration custody,
the lane wrapper's session and billing watch, and preflight."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness import agents, lane_agent, lane_watchdog

REPO = Path(__file__).resolve().parents[1]
SERVER = {"command": "/opt/kindex-lite", "args": ["--repo", "/lane"],
          "env": {"HOME": "/lane/home", "KIN_PROJECT_PATH": "/lane"}}
RECORD = {"mcp_server": SERVER}


def _home(tmp_path: Path) -> Path:
    operator = tmp_path / "operator"
    (operator / "Library").mkdir(parents=True)
    state = operator / ".gemini" / "antigravity-cli"
    state.mkdir(parents=True)
    (state / "antigravity-oauth-token").write_text("token", encoding="utf-8")
    (state / "mcp_config.json").write_text('{"mcpServers":{"kindex":{}}}', encoding="utf-8")
    (state / "settings.json").write_text("{}", encoding="utf-8")
    muse = operator / ".config" / "muse"
    muse.mkdir(parents=True)
    (muse / "auth.json").write_text('{"schema_version":1,"providers":{"meta":{}}}',
                                    encoding="utf-8")
    (muse / "settings.json").write_text('{"mcpServers":{"kindex":{}}}', encoding="utf-8")
    return operator


def test_registry_lists_every_harness_the_lanes_accept() -> None:
    for name in ("claude", "claude-ollama", "codex", "agy", "muse", "prime-agent",
                 "cursor-agent"):
        assert name in agents.HARNESSES
    assert lane_agent.agent("claude").auth == "claude-plan"
    assert lane_agent.agent("claude-ollama").auth == "ollama-server"
    assert lane_agent.agent("agy").auth == "agy-plan"
    assert lane_agent.agent("prime-agent").auth == "registry"
    # Metered by construction: a prime-agent lane needs a spend cap even without --metered.
    assert lane_agent.agent("prime-agent").metered
    assert not any(lane_agent.agent(name).metered
                   for name in ("claude", "claude-ollama", "agy", "muse", "cursor-agent"))
    # Every registry harness tmux_lane.sh routes through lane-setup is one lane_agent knows.
    for name in agents.HARNESSES:
        if name != "codex":
            assert name in lane_agent.AGENTS
    # Every lane the watchdog may pause and resume is one the registry can resume.
    for name in lane_watchdog.RESUMABLE_AGENTS - {"codex", "codex-ollama"}:
        assert agents.harness(name).resumable


def test_turn_cli_describes_native_terminal_without_print_mode(tmp_path: Path) -> None:
    out = subprocess.run(
        [sys.executable, str(REPO / "harness" / "agents.py"), "turn", "--agent", "claude",
         "--model", "claude-haiku-5-5", "--effort", "xhigh", "--cwd", str(tmp_path),
         "--extra-arg=--strict-mcp-config"],
        capture_output=True, check=True,
    ).stdout.decode().split("\0")
    assert out[0] == "terminal" and out[1] == "claude"
    assert "claude-haiku-5-5" in out and "xhigh" in out and "--strict-mcp-config" in out
    assert "--output-format" not in out and "--print" not in out and "-p" not in out


@pytest.mark.parametrize(("name", "flag"), [
    ("claude", "--resume"), ("claude-ollama", "--resume"), ("muse", "resume"),
    ("agy", "--conversation"),
])
def test_every_registry_harness_resumes_its_session(name: str, flag: str, tmp_path: Path) -> None:
    made = agents.harness(name).turn(model="m", effort="", cwd=tmp_path, resume="sess-1234")
    assert made.argv[made.argv.index(flag) + 1] == "sess-1234"
    if name == "agy":
        assert made.argv[-1] == "--print"  # the prompt follows it


def test_claude_ollama_is_native_claude_code_on_the_loopback_server(tmp_path: Path) -> None:
    made = agents.harness("claude-ollama").turn(model="glm-5.3:cloud", effort="", cwd=tmp_path)
    assert made.argv[0] == "claude"
    assert "-p" not in made.argv and "--print" not in made.argv
    env = agents.harness("claude-ollama").seat_env("glm-5.3:cloud")
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:11434"
    assert "ANTHROPIC_API_KEY" not in env
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "glm-5.3:cloud"
    with pytest.raises(ValueError):
        agents.harness("claude-ollama").seat_env("")


def test_a_plan_lane_never_admits_an_api_key() -> None:
    claude = agents.harness("claude")
    assert "ANTHROPIC_API_KEY" not in claude.admitted_env("plan")
    with pytest.raises(ValueError):
        claude.admitted_env("metered")
    assert "META_API_KEY" not in agents.harness("muse").admitted_env("plan")
    assert "CURSOR_API_KEY" not in agents.harness("cursor-agent").admitted_env("plan")
    with pytest.raises(ValueError):
        agents.harness("prime-agent").admitted_env("plan")


def test_lane_setup_writes_only_admitted_variables(tmp_path: Path) -> None:
    operator = _home(tmp_path)
    env_file = tmp_path / "lane" / "creds.env"
    environ = {"HOME": str(operator), "ANTHROPIC_API_KEY": "sk-should-not-travel",
               "CLAUDE_CONFIG_DIR": "/cfg"}
    setup = agents.prepare_lane("claude", billing="plan", home=tmp_path / "lane" / "home",
                                model="claude-opus-5-5", kindex_record=RECORD,
                                env_file=env_file, environ=environ)
    text = env_file.read_text(encoding="utf-8")
    assert "sk-should-not-travel" not in text and "CLAUDE_CONFIG_DIR=/cfg" in text
    assert env_file.stat().st_mode & 0o077 == 0
    config = Path(setup.argv[setup.argv.index("--mcp-config") + 1])
    assert json.loads(config.read_text())["mcpServers"] == {
        "kindex": {"command": SERVER["command"], "args": SERVER["args"], "env": SERVER["env"]}}
    assert "--strict-mcp-config" not in setup.argv
    assert "--setting-sources" not in setup.argv
    rejected_file = tmp_path / "m" / "creds.env"
    with pytest.raises(ValueError):
        agents.prepare_lane("claude", billing="metered", home=tmp_path / "m" / "home",
                            model="", kindex_record=RECORD,
                            env_file=rejected_file, environ=environ)
    assert not rejected_file.exists()



def test_muse_setup_preserves_operator_home_and_adds_only_its_scoped_mcp(tmp_path: Path) -> None:
    # Native Muse uses the operator's account and settings. The lane may add a
    # scoped server, not substitute a private HOME/config or rewrite sign-in.
    operator = _home(tmp_path)
    config = operator / ".config" / "muse"
    (config / "auth.json").write_text("opaque native credential format: do not parse")
    (config / "hooks.json").write_text('{"operator_hook": "retain"}')
    original = {path: path.read_bytes() for path in config.iterdir()}
    custom_xdg = tmp_path / "operator-xdg"
    custom_xdg.mkdir()
    environ = {"HOME": str(operator), "XDG_CONFIG_HOME": str(custom_xdg),
               "META_API_KEY": "synthetic-must-not-travel"}
    lane_home = tmp_path / "lane"
    env_file = tmp_path / "creds.env"
    setup = agents.prepare_lane("muse", billing="plan", home=lane_home,
                                model="muse-wonder-1.0", kindex_record=RECORD,
                                env_file=env_file, environ=environ)
    effective = {**environ, **setup.env}
    assert effective["HOME"] == str(operator)
    assert effective["XDG_CONFIG_HOME"] == str(custom_xdg)
    assert {path: path.read_bytes() for path in config.iterdir()} == original
    assert not any(path.name == "auth.json" for path in lane_home.rglob("*"))
    assert "META_API_KEY" not in setup.env
    assert "synthetic-must-not-travel" not in env_file.read_text()
    assert env_file.stat().st_mode & 0o077 == 0
    configs = [setup.argv[index + 1] for index, value in enumerate(setup.argv)
               if value == "-c"]
    assert len(configs) == 1
    key, encoded = configs[0].split("=", 1)
    assert key == "mcpServers.lane-kindex"
    server = json.loads(encoded)
    # Muse needs server environment assignments in its command, not a literal
    # env table. The launched server must still receive this exact lane scope.
    assert server["command"] == "/usr/bin/env"
    command_index = server["args"].index(SERVER["command"])
    assert set(server["args"][:command_index]) == {
        f"{key}={value}" for key, value in SERVER["env"].items()
    }
    assert server["args"][command_index + 1:] == SERVER["args"]
    assert "env" not in server


def test_muse_turn_is_native_interactive_without_automatic_factory_resume(tmp_path: Path) -> None:
    # CLI description is a public interface; this does not launch a model.
    out = subprocess.run(
        [sys.executable, str(REPO / "harness" / "agents.py"), "turn", "--agent", "muse",
         "--model", "operator-selected-model", "--cwd", str(tmp_path)],
        capture_output=True, check=True,
    ).stdout.decode().split("\0")
    assert out[0] == "terminal" and out[1] == "muse"
    assert out[out.index("--workspace") + 1] == str(tmp_path)
    assert out[out.index("--model") + 1] == "operator-selected-model"
    assert not {"exec", "--output-format", "--print", "--json", "--yolo",
                "--disable-request-user-input"}.intersection(out)
    assert not agents.harness("muse").resumable
    assert "muse" not in lane_watchdog.RESUMABLE_AGENTS


def test_cursor_preserves_operator_home_and_config(tmp_path: Path) -> None:
    operator = _home(tmp_path)
    config = operator / ".cursor"
    config.mkdir()
    settings = config / "cli.json"
    settings.write_text('{"operator": "keep unchanged"}')
    environ = {"HOME": str(operator), "CURSOR_CONFIG_DIR": str(config)}
    cursor = agents.prepare_lane("cursor-agent", billing="plan", home=tmp_path / "cursor",
                                 model="grok-4.7-xhigh", kindex_record=RECORD,
                                 env_file=tmp_path / "c.env", environ=environ)
    effective = {**environ, **cursor.env}
    assert effective["HOME"] == str(operator)
    assert effective["CURSOR_CONFIG_DIR"] == str(config)
    assert settings.read_text() == '{"operator": "keep unchanged"}'
    assert not (tmp_path / "cursor" / "Library").exists()


def test_agy_lane_gets_a_private_home_that_keeps_the_keychain(tmp_path: Path) -> None:
    operator = _home(tmp_path)
    environ = {"HOME": str(operator)}
    agy = agents.prepare_lane("agy", billing="plan", home=tmp_path / "agy", model="",
                              kindex_record=RECORD, env_file=tmp_path / "a.env",
                              environ=environ)
    state = Path(agy.env["HOME"]) / ".gemini"
    assert (state / "antigravity-cli" / "antigravity-oauth-token").is_symlink()
    assert not (state / "antigravity-cli" / "mcp_config.json").is_symlink()
    for path in (state / "antigravity-cli" / "mcp_config.json",
                 state / "config" / "mcp_config.json"):
        assert list(json.loads(path.read_text())["mcpServers"]) == ["kindex"]
    assert (state / "config" / ".migrated").exists()
    # A relaunch rebuilds the same home in place.
    agents.prepare_lane("agy", billing="plan", home=tmp_path / "agy", model="",
                        kindex_record=RECORD, env_file=tmp_path / "a.env", environ=environ)


# First streamed lines, as each harness printed them (2026-10-16, values shortened).
STREAMS = {
    "claude": ('{"type":"system","subtype":"init","session_id":"3ad997b3-721c-44df-9c17-'
               'e19d0f2617fc","apiKeySource":"none"}', "3ad997b3-721c-44df-9c17-e19d0f2617fc"),
    "cursor-agent": ('{"type":"system","subtype":"init","apiKeySource":"login","session_id":'
                     '"7e565b43-db46-4bbe-b991-8d180c2d29c7"}',
                     "7e565b43-db46-4bbe-b991-8d180c2d29c7"),
    "agy": ('{"event":"init","conversation_id":"63cad11c-be83-4b67-b890-353b3a784d62"}',
            "63cad11c-be83-4b67-b890-353b3a784d62"),

}


@pytest.mark.parametrize("name", sorted(STREAMS))
def test_each_harness_announces_its_session_in_its_first_event(name: str) -> None:
    line, session = STREAMS[name]
    assert agents.harness(name).session_in(line) == session


def test_billing_source_is_read_from_the_init_event() -> None:
    claude, cursor = agents.harness("claude"), agents.harness("cursor-agent")
    assert claude.billing_in(STREAMS["claude"][0]) == "plan"
    assert claude.billing_in('{"type":"system","subtype":"init","apiKeySource":'
                             '"ANTHROPIC_API_KEY"}') == "api"
    assert cursor.billing_in(STREAMS["cursor-agent"][0]) == "plan"
    assert cursor.billing_in('{"type":"system","subtype":"init","apiKeySource":"env"}') == "api"
    assert agents.harness("claude-ollama").billing_in(
        '{"type":"system","subtype":"init","apiKeySource":"ANTHROPIC_API_KEY"}') == ""


def test_outcomes_read_each_harness_final_event() -> None:
    agy = agents.harness("agy").outcome(
        STREAMS["agy"][0] + "\n" + '{"event":"step_update","step_update":{"step_type":'
        '"agent_response","text_delta":"OK\\n","usage":{"input_tokens":3,"output_tokens":1}}}')
    assert agy.text == "OK\n" and agy.session == STREAMS["agy"][1]
    assert agy.tokens["input"] == 3
    cursor = agents.harness("cursor-agent").outcome(
        '{"type":"result","result":"OK","session_id":"s-123456789","usage":{"inputTokens":5}}')
    assert cursor.text == "OK" and cursor.session == "s-123456789"


def test_credentials_reach_the_child_only_through_an_owner_only_file(tmp_path: Path) -> None:
    creds = tmp_path / "creds.env"
    creds.write_text("ANTHROPIC_API_KEY=sk-test-value\n", encoding="utf-8")
    creds.chmod(0o600)
    probe = tmp_path / "probe.out"
    rc = lane_agent.run_lane(
        [sys.executable, "-c",
         f"import os; open({str(probe)!r}, 'w').write(os.environ.get('ANTHROPIC_API_KEY', ''))"],
        slot="coder", log=tmp_path / "lane.log", exits=tmp_path / "exits.jsonl", env_file=creds,
    )
    assert rc == 0 and probe.read_text() == "sk-test-value"
    assert "sk-test-value" not in (tmp_path / "lane.log").read_text()
    creds.chmod(0o644)
    with pytest.raises(ValueError):
        lane_agent.run_lane([sys.executable, "-c", "pass"], slot="coder",
                            log=tmp_path / "lane.log", exits=tmp_path / "exits.jsonl",
                            env_file=creds)


def _streaming_child(lines: list[str]) -> list[str]:
    body = "import sys, time\n" + "".join(
        f"print({line!r}, flush=True)\n" for line in lines) + "time.sleep(30)\n"
    return [sys.executable, "-c", body]


@pytest.mark.parametrize(("agent", "variable"), [
    (name, variable) for name, harness in agents.HARNESSES.items() if harness.plan
    for variable in harness.api_env
])
@pytest.mark.parametrize("source", ["inherited", "env-file"])
def test_plan_wrapper_refuses_api_route_before_child_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: str, variable: str, source: str,
) -> None:
    marker = tmp_path / "started"
    secret = "synthetic-credential-not-for-logs"
    env_file = None
    if source == "inherited":
        monkeypatch.setenv(variable, secret)
    else:
        env_file = tmp_path / "creds.env"
        env_file.write_text(f"{variable}={secret}\n", encoding="utf-8")
        env_file.chmod(0o600)
    if agent == "claude" and source == "inherited":
        setup = agents.prepare_lane("claude", billing="plan", home=tmp_path / "lane",
                                    model="", kindex_record=RECORD,
                                    env_file=tmp_path / "clean.env",
                                    environ={"HOME": str(tmp_path), variable: secret})
        assert variable not in setup.env
        assert secret not in (tmp_path / "clean.env").read_text()
        assert not marker.exists()
        return
    native_args = {}
    if agent == "claude":
        prompt = tmp_path / "prompt"
        prompt.write_text("Synthetic native admission test")
        native_args = {"prompt": prompt, "persistent": True, "run_root": tmp_path,
                       "session_file": tmp_path / "session"}
    with pytest.raises(ValueError, match="API-routing variables|credential file") as raised:
        lane_agent.run_lane(
            [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
            slot="coder", log=tmp_path / "lane.log", exits=tmp_path / "exits.jsonl",
            session_agent=agent, plan_only=True, env_file=env_file, **native_args,
        )
    assert variable in str(raised.value) and secret not in str(raised.value)
    assert not marker.exists()
    assert not (tmp_path / "coder-process.json").exists()
    assert not (tmp_path / "exits.jsonl").exists()


@pytest.mark.parametrize("agent", ["", "prime-agent"])
def test_plan_wrapper_requires_a_subscription_capable_identity(tmp_path: Path, agent: str) -> None:
    with pytest.raises(ValueError, match="session agent|signed-in plan"):
        lane_agent.run_lane(
            [sys.executable, "-c", "raise AssertionError('must not start')"],
            slot="coder", log=tmp_path / "lane.log", exits=tmp_path / "exits.jsonl",
            session_agent=agent, plan_only=True,
        )


def test_cursor_stream_wrapper_refuses_without_starting(tmp_path: Path) -> None:
    marker = tmp_path / "started"
    session_file = tmp_path / "coder-thread-id"
    with pytest.raises(ValueError, match="native terminal"):
        lane_agent.run_lane(
            [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
            slot="coder", log=tmp_path / "lane.log", exits=tmp_path / "exits.jsonl",
            session_agent="cursor-agent", session_file=session_file, plan_only=True,
        )
    assert not marker.exists()
    assert not session_file.exists()


def test_cursor_does_not_silently_replace_a_requested_resume(tmp_path: Path) -> None:
    cursor = agents.harness("cursor-agent")
    assert not cursor.resumable
    # Factory does not promise automatic lane restart, but an explicitly
    # requested native resume must retain the exact conversation identity.
    made = cursor.turn(model="", effort="", cwd=tmp_path, resume="existing-session")
    assert "--resume=existing-session" in made.argv or (
        "--resume" in made.argv
        and made.argv[made.argv.index("--resume") + 1] == "existing-session"
    )


@pytest.mark.parametrize("plan_only", [True, False])
def test_claude_never_falls_back_to_a_noninteractive_stream(tmp_path, plan_only):
    with pytest.raises(ValueError, match="native terminal"):
        lane_agent.run_lane(
            _streaming_child(['{"type":"system","subtype":"init"}']),
            slot="coder", log=tmp_path / "lane.log", exits=tmp_path / "exits.jsonl",
            session_agent="claude", session_file=tmp_path / "t", plan_only=plan_only,
        )
    assert not (tmp_path / "exits.jsonl").exists()


def _runner(stdout: str, rc: int = 0) -> lane_agent.Runner:
    def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, rc, stdout, "")
    return runner


def test_claude_preflight_proves_a_plan_sign_in(tmp_path: Path) -> None:
    native = tmp_path / "claude"
    native.write_text("#!/bin/sh\nexit 0\n")
    native.chmod(0o755)
    environ = {"HOME": str(tmp_path), "PATH": str(tmp_path)}
    plan = json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiKeySource": None})
    assert lane_agent.preflight("claude", environ=environ, runner=_runner(plan)) == \
        "claude-plan:claude.ai"
    api = json.dumps({"loggedIn": True, "authMethod": "api_key",
                      "apiKeySource": "ANTHROPIC_API_KEY"})
    with pytest.raises(lane_agent.PreflightRefused, match="API key"):
        lane_agent.preflight("claude", environ=environ, runner=_runner(api))
    with pytest.raises(lane_agent.PreflightRefused, match="not signed in"):
        lane_agent.preflight("claude", environ=environ, runner=_runner('{"loggedIn": false}'))


def test_muse_preflight_uses_native_usage_and_never_parses_private_sign_in(tmp_path: Path) -> None:
    # The real subprocess seam must invoke Muse's account command, not trust a
    # private file schema or an injected runner that succeeds for every command.
    operator = _home(tmp_path)
    auth = operator / ".config" / "muse" / "auth.json"
    auth.write_text("native opaque sign-in, deliberately not JSON")
    original = auth.read_bytes()
    binary = tmp_path / "bin"
    binary.mkdir()
    native = binary / "muse"
    calls = tmp_path / "usage-calls"
    signed_out = tmp_path / "signed-out"
    native.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "if sys.argv[1:] != ['usage']: raise SystemExit(97)\n"
        f"with pathlib.Path({str(calls)!r}).open('a') as out:\n"
        "    out.write(json.dumps(dict(argv=sys.argv[1:], home=os.environ.get('HOME'))) + '\\n')\n"
        f"if pathlib.Path({str(signed_out)!r}).exists():\n"
        "    print('Please sign in', file=sys.stderr)\n"
        "    raise SystemExit(1)\n"
        "print('Plan account active')\n"
    )
    native.chmod(0o755)
    environ = {"HOME": str(operator), "PATH": str(binary)}
    lane_agent.preflight("muse", environ=environ)
    assert [json.loads(line) for line in calls.read_text().splitlines()] == [
        {"argv": ["usage"], "home": str(operator)}
    ]
    assert auth.read_bytes() == original
    with pytest.raises(lane_agent.PreflightRefused, match="META_API_KEY") as rejected:
        lane_agent.preflight("muse", environ={**environ, "META_API_KEY": "synthetic-only"})
    assert "synthetic-only" not in str(rejected.value)
    assert len(calls.read_text().splitlines()) == 1  # no paid routing even for the check
    signed_out.touch()
    with pytest.raises(lane_agent.PreflightRefused):
        lane_agent.preflight("muse", environ=environ)
    assert len(calls.read_text().splitlines()) == 2
    assert auth.read_bytes() == original


def test_agy_preflight_refuses_a_signed_out_cli() -> None:
    assert lane_agent.preflight("agy", environ=dict(os.environ),
                                runner=_runner("gemini-4-argon-high\tGemini")) == "agy-models"
    with pytest.raises(lane_agent.PreflightRefused):
        lane_agent.preflight("agy", environ={}, runner=_runner(
            "Error: Please sign in to view available models.", rc=1))


def test_done_claim_requires_a_typed_agent_response() -> None:
    done = "__LANE_DONE__ coder commits=3"
    for line in (
        json.dumps({"payload_type": "run.terminal.completed", "payload": {"text": f"ok\n{done}"}}),
        json.dumps({"type": "result", "result": done}),
        json.dumps({"event": "step_update", "step_update": {
            "step_type": "agent_response", "text_delta": done + "\n"}}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": done}}),
        # Muse can run the next message straight on with no newline after the count.
        json.dumps({"payload_type": "run.terminal.completed",
                    "payload": {"text": f"ok\n{done}Top finding: all items hold."}}),
    ):
        assert lane_watchdog._done_in(line)
    # The protocol's unfilled form, echoed back in a prompt event, is not a completion.
    assert not lane_watchdog._done_in(json.dumps(
        {"payload": {"prompt": "end with __LANE_DONE__ coder commits=<n>"}}))
    assert not lane_watchdog._done_in("__LANE_DONE__ coder commits=")
    for event in (
        {"type": "tool_result", "content": done},
        {"type": "item.completed", "item": {"type": "command_execution", "output": done}},
        {"type": "result", "is_error": True, "result": done},
        {"event": "step_update", "step_update": {"step_type": "tool_result", "text_delta": done}},
        {"payload": {"text": done}},
        {"type": "user", "content": done},
    ):
        assert not lane_watchdog._done_in(json.dumps(event))
    assert not lane_watchdog._done_in(done)
    assert lane_watchdog._done_in(json.dumps({"type": "result", "result": done}), slot="coder")
    assert not lane_watchdog._done_in(json.dumps({"type": "result", "result": done}), slot="tester")
    assert not lane_watchdog._done_in(json.dumps({"type": "result", "result": done}) + "\n{\n")


def test_a_plan_codex_lane_refuses_an_api_key_sign_in(tmp_path: Path) -> None:
    environ = {"HOME": str(tmp_path), "CODEX_HOME": str(tmp_path / ".codex")}
    assert lane_agent.preflight("codex", environ=environ,
                                runner=_runner("Logged in using ChatGPT")) == "codex-login-status"
    with pytest.raises(lane_agent.PreflightRefused, match="API key"):
        lane_agent.preflight("codex", environ=environ,
                             runner=_runner("Logged in using an API key - sk-***"))
    assert lane_agent.preflight("codex", environ=environ, billing="metered",
                                runner=_runner("Logged in using an API key")) == \
        "codex-login-status"


def test_worker_identity_failure_terminates_only_the_owned_child(tmp_path, monkeypatch):
    children = []
    original = lane_agent.subprocess.Popen

    def record_child(*args, **kwargs):
        child = original(*args, **kwargs)
        # _started uses subprocess.run internally; only retain the actual sleeper.
        if args[0][:2] == [sys.executable, '-c']:
            children.append(child)
        return child

    monkeypatch.setattr(lane_agent.subprocess, 'Popen', record_child)
    monkeypatch.setattr(lane_agent, '_started', lambda pid: '')
    rc = lane_agent.run_lane([sys.executable, '-c', 'import time; time.sleep(30)'],
                             slot='coder', log=tmp_path/'lane.log', exits=tmp_path/'exit.jsonl')
    assert rc == 127 and len(children) == 1 and children[0].poll() is not None
    assert 'cannot publish worker process identity' in (tmp_path/'lane.log').read_text()


def test_a_lane_reads_start_times_from_the_kernel_when_ps_cannot_run(tmp_path, monkeypatch):
    # A macOS sandbox refuses to exec ps, which is setuid root. That stopped a Validator at
    # startup (collection-bootstrap-20261105-r2); the kernel answers with the same text.
    gone = subprocess.Popen([sys.executable, '-c', 'pass'])
    gone.wait()
    try:
        expected = subprocess.run(['ps', '-o', 'lstart=', '-p', str(os.getpid())],
                                  capture_output=True, text=True, check=True,
                                  env={**os.environ, 'TZ': 'UTC', 'LC_ALL': 'C',
                                       'LANG': 'C'}).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip('needs a runnable ps to compare the kernel against')
    real_run = lane_agent.subprocess.run

    def refuse_ps(argv, *args, **kwargs):
        if argv[0] == 'ps':
            raise PermissionError(1, 'Operation not permitted', 'ps')
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(lane_agent.subprocess, 'run', refuse_ps)
    assert expected and lane_agent._started(os.getpid()) == expected
    assert lane_agent._started(gone.pid) == ''


@pytest.mark.skipif(sys.platform != 'darwin', reason='a macOS sandbox')
def test_a_lane_registers_inside_a_macos_sandbox(tmp_path):
    # Even a sandbox that allows everything refuses setuid binaries such as ps.
    lane = [sys.executable, str(REPO / 'harness/lane_agent.py'), 'run', '--slot', 'validator',
            '--terminal', '--log', str(tmp_path / 'lane.log'), '--exits',
            str(tmp_path / 'exits.jsonl'), '--', sys.executable, '-c',
            'import time; time.sleep(1)']
    result = subprocess.run(['sandbox-exec', '-p', '(version 1)(allow default)', *lane],
                            capture_output=True, text=True, stdin=subprocess.DEVNULL,
                            env={**os.environ, 'PYTHONPATH': str(REPO)}, timeout=60)
    assert result.returncode == 0, (tmp_path / 'lane.log').read_text()
    receipt = json.loads((tmp_path / 'validator-process.json').read_text())
    assert receipt['status'] == 'running'
    assert receipt['wrapper']['started'] and receipt['worker']['started']


def test_a_started_agent_the_lane_stops_is_not_reported_as_a_launch_failure(tmp_path,
                                                                           monkeypatch):
    monkeypatch.setattr(lane_agent, '_started', lambda pid: '')
    rc = lane_agent.run_lane([sys.executable, '-c', 'import time; time.sleep(30)'],
                             slot='validator', log=tmp_path/'lane.log',
                             exits=tmp_path/'exit.jsonl')
    log = (tmp_path/'lane.log').read_text()
    assert rc == 127 and 'could not start' not in log
    assert f'lane-agent: {sys.executable} started as pid ' in log
    assert 'but the lane failed: cannot publish worker process identity' in log


def test_ready_fires_once_after_the_receipt_or_after_the_exit(tmp_path, monkeypatch):
    seen = []

    def ready():
        seen.append(((tmp_path/'validator-process.json').exists(),
                     (tmp_path/'exits.jsonl').exists()))

    lane = dict(slot='validator', log=tmp_path/'lane.log', exits=tmp_path/'exits.jsonl',
                terminal=True, ready=ready)
    assert lane_agent.run_lane([sys.executable, '-c', 'import time; time.sleep(1)'],
                               **lane) == 0
    assert seen == [(True, False)]
    seen.clear()
    (tmp_path/'validator-process.json').unlink()
    (tmp_path/'exits.jsonl').unlink()
    monkeypatch.setattr(lane_agent, '_started', lambda pid: '')
    assert lane_agent.run_lane([sys.executable, '-c', 'import time; time.sleep(30)'],
                               **lane) == 127
    assert seen == [(False, True)]


def test_an_agent_that_exits_before_it_registers_is_announced_after_its_exit(tmp_path,
                                                                           monkeypatch):
    # Keep the exited child unreaped: a receipt alone must not wake the launcher before
    # its actual exit is recorded. macOS lacks waitid(WNOWAIT), so observe NOTE_EXIT.
    import select
    import socket
    from contextlib import closing

    real_started = lane_agent._started
    with socket.socket() as gate:
        gate.bind(("127.0.0.1", 0))
        gate.listen(1)
        gate.settimeout(5)

        def release_child():
            with gate.accept()[0] as child:
                child.settimeout(5)
                child.sendall(b"x")

        def exited_first(pid):
            if pid != os.getpid():
                if hasattr(os, "waitid"):
                    release_child()
                    os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
                else:
                    with closing(select.kqueue()) as events:
                        watch = select.kevent(pid, filter=select.KQ_FILTER_PROC,
                                              flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                                              fflags=select.KQ_NOTE_EXIT)
                        # Register before releasing the child; neither observation reaps it.
                        events.control([watch], 0, 0)
                        release_child()
                        observed = events.control(None, 1, 5)
                        assert len(observed) == 1
                        assert observed[0].ident == pid
                        assert observed[0].fflags & select.KQ_NOTE_EXIT
            return real_started(pid)

        monkeypatch.setattr(lane_agent, '_started', exited_first)
        seen = []
        command = (
            "import socket; "
            f"gate = socket.create_connection({gate.getsockname()!r}, timeout=5); "
            "assert gate.recv(1) == b'x'; gate.close(); raise SystemExit(3)"
        )
        rc = lane_agent.run_lane(
            [sys.executable, '-c', command], slot='validator', terminal=True,
            log=tmp_path/'lane.log', exits=tmp_path/'exits.jsonl',
            ready=lambda: seen.append((tmp_path/'exits.jsonl').exists()))
    assert rc == 3 and seen == [True]


def test_a_start_time_of_dash_or_the_epoch_is_no_start_time(monkeypatch):
    monkeypatch.setattr(lane_agent.subprocess, 'run', lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 0, '-\n', ''))
    assert lane_agent._started(os.getpid()) == ''
    if sys.platform == 'darwin':
        import ctypes
        import ctypes.util

        class Epoch:
            def sysctl(self, mib, count, info, size, new, new_size):
                ctypes.memset(info, 0, 8)
                return 0

        # find_library loads libraries itself on some versions, so it cannot see the fake.
        monkeypatch.setattr(ctypes.util, 'find_library', lambda name: 'libc')
        monkeypatch.setattr(ctypes, 'CDLL', lambda *args, **kwargs: Epoch())
        assert lane_agent._kernel_started(os.getpid()) == ''


def test_a_lane_refused_before_it_runs_records_its_exit_and_wakes_the_launcher(tmp_path,
                                                                             monkeypatch):
    signals = []
    real_run = lane_agent.subprocess.run

    def tmux(argv, *args, **kwargs):
        if argv[0] == 'tmux':
            signals.append(argv)
            return subprocess.CompletedProcess(argv, 0, b'', b'')
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(lane_agent.subprocess, 'run', tmux)
    monkeypatch.setenv('FACTORY_TMUX_SOCKET', 'factory-fixture')
    rc = lane_agent.main(['run', '--slot', 'validator', '--log', str(tmp_path/'lane.log'),
                          '--exits', str(tmp_path/'exits.jsonl'), '--ready-channel', 'ch', '--'])
    assert rc == 70
    assert 'lane-agent: lane command is empty' in (tmp_path/'lane.log').read_text()
    row = json.loads((tmp_path/'exits.jsonl').read_text())
    assert row['slot'] == 'validator' and row['rc'] == 70
    assert signals == [['tmux', '-L', 'factory-fixture', 'wait-for', '-S', 'ch']]


def test_claude_preflight_never_falls_back_to_an_ambient_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing launcher in the supplied environment cannot borrow our account."""
    ambient = tmp_path / "ambient"
    selected = tmp_path / "selected"
    ambient.mkdir()
    selected.mkdir()
    marker = tmp_path / "wrong-account-probed"
    native = ambient / "claude"
    native.write_text(
        "#!/bin/sh\n"
        f"printf probed > '{marker}'\n"
        "printf '%s\\n' '{\"loggedIn\":true,\"authMethod\":\"claude.ai\","
        "\"apiKeySource\":null}'\n",
        encoding="utf-8",
    )
    native.chmod(0o755)
    monkeypatch.setenv("PATH", str(ambient))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    supplied = {"HOME": str(tmp_path / "selected-home"), "PATH": str(selected)}
    with pytest.raises(lane_agent.PreflightRefused):
        lane_agent.preflight("claude", environ=supplied)
    assert not marker.exists(), "preflight probed the ambient account instead of refusing"


@pytest.mark.parametrize("command", ["lane-setup", "seat-env", "credential-env"])
def test_native_claude_setup_refuses_metered_before_writing_credentials(
    tmp_path: Path, command: str,
) -> None:
    """Setup cannot advertise or persist a route the native runtime forbids."""
    env_file = tmp_path / "credentials.env"
    argv = [sys.executable, str(REPO / "harness" / "agents.py"), command,
            "--agent", "claude", "--billing", "metered"]
    if command != "credential-env":
        argv += ["--env-file", str(env_file)]
    if command == "lane-setup":
        argv += ["--home", str(tmp_path / "lane-home")]
    sentinel = "synthetic-not-a-real-api-key"
    environ = {**os.environ, "HOME": str(tmp_path), "ANTHROPIC_API_KEY": sentinel}
    result = subprocess.run(argv, env=environ, capture_output=True, text=True, check=False)
    assert result.returncode != 0, "native Claude setup incorrectly admitted metered billing"
    assert not env_file.exists(), "rejected setup must not persist credentials"
    assert sentinel not in result.stdout + result.stderr
