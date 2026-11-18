"""tmux_lane.sh resets PYTHONPATH to the harness at start-up, so each harness script it
runs that imports factory_core or factory_runtime must get the package root back on that
call. Packaged installs (no editable install) failed to launch author lanes without it."""

import os
from pathlib import Path
import re
import subprocess
import sys

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "harness"
SCRIPT = (HARNESS / "tmux_lane.sh").read_text()
CALL = re.compile(r'(?P<root>PYTHONPATH="\$PYTHONPATH:\$REPO_ROOT"\s+)?"\$FACTORY_PYTHON"\s*(?:\\\n\s*)?"\$D/(?P<name>\w+)\.py"')
PACKAGE_IMPORT = re.compile(r"^(?:from|import)\s+factory_(?:core|runtime)\b", re.M)


def package_calls():
    for match in CALL.finditer(SCRIPT):
        if PACKAGE_IMPORT.search((HARNESS / f"{match['name']}.py").read_text()):
            yield match["name"], bool(match["root"])


def test_finds_the_lane_calls_that_import_the_package():
    assert {"lane_kindex", "lane_dialogue"} <= {name for name, _ in package_calls()}


def test_each_package_importing_call_imports_under_the_lane_pythonpath():
    # -S skips site-packages and .pth files, so only an explicit path can supply the package;
    # jsonschema's directory is added because it is the one runtime dependency.
    dependency = str(Path(jsonschema.__file__).resolve().parents[1])
    for name, adds_root in package_calls():
        path = [str(HARNESS / "trusted_startup"), str(HARNESS), dependency] + ([str(ROOT)] if adds_root else [])
        result = subprocess.run([sys.executable, "-S", str(HARNESS / f"{name}.py"), "--help"],
                                env={"PYTHONSAFEPATH": "1", "PYTHONPATH": os.pathsep.join(path), "PATH": "/usr/bin:/bin"},
                                capture_output=True, text=True, timeout=60)
        assert "ModuleNotFoundError" not in result.stderr, f"{name}: {result.stderr[-400:]}"
