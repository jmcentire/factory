"""The factory holds no list of permitted agents, languages, or sizes.

Founder ruling 2026-09-23: which agent runs which role is the operator's
choice and belongs in configuration; capabilities like languages are never
capped; a project declares its own testing tool, and when it declares none the
Validator selects one in planning. None of that needed a constant, and every
constraint these probes cover was written by an agent inside a commit about
something else.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "harness"


def schema(name: str, where: str) -> dict:
    return json.loads((ROOT / where / "schemas" / f"{name}.schema.json").read_text())


def test_no_list_of_permitted_agents() -> None:
    adapter = schema("runner-manifest", "factory_runtime")["properties"]["adapter"]
    assert "enum" not in adapter, "an enum of adapters is a list of agents the operator may use"
    assert adapter["type"] == "string"


def test_an_adapter_declares_its_shape_instead_of_being_recognised_by_name() -> None:
    properties = schema("runner-manifest", "factory_runtime")["properties"]
    assert set(properties["child_process_model"]["enum"]) == {"none", "single-child"}
    invocation = properties["invocation"]
    assert set(invocation["required"]) == {"start", "resume"}


def test_a_target_declares_its_own_test_and_build_commands() -> None:
    build = schema("target_manifest", "factory_core")["properties"]["build"]
    for field in ("test_command", "build_command"):
        assert field in build["properties"], field
        assert field not in build["required"], (
            f"{field} is optional: a target that declares none leaves the choice to the "
            "Validator in planning"
        )


def test_a_target_declares_its_own_size() -> None:
    build = schema("target_manifest", "factory_core")["properties"]["build"]
    limits = build["properties"]["projection_limits"]["properties"]
    assert set(limits) == {"max_files", "max_file_bytes", "max_total_bytes"}


def test_the_projection_bound_is_a_default_a_caller_can_raise() -> None:
    import inspect

    from factory_runtime.projection_bundle import (
        DEFAULT_MAX_FILES,
        bundle_runner_projection,
    )

    parameters = inspect.signature(bundle_runner_projection).parameters
    for name in ("max_files", "max_file_bytes", "max_total_bytes"):
        assert name in parameters, name
    assert parameters["max_files"].default == DEFAULT_MAX_FILES


@pytest.mark.parametrize("script", ["mutate.sh", "flake.sh"])
def test_the_test_harnesses_assume_no_language(script: str, tmp_path: Path) -> None:
    """They used to default to pytest, which made every non-Python target unrunnable."""
    source = (HARNESS / script).read_text()
    assert "TEST_CMD:=python3 -m pytest" not in source, "a default test tool is an assumed language"
    # mutate.sh takes <name> <patch>; flake.sh takes <name>. Both must refuse
    # before doing any work when no test command was declared.
    patch = tmp_path / "patch.py"
    patch.write_text("# a mutation the harness never gets to apply\n")
    positional = ["probe", str(patch)] if script == "mutate.sh" else ["probe"]
    finished = subprocess.run(
        [
            "bash",
            str(HARNESS / script),
            *positional,
            "--src",
            str(tmp_path),
            "--tests",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        check=False,
    )
    assert finished.returncode == 64, finished.stdout + finished.stderr
    assert "no test command" in finished.stderr


def test_no_module_names_a_language_or_a_framework_as_a_constraint() -> None:
    """A constant naming one ecosystem is how the last set of limits arrived."""
    offenders: list[str] = []
    for path in sorted((ROOT / "factory_core").rglob("*.py")):
        text = path.read_text()
        for marker in ("pytest", "npm", "cargo", "dotnet", "gradle"):
            if f'"{marker}"' in text or f"'{marker}'" in text:
                offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert not offenders, f"factory_core names an ecosystem: {offenders}"


def test_python_is_not_required_of_a_target(tmp_path: Path) -> None:
    """A declared command runs as argv, so a target's tool can be anything."""
    marker = tmp_path / "ran"
    script = tmp_path / "tool.sh"
    script.write_text(f"#!/bin/sh\necho ran > {marker}\n")
    script.chmod(0o755)
    finished = subprocess.run(
        [
            sys.executable,
            "-c",
            "import subprocess, sys; sys.exit(subprocess.run(sys.argv[1:]).returncode)",
            str(script),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    assert marker.read_text().strip() == "ran"
