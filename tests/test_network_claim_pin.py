"""Network-claim pin — the honest claim cannot silently re-strengthen.

The runner lane makes exactly one network claim today: ``unrestricted-outbound``.
That is the honest arm-(b) weakening — the Seatbelt profile grants general
outbound, and the schemas say so with a ``const``. These tests pin the pairing
so the claim cannot drift back to stronger wording (``model-api-only``) without
the enforcement that would make it true. When a provider egress boundary is
enforced and independently tested, this file and the schema consts change
together in ONE ratified change — never separately (contract 780ce1f092f6).
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME = REPO_ROOT / "factory_runtime"
SCHEMAS = RUNTIME / "schemas"
RUNNER_ISOLATION = RUNTIME / "runner_isolation.py"

_PINNED_SCHEMAS = (
    "runner-manifest.schema.json",
    "runner-receipt.schema.json",
    "runner-receipt-v2.schema.json",
)


def test_runner_schemas_pin_network_mode_to_unrestricted_outbound() -> None:
    """Every runner schema pins network_mode with a const of the honest claim —
    a manifest or receipt claiming anything stronger fails validation."""
    for name in _PINNED_SCHEMAS:
        schema = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
        network_mode = schema["properties"]["network_mode"]
        assert network_mode["const"] == "unrestricted-outbound", (
            f"{name}: properties.network_mode.const must pin the honest claim, got {network_mode!r}"
        )


def test_seatbelt_profile_grants_general_outbound() -> None:
    """The Seatbelt profile grants general outbound — the enforcement reality the
    schema const mirrors. When a provider egress boundary is enforced and
    independently tested, this test and the schema consts change together in one
    ratified change — never separately."""
    source = RUNNER_ISOLATION.read_text(encoding="utf-8")
    assert "(allow network-outbound)" in source


def test_stronger_network_claim_absent_from_runtime() -> None:
    """The retired stronger claim ``model-api-only`` appears in no .py or .json
    file under factory_runtime/ — an unenforced strong claim is a lie, and docs
    or tests may only mention it when describing the refusal."""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for pattern in ("*.py", "*.json")
        for path in sorted(RUNTIME.rglob(pattern))
        if "__pycache__" not in path.parts and "model-api-only" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"stronger network claim reappeared in: {offenders}"
