"""factory_core.invariant_kernel — the neutral invariant IR, composition ledger, and a
built-in graph-reachability analyzer, all target-agnostic.

This is the generic extraction of the composition gate proven on the first consuming
target. It answers one dangerous question: *can a set of individually-safe capability
deltas compose into a state that reaches a forbidden configuration?* It does so over a
neutral intermediate representation whose entire concrete vocabulary — which roles exist,
which data classes exist, which invariant ids exist — is per-target **data** supplied by
the signed kernel, never fixed in this core.

What is generic (here) vs. what is per-target data:

  * Generic (this module): the IR dataclasses (parse-from-``dict`` / ``to_dict``, frozen),
    the append-only ``CapabilityLedger`` and its ``replay`` composition, a
    backend-neutral ``Analyzer`` protocol, and one built-in ``ReachabilityAnalyzer`` that
    decides parameterized "no path from a start-role node to a forbidden-terminal-role
    node" laws over the composed graph, returning the shortest forbidden path as the
    counterexample.
  * Per-target data (supplied through the kernel): the role labels a node bears, the
    relation and data-class labels on a flow, and each invariant's ``id`` + ``degree`` +
    ``start_roles`` + ``forbidden_terminal_roles``. The built-in D0-style information-flow
    law becomes "no path from any node bearing role R_start to any node bearing role
    R_forbidden," where R_start / R_forbidden are kernel-supplied strings — there are no
    hardcoded role, invariant, or annotation names anywhere in this file.

Posture (matching ``manifest.py``): stdlib only, side-effect free at import, no clock, no
disk-reading, no target contact. The *diff* logic for source-derived delta-fidelity is
generic and lives here; the *extraction* of observed flows from a target checkout is
impurity that lives behind ``factory_core.adapters.RepoAdapter`` — a target adapter reads
its own source and returns the neutral ``SourceFacts`` shape defined below. This module
never scans a target.

The capability-delta JSON Schema (``schemas/capability_delta.schema.json``) is the wire
contract validated on the way in; :func:`load_delta` runs a delta ``dict`` through it
before parsing.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

DELTA_SCHEMA_VERSION = "factory-capability-delta/1"

# Exception-tolerance degrees, mirroring the capability-delta schema enum. Kept as a tuple
# of opaque labels — the core does not attach target meaning to any of them; the kernel
# does. A backend may treat some degrees as fail-closed (see ReachabilityAnalyzer).
DEGREES: tuple[str, ...] = ("D0", "D1", "D2", "D3", "D4")

_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "capability_delta.schema.json"


def _as_tuple(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


def _clean_strs(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(s for s in (str(v).strip() for v in values) if s)


# --------------------------------------------------------------------------- #
# Neutral IR: nodes, flows, deltas
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class GraphNode:
    """A node in the capability graph: an actor, store, channel, egress target, or
    persistence location — identified only by an opaque id plus abstract role labels.

    The role vocabulary is per-target data; this core assigns no meaning to any role
    string. It only asks, later, whether a node bears a role the kernel named as a start
    or a forbidden-terminal role.
    """

    id: str
    roles: frozenset[str] = frozenset()
    description: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> GraphNode:
        return cls(
            id=str(raw.get("id", "")).strip(),
            roles=frozenset(_clean_strs(_as_tuple(raw.get("roles")))),
            description=str(raw.get("description", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id}
        if self.roles:
            out["roles"] = sorted(self.roles)
        if self.description:
            out["description"] = self.description
        return out


@dataclass(frozen=True)
class CapabilityFlow:
    """A declared directed capability transfer between two nodes.

    ``relation`` and ``data_class`` are abstract labels whose vocabulary is per-target
    data; the built-in reachability backend treats an edge as traversable regardless of
    label (a flow is a flow). A future backend may gate traversal on label — that is a
    backend policy, not a change to this IR.
    """

    source: str
    target: str
    relation: str = "flow"
    data_class: str = ""
    rationale: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CapabilityFlow:
        return cls(
            source=str(raw.get("source", "")).strip(),
            target=str(raw.get("target", "")).strip(),
            relation=str(raw.get("relation", "flow")).strip() or "flow",
            data_class=str(raw.get("data_class", "")).strip(),
            rationale=str(raw.get("rationale", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"source": self.source, "target": self.target,
                               "relation": self.relation}
        if self.data_class:
            out["data_class"] = self.data_class
        if self.rationale:
            out["rationale"] = self.rationale
        return out


@dataclass(frozen=True)
class CapabilityDelta:
    """A per-change capability delta in the invariant IR vocabulary.

    ``stage`` is where the delta sits in the composition ledger; ``cancelled`` leaves a
    tombstone but contributes no active nodes/flows to the composed model.
    """

    id: str
    summary: str = ""
    stage: str = "candidate"
    nodes: tuple[GraphNode, ...] = ()
    flows: tuple[CapabilityFlow, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CapabilityDelta:
        nodes = tuple(
            GraphNode.from_dict(n) for n in _as_tuple(raw.get("nodes")) if isinstance(n, Mapping)
        )
        flows = tuple(
            CapabilityFlow.from_dict(f)
            for f in _as_tuple(raw.get("flows"))
            if isinstance(f, Mapping)
        )
        return cls(
            id=str(raw.get("id", "")).strip(),
            summary=str(raw.get("summary", "")),
            stage=str(raw.get("stage", "candidate")).strip() or "candidate",
            nodes=nodes,
            flows=flows,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": DELTA_SCHEMA_VERSION,
            "id": self.id,
            "summary": self.summary,
            "stage": self.stage,
            "nodes": [n.to_dict() for n in self.nodes],
            "flows": [f.to_dict() for f in self.flows],
        }


# --------------------------------------------------------------------------- #
# Schema-validated loading
# --------------------------------------------------------------------------- #

def _load_schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_delta_dict(raw: Mapping[str, Any]) -> None:
    """Validate a raw delta mapping against the capability-delta JSON Schema.

    Raises ``jsonschema.ValidationError`` if the instance is malformed. The schema fixes
    only the *shape*; every concrete vocabulary term inside remains per-target data.
    """
    jsonschema.validate(instance=dict(raw), schema=_load_schema())


def load_delta(raw: Mapping[str, Any]) -> CapabilityDelta:
    """Validate a raw delta mapping against the schema, then parse it into the IR."""
    validate_delta_dict(raw)
    return CapabilityDelta.from_dict(raw)


# --------------------------------------------------------------------------- #
# The kernel: parameterized invariants (start/forbidden roles + degree as DATA)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ReachabilityInvariant:
    """A parameterized information-flow law: *no path may exist from any node bearing one
    of ``start_roles`` to any node bearing one of ``forbidden_terminal_roles``.*

    This is the exact generalization of a hardcoded "no restricted-store-to-uncontrolled-
    egress" law: the two role sets and the degree are supplied by the kernel as data, so this
    core names no specific role, no specific invariant, and no specific target. An invariant
    with empty start or forbidden roles is *vacuous* (nothing to violate) but still
    reported as decided/satisfied, never as a silent skip.
    """

    id: str
    start_roles: frozenset[str] = frozenset()
    forbidden_terminal_roles: frozenset[str] = frozenset()
    degree: str = "D0"
    description: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ReachabilityInvariant:
        degree = str(raw.get("degree", "D0")).strip() or "D0"
        return cls(
            id=str(raw.get("id", "")).strip(),
            start_roles=frozenset(_clean_strs(_as_tuple(raw.get("start_roles")))),
            forbidden_terminal_roles=frozenset(
                _clean_strs(_as_tuple(raw.get("forbidden_terminal_roles")))
            ),
            degree=degree,
            description=str(raw.get("description", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "degree": self.degree}
        if self.start_roles:
            out["start_roles"] = sorted(self.start_roles)
        if self.forbidden_terminal_roles:
            out["forbidden_terminal_roles"] = sorted(self.forbidden_terminal_roles)
        if self.description:
            out["description"] = self.description
        return out


@dataclass(frozen=True)
class InvariantKernel:
    """The signed, human-owned invariant vocabulary and active invariant set.

    Everything here is per-target data: the id, version, the baseline nodes the platform
    already declares, and the reachability invariants (each with its own start/forbidden
    roles + degree). The core supplies the analyzer; the kernel supplies the laws.
    """

    id: str = ""
    version: str = "0"
    nodes: tuple[GraphNode, ...] = ()
    invariants: tuple[ReachabilityInvariant, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> InvariantKernel:
        nodes = tuple(
            GraphNode.from_dict(n) for n in _as_tuple(raw.get("nodes")) if isinstance(n, Mapping)
        )
        invariants = tuple(
            ReachabilityInvariant.from_dict(i)
            for i in _as_tuple(raw.get("invariants"))
            if isinstance(i, Mapping)
        )
        return cls(
            id=str(raw.get("id", "")).strip(),
            version=str(raw.get("version", "0")),
            nodes=nodes,
            invariants=invariants,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "nodes": [n.to_dict() for n in self.nodes],
            "invariants": [i.to_dict() for i in self.invariants],
        }


# --------------------------------------------------------------------------- #
# Composition: the append-only ledger and the composed model
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class LedgerFlow:
    """A composed flow, annotated with the delta that introduced it (for provenance and
    for naming which features participate in a counterexample)."""

    source: str
    target: str
    relation: str
    delta_id: str
    delta_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"source": self.source, "target": self.target,
                               "relation": self.relation, "delta_id": self.delta_id}
        if self.delta_summary:
            out["delta_summary"] = self.delta_summary
        return out


@dataclass(frozen=True)
class ComposedModel:
    """The union of the kernel baseline + every active delta (shipped + in-flight +
    candidate), with nodes keyed by id and flows carrying their originating delta id."""

    nodes: dict[str, GraphNode]
    flows: tuple[LedgerFlow, ...]
    delta_order: tuple[str, ...]

    def unknown_endpoints(self) -> tuple[str, ...]:
        """Flow endpoints that no node declares. The analyzer treats these as undecidable
        (unsupported), never as safe — an under-declared graph must not read as legal."""
        known = set(self.nodes)
        unknown: set[str] = set()
        for flow in self.flows:
            if flow.source not in known:
                unknown.add(flow.source)
            if flow.target not in known:
                unknown.add(flow.target)
        return tuple(sorted(unknown))

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [self.nodes[k].to_dict() for k in sorted(self.nodes)],
            "flows": [f.to_dict() for f in self.flows],
            "delta_order": list(self.delta_order),
        }


@dataclass(frozen=True)
class CapabilityLedger:
    """Append-only shipped + in-flight capability deltas.

    ``replay`` composes the kernel baseline with the ledger and an optional candidate into
    a single ``ComposedModel``. Later deltas re-declaring a node id override earlier ones;
    ``cancelled`` deltas leave a tombstone in ``delta_order`` but contribute no active
    nodes or flows, so a cancelled spec stops authorizing its path.
    """

    shipped: tuple[CapabilityDelta, ...] = ()
    in_flight: tuple[CapabilityDelta, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | Sequence[Any]) -> CapabilityLedger:
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, Mapping)):
            return cls(shipped=tuple(
                CapabilityDelta.from_dict(d) for d in raw if isinstance(d, Mapping)
            ))
        assert isinstance(raw, Mapping)
        shipped = tuple(
            CapabilityDelta.from_dict(d) for d in _as_tuple(raw.get("shipped"))
            if isinstance(d, Mapping)
        )
        in_flight = tuple(
            CapabilityDelta.from_dict(d) for d in _as_tuple(raw.get("in_flight"))
            if isinstance(d, Mapping)
        )
        return cls(shipped=shipped, in_flight=in_flight)

    def append(self, delta: CapabilityDelta) -> CapabilityLedger:
        """Return a new ledger with ``delta`` appended to the in-flight set (append-only:
        the original is never mutated)."""
        return CapabilityLedger(shipped=self.shipped, in_flight=self.in_flight + (delta,))

    def deltas(self, candidate: CapabilityDelta | None = None) -> tuple[CapabilityDelta, ...]:
        out = self.shipped + self.in_flight
        return out + ((candidate,) if candidate is not None else ())

    def replay(
        self, kernel: InvariantKernel, candidate: CapabilityDelta | None = None
    ) -> ComposedModel:
        nodes: dict[str, GraphNode] = {n.id: n for n in kernel.nodes}
        flows: list[LedgerFlow] = []
        order: list[str] = []
        for delta in self.deltas(candidate):
            order.append(delta.id)
            if delta.stage == "cancelled":
                continue  # tombstone: contributes no active capability
            for node in delta.nodes:
                nodes[node.id] = node
            for flow in delta.flows:
                flows.append(LedgerFlow(
                    source=flow.source,
                    target=flow.target,
                    relation=flow.relation,
                    delta_id=delta.id,
                    delta_summary=delta.summary,
                ))
        return ComposedModel(nodes=nodes, flows=tuple(flows), delta_order=tuple(order))


# --------------------------------------------------------------------------- #
# Analyzer result schema (the standard shape every backend returns)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class TraceStep:
    source: str
    target: str
    relation: str
    delta_id: str

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "target": self.target,
                "relation": self.relation, "delta_id": self.delta_id}


@dataclass(frozen=True)
class Violation:
    """A counterexample: which invariant failed, which features (delta ids) participate,
    and the SHORTEST forbidden path as an ordered list of trace steps."""

    invariant_id: str
    degree: str
    features: tuple[str, ...]
    path_nodes: tuple[str, ...]
    trace: tuple[TraceStep, ...]
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "invariant_id": self.invariant_id,
            "degree": self.degree,
            "features": list(self.features),
            "path_nodes": list(self.path_nodes),
            "trace": [s.to_dict() for s in self.trace],
            "message": self.message,
        }


@dataclass(frozen=True)
class Unsupported:
    """An invariant the backend could not decide. Reported explicitly so an undecidable
    invariant never silently becomes a pass."""

    invariant_id: str
    reason: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"invariant_id": self.invariant_id, "reason": self.reason}
        if self.detail:
            out["detail"] = self.detail
        return out


# Result statuses. "satisfied" = every checked invariant holds; "violated" = at least one
# counterexample; "unsupported" = the backend could not decide at least one invariant and
# found no violation among the ones it could.
STATUS_SATISFIED = "satisfied"
STATUS_VIOLATED = "violated"
STATUS_UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class AnalysisResult:
    status: str
    backend: str = ""
    checked_invariants: tuple[str, ...] = ()
    violations: tuple[Violation, ...] = ()
    unsupported: tuple[Unsupported, ...] = ()

    @property
    def satisfied(self) -> bool:
        return self.status == STATUS_SATISFIED

    @property
    def blocked(self) -> bool:
        return self.status != STATUS_SATISFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "backend": self.backend,
            "checked_invariants": list(self.checked_invariants),
            "violations": [v.to_dict() for v in self.violations],
            "unsupported": [u.to_dict() for u in self.unsupported],
        }


# --------------------------------------------------------------------------- #
# Backend-neutral analyzer protocol + the built-in reachability backend
# --------------------------------------------------------------------------- #

class Analyzer:
    """Backend-neutral analyzer contract.

    A backend takes the composed model + the kernel's invariants and returns the standard
    ``AnalysisResult``. It MUST report ``unsupported`` for any invariant it cannot decide
    and MUST NOT turn a parser/solver error into a false ``satisfied``. Subclasses (or any
    structural implementer) provide :meth:`analyze`.
    """

    name = "abstract"

    def analyze(
        self, kernel: InvariantKernel, model: ComposedModel
    ) -> AnalysisResult:  # pragma: no cover - interface
        raise NotImplementedError


def _shortest_forbidden_path(
    model: ComposedModel, invariant: ReachabilityInvariant
) -> tuple[str, ...] | None:
    """BFS over the composed flow graph. Returns the shortest node-id path (by edge count)
    from any node bearing a start role to any node bearing a forbidden-terminal role, or
    ``None`` if no such path exists.

    Ties (equal-length paths) are broken deterministically: starts are considered in
    sorted id order, and each node's out-edges are traversed in sorted (target, delta_id,
    relation) order, so the returned path is stable across runs.
    """
    if not invariant.start_roles or not invariant.forbidden_terminal_roles:
        return None  # vacuous law: nothing can violate it

    adjacency: dict[str, list[LedgerFlow]] = {}
    for flow in model.flows:
        adjacency.setdefault(flow.source, []).append(flow)
    for edges in adjacency.values():
        edges.sort(key=lambda f: (f.target, f.delta_id, f.relation))

    def bears(node_id: str, roles: frozenset[str]) -> bool:
        node = model.nodes.get(node_id)
        return node is not None and bool(node.roles & roles)

    starts = sorted(
        nid for nid in model.nodes if bears(nid, invariant.start_roles)
    )
    # A single BFS from a virtual super-source: enqueue all starts at distance 0 in sorted
    # order. A law is about reaching a forbidden node via >=1 flow, so a start node is never
    # a violation by itself (the trivial length-1 path) — but it MUST still be reachable as
    # the *target* of an edge from another start. The terminal test therefore fires at
    # edge-traversal time, before the visited guard can suppress a forbidden node that also
    # happens to bear a start role (and so was seeded at distance 0). Testing on arrival is
    # sound for shortest-path: BFS reaches every node by its shortest path first.
    queue: deque[tuple[str, tuple[str, ...]]] = deque()
    seen: set[str] = set(starts)  # starts are settled at distance 0; never re-enqueue them

    def expand(node_id: str, path: tuple[str, ...]) -> tuple[str, ...] | None:
        for flow in adjacency.get(node_id, []):
            reached = flow.target
            reached_path = path + (reached,)
            # A path of length >=1 flow that lands on a forbidden-terminal node is the
            # counterexample — tested here so an already-seen (e.g. also-a-start) forbidden
            # node is not skipped.
            if bears(reached, invariant.forbidden_terminal_roles):
                return reached_path
            if reached in seen:
                continue
            seen.add(reached)
            queue.append((reached, reached_path))
        return None

    for start in starts:
        hit = expand(start, (start,))
        if hit is not None:
            return hit
    while queue:
        node_id, path = queue.popleft()
        hit = expand(node_id, path)
        if hit is not None:
            return hit
    return None


def _trace_for_path(model: ComposedModel, path: tuple[str, ...]) -> tuple[TraceStep, ...]:
    """Turn a node-id path into ordered trace steps, choosing, for each hop, the edge with
    the smallest (delta_id, relation) so the trace is deterministic."""
    by_pair: dict[tuple[str, str], LedgerFlow] = {}
    for flow in model.flows:
        key = (flow.source, flow.target)
        current = by_pair.get(key)
        if current is None or (flow.delta_id, flow.relation) < (current.delta_id, current.relation):
            by_pair[key] = flow
    steps: list[TraceStep] = []
    for src, dst in zip(path, path[1:], strict=False):
        hop = by_pair.get((src, dst))
        if hop is None:  # pragma: no cover - path came from the same graph
            continue
        steps.append(TraceStep(source=src, target=dst, relation=hop.relation,
                               delta_id=hop.delta_id))
    return tuple(steps)


@dataclass(frozen=True)
class ReachabilityAnalyzer(Analyzer):
    """The built-in internal graph/reachability backend.

    It decides every :class:`ReachabilityInvariant` in the kernel: for each, it searches
    for a path from a start-role node to a forbidden-terminal-role node in the composed
    graph and, if found, returns the SHORTEST such path as the counterexample naming the
    invariant id, the features (delta ids) on the path, and the trace.

    Fail-closed: if a fail-closed-degree invariant's flow graph has endpoints no node
    declares, that invariant is reported ``unsupported`` (undecidable) rather than
    silently satisfied. ``fail_closed_degrees`` is a backend policy parameter (the kernel
    still owns each invariant's degree); by default D0 and D1 are fail-closed.
    """

    name: str = "internal_graph"
    fail_closed_degrees: frozenset[str] = field(default_factory=lambda: frozenset({"D0", "D1"}))

    def analyze(self, kernel: InvariantKernel, model: ComposedModel) -> AnalysisResult:
        checked: list[str] = []
        violations: list[Violation] = []
        unsupported: list[Unsupported] = []
        unknown = model.unknown_endpoints()

        for inv in kernel.invariants:
            # Undecidable under-declaration: a fail-closed invariant whose graph touches an
            # endpoint no node declares cannot be safely decided.
            if unknown and inv.degree in self.fail_closed_degrees:
                unsupported.append(Unsupported(
                    invariant_id=inv.id,
                    reason="undeclared_graph_endpoint",
                    detail=", ".join(unknown),
                ))
                continue
            checked.append(inv.id)
            path = _shortest_forbidden_path(model, inv)
            if path is None:
                continue
            trace = _trace_for_path(model, path)
            features = tuple(dict.fromkeys(s.delta_id for s in trace))  # ordered-unique
            violations.append(Violation(
                invariant_id=inv.id,
                degree=inv.degree,
                features=features,
                path_nodes=path,
                trace=trace,
                message=(
                    f"{inv.id}: a path of length {len(trace)} reaches a forbidden-terminal "
                    f"node from a start-role node ({' -> '.join(path)})."
                ),
            ))

        if violations:
            status = STATUS_VIOLATED
        elif unsupported:
            status = STATUS_UNSUPPORTED
        else:
            status = STATUS_SATISFIED
        return AnalysisResult(
            status=status,
            backend=self.name,
            checked_invariants=tuple(checked),
            violations=tuple(violations),
            unsupported=tuple(unsupported),
        )


def analyze(
    kernel: InvariantKernel,
    ledger: CapabilityLedger,
    candidate: CapabilityDelta | None = None,
    analyzer: Analyzer | None = None,
) -> AnalysisResult:
    """Compose kernel baseline + shipped + in-flight + candidate, then run the analyzer.

    The default analyzer is the built-in :class:`ReachabilityAnalyzer`. Any structural
    implementer of :class:`Analyzer` may be passed to swap in a stronger backend without
    changing the IR or the result schema.
    """
    model = ledger.replay(kernel, candidate)
    backend = analyzer if analyzer is not None else ReachabilityAnalyzer()
    return backend.analyze(kernel, model)


# --------------------------------------------------------------------------- #
# Source-derived delta-fidelity: neutral shape + generic diff (extraction is a seam)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SourceFlowFact:
    """One observed flow recovered from a target's source/infra by a target-side adapter.

    The *extraction* (scanning a checkout, parsing annotations or infra definitions) is
    impurity that belongs behind ``factory_core.adapters.RepoAdapter`` — a target adapter
    reads its own source and returns these facts. This core never scans a target; it only
    consumes the neutral shape.
    """

    source: str
    target: str
    relation: str = "flow"
    location: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.source, self.target, self.relation)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> SourceFlowFact:
        return cls(
            source=str(raw.get("source", "")).strip(),
            target=str(raw.get("target", "")).strip(),
            relation=str(raw.get("relation", "flow")).strip() or "flow",
            location=str(raw.get("location", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"source": self.source, "target": self.target,
                               "relation": self.relation}
        if self.location:
            out["location"] = self.location
        return out


@dataclass(frozen=True)
class SourceFacts:
    """The neutral bundle a ``RepoAdapter`` returns: observed nodes + observed flows."""

    nodes: tuple[GraphNode, ...] = ()
    flows: tuple[SourceFlowFact, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> SourceFacts:
        nodes = tuple(
            GraphNode.from_dict(n) for n in _as_tuple(raw.get("nodes")) if isinstance(n, Mapping)
        )
        flows = tuple(
            SourceFlowFact.from_dict(f)
            for f in _as_tuple(raw.get("flows"))
            if isinstance(f, Mapping)
        )
        return cls(nodes=nodes, flows=flows)


@dataclass(frozen=True)
class FidelityMismatch:
    reason: str
    source: str
    target: str
    relation: str
    location: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"reason": self.reason, "source": self.source,
                               "target": self.target, "relation": self.relation}
        if self.location:
            out["location"] = self.location
        return out


@dataclass(frozen=True)
class FidelityResult:
    status: str
    mismatches: tuple[FidelityMismatch, ...] = ()

    @property
    def blocked(self) -> bool:
        return self.status != STATUS_SATISFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "blocked": self.blocked,
            "mismatches": [m.to_dict() for m in self.mismatches],
        }


def check_delta_fidelity(delta: CapabilityDelta, facts: SourceFacts) -> FidelityResult:
    """Generic diff: does the declared delta describe exactly the observed flows?

    A flow observed in source but not declared, or declared but not observed, is a
    mismatch. This is pure set logic over the neutral shapes — no target contact.
    """
    declared = {(f.source, f.target, f.relation) for f in delta.flows}
    observed = {f.key for f in facts.flows}
    locations = {f.key: f.location for f in facts.flows}
    mismatches: list[FidelityMismatch] = []
    for src, tgt, rel in sorted(observed - declared):
        mismatches.append(FidelityMismatch(
            reason="source_flow_not_declared", source=src, target=tgt, relation=rel,
            location=locations.get((src, tgt, rel), ""),
        ))
    for src, tgt, rel in sorted(declared - observed):
        mismatches.append(FidelityMismatch(
            reason="declared_flow_not_source_observed", source=src, target=tgt, relation=rel,
        ))
    return FidelityResult(
        status=STATUS_VIOLATED if mismatches else STATUS_SATISFIED,
        mismatches=tuple(mismatches),
    )
