"""Host Git over an agent-written repository runs nothing it planted and reads only the commit.

Oracle: Git's documented behavior (git-config core.fsmonitor/core.hooksPath/protocol.allow,
gitattributes export-ignore and filter drivers, git-replace) and the factory rule that a lane's
repository is untrusted from agent start (docs/HARNESS.md). Every test drives real Git against a
repository arranged the way a hostile agent could leave it, and checks either that a marker
command never ran or that the bytes on disk are the commit's own.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from factory_runtime.host_git import (
    HostGitError,
    MaterializedCommit,
    materialize_commit,
    resolve_commit_tree,
    run_host_git,
)

ROOT = Path(__file__).resolve().parents[1]
CEILINGS = {"max_files": 1000, "max_total_bytes": 1024 * 1024}


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            *arguments,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def committed_repository(tmp_path: Path, files: dict[str, str]) -> tuple[Path, str]:
    repository = tmp_path / "lane"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repository)], check=True)
    for name, text in files.items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(repository, "add", "-A")
    git(repository, "commit", "-qm", "candidate")
    return repository, git(repository, "rev-parse", "HEAD")


def marker_command(marker: Path) -> str:
    return f"touch {marker}; true"


def materialize(
    repository: Path, commit: str, destination: Path, **options: bool
) -> MaterializedCommit:
    return materialize_commit(
        repository,
        commit,
        destination,
        admit_symlinks=options.get("admit_symlinks", False),
        **CEILINGS,
    )


def test_a_planted_fsmonitor_does_not_run_when_the_host_reads_status(tmp_path: Path) -> None:
    repository, _ = committed_repository(tmp_path, {"a.txt": "a\n"})
    marker = tmp_path / "fsmonitor-ran"
    git(repository, "config", "core.fsmonitor", marker_command(marker))

    run_host_git(repository, ("status", "--porcelain"))

    assert not marker.exists()


def test_a_planted_hook_does_not_run_when_the_host_commits(tmp_path: Path) -> None:
    repository, _ = committed_repository(tmp_path, {"a.txt": "a\n"})
    marker = tmp_path / "hook-ran"
    hook = repository / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\n{marker_command(marker)}\n", encoding="utf-8")
    hook.chmod(0o755)
    (repository / "a.txt").write_text("b\n", encoding="utf-8")

    run_host_git(
        repository,
        ("-c", "user.name=h", "-c", "user.email=h@local", "commit", "-qam", "host"),
    )

    assert not marker.exists()


def test_an_ext_remote_is_refused_even_when_the_repository_allows_it(tmp_path: Path) -> None:
    repository, _ = committed_repository(tmp_path, {"a.txt": "a\n"})
    marker = tmp_path / "transport-ran"
    transport = tmp_path / "transport.sh"
    transport.write_text(f"#!/bin/sh\n{marker_command(marker)}\n", encoding="utf-8")
    transport.chmod(0o755)
    git(repository, "config", "protocol.ext.allow", "always")
    git(repository, "remote", "add", "origin", f"ext::{transport}")

    with pytest.raises(HostGitError, match="ls-remote failed"):
        run_host_git(repository, ("ls-remote", "origin"))

    assert not marker.exists()


def test_the_judged_tree_keeps_paths_the_repository_marks_export_ignore(
    tmp_path: Path,
) -> None:
    repository, commit = committed_repository(
        tmp_path, {"impl.py": "x = 1\n", "tests/test_impl.py": "assert False\n"}
    )
    (repository / ".git" / "info").mkdir(exist_ok=True)  # a git template may omit info/
    (repository / ".git" / "info" / "attributes").write_text(
        "tests/** export-ignore\ntests export-ignore\n", encoding="utf-8"
    )
    destination = tmp_path / "judged"

    materialize(repository, commit, destination)

    assert (destination / "tests" / "test_impl.py").read_text() == "assert False\n"


def test_a_planted_smudge_filter_neither_runs_nor_rewrites_the_judged_bytes(
    tmp_path: Path,
) -> None:
    repository, commit = committed_repository(tmp_path, {"impl.py": "x = 1\n"})
    marker = tmp_path / "smudge-ran"
    (repository / ".git" / "info").mkdir(exist_ok=True)  # a git template may omit info/
    (repository / ".git" / "info" / "attributes").write_text("* filter=plant\n")
    git(repository, "config", "filter.plant.smudge", f"sh -c 'touch {marker}; echo x = 2'")
    destination = tmp_path / "judged"

    materialize(repository, commit, destination)

    assert not marker.exists()
    assert (destination / "impl.py").read_text() == "x = 1\n"


def test_the_judged_tree_is_the_named_commit_not_its_replacement(tmp_path: Path) -> None:
    repository, commit = committed_repository(tmp_path, {"impl.py": "x = 1\n"})
    (repository / "impl.py").write_text("x = 2\n", encoding="utf-8")
    git(repository, "commit", "-qam", "other")
    other = git(repository, "rev-parse", "HEAD")
    git(repository, "replace", commit, other)
    destination = tmp_path / "judged"

    result = materialize(repository, commit, destination)

    assert (destination / "impl.py").read_text() == "x = 1\n"
    assert result.tree == git(repository, "--no-replace-objects", "rev-parse", f"{commit}^{{tree}}")


def test_modes_survive_and_links_are_written_only_when_admitted(tmp_path: Path) -> None:
    repository, _ = committed_repository(tmp_path, {"run.sh": "#!/bin/sh\n", "data.txt": "d\n"})
    (repository / "run.sh").chmod(0o755)
    os.symlink("data.txt", repository / "link")
    git(repository, "add", "-A")
    git(repository, "commit", "-qm", "modes")
    commit = git(repository, "rev-parse", "HEAD")

    with pytest.raises(HostGitError, match="symbolic link"):
        materialize(repository, commit, tmp_path / "refused")
    materialize(repository, commit, tmp_path / "judged", admit_symlinks=True)

    assert os.access(tmp_path / "judged" / "run.sh", os.X_OK)
    assert not os.access(tmp_path / "judged" / "data.txt", os.X_OK)
    assert os.readlink(tmp_path / "judged" / "link") == "data.txt"


def test_a_submodule_is_refused_because_its_content_is_not_in_the_commit(
    tmp_path: Path,
) -> None:
    repository, commit = committed_repository(tmp_path, {"a.txt": "a\n"})
    git(repository, "update-index", "--add", "--cacheinfo", f"160000,{commit},vendored")
    git(repository, "commit", "-qm", "gitlink")

    with pytest.raises(HostGitError, match="submodule"):
        materialize(repository, git(repository, "rev-parse", "HEAD"), tmp_path / "judged")


def test_paths_that_collide_on_a_case_insensitive_disk_are_refused(tmp_path: Path) -> None:
    repository, _ = committed_repository(tmp_path, {"a.txt": "a\n"})
    blob = git(repository, "rev-parse", "HEAD:a.txt")
    git(repository, "update-index", "--add", "--cacheinfo", f"100644,{blob},Docs/x.txt")
    git(repository, "update-index", "--add", "--cacheinfo", f"100644,{blob},docs/y.txt")
    git(repository, "commit", "-qm", "collide")

    with pytest.raises(HostGitError, match="collide"):
        materialize(repository, git(repository, "rev-parse", "HEAD"), tmp_path / "judged")


@pytest.mark.parametrize("named", ["HEAD", "main", "short"])
def test_only_a_full_object_id_names_a_commit(tmp_path: Path, named: str) -> None:
    repository, commit = committed_repository(tmp_path, {"a.txt": "a\n"})
    selector = commit[:12] if named == "short" else named

    with pytest.raises(HostGitError, match="full lowercase object id"):
        resolve_commit_tree(repository, selector)


def test_the_byte_ceiling_refuses_before_any_file_is_written(tmp_path: Path) -> None:
    repository, commit = committed_repository(tmp_path, {"big.txt": "x" * 4096})
    destination = tmp_path / "judged"

    with pytest.raises(HostGitError, match="exceeds 1024 bytes"):
        materialize_commit(
            repository,
            commit,
            destination,
            admit_symlinks=False,
            max_files=10,
            max_total_bytes=1024,
        )

    assert not destination.exists()


def test_the_destination_must_be_new(tmp_path: Path) -> None:
    repository, commit = committed_repository(tmp_path, {"a.txt": "a\n"})
    destination = tmp_path / "judged"
    destination.mkdir()

    with pytest.raises(HostGitError, match="new directory"):
        materialize(repository, commit, destination)


def test_endgame_reads_the_candidate_only_through_host_git() -> None:
    """Endgame's candidate may be a lane repository; no host Git may run there directly."""

    text = (ROOT / "harness" / "endgame.sh").read_text(encoding="utf-8")

    assert 'git -C "$CANDIDATE"' not in text
    assert 'git -C "$FRESH"' not in text
    assert '"$D/host_git.py" resolve --repository "$CANDIDATE"' in text
    assert '"$D/host_git.py" materialize --repository "$CANDIDATE"' in text
    assert "A status that could not be read is not a clean status." in text
