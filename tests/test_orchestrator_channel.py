from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

import harness.orchestrator_channel as orchestrator_channel
from harness.lane_dialogue import record_question
from harness.orchestrator_channel import (
    OrchestratorChannelError,
    append_activity,
    record_assessment,
    require_current,
    require_through,
)
from harness.run_guidance import assessment_state as guidance_assessment_state
from tests.test_run_guidance import guidance_fixture, write_complete_evidence


def resident_root(tmp_path: Path) -> Path:
    root = tmp_path / "run"
    root.mkdir()
    (root / "harness.json").write_text(
        json.dumps({"orchestrator_mode": "resident-monitoring", "status": "open"}),
        encoding="utf-8",
    )
    return root


def assessment(cursor: int, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": "factory-orchestrator-assessment/2",
        "through_cursor": cursor,
        "ultimate_goal": "Prove the Factory workflow, not merely produce code.",
        "current_action": "Monitor an ordinary Validator conversation delta.",
        "latest_input": "Continue the already specified four-seat run.",
        "latest_input_class": "intensity-change",
        "classified_because": "The input changes urgency but names no replacement method.",
        "direction_correct": True,
        "if_continued": "Independent agents produce reviewable evidence.",
        "side_effects": ["More coordination time before visible code."],
        "desirable_outcome": True,
        "advances_goal": True,
        "aligned": True,
        "adherence_findings": [],
        "task_complexity": "medium",
        "latent_ambiguity": "low",
        "requirements_considered": [
            "Preserve independent author lanes and a resident strategic supervisor."
        ],
        "complexity_hotspots": [],
        "planning_mode": "decompose",
        "specification_questions": [],
        "work_breakdown": ["Implement one bounded runtime slice."],
        "model_routing": ["Bounded mechanical slice -> lowest qualified model."],
        "causal_hypotheses": [],
        "outcome_discriminators": [],
        "dispatch_context_mode": "chunk-specific",
        "kindex_state_updates": ["9e6eb26bd101"],
        "recommended_strategy": "Continue the ratified lane method.",
        "judging_pass_state": "active",
        "observed_harness_status": "open",
        "run_state_basis": "harness.json is open; no Gate L close exists.",
        "outstanding_work": ["Complete and independently judge the current slice."],
        "decision": "no-op",
        "summary": "Direction and process remain aligned.",
        "kindex_status": "consulted",
        "kindex_context": ["34b8fe8ebcb6"],
        "kindex_basis": "Recovered the founder-specified intent-check fields and triggers.",
    }
    body.update(overrides)
    return body


def resident_guidance_root(tmp_path: Path) -> tuple[Path, Path]:
    root, artifacts, _admission = guidance_fixture(tmp_path)
    metadata = json.loads((root / "harness.json").read_text())
    metadata.update({"orchestrator_mode": "resident-monitoring", "status": "open"})
    (root / "harness.json").write_text(json.dumps(metadata), encoding="utf-8")
    return root, artifacts


def guidance_assessment(root: Path, cursor: int, **overrides: object) -> dict[str, object]:
    state = guidance_assessment_state(root, root / "artifacts")
    body = assessment(
        cursor,
        schema_version="factory-orchestrator-assessment/3",
        guidance_state=state["state"],
        guidance_selection_digest=state["selection_digest"],
        guidance_application_digest=state["application_digest"],
        guidance_evidence_digest=state["evidence_digest"],
        guidance_findings=state["findings"],
    )
    body.update(overrides)
    return body


def pressure_hotspot(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "requirement": "Preserve the existing multi-backend abstraction.",
        "provenance": "inherited-code",
        "complexity_effect": "disproportionate",
        "complexity_basis": (
            "Relaxing it removes the compatibility layer and changes the plan from "
            "three dependent chunks to one direct write-path change."
        ),
        "driver": "interaction",
        "interacts_with": ["Add atomic retries to the new write path."],
        "assumptions": ["Existing complexity is assumed to encode a current user need."],
        "simpler_path": "Implement one backend directly and delete the unused abstraction.",
        "disposition": "question-required",
        "basis": None,
        "clarifying_question": (
            "Must the inherited multi-backend abstraction remain, or is one backend sufficient?"
        ),
        "kindex_node_id": "ed9486f73f66",
    }
    body.update(overrides)
    return body


