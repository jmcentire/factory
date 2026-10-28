"""Refuse a required Codex hook that the installed client would skip as untrusted.

Configuration is not activation. Query the client's supported, read-only hooks/list
interface before a model turn; never grant trust, start a thread, or print other
configured hooks. This establishes admission, not behavioral provider acceptance.
"""
from __future__ import annotations

import contextlib
import json
import os
import pathlib
import re
import selectors
import subprocess
import time
import tomllib
from typing import Any


class HookAdmissionError(ValueError):
    """The required hook is not demonstrably active under its existing trust."""


def preserve_trust_with_scoped_mcp(command: list[str], cwd: pathlib.Path) -> list[str]:
    """Keep persisted hook trust without restoring the operator's extra MCP connections.

    exec's ignore-user-config also drops the trust that hooks/list inspected. MCP
    tables merge, so the lane's Kindex override alone cannot exclude ambient servers.
    This changes only this invocation, never user settings, hook trust or hook state.
    """
    if "--ignore-user-config" not in command or required_override(command) is None:
        return command
    overrides = [command[i + 1] for i, arg in enumerate(command[:-1]) if arg == "-c"]
    try:
        scoped = [tomllib.loads(value).get("mcp_servers") for value in overrides
                  if value.startswith(("mcp_servers=", "mcp_servers."))]
    except ValueError:
        raise HookAdmissionError("invalid scoped Kindex configuration") from None
    if len(scoped) != 1 or not isinstance(scoped[0], dict) or set(scoped[0]) != {"kindex"}:
        raise HookAdmissionError("required hook launch needs scoped Kindex configuration")
    try:
        result = subprocess.run([command[0], "mcp", "list", "--json"], cwd=cwd,
                                capture_output=True, text=True, timeout=10, check=True)
        if len(result.stdout) > 1024 * 1024:
            raise ValueError("metadata exceeds bound")
        servers = json.loads(result.stdout)
        if not isinstance(servers, list):
            raise ValueError("invalid server metadata")
        names = [server["name"] for server in servers]
        if any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name)
               for name in names) or len(names) != len(set(names)):
            raise ValueError("invalid server names")
        kinds = {server["name"]: server["transport"]["type"] for server in servers}
        if any(kind not in ("stdio", "streamable_http") for kind in kinds.values()):
            raise ValueError("unsupported MCP transport")
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        raise HookAdmissionError(
            "cannot verify scoped MCP configuration; no model started") from None
    narrowed = []
    for name in sorted(names):
        if name != "kindex":
            # Plugin entries need a valid transport even when disabled. Never copy
            # command arguments, headers, credentials or endpoint values from metadata.
            transport = ('command="/usr/bin/false"' if kinds[name] == "stdio"
                         else 'url="http://127.0.0.1:1"')
            narrowed += ["-c", f"mcp_servers.{name}={{enabled=false,{transport}}}"]
    # Global config arguments precede the subcommand/prompt, including on resume.
    return [command[0], *narrowed,
            *(arg for arg in command[1:] if arg != "--ignore-user-config")]


def required_override(command: list[str]) -> str | None:
    if not command or pathlib.Path(command[0]).name != "codex":
        return None
    overrides = []
    for index, arg in enumerate(command):
        if arg in ("-c", "--config") and index + 1 < len(command):
            overrides.append(command[index + 1])
        elif arg.startswith("--config="):
            overrides.append(arg.partition("=")[2])
        elif arg.startswith("-c") and len(arg) > 2:
            overrides.append(arg[2:])
    required = []
    overrides_disabled = False
    for value in overrides:
        try:
            parsed = tomllib.loads(value)
        except ValueError:
            if value.startswith(("hooks", "features")):
                raise HookAdmissionError("required hook settings are malformed") from None
            continue
        if "hooks" in parsed:
            required.append(value)
        features = parsed.get("features", {})
        if isinstance(features, dict) and (features.get("hooks") is False
                                          or features.get("codex_hooks") is False):
            overrides_disabled = True
    if not required:
        return None
    if len(required) != 1 or not required[0].startswith("hooks.PreToolUse="):
        raise HookAdmissionError("required Codex hook configuration is ambiguous")
    if overrides_disabled or any(arg in ("--disable=hooks", "--disable=codex_hooks") or
           (arg == "--disable" and command[index + 1:index + 2] in
            (["hooks"], ["codex_hooks"]))
           for index, arg in enumerate(command)):
        raise HookAdmissionError("required Codex hooks are disabled")
    return required[0]


