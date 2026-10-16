"""Host Git over an agent-written repository runs nothing it planted and reads only the commit.

Oracle: Git's documented behavior (git-config core.fsmonitor/core.hooksPath/protocol.allow,
gitattributes export-ignore and filter drivers, git-replace) and the factory rule that a lane's
repository is untrusted from agent start (docs/HARNESS.md). Every test drives real Git against a
repository arranged the way a hostile agent could leave it, and checks either that a marker
command never ran or that the bytes on disk are the commit's own.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
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


LFS_ATTRIBUTES = "*.png filter=lfs diff=lfs merge=lfs -text\n"


def lfs_pointer(content: bytes) -> bytes:
    """A Git LFS v1 pointer for ``content`` (git-lfs spec, docs/spec.md)."""

    oid = hashlib.sha256(content).hexdigest()
    return (
        f"version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(content)}\n"
    ).encode("ascii")


def lfs_repository(
    tmp_path: Path, files: dict[str, bytes], *, attributes: str = LFS_ATTRIBUTES
) -> tuple[Path, str]:
    """Commit ``files`` byte for byte (no clean filter runs), beside ``attributes``."""

    repository, _ = committed_repository(tmp_path, {".gitattributes": attributes})
    for name, data in files.items():
        blob = subprocess.run(
            ["git", "-C", str(repository), "hash-object", "-w", "--no-filters", "--stdin"],
            input=data,
            check=True,
            capture_output=True,
        ).stdout.decode("ascii").strip()
        git(repository, "update-index", "--add", "--cacheinfo", f"100644,{blob},{name}")
    git(repository, "commit", "-qm", "lfs")
    return repository, git(repository, "rev-parse", "HEAD")


def lfs_store(tmp_path: Path, objects: dict[str, bytes]) -> Path:
    """A local LFS object directory: ``<oid[0:2]>/<oid[2:4]>/<oid>`` (git-lfs layout)."""

    store = tmp_path / "lfs-objects"
    store.mkdir()
    for oid, data in objects.items():
        path = store / oid[:2] / oid[2:4] / oid
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return store


def materialize_lfs(
    repository: Path, commit: str, destination: Path, store: Path | None, **ceilings: int
) -> MaterializedCommit:
    return materialize_commit(
        repository,
        commit,
        destination,
        admit_symlinks=False,
        max_files=1000,
        max_total_bytes=1024 * 1024,
        lfs_objects=store,
        max_lfs_bytes=ceilings.get("max_lfs_bytes", 1024 * 1024),
    )


ART = b"\x89PNG\r\n\x1a\n not really a png, but bytes git-lfs would hold\n"
ART_OID = hashlib.sha256(ART).hexdigest()


def test_an_lfs_pointer_is_written_as_the_content_its_digest_names(tmp_path: Path) -> None:
    repository, commit = lfs_repository(tmp_path, {"art.png": lfs_pointer(ART)})
    destination = tmp_path / "judged"

    result = materialize_lfs(
        repository, commit, destination, lfs_store(tmp_path, {ART_OID: ART})
    )

    assert (destination / "art.png").read_bytes() == ART
    assert (result.lfs_hydrated, result.lfs_pointers_left, result.lfs_bytes) == (1, 0, len(ART))


def test_without_an_lfs_store_the_pointer_bytes_are_kept_and_counted(tmp_path: Path) -> None:
    repository, commit = lfs_repository(tmp_path, {"art.png": lfs_pointer(ART)})
    destination = tmp_path / "judged"

    result = materialize(repository, commit, destination)

    assert (destination / "art.png").read_bytes() == lfs_pointer(ART)
    assert (result.lfs_hydrated, result.lfs_pointers_left) == (0, 1)


def test_a_pointer_shaped_file_git_does_not_route_through_lfs_is_left_alone(
    tmp_path: Path,
) -> None:
    repository, commit = lfs_repository(tmp_path, {"fixture.txt": lfs_pointer(ART)})
    destination = tmp_path / "judged"

    result = materialize_lfs(
        repository, commit, destination, lfs_store(tmp_path, {ART_OID: ART})
    )

    assert (destination / "fixture.txt").read_bytes() == lfs_pointer(ART)
    assert (result.lfs_hydrated, result.lfs_pointers_left) == (0, 0)


def test_an_lfs_object_missing_from_the_store_refuses_before_any_file_is_written(
    tmp_path: Path,
) -> None:
    repository, commit = lfs_repository(tmp_path, {"art.png": lfs_pointer(ART)})
    destination = tmp_path / "judged"

    with pytest.raises(HostGitError, match=rf"not in the LFS store .*art\.png \({ART_OID}\)"):
        materialize_lfs(repository, commit, destination, lfs_store(tmp_path, {}))

    assert not destination.exists()


def test_an_lfs_object_whose_bytes_do_not_match_its_digest_is_refused(tmp_path: Path) -> None:
    """The pointer names its content by SHA-256: a store object with other bytes of the same
    size is a substitution, so nothing it holds reaches the judged tree."""

    forged = bytes(reversed(ART))
    repository, commit = lfs_repository(tmp_path, {"art.png": lfs_pointer(ART)})
    destination = tmp_path / "judged"

    with pytest.raises(HostGitError, match="does not match its pointer"):
        materialize_lfs(repository, commit, destination, lfs_store(tmp_path, {ART_OID: forged}))

    assert not (destination / "art.png").exists()


def test_the_lfs_byte_ceiling_refuses_before_any_file_is_written(tmp_path: Path) -> None:
    repository, commit = lfs_repository(tmp_path, {"art.png": lfs_pointer(ART)})
    destination = tmp_path / "judged"

    with pytest.raises(HostGitError, match="LFS content exceeds 8 bytes"):
        materialize_lfs(
            repository,
            commit,
            destination,
            lfs_store(tmp_path, {ART_OID: ART}),
            max_lfs_bytes=8,
        )

    assert not destination.exists()


def test_the_cli_hydrates_from_the_lfs_store_it_is_given(tmp_path: Path) -> None:
    repository, commit = lfs_repository(tmp_path, {"art.png": lfs_pointer(ART)})
    destination = tmp_path / "judged"
    store = lfs_store(tmp_path, {ART_OID: ART})

    printed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "harness" / "host_git.py"),
            "materialize",
            "--repository",
            str(repository),
            "--commit",
            commit,
            "--destination",
            str(destination),
            "--lfs-objects",
            str(store),
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    ).stdout

    assert json.loads(printed)["lfs_hydrated"] == 1
    assert (destination / "art.png").read_bytes() == ART


def test_endgame_reads_the_candidate_only_through_host_git() -> None:
    """Endgame's candidate may be a lane repository; no host Git may run there directly."""

    text = (ROOT / "harness" / "endgame.sh").read_text(encoding="utf-8")

    assert 'git -C "$CANDIDATE"' not in text
    assert 'git -C "$FRESH"' not in text
    assert '"$D/host_git.py" resolve --repository "$CANDIDATE"' in text
    assert 'MATERIALIZE_ARGS=(--repository "$CANDIDATE"' in text
    assert '"$D/host_git.py" materialize "${MATERIALIZE_ARGS[@]}"' in text
    assert '--lfs-objects "$FACTORY_LFS_OBJECTS"' in text
    assert "A status that could not be read is not a clean status." in text
