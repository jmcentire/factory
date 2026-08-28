"""Forcing tests for the two wiring-audit hardening checks (kindex `3910eaa6c7e5`),
both surfaced by the cold frame-check seat's review of dogfood run 1:

* the baseline-pre-seeding vector — an orphan shipped with its own exact-match
  baseline entry in the same change passes green, defeating "new code turns red";
* stale baseline entries silently masking a finding string's future reappearance.

Each test builds a REAL git repository under ``tmp_path`` (not a synthetic tree
inspected in isolation) because the diff guard's whole subject is "what changed
against HEAD" — there is no way to force that condition without real git history.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK_WIRING = REPO_ROOT / "scripts" / "check_wiring.py"


def _run(root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK_WIRING), "--root", str(root), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )


def _init_clean_repo(root: Path) -> None:
    """A real git repo, one clean commit, wiring green, empty baseline."""
    _write(root / "factory_core" / "__init__.py", "")
    _write(
        root / "factory_core" / "alpha.py",
        """\
        def used_function():
            return "used"
        """,
    )
    _write(root / "factory_runtime" / "__init__.py", "")
    _write(
        root / "factory_runtime" / "cli.py",
        """\
        from factory_core.alpha import used_function

        RESULT = used_function()
        """,
    )
    _write(
        root / "scripts" / "run_synthetic.py",
        "import factory_runtime.cli\n",
    )
    (root / "harness").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)
    _write(root / "wiring_baseline.json", "[]")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "clean baseline commit")


def test_baseline_pre_seeded_with_its_own_finding_is_caught(tmp_path) -> None:
    """The exact vector the cold seat found: orphan + suppressing entry in one diff."""

    root = tmp_path / "repo"
    _init_clean_repo(root)

    # Uncommitted change: introduce a new file with a dead export AND baseline it,
    # both in the same (as-yet-uncommitted) diff against HEAD.
    orphan_finding = "zero-caller-export:factory_core/alpha.py:orphan_export"
    with (root / "factory_core" / "alpha.py").open("a", encoding="utf-8") as f:
        f.write('\n\ndef orphan_export():\n    return "nobody references me"\n')
    (root / "wiring_baseline.json").write_text(
        json.dumps([{"finding": orphan_finding, "justification": "pre-seeded same-diff"}]),
        encoding="utf-8",
    )

    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "baseline-added-with-finding:factory_core/alpha.py:-" in result.stdout
    # The suppressed finding itself is (correctly) not re-emitted — the guard fires on
    # the SEPARATE, non-suppressible-by-that-same-entry class, not a duplicate report.
    assert orphan_finding not in result.stdout.splitlines()


def test_baseline_entry_for_a_file_unchanged_in_the_diff_is_not_flagged(tmp_path) -> None:
    """A genuinely pre-existing baseline entry, added in a later, unrelated diff, is fine."""

    root = tmp_path / "repo"
    _init_clean_repo(root)
    pre_existing_finding = "zero-caller-export:factory_core/alpha.py:used_function"
    # Baseline a symbol whose FILE is not touched in this diff at all (only the
    # baseline file itself changes) — this must not trigger the pre-seeding guard.
    (root / "wiring_baseline.json").write_text(
        json.dumps(
            [{"finding": pre_existing_finding, "justification": "unrelated later baseline"}]
        ),
        encoding="utf-8",
    )
    # used_function is still referenced by cli.py, so this baseline entry is inert —
    # that's fine, the guard only cares whether alpha.py itself changed in this diff.
    result = _run(root)
    assert "baseline-added-with-finding" not in result.stdout
    assert result.returncode == 0, result.stdout + result.stderr


def test_baseline_pre_seeding_guard_is_itself_suppressible_with_justification(tmp_path) -> None:
    """The guard's own finding follows the same universal, disclosed suppression path."""

    root = tmp_path / "repo"
    _init_clean_repo(root)
    orphan_finding = "zero-caller-export:factory_core/alpha.py:orphan_export"
    with (root / "factory_core" / "alpha.py").open("a", encoding="utf-8") as f:
        f.write('\n\ndef orphan_export():\n    return "nobody references me"\n')
    guard_finding = "baseline-added-with-finding:factory_core/alpha.py:-"
    (root / "wiring_baseline.json").write_text(
        json.dumps(
            [
                {"finding": orphan_finding, "justification": "pre-seeded same-diff"},
                {"finding": guard_finding, "justification": "reviewed and accepted by operator"},
            ]
        ),
        encoding="utf-8",
    )
    result = _run(root)
    assert result.returncode == 0, result.stdout + result.stderr


def test_guard_skipped_outside_a_git_repo_with_a_clear_note(tmp_path) -> None:
    root = tmp_path / "not_a_repo"
    _write(root / "factory_core" / "__init__.py", "")
    _write(
        root / "factory_core" / "alpha.py",
        """\
        def used_function():
            return "used"
        """,
    )
    _write(root / "factory_runtime" / "__init__.py", "")
    _write(
        root / "factory_runtime" / "cli.py",
        "from factory_core.alpha import used_function\nRESULT = used_function()\n",
    )
    _write(root / "scripts" / "run_synthetic.py", "import factory_runtime.cli\n")
    (root / "harness").mkdir(parents=True, exist_ok=True)
    result = _run(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "baseline-pre-seeding guard skipped" in result.stderr


def test_stale_baseline_entry_is_warned_not_silently_dropped(tmp_path) -> None:
    root = tmp_path / "repo"
    _init_clean_repo(root)
    stale_finding = "zero-caller-export:factory_core/alpha.py:a_function_that_no_longer_exists"
    (root / "wiring_baseline.json").write_text(
        json.dumps([{"finding": stale_finding, "justification": "no longer applies"}]),
        encoding="utf-8",
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "commit the stale baseline so the diff guard is quiet")
    result = _run(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "WARN" in result.stderr
    assert stale_finding in result.stderr


def test_no_stale_warning_when_every_baseline_entry_is_live(tmp_path) -> None:
    root = tmp_path / "repo"
    _init_clean_repo(root)
    live_finding = "zero-caller-export:factory_core/alpha.py:used_function"
    # Make it genuinely unreferenced so the baseline entry is live, not stale.
    (root / "factory_runtime" / "cli.py").write_text(
        "import factory_core.alpha\n", encoding="utf-8"
    )
    (root / "wiring_baseline.json").write_text(
        json.dumps([{"finding": live_finding, "justification": "still applies"}]),
        encoding="utf-8",
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "commit so the diff guard is quiet")
    result = _run(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "WARN" not in result.stderr
