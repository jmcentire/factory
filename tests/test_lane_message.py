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
    strategy = root / "artifacts" / "testing-strategy.md"
    strategy.parent.mkdir(exist_ok=True)
    strategy.write_text("# Testing strategy\nExercise the real seam.\n")
    frozen = run(
        ["bash", str(HARNESS / "tmux_lane.sh"), "r1", "tester", "freeze", "--runs",
         str(root.parent)],
        operator,
        {**env, "TMUX_STUB_WINDOWS": "validator"},
    )
    assert "not quiescent" not in frozen.stderr
    assert frozen.returncode == 0, frozen.stdout + frozen.stderr


def test_a_busy_lane_never_gets_a_delivery_receipt_for_a_queued_message(tmp_path: Path) -> None:
    """dogfood #68: a queued message reaches the lane only at its next turn boundary, so it is
    never recorded as delivered. Specification input to a busy lane is refused for retry at idle;
    a status probe may queue, reported as not delivered."""

    operator, root, env, tmux_log = lane_fixture(tmp_path, pane_dead="0")
    question, _ = record_question(root, "tester", "Is cancellation idempotent?")
    reply = tmp_path / "answer.txt"
    reply.write_text("Yes.\n", encoding="utf-8")
    args = (
        "--question-id", str(question["question_id"]), "--answer-file", str(reply),
        "--basis", "ratified requirement R-4", "--authority", "ratified-spec",
    )

    busy = answer(operator, root, env, "tester", *args)
    assert busy.returncode == 75, busy.stdout + busy.stderr
    assert "NOT delivered" in busy.stderr and "idle" in busy.stderr
    probe = run(
        ["bash", str(HARNESS / "tmux_lane_message.sh"), "r1", "orchestrator", "tester", "status",
         "--runs", str(root.parent)],
        operator,
        env,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert "NOT delivered" in probe.stdout
    journal = read_chain(root / "dialogue" / "journal.jsonl")
    assert "message-delivered" not in [row["record_type"] for row in journal]

    # Once the turn ends, the same planned answer is delivered by resume, with its receipt.
    tmux = tmp_path / "factory-bin" / "tmux"
    tmux.write_text(tmux.read_text().replace('printf "0\\n"', 'printf "1\\n"'))
    delivered = answer(operator, root, env, "tester", *args)
    assert delivered.returncode == 0, delivered.stdout + delivered.stderr
    journal = read_chain(root / "dialogue" / "journal.jsonl")
    receipts = [row for row in journal if row["record_type"] == "message-delivered"]
    assert [row["transport"] for row in receipts] == ["resume"]
    assert receipts[0]["message_id"] == journal[1]["message_id"]


def test_an_orchestrator_status_probe_to_an_idle_lane_exits_cleanly(tmp_path: Path) -> None:
    """An Orchestrator delivery is not echoed into its own activity feed."""

    operator, root, env, _ = lane_fixture(tmp_path)
    probe = run(
        ["bash", str(HARNESS / "tmux_lane_message.sh"), "r1", "orchestrator", "tester", "status",
         "--runs", str(root.parent)],
        operator,
        env,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert "delivered" in probe.stdout


def message(
    operator: Path, root: Path, env: dict[str, str], sender: str, lane: str, kind: str,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    return run(
        ["bash", str(HARNESS / "tmux_lane_message.sh"), "r1", sender, lane, kind,
         "--runs", str(root.parent), *extra],
        operator,
        env,
    )


def test_a_progress_review_resumes_the_coders_own_thread(tmp_path: Path) -> None:
    """A finding reaches a codex-exec Coder only by resuming its thread: inject.sh types into a
    pane that `codex exec` never reads, and a new --round starts a fresh context. The review is
    framed, leak-guarded and receipted like an answer."""

    operator, root, env, tmux_log = lane_fixture(tmp_path, "coder")
    review = tmp_path / "review.txt"
    review.write_text(
        "R-3: checkout of an empty cart returned 200 with total 0; the specification requires "
        "the typed EmptyCart refusal.\n", encoding="utf-8")

    delivered = message(operator, root, env, "validator", "coder", "review",
                        "--verdict", "redirect", "--review-file", str(review))
    assert delivered.returncode == 0, delivered.stdout + delivered.stderr
    planned = read_chain(root / "dialogue" / "journal.jsonl")[0]
    assert planned["message_kind"] == "progress-review" and planned["authority"] == "ratified-spec"
    text = planned["text"]
    assert text.startswith("FACTORY_REVIEW verdict=REDIRECT\n")
    assert "R-3: checkout of an empty cart" in text
    assert "rerun the checks that passed before and restore any you break" in text
    receipts = [row for row in read_chain(root / "dialogue" / "journal.jsonl")
                if row["record_type"] == "message-delivered"]
    assert [row["transport"] for row in receipts] == ["resume"]
    assert "exec --json resume" in tmux_log.read_text(encoding="utf-8")


def test_a_progress_review_is_leak_guarded_and_only_the_validator_sends_it(
    tmp_path: Path,
) -> None:
    operator, root, env, _ = lane_fixture(tmp_path, "coder")
    review = tmp_path / "review.txt"
    review.write_text("test_checkout_empty_cart fails: see the assertion.\n", encoding="utf-8")
    leaked = message(operator, root, env, "validator", "coder", "review",
                     "--verdict", "redirect", "--review-file", str(review))
    assert leaked.returncode == 80, leaked.stdout + leaked.stderr
    review.write_text("R-3 holds.\n", encoding="utf-8")
    refused = message(operator, root, env, "orchestrator", "coder", "review",
                      "--verdict", "on-track", "--review-file", str(review))
    assert refused.returncode == 77
    no_verdict = message(operator, root, env, "validator", "coder", "review",
                         "--review-file", str(review))
    assert no_verdict.returncode == 64
    tester = tmp_path / "tester"
    tester.mkdir()
    operator, root, env, _ = lane_fixture(tester, "tester")
    to_tester = message(operator, root, env, "validator", "tester", "review",
                        "--verdict", "on-track", "--review-file", str(review))
    assert to_tester.returncode == 64 and "Coder lane" in to_tester.stderr


def test_only_the_watchdog_sends_the_generated_wrap_up(tmp_path: Path) -> None:
    operator, root, env, _ = lane_fixture(tmp_path, "coder")
    refused = message(operator, root, env, "validator", "coder", "wrapup", "--minutes", "5")
    assert refused.returncode == 77
    refused = message(operator, root, env, "watchdog", "coder", "status")
    assert refused.returncode == 77
    delivered = message(operator, root, env, "watchdog", "coder", "wrapup", "--minutes", "5",
                        "--wait-idle", "2")
    assert delivered.returncode == 0, delivered.stdout + delivered.stderr
    planned = read_chain(root / "dialogue" / "journal.jsonl")[0]
    assert planned["sender"] == "watchdog" and planned["message_kind"] == "wrap-up"
    assert planned["text"].startswith("FACTORY_WRAP_UP: about 5 minutes of this lane's time")
    assert "Start nothing new" in planned["text"]


def test_a_lane_at_its_cap_refuses_a_review_and_status_refuses_stray_arguments(
    tmp_path: Path,
) -> None:
    operator, root, env, _ = lane_fixture(tmp_path, "coder")
    (root / "tmux-lanes" / "coder-watch.json").write_text('{"status": "capped"}\n')
    review = tmp_path / "review.txt"
    review.write_text("R-3: fix the refusal.\n", encoding="utf-8")
    refused = message(operator, root, env, "validator", "coder", "review",
                      "--verdict", "repair", "--review-file", str(review))
    assert refused.returncode == 70 and "reached its cap" in refused.stderr
    assert "new --round" in refused.stderr
    stray = message(operator, root, env, "validator", "coder", "status", "--verdict", "redirect")
    assert stray.returncode == 64
