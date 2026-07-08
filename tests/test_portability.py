"""Portability proof — the core is importable, testable, and green with NO target pack
present. The only target in the tree is the synthetic empty fixture, and the core reasons over
it end-to-end without importing anything target-specific.

This is the PRD §10 acceptance criterion #1 (portability) and #7 (purity), asserted as code.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

from factory_core.roles import (
    ILLUSTRATIVE_CAPABILITIES,
    CapabilityCatalog,
    RoleModel,
    build_grants,
    build_roles,
)
from factory_core.target import load_target_manifest
from tests.conftest import SYNTHETIC_TARGET

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_core_imports_with_no_target_pack() -> None:
    for mod in ("factory_core", "factory_core.manifest", "factory_core.target",
                "factory_core.adapters", "factory_core.roles"):
        assert importlib.import_module(mod) is not None


def test_no_target_pack_exists_in_the_tree() -> None:
    # The whole promise: deleting every target pack leaves the core green. There is no
    # target-pack directory here at all — only the synthetic fixture.
    assert not (REPO_ROOT / "targets").exists()
    assert not (REPO_ROOT / "target_packs").exists()
    assert importlib.util.find_spec("targets") is None
    assert importlib.util.find_spec("target_packs") is None


def test_full_pipeline_shape_against_the_synthetic_empty_target() -> None:
    # Load the only target present (synthetic/empty) and drive the Phase-0 surfaces.
    tm = load_target_manifest(SYNTHETIC_TARGET)
    assert tm.target_id == "synthetic-empty"

    catalog = CapabilityCatalog.from_names(ILLUSTRATIVE_CAPABILITIES)
    model = RoleModel(catalog, build_roles(tm.roles))
    grants = build_grants(tm.grants, tm.target_id)
    caps = model.resolve("alice@example.invalid", tm.target_id, grants)
    assert "prd.author" in caps


def test_purity_guard_is_green_as_part_of_the_portability_proof() -> None:
    spec = importlib.util.spec_from_file_location(
        "check_core_purity", REPO_ROOT / "scripts" / "check_core_purity.py"
    )
    assert spec and spec.loader
    purity = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = purity
    spec.loader.exec_module(purity)
    findings = purity.run(
        REPO_ROOT / "factory_core",
        REPO_ROOT / "core_purity_baseline.json",
        REPO_ROOT / "pyproject.toml",
    )
    assert findings == [], "portability requires purity: " + "\n".join(str(f) for f in findings)
