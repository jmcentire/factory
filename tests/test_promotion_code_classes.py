"""Reverse forcing test for the promotion reason-code classification (plan §1.1).

The preflight's hard-NO set is `CONFIGURATION_DETERMINED_CODES`, exported from
promotion.py itself. This test closes the loop in both directions: every
emission-context code literal in the module must map to exactly one declared
class (a NEW code fails closed here until classified), and every classified
code must correspond to a real emission (no stale entries). Composed codes
(`approver-*`, `specialist-review-authority-*`, ...) are declared as
prefix+fragment compositions matching the module's actual rewrite mechanics.

Residual, stated: the emission scan recognizes the module's append idioms
(single-line and multi-line, via a position-window context); a NEW code emitted
through a novel idiom outside that vocabulary could evade direction 1 — the
stale-entry direction and the runtime consumers keep the table honest, and the
idiom vocabulary grows with the module.
"""

from __future__ import annotations

import re
from pathlib import Path

from factory_core.promotion import (
    CONFIGURATION_DETERMINED_CODES,
    PROMOTION_CODE_CLASSES,
    SURFACE_SCOPED_CODES,
)

SOURCE = (Path(__file__).resolve().parent.parent / "factory_core" / "promotion.py").read_text(
    encoding="utf-8"
)

# Raw fragments that compose into classified codes at emission time
# (_record_human's authority-* codes are never emitted raw: the approver seat
# rewrites the prefix; review/quarantine/risk seats prepend theirs).
_COMPOSITION_FRAGMENTS = {
    "authority-is-agent",
    "authority-not-enrolled",
    "authority-equals-implementer",
    "authority-equals-verifier",
}
_COMPOSITION_PREFIXES = (
    "approver-",
    "specialist-review-",
    "risk-acceptance-",
    "standard-flake-quarantine-",
)

# Emission-context literals that are values or labels, never reason codes.
_NON_CODES = {
    "report-and-promote",
    "risk-accepted",
    "negative-evidence",  # wrapper token, classified — listed to note it IS classified
    "flake-count",
    "retry-count",
}


def _classifier_region_stripped(text: str) -> str:
    """Remove the classification tables themselves so declared codes do not
    self-satisfy the emission scan."""
    start = text.index("_CONFIGURATION_CODES = (")
    end = text.index("SURFACE_SCOPED_CODES = frozenset(_SURFACE_SCOPED_CODES)")
    return text[:start] + text[end:]


def _emission_tokens(text: str) -> set[str]:
    """Position-window context matching: multi-line append(...) calls put the
    literal on a continuation line, so the emission context is sought in the
    140 characters BEFORE each literal, not on its physical line."""
    body = _classifier_region_stripped(text)
    context = re.compile(
        r"append\(|hard_reasons|gate_reasons|\bgaps\b|gap_all|negatives|"
        r"local_reports|\breports\b|provenance_issues|tool_policy_issues|"
        r"missing_code=|invalid_code=|prefix="
    )
    literal = re.compile(r'f?"([a-z][a-z0-9]*(?:-[a-z0-9_]+)+)[:"{]')
    tokens: set[str] = set()
    for match in literal.finditer(body):
        window = body[max(0, match.start() - 140) : match.start()]
        if context.search(window):
            tokens.add(match.group(1).rstrip("-"))
    return tokens


def test_every_emission_code_is_classified() -> None:
    """A new reason code fails closed here until classified — the plan's
    reverse forcing requirement."""
    unclassified = set()
    for token in _emission_tokens(SOURCE):
        if token in PROMOTION_CODE_CLASSES:
            continue
        if token in _COMPOSITION_FRAGMENTS or token in _NON_CODES:
            continue
        if any(token == prefix.rstrip("-") for prefix in _COMPOSITION_PREFIXES):
            continue
        unclassified.add(token)
    assert not unclassified, (
        f"emission codes without a declared class (classify them in "
        f"PROMOTION_CODE_CLASSES): {sorted(unclassified)}"
    )


def test_every_classified_code_has_a_real_emission() -> None:
    """No stale classification entries: each classified code appears verbatim
    in the module or is a declared prefix+fragment composition."""
    body = _classifier_region_stripped(SOURCE)
    stale = set()
    for code in PROMOTION_CODE_CLASSES:
        if f'"{code}' in body or f"'{code}" in body:
            continue
        composed = any(
            code.startswith(prefix) and (
                "authority-" + code.removeprefix(prefix).removeprefix("authority-")
                in _COMPOSITION_FRAGMENTS
                or code.removeprefix(prefix) in _COMPOSITION_FRAGMENTS
            )
            for prefix in _COMPOSITION_PREFIXES
        )
        if composed:
            continue
        stale.add(code)
    assert not stale, f"classified codes with no emission site: {sorted(stale)}"


def test_class_partitions_are_disjoint_and_complete() -> None:
    assert CONFIGURATION_DETERMINED_CODES & SURFACE_SCOPED_CODES == frozenset()
    # 1.1c dropped the eight Gate M envelope labels (7 construction-evidence + the
    # surface-scoped candidate-receipt-required) and added the three derivation reports.
    assert len(PROMOTION_CODE_CLASSES) == 117
    assert len(CONFIGURATION_DETERMINED_CODES) == 25
    assert len(SURFACE_SCOPED_CODES) == 5


def test_the_preflight_hard_no_exemplars_are_configuration() -> None:
    """The plan names these as the intake hard-NO exemplars."""
    for code in (
        "criticality-profile-invalid",
        "tool-policy-missing",
        "tool-policy-invalid",
        "critical-ratification-delegates-undeclared",
        "critical-delegate-not-enrolled-human",
        "insufficient-approvers",
    ):
        assert code in CONFIGURATION_DETERMINED_CODES, code
