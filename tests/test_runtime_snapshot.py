from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

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
    blob = freeze_blob(tmp_path / "blobs", label="input", data=b"authority")
    source = tmp_path / "source"
    source.mkdir()
    (source / "candidate.txt").write_bytes(b"candidate")
    tree = freeze_tree(source, tmp_path / "trees")

    assert published == [blob.directory, tree.directory]
    assert not stat.S_IMODE(blob.directory.stat().st_mode) & 0o222
    assert not stat.S_IMODE(tree.directory.stat().st_mode) & 0o222


def test_tree_freeze_retains_exact_bytes_after_the_source_changes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payload = source / "candidate.txt"
    payload.write_bytes(b"reviewed candidate")
    payload.chmod(0o444)

    snapshot = freeze_tree(source, tmp_path / "snapshots")
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
    snapshot = freeze_tree(source, tmp_path / "snapshots")

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
        freeze_tree(source, tmp_path / "snapshots")

    source.joinpath("escape").unlink()
    payload = source / "candidate.txt"
    payload.write_text("candidate", encoding="utf-8")
    payload.chmod(0o444)
    snapshot = freeze_tree(source, tmp_path / "snapshots")
    snapshot.directory.chmod(0o755)
    with pytest.raises(SnapshotError, match="directory is writable"):
        verify_frozen_tree(snapshot.directory, expected_digest=snapshot.digest)


def test_blob_verification_refuses_a_linked_manifest(tmp_path: Path) -> None:
    snapshot = freeze_blob(tmp_path / "blobs", label="input", data=b"authority")
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
