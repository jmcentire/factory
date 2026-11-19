"""The lane launchers name their helpers' import path themselves (top-of-script export and
the rebuilt environments for preflight, wrapper and watcher). Harness helpers import
factory_core and factory_runtime, directly or lazily, so every such path must reach the
package root. Packaged installs (no editable install) failed to launch author lanes when
it did not."""

import os
import re
import subprocess
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "harness"
LAUNCHERS = ("tmux_lane.sh", "tmux_lane_message.sh")
# The value each launcher gives PYTHONPATH wherever it names trusted_startup,
# either quoted or as printf's %q argument.
ASSIGNMENT = re.compile(
    r'PYTHONPATH=(?:"(?P<quoted>[^"]*trusted_startup[^"]*)"'
    r"""|%q'\s+"(?P<printf>[^"]*trusted_startup[^"]*)")"""
)
VARIABLES = {"_factory_harness": str(HARNESS), "D": str(HARNESS), "REPO_ROOT": str(ROOT)}


def helper_paths():
    for launcher in LAUNCHERS:
        for match in ASSIGNMENT.finditer((HARNESS / launcher).read_text()):
            expression = match["quoted"] or match["printf"]
            value = subprocess.run(
                ["bash", "-c", f'printf %s "{expression}"'],
                capture_output=True, text=True, check=True,
                env={**VARIABLES, "PATH": "/usr/bin:/bin"},
            ).stdout
            yield launcher, expression, value


def test_every_launcher_names_its_helper_paths():
    found = list(helper_paths())
    assert {launcher for launcher, _, _ in found} == set(LAUNCHERS)
    assert len(found) >= 6


def test_each_helper_path_reaches_the_package():
    # -S skips site-packages and .pth files, so no installed or editable copy can stand in
    # for the path; jsonschema's directory is added because it is the one runtime dependency.
    dependency = str(Path(jsonschema.__file__).resolve().parents[1])
    probe = "import factory_core.engagement, factory_runtime.kindex_workspace"
    for launcher, expression, value in helper_paths():
        assert all(os.path.isabs(entry) for entry in value.split(":")), (launcher, expression)
        result = subprocess.run(
            [sys.executable, "-S", "-c", probe],
            env={"PYTHONSAFEPATH": "1", "PYTHONPATH": f"{value}:{dependency}",
                 "PATH": "/usr/bin:/bin"},
            cwd="/", capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"{launcher} {expression}: {result.stderr[-400:]}"


def test_no_launcher_helper_path_reaches_the_agent():
    # The agent's environment drops the helpers' start-up isolation only when it is exactly
    # trusted_start.TRUSTED_STARTUP; a launcher value that differs would leak Factory's
    # import path into every python the agent runs in the user's repository.
    from harness import trusted_start

    for launcher, expression, value in helper_paths():
        given = trusted_start.provider_environment(
            {"PYTHONSAFEPATH": "1", "PYTHONPATH": value, "HOME": "/home/operator"})
        assert "PYTHONPATH" not in given and "PYTHONSAFEPATH" not in given, (
            launcher, expression, value, trusted_start.TRUSTED_STARTUP)
        assert given["HOME"] == "/home/operator"
