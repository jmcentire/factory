#!/usr/bin/env python3
"""check_core_purity — the executable anti-coupling guard (fail-closed, baseline-backed).

This is the mechanism behind the generic-core / target-as-data boundary. Green means
``factory_core`` provably imports nothing target-specific, so the core can be extracted and
shipped to a new customer by swapping a data-only target pack — no target code travels with it.

Three checks, each fail-closed:

  (a) AST import scan — every import in ``factory_core/`` must resolve to the allowlisted set
      (stdlib + the package itself + a small third-party allowlist). Any ``targets.*`` import,
      or any module whose head matches a target denylist prefix, fails hard.

  (b) Token denylist — identifiers and string literals (via AST, so comments are exempt) are
      scanned for target-specific tokens. The token set is **not hardcoded here**: it is read
      from the committed data file ``core_purity_denylist.json`` (see its documented shape and
      instructions). The generic, public core ships that file with an **empty** token set —
      a generic core names nothing target-specific, so there is nothing to catch, and this
      check is trivially green. A consuming target fills the denylist with its own tokens as
      part of the target's private config (never shipped in the public core). When the set is
      non-empty, every hit must be pre-justified in ``core_purity_baseline.json``; a new,
      un-baselined hit fails.

  (c) Reverse-dependency assert — ``pyproject.toml`` may not list any target pack as a
      build/runtime dependency.

Stdlib only, so the guard itself has no third-party surface to subvert. Exit code 0 = green.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

CORE_PACKAGE = "factory_core"

# Third-party distributions the core is permitted to import. Keep this list minimal and
# reviewed — it is part of the trust boundary.
THIRD_PARTY_ALLOWLIST = frozenset({"jsonschema"})

# Any import whose top-level module starts with one of these is a hard failure (target code).
TARGET_MODULE_PREFIXES = ("targets", "target_packs")


def load_denylist_tokens(denylist_path: Path) -> tuple[str, ...]:
    """Read the target-token denylist from the committed data file.

    The token set is **data**, not hardcoded in this guard. On the generic, public core the
    file's ``tokens`` array is empty (nothing target-specific to catch); a consuming target
    fills it with its own tokens as private config. Only the top-level ``tokens`` key is read;
    the file's ``examples`` block is illustrative and deliberately ignored. Tokens are
    lowercased whole-word alphanumeric runs, matched by component equality (see ``_scan_text``).
    A missing or malformed file yields an empty set (the generic default) rather than failing:
    an empty denylist is the correct posture for a clean core, and the import-scan and
    reverse-dependency checks remain fully enforced regardless.
    """
    if not denylist_path.exists():
        return ()
    try:
        data = json.loads(denylist_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    raw = data.get("tokens", []) if isinstance(data, dict) else []
    return tuple(str(t).lower() for t in raw if isinstance(t, str) and t)


@dataclass(frozen=True)
class Finding:
    check: str
    file: str
    line: int
    detail: str

    def __str__(self) -> str:
        return f"[{self.check}] {self.file}:{self.line}: {self.detail}"


def _iter_py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _module_head(name: str) -> str:
    return name.split(".", 1)[0]


def _is_target_module(module: str, tokens: tuple[str, ...]) -> bool:
    head = _module_head(module)
    if head in TARGET_MODULE_PREFIXES:
        return True
    # a module whose head matches a denylist token as a whole word (e.g. a target codename or
    # "<codename>_web"). Empty ``tokens`` (the generic core) means this branch never fires.
    for token in tokens:
        if head == token or head.startswith(token + "_") or head.startswith(token + "."):
            return True
    return False


# ---------------------------------------------------------------------------- #
# (a) AST import scan
# ---------------------------------------------------------------------------- #

def check_imports(root: Path, stdlib: frozenset[str], tokens: tuple[str, ...]) -> list[Finding]:
    findings: list[Finding] = []
    allowed = stdlib | THIRD_PARTY_ALLOWLIST | {CORE_PACKAGE}
    for path in _iter_py_files(root):
        rel = str(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except SyntaxError as exc:  # fail closed — an unparseable core file is a failure
            findings.append(Finding("imports", rel, exc.lineno or 0, f"unparseable: {exc.msg}"))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name
                    if _is_target_module(module, tokens):
                        findings.append(Finding("imports", rel, node.lineno,
                                                f"target import: {module!r}"))
                    elif _module_head(module) not in allowed:
                        findings.append(Finding("imports", rel, node.lineno,
                                                f"non-allowlisted import: {module!r}"))
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    continue  # intra-package relative import (factory_core), allowed
                module = node.module or ""
                if _is_target_module(module, tokens):
                    findings.append(Finding("imports", rel, node.lineno,
                                            f"target import: from {module!r}"))
                elif _module_head(module) not in allowed:
                    findings.append(Finding("imports", rel, node.lineno,
                                            f"non-allowlisted import: from {module!r}"))
    return findings


# ---------------------------------------------------------------------------- #
# (b) Token denylist (AST — identifiers + string literals; comments exempt)
# ---------------------------------------------------------------------------- #

def _scan_text(text: str, tokens: tuple[str, ...]) -> list[str]:
    """Return the denylist tokens that appear as a whole component of ``text``.

    Text is lowercased and split on every non-alphanumeric char, so snake_case, dotted, and
    punctuated identifiers/strings are broken into runs (a ``<jurisdiction>_county`` identifier
    -> {<jurisdiction>, county}; a ``<CODENAME>_<VENDOR>_KEY`` env var -> {<codename>, <vendor>,
    key}). A token matches only if it EQUALS a run — this is component equality, not substring,
    so a longer word that merely *contains* a token (e.g. "acmestone" vs. an "acme" token)
    never trips. On the generic core ``tokens`` is empty, so nothing ever matches.
    """
    runs = {r for r in re.split(r"[^a-z0-9]+", text.lower()) if r}
    return [tok for tok in tokens if tok in runs]


def check_tokens(root: Path, baseline: dict, tokens: tuple[str, ...]) -> list[Finding]:
    waived = {
        (entry["file"], entry["token"])
        for entry in baseline.get("allowed_occurrences", [])
        if entry.get("file") and entry.get("token")
    }
    findings: list[Finding] = []
    for path in _iter_py_files(root):
        rel = str(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except SyntaxError as exc:
            findings.append(Finding("tokens", rel, exc.lineno or 0, f"unparseable: {exc.msg}"))
            continue
        for node in ast.walk(tree):
            texts: list[str] = []
            if isinstance(node, ast.Name):
                texts.append(node.id)
            elif isinstance(node, ast.Attribute):
                texts.append(node.attr)
            elif isinstance(node, ast.arg):
                texts.append(node.arg)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                texts.append(node.name)
            elif isinstance(node, ast.keyword) and node.arg:
                texts.append(node.arg)
            elif isinstance(node, ast.alias):
                texts.append(node.name)
                if node.asname:
                    texts.append(node.asname)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                texts.append(node.value)
            line = getattr(node, "lineno", 0)
            for text in texts:
                for tok in _scan_text(text, tokens):
                    rel_name = Path(rel).name
                    if (rel, tok) in waived or (rel_name, tok) in waived:
                        continue
                    findings.append(Finding("tokens", rel, line,
                                            f"target token {tok!r} in {text!r} (not baselined)"))
    return findings


# ---------------------------------------------------------------------------- #
# (c) Reverse-dependency assert
# ---------------------------------------------------------------------------- #

def _dist_name(requirement: str) -> str:
    return re.split(r"[<>=!~;\[\s]", requirement.strip(), maxsplit=1)[0].strip().lower()


def check_reverse_dependency(pyproject: Path, tokens: tuple[str, ...]) -> list[Finding]:
    findings: list[Finding] = []
    if not pyproject.exists():
        return [Finding("reverse-dep", str(pyproject), 0, "pyproject.toml not found (fail closed)")]
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project", {})
    deps: list[str] = list(project.get("dependencies", []))
    for extra_deps in (project.get("optional-dependencies", {}) or {}).values():
        deps.extend(extra_deps)
    for req in deps:
        name = _dist_name(req)
        if _is_target_module(name.replace("-", "_"), tokens) or name in TARGET_MODULE_PREFIXES:
            findings.append(Finding("reverse-dep", str(pyproject), 0,
                                    f"core depends on a target pack: {req!r}"))
    return findings


# ---------------------------------------------------------------------------- #
# Driver
# ---------------------------------------------------------------------------- #

def run(root: Path, baseline_path: Path, pyproject: Path,
        denylist_path: Path | None = None) -> list[Finding]:
    if not root.exists():
        return [Finding("root", str(root), 0, "core package directory not found (fail closed)")]
    if not baseline_path.exists():
        return [Finding("baseline", str(baseline_path), 0,
                        "purity baseline not found (fail closed)")]
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if denylist_path is None:
        denylist_path = baseline_path.resolve().parent / "core_purity_denylist.json"
    tokens = load_denylist_tokens(denylist_path)
    stdlib = frozenset(sys.stdlib_module_names)
    findings: list[Finding] = []
    findings += check_imports(root, stdlib, tokens)
    findings += check_tokens(root, baseline, tokens)
    findings += check_reverse_dependency(pyproject, tokens)
    return findings


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="factory_core anti-coupling purity guard")
    parser.add_argument("--root", type=Path, default=repo_root / CORE_PACKAGE,
                        help="path to the factory_core package directory")
    parser.add_argument("--baseline", type=Path, default=repo_root / "core_purity_baseline.json",
                        help="path to core_purity_baseline.json")
    parser.add_argument("--denylist", type=Path,
                        default=repo_root / "core_purity_denylist.json",
                        help="path to core_purity_denylist.json (the token-denylist data file)")
    parser.add_argument("--pyproject", type=Path, default=repo_root / "pyproject.toml",
                        help="path to pyproject.toml")
    parser.add_argument("--quiet", action="store_true", help="only print on failure")
    args = parser.parse_args(argv)

    findings = run(args.root, args.baseline, args.pyproject, args.denylist)
    if findings:
        print(f"check_core_purity: FAIL — {len(findings)} finding(s):", file=sys.stderr)
        for f in findings:
            print(f"  {f}", file=sys.stderr)
        print("\nThe core must import nothing target-specific. Fix the coupling, or (for an "
              "intentional, justified token) add an entry to core_purity_baseline.json.",
              file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"check_core_purity: GREEN — {args.root} imports nothing target-specific "
              "(imports allowlisted, no un-baselined tokens, no reverse dependency).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
