"""Any harness in any seat: the registry, plan-only billing, each lane's private configuration,
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
    assert lane_agent.agent("muse").auth == "muse-plan"
    assert lane_agent.agent("prime-agent").auth == "registry"
    # Every registry harness tmux_lane.sh routes through lane-setup is one lane_agent knows.
    for name in agents.HARNESSES:
        if name != "codex":
            assert name in lane_agent.AGENTS
    # Every lane the watchdog may pause and resume is one the registry can resume.
    for name in lane_watchdog.RESUMABLE_AGENTS - {"codex", "codex-ollama"}:
        assert agents.harness(name).resumable


def test_turn_cli_prints_nul_separated_argv(tmp_path: Path) -> None:
    out = subprocess.run(
        [sys.executable, str(REPO / "harness" / "agents.py"), "turn", "--agent", "claude",
         "--model", "claude-haiku-5-5", "--effort", "xhigh", "--cwd", str(tmp_path),
         "--extra-arg=--strict-mcp-config"],
        capture_output=True, check=True,
    ).stdout.decode().split("\0")
    assert out[0] == "argv" and out[1] == "claude"
    assert "claude-haiku-5-5" in out and "xhigh" in out and "--strict-mcp-config" in out
    assert out[out.index("--output-format") + 1] == "stream-json"


@pytest.mark.parametrize(("name", "flag"), [
    ("claude", "--resume"), ("claude-ollama", "--resume"), ("muse", "--session-id"),
    ("cursor-agent", "--resume"), ("agy", "--conversation"),
])
def test_every_registry_harness_resumes_its_session(name: str, flag: str, tmp_path: Path) -> None:
    made = agents.harness(name).turn(model="m", effort="", cwd=tmp_path, resume="sess-1234")
    assert made.argv[made.argv.index(flag) + 1] == "sess-1234"
    if name == "agy":
        assert made.argv[-1] == "--print"  # the prompt follows it


def test_claude_ollama_is_bare_claude_code_on_the_loopback_server(tmp_path: Path) -> None:
    made = agents.harness("claude-ollama").turn(model="glm-5.3:cloud", effort="", cwd=tmp_path)
    assert made.argv[:3] == ("claude", "-p", "--bare")
    env = agents.harness("claude-ollama").seat_env("glm-5.3:cloud")
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:11434"
    assert env["ANTHROPIC_API_KEY"] == "ollama"
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "glm-5.3:cloud"
    with pytest.raises(ValueError):
        agents.harness("claude-ollama").seat_env("")


def test_a_plan_lane_never_admits_an_api_key() -> None:
    claude = agents.harness("claude")
    assert "ANTHROPIC_API_KEY" not in claude.admitted_env("plan")
    assert "ANTHROPIC_API_KEY" in claude.admitted_env("metered")
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
    assert "--strict-mcp-config" in setup.argv
    assert setup.argv[setup.argv.index("--setting-sources") + 1] == "project,local"
    metered = agents.prepare_lane("claude", billing="metered", home=tmp_path / "m" / "home",
                                  model="", kindex_record=RECORD,
                                  env_file=tmp_path / "m" / "creds.env", environ=environ)
    assert metered.argv and "sk-should-not-travel" in (tmp_path / "m" / "creds.env").read_text()


def test_muse_lane_sees_only_the_lane_kindex(tmp_path: Path) -> None:
    operator = _home(tmp_path)
    setup = agents.prepare_lane("muse", billing="plan", home=tmp_path / "lane",
                                model="muse-wonder-1.0", kindex_record=RECORD,
                                env_file=tmp_path / "creds.env",
                                environ={"HOME": str(operator), "META_API_KEY": "mk-no"})
    config = Path(setup.env["XDG_CONFIG_HOME"]) / "muse"
    settings = json.loads((config / "settings.json").read_text())
    assert list(settings["mcpServers"]) == ["kindex"]
    assert settings["mcpServers"]["kindex"]["mode"] == "optional"
    assert "mcp_servers" not in settings  # both keys together drop the whole MCP member
    assert (config / "auth.json").is_symlink()
    assert "mk-no" not in (tmp_path / "creds.env").read_text()


def test_cursor_and_agy_lanes_get_a_private_home_that_keeps_the_keychain(tmp_path: Path) -> None:
    operator = _home(tmp_path)
    environ = {"HOME": str(operator)}
    cursor = agents.prepare_lane("cursor-agent", billing="plan", home=tmp_path / "cursor",
                                 model="grok-4.7-xhigh", kindex_record=RECORD,
                                 env_file=tmp_path / "c.env", environ=environ)
    private = Path(cursor.env["HOME"])
    assert (private / "Library").resolve() == (operator / "Library").resolve()
    assert list(json.loads((private / ".cursor" / "mcp.json").read_text())["mcpServers"]) == \
        ["kindex"]
    assert cursor.env["CURSOR_CONFIG_DIR"] == str(private / ".cursor")

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
    "muse": ('{"schema_version":1,"stream":{"kind":"session","id":"01a14609-38d9-7751-9cca-'
             'b4f193d1ec5f"},"payload_type":"runtime.command.accepted","payload":{}}',
             "01a14609-38d9-7751-9cca-b4f193d1ec5f"),
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
    muse = agents.harness("muse").outcome(
        STREAMS["muse"][0] + "\n" + '{"stream":{"kind":"session","id":"x"},"payload_type":'
        '"run.terminal.completed","payload":{"text":"DONE"}}')
    assert muse.text == "DONE" and muse.session == STREAMS["muse"][1]
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


def test_wrapper_records_the_session_as_soon_as_it_streams(tmp_path: Path) -> None:
    session_file = tmp_path / "coder-thread-id"
    rc = lane_agent.run_lane(
        [sys.executable, "-c", f"print({STREAMS['cursor-agent'][0]!r})"],
        slot="coder", log=tmp_path / "lane.log", exits=tmp_path / "exits.jsonl",
        session_agent="cursor-agent", session_file=session_file, plan_only=True,
    )
    assert rc == 0
    assert session_file.read_text().strip() == STREAMS["cursor-agent"][1]
    assert session_file.stat().st_mode & 0o077 == 0


def test_a_plan_lane_that_starts_on_api_billing_is_stopped(tmp_path: Path) -> None:
    api_init = '{"type":"system","subtype":"init","apiKeySource":"ANTHROPIC_API_KEY",' \
               '"session_id":"3ad997b3-721c-44df-9c17-e19d0f2617fc"}'
    rc = lane_agent.run_lane(
        _streaming_child([api_init, '{"type":"assistant"}']),
        slot="coder", log=tmp_path / "lane.log", exits=tmp_path / "exits.jsonl",
        session_agent="claude", session_file=tmp_path / "t", plan_only=True,
    )
    assert rc == lane_agent.BILLING_REFUSED
    assert "per-use API billing" in (tmp_path / "lane.log").read_text()
    exits = [json.loads(line) for line in (tmp_path / "exits.jsonl").read_text().splitlines()]
    assert exits[-1]["rc"] == lane_agent.BILLING_REFUSED
    # A metered lane is not stopped for the same line.
    rc = lane_agent.run_lane(
        [sys.executable, "-c", f"print({api_init!r})"],
        slot="coder", log=tmp_path / "m.log", exits=tmp_path / "m.jsonl",
        session_agent="claude", session_file=tmp_path / "m", plan_only=False,
    )
    assert rc == 0


def _runner(stdout: str, rc: int = 0) -> lane_agent.Runner:
    def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, rc, stdout, "")
    return runner


def test_claude_preflight_proves_a_plan_sign_in() -> None:
    plan = json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiKeySource": None})
    assert lane_agent.preflight("claude", environ={}, runner=_runner(plan)) == \
        "claude-plan:claude.ai"
    api = json.dumps({"loggedIn": True, "authMethod": "api_key",
                      "apiKeySource": "ANTHROPIC_API_KEY"})
    with pytest.raises(lane_agent.PreflightRefused, match="API key"):
        lane_agent.preflight("claude", environ={}, runner=_runner(api))
    with pytest.raises(lane_agent.PreflightRefused, match="not signed in"):
        lane_agent.preflight("claude", environ={}, runner=_runner('{"loggedIn": false}'))


def test_muse_preflight_refuses_an_api_key_and_a_missing_sign_in(tmp_path: Path) -> None:
    operator = _home(tmp_path)
    assert lane_agent.preflight("muse", environ={"HOME": str(operator)},
                                runner=_runner("muse 1.4.10")) == "muse-auth-stored"
    with pytest.raises(lane_agent.PreflightRefused, match="META_API_KEY"):
        lane_agent.preflight("muse", environ={"HOME": str(operator), "META_API_KEY": "k"},
                             runner=_runner(""))
    with pytest.raises(lane_agent.PreflightRefused, match="no stored sign-in"):
        lane_agent.preflight("muse", environ={"HOME": str(tmp_path / "nobody")},
                             runner=_runner(""))


def test_agy_preflight_refuses_a_signed_out_cli() -> None:
    assert lane_agent.preflight("agy", environ=dict(os.environ),
                                runner=_runner("gemini-4-argon-high\tGemini")) == "agy-models"
    with pytest.raises(lane_agent.PreflightRefused):
        lane_agent.preflight("agy", environ={}, runner=_runner(
            "Error: Please sign in to view available models.", rc=1))


def test_done_line_is_found_inside_any_harness_event() -> None:
    done = "__LANE_DONE__ coder commits=3"
    for line in (
        json.dumps({"payload_type": "run.terminal.completed", "payload": {"text": f"ok\n{done}"}}),
        json.dumps({"type": "result", "result": done}),
        json.dumps({"event": "step_update", "step_update": {"text_delta": done + "\n"}}),
        json.dumps({"item": {"type": "agent_message", "text": done}}),
    ):
        assert lane_watchdog._done_in(line)
    # The protocol's unfilled form, echoed back in a prompt event, is not a completion.
    assert not lane_watchdog._done_in(json.dumps(
        {"payload": {"prompt": "end with __LANE_DONE__ coder commits=<n>"}}))


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
