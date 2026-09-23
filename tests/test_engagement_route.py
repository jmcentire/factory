"""The executable half of engagement: what gets recorded instead of asked."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1] / "harness"


def run_root(tmp_path: Path, engagement: str, channel: str | None = None) -> Path:
    root = tmp_path / "run"
    root.mkdir(exist_ok=True)
    (root / "harness.json").write_text(
        json.dumps({"engagement": engagement, "question_channel": channel}),
        encoding="utf-8",
    )
    return root


def route(root: Path, *args: str) -> dict:
    finished = subprocess.run(
        [sys.executable, str(HARNESS / "engagement_route.py"), "item", "--root", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    return json.loads(finished.stdout)


def register(root: Path, name: str) -> list[dict]:
    path = root / "registers" / name
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_autonomous_records_and_never_sends(tmp_path: Path) -> None:
    root = run_root(tmp_path, "autonomous")
    record = route(
        root,
        "--subject",
        "who owns the hold TTL",
        "--undeterminable",
        "--owned-here",
        "--basis",
        "weak",
    )
    assert record["action"] == "record-escalation"
    assert "delivery" not in record
    assert register(root, "escalations.jsonl")[0]["subject"] == "who owns the hold TTL"


def test_a_determinable_item_is_decided_and_not_written_to_a_register(tmp_path: Path) -> None:
    root = run_root(tmp_path, "autonomous")
    record = route(root, "--subject", "the schema already says this", "--owned-here")
    assert record["action"] == "decide-locally"
    assert register(root, "assumptions.jsonl") == []
    assert register(root, "escalations.jsonl") == []


def test_scheduled_sends_the_exceptional_through_the_configured_channel(tmp_path: Path) -> None:
    sent = tmp_path / "sent.txt"
    script = tmp_path / "channel.py"
    script.write_text(WRITER % str(sent), encoding="utf-8")
    channel = f"{shell_quote(sys.executable)} {shell_quote(str(script))}"
    root = run_root(tmp_path, "scheduled", channel)
    record = route(
        root,
        "--subject",
        "irreversible cutover with a weak basis",
        "--undeterminable",
        "--owned-here",
        "--basis",
        "weak",
        "--reversibility",
        "irreversible",
    )
    assert record["action"] == "notify-channel"
    assert record["delivery"]["sent"] is True
    assert "irreversible cutover" in sent.read_text()


def test_a_failing_channel_records_unsent_and_still_does_not_block(tmp_path: Path) -> None:
    root = run_root(tmp_path, "scheduled", "/nonexistent/channel")
    record = route(
        root,
        "--subject",
        "weak basis on a Critical surface",
        "--undeterminable",
        "--owned-here",
        "--basis",
        "weak",
        "--critical",
    )
    assert record["action"] == "notify-channel"
    assert record["delivery"]["sent"] is False
    assert register(root, "escalations.jsonl")[0]["delivery"]["sent"] is False


def test_an_unconfigured_run_is_interactive_never_dark(tmp_path: Path) -> None:
    root = tmp_path / "bare"
    root.mkdir()
    (root / "harness.json").write_text("{}", encoding="utf-8")
    record = route(root, "--subject", "unowned decision", "--undeterminable", "--owned-here")
    assert record["engagement"] == "interactive"
    assert record["action"] == "block-for-human"


def test_every_external_action_lands_in_the_side_effect_register(tmp_path: Path) -> None:
    root = run_root(tmp_path, "autonomous")
    finished = subprocess.run(
        [
            sys.executable,
            str(HARNESS / "engagement_route.py"),
            "announce",
            "--root",
            str(root),
            "--action",
            "push tag v1.2.3",
            "--reversibility",
            "irreversible",
            "--inverse",
            "delete the tag before anyone fetches it",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    row = json.loads(finished.stdout)
    assert row["disposition"] == "proceeded"
    assert register(root, "side-effects.jsonl")[0]["inverse"].startswith("delete the tag")


WRITER = "import sys,pathlib; pathlib.Path(%r).write_text(sys.stdin.read())"


def shell_quote(text: str) -> str:
    import shlex

    return shlex.quote(text)
