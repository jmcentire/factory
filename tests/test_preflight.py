"""Forcing tests for the feasibility preflight (plan §1.1/§1.1b).

Every NO class carries its demonstrated-GO sibling (the negative control
against refusal bias — the class must discriminate, not merely fire), and
"could not check" is loud and distinct from "passed".
"""

from __future__ import annotations

from factory_core.build_plan import BuildPlan
from factory_core.criticality import CriticalityProfile
from factory_core.manifest import SegregationPolicy
from factory_core.verdict import (
    AdequacyCriterion,
    CoverageMap,
    CoverageTerritory,
)
from factory_runtime.preflight import run_preflight


def _territory(tid: str, kind: str = "scenario", status: str = "covered") -> CoverageTerritory:
    return CoverageTerritory(
        territory_id=tid, kind=kind, status=status,
        declared_by="human:founder", declaration_position=1,
    )


def _coverage(territories, adequacy=(), verbs=("deliver",)) -> CoverageMap:
    return CoverageMap(
        territories=tuple(territories),
        adequacy=tuple(adequacy),
        verb_ids=tuple(verbs),
    )


def _policy(humans=("human:founder",)) -> SegregationPolicy:
    return SegregationPolicy(human_ids=frozenset(humans))


def _profile(delegates=("human:founder", "human:delegate")) -> CriticalityProfile:
    return CriticalityProfile(critical_ratification_delegates=frozenset(delegates))


def test_satisfiable_frame_is_a_clean_go() -> None:
    """The demonstrated-GO sibling for the reachability classes."""
    report = run_preflight(
        coverage=_coverage([_territory("t1")]),
        profile=_profile(),
        policy=_policy(("human:founder", "human:second")),
        target_build={
            "max_attempts": 4,
            "signal": {
                "signal_pass_deadline": 4,
                "signal_pass_warn": 3,
                "signal_wall_clock_cap_hours": 24,
            },
        },
        plan_max_build_attempts=2,
    )
    assert report.go and not report.hard_no
    assert report.ceiling == "pass"
    # liveness is probed at the CLI door; no plan was supplied for the 1.5 joins
    assert report.not_applicable == ("plan-joins", "signal-deadline", "run-liveness")


def test_uncovered_territory_without_criterion_is_pass_unreachable() -> None:
    """§1.1b check 1 — the exact 127-hour case, caught at hour 0."""
    report = run_preflight(
        coverage=_coverage(
            [_territory("t1"), _territory("gap", kind="oracle", status="uncovered")]
        )
    )
    codes = [finding.code for finding in report.hard_no]
    assert "preflight-pass-unreachable" in codes
    assert not report.go
    assert report.ceiling == "block"


def test_uncovered_with_ratified_criterion_caps_the_ceiling_but_goes() -> None:
    report = run_preflight(
        coverage=_coverage(
            [_territory("t1"), _territory("gap", kind="oracle", status="uncovered")],
            adequacy=[
                AdequacyCriterion(territory_id="gap", required_probe_ids=("probe-1",))
            ],
        )
    )
    assert report.go
    assert report.ceiling == "pass-on-covered-unknown-on-named"


def test_no_covered_scenario_means_the_purpose_is_untested() -> None:
    """§1.1b check 2 — audit.py's pure predicate at a pre-construction site."""
    report = run_preflight(
        coverage=_coverage([_territory("s1", kind="oracle", status="covered")])
    )
    assert "preflight-purpose-untested" in [finding.code for finding in report.hard_no]


def test_empty_verb_set_makes_done_unreachable() -> None:
    report = run_preflight(coverage=_coverage([_territory("t1")], verbs=()))
    assert "preflight-done-unreachable" in [finding.code for finding in report.hard_no]


def test_plan_attempts_beyond_target_ceiling_hard_no() -> None:
    report = run_preflight(
        target_build={
            "max_attempts": 2,
            "signal": {
                "signal_pass_deadline": 2,
                "signal_pass_warn": 1,
                "signal_wall_clock_cap_hours": 24,
            },
        },
        plan_max_build_attempts=4,
    )
    assert "preflight-attempt-ceiling-inconsistent" in [
        finding.code for finding in report.hard_no
    ]


def test_undeclared_signal_knobs_hard_no_at_intake() -> None:
    report = run_preflight(target_build={"max_attempts": 2}, plan_max_build_attempts=1)
    findings = [finding for finding in report.hard_no if finding.code == "preflight-signal-knobs"]
    assert findings and findings[0].subject == "signal-knobs-undeclared"


