from __future__ import annotations

import json
import os
import shutil
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

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
        source_path = Path(os.fsdecode(source))
        if not stat.S_IMODE(source_path.stat().st_mode) & stat.S_IWUSR:
            raise PermissionError("hosted macOS refuses to rename a sealed directory")
        original_rename(source, destination)
        published.append(Path(os.fsdecode(destination)))

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


def test_blob_retry_recovers_a_complete_publication_interrupted_before_sealing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "blobs"
    real_seal = snapshot_module._seal_snapshot_root

    def interrupt_after_rename(_path: Path, _descriptor: int) -> None:
        raise SnapshotError("injected crash before root sealing")

    monkeypatch.setattr(snapshot_module, "_seal_snapshot_root", interrupt_after_rename)
    with pytest.raises(SnapshotError, match="injected crash"):
        freeze_blob(
            store,
            durable_through=tmp_path,
            label="input",
            data=b"authority",
        )

    (published,) = tuple(store.joinpath("input").iterdir())
    assert stat.S_IMODE(published.stat().st_mode) == 0o700
    inode = published.stat().st_ino

    monkeypatch.setattr(snapshot_module, "_seal_snapshot_root", real_seal)
    recovered = freeze_blob(
        store,
        durable_through=tmp_path,
        label="input",
        data=b"authority",
    )

    assert recovered.directory.stat().st_ino == inode
    assert stat.S_IMODE(recovered.directory.stat().st_mode) == 0o555


def test_tree_retry_recovers_a_complete_publication_interrupted_before_sealing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "candidate.txt").write_bytes(b"candidate")
    store = tmp_path / "trees"
    real_seal = snapshot_module._seal_snapshot_root

    def interrupt_after_rename(_path: Path, _descriptor: int) -> None:
        raise SnapshotError("injected crash before root sealing")

    monkeypatch.setattr(snapshot_module, "_seal_snapshot_root", interrupt_after_rename)
    with pytest.raises(SnapshotError, match="injected crash"):
        freeze_tree(source, store, durable_through=tmp_path)

    (published,) = tuple(store.iterdir())
    assert stat.S_IMODE(published.stat().st_mode) == 0o700
    inode = published.stat().st_ino

    monkeypatch.setattr(snapshot_module, "_seal_snapshot_root", real_seal)
    recovered = freeze_tree(source, store, durable_through=tmp_path)

    assert recovered.directory.stat().st_ino == inode
    assert stat.S_IMODE(recovered.directory.stat().st_mode) == 0o555


