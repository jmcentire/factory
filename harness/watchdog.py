#!/usr/bin/env python3
"""Signal-deadline watchdog (remediation plan §0.4c).

The founder's criterion made live: by the end of pass ``signal_pass_deadline`` a
NO-relevant signal or a terminal disposition must exist, or the host stops the
run itself — the NO arrives early or the machinery refuses to keep burning. Two
knobs, two firing modes:

- **Pass deadline** — passes come only from the factory CLI ``pass-count``
  (one pass = one VALIDATING admission in the VERIFIED run ledger, plan §0.4b);
  never a line count. At ``signal_pass_warn`` a host WARN event lands on the
  operator channel; at the deadline with no NO-relevant signal the watchdog
  records the terminal NO itself (``record_no.sh --kind watchdog-deadline`` — a
  BOUND, class-excluded from the rewarded set) and halts dispatch. The
  ``record_no`` exit code is CHECKED: a refused record leaves the run open and
  raises ``record_no_refused`` — the deadline is then unsatisfied, not silently
  recorded (verification round-3 carryover).

- **Wall-clock backstop** — the only control that catches the zero-pass
  hung-lane class. The gap derives from this watchdog's own host-timestamped
  observations of the pass count (clock OWNERSHIP: ledger entries carry
  caller-stamped created_at, and deriving the gap from those verified stamps
  remains a legitimate future option — round-5 F-5), persisted in
  ``watchdog.json``. It fires only on a stall seen across TWO
  live checks, so a dead watchdog can never misattribute its own absence as a
  run stall; firing writes a blocking operator event — never silence, and never
  an automatic terminal (a stall is an alarm; the deadline owns the NO).

Tamper scope, stated plainly: until Phase 2.2 keys the run ledger, a
whole-chain rewrite can mint pass counts — the deadline claims correct behavior
over an INTACT ledger, not tamper-proofness (round-5 F-8.1).

Knob values come ONLY from the frozen generation blob via the factory CLI
``signal-knobs`` door — this module reads no ambient environment for any knob
(``WAKE_TIMEOUT`` on the neighboring seam is the anti-precedent). NO-relevance
comes from the committed baseline's kind classification; ``signal_pass_warn``
events are in neither registry and therefore never NO-relevant.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import time
from collections.abc import Callable, Mapping

_STATE_NAME = "watchdog.json"
_TERMINAL_STATUSES = {"closed", "no"}


class WatchdogError(RuntimeError):
    pass


def _load_json(path: pathlib.Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


class SignalWatchdog:
    def __init__(
        self,
        *,
        root: pathlib.Path,
        runs_root: pathlib.Path,
        run_id: str,
        factory_cli: list[str],
        replay_authority_args: tuple[str, ...],
        harness_dir: pathlib.Path,
        now: Callable[[], float] = time.time,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        self.root = root
        self.runs_root = runs_root
        self.run_id = run_id
        self.factory_cli = factory_cli
        self.replay_authority_args = replay_authority_args
        self.harness_dir = harness_dir
        self.now = now
        self.runner = runner
        self.state_path = root / _STATE_NAME
        self._knobs: dict | None = None

    # -- inputs (host-owned, never ambient) ----------------------------------

    def _cli_json(self, command: str) -> dict:
        result = self.runner(
            [
                *self.factory_cli,
                command,
                "--runs",
                str(self.runs_root),
                "--run-id",
                self.run_id,
                *self.replay_authority_args,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise WatchdogError(f"{command} refused: {result.stderr.strip()[:200]}")
        try:
            document = json.loads(result.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError) as exc:
            raise WatchdogError(f"{command} output unreadable: {exc}") from exc
        if not isinstance(document, Mapping):
            raise WatchdogError(f"{command} output is not an object")
        return dict(document)

    def knobs(self) -> dict:
        if self._knobs is None:
            self._knobs = self._cli_json("signal-knobs")
        return self._knobs

    def _no_relevant_kinds(self) -> set[str]:
        baseline = _load_json(self.harness_dir.parent / "acceptance_baseline.json")
        kinds = baseline.get("no_relevant_kinds")
        if not isinstance(kinds, Mapping):
            return set()
        return {kind for kind, relevant in kinds.items() if relevant is True}

    def _no_relevant_signal_present(self) -> bool:
        relevant = self._no_relevant_kinds()
        events_path = self.root / "events.jsonl"
        try:
            lines = events_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return False
        for line in lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("kind") in relevant:
                return True
        return False

    def _harness_status(self) -> str:
        return str(_load_json(self.root / "harness.json").get("status", ""))

    # -- state ----------------------------------------------------------------

    def _write_state(self, state: dict) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")
        temporary.replace(self.state_path)

    # -- the check ------------------------------------------------------------

    def check(
        self,
        emit: Callable[..., None],
        block: Callable[[str, str, str], None],
    ) -> str:
        """One watchdog observation. Returns a verdict string for the caller's
        log; every non-ok verdict has already emitted its host event."""

        status = self._harness_status()
        if status in _TERMINAL_STATUSES:
            return "terminal"
        try:
            knobs = self.knobs()
            passes = int(self._cli_json("pass-count")["passes"])
        except (WatchdogError, KeyError, ValueError, TypeError) as exc:
            # Round-6 6-8: a persistent observation refusal must not silently disarm
            # both firing modes — count consecutive errors across lives and escalate
            # (wake=True, once) at the threshold; a later good observation re-arms.
            state = _load_json(self.state_path)
            errors = state.get("consecutive_errors")
            errors = (errors if isinstance(errors, int) and errors >= 0 else 0) + 1
            state["consecutive_errors"] = errors
            escalate = errors >= 3 and not state.get("error_escalated")
            if escalate:
                state["error_escalated"] = True
            self._write_state(state)
            emit(
                "watchdog_error",
                f"observation refused ({errors} consecutive): {exc}"
                + (" — monitoring degraded, operator attention required" if escalate else ""),
                wake=escalate,
            )
            return "error"

        state = _load_json(self.state_path)
        state["consecutive_errors"] = 0
        state["error_escalated"] = False
        # Round-6 6-7: tampered-but-valid-JSON state (wrong-shape ts/passes) degrades
        # to re-arm exactly like a deleted or invalid-JSON file — never a crash that
        # kills the only monitor. Numeric coercion happens HERE, once, fail-safe.
        observations = []
        state_corrupt = False
        for candidate in state.get("observations", []):
            if not isinstance(candidate, dict):
                state_corrupt = True
                continue
            try:
                observations.append(
                    {"ts": float(candidate["ts"]), "passes": int(candidate["passes"])}
                )
            except (KeyError, ValueError, TypeError):
                state_corrupt = True
        if state_corrupt:
            observations = []
            state["stall_seen_ts"] = None
            state["backstop_fired"] = False
            emit(
                "watchdog_state_reset",
                "persisted watchdog state was malformed: re-armed from empty "
                "(tamper or corruption; a stall must now be seen twice again)",
                wake=False,
            )
        current = self.now()
        if not observations or passes > observations[-1]["passes"]:
            observations.append({"ts": current, "passes": passes})
            observations = observations[-8:]
            state["stall_seen_ts"] = None
            # Round-6 6-6: progress re-arms the backstop — fired-once is a latch
            # against repeat pages for ONE stall, never permanent silence.
            state["backstop_fired"] = False
        previous_check = state.get("last_check_ts")
        state["last_check_ts"] = current
        state["observations"] = observations

        verdict = "ok"
        deadline = int(knobs["signal_pass_deadline"])
        warn = int(knobs["signal_pass_warn"])
        cap_seconds = float(knobs["signal_wall_clock_cap_hours"]) * 3600.0

        if passes >= warn and not state.get("warned"):
            state["warned"] = True
            emit(
                "signal_pass_warn",
                f"pass {passes}/{deadline}: warn threshold reached with no terminal yet",
                wake=True,
            )
            verdict = "warned"

        signal_present = self._no_relevant_signal_present()
        if passes >= deadline and not signal_present and not state.get("fired"):
            # Residual-blocker refinement lands with Phase 1's preflight; until
            # then no-signal-and-not-terminal is the fail-closed firing form.
            record = self.runner(
                [
                    "bash",
                    str(self.harness_dir / "record_no.sh"),
                    self.run_id,
                    "--kind",
                    "watchdog-deadline",
                    "--reason",
                    f"signal deadline expired at pass {passes}/{deadline} "
                    f"with no NO-relevant signal",
                    "--runs",
                    str(self.runs_root),
                ],
                capture_output=True,
                text=True,
            )
            if record.returncode != 0:
                emit(
                    "record_no_refused",
                    f"deadline expired but the terminal NO was refused "
                    f"(rc={record.returncode}): {record.stderr.strip()[:200]} — "
                    f"run stays open, deadline UNSATISFIED",
                    wake=True,
                )
                verdict = "refused"
            else:
                state["fired"] = True
                emit(
                    "signal_deadline_expired",
                    f"pass {passes}/{deadline}: terminal NO recorded "
                    f"(watchdog-deadline, class bound) — halting dispatch",
                    wake=True,
                )
                verdict = "deadline-fired"

        if verdict in {"ok", "warned"} and observations:
            gap = current - observations[-1]["ts"]
            if gap > cap_seconds:
                stall_seen = state.get("stall_seen_ts")
                if not isinstance(stall_seen, (int, float)):
                    stall_seen = None
                if stall_seen is None:
                    # First live sighting of the stall: arm, never fire — a
                    # watchdog outage must not be misattributed as a run stall.
                    state["stall_seen_ts"] = current
                elif previous_check is not None and not state.get("backstop_fired"):
                    state["backstop_fired"] = True
                    detail = (
                        f"no pass advance for {gap / 3600.0:.1f}h "
                        f"(cap {cap_seconds / 3600.0:.1f}h) across two live checks"
                    )
                    block("validator", "wall_clock_backstop", detail)
                    emit("wall_clock_backstop_expired", detail, wake=True)
                    verdict = "backstop-fired"

        self._write_state(state)
        return verdict
