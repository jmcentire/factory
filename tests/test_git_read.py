"""git_read.py runs only git reads, whatever a repository's own configuration says."""

import subprocess
from pathlib import Path

import pytest

from harness import git_read


def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True,  # noqa: E731
                                    capture_output=True)
    run("init", "-q")
    (repo / "f.txt").write_text("one\n")
    run("add", "f.txt")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "one")
    (repo / "f.txt").write_text("two\n")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "two")
    return repo


@pytest.mark.parametrize("subcommand", ["push", "commit", "config", "reset", "checkout", "fetch",
                                        "rebase", "stash", "tag", "clean", "remote", "am"])
def test_writes_are_refused(subcommand):
    with pytest.raises(ValueError):
        git_read.command(["-C", "/repo", subcommand])


@pytest.mark.parametrize("args", [["branch", "new-name"], ["log", "--output=x"],
                                  ["diff", "--ext-diff"], ["show", "--textconv"]])
def test_writing_or_program_running_options_are_refused(args):
    with pytest.raises(ValueError):
        git_read.command(["-C", "/repo", *args])


def test_reads_run_with_diff_programs_and_hooks_off():
    built = git_read.command(["-C", "/repo", "log", "-p", "--format=fuller", "a..b"])
    n = 1 + len(git_read.SAFE)
    assert built[:n] == ["git", *git_read.SAFE] and built[n:n + 3] == ["-C", "/repo", "log"]
    assert built[n + 3:] == ["--no-ext-diff", "--no-textconv", "-p", "--format=fuller", "a..b"]
    assert git_read.command(["-C", "/r", "branch", "--list"])[-2:] == ["branch", "--list"]


def test_real_reads_work_and_a_configured_diff_program_never_runs(tmp_path, capfd):
    repo = repository(tmp_path)
    marker = tmp_path / "ran"
    program = tmp_path / "differ.sh"
    program.write_text(f"#!/bin/sh\ntouch {marker}\n")
    program.chmod(0o755)
    subprocess.run(["git", "-C", str(repo), "config", "diff.external", str(program)], check=True)
    assert git_read.main(["-C", str(repo), "log", "-p", "--format=fuller", "HEAD~1..HEAD"]) == 0
    assert "+two" in capfd.readouterr().out
    assert not marker.exists()
    assert git_read.main(["-C", str(repo), "config", "user.name", "x"]) == 64
    assert subprocess.run(["git", "-C", str(repo), "config", "--local", "user.name"],
                          capture_output=True).stdout == b""
