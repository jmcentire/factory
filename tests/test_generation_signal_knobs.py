"""Forcing tests for the signal-deadline knob validation (remediation plan §0.4a).

The three knobs are target-ABI data, mandatory at generation readiness, frozen
into the generation tuple through the target-manifest digest axis, and bounded
by the attempt ceiling — a target with max_attempts=2 must not carry dead
deadline code, and a mid-run re-sign that only raises the deadline must fail.
"""

from __future__ import annotations

from factory_runtime.generation import _signal_knob_issues


def _build(signal: object = None, max_attempts: int = 4) -> dict:
    build: dict = {
        "pattern_catalog_digest": "sha256:" + "a" * 64,
        "max_attempts": max_attempts,
        "construction_modes": ["regenerate"],
    }
    if signal is not None:
        build["signal"] = signal
    return build


def _knobs(deadline: int = 4, warn: int = 3, cap: float = 24.0) -> dict:
    return {
        "signal_pass_deadline": deadline,
        "signal_pass_warn": warn,
        "signal_wall_clock_cap_hours": cap,
    }


def test_declared_founder_default_knobs_are_clean() -> None:
    assert _signal_knob_issues(_build(_knobs()), None) == ()


def test_undeclared_knobs_refuse_readiness() -> None:
    """Mandatory at readiness — configurable never means disable-able."""
    assert _signal_knob_issues(_build(), None) == ("signal-knobs-undeclared",)


def test_invalid_knob_shapes_refuse() -> None:
    for bad in (
        _knobs(deadline=0),
        _knobs(warn=0),
        _knobs(cap=0),
        _knobs(cap=-1),
        {"signal_pass_deadline": True, "signal_pass_warn": 1, "signal_wall_clock_cap_hours": 24},
        {"signal_pass_deadline": 4},
    ):
        assert _signal_knob_issues(_build(bad), None) == ("signal-knobs-invalid",), bad


def test_deadline_beyond_attempt_ceiling_refuses() -> None:
    """signal_pass_deadline <= max_attempts — a target with max_attempts=2 must
    not carry dead deadline code (ratification consistency check)."""
    issues = _signal_knob_issues(_build(_knobs(deadline=4), max_attempts=2), None)
    assert "signal-pass-deadline-exceeds-max-attempts" in issues


def test_warn_beyond_deadline_refuses() -> None:
    issues = _signal_knob_issues(_build(_knobs(deadline=2, warn=3), max_attempts=4), None)
    assert "signal-warn-exceeds-deadline" in issues


def test_warn_equal_to_deadline_is_allowed() -> None:
    """A max_attempts=1 target can only declare deadline=1, warn=1."""
    assert _signal_knob_issues(_build(_knobs(deadline=1, warn=1), max_attempts=1), None) == ()


def test_deadline_raised_after_start_refuses() -> None:
    """Round-4 D3: the comparison operand is the DEADLINE frozen at attempt 1 —
    a re-sign that raises 2 -> 3 fires even inside the attempt ceiling (the
    old ceiling-based check under-fired here)."""
    issues = _signal_knob_issues(
        _build(_knobs(deadline=3), max_attempts=8), frozen_deadline=2
    )
    assert "deadline-knob-raised-after-start" in issues


def test_unchanged_deadline_never_wedges_a_legal_manifest() -> None:
    """Round-4 D3 over-fire regression: a manifest legal when frozen (deadline 4,
    ceiling 8) must stay clean at every re-prepare — the old check compared
    against the frozen ATTEMPT limit and permanently wedged this case."""
    issues = _signal_knob_issues(
        _build(_knobs(deadline=4), max_attempts=8), frozen_deadline=4
    )
    assert issues == ()


def test_lowered_deadline_is_clean() -> None:
    assert (
        _signal_knob_issues(_build(_knobs(deadline=2, warn=1)), frozen_deadline=4) == ()
    )


def test_infinite_wall_clock_cap_refuses() -> None:
    """Round-4 D4: TOML parses inf and it satisfied the bare positivity check —
    the mandatory backstop was disable-able by declaration. Finite or refused."""
    issues = _signal_knob_issues(
        _build(_knobs(cap=float("inf"))), None
    )
    assert issues == ("signal-knobs-invalid",)
