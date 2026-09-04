"""factory_core.target — the TargetManifest loader (data in, never code).

A target is fed to the factory as a single content-addressed TOML manifest: repo coordinates
+ ref (+ optional monorepo subpath), adapter *selections* (names, never import paths),
role->capability bindings, a compliance-rule path, an operational build ABI, effort/cost
parameters, and a demo-env descriptor. This module parses that manifest, validates it against
a JSON Schema, and
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

from factory_core.manifest import digest_bytes

SCHEMA_PATH = Path(__file__).parent / "schemas" / "target_manifest.schema.json"
SCHEMA_VERSION = "factory-target-manifest/2"

# The five adapter seams a manifest may select an implementation for (by name, never by code).
ADAPTER_KINDS = ("repo", "knowledge", "compliance", "idp", "artifact_sink")

# A registered adapter selection is a plain lowercase name (a registry key). Anything else —
# a dotted path, a colon, a slash, a file extension — is not a name and is refused.
_ADAPTER_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_CANONICAL_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

# Patterns that mark a string as a *code reference* rather than data. None of these match a
# URL (``https://...`` has ``:`` followed by ``/``, never ``:<identifier>``), so repo
# coordinates pass while an import/callable path is refused.
_CODE_REF_PATTERNS = (
    re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_][\w.]*$"),  # module:attr callable ("pkg.mod:Class")
    re.compile(r"\.py[cwox]?$"),  # a python source/bytecode path
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
    build: dict[str, Any]
    roles: list[dict[str, Any]] = field(default_factory=list)
    grants: list[dict[str, Any]] = field(default_factory=list)
    effort: dict[str, Any] = field(default_factory=dict)
    demo_env: dict[str, Any] = field(default_factory=dict)
    signature: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    # provenance stamped by the loader — 2.1: ONE digest, over the exact bytes that were
    # read. The canonical re-derivation and its in-file declared content_digest are gone:
    # signing a re-encoding of what the parser produced verified the parser, not the file.
    source_digest: str = ""  # content address of the raw TOML bytes
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def test_entrypoint(self) -> tuple[str, ...]:
        """The target-native acceptance-test argv, or empty when the target declares none.

        This is target data, bound by the manifest's content/source digests at admission. The
        core never interprets it beyond running it as an exact argv (no shell) in the Validator.
        """

        raw = self.build.get("test_entrypoint", ())
        return tuple(str(part) for part in raw)


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
    if _CANONICAL_DIGEST.fullmatch(value):
        return False
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
    """Read a target manifest, then validate the exact bytes obtained from that read."""

    source = Path(path)
    return load_target_manifest_bytes(
        source.read_bytes(),
        schema_path=schema_path,
        require_signature=require_signature,
        verify_signature=verify_signature,
        source_label=str(source),
    )


def load_target_manifest_bytes(
    raw_bytes: bytes,
    *,
    schema_path: str | Path = SCHEMA_PATH,
    require_signature: bool = False,
    verify_signature: Callable[[bytes, dict[str, Any]], bool] | None = None,
    source_label: str = "<bytes>",
) -> TargetManifest:
    """Load, validate, and content-address a target manifest — fail-closed, before any adapter
    is resolved.

    Steps, in order (schema/2 — sign the bytes that were read, plan 2.1):
      1. read raw bytes and compute THE content address (``digest_bytes(raw_bytes)``);
      2. parse TOML (malformed TOML -> refuse);
      3. validate against the JSON Schema (missing/extra/mistyped fields -> refuse; a
         schema/1 manifest refuses HERE, the earliest firing point — migration is
         fail-closed re-declaration, never a legacy-acceptance path);
      4. refuse any code reference and validate adapter selections are registry names;
      5. if ``require_signature`` is set, a ``[signature]`` block naming the key id must
         be present and (when ``verify_signature`` is supplied) must verify over the RAW
         BYTES — never over a canonical re-encoding, which would sign what the parser
         produced instead of what was read.

    ``verify_signature(raw_bytes, signature_block) -> bool`` is the trust-root seam.
    ENFORCEMENT, STATED HONESTLY: the live gate is the request-bound raw-byte digest
    equality in host code (workflow.py's resolution check); this seam is DORMANT until an
    out-of-repo trust root exists, and no doc may count it as active. The in-file
    signature value as a self-verifiable claim is deleted — a file cannot vouch for
    itself; the signature value lives detached, with the trust root.
    """
    source_digest = digest_bytes(raw_bytes)

    try:
        data = tomllib.loads(raw_bytes.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise TargetManifestError(f"malformed target manifest (not valid TOML): {exc}") from exc

    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as exc:
        location = "/".join(str(part) for part in exc.absolute_path) or "<root>"
        raise TargetManifestError(
            f"target manifest {source_label} schema violation at {location}: {exc.message}"
        ) from exc

    # The signature block carries integrity metadata (content digests look like ``module:attr``
    # and are opaque), so it is excluded from the data body and from the code-reference scan.
    body = {k: v for k, v in data.items() if k != "signature"}
    _refuse_code_references(body)
    _validate_adapter_names(data.get("adapters", {}))

    signature = data.get("signature", {}) or {}
    if require_signature:
        if not str(signature.get("key_id", "")).strip():
            raise TargetManifestError(
                "signature required but the target manifest names no signing key id "
                "(fail closed before adapter resolution)"
            )
        if verify_signature is not None and not verify_signature(raw_bytes, signature):
            raise TargetManifestError(
                "signature verification failed against the supplied trust root (fail closed)"
            )

    return TargetManifest(
        target_id=data["target_id"],
        repo=data["repo"],
        adapters=data["adapters"],
        compliance=data["compliance"],
        build=data["build"],
        roles=data.get("roles", []),
        grants=data.get("grants", []),
        effort=data.get("effort", {}),
        demo_env=data.get("demo_env", {}),
        signature=signature,
        schema_version=data.get("schema_version", SCHEMA_VERSION),
        source_digest=source_digest,
        raw=data,
    )
