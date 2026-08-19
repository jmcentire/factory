#!/usr/bin/env python3
"""Smoke the installed Factory distribution without importing the source checkout."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
from pathlib import Path

import jsonschema

import factory_core
from factory_runtime.schema import SCHEMA_NAMES, load_schema


def main() -> int:
    installed_version = importlib.metadata.version("factory-core")
    if factory_core.__version__ != installed_version:
        raise RuntimeError("installed package and runtime versions differ")

    for name in sorted(SCHEMA_NAMES):
        jsonschema.Draft202012Validator.check_schema(load_schema(name))
    if load_schema("runner-receipt-v2")["$id"] != "factory://schemas/runner-receipt/2":
        raise RuntimeError("historical runner-receipt/2 schema is not addressable")
    if load_schema("runner-receipt")["$id"] != "factory://schemas/runner-receipt/3":
        raise RuntimeError("current runner-receipt/3 schema is not addressable")

    factory_cli = Path(sys.executable).with_name("factory")
    completed = subprocess.run(
        [str(factory_cli), "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0 or "usage:" not in completed.stdout.lower():
        raise RuntimeError(f"installed factory --help failed: {completed.stderr.strip()}")

    print(
        f"installed distribution verified: factory-core {installed_version}; "
        f"{len(SCHEMA_NAMES)} schemas"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