def test_every_activity_delta_has_a_monotonic_cursor_and_must_be_assessed(
    tmp_path: Path,
) -> None:
    root = resident_root(tmp_path)
    first = append_activity(
        root,
        kind="pane_delta",
        source="validator",
        detail="ordinary conversation with no anomaly keywords",
        snapshot="I am comparing two ordinary approaches.",
    )
    second = append_activity(
        root,
        kind="cadence",
        source="dispatcher",
        detail="independent strategic cadence",
    )

    assert (first, second) == (1, 2)
    rows = [
        json.loads(line)
        for line in (root / "orchestrator" / "activity.jsonl").read_text().splitlines()
    ]
    assert [row["cursor"] for row in rows] == [1, 2]
    assert rows[0]["snapshot"] == "I am comparing two ordinary approaches."

    record_assessment(root, assessment(1))
    with pytest.raises(OrchestratorChannelError, match="not current"):
        require_current(root)
    record_assessment(root, assessment(2, summary="Cadence and conversation remain aligned."))
    assert require_current(root) == (2, 2)


def test_divergent_or_nonadherent_assessment_cannot_report_noop(tmp_path: Path) -> None:
    root = resident_root(tmp_path)
    append_activity(
        root,
        kind="pane_delta",
        source="validator",
        detail="Validator picked up an author pen",
        snapshot="I wrote both implementation and tests myself.",
    )

    with pytest.raises(OrchestratorChannelError, match="must block"):
        record_assessment(
            root,
            assessment(
                1,
                direction_correct=False,
                advances_goal=False,
                aligned=False,
                adherence_findings=["The Validator collapsed the independent author lanes."],
                decision="no-op",
            ),
        )


def test_orchestrator_block_is_monotone_and_gates_the_validator(tmp_path: Path) -> None:
    root = resident_root(tmp_path)
    append_activity(
        root,
        kind="pre_dispatch",
        source="validator",
        detail="before dispatching coder",
    )
    record_assessment(
        root,
        assessment(
            1,
            direction_correct=False,
            desirable_outcome=False,
            advances_goal=False,
            aligned=False,
            adherence_findings=["Configured model assignment was silently replaced."],
            recommended_strategy="Restore the user-specified model and lane assignment.",
            decision="block",
            summary="Dispatch direction conflicts with the user's stated method.",
        ),
    )

    blocking = [
        json.loads(line)
        for line in (root / "lanes" / "validator.blocking").read_text().splitlines()
    ]
    assert blocking[0]["class"] == "orchestrator_response"
    assert blocking[0]["effect_route"] == "validator-blocking-only"
    assert require_current(root) == (1, 1)


def test_block_effect_is_durable_before_the_current_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = resident_root(tmp_path)
    append_activity(
        root,
        kind="pre_dispatch",
        source="validator",
        detail="before dispatching coder",
    )
    real_append = orchestrator_channel._append

    def crash_before_report(path: Path, row: object) -> None:
        if path.name == "reports.jsonl":
            raise OSError("simulated report publication crash")
        real_append(path, row)  # type: ignore[arg-type]

    monkeypatch.setattr(orchestrator_channel, "_append", crash_before_report)
    with pytest.raises(OSError, match="simulated report publication crash"):
        record_assessment(
            root,
            assessment(
                1,
                direction_correct=False,
                advances_goal=False,
                aligned=False,
                adherence_findings=["The Validator bypassed the author lanes."],
                decision="block",
                summary="Dispatch must stop before role separation is restored.",
            ),
        )

    assert (root / "lanes" / "validator.blocking").is_file()
    with pytest.raises(OrchestratorChannelError, match="not current"):
        require_current(root)


def test_orchestrator_assessment_schema_has_no_grant_effect(tmp_path: Path) -> None:
    root = resident_root(tmp_path)
    append_activity(
        root,
        kind="cadence",
        source="dispatcher",
        detail="independent strategic cadence",
    )
    body = assessment(1)
    body["grant"] = "promote"

    with pytest.raises(OrchestratorChannelError, match="unknown or missing"):
        record_assessment(root, body)


def test_direct_malformed_report_cannot_fake_a_current_orchestrator(tmp_path: Path) -> None:
    root = resident_root(tmp_path)
    append_activity(
        root,
        kind="cadence",
        source="dispatcher",
        detail="independent strategic cadence",
    )
    malformed = {"through_cursor": 1}
    canonical = json.dumps(malformed, sort_keys=True, separators=(",", ":")).encode()
    report = {
        "schema_version": "factory-orchestrator-report/1",
        "recorded_at": "2026-09-02T12:00:00+00:00",
        "assessment_digest": "sha256:" + hashlib.sha256(canonical).hexdigest(),
        "assessment": malformed,
    }
    (root / "orchestrator" / "reports.jsonl").write_text(
        json.dumps(report) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OrchestratorChannelError, match="unknown or missing"):
        require_current(root)


