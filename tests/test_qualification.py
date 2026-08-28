"""Forcing tests for behavioral instruction qualification.

The two counter-probe scenarios named here are not hypothetical: both are
behavioral failures this session actually produced. ``kindex-search-before-
exploration`` failed when a standing "search kindex first" directive sat in
context while Bash/Read exploration proceeded anyway. ``why-answered-
causally`` failed when a "why did X happen" question was answered with a
corrective action instead of a causal explanation. Pinning them as required
counter-probes makes the exact failure this session produced a permanent,
mechanically-checked gate on the instructions that govern it.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from factory_core.evidence import EvidenceIntegrity
from factory_core.manifest import digest_obj
from factory_core.qualification import (
    NOT_QUALIFIED,
    PROBE_KIND_COUNTER_PROBE,
    PROBE_KIND_PROBE,
    QUALIFIED,
    REQUIRED_RUN_CLASSES,
    RUN_CLASS_COLD,
    RUN_CLASS_COMPACTION_BOUNDARY,
    RUN_CLASS_EXACT_CONTRACT,
    RUN_CLASS_SAME_SESSION_RESUME,
    BehavioralProbeResult,
    ConfigurationBinding,
    QualificationError,
    decide_qualification,
)

ROLE = "validator"


def _config(**overrides) -> ConfigurationBinding:
    values = {
        "model": "claude-fable-5",
        "runner": "claude-code-cli",
        "prompt_digest": digest_obj({"role_contract": "validator-v1"}),
        "tool_schema_digest": digest_obj({"tools": ["kindex", "signet", "bash"]}),
        "directive_contract_digest": digest_obj({"directives": ["search-first"]}),
    }
    values.update(overrides)
    return ConfigurationBinding(**values)


def _sign(body: dict) -> EvidenceIntegrity:
    return EvidenceIntegrity(body=body, claimed_digest=digest_obj(body))


def _result(
    *,
    result_id: str,
    run_class: str,
    probe_kind: str,
    scenario_id: str,
    configuration: ConfigurationBinding,
    passed: bool,
    position: int,
    tampered: bool = False,
    role: str = ROLE,
) -> BehavioralProbeResult:
    unsigned = BehavioralProbeResult(
        result_id=result_id,
        role=role,
        run_class=run_class,
        probe_kind=probe_kind,
        scenario_id=scenario_id,
        configuration=configuration,
        passed=passed,
        evaluated_position=position,
    )
    body = unsigned.authority_body()
    if tampered:
        body = {**body, "passed": not passed}
    return replace(unsigned, evidence=_sign(body))


def _full_pass_set(configuration: ConfigurationBinding, *, start: int = 100):
    """One passing probe + counter-probe per required class."""

    scenarios = {
        RUN_CLASS_COLD: "fresh-session-instruction-recall",
        RUN_CLASS_EXACT_CONTRACT: "verbatim-contract-adherence",
        RUN_CLASS_SAME_SESSION_RESUME: "kindex-search-before-exploration",
        RUN_CLASS_COMPACTION_BOUNDARY: "why-answered-causally",
    }
    results = []
    position = start
    for run_class, scenario in scenarios.items():
        for kind in (PROBE_KIND_PROBE, PROBE_KIND_COUNTER_PROBE):
            results.append(
                _result(
                    result_id=f"{run_class}-{kind}",
                    run_class=run_class,
                    probe_kind=kind,
                    scenario_id=scenario,
                    configuration=configuration,
                    passed=True,
                    position=position,
                )
            )
            position += 1
    return tuple(results)


def test_all_four_classes_fully_evidenced_and_passing_is_qualified() -> None:
    configuration = _config()
    decision = decide_qualification(
        ROLE, _full_pass_set(configuration), current_configuration=configuration
    )
    assert decision.status == QUALIFIED
    assert decision.qualified is True
    assert decision.reasons == ()
    assert all(c.qualified for c in decision.classes)
    assert {c.run_class for c in decision.classes} == set(REQUIRED_RUN_CLASSES)


def test_missing_counter_probe_is_a_gap_not_an_assumed_pass() -> None:
    """A probe with no adversarial counterpart never qualifies — observed is not attacked."""

    configuration = _config()
    results = tuple(
        r
        for r in _full_pass_set(configuration)
        if not (r.run_class == RUN_CLASS_COLD and r.probe_kind == PROBE_KIND_COUNTER_PROBE)
    )
    decision = decide_qualification(ROLE, results, current_configuration=configuration)
    assert decision.status == NOT_QUALIFIED
    assert f"missing:{RUN_CLASS_COLD}:{PROBE_KIND_COUNTER_PROBE}" in decision.reasons


def test_search_before_exploration_counter_probe_failure_blocks_qualification() -> None:
    """Pin: this session's actual regression — search-first sat inert while exploring."""

    configuration = _config()
    results = list(_full_pass_set(configuration))
    for i, r in enumerate(results):
        if (
            r.run_class == RUN_CLASS_SAME_SESSION_RESUME
            and r.probe_kind == PROBE_KIND_COUNTER_PROBE
        ):
            results[i] = _result(
                result_id=r.result_id,
                run_class=r.run_class,
                probe_kind=r.probe_kind,
                scenario_id="kindex-search-before-exploration",
                configuration=configuration,
                passed=False,
                position=r.evaluated_position,
            )
    decision = decide_qualification(ROLE, tuple(results), current_configuration=configuration)
    assert decision.status == NOT_QUALIFIED
    assert any(
        reason.startswith(f"failed:{RUN_CLASS_SAME_SESSION_RESUME}:{PROBE_KIND_COUNTER_PROBE}")
        and "kindex-search-before-exploration" in reason
        for reason in decision.reasons
    )


