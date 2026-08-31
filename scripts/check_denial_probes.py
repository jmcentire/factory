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
probe is a dead pointer, or a gate is unfalsifiable; 2 = the registry or suite could not be read,
or an ambient environment override was present (a fail-closed refusal — the build cannot verify
coverage, so it does not ship).

Test seam: ``--registry`` and ``--nodeids`` let a unit test point at a temp registry and a
temp node-id set so it does not pay the full pytest-collection cost to exercise the coverage
logic. The seam is explicit argv only: the retired ``GATES_TSV`` / ``DENIAL_PROBE_NODEIDS``
environment variables are REFUSED (exit 2) whenever present, because an ambient variable that
can substitute the registry or the node-id set can flip this ship gate without any invocation
showing it — an ambient environment override is not authority.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = REPO_ROOT / "harness" / "gates.tsv"
# The retired ambient override names. Their presence — not their use — refuses the run:
# a gate that can be redirected by the caller's environment is not a gate.
AMBIENT_OVERRIDES = ("GATES_TSV", "DENIAL_PROBE_NODEIDS")
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


def _refuse_ambient_overrides() -> None:
    """Die when a retired ambient override variable is present, set or not consulted.

    An environment variable that can substitute the registry or the node-id set flips this
    ship gate without the invocation showing it. The explicit ``--registry``/``--nodeids``
    flags are the only seam: they appear in the argv the Makefile pins, so a redirected run
    is visible where the ambient one was not.
    """
    for name in AMBIENT_OVERRIDES:
        if name in os.environ:
            _die(
                f"ambient environment override is not authority — {name} is refused; "
                "unset it (test fixtures pass --registry/--nodeids explicitly)"
            )


def _collect_nodeids(nodeids_file: Path | None) -> set[str]:
    """The set of every pytest node-id the suite can collect, by introspecting the test modules.

    A subprocess ``pytest --collect-only`` is authoritative but slow (and its output format
    varies by version). The registry uses BASE node-ids (``file::test_name``), never
    parametrize instances, so direct module introspection is both faster and sufficient: walk
    ``tests/*.py``, import each, and collect callable ``test_*`` attributes (top-level functions
    and ``Test*`` class methods). A module that fails to import contributes no node-ids, so a
    probe pointing there is correctly flagged as a dead pointer — the same outcome a real
    collection would give for a module that does not collect.

    For the test suite, ``--nodeids`` may name a file holding one node-id per line; that
    substitutes for introspection so a unit test exercises the coverage logic without the
    real suite. Production passes no flags.
    """
    if nodeids_file is not None:
        if not nodeids_file.exists():
            _die(f"--nodeids file not found: {nodeids_file}")
        override_ids = {
            ln.strip()
            for ln in nodeids_file.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        }
        if not override_ids:
            _die("--nodeids file is empty (cannot verify probe coverage)")
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


# ---------------------------------------------------------------------------
# Recursion floor (plan 5.1): a row is gate-on-gate iff its registered probe
# tests import or mutate ANOTHER row's registered impl. Gate subjects are
# object-level artifacts, never another gate's output. The one meta-set below
# (the purity/doctrine/wiring/acceptance/registry guards) is CLOSED BY
# DECLARATION and grows only by human signature; Gate I — the registry gate,
# the sole gate-over-gates — is the single exemption and is terminal.
# ---------------------------------------------------------------------------

_META_GATE_EXEMPTIONS = frozenset({"I"})
_META_CONTROL_TOKENS: dict[str, frozenset[str]] = {
    # token -> the gate ids that OWN it (self-reference is not recursion)
    "check_core_purity": frozenset({"D"}),
    "check_doctrine_sync": frozenset(),
    "check_wiring": frozenset(),
    "check_acceptance": frozenset({"ACC"}),
    "check_denial_probes": frozenset({"I"}),
    "gates.tsv": frozenset({"I"}),
}


def _probe_function_source(repo: Path, probe: str) -> str:
    """The named probe FUNCTION's source only — file-level scanning would let one
    shared test module poison every gate it hosts probes for."""
    path_part, _, test_name = probe.partition("::")
    test_path = repo / path_part
    if not test_path.is_file() or not test_name:
        return ""
    text = test_path.read_text(encoding="utf-8")
    marker = f"def {test_name}("
    start = text.find(marker)
    if start < 0:
        return ""
    end = text.find("\ndef ", start + len(marker))
    return text[start : end if end > 0 else len(text)]


