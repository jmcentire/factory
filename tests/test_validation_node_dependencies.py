"""Checks reuse the source checkout's Node install when the candidate's lockfile matches it.

Seen in two runs on a pnpm workspace: the sealed check had no package registry, so every
attempt to install failed, and only a hand-copied node_modules let the checks run.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from harness import run_record, validate_candidate

LOCK = "lockfileVersion: '9.0'\n"


def _commit(repository: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        (repository / name).parent.mkdir(parents=True, exist_ok=True)
        (repository / name).write_text(text)
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "-qm", message], check=True)
    return subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


def _workspace(tmp_path: Path) -> tuple[Path, Path, str]:
    """A pnpm-shaped workspace whose source checkout has an install and an edited package."""
    repository = tmp_path / "source"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    files = {".gitignore": "node_modules/\n", "package.json": "{}\n", "pnpm-lock.yaml": LOCK,
             "packages/lib/package.json": "{}\n", "packages/lib/index.js": "BASE\n",
             "services/app/package.json": "{}\n"}
    _commit(repository, files, "base")
    commit = _commit(repository, {"packages/lib/index.js": "CANDIDATE\n"}, "candidate")
    # The install: a registry package, and a workspace link relative to where it sits.
    (repository / "node_modules/dep").mkdir(parents=True)
    (repository / "node_modules/dep/index.js").write_text("REGISTRY\n")
    (repository / "node_modules/lib").symlink_to("../packages/lib")
    (repository / "services/app/node_modules").mkdir(parents=True)
    (repository / "services/app/node_modules/lib").symlink_to("../../../packages/lib")
    # Uncommitted work in the source checkout must not stand in for the candidate's files.
    (repository / "packages/lib/index.js").write_text("SOURCE WORKTREE\n")
    runs = tmp_path / "runs"
    run_record.create(runs, "task", repository, b"Check the workspace")
    return runs, repository, commit


def _check(runs: Path, repository: Path, commit: str, script: str) -> tuple[int, dict, str]:
    code, result = validate_candidate.validate(runs, "task", repository, commit,
                                               ["sh", "-c", script])
    return (code, json.loads((result / "result.json").read_text()),
            (result / "output.log").read_text())


def test_a_matching_install_is_copied_and_resolves_the_candidates_own_packages(tmp_path):
    runs, repository, commit = _workspace(tmp_path)
    code, result, output = _check(
        runs, repository, commit,
        "cat node_modules/dep/index.js node_modules/lib/index.js "
        "services/app/node_modules/lib/index.js")
    assert code == 0, output
    assert output == "REGISTRY\nCANDIDATE\nCANDIDATE\n"
    assert result["dependencies"] == {"status": "reused", "source": str(repository.resolve()),
                                      "copied": ["node_modules", "services/app/node_modules"]}
    assert (repository / "packages/lib/index.js").read_text() == "SOURCE WORKTREE\n"


def test_a_changed_lockfile_is_not_reused(tmp_path):
    runs, repository, _ = _workspace(tmp_path)
    commit = _commit(repository, {"pnpm-lock.yaml": LOCK + "# new dependency\n"}, "lock")
    (repository / "pnpm-lock.yaml").write_text(LOCK)  # the install still matches the old one
    code, result, _ = _check(runs, repository, commit, "test ! -e node_modules")
    assert code == 0
    assert result["dependencies"] == {
        "status": "not-reused", "reason": "pnpm-lock.yaml differs from the source checkout"}


def test_a_repository_without_a_node_lockfile_is_untouched(tmp_path):
    repository = tmp_path / "source"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    commit = _commit(repository, {"package.json": "{}\n"}, "base")
    (repository / "node_modules/dep").mkdir(parents=True)
    runs = tmp_path / "runs"
    run_record.create(runs, "task", repository, b"Check")
    code, result, _ = _check(runs, repository, commit, "test ! -e node_modules")
    assert code == 0 and result["dependencies"] == {"status": "not-node"}


def test_factory_run_keeps_runs_with_the_repository_not_the_working_directory(
        tmp_path, monkeypatch):
    # Seen in runs: `factory run --repo X` from another directory put runs under that directory.
    import subprocess as process

    from factory_runtime import cli

    started = []
    monkeypatch.setattr(process, "run", lambda command, **_: started.append(command)
                        or process.CompletedProcess(command, 0))
    monkeypatch.delenv("FACTORY_RUNS_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    repository = tmp_path / "repo"
    assert cli.main(["run", "task", "brief", "--repo", str(repository)]) == 0
    monkeypatch.setenv("FACTORY_RUNS_DIR", str(tmp_path / "env-runs"))
    assert cli.main(["run", "task", "brief", "--repo", str(repository)]) == 0
    assert cli.main(["run", "task", "brief", "--repo", str(repository), "--runs", "chosen"]) == 0
    chosen = [command[command.index("--runs") + 1] for command in started]
    assert chosen == [str(repository / ".factory/runs"), str(tmp_path / "env-runs"), "chosen"]
