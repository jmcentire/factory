"""Contract-coverage tests — the pure path normalization, forward/reverse FE<->BE contract
diff, the data-driven excuse classifier, and the completeness status lattice + launch-readiness
predicate.

These prove the P2 extraction is correct AND target-agnostic:

  * normalization: concrete paths collapse to comparable templates (params, query, scheme,
    trailing slash) — pure string logic, no framework assumption;
  * forward diff: a caller edge with no provider is a break; a matching one is not; an
    unresolved edge is reported, never a match and never a break;
  * reverse diff: a provider with no caller is an orphan; the data-driven classifier buckets it
    excused (internal-by-design) vs. unexcused (user-facing gap); an unclassified orphan is a
    gap by default (fail-closed);
  * launch-readiness: red until every row is PROVED or EXCUSED; an empty inventory is not
    vacuously ready;
  * token check: the two modules + the fixtures name nothing target-specific.

Every fixture uses ABSTRACT inventories (svc_alpha, /api/widgets, client_home, row-N, …) — no
target/route/service/compliance vocabulary. All hermetic, stdlib + jsonschema only.
"""

from __future__ import annotations

import json
from pathlib import Path

from factory_core.completeness import (
    STATUS_DECLARED,
    STATUS_EXCUSED,
    STATUS_GAP,
    STATUS_PARTIAL,
    STATUS_PROVED,
    Inventory,
    InventoryRow,
    is_complete,
    launch_ready,
    meet,
    normalize_status,
    status_rank,
)
from factory_core.contract import (
    UNCLASSIFIED_LABEL,
    CallEdge,
    Endpoint,
    ExcuseClassifier,
    ExcuseRule,
    check_contract,
    forward_contract,
    normalize_method,
    normalize_path,
    reverse_contract,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures" / "contract"
CONTRACT_MODULE = REPO_ROOT / "factory_core" / "contract.py"
COMPLETENESS_MODULE = REPO_ROOT / "factory_core" / "completeness.py"

# The same denylist the purity guard applies to core .py files, read from the SAME data file
# (core_purity_denylist.json), extended here over the two new modules + their fixtures (the guard
# scans core .py, not tests/fixtures). On the generic, public core the token set is empty, so
# these checks are trivially green; a target that configures tokens gets them extended for free.
DENYLIST_TOKENS = tuple(
    json.loads((REPO_ROOT / "core_purity_denylist.json").read_text(encoding="utf-8"))
    .get("tokens", [])
)


def _load(name: str) -> list:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Path / method normalization
# --------------------------------------------------------------------------- #

def test_normalize_collapses_param_segments() -> None:
    assert normalize_path("/api/widgets/{id}") == "/api/widgets/{}"
    # A different param name compares equal after normalization.
    assert normalize_path("/api/widgets/{widgetId}") == normalize_path("/api/widgets/{id}")


def test_normalize_strips_query_fragment_and_trailing_slash() -> None:
    assert normalize_path("/api/x/?q=1#frag") == "/api/x"
    assert normalize_path("/api/x/") == "/api/x"
    assert normalize_path("/") == "/"
    assert normalize_path("") == "/"


def test_normalize_strips_scheme_and_host() -> None:
    assert normalize_path("https://host.invalid/api/x/{id}") == "/api/x/{}"
    assert normalize_path("http://h/api/y") == "/api/y"


def test_normalize_method_uppercases_and_trims() -> None:
    assert normalize_method(" get ") == "GET"
    assert normalize_method("Post") == "POST"


def test_endpoint_and_calledge_keys_compare_by_normalized_pair() -> None:
    ep = Endpoint(method="get", path="/api/widgets/{id}")
    edge = CallEdge(method="GET", path="/api/widgets/{wid}")
    assert ep.key == edge.key == ("GET", "/api/widgets/{}")


# --------------------------------------------------------------------------- #
# Forward contract diff
# --------------------------------------------------------------------------- #

def test_forward_break_when_caller_has_no_provider() -> None:
    endpoints = [Endpoint.from_dict(d) for d in _load("provider_operations.json")]
    calls = [CallEdge.from_dict(d) for d in _load("caller_edges.json")]
    report = forward_contract(calls, endpoints)
    assert report.ok is False
    # The publish call targets a path no provider serves -> exactly one break.
    assert len(report.breaks) == 1
    assert report.breaks[0].key == ("POST", "/api/widgets/{}/publish")
    # The empty-path caller is reported unresolved, never a break.
    assert len(report.unresolved) == 1
    assert report.unresolved[0].caller == "client_dynamic"


def test_forward_ok_when_every_caller_matches() -> None:
    endpoints = [
        Endpoint(method="GET", path="/api/x/{id}"),
        Endpoint(method="POST", path="/api/x"),
    ]
    calls = [
        CallEdge(method="GET", path="/api/x/{other}"),
        CallEdge(method="POST", path="/api/x/"),
    ]
    report = forward_contract(calls, endpoints)
    assert report.ok is True
    assert report.breaks == ()
    assert report.unresolved == ()


# --------------------------------------------------------------------------- #
# Reverse contract diff + data-driven classification
# --------------------------------------------------------------------------- #

def test_reverse_orphans_classified_both_ways_by_data_rules() -> None:
    endpoints = [Endpoint.from_dict(d) for d in _load("provider_operations.json")]
    calls = [CallEdge.from_dict(d) for d in _load("caller_edges.json")]
    classifier = ExcuseClassifier.from_dicts(_load("excuse_rules.json"))
    report = reverse_contract(endpoints, calls, classifier)

    by_path = {c.endpoint.key[1]: c for c in report.orphans}
    # Three providers have no caller.
    assert set(by_path) == {"/internal/reindex", "/api/hooks/inbound", "/api/orphaned"}
    # Two are excused (internal-by-design) by DATA rules; one is a surfaced user-facing gap.
    assert by_path["/internal/reindex"].excused is True
    assert by_path["/internal/reindex"].label == "internal-by-design"
    assert by_path["/api/hooks/inbound"].excused is True
    assert by_path["/api/hooks/inbound"].label == "inbound-hook"
    assert by_path["/api/orphaned"].excused is False
    assert by_path["/api/orphaned"].label == "surfaced-gap"

    # The residual is exactly the one user-facing gap; the contract is NOT ok.
    assert report.ok is False
    assert {c.endpoint.key[1] for c in report.unexcused} == {"/api/orphaned"}
    assert {c.endpoint.key[1] for c in report.excused} == {
        "/internal/reindex",
        "/api/hooks/inbound",
    }


def test_reverse_ok_when_every_orphan_is_excused() -> None:
    endpoints = [
        Endpoint(method="POST", path="/internal/a", raw_path="/internal/a"),
        Endpoint(method="GET", path="/api/used"),
    ]
    calls = [CallEdge(method="GET", path="/api/used")]
    classifier = ExcuseClassifier(
        rules=(ExcuseRule(label="internal", excused=True, path_prefix="/internal/"),)
    )
    report = reverse_contract(endpoints, calls, classifier)
    assert report.ok is True
    assert len(report.orphans) == 1
    assert report.orphans[0].excused is True


def test_unclassified_orphan_is_a_gap_by_default() -> None:
    # No classifier -> every orphan falls through to the fail-closed default.
    endpoints = [Endpoint(method="GET", path="/api/lonely")]
    report = reverse_contract(endpoints, calls=[], classifier=None)
    assert report.ok is False
    orphan = report.orphans[0]
    assert orphan.label == UNCLASSIFIED_LABEL
    assert orphan.excused is False


def test_excuse_rule_with_no_predicate_matches_nothing() -> None:
    # A blank rule cannot accidentally excuse everything.
    rule = ExcuseRule(label="blank", excused=True)
    assert rule.matches(Endpoint(method="GET", path="/anything")) is False


def test_first_matching_rule_wins_and_predicates_are_anded() -> None:
    ep = Endpoint(method="POST", path="/api/x", provider="svc_alpha", raw_path="/api/x",
                  tags=frozenset({"hook"}))
    classifier = ExcuseClassifier(rules=(
        # This rule requires BOTH provider AND method; it matches (order precedence).
        ExcuseRule(label="first", excused=True, providers=frozenset({"svc_alpha"}),
                   methods=frozenset({"POST"})),
        ExcuseRule(label="second", excused=False, tags=frozenset({"hook"})),
    ))
    assert classifier.classify(ep).label == "first"
    # A method-mismatched variant skips the first rule and matches the second.
    ep2 = Endpoint(method="GET", path="/api/x", provider="svc_alpha", raw_path="/api/x",
                   tags=frozenset({"hook"}))
    assert classifier.classify(ep2).label == "second"


def test_check_contract_runs_both_directions() -> None:
    endpoints = [Endpoint.from_dict(d) for d in _load("provider_operations.json")]
    calls = [CallEdge.from_dict(d) for d in _load("caller_edges.json")]
    classifier = ExcuseClassifier.from_dicts(_load("excuse_rules.json"))
    report = check_contract(calls, endpoints, classifier)
    # Forward has a break and reverse has an unexcused orphan -> not ok overall.
    assert report.ok is False
    assert report.forward.ok is False
    assert report.reverse.ok is False
    payload = report.to_dict()
    assert payload["ok"] is False
    assert payload["forward"]["breaks"]
    assert payload["reverse"]["unexcused"]


# --------------------------------------------------------------------------- #
# Completeness status lattice
# --------------------------------------------------------------------------- #

def test_status_lattice_order_and_completion() -> None:
    assert status_rank(STATUS_GAP) < status_rank(STATUS_PARTIAL) < status_rank(STATUS_DECLARED) \
        < status_rank(STATUS_PROVED)
    # DECLARED is a claim, not completion.
    assert is_complete(STATUS_DECLARED) is False
    assert is_complete(STATUS_PARTIAL) is False
    assert is_complete(STATUS_GAP) is False
    # Only PROVED and EXCUSED complete a row.
    assert is_complete(STATUS_PROVED) is True
    assert is_complete(STATUS_EXCUSED) is True


def test_unknown_status_is_fail_closed_to_gap() -> None:
    assert normalize_status("totally-made-up") == STATUS_GAP
    assert InventoryRow(id="r", status="nonsense").status == STATUS_GAP


def test_meet_takes_the_least_complete_dimension() -> None:
    assert meet(STATUS_PROVED, STATUS_PARTIAL) == STATUS_PARTIAL
    assert meet(STATUS_DECLARED, STATUS_GAP) == STATUS_GAP
    # EXCUSED meets down to the real operand (does not drag it up or down).
    assert meet(STATUS_EXCUSED, STATUS_PARTIAL) == STATUS_PARTIAL
    assert meet(STATUS_EXCUSED, STATUS_PROVED) == STATUS_PROVED


# --------------------------------------------------------------------------- #
# Launch-readiness predicate
# --------------------------------------------------------------------------- #

def test_launch_not_ready_lists_the_blocking_rows() -> None:
    inv = Inventory.from_dicts(_load("inventory_not_ready.json"))
    readiness = launch_ready(inv)
    assert readiness.ready is False
    # PROVED + EXCUSED complete; DECLARED, PARTIAL, GAP are blocking.
    blocking_ids = {row.id for row in readiness.blocking}
    assert blocking_ids == {"row-2", "row-3", "row-4"}
    assert readiness.complete == 2
    assert readiness.blocking_by_dimension == {"behavior": 1, "data-path": 1, "route": 1}


def test_launch_ready_when_every_row_proved_or_excused() -> None:
    inv = Inventory.from_dicts(_load("inventory_ready.json"))
    readiness = launch_ready(inv)
    assert readiness.ready is True
    assert readiness.blocking == ()
    assert readiness.complete == readiness.total == 5
    assert readiness.to_dict()["open"] == 0


def test_empty_inventory_is_not_vacuously_ready() -> None:
    inv = Inventory()
    assert launch_ready(inv).ready is False
    # Only when the caller explicitly opts into empty-is-complete.
    assert launch_ready(inv, require_nonempty=False).ready is True


def test_inventory_summary_counts_by_status_and_dimension() -> None:
    inv = Inventory.from_dicts(_load("inventory_not_ready.json"))
    summary = inv.summary()
    assert summary["total"] == 5
    assert summary["complete"] == 2
    assert summary["open"] == 3
    assert summary["by_status"][STATUS_PROVED] == 1
    assert summary["by_status"][STATUS_EXCUSED] == 1
    assert summary["by_dimension"]["behavior"]["total"] == 2
    assert summary["by_dimension"]["behavior"]["complete"] == 1


# --------------------------------------------------------------------------- #
# Target-agnosticism: the modules + fixtures name nothing target-specific
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


def test_modules_name_nothing_target_specific() -> None:
    for module_path in (CONTRACT_MODULE, COMPLETENESS_MODULE):
        runs = _runs(module_path.read_text(encoding="utf-8"))
        hits = [tok for tok in DENYLIST_TOKENS if tok in runs]
        assert hits == [], f"{module_path.name} must name nothing target-specific; found {hits}"


def test_fixtures_name_nothing_target_specific() -> None:
    for path in sorted(FIX.glob("*.json")):
        runs = _runs(path.read_text(encoding="utf-8"))
        hits = [tok for tok in DENYLIST_TOKENS if tok in runs]
        assert hits == [], f"{path.name} must name nothing target-specific; found {hits}"
