"""Test configuration — make the repo root importable without requiring an install.

Inserting the repo root on ``sys.path`` lets ``import factory_core`` (and importing the
``scripts`` guard) work whether or not the package has been pip-installed, so ``make test``
runs from a bare checkout.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SYNTHETIC_TARGET = FIXTURES / "synthetic_target" / "target.toml"
