"""Compatibility import for the eval scripts; runtime owns the single rulebook."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "harness"))
from jev_rules import *  # noqa: F403,E402
