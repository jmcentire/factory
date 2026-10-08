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
