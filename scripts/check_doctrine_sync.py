#!/usr/bin/env python3
"""Check the current four-role contract and active doctrine consistency.

This remains a mandatory release check. It protects role ownership, independent
inputs, critical coverage, permission boundaries and truthful evidence. Exact
section counts, signed phase documents and catalog ceremony were superseded by
the user's direct cleanup instruction; their removal is not removal of those
substantive protections. Behavioral tests, not this prose check, prove runtime
properties. Every retained rule has a planted-deletion regression.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANONICAL = Path("docs/SOFTWARE-FACTORY.md")
HISTORICAL_MARKDOWN = frozenset({Path("docs/PROVENANCE-SYNC.md")})

STALE_COMMITMENTS: tuple[str, ...] = (
    "the seven non-negotiables",
    "code, test, validate",
    "testing plan proposal",
    "protected hidden tests",
    "hidden suite is a protected",
    "product spec, eng spec",
    "consequenceprofile",
    "consequence-driven distinct-human approver floor",
    # Founder ruling 2026-09-21: exactly four roles, and the Orchestrator is always
    # resident. These phrases encoded the agent-introduced opposite; any return fails.
    "invoked, not resident",
    "invoked-on-trigger",
    "not a fourth role",
    "three roles",
    "three-role",
    "triumvirate",
    "headless-projection",
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


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
    # Ruling 5c: root markdown enumerates by rglob (depth 1) and prompts/*.md
    # joins the scanned surface set — a new file is scanned with no registration
    # step. Generated ROLE-DOCTRINE.md and historical/proposal surfaces stay out.
    root_docs = tuple(
        path.relative_to(root) for path in sorted(root.glob("*.md")) if path.is_file()
    )
    docs = tuple(
        path.relative_to(root)
        for path in sorted((root / "docs").rglob("*.md"))
        if path.relative_to(root) not in HISTORICAL_MARKDOWN
        and path.relative_to(root) != CANONICAL
        and "HISTORICAL_MARKDOWN" not in path.parts
        and "proposals" not in path.parts
        and path.name != "ROLE-DOCTRINE.md"
    )
    prompts = tuple(path.relative_to(root) for path in sorted((root / "prompts").rglob("*.md")))
    return root_docs + docs + prompts


def _active_python(root: Path) -> tuple[Path, ...]:
    # Ruling 5c: factory_runtime, harness/*.py, and scripts/*.py join the parity
    # scope — the same one-line rglob per tree.
    trees = ("factory_core", "factory_runtime", "harness", "scripts")
    return tuple(
        path.relative_to(root) for tree in trees for path in sorted((root / tree).rglob("*.py"))
    )


# One canonical statement per policy; prompts are role-specific applications.
# These checks deliberately retain meaningful policy while allowing concise prose.
RULES: tuple[tuple[str, str], ...] = (
    (
        "scope",
        "The current user request is authoritative intent.",
    ),
    ("no-manual-signatures", "No manual signature or repeated authorization ceremony is required."),
    ("attestation-intent", "verify that the claimed action and scope follow it"),
    (
        "concerns",
        "the requested capability, architecture guidance, and testing/monitoring strategy",
    ),
    (
        "no-ceremony",
        "No catalog, signing key, human receipt, phase ratification, external checkpoint",
    ),
    (
        "model-choice",
        "There is no model approval list.",
    ),
    (
        "construction-authority",
        "they cannot add requirements or change expected behavior",
    ),
    (
        "tester-input",
        "The Tester never receives the Coder's construction plan, recipe selection or "
        "implementation.",
    ),
    (
        "communication",
        "The writer of a fix does not control the judge, cannot negotiate the "
        "verdict, and cannot talk to the Tester.",
    ),
    (
        "orchestrator",
        "The Orchestrator continuously watches all roles, owns strategic oversight "
        "and process adherence",
    ),
    (
        "no-lag-stop",
        "no supervisor suspending productive roles for review lag",
    ),
    (
        "input-isolation",
        "A clean clone alone does not protect the Tester from implementation or the "
        "Coder from independent test details.",
    ),
    (
        "scoped-memory",
        "the live author graphs are not a shared communication channel",
    ),
    (
        "self-reference",
        "the Validator helped interpret the specification and then judges against it",
    ),
    (
        "independence",
        "Input separation and model diversity address different failure modes.",
    ),
    (
        "assignments",
        "Explicit assignments govern.",
    ),
    (
        "capability",
        "A forbidden capability must be unavailable to the agent, not merely "
        "discouraged in a prompt.",
    ),
    (
        "platform-permissions",
        "Actual platform permissions govern tool use; Factory cannot grant them or "
        "click through their dialogs.",
    ),
    (
        "critical-evidence",
        "an unresolved critical coverage gap blocks acceptance of that behavior",
    ),
    (
        "risk-axes",
        "Oracle adequacy and consequence severity are separate questions",
    ),
    (
        "side-effects",
        "The classification includes the surfaces reached by side effects.",
    ),
    (
        "risk-owner",
        "The Validator assigns it per component and reachable surface.",
    ),
    (
        "cited-evidence",
        "An acceptance claim needs cited evidence; an unchecked item remains a visible gap.",
    ),
    (
        "frozen-review",
        "Review the exact frozen candidate, not a moving working tree.",
    ),
    (
        "reproduction",
        "reproduce the defect in a disposable environment when possible and retain "
        "the failing result",
    ),
    (
        "unavailable-reproduction",
        "If reproduction is unavailable, state what remains unproven.",
    ),
    (
        "negative-control",
        "The negative control demonstrates the defect on the broken baseline",
    ),
    (
        "positive-control",
        "the positive control protects behavior that already works",
    ),
    (
        "regression",
        "A previously passing control that fails after a change is a regression to investigate",
    ),
    (
        "over-constraint",
        "Suspected over-constraint is a concrete contract question for the Validator.",
    ),
    (
        "determinism",
        "Critical deterministic guarantees need deterministic evidence; repeating a "
        "flaky check until green is not qualification.",
    ),
    (
        "test-disposition",
        "the Validator determines whether the candidate violates its intended "
        "contract, the baseline was already broken, or the test asserts obsolete "
        "behavior",
    ),
    (
        "no-test-weakening",
        "The author may not silently weaken the test to claim success.",
    ),
    (
        "monitor-provenance",
        "each monitor must be understandable and traceable to the criterion it watches",
    ),
    (
        "monitor-density",
        "monitor count is diagnostic, not a target",
    ),
    (
        "monitor-authorship",
        "Generated monitors may cover any criticality class.",
    ),
    (
        "truthful-authorship",
        "never label agent output as human-authored or require human authorship as a gate",
    ),
    (
        "monitor-triage",
        "An alert-triage agent must not delete or weaken the monitor that raised its alert.",
    ),
    (
        "monitor-state",
        "Retain monitor state outside the agent's transient context.",
    ),
    (
        "notifications",
        "ensure each notification communicates a meaningful finding or requested action",
    ),
    (
        "recoverable-evidence",
        "Retain the recoverable subjects as well as their hashes.",
    ),
    (
        "model-record",
        "Record each role's assigned and observed model/client version",
    ),
    (
        "honest-model-record",
        "distinguishing unknown values and configured choices from live observations",
    ),
    (
        "independence-record",
        "Record the actual independence arrangement and its limits",
    ),
    (
        "coordination",
        "Keep transient coordination out of product commits",
    ),
    (
        "no-shell-input",
        "All coordination text is data.",
    ),
    (
        "delivery",
        "A queued message is not an acknowledgment.",
    ),
    (
        "durability",
        "Optional log suppression never discards decisions, blockers or handoffs.",
    ),
    (
        "honesty",
        "It does not assert that every implementation path or native provider is qualified.",
    ),
)
PRINCIPLES = (
    "fail closed on hazards",
    "one authoritative owner per fact",
    "least privilege",
    "auditability",
    "no silent failure",
    "honest documentation and reports",
    "provenance of intent",
    "observed verification rather than self-attestation",
)
ROLES = {
    "Validator": ("validate.md", "User, Orchestrator, Coder, Tester", "accept or reject"),
    "Orchestrator": (
        "orchestrate.md",
        "User, Validator; typed author status probes",
        "Resident strategic oversight",
    ),
    "Coder": ("engineer.md", "Validator only", "Implementation against the task"),
    "Tester": ("test.md", "Validator only", "Independently derive and implement checks"),
}


def check_repository(root: Path = ROOT) -> tuple[str, ...]:
    if not (root / CANONICAL).is_file():
        return (f"missing-canonical:{CANONICAL}",)
    errors: list[str] = []
    canonical = _read(root, CANONICAL)
    normalized = _normalized(canonical)
    if tuple(title for level, title in _headings(canonical) if level == 1) != ("Software Factory",):
        errors.append("canonical-title-mismatch")
    for name, required in RULES:
        if required not in normalized:
            errors.append(f"doctrine-rule-missing:{name}")
    for required in PRINCIPLES:
        if required not in normalized:
            errors.append(f"principle-missing:{required}")
    rows = _table_rows(_section_lines(canonical, 2, "Decisions that remain necessary"))
    if tuple(row[0] for row in rows) != tuple(ROLES):
        errors.append("role-map-mismatch")
    by_role = {row[0]: row for row in rows}
    for role, (filename, channel, responsibility) in ROLES.items():
        row = by_role.get(role, ())
        if len(row) != 4 or row[2] != channel:
            errors.append(f"role-channel-mismatch:{role}")
        if len(row) != 4 or responsibility not in row[1]:
            errors.append(f"role-ownership-mismatch:{role}")
        prompt = root / "prompts" / filename
        if not prompt.is_file():
            errors.append(f"missing-role-prompt:{role}")
            continue
        text = prompt.read_text(encoding="utf-8")
        if not text.startswith(f"# {role}\n"):
            errors.append(f"role-prompt-title-mismatch:{role}")
        if "Kindex" not in text:
            errors.append(f"role-memory-guidance-missing:{role}")
    criticality = _table_rows(_section_lines(canonical, 2, "Evidence and consequences"))
    if tuple(row[0] for row in criticality) != ("Critical", "Standard", "Cosmetic"):
        errors.append("criticality-map-mismatch")
    critical = next((row for row in criticality if row[0] == "Critical"), ())
    if (
        len(critical) != 3
        or "No waiver can substitute for missing critical evidence." not in critical[2]
    ):
        errors.append("critical-gap-must-block")
    if (
        len(critical) != 3
        or "Unclassified behavior is treated as critical until examined." not in critical[1]
    ):
        errors.append("unclassified-critical-default-missing")
    if "Working-copy question" in canonical:
        errors.append("unresolved-working-copy-question")
    for path in (CANONICAL, *_active_markdown(root), *_active_python(root)):
        if path.name == "check_doctrine_sync.py":
            continue  # The list's definition is not an operating instruction.
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
        "check_doctrine_sync: GREEN — four roles, independent inputs, critical evidence, "
        "permissions, provenance and active-surface consistency checked"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