def test_orchestrator_cannot_counterfeit_run_closure_in_an_assessment(tmp_path: Path) -> None:
    root = resident_root(tmp_path)
    append_activity(
        root,
        kind="pre_verdict",
        source="validator",
        detail="blocking verdict with known failures",
    )

    with pytest.raises(OrchestratorChannelError, match="authoritative harness status"):
        record_assessment(
            root,
            assessment(
                1,
                observed_harness_status="closed",
                judging_pass_state="complete",
                summary="The judging pass ended with a blocking verdict.",
            ),
        )

    with pytest.raises(OrchestratorChannelError, match="open harness"):
        record_assessment(
            root,
            assessment(
                1,
                judging_pass_state="complete",
                summary="Run r1 is officially closed.",
            ),
        )


def test_planning_classifier_refuses_hidden_ambiguity_as_direct_work(tmp_path: Path) -> None:
    root = resident_root(tmp_path)
    append_activity(
        root,
        kind="pre_dispatch",
        source="validator",
        detail="classify task before dispatch",
    )

    with pytest.raises(OrchestratorChannelError, match="high latent ambiguity"):
        record_assessment(
            root,
            assessment(
                1,
                latent_ambiguity="high",
                planning_mode="direct",
                work_breakdown=[],
                model_routing=[],
            ),
        )

    with pytest.raises(OrchestratorChannelError, match="must block"):
        record_assessment(
            root,
            assessment(
                1,
                latent_ambiguity="high",
                planning_mode="clarify",
                specification_questions=["Which of the two valid semantics is intended?"],
                work_breakdown=[],
                model_routing=[],
                decision="no-op",
            ),
        )


def test_requirement_pressure_pass_questions_disproportionate_inherited_complexity(
    tmp_path: Path,
) -> None:
    root = resident_root(tmp_path)
    append_activity(
        root,
        kind="pre_dispatch",
        source="validator",
        detail="challenge requirement cost before decomposing the implementation",
    )
    question = "Must the inherited multi-backend abstraction remain, or is one backend sufficient?"
    hotspot = pressure_hotspot()

    with pytest.raises(OrchestratorChannelError, match="requires clarify planning mode"):
        record_assessment(
            root,
            assessment(
                1,
                task_complexity="high",
                complexity_hotspots=[hotspot],
                planning_mode="decompose",
                specification_questions=[question],
            ),
        )

    with pytest.raises(OrchestratorChannelError, match="must block"):
        record_assessment(
            root,
            assessment(
                1,
                task_complexity="high",
                latent_ambiguity="high",
                complexity_hotspots=[hotspot],
                planning_mode="clarify",
                specification_questions=[question],
                work_breakdown=[],
                model_routing=[],
                kindex_state_updates=["ed9486f73f66"],
                decision="no-op",
            ),
        )


def test_high_complexity_cannot_hide_all_requirement_pressure_points(tmp_path: Path) -> None:
    root = resident_root(tmp_path)
    append_activity(
        root,
        kind="pre_dispatch",
        source="validator",
        detail="classify a high-complexity task",
    )

    with pytest.raises(OrchestratorChannelError, match="pressure points"):
        record_assessment(root, assessment(1, task_complexity="high"))

    with pytest.raises(OrchestratorChannelError, match="complexity hotspot basis"):
        record_assessment(
            root,
            assessment(
                1,
                task_complexity="high",
                latent_ambiguity="high",
                complexity_hotspots=[pressure_hotspot(complexity_basis="")],
                planning_mode="clarify",
                specification_questions=[str(pressure_hotspot()["clarifying_question"])],
                work_breakdown=[],
                model_routing=[],
                kindex_state_updates=["ed9486f73f66"],
                decision="block",
            ),
        )


def test_decomposition_requires_written_kindex_state_and_falsifiable_discriminators(
    tmp_path: Path,
) -> None:
    root = resident_root(tmp_path)
    append_activity(
        root,
        kind="pre_dispatch",
        source="validator",
        detail="decompose and preserve causal branches",
    )

    with pytest.raises(OrchestratorChannelError, match="written to Kindex"):
        record_assessment(root, assessment(1, kindex_state_updates=[]))

    with pytest.raises(OrchestratorChannelError, match="outcome discriminators"):
        record_assessment(
            root,
            assessment(
                1,
                causal_hypotheses=[
                    "Recurrence means transmission; non-recurrence means enumeration."
                ],
                outcome_discriminators=[],
            ),
        )


