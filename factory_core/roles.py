"""factory_core.roles — the role / capability model SCHEMA (not a catalog of authority).

The atomic unit of access is the **capability**. A **role** is a named bundle of
capabilities. A **grant** binds a principal to a role (or to capabilities directly), scoped
to a single target. Crucially: the *catalog of capabilities*, the *roles*, and the *grants*
are all per-target **data**, fed in through the manifest and IdP seams — they are never core
classes. This module owns only the shape and the resolution rules; it hardcodes no authority.

This is distinct from the segregation-of-duties identities in ``manifest`` (implementer /
verifier / approver), which are about *who signed a transition*. RBAC here is about *what a
principal may do on a target*. The two intersect only at the ledger: a principal must hold the
capability to act, and the acting identities must still be three distinct SoD principals.

An ``ILLUSTRATIVE_CAPABILITIES`` set is provided purely as a convenience/example (it mirrors
the PRD's illustrative catalog). It is NOT authoritative and NOT loaded by default — a real
target feeds its own catalog.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

#: Example capability names (PRD §5). Illustrative only — a real target feeds its own catalog.
ILLUSTRATIVE_CAPABILITIES: frozenset[str] = frozenset({
    "prd.author",
    "prd.submit",
    "spec.author",
    "spec.approve",
    "build.request",
    "spend.approve",
    "demo.feedback",
    "capability.accept",
    "roadmap.prioritize",
    "gate.override",
    "admin.manage",
})


class RoleModelError(ValueError):
    """Raised when roles/grants are inconsistent with the capability catalog (fail closed)."""


@dataclass(frozen=True)
class Capability:
    """An atomic, named unit of access. The name is the identity; description is human aid."""

    name: str
    description: str = ""


@dataclass(frozen=True)
class Role:
    """A named bundle of capability names. Roles are data, defined per target."""

    name: str
    capabilities: frozenset[str] = frozenset()
    description: str = ""


@dataclass(frozen=True)
class Grant:
    """Binds a principal to a role and/or direct capabilities, scoped to one target.

    A principal may be ``spec.approve`` on target A and read-only on target B — grants are
    per-target data, so the same principal resolves to different capability sets per target.
    """

    principal: str
    target_id: str
    role: str = ""
    capabilities: frozenset[str] = frozenset()


class CapabilityCatalog:
    """The known-capabilities set for a target. Membership is the ONLY authority over what a
    capability name means; a role may not grant a capability the catalog does not define."""

    def __init__(self, capabilities: Iterable[Capability | str]) -> None:
        self._caps: dict[str, Capability] = {}
        for cap in capabilities:
            c = cap if isinstance(cap, Capability) else Capability(name=cap)
            self._caps[c.name] = c

    @classmethod
    def from_names(cls, names: Iterable[str]) -> CapabilityCatalog:
        return cls(Capability(name=n) for n in names)

    def __contains__(self, name: object) -> bool:
        return name in self._caps

    def names(self) -> frozenset[str]:
        return frozenset(self._caps)

    def validate_role(self, role: Role) -> list[str]:
        """Return the role's capability names that the catalog does not define (empty = ok)."""
        return sorted(name for name in role.capabilities if name not in self._caps)


class RoleModel:
    """The resolution engine over a catalog + a set of roles. Roles and grants are DATA fed to
    it; the model validates them against the catalog and resolves a principal's effective
    capabilities on a target. It stores no authority of its own."""

    def __init__(self, catalog: CapabilityCatalog, roles: Iterable[Role]) -> None:
        self.catalog = catalog
        self.roles: dict[str, Role] = {}
        problems: list[str] = []
        for role in roles:
            unknown = catalog.validate_role(role)
            if unknown:
                problems.append(f"role {role.name!r} grants unknown capabilities: {unknown}")
            self.roles[role.name] = role
        if problems:
            raise RoleModelError("; ".join(problems))

    def resolve(
        self,
        principal: str,
        target_id: str,
        grants: Sequence[Grant],
    ) -> frozenset[str]:
        """Resolve ``principal``'s effective capabilities on ``target_id`` from ``grants``.

        A grant applies only if its ``principal`` and ``target_id`` match (per-target scoping —
        no cross-target bleed). Effective capabilities are the union of every applicable
        grant's direct capabilities and the capabilities of its named role. Unknown roles and
        capabilities outside the catalog are dropped (default-safe), not silently trusted.
        """
        effective: set[str] = set()
        catalog_names = self.catalog.names()
        for grant in grants:
            if grant.principal != principal or grant.target_id != target_id:
                continue
            if grant.role:
                role = self.roles.get(grant.role)
                if role is not None:
                    effective |= set(role.capabilities)
            effective |= set(grant.capabilities)
        return frozenset(effective & catalog_names)


def build_roles(role_data: Iterable[Mapping[str, object]]) -> list[Role]:
    """Build ``Role`` objects from the manifest's fed-in role bindings (data -> value objects)."""
    built: list[Role] = []
    for entry in role_data:
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        raw_caps = entry.get("capabilities")
        items = raw_caps if isinstance(raw_caps, (list, tuple)) else []
        caps = frozenset(str(c) for c in items)
        built.append(
            Role(name=name, capabilities=caps, description=str(entry.get("description", "")))
        )
    return built


def build_grants(grant_data: Iterable[Mapping[str, object]], target_id: str) -> list[Grant]:
    """Build ``Grant`` objects for ``target_id`` from the manifest's fed-in grant bindings."""
    built: list[Grant] = []
    for entry in grant_data:
        principal = str(entry.get("principal", "")).strip()
        if not principal:
            continue
        raw_caps = entry.get("capabilities")
        items = raw_caps if isinstance(raw_caps, (list, tuple)) else []
        caps = frozenset(str(c) for c in items)
        built.append(
            Grant(
                principal=principal,
                target_id=target_id,
                role=str(entry.get("role", "")),
                capabilities=caps,
            )
        )
    return built
