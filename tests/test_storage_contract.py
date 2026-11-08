"""Normative: cleanup must reclaim safely, preserve recoverability, and identify builds.

Oracles are the user request and source-free CLI contract supplied by Validator.
Tests use real temporary Git repositories, CLI processes, archive bytes and tmux.
They never clean a user's actual runs. No prose assertions or implementation mocks.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("FACTORY_STORAGE_TEST_ROOT", Path(__file__).resolve().parents[1]))


def cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run(
        [sys.executable, "-m", "factory_runtime.cli", *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def run(tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "source.txt").write_text("initial\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "initial")
    task = tmp_path / "task.txt"
    task.write_text("Keep task evidence and source recoverable.")
    runs = tmp_path / "runs"
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    p = subprocess.run(
        [
            sys.executable,
            str(ROOT / "harness/run_record.py"),
            "create",
            "--runs",
            str(runs),
            "--run",
            "example",
            "--repo",
            str(repo),
            "--task-file",
            str(task),
        ],
        env=env,
        text=True,
        capture_output=True,
    )
    assert p.returncode == 0, p.stderr
    return runs, runs / "example", repo


def snapshot(root: Path) -> dict[str, tuple]:
    result = {}
    for p in sorted(root.rglob("*")):
        s = p.lstat()
        key = str(p.relative_to(root))
        if p.is_symlink():
            result[key] = ("link", os.readlink(p))
        elif p.is_file():
            result[key] = ("file", stat.S_IMODE(s.st_mode), p.read_bytes())
        elif p.is_dir():
            result[key] = ("dir", stat.S_IMODE(s.st_mode))
    return result


def archive_args(runs: Path) -> tuple[str, ...]:
    return ("cleanup", "--runs", str(runs), "--run", "example", "--archive", "--completed")


def archive_path(runs: Path) -> Path:
    matches = list(runs.parent.rglob("*.tar.gz"))
    assert len(matches) == 1, matches
    return matches[0]


def test_inventory_and_preview_do_not_mutate_evidence(run):
    runs, root, _ = run
    (root / "log.txt").write_text("diagnostics\n" * 1000)
    before = snapshot(runs.parent)
    inventory = cli("cleanup", "--runs", str(runs))
    assert inventory.returncode == 0, inventory.stderr
    assert inventory.stdout.strip()
    preview = cli(*archive_args(runs))
    assert preview.returncode == 0, preview.stderr
    assert snapshot(runs.parent) == before


def test_archive_roundtrip_preserves_dirty_unpushed_work_and_links(run, tmp_path):
    runs, root, _ = run
    lane = root / "lane"
    lane.mkdir()
    git(lane, "init", "-q")
    git(lane, "config", "user.name", "Test")
    git(lane, "config", "user.email", "test@example.invalid")
    source = lane / "source.txt"
    source.write_text("unpushed commit\n")
    git(lane, "add", ".")
    git(lane, "commit", "-qm", "unpublished")
    commit = git(lane, "rev-parse", "HEAD")
    # Local Git clones commonly hardlink object storage; archival must retain bytes.
    object_file = next(p for p in (lane / ".git/objects").glob("??/*") if p.is_file())
    os.link(object_file, lane / "hardlinked-object-copy")
    source.write_text("precious dirty edits\n")
    executable = root / "tool.sh"
    executable.write_text("#!/bin/sh\necho retained\n")
    executable.chmod(0o755)
    os.link(executable, root / "second-tool.sh")
    (root / "log.txt").write_bytes(b"compressible evidence\n" * 10000)
    outside = tmp_path / "outside"
    outside.write_text("not owned by run")
    (root / "outside-link").symlink_to(outside)
    root.chmod(0o700)
    before = snapshot(root)
    p = cli(*archive_args(runs), "--apply")
    assert p.returncode == 0, p.stderr
    archive = archive_path(runs)
    assert not root.exists(), "archive apply must actually reclaim the original payload"
    assert archive.stat().st_size < 100000
    restored = tmp_path / "restored"
    p = cli("cleanup", "--restore", str(archive), "--destination", str(restored), "--apply")
    assert p.returncode == 0, p.stderr
    assert snapshot(restored) == before
    assert stat.S_IMODE(restored.stat().st_mode) == 0o700
    assert git(restored / "lane", "rev-parse", "HEAD") == commit
    assert "source.txt" in git(restored / "lane", "status", "--porcelain")
    assert outside.read_text() == "not owned by run"


def test_restore_never_clobbers_existing_destination(run, tmp_path):
    runs, _, _ = run
    p = cli(*archive_args(runs), "--apply")
    assert p.returncode == 0, p.stderr
    archive = archive_path(runs)
    destination = tmp_path / "occupied"
    destination.mkdir()
    (destination / "keep").write_text("user data")
    before = snapshot(destination)
    p = cli("cleanup", "--restore", str(archive), "--destination", str(destination), "--apply")
    assert p.returncode != 0
    assert snapshot(destination) == before


def test_archive_requires_completion_confirmation(run):
    runs, root, _ = run
    before = snapshot(root)
    p = cli("cleanup", "--runs", str(runs), "--run", "example", "--archive", "--apply")
    assert p.returncode != 0
    assert snapshot(root) == before


def test_unknown_run_and_symlink_root_are_not_cleaned(run, tmp_path):
    runs, _, _ = run
    unknown = runs / "unknown"
    unknown.mkdir()
    (unknown / "valuable").write_text("not a factory run")
    before = snapshot(unknown)
    p = cli(
        "cleanup", "--runs", str(runs), "--run", "unknown", "--archive", "--completed", "--apply"
    )
    assert p.returncode != 0
    assert snapshot(unknown) == before
    target = tmp_path / "other"
    target.mkdir()
    (target / "valuable").write_text("untouched")
    (runs / "linked").symlink_to(target, target_is_directory=True)
    p = cli(
        "cleanup", "--runs", str(runs), "--run", "linked", "--archive", "--completed", "--apply"
    )
    assert p.returncode != 0
    assert (target / "valuable").read_text() == "untouched"


def test_live_tmux_run_cannot_be_archived(run):
    if not shutil.which("tmux"):
        pytest.skip("tmux unavailable")
    runs, root, _ = run
    record = json.loads((root / "harness.json").read_text())
    socket = record["tmux_socket"]
    start = subprocess.run(
        ["tmux", "-L", socket, "new-session", "-d", "-s", "test", "sleep 120"],
        capture_output=True,
        text=True,
    )
    if start.returncode:
        pytest.skip(f"tmux unavailable in sandbox: {start.stderr}")
    try:
        before = snapshot(root)
        p = cli(*archive_args(runs), "--apply")
        assert p.returncode != 0
        assert snapshot(root) == before
        assert (
            subprocess.run(["tmux", "-L", socket, "has-session"], capture_output=True).returncode
            == 0
        )
    finally:
        subprocess.run(["tmux", "-L", socket, "kill-server"], capture_output=True)


def test_corrupted_archive_does_not_create_partial_restore(run, tmp_path):
    runs, _, _ = run
    p = cli(*archive_args(runs), "--apply")
    assert p.returncode == 0, p.stderr
    archive = archive_path(runs)
    data = archive.read_bytes()
    archive.write_bytes(data[: len(data) // 2])
    destination = tmp_path / "corrupt-restore"
    p = cli("cleanup", "--restore", str(archive), "--destination", str(destination), "--apply")
    assert p.returncode != 0
    assert not destination.exists()


def test_version_and_new_run_identify_actual_factory_build(run):
    _, root, _ = run
    p = cli("version", "--json")
    assert p.returncode == 0, p.stderr
    version = json.loads(p.stdout)
    assert version["version"]
    assert version["revision"] == git(ROOT, "rev-parse", "HEAD")
    assert isinstance(version["dirty"], bool)
    # The fixture was created from this exact executable/source, not the target repo.
    record = json.loads((root / "harness.json").read_text())
    assert record["factory_version"]["revision"] == version["revision"]
    assert record["factory_version"]["version"] == version["version"]
    assert record["factory_version"]["harness_schema"] == record["schema_version"]
    display = cli("--version")
    assert display.returncode == 0
    # Human version display is promised, but its output stream is not specified.
    displayed = display.stdout + display.stderr
    assert version["version"] in displayed
    assert version["revision"][:7] in displayed


def test_old_run_without_version_remains_inventory_compatible(run):
    runs, root, _ = run
    path = root / "harness.json"
    record = json.loads(path.read_text())
    record.pop("factory_version", None)
    path.write_text(json.dumps(record))
    before = snapshot(root)
    p = cli("cleanup", "--runs", str(runs))
    assert p.returncode == 0, p.stderr
    assert snapshot(root) == before


def test_live_recorded_process_protects_run_even_without_tmux(run):
    runs, root, _ = run
    (root / "worker.pid").write_text(str(os.getpid()))
    before = snapshot(root)
    p = cli(*archive_args(runs), "--apply")
    assert p.returncode != 0
    assert snapshot(root) == before


def test_linked_worktree_is_not_archived_as_if_self_contained(run, tmp_path):
    runs, root, repo = run
    lane = root / "linked-worktree"
    git(repo, "worktree", "add", "--detach", str(lane), "HEAD")
    before = snapshot(root)
    try:
        p = cli(*archive_args(runs), "--apply")
        assert p.returncode != 0
        assert snapshot(root) == before
        assert git(lane, "rev-parse", "HEAD") == git(repo, "rev-parse", "HEAD")
    finally:
        git(repo, "worktree", "remove", str(lane))


def test_recorded_live_worker_blocks_archive_when_tmux_has_gone(run):
    runs, root, _ = run
    child = subprocess.Popen(["sleep", "120"])
    try:
        (root / "validator-process.json").write_text(
            json.dumps(
                {
                    "schema": "factory-worker-process/1",
                    "slot": "validator",
                    "status": "running",
                    "worker": {"pid": child.pid, "started": "unknown"},
                    "wrapper": {"pid": child.pid, "started": "unknown"},
                }
            )
        )
        before = snapshot(root)
        p = cli(*archive_args(runs), "--apply")
        assert p.returncode != 0
        assert snapshot(root) == before
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=5)


@pytest.mark.parametrize("escape", ["../escaped-file", "/tmp/factory-storage-forbidden-escape"])
def test_restore_refuses_manifest_path_escape(run, tmp_path, escape):
    runs, _, _ = run
    p = cli(*archive_args(runs), "--apply")
    assert p.returncode == 0, p.stderr
    archive = archive_path(runs)
    receipt = runs / "example.archive.json"
    doc = json.loads(receipt.read_text())
    doc["entries"][0]["path"] = escape
    receipt.write_text(json.dumps(doc))
    destination = tmp_path / "restore"
    before = snapshot(tmp_path)
    p = cli("cleanup", "--restore", str(archive), "--destination", str(destination), "--apply")
    assert p.returncode != 0
    assert snapshot(tmp_path) == before


def test_archive_can_stage_on_separate_storage_without_touching_other_data(run, tmp_path):
    runs, root, _ = run
    external = tmp_path / "archive storage"
    external.mkdir()
    unrelated = external / "keep"
    unrelated.write_text("unrelated bytes")
    p = cli(*archive_args(runs), "--archive-dir", str(external), "--apply")
    assert p.returncode == 0, p.stderr
    assert not root.exists()
    archive = external / "example.tar.gz"
    assert archive.is_file()
    assert not (runs / "example.tar.gz").exists()
    assert unrelated.read_text() == "unrelated bytes"
    destination = tmp_path / "restored external"
    p = cli("cleanup", "--restore", str(archive), "--destination", str(destination), "--apply")
    assert p.returncode == 0, p.stderr
    assert (destination / "TASK.md").read_text() == "Keep task evidence and source recoverable."


def test_archive_destination_inside_source_is_refused(run):
    runs, root, _ = run
    destination = root / "archive"
    destination.mkdir()
    before = snapshot(root)
    p = cli(*archive_args(runs), "--archive-dir", str(destination), "--apply")
    assert p.returncode != 0
    assert snapshot(root) == before


def test_custom_extended_metadata_survives_archive_and_restore(run, tmp_path):
    runs, root, _ = run
    path = root / "metadata.txt"
    path.write_text("retain me")
    name = "com.factory.storage-test" if sys.platform == "darwin" else "user.factory-storage-test"
    if hasattr(os, "setxattr"):
        try:
            os.setxattr(path, name, b"precious metadata")
        except OSError as error:
            pytest.skip(f"filesystem lacks xattrs: {error}")
    elif sys.platform == "darwin" and shutil.which("xattr"):
        subprocess.run(["xattr", "-w", name, "precious metadata", str(path)], check=True)
    else:
        pytest.skip("extended attributes unavailable")
    path.chmod(0o400)
    before = snapshot(root)
    p = cli(*archive_args(runs), "--apply")
    assert p.returncode == 0, p.stderr
    archive = archive_path(runs)
    destination = tmp_path / "metadata-restored"
    p = cli("cleanup", "--restore", str(archive), "--destination", str(destination), "--apply")
    assert p.returncode == 0, p.stderr
    assert snapshot(destination) == before
    restored_file = destination / "metadata.txt"
    if hasattr(os, "getxattr"):
        value = os.getxattr(restored_file, name)
    else:
        value = subprocess.check_output(["xattr", "-p", name, str(restored_file)]).strip()
    assert value == b"precious metadata"


def test_inventory_reports_archives_after_reclaiming_original_run(run):
    runs, root, _ = run
    (root / "log.txt").write_bytes(b"repeated evidence\n" * 10000)
    p = cli(*archive_args(runs), "--apply")
    assert p.returncode == 0, p.stderr
    archive = archive_path(runs)
    p = cli("cleanup", "--runs", str(runs))
    assert p.returncode == 0, p.stderr
    inventory = json.loads(p.stdout)
    assert inventory["archives"]
    assert inventory["total_archive_bytes"] >= archive.stat().st_size
    assert inventory["total_storage_bytes"] >= inventory["total_archive_bytes"]
    assert not root.exists()
