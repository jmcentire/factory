"""Complete retained activity is visible without pane input or automatic hold authority."""

import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.dispatcher import observe


def test_every_role_activity_and_messages_are_read_without_control(tmp_path):
    lanes = tmp_path / "tmux-lanes"
    lanes.mkdir()
    for name in ("coder", "tester"):
        (lanes / f"{name}-lane.log").write_text(f"{name} work\n")
    (tmp_path / "validator-lane.log").write_text("VALIDATOR: $(touch BAD)\n")
    (tmp_path / "orchestrator-lane.log").write_text("avoid self-feedback")
    (tmp_path / "orchestrator").mkdir()
    (tmp_path / "orchestrator/inbox.jsonl").write_text('{"text":"new question"}\n')
    with patch("subprocess.run", side_effect=AssertionError("no terminal operations")):
        first = observe(tmp_path, {})
        second = observe(tmp_path, first["cursors"])
    assert len(first["entries"]) == 4
    assert not second["entries"]
    assert "$(touch BAD)" in str(first)
    assert not (tmp_path / "BAD").exists()
    assert not (tmp_path / "orchestrator/seat-control.json").exists()


def test_bounded_chunks_preserve_all_output_and_restart_cursor(tmp_path):
    log = tmp_path / "validator-lane.log"
    original = "x" * 16383 + "🦊" + "y" * 40000
    log.write_text(original)
    cursor, found = {}, []
    while True:
        result = observe(tmp_path, cursor)
        cursor = result["cursors"]
        if not result["entries"]:
            break
        found.extend(row["text"] for row in result["entries"])
    assert "".join(found) == original
    with log.open("a") as stream:
        stream.write("final")
    after = observe(tmp_path, cursor)
    assert after["entries"][0]["text"] == "final"
    assert after["entries"][0]["sha256"] == hashlib.sha256(b"final").hexdigest()
    # Truncation/rewrite is surfaced even when the new file is longer than the old offset.
    log.write_text("replacement" * 7000)
    replaced = observe(tmp_path, after["cursors"])
    assert replaced["entries"][0]["reset"] is True
    assert replaced["entries"][0]["start"] == 0


def test_observation_refuses_symlinked_log(tmp_path):
    (tmp_path / "secret").write_text("unrelated")
    (tmp_path / "validator-lane.log").symlink_to(tmp_path / "secret")
    with pytest.raises(OSError):
        observe(tmp_path, {})


def test_character_split_across_appends_is_not_lost_or_replaced(tmp_path):
    log = tmp_path / "validator-lane.log"
    log.write_bytes(b"work " + "🦊".encode()[:2])
    first = observe(tmp_path, {})
    assert first["entries"][0]["text"] == "work "
    assert first["cursors"][log.name]["offset"] == 5
    with log.open("ab") as stream:
        stream.write("🦊".encode()[2:] + b" complete")
    second = observe(tmp_path, first["cursors"])
    assert second["entries"][0]["text"] == "🦊 complete"
    assert not observe(tmp_path, second["cursors"])["entries"]


def test_native_output_and_failures_remain_observations_not_verdicts(tmp_path):
    log = tmp_path / "validator-lane.log"
    raw = b'plausible SUCCESS prose\n{"unfinished": \xff'
    log.write_bytes(raw)
    exits = tmp_path / "orchestrator-exits.jsonl"
    exits.write_text('{"rc":127,"slot":"orchestrator"}\n')
    with patch("subprocess.run", side_effect=AssertionError("no agent or terminal launch")):
        result = observe(tmp_path, {})
    rows = {row["source"]: row for row in result["entries"]}
    assert rows[log.name]["text"] == raw.decode("utf-8", errors="replace")
    assert rows[log.name]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert '"rc":127' in rows[exits.name]["text"]
    assert log.read_bytes() == raw
    assert set(p.name for p in tmp_path.iterdir()) == {log.name, exits.name}


@pytest.mark.parametrize("schema", [None, "factory-harness/2", "factory-harness/3"])
def test_retired_headless_audit_never_launches_agents_or_mutates_records(tmp_path, schema):
    root = tmp_path / "run"
    root.mkdir()
    if schema:
        (root / "harness.json").write_text(json.dumps({"schema_version": schema}))
    (root / "TASK.md").write_text("Keep the resident roles")
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    binary = tmp_path / "bin"
    binary.mkdir()
    marker = tmp_path / "unexpected-executable"
    for name in ("agy", "codex", "claude", "python3", "tmux", "factory"):
        stub = binary / name
        stub.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 97\n")
        stub.chmod(0o755)
    script = Path(__file__).resolve().parents[1] / "harness/orchestrator_wake.sh"
    result = subprocess.run(
        ["/bin/bash", str(script), "task", '{"kind":"legacy-wake"}'],
        env={"PATH": f"{binary}:/usr/bin:/bin", "HARNESS_RUN_ROOT": str(root),
             "FACTORY_ORCHESTRATOR_AGENT": "agy"},
        capture_output=True, text=True,
    )
    assert result.returncode == 64 and "one-shot audits are retired" in result.stderr
    assert "--observe" in result.stderr
    assert not marker.exists()
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before


def test_observation_does_not_enumerate_old_audit_or_operator_files(tmp_path):
    for folder in ("minutes", "wakes", "artifacts", ".claude/projects"):
        directory = tmp_path / folder
        directory.mkdir(parents=True)
        (directory / "validator-lane.log").write_text("unrelated historical context")
    assert observe(tmp_path, {}) == {"entries": [], "cursors": {}}
