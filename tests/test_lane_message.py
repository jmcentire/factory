"""tmux_lane_message.sh: what a Validator delivery to a tmux Codex lane proves."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from harness.lane_dialogue import record_question
from tests.test_harness_scripts import (
    HARNESS,
    execution_truth_fixture,
    factory_ignition_env,
    read_chain,
    run,
)

THREAD = "12345678-1234-4234-8234-123456789abc"


def lane_fixture(
    tmp_path: Path, lane: str = "tester", *, pane_dead: str = "1"
) -> tuple[Path, Path, dict[str, str], Path]:
    """A launched Codex lane with a retained thread; tmux reports the lane pane as given."""

    operator, root, _ = execution_truth_fixture(tmp_path, harness_status="open")
    env, tmux_log = factory_ignition_env(tmp_path, root)
    tmux = tmp_path / "factory-bin" / "tmux"
    tmux.write_text(
        tmux.read_text(encoding="utf-8").replace(
            'printf "1\\n"; exit 0; fi', f'printf "{pane_dead}\\n"; exit 0; fi'
        ),
        encoding="utf-8",
    )
    repository = tmp_path / f"standalone-{lane}"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repository)], check=True)
    launches = root / "tmux-lanes"
    launches.mkdir()
    (launches / f"{lane}-launch.jsonl").write_text(
        json.dumps(
            {
                "status": "active",
                "run_id": "r1",
                "role": lane,
                "agent": "codex",
                "repository": str(repository),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (launches / f"{lane}-thread-id").write_text(THREAD + "\n", encoding="utf-8")
    return operator, root, env, tmux_log


def answer(
    operator: Path, root: Path, env: dict[str, str], lane: str, *extra: str
) -> subprocess.CompletedProcess[str]:
    return run(
        [
            "bash",
            str(HARNESS / "tmux_lane_message.sh"),
            "r1",
            "validator",
            lane,
            "answer",
            "--runs",
            str(root.parent),
            *extra,
        ],
        operator,
        env,
    )


def test_an_answer_past_the_inline_ceiling_is_delivered_as_a_digest_pinned_file(
    tmp_path: Path,
) -> None:
    """dogfood #41: a reviewed 24 KB batched answer is delivered as-is, by file + sha256."""

    import hashlib

    operator, root, env, _ = lane_fixture(tmp_path)
    question, _ = record_question(root, "tester", "Please resolve these nine items together.")
    reply = tmp_path / "answer.txt"
    body = "".join(f"Item {n}: the ruling for item {n} is unchanged.\n" for n in range(500))
    reply.write_text(body, encoding="utf-8")
    assert len(body.encode()) > 12_000

    delivered = answer(
        operator, root, env, "tester",
        "--question-id", str(question["question_id"]), "--answer-file", str(reply),
        "--basis", "ratified requirement R-9", "--authority", "ratified-spec",
    )
    assert delivered.returncode == 0, delivered.stdout + delivered.stderr
    digest = hashlib.sha256(body.encode()).hexdigest()
    pinned = root / "tmux-lanes" / "tester-inputs" / f"{digest}.txt"
    assert pinned.read_text(encoding="utf-8") == body
    assert pinned.stat().st_mode & 0o222 == 0
    planned = read_chain(root / "dialogue" / "journal.jsonl")[1]
    assert f"FACTORY_RELAY file={pinned} sha256={digest}" in planned["text"]
    assert planned["text"].startswith("FACTORY_ANSWER question_id=")


def test_a_large_coder_answer_is_leak_guarded_on_its_file_bytes(tmp_path: Path) -> None:
    """The oracle-leak guard judges the file's bytes; a refused file never reaches the lane."""

    operator, root, env, _ = lane_fixture(tmp_path, "coder")
    question, _ = record_question(root, "coder", "Which error does checkout raise?")
    reply = tmp_path / "answer.txt"
    reply.write_text("Raise the typed error.\n" * 700 + "See test_checkout_raises.\n")

    refused = answer(
        operator, root, env, "coder",
        "--question-id", str(question["question_id"]), "--answer-file", str(reply),
        "--basis", "ratified requirement R-2", "--authority", "ratified-spec",
    )
    assert refused.returncode == 80, refused.stdout + refused.stderr
    assert not (root / "tmux-lanes" / "coder-inputs").exists()


def test_idle_reads_the_lane_window_by_exact_name(tmp_path: Path) -> None:
    """dogfood #69: the Validator can ask whether a lane is idle. A lane whose window is gone is
    idle even while the session's active pane (the Validator's) is alive; a live lane is busy."""

    operator, root, env, _ = lane_fixture(tmp_path, pane_dead="0")

    def idle(windows: str) -> subprocess.CompletedProcess[str]:
        return run(
            ["bash", str(HARNESS / "tmux_lane_message.sh"), "r1", "validator", "tester", "idle",
             "--runs", str(root.parent)],
            operator,
            {**env, "TMUX_STUB_WINDOWS": windows},
        )

    gone = idle("validator")
    assert gone.returncode == 0, gone.stdout + gone.stderr
    assert "idle (window gone)" in gone.stdout
    busy = idle("validator\ntester")
    assert busy.returncode == 75, busy.stdout + busy.stderr
    assert "busy" in busy.stdout


def test_freeze_does_not_mistake_the_active_pane_for_a_gone_lane(tmp_path: Path) -> None:
    """dogfood #69: with the lane window gone, freeze must not probe the session's active pane."""

    operator, root, env, _ = lane_fixture(tmp_path, pane_dead="0")
    frozen = run(
        ["bash", str(HARNESS / "tmux_lane.sh"), "r1", "tester", "freeze", "--runs",
         str(root.parent)],
        operator,
        {**env, "TMUX_STUB_WINDOWS": "validator"},
    )
    assert "not quiescent" not in frozen.stderr
    assert frozen.returncode == 0, frozen.stdout + frozen.stderr
