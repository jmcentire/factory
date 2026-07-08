"""Invariant-kernel tests — the pure IR, composition ledger, and reachability analyzer.

These prove the generic extraction is correct AND target-agnostic:

  * schema-load: a raw delta mapping is validated against the capability-delta JSON Schema
    before it parses into the IR (a malformed delta is rejected);
  * satisfied vs violated: the analyzer returns the standard result schema for both;
  * shortest-path correctness: the reported counterexample is the SHORTEST forbidden path,
    not merely *a* path;
  * composition: an in-flight delta that is legal alone composes with another legal delta
    into a violation the analyzer catches, naming both features;
  * fail-closed: an under-declared graph endpoint is reported ``unsupported`` for a
    fail-closed-degree invariant, never silently satisfied;
  * delta-fidelity: the generic declared-vs-observed diff;
  * token check: the new module + its fixtures name nothing target-specific.

Every fixture uses ABSTRACT roles/ids (origin, sink, store_x, hub_z, egress_y, role_a, …)
— never any target vocabulary. All hermetic, stdlib + the allowlisted jsonschema only.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from factory_core.invariant_kernel import (
    STATUS_SATISFIED,
    STATUS_UNSUPPORTED,
    STATUS_VIOLATED,
    CapabilityDelta,
    CapabilityFlow,
    CapabilityLedger,
    FidelityResult,
    GraphNode,
    InvariantKernel,
    ReachabilityAnalyzer,
    ReachabilityInvariant,
    SourceFacts,
    SourceFlowFact,
    analyze,
    check_delta_fidelity,
    load_delta,
    validate_delta_dict,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures" / "invariant_kernel"
MODULE_PATH = REPO_ROOT / "factory_core" / "invariant_kernel.py"

# The same denylist the purity guard applies to core .py files, read from the SAME data file
# (core_purity_denylist.json), extended here over the new module + its fixtures so the neutral
# surface cannot silently acquire a configured target token (the guard scans core .py, not
# tests/fixtures). On the generic, public core the token set is empty, so these checks are
# trivially green; a target that configures tokens gets the guarantee extended for free.
DENYLIST_TOKENS = tuple(
    json.loads((REPO_ROOT / "core_purity_denylist.json").read_text(encoding="utf-8"))
    .get("tokens", [])
)


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def kernel() -> InvariantKernel:
    return InvariantKernel.from_dict(_load("kernel.json"))


# --------------------------------------------------------------------------- #
# Schema-validated loading
# --------------------------------------------------------------------------- #

def test_load_delta_validates_and_parses() -> None:
    delta = load_delta(_load("delta_a.json"))
    assert isinstance(delta, CapabilityDelta)
    assert delta.id == "feat-a"
    assert delta.flows[0].source == "store_x"
    assert delta.flows[0].target == "hub_z"


def test_load_delta_rejects_malformed() -> None:
    # missing required "id"
    with pytest.raises(jsonschema.ValidationError):
        load_delta({"schema_version": "factory-capability-delta/1"})
    # bad degree in invariants_touched
    with pytest.raises(jsonschema.ValidationError):
        validate_delta_dict({
            "schema_version": "factory-capability-delta/1",
            "id": "x",
            "invariants_touched": [{"id": "L", "degree": "D9"}],
        })


def test_delta_roundtrips_through_dict() -> None:
    delta = load_delta(_load("delta_b.json"))
    again = CapabilityDelta.from_dict(delta.to_dict())
    assert again == delta


# --------------------------------------------------------------------------- #
# Satisfied vs violated (standard result schema for both)
# --------------------------------------------------------------------------- #

def test_satisfied_case_returns_clean_result(kernel: InvariantKernel) -> None:
    # Baseline + only feat-a (origin -> hub, no edge to sink) is legal.
    ledger = CapabilityLedger.from_dict(_load("ledger_satisfied.json"))
    result = analyze(kernel, ledger)
    assert result.status == STATUS_SATISFIED
    assert result.satisfied is True
    assert result.blocked is False
    assert result.violations == ()
    assert result.unsupported == ()
    # Both the real reachability law and the vacuous advisory law are decided.
    assert "INV-NO-ORIGIN-TO-SINK" in result.checked_invariants
    assert "INV-ADVISORY-VACUOUS" in result.checked_invariants
    # Standard schema shape is serializable.
    payload = result.to_dict()
    assert payload["status"] == STATUS_SATISFIED
    assert payload["backend"] == "internal_graph"


def test_direct_violation_returns_counterexample(kernel: InvariantKernel) -> None:
    # A single candidate delta that connects origin straight to sink.
    candidate = load_delta(_load("delta_direct_violation.json"))
    result = analyze(kernel, CapabilityLedger(), candidate)
    assert result.status == STATUS_VIOLATED
    assert result.blocked is True
    assert len(result.violations) == 1
    v = result.violations[0]
    assert v.invariant_id == "INV-NO-ORIGIN-TO-SINK"
    assert v.degree == "D0"
    assert v.path_nodes == ("store_x", "egress_y")
    assert v.features == ("feat-direct",)
    assert [(s.source, s.target) for s in v.trace] == [("store_x", "egress_y")]


# --------------------------------------------------------------------------- #
# Shortest-path correctness
# --------------------------------------------------------------------------- #

def test_reports_shortest_forbidden_path() -> None:
    # Two ways from origin to sink: a direct edge (len 1) and a 2-hop detour. The analyzer
    # must report the SHORTEST (the direct edge), not the detour.
    kern = InvariantKernel(
        nodes=(
            GraphNode(id="s", roles=frozenset({"origin"})),
            GraphNode(id="m", roles=frozenset()),
            GraphNode(id="t", roles=frozenset({"sink"})),
        ),
        invariants=(ReachabilityInvariant(
            id="L", degree="D0",
            start_roles=frozenset({"origin"}),
            forbidden_terminal_roles=frozenset({"sink"}),
        ),),
    )
    delta = CapabilityDelta(
        id="feat",
        flows=(
            CapabilityFlow(source="s", target="m", relation="a"),
            CapabilityFlow(source="m", target="t", relation="b"),
            CapabilityFlow(source="s", target="t", relation="c"),  # the short one
        ),
    )
    result = analyze(kern, CapabilityLedger(), delta)
    assert result.status == STATUS_VIOLATED
    v = result.violations[0]
    assert v.path_nodes == ("s", "t")
    assert len(v.trace) == 1


def test_shortest_path_when_only_detour_exists() -> None:
    # No direct edge; the only route is the 2-hop path, which must be reported in order.
    kern = InvariantKernel(
        nodes=(
            GraphNode(id="s", roles=frozenset({"origin"})),
            GraphNode(id="m", roles=frozenset()),
            GraphNode(id="t", roles=frozenset({"sink"})),
        ),
        invariants=(ReachabilityInvariant(
            id="L", degree="D0",
            start_roles=frozenset({"origin"}),
            forbidden_terminal_roles=frozenset({"sink"}),
        ),),
    )
    delta = CapabilityDelta(id="feat", flows=(
        CapabilityFlow(source="s", target="m"),
        CapabilityFlow(source="m", target="t"),
    ))
    v = analyze(kern, CapabilityLedger(), delta).violations[0]
    assert v.path_nodes == ("s", "m", "t")
    assert [(s.source, s.target) for s in v.trace] == [("s", "m"), ("m", "t")]


def test_forbidden_node_that_also_bears_start_role_is_caught() -> None:
    # Regression: a node bearing BOTH a start role and a forbidden-terminal role, reached via
    # >=1 flow, is a genuine violation. Because every start is settled at distance 0, an
    # edge-target that is also a start must still be tested as a terminal — otherwise the
    # only forbidden path is silently dropped and a D0 gate reports a false 'satisfied'.
    kern = InvariantKernel(
        nodes=(
            GraphNode(id="p", roles=frozenset({"origin"})),
            GraphNode(id="q", roles=frozenset({"origin", "sink"})),
        ),
        invariants=(ReachabilityInvariant(
            id="L", degree="D0",
            start_roles=frozenset({"origin"}),
            forbidden_terminal_roles=frozenset({"sink"}),
        ),),
    )
    delta = CapabilityDelta(id="feat", flows=(CapabilityFlow(source="p", target="q"),))
    result = analyze(kern, CapabilityLedger(), delta)
    assert result.status == STATUS_VIOLATED
    v = result.violations[0]
    assert v.path_nodes == ("p", "q")


def test_start_node_reaching_itself_via_self_loop_is_caught() -> None:
    # The tightest possible violation: one node bearing both roles with a self-loop. Reaching
    # the forbidden node via >=1 flow (a -> a) must be reported, not treated as the trivial
    # length-1 self path.
    kern = InvariantKernel(
        nodes=(GraphNode(id="a", roles=frozenset({"origin", "sink"})),),
        invariants=(ReachabilityInvariant(
            id="L", degree="D0",
            start_roles=frozenset({"origin"}),
            forbidden_terminal_roles=frozenset({"sink"}),
        ),),
    )
    delta = CapabilityDelta(id="feat", flows=(CapabilityFlow(source="a", target="a"),))
    result = analyze(kern, CapabilityLedger(), delta)
    assert result.status == STATUS_VIOLATED
    assert result.violations[0].path_nodes == ("a", "a")


# --------------------------------------------------------------------------- #
# Composition: legal alone, illegal composed
# --------------------------------------------------------------------------- #

def test_each_delta_legal_alone(kernel: InvariantKernel) -> None:
    a = load_delta(_load("delta_a.json"))  # store_x -> hub_z
    b = load_delta(_load("delta_b.json"))  # hub_z -> egress_y
    # A alone: origin reaches only the roleless hub. Legal.
    assert analyze(kernel, CapabilityLedger(), a).status == STATUS_SATISFIED
    # B alone: the edge is hub_z -> egress_y, but hub_z is not an origin and nothing
    # reaches it, so no origin->sink path. Legal.
    assert analyze(kernel, CapabilityLedger(), b).status == STATUS_SATISFIED


def test_composition_of_two_legal_deltas_violates(kernel: InvariantKernel) -> None:
    a = load_delta(_load("delta_a.json"))
    b = load_delta(_load("delta_b.json"))
    # A is shipped/in-flight; B is the candidate. Composed: store_x -> hub_z -> egress_y.
    ledger = CapabilityLedger().append(a)
    result = analyze(kernel, ledger, b)
    assert result.status == STATUS_VIOLATED
    v = result.violations[0]
    assert v.invariant_id == "INV-NO-ORIGIN-TO-SINK"
    assert v.path_nodes == ("store_x", "hub_z", "egress_y")
    # Both features are named in the counterexample, in path order.
    assert v.features == ("feat-a", "feat-b")


def test_cancelled_delta_leaves_tombstone_but_no_capability(kernel: InvariantKernel) -> None:
    a = load_delta(_load("delta_a.json"))
    b_raw = _load("delta_b.json")
    b_raw["stage"] = "cancelled"
    b_cancelled = CapabilityDelta.from_dict(b_raw)
    ledger = CapabilityLedger().append(a).append(b_cancelled)
    result = analyze(kernel, ledger)
    # The cancelled B contributes no active flow, so no origin->sink path composes.
    assert result.status == STATUS_SATISFIED
    # But its id remains in the replayed order as a tombstone.
    model = ledger.replay(kernel)
    assert "feat-b" in model.delta_order
    assert all(f.delta_id != "feat-b" for f in model.flows)


def test_ledger_append_is_pure() -> None:
    base = CapabilityLedger()
    once = base.append(CapabilityDelta(id="one"))
    assert base.in_flight == ()  # original untouched
    assert once.in_flight[0].id == "one"


# --------------------------------------------------------------------------- #
# Fail-closed: undeclared endpoint is unsupported, never silently satisfied
# --------------------------------------------------------------------------- #

def test_undeclared_endpoint_is_unsupported_for_fail_closed_degree() -> None:
    kern = InvariantKernel(
        nodes=(GraphNode(id="s", roles=frozenset({"origin"})),),
        invariants=(ReachabilityInvariant(
            id="L", degree="D0",
            start_roles=frozenset({"origin"}),
            forbidden_terminal_roles=frozenset({"sink"}),
        ),),
    )
    # The flow targets a node "ghost" that no node declares.
    delta = CapabilityDelta(id="feat", flows=(CapabilityFlow(source="s", target="ghost"),))
    result = analyze(kern, CapabilityLedger(), delta)
    assert result.status == STATUS_UNSUPPORTED
    assert result.blocked is True
    assert result.violations == ()
    assert result.unsupported[0].invariant_id == "L"
    assert result.unsupported[0].reason == "undeclared_graph_endpoint"
    assert "ghost" in result.unsupported[0].detail


def test_undeclared_endpoint_tolerated_for_advisory_degree() -> None:
    # A D4 (not fail-closed) invariant with an undeclared endpoint is still decided; since
    # the ghost bears no role, no forbidden path exists and it is satisfied.
    analyzer = ReachabilityAnalyzer()  # default fail_closed_degrees = {D0, D1}
    kern = InvariantKernel(
        nodes=(GraphNode(id="s", roles=frozenset({"origin"})),),
        invariants=(ReachabilityInvariant(
            id="L4", degree="D4",
            start_roles=frozenset({"origin"}),
            forbidden_terminal_roles=frozenset({"sink"}),
        ),),
    )
    delta = CapabilityDelta(id="feat", flows=(CapabilityFlow(source="s", target="ghost"),))
    model = CapabilityLedger().replay(kern, delta)
    result = analyzer.analyze(kern, model)
    assert result.status == STATUS_SATISFIED
    assert "L4" in result.checked_invariants


def test_vacuous_invariant_is_satisfied_never_skipped() -> None:
    kern = InvariantKernel(
        nodes=(GraphNode(id="s", roles=frozenset({"origin"})),),
        invariants=(ReachabilityInvariant(id="empty", degree="D0"),),  # no roles
    )
    result = analyze(kern, CapabilityLedger())
    assert result.status == STATUS_SATISFIED
    assert result.checked_invariants == ("empty",)


# --------------------------------------------------------------------------- #
# Source-derived delta-fidelity (generic diff; extraction stays behind a seam)
# --------------------------------------------------------------------------- #

def test_fidelity_pass_when_declared_matches_observed() -> None:
    delta = CapabilityDelta(id="feat", flows=(
        CapabilityFlow(source="role_a", target="store_x", relation="write"),
    ))
    facts = SourceFacts(flows=(
        SourceFlowFact(source="role_a", target="store_x", relation="write", location="f:1"),
    ))
    result = check_delta_fidelity(delta, facts)
    assert isinstance(result, FidelityResult)
    assert result.status == STATUS_SATISFIED
    assert result.blocked is False


def test_fidelity_flags_undeclared_and_missing_flows() -> None:
    delta = CapabilityDelta(id="feat", flows=(
        CapabilityFlow(source="role_a", target="store_x", relation="write"),
    ))
    facts = SourceFacts(flows=(
        # observed but not declared:
        SourceFlowFact(source="store_x", target="egress_y", relation="export", location="g:2"),
    ))
    result = check_delta_fidelity(delta, facts)
    assert result.status == STATUS_VIOLATED
    reasons = {(m.reason, m.source, m.target) for m in result.mismatches}
    assert ("source_flow_not_declared", "store_x", "egress_y") in reasons
    assert ("declared_flow_not_source_observed", "role_a", "store_x") in reasons


# --------------------------------------------------------------------------- #
# Target-agnosticism: the module + fixtures name nothing target-specific
# --------------------------------------------------------------------------- #

def _runs(text: str) -> set[str]:
    runs, token = set(), ""
    for ch in text.lower():
        if ch.isalnum():
            token += ch
        else:
            if token:
                runs.add(token)
            token = ""
    if token:
        runs.add(token)
    return runs


def test_module_names_nothing_target_specific() -> None:
    runs = _runs(MODULE_PATH.read_text(encoding="utf-8"))
    hits = [tok for tok in DENYLIST_TOKENS if tok in runs]
    assert hits == [], f"invariant_kernel.py must name nothing target-specific; found {hits}"


def test_fixtures_name_nothing_target_specific() -> None:
    for path in sorted(FIX.glob("*.json")):
        runs = _runs(path.read_text(encoding="utf-8"))
        hits = [tok for tok in DENYLIST_TOKENS if tok in runs]
        assert hits == [], f"{path.name} must name nothing target-specific; found {hits}"
