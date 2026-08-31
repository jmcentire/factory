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


def test_backstop_persists_across_watchdog_restarts(tmp_path: Path) -> None:
    """#33 forcing test (a): kill/restart mid-gap — the arm state and the last
    advance observation are durable in watchdog.json, so a flapping watchdog
    cannot convert its own downtime into unbounded deadline extension. A
    persisted wake WAS live when it wrote; the fresh instance fires from it."""
    runner = FakeRunner(passes=1, cap_hours=1.0)
    clock = [1000.0]
    watchdog, _ = make_watchdog(tmp_path, runner, clock)
    recorder = Recorder()
    watchdog.check(recorder.emit, recorder.block)  # observes pass 1
    clock[0] += 10 * 3600
    watchdog.check(recorder.emit, recorder.block)  # arms (persisted)

    # Simulated restart: a brand-new instance over the same root.
    clock[0] += 60
    restarted, _ = make_watchdog(tmp_path, runner, clock)
    fresh = Recorder()
    assert restarted.check(fresh.emit, fresh.block) == "backstop-fired"
    assert fresh.blocks and fresh.blocks[0][1] == "wall_clock_backstop"


def test_backstop_tampered_state_degrades_never_fires_falsely(tmp_path: Path) -> None:
    """#33 forcing test (b): a deleted/corrupt watchdog.json degrades to
    re-observe-and-re-arm — one-check delay, no false fire, no crash, and no
    silent permanent disarm (the next two live checks fire again)."""
    runner = FakeRunner(passes=1, cap_hours=1.0)
    clock = [1000.0]
    watchdog, root = make_watchdog(tmp_path, runner, clock)
    recorder = Recorder()
    watchdog.check(recorder.emit, recorder.block)
    clock[0] += 10 * 3600
    watchdog.check(recorder.emit, recorder.block)  # armed

    (root / "watchdog.json").write_text("{corrupt", encoding="utf-8")
    clock[0] += 60
    assert watchdog.check(recorder.emit, recorder.block) == "ok"  # re-observes
    assert not recorder.blocks
    clock[0] += 2 * 3600
    watchdog.check(recorder.emit, recorder.block)  # re-arms
    clock[0] += 60
    assert watchdog.check(recorder.emit, recorder.block) == "backstop-fired"
    assert len(recorder.blocks) == 1


def test_cli_doors_are_pinned() -> None:
    """Round-5 F-8.3: the pass-count and signal-knobs handlers were deletable
    with the suite green. Pin their existence: the CLI must know both commands
    (an unknown command is an argparse 'invalid choice', which must not appear)."""
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    for command in ("pass-count", "signal-knobs"):
        result = subprocess.run(
            [sys.executable, "-m", "factory_runtime.cli", command,
             "--runs", "/nonexistent", "--run-id", "rX",
             "--genesis", "", "--root-public-key", "", "--tessera-bin", "tessera"],
            capture_output=True, text=True, cwd=repo,
        )
        assert "invalid choice" not in result.stderr, f"{command} door missing"
        assert result.returncode != 0  # nonexistent run refuses, door exists


def test_backstop_rearms_after_progress(tmp_path: Path) -> None:
    """Round-6 6-6: fired-once latches ONE stall, never permanent silence — a pass
    advance re-arms the backstop so a second stall pages again."""
    runner = FakeRunner(passes=1, cap_hours=1.0)
    clock = [0.0]
    watchdog, _ = make_watchdog(tmp_path, runner, clock)
    rec = Recorder()
    watchdog.check(rec.emit, rec.block)          # baseline observation
    clock[0] += 3700.0
    watchdog.check(rec.emit, rec.block)          # stall sighted: armed
    clock[0] += 10.0
    watchdog.check(rec.emit, rec.block)          # second live check: FIRES
    assert rec.kinds().count("wall_clock_backstop_expired") == 1
    runner.passes = 2                            # progress resumes
    clock[0] += 10.0
    watchdog.check(rec.emit, rec.block)          # advance clears the latch
    clock[0] += 3700.0
    watchdog.check(rec.emit, rec.block)          # second stall sighted
    clock[0] += 10.0
    watchdog.check(rec.emit, rec.block)          # and FIRES AGAIN
    assert rec.kinds().count("wall_clock_backstop_expired") == 2


def test_tampered_valid_json_state_degrades_to_rearm_not_crash(tmp_path: Path) -> None:
    """Round-6 6-7: valid-JSON-wrong-shape state (strings where numbers belong) is
    the same class as a deleted file — reset and re-arm, never a crash that kills
    the only monitor."""
    runner = FakeRunner(passes=1)
    clock = [1000.0]
    watchdog, root = make_watchdog(tmp_path, runner, clock)
    rec = Recorder()
    watchdog.check(rec.emit, rec.block)
    state_path = root / "watchdog.json"
    doc = json.loads(state_path.read_text())
    doc["observations"] = [{"ts": "not-a-number", "passes": "nope"}]
    state_path.write_text(json.dumps(doc))
    clock[0] += 10.0
    verdict = watchdog.check(rec.emit, rec.block)   # must not raise
    assert verdict in {"ok", "warned"}
    assert "watchdog_state_reset" in rec.kinds()


