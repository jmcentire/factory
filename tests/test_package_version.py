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
    headers = re.findall(r"^## \[([^]]+)]", changelog, re.MULTILINE)
    # Keep a Changelog: work merged ahead of its release number sits under one [Unreleased]
    # section at the top; the newest released section still names the package version.
    released = headers[1:] if headers[:1] == ["Unreleased"] else headers

    assert factory_core.__version__ == version
    assert "Unreleased" not in released
    assert released and released[0] == version
