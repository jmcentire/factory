#!/usr/bin/env python3
"""check_no_human_gates — fail the build on any false gate in the factory's own run loop.

P-0001 (founder, 2026-10-18): the run involves the founder only at a genuine impasse, when no
role in the run knows what needs doing. The founder's words:

    "The only thing it fucking needs is if there's a [genuine] impasse."
    "Do you know what needs fucking doing? YES. THEN THERE IS NO FUCKING IMPASSE."
    "If you ever need me to 'just type go' or 'approve' <---- FUCK YOU. If you're doing what I
    asked you to do, then fucking do it. I ALREADY FUCKING ASKED YOU TO DO IT."
    "Kill HALT ... ERASE THE FUCKING WORD."

The founder had demanded the human gates go away before, and prose did not hold: later commits
put them back. So this is mechanical. Any phrasing that makes the run stop, wait, page or ask the
founder/human/operator to approve, confirm, clear or ratify — and any run-wide stop ("HALT") —
fails `make ship`.

Scanned: prompts/, docs/, harness/, factory_core/, factory_runtime/ (text and code) and the
top-level README.md, AGENTS.md, CLAUDE.md and REVIEW.md. Not scanned:
CHANGELOG.md (history), docs/proposals/, docs/HISTORICAL_MARKDOWN/ and docs/PROVENANCE-SYNC.md
(archival records; check_doctrine_sync.py treats PROVENANCE-SYNC.md as history too), and
docs/practices/incident-response.md (the paging standard for products
the factory builds, not for the factory's own run loop).

A phrase stated as what never happens ("nothing waits on the founder") is the rule, not a gate,
and passes. A line that must keep a matching phrase — the founder's own quoted words, a
digest-pinned catalog string, or replay of a record written before 0.8.20 — carries
``gate-exempt: <reason>`` on the same line (in Markdown, inside an HTML comment). The reason is
required, and it is reviewed like code.

Stdlib only. Exit code 0 = green.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNED = ("prompts", "docs", "harness", "factory_core", "factory_runtime")
#: Top-level doctrine files, scanned too; CHANGELOG.md is history and is not.
TOP_LEVEL = ("README.md", "AGENTS.md", "CLAUDE.md", "REVIEW.md")
EXCLUDED = (
    "docs/proposals/",
    "docs/HISTORICAL_MARKDOWN/",
    "docs/practices/incident-response.md",
    "docs/PROVENANCE-SYNC.md",
)
SUFFIXES = {".md", ".py", ".sh", ".json", ".tsv", ".toml", ".yaml", ".yml", ".txt", ".conf"}
EXEMPT = re.compile(r"gate-exempt:\s*\S")
#: P-0001 allows exactly one thing to reach the founder: a genuine impasse. A line that routes
#: only that is the rule.
IMPASSE = re.compile(r"\bgenuine impasse\b", re.IGNORECASE)

_WHO = r"(?:a |the )?(?:human|founder|operator|user)s?"
#: Each pattern names one false gate. The message is what the build prints.
PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), message)
    for pattern, message in (
        (r"\bonly " + _WHO + r"\b(?! said| says| asked| stated)",
         "an 'only the human can' gate"),
        (r"\buntil " + _WHO + r" (?:clears|acts|rules|answers|decides|approves|looks|signs)",
         "a step that waits until the human acts"),
        (r"\b(?:human|founder|operator)s? (?:clears|must clear|clearance)\b",
         "a human clearance"),
        (r"\b(?:human|founder|operator)[- ](?:approval|sign-?off|signature on|ratification)\b",
         "a human approval, sign-off or ratification as a precondition"),
        (r"\b(?:requires?|needs?) " + _WHO + r" (?:approval|sign-?off|ratification|decision|"
         r"ruling|signature|to (?:approve|confirm|ratify|sign|decide|rule|say so))\b",
         "a step that requires the human"),
        (r"\bask(?:s|ed|ing)? " + _WHO + r" (?:for|before|to approve|to confirm|whether|which)\b",
         "a step that asks the human"),
        (r"\bwait(?:s|ed|ing)? (?:on|for) " + _WHO + r"\b",
         "a step that waits on the human"),
        (r"\bpag(?:e|es|ed|ing) " + _WHO + r"\b",
         "paging the human"),
        (r"\b(?:escalat|rout)\w* to " + _WHO + r"\b",
         "escalating or routing to the human"),
        (r"\bgoes (?:back )?to " + _WHO + r"\b",
         "handing an item to the human"),
        (r"\b(?:the )?(?:human|founder) (?:decides|must decide|must answer|must rule|rules on)\b",
         "the human deciding what the run knows how to decide"),
        (r"\bstop and ask\b", "stop-and-ask"),
        (r"\b(?:just )?type ['\"]?go\b", "asking the founder to type go"),
        (r"\bapprove to continue\b", "approve-to-continue"),
        (r"\b(?:founder|human|operator)-only\b", "a founder-only step"),
        (r"\bHALT\b", "a run-wide stop (HALT)"),
        (r"\bhalt(?:s|ed|ing)?\b", "a run-wide stop (halt)"),
    )
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    message: str
    text: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.message}: {self.text.strip()[:160]}"


def _scanned_files(root: Path) -> list[Path]:
    files: list[Path] = [root / name for name in TOP_LEVEL if (root / name).is_file()]
    for top in SCANNED:
        base = root / top
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.is_symlink() or path.suffix not in SUFFIXES:
                continue
            relative = path.relative_to(root).as_posix()
            if "__pycache__" in relative or any(relative.startswith(x) for x in EXCLUDED):
                continue
            files.append(path)
    return files


#: A phrase stated as what never happens ("nothing waits on the founder") is the rule, not a
#: gate. The negation must sit in the same clause, shortly before the phrase.
NEGATED = re.compile(r"\b(?:never|nothing|no|not|nobody|without|none)\b[^.;:!?]{0,96}$",
                     re.IGNORECASE)


def _gates(line: str) -> str | None:
    for pattern, message in PATTERNS:
        for match in pattern.finditer(line):
            if not NEGATED.search(line[: match.start()]):
                return message
    return None


def scan_text(relative: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), 1):
        if EXEMPT.search(line) or IMPASSE.search(line):
            continue
        message = _gates(line)
        if message is not None:
            findings.append(Finding(relative, number, message, line))
    return findings


def scan(root: Path = REPO_ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for path in _scanned_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        findings.extend(scan_text(path.relative_to(root).as_posix(), text))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--root", default=str(REPO_ROOT))
    arguments = parser.parse_args(argv)
    findings = scan(Path(arguments.root))
    if not findings:
        print("no-human-gates: clean")
        return 0
    for finding in findings:
        print(finding.render(), file=sys.stderr)
    print(
        f"no-human-gates: {len(findings)} false gate(s). P-0001 (founder, 2026-10-18): the run "
        "involves the founder only at a genuine impasse, when no role knows what needs doing. "
        "\"If you ever need me to 'just type go' or 'approve' <---- FUCK YOU. If you're doing "
        "what I asked you to do, then fucking do it. I ALREADY FUCKING ASKED YOU TO DO IT.\" "
        "Rewrite each as decide, record, proceed; a line that must keep the phrase carries "
        "'gate-exempt: <reason>'.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
