"""Adapter Protocol conformance — a stub implementation satisfies every seam; a class
missing methods does not. Confirms the seams are usable structurally, without importing the
core into the implementation."""

from __future__ import annotations

from typing import Protocol

from factory_core import adapters
from factory_core.adapters import (
    ADAPTER_PROTOCOLS,
    ArtifactSink,
    ComplianceAdapter,
    IdpAdapter,
    KnowledgeAdapter,
    RepoAdapter,
)
from factory_core.registry import KIND_TO_PROTOCOL
from factory_core.target import ADAPTER_KINDS


class ConformingStub:
    """A single stub that implements every method across all five seams."""

    # RepoAdapter
    def clone(self, ref, *, subpath=""): return "/tmp/checkout"
    def read_file(self, path): return b""
    def list_files(self, subpath=""): return []
    def provider_operations(self): return []
    def caller_edges(self): return []
    def create_branch(self, name, base): return f"refs/heads/{name}"
    def open_pull_request(self, branch, *, title, body): return "pr://1"

    # KnowledgeAdapter
    def read_working_agreement(self): return "# AGENTS.md"
    def read_knowledge(self): return []
    def write_affinity(self, nodes, *, run_id): return "pr://affinity"
    def inventory_rows(self): return []

    # ComplianceAdapter
    def invariants(self): return []
    def impact_preview(self, change): return {}

    # IdpAdapter
    def authenticate(self, credentials): return {}
    def claims_to_capabilities(self, claims): return frozenset()

    # ArtifactSink
    def put(self, kind, key, blob): return "sha256:abc"
    def get(self, kind, key): return b""


class NotAnAdapter:
    def unrelated(self): return None


def test_stub_conforms_to_every_protocol() -> None:
    stub = ConformingStub()
    for proto in ADAPTER_PROTOCOLS:
        assert isinstance(stub, proto), f"stub should satisfy {proto.__name__}"


def test_each_protocol_individually() -> None:
    stub = ConformingStub()
    assert isinstance(stub, RepoAdapter)
    assert isinstance(stub, KnowledgeAdapter)
    assert isinstance(stub, ComplianceAdapter)
    assert isinstance(stub, IdpAdapter)
    assert isinstance(stub, ArtifactSink)


def test_non_conforming_class_is_rejected() -> None:
    obj = NotAnAdapter()
    for proto in ADAPTER_PROTOCOLS:
        assert not isinstance(obj, proto), f"{proto.__name__} should reject a non-implementer"


#: The declared seam set as OBJECTS, pinned here independently of the module so that moving the
#: set requires editing both this literal and ``factory_core.adapters.ADAPTER_PROTOCOLS``. The
#: cardinality is a design decision, not a boundary condition; the *membership* is the boundary.
#: Objects rather than names: two classes can share a ``__name__``, and a same-named class from
#: another module swapped into the tuple would satisfy a name comparison while changing the
#: exported surface — which is the thing being guarded.
DECLARED_SEAMS = frozenset(
    {
        RepoAdapter,
        KnowledgeAdapter,
        ComplianceAdapter,
        IdpAdapter,
        ArtifactSink,
    }
)
DECLARED_SEAM_NAMES = frozenset(proto.__name__ for proto in DECLARED_SEAMS)


def test_the_declared_seam_set_is_what_the_core_exports() -> None:
    """A seam added to (or dropped from) the module without amending the declaration fails.

    This replaces the former ``test_there_are_exactly_five_seams``, which asserted the count.
    The count was never the invariant — two competing proposals could both satisfy "six" while
    disagreeing about which sixth seam existed, and a rename would satisfy "five" while changing
    the surface entirely. The declared membership is the thing worth guarding.
    """
    assert frozenset(ADAPTER_PROTOCOLS) == DECLARED_SEAMS
    # Identity, not just membership: the exported tuple holds these exact classes.
    assert all(any(proto is declared for declared in DECLARED_SEAMS) for proto in ADAPTER_PROTOCOLS)


def test_no_undeclared_seam_hides_in_the_module() -> None:
    """Every ``Protocol`` defined in ``factory_core.adapters`` is in the declared set.

    Without this, a new Protocol could be defined and imported by a target while
    ``ADAPTER_PROTOCOLS`` stayed at five — a sixth seam in fact, declared nowhere.
    """
    defined = {
        obj
        for obj in vars(adapters).values()
        if isinstance(obj, type)
        and obj is not Protocol
        and Protocol in getattr(obj, "__mro__", ())
        and obj.__module__ == adapters.__name__
    }
    assert defined == DECLARED_SEAMS, (
        "a Protocol defined in factory_core.adapters is not in the declared seam set; "
        "add it to ADAPTER_PROTOCOLS and DECLARED_SEAMS, or it is an undeclared seam"
    )


def test_every_declared_seam_is_reachable_through_a_registry_kind() -> None:
    """A declared seam no manifest kind maps to is declared and unreachable.

    ``registry.py`` already asserts ``KIND_TO_PROTOCOL`` keys against
    ``target.ADAPTER_KINDS``; nothing tied its *values* back to the declared set.
    """
    assert frozenset(KIND_TO_PROTOCOL.values()) == DECLARED_SEAMS


def test_the_declared_set_has_no_duplicates() -> None:
    # frozenset comparison above would pass with a duplicated entry in the tuple.
    assert len(ADAPTER_PROTOCOLS) == len(DECLARED_SEAMS)


def test_the_seam_set_is_consistent_across_every_place_it_is_declared() -> None:
    """The set is stated in four places; a guard that pins two of them is half a guard.

    ``adapters.ADAPTER_PROTOCOLS`` is the declaration, ``DECLARED_SEAMS`` above pins it from
    outside the module, and ``target.ADAPTER_KINDS`` / ``registry.KIND_TO_PROTOCOL`` are the
    manifest-facing names. ``registry.py`` already asserts its keys against ``ADAPTER_KINDS`` at
    import; this closes the remaining pair.
    """
    assert set(KIND_TO_PROTOCOL) == set(ADAPTER_KINDS)
    assert len(KIND_TO_PROTOCOL) == len(DECLARED_SEAMS)


class MissingInventorySeams:
    """A RepoAdapter-shaped class WITHOUT the P2 inventory methods — must be rejected, so the
    seam bump is a real part of the contract, not a documentation-only addition."""

    def clone(self, ref, *, subpath=""): return "/tmp/checkout"
    def read_file(self, path): return b""
    def list_files(self, subpath=""): return []
    def create_branch(self, name, base): return f"refs/heads/{name}"
    def open_pull_request(self, branch, *, title, body): return "pr://1"


def test_p2_inventory_methods_are_part_of_the_repo_seam() -> None:
    # The new provider_operations/caller_edges methods are contractually required.
    assert not isinstance(MissingInventorySeams(), RepoAdapter)
