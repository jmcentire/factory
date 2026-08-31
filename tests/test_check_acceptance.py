"""Forcing tests for check_acceptance.py (remediation plan §0.3).

The acceptance instrument's numbers must derive from committed data and the git
tree. These tests force each refusal: a ledger that lies about the tree, a
hand-maintained LOC field, an unclassified kind, a deadline expiry promoted to a
rewarded signal, an uncited invented number, and an ambient override.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECKER = REPO / "scripts" / "check_acceptance.py"


def _run(
    tmp_path: Path,
    *,
    ledger_rows: list[dict] | None = None,
    baseline: dict | None = None,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    args = [sys.executable, str(CHECKER)]
    if ledger_rows is not None:
        ledger = tmp_path / "ledger.jsonl"
        ledger.write_text(
            "".join(json.dumps(row) + "\n" for row in ledger_rows), encoding="utf-8"
        )
        args += ["--ledger", str(ledger)]
    if baseline is not None:
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps(baseline), encoding="utf-8")
        args += ["--baseline", str(path)]
    import os

    env = {k: v for k, v in os.environ.items() if k not in ("REMOVAL_LEDGER",)}
    env.update(env_extra or {})
    return subprocess.run(args, capture_output=True, text=True, env=env)


def _good_baseline() -> dict:
    return json.loads((REPO / "acceptance_baseline.json").read_text(encoding="utf-8"))


def test_checker_green_on_committed_repo_data(tmp_path: Path) -> None:
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr


def test_landed_delete_row_that_lies_about_the_tree_fails(tmp_path: Path) -> None:
    r = _run(
        tmp_path,
        ledger_rows=[
            {
                "phase": "x",
                "axis": "x",
                "kind": "delete",
                "subject": {"path": "factory_core/manifest.py"},
                "note": "lie",
                "status": "landed",
            }
        ],
    )
    assert r.returncode == 1
    assert "still exists at HEAD" in r.stderr


def test_hand_maintained_loc_field_is_refused(tmp_path: Path) -> None:
    r = _run(
        tmp_path,
        ledger_rows=[
            {
                "phase": "x",
                "axis": "x",
                "kind": "add",
                "subject": {"path": "scripts/check_acceptance.py"},
                "removed_loc": 40,
                "status": "landed",
            }
        ],
    )
    assert r.returncode == 1
    assert "hand-maintained" in r.stderr


def test_registered_kind_without_classification_fails(tmp_path: Path) -> None:
    baseline = _good_baseline()
    del baseline["no_relevant_kinds"]["refusal-promote"]
    r = _run(tmp_path, baseline=baseline)
    assert r.returncode == 1
    assert "unclassified" in r.stderr and "refusal-promote" in r.stderr


def test_deadline_expiry_cannot_be_promoted_to_a_rewarded_signal(tmp_path: Path) -> None:
    baseline = _good_baseline()
    baseline["no_relevant_kinds"]["watchdog-deadline"] = True
    r = _run(tmp_path, baseline=baseline)
    assert r.returncode == 1
    assert "bound" in r.stderr


def test_blocking_written_must_stay_excluded(tmp_path: Path) -> None:
    baseline = _good_baseline()
    baseline["excluded_event_kinds"] = []
    r = _run(tmp_path, baseline=baseline)
    assert r.returncode == 1
    assert "blocking_written" in r.stderr


def test_underived_without_justification_fails(tmp_path: Path) -> None:
    baseline = _good_baseline()
    baseline["baseline_rows"] = [
        {"metric": "m", "run": "r", "value": 1, "artifact": "UNDERIVED", "justification": ""}
    ]
    r = _run(tmp_path, baseline=baseline)
    assert r.returncode == 1
    assert "UNDERIVED without justification" in r.stderr


def test_cited_artifact_digest_mismatch_fails(tmp_path: Path) -> None:
    baseline = _good_baseline()
    baseline["baseline_rows"] = [
        {
            "metric": "m",
            "run": "r",
            "value": 1,
            "artifact": {"path": "acceptance_baseline.json", "sha256": "0" * 64},
        }
    ]
    r = _run(tmp_path, baseline=baseline)
    assert r.returncode == 1
    assert "digest mismatch" in r.stderr


def test_ambient_override_is_refused_on_presence(tmp_path: Path) -> None:
    r = _run(tmp_path, env_extra={"REMOVAL_LEDGER": "/tmp/x"})
    assert r.returncode == 2
    assert "ambient override" in r.stderr
