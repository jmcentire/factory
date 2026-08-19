from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

import factory_runtime.durability as durability_module
import factory_runtime.snapshot as snapshot_module
from factory_runtime.durability import DurabilityError
from factory_runtime.snapshot import (
    SnapshotError,
    freeze_blob,
    freeze_tree,
    tree_digest,
    verify_frozen_blob,
    verify_frozen_tree,
)


def test_snapshot_staging_root_remains_renameable_until_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_rename = os.rename
    published: list[Path] = []

    def macos_rename(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        source_path = Path(source)
        if not stat.S_IMODE(source_path.stat().st_mode) & stat.S_IWUSR:
            raise PermissionError("hosted macOS refuses to rename a sealed directory")
        original_rename(source, destination)
        published.append(Path(destination))

    monkeypatch.setattr(os, "rename", macos_rename)
    blob = freeze_blob(
        tmp_path / "blobs",
        durable_through=tmp_path,
        label="input",
        data=b"authority",
    )
    source = tmp_path / "source"
    source.mkdir()
    (source / "candidate.txt").write_bytes(b"candidate")
    tree = freeze_tree(source, tmp_path / "trees", durable_through=tmp_path)

    assert published == [blob.directory, tree.directory]
    assert not stat.S_IMODE(blob.directory.stat().st_mode) & 0o222
    assert not stat.S_IMODE(tree.directory.stat().st_mode) & 0o222


def test_tree_freeze_retains_exact_bytes_after_the_source_changes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payload = source / "candidate.txt"
    payload.write_bytes(b"reviewed candidate")
    payload.chmod(0o444)

    snapshot = freeze_tree(source, tmp_path / "snapshots", durable_through=tmp_path)
    payload.chmod(0o644)
    payload.write_bytes(b"later unreviewed candidate")

    verified = verify_frozen_tree(snapshot.directory, expected_digest=snapshot.digest)
    assert (verified.files_directory / "candidate.txt").read_bytes() == b"reviewed candidate"
    assert tree_digest(verified.files_directory) == snapshot.digest


def test_a_digest_only_manifest_is_not_accepted_as_a_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "candidate.txt").write_text("candidate", encoding="utf-8")
    digest = tree_digest(source)
    manifest_only = tmp_path / digest.removeprefix("sha256:")
    manifest_only.mkdir()
    (manifest_only / "manifest.json").write_text(
        json.dumps({"tree_digest": digest}),
        encoding="utf-8",
    )
    manifest_only.chmod(0o555)

    with pytest.raises(SnapshotError, match="unexpected contents"):
        verify_frozen_tree(manifest_only, expected_digest=digest)


def test_tree_verification_rederives_retained_payload_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payload = source / "candidate.txt"
    payload.write_bytes(b"reviewed")
    payload.chmod(0o444)
    snapshot = freeze_tree(source, tmp_path / "snapshots", durable_through=tmp_path)

    snapshot.directory.chmod(0o755)
    snapshot.files_directory.chmod(0o755)
    retained = snapshot.files_directory / "candidate.txt"
    retained.chmod(0o644)
    retained.write_bytes(b"tampered")
    retained.chmod(0o444)
    snapshot.files_directory.chmod(0o555)
    snapshot.directory.chmod(0o555)

    with pytest.raises(SnapshotError, match="payload digest mismatch"):
        verify_frozen_tree(snapshot.directory, expected_digest=snapshot.digest)


