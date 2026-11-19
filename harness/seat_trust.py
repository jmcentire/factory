"""Trust a seat's own working folder before its agent starts, so it does not wait at a
first-launch trust question.

Only the folder the seat runs in is trusted: a lane's own clone, or the run's repository for a
coordinator. For Codex, the only hooks trusted are the lane's own report hooks, the project file
``.codex/hooks.json`` in that folder, which this Factory wrote. Codex's own app-server reads the
hooks and writes the trust, so its configuration keeps its own format. Claude Code's trust is
the project entry in its own ``.claude.json``. Best effort: on any failure the agent asks its
question as before. Opt-in: only with FACTORY_SEAT_TRUST=1, since it writes the operator's
agent profiles.
"""
from __future__ import annotations

import json
import os
import pathlib
import selectors
import subprocess
import tempfile
import time
from typing import Mapping


def enabled(environ: Mapping[str, str]) -> bool:
    return environ.get("FACTORY_SEAT_TRUST") == "1"


def claude(cwd: pathlib.Path, environ: Mapping[str, str]) -> bool:
    """Mark ``cwd`` trusted in the Claude Code profile the seat uses; True if it wrote."""
    base = environ.get("CLAUDE_CONFIG_DIR")
    home = environ.get("HOME")
    if not base and not home:
        return False
    path = pathlib.Path(base) / ".claude.json" if base else pathlib.Path(home) / ".claude.json"
    # Never create or replace through a link a profile Claude Code has not written.
    if not path.is_file() or path.is_symlink():
        return False
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        return False
    projects = data.setdefault("projects", {})
    entry = projects.setdefault(str(cwd.resolve()), {})
    if not isinstance(entry, dict) or entry.get("hasTrustDialogAccepted") is True:
        return False
    entry["hasTrustDialogAccepted"] = True
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".claude.json.factory-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        pathlib.Path(temporary).unlink(missing_ok=True)
    return True


def codex(executable: str, cwd: pathlib.Path, environ: Mapping[str, str], *,
          timeout: float = 15) -> list[str]:
    """Trust ``cwd`` and its own ``.codex/hooks.json`` hooks in Codex's configuration, through
    Codex's app-server. Returns the hook keys it trusted."""
    folder = str(cwd.resolve())
    own_hooks = str(cwd.resolve() / ".codex/hooks.json")
    process = subprocess.Popen([executable, "app-server", "--stdio"], cwd=folder, env=dict(environ),
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)
    assert process.stdin is not None and process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    buffer = b""

    def call(identifier: int, method: str, params: dict) -> dict:
        nonlocal buffer
        process.stdin.write(json.dumps({"id": identifier, "method": method,
                                        "params": params}).encode() + b"\n")
        process.stdin.flush()
        while time.monotonic() < deadline:
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                value = json.loads(line)
                if isinstance(value, dict) and value.get("id") == identifier:
                    if "error" in value:
                        raise ValueError(f"Codex refused {method}")
                    return value["result"]
            if selector.select(min(.1, max(0, deadline - time.monotonic()))):
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise ValueError("Codex app-server exited")
                buffer += chunk
        raise ValueError(f"Codex {method} timed out")

    try:
        call(1, "initialize", {"clientInfo": {"name": "factory-seat-trust", "version": "1"},
                               "capabilities": {"experimentalApi": True}})
        process.stdin.write(b'{"method":"initialized","params":{}}\n')
        process.stdin.flush()
        call(2, "config/value/write", {"keyPath": "projects", "mergeStrategy": "upsert",
                                       "value": {folder: {"trust_level": "trusted"}}})
        listed = call(3, "hooks/list", {"cwds": [folder]})
        own = {hook["key"]: hook["currentHash"]
               for entry in listed["data"] for hook in entry["hooks"]
               if hook.get("source") == "project" and hook.get("sourcePath") == own_hooks
               and hook.get("trustStatus") != "trusted"}
        if own:
            call(4, "config/value/write", {
                "keyPath": "hooks", "mergeStrategy": "upsert",
                "value": {"state": {key: {"trusted_hash": digest} for key, digest in own.items()}}})
        return sorted(own)
    finally:
        selector.close()
        process.stdin.close()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        process.stdout.close()
