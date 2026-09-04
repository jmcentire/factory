"""Two-step, non-executable acceptance-catalog proposal and activation boundary.

Catalog authorship is deliberately separated from retention and dispatch.  A proposal is
only canonical data outside the run root.  Activation reopens the current ratified run,
verifies two independent receipts over that exact proposal, and then delegates catalog
retention to the existing acceptance-obligation verifier.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory_core.manifest import digest_obj
from factory_runtime.acceptance_obligations import (
    AcceptanceObligationCatalog,
    AcceptanceObligationError,
    StoredAcceptanceCatalog,
    verify_and_retain_acceptance_catalog,
)
from factory_runtime.attempt_admission import AttemptAdmissionError, _target_profile
from factory_runtime.authority import AuthorityPolicy
from factory_runtime.durability import DurabilityError, fsync_directory_chain
from factory_runtime.state import RunState, RunStore
from factory_runtime.tessera import TesseraCli

SCHEMA_VERSION = "factory-acceptance-catalog-proposal/1"
_NONCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{15,127}$")


class CatalogProposalError(ValueError):
    """A catalog proposal is not safe to activate."""


def _canonical_bytes(document: Mapping[str, Any]) -> bytes:
    encoded = json.dumps(
        dict(document), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode(
        "utf-8"
    )
    return encoded + b"\n"


def _read_regular_json(path: str | Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    candidate = Path(path)
    try:
        info = candidate.lstat()
        if candidate.is_symlink() or not candidate.is_file():
            raise OSError("not a regular file")
        raw = candidate.read_bytes()
        if candidate.stat().st_ino != info.st_ino:
            raise OSError("changed while read")
        document = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogProposalError(f"{label} is not a stable regular JSON file") from exc
    if not isinstance(document, dict):
        raise CatalogProposalError(f"{label} must be a JSON object")
    if raw != _canonical_bytes(document):
        raise CatalogProposalError(f"{label} must use canonical JSON bytes")
    return document, raw


def _source_root(projection: Any, root: Path, run_id: str) -> Path:
    target_path = root / run_id / "evidence" / "target-resolution" / "target-state.json"
    try:
        target, _ = _read_regular_json(target_path, label="retained target state")
        source = target.get("source_root")
        if not isinstance(source, str):
            raise ValueError("missing source root")
        resolved = Path(source).resolve(strict=True)
    except (CatalogProposalError, OSError, ValueError) as exc:
        raise CatalogProposalError("retained target state has no stable source root") from exc
    return resolved


def _validate_native_profile(profile: object, source_root: Path) -> dict[str, Any]:
    if not isinstance(profile, Mapping):
        raise CatalogProposalError("proposal has no native runtime profile")
    # Reuse the typed executor's closed native-profile parser.  It rejects every
    # non-native mode, unrecognised field, inherited environment, and actual port
    # grant; the runner allocates ports later from the declared endpoint shapes.
    if profile.get("mode") != "native-two-profile":
        raise CatalogProposalError("proposal runtime must use native-two-profile")
    try:
        _, paths, _, native = _target_profile({"target_runtime_profile": profile})
    except AttemptAdmissionError as exc:
        raise CatalogProposalError(f"proposal native runtime profile is invalid: {exc}") from exc
    if native is None or not paths:
        raise CatalogProposalError(
            "proposal native runtime profile must declare read-only target paths"
        )
    for path in paths:
        if path == source_root or not path.is_relative_to(source_root):
            raise CatalogProposalError(
                "proposal runtime read path must be a bounded descendant of the "
                "retained target source"
            )
    # No command in this document is executed here.  Refuse shell entrypoints so
    # this data shape cannot be repurposed as a shell carrier by a later caller.
    forbidden = {"sh", "bash", "zsh", "fish", "/bin/sh", "/bin/bash", "/bin/zsh"}
    readiness = profile.get("readiness")
    readiness_argv = readiness.get("entrypoint") if isinstance(readiness, Mapping) else None
    for label, argv in (
        ("candidate_launch", profile.get("candidate_launch")),
        ("test_entrypoint", profile.get("test_entrypoint")),
        ("readiness entrypoint", readiness_argv),
    ):
        if not isinstance(argv, list) or not argv or argv[0] in forbidden:
            raise CatalogProposalError(f"proposal {label} is not an allowed argv declaration")
    return json.loads(_canonical_bytes(dict(profile)))


def validate_catalog_proposal(
    document: Mapping[str, Any], *, runs_root: str | Path
) -> tuple[AcceptanceObligationCatalog, dict[str, Any]]:
    """Validate a proposal against retained authority without retaining or executing it."""

    allowed = {
        "schema_version",
        "run_id",
        "generation",
        "target_state_digest",
        "phase_artifact_digests",
        "proposal_nonce",
        "catalog",
        "target_runtime_profile",
    }
    if set(document) != allowed or document.get("schema_version") != SCHEMA_VERSION:
        raise CatalogProposalError("catalog proposal has an unsupported schema or fields")
    run_id = document.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise CatalogProposalError("catalog proposal run id is invalid")
    nonce = document.get("proposal_nonce")
    if not isinstance(nonce, str) or not _NONCE.fullmatch(nonce):
        raise CatalogProposalError("catalog proposal nonce is invalid")
    root = Path(runs_root).resolve(strict=True)
    try:
        projection = RunStore(root).load(run_id)
    except Exception as exc:
        raise CatalogProposalError("catalog proposal names no retained Factory run") from exc
    if projection.state != RunState.OPERATIONAL_MATURITY_RATIFIED:
        raise CatalogProposalError("catalog proposal requires operational-maturity ratification")
    if nonce in RunStore(root).consumed_authority_nonces(run_id):
        raise CatalogProposalError("catalog proposal nonce has already been consumed")
    expected = {
        "run_id": run_id,
        "generation": projection.generation,
        "target_state_digest": projection.target_state_digest,
        "phase_artifact_digests": dict(projection.phase_artifact_digests),
    }
    for name, value in expected.items():
        if document.get(name) != value:
            raise CatalogProposalError(f"catalog proposal has wrong {name}")
    raw_catalog = document.get("catalog")
    if not isinstance(raw_catalog, Mapping):
        raise CatalogProposalError("catalog proposal catalog is invalid")
    try:
        catalog = AcceptanceObligationCatalog.from_dict(dict(raw_catalog))
    except AcceptanceObligationError as exc:
        raise CatalogProposalError(str(exc)) from exc
    for name, value in expected.items():
        if catalog.document.get(name) != value:
            raise CatalogProposalError(f"proposal catalog has wrong {name}")
    source_root = _source_root(projection, root, run_id)
    profile = _validate_native_profile(document.get("target_runtime_profile"), source_root)
    return catalog, profile


def create_catalog_proposal(
    input_path: str | Path, output_path: str | Path, *, runs_root: str | Path
) -> str:
    """Canonicalize a strictly validated proposal outside Factory run evidence.

    This is intentionally not an authority mutation: no lane, subprocess, receipt,
    run artifact, resource, or catalog retention is created.
    """

    document, _ = _read_regular_json(input_path, label="catalog proposal input")
    validate_catalog_proposal(document, runs_root=runs_root)
    root = Path(runs_root).resolve(strict=True)
    output = Path(output_path)
    if not output.is_absolute() or output.is_symlink() or output.exists():
        raise CatalogProposalError("catalog proposal output must be a new absolute regular path")
    parent = output.parent.resolve(strict=True)
    if parent == root or root in parent.parents:
        raise CatalogProposalError(
            "catalog proposal output may not be retained inside the runs root"
        )
    encoded = _canonical_bytes(document)
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise CatalogProposalError("could not create catalog proposal output") from exc
    return digest_obj(document)


@dataclass(frozen=True)
class ActivatedCatalogProposal:
    proposal_digest: str
    stored_catalog: StoredAcceptanceCatalog
    native_runtime_profile: Mapping[str, Any]


def activate_catalog_proposal(
    proposal_path: str | Path,
    *,
    human_receipt_path: str | Path,
    validator_receipt_path: str | Path,
    runs_root: str | Path,
    policy: AuthorityPolicy,
    tessera: TesseraCli,
) -> ActivatedCatalogProposal:
    """Verify dual proposal ratification, then retain through the existing verifier."""

    document, _ = _read_regular_json(proposal_path, label="catalog proposal")
    catalog, profile = validate_catalog_proposal(document, runs_root=runs_root)
    proposal_digest = digest_obj(document)
    root = Path(runs_root).resolve(strict=True)
    try:
        stored = verify_and_retain_acceptance_catalog(
            root,
            str(document["run_id"]),
            catalog_document=catalog.document,
            human_receipt_path=human_receipt_path,
            validator_receipt_path=validator_receipt_path,
            policy=policy,
            tessera=tessera,
            receipt_subject_digest=proposal_digest,
        )
    except AcceptanceObligationError as exc:
        raise CatalogProposalError(str(exc)) from exc
    directory = stored.directory
    _write_once_or_identical(directory / "proposal.json", _canonical_bytes(document))
    _write_once_or_identical(directory / "native-runtime-profile.json", _canonical_bytes(profile))
    try:
        fsync_directory_chain(directory, through=root / str(document["run_id"]))
    except DurabilityError as exc:
        raise CatalogProposalError(str(exc)) from exc
    return ActivatedCatalogProposal(proposal_digest, stored, profile)


def _write_once_or_identical(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
            raise CatalogProposalError("retained catalog proposal path changed or conflicts")
        return
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    except FileExistsError:
        if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
            raise CatalogProposalError("retained catalog proposal path conflicts") from None
    except OSError as exc:
        raise CatalogProposalError("could not durably retain catalog proposal") from exc
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