def test_tree_freeze_refuses_links_and_verifier_refuses_writable_storage(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (source / "escape").symlink_to(outside)
    with pytest.raises(SnapshotError, match="symlink"):
        freeze_tree(source, tmp_path / "snapshots", durable_through=tmp_path)

    source.joinpath("escape").unlink()
    payload = source / "candidate.txt"
    payload.write_text("candidate", encoding="utf-8")
    payload.chmod(0o444)
    snapshot = freeze_tree(source, tmp_path / "snapshots", durable_through=tmp_path)
    snapshot.directory.chmod(0o755)
    with pytest.raises(SnapshotError, match="directory is writable"):
        verify_frozen_tree(snapshot.directory, expected_digest=snapshot.digest)


def test_blob_verification_refuses_a_linked_manifest(tmp_path: Path) -> None:
    snapshot = freeze_blob(
        tmp_path / "blobs",
        durable_through=tmp_path,
        label="input",
        data=b"authority",
    )
    external = tmp_path / "external-manifest.json"
    external.write_bytes(snapshot.directory.joinpath("manifest.json").read_bytes())
    snapshot.directory.chmod(0o755)
    snapshot.directory.joinpath("manifest.json").unlink()
    snapshot.directory.joinpath("manifest.json").symlink_to(external)
    snapshot.directory.chmod(0o555)

    with pytest.raises(SnapshotError, match="symlink"):
        verify_frozen_blob(
            snapshot.directory,
            expected_digest=snapshot.digest,
            label="input",
        )


def test_blob_publication_commits_every_ancestor_through_declared_run_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root = tmp_path / "run-1"
    run_root.mkdir()
    store = run_root / "new" / "evidence" / "blobs"
    real_fsync_directory = durability_module.fsync_directory
    synced: list[Path] = []

    def track_fsync_directory(path: str | Path) -> None:
        synced.append(Path(path))
        real_fsync_directory(path)

    monkeypatch.setattr(durability_module, "fsync_directory", track_fsync_directory)

    frozen = freeze_blob(
        store,
        durable_through=run_root,
        label="input",
        data=b"authority",
    )

    expected: list[Path] = []
    current = frozen.directory
    while True:
        expected.append(current)
        if current == run_root:
            break
        current = current.parent
    assert synced == expected


def test_tree_publication_commits_internal_directories_before_public_address(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root = tmp_path / "run-1"
    run_root.mkdir()
    source = tmp_path / "source"
    (source / "a" / "b").mkdir(parents=True)
    (source / "a" / "b" / "candidate.txt").write_bytes(b"candidate")
    real_internal_fsync = snapshot_module.fsync_directory
    real_chain_fsync = snapshot_module.fsync_directory_chain
    events: list[tuple[str, Path]] = []

    def track_internal(path: str | Path) -> None:
        events.append(("internal", Path(path)))
        real_internal_fsync(path)

    def track_chain(start: str | Path, *, through: str | Path) -> None:
        events.append(("chain", Path(start)))
        assert Path(through) == run_root
        real_chain_fsync(start, through=through)

    monkeypatch.setattr(snapshot_module, "fsync_directory", track_internal)
    monkeypatch.setattr(snapshot_module, "fsync_directory_chain", track_chain)

    frozen = freeze_tree(
        source,
        run_root / "evidence" / "review-snapshots",
        durable_through=run_root,
    )

    assert events == [
        ("internal", frozen.files_directory / "a" / "b"),
        ("internal", frozen.files_directory / "a"),
        ("internal", frozen.files_directory),
        ("chain", frozen.directory),
    ]


def test_identical_blob_retry_resyncs_without_replacing_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root = tmp_path / "run-1"
    run_root.mkdir()
    store = run_root / "evidence" / "blobs"
    first = freeze_blob(
        store,
        durable_through=run_root,
        label="input",
        data=b"authority",
    )
    inode = first.directory.stat().st_ino
    real_sync = snapshot_module.fsync_directory_chain
    synced: list[tuple[Path, Path]] = []

    def track_sync(start: str | Path, *, through: str | Path) -> None:
        synced.append((Path(start), Path(through)))
        real_sync(start, through=through)

    def refuse_rename(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("an identical retry must not republish the snapshot")

    monkeypatch.setattr(snapshot_module, "fsync_directory_chain", track_sync)
    monkeypatch.setattr(snapshot_module.os, "rename", refuse_rename)

    retried = freeze_blob(
        store,
        durable_through=run_root,
        label="input",
        data=b"authority",
    )

    assert retried.directory.stat().st_ino == inode
    assert synced == [(first.directory, run_root)]


def test_publication_sync_failure_blocks_return_and_identical_retry_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root = tmp_path / "run-1"
    run_root.mkdir()
    store = run_root / "evidence" / "blobs"
    real_sync = snapshot_module.fsync_directory_chain

    def fail_sync(_start: str | Path, *, through: str | Path) -> None:
        assert Path(through) == run_root
        raise DurabilityError("injected publication sync failure")

    monkeypatch.setattr(snapshot_module, "fsync_directory_chain", fail_sync)
    with pytest.raises(SnapshotError, match="injected publication sync failure"):
        freeze_blob(
            store,
            durable_through=run_root,
            label="input",
            data=b"authority",
        )

    published = tuple(store.joinpath("input").iterdir())
    assert len(published) == 1
    inode = published[0].stat().st_ino

    monkeypatch.setattr(snapshot_module, "fsync_directory_chain", real_sync)
    recovered = freeze_blob(
        store,
        durable_through=run_root,
        label="input",
        data=b"authority",
    )
    assert recovered.directory.stat().st_ino == inode


def test_snapshot_refuses_to_sync_outside_or_through_filesystem_root(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run-1"
    run_root.mkdir()

    with pytest.raises(SnapshotError, match="outside declared durability root"):
        freeze_blob(
            tmp_path / "outside",
            durable_through=run_root,
            label="input",
            data=b"authority",
        )
    with pytest.raises(SnapshotError, match="may not be a filesystem root"):
        freeze_blob(
            tmp_path / "outside",
            durable_through=Path(tmp_path.anchor),
            label="input",
            data=b"authority",
        )
