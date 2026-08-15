"""Gate I — the denial-probe registry and its build-time coverage check.

These tests are the META denial probe (registered in harness/gates.tsv as gate ``I``): they
prove that ``scripts/check_denial_probes.py`` fails the build when a gate ships with no probe,
a dead probe pointer, or an unfalsifiable probe. The prohibited action is "a factory gate
ships with no registered, collecting denial probe" — theater. Gate I's own ``red_now`` is
"check_denial_probes.py accepts a gates.tsv with a gate whose probes column is empty, or a
probe node-id that does not collect"; each test below is that mutation made executable.

The one real-suite test (``test_check_passes_when_registry_complete``) runs the check against
the ACTUAL registry and the ACTUAL pytest collection — it is the end-to-end proof that every
factory gate has a collecting probe today. The others exercise the coverage logic against temp
registries and a temp node-id set (so they do not pay the full collection cost).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK = REPO_ROOT / "scripts" / "check_denial_probes.py"
REAL_REGISTRY = REPO_ROOT / "harness" / "gates.tsv"
RUNNER = REPO_ROOT / "harness" / "denial_probe.sh"

# A node-id set that contains the probes these temp registries cite, so "collects" passes for
# the good cases and the only failure is the one each test isolates.
_GOOD_A = "tests/test_harness_scripts.py::test_receipt_machine_derives_test_count"
_GOOD_B = "tests/test_harness_scripts.py::test_dispatch_refuses_without_authority_tuple"


def _run_check(*, registry: Path | str | None, nodeids: Path | None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if registry is not None:
        env["GATES_TSV"] = str(registry)
    else:
        env.pop("GATES_TSV", None)
    if nodeids is not None:
        env["DENIAL_PROBE_NODEIDS"] = str(nodeids)
    else:
        env.pop("DENIAL_PROBE_NODEIDS", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(CHECK)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def _write_registry(path: Path, rows: list[tuple[str, str, str, str, str]]) -> None:
    lines = ["# temp registry for test"]
    for r in rows:
        lines.append("\t".join(r))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_nodeids(path: Path, ids: list[str]) -> None:
    path.write_text("\n".join(ids) + "\n", encoding="utf-8")


def _good_row(gate: str = "A", probes: str = _GOOD_A) -> tuple[str, str, str, str, str]:
    return (gate, "a gate", "the run advances", probes, "the gate is neutered -> probe goes red")


# --- the real registry (the end-to-end Gate I probe) -------------------------------


def test_check_passes_when_registry_complete(tmp_path: Path) -> None:
    """The ACTUAL harness/gates.tsv against the ACTUAL pytest collection must pass: every
    factory gate has a collecting denial probe and a red_now today. This is the end-to-end
    Gate I probe — if it fails, a gate is theater RIGHT NOW. Run without env overrides so the
    check reads the real registry and collects the real suite."""
    proc = _run_check(registry=None, nodeids=None)
    assert proc.returncode == 0, (
        f"check-denial-probes FAILED against the real registry — a gate is theater:\n"
        f"{proc.stderr}\n{proc.stdout}"
    )
    assert "GREEN" in proc.stdout
    # Every gate id the plan names should be registered.
    for gate in [
        "A",
        "B",
        "C",
        "D",
        "E",
        "F",
        "G",
        "H",
        "I",
        "J",
        "K",
        "L",
        "M",
        "N",
        "O",
        "F3",
        "R2",
        "R3",
        "F4",
    ]:
        assert any(line.strip().startswith(gate + "  ") for line in proc.stdout.splitlines()), (
            f"gate {gate} not reported by the coverage check"
        )


# --- the coverage logic (temp registry + temp node-id set) -------------------------


def test_check_fails_when_a_gate_has_no_probe(tmp_path: Path) -> None:
    """red_now for gate I: a gate whose probes column is empty is theater, not coverage."""
    reg = tmp_path / "gates.tsv"
    nodes = tmp_path / "nodeids"
    _write_nodeids(nodes, [_GOOD_A, _GOOD_B])
    _write_registry(reg, [_good_row("A", ""), _good_row("B", _GOOD_B)])
    proc = _run_check(registry=reg, nodeids=nodes)
    assert proc.returncode == 1
    assert "no registered probe" in proc.stderr
    assert "A" in proc.stderr


def test_check_fails_when_a_probe_nodeid_does_not_collect(tmp_path: Path) -> None:
    """A registered probe that does not exist in the suite is a dead pointer — theater that
    looks like coverage. The build fails even though the gate 'has a probe'."""
    reg = tmp_path / "gates.tsv"
    nodes = tmp_path / "nodeids"
    _write_nodeids(nodes, [_GOOD_B])  # does NOT contain _GOOD_A
    _write_registry(reg, [_good_row("A", _GOOD_A), _good_row("B", _GOOD_B)])
    proc = _run_check(registry=reg, nodeids=nodes)
    assert proc.returncode == 1
    assert "does not collect" in proc.stderr
    assert _GOOD_A in proc.stderr


def test_check_fails_when_a_gate_has_no_red_now(tmp_path: Path) -> None:
    """A probe with no declared red_now is unfalsifiable — a probe that cannot be turned red
    is not evidence (the /test skill's falsifiability ledger, machine-checked)."""
    reg = tmp_path / "gates.tsv"
    nodes = tmp_path / "nodeids"
    _write_nodeids(nodes, [_GOOD_A])
    _write_registry(reg, [("A", "a gate", "the run advances", _GOOD_A, "")])
    proc = _run_check(registry=reg, nodeids=nodes)
    assert proc.returncode == 1
    assert "no red_now" in proc.stderr
    assert "unfalsifiable" in proc.stderr


def test_check_fails_on_malformed_row(tmp_path: Path) -> None:
    """A row with the wrong number of columns is a malformed registry — fail-closed (exit 2),
    because a half-parsed registry would silently drop a gate and that is theater."""
    reg = tmp_path / "gates.tsv"
    nodes = tmp_path / "nodeids"
    _write_nodeids(nodes, [_GOOD_A])
    reg.write_text("# header\nA\tonly\ttwo\tcolumns\n", encoding="utf-8")
    proc = _run_check(registry=reg, nodeids=nodes)
    assert proc.returncode == 2
    assert "expected 5 tab columns" in proc.stderr


def test_check_fails_on_duplicate_gate(tmp_path: Path) -> None:
    """Two rows for one gate id is a registry error — which row is the gate?"""
    reg = tmp_path / "gates.tsv"
    nodes = tmp_path / "nodeids"
    _write_nodeids(nodes, [_GOOD_A, _GOOD_B])
    _write_registry(reg, [_good_row("A", _GOOD_A), _good_row("A", _GOOD_B)])
    proc = _run_check(registry=reg, nodeids=nodes)
    assert proc.returncode == 1
    assert "duplicate gate id" in proc.stderr


def test_check_fails_when_registry_missing(tmp_path: Path) -> None:
    """A missing registry cannot be verified — fail-closed (exit 2), never a quiet pass."""
    proc = _run_check(registry=tmp_path / "does-not-exist.tsv", nodeids=None)
    assert proc.returncode == 2
    assert "registry not found" in proc.stderr


# --- the runner (harness/denial_probe.sh) -----------------------------------------


def test_denial_probe_runner_lists_gates() -> None:
    """denial_probe.sh --list enumerates the registered gates and exits 0."""
    proc = subprocess.run(
        ["bash", str(RUNNER), "--list"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert proc.returncode == 0
    assert "A" in proc.stdout
    assert "M" in proc.stdout
    assert "F3" in proc.stdout
    assert "probes:" in proc.stdout


def test_denial_probe_runner_rejects_unknown_gate() -> None:
    """An unknown gate id is a usage error (exit 64), not a pass and not a probe failure."""
    proc = subprocess.run(
        ["bash", str(RUNNER), "ZZZ"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert proc.returncode == 64
    assert "unknown gate" in proc.stderr
