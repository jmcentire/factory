#!/usr/bin/env python3
"""Carry the installed Signet evaluator into isolated CLI configuration without copying hooks.

This adapter never invents policy or edits the evaluator. Its host-pinned contract selects the
existing evaluator, owner HOME, project and session. Empty, malformed, failed and unsupported
decisions deny. Codex/Cursor cannot enforce an ASK in PreToolUse, so it remains a blocked
decision rather than silently becoming permission. Configuration generation is not activation
or a proof that a particular installed client loads/enforces hooks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shlex
import socket
import subprocess
import sys
import uuid
from typing import Any

SCHEMA = "factory-signet-hook/1"
CLIENTS = ("claude", "codex", "cursor-agent")
MAX_INPUT = 2 * 1024 * 1024


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def contract(path: pathlib.Path, expected: str) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if (not isinstance(value, dict) or set(value) != {
            "schema", "evaluator", "evaluator_sha256", "owner_home", "owner_user",
            "project", "session"}
            or value["schema"] != SCHEMA or digest(value) != expected):
        raise ValueError("Signet hook contract absent or changed")
    for field in ("evaluator", "owner_home", "project"):
        if not isinstance(value[field], str) or not pathlib.Path(value[field]).is_absolute():
            raise ValueError("Signet hook paths must be absolute")
    if not isinstance(value["session"], str) or not value["session"].strip():
        raise ValueError("Signet session is required")
    if (not isinstance(value["owner_user"], str) or not value["owner_user"].strip()
            or any(ord(c) < 32 for c in value["owner_user"])):
        raise ValueError("Signet owner account identity is required")
    executable = pathlib.Path(value["evaluator"])
    if (not os.access(executable, os.X_OK)
            or hashlib.sha256(executable.read_bytes()).hexdigest() != value["evaluator_sha256"]):
        raise ValueError("installed evaluator changed; requalification required")
    if (not pathlib.Path(value["owner_home"]).is_dir()
            or not pathlib.Path(value["project"]).is_dir()):
        raise ValueError("Signet owner or scoped project unavailable")
    return value


def normalize(client: str, payload: Any, fixed: dict[str, Any]) -> dict[str, Any]:
    if client not in CLIENTS or not isinstance(payload, dict):
        raise ValueError("unsupported hook client or payload")
    event = payload.get("hook_event_name")
    name = payload.get("tool_name")
    parameters = payload.get("tool_input")
    if client == "cursor-agent" and event == "beforeShellExecution":
        if not isinstance(payload.get("command"), str):
            raise ValueError("shell command is missing")
        name, parameters = "Bash", {"command": payload["command"]}
    elif client == "cursor-agent" and event == "beforeMCPExecution":
        server = payload.get("mcp_server_name")
        if (not isinstance(server, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", server)
                or not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name)):
            raise ValueError("MCP server/tool identity is missing or ambiguous")
        prefix = f"mcp__{server}__"
        if name.startswith("mcp__") and not name.startswith(prefix):
            raise ValueError("MCP tool and server identities disagree")
        name = name if name.startswith(prefix) else prefix + name
        if isinstance(parameters, str):
            parameters = json.loads(parameters)
    elif event != ("preToolUse" if client == "cursor-agent" else "PreToolUse"):
        raise ValueError("unsupported tool hook event")
    if not isinstance(name, str) or not name or not isinstance(parameters, dict):
        raise ValueError("hook tool name/input is missing")
    # Cursor's documented Shell is the same operation as Signet's canonical Bash.
    if client == "cursor-agent" and name == "Shell":
        name = "Bash"
    return {"hook_event_name": "PreToolUse", "tool_name": name, "tool_input": parameters,
            "session_id": fixed["session"], "cwd": fixed["project"]}


def denial(client: str, reason: str) -> dict[str, Any]:
    if client == "cursor-agent":
        return {"permission": "deny", "user_message": reason, "agent_message": reason}
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
            "permissionDecision": "deny", "permissionDecisionReason": reason}}


def translate(client: str, output: Any, event: str = "preToolUse") -> dict[str, Any]:
    if not isinstance(output, dict) or set(output) - {"hookSpecificOutput"}:
        raise ValueError("unsupported evaluator response; cannot discard policy effects")
    specific = output.get("hookSpecificOutput")
    if (not isinstance(specific, dict) or specific.get("hookEventName") != "PreToolUse"
            or set(specific) - {"hookEventName", "permissionDecision", "permissionDecisionReason",
                                "updatedInput", "additionalContext"}):
        raise ValueError("malformed evaluator response")
    decision = specific.get("permissionDecision")
    if decision not in ("allow", "deny", "ask"):
        raise ValueError("no explicit policy decision")
    if "updatedInput" in specific and not isinstance(specific["updatedInput"], dict):
        raise ValueError("malformed policy input replacement")
    if any(not isinstance(specific[key], str) for key in
           ("permissionDecisionReason", "additionalContext") if key in specific):
        raise ValueError("malformed policy context")
    if decision == "ask" and client != "claude" and not (
            client == "cursor-agent" and event == "beforeShellExecution"):
        return denial(client, "Signet requires a scoped approval; this action remains blocked.")
    if client != "cursor-agent":
        return output
    if event != "preToolUse" and "updatedInput" in specific:
        return denial(client, "This Cursor event cannot preserve Signet's input replacement.")
    if specific.get("additionalContext") and event == "preToolUse":
        # This event delivers agent_message only on denial. Deliver guidance before the
        # action, without silently dropping it or claiming the original call executed.
        return denial(client, "Signet policy context must be received before this action:\n"
                      + specific["additionalContext"])
    result: dict[str, Any] = {"permission": decision}
    if "permissionDecisionReason" in specific:
        result["user_message"] = result["agent_message"] = specific["permissionDecisionReason"]
    if "updatedInput" in specific:
        result["updated_input"] = specific["updatedInput"]
    if specific.get("additionalContext"):
        result["agent_message"] = (result.get("agent_message", "") + "\n"
                                   + specific["additionalContext"]).strip()
    return result


def evaluate(client: str, payload: Any, fixed: dict[str, Any]) -> dict[str, Any]:
    return evaluate_with_health(client, payload, fixed)[0]


def evaluate_with_health(client: str, payload: Any,
                         fixed: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized = normalize(client, payload, fixed)
    # Do not inherit a private Cursor HOME, policy-path override or arbitrary secret variables.
    environment = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG") if key in os.environ}
    # Signet uses USER in its existing vault/state selection. Dropping it makes a healthy
    # terminal appear unconfigured and loses the real Kindex engagement ledger. Pin the
    # nonsecret account name rather than inheriting a potentially changed lane identity.
    environment.update(HOME=fixed["owner_home"], USER=fixed["owner_user"],
                       LOGNAME=fixed["owner_user"], SIGNET_SESSION=fixed["session"],
                       KIN_PROJECT_PATH=fixed["project"])
    health = subprocess.run([fixed["evaluator"], "integration", "describe"],
                            capture_output=True, text=True, timeout=5,
                            cwd=fixed["project"], env=environment)
    if health.returncode:
        raise ValueError("Signet health unavailable")
    advertised = json.loads(health.stdout)
    if (not isinstance(advertised, dict) or advertised.get("owner") != "signet-eval"
            or advertised.get("protocol_version") != 1 or advertised.get("active") is not True
            or advertised.get("disabled") is not False or not advertised.get("policy_revision")):
        raise ValueError("Signet enforcement is not active")
    completed = subprocess.run([fixed["evaluator"], "--adapter", "claude", "eval"],
                               input=json.dumps(normalized), capture_output=True, text=True,
                               timeout=15, cwd=fixed["project"], env=environment)
    if completed.returncode:
        raise ValueError("Signet evaluation failed; action remains blocked")
    return translate(client, json.loads(completed.stdout), payload["hook_event_name"]), advertised


def evaluate_host(client: str, payload: Any, address: str, plan_digest: str) -> dict[str, Any]:
    """The only alternative evaluator is the pinned, already-authorized local custody service.

    A disconnected/invalid service is a denial, never fallback to stateless local evaluation.
    Raw tool data travels only over this local socket and is not printed or journaled.
    """
    if (not pathlib.Path(address).is_absolute()
            or not re.fullmatch(r"[0-9a-f]{64}", plan_digest)):
        raise ValueError("invalid policy custody binding")
    import lane_custody
    request = {"kind": "policy", "request_id": uuid.uuid4().hex,
               "plan_digest": plan_digest, "payload": payload}
    raw = json.dumps(request).encode() + b"\n"
    if len(raw) > MAX_INPUT:
        raise ValueError("policy request exceeds bound")
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(22)
        connection.connect(address)
        connection.sendall(raw)
        result = lane_custody._receive(connection, timeout=22, maximum=MAX_INPUT)
    if (not isinstance(result, dict) or set(result) != {"status", "output"}
            or result["status"] != "policy"):
        raise ValueError("policy custody unavailable or refused")
    output = result["output"]
    # The host is bound to one client; reject incompatible responses, never reinterpret them.
    if client == "cursor-agent":
        if (not isinstance(output, dict) or output.get("permission") not in {"allow", "deny", "ask"}
                or set(output) - {"permission", "user_message", "agent_message", "updated_input"}
                or any(not isinstance(output[k], str) for k in
                       ("user_message", "agent_message") if k in output)
                or ("updated_input" in output and not isinstance(output["updated_input"], dict))):
            raise ValueError("invalid policy custody response")
        return output
    return translate(client, output, payload["hook_event_name"])


def configuration(client: str, path: pathlib.Path, expected: str, python: pathlib.Path,
                  *, custody_socket: str | None = None,
                  custody_digest: str | None = None) -> dict:
    """Build candidate client settings; does not install them or approve hook trust."""
    contract(path, expected)
    arguments = [str(python), str(pathlib.Path(__file__).resolve()),
                 "--client", client, "--contract", str(path), "--digest", expected]
    if custody_socket is not None or custody_digest is not None:
        if (not custody_socket or not pathlib.Path(custody_socket).is_absolute()
                or not custody_digest or not re.fullmatch(r"[0-9a-f]{64}", custody_digest)):
            raise ValueError("policy custody settings must be a complete host binding")
        arguments += ["--custody-socket", custody_socket, "--custody-digest", custody_digest]
    command = shlex.join(arguments)
    if client == "cursor-agent":
        base = {"command": command, "timeout": 25, "failClosed": True}
        return {"version": 1, "hooks": {
            "preToolUse": [{**base, "matcher": r"^(?!(?:Shell$|MCP:|mcp__))"}],
            "beforeShellExecution": [base], "beforeMCPExecution": [base]}}
    if client not in ("claude", "codex"):
        raise ValueError("client has no qualified hook configuration")
    handler: dict[str, Any] = {"type": "command", "command": command, "timeout": 25}
    if client == "claude":
        handler["onFailure"] = "block"
    return {"hooks": {"PreToolUse": [{"hooks": [handler]}]}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=CLIENTS)
    parser.add_argument("--contract", type=pathlib.Path)
    parser.add_argument("--digest")
    parser.add_argument("--custody-socket")
    parser.add_argument("--custody-digest")
    parser.add_argument("--resume-record", type=pathlib.Path)
    parser.add_argument("--render-codex", action="store_true",
                        help="render one explicit Codex config override; does not approve trust")
    parser.add_argument("--check-codex-cwd", type=pathlib.Path,
                        help="require the exact existing Codex hook trust before returning args")
    args = parser.parse_args(argv)
    if args.resume_record:
        try:
            value = json.loads(args.resume_record.read_text())
            retained = resume_arguments(value)
            if retained and args.check_codex_cwd:
                from codex_hook_admission import check_command
                check_command(["codex", *shlex.split(retained)], args.check_codex_cwd)
            print(retained)
            return 0
        except (OSError, ValueError, TypeError):
            return 77
    if not args.client or not args.contract or not args.digest:
        parser.error("--client, --contract and --digest are required for hook execution/rendering")
    if args.render_codex:
        try:
            if args.client != "codex":
                raise ValueError("Codex settings requested for a different client")
            from lane_agent import _toml_value
            settings = configuration(args.client, args.contract, args.digest,
                                     pathlib.Path(sys.executable),
                                     custody_socket=args.custody_socket,
                                     custody_digest=args.custody_digest)
            override = "hooks.PreToolUse=" + _toml_value(settings["hooks"]["PreToolUse"])
            if args.check_codex_cwd:
                from codex_hook_admission import check_command
                check_command(["codex", "-c", "features.hooks=true", "-c", override],
                              args.check_codex_cwd)
            print(override)
            return 0
        except (OSError, ValueError, TypeError):
            print("required Signet hook configuration or Codex trust unavailable; "
                  "review the exact hook with Codex /hooks", file=sys.stderr)
            return 77
    try:
        fixed = contract(args.contract, args.digest)
        raw = sys.stdin.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError("hook input exceeds bound")
        if args.custody_socket is not None or args.custody_digest is not None:
            if not args.custody_socket or not args.custody_digest:
                raise ValueError("incomplete policy custody binding")
            output = evaluate_host(args.client, json.loads(raw),
                                   args.custody_socket, args.custody_digest)
        else:
            output = evaluate(args.client, json.loads(raw), fixed)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        # Do not echo input, evaluator stderr, credential values or arbitrary exception text.
        output = denial(args.client, "Factory Signet hook unavailable or invalid; action blocked.")
    print(json.dumps(output))
    return 0


def resume_arguments(record: Any) -> str:
    if (not isinstance(record, dict) or record.get("schema_version") != "factory-lane-turn/1"
            or record.get("agent") != "codex"):
        raise ValueError("invalid retained Codex turn")
    extra = record.get("extra", [])
    if extra == []:
        return ""
    if (not isinstance(extra, list) or len(extra) != 4 or extra[:3] != [
            "-c", "features.hooks=true", "-c"] or not isinstance(extra[3], str)
            or not extra[3].startswith("hooks.PreToolUse=")):
        raise ValueError("retained arguments are not the launch's guard configuration")
    import tomllib
    parsed = tomllib.loads(extra[3])
    if set(parsed) != {"hooks"} or set(parsed["hooks"]) != {"PreToolUse"}:
        raise ValueError("retained hook override includes other settings")
    return shlex.join(extra)


if __name__ == "__main__":
    raise SystemExit(main())
