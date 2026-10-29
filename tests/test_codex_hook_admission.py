from __future__ import annotations

import copy
import json
import sys

import pytest

from harness.codex_hook_admission import (
    HookAdmissionError,
    check_command,
    preserve_trust_with_scoped_mcp,
    required_override,
    validate_listing,
)
from harness.codex_lane_session import SessionError, run_turn

OVERRIDE = 'hooks.PreToolUse=[{hooks=[{type="command",command="/host/guard",timeout=25}]}]'


def listing(cwd):
    return {"result": {"data": [{"cwd": str(cwd), "errors": [], "hooks": [{
        "eventName": "preToolUse", "command": "/host/guard", "enabled": True,
        "source": "sessionFlags", "trustStatus": "trusted", "matcher": None,
        "timeoutSec": 25, "currentHash": "sha256:" + "a" * 64}]}]}}


@pytest.mark.parametrize(("field", "value"), [
    ("trustStatus", "untrusted"), ("trustStatus", "modified"), ("trustStatus", "unknown"),
    ("enabled", False), ("command", "/different/guard"), ("source", "project"),
    ("eventName", "postToolUse"), ("matcher", "Read"), ("timeoutSec", 0),
    ("currentHash", "sha256:invalid"),
])
def test_hook_presence_never_substitutes_for_exact_active_trust(tmp_path, field, value):
    response = listing(tmp_path)
    response["result"]["data"][0]["hooks"][0][field] = value
    with pytest.raises(HookAdmissionError):
        validate_listing(response, OVERRIDE, tmp_path)


@pytest.mark.parametrize("failure", ["missing", "duplicate", "errors", "wrong-cwd", "malformed"])
def test_ambiguous_inspection_fails_closed(tmp_path, failure):
    response = listing(tmp_path)
    entry = response["result"]["data"][0]
    if failure == "missing":
        entry["hooks"] = []
    elif failure == "duplicate":
        entry["hooks"] *= 2
    elif failure == "errors":
        entry["errors"] = [{"message": "source unavailable"}]
    elif failure == "wrong-cwd":
        entry["cwd"] = str(tmp_path / "other")
    else:
        response = {"error": {"message": "unsupported"}}
    with pytest.raises(HookAdmissionError):
        validate_listing(response, OVERRIDE, tmp_path)


def test_only_required_hook_metadata_is_retained(tmp_path):
    response = listing(tmp_path)
    extra = copy.deepcopy(response["result"]["data"][0]["hooks"][0])
    extra.update(command="private unrelated command", source="user", trustStatus="untrusted")
    response["result"]["data"][0]["hooks"].append(extra)
    proof = validate_listing(response, OVERRIDE, tmp_path)
    assert proof == {"event": "preToolUse", "trust": "trusted",
                     "hash": "sha256:" + "a" * 64, "source": "sessionFlags"}


def test_disabled_and_ambiguous_overrides_cannot_evade_admission():
    base = ["codex", "exec", "-c", OVERRIDE]
    for more in (["-c", "features.hooks=false"], ["--disable", "hooks"],
                 ["--disable=hooks"], ["-c", OVERRIDE],
                 ["-cfeatures.hooks=false"], ["-c", "features={hooks=false}"],
                 ["--disable", "codex_hooks"], ["-c", "hooks={}"]):
        with pytest.raises(HookAdmissionError):
            required_override(base + more)
    assert required_override(["python", "-c", "pass"]) is None
    assert required_override(["codex", "exec", "--config=" + OVERRIDE]) == OVERRIDE


@pytest.mark.parametrize("resume", [False, True])
def test_normal_hook_trust_does_not_restore_ambient_mcp(tmp_path, monkeypatch, resume):
    import subprocess

    servers = [{"name": name, "enabled": True,
                "transport": {"type": kind, "env": {"PRIVATE": "hidden"}}}
               for name, kind in (("kindex", "stdio"), ("computer-use", "stdio"),
                                  ("kindex-2", "streamable_http"))]
    observed = []

    def metadata(command, **kwargs):
        observed.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(servers), "")

    monkeypatch.setattr(subprocess, "run", metadata)
    scoped = 'mcp_servers.kindex={command="/approved/local-kindex"}'
    command = ["codex", "exec", *(["resume"] if resume else []), "--ignore-user-config",
               "--ignore-rules", "--strict-config", "--disable", "apps",
               "-c", OVERRIDE, "-c", scoped, *(["existing-session"] if resume else []), "-"]
    fixed = preserve_trust_with_scoped_mcp(command, tmp_path)
    assert observed == [["codex", "mcp", "list", "--json"]]
    assert fixed == [
        "codex", "-c", 'mcp_servers.computer-use={enabled=false,command="/usr/bin/false"}',
        "-c", 'mcp_servers.kindex-2={enabled=false,url="http://127.0.0.1:1"}',
        *(arg for arg in command[1:] if arg != "--ignore-user-config")]
    assert "hidden" not in str(fixed)


