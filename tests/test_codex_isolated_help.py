"""The public Codex host CLI starts in an isolated, dependency-bearing virtualenv."""

import os
import subprocess
import sys
from pathlib import Path


def test_codex_help_starts_without_untrusted_python_startup(tmp_path):
    root = Path(__file__).resolve().parents[1]
    # Local test runs already use the project's dependency-bearing virtualenv.
    # CI may use a provisioned global interpreter: give it a real venv while
    # retaining that interpreter's installed dependencies, without network access.
    python = Path(sys.executable)
    if sys.prefix == sys.base_prefix:
        environment = tmp_path / "venv"
        subprocess.run(
            [str(python), "-m", "venv", "--without-pip", "--system-site-packages",
             str(environment)], check=True, capture_output=True, timeout=30,
        )
        python = environment / "bin" / "python"
    marker = tmp_path / "untrusted-startup-ran"
    poison = f"open({str(marker)!r}, 'w').write('executed')\nraise RuntimeError('untrusted')\n"
    workspace = tmp_path / "workspace"
    injected = tmp_path / "pythonpath"
    userbase = tmp_path / "userbase"
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    usersite = userbase / "lib" / version / "site-packages"
    for directory in (workspace, injected, usersite):
        directory.mkdir(parents=True)
        for name in ("json.py", "argparse.py", "sitecustomize.py", "usercustomize.py"):
            (directory / name).write_text(poison)
    # Prove the startup payload is executable when isolation is absent.
    control = subprocess.run(
        [str(python), "-S", "-c", "import json"], cwd=workspace,
        env={"PATH": os.defpath, "PYTHONPATH": str(injected)},
        capture_output=True, text=True, timeout=15,
    )
    assert control.returncode != 0 and marker.exists()
    marker.unlink()
    result = subprocess.run(
        [str(python), "-I", "-S", str(root / "harness" / "codex_lane_session.py"), "--help"],
        cwd=workspace,
        env={"PATH": os.defpath, "HOME": str(tmp_path), "PYTHONPATH": str(injected),
             "PYTHONUSERBASE": str(userbase)},
        capture_output=True, text=True, timeout=15,
    )
    assert not marker.exists(), result.stderr
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()
    assert "--help" in result.stdout
