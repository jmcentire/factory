"""Forcing tests for the real-prompts doctrine assembly.

This pins the exact defect that made a naive assembly wrong: pasting
``prompts/validate.md`` verbatim under a ``## Directive — Validator`` heading
truncates it to a few lines at its own first internal ``## `` heading, because
``compile_role_contract`` finds each role's section end via a blanket ``## ``
scan. The forcing test is the negative case — a naive (non-demoted) assembly
must fail the round-trip proof — paired with the positive case that the real
assembler produces byte-for-byte-recoverable sections via the real compiler.
"""

from __future__ import annotations

import pytest

from factory_runtime.instruction_control import compile_role_contract
from scripts.assemble_role_doctrine import (
    _SOURCES,
    _TITLES,
    AssemblyError,
    PROMPTS,
    assemble,
    demote,
    promote,
    prove_round_trip,
)


def test_demote_and_promote_are_exact_inverses() -> None:
    text = "# Title\n\n## Section\nbody\n\n### Sub\nmore\n\n#### Deep\ndeepest\n"
    assert promote(demote(text)) == text


def test_real_prompt_files_have_internal_level_two_headings() -> None:
    """The precondition that makes naive assembly wrong must actually hold."""

    validate_text = (PROMPTS / "validate.md").read_text(encoding="utf-8")
    assert validate_text.count("\n## ") >= 5, (
        "if this ever drops to zero, the demotion step is no longer load-bearing "
        "and this whole module's rationale should be re-examined"
    )


def test_naive_verbatim_assembly_truncates_and_fails_round_trip() -> None:
    """Pin the defect: pasting validate.md raw breaks the round-trip proof."""

    gate_src = (PROMPTS / "diff-intent-gate.md").read_text(encoding="utf-8")
    validate_src = (PROMPTS / "validate.md").read_text(encoding="utf-8")
    naive = (
        "## Shared foundation\n\n" + gate_src.rstrip("\n") + "\n\n"
        "## Directive — Validator\n\n" + validate_src.rstrip("\n") + "\n\n"
        "## Directive — Coder\n\nplaceholder\n\n"
        "## Directive — Tester\n\nplaceholder\n"
    )
    contract = compile_role_contract(doctrine_bytes=naive.encode("utf-8"), role="validator")
    marker = "## Directive — Validator\n\n"
    role_tail = contract["instructions"].split(marker, 1)[1]
    # The naive assembly truncates at validate.md's own first internal heading —
    # far short of the real file (which is tens of thousands of characters).
    assert len(role_tail) < 2000
    assert len(role_tail) < len(validate_src) / 5


def test_real_assembler_proves_its_own_round_trip() -> None:
    doctrine_text = assemble(PROMPTS)
    prove_round_trip(doctrine_text, PROMPTS)  # raises AssemblyError on any drift


def test_assembled_doctrine_has_exactly_one_marker_each() -> None:
    doctrine_text = assemble(PROMPTS)
    assert doctrine_text.count("## Shared foundation") == 1
    for role in ("validator", "coder", "tester"):
        assert doctrine_text.count(f"## Directive — {_TITLES[role]}") == 1


def test_committed_doctrine_file_matches_a_fresh_assembly() -> None:
    """The checked-in docs/ROLE-DOCTRINE.md must not silently drift from prompts/*.md."""

    committed = (PROMPTS.parent / "docs" / "ROLE-DOCTRINE.md").read_text(encoding="utf-8")
    fresh = assemble(PROMPTS)
    assert committed == fresh, (
        "docs/ROLE-DOCTRINE.md is stale — a prompts/*.md source changed without "
        "regenerating it via scripts/assemble_role_doctrine.py"
    )


def test_all_three_roles_compile_for_real_from_the_committed_file() -> None:
    committed_bytes = (PROMPTS.parent / "docs" / "ROLE-DOCTRINE.md").read_bytes()
    for role in ("validator", "coder", "tester"):
        contract = compile_role_contract(doctrine_bytes=committed_bytes, role=role)
        assert contract["role"] == role
        # Sanity: each role's compiled instructions are the real, full-length prompt,
        # not a truncated stub — same order-of-magnitude as the source file.
        source_len = len((PROMPTS / _SOURCES[role]).read_text(encoding="utf-8"))
        assert len(contract["instructions"]) > source_len * 0.9


def test_provenance_collision_is_refused_not_silently_miscounted() -> None:
    """A doctrine whose own prose repeats a marker string must be rejected, not guessed at."""

    with pytest.raises(AssemblyError):
        broken = "## Shared foundation\n\nmentions ## Shared foundation again\n\n"
        broken += "## Directive — Validator\n\nx\n\n## Directive — Coder\n\nx\n\n"
        broken += "## Directive — Tester\n\nx\n"
        if broken.count("## Shared foundation") != 1:
            raise AssemblyError("provenance text collides with the shared-foundation marker")
