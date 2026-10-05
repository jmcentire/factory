"""Forcing tests for scripts/install.py: it writes what a working install needs, never touches a
file it did not write, and `doctor` reports ready only when every piece is in place."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "install.py"

spec = importlib.util.spec_from_file_location("factory_install", SCRIPT)
assert spec is not None and spec.loader is not None
inst = importlib.util.module_from_spec(spec)
sys.modules["factory_install"] = inst
spec.loader.exec_module(inst)


def _layout(tmp_path: Path) -> inst.Layout:
    """A complete fake checkout environment: venv python, synced stamp, executable Tessera."""

    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").symlink_to(sys.executable)
    (venv / ".factory-deps").touch()
    os.utime(venv / ".factory-deps", (2**31, 2**31))  # newer than any pyproject.toml
    tessera = tmp_path / "tessera"
    tessera.write_text("#!/bin/sh\n", encoding="utf-8")
    tessera.chmod(0o755)
    return inst.Layout(root=REPO, venv=venv, tessera=tessera, bindir=tmp_path / "bin",
                       commands_dir=tmp_path / "commands")


def _path_env(layout: inst.Layout, tmp_path: Path) -> str:
    claude_dir = tmp_path / "claude-bin"
    claude_dir.mkdir(exist_ok=True)
    (claude_dir / "claude").write_text("#!/bin/sh\n", encoding="utf-8")
    (claude_dir / "claude").chmod(0o755)
    return os.pathsep.join([str(layout.bindir), str(claude_dir)])


def _failing(checks: list[inst.Check]) -> set[str]:
    return {c.name for c in checks if c.status == "fail"}


def test_install_then_doctor_is_ready(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    inst.install(layout)
    checks = inst.doctor(layout, _path_env(layout, tmp_path))
    assert _failing(checks) == set(), checks
    assert inst.report(checks)[1] is True
    for name, source in inst.COMMANDS:
        assert str(REPO / source) in layout.loader(name).read_text(encoding="utf-8")


def test_launcher_runs_the_venv_cli_with_the_pinned_tessera(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    (layout.venv / "bin" / "factory").write_text(
        '#!/bin/sh\necho "$FACTORY_TESSERA_BIN $*"\n', encoding="utf-8"
    )
    (layout.venv / "bin" / "factory").chmod(0o755)
    inst.install(layout)
    env = {k: v for k, v in os.environ.items() if k != "FACTORY_TESSERA_BIN"}
    out = subprocess.run([str(layout.launcher), "status"], capture_output=True, text=True,
                         env=env, check=True).stdout
    assert out.strip() == f"{layout.tessera} status"
    env["FACTORY_TESSERA_BIN"] = "/explicit/tessera"
    out = subprocess.run([str(layout.launcher)], capture_output=True, text=True,
                         env=env, check=True).stdout
    assert out.strip() == "/explicit/tessera"


def test_install_is_idempotent(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    inst.install(layout)
    assert all(line.startswith("same") for line in inst.install(layout))


def test_existing_and_edited_files_are_never_overwritten_or_removed(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    layout.commands_dir.mkdir(parents=True)
    hand_written = "# /validate — mine\n\nRead somewhere else.\n"
    layout.loader("validate").write_text(hand_written, encoding="utf-8")
    inst.install(layout)
    assert layout.loader("validate").read_text(encoding="utf-8") == hand_written

    edited = layout.loader("test").read_text(encoding="utf-8") + "- my auth binding\n"
    layout.loader("test").write_text(edited, encoding="utf-8")
    inst.install(layout)
    assert layout.loader("test").read_text(encoding="utf-8") == edited

    inst.uninstall(layout)
    assert layout.loader("validate").read_text(encoding="utf-8") == hand_written
    assert layout.loader("test").read_text(encoding="utf-8") == edited
    assert not layout.loader("engineer").exists()
    assert not layout.launcher.exists()


def test_doctor_names_each_missing_piece(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    layout.tessera.unlink()
    checks = inst.doctor(layout, _path_env(layout, tmp_path))
    assert {"tessera", "launcher", "/validate", "/review"} <= _failing(checks)
    text, ready = inst.report(checks)
    assert not ready and "make install" in text


def test_doctor_fails_when_launcher_is_off_path_or_shadowed(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    inst.install(layout)
    claude_only = _path_env(layout, tmp_path).split(os.pathsep, 1)[1]
    off_path = inst.doctor(layout, claude_only)
    assert "PATH" in _failing(off_path)
    assert f'export PATH="{layout.bindir}:$PATH"' in inst.report(off_path)[0]

    stale = tmp_path / "stale-bin"
    stale.mkdir()
    (stale / "factory").write_text("#!/bin/sh\n", encoding="utf-8")
    (stale / "factory").chmod(0o755)
    shadowed = inst.doctor(layout, os.pathsep.join([str(stale), _path_env(layout, tmp_path)]))
    assert "PATH" in _failing(shadowed)


def test_doctor_flags_a_loader_from_another_checkout(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    inst.install(layout)
    elsewhere = inst.Layout(root=tmp_path / "other", venv=layout.venv, tessera=layout.tessera,
                            bindir=layout.bindir, commands_dir=layout.commands_dir)
    (elsewhere.root / "prompts").mkdir(parents=True)
    (elsewhere.root / "prompts" / "build.md").write_text("# /build — x\n", encoding="utf-8")
    layout.loader("build").write_text(
        inst.render_loader(elsewhere, "build", "prompts/build.md"), encoding="utf-8"
    )
    assert "/build" in _failing(inst.doctor(layout, _path_env(layout, tmp_path)))
    inst.install(layout)  # ours, so install repoints it
    assert "/build" not in _failing(inst.doctor(layout, _path_env(layout, tmp_path)))
