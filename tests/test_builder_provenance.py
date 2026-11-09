"""Normative: builder attribution comes from retained run identity and actual Git/PR facts.

Source: the Validator's source-free builder-provenance contract, 2026-11-09. These six
local tests use real files, commits, role projections and command execution. No provider
is called. Falsifiers include substituting the product/current version, checking HEAD or
only the tip, accepting duplicate stamps, inspecting inherited lane history, or allowing
provenance warnings to replace the actual test outcome. Supplied PR snapshots establish
comparison behavior only, not correctness of a provider's response or remote publication.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness import role_inputs, run_record, validate_candidate
from tests.test_coordinator_startup import launch as launch

ROOT = Path(__file__).resolve().parents[1]
BUILDER = "4.5.6"
TRAILERS = f"FactoryRun: task\nBuilt-By: Factory v{BUILDER}"
PR_LINE = f"PR built by Factory v{BUILDER}"


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Independent Tester",
         "-c", "user.email=tester@example.invalid", "-c", "commit.gpgsign=false", *args],
        check=True, text=True, capture_output=True,
    ).stdout.strip()


def commit(repo, message):
    source = repo / "source.txt"
    source.write_text((source.read_text() if source.exists() else "") + "actual change\n")
    git(repo, "add", "source.txt")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def builder_run(tmp_path):
    repo = tmp_path / "product"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "pyproject.toml").write_text('[project]\nname = "product"\nversion = "9.9.9"\n')
    git(repo, "add", "pyproject.toml")
    commit(repo, "Inherited product history without Factory stamps")
    runs = tmp_path / "builder-runs"
    root = run_record.create(runs, "task", repo, b"Implement the requested product behavior")
    _, doc = run_record.load(runs, "task")
    # A retained older builder version intentionally differs from the target and installation.
    doc["factory_version"]["version"] = BUILDER
    (root / "harness.json").write_text(json.dumps(doc))
    return runs, root, repo, doc


def test_attribution_uses_retained_identity_and_never_invents_missing_version(
    builder_run, launch, monkeypatch,
):
    _, root, repo, doc = builder_run
    assert doc["factory_version"]["name"] == "Factory"
    historical = {**doc, "factory_version": dict(doc["factory_version"])}
    historical["factory_version"].pop("name")
    expected_identity = {**historical["factory_version"], "name": "Factory"}
    assert run_record.builder_attribution(historical) == {
        "identity": expected_identity, "commit_trailers": TRAILERS, "pr_line": PR_LINE,
    }
    for retained in (None, {}, {"version": 123}, {"version": ""}, {"version": "4.5.6\nforged"}):
        old = {**doc, "factory_version": retained}
        attribution = run_record.builder_attribution(old)
        assert attribution["commit_trailers"] == "FactoryRun: task"
        assert attribution["pr_line"] is None
        report = run_record.check_builder(root, old, repo, doc["resolved_commit"])
        assert report["status"] == "unknown"

    # Older saved startup prompts predate named builder identity. Restoring an unnamed
    # retained version must not replace those prompts or prevent retry after a launch failure.
    legacy_root, _, _, capture, _, _ = launch
    record_path = legacy_root / "harness.json"
    legacy_doc = json.loads(record_path.read_text())
    retained = legacy_doc.pop("factory_version")
    retained.pop("name")
    record_path.write_text(json.dumps(legacy_doc))
    monkeypatch.setenv("FACTORY_VALIDATOR_AGENT", "kickoff")
    with pytest.raises(ValueError, match="startup failed"):
        capture(fail_start=True)
    prompts = {name: (legacy_root / name).read_bytes() for name in
               ("validator/BRIEF.md", "orchestrator/initial-prompt.txt")}
    legacy_doc["factory_version"] = retained
    record_path.write_text(json.dumps(legacy_doc))
    capture()
    assert {name: (legacy_root / name).read_bytes() for name in prompts} == prompts
    assert json.loads(record_path.read_text())["factory_version"] == retained


def test_checks_exact_task_history_excluding_baseline_and_detects_bad_stamps(builder_run):
    _, root, repo, doc = builder_run
    first = commit(repo, "First task change\n\n" + TRAILERS)
    exact = commit(repo, "Second task change\n\n" + TRAILERS)
    commit(repo, "Later unstamped work beyond the requested candidate")
    report = run_record.check_builder(root, doc, repo, exact)
    assert report["status"] == "consistent" and report["findings"] == []
    assert report["commit"] == exact
    assert set(report["commits"]) == {first, exact}
    assert report["expected"]["commit_trailers"] == TRAILERS

    git(repo, "config", "trailer.foo.key", "Built-By")
    bad_stamps = (
        "FactoryRun: task",  # Missing builder.
        f"Built-By: Factory v{BUILDER}",  # Missing run.
        TRAILERS + f"\nBuilt-By: Factory v{BUILDER}",  # Duplicate, even when identical.
        TRAILERS + "\nFactoryRun: another-task",  # Conflicting run.
        "FactoryRun: task\nBuilt-By: Factory v9.9.9",  # Product version is not builder version.
        f"FactoryRun: task\nfoo: Factory v{BUILDER}",  # Local Git aliases are not attribution.
    )
    for stamp in bad_stamps:
        git(repo, "commit", "--amend", "-qm", "Task change\n\n" + stamp)
        candidate = git(repo, "rev-parse", "HEAD")
        bad = run_record.check_builder(root, doc, repo, candidate)
        assert bad["status"] == "mismatch", stamp
        assert bad["findings"] and candidate in bad["commits"]
    later_good = commit(repo, "Correct tip cannot conceal earlier bad attribution\n\n" + TRAILERS)
    assert run_record.check_builder(root, doc, repo, later_good)["status"] == "mismatch"
    git(repo, "config", "trailer.separators", "=")
    assert run_record.check_builder(root, doc, repo, exact)["status"] == "consistent"


def test_role_input_baseline_is_not_mistaken_for_task_authored_history(builder_run, tmp_path):
    runs, root, _, doc = builder_run
    lane = tmp_path / "coder"
    role_inputs.prepare(runs, "task", "coder", lane, [], [])
    initial = git(lane, "rev-parse", "HEAD")
    candidate = commit(lane, "Implement the task\n\n" + TRAILERS)
    report = run_record.check_builder(root, doc, lane, candidate)
    assert report["status"] == "consistent", report["findings"]
    assert report["commit"] == candidate and report["commits"] == [candidate]
    assert initial not in report["commits"]


def test_pr_snapshot_must_match_exact_head_and_unique_standalone_attribution(builder_run):
    _, root, repo, doc = builder_run
    candidate = commit(repo, "Task change\n\n" + TRAILERS)
    pr = {"url": "https://example.invalid/pull/7", "headRefOid": candidate,
          "body": f"Requested change\n\n{PR_LINE}\nFactoryRun: task\n"}
    good = run_record.check_builder(root, doc, repo, candidate, pr=pr)
    assert good["status"] == "consistent" and good["pr"] == pr
    wrong = (
        {**pr, "headRefOid": doc["resolved_commit"]},
        {**pr, "body": f"> {PR_LINE}\nFactoryRun: task"},
        {**pr, "body": pr["body"] + PR_LINE + "\n"},
        {**pr, "body": f"{PR_LINE}\nFactoryRun: another-task"},
    )
    for snapshot in wrong:
        report = run_record.check_builder(root, doc, repo, candidate, pr=snapshot)
        assert report["status"] == "mismatch" and report["findings"], snapshot


def test_validation_reports_missing_attribution_without_overriding_test_outcome(builder_run):
    runs, root, repo, _ = builder_run
    candidate = commit(repo, "Task implementation missing its stamps")
    for exit_code in (0, 7):
        command = [sys.executable, "-c",
                   "from pathlib import Path; assert Path('source.txt').read_text(); "
                   f"raise SystemExit({exit_code})"]
        code, attempt = validate_candidate.validate(runs, "task", repo, candidate, command)
        report_path = attempt / "result.json"
        result = json.loads(report_path.read_text())
        assert code == result["exit_code"] == exit_code
        assert result["status"] == ("tests-passed" if exit_code == 0 else "tests-failed")
        provenance = result["builder_provenance"]
        assert provenance["status"] == "mismatch"
        assert provenance["commit"] == candidate
        assert provenance["expected"]["pr_line"] == PR_LINE
        notices = [json.loads(line) for line in
                   (root / "orchestrator/inbox.jsonl").read_text().splitlines()]
        notice = next(row for row in notices if str(report_path) in row["text"])
        assert "mismatch" in notice["text"] and BUILDER in notice["text"]


def test_provenance_cli_prints_expected_then_retains_actual_report_and_notice(builder_run):
    runs, root, repo, doc = builder_run
    command = [sys.executable, str(ROOT / "harness/run_record.py"), "provenance",
               "--runs", str(runs), "--run", "task"]
    environment = dict(os.environ, PYTHONPATH=str(ROOT), FACTORY_PYTHON=sys.executable)
    expected = subprocess.run(command, env=environment, text=True, capture_output=True, timeout=15)
    assert expected.returncode == 0, expected.stderr
    assert json.loads(expected.stdout)["commit_trailers"] == TRAILERS
    assert not (root / "provenance").exists()
    candidate = commit(repo, "Actual task change\n\n" + TRAILERS)
    checked = subprocess.run(
        [*command, "--repo", str(repo), "--commit", candidate],
        env=environment, text=True, capture_output=True, timeout=15,
    )
    assert checked.returncode == 0, checked.stderr
    reports = list((root / "provenance").glob("*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report["status"] == "consistent" and report["commit"] == candidate
    assert report["expected"]["pr_line"] == PR_LINE
    assert str(reports[0]) in checked.stdout
    notice = json.loads((root / "orchestrator/inbox.jsonl").read_text())
    assert str(reports[0]) in notice["text"] and candidate in notice["text"]

    # The gh process is a recorded external seam, not evidence of provider compatibility.
    binary = root.parent / "bin"
    binary.mkdir()
    invocations = binary / "gh-arguments.jsonl"
    url = "https://github.com/fixture-owner/fixture-repository/pull/7"
    snapshot = {"url": url, "headRefOid": doc["resolved_commit"],
                "body": f"{PR_LINE}\nFactoryRun: task\n"}
    gh = binary / "gh"
    gh.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        f"with open({str(invocations)!r}, 'a') as log:\n"
        "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if os.environ.get('FIXTURE_GH_FAIL'): raise SystemExit(9)\n"
        f"print({json.dumps(snapshot)!r})\n"
    )
    gh.chmod(0o755)
    environment["PATH"] = str(binary) + os.pathsep + environment["PATH"]
    checked_pr = subprocess.run(
        [*command, "--repo", str(repo), "--commit", candidate, "--pr", url],
        env=environment, text=True, capture_output=True, timeout=15,
    )
    assert checked_pr.returncode == 1, checked_pr.stderr
    assert json.loads(invocations.read_text()) == [
        "pr", "view", url, "--json", "url,body,headRefOid",
    ]
    mismatches = set((root / "provenance").glob("*.json")) - set(reports)
    assert len(mismatches) == 1
    mismatch = json.loads(mismatches.pop().read_text())
    assert mismatch["status"] == "mismatch" and mismatch["pr"] == snapshot
    before_failure = set((root / "provenance").glob("*.json"))
    failed_fetch = subprocess.run(
        [*command, "--repo", str(repo), "--commit", candidate, "--pr", url],
        env={**environment, "FIXTURE_GH_FAIL": "1"},
        text=True, capture_output=True, timeout=15,
    )
    assert failed_fetch.returncode != 0
    new_reports = set((root / "provenance").glob("*.json")) - before_failure
    assert all(json.loads(path.read_text())["status"] != "consistent" for path in new_reports)
