"""Forcing tests for the glossary checker (gate GLO, plan 5.2 ruling 5a)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECK = REPO / "scripts" / "check_glossary.py"


def _module():
    spec = importlib.util.spec_from_file_location("check_glossary", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_glossary_is_green() -> None:
    """The committed glossary verifies end-to-end — a stale entry here means a
    referent changed without its definition being re-read."""
    proc = subprocess.run(
        [sys.executable, str(CHECK)], capture_output=True, text=True, cwd=REPO
    )
    assert proc.returncode == 0, proc.stderr


def test_stale_referent_digest_is_red(tmp_path: Path) -> None:
    """The red_now: a referent whose source changed makes its recorded digest
    stale, and symbol_digest is what detects it."""
    module = _module()
    target = tmp_path / "mod.py"
    target.write_text("def anchor():\n    return 1\n", encoding="utf-8")
    original = module.symbol_digest(target, "anchor")
    target.write_text("def anchor():\n    return 2\n", encoding="utf-8")
    assert module.symbol_digest(target, "anchor") != original


def test_ghost_referent_is_red(tmp_path: Path) -> None:
    module = _module()
    target = tmp_path / "mod.py"
    target.write_text("def other():\n    return 1\n", encoding="utf-8")
    assert module.symbol_digest(target, "anchor") is None
