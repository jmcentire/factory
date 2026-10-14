"""The Validator procedure states, before Phase A, what dispatch needs from the target."""

from pathlib import Path

PROCEDURE_ROOT = Path(__file__).resolve().parents[1]
PROCEDURE = (PROCEDURE_ROOT / "prompts" / "validate.md").read_text(encoding="utf-8")


def test_projection_config_requirement_is_stated_before_phase_a() -> None:
    phase_a = PROCEDURE.index("## Phase A \u2014 The frame")
    prerequisites = PROCEDURE.index("## Dispatch prerequisites")
    assert prerequisites < phase_a
    section = PROCEDURE[prerequisites:phase_a]
    assert ".factory/projection.conf" in section and "pinned commit" in section


def test_ledger_rows_are_batched_with_per_row_verdicts_and_a_citable_restatement_rule() -> None:
    flat = " ".join(PROCEDURE.split())
    assert "in one message, each an accept-or-refute decision by row id" in flat
    assert "verbatim source beside it" in flat
    assert "a blanket accept ratifies no row" in flat
    assert "A row with no citation is never a restatement." in flat
    assert "every CHANGES-MEANING row comes back to the human" in flat


def test_declared_dependencies_are_cached_read_only_before_dispatch() -> None:
    phase_b = PROCEDURE[PROCEDURE.index("## Phase B"):PROCEDURE.index("## Cadence")]
    cache = phase_b.index("Cache the declared dependencies")
    assert phase_b.index("Sign the run tool policy") < cache < phase_b.index("Dispatch the Coder")
    assert "read-only" in phase_b[cache:phase_b.index("Dispatch the Coder")]


def test_procedure_names_projection_for_both_lanes_and_the_offline_check() -> None:
    assert "refuses projection for **both** lanes" in PROCEDURE
    assert "deno run --cached-only" in PROCEDURE
    # Deno 2.7 rejects --cached-only on check (dogfood #57).
    assert "deno check --cached-only" not in PROCEDURE


def test_both_roles_put_a_planned_case_id_where_discovery_reads_it() -> None:
    """Discovery reads only the collected test's name, so neither role may offer the test's
    code as a place for the case id (review of #73: an id in code passed the freeze, then
    failed Phase C)."""

    from harness import strategy_plan

    tester = " ".join((PROCEDURE_ROOT / "prompts" / "test.md").read_text(encoding="utf-8").split())
    validator = " ".join(PROCEDURE.split())
    assert "in the test name of the test that realizes it" in tester
    assert "name or its code" not in tester
    assert "test_cap[R7.1]" in tester and "test_cap[R7.1]" in validator
    assert "Put each case id in the name of the test that covers it" in validator
    assert strategy_plan.untested({"R7.1": "Critical"}, ["test_cap"])["Critical"] == ["R7.1"]
    assert strategy_plan.untested({"R7.1": "Critical"}, ["test_cap[R7.1]"])["Critical"] == []
