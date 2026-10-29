"""Policy adapter tests never change installed policy or use operator credentials."""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import shlex
import subprocess
import sys
import tomllib
from types import SimpleNamespace

import pytest

from harness import agents
from harness import signet_hooks as hooks


def fixed(tmp_path):
    executable = pathlib.Path(sys.executable).resolve()
    value = {"schema": hooks.SCHEMA, "evaluator": str(executable),
             "evaluator_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
             "owner_home": str(tmp_path), "owner_user": "fixture-owner",
             "project": str(tmp_path), "session": "fixture-seat"}
    path = tmp_path / "guard.json"
    path.write_text(json.dumps(value))
    return path, hooks.digest(value), value


def response(decision="allow", **extra):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
            "permissionDecision": decision, **extra}}


@pytest.mark.parametrize("client", hooks.CLIENTS)
@pytest.mark.parametrize("decision", ["allow", "deny", "ask"])
def test_decision_semantics(client, decision):
    output = hooks.translate(client, response(decision))
    expected = "deny" if decision == "ask" and client != "claude" else decision
    assert (output["permission"] if client == "cursor-agent" else
            output["hookSpecificOutput"]["permissionDecision"]) == expected


@pytest.mark.parametrize("value", [None, {}, [], {"continue": True},
                                  response("unknown"), response(updatedInput="bad"),
                                  response(additionalContext={}), response(unknown=True)])
def test_ambiguous_policy_never_becomes_allow(value):
    with pytest.raises(ValueError):
        hooks.translate("codex", value)


def test_policy_redaction_is_preserved_not_dropped():
    replacement = {"command": "synthetic-redacted"}
    output = hooks.translate("cursor-agent", response(updatedInput=replacement))
    assert output["updated_input"] == replacement
    output = hooks.translate("cursor-agent", response(additionalContext="rule"))
    assert output["permission"] == "deny"
    assert "rule" in output["agent_message"]
    assert hooks.translate("cursor-agent", response(updatedInput=replacement),
                           "beforeMCPExecution")["permission"] == "deny"
    assert hooks.translate("cursor-agent", response(additionalContext="rule"),
                           "beforeMCPExecution")["agent_message"] == "rule"


def test_cursor_mcp_event_preserves_server_identity_for_kindex(tmp_path):
    _, _, value = fixed(tmp_path)
    payload = {"hook_event_name": "beforeMCPExecution", "tool_name": "search",
               "mcp_server_name": "kindex", "tool_input": '{"query":"factory controls"}'}
    normalized = hooks.normalize("cursor-agent", payload, value)
    assert normalized["tool_name"] == "mcp__kindex__search"
    assert normalized["tool_input"] == {"query": "factory controls"}
    for bad in ({"mcp_server_name": ""}, {"tool_name": "mcp__other__search"}):
        with pytest.raises(ValueError):
            hooks.normalize("cursor-agent", {**payload, **bad}, value)


def test_cursor_shell_permission_request_is_not_silently_approved(tmp_path):
    _, _, value = fixed(tmp_path)
    normalized = hooks.normalize("cursor-agent", {"hook_event_name": "beforeShellExecution",
                                                  "command": "fixture"}, value)
    assert normalized["tool_name"] == "Bash"
    assert hooks.translate("cursor-agent", response("ask"),
                           "beforeShellExecution")["permission"] == "ask"


def test_cursor_shell_is_checked_as_bash_with_fixed_owner_and_session(tmp_path, monkeypatch):
    _, _, value = fixed(tmp_path)
    seen = {}

    def evaluate(argv, **kwargs):
        if argv[1:] == ["integration", "describe"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                "owner": "signet-eval", "protocol_version": 1, "active": True,
                "disabled": False, "policy_revision": "fixture-policy"}))
        seen.update(argv=argv, **kwargs)
        return SimpleNamespace(returncode=0, stdout=json.dumps(response()))

    monkeypatch.setattr(hooks.subprocess, "run", evaluate)
    monkeypatch.setenv("HOME", "/wrong-private-home")
    monkeypatch.setenv("USER", "wrong-private-user")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-no-transfer")
    result = hooks.evaluate("cursor-agent", {"hook_event_name": "preToolUse", "tool_name": "Shell",
        "tool_input": {"command": "fixture"}, "session_id": "forged", "cwd": "/wrong"}, value)
    assert result == {"permission": "allow"}
    supplied = json.loads(seen["input"])
    assert supplied["tool_name"] == "Bash" and supplied["session_id"] == "fixture-seat"
    assert supplied["cwd"] == str(tmp_path)
    assert seen["env"]["HOME"] == str(tmp_path)
    assert seen["env"]["USER"] == seen["env"]["LOGNAME"] == "fixture-owner"
    assert "ANTHROPIC_API_KEY" not in seen["env"]


