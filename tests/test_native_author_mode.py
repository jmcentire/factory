"""Explicit native authoring changes ignition truth, not execution authority."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from factory_runtime.resources import ResourceLedger
from tests.conftest import promoting_promotion_inputs, write_promoting_chain
from tests.test_harness_scripts import (
    HARNESS,
    _add_current_harness_contract,
    _dispatch_env,
    _factory_cli_env,
    _make_run,
    _write_green_endgame_admission,
    dispatch_success_fixture,
    execution_truth_fixture,
    factory_ignition_env,
    run,
)

TASK = "Prepare reviewable changes without claiming unperformed qualified execution."


def _prepared(tmp_path: Path):
    operator, root, _ = execution_truth_fixture(tmp_path, task=TASK, harness_status=None)
    env, tmux_log = factory_ignition_env(tmp_path, root)
    return operator, root, env, tmux_log


def _ignite(operator: Path, root: Path, env: dict[str, str], *options: str):
    return run(
        ["bash", str(HARNESS / "factory.sh"), "r1", TASK, "--runs", str(root.parent), *options],
        operator, env,
    )


def _metadata(root: Path) -> dict:
    return json.loads((root / "harness.json").read_text())


def _validator_prompt(tmux_log: Path) -> str:
    line = next(line for line in tmux_log.read_text().splitlines() if "-n validator " in line)
    command = line.split(" -c ", 1)[1].split(" ", 1)[1]
    prompt = shlex.split(command)[-1]
    assert "Act as the Validator" in prompt
    return prompt


def _assert_open_without_qualification(root: Path) -> None:
    metadata = _metadata(root)
    assert metadata["status"] == "open"
    assert metadata["author_lane_mode"] == "native-tmux"
    assert metadata["launcher_qualification"] == "UNQUALIFIED_NATIVE_TMUX"
    assert metadata["lane_isolation"] == "UNQUALIFIED_NATIVE_TMUX"
    assert not (root / "promotion_verdict.json").exists()
    assert not (root / "tmux-lanes").exists()
    assert not (root / "evidence/runner").exists()


@pytest.mark.parametrize("options", [(), ("--author-lanes", "qualified")])
def test_qualified_author_mode_remains_the_default_and_explicit_equivalent(
    tmp_path: Path, options: tuple[str, ...],
) -> None:
    operator, root, env, tmux_log = _prepared(tmp_path)

    result = _ignite(operator, root, env, "--budget", "5", *options)

    assert result.returncode == 0, result.stdout + result.stderr
    metadata = _metadata(root)
    assert metadata["author_lane_mode"] == "qualified"
    assert metadata["launcher_qualification"] == "QUALIFIED_PR2"
    assert metadata["lane_isolation"] == "QUALIFIED_PR2"
    assert metadata["budget_usd"] == 5
    assert metadata["budget_enforcement"] == "reserved-runner-ceilings"
    assert metadata["status"] == "open"
    prompt = _validator_prompt(tmux_log)
    assert "only through the qualified harness/dispatch_lane.sh runner" in prompt
    assert "typed broker" in prompt


def test_native_author_mode_ignites_truthfully_and_briefs_the_native_pr_route(
    tmp_path: Path,
) -> None:
    operator, root, env, tmux_log = _prepared(tmp_path)
    retained = (root / "run.json").read_bytes()

    result = _ignite(operator, root, env, "--author-lanes", "native-tmux", "--budget", "5")

    assert result.returncode == 0, result.stdout + result.stderr
    _assert_open_without_qualification(root)
    metadata = _metadata(root)
    assert metadata["budget_usd"] == 5
    assert metadata["budget_enforcement"] == "not-enforced-native-authoring"
    assert (root / "run.json").read_bytes() == retained
    assert (root / "TASK.md").read_text() == TASK
    prompt = _validator_prompt(tmux_log)
    assert "harness/tmux_lane.sh" in prompt
    assert "unqualified" in prompt.lower()
    assert "reviewable" in prompt.lower() and "PR" in prompt
    assert "endgame" in prompt.lower() and "promotion" in prompt.lower()
    assert all(term in prompt.lower() for term in (
        "cannot", "close", "promote", "fresh", "authorized", "qualified",
    ))
    assert "only through the qualified harness/dispatch_lane.sh runner" not in prompt


@pytest.mark.parametrize("options", [
    ("--author-lanes", "native"),
    ("--author-lanes",),
    ("--author-lanes", "qualified", "--author-lanes", "native-tmux"),
    ("--author-lanes", "qualified", "--author-lanes", "qualified"),
])
def test_author_mode_malformed_or_repeated_choice_has_no_ignition_effect(
    tmp_path: Path, options: tuple[str, ...],
) -> None:
    operator, root, env, tmux_log = _prepared(tmp_path)
    before = ResourceLedger(root, "r1").latest()
    retained = (root / "run.json").read_bytes()

    result = _ignite(operator, root, env, *options)

    assert result.returncode == 64, result.stdout + result.stderr
    assert not (root / "harness.json").exists()
    assert not (root / "TASK.md").exists()
    assert not tmux_log.exists()
    assert ResourceLedger(root, "r1").latest() == before
    assert (root / "run.json").read_bytes() == retained


@pytest.mark.parametrize("first,second", [
    ("qualified", "native-tmux"), ("native-tmux", "qualified"),
])
def test_existing_author_mode_cannot_be_changed_by_another_ignition(
    tmp_path: Path, first: str, second: str,
) -> None:
    operator, root, env, tmux_log = _prepared(tmp_path)
    initial = _ignite(operator, root, env, "--author-lanes", first)
    assert initial.returncode == 0, initial.stdout + initial.stderr
    files = {name: (root / name).read_bytes() for name in ("harness.json", "TASK.md", "run.json")}
    resources = ResourceLedger(root, "r1").latest()
    launches = tmux_log.read_bytes()

    result = _ignite(operator, root, env, "--author-lanes", second)

    assert result.returncode != 0
    assert {name: (root / name).read_bytes() for name in files} == files
    assert ResourceLedger(root, "r1").latest() == resources
    assert tmux_log.read_bytes() == launches


def test_native_choice_never_adopts_or_rewrites_existing_legacy_harness(tmp_path: Path) -> None:
    operator, root, _ = execution_truth_fixture(tmp_path, task=TASK, harness_status="open")
    env, tmux_log = factory_ignition_env(tmp_path, root)
    before = (root / "harness.json").read_bytes()
    assert "author_lane_mode" not in _metadata(root)

    result = _ignite(operator, root, env, "--author-lanes", "native-tmux")

    assert result.returncode != 0
    assert (root / "harness.json").read_bytes() == before
    assert not tmux_log.exists()


@pytest.mark.parametrize("mode,qualification,allowed", [
    ("qualified", "QUALIFIED_PR2", True),
    ("native-tmux", "UNQUALIFIED_NATIVE_TMUX", False),
    ("native-tmux", "QUALIFIED_PR2", False),
])
def test_native_author_mode_cannot_bypass_the_existing_qualified_close_boundary(
    tmp_path: Path, mode: str, qualification: str, allowed: bool,
) -> None:
    root = _make_run(tmp_path)
    metadata = _add_current_harness_contract(root)
    metadata.update({"author_lane_mode": mode, "launcher_qualification": qualification,
                     "lane_isolation": qualification})
    (root / "harness.json").write_text(json.dumps(metadata))
    admission = _write_green_endgame_admission(root, metadata)
    (root / "promotion_inputs.json").write_text(json.dumps(promoting_promotion_inputs()))
    write_promoting_chain(root)

    result = run(
        ["bash", str(HARNESS / "promote.sh"), "r1", "--endgame-admission", str(admission)],
        tmp_path, _factory_cli_env(),
    )

    if allowed:
        assert result.returncode == 0, result.stdout + result.stderr
        assert _metadata(root)["status"] == "closed"
    else:
        assert result.returncode != 0, result.stdout + result.stderr
        assert _metadata(root)["status"] == "open"
        assert _metadata(root)["launcher_qualification"] == qualification


@pytest.mark.parametrize("mode", ["qualified", "native-tmux"])
def test_author_mode_never_substitutes_for_missing_qualified_runner_configuration(
    tmp_path: Path, mode: str,
) -> None:
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=True)
    metadata = _metadata(root)
    qualification = "QUALIFIED_PR2" if mode == "qualified" else "UNQUALIFIED_NATIVE_TMUX"
    metadata.update({"author_lane_mode": mode, "launcher_qualification": qualification,
                     "lane_isolation": qualification})
    (root / "harness.json").write_text(json.dumps(metadata))
    environment = _dispatch_env(stub, root)
    del environment["FACTORY_BROKER_REGISTRY_DIR"]

    result = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd, environment,
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert not (tmp_path / "boundary.log").exists()
    assert _metadata(root)["status"] == "open"
    assert _metadata(root)["launcher_qualification"] == qualification


@pytest.mark.parametrize("qualification,isolation", [
    ("UNQUALIFIED_NATIVE_TMUX", "UNQUALIFIED_NATIVE_TMUX"),
    ("UNQUALIFIED_NATIVE_TMUX", "QUALIFIED_PR2"),
    ("QUALIFIED_PR2", "UNQUALIFIED_NATIVE_TMUX"),
])
def test_native_qualification_labels_without_mode_cannot_enter_legacy_close_path(
    tmp_path: Path, qualification: str, isolation: str,
) -> None:
    root = _make_run(tmp_path)
    metadata = _add_current_harness_contract(root)
    assert "author_lane_mode" not in metadata
    metadata.update({"launcher_qualification": qualification, "lane_isolation": isolation})
    (root / "harness.json").write_text(json.dumps(metadata))
    admission = _write_green_endgame_admission(root, metadata)
    (root / "promotion_inputs.json").write_text(json.dumps(promoting_promotion_inputs()))
    write_promoting_chain(root)

    result = run(
        ["bash", str(HARNESS / "promote.sh"), "r1", "--endgame-admission", str(admission)],
        tmp_path, _factory_cli_env(),
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert _metadata(root)["status"] == "open"
    assert "author_lane_mode" not in _metadata(root)
