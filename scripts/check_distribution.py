#!/usr/bin/env python3
"""Fail closed when Factory wheel/sdist contents are incomplete or unsafe."""

from __future__ import annotations

import argparse
import email
import hashlib
import json
import tarfile
import tomllib
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

HISTORICAL_RUNNER_RECEIPT_V2_SHA256 = (
    "6e3a432425e2b79395c7c7cfdb59b3f09ba0b6b24daf0c952637e71f055f8e7c"
)
REQUIRED_PACKAGE_FILES = (
    "factory_core/__init__.py",
    "factory_runtime/__init__.py",
    "factory_runtime/schemas/runner-receipt-v2.schema.json",
    "factory_runtime/schemas/runner-receipt.schema.json",
    "factory_runtime/schemas/validator-adversarial-review.schema.json",
    "factory_runtime/schemas/validator-review-subject.schema.json",
)


class DistributionError(ValueError):
    """A built distribution is incomplete, ambiguous, or unsafe."""


def _one[T](paths: Iterable[T], *, label: str) -> T:
    selected = sorted(paths, key=str)
    if len(selected) != 1:
        raise DistributionError(f"expected exactly one {label}, found {len(selected)}")
    return selected[0]


def _safe_parts(name: str, *, label: str) -> tuple[str, ...]:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise DistributionError(f"{label} contains an unsafe member path: {name!r}")
    return path.parts


def _metadata_version(raw: bytes, *, label: str) -> str:
    metadata = email.message_from_bytes(raw)
    if metadata.get("Name") != "factory-core":
        raise DistributionError(f"{label} has the wrong project name")
    version = metadata.get("Version")
    if not version:
        raise DistributionError(f"{label} has no project version")
    return version


def _verify_schema_bytes(data: bytes, *, label: str) -> None:
    actual = hashlib.sha256(data).hexdigest()
    if actual != HISTORICAL_RUNNER_RECEIPT_V2_SHA256:
        raise DistributionError(
            f"{label} changed historical runner-receipt/2 bytes: {actual}"
        )
    try:
        schema = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DistributionError(f"{label} is not JSON: {exc}") from exc
    if schema.get("$id") != "factory://schemas/runner-receipt/2":
        raise DistributionError(f"{label} has the wrong canonical schema id")


def inspect_wheel(path: Path, *, version: str) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        parts = [_safe_parts(name, label="wheel") for name in names]
        if any("__pycache__" in item or item[-1].endswith((".pyc", ".pyo")) for item in parts):
            raise DistributionError("wheel contains Python cache artifacts")
        if any(item[0] == "tests" for item in parts):
            raise DistributionError("wheel unexpectedly contains repository tests")
        missing = sorted(set(REQUIRED_PACKAGE_FILES) - set(names))
        if missing:
            raise DistributionError("wheel is missing required files: " + ", ".join(missing))
        metadata_name = _one(
            (name for name in names if name.endswith(".dist-info/METADATA")),
            label="wheel metadata file",
        )
        if _metadata_version(archive.read(metadata_name), label="wheel metadata") != version:
            raise DistributionError("wheel metadata version does not match pyproject.toml")
        entry_points = _one(
            (name for name in names if name.endswith(".dist-info/entry_points.txt")),
            label="wheel entry-points file",
        )
        if b"factory = factory_runtime.cli:main" not in archive.read(entry_points):
            raise DistributionError("wheel does not expose the factory CLI")
        _verify_schema_bytes(
            archive.read("factory_runtime/schemas/runner-receipt-v2.schema.json"),
            label="wheel historical runner schema",
        )


def inspect_sdist(path: Path, *, version: str) -> None:
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        parts = [_safe_parts(member.name, label="sdist") for member in members]
        if any(not (member.isfile() or member.isdir()) for member in members):
            raise DistributionError("sdist contains a link or special-file member")
        roots = {item[0] for item in parts}
        if len(roots) != 1:
            raise DistributionError("sdist does not have exactly one archive root")
        root = next(iter(roots))
        relative_names = {"/".join(item[1:]) for item in parts if len(item) > 1}
        if any(item.startswith("tests/") or item == "tests" for item in relative_names):
            raise DistributionError("sdist contains a partial repository test tree")
        if any(
            "__pycache__" in item or item.endswith((".pyc", ".pyo"))
            for item in relative_names
        ):
            raise DistributionError("sdist contains Python cache artifacts")
        required = {
            "LICENSE",
            "README.md",
            "pyproject.toml",
            "PKG-INFO",
            *REQUIRED_PACKAGE_FILES,
        }
        missing = sorted(required - relative_names)
        if missing:
            raise DistributionError("sdist is missing required files: " + ", ".join(missing))

        metadata_member = archive.getmember(f"{root}/PKG-INFO")
        metadata_stream = archive.extractfile(metadata_member)
        if metadata_stream is None:
            raise DistributionError("sdist metadata is unreadable")
        if _metadata_version(metadata_stream.read(), label="sdist metadata") != version:
            raise DistributionError("sdist metadata version does not match pyproject.toml")

        schema_member = archive.getmember(
            f"{root}/factory_runtime/schemas/runner-receipt-v2.schema.json"
        )
        schema_stream = archive.extractfile(schema_member)
        if schema_stream is None:
            raise DistributionError("sdist historical runner schema is unreadable")
        _verify_schema_bytes(schema_stream.read(), label="sdist historical runner schema")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    arguments = parser.parse_args()

    project = tomllib.loads(
        (arguments.project_root / "pyproject.toml").read_text(encoding="utf-8")
    )
    version = str(project["project"]["version"])
    wheel = _one(arguments.dist_dir.glob("*.whl"), label="wheel")
    sdist = _one(arguments.dist_dir.glob("*.tar.gz"), label="sdist")
    inspect_wheel(wheel, version=version)
    inspect_sdist(sdist, version=version)
    print(f"distribution artifacts verified: factory-core {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
