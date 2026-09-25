"""The fidelity contract: build what was asked, and say so when you didn't.

Every probe here names the founder-reported failure it forbids (all 2026-09-24),
because a gate whose motivating failure is unrecorded drifts into decoration.
"""

from __future__ import annotations

import pytest

from factory_core import fidelity as fid


def deliverable(i: str = "d1", ref: str = "directive:1") -> fid.Deliverable:
    return fid.Deliverable(
        deliverable_id=i, directive_ref=ref, summary=f"the {i} the human asked for"
    )


def test_a_deliverable_nobody_built_is_not_cleared_by_adjacent_work() -> None:
    """ "Two and a half days of work to do everything BUT what I asked."

    A distribution service gained availability changes, pricing work and new
    internal APIs, and zero code to send what we have to a channel. Volume of
    adjacent output must not clear the one thing that was requested.
    """

    asked = [deliverable("send-to-channel"), deliverable("adapt-for-channel")]
    built = [
        fid.Attribution("pricing.py", "send-to-channel"),
        fid.Attribution("availability.py", "send-to-channel"),
    ]
    result = fid.render(
        asked,
        [
            fid.Assessment("send-to-channel", fid.DELIVERED),
            fid.Assessment("adapt-for-channel", fid.DELIVERED),
        ],
        built,
    )
    assert result.disposition == fid.FIDELITY_SCOPE_DRIFT
    assert result.unserved == ("adapt-for-channel",)
    assert not result.may_reach_done
    # And it says which one, so the answer is actionable rather than a mood.
    assert "adapt-for-channel" in result.reason


def test_work_nobody_asked_for_is_named_rather_than_counted_as_progress() -> None:
    asked = [deliverable("send-to-channel")]
    built = [
        fid.Attribution("channel_push.py", "send-to-channel"),
        fid.Attribution("new_internal_api.py"),  # attributed to nothing
        fid.Attribution("pricing_rework.py", "some-idea-of-my-own"),
    ]
    result = fid.render(asked, [fid.Assessment("send-to-channel", fid.DELIVERED)], built)
    assert result.disposition == fid.FIDELITY_SCOPE_DRIFT
    assert result.unasked == ("new_internal_api.py", "pricing_rework.py")


def test_a_bounded_fix_is_rework_not_a_risk_to_accept() -> None:
    """ "Not everything is: rearchitect the entire company or abandon this ticket."

    Risk acceptance exists for the structural case. A fix that fits in the ticket
    is work, and work is what the run is for.
    """

    asked = [deliverable("d1")]
    built = [fid.Attribution("thing.py", "d1")]
    result = fid.render(
        asked,
        [
            fid.Assessment(
                "d1", fid.PARTIAL, divergence="retries are missing", fix_scope=fid.BOUNDED
            )
        ],
        built,
    )
    assert result.disposition == fid.FIDELITY_REWORK
    assert not result.may_reach_done
    assert not fid.risk_acceptance_available(
        [fid.Assessment("d1", fid.PARTIAL, divergence="x", fix_scope=fid.BOUNDED)]
    )


def test_one_bounded_shortfall_removes_risk_acceptance_for_the_whole_run() -> None:
    """Otherwise a single structural item launders every bounded one beside it."""

    assessments = [
        fid.Assessment("d1", fid.DIVERGES, divergence="wrong transport", fix_scope=fid.STRUCTURAL),
        fid.Assessment("d2", fid.PARTIAL, divergence="no pagination", fix_scope=fid.BOUNDED),
    ]
    assert not fid.risk_acceptance_available(assessments)
    result = fid.render(
        [deliverable("d1"), deliverable("d2")],
        assessments,
        [fid.Attribution("a.py", "d1"), fid.Attribution("b.py", "d2")],
    )
    assert result.disposition == fid.FIDELITY_REWORK


def test_risk_acceptance_is_offered_only_when_every_shortfall_is_structural() -> None:
    asked = [deliverable("d1")]
    result = fid.render(
        asked,
        [
            fid.Assessment(
                "d1",
                fid.DIVERGES,
                divergence="needs the event bus we do not have",
                fix_scope=fid.STRUCTURAL,
            )
        ],
        [fid.Attribution("a.py", "d1")],
    )
    assert result.disposition == fid.FIDELITY_RISK_ACCEPTANCE_ELIGIBLE
    # Eligibility is a ticket to the human, never a self-issued permission to ship.
    assert not result.may_reach_done


