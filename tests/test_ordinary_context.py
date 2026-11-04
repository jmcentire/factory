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
