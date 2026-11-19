"""FACTORY_AGENT_BIN: the entrypoints a run records are the ones every later launch uses.

Fixtures are temporary executables that report how they were started. No provider runs.
"""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from harness import run_record
from tests.test_coordinator_startup import launch as launch
from tests.test_harness_scripts import HARNESS, ordinary_message_fixture, run

CONTEXT = Path(__file__).resolve().parents[1] / "harness/run_context.sh"
REPORT = (
    f"#!{sys.executable}\n"
    "import json, os, sys\n"
    "print(json.dumps({'wrapper': sys.argv[0], 'argv': sys.argv[1:], 'env': dict(os.environ)}))\n"
)


def executable(path: Path, body: str = REPORT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


@pytest.fixture
def entrypoints(tmp_path, monkeypatch):
    """Requested before ``launch``, so the run it creates records this directory."""
    directory = tmp_path / "entrypoints"
    executable(directory / "claude")
    # A link to a wrapper kept elsewhere, the shape a shared launcher directory takes.
    (directory / "codex").symlink_to(executable(tmp_path / "wrappers" / "codex-in-devshell"))
    monkeypatch.setenv("FACTORY_AGENT_BIN", str(directory))
    return directory


def shell_context(runs, *arguments, ambient=None):
    script = (
        f"source {shlex.quote(str(CONTEXT))}\n"
        f"factory_load_context task {shlex.quote(str(runs))} {' '.join(arguments)} || exit $?\n"
        'printf "%s\\0" "$PATH" "${FACTORY_CLAUDE_LAUNCHER:-}"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "FACTORY_PYTHON": sys.executable, **(ambient or {})},
    )
    return result, result.stdout.split("\0")[:2]


def test_create_records_the_directory_and_each_resolved_entry(entrypoints, launch, tmp_path):
    root = launch[0]
    assert json.loads((root / "harness.json").read_text())["agent_bin"] == {
        "directory": str(entrypoints.resolve()),
        "entries": {
            "claude": str((entrypoints / "claude").resolve()),
            "codex": str((tmp_path / "wrappers" / "codex-in-devshell").resolve()),
        },
    }


@pytest.mark.parametrize("fault, message", [
    ("missing", r"FACTORY_AGENT_BIN\) is missing"),
    ("file", r"FACTORY_AGENT_BIN\) is not a directory"),
    ("relative", "must name one absolute directory"),
    ("empty", "contains no agent entrypoints"),
    ("unexpected", "unexpected entry: .*helper"),
    ("in-target", "outside the target and runs"),
    ("in-runs", "outside the target and runs"),
    ("links-into-target", "outside the target and runs"),
])
def test_create_refuses_an_unusable_directory_before_any_run_state(
    launch, tmp_path, monkeypatch, fault, message,
):
    root, repository, _, _, _, _ = launch
    runs = root.parent
    directory = tmp_path / "chosen"
    if fault == "file":
        directory.write_text("")
    elif fault == "relative":
        directory = Path("chosen")
    elif fault == "empty":
        (directory / "notes.txt").parent.mkdir()
        (directory / "notes.txt").write_text("not executable, so not an entry")
    elif fault == "unexpected":
        executable(directory / "codex")
        executable(directory / "helper")
    elif fault == "in-target":
        directory = repository / "tools"
        executable(directory / "codex")
    elif fault == "in-runs":
        directory = runs / "shared-bin"
        executable(directory / "codex")
    elif fault == "links-into-target":
        directory.mkdir()
        (directory / "codex").symlink_to(executable(repository / "tools" / "codex"))
    monkeypatch.setenv("FACTORY_AGENT_BIN", str(directory))
    with pytest.raises(ValueError, match=message):
        run_record.create(runs, "second", repository, b"Any task.\n")
    assert not (runs / "second").exists()


@pytest.mark.parametrize("change, message", [
    ("add", "added ollama"),
    ("remove", "removed codex"),
    ("retarget", "retargeted codex"),
    ("several", "added ollama, removed claude, retargeted codex"),
])
def test_a_changed_entry_set_refuses_launch_context_and_names_each_change(
    entrypoints, launch, tmp_path, capfdbinary, change, message,
):
    root = launch[0]
    runs = root.parent
    if change in ("add", "several"):
        executable(entrypoints / "ollama")
    if change in ("remove", "several"):
        (entrypoints / ("codex" if change == "remove" else "claude")).unlink()
    if change in ("retarget", "several"):
        (entrypoints / "codex").unlink()
        (entrypoints / "codex").symlink_to(executable(tmp_path / "wrappers" / "another"))
    with pytest.raises(ValueError, match=f"changed in {entrypoints}: {message}$"):
        run_record.context(runs, "task", with_agent_bin=True)
    with pytest.raises(ValueError, match="retained agent entrypoints changed"):
        run_record.start(runs, "task")
    # Inspection reads the record without consulting the directory.
    capfdbinary.readouterr()
    run_record.context(runs, "task")
    assert capfdbinary.readouterr().out.split(b"\0")[0] == str(root).encode()


def test_a_removed_directory_is_reported_as_missing(entrypoints, launch):
    runs = launch[0].parent
    entrypoints.rename(entrypoints.with_name("moved"))
    with pytest.raises(ValueError, match=r"FACTORY_AGENT_BIN\) is missing: .*entrypoints$"):
        run_record.context(runs, "task", with_agent_bin=True)


def test_a_file_that_is_not_executable_is_not_a_change(entrypoints, launch):
    runs = launch[0].parent
    (entrypoints / ".DS_Store").write_bytes(b"\0")
    loaded, (path, launcher) = shell_context(runs, "--agent-entrypoints")
    assert loaded.returncode == 0, loaded.stderr
    assert path == f"{entrypoints.resolve()}:/usr/bin:/bin" and launcher == "claude"


def test_launch_context_puts_the_run_entries_first_and_selects_its_claude(entrypoints, launch):
    runs = launch[0].parent
    ambient = {"FACTORY_CLAUDE_LAUNCHER": "cl", "FACTORY_AGENT_BIN": "/somewhere/else"}
    loaded, values = shell_context(runs, "--agent-entrypoints", ambient=ambient)
    assert loaded.returncode == 0, loaded.stderr
    assert values == [f"{entrypoints.resolve()}:/usr/bin:/bin", "claude"]
    # Inspection callers do not ask for entrypoints and keep their own environment.
    loaded, values = shell_context(runs, ambient=ambient)
    assert loaded.returncode == 0, loaded.stderr
    assert values == ["/usr/bin:/bin", "cl"]


def test_a_run_created_without_entrypoints_ignores_the_variable_afterwards(
    launch, tmp_path, monkeypatch,
):
    root, _, binary, capture, _, execute = launch
    assert "agent_bin" not in json.loads((root / "harness.json").read_text())
    later = tmp_path / "later"
    executable(later / "codex")
    monkeypatch.setenv("FACTORY_AGENT_BIN", str(later))
    loaded, values = shell_context(
        root.parent, "--agent-entrypoints", ambient={"FACTORY_CLAUDE_LAUNCHER": "cl"})
    assert loaded.returncode == 0, loaded.stderr
    assert values == ["/usr/bin:/bin", "cl"]
    started = execute(capture(), "validator")
    assert "wrapper" not in started
    assert started["env"]["PATH"].split(os.pathsep)[0] == str(binary)


def test_coordinators_start_the_recorded_entry_and_other_providers_use_path(
    entrypoints, launch, tmp_path, monkeypatch,
):
    _, _, binary, capture, _, execute = launch
    # Neither a later value of the variable nor an earlier PATH entry replaces the record.
    monkeypatch.setenv("FACTORY_AGENT_BIN", str(tmp_path / "not-this-one"))
    calls = capture()
    validator = execute(calls, "validator")
    assert validator["wrapper"] == str(entrypoints.resolve() / "codex")
    assert validator["env"]["PATH"].split(os.pathsep)[:2] == [
        str(entrypoints.resolve()), str(binary)]
    # The orchestrator runs agy, which this directory does not provide.
    orchestrator = execute(calls, "orchestrator")
    assert "wrapper" not in orchestrator
    assert orchestrator["env"]["PATH"] == validator["env"]["PATH"]


def test_a_claude_entry_outranks_the_operators_launcher_for_a_coordinator(
    entrypoints, launch, monkeypatch,
):
    _, _, _, capture, invocation, _ = launch
    monkeypatch.setenv("FACTORY_VALIDATOR_AGENT", "claude")
    monkeypatch.setenv("FACTORY_CLAUDE_LAUNCHER", "cl")
    _, environment, _ = invocation(capture(), "validator")
    assert environment["FACTORY_CLAUDE_LAUNCHER"] == "claude"
    assert environment["PATH"].split(os.pathsep)[0] == str(entrypoints.resolve())


def test_the_ollama_guard_asks_the_recorded_entry(tmp_path, monkeypatch, request):
    directory = tmp_path / "entrypoints"
    log = tmp_path / "ollama.calls"
    executable(directory / "ollama", (
        f"#!{sys.executable}\nimport json, sys\n"
        f"with open({str(log)!r}, 'a') as out: out.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    ))
    monkeypatch.setenv("FACTORY_AGENT_BIN", str(directory))
    _, _, binary, capture, invocation, _ = request.getfixturevalue("launch")
    # Ollama exists only as the run's entry: PATH alone would report it as not installed.
    assert not (binary / "ollama").exists()
    monkeypatch.setenv("FACTORY_VALIDATOR_AGENT", "ollama")
    monkeypatch.setenv("FACTORY_VALIDATOR_MODEL", "chosen-local:latest")
    _, _, command = invocation(capture(model_probe=True), "validator")
    assert command[0] == str(directory.resolve() / "ollama")
    assert json.loads(log.read_text().splitlines()[0]) == ["show", "chosen-local:latest"]


def test_a_live_ollama_lane_queues_through_the_recorded_codex_entry(tmp_path, monkeypatch):
    observed = tmp_path / "queue.json"
    wrapper = executable(tmp_path / "entrypoints" / "codex", (
        f"#!{sys.executable}\nimport json, pathlib, sys\n"
        f"pathlib.Path({str(observed)!r}).write_text(json.dumps(sys.argv[1:]))\n"
    ))
    monkeypatch.setenv("FACTORY_AGENT_BIN", str(wrapper.parent))
    operator, root, env, _ = ordinary_message_fixture(tmp_path)
    binary = tmp_path / "factory-bin"
    executable(binary / "ollama", '#!/bin/sh\n[ "$1" = show ] && [ "$2" = chosen-local:latest ]\n')
    # The tmux stand-in reports a live pane. Queueing itself runs the chosen executable.
    tmux = binary / "tmux"
    tmux.write_text(tmux.read_text().replace('printf "1\\n"', 'printf "0\\n"'))
    lane = root / "tmux-lanes"
    lane.mkdir()
    thread = "12345678-1234-4234-8234-123456789abc"
    (lane / "coder-thread-id").write_text(thread + "\n")
    (lane / "coder-launch.jsonl").write_text(json.dumps({
        "schema_version": "factory-tmux-lane-launch/1", "status": "active", "mode": "launch",
        "run_id": "r1", "role": "coder", "agent": "codex-ollama",
        "model": "chosen-local:latest", "repository": str(operator),
    }) + "\n")

    result = run(["bash", str(HARNESS / "tmux_lane_message.sh"), "r1", "validator", "coder",
                  "status", "--runs", str(root.parent)], operator, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NOT delivered" in result.stdout
    argv = json.loads(observed.read_text())
    queue = argv.index("queue")
    assert argv[queue:queue + 4] == ["queue", "--thread", thread, "--message"]
    assert "FACTORY_STATUS_PROBE" in argv[queue + 4]
