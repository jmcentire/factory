#!/usr/bin/env python3
"""install — put this checkout to use: the ``factory`` launcher and the Claude Code commands.

``make install`` builds the venv and the pinned Tessera, then calls this to write two kinds of
thin, generated file outside the repository:

  launcher   ``$BINDIR/factory`` — runs ``.venv/bin/factory`` with ``FACTORY_TESSERA_BIN``
             defaulted to the pinned build, so no activation step and no stray ``tessera``.
  loaders    ``~/.claude/commands/{validate,engineer,test,orchestrate,build,review}.md`` — each
             tells the agent to read the canonical prompt in this checkout. The prompt bytes
             stay here; the loader only names where they are.
  profile    the operator's first model profile, asked for in a terminal when none exists
             (``factory_runtime/model_profiles.py``): which agent and model run each role.
             Without one, launches use each agent's own default model; doctor says so.

The invariant: the installer never overwrites or removes a file it cannot prove it wrote.
Every generated file ends with a marker carrying the SHA-256 of the rest of its content. A file
whose marker still matches is ours and unedited, so it is safe to refresh or remove; anything
else — no marker, or a loader the operator has since edited to add machine-local bindings — is
kept and reported. ``doctor`` is the read-only counterpart: it checks every piece a working
install needs and prints the exact fix for each one that is missing. Exit 0 means ready.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
PY_FLOOR = (3, 12)

# (command name, canonical source relative to the checkout). The loader's title is the
# source's own first line, so a renamed lane never needs an edit here.
COMMANDS: tuple[tuple[str, str], ...] = (
    ("validate", "prompts/validate.md"),
    ("engineer", "prompts/engineer.md"),
    ("test", "prompts/test.md"),
    ("orchestrate", "prompts/orchestrate.md"),
    ("build", "prompts/build.md"),
    ("review", "skills/review.md"),
)

LOADER_MARK = re.compile(r"^<!-- factory-loader/1 sha256=([0-9a-f]{64}) -->\n\Z", re.M)
LAUNCHER_MARK = re.compile(r"^# factory-launcher/1 sha256=([0-9a-f]{64})\n\Z", re.M)


@dataclass(frozen=True)
class Layout:
    """Where a checkout installs to. Every path is explicit so tests never touch $HOME."""

    root: Path
    venv: Path
    tessera: Path
    bindir: Path
    commands_dir: Path
    profiles: Path

    @property
    def launcher(self) -> Path:
        return self.bindir / "factory"

    def loader(self, name: str) -> Path:
        return self.commands_dir / f"{name}.md"


def _digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def render_loader(layout: Layout, name: str, source: str) -> str:
    canonical = layout.root / source
    title = canonical.read_text(encoding="utf-8").splitlines()[0]
    body = (
        f"{title}\n\n"
        f"Read `{canonical}` and follow it as this command's complete instructions, with\n"
        f"`$FACTORY_HOME` = `{layout.root}`. That file is the canonical source; this file is a\n"
        "thin loader so canonical edits propagate without fan-out.\n\n"
        "Written by `make install` in that checkout. Add machine-local bindings (auth, target\n"
        "labels) below; once this file is edited, reinstalling keeps it as it is.\n\n"
    )
    return f"{body}<!-- factory-loader/1 sha256={_digest(body)} -->\n"


def render_launcher(layout: Layout) -> str:
    tessera = shlex.quote(str(layout.tessera))
    body = (
        "#!/bin/sh\n"
        f"# factory launcher for the checkout at {layout.root} (written by `make install`).\n"
        "# Uses the pinned Tessera build unless FACTORY_TESSERA_BIN is already set.\n"
        f'if [ -z "${{FACTORY_TESSERA_BIN:-}}" ] && [ -x {tessera} ]; then\n'
        f"  FACTORY_TESSERA_BIN={tessera}; export FACTORY_TESSERA_BIN\n"
        "fi\n"
        f'exec {shlex.quote(str(layout.venv / "bin" / "factory"))} "$@"\n'
    )
    return f"{body}# factory-launcher/1 sha256={_digest(body)}\n"


def _ownership(path: Path, mark: re.Pattern[str]) -> str:
    """``absent``, ``ours`` (marker present and content unedited), or ``foreign``."""

    if not path.exists() and not path.is_symlink():
        return "absent"
    if path.is_symlink() or not path.is_file():
        return "foreign"
    text = path.read_text(encoding="utf-8", errors="replace")
    match = mark.search(text)
    if match is None:
        return "foreign"
    return "ours" if _digest(text[: match.start()]) == match.group(1) else "foreign"


def _write(path: Path, content: str, mark: re.Pattern[str], *, executable: bool) -> str:
    state = _ownership(path, mark)
    if state == "foreign":
        return f"kept    {path} (not written by this installer, or edited since)"
    if state == "ours" and path.read_text(encoding="utf-8") == content:
        return f"same    {path}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return f"{'wrote' if state == 'absent' else 'updated'} {path}"


def model_profiles(root: Path) -> ModuleType:
    """The checkout's model_profiles module, loaded by path: the doctor may run on the bootstrap
    interpreter before the venv exists, and the module is stdlib-only for exactly that."""

    name = "factory_install_model_profiles"
    spec = importlib.util.spec_from_file_location(
        name, root / "factory_runtime" / "model_profiles.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load model_profiles from {root}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


def ensure_profile(
    layout: Layout,
    *,
    interactive: bool,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
) -> str:
    """Leave existing profiles alone; with none, ask for one in a terminal, else say how."""

    profiles_module = model_profiles(layout.root)
    existing = profiles_module.load(layout.profiles)
    if existing.profiles:
        return f"same    model profiles in {layout.profiles} (default: {existing.default})"
    if not interactive:
        return (f"skipped model profile: none in {layout.profiles}; launches use each agent's "
                "own default model until you create one with `factory profile create`")
    say("")
    say("No model profile yet. The factory never picks a model, so name the agent and the")
    say("model for each role. Add more profiles later with `factory profile create`.")
    profile = profiles_module.prompt_profile(ask, say)
    profiles_module.save(
        layout.profiles, profiles_module.Profiles(profile.name, {profile.name: profile})
    )
    return f"wrote   {layout.profiles} (model profile {profile.name!r}, the default)"


def install(layout: Layout, *, interactive: bool = False) -> list[str]:
    lines = [_write(layout.launcher, render_launcher(layout), LAUNCHER_MARK, executable=True)]
    for name, source in COMMANDS:
        lines.append(
            _write(layout.loader(name), render_loader(layout, name, source), LOADER_MARK,
                   executable=False)
        )
    lines.append(ensure_profile(layout, interactive=interactive))
    return lines


def uninstall(layout: Layout) -> list[str]:
    targets = [(layout.launcher, LAUNCHER_MARK)]
    targets += [(layout.loader(name), LOADER_MARK) for name, _ in COMMANDS]
    lines = []
    for path, mark in targets:
        state = _ownership(path, mark)
        if state == "ours":
            path.unlink()
            lines.append(f"removed {path}")
        elif state == "foreign":
            lines.append(f"kept    {path} (not written by this installer, or edited since)")
    return lines


@dataclass(frozen=True)
class Check:
    status: str  # "ok" | "warn" | "fail"
    name: str
    detail: str
    fix: str = ""


def _python_check(layout: Layout) -> Check:
    python = layout.venv / "bin" / "python"
    if not python.exists():
        return Check("fail", "python", f"no virtualenv at {layout.venv}", "make install")
    probe = subprocess.run(
        [str(python), "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
        capture_output=True, text=True, check=False,
    )
    version = probe.stdout.strip()
    parts = tuple(int(p) for p in version.split(".")[:2]) if probe.returncode == 0 else ()
    if parts < PY_FLOOR:
        return Check(
            "fail", "python", f"{python} is {version or 'not runnable'}",
            f"install Python {PY_FLOOR[0]}.{PY_FLOOR[1]}+, then: make clean-venv install",
        )
    stamp = layout.venv / ".factory-deps"
    pyproject = layout.root / "pyproject.toml"
    if not stamp.exists() or stamp.stat().st_mtime < pyproject.stat().st_mtime:
        return Check("fail", "python", "virtualenv is behind pyproject.toml", "make install")
    return Check("ok", "python", f"{version} in {layout.venv}")


def _tessera_check(layout: Layout, path_env: str) -> Check:
    if os.access(layout.tessera, os.X_OK):
        return Check("ok", "tessera", f"pinned build at {layout.tessera}")
    if shutil.which("cargo", path=path_env) is None:
        fix = "install Rust from https://rustup.rs, then: make install"
    else:
        fix = "make install"
    return Check("fail", "tessera", "pinned build missing (needed to sign anything)", fix)


def _launcher_check(layout: Layout, path_env: str) -> list[Check]:
    state = _ownership(layout.launcher, LAUNCHER_MARK)
    if state == "absent":
        return [Check("fail", "launcher", f"{layout.launcher} missing", "make install")]
    checks = []
    if state == "foreign":
        checks.append(Check("warn", "launcher", f"{layout.launcher} exists but was not written "
                            "by this installer; left alone"))
    elif layout.launcher.read_text(encoding="utf-8") != render_launcher(layout):
        checks.append(Check("fail", "launcher", f"{layout.launcher} targets another checkout",
                            "make install"))
    else:
        checks.append(Check("ok", "launcher", str(layout.launcher)))
    found = shutil.which("factory", path=path_env)
    if found is None:
        checks.append(Check(
            "fail", "PATH", f"{layout.bindir} is not on PATH, so `factory` is not found",
            f'add to your shell profile: export PATH="{layout.bindir}:$PATH"',
        ))
    elif Path(found).resolve() != layout.launcher.resolve():
        checks.append(Check(
            "fail", "PATH", f"`factory` runs {found}, not this checkout's launcher",
            f"remove the other install ({found}), or put {layout.bindir} earlier on PATH",
        ))
    else:
        checks.append(Check("ok", "PATH", f"`factory` -> {found}"))
    return checks


def _loader_checks(layout: Layout) -> list[Check]:
    checks = []
    for name, source in COMMANDS:
        path = layout.loader(name)
        state = _ownership(path, LOADER_MARK)
        if state == "absent":
            checks.append(Check("fail", f"/{name}", f"{path} missing", "make install"))
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        canonical = str(layout.root / source)
        if state == "ours" and text == render_loader(layout, name, source):
            checks.append(Check("ok", f"/{name}", str(path)))
        elif state == "ours":
            checks.append(Check("fail", f"/{name}", f"{path} is stale or names another "
                                "checkout", "make install"))
        elif canonical in text:
            checks.append(Check("ok", f"/{name}", f"{path} (edited locally; reads {canonical})"))
        else:
            checks.append(Check("warn", f"/{name}", f"{path} was not written by this installer "
                                f"and does not read {canonical}"))
    return checks


def _kindex_check(path_env: str) -> Check:
    """Coder and Tester lanes refuse to launch without kindex-lite >= 0.48.0 (lane_kindex.py)."""

    fix = "pip install 'kindex[mcp,kinbase]>=0.48.1' (reinstall if kindex-lite is missing)"
    lite = shutil.which("kindex-lite", path=path_env)
    if lite is None:
        return Check("warn", "kindex", "kindex-lite not found; Coder and Tester lanes refuse to "
                     f"launch without it. Fix: {fix}")
    kin = Path(lite).resolve().parent / "kin"
    done = subprocess.run([str(kin), "--version"], capture_output=True, text=True, check=False)
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", done.stdout)
    if done.returncode != 0 or match is None or tuple(map(int, match.groups())) < (0, 48, 0):
        return Check("warn", "kindex", f"Kindex beside {lite} is older than 0.48.0 or unreadable; "
                     f"lanes will refuse to launch. Fix: {fix}")
    return Check("ok", "kindex", f"kindex-lite {match.group(0)} at {lite}")


def _profile_checks(layout: Layout, path_env: str, ollama_timeout: float) -> list[Check]:
    """Report what a launch would do, never a stricter standard of its own. With no profile a
    launch works on each agent's own default model, so that is a warning. A malformed file or no
    default makes factory.sh refuse, so those fail. A default profile's agents and Ollama models
    are warnings, because a launch refuses them anyway and they may come after the profile."""

    profiles_module = model_profiles(layout.root)
    create = "factory profile create  (or rerun `make install` in a terminal to be asked)"
    try:
        profiles = profiles_module.load(layout.profiles)
    except profiles_module.ProfileError as error:
        return [Check("fail", "profile", str(error), f"fix {layout.profiles}, or move it aside "
                      f"and: {create}")]
    if not profiles.profiles:
        return [Check("warn", "profile", f"no model profile in {layout.profiles}; launches use "
                      f"each agent's own default model. Create one: {create}", create)]
    if profiles.default is None:
        return [Check("fail", "profile", f"{layout.profiles} has no default profile",
                      "factory profile use <name>")]
    profile = profiles.profiles[profiles.default]
    checks = [Check("ok", "profile", f"{profile.name} (default) in {layout.profiles}")]
    for role in profiles_module.ROLES:
        bound = profile.roles[role]
        needed = {"codex-ollama": ("codex", "ollama"), "ollama": ("ollama", "codex")}.get(
            bound.agent, (bound.agent,)
        )
        missing = [tool for tool in needed if shutil.which(tool, path=path_env) is None]
        if missing:
            checks.append(Check("warn", role, f"{bound.agent}:{bound.model} needs "
                                f"{', '.join(missing)}, not found on PATH"))
            continue
        if bound.agent in profiles_module.OLLAMA_AGENTS:
            # A read-only diagnostic always answers; "could not find out" is an answer.
            try:
                shown = subprocess.run(
                    [shutil.which("ollama", path=path_env) or "ollama", "show", bound.model],
                    capture_output=True, text=True, check=False, timeout=ollama_timeout,
                )
            except subprocess.TimeoutExpired:
                checks.append(Check("warn", role, f"could not ask Ollama about {bound.model} "
                                    f"(timed out after {ollama_timeout:g}s); check that Ollama "
                                    "is running"))
                continue
            except OSError as error:
                checks.append(Check("warn", role, f"could not ask Ollama about {bound.model} "
                                    f"({error})"))
                continue
            if shown.returncode != 0:
                checks.append(Check("warn", role, f"Ollama model {bound.model} is not on this "
                                    "machine (or Ollama is not running); the factory never "
                                    f"downloads one. Fix: ollama pull {bound.model}"))
                continue
        checks.append(Check("ok", role, f"{bound.agent}:{bound.model}"))
    return checks


def doctor(layout: Layout, path_env: str, *, ollama_timeout: float = 30.0) -> list[Check]:
    checks = [_python_check(layout), _tessera_check(layout, path_env)]
    checks += _launcher_check(layout, path_env)
    checks += _loader_checks(layout)
    checks += _profile_checks(layout, path_env, ollama_timeout)
    checks.append(_kindex_check(path_env))
    if shutil.which("claude", path=path_env) is None:
        checks.append(Check("warn", "claude", "Claude Code CLI not found; the /commands need it "
                            "(https://claude.com/claude-code)"))
    else:
        checks.append(Check("ok", "claude", "Claude Code CLI found"))
    return checks


def report(checks: list[Check]) -> tuple[str, bool]:
    width = max(len(c.name) for c in checks)
    lines = [f"  {c.status:<4}  {c.name:<{width}}  {c.detail}" for c in checks]
    failures = [c for c in checks if c.status == "fail"]
    if not failures:
        lines += [
            "",
            "doctor: READY.",
            "Next: open Claude Code in the repository you want to work on and type /build for",
            "small work, or /validate to start a full factory run. `factory --help` lists the CLI.",
        ]
        return "\n".join(lines), True
    fixes = list(dict.fromkeys(c.fix for c in failures if c.fix))
    lines += ["", f"doctor: NOT READY ({len(failures)} problem(s)). Fix, then rerun `make doctor`:"]
    lines += [f"  - {fix}" for fix in fixes]
    return "\n".join(lines), False


def _layout(arguments: argparse.Namespace) -> Layout:
    root = Path(arguments.root).resolve()
    return Layout(
        root=root,
        venv=Path(arguments.venv).expanduser().resolve() if arguments.venv else root / ".venv",
        tessera=(Path(arguments.tessera).expanduser().resolve() if arguments.tessera
                 else root / ".tools/tessera/target/release/tessera"),
        bindir=Path(arguments.bindir).expanduser(),
        commands_dir=Path(arguments.commands_dir).expanduser(),
        profiles=(Path(arguments.profiles).expanduser() if arguments.profiles
                  else model_profiles(root).default_path()),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("install", "uninstall", "doctor"))
    parser.add_argument("--root", default=str(ROOT), help="the factory checkout")
    parser.add_argument("--venv", default="", help="virtualenv (default: <root>/.venv)")
    parser.add_argument("--tessera", default="", help="pinned Tessera binary")
    parser.add_argument("--bindir", default="~/.local/bin", help="where the launcher goes")
    parser.add_argument("--commands-dir", default="~/.claude/commands",
                        help="Claude Code user commands directory")
    parser.add_argument("--profiles", default="",
                        help="model profiles file (default: $FACTORY_PROFILES, else "
                        "${XDG_CONFIG_HOME:-~/.config}/factory/profiles.json)")
    arguments = parser.parse_args(argv)
    layout = _layout(arguments)
    if arguments.action == "doctor":
        text, ready = report(doctor(layout, os.environ.get("PATH", "")))
        print(text)
        return 0 if ready else 1
    if arguments.action == "install":
        lines = install(layout, interactive=sys.stdin.isatty() and sys.stdout.isatty())
    else:
        lines = uninstall(layout)
    for line in lines:
        print(f"{arguments.action}: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
