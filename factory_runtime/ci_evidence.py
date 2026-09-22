"""Retained CI-output evidence — promotion's host-verified integration proof.

The plan's resolved CI row (4.1): ``promoted`` admission requires a retained
CI output document whose content address is carried on the transition and
whose body binds the EXACT approved candidate digest — host-verified
retention plus the existing byte-for-byte approved-candidate pinning, and no
additional human receipt. A CI claim that is absent, fails to re-derive its
address, or names a different candidate refuses promotion. Nothing here
interprets CI semantics beyond that binding; both the write path and the
replay path call the ONE verifier below, so the requirement cannot drift
into write/derive twins.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

from factory_runtime.snapshot import SnapshotError, freeze_blob, verify_frozen_blob

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_LABEL = "ci-output"


class CiOutputError(ValueError):
    """A CI output document could not be verified against the promotion."""


def _store_root(run_dir: str | Path) -> Path:
    return Path(run_dir) / "evidence" / "ci"


def retain_ci_output(run_dir: str | Path, document: Mapping[str, object]) -> str:
    """Retain one CI output document content-addressed under the run; return its digest.

    The caller (the CI seam) supplies the document; retention is durable
    through the run directory so the promoted ledger entry may cite it.
    """

    if not isinstance(document, Mapping):
        raise CiOutputError("CI output document must be an object")
    data = json.dumps(
        document, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    try:
        frozen = freeze_blob(
            _store_root(run_dir),
            durable_through=run_dir,
            label=_LABEL,
            data=data,
        )
    except SnapshotError as exc:
        raise CiOutputError(f"CI output retention failed: {exc}") from exc
    return frozen.digest


def verify_retained_ci_output(
    run_dir: str | Path,
    *,
    candidate_digest: str,
    expected_digest: str,
) -> None:
    """Verify the cited CI output exists, re-derives its address, and binds the candidate.

    The binding is exact: the retained document's ``candidate`` field must
    equal the approved candidate digest the promotion pins byte-for-byte.
    """

    if not _DIGEST.fullmatch(str(expected_digest)):
        raise CiOutputError("ci-output digest is not a canonical content address")
    blob_dir = _store_root(run_dir) / _LABEL / str(expected_digest).removeprefix("sha256:")
    try:
        blob = verify_frozen_blob(blob_dir, expected_digest=expected_digest, label=_LABEL)
        document = json.loads(blob.payload_path.read_bytes().decode("utf-8"))
    except (SnapshotError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CiOutputError(f"retained CI output is invalid: {exc}") from exc
    if not isinstance(document, Mapping):
        raise CiOutputError("retained CI output must be an object")
    bound = str(document.get("candidate", ""))
    if bound != candidate_digest:
        raise CiOutputError(
            "retained CI output binds a different candidate than the approved "
            "digest being promoted"
        )