def test_persistent_observation_refusal_escalates_once_and_rearms(tmp_path: Path) -> None:
    """Round-6 6-8: a persistent pass-count refusal cannot silently disarm both
    firing modes — the third consecutive error escalates (wake=True) exactly once,
    and a good observation resets the counter so a later outage escalates again."""

    class RefusingRunner(FakeRunner):
        def __init__(self) -> None:
            super().__init__(passes=1)
            self.refuse = False

        def __call__(self, argv, capture_output=True, text=True):
            joined = " ".join(str(a) for a in argv)
            if self.refuse and "pass-count" in joined:
                return subprocess.CompletedProcess(argv, 2, "", "ledger refused")
            return super().__call__(argv, capture_output=capture_output, text=text)

    runner = RefusingRunner()
    clock = [1000.0]
    watchdog, _ = make_watchdog(tmp_path, runner, clock)
    rec = Recorder()
    runner.refuse = True
    for _ in range(4):
        clock[0] += 10.0
        watchdog.check(rec.emit, rec.block)
    escalated = [e for e in rec.events if e[0] == "watchdog_error" and e[2]]
    assert len(escalated) == 1, rec.events   # wake=True exactly once at threshold
    runner.refuse = False
    clock[0] += 10.0
    assert watchdog.check(rec.emit, rec.block) in {"ok", "warned"}
    runner.refuse = True
    for _ in range(3):
        clock[0] += 10.0
        watchdog.check(rec.emit, rec.block)
    escalated = [e for e in rec.events if e[0] == "watchdog_error" and e[2]]
    assert len(escalated) == 2               # re-armed after the good observation


def test_pass_unit_is_the_cli_door_never_dispatch_receipt_lines(tmp_path: Path) -> None:
    """Round-6 6-3: the pass unit is one VALIDATING admission in the VERIFIED run
    ledger via the pass-count door — a run root full of dispatch receipt lines
    changes nothing, and the module never even names the receipts file."""
    runner = FakeRunner(passes=0, warn=3)
    clock = [1000.0]
    watchdog, root = make_watchdog(tmp_path, runner, clock)
    (root / "dispatches.jsonl").write_text("{}\n" * 50, encoding="utf-8")
    rec = Recorder()
    assert watchdog.check(rec.emit, rec.block) == "ok"   # 50 lines != 50 passes
    assert "signal_pass_warn" not in rec.kinds()
    source = (HARNESS / "watchdog.py").read_text(encoding="utf-8")
    assert "dispatches" not in source  # no line-count fallback can exist unnamed


def test_dispatcher_run_loop_invokes_the_watchdog_and_halts_on_no(tmp_path: Path) -> None:
    """Round-6 6-2: the watchdog's one invocation line is load-bearing — this drives
    the REAL run_loop and only the watchdog's own firing (status 'no') stops it, so
    deleting dispatcher.py's signal_watchdog.check(...) line hangs the loop and
    turns this red."""
    spec = importlib.util.spec_from_file_location("dispatcher", HARNESS / "dispatcher.py")
    assert spec and spec.loader
    dispatcher_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dispatcher_module)

    root = tmp_path / "runs" / "r1"
    root.mkdir(parents=True)
    (root / "run.json").write_text(json.dumps({"target_state": {}}), encoding="utf-8")
    (root / "harness.json").write_text(
        json.dumps({"schema_version": "factory-harness/2", "status": "open"}),
        encoding="utf-8",
    )
    d = dispatcher_module.Dispatcher("r1", root, interval=0)

    calls = {"n": 0}

    class FiringWatchdog:
        def check(self, emit, block):
            calls["n"] += 1
            doc = json.loads((root / "harness.json").read_text())
            doc["status"] = "no"
            (root / "harness.json").write_text(json.dumps(doc))
            return "deadline-fired"

    d.signal_watchdog = FiringWatchdog()
    d.windows = lambda: []
    d.check_halt = lambda: None
    d.check_alignment_audit = lambda: None
    d.check_leases = lambda: None
    d.snapshot_minutes = lambda: None

    ticks = {"n": 0}

    def bounded_sleep(_seconds):
        ticks["n"] += 1
        if ticks["n"] > 5:
            raise AssertionError(
                "run_loop spun past the watchdog halt — the invocation line is dead"
            )

    dispatcher_module.time.sleep = bounded_sleep
    d.run_loop()
    assert calls["n"] == 1
    events = (root / "events.jsonl").read_text()
    assert "dispatcher_stop" in events and "run no" in events
