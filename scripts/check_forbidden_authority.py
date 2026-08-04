#!/usr/bin/env python3
"""check_forbidden_authority — bans two upstream seams by capability (fail-closed).

Both bans are capability judgements the build plan records, not preferences, and both were
supposed to be enforced "with an import guard" rather than by review convention. Until this
guard existed, nothing enforced either one.

  (a) exemplar's ``TesseraSeal`` is a five-field model with ``content_hash`` /
      ``previous_hash`` / ``chain_hash`` / ``sealed_at`` / ``sealer_id`` and **no signature
      field of any kind**. It cannot verify a founder signature against a fingerprint, so it
      cannot perform the genesis ceremony at all. Anything anchoring authority must use the real
      ``jmcentire/tessera`` engine. Note the ban is on ``TesseraSeal``, NOT on Tessera: the
      engine is required, the impostor is refused.

  (b) ``signet-sdk``'s ``check_authority`` computes a SHA-256 binding and then discards it —
      ``is_authority_granted`` returns true for any non-zero digest, which is every structurally
      valid input. It is worse than an honest stub because it hashes first, so it *looks* like a
      decision at the call site. ``signet-cred`` is a real credential engine and is not banned;
      this ban is the SDK authority seam only.

Scope and honest limits: this is an AST scan over identifier references and imports, so comments
and docstrings can name what is banned without tripping it — a guard that could not be explained
in the file it guards would be unusable. It does not close a deliberate
``getattr(mod, "check_authority")`` evasion; it is a guard against a banned seam reaching a
Critical path, not a sandbox against an adversary with commit access.

Stdlib only. Exit code 0 = green.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

# Symbols that may never be referenced by name. Each is banned for a demonstrated capability
# gap, cited in the module docstring above.
BANNED_SYMBOLS: dict[str, str] = {
    "TesseraSeal": "exemplar's TesseraSeal has no signature field; it cannot verify a founder "
                   "signature. Use the real jmcentire/tessera engine.",
    "check_authority": "signet-sdk's check_authority grants unconditionally. It must never "
                       "appear on a Critical path.",
    "is_authority_granted": "signet-sdk's blanket-grant helper behind check_authority.",
}

# Top-level modules that may never be imported.
BANNED_MODULES: dict[str, str] = {
    "exemplar": "exemplar is a reference implementation, not a dependency; its TesseraSeal "
                "cannot verify signatures.",
    "signet_sdk": "the signet SDK's authority seam grants unconditionally; use signet-cred.",
}


@dataclass(frozen=True)
class Finding:
    kind: str
    path: str
    line: int
    detail: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.path}:{self.line}: {self.detail}"


def _iter_py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _module_head(name: str) -> str:
    return name.split(".", 1)[0]


def check_file(path: Path, display: str) -> list[Finding]:
    """Scan one module for banned imports and banned identifier references."""
    # A fail-closed guard must not exit on a traceback: an unreadable or non-UTF-8 module is a
    # file the ban could not be checked against, which is a finding, not a crash.
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [Finding("unreadable", display, 0, f"could not read (fail closed): {exc}")]
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [Finding("parse", display, exc.lineno or 0, f"could not parse (fail closed): {exc}")]

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                head = _module_head(alias.name)
                if head in BANNED_MODULES:
                    findings.append(
                        Finding("import", display, node.lineno,
                                f"imports banned module {alias.name!r} — {BANNED_MODULES[head]}")
                    )
        elif isinstance(node, ast.ImportFrom):
            head = _module_head(node.module or "")
            if head in BANNED_MODULES:
                findings.append(
                    Finding("import", display, node.lineno,
                            f"imports from banned module {node.module!r} — "
                            f"{BANNED_MODULES[head]}")
                )
            for alias in node.names:
                if alias.name in BANNED_SYMBOLS:
                    findings.append(
                        Finding("symbol", display, node.lineno,
                                f"imports banned symbol {alias.name!r} — "
                                f"{BANNED_SYMBOLS[alias.name]}")
                    )
        elif isinstance(node, ast.Name) and node.id in BANNED_SYMBOLS:
            findings.append(
                Finding("symbol", display, node.lineno,
                        f"references banned symbol {node.id!r} — {BANNED_SYMBOLS[node.id]}")
            )
        elif isinstance(node, ast.Attribute) and node.attr in BANNED_SYMBOLS:
            findings.append(
                Finding("symbol", display, node.lineno,
                        f"references banned attribute {node.attr!r} — "
                        f"{BANNED_SYMBOLS[node.attr]}")
            )
    return findings


def run(roots: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for root in roots:
        if not root.exists():
            findings.append(
                Finding("root", str(root), 0, "scan root not found (fail closed)")
            )
            continue
        for path in _iter_py_files(root):
            try:
                display = str(path.relative_to(root.parent))
            except ValueError:
                display = str(path)
            findings += check_file(path, display)
    return findings


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="ban exemplar's TesseraSeal and signet-sdk's authority seam"
    )
    parser.add_argument("--root", type=Path, action="append", dest="roots",
                        help="package directory to scan (repeatable)")
    parser.add_argument("--quiet", action="store_true", help="only print on failure")
    args = parser.parse_args(argv)

    roots = args.roots or [repo_root / "factory_core", repo_root / "factory_runtime"]
    findings = run(roots)
    if findings:
        print(f"check_forbidden_authority: FAIL — {len(findings)} finding(s):", file=sys.stderr)
        for f in findings:
            print(f"  {f}", file=sys.stderr)
        print("\nThese seams are banned by capability, not preference: exemplar's TesseraSeal "
              "cannot verify a signature, and signet-sdk's check_authority grants "
              "unconditionally. Use the real Tessera engine and signet-cred.", file=sys.stderr)
        return 1
    if not args.quiet:
        names = ", ".join(sorted(BANNED_SYMBOLS))
        print(f"check_forbidden_authority: GREEN — no banned authority seam referenced "
              f"({names}; modules: {', '.join(sorted(BANNED_MODULES))}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
