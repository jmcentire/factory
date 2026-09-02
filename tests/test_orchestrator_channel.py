from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import harness.orchestrator_channel as orchestrator_channel
from harness.lane_dialogue import record_question
from harness.orchestrator_channel import (
    OrchestratorChannelError,
    append_activity,
    record_assessment,
    require_current,
)


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
