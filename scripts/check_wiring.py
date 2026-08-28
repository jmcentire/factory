#!/usr/bin/env python3
"""check_wiring — the executable dead-wiring audit (fail-closed, baseline-backed).

A module or public symbol that no entrypoint can reach is inert mass: it ships, it rots,
and — worse — it can *look* like an enforced control while nothing ever calls it. In a
factory whose guarantees are mechanisms, an unwired mechanism is a defect, not a spare
part. Green means every provided service is provably wired: reachable from a real
entrypoint over static imports and attribute references.

The invariant: every public (non-underscore) module-level class and function in
``factory_core/`` and ``factory_runtime/`` is referenced on some path that starts at an
entrypoint (``factory_runtime/cli.py``, ``scripts/*.py``, ``harness/*.py``), and every
module in those two packages is imported on such a path. Tests are deliberately NOT
entrypoints: a symbol referenced only from ``tests/`` is dead wiring and is reported
(``tests/`` is never scanned as a reference source).

Finding classes (exact strings; one finding per line on stdout, format
``<class>:<repo-relative-path>:<symbol-or-dash>``, sorted, de-duplicated):

  zero-caller-export   public symbol with no reference reachable from any entrypoint
                       (references from its own defining module do not count)
  unreachable-module   package module never imported on any entrypoint-reachable path
  unresolved-reference a reference static analysis cannot resolve — ``getattr`` with a
                       dynamic name, ``__import__``/``import_module`` with a variable,
                       ``import *`` from a scanned package; undecidable is never silent
  parse-failure        a file ``ast.parse`` cannot read — the audit goes red, never skips

Exit codes: 0 = no findings beyond the baseline; 1 = at least one non-baselined finding;
2 = internal/usage error (fail closed). The baseline (``wiring_baseline.json``, a JSON
array of ``{"finding", "justification"}`` objects) suppresses exact-match lines only; a
missing baseline file is an empty baseline. False reds are preferred to false greens
everywhere: anything the analysis cannot decide is reported, not skipped.

Stdlib only, so the guard itself has no third-party surface to subvert.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# The two packages whose provided services (public module-level classes/functions) are audited.
PACKAGES = ("factory_core", "factory_runtime")

# Directories whose top-level *.py files are entrypoints, plus the CLI module below.
ENTRYPOINT_DIRS = ("scripts", "harness")
CLI_ENTRYPOINT = ("factory_runtime", "cli.py")


class WiringError(Exception):
    """A usage or environment error: the audit cannot run, so it must not report green."""


@dataclass
class ModuleInfo:
    """One scanned source file: a package module or an entrypoint script."""

    name: str  # dotted module name for package modules; repo-relative path for scripts
    rel_path: str  # repo-relative POSIX path (the path used in finding lines)
    is_package: bool  # True for an __init__.py
    in_packages: bool  # True when the file lives inside one of PACKAGES
    public_symbols: tuple[str, ...] = ()
    edges: set[str] = field(default_factory=set)  # imported known-module names
    refs: set[tuple[str, str]] = field(default_factory=set)  # (module, symbol) references


# ---------------------------------------------------------------------------- #
# Discovery and parsing
# ---------------------------------------------------------------------------- #

def discover_and_parse(
    root: Path, findings: set[str]
) -> tuple[dict[str, ModuleInfo], list[str], dict[str, ast.Module]]:
    """Enumerate package modules and entrypoints; parse each file exactly once.

    A file that fails ``ast.parse`` becomes a ``parse-failure`` finding and contributes no
    edges or symbols — the audit goes red rather than silently narrowing its own scope.
    """
    modules: dict[str, ModuleInfo] = {}
    entrypoints: list[str] = []
    paths: dict[str, Path] = {}
    for pkg in PACKAGES:
        pkg_dir = root / pkg
        if not pkg_dir.is_dir():
            raise WiringError(f"package directory not found: {pkg_dir} (fail closed)")
        for path in sorted(pkg_dir.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel_parts = path.relative_to(root).parts
            is_init = rel_parts[-1] == "__init__.py"
            dotted = ".".join(rel_parts[:-1]) if is_init else ".".join(rel_parts)[: -len(".py")]
            modules[dotted] = ModuleInfo(
                name=dotted,
                rel_path=path.relative_to(root).as_posix(),
                is_package=is_init,
                in_packages=True,
            )
            paths[dotted] = path
    cli_path = root.joinpath(*CLI_ENTRYPOINT)
    cli_name = ".".join(CLI_ENTRYPOINT)[: -len(".py")]
    if cli_path.is_file() and cli_name in modules:
        entrypoints.append(cli_name)
    for sub in ENTRYPOINT_DIRS:
        for path in sorted((root / sub).glob("*.py")):
            rel = path.relative_to(root).as_posix()
            modules[rel] = ModuleInfo(name=rel, rel_path=rel, is_package=False, in_packages=False)
            paths[rel] = path
            entrypoints.append(rel)
    trees: dict[str, ast.Module] = {}
    for name, path in paths.items():
        try:
            trees[name] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, ValueError):
            findings.add(f"parse-failure:{modules[name].rel_path}:-")
    return modules, entrypoints, trees


# ---------------------------------------------------------------------------- #
# Per-module static analysis: imports, attribute references, dynamic dispatch
# ---------------------------------------------------------------------------- #

def _add_edges(info: ModuleInfo, dotted: str, known: frozenset[str]) -> None:
    """Record an import edge to ``dotted`` and to every known ancestor package.

    Importing ``a.b.c`` executes ``a/__init__.py`` and ``a/b/__init__.py`` too, so each
    known prefix is reachable, not just the leaf.
    """
    parts = dotted.split(".")
    for i in range(1, len(parts) + 1):
        prefix = ".".join(parts[:i])
        if prefix in known:
            info.edges.add(prefix)


def _from_base(info: ModuleInfo, node: ast.ImportFrom) -> str | None:
    """Resolve the absolute module a ``from ... import`` names, honoring relative levels."""
    if node.level == 0:
        return node.module
    if not info.in_packages:
        return None  # a relative import outside the packages cannot name a package module
    anchor = info.name if info.is_package else info.name.rsplit(".", 1)[0]
    for _ in range(node.level - 1):
        if "." not in anchor:
            return None  # escapes the package; a runtime ImportError, not a wiring edge
        anchor = anchor.rsplit(".", 1)[0]
    return f"{anchor}.{node.module}" if node.module else anchor


def _attr_chain(node: ast.Attribute) -> tuple[str, list[str]] | None:
    """Flatten ``name.a.b`` into ``("name", ["a", "b"])``; None when the root is not a Name."""
    attrs: list[str] = []
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        attrs.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        attrs.reverse()
        return cur.id, attrs
    return None


def _expr_module(node: ast.expr, bindings: dict[str, str], known: frozenset[str]) -> str | None:
    """Resolve an expression to a known module name, or None (a Name/Attribute chain only)."""
    if isinstance(node, ast.Name):
        dotted = bindings.get(node.id)
        return dotted if dotted in known else None
    if isinstance(node, ast.Attribute):
        chain = _attr_chain(node)
        if chain is None:
            return None
        rooted = bindings.get(chain[0])
        if rooted is None:
            return None
        dotted = ".".join([rooted, *chain[1]])
        return dotted if dotted in known else None
    return None


def _str_const(node: ast.expr) -> str | None:
    """The node's string value when it is a string literal, else None (typed narrowing)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _collect_bindings(
    info: ModuleInfo, tree: ast.Module, known: frozenset[str], findings: set[str]
) -> dict[str, str]:
    """First pass: every import statement yields edges plus local-name -> module bindings."""
    bindings: dict[str, str] = {}
    unresolved_line = f"unresolved-reference:{info.rel_path}:-"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _add_edges(info, alias.name, known)
                if alias.asname:
                    bindings[alias.asname] = alias.name
                else:
                    head = alias.name.split(".", 1)[0]
                    bindings[head] = head
        elif isinstance(node, ast.ImportFrom):
            base = _from_base(info, node)
            if base is None:
                continue
            _add_edges(info, base, known)
            for alias in node.names:
                if alias.name == "*":
                    # A star import of a scanned package defeats symbol-level resolution;
                    # undecidable must be red, never a silent pass.
                    if base in known or base.split(".", 1)[0] in PACKAGES:
                        findings.add(unresolved_line)
                    continue
                full = f"{base}.{alias.name}"
                if full in known:
                    bindings[alias.asname or alias.name] = full
                    _add_edges(info, full, known)
                elif base in known:
                    info.refs.add((base, alias.name))
    return bindings