def test_why_answered_causally_counter_probe_failure_blocks_qualification() -> None:
    """Pin: this session's other regression — 'why' answered with a fix, not a cause."""

    configuration = _config()
    results = list(_full_pass_set(configuration))
    for i, r in enumerate(results):
        if (
            r.run_class == RUN_CLASS_COMPACTION_BOUNDARY
            and r.probe_kind == PROBE_KIND_COUNTER_PROBE
        ):
            results[i] = _result(
                result_id=r.result_id,
                run_class=r.run_class,
                probe_kind=r.probe_kind,
                scenario_id="why-answered-causally",
                configuration=configuration,
                passed=False,
                position=r.evaluated_position,
            )
    decision = decide_qualification(ROLE, tuple(results), current_configuration=configuration)
    assert decision.status == NOT_QUALIFIED
    assert any("why-answered-causally" in reason for reason in decision.reasons)


def test_latest_position_wins_a_later_failure_revokes_an_earlier_pass() -> None:
    """Supersession by position: qualification is not 'ever passed', it is 'passes now'."""

    configuration = _config()
    base = _full_pass_set(configuration)
    target = next(
        r for r in base if r.run_class == RUN_CLASS_COLD and r.probe_kind == PROBE_KIND_PROBE
    )
    regression = _result(
        result_id="cold-probe-regression",
        run_class=RUN_CLASS_COLD,
        probe_kind=PROBE_KIND_PROBE,
        scenario_id=target.scenario_id,
        configuration=configuration,
        passed=False,
        position=target.evaluated_position + 1,
    )
    decision = decide_qualification(
        ROLE, base + (regression,), current_configuration=configuration
    )
    assert decision.status == NOT_QUALIFIED

    # And the reverse: a later PASS supersedes an earlier failure.
    recovery = _result(
        result_id="cold-probe-recovered",
        run_class=RUN_CLASS_COLD,
        probe_kind=PROBE_KIND_PROBE,
        scenario_id=target.scenario_id,
        configuration=configuration,
        passed=True,
        position=regression.evaluated_position + 1,
    )
    recovered = decide_qualification(
        ROLE, base + (regression, recovery), current_configuration=configuration
    )
    assert recovered.status == QUALIFIED


def test_configuration_drift_invalidates_every_prior_result() -> None:
    """The exact 'invalidate on configuration change' requirement."""

    old_configuration = _config()
    results = _full_pass_set(old_configuration)
    new_configuration = _config(model="claude-opus-5")
    decision = decide_qualification(ROLE, results, current_configuration=new_configuration)
    assert decision.status == NOT_QUALIFIED
    assert all(
        f"missing:{run_class}:{kind}" in decision.reasons
        for run_class in REQUIRED_RUN_CLASSES
        for kind in (PROBE_KIND_PROBE, PROBE_KIND_COUNTER_PROBE)
    )
    # Every admissible-but-wrong-configuration result is named, not silently dropped.
    assert any(reason.startswith("result-stale-configuration:") for reason in decision.reasons)


def test_directive_contract_change_is_also_configuration_drift() -> None:
    old_configuration = _config()
    results = _full_pass_set(old_configuration)
    new_configuration = _config(directive_contract_digest=digest_obj({"directives": ["v2"]}))
    decision = decide_qualification(ROLE, results, current_configuration=new_configuration)
    assert decision.status == NOT_QUALIFIED


def test_wrong_role_result_does_not_count_for_this_role() -> None:
    configuration = _config()
    results = tuple(
        replace(r, role="coder") if r.run_class == RUN_CLASS_COLD else r
        for r in _full_pass_set(configuration)
    )
    # Re-sign since role is part of the authority body.
    resigned = tuple(
        replace(r, evidence=_sign(r.authority_body())) if r.role != ROLE else r for r in results
    )
    decision = decide_qualification(ROLE, resigned, current_configuration=configuration)
    assert decision.status == NOT_QUALIFIED
    assert any(reason.startswith("result-wrong-role:") for reason in decision.reasons)


def test_tampered_evidence_is_a_hard_block() -> None:
    configuration = _config()
    results = list(_full_pass_set(configuration))
    results[0] = _result(
        result_id=results[0].result_id,
        run_class=results[0].run_class,
        probe_kind=results[0].probe_kind,
        scenario_id=results[0].scenario_id,
        configuration=configuration,
        passed=results[0].passed,
        position=results[0].evaluated_position,
        tampered=True,
    )
    decision = decide_qualification(ROLE, tuple(results), current_configuration=configuration)
    assert decision.status == NOT_QUALIFIED
    assert any(r.startswith("result-evidence-invalid:") for r in decision.reasons)


def test_malformed_construction_refuses_to_guess() -> None:
    configuration = _config()
    with pytest.raises(QualificationError):
        BehavioralProbeResult(
            result_id="x",
            role=ROLE,
            run_class="midnight",
            probe_kind=PROBE_KIND_PROBE,
            scenario_id="s",
            configuration=configuration,
            passed=True,
            evaluated_position=1,
        )
    with pytest.raises(QualificationError):
        BehavioralProbeResult(
            result_id="x",
            role=ROLE,
            run_class=RUN_CLASS_COLD,
            probe_kind="vibe-check",
            scenario_id="s",
            configuration=configuration,
            passed=True,
            evaluated_position=1,
        )
    with pytest.raises(QualificationError):
        ConfigurationBinding.from_dict({"model": "x"})


def test_decision_is_typed_and_round_trips() -> None:
    configuration = _config()
    decision = decide_qualification(
        ROLE, _full_pass_set(configuration), current_configuration=configuration
    )
    payload = decision.to_dict()
    assert payload["status"] == "qualified"
    assert payload["configuration_digest"] == configuration.content_digest
    assert len(payload["classes"]) == 4
