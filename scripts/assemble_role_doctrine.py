#!/usr/bin/env python3
"""assemble_role_doctrine — build docs/ROLE-DOCTRINE.md from the real prompts/*.md.

``factory_runtime.instruction_control.compile_role_contract`` expects one doctrine
source with a shared-foundation heading and one per-role directive heading
demarcating sections (built for the mechanical dark-run dispatch pipeline). No such
file exists in this generic core by default — doctrine is target-supplied data — so
that pipeline has nothing real to compile against until a target supplies one, or
until this script assembles the real prompts this repo already ships into that shape.

This is a structural assembly, not new content. ``prompts/validate.md``,
``prompts/engineer.md``, and ``prompts/test.md`` each use ``## `` for their own
internal section headings — the exact level ``compile_role_contract`` scans to find
each role section's end. Pasted verbatim, a role's own first internal heading would
silently truncate its compiled instructions to a few lines. The fix here is
mechanical and lossless: every heading in each source file is demoted by exactly one
level (``##`` -> ``###``, ``###`` -> ``####``, ...) so it stops colliding with the
section-boundary scan. Nothing is invented, dropped, or reworded.

The loss-free claim is not asserted, it is proven: this script promotes every heading
back by one level in each compiled role section and asserts the result equals the
source file byte-for-byte, via the real ``compile_role_contract``, before it writes
anything.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS = REPO_ROOT / "prompts"
OUTPUT = REPO_ROOT / "docs" / "ROLE-DOCTRINE.md"

_TITLES = {"validator": "Validator", "coder": "Coder", "tester": "Tester"}
_SOURCES = {"validator": "validate.md", "coder": "engineer.md", "tester": "test.md"}
_SHARED_SOURCE = "diff-intent-gate.md"

_HEADING = re.compile(r"^(#{2,6}) ", re.MULTILINE)


class AssemblyError(RuntimeError):
    """Raised when the assembled doctrine fails its own round-trip proof."""


def demote(text: str) -> str:
    """Demote every heading level 2-6 by exactly one."""

    return _HEADING.sub(lambda m: "#" * (len(m.group(1)) + 1) + " ", text)


def promote(text: str) -> str:
    """The exact inverse of :func:`demote`."""

    return re.sub(
        r"^(#{3,7}) ",
        lambda m: "#" * (len(m.group(1)) - 1) + " ",
        text,
        flags=re.MULTILINE,
    )


_PROVENANCE = """# Role doctrine — assembled from prompts/*.md

**This file is a structural assembly, not new content.**
`factory_runtime.instruction_control.compile_role_contract` expects one doctrine
source with two canonical heading kinds demarcating role sections (built for
the mechanical dark-run dispatch pipeline): a shared-foundation heading, and
one per-role directive heading. No such file existed in this generic core —
doctrine is normally target-supplied data. This file exists so that pipeline
has something REAL to compile against, assembled from the actual live
prompts this repo ships (`prompts/validate.md`, `prompts/engineer.md`,
`prompts/test.md`, `prompts/diff-intent-gate.md`), verbatim except for one
mechanical transformation: every heading inside each source file is demoted
by exactly one level (level 2 to level 3, level 3 to level 4, ...) so it no
longer collides with the section-boundary scan below. `scripts/assemble_role_doctrine.py`
proves this transformation is lossless before writing this file: promoting
every heading in each per-role section back by one level reproduces the
corresponding `prompts/*.md` file byte-for-byte, verified via the real
`compile_role_contract` — not merely asserted.

Regenerate with `python3 scripts/assemble_role_doctrine.py` if any source
prompt changes; do not hand-edit the sections below.

"""


def assemble(prompts_dir: Path) -> str:
    """Return the assembled doctrine text (has NOT yet been proven round-trip-safe)."""

    gate_src = (prompts_dir / _SHARED_SOURCE).read_text(encoding="utf-8")
    parts = [_PROVENANCE, "## Shared foundation\n\n", gate_src.rstrip("\n"), "\n\n"]
    for role in ("validator", "coder", "tester"):
        source_text = (prompts_dir / _SOURCES[role]).read_text(encoding="utf-8")
        parts.append(f"## Directive — {_TITLES[role]}\n\n")
        parts.append(demote(source_text).rstrip("\n"))
        parts.append("\n\n")
    text = "".join(parts).rstrip("\n") + "\n"
    if text.count("## Shared foundation") != 1:
        raise AssemblyError("provenance text collides with the shared-foundation marker")
    for role in ("validator", "coder", "tester"):
        marker = f"## Directive — {_TITLES[role]}"
        if text.count(marker) != 1:
            raise AssemblyError(f"doctrine does not have exactly one {marker!r} heading")
    return text


def prove_round_trip(doctrine_text: str, prompts_dir: Path) -> None:
    """Prove, via the real compiler, that every role section reproduces its source."""

    from factory_runtime.instruction_control import compile_role_contract

    doctrine_bytes = doctrine_text.encode("utf-8")
    for role in ("validator", "coder", "tester"):
        original = (prompts_dir / _SOURCES[role]).read_text(encoding="utf-8")
        contract = compile_role_contract(doctrine_bytes=doctrine_bytes, role=role)
        marker = f"## Directive — {_TITLES[role]}\n\n"
        instructions = contract["instructions"]
        if marker not in instructions:
            raise AssemblyError(f"compiled {role} instructions lost their own heading")
        role_tail = instructions.split(marker, 1)[1]
        reconstructed = promote(role_tail).rstrip("\n") + "\n"
        expected = original.rstrip("\n") + "\n"
        if reconstructed != expected:
            raise AssemblyError(f"round-trip fidelity failed for role={role}")


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    try:
        doctrine_text = assemble(PROMPTS)
        prove_round_trip(doctrine_text, PROMPTS)
    except AssemblyError as exc:
        print(f"assemble_role_doctrine: ERROR — {exc}", file=sys.stderr)
        return 2
    OUTPUT.write_text(doctrine_text, encoding="utf-8")
    print(f"assemble_role_doctrine: wrote {OUTPUT} ({len(doctrine_text)} chars); "
          "round-trip proven for validator, coder, tester")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