def validate_listing(response: Any, override: str, cwd: pathlib.Path) -> dict[str, Any]:
    try:
        expected = tomllib.loads(override)["hooks"]["PreToolUse"]
        if len(expected) != 1 or expected[0].get("matcher") not in (None, "", "*"):
            raise ValueError("unexpected matcher")
        handlers = expected[0]["hooks"]
        if len(handlers) != 1 or handlers[0]["type"] != "command":
            raise ValueError("unexpected handler")
        handler = handlers[0]
        entries = response["result"]["data"]
        matching: list[dict[str, Any]] = []
        for entry in entries:
            if pathlib.Path(entry["cwd"]).resolve() != cwd.resolve() or entry.get("errors"):
                raise ValueError("hook source inspection failed")
            matching.extend(h for h in entry["hooks"] if
                            h.get("command") == handler["command"] and
                            h.get("eventName") == "preToolUse" and
                            h.get("source") == "sessionFlags")
        if len(matching) != 1:
            raise ValueError("required hook missing or ambiguous")
        hook = matching[0]
        if (hook.get("enabled") is not True or hook.get("trustStatus") != "trusted"
                or hook.get("matcher") not in (None, "", "*")
                or hook.get("timeoutSec") != handler.get("timeout", 600)
                or not isinstance(hook.get("currentHash"), str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", hook["currentHash"])):
            raise ValueError("required hook not trusted/enabled")
        return {"event": "preToolUse", "trust": "trusted", "hash": hook["currentHash"],
                "source": "sessionFlags"}
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise HookAdmissionError(
            "required Codex hook is absent, invalid, disabled or untrusted; "
            "review this exact hook with Codex /hooks before launching; no model started"
        ) from error


def check_command(command: list[str], cwd: pathlib.Path, *, timeout: float = 10) -> dict | None:
    override = required_override(command)
    if override is None:
        return None
    # This only lists metadata. It does not execute a hook or start a provider session.
    process = subprocess.Popen([command[0], "-c", "features.hooks=true", "-c", override,
                                "app-server", "--stdio"], cwd=cwd,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)
    assert process.stdin is not None and process.stdout is not None
    stdout = process.stdout
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    buffer = b""
    received = 0

    def send(value: dict) -> None:
        assert process.stdin is not None
        process.stdin.write(json.dumps(value).encode() + b"\n")
        process.stdin.flush()

    def response(identifier: int) -> dict:
        nonlocal buffer, received
        while time.monotonic() < deadline:
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise HookAdmissionError("Codex hook inspection returned malformed metadata")
                if value.get("id") == identifier:
                    if "error" in value:
                        raise HookAdmissionError("Codex hook inspection refused")
                    return value
            if not selector.select(min(.1, max(0, deadline - time.monotonic()))):
                continue
            chunk = os.read(stdout.fileno(), 65536)
            if not chunk:
                raise HookAdmissionError("Codex hook inspection exited without evidence")
            buffer += chunk
            received += len(chunk)
            if received > 1024 * 1024:
                raise HookAdmissionError("Codex hook inspection exceeded its output bound")
        raise HookAdmissionError("Codex hook inspection timed out; no model started")

    try:
        send({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "factory-hook-admission", "version": "1"},
            "capabilities": {"experimentalApi": True}}})
        response(1)
        send({"method": "initialized", "params": {}})
        send({"id": 2, "method": "hooks/list", "params": {"cwds": [str(cwd)]}})
        return validate_listing(response(2), override, cwd)
    except HookAdmissionError:
        raise
    except (OSError, ValueError, TypeError) as error:
        raise HookAdmissionError(
            "required Codex hook admission failed; no model started") from error
    finally:
        selector.close()
        with contextlib.suppress(OSError):
            process.stdin.close()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        process.stdout.close()
