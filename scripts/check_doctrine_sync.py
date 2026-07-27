#!/usr/bin/env python3
"""Fail-closed structural parity guard for the active factory doctrine surfaces.

The canonical specification is intentionally prose, but its load-bearing shape is structural:
sixteen numbered system sections plus §3.5 Criticality, three phase headings, three role
directives, a three-row role map, three criticality classes, and eight numbered
non-negotiables. This guard parses those structures rather than asking whether a phrase happens
to occur somewhere in the file.

It also scans every active Markdown surface (including future files) and core module for
superseded commitment phrases. Historical provenance is explicitly excluded: a history should
not be rewritten merely to make the present-tense parity scan green.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANONICAL = Path("docs/SOFTWARE-FACTORY.md")
HISTORICAL_MARKDOWN = frozenset({Path("docs/PROVENANCE-SYNC.md")})

EXPECTED_PART_I_SECTIONS: tuple[str, ...] = (
    "1. What this is, and why",
    "2. What already exists, and what is missing from it",
    "3. The three roles",
    "3.5. Criticality",
    "4. The three phases",
    "5. Translation boundaries",
    "6. What is shared and what is independent",
    "7. The eight non-negotiables",
    "8. Two flows, one structure",
    "9. The environment ladder",
    "10. The build loop",
    "11. The gate",
    "12. The evidence plane",
    "13. The factory is itself a regulated system",
    "14. What the factory cannot reach",
    "15. What changes for engineers",
    "16. The core guarantee",
)

EXPECTED_PHASE_HEADINGS: tuple[str, ...] = (
    "Phase 1 — Product specification",
    "Phase 2 — Architecture",
    "Phase 3 — Operational maturity",
)

EXPECTED_ROLE_DIRECTIVES: tuple[str, ...] = (
    "Directive — Validator",
    "Directive — Coder",
    "Directive — Tester",
)

EXPECTED_ROLE_ROWS: tuple[str, ...] = ("Validator", "Coder", "Tester")
EXPECTED_ROLE_CHANNELS: tuple[tuple[str, str], ...] = (
    ("Validator", "Human, Coder, Tester"),
    ("Coder", "Validator only"),
    ("Tester", "Validator only"),
)
EXPECTED_PHASE_ROWS: tuple[str, ...] = (
    "1. Product spec",
    "2. Architecture",
    "3. Operational maturity",
)
EXPECTED_CRITICALITY_ROWS: tuple[str, ...] = ("Critical", "Standard", "Cosmetic")

EXPECTED_NON_NEGOTIABLES: tuple[str, ...] = (
    "Fail closed on the hazards",
    "Single authoritative owner per fact",
    "Least privilege",
    "Full auditability",
    "No silent failure",
    "Honesty in docs and self-reports",
    "Provenance of intent",
    "Live-verified, not self-attested",
)

STALE_COMMITMENTS: tuple[str, ...] = (
    "the seven non-negotiables",
    "code, test, validate",
    "testing plan proposal",
    "protected hidden tests",
    "hidden suite is a protected",
    "product spec, eng spec",
    "consequenceprofile",
    "consequence-driven distinct-human approver floor",
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_NON_NEGOTIABLE_RE = re.compile(r"^\*\*(\d+)\.\s+(.+?)\.\*\*")


def _read(root: Path, relative: Path) -> str:
    return (root / relative).read_text(encoding="utf-8")


def _headings(text: str) -> tuple[tuple[int, str], ...]:
    found: list[tuple[int, str]] = []
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            found.append((len(match.group(1)), match.group(2)))
    return tuple(found)


def _section_lines(text: str, level: int, title: str) -> tuple[str, ...]:
    lines = text.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if match and len(match.group(1)) == level and match.group(2) == title:
            start = index + 1
            break
    if start is None:
        return ()

    selected: list[str] = []
    for line in lines[start:]:
        match = _HEADING_RE.match(line)
        if match and len(match.group(1)) <= level:
            break
        selected.append(line)
    return tuple(selected)


def _table_first_column(section: Iterable[str]) -> tuple[str, ...]:
    """Parse the first column of the first Markdown table in a selected section."""
    rows: list[str] = []
    in_table = False
    skipped_header = False
    for raw in section:
        line = raw.strip()
        if not line.startswith("|"):
            if in_table:
                break
            continue
        cells = tuple(cell.strip() for cell in line.strip("|").split("|"))
        if not in_table:
            in_table = True
            continue
        if not skipped_header:
            skipped_header = True
            continue
        if cells and cells[0]:
            rows.append(cells[0].strip("*"))
    return tuple(rows)


def _table_rows(section: Iterable[str]) -> tuple[tuple[str, ...], ...]:
    """Parse all body rows from the first Markdown table in a selected section."""
    rows: list[tuple[str, ...]] = []
    in_table = False
    skipped_header = False
    for raw in section:
        line = raw.strip()
        if not line.startswith("|"):
            if in_table:
                break
            continue
        cells = tuple(cell.strip().strip("*") for cell in line.strip("|").split("|"))
        if not in_table:
            in_table = True
            continue
        if not skipped_header:
            skipped_header = True
            continue
        rows.append(cells)
    return tuple(rows)


def _normalized(text: str) -> str:
    lines = (re.sub(r"^\s*>\s?", "", line) for line in text.splitlines())
    return re.sub(r"\s+", " ", "\n".join(lines)).strip()


def _active_markdown(root: Path) -> tuple[Path, ...]:
    """Enumerate every active Markdown surface so a new site cannot evade parity scanning."""
    root_docs = tuple(
        path
        for path in (Path("README.md"), Path("CLAUDE.md"), Path("AGENTS.md"))
        if (root / path).exists()
    )
    docs = tuple(
        path.relative_to(root)
        for path in sorted((root / "docs").rglob("*.md"))
        if path.relative_to(root) not in HISTORICAL_MARKDOWN
        and path.relative_to(root) != CANONICAL
    )
    return root_docs + docs


def _active_python(root: Path) -> tuple[Path, ...]:
    return tuple(path.relative_to(root) for path in sorted((root / "factory_core").rglob("*.py")))


def check_repository(root: Path = ROOT) -> tuple[str, ...]:
    errors: list[str] = []
    canonical_path = root / CANONICAL
    if not canonical_path.exists():
        return (f"missing-canonical:{CANONICAL}",)

    canonical = _read(root, CANONICAL)
    headings = _headings(canonical)

    h1 = tuple(title for level, title in headings if level == 1)
    expected_h1 = (
        "The Software Factory",
        "Part I — The System",
        "Part II — Role Directives",
    )
    if h1 != expected_h1:
        errors.append(f"canonical-h1-mismatch:{h1!r}")

    part_i_lines = _section_lines(canonical, 1, "Part I — The System")
    part_i_headings = tuple(
        title for level, title in _headings("\n".join(part_i_lines)) if level == 2
    )
    if part_i_headings != EXPECTED_PART_I_SECTIONS:
        errors.append(f"part-i-sections-mismatch:{part_i_headings!r}")

    phase_section = _section_lines(canonical, 2, "4. The three phases")
    phase_headings = tuple(
        title
        for level, title in _headings("\n".join(phase_section))
        if level == 3 and title.startswith("Phase ")
    )
    if phase_headings != EXPECTED_PHASE_HEADINGS:
        errors.append(f"phase-headings-mismatch:{phase_headings!r}")

    criticality_section = _section_lines(canonical, 2, "3.5. Criticality")
    criticality_rows = _table_first_column(criticality_section)
    if criticality_rows != EXPECTED_CRITICALITY_ROWS:
        errors.append(f"criticality-map-mismatch:{criticality_rows!r}")
    criticality_text = _normalized("\n".join(criticality_section))
    if "An unclassified surface is critical." not in criticality_text:
        errors.append("unclassified-critical-default-missing")
    if "including the surfaces its side effects reach" not in criticality_text:
        errors.append("side-effect-criticality-inheritance-missing")

    role_directives = tuple(
        title for level, title in headings if level == 2 and title.startswith("Directive — ")
    )
    if role_directives != EXPECTED_ROLE_DIRECTIVES:
        errors.append(f"role-directives-mismatch:{role_directives!r}")

    role_section = _section_lines(canonical, 3, "Roles")
    role_rows = _table_first_column(role_section)
    if role_rows != EXPECTED_ROLE_ROWS:
        errors.append(f"role-map-mismatch:{role_rows!r}")
    role_channels = tuple((row[0], row[1]) for row in _table_rows(role_section) if len(row) >= 2)
    if role_channels != EXPECTED_ROLE_CHANNELS:
        errors.append(f"role-channel-map-mismatch:{role_channels!r}")

    phase_rows = _table_first_column(_section_lines(canonical, 3, "Phases"))
    if phase_rows != EXPECTED_PHASE_ROWS:
        errors.append(f"phase-map-mismatch:{phase_rows!r}")

    non_negotiable_lines = _section_lines(canonical, 2, "7. The eight non-negotiables")
    numbered: list[tuple[int, str]] = []
    for line in non_negotiable_lines:
        match = _NON_NEGOTIABLE_RE.match(line)
        if match:
            numbered.append((int(match.group(1)), match.group(2)))
    expected_numbered = tuple(enumerate(EXPECTED_NON_NEGOTIABLES, start=1))
    if tuple(numbered) != expected_numbered:
        errors.append(f"non-negotiables-mismatch:{tuple(numbered)!r}")

    status = "\n".join(_section_lines(canonical, 2, "Status of this document")).casefold()
    if "this document specifies the system. it does not report the system." not in status:
        errors.append("status-honesty-boundary-missing")
    if "readme.md#doctrine--code-mapping" not in status:
        errors.append("operational-guide-link-missing")

    shared_foundation = _normalized(
        "\n".join(_section_lines(canonical, 2, "Shared foundation"))
    )
    communication_contract = (
        "The writer of a fix does not control the judge, cannot negotiate the verdict, "
        "and cannot talk to the Tester."
    )
    if communication_contract not in shared_foundation:
        errors.append("core-doctrine-communication-mismatch")
    if "Working-copy question" in canonical:
        errors.append("unresolved-working-copy-question")

    determinism = _section_lines(
        canonical, 3, "Determinism is class-scoped, and retry is search"
    )
    determinism_rows = _table_first_column(determinism)
    if determinism_rows != EXPECTED_CRITICALITY_ROWS:
        errors.append(f"determinism-policy-mismatch:{determinism_rows!r}")

    gate_axes = _section_lines(canonical, 3, "The two axes compose")
    gate_rows = _table_first_column(gate_axes)
    if gate_rows != EXPECTED_CRITICALITY_ROWS:
        errors.append(f"criticality-gate-matrix-mismatch:{gate_rows!r}")
    gate_matrix = _table_rows(gate_axes)
    critical_row = next((row for row in gate_matrix if row and row[0] == "Critical"), ())
    if len(critical_row) < 3 or critical_row[2] != "Block":
        errors.append("criticality-critical-gap-must-block")
    gate_text = _normalized("\n".join(gate_axes))
    if "Oracle adequacy and criticality are independent and both apply." not in gate_text:
        errors.append("oracle-criticality-independence-missing")

    validator = _normalized("\n".join(_section_lines(canonical, 2, "Directive — Validator")))
    if "Assign criticality per component and externally reachable surface." not in validator:
        errors.append("validator-criticality-assignment-missing")
    if "You never promote a Critical surface on a waiver." not in validator:
        errors.append("validator-critical-no-waiver-missing")

    tester = _normalized("\n".join(_section_lines(canonical, 2, "Directive — Tester")))
    if "Write deterministically on critical surfaces." not in tester:
        errors.append("tester-critical-determinism-missing")

    for path in _active_markdown(root) + _active_python(root):
        text = _read(root, path).casefold()
        for stale in STALE_COMMITMENTS:
            if stale in text:
                errors.append(f"stale-commitment:{path}:{stale}")

    return tuple(dict.fromkeys(errors))


def main() -> int:
    errors = check_repository()
    if errors:
        print("check_doctrine_sync: RED — active doctrine surfaces are inconsistent")
        for error in errors:
            print(f"  {error}")
        return 1
    print(
        "check_doctrine_sync: GREEN — canonical three-role/three-phase/eight-rule structure, "
        "criticality/determinism policy, communication contract, and active-surface parity hold"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
