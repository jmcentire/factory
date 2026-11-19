"""Identify the Factory build, independently of the target repository's revision."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from factory_core import __version__
from factory_runtime import host_git


def _stamped_revision(stamp: Path) -> str | None:
    refusal = f"{stamp} must hold one full lowercase Git revision and nothing else"
    try:
        text = stamp.read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    except UnicodeDecodeError as exc:
        raise ValueError(refusal) from exc
    revision = text.removesuffix("\n")
    if not host_git.OBJECT_ID.fullmatch(revision):
        raise ValueError(refusal)
    return revision


def build_identity() -> dict[str, Any]:
    module = Path(__file__).resolve()
    root = module.parents[1]
    result: dict[str, Any] = {
        "name": "Factory", "version": __version__, "revision": None, "dirty": None,
        "harness_schema": "factory-harness/3", "python": sys.version.split()[0],
    }
    if not (root / "pyproject.toml").is_file() or not (root / ".git").exists():
        stamped = _stamped_revision(module.with_name("BUILD_REVISION"))
        if stamped is not None:
            result["revision"], result["dirty"] = stamped, False
        return result
    try:
        result["revision"] = host_git.run_host_git(root, ["rev-parse", "HEAD"]).decode().strip()
        result["dirty"] = bool(host_git.run_host_git(
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
