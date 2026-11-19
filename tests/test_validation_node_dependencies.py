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
                                      "copied": ["node_modules", "services/app/node_modules"],
                                      "generated": []}
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


def test_factory_run_honors_factory_runs_dir_and_keeps_the_default(tmp_path, monkeypatch):
    # FACTORY_RUNS_DIR, when set, names the runs directory; otherwise the default is unchanged.
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
    assert chosen == [".factory/runs", str(tmp_path / "env-runs"), "chosen"]


def test_a_reused_install_is_checked_without_pnpms_pre_run_install(tmp_path):
    # Seen on bff-site: pnpm's verifyDepsBeforeRun reinstalled before the script, got 401 from
    # the private registry, and left the copied node_modules empty.
    runs, repository, commit = _workspace(tmp_path)
    code, _, output = _check(runs, repository, commit,
                             'test "$npm_config_verify_deps_before_run" = false && '
                             'test "$pnpm_config_verify_deps_before_run" = false')
    assert code == 0, output
    changed = _commit(repository, {"pnpm-lock.yaml": LOCK + "# new dependency\n"}, "lock")
    (repository / "pnpm-lock.yaml").write_text(LOCK)
    code, _, output = _check(runs, repository, changed,
                             'test -z "$npm_config_verify_deps_before_run"')
    assert code == 0, output  # nothing reused, so pnpm keeps its own install behaviour


def test_a_node_candidate_is_built_outside_the_repositorys_ignored_runs(tmp_path):
    # Seen on bff-site: the candidate sat under the repository's ignored .factory/runs, so
    # oxlint found no files to lint.
    _, repository, commit = _workspace(tmp_path)
    (repository / ".gitignore").write_text("node_modules/\n.factory/\n")
    runs = repository / ".factory/runs"
    run_record.create(runs, "task", repository, b"Check the workspace")
    code, result, output = _check(runs, repository, commit,
                                  f'! git -C "{repository}" check-ignore -q "$PWD" 2>/dev/null && '
                                  'cat packages/lib/index.js')
    assert code == 0, output
    assert output == "CANDIDATE\n"
    assert not Path(result["candidate_path"]).is_relative_to(repository)


def test_a_repository_without_a_node_lockfile_keeps_its_candidate_in_the_run(tmp_path):
    repository = tmp_path / "source"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    commit = _commit(repository, {".gitignore": ".factory/\n", "setup.py": ""}, "base")
    runs = repository / ".factory/runs"
    run_record.create(runs, "task", repository, b"Check")
    code, result, _ = _check(runs, repository, commit, "true")
    assert code == 0
    assert Path(result["candidate_path"]).is_relative_to(runs)


def test_ignored_generated_code_comes_with_a_reused_install_but_nothing_else(tmp_path):
    # Seen on bff-site: services/*/src/sdks/__generated__ (written by codegen, ignored) was
    # missing, so tsc failed with TS2307 and the service tests could not start.
    runs, repository, commit = _workspace(tmp_path)
    (repository / ".gitignore").write_text("node_modules/\n__generated__/\n.env\n")
    commit = _commit(repository, {"packages/lib/index.js": "CANDIDATE\n"}, "ignore generated")
    (repository / "services/app/__generated__").mkdir(parents=True)
    (repository / "services/app/__generated__/client.js").write_text("GENERATED\n")
    (repository / ".env").write_text("TOKEN=secret\n")
    code, result, output = _check(runs, repository, commit,
                                  "cat services/app/__generated__/client.js && test ! -e .env")
    assert code == 0, output
    assert output == "GENERATED\n"
    assert result["dependencies"]["generated"] == ["services/app/__generated__"]
