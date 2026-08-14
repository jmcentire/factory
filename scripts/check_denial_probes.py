#!/usr/bin/env python3
"""check_denial_probes.py — no factory gate ships without a registered, collecting denial probe.

Gate I of the control-structure plan (slice 6). The plan's rule (Part 2 §5; Amendment 2.4):
"a gate without a passing probe is theater and fails the build." A *denial* probe tests the
end-to-end blocking path (probe triggers -> gate fires -> artifact NOT promoted -> run does NOT
advance), never the fix's artifact (an internal function returning False). This script enforces
the COVERAGE half of that rule at build time: it reads the gate registry (``harness/gates.tsv``)
and fails the build when:

- a gate has no registered probe (the theater case — a gate that exists with nothing watching it),
- a registered probe node-id does not collect in the pytest suite (a stale or mistyped pointer:
  the gate *believes* it is probed but the probe is gone — theater that looks like coverage),
- a gate has no ``red_now`` declared (an unfalsifiable probe — one that cannot be turned red is
  not evidence; the ``/test`` skill's falsifiability ledger made machine-checkable).

The PASSING half (each probe actually blocks) is enforced by ``make test`` running every
registered probe and the suite being green: a registered probe that fails there fails the build
too. This script does not re-run the probes — it guarantees none is missing and none is a dead
pointer. Together with a green ``make test``, that is "every gate has a passing, end-to-end
denial probe."

Honest scope (the next proxy, plan Part 5 §4). This script verifies coverage, collection, and
declared falsifiability — not that the probe tests the *prohibited action* rather than its
artifact. That deeper check (apply the ``red_now`` mutation, confirm the probe goes red, revert)
is the named residual: the registry's ``red_now`` column carries the mutation description so a
future runner can automate it, but automating per-gate mutation application is fragile and is
not done here. A probe that passes while the gate is neutered is the residual theater this check
cannot catch; the ``red_now`` declaration is the contract that names it.

Exit codes: 0 = every gate has a collecting probe and a red_now; 1 = a gate is uncovered, a
probe is a dead pointer, or a gate is unfalsifiable; 2 = the registry or suite could not be read
(a fail-closed refusal — the build cannot verify coverage, so it does not ship).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Env overrides exist for the test suite: a test points at a temp registry and a temp
# node-id set so it does not pay the full pytest-collection cost (or depend on the real
# suite) to exercise the coverage logic. Production runs leave them unset.
REGISTRY = Path(os.environ.get("GATES_TSV", REPO_ROOT / "harness" / "gates.tsv"))
FIELDS = ("gate", "name", "prohibits", "probes", "red_now")


def _die(msg: str, code: int = 2) -> None:
    print(f"check-denial-probes: {msg}", file=sys.stderr)
    sys.exit(code)


def _load_registry(path: Path) -> list[dict[str, str]]:
    """Parse gates.tsv into a list of gate rows. ``#`` lines and blanks are skipped.

    Each data row is TAB-separated into the FIELDS columns; a row with the wrong field
    count is a malformed registry (fail-closed, exit 2) — a half-parsed registry would
    silently drop a gate and that is the theater this check exists to prevent.
    """
    if not path.exists():
        _die(f"registry not found: {path}")
    rows: list[dict[str, str]] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        cols = raw.rstrip("\n").split("\t")
        if len(cols) != len(FIELDS):
            _die(f"{path}:{lineno}: expected {len(FIELDS)} tab columns, got {len(cols)}")
        rows.append(dict(zip(FIELDS, cols, strict=False)))
    if not rows:
        _die(f"registry {path} has no gate rows")
    return rows


def _collect_nodeids() -> set[str]:
    """The set of every pytest node-id the suite can collect, by introspecting the test modules.

    A subprocess ``pytest --collect-only`` is authoritative but slow (and its output format
    varies by version). The registry uses BASE node-ids (``file::test_name``), never
    parametrize instances, so direct module introspection is both faster and sufficient: walk
    ``tests/*.py``, import each, and collect callable ``test_*`` attributes (top-level functions
    and ``Test*`` class methods). A module that fails to import contributes no node-ids, so a
    probe pointing there is correctly flagged as a dead pointer — the same outcome a real
    collection would give for a module that does not collect.

    For the test suite, ``DENIAL_PROBE_NODEIDS`` may name a file holding one node-id per line;
    that substitutes for introspection so a unit test exercises the coverage logic without the
    real suite. Production leaves it unset.
    """
    override = os.environ.get("DENIAL_PROBE_NODEIDS")
    if override:
        p = Path(override)
        if not p.exists():
            _die(f"DENIAL_PROBE_NODEIDS file not found: {p}")
        override_ids = {
            ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()
        }
        if not override_ids:
            _die("DENIAL_PROBE_NODEIDS file is empty (cannot verify probe coverage)")
        return override_ids

    import importlib.util
    import inspect

    # conftest.py inserts REPO_ROOT on sys.path so test modules can `import factory_core`;
    # replicate it here because introspection does not run conftest.
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    nodeids: set[str] = set()
    for mod_path in sorted((REPO_ROOT / "tests").glob("test_*.py")):
        rel = f"tests/{mod_path.name}"
        mod_name = f"_denial_probe_collect_{mod_path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, mod_path)
            assert spec and spec.loader  # noqa: S101  (spec_from_file_location always returns one)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            # An unimportable module collects nothing — a probe pointing here is a dead pointer,
            # which the membership check below flags. Do not crash the check on one bad module.
            continue
        for name, obj in vars(module).items():
            if name.startswith("test") and callable(obj) and not name.startswith("_"):
                nodeids.add(f"{rel}::{name}")
        for cls_name, cls in vars(module).items():
            if cls_name.startswith("Test") and inspect.isclass(cls):
                for name, obj in vars(cls).items():
                    if name.startswith("test") and callable(obj):
                        nodeids.add(f"{rel}::{cls_name}::{name}")
    if not nodeids:
        _die("test-module introspection returned no node-ids (cannot verify probe coverage)")
    return nodeids


def main() -> int:
    rows = _load_registry(REGISTRY)
    nodeids = _collect_nodeids()
    seen_gates: set[str] = set()
    problems: list[str] = []

    for row in rows:
        gate = row["gate"].strip()
        if not gate:
            problems.append("a registry row has an empty gate id")
            continue
        if gate in seen_gates:
            problems.append(f"duplicate gate id {gate!r} in the registry")
        seen_gates.add(gate)

        probes_raw = row["probes"].strip()
        if not probes_raw:
            problems.append(
                f"gate {gate!r} has no registered probe — theater (add a probe to gates.tsv)"
            )
            continue
        probes = [p.strip() for p in probes_raw.split(";") if p.strip()]
        if not probes:
            problems.append(f"gate {gate!r} probes column is non-empty but yields no node-ids")
            continue
        for p in probes:
            if p not in nodeids:
                problems.append(
                    f"gate {gate!r} probe {p!r} does not collect in the suite — "
                    "dead pointer (theater that looks like coverage)"
                )

        red_now = row["red_now"].strip()
        if not red_now:
            problems.append(
                f"gate {gate!r} has no red_now — an unfalsifiable probe is theater "
                "(name the mutation that turns it red)"
            )

    # Report coverage (gate -> #probes), then problems.
    print(f"check-denial-probes: {len(rows)} gates registered, {len(nodeids)} node-ids collectable")
    for row in rows:
        n = len([p for p in row["probes"].split(";") if p.strip()])
        print(f"  {row['gate']:>3}  {row['name']:<46}  probes={n}")

    if problems:
        print("\ncheck-denial-probes: FAILED — theater detected:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print("check-denial-probes: GREEN — every gate has a collecting denial probe and a red_now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())