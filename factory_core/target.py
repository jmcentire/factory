"""factory_core.target — the TargetManifest loader (data in, never code).

A target is fed to the factory as a single content-addressed TOML manifest: repo coordinates
+ ref (+ optional monorepo subpath), adapter *selections* (names, never import paths),
role->capability bindings, a compliance-rule path, effort/cost parameters, and a demo-env
descriptor. This module parses that manifest, validates it against a JSON Schema, and
**refuses any code reference** — the manifest is data only; it may never smuggle in a Python
import, module:attr callable, or ``.py`` path. That refusal is the structural guarantee
behind the generic-core / target-as-data boundary: a target can never inject code into the
core, only select from named seams the core already owns.

Fail-closed ordering (matches the Phase 0 signing finding): the loader parses, schema-checks,
refuses code references, and verifies the content address / signature **before** any adapter
is resolved. If a signature is required and cannot be verified against the supplied trust
root, the loader refuses. Full key anchoring / rotation / revocation (an out-of-repo trust
root) remains an open founder decision — this module provides the enforcement *seam* and the
canonical-bytes content address it rides on, not a bundled PKI.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from factory_core.manifest import digest_bytes, digest_obj

SCHEMA_PATH = Path(__file__).parent / "schemas" / "target_manifest.schema.json"
SCHEMA_VERSION = "factory-target-manifest/1"

# The five adapter seams a manifest may select an implementation for (by name, never by code).
ADAPTER_KINDS = ("repo", "knowledge", "compliance", "idp", "artifact_sink")

# A registered adapter selection is a plain lowercase name (a registry key). Anything else —
# a dotted path, a colon, a slash, a file extension — is not a name and is refused.
_ADAPTER_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

# Patterns that mark a string as a *code reference* rather than data. None of these match a
# URL (``https://...`` has ``:`` followed by ``/``, never ``:<identifier>``), so repo
# coordinates pass while an import/callable path is refused.
_CODE_REF_PATTERNS = (
    re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_][\w.]*$"),   # module:attr callable ("pkg.mod:Class")
    re.compile(r"\.py[cwox]?$"),                         # a python source/bytecode path
    re.compile(r"(?:^|\s)(?:import|from)\s+[A-Za-z_]"),  # an inline import statement
    re.compile(r"^(?:targets|target_packs)\.[A-Za-z_]"),  # an explicit target-pack import head
)


class TargetManifestError(ValueError):
    """Raised when a target manifest is malformed, schema-invalid, carries a code reference,
    or fails content-address / signature verification. Always fail-closed: on any doubt the
    loader refuses rather than returning a partially-trusted target."""


@dataclass
class TargetManifest:
    """A validated, data-only target descriptor. Nothing here is code; every value is a string,
    number, or nested container the core interprets against seams it already owns."""

    target_id: str
    repo: dict[str, Any]
    adapters: dict[str, str]
    compliance: dict[str, Any]
    roles: list[dict[str, Any]] = field(default_factory=list)
    grants: list[dict[str, Any]] = field(default_factory=list)
    effort: dict[str, Any] = field(default_factory=dict)
    demo_env: dict[str, Any] = field(default_factory=dict)
    signature: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    # provenance stamped by the loader
    source_digest: str = ""   # content address of the raw TOML bytes
    content_digest: str = ""  # canonical address of the manifest data (signature block excluded)
    raw: dict[str, Any] = field(default_factory=dict)


def _walk_strings(obj: Any) -> list[str]:
    """Collect every string value reachable in a nested dict/list structure."""
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for value in obj.values():
            out.extend(_walk_strings(value))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            out.extend(_walk_strings(item))
    return out


def _looks_like_code_ref(value: str) -> bool:
    return any(pat.search(value) for pat in _CODE_REF_PATTERNS)


def _refuse_code_references(data: dict[str, Any]) -> None:
    for value in _walk_strings(data):
        if _looks_like_code_ref(value):
            raise TargetManifestError(
                f"refused code reference in target manifest: {value!r}. A target manifest is "
                "data only — adapter selections are registry names, not import paths; the core "
                "never imports anything a target names."
            )


def _validate_adapter_names(adapters: dict[str, Any]) -> None:
    for kind, name in adapters.items():
        if kind not in ADAPTER_KINDS:
            raise TargetManifestError(
                f"unknown adapter seam {kind!r}; the core owns exactly {ADAPTER_KINDS}"
            )
        if not isinstance(name, str) or not _ADAPTER_NAME.match(name):
            raise TargetManifestError(
                f"adapter selection {kind}={name!r} is not a registered adapter name "
                "(must match ^[a-z][a-z0-9_]*$) — a target may only *select* a named seam, "
                "never reference code"
            )


def load_target_manifest(
    path: str | Path,
    *,
    schema_path: str | Path = SCHEMA_PATH,
    require_signature: bool = False,
    verify_signature: Callable[[bytes, dict[str, Any]], bool] | None = None,
) -> TargetManifest:
    """Load, validate, and content-address a target manifest — fail-closed, before any adapter
    is resolved.

    Steps, in order:
      1. read raw bytes and compute the source content address;
      2. parse TOML (malformed TOML -> refuse);
      3. validate against the JSON Schema (missing/extra/mistyped fields -> refuse);
      4. refuse any code reference and validate adapter selections are registry names;
      5. compute the canonical content address over the data (excluding the signature block);
      6. verify the signature: if the manifest declares a ``content_digest`` it must equal the
         computed address; if ``require_signature`` is set, a signature must be present and
         (when ``verify_signature`` is supplied — the out-of-repo trust-root seam) must verify.

    ``verify_signature(canonical_bytes, signature_block) -> bool`` is the trust-root seam. When
    absent and ``require_signature`` is True, only self-consistency of the declared digest is
    enforced (a dev posture); anchoring the key out-of-repo with rotation/revocation is a
    deferred founder decision, wired here but not bundled.
    """
    p = Path(path)
    raw_bytes = p.read_bytes()
    source_digest = digest_bytes(raw_bytes)

    try:
        data = tomllib.loads(raw_bytes.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise TargetManifestError(f"malformed target manifest (not valid TOML): {exc}") from exc

    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as exc:
        location = "/".join(str(p) for p in exc.absolute_path) or "<root>"
        raise TargetManifestError(
            f"target manifest schema violation at {location}: {exc.message}"
        ) from exc

    # The signature block carries integrity metadata (content digests look like ``module:attr``
    # and are opaque), so it is excluded from the data body and from the code-reference scan.
    body = {k: v for k, v in data.items() if k != "signature"}
    _refuse_code_references(body)
    _validate_adapter_names(data.get("adapters", {}))

    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    content_digest = digest_obj(body)

    signature = data.get("signature", {}) or {}
    declared = signature.get("content_digest", "")
    if declared and declared != content_digest:
        raise TargetManifestError(
            f"content-address mismatch: manifest declares {declared} but its canonical content "
            f"addresses to {content_digest} (tampered or mis-signed)"
        )

    if require_signature:
        if not signature:
            raise TargetManifestError(
                "signature required but the target manifest carries no [signature] block "
                "(fail closed before adapter resolution)"
            )
        if verify_signature is not None:
            if not verify_signature(canonical, signature):
                raise TargetManifestError(
                    "signature verification failed against the supplied trust root (fail closed)"
                )
        elif not declared:
            raise TargetManifestError(
                "signature required but no verifiable content_digest is present and no trust "
                "root was supplied to verify against"
            )

    return TargetManifest(
        target_id=data["target_id"],
        repo=data["repo"],
        adapters=data["adapters"],
        compliance=data["compliance"],
        roles=data.get("roles", []),
        grants=data.get("grants", []),
        effort=data.get("effort", {}),
        demo_env=data.get("demo_env", {}),
        signature=signature,
        schema_version=data.get("schema_version", SCHEMA_VERSION),
        source_digest=source_digest,
        content_digest=content_digest,
        raw=data,
    )
