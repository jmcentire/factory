"""factory_core.adapters — the five target-contact seams (interfaces only).

Every point where the factory touches a target is mediated by one of these
``typing.Protocol`` seams, resolved at runtime from a signed ``TargetManifest`` by *name*,
never by import. The core owns these interfaces; a target pack supplies concrete
implementations selected by the manifest. Because these are Protocols, a target implementation
conforms structurally — it need not import the core to satisfy a seam — which is what keeps
the dependency arrow pointing consumer -> factory and never the reverse.

The seams are the whole target surface. There is deliberately no sixth: anything a target
needs the factory to do must fit one of these, or the boundary has been breached.

Docstrings track the Factory Portal PRD v2 §6.2. Method sets are versioned with the core so a
target pack can declare the core version it targets.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from factory_core.completeness import InventoryRow
from factory_core.contract import CallEdge, Endpoint


@runtime_checkable
class RepoAdapter(Protocol):
    """Clone/read/branch/PR against the target VCS.

    All target-code contact is read-then-write-scoped: reads use a least-privilege,
    short-lived credential; branch/PR creation is a separate, later-granted write scope. The
    factory never auto-pushes to a protected branch — every mutation is a pull request.

    The two inventory methods (``provider_operations`` / ``caller_edges``) are where the
    *target-coupled scanning* lives: a target implementation reads its own route modules and
    provider specs and returns **neutral records** (``factory_core.contract.Endpoint`` /
    ``CallEdge``). The core consumes those inventories through
    ``factory_core.contract.check_contract`` and never scans a target itself. Added in the P2
    sync as a minimal, target-agnostic surface bump (see the sync record under ``docs/``).
    """

    def clone(self, ref: str, *, subpath: str = "") -> str:
        """Shallow-clone the target at ``ref`` into an ephemeral checkout; return its path."""
        ...

    def read_file(self, path: str) -> bytes:
        """Read a single file's bytes from the target checkout."""
        ...

    def list_files(self, subpath: str = "") -> Sequence[str]:
        """List file paths under ``subpath`` (default: the whole checkout)."""
        ...

    def provider_operations(self) -> Sequence[Endpoint]:
        """Return the target's backend provider operations as neutral ``Endpoint`` records.

        The target scans its own API/spec surface (e.g. its OpenAPI docs) and returns one
        record per (method, path) it serves. The core owns only the diff — it never parses a
        spec. This is the provider side of the FE<->BE contract."""
        ...

    def caller_edges(self) -> Sequence[CallEdge]:
        """Return the target's frontend/client caller edges as neutral ``CallEdge`` records.

        The target scans its own client/route source and returns one record per (method, path)
        it calls; a statically-unresolvable call is returned with an empty path so the core can
        report it as unresolved rather than silently drop it. This is the caller side of the
        FE<->BE contract."""
        ...

    def create_branch(self, name: str, base: str) -> str:
        """Create a working branch from ``base``; return its ref."""
        ...

    def open_pull_request(self, branch: str, *, title: str, body: str) -> str:
        """Open a PR from ``branch`` into its base; return the PR reference/URL."""
        ...


@runtime_checkable
class KnowledgeAdapter(Protocol):
    """Read ``.kin/`` + AGENTS/CLAUDE, and write learned affinity back — the repo is both the
    source and the sink of its own factory-knowledge.

    Read surfaces the target's working agreement and durable knowledge so a first-time
    stakeholder is guided by the repo's own rules. Write-back persists distilled affinity as
    append-only, team-audience nodes into the target's own ``.kin/`` via PR — never into
    factory state — so knowledge travels with the code and multi-repo isolation is automatic.
    """

    def read_working_agreement(self) -> str:
        """Return the target's authoritative self-description (AGENTS.md / CLAUDE.md)."""
        ...

    def read_knowledge(self) -> Iterable[Mapping[str, Any]]:
        """Yield the target's durable knowledge nodes (team-audience ``.kin`` entries)."""
        ...

    def write_affinity(self, nodes: Sequence[Mapping[str, Any]], *, run_id: str) -> str:
        """Write distilled affinity nodes back to the target's ``.kin/`` and open a PR; return
        the PR reference. Nodes are provisional/unflagged until a human endorses them."""
        ...

    def inventory_rows(self) -> Sequence[InventoryRow]:
        """Return the target's completeness-ledger rows as neutral ``InventoryRow`` records.

        The target parses its own source material (behavior catalog, data inventory, open
        questions, residual registers) and returns one falsifiable row per enumerated claim,
        each with a lattice status. The core owns only the aggregation and the launch-readiness
        predicate (``factory_core.completeness``) — it never parses a target document. Added in
        the P2 sync (see the sync record under ``docs/``)."""
        ...


@runtime_checkable
class ComplianceAdapter(Protocol):
    """Surface the target's invariants and a compliance-impact preview from fed-in rules.

    The rule *engine* is core; the rule *content* is fed-in declarative data (the target's
    directives/constraints serialized as a ruleset the manifest points at). This seam reads
    that data — it never imports target code.
    """

    def invariants(self) -> Sequence[Mapping[str, Any]]:
        """Return the target's hard invariants (id, statement, enforcing mechanism)."""
        ...

    def impact_preview(self, change: Mapping[str, Any]) -> Mapping[str, Any]:
        """Given a proposed change, return which invariants/rules it touches and how."""
        ...


@runtime_checkable
class IdpAdapter(Protocol):
    """Identity: SSO for staff (OIDC) or a generic per-target IdP (OIDC-first; SAML available
    as a per-target configuration option).

    Maps verified IdP claims to a factory principal and its per-target capability grants. No
    local passwords; JIT provisioning assigns an unprivileged default role until an admin
    grants more.
    """

    def authenticate(self, credentials: Mapping[str, Any]) -> Mapping[str, Any]:
        """Verify credentials/assertion and return the resulting identity claims."""
        ...

    def claims_to_capabilities(self, claims: Mapping[str, Any]) -> frozenset[str]:
        """Map verified claims to the principal's effective capability set for this target."""
        ...


@runtime_checkable
class ArtifactSink(Protocol):
    """Where PRDs, specs, demos, and ledger entries land.

    Content-addressed by default: ``put`` returns the stored artifact's address so the ledger
    can reference it by digest rather than by mutable location.
    """

    def put(self, kind: str, key: str, blob: bytes) -> str:
        """Store ``blob`` under (``kind``, ``key``); return its content address / reference."""
        ...

    def get(self, kind: str, key: str) -> bytes:
        """Retrieve a previously stored artifact's bytes."""
        ...


#: The complete set of seams. Any target contact must go through exactly one of these.
ADAPTER_PROTOCOLS: tuple[type, ...] = (
    RepoAdapter,
    KnowledgeAdapter,
    ComplianceAdapter,
    IdpAdapter,
    ArtifactSink,
)
