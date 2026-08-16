from __future__ import annotations

import re
import tomllib
from pathlib import Path

import factory_core

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_version_matches_package_metadata_and_changelog() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    latest = re.search(r"^## \[([^]]+)]", changelog, re.MULTILINE)

    assert factory_core.__version__ == version
    assert latest is not None
    assert latest.group(1) == version
