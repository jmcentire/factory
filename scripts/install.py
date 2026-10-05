#!/usr/bin/env python3
"""install — put this checkout to use: the ``factory`` launcher and the Claude Code commands.

``make install`` builds the venv and the pinned Tessera, then calls this to write two kinds of
thin, generated file outside the repository:

  launcher   ``$BINDIR/factory`` — runs ``.venv/bin/factory`` with ``FACTORY_TESSERA_BIN``
             defaulted to the pinned build, so no activation step and no stray ``tessera``.
  loaders    ``~/.claude/commands/{validate,engineer,test,orchestrate,build,review}.md`` — each
             tells the agent to read the canonical prompt in this checkout. The prompt bytes
             stay here; the loader only names where they are.

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
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

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


def install(layout: Layout) -> list[str]:
    lines = [_write(layout.launcher, render_launcher(layout), LAUNCHER_MARK, executable=True)]
    for name, source in COMMANDS:
        lines.append(
            _write(layout.loader(name), render_loader(layout, name, source), LOADER_MARK,
                   executable=False)
        )
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


def doctor(layout: Layout, path_env: str) -> list[Check]:
    checks = [_python_check(layout), _tessera_check(layout, path_env)]
    checks += _launcher_check(layout, path_env)
    checks += _loader_checks(layout)
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
    arguments = parser.parse_args(argv)
    layout = _layout(arguments)
    if arguments.action == "doctor":
        text, ready = report(doctor(layout, os.environ.get("PATH", "")))
        print(text)
        return 0 if ready else 1
    action = install if arguments.action == "install" else uninstall
    for line in action(layout):
        print(f"{arguments.action}: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
