"""Seats start with their program's animations off where it has a setting for that.

A seat's terminal output is kept in its lane log, and every spinner or shimmer frame lands
there (one run's orchestrator-lane.log held thousands of lines of `⣾ Running co`). Claude Code
and Codex have a setting for it; Antigravity and Cursor have none.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

import native_session  # noqa: E402
from run_record import resident_command  # noqa: E402

QUIET = ["-c", "tui.animations=false"]


def _claude(tmp_path: Path, arguments: list[str]) -> dict:
    launcher = native_session.Launcher("claude", "executable", path="/bin/claude")
    started = native_session.plan(arguments, launcher=launcher, directory=tmp_path / "native",
                                  slot="validator", name="validator")
    return json.loads(Path(started.argv[started.argv.index("--settings") + 1]).read_text())


def test_a_claude_seat_starts_with_reduced_motion(tmp_path: Path) -> None:
    settings = _claude(tmp_path, ["--model", "m"])
    assert settings["prefersReducedMotion"] is True
    assert settings["spinnerTipsEnabled"] is False
    assert set(settings["hooks"]) == set(native_session.HOOK_EVENTS)


def test_a_claude_lanes_own_settings_file_keeps_reduced_motion(tmp_path: Path) -> None:
    own = tmp_path / "signet-settings.json"
    own.write_text(json.dumps({"hooks": {"PreToolUse": [{"hooks": []}]}, "sentinel": 1}))
    settings = _claude(tmp_path, ["--settings", str(own)])
    assert settings["prefersReducedMotion"] is True and settings["sentinel"] == 1
    assert "PreToolUse" in settings["hooks"]


def _codex(tmp_path: Path, arguments: list[str], **kwargs: str) -> list[str]:
    started = native_session.codex_plan(arguments, executable="/bin/codex",
                                        directory=tmp_path / "native", slot="coder",
                                        workspace=tmp_path, prompt="the task", **kwargs)
    return list(started.argv[1:])


def test_a_codex_start_turns_its_animations_off_among_its_options(tmp_path: Path) -> None:
    argv = _codex(tmp_path, ["--no-daemon", "--no-alt-screen", "-C", str(tmp_path)])
    end = argv.index("--")
    assert argv[end - 2:end] == QUIET


def test_a_codex_resume_keeps_its_session_id_last(tmp_path: Path) -> None:
    session = str(uuid.uuid4())
    argv = _codex(tmp_path, ["resume", "--no-daemon", "-C", str(tmp_path), session],
                  message_id="m1")
    end = argv.index("--")
    assert argv[end - 3:end] == [*QUIET, session]


def test_a_codex_coordinator_turns_its_animations_off(tmp_path: Path) -> None:
    argv = resident_command("codex", "m", "", "the task", tmp_path)
    at = argv.index("tui.animations=false")
    assert argv[at - 1] == "-c" and argv[-1] == "the task"
