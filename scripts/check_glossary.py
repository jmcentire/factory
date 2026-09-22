#!/usr/bin/env python3
"""check_glossary — the declared definition site stays true (plan 5.2, ruling 5a).

Two EXACT mechanical checks, never semantic paraphrase policing:

1. **single-definition-site** — each glossary term appears as *the* definition
   (the ``- **term** —`` pattern) exactly once across the scanned surfaces.
2. **referent-integrity** — every entry's cited ``module.py::symbol`` exists and
   its source segment re-derives the recorded digest, so a doc goes stale the
   moment its referent changes and the build says so.

Exit 0 green, 1 red, 2 usage. The paraphrase residual keeps its true defense
(fewer surfaces + the doctrine guard's denylist); nothing makes it impossible —
stated, not hidden.
"""

from __future__ import annotations

import ast
import hashlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GLOSSARY = REPO / "docs" / "GLOSSARY.md"

#: The scanned definition surfaces (rglob — new files need no registration).
_SURFACE_GLOBS = ("docs/**/*.md", "prompts/**/*.md", "*.md")
#: Generated and historical surfaces are exempt from the uniqueness scan.
_EXEMPT_PARTS = ("HISTORICAL_MARKDOWN", "ROLE-DOCTRINE.md", "proposals")

_ENTRY = re.compile(
    r"^- \*\*(?P<term>[^*]+)\*\* — .*?"
    r"Referent: `(?P<module>[\w/\.]+)::(?P<symbol>\w+)` @ (?P<digest>sha256:[0-9a-f]{64})",
    re.M,
)
_DEFINITION = re.compile(r"^- \*\*([^*]+)\*\* — ", re.M)


def symbol_digest(module_path: Path, symbol: str) -> str | None:
    """sha256 of the named top-level function/class source segment, or None."""
    try:
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ) and node.name == symbol:
            segment = ast.get_source_segment(source, node)
            if segment is None:
                return None
            return "sha256:" + hashlib.sha256(segment.encode("utf-8")).hexdigest()
    return None


def main() -> int:
    if not GLOSSARY.is_file():
        print("check_glossary: RED — docs/GLOSSARY.md is missing", file=sys.stderr)
        return 1
    text = GLOSSARY.read_text(encoding="utf-8")
    entries = list(_ENTRY.finditer(text))
    problems: list[str] = []
    if not entries:
        problems.append("glossary has no parseable digest-bound entries")

    # referent-integrity
    for match in entries:
        module = REPO / match.group("module")
        recomputed = symbol_digest(module, match.group("symbol"))
        if recomputed is None:
            problems.append(
                f"term {match.group('term')!r}: referent "
                f"{match.group('module')}::{match.group('symbol')} does not exist "
                f"as a top-level symbol"
            )
        elif recomputed != match.group("digest"):
            problems.append(
                f"term {match.group('term')!r}: referent "
                f"{match.group('module')}::{match.group('symbol')} changed — the "
                f"definition is STALE (recorded {match.group('digest')[:19]}..., "
                f"current {recomputed[:19]}...); re-read the symbol and re-derive"
            )

    # single-definition-site (exact-token, across the scanned surfaces)
    counts: dict[str, list[str]] = {}
    for pattern in _SURFACE_GLOBS:
        for path in sorted(REPO.glob(pattern)):
            if any(part in str(path) for part in _EXEMPT_PARTS):
                continue
            body = path.read_text(encoding="utf-8", errors="ignore")
            for definition in _DEFINITION.finditer(body):
                term = definition.group(1).strip().lower()
                counts.setdefault(term, []).append(str(path.relative_to(REPO)))
    glossary_terms = {match.group("term").strip().lower() for match in entries}
    for term in sorted(glossary_terms):
        sites = counts.get(term, [])
        if len(sites) > 1:
            problems.append(
                f"term {term!r} is defined at {len(sites)} sites ({', '.join(sites)}) "
                f"— the glossary is THE definition site, everything else links"
            )

    if problems:
        print("check_glossary: RED —", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(
        f"check_glossary: GREEN — {len(entries)} digest-bound entries verified; "
        f"single-definition-site holds across the scanned surfaces"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