def test_a_shortfall_must_name_the_divergence_and_cost_the_remedy() -> None:
    """An uncosted shortfall is how risk acceptance became a shrug."""

    with pytest.raises(fid.FidelityError):
        fid.Assessment("d1", fid.PARTIAL)  # no divergence
    with pytest.raises(fid.FidelityError):
        fid.Assessment("d1", fid.DIVERGES, divergence="it is bad")  # no fix_scope
    with pytest.raises(fid.FidelityError):
        fid.Assessment("d1", fid.NOT_ATTEMPTED, divergence="ran out of time", fix_scope="smallish")


def test_an_unassessed_deliverable_fails_closed() -> None:
    """ "It knows it's shit and delivers it anyway" — silence is not delivery."""

    asked = [deliverable("d1"), deliverable("d2")]
    built = [fid.Attribution("a.py", "d1"), fid.Attribution("b.py", "d2")]
    result = fid.render(asked, [fid.Assessment("d1", fid.DELIVERED)], built)
    assert result.disposition == fid.FIDELITY_UNASSESSED
    assert "d2" in result.reason
    assert not result.may_reach_done


def test_only_a_clean_delivery_may_reach_done() -> None:
    asked = [deliverable("d1")]
    ok = fid.render(asked, [fid.Assessment("d1", fid.DELIVERED)], [fid.Attribution("a.py", "d1")])
    assert ok.disposition == fid.FIDELITY_DELIVERS
    assert ok.may_reach_done
    for other in (
        fid.FIDELITY_REWORK,
        fid.FIDELITY_RISK_ACCEPTANCE_ELIGIBLE,
        fid.FIDELITY_SCOPE_DRIFT,
        fid.FIDELITY_UNASSESSED,
    ):
        assert not fid.Fidelity(other, "x").may_reach_done, other


def test_an_empty_enumeration_is_refused_never_vacuously_faithful() -> None:
    """With nothing enumerated, any work would pass. That is the whole failure."""

    with pytest.raises(fid.FidelityError):
        fid.render([], [])


def test_a_deliverable_needs_a_citation_to_the_request() -> None:
    """A deliverable nobody can trace to a request is the lane's own idea."""

    with pytest.raises(fid.FidelityError):
        fid.Deliverable(deliverable_id="d1", directive_ref="", summary="something I thought of")


def test_risk_acceptance_becoming_the_norm_is_itself_the_signal() -> None:
    """ "When 9 out of 10 are PASS_WITH_RISK_ACCEPTANCE then we're using it as a
    get out of jail free card rather than as it's intended."

    The O-ring rail: a baseline learned while chronically broken rounds a real
    outage down to ordinary. No individual run need be invalid for the rate to be.
    """

    nine_of_ten = [fid.FIDELITY_RISK_ACCEPTANCE_ELIGIBLE] * 9 + [fid.FIDELITY_DELIVERS]
    assert fid.risk_acceptance_is_systematic(nine_of_ten)
    mostly_clean = [fid.FIDELITY_DELIVERS] * 9 + [fid.FIDELITY_RISK_ACCEPTANCE_ELIGIBLE]
    assert not fid.risk_acceptance_is_systematic(mostly_clean)
    # No history is not evidence of health.
    assert not fid.risk_acceptance_is_systematic([])


def test_prose_is_not_an_input() -> None:
    """Mirrors verdict.py: confidence language cannot move the outcome.

    The same divergence text with a bounded cost and a structural cost must give
    different answers, and identical costs must give identical answers however
    the divergence is worded.
    """

    asked = [deliverable("d1")]
    built = [fid.Attribution("a.py", "d1")]

    def run(divergence: str, scope: str) -> str:
        return fid.render(
            asked,
            [fid.Assessment("d1", fid.PARTIAL, divergence=divergence, fix_scope=scope)],
            built,
        ).disposition

    assert run("trivial nit, ship it", fid.BOUNDED) == run(
        "catastrophic, will destroy production", fid.BOUNDED
    )
    assert run("same words", fid.BOUNDED) != run("same words", fid.STRUCTURAL)