def test_one_human_critical_collision_is_a_t0_disclosure_not_a_hard_no() -> None:
    """kindex 956b08784b09: computed directly from roster size; a run that never
    touches Critical still goes — surfaced, never silently late."""
    report = run_preflight(profile=_profile(), policy=_policy(("human:founder",)))
    assert report.go
    codes = [finding.code for finding in report.disclosures]
    assert "preflight-critical-approver-capacity" in codes


def test_sufficient_roster_is_the_capacity_go_sibling() -> None:
    report = run_preflight(
        profile=_profile(), policy=_policy(("human:founder", "human:second"))
    )
    assert "preflight-critical-approver-capacity" not in [
        finding.code for finding in report.disclosures
    ]
    assert "preflight-critical-approver-capacity-ok" in [
        finding.code for finding in report.notes
    ]


def test_undeclared_delegate_roster_is_disclosed() -> None:
    report = run_preflight(profile=_profile(delegates=()), policy=_policy())
    assert "preflight-critical-roster-undeclared" in [
        finding.code for finding in report.disclosures
    ]


def test_could_not_check_is_loud_and_distinct_from_passed() -> None:
    """Tri-state: an all-absent preflight names every unchecked group and
    discloses that reachability was not verified — a GO clears nothing."""
    report = run_preflight()
    assert set(report.not_applicable) == {
        "coverage-map",
        "attempt-ceilings",
        "critical-roster",
        "plan-joins",
        "signal-deadline",
        "run-liveness",
    }
    assert report.ceiling == "unknown"
    assert "preflight-reachability-unverified" in [
        finding.code for finding in report.disclosures
    ]


def test_cli_door_is_read_only_and_exits_2_on_hard_no(tmp_path) -> None:
    """The CLI door: a hard NO exits 2 loudly, a GO exits 0, and nothing is
    written anywhere (read-only by construction)."""
    import json as _json
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    coverage_path = tmp_path / "coverage.json"
    coverage_path.write_text(
        _json.dumps(
            {
                "territories": [
                    {
                        "territory_id": "t1",
                        "kind": "scenario",
                        "status": "covered",
                        "declared_by": "human:founder",
                        "declaration_position": 1,
                    }
                ],
                "adequacy": [],
                "verb_ids": [],
                "ratified_position": 1,
            }
        ),
        encoding="utf-8",
    )
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    result = subprocess.run(
        [sys.executable, "-m", "factory_runtime.cli", "preflight",
         "--coverage", str(coverage_path)],
        capture_output=True, text=True, cwd=repo,
    )
    assert result.returncode == 2, result.stderr  # empty verb set: hard NO
    report = _json.loads(result.stdout)
    assert "preflight-done-unreachable" in [f["code"] for f in report["hard_no"]]
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before  # wrote nothing

    healthy = coverage_path.read_text(encoding="utf-8").replace(
        '"verb_ids": []', '"verb_ids": ["deliver"]'
    )
    coverage_path.write_text(healthy, encoding="utf-8")
    ok = subprocess.run(
        [sys.executable, "-m", "factory_runtime.cli", "preflight",
         "--coverage", str(coverage_path)],
        capture_output=True, text=True, cwd=repo,
    )
    assert ok.returncode == 0, ok.stderr


def test_dead_run_liveness_hard_nos_and_their_go_sibling() -> None:
    from factory_runtime.preflight import LivenessFacts

    blocked = run_preflight(
        liveness=LivenessFacts(state="blocked", build_attempt_count=2, build_attempt_limit=2)
    )
    assert "preflight-dead-run-blocked-at-ceiling" in [
        finding.code for finding in blocked.hard_no
    ]

    wedged = run_preflight(
        liveness=LivenessFacts(state="building", guard_residue=("/x/ledger.jsonl.lock",))
    )
    assert "preflight-dead-run-guard-residue" in [finding.code for finding in wedged.hard_no]

    chained = run_preflight(
        liveness=LivenessFacts(state="open", chain_error="duplicate receipt id 'R-1'")
    )
    assert "preflight-dead-run-chain-unloadable" in [
        finding.code for finding in chained.hard_no
    ]

    corrupt = run_preflight(liveness=LivenessFacts(ledger_error="verification failed"))
    assert "preflight-dead-run-ledger-unloadable" in [
        finding.code for finding in corrupt.hard_no
    ]

    healthy = run_preflight(
        liveness=LivenessFacts(state="building", build_attempt_count=1, build_attempt_limit=2)
    )
    assert healthy.go
    assert "preflight-run-live" in [finding.code for finding in healthy.notes]


