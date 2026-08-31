"""Wiring-audit oracles — contract tests for scripts/check_wiring.py over synthetic trees.

Written blind to the implementation, purely against the ratified interface contract:
findings on stdout as ``<class>:<repo-relative-path>:<symbol-or-dash>`` (sorted); exit codes
0 (clean beyond baseline) / 1 (non-baselined findings) / 2 (internal/usage error); a JSON-array
baseline of ``{"finding": ..., "justification": ...}`` entries suppressing exact matches only;
entrypoints are factory_runtime/cli.py, scripts/*.py, harness/*.py — tests are never entrypoints.
Every test builds its own synthetic mini-tree under tmp_path and invokes the real script with
--root pointing at that tree.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK_WIRING = REPO_ROOT / "scripts" / "check_wiring.py"

# The exact finding line the seeded dead export must produce, per the contract's line format.
DEAD_FINDING = "zero-caller-export:factory_core/alpha.py:orphan_export"

_DEAD_EXPORT = """

def orphan_export():
    return "nobody references me"
"""


def _run(root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    """Invoke the real wiring gate against a synthetic tree; never raise on exit code."""
    return subprocess.run(
        [sys.executable, str(CHECK_WIRING), "--root", str(root), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def _append(path: Path, text: str) -> None:
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def _make_clean_tree(root: Path) -> None:
    """Build a synthetic mini-repo where every public symbol is reachable from an entrypoint.

    Re-running over the same root rewrites every file back to the clean state.
    """
    _write(root / "factory_core" / "__init__.py", "")
    _write(
        root / "factory_core" / "alpha.py",
        """\
        # Synthetic module whose public surface is fully wired.


        def used_function():
            return "used"


        class UsedClass:
            pass
        """,
    )
    _write(root / "factory_runtime" / "__init__.py", "")
    _write(
        root / "factory_runtime" / "cli.py",
        """\
        # Synthetic CLI entrypoint: statically references every public symbol.

        from factory_core.alpha import UsedClass, used_function

        RESULT = used_function()
        INSTANCE = UsedClass()
        """,
    )
    _write(
        root / "scripts" / "run_synthetic.py",
        """\
        # Synthetic script entrypoint keeping the runtime package itself reachable.

        import factory_runtime.cli
        """,
    )
    (root / "harness").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)


def _add_dead_export(root: Path) -> None:
    _append(root / "factory_core" / "alpha.py", _DEAD_EXPORT)


def _write_baseline(path: Path, finding: str) -> None:
    import hashlib as _h

    justification = "ratified synthetic suppression"
    entries = [{
        "finding": finding,
        "justification": justification,
        "owner": "human:founder",
        "expires": "2099-01-01",
        "justification_digest": "sha256:"
        + _h.sha256(justification.encode("utf-8")).hexdigest(),
    }]
    path.write_text(json.dumps(entries), encoding="utf-8")


def test_wiring_red_on_seeded_dead_export(tmp_path) -> None:
    """Doneness scenario, both directions: clean is green, a seeded dead export turns red,
    removing it turns green again. The clean run also exercises the missing-baseline default
    (no baseline file in the tree means an empty baseline)."""
    root = tmp_path / "synthetic"
    _make_clean_tree(root)

    clean = _run(root)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert clean.stdout.strip() == "", clean.stdout

    _add_dead_export(root)
    seeded = _run(root)
    assert seeded.returncode == 1, seeded.stdout + seeded.stderr
    assert DEAD_FINDING in seeded.stdout.splitlines(), seeded.stdout

    _make_clean_tree(root)  # rewrites alpha.py without the orphan
    again = _run(root)
    assert again.returncode == 0, again.stdout + again.stderr
    assert again.stdout.strip() == "", again.stdout


def test_import_does_not_mask(tmp_path) -> None:
    """An import of the dead export from the tree's tests/ dir must not count as wiring —
    tests are not entrypoints, so the zero-caller-export finding must survive."""
    root = tmp_path / "synthetic"
    _make_clean_tree(root)
    _add_dead_export(root)
    _write(
        root / "tests" / "test_uses_orphan.py",
        """\
        # Synthetic test importing the orphan; tests are never entrypoints.

        from factory_core.alpha import orphan_export


        def test_orphan():
            assert orphan_export() == "nobody references me"
        """,
    )

    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    assert DEAD_FINDING in result.stdout.splitlines(), result.stdout


def test_fail_closed_on_parse_failure(tmp_path) -> None:
    """A syntactically invalid module in factory_core is a parse-failure finding (exit 1);
    the run must report it, never silently skip the file."""
    root = tmp_path / "synthetic"
    _make_clean_tree(root)
    _write(
        root / "factory_core" / "broken.py",
        """\
        # Synthetic module with a syntax error.

        def broken(:
        """,
    )

    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    parse_lines = [
        line
        for line in result.stdout.splitlines()
        if line.startswith("parse-failure:factory_core/broken.py:")
    ]
    assert parse_lines, result.stdout


def test_baseline_is_data(tmp_path) -> None:
    """A baseline entry exactly matching the finding line suppresses it (exit 0); a
    non-matching entry suppresses nothing (exit 1, finding still emitted)."""
    root = tmp_path / "synthetic"
    _make_clean_tree(root)
    _add_dead_export(root)

    matching = tmp_path / "matching_baseline.json"
    _write_baseline(matching, DEAD_FINDING)
    suppressed = _run(root, "--baseline", str(matching))
    assert suppressed.returncode == 0, suppressed.stdout + suppressed.stderr
    assert DEAD_FINDING not in suppressed.stdout.splitlines(), suppressed.stdout

    near_miss = tmp_path / "near_miss_baseline.json"
    _write_baseline(near_miss, "zero-caller-export:factory_core/alpha.py:some_other_symbol")
    unsuppressed = _run(root, "--baseline", str(near_miss))
    assert unsuppressed.returncode == 1, unsuppressed.stdout + unsuppressed.stderr
    assert DEAD_FINDING in unsuppressed.stdout.splitlines(), unsuppressed.stdout


def test_unresolved_reference_is_conservative(tmp_path) -> None:
    """Dynamic getattr must yield an unresolved-reference finding, never silent success.

    Characterization probe: unresolved-reference-conservative — dynamic references are never silent.

    The dynamic-lookup function itself is wired from the entrypoint, so the only defect in the
    tree is the statically unresolvable reference. The contract fixes the finding class and the
    exit code; which file the finding names (reference site vs. target module) is left to the
    implementation, so only the class prefix is asserted.
    """
    root = tmp_path / "synthetic"
    _make_clean_tree(root)
    _write(
        root / "factory_core" / "dyn.py",
        """\
        # Synthetic module reaching a sibling symbol only through a dynamic name.

        from factory_core import alpha


        def dynamic_lookup(name):
            return getattr(alpha, name)
        """,
    )
    _append(
        root / "factory_runtime" / "cli.py",
        "\nfrom factory_core.dyn import dynamic_lookup\n\nHOOK = dynamic_lookup\n",
    )

    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    unresolved = [
        line for line in result.stdout.splitlines() if line.startswith("unresolved-reference:")
    ]
    assert unresolved, result.stdout


def test_unreachable_module_is_reported(tmp_path) -> None:
    """A module no entrypoint transitively reaches is an unreachable-module finding (exit 1).

    The island module carries only a private helper, so zero-caller-export cannot apply and the
    unreachable-module class is pinned unambiguously.
    """
    root = tmp_path / "synthetic"
    _make_clean_tree(root)
    _write(
        root / "factory_core" / "island.py",
        """\
        # Synthetic module nothing imports; only a private helper, so no public exports.


        def _private_helper():
            return "island"
        """,
    )

    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    island = [
        line
        for line in result.stdout.splitlines()
        if line.startswith("unreachable-module:factory_core/island.py")
    ]
    assert island, result.stdout


def test_default_baseline_is_read_from_root(tmp_path) -> None:
    """Without --baseline the tool reads wiring_baseline.json under --root."""
    root = tmp_path / "synthetic"
    _make_clean_tree(root)
    _add_dead_export(root)
    _write_baseline(root / "wiring_baseline.json", DEAD_FINDING)

    result = _run(root)
    assert result.returncode == 0, result.stdout + result.stderr


def test_exit_2_on_nonexistent_root(tmp_path) -> None:
    """A --root that does not exist is a usage error (exit 2), never a silent pass."""
    result = _run(tmp_path / "does_not_exist")
    assert result.returncode == 2, result.stdout + result.stderr


def test_determinism_two_runs_identical_sorted_stdout(tmp_path) -> None:
    """Two runs over the same tree emit byte-identical stdout, and findings arrive sorted.

    Both seeded findings share one class, so line-sort order is unambiguous under the contract.
    """
    root = tmp_path / "synthetic"
    _make_clean_tree(root)
    _add_dead_export(root)
    _write(
        root / "factory_core" / "beta.py",
        """\
        # Second wired synthetic module carrying its own dead export.


        def used_beta():
            return "beta"


        def second_orphan():
            return "also unreferenced"
        """,
    )
    _append(
        root / "factory_runtime" / "cli.py",
        "\nfrom factory_core.beta import used_beta\n\nBETA = used_beta()\n",
    )

    first = _run(root)
    second = _run(root)
    assert first.returncode == 1, first.stdout + first.stderr
    assert first.stdout == second.stdout
    lines = [line for line in first.stdout.splitlines() if line]
    assert len(lines) >= 2, first.stdout
    assert lines == sorted(lines), first.stdout
    assert DEAD_FINDING in lines, first.stdout
    assert "zero-caller-export:factory_core/beta.py:second_orphan" in lines, first.stdout