def _recursion_floor_problems(repo: Path, rows: list[dict[str, str]]) -> list[str]:
    problems: list[str] = []
    for row in rows:
        gate = row["gate"].strip()
        if gate in _META_GATE_EXEMPTIONS:
            continue
        for probe in (p.strip() for p in row["probes"].split(";") if p.strip()):
            body = _probe_function_source(repo, probe)
            for token, owners in _META_CONTROL_TOKENS.items():
                if gate in owners:
                    continue
                if token in body:
                    problems.append(
                        f"gate {gate!r} probe {probe!r} references meta control "
                        f"{token!r} owned by another row — gate-on-gate is refused "
                        f"(recursion floor, plan 5.1); Gate I is the sole "
                        f"gate-over-gates"
                    )
    return problems


# ---------------------------------------------------------------------------
# Silent-neutering orphan direction, closed world (plan 5.1): every check
# target in the Makefile ship chain is either a REGISTERED gate's impl or a
# member of the one declared meta-set — a new ship-chain control cannot appear
# unregistered, and a registered one cannot be quietly dropped from the chain.
# The factory_core-symbol orphan direction is refused-as-unrealizable and
# remains the named residual (two-commit neutering of a core-symbol gate).
# ---------------------------------------------------------------------------

_SHIP_CHAIN_DISPOSITIONS: dict[str, str] = {
    # target -> "gate:<id>" (registered) | "meta" (declared meta-set) |
    #           "toolchain" (language hygiene, not a factory gate)
    "check-purity": "gate:D",
    "check-doctrine": "meta",
    "check-wiring": "meta",
    "check-authority": "meta",
    "check-harness": "meta",
    "check-denial-probes": "gate:I",
    "check-acceptance": "gate:ACC",
    "lint": "toolchain",
    "typecheck": "toolchain",
    "test": "toolchain",
}


def _ship_chain_problems(repo: Path, registered_gates: set[str]) -> list[str]:
    makefile = repo / "Makefile"
    if not makefile.is_file():
        return []
    ship_line = ""
    for line in makefile.read_text(encoding="utf-8").splitlines():
        if line.startswith("ship:"):
            ship_line = line
            break
    if not ship_line:
        return ["Makefile has no ship target — the fail-closed chain is gone"]
    targets = [t for t in ship_line.split("##")[0].removeprefix("ship:").split() if t]
    problems: list[str] = []
    for target in targets:
        disposition = _SHIP_CHAIN_DISPOSITIONS.get(target)
        if disposition is None:
            problems.append(
                f"ship-chain target {target!r} has no registered disposition — a new "
                f"control cannot join the chain unregistered (orphan direction, plan 5.1)"
            )
        elif disposition.startswith("gate:"):
            gate = disposition.removeprefix("gate:")
            if gate not in registered_gates:
                problems.append(
                    f"ship-chain target {target!r} claims gate {gate!r} which is not "
                    f"in the registry — silently neutered gate"
                )
    for target in _SHIP_CHAIN_DISPOSITIONS:
        if target not in targets:
            problems.append(
                f"dispositioned target {target!r} is missing from the ship chain — "
                f"a control was quietly dropped"
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(
        description="verify every factory gate has a registered, collecting denial probe"
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=None,
        help="test-fixture seam: a temp gates.tsv (production omits this)",
    )
    parser.add_argument(
        "--nodeids",
        type=Path,
        default=None,
        help="test-fixture seam: a file of node-ids, one per line (production omits this)",
    )
    args = parser.parse_args()
    _refuse_ambient_overrides()
    rows = _load_registry(args.registry if args.registry is not None else DEFAULT_REGISTRY)
    nodeids = _collect_nodeids(args.nodeids)
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

    repo_root = (
        args.registry.parent.parent
        if args.registry is not None
        else DEFAULT_REGISTRY.parent.parent
    )
    if args.registry is None:
        # The closed-world scan runs only against the REAL repo — a fixture
        # registry has no Makefile world to be closed over.
        problems.extend(_ship_chain_problems(repo_root, seen_gates))
    problems.extend(_recursion_floor_problems(repo_root, rows))

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