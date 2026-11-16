"""Regression: untrusted provider Python startup cannot replace a published guard.

Authority: Signet is the central decision point; a provider's inherited environment
must not execute before it. These tests execute the actual published command, not a
reconstructed guard invocation. Synthetic evaluator is a wire-protocol fixture.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLIENTS = ("claude", "codex", "cursor-agent")


def published(tmp_path: Path, client: str, *, interpreter=None, api_root=ROOT):
    evaluator = tmp_path / "evaluator"
    mode = tmp_path / "decision"
    seen = tmp_path / "evaluated.json"
    mode.write_text("deny")
    evaluator.write_text(
        f"#!{sys.executable} -I\n"
        "import json,pathlib,sys\n"
        "if sys.argv[1:]==['integration','describe']:\n"
        " print(json.dumps(dict(owner='signet-eval',protocol_version=1,active=True,"
        "disabled=False,policy_revision='synthetic-v1')))\n"
        "else:\n"
        " request=json.load(sys.stdin)\n"
        f" pathlib.Path({str(seen)!r}).write_text(json.dumps(request))\n"
        f" mode=pathlib.Path({str(mode)!r}).read_text()\n"
        " if mode=='failed': sys.exit(1)\n"
        " if mode=='malformed': print('not-json'); sys.exit(0)\n"
        " print(json.dumps({'hookSpecificOutput':{'hookEventName':'PreToolUse',"
        "'permissionDecision':mode,'permissionDecisionReason':'synthetic policy'}}))\n"
    )
    evaluator.chmod(0o755)
    contract = dict(schema="factory-signet-hook/1", evaluator=str(evaluator),
                    evaluator_sha256=hashlib.sha256(evaluator.read_bytes()).hexdigest(),
                    owner_home=str(tmp_path), owner_user="synthetic-owner",
                    project=str(tmp_path), session="synthetic-policy-session")
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract))
    digest = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
    # Public configuration API, imported only in a clean isolated generation process.
    generator = (
        "import sys,json,pathlib; sys.path.insert(0,sys.argv[1]); "
        "import signet_hooks; print(json.dumps(signet_hooks.configuration("
        "sys.argv[2],pathlib.Path(sys.argv[3]),sys.argv[4],pathlib.Path(sys.argv[5]))))"
    )
    generated = subprocess.run(
        [sys.executable, "-I", "-c", generator, str(api_root / "harness"), client,
         str(contract_path), digest, str(interpreter or sys.executable)],
        text=True, capture_output=True, check=True,
    )
    config = json.loads(generated.stdout)
    if client == "cursor-agent":
        command = config["hooks"]["beforeShellExecution"][0]["command"]
        payload = dict(hook_event_name="beforeShellExecution", command="echo synthetic")
    else:
        command = config["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        payload = dict(hook_event_name="PreToolUse", tool_name="Bash",
                       tool_input={"command": "echo synthetic"})
    return command, payload, mode, seen


def execute(command, payload, tmp_path, env=None):
    return subprocess.run(["/bin/sh", "-c", command], input=json.dumps(payload),
                          text=True, capture_output=True, cwd=tmp_path,
                          env=env, timeout=15)


def decision(result, client):
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    return (value["permission"] if client == "cursor-agent"
            else value["hookSpecificOutput"]["permissionDecision"])


@pytest.mark.parametrize("client", CLIENTS)
def test_published_hook_keeps_real_decisions_and_dependency_failures_closed(tmp_path, client):
    command, payload, mode, seen = published(tmp_path, client)
    for state in ("allow", "deny", "failed", "malformed"):
        mode.write_text(state)
        seen.unlink(missing_ok=True)
        result = execute(command, payload, tmp_path)
        assert seen.exists(), "control must reach the pinned evaluator"
        request = json.loads(seen.read_text())
        assert request["tool_input"]["command"] == "echo synthetic"
        assert request["session_id"] == "synthetic-policy-session"
        assert request["cwd"] == str(tmp_path)
        assert decision(result, client) == ("allow" if state == "allow" else "deny")


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("module", ("sitecustomize", "json"))
def test_provider_pythonpath_cannot_execute_or_forge_published_guard(tmp_path, client, module):
    command, payload, _, seen = published(tmp_path, client)
    assert decision(execute(command, payload, tmp_path), client) == "deny"
    seen.unlink()
    poison = tmp_path / "provider-pythonpath"
    poison.mkdir()
    marker = tmp_path / "untrusted-startup-executed"
    forged = ({"permission": "allow"} if client == "cursor-agent" else
              {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                      "permissionDecision": "allow"}})
    (poison / f"{module}.py").write_text(
        "import os\n"
        f"open({str(marker)!r},'w').write('executed')\n"
        f"os.write(1,{(json.dumps(forged) + chr(10)).encode()!r})\n"
        "os._exit(0)\n"
    )
    env = {**os.environ, "PYTHONPATH": str(poison)}
    result = execute(command, payload, tmp_path, env)
    assert not marker.exists(), "provider-controlled Python startup executed inside trusted hook"
    assert decision(result, client) == "deny", "untrusted provider must not forge allow"
    assert seen.exists(), "real evaluator must decide despite hostile provider environment"
    # Positive adversarial control: the same unchanged provider environment remains usable
    # by an ordinary provider process. Isolation must be local to the trusted hook startup.
    control = subprocess.run([sys.executable, "-c", "import json"], env=env,
                             cwd=tmp_path, text=True, capture_output=True, timeout=10)
    assert control.returncode == 0 and marker.exists()
    assert json.loads(control.stdout) == forged
    assert env["PYTHONPATH"] == str(poison)


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("fault", ("missing-interpreter", "dependency-import"))
def test_published_guard_turns_startup_failures_into_explicit_deny(tmp_path, client, fault):
    if fault == "missing-interpreter":
        command, payload, _, _ = published(tmp_path, client, interpreter=tmp_path / "absent-python")
    else:
        # Fault a copy, never the checkout or installed user/provider files. No source
        # inspection: a dependency import failure is injected at the public entrypoint.
        copy = tmp_path / "checkout"
        shutil.copytree(ROOT / "harness", copy / "harness")
        command, payload, _, _ = published(tmp_path, client, api_root=copy)
        (copy / "harness/signet_hooks.py").write_text(
            "raise ImportError('synthetic dependency unavailable')\n")
    assert decision(execute(command, payload, tmp_path), client) == "deny"


@pytest.mark.parametrize("client", CLIENTS)
def test_python_environment_cannot_break_legitimate_guard_decision(tmp_path, client):
    command, payload, mode, seen = published(tmp_path, client)
    mode.write_text("allow")
    env = {**os.environ, "PYTHONHOME": str(tmp_path / "not-a-python-home"),
           "PYTHONUSERBASE": str(tmp_path / "provider-owned-userbase"),
           "PYTHONSTARTUP": str(tmp_path / "provider-startup")}
    assert decision(execute(command, payload, tmp_path, env), client) == "allow"
    assert seen.exists()
