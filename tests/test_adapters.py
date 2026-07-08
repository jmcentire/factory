"""Adapter Protocol conformance — a stub implementation satisfies every seam; a class
missing methods does not. Confirms the seams are usable structurally, without importing the
core into the implementation."""

from __future__ import annotations

from factory_core.adapters import (
    ADAPTER_PROTOCOLS,
    ArtifactSink,
    ComplianceAdapter,
    IdpAdapter,
    KnowledgeAdapter,
    RepoAdapter,
)


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


def test_there_are_exactly_five_seams() -> None:
    assert len(ADAPTER_PROTOCOLS) == 5


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
