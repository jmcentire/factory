"""Behavioral replacements for retired endgame/promotion shell-structure assertions.

Exercise actual failure paths and a valid path, rather than requiring particular
shell branches, signatures, refusal-event names or a promotion state machine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness import run_record, validate_candidate

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def task(tmp_path):
    repository = tmp_path / "source"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    (repository / "value.txt").write_text("actual content\n")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    runs = tmp_path / "runs"
    root = run_record.create(runs, "task", repository, b"Check exact content")
    _, record = run_record.load(runs, "task")
    return runs, root, repository, record["resolved_commit"]


def cli_args(task, command):
    runs, _, repository, commit = task
    return [
        "validate_candidate.py",
        "task",
        commit,
        "--runs",
        str(runs),
        "--repo",
        str(repository),
        "--",
        *command,
    ]


def test_validation_requires_real_checks(task):
    runs, root, repository, commit = task
    before = (root / "harness.json").read_bytes()
    with pytest.raises(ValueError, match="actual project test command"):
        validate_candidate.validate(runs, "task", repository, commit, [])
    assert not (root / "validation").exists()
    assert (root / "harness.json").read_bytes() == before


def test_unknown_run_cannot_be_created_or_accepted_by_validation(task):
    runs, _, repository, commit = task
    with pytest.raises(FileNotFoundError):
        validate_candidate.validate(runs, "missing", repository, commit, ["true"])
    assert not (runs / "missing").exists()


@pytest.mark.parametrize("exit_code", [0, 7])
def test_checks_report_actual_outcome_without_acceptance_or_promotion(task, exit_code):
    runs, root, repository, commit = task
    before = (root / "harness.json").read_bytes()
    code, attempt = validate_candidate.validate(
        runs, "task", repository, commit, [sys.executable, "-c", f"raise SystemExit({exit_code})"]
    )
    result = json.loads((attempt / "result.json").read_text())
    assert code == exit_code and result["exit_code"] == exit_code
    assert result["status"] == ("tests-passed" if exit_code == 0 else "tests-failed")
    assert result["candidate"]["commit"] == commit
    assert (root / "harness.json").read_bytes() == before
    assert not (root / "promotion_verdict.json").exists()
    assert not (root / "run.json").exists()


def test_endgame_failure_retains_evidence_and_never_runs_checks(task, tmp_path):
    runs, root, repository, _ = task
    marker = tmp_path / "executed"
    env = {
        "PATH": os.environ["PATH"],
        "FACTORY_PYTHON": sys.executable,
        "HOME": str(tmp_path),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        [
            "bash",
            str(REPO / "harness/endgame.sh"),
            "task",
            "f" * 40,
            "--runs",
            str(runs),
            "--repo",
            str(repository),
            "--",
            sys.executable,
            "-c",
            f"open({str(marker)!r}, 'w').close()",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1 and result.stdout.startswith("FAIL:")
    assert not marker.exists()
    records = list((root / "validation").glob("*/result.json"))
    assert len(records) == 1
    evidence = json.loads(records[0].read_text())
    assert evidence["status"] == "validation-failed" and evidence["error"]
    assert "candidate" not in evidence


def test_validation_usage_failure_has_no_success_verdict(task, monkeypatch, capsys):
    args = cli_args(task, [])[:-1]  # No command separator: no validation attempt is specified.
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit) as failure:
        validate_candidate.main()
    assert failure.value.code == 2
    output = capsys.readouterr()
    assert "supply a project test command" in output.err and "PASS" not in output.out
    assert not (task[1] / "validation").exists()


def test_result_storage_failure_prevents_success_verdict(task, monkeypatch, capsys):
    original = (task[1] / "harness.json").read_bytes()

    def no_storage(*args):
        raise OSError("fixture result storage unavailable")

    monkeypatch.setattr(validate_candidate, "write_new", no_storage)
    monkeypatch.setattr(sys, "argv", cli_args(task, [sys.executable, "-c", "raise SystemExit(0)"]))
    assert validate_candidate.main() == 70
    output = capsys.readouterr()
    assert "storage unavailable" in output.err and "PASS" not in output.out
    assert (task[1] / "harness.json").read_bytes() == original


@pytest.mark.parametrize(
    "fault",
    ["changed-task", "noncommit", "missing-repository", "escaping-symlink", "missing-command"],
)
def test_failure_paths_cannot_look_like_success(task, tmp_path, monkeypatch, capsys, fault):
    runs, root, repository, commit = task
    marker = tmp_path / "executed"
    command = [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]
    if fault == "changed-task":
        (root / "TASK.md").write_text("substituted intent")
    elif fault == "noncommit":
        commit = subprocess.check_output(
            ["git", "-C", str(repository), "rev-parse", "HEAD^{tree}"], text=True
        ).strip()
    elif fault == "missing-repository":
        repository = tmp_path / "missing"
    elif fault == "escaping-symlink":
        (repository / "escape").symlink_to(tmp_path / "outside")
        subprocess.run(["git", "-C", str(repository), "add", "escape"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-qm",
                "unsafe link",
            ],
            check=True,
        )
        commit = subprocess.check_output(
            ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
        ).strip()
    else:
        command = [str(tmp_path / "nonexistent-command")]
    monkeypatch.setattr(sys, "argv", cli_args((runs, root, repository, commit), command))
    assert validate_candidate.main() != 0
    output = capsys.readouterr()
    assert "PASS" not in output.out and ("FAIL" in output.out or "validation:" in output.err)
    assert not marker.exists()
    records = list((root / "validation").glob("*/result.json"))
    if fault == "changed-task":
        assert not records  # An untrusted task record cannot authorize a validation attempt.
    else:
        assert len(records) == 1
        evidence = json.loads(records[0].read_text())
        assert evidence["exit_code"] != 0
        assert evidence["status"] in {"validation-failed", "tests-failed"}
        if fault != "missing-command":
            assert "candidate" not in evidence


def test_project_contract_checks_judge_the_candidate_not_dirty_checkout(task):
    runs, root, repository, _ = task
    producer = repository / "producer.json"
    consumer = repository / "consumer.json"
    producer.write_text(json.dumps({"field": "level"}))
    consumer.write_text(json.dumps({"field": "severity"}))
    git = ["git", "-C", str(repository), "-c", "user.name=Fixture",
           "-c", "user.email=fixture@example.invalid"]
    subprocess.run([*git, "add", "producer.json", "consumer.json"], check=True)
    subprocess.run([*git, "commit", "-qm", "mismatched boundary"], check=True)
    failing = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    producer.write_text(json.dumps({"field": "severity"}))  # only the user's dirty tree is fixed
    command = [sys.executable, "-c", "import json; "
               "assert json.load(open('producer.json')) == json.load(open('consumer.json'))"]
    code, attempt = validate_candidate.validate(runs, "task", repository, failing, command)
    assert code != 0
    first = json.loads((attempt / "result.json").read_text())
    assert first["status"] == "tests-failed" and first["candidate"]["commit"] == failing
    assert "AssertionError" in (attempt / "output.log").read_text()
    assert json.loads(producer.read_text())["field"] == "severity"
    subprocess.run([*git, "add", "producer.json"], check=True)
    subprocess.run([*git, "commit", "-qm", "fix boundary"], check=True)
    fixed = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    code, success = validate_candidate.validate(runs, "task", repository, fixed, command)
    assert code == 0
    assert json.loads((success / "result.json").read_text())["candidate"]["commit"] == fixed
    assert json.loads((attempt / "result.json").read_text()) == first
    assert not (root / "promotion_verdict.json").exists()
