"""A target's declared projection limits reach every lane boundary that bounds a projection.

The limits come from the manifest bytes the run authorized, never from the live target, and a
retained manifest whose digest is not the one the run's target state names is refused.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import factory_runtime.cli as runtime_cli
from factory_core.target import load_target_manifest
from factory_runtime.projection_limits import (
    RETAINED_MANIFEST,
    ProjectionLimitsError,
    retained_projection_limits,
)
from factory_runtime.state import RunStore
from tests.test_harness_scripts import execution_truth_fixture

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT / "tests" / "fixtures" / "synthetic_target" / "target.toml"


def _manifest_with_limits(tmp_path: Path, **limits: int) -> Path:
    text = SYNTHETIC.read_text(encoding="utf-8")
    table = "".join(f"{field} = {value}\n" for field, value in limits.items())
    path = tmp_path / "limited-target.toml"
    path.write_text(f"{text.rstrip()}\n\n[build.projection_limits]\n{table}", encoding="utf-8")
    return path


def _control_root(tmp_path: Path, manifest: Path) -> tuple[Path, str]:
    root = tmp_path / "run"
    retained = root / RETAINED_MANIFEST
    retained.parent.mkdir(parents=True)
    retained.write_bytes(manifest.read_bytes())
    return root, load_target_manifest(manifest).source_digest


def test_only_the_declared_limits_are_returned(tmp_path: Path) -> None:
    root, digest = _control_root(tmp_path, _manifest_with_limits(tmp_path, max_file_bytes=64))

    assert retained_projection_limits(root, expected_digest=digest) == {"max_file_bytes": 64}


def test_a_manifest_that_declares_no_limits_leaves_every_default(tmp_path: Path) -> None:
    root, digest = _control_root(tmp_path, SYNTHETIC)

    assert retained_projection_limits(root, expected_digest=digest) == {}


def test_a_retained_manifest_with_another_digest_is_refused(tmp_path: Path) -> None:
    root, _digest = _control_root(tmp_path, _manifest_with_limits(tmp_path, max_files=1))
    authorized = load_target_manifest(SYNTHETIC).source_digest

    with pytest.raises(ProjectionLimitsError, match="differs from the one"):
        retained_projection_limits(root, expected_digest=authorized)


def test_a_run_without_a_retained_manifest_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ProjectionLimitsError, match="no retained target manifest"):
        retained_projection_limits(tmp_path, expected_digest="sha256:" + "0" * 64)


def _freeze(tmp_path: Path, source: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    store = tmp_path / "store"
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "harness" / "lane_repository.py"),
            "freeze",
            "--source",
            str(source),
            "--store",
            str(store / "snapshots"),
            "--durable-through",
            str(store),
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
        env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"},
    )


def test_the_lane_freeze_honors_the_run_declared_file_ceiling(tmp_path: Path) -> None:
    source = tmp_path / "lane"
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / "big.txt").write_bytes(b"x" * 100)
    limited, digest = _control_root(tmp_path, _manifest_with_limits(tmp_path, max_file_bytes=64))

    refused = _freeze(
        tmp_path / "a", source, "--control-root", str(limited), "--target-manifest-digest", digest
    )
    assert refused.returncode == 70
    assert "file exceeds its byte ceiling: big.txt" in refused.stderr

    default_root = tmp_path / "default"
    default_root.mkdir()
    open_root, open_digest = _control_root(default_root, SYNTHETIC)
    frozen = _freeze(
        tmp_path / "b",
        source,
        "--control-root",
        str(open_root),
        "--target-manifest-digest",
        open_digest,
    )
    assert frozen.returncode == 0, frozen.stderr


def test_the_lane_freeze_refuses_half_a_run_binding(tmp_path: Path) -> None:
    source = tmp_path / "lane"
    source.mkdir()

    refused = _freeze(tmp_path, source, "--control-root", str(tmp_path))

    assert refused.returncode == 70
    assert "must be given together" in refused.stderr


def test_the_runner_bundle_receives_the_run_declared_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest_with_limits(tmp_path, max_files=7, max_total_bytes=4096)
    _operator, run_root, _context = execution_truth_fixture(
        tmp_path, target_manifest=manifest
    )
    runs = run_root.parent
    run = RunStore(runs).load("r1")
    authorized = load_target_manifest(manifest).source_digest
    assert run.target_state["target_manifest_digest"] == authorized
    received: dict[str, object] = {}

    def capture(root: object, **keywords: object) -> dict[str, object]:
        received.update(keywords)
        return {"projection_manifest_digest": "sha256:" + "0" * 64}

    monkeypatch.setattr(runtime_cli, "bundle_runner_projection", capture)
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"role": "coder"}), encoding="utf-8")
    projection_root = tmp_path / "projection"
    projection_root.mkdir()

    assert (
        runtime_cli.main(
            [
                "bundle-runner-projection",
                "--runs",
                str(runs),
                "--run-id",
                "r1",
                "--role",
                "coder",
                "--projection-root",
                str(projection_root),
                "--projection-receipt",
                str(receipt),
                "--output",
                str(tmp_path / "bundle.json"),
            ]
        )
        == 0
    )
    assert received["max_files"] == 7
    assert received["max_total_bytes"] == 4096
    assert "max_file_bytes" not in received