@pytest.mark.parametrize("servers", [{}, [{"name": "bad.name"}], [{"name": "x"}, {"name": "x"}],
                                      [{"enabled": True}]])
def test_ambiguous_ambient_mcp_prevents_config_correction(tmp_path, monkeypatch, servers):
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a[0], 0, json.dumps(servers), ""))
    with pytest.raises(HookAdmissionError, match="cannot verify scoped MCP"):
        preserve_trust_with_scoped_mcp(["codex", "exec", "--ignore-user-config", "-c", OVERRIDE,
                                      "-c", 'mcp_servers={kindex={command="local"}}', "-"],
                                     tmp_path)


def test_no_required_hook_keeps_existing_command(tmp_path):
    command = ["codex", "exec", "--ignore-user-config", "-"]
    assert preserve_trust_with_scoped_mcp(command, tmp_path) == command


def fake_codex(tmp_path, monkeypatch, *, trust="untrusted", behavior="normal"):
    executable = tmp_path / "codex"
    executable.write_text(f"#!{sys.executable}\n" + '''
import json, os, pathlib, sys, time
if "app-server" not in sys.argv:
    pathlib.Path(os.environ["MODEL_MARKER"]).write_text("model started")
    sys.stdin.read()
    print(json.dumps({"type":"thread.started", "thread_id":"12345678-1234-4234-8234-123456789abc"}))
    raise SystemExit(0)
for line in sys.stdin:
    value=json.loads(line)
    with open(os.environ["METHODS"],"a") as log: log.write(value["method"]+"\\n")
    if value["method"]=="initialize":
        print(json.dumps({"id":value["id"],"result":{}}),flush=True)
    elif value["method"]=="hooks/list":
        mode=os.environ["HOOK_BEHAVIOR"]
        if mode=="hang": time.sleep(30)
        if mode=="partial":
            print('{"id":',end="",flush=True);time.sleep(30)
        if mode=="exit": raise SystemExit(1)
        if mode=="malformed":
            print('[]',flush=True); continue
        data=json.loads(os.environ["HOOK_LISTING"])
        data["id"]=value["id"]
        print(json.dumps(data),flush=True)
''')
    executable.chmod(0o700)
    response = listing(tmp_path)
    response["result"]["data"][0]["hooks"][0]["trustStatus"] = trust
    for key, value in {"MODEL_MARKER": str(tmp_path / "model-started"),
                       "METHODS": str(tmp_path / "methods"),
                       "HOOK_BEHAVIOR": behavior, "HOOK_LISTING": json.dumps(response)}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    return [str(executable), "exec", "-c", OVERRIDE]


@pytest.mark.parametrize("resume", [False, True])
def test_launch_and_resume_never_start_model_with_untrusted_hook(tmp_path, monkeypatch, resume):
    command = fake_codex(tmp_path, monkeypatch)
    if resume:
        command += ["resume", "previous-thread"]
    prompt = tmp_path / "prompt"
    prompt.write_text("synthetic")
    with pytest.raises(SessionError, match="untrusted"):
        run_turn(prompt, tmp_path / "thread", tmp_path / "events", command)
    assert not (tmp_path / "model-started").exists()
    assert not (tmp_path / "thread").exists()
    assert (tmp_path / "methods").read_text().splitlines() == [
        "initialize", "initialized", "hooks/list"]


def test_trusted_hook_allows_original_model_command(tmp_path, monkeypatch):
    command = fake_codex(tmp_path, monkeypatch, trust="trusted")
    prompt = tmp_path / "prompt"
    prompt.write_text("synthetic")
    assert run_turn(prompt, tmp_path / "thread", tmp_path / "events", command) == 0
    assert (tmp_path / "model-started").read_text() == "model started"


@pytest.mark.parametrize("behavior", ["hang", "partial", "exit", "malformed"])
def test_unavailable_or_incomplete_protocol_is_bounded(tmp_path, monkeypatch, behavior):
    command = fake_codex(tmp_path, monkeypatch, behavior=behavior)
    with pytest.raises(HookAdmissionError):
        check_command(command, tmp_path, timeout=.2)
    assert not (tmp_path / "model-started").exists()
