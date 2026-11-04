"""Metered author reservations survive retries and serialize competing launches."""

import json
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from harness.lane_budget import BudgetRefused, reserve


@pytest.fixture
def budget(tmp_path):
    metadata = tmp_path / "harness.json"
    metadata.write_text('{"budget_usd":1}')
    return dict(metadata_path=metadata, ledger_path=tmp_path / "budget.jsonl",
                requested=750000, run="task", role="coder", generation=1,
                reservation_id="coder-1")


def child(budget, *, killed=False):
    script = '''
import os, signal, sys
from pathlib import Path
from harness.lane_budget import reserve, BudgetRefused
if sys.argv[4] == 'kill':
    sync = os.fsync
    def after_sync(fd):
        sync(fd)
        os.kill(os.getpid(), signal.SIGKILL)
    os.fsync = after_sync
try:
    print(reserve(metadata_path=Path(sys.argv[1]), ledger_path=Path(sys.argv[2]),
                  requested=750000, run='task', role='coder', generation=1,
                  reservation_id=sys.argv[3]))
except BudgetRefused as error:
    print(error, file=sys.stderr)
    sys.exit(70)
'''
    return subprocess.Popen(
        [sys.executable, "-c", script, str(budget["metadata_path"]),
         str(budget["ledger_path"]), budget["reservation_id"], "kill" if killed else "normal"],
        cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )


def test_competing_author_caps_cannot_oversubscribe_the_budget(budget):
    processes = [child(budget), child({**budget, "reservation_id": "coder-2"})]
    outcomes = [p.communicate(timeout=10) for p in processes]
    assert sorted(p.returncode for p in processes) == [0, 70], outcomes
    rows = [json.loads(line) for line in budget["ledger_path"].read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["reserved_max_cost_microusd"] == 750000


def test_retry_after_killed_reservation_writer_reuses_the_same_cap_once(budget):
    process = child(budget, killed=True)
    process.communicate(timeout=10)
    assert process.returncode == -signal.SIGKILL
    before = budget["ledger_path"].read_bytes()
    original = json.loads(before)
    assert reserve(**budget) == "sha256:" + original["hash"]
    assert budget["ledger_path"].read_bytes() == before
    with pytest.raises(BudgetRefused, match="different scope"):
        reserve(**{**budget, "requested": 1})
    with pytest.raises(BudgetRefused, match="exceed"):
        reserve(**{**budget, "reservation_id": "coder-2"})


@pytest.mark.parametrize("damage", ["partial", "tampered"])
def test_uncertain_or_tampered_budget_evidence_is_preserved_not_spent_again(budget, damage):
    reserve(**budget)
    ledger = budget["ledger_path"]
    if damage == "partial":
        with ledger.open("ab") as stream:
            stream.write(b'{"interrupted":')
    else:
        ledger.write_text(ledger.read_text().replace('"reserved_max_cost_microusd":750000',
                                                    '"reserved_max_cost_microusd":1'))
    before = ledger.read_bytes()
    with pytest.raises((BudgetRefused, json.JSONDecodeError)):
        reserve(**{**budget, "reservation_id": "another"})
    assert ledger.read_bytes() == before


def test_no_budget_cannot_be_treated_as_free_metered_capacity(budget):
    budget["metadata_path"].write_text('{}')
    with pytest.raises(BudgetRefused, match="explicit objective budget"):
        reserve(**budget)
    assert not budget["ledger_path"].exists()
