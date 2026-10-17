from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from harness.validator_debrief import SCHEMA, DebriefError, check, main, overdue

NOW = dt.datetime(2026, 10, 16, 20, 0, tzinfo=dt.UTC)


def debrief(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": SCHEMA,
        "recorded_at": NOW.isoformat(),
        "through_cursor": 14,
        "trigger": "slice-boundary",
        "summary": "Slice 1 core is verified against R1-R4; staleness is claimed, not shown.",
        "signal_class": "action-required",
        "verified": [
            {"item": "post_update dedupes by key (R2).",
             "evidence": ["artifacts/verdicts/slice-1.json", "commit:9f8e7d6c"],
             "signal_class": "informative"},
        ],
        "unverified": [
            {"claim": "A missed heartbeat marks the agent stale.", "claimed_by": "coder",
             "next_check": "Kill a heartbeat and observe the alert from /status.",
             "signal_class": "at-your-convenience"},
        ],
        "risks": [
            {"risk": "The Durable Object alarm drifts under load.", "refs": [],
             "signal_class": "at-your-convenience"},
        ],
        "decisions_owed": [
            {"question": "Is the self-hosted MCP passthrough in scope for v1?",
             "signal_class": "action-required"},
        ],
    }
    body.update(overrides)
    return body


def test_a_classified_evidenced_debrief_passes() -> None:
    assert check(debrief())["trigger"] == "slice-boundary"


def test_a_debrief_that_judges_nothing_is_a_status_recap() -> None:
    with pytest.raises(DebriefError, match="status recap"):
        check(debrief(verified=[], unverified=[]))


@pytest.mark.parametrize("evidence", [[], ["the coder says the tests pass"],
                                      ["orchestrator/OUTSTANDING-WORK.md"]])
def test_verified_items_cite_the_work(evidence: list[str]) -> None:
    item = {"item": "Done.", "evidence": evidence, "signal_class": "informative"}
    with pytest.raises(DebriefError, match="evidence"):
        check(debrief(verified=[item]))


def test_a_decision_owed_is_at_least_action_required() -> None:
    owed = [{"question": "Ship?", "signal_class": "at-your-convenience"}]
    with pytest.raises(DebriefError, match="at least action-required"):
        check(debrief(decisions_owed=owed, signal_class="action-required"))


def test_overall_class_is_never_below_its_items() -> None:
    with pytest.raises(DebriefError, match="below its items"):
        check(debrief(signal_class="informative"))


def test_overdue_counts_only_well_formed_debriefs(tmp_path: Path) -> None:
    directory = tmp_path / "artifacts" / "debriefs"
    directory.mkdir(parents=True)
    assert overdue(tmp_path, now=NOW) == "no well-formed debrief has been written"
    (directory / "a.json").write_text(json.dumps(debrief(verified=[], unverified=[])))
    assert overdue(tmp_path, now=NOW) == "no well-formed debrief has been written"
    stamp = (NOW - dt.timedelta(minutes=30)).isoformat()
    (directory / "b.json").write_text(json.dumps(debrief(recorded_at=stamp)))
    assert overdue(tmp_path, now=NOW) is None
    reason = overdue(tmp_path, now=NOW + dt.timedelta(minutes=45))
    assert reason is not None and "75 minutes old" in reason


def test_cli_check_and_overdue(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "d.json"
    path.write_text(json.dumps(debrief()))
    assert main(["check", str(path)]) == 0
    assert main(["overdue", "--root", str(tmp_path)]) == 1
    assert "owed" in capsys.readouterr().out