def _dynamic_call(
    node: ast.Call,
    info: ModuleInfo,
    bindings: dict[str, str],
    known: frozenset[str],
    findings: set[str],
) -> None:
    """Classify dynamic-dispatch calls: resolve what is constant, report what is not."""
    func = node.func
    if isinstance(func, ast.Name):
        callee = func.id
    elif isinstance(func, ast.Attribute):
        callee = func.attr
    else:
        return
    unresolved_line = f"unresolved-reference:{info.rel_path}:-"
    if callee == "getattr":
        if any(isinstance(a, ast.Starred) for a in node.args[:2]):
            findings.add(unresolved_line)
            return
        if len(node.args) < 2:
            return
        name_val = _str_const(node.args[1])
        if name_val is None:
            findings.add(unresolved_line)
            return
        base_mod = _expr_module(node.args[0], bindings, known)
        if base_mod is not None:
            info.refs.add((base_mod, name_val))
            _add_edges(info, base_mod, known)
    elif callee in ("__import__", "import_module"):
        arg: ast.expr | None = node.args[0] if node.args else None
        if arg is None:
            for kw in node.keywords:
                if kw.arg == "name":
                    arg = kw.value
                    break
        arg_val = None if arg is None else _str_const(arg)
        if arg_val is None:
            findings.add(unresolved_line)
        else:
            _add_edges(info, arg_val, known)


