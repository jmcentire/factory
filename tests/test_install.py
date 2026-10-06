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
                       commands_dir=tmp_path / "commands", profiles=tmp_path / "profiles.json")


def _with_profile(layout: inst.Layout, **roles: str) -> None:
    """Give the layout a default model profile; any role not named runs codex:gpt-test."""

    mp = inst.model_profiles(REPO)
    bindings = {role: roles.get(role, "codex:gpt-test") for role in mp.ROLES}
    profile = mp.Profile("local", "", {r: mp.parse_binding(r, b) for r, b in bindings.items()})
    mp.save(layout.profiles, mp.Profiles("local", {"local": profile}))


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
    _with_profile(layout)
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
    _with_profile(layout)
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
                            bindir=layout.bindir, commands_dir=layout.commands_dir,
                            profiles=layout.profiles)
    (elsewhere.root / "prompts").mkdir(parents=True)
    (elsewhere.root / "prompts" / "build.md").write_text("# /build — x\n", encoding="utf-8")
    layout.loader("build").write_text(
        inst.render_loader(elsewhere, "build", "prompts/build.md"), encoding="utf-8"
    )
    assert "/build" in _failing(inst.doctor(layout, _path_env(layout, tmp_path)))
    inst.install(layout)  # ours, so install repoints it
    assert "/build" not in _failing(inst.doctor(layout, _path_env(layout, tmp_path)))


def test_make_doctor_judges_the_callers_path_not_makes(tmp_path: Path) -> None:
    """make puts the venv's bin first on PATH for its own recipes. The doctor must still check
    what `factory` resolves to in the operator's shell, or every install reports a false PATH
    failure (the venv's own `factory` would always come first)."""

    layout = _layout(tmp_path)
    inst.install(layout)
    env = {**os.environ, "PATH": f"{layout.bindir}{os.pathsep}{os.environ['PATH']}"}
    env.pop("CI", None)  # exercise the venv-managed branch that rewrites PATH
    done = subprocess.run(
        ["make", "--no-print-directory", "doctor", f"BINDIR={layout.bindir}",
         f"CLAUDE_COMMANDS_DIR={layout.commands_dir}"],
        cwd=REPO, env=env, capture_output=True, text=True, check=False,
    )
    path_line = next(line for line in done.stdout.splitlines() if "PATH" in line.split()[:2])
    assert path_line.split()[0] == "ok", done.stdout


def test_doctor_is_not_ready_without_a_model_profile(tmp_path: Path) -> None:
    """The factory never picks a model, so an install with no profile is not a working install."""

    layout = _layout(tmp_path)
    inst.install(layout)
    checks = inst.doctor(layout, _path_env(layout, tmp_path))
    assert _failing(checks) == {"profile"}, checks
    text, ready = inst.report(checks)
    assert not ready and "factory profile create" in text
    layout.profiles.write_text("{not json", encoding="utf-8")
    assert _failing(inst.doctor(layout, _path_env(layout, tmp_path))) == {"profile"}


def test_install_asks_for_a_profile_in_a_terminal_and_never_suggests_one(tmp_path: Path) -> None:
    """With no profile, an interactive install asks for every role's agent and model, asks again
    on an answer the harness could not launch, and saves what was named as the default."""

    layout = _layout(tmp_path)
    answers = iter([
        "local",
        "agy", "claude", "opus-test",            # agy cannot be the Validator: asked again
        "codex", "",                             # an empty model is refused: asked again
        "codex", "gpt-orch",
        "codex-ollama", "glm-test:cloud",
        "codex", "gpt-tester",
    ])
    said: list[str] = []
    line = inst.ensure_profile(layout, interactive=True, ask=lambda _: next(answers),
                               say=said.append)
    assert line.startswith("wrote") and "'local'" in line
    assert any("choose one of" in text for text in said)
    mp = inst.model_profiles(REPO)
    saved = mp.load(layout.profiles)
    assert saved.default == "local"
    assert {r: (b.agent, b.model) for r, b in saved.profiles["local"].roles.items()} == {
        "validator": ("claude", "opus-test"),
        "orchestrator": ("codex", "gpt-orch"),
        "coder": ("codex-ollama", "glm-test:cloud"),
        "tester": ("codex", "gpt-tester"),
    }
    assert oct(layout.profiles.stat().st_mode & 0o777) == "0o600"
    # A second install leaves the operator's profiles alone.
    assert inst.ensure_profile(layout, interactive=True).startswith("same")


def test_install_without_a_terminal_names_the_command_instead_of_guessing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    lines = inst.install(layout, interactive=False)
    assert any("factory profile create" in line for line in lines)
    assert not layout.profiles.exists()


def test_doctor_warns_when_a_profiles_ollama_model_is_absent(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    inst.install(layout)
    _with_profile(layout, coder="codex-ollama:glm-test:cloud")
    tools = tmp_path / "tools"
    tools.mkdir()
    for tool, body in (("codex", "exit 0"), ("ollama", 'exit 1')):
        (tools / tool).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        (tools / tool).chmod(0o755)
    checks = inst.doctor(layout, os.pathsep.join([_path_env(layout, tmp_path), str(tools)]))
    coder = next(c for c in checks if c.name == "coder")
    assert coder.status == "warn" and "ollama pull glm-test:cloud" in coder.detail
    assert _failing(checks) == set(), checks
