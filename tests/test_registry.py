"""AdapterRegistry tests — the generic name->implementation resolver.

The registry is the missing link between a data-only ``TargetManifest`` (which *selects*
adapters by name) and a running adapter instance. It is generic by construction: it names no
target and no concrete adapter; a pack registers concrete implementations against it, and the
core only resolves names to instances and verifies each instance structurally satisfies the
seam Protocol it was registered under. Fail-closed on every ambiguity: unknown seam kind,
unregistered name, or an implementation that does not satisfy the seam.

These tests use in-test fakes so the suite stays target-agnostic (the portability proof must
pass with no pack present).
"""

from __future__ import annotations

from typing import Any

import pytest

from factory_core.adapters import RepoAdapter
from factory_core.registry import AdapterRegistry, AdapterResolutionError
from factory_core.target import ADAPTER_KINDS, load_target_manifest
from tests.conftest import SYNTHETIC_TARGET


class _FakeRepo:
    """A minimal object that structurally satisfies RepoAdapter (all seam methods present)."""

    def __init__(self, config: Any = None) -> None:
        self.config = config

    def clone(self, ref: str, *, subpath: str = "") -> str:
        return "/tmp/checkout"

    def read_file(self, path: str) -> bytes:
        return b""

    def list_files(self, subpath: str = ""):
        return []

    def provider_operations(self):
        return []

    def caller_edges(self):
        return []

    def create_branch(self, name: str, base: str) -> str:
        return name

    def open_pull_request(self, branch: str, *, title: str, body: str) -> str:
        return "pr://1"


class _BrokenRepo:
    """Missing most RepoAdapter methods — must be refused as not satisfying the seam.

    Accepts the config arg so it constructs cleanly; the registry's structural check (not a
    constructor error) is what must reject it."""

    def __init__(self, config: Any = None) -> None:
        self.config = config

    def clone(self, ref: str, *, subpath: str = "") -> str:
        return "/tmp/checkout"


class _FakeKnowledge:
    def __init__(self, config: Any = None) -> None:
        self.config = config

    def read_working_agreement(self) -> str:
        return ""

    def read_knowledge(self):
        return []

    def write_affinity(self, nodes, *, run_id: str) -> str:
        return "pr://1"

    def inventory_rows(self):
        return []


class _FakeCompliance:
    def __init__(self, config: Any = None) -> None:
        self.config = config

    def invariants(self):
        return []

    def impact_preview(self, change):
        return {}


class _FakeIdp:
    def __init__(self, config: Any = None) -> None:
        self.config = config

    def authenticate(self, credentials):
        return {}

    def claims_to_capabilities(self, claims):
        return frozenset()


class _FakeSink:
    def __init__(self, config: Any = None) -> None:
        self.config = config

    def put(self, kind: str, key: str, blob: bytes) -> str:
        return "sha256:0"

    def get(self, kind: str, key: str) -> bytes:
        return b""


#: A conforming fake per seam kind, so resolve_for can be exercised over a full manifest.
_FAKE_BY_KIND = {
    "repo": _FakeRepo,
    "knowledge": _FakeKnowledge,
    "compliance": _FakeCompliance,
    "idp": _FakeIdp,
    "artifact_sink": _FakeSink,
}


# --------------------------------------------------------------------------- #
# Resolve a registered adapter
# --------------------------------------------------------------------------- #

def test_resolves_a_registered_adapter_to_an_instance() -> None:
    reg = AdapterRegistry()
    reg.register("repo", "fake_git", _FakeRepo)
    instance = reg.resolve("repo", "fake_git")
    assert isinstance(instance, _FakeRepo)


def test_resolved_adapter_satisfies_the_seam_protocol() -> None:
    reg = AdapterRegistry()
    reg.register("repo", "fake_git", _FakeRepo)
    instance = reg.resolve("repo", "fake_git")
    assert isinstance(instance, RepoAdapter)


def test_provider_receives_the_resolve_config() -> None:
    reg = AdapterRegistry()
    reg.register("repo", "fake_git", _FakeRepo)
    sentinel = {"url": "https://example.invalid/x.git", "ref": "main"}
    instance = reg.resolve("repo", "fake_git", sentinel)
    assert instance.config is sentinel


# --------------------------------------------------------------------------- #
# Fail closed
# --------------------------------------------------------------------------- #

def test_rejects_unknown_seam_kind_on_register() -> None:
    reg = AdapterRegistry()
    with pytest.raises(AdapterResolutionError, match="unknown adapter seam"):
        reg.register("teleport", "whatever", _FakeRepo)


def test_rejects_unregistered_adapter_name() -> None:
    reg = AdapterRegistry()
    with pytest.raises(AdapterResolutionError, match="no adapter registered"):
        reg.resolve("repo", "never_registered")


def test_refuses_implementation_that_does_not_satisfy_the_seam() -> None:
    reg = AdapterRegistry()
    reg.register("repo", "broken", _BrokenRepo)
    with pytest.raises(AdapterResolutionError, match="does not satisfy"):
        reg.resolve("repo", "broken")


# --------------------------------------------------------------------------- #
# Resolve every adapter a manifest selects
# --------------------------------------------------------------------------- #

def test_resolve_for_resolves_every_selected_adapter() -> None:
    manifest = load_target_manifest(SYNTHETIC_TARGET)
    reg = AdapterRegistry()
    for kind, name in manifest.adapters.items():
        reg.register(kind, name, _FAKE_BY_KIND[kind])

    resolved = reg.resolve_for(manifest)

    assert set(resolved) == set(ADAPTER_KINDS)
    assert set(resolved) == set(manifest.adapters)


def test_resolve_for_passes_the_manifest_as_config() -> None:
    manifest = load_target_manifest(SYNTHETIC_TARGET)
    reg = AdapterRegistry()
    for kind, name in manifest.adapters.items():
        reg.register(kind, name, _FAKE_BY_KIND[kind])

    resolved = reg.resolve_for(manifest)

    assert resolved and all(inst.config is manifest for inst in resolved.values())
