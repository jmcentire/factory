"""Optional discovery must not turn an uncollected or unnamed case into coverage.

Historical Phase-A/ledger/signing/cache prerequisites are retired. Their surviving
input and runtime constraints are tested in test_role_inputs, test_normal_workflow,
test_lane_controls and test_strategy_plan, rather than asserting old prompt headings.
"""

from harness import strategy_plan


def test_optional_named_case_discovery_does_not_credit_an_unnamed_case() -> None:
    assert strategy_plan.untested({"R7.1": "Critical"}, ["test_cap"])["Critical"] == ["R7.1"]
    assert strategy_plan.untested({"R7.1": "Critical"}, ["test_cap[R7.1]"])["Critical"] == []
