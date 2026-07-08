"""Purity guard tests — GREEN on the clean core, RED when target coupling is injected."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_purity():
    spec = importlib.util.spec_from_file_location(
        "check_core_purity", REPO_ROOT / "scripts" / "check_core_purity.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass decorator resolves the module via sys.modules
    spec.loader.exec_module(module)
    return module


PURITY = _load_purity()
BASELINE = REPO_ROOT / "core_purity_baseline.json"
DENYLIST = REPO_ROOT / "core_purity_denylist.json"
PYPROJECT = REPO_ROOT / "pyproject.toml"
CORE = REPO_ROOT / "factory_core"


def _write_denylist(path: Path, tokens: list[str]) -> Path:
    path.write_text(json.dumps({"version": 1, "tokens": tokens}), encoding="utf-8")
    return path


def test_clean_core_is_green() -> None:
    findings = PURITY.run(CORE, BASELINE, PYPROJECT, DENYLIST)
    assert findings == [], "the clean core must import nothing target-specific:\n" + "\n".join(
        str(f) for f in findings
    )


def test_shipped_denylist_is_empty() -> None:
    # The public, generic core ships an empty token set — nothing target-specific to catch.
    tokens = PURITY.load_denylist_tokens(DENYLIST)
    assert tokens == (), f"the generic core must ship an empty denylist; found {tokens}"


def test_red_when_a_targets_import_is_injected(tmp_path) -> None:
    core_copy = tmp_path / "factory_core"
    shutil.copytree(CORE, core_copy)
    (core_copy / "_injected.py").write_text("import target_packs.acme  # coupling!\n")

    findings = PURITY.run(core_copy, BASELINE, PYPROJECT, DENYLIST)
    assert findings, "injected target import must be caught"
    assert any(f.check == "imports" and "target import" in f.detail for f in findings)


def test_red_when_a_non_allowlisted_import_is_injected(tmp_path) -> None:
    core_copy = tmp_path / "factory_core"
    shutil.copytree(CORE, core_copy)
    (core_copy / "_injected.py").write_text("import requests  # not on the allowlist\n")

    findings = PURITY.run(core_copy, BASELINE, PYPROJECT, DENYLIST)
    assert any(f.check == "imports" and "non-allowlisted" in f.detail for f in findings)


def test_configured_denylist_token_is_caught(tmp_path) -> None:
    # The token set is DATA: configure a denylist with fictional example tokens and prove the
    # guard catches an identifier/string that names one of them. This exercises the same
    # mechanism a consuming target uses when it fills in its own tokens.
    core_copy = tmp_path / "factory_core"
    shutil.copytree(CORE, core_copy)
    (core_copy / "_injected.py").write_text('acme_rule = "widget posture"\n')
    denylist = _write_denylist(tmp_path / "denylist.json", ["acme", "widget"])

    findings = PURITY.run(core_copy, BASELINE, PYPROJECT, denylist)
    tokens = {f.detail for f in findings if f.check == "tokens"}
    assert any("acme" in d for d in tokens)
    assert any("widget" in d for d in tokens)


def test_empty_denylist_catches_no_tokens(tmp_path) -> None:
    # With an empty token set (the generic-core default) the same identifier is NOT flagged —
    # the token check is trivially green, while import/reverse-dep checks stay enforced.
    core_copy = tmp_path / "factory_core"
    shutil.copytree(CORE, core_copy)
    (core_copy / "_injected.py").write_text('acme_rule = "widget posture"\n')
    denylist = _write_denylist(tmp_path / "denylist.json", [])

    findings = PURITY.run(core_copy, BASELINE, PYPROJECT, denylist)
    assert [f for f in findings if f.check == "tokens"] == []


def test_configured_target_module_import_is_caught(tmp_path) -> None:
    # A configured token also gates target-named module imports (e.g. "acme_web").
    core_copy = tmp_path / "factory_core"
    shutil.copytree(CORE, core_copy)
    (core_copy / "_injected.py").write_text("import acme_web  # target module\n")
    denylist = _write_denylist(tmp_path / "denylist.json", ["acme"])

    findings = PURITY.run(core_copy, BASELINE, PYPROJECT, denylist)
    assert any(f.check == "imports" and "target import" in f.detail for f in findings)


def test_missing_denylist_defaults_to_empty(tmp_path) -> None:
    # A missing denylist file yields the empty (generic) token set, not a hard failure — the
    # other two checks remain fully enforced.
    assert PURITY.load_denylist_tokens(tmp_path / "nope.json") == ()


def test_missing_baseline_fails_closed(tmp_path) -> None:
    findings = PURITY.run(CORE, tmp_path / "nope.json", PYPROJECT, DENYLIST)
    assert any(f.check == "baseline" for f in findings)


def test_main_returns_zero_on_clean_core() -> None:
    code = PURITY.main([
        "--root", str(CORE),
        "--baseline", str(BASELINE),
        "--denylist", str(DENYLIST),
        "--pyproject", str(PYPROJECT),
        "--quiet",
    ])
    assert code == 0
