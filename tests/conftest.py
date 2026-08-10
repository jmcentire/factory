"""Test configuration — make the repo root importable without requiring an install.

Inserting the repo root on ``sys.path`` lets ``import factory_core`` (and importing the
``scripts`` guard) work whether or not the package has been pip-installed, so ``make test``
runs from a bare checkout.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SYNTHETIC_TARGET = FIXTURES / "synthetic_target" / "target.toml"


def ratification_receipts(phase: str) -> dict[str, str]:
    """The two receipt digests a `*-ratified` transition must name, for a test driving the store.

    `RunStore.transition` refuses a ratification that does not name a human receipt and a distinct
    Validator receipt (`factory_runtime.state._require_ratification_receipts`), so a test that walks
    the states directly has to supply them. Derived from the phase name so every value is distinct
    from every other and from any artifact digest — the store checks distinctness, and a helper that
    handed back one constant would defeat the check it exists to satisfy.

    These are stand-in digests, NOT verified receipts. Only `WorkflowEngine.ratify_phase` verifies a
    receipt; see `tests/test_runtime_workflow.py` and the real-Tessera integration test for that.
    """
    return {
        f"{phase}:{role}-receipt": "sha256:"
        + hashlib.sha256(f"{phase}:{role}-receipt".encode()).hexdigest()
        for role in ("human", "validator")
    }
