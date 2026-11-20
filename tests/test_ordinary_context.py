"""Shell context reads ordinary pinned inputs literally, without signed-resume machinery."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from tests.test_validation_failure_paths import task as task

CONTEXT = Path(__file__).resolve().parents[1] / "harness/run_context.sh"


def context(task, *, ambient=None):
    runs, _, _, _ = task
    script = (
        f"source {shlex.quote(str(CONTEXT))}\n"
        f"factory_load_context task {shlex.quote(str(runs))} || exit $?\n"
        '"$FACTORY_PYTHON" -c \'import json,os; '
        'print(json.dumps({k:v for k,v in os.environ.items() if k.startswith("FACTORY_")}))\'\n'
    )
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True,
        env={"PATH": os.environ["PATH"], "FACTORY_PYTHON": sys.executable, **(ambient or {})},
    )


def test_context_uses_exact_record_not_ambient_source_or_resume_manifest(task, tmp_path):
    _, root, repository, commit = task
    forged = tmp_path / "config.sources"
    forged.write_text("factory-directive-ledger=/unrelated/directives\n")
    loaded = context(task, ambient={
        "FACTORY_WORKDIR": "/unrelated/source", "FACTORY_BASE_COMMIT": "f" * 40,
        "FACTORY_CONTROL_ROOT": "/unrelated/run", "FACTORY_TMUX_SOCKET": "default",
        "FACTORY_RESUME_CONFIG_MANIFEST": str(forged),
    })
    assert loaded.returncode == 0, loaded.stderr
    values = json.loads(loaded.stdout)
    record = json.loads((root / "harness.json").read_text())
    assert values["FACTORY_WORKDIR"] == str(repository)
    assert values["FACTORY_BASE_COMMIT"] == commit
    assert values["FACTORY_CONTROL_ROOT"] == str(root)
    assert values["FACTORY_TMUX_SOCKET"] == record["tmux_socket"]


def test_context_reads_literal_fields_without_evaluating_shell_text(task, tmp_path):
    _, root, _, _ = task
    marker = tmp_path / "EXECUTED"
    log = f"{tmp_path}/log $(touch {marker})\nsecond line"
    record = json.loads((root / "harness.json").read_text())
    record["log"] = log
    (root / "harness.json").write_text(json.dumps(record))
    loaded = context(task)
    assert loaded.returncode == 0, loaded.stderr
    assert json.loads(loaded.stdout)["FACTORY_LOG"] == log
    assert not marker.exists()


def test_changed_task_refuses_context_before_downstream_command(task):
    _, root, _, _ = task
    (root / "TASK.md").write_text("substituted task")
    before = (root / "harness.json").read_bytes()
    loaded = context(task)
    assert loaded.returncode == 70 and "retained task changed" in loaded.stderr
    assert loaded.stdout == ""
    assert (root / "harness.json").read_bytes() == before


def test_attach_execs_selected_tmux_for_the_recorded_socket(task, tmp_path):
    runs, root, _, _ = task
    record = json.loads((root / "harness.json").read_text())
    tmux = tmp_path / "pinned tmux"
    tmux.write_text('#!/bin/sh\nprintf "%s\n" "$TMUX_TMPDIR" "$@"\nexit 17\n')
    tmux.chmod(0o755)
    command = [sys.executable, "-m", "factory_runtime.cli", "attach", root.name]
    result = subprocess.run(
        [*command, "--runs", str(runs)], capture_output=True, text=True,
        env={**os.environ, "FACTORY_TMUX": str(tmux), "TMUX_TMPDIR": str(tmp_path / "wrong"),
             "FACTORY_RUNS_DIR": str(tmp_path / "another run directory")},
    )
    assert result.returncode == 17, result.stderr
    assert result.stdout.splitlines() == [
        record["tmux_tmpdir"], "-L", record["tmux_socket"], "attach", "-t", "=" + root.name,
    ]
    refused = subprocess.run(
        command, capture_output=True, text=True,
        env={**os.environ, "FACTORY_TMUX": str(tmux.with_name("missing tmux")),
             "FACTORY_RUNS_DIR": str(runs)},
    )
    assert refused.returncode == 70
    assert "FACTORY_TMUX executable is unavailable" in refused.stderr
