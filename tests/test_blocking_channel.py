"""The validator.blocking attention channel: per-event disposition, receipts, reopen."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from tests.test_harness_scripts import HARNESS, append_blockers, read_chain, run

STALL = {"ts": "2026-10-10T16:40:00+00:00", "class": "stall", "evidence": "tester quiet 30m"}
BLOCK = {
    "ts": "2026-10-10T16:40:33+00:00",
    "class": "orchestrator_response",
    "response": "BLOCK: the answer cites no ratified artifact.",
    "wake": "activity-cursor:2326",
    "trust_class": "untrusted-advisory",
    "effect_route": "validator-blocking-only",
}


def event_digest(event: dict[str, object]) -> str:
    canonical = json.dumps(event, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def consume(
    tmp_path: Path, root: Path, disposition: str, subject: str
) -> subprocess.CompletedProcess[str]:
    evidence = root / f"evidence-{disposition}.txt"
    evidence.write_text(f"Why this one is {disposition}.\n", encoding="utf-8")
    return run(
        [
            "bash", str(HARNESS / "consume_block.sh"), root.name, "validator",
            "--disposition", disposition,
            "--reason", f"{disposition}: judged on its own evidence",
            "--subject-digest", subject,
            "--evidence-file", str(evidence),
            "--evidence-digest", "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest(),
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )


def test_each_pending_event_takes_its_own_disposition(tmp_path: Path) -> None:
    """dogfood #45: a mixed backlog is dispositioned event by event, never one stamp for all;
    an event that is not named stays pending with its bytes intact."""

    root = tmp_path / ".harness" / "runs" / "r1"
    blocking = append_blockers(root, "validator", STALL, BLOCK)

    first = consume(tmp_path, root, "refute", event_digest(STALL))
    assert first.returncode == 0, first.stdout + first.stderr
    assert "1 still pending" in first.stdout
    assert [json.loads(line) for line in blocking.read_text().splitlines()] == [BLOCK]

    second = consume(tmp_path, root, "escalate", event_digest(BLOCK))
    assert second.returncode == 0, second.stdout + second.stderr
    assert "dispatch gate released" in second.stdout
    assert blocking.read_text() == ""
    consumed = {
        row["event_digest"]: row["disposition"]
        for row in read_chain(root / "events.jsonl")
        if row["kind"] == "blocking_consumed"
    }
    assert consumed == {event_digest(STALL): "refute", event_digest(BLOCK): "escalate"}
