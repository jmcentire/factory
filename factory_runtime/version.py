"""Identify the Factory build, independently of the target repository's revision."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from factory_core import __version__


def build_identity() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    result: dict[str, Any] = {
        "version": __version__, "revision": None, "dirty": None,
        "harness_schema": "factory-harness/3", "python": sys.version.split()[0],
    }
    if not (root / "pyproject.toml").is_file() or not (root / ".git").exists():
        return result
    try:
        from factory_runtime.host_git import run_host_git
        result["revision"] = run_host_git(root, ["rev-parse", "HEAD"]).decode().strip()
        result["dirty"] = bool(run_host_git(
            root, ["status", "--porcelain", "--untracked-files=normal"]
        ))
    except (OSError, RuntimeError, subprocess.SubprocessError):
        pass  # Installed distributions need not be Git checkouts; unknown is explicit.
    return result


def format_version(*, as_json: bool = False) -> str:
    identity = build_identity()
    if as_json:
        return json.dumps(identity, sort_keys=True)
    revision = identity["revision"] or "revision unknown"
    dirty = " dirty" if identity["dirty"] else ""
    return f"factory {identity['version']} ({revision}{dirty})"
