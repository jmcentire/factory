"""Probe registration must agree with ordinary Python test-module collection."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def test_gate_collects_future_annotation_dataclass_modules_and_refuses_unknown_probes(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    scripts = tmp_path / "scripts"
    tests = tmp_path / "tests"
    harness = tmp_path / "harness"
    scripts.mkdir()
    tests.mkdir()
    harness.mkdir()
    script = scripts / "check_denial_probes.py"
    shutil.copyfile(repository / "scripts" / script.name, script)
    module = tests / "test_future_dataclass.py"
    module.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class Observation:\n"
        "    value: int\n\n"
        "def test_observation():\n"
        "    assert Observation(3).value == 3\n",
        encoding="utf-8",
    )
    (tests / "test_control.py").write_text(
        "def test_control():\n    assert True\n", encoding="utf-8"
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"GATES_TSV", "DENIAL_PROBE_NODEIDS", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"}
    }
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    ordinary = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(module)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ordinary.returncode == 0, ordinary.stderr or ordinary.stdout
    assert "test_future_dataclass.py::test_observation" in ordinary.stdout
    registry = harness / "gates.tsv"

    def register(probe: str) -> None:
        registry.write_text(
            "REG\tFixture observation\tMissing observation\t"
            f"tests/test_future_dataclass.py::{probe}\tDelete the fixture observation\n",
            encoding="utf-8",
        )

    command = [sys.executable, str(script), "--registry", str(registry)]
    register("test_observation")
    accepted = subprocess.run(
        command, cwd=tmp_path, env=environment, capture_output=True, text=True, check=False
    )
    assert accepted.returncode == 0, accepted.stderr or accepted.stdout
    assert "GREEN" in accepted.stdout

    register("test_not_present")
    refused = subprocess.run(
        command, cwd=tmp_path, env=environment, capture_output=True, text=True, check=False
    )
    assert refused.returncode == 1, refused.stderr or refused.stdout
    assert "does not collect" in refused.stderr
    assert "test_not_present" in refused.stderr