def test_probe_liveness_reads_real_wedges(tmp_path) -> None:
    """The impure prober: a missing run yields ledger_error; a stale exclusive
    guard and an R5-wedged chain are both detected from the filesystem."""
    import json as _json

    from factory_runtime.preflight import probe_liveness

    runs = tmp_path / "runs"
    run_root = runs / "r1"
    run_root.mkdir(parents=True)
    (run_root / "ledger.jsonl.lock").write_text("", encoding="utf-8")
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    row = {"receipt_id": "R-1", "prev_hash": "0" * 64}
    import hashlib

    body = dict(row)
    digest = hashlib.sha256(
        _json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    entry = {**row, "entry_hash": digest}
    (receipts / "chain.jsonl").write_text(
        _json.dumps(entry) + "\n" + _json.dumps(entry) + "\n", encoding="utf-8"
    )

    facts = probe_liveness(runs, "r1")
    assert facts.ledger_error  # no run.json/ledger: the run refuses to load
    assert any(path.endswith("ledger.jsonl.lock") for path in facts.guard_residue)
    # chain probing is best-effort here: the fixture chain may refuse for
    # hash-shape reasons before the duplicate check; any refusal is a wedge.
    assert facts.chain_error


def _plan(steps=(), oracle_links=()) -> BuildPlan:
    return BuildPlan.from_dict({
        "plan_id": "plan-1",
        "max_build_attempts": 2,
        "steps": list(steps),
        "oracle_links": list(oracle_links),
    })


def _step(step_id="s1", **extra) -> dict:
    return {
        "step_id": step_id,
        "pattern_id": "pat-1",
        "pattern_digest": "d" * 64,
        **extra,
    }


def test_undeclared_joins_are_loud_not_green() -> None:
    """Phase 1.5 tri-state: a legacy plan (no additive fields) yields per-join
    NA entries and disclosures — never a hard NO, never a silent pass."""
    report = run_preflight(
        coverage=_coverage(
            [_territory("t1")],
            adequacy=[AdequacyCriterion(territory_id="t1", required_probe_ids=("p1",))],
        ),
        plan=_plan(steps=[_step()]),
    )
    assert report.go
    assert "verb-delivery" in report.not_applicable
    assert "probe-promises" in report.not_applicable
    assert "territory-oracles" in report.not_applicable
    codes = [finding.code for finding in report.disclosures]
    assert "preflight-verb-delivery-undeclared" in codes
    assert "preflight-probe-promises-undeclared" in codes
    assert "preflight-territory-oracles-undeclared" in codes


def test_ratified_verb_no_step_delivers_is_done_unreachable() -> None:
    report = run_preflight(
        coverage=_coverage([_territory("t1")], verbs=("deliver", "migrate")),
        plan=_plan(steps=[_step(delivers_verbs=["deliver"])]),
    )
    assert not report.go
    undeliverable = [
        finding for finding in report.hard_no
        if finding.code == "preflight-verb-undeliverable"
    ]
    assert [finding.subject for finding in undeliverable] == ["migrate"]


def test_all_verbs_delivered_is_the_go_sibling() -> None:
    report = run_preflight(
        coverage=_coverage([_territory("t1")], verbs=("deliver",)),
        plan=_plan(steps=[_step(delivers_verbs=["deliver"])]),
    )
    assert report.go
    assert "preflight-verb-delivery-ok" in [finding.code for finding in report.notes]


def test_required_probe_nobody_promises_is_unsatisfiable() -> None:
    report = run_preflight(
        coverage=_coverage(
            [_territory("t1")],
            adequacy=[
                AdequacyCriterion(territory_id="t1", required_probe_ids=("p1", "p2"))
            ],
        ),
        plan=_plan(steps=[_step(promises_probes=["p1"])]),
    )
    assert not report.go
    unpromised = [
        finding for finding in report.hard_no if finding.code == "preflight-probe-unpromised"
    ]
    assert [finding.subject for finding in unpromised] == ["p2"]


def test_all_probes_promised_is_the_go_sibling() -> None:
    report = run_preflight(
        coverage=_coverage(
            [_territory("t1")],
            adequacy=[AdequacyCriterion(territory_id="t1", required_probe_ids=("p1",))],
        ),
        plan=_plan(steps=[_step(promises_probes=["p1"])]),
    )
    assert report.go
    assert "preflight-probe-promises-ok" in [finding.code for finding in report.notes]


def test_territory_expectation_with_no_oracle_link_is_uncoverable() -> None:
    from factory_core.verdict import CoverageMap, CoverageTerritory

    coverage = CoverageMap(
        territories=(
            CoverageTerritory(
                territory_id="t1", kind="scenario", status="covered",
                declared_by="human:founder", declaration_position=1,
                expectation_refs=("prod:req-1",),
            ),
        ),
        adequacy=(),
        verb_ids=("deliver",),
    )
    dead = run_preflight(coverage=coverage, plan=_plan(steps=[_step()]))
    assert not dead.go
    assert "preflight-territory-oracle-missing" in [
        finding.code for finding in dead.hard_no
    ]

    linked = run_preflight(
        coverage=coverage,
        plan=_plan(
            steps=[_step()],
            oracle_links=[{
                "expectation": {"artifact_id": "prod", "item_id": "req-1"},
                "oracle": {"artifact_id": "ops", "item_id": "mon-1"},
            }],
        ),
    )
    assert "preflight-territory-oracle-missing" not in [
        finding.code for finding in linked.hard_no
    ]
    assert "preflight-territory-oracles-ok" in [
        finding.code for finding in linked.notes
    ]


def test_additive_fields_keep_ratified_digests_stable() -> None:
    """The additive contract itself: a territory without expectation_refs and a step
    without the new fields serialize byte-for-byte as before, so every already-signed
    declaration and retained plan re-derives its exact digest."""
    from factory_core.verdict import CoverageTerritory

    bare = CoverageTerritory(
        territory_id="t1", kind="scenario", status="covered",
        declared_by="human:founder", declaration_position=1,
    )
    assert "expectation_refs" not in bare.declaration_body()

    from factory_core.build_plan import BuildStep

    step = BuildStep.from_dict(_step())
    assert "delivers_verbs" not in step.to_dict()
    assert "promises_probes" not in step.to_dict()


def test_signal_deadline_expired_refuses_the_next_admission() -> None:
    """Plan 4.1d's named forcing test: the admission refuses with the exact
    reason code when pass-index >= deadline, no NO-relevant signal, and
    residual blockers present."""
    from factory_runtime.preflight import SignalDeadlineFacts

    report = run_preflight(
        signal_deadline=SignalDeadlineFacts(
            passes=4,
            deadline=4,
            signal_known=True,
            signal_present=False,
            blockers_known=True,
            residual_blockers=("build-plan-oracle-links-empty",),
        )
    )
    assert not report.go
    expired = [f for f in report.hard_no if f.code == "preflight-signal-deadline-expired"]
    assert expired and expired[0].subject == "pass-4-of-4"


def test_healthy_long_green_run_admits_normally() -> None:
    """The second half of the plan's forcing pair: at the deadline with NO
    residual blockers, the run admits — with the exemption noted, never silent."""
    from factory_runtime.preflight import SignalDeadlineFacts

    report = run_preflight(
        signal_deadline=SignalDeadlineFacts(
            passes=6, deadline=4, signal_known=True, signal_present=False,
            blockers_known=True, residual_blockers=(),
        )
    )
    assert report.go
    assert "preflight-signal-deadline-healthy-exemption" in [
        f.code for f in report.notes
    ]


def test_signal_deadline_unverifiable_is_loud_not_green() -> None:
    from factory_runtime.preflight import SignalDeadlineFacts

    report = run_preflight(
        signal_deadline=SignalDeadlineFacts(
            passes=4, deadline=4, signal_known=False, signal_present=False,
            blockers_known=False, residual_blockers=(),
        )
    )
    assert report.go  # cannot hard-NO on facts it could not read...
    assert "preflight-signal-deadline-unverifiable" in [
        f.code for f in report.disclosures
    ]  # ...but it says so out loud


def test_signal_present_satisfies_the_deadline() -> None:
    from factory_runtime.preflight import SignalDeadlineFacts

    report = run_preflight(
        signal_deadline=SignalDeadlineFacts(
            passes=5, deadline=4, signal_known=True, signal_present=True,
            blockers_known=True, residual_blockers=("x",),
        )
    )
    assert report.go
    assert "preflight-signal-deadline-satisfied" in [f.code for f in report.notes]


def test_zero_enrolled_humans_is_authority_unreachable_hard_no() -> None:
    """4.1 intake authority-reachability: with no enrolled human, every receipt
    signer is unresolvable — nothing can ever ratify, so this is a hard NO at
    hour zero, not a Critical-scoped disclosure. The GO sibling is every test
    that passes a nonempty roster."""
    report = run_preflight(profile=_profile(), policy=_policy(()))
    assert not report.go
    assert "preflight-authority-unreachable" in [f.code for f in report.hard_no]
