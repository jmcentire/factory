"""factory_core.registry — resolve a target's *named* adapter selections to live instances.

This is the generic bridge between a data-only ``TargetManifest`` and a running factory. A
manifest may only *select* a seam by name (``repo = "readonly_git"``); the loader
(``factory_core.target``) proves the name is a name and never code. What was missing is the
step that turns that name into an object: a **registry** that a target pack populates with
concrete implementations, and that the core queries by name.

The registry is generic by construction — it names no target and no concrete adapter. It only:

  * accepts registrations keyed by ``(seam_kind, name)``, where ``seam_kind`` must be one of the
    five seams the core owns (``factory_core.target.ADAPTER_KINDS``);
  * resolves a ``(seam_kind, name)`` to an instance by calling the registered provider with the
    per-target config; and
  * verifies, fail-closed, that the produced instance structurally satisfies the seam's
    ``Protocol`` — so a target can never smuggle in an object that only *claims* to be an
    adapter.

The dependency arrow points pack -> core: a pack imports the core to register against it; the
core imports nothing from any pack. Keeping this module stdlib + ``factory_core`` only is what
lets the purity guard stay green.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from factory_core.adapters import (
    ArtifactSink,
    ComplianceAdapter,
    IdpAdapter,
    KnowledgeAdapter,
    RepoAdapter,
)
from factory_core.target import ADAPTER_KINDS, TargetManifest

#: The seam kind each adapter name resolves against, paired with the Protocol an instance must
#: satisfy. Built explicitly (not by zipping two ordered tuples) so a future seam addition can
#: never silently misalign a kind with the wrong Protocol.
KIND_TO_PROTOCOL: dict[str, type] = {
    "repo": RepoAdapter,
    "knowledge": KnowledgeAdapter,
    "compliance": ComplianceAdapter,
    "idp": IdpAdapter,
    "artifact_sink": ArtifactSink,
}

# Fail closed if the core's seam set ever drifts from the kinds this module knows how to check.
assert set(KIND_TO_PROTOCOL) == set(ADAPTER_KINDS), (
    "registry KIND_TO_PROTOCOL is out of sync with factory_core.target.ADAPTER_KINDS"
)

#: Any of the five seam types the core owns. A provider yields exactly one of these; which one
#: is fixed by the seam kind it is registered under, and re-proven structurally at resolve time.
Adapter = RepoAdapter | KnowledgeAdapter | ComplianceAdapter | IdpAdapter | ArtifactSink

#: A provider builds an adapter instance from the per-target config handed to ``resolve`` (the
#: ``TargetManifest`` when resolving through ``resolve_for``). It is code, supplied by a pack —
#: never named in a manifest. The return is narrowed to ``Adapter``: the core knows the result
#: must be one of its own seams. The input stays ``Any`` on purpose — it is per-target config
#: whose shape each pack defines (a manifest, a pack-specific object, or ``None``), so the core
#: does not constrain it.
Provider = Callable[[Any], Adapter]


class AdapterResolutionError(ValueError):
    """Raised when an adapter cannot be resolved to a conforming instance. Always fail-closed:
    an unknown seam kind, an unregistered name, or an instance that does not satisfy the seam
    Protocol is refused rather than returned partially trusted."""


class AdapterRegistry:
    """A name->implementation registry for the five adapter seams.

    A pack registers concrete implementations; the core resolves names to instances and proves
    each instance satisfies its seam. Nothing here is target-specific."""

    def __init__(self) -> None:
        self._providers: dict[tuple[str, str], Provider] = {}

    def register(self, kind: str, name: str, provider: Provider) -> None:
        """Register ``provider`` as the implementation of seam ``kind`` selected by ``name``.

        ``kind`` must be one of the seams the core owns; anything else is refused (a target can
        never invent a sixth seam). A later registration for the same ``(kind, name)`` replaces
        the earlier one."""
        if kind not in KIND_TO_PROTOCOL:
            raise AdapterResolutionError(
                f"unknown adapter seam {kind!r}; the core owns exactly {tuple(ADAPTER_KINDS)}"
            )
        self._providers[(kind, name)] = provider

    def resolve(self, kind: str, name: str, config: Any = None) -> Adapter:
        """Resolve seam ``kind`` selected by ``name`` to a live, seam-conforming instance.

        Calls the registered provider with ``config`` (per-target data) and verifies the result
        structurally satisfies ``KIND_TO_PROTOCOL[kind]``. Fail-closed on unknown kind,
        unregistered name, or non-conforming instance."""
        if kind not in KIND_TO_PROTOCOL:
            raise AdapterResolutionError(
                f"unknown adapter seam {kind!r}; the core owns exactly {tuple(ADAPTER_KINDS)}"
            )
        try:
            provider = self._providers[(kind, name)]
        except KeyError:
            raise AdapterResolutionError(
                f"no adapter registered for seam {kind!r} under name {name!r}; register a "
                "provider for it before resolving (a target only selects seams the core owns)"
            ) from None

        instance = provider(config)

        protocol = KIND_TO_PROTOCOL[kind]
        if not isinstance(instance, protocol):
            raise AdapterResolutionError(
                f"adapter {name!r} for seam {kind!r} does not satisfy {protocol.__name__}: the "
                "provider returned an object missing one or more of the seam's methods"
            )
        return instance

    def resolve_for(self, manifest: TargetManifest, config: Any = None) -> dict[str, Adapter]:
        """Resolve every adapter a ``manifest`` selects, returning ``{seam_kind: instance}``.

        Each provider receives ``config`` when supplied, otherwise the ``manifest`` itself as
        the per-target data source (so e.g. a repo adapter can read ``manifest.repo``). Fails
        closed on the first seam that cannot be resolved."""
        payload = manifest if config is None else config
        return {
            kind: self.resolve(kind, name, payload)
            for kind, name in manifest.adapters.items()
        }
