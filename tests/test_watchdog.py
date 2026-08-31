"""Forcing tests for the signal-deadline watchdog (remediation plan §0.4c).

Passes come only from the CLI's verified-ledger count; knobs only from the
frozen-blob CLI door; the deadline records the terminal NO itself and CHECKS
record_no's exit code; the wall-clock backstop fires only on a stall seen
across two live checks — a dead watchdog never misattributes its own absence.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

HARNESS = Path(__file__).resolve().parent.parent / "harness"


def load_watchdog() -> object:
    spec = importlib.util.spec_from_file_location("watchdog", HARNESS / "watchdog.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, bool]] = []
        self.blocks: list[tuple[str, str, str]] = []

    def emit(self, kind: str, detail: str, wake: bool = False) -> None:
        self.events.append((kind, detail, wake))

    def block(self, lane: str, cls: str, evidence: str) -> None:
        self.blocks.append((lane, cls, evidence))

    def kinds(self) -> list[str]:
        return [kind for kind, _, _ in self.events]


class FakeRunner:
    """Answers the two CLI doors and record_no; scriptable per test."""

    def __init__(self, *, passes: int = 0, deadline: int = 4, warn: int = 3,
                 cap_hours: float = 24.0, record_no_rc: int = 0) -> None:
        self.passes = passes
        self.deadline = deadline
        self.warn = warn
        self.cap_hours = cap_hours
        self.record_no_rc = record_no_rc
        self.record_no_calls: list[list[str]] = []

    def __call__(self, argv, capture_output=True, text=True):
        joined = " ".join(str(a) for a in argv)
        if "signal-knobs" in joined:
            body = {
                "signal_pass_deadline": self.deadline,
                "signal_pass_warn": self.warn,
                "signal_wall_clock_cap_hours": self.cap_hours,
            }
            return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")
        if "pass-count" in joined:
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"passes": self.passes}), ""
            )
        if "record_no.sh" in joined:
            self.record_no_calls.append([str(a) for a in argv])
            return subprocess.CompletedProcess(argv, self.record_no_rc, "", "refused")
        raise AssertionError(f"unexpected runner invocation: {joined}")


def make_watchdog(tmp_path: Path, runner: FakeRunner, clock: list[float]):
    module = load_watchdog()
    root = tmp_path / "runs" / "r1"
    root.mkdir(parents=True, exist_ok=True)
    (root / "harness.json").write_text(json.dumps({"status": "open"}), encoding="utf-8")
    # The committed baseline the watchdog consults for NO-relevance sits at the
    # harness dir's parent (repo root); point harness_dir at a scratch layout.
    harness_dir = tmp_path / "harness"
    harness_dir.mkdir(exist_ok=True)
    (tmp_path / "acceptance_baseline.json").write_text(
        json.dumps(
            {
                "no_relevant_kinds": {
                    "refusal-promote": True,
                    "watchdog-deadline": False,
                }
            }
        ),
        encoding="utf-8",
    )
    (harness_dir / "record_no.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    watchdog = module.SignalWatchdog(  # type: ignore[attr-defined]
        root=root,
        runs_root=tmp_path / "runs",
        run_id="r1",
        factory_cli=["factory"],
        replay_authority_args=(),
        harness_dir=harness_dir,
        now=lambda: clock[0],
        runner=runner,
    )
    return watchdog, root


def test_warn_fires_once_at_warn_pass(tmp_path: Path) -> None:
    runner = FakeRunner(passes=3)
    clock = [1000.0]
    watchdog, _ = make_watchdog(tmp_path, runner, clock)
    recorder = Recorder()
    assert watchdog.check(recorder.emit, recorder.block) == "warned"
    clock[0] += 60
    watchdog.check(recorder.emit, recorder.block)
    assert recorder.kinds().count("signal_pass_warn") == 1


def test_deadline_records_terminal_no_and_halts(tmp_path: Path) -> None:
    runner = FakeRunner(passes=4)
    clock = [1000.0]
    watchdog, _ = make_watchdog(tmp_path, runner, clock)
    recorder = Recorder()
    verdict = watchdog.check(recorder.emit, recorder.block)
    assert verdict == "deadline-fired"
    assert len(runner.record_no_calls) == 1
    call = " ".join(runner.record_no_calls[0])
    assert "--kind watchdog-deadline" in call
    assert "signal_deadline_expired" in recorder.kinds()


def test_refused_record_no_leaves_deadline_unsatisfied(tmp_path: Path) -> None:
    """Round-3 carryover: record_no's exit code is CHECKED — a refused record
    leaves the run open and raises record_no_refused; a later check retries."""
    runner = FakeRunner(passes=4, record_no_rc=2)
    clock = [1000.0]
    watchdog, _ = make_watchdog(tmp_path, runner, clock)
    recorder = Recorder()
    assert watchdog.check(recorder.emit, recorder.block) == "refused"
    assert "record_no_refused" in recorder.kinds()
    assert "signal_deadline_expired" not in recorder.kinds()
    clock[0] += 60
    runner.record_no_rc = 0
    assert watchdog.check(recorder.emit, recorder.block) == "deadline-fired"


def test_existing_no_relevant_signal_disarms_the_deadline(tmp_path: Path) -> None:
    runner = FakeRunner(passes=4)
    clock = [1000.0]
    watchdog, root = make_watchdog(tmp_path, runner, clock)
    (root / "events.jsonl").write_text(
        json.dumps({"kind": "refusal-promote", "class": "refusal"}) + "\n",
        encoding="utf-8",
    )
    recorder = Recorder()
    assert watchdog.check(recorder.emit, recorder.block) == "warned"  # warn still fires
    assert not runner.record_no_calls
    assert "signal_deadline_expired" not in recorder.kinds()


def test_backstop_fires_only_across_two_live_checks(tmp_path: Path) -> None:
    """A dead watchdog must not misattribute its own absence: the first live
    sighting of a stall arms; only the second fires the blocking event."""
    runner = FakeRunner(passes=1, cap_hours=1.0)
    clock = [1000.0]
    watchdog, _ = make_watchdog(tmp_path, runner, clock)
    recorder = Recorder()
    assert watchdog.check(recorder.emit, recorder.block) == "ok"  # observes pass 1

    clock[0] += 10 * 3600  # 10h outage-or-stall later
    assert watchdog.check(recorder.emit, recorder.block) == "ok"  # arms only
    assert not recorder.blocks
    clock[0] += 60
    assert watchdog.check(recorder.emit, recorder.block) == "backstop-fired"
    assert recorder.blocks and recorder.blocks[0][1] == "wall_clock_backstop"
    assert "wall_clock_backstop_expired" in recorder.kinds()
    clock[0] += 60
    watchdog.check(recorder.emit, recorder.block)
    assert len(recorder.blocks) == 1  # fires once


def test_pass_advance_resets_the_stall(tmp_path: Path) -> None:
    runner = FakeRunner(passes=1, cap_hours=1.0)
    clock = [1000.0]
    watchdog, _ = make_watchdog(tmp_path, runner, clock)
    recorder = Recorder()
    watchdog.check(recorder.emit, recorder.block)
    clock[0] += 10 * 3600
    runner.passes = 2  # advanced during the gap — observed on this check
    assert watchdog.check(recorder.emit, recorder.block) == "ok"
    clock[0] += 60
    assert watchdog.check(recorder.emit, recorder.block) == "ok"
    assert not recorder.blocks


def test_watchdog_reads_no_ambient_environment_for_knobs() -> None:
    """The WAKE_TIMEOUT anti-precedent: knob values come only from the frozen
    blob via the CLI door — the module contains no environment read at all."""
    source = (HARNESS / "watchdog.py").read_text(encoding="utf-8")
    assert "os.environ" not in source
    assert "getenv" not in source
