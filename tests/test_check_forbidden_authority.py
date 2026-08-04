"""Forbidden-authority guard tests — GREEN on the clean repo, RED when a banned seam appears.

Two upstream implementations are banned by capability, not by preference, and the build plan
says to enforce the ban with an import guard rather than a review convention:

* exemplar's ``TesseraSeal`` has no signature field of any kind, so it cannot verify a founder
  signature against a fingerprint. Anything that anchors authority must use the real
  ``jmcentire/tessera`` engine instead.
* ``signet-sdk``'s ``check_authority`` hashes a binding and then grants unconditionally
  (``is_authority_granted`` returns true for any non-zero digest), so it *looks* like a decision
  at the call site. That is worse than an honest stub.

These tests are what make the ban mechanical.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD_PATH = REPO_ROOT / "scripts" / "check_forbidden_authority.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("check_forbidden_authority", GUARD_PATH)
    assert spec and spec.loader, f"the forbidden-authority guard must exist at {GUARD_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass decorator resolves the module via sys.modules
    spec.loader.exec_module(module)
    return module


GUARD = _load_guard()


def _package(root: Path, name: str, source: str) -> Path:
    pkg = root / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "candidate.py").write_text(source, encoding="utf-8")
    return pkg


def test_the_real_packages_are_green() -> None:
    findings = GUARD.run([REPO_ROOT / "factory_core", REPO_ROOT / "factory_runtime"])
    assert findings == [], "the shipped packages must reference no banned authority seam:\n" + (
        "\n".join(str(f) for f in findings)
    )


def test_exemplar_tessera_seal_import_is_refused(tmp_path: Path) -> None:
    pkg = _package(
        tmp_path,
        "candidate_pkg",
        "from exemplar.src.schemas.schemas import TesseraSeal\n\nseal = TesseraSeal\n",
    )
    findings = GUARD.run([pkg])
    assert findings, "importing exemplar's TesseraSeal must fail closed"
    assert any("TesseraSeal" in str(f) for f in findings)


def test_signet_sdk_check_authority_call_is_refused(tmp_path: Path) -> None:
    pkg = _package(
        tmp_path,
        "candidate_pkg",
        "def gate(signet_id, authority):\n    return check_authority(signet_id, authority)\n",
    )
    findings = GUARD.run([pkg])
    assert findings, "referencing signet-sdk's check_authority must fail closed"
    assert any("check_authority" in str(f) for f in findings)


def test_signet_sdk_module_import_is_refused(tmp_path: Path) -> None:
    pkg = _package(tmp_path, "candidate_pkg", "import signet_sdk\n")
    findings = GUARD.run([pkg])
    assert findings, "importing signet_sdk must fail closed"


def test_a_comment_naming_a_banned_symbol_is_not_a_violation(tmp_path: Path) -> None:
    # The guard is an AST scan, not a grep: doctrine documents and code comments must be able to
    # NAME what is banned without tripping the ban. Otherwise the guard cannot be explained in
    # the file it guards.
    pkg = _package(
        tmp_path,
        "candidate_pkg",
        "# We deliberately do not use TesseraSeal or check_authority here.\nvalue = 1\n",
    )
    assert GUARD.run([pkg]) == []


def test_the_real_tessera_seam_is_not_caught(tmp_path: Path) -> None:
    # The ban is on exemplar's TesseraSeal specifically, NOT on Tessera. A guard that refused
    # every tessera reference would ban the engine the doctrine requires.
    pkg = _package(
        tmp_path,
        "candidate_pkg",
        "from factory_runtime.tessera import TesseraCli\n\nclient = TesseraCli\n",
    )
    assert GUARD.run([pkg]) == []


def test_a_missing_package_fails_closed(tmp_path: Path) -> None:
    findings = GUARD.run([tmp_path / "does_not_exist"])
    assert findings, "a missing scan root must fail closed rather than report green"


def test_an_undecodable_file_is_a_finding_not_a_crash(tmp_path: Path) -> None:
    # A guard that raises instead of reporting turns "could not check this file" into a broken
    # build with no location. Fail closed with a finding.
    pkg = tmp_path / "candidate_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "candidate.py").write_bytes(b"\xff\xfe invalid utf-8 \x00")

    findings = GUARD.run([pkg])
    assert findings, "an unreadable module must produce a finding"
    assert any(f.kind == "unreadable" for f in findings)
