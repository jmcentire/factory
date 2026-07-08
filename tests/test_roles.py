"""Role / capability model tests — the schema resolves per-target grants and rejects roles
that grant capabilities the catalog does not define. Roles/grants are data; the model holds
no authority of its own."""

from __future__ import annotations

import pytest

from factory_core.roles import (
    ILLUSTRATIVE_CAPABILITIES,
    CapabilityCatalog,
    Grant,
    Role,
    RoleModel,
    RoleModelError,
    build_grants,
    build_roles,
)
from factory_core.target import load_target_manifest
from tests.conftest import SYNTHETIC_TARGET


def _catalog() -> CapabilityCatalog:
    return CapabilityCatalog.from_names(ILLUSTRATIVE_CAPABILITIES)


def test_role_model_rejects_unknown_capability() -> None:
    catalog = _catalog()
    with pytest.raises(RoleModelError, match="unknown capabilities"):
        RoleModel(catalog, [Role(name="rogue", capabilities=frozenset({"not.a.real.cap"}))])


def test_resolve_effective_capabilities_from_a_role_grant() -> None:
    catalog = _catalog()
    model = RoleModel(catalog, [
        Role(name="requester", capabilities=frozenset({"prd.author", "prd.submit"})),
    ])
    grants = [Grant(principal="alice", target_id="acme", role="requester")]
    caps = model.resolve("alice", "acme", grants)
    assert caps == frozenset({"prd.author", "prd.submit"})


def test_grants_are_scoped_per_target() -> None:
    catalog = _catalog()
    model = RoleModel(catalog, [Role(name="approver", capabilities=frozenset({"spec.approve"}))])
    grants = [Grant(principal="bob", target_id="acme", role="approver")]
    assert model.resolve("bob", "acme", grants) == frozenset({"spec.approve"})
    assert model.resolve("bob", "other", grants) == frozenset(), "no cross-target bleed"


def test_capabilities_outside_the_catalog_are_dropped() -> None:
    catalog = _catalog()
    model = RoleModel(catalog, [])
    grants = [
        Grant(principal="eve", target_id="acme",
              capabilities=frozenset({"admin.manage", "made.up"})),
    ]
    # 'admin.manage' is in the catalog; 'made.up' is not and is default-dropped.
    assert model.resolve("eve", "acme", grants) == frozenset({"admin.manage"})


def test_build_roles_and_grants_from_manifest_data() -> None:
    tm = load_target_manifest(SYNTHETIC_TARGET)
    roles = build_roles(tm.roles)
    grants = build_grants(tm.grants, tm.target_id)
    model = RoleModel(_catalog(), roles)
    caps = model.resolve("alice@example.invalid", tm.target_id, grants)
    assert "prd.author" in caps
    assert model.resolve("bob@example.invalid", tm.target_id, grants) == frozenset(
        {"spec.approve", "capability.accept"}
    )