def test_pending_lane_question_mechanically_forces_clarification_block(
    tmp_path: Path,
) -> None:
    root = resident_root(tmp_path)
    question = "What should hold do when the UBR already has a live row?"
    record_question(root, "coder", question)
    append_activity(
        root,
        kind="deterministic_signal",
        source="dispatcher",
        detail="lane question entered the typed channel",
    )

    with pytest.raises(OrchestratorChannelError, match="omits a pending"):
        record_assessment(root, assessment(1))

    report = record_assessment(
        root,
        assessment(
            1,
            latent_ambiguity="high",
            planning_mode="clarify",
            specification_questions=[question],
            work_breakdown=[],
            model_routing=[],
            decision="block",
            summary="A retained semantic question must be answered before work resumes.",
        ),
    )
    assert report["assessment"]["decision"] == "block"


def test_guidance_run_requires_assessment_three_bound_to_retained_state(tmp_path: Path) -> None:
    root, _artifacts = resident_guidance_root(tmp_path)
    append_activity(
        root,
        kind="cadence",
        source="dispatcher",
        detail="audit selected run guidance",
    )

    with pytest.raises(OrchestratorChannelError, match="requires assessment/3"):
        record_assessment(root, assessment(1))
    with pytest.raises(OrchestratorChannelError, match="differs from the retained"):
        record_assessment(
            root,
            guidance_assessment(root, 1, guidance_selection_digest="sha256:" + "f" * 64),
        )

    record_assessment(root, guidance_assessment(root, 1))
    assert require_current(root) == (1, 1)


def test_pending_guidance_cannot_cross_pre_dispatch(tmp_path: Path) -> None:
    root, artifacts = resident_guidance_root(tmp_path)
    (artifacts / "guidance" / "application.json").unlink()
    append_activity(
        root,
        kind="pre_dispatch",
        source="validator",
        detail="before dispatching a selected-guidance run",
    )
    record_assessment(root, guidance_assessment(root, 1))

    with pytest.raises(OrchestratorChannelError, match="not routing-verified"):
        require_through(root, 1)


def test_routing_verified_guidance_can_cross_pre_dispatch(tmp_path: Path) -> None:
    root, _artifacts = resident_guidance_root(tmp_path)
    append_activity(
        root,
        kind="pre_dispatch",
        source="validator",
        detail="before dispatching a selected-guidance run",
    )
    record_assessment(root, guidance_assessment(root, 1))

    assert require_through(root, 1) == 1


def test_guidance_evidence_and_fresh_assessment_are_required_before_verdict(
    tmp_path: Path,
) -> None:
    root, artifacts = resident_guidance_root(tmp_path)
    append_activity(
        root,
        kind="pre_verdict",
        source="validator",
        detail="before rendering a verdict",
    )
    record_assessment(root, guidance_assessment(root, 1))
    with pytest.raises(OrchestratorChannelError, match="not evidence-complete"):
        require_through(root, 1)

    write_complete_evidence(root, artifacts, "a" * 40)
    with pytest.raises(OrchestratorChannelError, match="stale against"):
        require_through(root, 1)

    append_activity(
        root,
        kind="deterministic_signal",
        source="validator",
        detail="guidance evidence became complete",
    )
    record_assessment(root, guidance_assessment(root, 2))
    assert require_through(root, 1) == 2


def test_noncompliant_guidance_assessment_must_block(tmp_path: Path) -> None:
    root, artifacts = resident_guidance_root(tmp_path)
    write_complete_evidence(root, artifacts, "a" * 40)
    evidence_path = artifacts / "guidance" / "evidence.json"
    evidence = json.loads(evidence_path.read_text())
    evidence["results"][1]["evidence"] = []
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    append_activity(
        root,
        kind="pre_verdict",
        source="validator",
        detail="invalid selected-guidance evidence",
    )

    with pytest.raises(OrchestratorChannelError, match="must block"):
        record_assessment(root, guidance_assessment(root, 1))

    report = record_assessment(root, guidance_assessment(root, 1, decision="block"))
    assert report["assessment"]["guidance_findings"] == ["guidance-evidence-invalid"]