def _collect_references(
    info: ModuleInfo,
    tree: ast.Module,
    bindings: dict[str, str],
    known: frozenset[str],
    findings: set[str],
) -> None:
    """Second pass: attribute chains rooted in module bindings, plus dynamic dispatch."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            chain = _attr_chain(node)
            if chain is None:
                continue
            rooted = bindings.get(chain[0])
            if rooted is None:
                continue
            parts = rooted.split(".") + chain[1]
            for i in range(len(parts), 0, -1):
                prefix = ".".join(parts[:i])
                if prefix in known:
                    _add_edges(info, prefix, known)
                    if i < len(parts):
                        info.refs.add((prefix, parts[i]))
                    break
        elif isinstance(node, ast.Call):
            _dynamic_call(node, info, bindings, known, findings)


def _collect_defs(body: list[ast.stmt], out: list[str]) -> None:
    """Gather module-level def/class names, descending into top-level If/Try blocks only."""
    for node in body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if not node.name.startswith("_"):
                out.append(node.name)
        elif isinstance(node, ast.If):
            _collect_defs(node.body, out)
            _collect_defs(node.orelse, out)
        elif isinstance(node, ast.Try | ast.TryStar):
            _collect_defs(node.body, out)
            for handler in node.handlers:
                _collect_defs(handler.body, out)
            _collect_defs(node.orelse, out)
            _collect_defs(node.finalbody, out)


def _public_symbols(tree: ast.Module) -> tuple[str, ...]:
    out: list[str] = []
    _collect_defs(tree.body, out)
    return tuple(sorted(set(out)))


# ---------------------------------------------------------------------------- #
# Reachability and findings
# ---------------------------------------------------------------------------- #

def _reachable(modules: dict[str, ModuleInfo], entrypoints: list[str]) -> frozenset[str]:
    reached: set[str] = set()
    stack = [name for name in entrypoints if name in modules]
    while stack:
        name = stack.pop()
        if name in reached:
            continue
        reached.add(name)
        stack.extend(modules[name].edges - reached)
    return frozenset(reached)


def run(root: Path) -> set[str]:
    """Produce the full (unsuppressed) finding-line set for the tree under ``root``."""
    findings: set[str] = set()
    modules, entrypoints, trees = discover_and_parse(root, findings)
    known = frozenset(m.name for m in modules.values() if m.in_packages)
    for name, tree in trees.items():
        info = modules[name]
        bindings = _collect_bindings(info, tree, known, findings)
        _collect_references(info, tree, bindings, known, findings)
        if info.in_packages:
            info.public_symbols = _public_symbols(tree)
    reached = _reachable(modules, entrypoints)
    for info in modules.values():
        if info.in_packages and info.name not in reached:
            findings.add(f"unreachable-module:{info.rel_path}:-")
    # A reference keeps a symbol alive only when its source module is itself reachable and
    # is not the defining module. tests/ is never scanned, so test-only references cannot
    # count by construction.
    alive: set[tuple[str, str]] = set()
    for info in modules.values():
        if info.name in reached:
            alive.update((mod, sym) for mod, sym in info.refs if mod != info.name)
    for info in modules.values():
        if not info.in_packages:
            continue
        for sym in info.public_symbols:
            if (info.name, sym) not in alive:
                findings.add(f"zero-caller-export:{info.rel_path}:{sym}")
    return findings


# ---------------------------------------------------------------------------- #
# Baseline and driver
# ---------------------------------------------------------------------------- #

def load_baseline(path: Path) -> frozenset[str]:
    """Read the exact-match suppression set. Missing file = empty; malformed = usage error."""
    if not path.exists():
        return frozenset()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WiringError(f"unreadable baseline {path}: {exc}") from exc
    if not isinstance(data, list):
        raise WiringError(f"baseline {path} must be a JSON array of objects")
    lines: set[str] = set()
    for i, entry in enumerate(data):
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("finding"), str)
            or not entry.get("finding")
            or not isinstance(entry.get("justification"), str)
            or not entry.get("justification")
        ):
            raise WiringError(
                f"baseline {path} entry {i}: each entry needs non-empty "
                "'finding' and 'justification' strings"
            )
        lines.add(entry["finding"])
    return frozenset(lines)


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="fail-closed wiring audit: every provided service reachable "
        "from a real entrypoint"
    )
    parser.add_argument("--root", type=Path, default=repo_root,
                        help="repository root containing the packages and entrypoints")
    parser.add_argument("--baseline", type=Path, default=None,
                        help="path to wiring_baseline.json (default: <root>/wiring_baseline.json)")
    try:
        args = parser.parse_args(argv)
        root = args.root.resolve()
        if not root.is_dir():
            raise WiringError(f"root is not a directory: {root}")
        baseline_path = (
            args.baseline if args.baseline is not None else root / "wiring_baseline.json"
        )
        baseline = load_baseline(baseline_path)
        findings = run(root)
    except WiringError as exc:
        print(f"check_wiring: ERROR — {exc}", file=sys.stderr)
        return 2
    emitted = sorted(line for line in findings if line not in baseline)
    for line in emitted:
        print(line)
    suppressed = len(findings) - len(emitted)
    if emitted:
        print(
            f"check_wiring: FAIL — {len(emitted)} non-baselined finding(s) "
            f"({suppressed} baselined). Wire the symbol to an entrypoint, delete it, or "
            "(for a justified pre-existing case) add an exact-match entry to "
            "wiring_baseline.json.",
            file=sys.stderr,
        )
        return 1
    print(
        f"check_wiring: GREEN — every provided service is reachable from an entrypoint "
        f"({suppressed} baselined finding(s) suppressed).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # fail closed: an internal error must never look like green
        print(f"check_wiring: INTERNAL ERROR — {exc!r}", file=sys.stderr)
        raise SystemExit(2) from exc