def test_concurrent_blob_publishers_converge_on_one_canonical_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "blobs"
    rendezvous = Barrier(2)
    real_rename = os.rename

    def collide_at_publication(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        if Path(os.fsdecode(source)).name.startswith(".blob-"):
            rendezvous.wait(timeout=5)
        real_rename(source, destination)

    monkeypatch.setattr(snapshot_module.os, "rename", collide_at_publication)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [
            workers.submit(
                freeze_blob,
                store,
                durable_through=tmp_path,
                label="input",
                data=b"authority",
            )
            for _ in range(2)
        ]
        publications = [future.result(timeout=10) for future in futures]

    canonical = tuple(store.joinpath("input").iterdir())
    assert len(canonical) == 1
    assert canonical[0].stat().st_ino == publications[0].directory.stat().st_ino
    assert publications[0].directory.stat().st_ino == publications[1].directory.stat().st_ino
    assert stat.S_IMODE(canonical[0].stat().st_mode) == 0o555


def test_concurrent_tree_publishers_converge_on_one_canonical_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "candidate.txt").write_bytes(b"candidate")
    store = tmp_path / "trees"
    rendezvous = Barrier(2)
    real_rename = os.rename

    def collide_at_publication(
        staging: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        if Path(os.fsdecode(staging)).name.startswith(".tree-"):
            rendezvous.wait(timeout=5)
        real_rename(staging, destination)

    monkeypatch.setattr(snapshot_module.os, "rename", collide_at_publication)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [
            workers.submit(
                freeze_tree,
                source,
                store,
                durable_through=tmp_path,
            )
            for _ in range(2)
        ]
        publications = [future.result(timeout=10) for future in futures]

    canonical = tuple(store.iterdir())
    assert len(canonical) == 1
    assert canonical[0].stat().st_ino == publications[0].directory.stat().st_ino
    assert publications[0].directory.stat().st_ino == publications[1].directory.stat().st_ino
    assert stat.S_IMODE(canonical[0].stat().st_mode) == 0o555


def test_blob_recovery_refuses_and_preserves_a_malformed_writable_orphan(
    tmp_path: Path,
) -> None:
    data = b"authority"
    digest = snapshot_module.digest_bytes(data)
    destination = tmp_path / "blobs" / "input" / digest.removeprefix("sha256:")
    destination.mkdir(parents=True, mode=0o700)
    poisoned = b"poisoned!"
    destination.joinpath("payload").write_bytes(poisoned)
    destination.joinpath("manifest.json").write_bytes(
        json.dumps(
            {
                "schema_version": "factory-blob-snapshot/1",
                "label": "input",
                "digest": digest,
                "size": len(poisoned),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    destination.joinpath("payload").chmod(0o444)
    destination.joinpath("manifest.json").chmod(0o444)

    with pytest.raises(SnapshotError, match="content address mismatch"):
        freeze_blob(
            tmp_path / "blobs",
            durable_through=tmp_path,
            label="input",
            data=data,
        )

    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    assert destination.joinpath("payload").read_bytes() == poisoned


def test_tree_recovery_refuses_and_preserves_a_malformed_writable_orphan(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source_payload = source / "candidate.txt"
    source_payload.write_bytes(b"candidate")
    digest = tree_digest(source)
    destination = tmp_path / "trees" / digest.removeprefix("sha256:")
    files = destination / "files"
    files.mkdir(parents=True, mode=0o755)
    poisoned = files / "candidate.txt"
    poisoned.write_bytes(b"poisoned!")
    original_mode = stat.S_IMODE(source_payload.stat().st_mode)
    destination.joinpath("manifest.json").write_bytes(
        json.dumps(
            {
                "schema_version": "factory-tree-snapshot/1",
                "tree_digest": digest,
                "files": [
                    {
                        "path": "candidate.txt",
                        "mode": original_mode,
                        "frozen_mode": original_mode & ~0o222,
                        "digest": snapshot_module.digest_bytes(b"candidate"),
                    }
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    poisoned.chmod(original_mode & ~0o222)
    destination.joinpath("manifest.json").chmod(0o444)
    files.chmod(0o555)
    destination.chmod(0o700)

    with pytest.raises(SnapshotError, match="payload digest mismatch"):
        freeze_tree(source, tmp_path / "trees", durable_through=tmp_path)

    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    assert poisoned.read_bytes() == b"poisoned!"


def test_blob_publication_refuses_canonical_path_replacement_during_sealing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"authority"
    digest = snapshot_module.digest_bytes(data)
    destination = tmp_path / "blobs" / "input" / digest.removeprefix("sha256:")
    displaced = destination.with_name(f"{destination.name}.displaced")
    real_fchmod = os.fchmod
    replaced = False

    def replace_after_sealing(descriptor: int, mode: int) -> None:
        nonlocal replaced
        real_fchmod(descriptor, mode)
        if mode == 0o555 and stat.S_ISDIR(os.fstat(descriptor).st_mode) and not replaced:
            replaced = True
            os.rename(destination, displaced)
            destination.mkdir(mode=0o700)

    monkeypatch.setattr(snapshot_module.os, "fchmod", replace_after_sealing)
    with pytest.raises(SnapshotError, match="pathname changed"):
        freeze_blob(
            tmp_path / "blobs",
            durable_through=tmp_path,
            label="input",
            data=data,
        )

    assert replaced
    assert stat.S_IMODE(displaced.stat().st_mode) == 0o555
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700


def test_blob_publication_refuses_exact_path_replacement_after_durability_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"authority"
    digest = snapshot_module.digest_bytes(data)
    destination = tmp_path / "blobs" / "input" / digest.removeprefix("sha256:")
    displaced = destination.with_name(f"{destination.name}.displaced")
    real_sync = snapshot_module._sync_snapshot_publication

    def replace_after_sync(
        directory: Path,
        *,
        durable_through: Path,
        internal_directories: tuple[Path, ...] = (),
    ) -> None:
        real_sync(
            directory,
            durable_through=durable_through,
            internal_directories=internal_directories,
        )
        os.rename(directory, displaced)
        shutil.copytree(displaced, directory)

    monkeypatch.setattr(snapshot_module, "_sync_snapshot_publication", replace_after_sync)
    with pytest.raises(SnapshotError, match="pathname changed"):
        freeze_blob(
            tmp_path / "blobs",
            durable_through=tmp_path,
            label="input",
            data=data,
        )

    assert destination.stat().st_ino != displaced.stat().st_ino
    verify_frozen_blob(destination, expected_digest=digest, label="input")


def test_tree_recovery_detects_child_mutation_after_descriptor_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "candidate.txt").write_bytes(b"candidate")
    store = tmp_path / "trees"
    real_seal = snapshot_module._seal_snapshot_root

    def interrupt_before_sealing(_path: Path, _descriptor: int) -> None:
        raise SnapshotError("injected crash before root sealing")

    monkeypatch.setattr(snapshot_module, "_seal_snapshot_root", interrupt_before_sealing)
    with pytest.raises(SnapshotError, match="injected crash"):
        freeze_tree(source, store, durable_through=tmp_path)
    (destination,) = tuple(store.iterdir())
    retained = destination / "files" / "candidate.txt"

    monkeypatch.setattr(snapshot_module, "_seal_snapshot_root", real_seal)
    real_preflight = snapshot_module._preflight_recoverable_tree
    mutated = False

    def mutate_after_preflight(descriptor: int, *, expected_digest: str) -> None:
        nonlocal mutated
        real_preflight(descriptor, expected_digest=expected_digest)
        if not mutated:
            retained.chmod(0o644)
            retained.write_bytes(b"poisoned!")
            retained.chmod(0o444)
            mutated = True

    monkeypatch.setattr(
        snapshot_module,
        "_preflight_recoverable_tree",
        mutate_after_preflight,
    )
    with pytest.raises(SnapshotError, match="payload digest mismatch"):
        freeze_tree(source, store, durable_through=tmp_path)

    assert mutated
    assert stat.S_IMODE(destination.stat().st_mode) == 0o555
    assert retained.read_bytes() == b"poisoned!"


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

    assert len(events) == 8
    staging_root = events[3][1]
    assert events[:4] == [
        ("internal", staging_root / "files" / "a" / "b"),
        ("internal", staging_root / "files" / "a"),
        ("internal", staging_root / "files"),
        ("internal", staging_root),
    ]
    assert events[4:] == [
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


@pytest.mark.parametrize("failure_index", range(4))
def test_blob_retry_recovers_after_each_directory_chain_sync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_index: int,
) -> None:
    run_root = tmp_path / "run-1"
    run_root.mkdir()
    store = run_root / "blobs"
    real_fsync = durability_module.fsync_directory
    calls: list[Path] = []
    inject = True

    def fail_one_component(path: str | Path) -> None:
        calls.append(Path(path))
        if inject and len(calls) - 1 == failure_index:
            raise DurabilityError(f"injected chain failure {failure_index}")
        real_fsync(path)

    monkeypatch.setattr(durability_module, "fsync_directory", fail_one_component)
    with pytest.raises(SnapshotError, match=f"injected chain failure {failure_index}"):
        freeze_blob(
            store,
            durable_through=run_root,
            label="input",
            data=b"authority",
        )

    (published,) = tuple(store.joinpath("input").iterdir())
    inode = published.stat().st_ino
    assert stat.S_IMODE(published.stat().st_mode) == 0o555

    inject = False
    recovered = freeze_blob(
        store,
        durable_through=run_root,
        label="input",
        data=b"authority",
    )
    assert recovered.directory.stat().st_ino == inode
    assert stat.S_IMODE(recovered.directory.stat().st_mode) == 0o555


def test_tree_retry_recovers_after_published_internal_directory_sync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root = tmp_path / "run-1"
    run_root.mkdir()
    source = tmp_path / "source"
    (source / "a" / "b").mkdir(parents=True)
    (source / "a" / "b" / "candidate.txt").write_bytes(b"candidate")
    store = run_root / "trees"
    address = tree_digest(source).removeprefix("sha256:")
    real_fsync = snapshot_module.fsync_directory
    inject = True

    def fail_published_internal(path: str | Path) -> None:
        if inject and address in Path(path).parts:
            raise DurabilityError("injected published internal-directory failure")
        real_fsync(path)

    monkeypatch.setattr(snapshot_module, "fsync_directory", fail_published_internal)
    with pytest.raises(SnapshotError, match="published internal-directory failure"):
        freeze_tree(source, store, durable_through=run_root)

    (published,) = tuple(store.iterdir())
    inode = published.stat().st_ino
    assert stat.S_IMODE(published.stat().st_mode) == 0o555

    inject = False
    recovered = freeze_tree(source, store, durable_through=run_root)
    assert recovered.directory.stat().st_ino == inode
    assert stat.S_IMODE(recovered.directory.stat().st_mode) == 0o555


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
