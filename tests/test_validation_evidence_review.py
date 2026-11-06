"""Behavioral evidence is retained for review, never promoted to acceptance."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness.run_record import create
from harness.validate_candidate import validate


@pytest.fixture
def candidate_run(tmp_path):
    repo = tmp_path / "repository"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Independent Tester")
    git("config", "user.email", "tester@example.invalid")
    (repo / "value.txt").write_text("committed candidate\n")
    git("add", "value.txt")
    git("commit", "-qm", "Candidate fixture")
    sha = git("rev-parse", "HEAD")
    runs = tmp_path / "runs"
    create(runs, "review-run", repo, b"Check candidate behavior independently")
    # Move HEAD and dirty the working tree after recording the exact candidate.
    (repo / "value.txt").write_text("later commit\n")
    git("add", "value.txt")
    git("commit", "-qm", "Advance checkout beyond candidate")
    (repo / "value.txt").write_text("user's uncommitted edit\n")
    return runs, repo, sha


def evidence_command(exit_code):
    return [
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; "
        "assert Path('value.txt').read_text() == 'committed candidate\\n'; "
        "print('behavior-output-marker', flush=True); "
        "print('behavior-error-marker', file=sys.stderr, flush=True); "
        "Path('value.txt').write_text('test-local mutation\\n'); "
        f"sys.exit({exit_code})",
    ]


def assert_review_notice(runs, sha, result_path, status):
    inbox = runs / "review-run" / "orchestrator" / "inbox.jsonl"
    notices = [json.loads(line) for line in inbox.read_text().splitlines()]
    matching = [notice for notice in notices if str(result_path) in notice["text"]]
    assert len(matching) == 1, "Each retained result needs one identifiable review notice"
    notice = matching[0]
    text = notice["text"]
    for fact in ("review-run", sha, status):
        assert fact in text
    assert "not-evaluated" in text
    return text


@pytest.mark.parametrize("exit_code", [0, 23])
def test_completed_command_retains_evidence_and_requests_review(candidate_run, exit_code):
    runs, repo, sha = candidate_run
    command = evidence_command(exit_code)
    original_head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"])
    original_status = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"])
    actual_exit, root = validate(runs, "review-run", repo, sha, command)
    result_path = root / "result.json"
    result = json.loads(result_path.read_text())
    assert actual_exit == result["exit_code"] == exit_code
    assert result["command"] == command
    assert result["candidate"]["commit"] == sha
    assert result["requested_commit"] == sha
    assert result["acceptance"] == "not-evaluated"
    status = "tests-passed" if exit_code == 0 else "tests-failed"
    assert result["status"] == status
    output = (root / "output.log").read_text()
    assert "behavior-output-marker" in output
    assert "behavior-error-marker" in output
    assert (repo / "value.txt").read_text() == "user's uncommitted edit\n"
    assert (
        subprocess.check_output(["git", "-C", str(repo), "show", f"{sha}:value.txt"], text=True)
        == "committed candidate\n"
    )
    assert subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"]) == original_head
    assert (
        subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"])
        == original_status
    )
    assert_review_notice(runs, sha, result_path, status)


def test_materialization_failure_retains_nonacceptance_and_notifies(candidate_run):
    runs, repo, _ = candidate_run
    nonexistent_sha = "f" * 40
    marker = runs.parent / "command-executed-marker"
    exit_code, root = validate(
        runs,
        "review-run",
        repo,
        nonexistent_sha,
        [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
    )
    result_path = root / "result.json"
    result = json.loads(result_path.read_text())
    assert not marker.exists(), "A candidate materialization failure must not execute the command"
    assert exit_code != 0
    assert result["status"] == "validation-failed"
    assert result["acceptance"] == "not-evaluated"
    assert result["requested_commit"] == nonexistent_sha
    assert_review_notice(runs, nonexistent_sha, result_path, "validation-failed")
    assert (repo / "value.txt").read_text() == "user's uncommitted edit\n"


@pytest.mark.parametrize("exit_code", [0, 23])
def test_notification_failure_is_reported_without_losing_command_evidence(candidate_run, exit_code):
    runs, repo, sha = candidate_run
    inbox = runs / "review-run" / "orchestrator" / "inbox.jsonl"
    inbox.mkdir(parents=True)
    with pytest.raises(ValueError) as failure:
        validate(runs, "review-run", repo, sha, evidence_command(exit_code))
    results = list((runs / "review-run" / "validation").glob("*/result.json"))
    assert len(results) == 1
    result_path = results[0]
    result = json.loads(result_path.read_text())
    assert result["exit_code"] == exit_code
    assert result["acceptance"] == "not-evaluated"
    assert "behavior-output-marker" in result_path.with_name("output.log").read_text()
    assert str(result_path) in str(failure.value)
    error = str(failure.value).lower()
    assert "notification" in error or "notice" in error or "inbox" in error


def test_cli_reports_notification_failure_instead_of_command_success(candidate_run):
    runs, repo, sha = candidate_run
    (runs / "review-run" / "orchestrator" / "inbox.jsonl").mkdir(parents=True)
    cli = Path(__file__).resolve().parents[1] / "harness" / "endgame.sh"
    completed = subprocess.run(
        [
            "bash",
            str(cli),
            "review-run",
            sha,
            "--runs",
            str(runs),
            "--repo",
            str(repo),
            "--",
            *evidence_command(0),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}",
        },
    )
    assert completed.returncode != 0
    results = list((runs / "review-run" / "validation").glob("*/result.json"))
    assert len(results) == 1, completed.stdout + completed.stderr
    assert json.loads(results[0].read_text())["exit_code"] == 0
    assert str(results[0]) in completed.stdout + completed.stderr
