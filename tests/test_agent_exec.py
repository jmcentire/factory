"""agent_exec closes an agent's stdin and caps its wall clock (dogfood #55)."""

from __future__ import annotations

import time
from pathlib import Path

from harness import agent_exec


def test_an_agent_reading_stdin_gets_end_of_file_not_a_wait(tmp_path: Path) -> None:
    log = tmp_path / "review.log"
    started = time.monotonic()
    rc = agent_exec.main(["--timeout-minutes", "1", "--log", str(log), "--",
                          "sh", "-c", 'cat; echo "stdin closed"'])
    assert rc == 0 and time.monotonic() - started < 30
    assert log.read_text() == "stdin closed\n"


def test_the_prompt_file_is_the_agents_only_input(tmp_path: Path) -> None:
    prompt, log = tmp_path / "prompt.md", tmp_path / "review.log"
    prompt.write_text("review this\n")
    assert agent_exec.main(["--timeout-minutes", "1", "--stdin-file", str(prompt),
                            "--log", str(log), "--", "cat"]) == 0
    assert log.read_text() == "review this\n"


def test_a_call_past_its_cap_is_stopped_with_the_timeout_code(tmp_path: Path) -> None:
    started = time.monotonic()
    rc = agent_exec.main(["--timeout-minutes", "0.01", "--", "sleep", "30"])
    assert rc == agent_exec.TIMED_OUT and time.monotonic() - started < 20
