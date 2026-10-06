"""Forcing tests for harness/factory_python.sh: harness scripts run factory code on the factory's
own interpreter, never on whichever `python3` is first on PATH, and stop with the fix when there
is no usable one."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PRELUDE = REPO / "harness" / "factory_python.sh"
PROBE = 'source "$1/harness/factory_python.sh"; python3 -c "import sys; print(sys.executable)"'


def _checkout(tmp_path: Path, *, venv: bool) -> Path:
    root = tmp_path / "checkout"
    (root / "harness").mkdir(parents=True)
    shutil.copy2(PRELUDE, root / "harness" / "factory_python.sh")
    if venv:
        (root / ".venv" / "bin").mkdir(parents=True)
        (root / ".venv" / "bin" / "python").symlink_to(sys.executable)
    return root


def _bad_python3(tmp_path: Path) -> Path:
    """A `python3` that cannot run the factory, first on PATH — a clean Mac's 3.9, say."""

    bindir = tmp_path / "bad-bin"
    bindir.mkdir()
    bad = bindir / "python3"
    bad.write_text("#!/bin/sh\necho BAD-PYTHON\nexit 1\n", encoding="utf-8")
    bad.chmod(0o755)
    return bindir


def _probe(root: Path, path: str, **extra: str) -> subprocess.CompletedProcess[str]:
    env = {"PATH": path, "HOME": os.environ.get("HOME", "/tmp"), **extra}
    return subprocess.run(["bash", "-c", PROBE, "probe", str(root)], capture_output=True,
                          text=True, env=env, check=False)


def test_bare_python3_runs_the_checkout_venv_not_the_path(tmp_path: Path) -> None:
    root = _checkout(tmp_path, venv=True)
    done = _probe(root, f"{_bad_python3(tmp_path)}:/usr/bin:/bin")
    assert done.returncode == 0, done.stderr
    assert "BAD-PYTHON" not in done.stdout
    assert Path(done.stdout.strip()).resolve() == Path(sys.executable).resolve()


def test_without_a_usable_interpreter_the_script_stops_with_the_fix(tmp_path: Path) -> None:
    root = _checkout(tmp_path, venv=False)
    done = _probe(root, f"{_bad_python3(tmp_path)}:/usr/bin:/bin")
    assert done.returncode == 69
    assert "make install" in done.stderr
    assert "BAD-PYTHON" not in done.stdout


def test_an_explicit_factory_python_wins(tmp_path: Path) -> None:
    root = _checkout(tmp_path, venv=False)
    done = _probe(root, f"{_bad_python3(tmp_path)}:/usr/bin:/bin",
                  FACTORY_PYTHON=sys.executable)
    assert done.returncode == 0, done.stderr
    assert Path(done.stdout.strip()).resolve() == Path(sys.executable).resolve()


def test_every_harness_python_call_is_covered() -> None:
    """A harness script that runs factory code without the prelude would silently fall back to
    PATH. Scripts that run the target's code or a caller's command are the declared exceptions."""

    target_interpreter = {"mutate.sh", "flake.sh", "receipt.sh"}
    not_entry_points = {"factory_python.sh", "model_availability.sh", "inject.sh"}
    uncovered = []
    for script in sorted((REPO / "harness").glob("*.sh")):
        if script.name in target_interpreter | not_entry_points:
            continue
        text = script.read_text(encoding="utf-8")
        if "python3" in text and "factory_python.sh" not in text:
            uncovered.append(script.name)
        if "exec python3" in text:
            uncovered.append(f"{script.name}: exec python3 bypasses the function")
    assert uncovered == []
