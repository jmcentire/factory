from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from factory_core.manifest import digest_bytes
from factory_runtime.projection_bundle import ProjectionBundleError, bundle_runner_projection
from factory_runtime.schema import validate_document


def _manifest(root: Path) -> str:
    digest = hashlib.sha256()
    for base, directories, names in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if name != ".git")
        for name in sorted(names):
            path = Path(base) / name
            relative = path.relative_to(root).as_posix()
            mode = stat.S_IMODE(path.lstat().st_mode)
            digest.update(relative.encode() + b"\0" + oct(mode).encode() + b"\0")
            digest.update(b"file\0" + path.read_bytes())
    return "sha256:" + digest.hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "role-tree"
    (root / "src").mkdir(parents=True)
    (root / "src" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "README.md").write_text("# Projection\n", encoding="utf-8")
    git = root / ".git"
    git.mkdir()
    (git / "config").write_text("secret host history", encoding="utf-8")
    receipt = {
        "role": "coder",
        "sha": "a" * 40,
        "tree": "b" * 40,
        "source_root": str(tmp_path / "must-not-leak-source"),
        "dest": str(root),
        "manifest_digest": _manifest(root),
    }
    return root, receipt


def _bundle(root: Path, receipt: dict[str, str]) -> dict[str, Any]:
    return bundle_runner_projection(
        root,
        projection_receipt=receipt,
        run_id="run-1",
        generation=1,
        role="coder",
        target_state_digest=digest_bytes(b"target-state"),
        resolved_commit="a" * 40,
        resolved_tree="b" * 40,
    )


def test_projection_is_bounded_data_and_contains_no_host_paths_or_git_history(
    tmp_path: Path,
) -> None:
    root, receipt = _fixture(tmp_path)

    document = _bundle(root, receipt)

    validate_document("runner-projection", document)
    serialized = json.dumps(document, sort_keys=True)
    assert str(root) not in serialized
    assert str(tmp_path / "must-not-leak-source") not in serialized
    assert "secret host history" not in serialized
    assert [item["relative_path"] for item in document["files"]] == [
        "README.md",
        "src/feature.py",
    ]


def test_projection_rederives_receipt_and_rejects_changed_bytes(tmp_path: Path) -> None:
    root, receipt = _fixture(tmp_path)
    (root / "README.md").write_text("changed after receipt\n", encoding="utf-8")

    with pytest.raises(ProjectionBundleError, match="differ"):
        _bundle(root, receipt)


def test_projection_rejects_symlink_even_when_it_points_inside(tmp_path: Path) -> None:
    root, receipt = _fixture(tmp_path)
    link = root / "copy.py"
    link.symlink_to("src/feature.py")
    receipt["manifest_digest"] = "sha256:" + "0" * 64

    with pytest.raises(ProjectionBundleError, match="regular files only"):
        _bundle(root, receipt)


def test_projection_rejects_an_individually_oversized_file(tmp_path: Path) -> None:
    root, receipt = _fixture(tmp_path)
    (root / "large.bin").write_bytes(b"x" * 524_289)
    receipt["manifest_digest"] = "sha256:" + "0" * 64

    with pytest.raises(ProjectionBundleError, match="too large"):
        _bundle(root, receipt)


def test_projection_file_content_digest_rederives(tmp_path: Path) -> None:
    root, receipt = _fixture(tmp_path)
    document = _bundle(root, receipt)
    feature = next(item for item in document["files"] if item["relative_path"] == "src/feature.py")
    assert feature["content_digest"] == digest_bytes(b"VALUE = 1\n")
