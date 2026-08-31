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


def test_external_citation_mismatch_on_readable_artifact_fails(tmp_path: Path) -> None:
    """A readable external artifact with a wrong digest is a lie, not a gap."""
    external = tmp_path / "retained.log"
    external.write_text("run artifact bytes", encoding="utf-8")
    baseline = _good_baseline()
    baseline["baseline_rows"] = [
        {
            "metric": "m",
            "run": "r",
            "value": 1,
            "artifacts": [{"role": "t0", "path": str(external), "sha256": "0" * 64}],
        }
    ]
    r = _run(tmp_path, baseline=baseline)
    assert r.returncode == 1
    assert "digest mismatch" in r.stderr


def test_external_citation_absent_is_a_loud_note_not_a_failure(tmp_path: Path) -> None:
    """Tri-state: a retained-run artifact missing on this machine is 'could not
    check' — loud on stdout, never silently green, never a ship failure."""
    baseline = _good_baseline()
    baseline["baseline_rows"] = [
        {
            "metric": "m",
            "run": "r",
            "value": 1,
            "artifacts": [
                {"role": "t0", "path": str(tmp_path / "gone.log"), "sha256": "0" * 64}
            ],
        }
    ]
    r = _run(tmp_path, baseline=baseline)
    assert r.returncode == 0, r.stderr
    assert "external citation not verifiable here" in r.stdout


def test_in_repo_citation_stays_strict(tmp_path: Path) -> None:
    baseline = _good_baseline()
    baseline["baseline_rows"] = [
        {
            "metric": "m",
            "run": "r",
            "value": 1,
            "artifacts": [{"role": "t0", "path": "no/such/file.json", "sha256": "0" * 64}],
        }
    ]
    r = _run(tmp_path, baseline=baseline)
    assert r.returncode == 1
    assert "unreadable" in r.stderr


def test_moved_pre_tag_fails_against_the_pinned_commit(tmp_path: Path) -> None:
    """Round-3 G2 (reproduced live by the verification seat): the boundary is
    pinned by COMMIT in committed data — a re-pointed tag fails, by name alone
    it would not."""
    baseline = _good_baseline()
    baseline["pre_tag_commit"] = "f" * 40
    r = _run(tmp_path, baseline=baseline)
    assert r.returncode == 1
    assert "the boundary tag moved" in r.stderr


def test_missing_pre_tag_commit_pin_fails(tmp_path: Path) -> None:
    baseline = _good_baseline()
    del baseline["pre_tag_commit"]
    r = _run(tmp_path, baseline=baseline)
    assert r.returncode == 1
    assert "movable by name" in r.stderr


def test_terminal_kind_relevance_owned_by_registry(tmp_path: Path) -> None:
    """Round-3 G4: one owner per fact — a baseline classification contradicting
    the terminal registry's signal/bound class is a fork of the fact."""
    baseline = _good_baseline()
    baseline["no_relevant_kinds"]["preflight"] = False  # registry says signal
    r = _run(tmp_path, baseline=baseline)
    assert r.returncode == 1
    assert "registry owns this fact" in r.stderr


def test_fabricated_gate_retirement_fails_closed(tmp_path: Path) -> None:
    """Round-3 D2: a retirement claim over a gate that never existed at pre_tag
    is a fabrication, not a removal."""
    r = _run(
        tmp_path,
        ledger_rows=[
            {
                "phase": "x",
                "axis": "4",
                "kind": "gate-retire",
                "subject": {"gate": "Z9"},
                "note": "fabricated",
                "status": "landed",
            }
        ],
    )
    assert r.returncode == 1
    assert "never existed at" in r.stderr


def test_gate_retire_flow_with_parametrized_probe(tmp_path: Path) -> None:
    """Round-3 D2 end-to-end in a scratch repo: a real retirement passes only
    when the gate's pre_tag probes (parametrized node-ids included) are gone;
    a surviving probe function fails."""
    import subprocess as sp

    repo = tmp_path / "repo"
    (repo / "harness").mkdir(parents=True)
    (repo / "tests").mkdir()

    def git(*args: str) -> None:
        sp.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (repo / "harness" / "gates.tsv").write_text(
        "X\tname\tprohibits\ttests/x_test.py::test_x[param-1]\tred\n", encoding="utf-8"
    )
    (repo / "harness" / "refusal_event_kinds.json").write_text(
        json.dumps({"kinds": {"refusal-x": {"class": "signal", "description": "d"}}}),
        encoding="utf-8",
    )
    (repo / "harness" / "terminal_no_kinds.json").write_text(
        json.dumps({"kinds": {"operator": {"class": "signal", "description": "d"}}}),
        encoding="utf-8",
    )
    (repo / "tests" / "x_test.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "pre")
    git("tag", "-a", "pre", "-m", "boundary")
    sha = sp.run(
        ["git", "-C", str(repo), "rev-parse", "pre^{commit}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    # Retire the gate at HEAD but leave the probe function alive.
    (repo / "harness" / "gates.tsv").write_text("# empty\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "retire")

    baseline = {
        "pre_tag": "pre",
        "pre_tag_commit": sha,
        "no_relevant_kinds": {"refusal-x": True, "operator": True},
        "excluded_event_kinds": ["blocking_written"],
        "baseline_rows": [],
    }
    ledger = [
        {
            "phase": "x", "axis": "4", "kind": "gate-retire",
            "subject": {"gate": "X"}, "note": "retired", "status": "landed",
        }
    ]
    baseline_path = tmp_path / "b.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    ledger_path = tmp_path / "l.jsonl"
    ledger_path.write_text("".join(json.dumps(r) + "\n" for r in ledger), encoding="utf-8")

    def run_checker() -> sp.CompletedProcess:
        import os
        env = dict(os.environ)
        return sp.run(
            [sys.executable, str(CHECKER), "--repo", str(repo),
             "--ledger", str(ledger_path), "--baseline", str(baseline_path)],
            capture_output=True, text=True, env=env,
        )

    surviving = run_checker()
    assert surviving.returncode == 1
    assert "probe test_x survives" in surviving.stderr

    # Kill the probe function; the retirement now verifies.
    (repo / "tests" / "x_test.py").write_text("def test_other():\n    pass\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "probe dies with its gate")
    clean = run_checker()
    assert clean.returncode == 0, clean.stderr


def test_required_metrics_floor_pins_the_reference_corpus(tmp_path: Path) -> None:
    """Round-3 G3 pre-wire: with required_metrics ratified into the baseline,
    thinning the corpus below the floor fails ship."""
    baseline = _good_baseline()
    baseline["required_metrics"] = [
        {"metric": "first_no_relevant_signal_hours", "min_rows": 4},
        {"metric": "max_healthy_inter_pass_advance_gap_hours", "min_rows": 1},
    ]
    r = _run(tmp_path, baseline=baseline)
    assert r.returncode == 0, r.stderr

    baseline["baseline_rows"] = [
        row for row in baseline["baseline_rows"]
        if row["metric"] != "max_healthy_inter_pass_advance_gap_hours"
    ]
    r = _run(tmp_path, baseline=baseline)
    assert r.returncode == 1
    assert "thinned below its ratified floor" in r.stderr