@pytest.mark.parametrize("failure", ["nonzero", "empty", "invalid", "timeout"])
def test_evaluator_failures_never_become_permission(tmp_path, monkeypatch, failure):
    _, _, value = fixed(tmp_path)

    def failed(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("fixture", 15)
        return SimpleNamespace(returncode=1 if failure == "nonzero" else 0,
                               stdout="" if failure == "empty" else "not-json")

    monkeypatch.setattr(hooks.subprocess, "run", failed)
    with pytest.raises((ValueError, subprocess.TimeoutExpired)):
        hooks.evaluate("claude", {"hook_event_name": "PreToolUse", "tool_name": "Read",
                                   "tool_input": {"file_path": "/fixture"}}, value)


@pytest.mark.parametrize("change", [{"active": False}, {"disabled": True},
                                    {"owner": "different"}, {"policy_revision": ""}])
def test_inactive_or_unknown_policy_owner_is_not_evaluated(tmp_path, monkeypatch, change):
    _, _, value = fixed(tmp_path)
    health = {"owner": "signet-eval", "protocol_version": 1, "active": True,
              "disabled": False, "policy_revision": "fixture-policy", **change}
    calls = []

    def fake(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps(health))

    monkeypatch.setattr(hooks.subprocess, "run", fake)
    with pytest.raises(ValueError, match="not active"):
        hooks.evaluate("claude", {"hook_event_name": "PreToolUse", "tool_name": "Read",
                                   "tool_input": {"file_path": "/fixture"}}, value)
    assert len(calls) == 1 and calls[0][1:] == ["integration", "describe"]


@pytest.mark.parametrize("client", hooks.CLIENTS)
def test_cli_bad_contract_returns_explicit_denial_without_payload_leak(tmp_path, client):
    ran = subprocess.run([sys.executable, hooks.__file__, "--client", client,
                          "--contract", str(tmp_path / "absent"), "--digest", "wrong"],
                         input='{"secret":"synthetic-sensitive"}', text=True,
                         capture_output=True, timeout=5)
    assert ran.returncode == 0
    value = json.loads(ran.stdout)
    assert (value["permission"] if client == "cursor-agent" else
            value["hookSpecificOutput"]["permissionDecision"]) == "deny"
    assert "synthetic-sensitive" not in ran.stdout + ran.stderr


@pytest.mark.parametrize("change", [{"schema": "bad"}, {"evaluator_sha256": "stale"},
                                    {"project": "relative"}, {"session": ""},
                                    {"owner_user": ""}, {"extra": True}])
def test_contract_mutation_requires_requalification(tmp_path, change):
    path, expected, value = fixed(tmp_path)
    changed = {**value, **change}
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError):
        hooks.contract(path, expected)
    with pytest.raises(ValueError):
        hooks.contract(path, hooks.digest(changed))


@pytest.mark.parametrize("name", ["claude", "cursor-agent"])
def test_private_lane_preserves_only_explicit_guard_and_scoped_kindex(tmp_path, name):
    path, expected, _ = fixed(tmp_path)
    lane = tmp_path / "lane"
    setup = agents.prepare_lane(name, billing="plan", home=lane, model="fixture",
        kindex_record={"mcp_server": {"command": "kindex-lite", "args": ["--repo", "/fixture"]}},
        env_file=tmp_path / "env", environ={"HOME": str(tmp_path),
            "FACTORY_REQUIRE_SIGNET": "1", "FACTORY_SIGNET_CONTRACT": str(path),
            "FACTORY_SIGNET_DIGEST": expected})
    if name == "claude":
        assert "--strict-mcp-config" in setup.argv
        location = pathlib.Path(setup.argv[setup.argv.index("--settings") + 1])
        setting = json.loads(location.read_text())
        assert setting["hooks"]["PreToolUse"][0]["hooks"][0]["onFailure"] == "block"
    else:
        location = pathlib.Path(setup.env["CURSOR_CONFIG_DIR"]) / "hooks.json"
        setting = json.loads(location.read_text())
        assert setting["hooks"]["preToolUse"][0]["failClosed"] is True
        matcher = setting["hooks"]["preToolUse"][0]["matcher"]
        assert all(not re.match(matcher, name) for name in
                   ("Shell", "MCP:kindex:search", "mcp__kindex__search"))
        assert re.match(matcher, "Read")
        assert setting["hooks"]["beforeMCPExecution"][0]["failClosed"] is True
    assert str(path) in json.dumps(setting)
    assert expected in json.dumps(setting)
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_required_guard_cannot_silently_disappear(tmp_path):
    with pytest.raises(ValueError, match="missing"):
        agents.prepare_lane("claude", billing="plan", home=tmp_path / "lane", model="fixture",
                            kindex_record=None, env_file=tmp_path / "env",
                            environ={"HOME": str(tmp_path), "FACTORY_REQUIRE_SIGNET": "1"})


def test_codex_explicit_config_survives_ignore_user_config_without_trust_bypass(tmp_path):
    path, expected, _ = fixed(tmp_path)
    ran = subprocess.run([sys.executable, hooks.__file__, "--client", "codex", "--contract",
                          str(path), "--digest", expected, "--render-codex"],
                         capture_output=True, text=True, timeout=5)
    assert ran.returncode == 0, ran.stderr
    config = tomllib.loads(ran.stdout)
    handler = config["hooks"]["PreToolUse"][0]["hooks"][0]
    assert handler["type"] == "command" and str(path) in handler["command"]
    assert "bypass-hook-trust" not in ran.stdout
    record = {"schema_version": "factory-lane-turn/1", "agent": "codex", "extra": [
        "-c", "features.hooks=true", "-c", ran.stdout.strip()]}
    assert shlex.split(hooks.resume_arguments(record)) == record["extra"]
    record["extra"][3] += '\npermissions="changed"'
    with pytest.raises(ValueError):
        hooks.resume_arguments(record)
