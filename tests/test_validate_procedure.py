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


RULING = (
    "founder ruling, 2026-10-14, verbatim: \"We had discussed a mode of 'operatorless' running "
    'which should use the shortcut. Otherwise, standard mode should follow the doctrine."'
)


def test_only_autonomous_engagement_routes_restatements_past_the_human() -> None:
    """Founder ruling 2026-10-14: the restatement shortcut is for operatorless runs; under
    interactive and scheduled engagement the human rules on every ledger row by id, and the
    procedure and the canonical doctrine say the same thing."""

    from factory_core.engagement import AUTONOMOUS, ENGAGEMENTS

    flat = " ".join(PROCEDURE.split())
    doctrine = " ".join(
        (PROCEDURE_ROOT / "docs" / "SOFTWARE-FACTORY.md").read_text(encoding="utf-8").split()
    )
    human_reviewed = [level for level in ENGAGEMENTS if level != AUTONOMOUS]
    assert sorted(human_reviewed) == ["interactive", "scheduled"]
    assert ("Under `interactive` and `scheduled` engagement the human accepts or refutes every "
            "row by id, a row that restates an already-decided item included.") in flat
    assert "Only under `autonomous` engagement" in flat
    assert RULING in flat and RULING.replace("founder ruling", "Founder ruling") in doctrine
    assert "every CHANGES-MEANING row comes back to the human" not in flat
    assert "a CHANGES-MEANING row is an escalation record that leads the verdict" in flat
    assert ("That holds under `interactive` and `scheduled` engagement for every row, including "
            "one that only restates an already-decided item.") in doctrine
    assert "A CHANGES-MEANING row is recorded as an escalation record" in doctrine


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
