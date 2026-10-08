"""The Validator procedure states, before Phase A, what dispatch needs from the target."""

from pathlib import Path

PROCEDURE = (Path(__file__).resolve().parents[1] / "prompts" / "validate.md").read_text(
    encoding="utf-8"
)


def test_projection_config_requirement_is_stated_before_phase_a() -> None:
    phase_a = PROCEDURE.index("## Phase A \u2014 The frame")
    prerequisites = PROCEDURE.index("## Dispatch prerequisites")
    assert prerequisites < phase_a
    section = PROCEDURE[prerequisites:phase_a]
    assert ".factory/projection.conf" in section and "pinned commit" in section


def test_subscription_lane_runner_is_the_default_and_qualified_runner_opt_in() -> None:
    phase_a = PROCEDURE.index("## Phase A \u2014 The frame")
    section = PROCEDURE[PROCEDURE.index("## Dispatch prerequisites"):phase_a]
    assert "harness/tmux_lane.sh" in section and "default" in section
    assert "dispatch_lane.sh" in section and "opt-in" in section


def test_declared_dependencies_are_cached_read_only_before_dispatch() -> None:
    phase_b = PROCEDURE[PROCEDURE.index("## Phase B"):PROCEDURE.index("## Cadence")]
    cache = phase_b.index("Cache the declared dependencies")
    assert phase_b.index("Sign the run tool policy") < cache < phase_b.index("Dispatch the Coder")
    assert "read-only" in phase_b[cache:phase_b.index("Dispatch the Coder")]
