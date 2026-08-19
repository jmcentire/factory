"""Portable, file-backed contracts for one isolated Factory lane.

The contract is deliberately runner-neutral.  A launcher may choose tmux, a remote
runner, or a direct process, but no such transport detail is authority in this
document.  The lane receives the exact input artifacts, private directories, and
the path at which it may publish its role-owned completion receipt.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from factory_core.manifest import digest_bytes
from factory_runtime.schema import DocumentValidationError, validate_document

_MAX_DOCUMENT_BYTES = 262_144


class LaneContractError(ValueError):
    """A lane machine contract or completion receipt is unsafe or malformed."""


def _canonical_bytes(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _write_once(path: Path, document: Mapping[str, Any]) -> None:
    payload = _canonical_bytes(document)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise LaneContractError(f"refusing to replace existing lane document: {path}") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _read_document(path: Path, *, schema_name: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise LaneContractError(f"{schema_name} is missing, not regular, or symlinked")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise LaneContractError(f"{schema_name} is unreadable: {exc}") from exc
    if len(raw) > _MAX_DOCUMENT_BYTES:
        raise LaneContractError(f"{schema_name} exceeds its size ceiling")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LaneContractError(f"{schema_name} is invalid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise LaneContractError(f"{schema_name} must be a JSON object")
    if raw != _canonical_bytes(document):
        raise LaneContractError(f"{schema_name} is not canonical")
    try:
        validate_document(schema_name, document)
    except DocumentValidationError as exc:
        raise LaneContractError(str(exc)) from exc
    return document


def write_lane_contract(
    path: str | Path,
    *,
    contract_id: str,
    run_id: str,
    attempt_id: str,
    role: str,
    input_artifacts: Sequence[Mapping[str, str]],
    work_directory: str | Path,
    output_directory: str | Path,
    private_directory: str | Path,
    completion_receipt_path: str | Path,
) -> str:
    """Validate and durably materialize a lane contract, returning its digest."""

    document: dict[str, Any] = {
        "schema_version": "factory-lane-contract/1",
        "contract_id": contract_id,
        "run_id": run_id,
        "attempt_id": attempt_id,
        "role": role,
        "input_artifacts": [dict(item) for item in input_artifacts],
        "directories": {
            "work": str(Path(work_directory).resolve()),
            "output": str(Path(output_directory).resolve()),
            "private": str(Path(private_directory).resolve()),
        },
        "completion_receipt_path": str(Path(completion_receipt_path).resolve()),
    }
    try:
        validate_document("lane-contract", document)
    except DocumentValidationError as exc:
        raise LaneContractError(str(exc)) from exc
    _write_once(Path(path), document)
    return digest_bytes(_canonical_bytes(document))


def load_lane_contract(path: str | Path) -> dict[str, Any]:
    """Read a canonical, regular-file lane contract."""

    return _read_document(Path(path), schema_name="lane-contract")


def load_lane_completion_receipt(
    path: str | Path,
    *,
    expected_run_id: str,
    expected_attempt_id: str,
    expected_role: str,
    expected_contract_digest: str,
) -> dict[str, Any]:
    """Read a receipt and bind it to the exact issued lane contract."""

    receipt = _read_document(Path(path), schema_name="lane-completion-receipt")
    for field, expected in (
        ("run_id", expected_run_id),
        ("attempt_id", expected_attempt_id),
        ("role", expected_role),
        ("contract_digest", expected_contract_digest),
    ):
        if receipt[field] != expected:
            raise LaneContractError(f"completion receipt {field} does not bind this lane contract")
    return receipt