def _run_root(tmp_path: Path) -> Path:
    root = tmp_path / ".factory" / "runs" / "r1"
    root.mkdir(parents=True)
    (root / "harness.json").write_text(
        json.dumps({"orchestrator_mode": "resident-monitoring", "status": "open"}),
        encoding="utf-8",
    )
    return root


def test_the_orchestrator_blocks_and_never_stops_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Founder, 2026-10-18 (P-0001): the run never stops waiting on anyone. The
    Orchestrator's effects are block or no-op; a block gates the Validator's next
    transition until the Validator disposes of it, and no window is killed."""
    root = _run_root(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        subprocess, "run", lambda command, **_kwargs: calls.append(list(command))
    )
    append_activity(
        root,
        kind="pane_delta",
        source="validator",
        detail="Validator overrode an adherence call",
        snapshot="I own this run; I will write the fix myself and skip the Tester.",
    )
    with pytest.raises(OrchestratorChannelError, match="block or no-op"):
        record_assessment(
            root,
            assessment(
                1,
                aligned=False,
                adherence_findings=["The Validator picked up the Coder's pen."],
                decision="halt",
                summary="stop",
            ),
        )
    record_assessment(
        root,
        assessment(
            1,
            direction_correct=False,
            advances_goal=False,
            aligned=False,
            adherence_findings=["The Validator ignored a block and picked up the Coder's pen."],
            decision="block",
            summary="Validator is acting as if it owns the run; hand the fix back to the Coder.",
        ),
    )
    blocking = (root / "lanes" / "validator.blocking").read_text()
    assert "orchestrator_response" in blocking
    assert calls == []
    assert not list(tmp_path.rglob("HALT"))


def test_a_stop_report_recorded_before_0_8_20_replays_as_a_block(tmp_path: Path) -> None:
    """A live run's journal may already hold the retired stop decision; the channel
    must keep replaying it, or upgrading the factory would break the run."""
    root = _run_root(tmp_path)
    append_activity(root, kind="cadence", source="dispatcher", detail="cadence")
    record_assessment(
        root,
        assessment(
            1,
            aligned=False,
            adherence_findings=["Validator negotiated past a gate."],
            decision="block",
            summary="blocked",
        ),
    )
    journal = root / "orchestrator" / "reports.jsonl"
    row = json.loads(journal.read_text().splitlines()[0])
    row["assessment"]["decision"] = "halt"
    row["assessment_digest"] = (
        "sha256:" + hashlib.sha256(orchestrator_channel._canonical(row["assessment"])).hexdigest()
    )
    journal.write_text(json.dumps(row) + "\n")
    append_activity(root, kind="cadence", source="dispatcher", detail="cadence")
    report = record_assessment(root, assessment(2))
    assert report["assessment"]["through_cursor"] == 2


# -- assessment/5: the code-and-progress review (founder direction 2026-10-16) ------------------


def progress_root(tmp_path: Path, every: int = 2) -> Path:
    root = tmp_path / "run"
    root.mkdir()
    (root / "harness.json").write_text(
        json.dumps({"orchestrator_mode": "resident-monitoring", "status": "open",
                    "progress_review": {"every_cadence_ticks": every}}),
        encoding="utf-8",
    )
    return root


def progress_review(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "evidence": ["lanes/coder/src/collector/adapter.ts:12-48", "commit:1a2b3c4d"],
        "requirements_progress": [
            {"requirement": "Every number shows its source and age.", "state": "in-progress",
             "evidence": ["lanes/coder/src/record.ts:5-30"]},
            {"requirement": "A failed scrape posts source-broken.", "state": "not-started",
             "evidence": []},
        ],
        "trend": "on-track",
        "trend_basis": "Two of five adapters committed in two ticks against a plan of one a tick.",
        "code_findings": [
            {"finding": "Adapter reads the clock directly; inject it to test staleness.",
             "refs": ["lanes/coder/src/collector/adapter.ts:20"],
             "signal_class": "at-your-convenience"},
        ],
    }
    body.update(overrides)
    return body


def progress_assessment(cursor: int, **overrides: object) -> dict[str, object]:
    body = assessment(
        cursor,
        schema_version="factory-orchestrator-assessment/5",
        guidance_state="none",
        guidance_selection_digest=None,
        guidance_application_digest=None,
        guidance_evidence_digest=None,
        guidance_findings=[],
        screen_review=None,
        signal_class="at-your-convenience",
        progress_review=progress_review(),
    )
    body.update(overrides)
    return body


def _cadence(root: Path) -> int:
    return append_activity(root, kind="cadence", source="dispatcher", detail="cadence tick")


def test_progress_run_refuses_an_assessment_without_the_review_schema(tmp_path: Path) -> None:
    root = progress_root(tmp_path)
    _cadence(root)
    with pytest.raises(OrchestratorChannelError, match="requires assessment/5"):
        record_assessment(root, assessment(1))


def test_review_is_due_every_second_tick_and_a_status_recap_is_refused(tmp_path: Path) -> None:
    root = progress_root(tmp_path)
    _cadence(root)
    # The first tick owes no review: a status-shaped assessment is recorded.
    record_assessment(root, progress_assessment(1, progress_review=None,
                                                signal_class="informative"))
    _cadence(root)
    with pytest.raises(OrchestratorChannelError, match="code-and-progress review is due"):
        record_assessment(root, progress_assessment(2, progress_review=None,
                                                    signal_class="informative"))
    record_assessment(root, progress_assessment(2))
    assert require_current(root) == (2, 2)
    # The count restarts after a review.
    _cadence(root)
    record_assessment(root, progress_assessment(3, progress_review=None,
                                                signal_class="informative"))


def test_a_slice_boundary_always_owes_the_review(tmp_path: Path) -> None:
    root = progress_root(tmp_path, every=8)
    append_activity(root, kind="pre_dispatch", source="validator", detail="dispatch slice 2")
    with pytest.raises(OrchestratorChannelError, match="review is due"):
        record_assessment(root, progress_assessment(1, progress_review=None,
                                                    signal_class="informative"))
    record_assessment(root, progress_assessment(1))


@pytest.mark.parametrize(
    "evidence",
    [
        [],
        ["orchestrator/activity.jsonl:12"],
        ["/runs/r1/tmux-lanes/coder-lane.log"],
        ["orchestrator/OUTSTANDING-WORK.md"],
        ["the coder says it finished the adapter"],
    ],
)
def test_review_evidence_must_be_the_work_not_status(tmp_path: Path, evidence: list[str]) -> None:
    root = progress_root(tmp_path)
    _cadence(root)
    _cadence(root)
    with pytest.raises(OrchestratorChannelError, match="evidence"):
        record_assessment(root, progress_assessment(2, progress_review=progress_review(
            evidence=evidence)))


def test_claimed_progress_needs_its_own_evidence(tmp_path: Path) -> None:
    root = progress_root(tmp_path)
    _cadence(root)
    _cadence(root)
    claimed = [{"requirement": "Adapters normalize records.", "state": "done", "evidence": []}]
    with pytest.raises(OrchestratorChannelError, match="requirements_progress evidence"):
        record_assessment(root, progress_assessment(2, progress_review=progress_review(
            requirements_progress=claimed)))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"signal_class": "loud"}, "signal_class must be one of"),
        ({"decision": "halt", "aligned": False, "adherence_findings": ["Validator wrote code."],
          "signal_class": "critical"}, "must be block or no-op"),
        ({"planning_mode": "clarify", "decision": "block",
          "specification_questions": ["Is Muse billing in scope for v1?"],
          "signal_class": "at-your-convenience"}, "below what it reports"),
        ({"decision": "block", "aligned": False, "signal_class": "informative"},
         "below what it reports"),
        ({"signal_class": "informative"}, "below what it reports"),  # its finding is higher
    ],
)
def test_signal_class_is_never_below_what_the_assessment_reports(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    root = progress_root(tmp_path)
    _cadence(root)
    _cadence(root)
    with pytest.raises(OrchestratorChannelError, match=message):
        record_assessment(root, progress_assessment(2, **overrides))


def test_a_block_may_be_classified_above_its_floor(tmp_path: Path) -> None:
    root = progress_root(tmp_path)
    _cadence(root)
    _cadence(root)
    report = record_assessment(root, progress_assessment(
        2, decision="block", aligned=False, signal_class="critical"))
    assert report["assessment"]["signal_class"] == "critical"


def test_legacy_runs_keep_accepting_assessment_three(tmp_path: Path) -> None:
    root = resident_root(tmp_path)
    _cadence(root)
    _cadence(root)
    record_assessment(root, assessment(2))
    assert orchestrator_channel.progress_review_due_now(root, 2) is None


def test_dispatcher_can_tell_the_orchestrator_a_review_is_due(tmp_path: Path) -> None:
    root = progress_root(tmp_path)
    _cadence(root)
    assert orchestrator_channel.progress_review_due_now(root, 1) is False
    _cadence(root)
    assert orchestrator_channel.progress_review_due_now(root, 2) is True
