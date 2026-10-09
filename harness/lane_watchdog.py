#!/usr/bin/env python3
"""lane_watchdog — the progress watchdog every tmux author lane runs beside.

``harness/watchdog.py`` watches the run (the signal deadline); this watches one lane.
``tmux_lane.sh`` starts it in a ``watch-<lane>`` window beside every lane it launches and
refuses the launch when it cannot; ``tmux_lane_message.sh`` starts a new life of it when it
resumes a lane. Its inputs are the lane's launch row, never the environment.

msg-r2 lost about $1,200 of metered credit to a Tester model that read for hours without
committing, and hours more to lanes that finished and sat idle because nothing noticed
(docs/practices/lessons-msg-r2-2026-10.md §2-§3). A launcher that leaves those to the
Validator's attention has no control at all, so the watcher, not a person, does three things:

- **Stall stop.** After a read allowance (default 15 minutes) a lane must commit within the
  stall window (default 20 minutes), measured from the allowance's end or its last commit,
  whichever is later. A lane with no commit in that window is stopped (SIGTERM to its pane's
  process group), a ``refusal-lane-stall`` event is recorded, and the operator is woken. A lane
  waiting on its own unanswered FACTORY_QUESTION is waiting on the Validator, not stalled (the
  LIVE gate), so the window is held open while a question is pending.
- **Cap stop.** A metered lane runs under a spend cap. None of the agents we run exposes a live
  spend figure, so the enforced proxy is wall-clock time: spend is estimated as
  ``usd_per_hour x active time`` (the rate declared at launch) and the lane is stopped
  (``refusal-lane-cap``) when the estimate reaches the cap or the declared wall-clock cap
  passes, whichever comes first.
- **Completion wake.** When the lane prints ``__LANE_DONE__ <slot> commits=<n>`` or its process
  ends (the ``lane_agent.py run`` exit record; ``__LANE_EXIT__`` in the log), the operator is
  woken.

Commits are read from the lane repository's reflog *file*, never by running git: the repository
is agent-owned once the agent starts, and host git there would execute the agent's
configuration. Every lane's end appends one row to ``tmux-lanes/lane-output.jsonl`` (commits,
time to first commit, wall time, spend, commits per dollar; ``lane_watchdog.py report``). A
qualification probe (``tmux_lane.sh <run> <role> qualify``) also appends its pass or fail to
the runs directory's ``lane-qualifications.jsonl``, which a metered launch requires.

Waking the operator writes one row to the run's ``events.jsonl`` (``wake: true``) and one to
``tmux-lanes/wake.jsonl`` (the file a Validator's monitor tails), prints a ``FACTORY_WAKE`` line
in the watcher's window, and flashes a tmux message to the run's session.
"""

from __future__ import annotations

import argparse
import datetime
import decimal
import json
import os
import pathlib
import re
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from typing import Any


class LaneWatchError(RuntimeError):
    pass


def _load_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


LANE_WATCH_SCHEMA = "factory-lane-watch/1"
LANE_OUTPUT_SCHEMA = "factory-lane-output/1"
QUALIFICATION_SCHEMA = "factory-lane-qualification/1"
LANE_FINAL = frozenset({"done", "exited", "stalled", "capped"})
DEFAULT_READ_ALLOWANCE_S = 15 * 60
DEFAULT_STALL_WINDOW_S = 20 * 60
DEFAULT_POLL_S = 30
_DONE = re.compile(r"^\W*__LANE_DONE__\s+\S+\s+commits=\d+\W*$")
_REFLOG = re.compile(r"^([0-9a-f]{40,64}) ([0-9a-f]{40,64}) [^\t]*> (\d+) [+-]\d{4}\t(.*)$")
_MAX_REFLOG_BYTES = 4 * 1024 * 1024
_MAX_LOG_SCAN_BYTES = 1024 * 1024
_MAX_PANE_LINES = 2000


def _bounded_tail(path: pathlib.Path, ceiling: int, offset: int = 0) -> tuple[bytes, int]:
    """Read at most ``ceiling`` bytes of a regular, non-symlinked file from ``offset`` (or its
    last ``ceiling`` bytes when ``offset`` is negative). Missing reads as empty."""

    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return b"", max(offset, 0)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            return b"", max(offset, 0)
        start = max(metadata.st_size - ceiling, 0) if offset < 0 else min(offset, metadata.st_size)
        os.lseek(descriptor, start, os.SEEK_SET)
        raw = os.read(descriptor, ceiling)
        return raw, start + len(raw)
    finally:
        os.close(descriptor)


def read_commits(repository: pathlib.Path, since: float) -> list[dict[str, Any]]:
    """The commits made in ``repository`` at or after ``since``, from ``.git/logs/HEAD``.

    No git process runs: the bytes are parsed. A symlinked ``.git`` path reads as no commits,
    so a lane cannot point its watcher at another repository's history."""

    git = repository / ".git"
    reflog = git / "logs" / "HEAD"
    if any(path.is_symlink() for path in (git, git / "logs", reflog)):
        return []
    raw, _ = _bounded_tail(reflog, _MAX_REFLOG_BYTES, -1)
    floor = int(since)
    commits: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        matched = _REFLOG.match(line)
        if matched is None or not matched.group(4).startswith("commit"):
            continue
        stamp = int(matched.group(3))
        if stamp >= floor:
            commits.append(
                {"sha": matched.group(2), "ts": stamp, "subject": matched.group(4)[:200]}
            )
    return commits


def _done_in(text: str) -> bool:
    """A ``__LANE_DONE__ <slot> commits=<n>`` line, in plain output or inside a Codex agent
    message. The protocol states the form with ``<n>`` unfilled, so an echoed prompt is not a
    completion."""

    for line in text.splitlines():
        if _DONE.match(line):
            return True
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = value.get("item") if isinstance(value, dict) else None
        if (
            isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
            and any(_DONE.match(part) for part in item["text"].splitlines())
        ):
            return True
    return False


def _epoch(stamp: object) -> float:
    if isinstance(stamp, (int, float)):
        return float(stamp)
    return datetime.datetime.fromisoformat(str(stamp)).timestamp()


def _iso(epoch: float) -> str:
    return datetime.datetime.fromtimestamp(epoch, datetime.UTC).isoformat(timespec="seconds")


def _read_rows(path: pathlib.Path) -> list[dict[str, Any]]:
    if path.is_symlink():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _append_row(path: pathlib.Path, row: Mapping[str, object]) -> None:
    payload = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _tmux(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *arguments], capture_output=True, text=True, check=False, timeout=15
    )


def stop_pane(target: str, tmux: Callable[..., subprocess.CompletedProcess[str]] = _tmux) -> str:
    """SIGTERM the process group of the lane's pane; the pane stays (remain-on-exit) so the
    lane can still be frozen. Returns what was done, for the record."""

    shown = tmux("display-message", "-p", "-t", target, "#{pane_pid} #{pane_dead}")
    fields = shown.stdout.split()
    if shown.returncode != 0 or len(fields) != 2 or not fields[0].isdigit():
        return f"pane {target} not found; nothing to stop"
    if fields[1] == "1":
        return f"pane {target} had already exited"
    pid = int(fields[0])
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        return f"pane {target} process {pid} had already exited"
    return f"sent SIGTERM to the process group of {target} (pid {pid})"


class LaneWatch:
    """One lane's watcher. ``check`` is one observation; ``LANE_FINAL`` verdicts end it."""

    def __init__(
        self,
        *,
        root: pathlib.Path,
        slot: str,
        now: Callable[[], float] = time.time,
        stop: Callable[[str], str] = stop_pane,
        tmux: Callable[..., subprocess.CompletedProcess[str]] = _tmux,
        echo: Callable[[str], None] = print,
    ) -> None:
        self.root = pathlib.Path(root)
        self.lanes = self.root / "tmux-lanes"
        self.slot = slot
        self.now = now
        self.stop = stop
        self.tmux = tmux
        self.echo = echo
        self.journal = self.lanes / f"{slot}-launch.jsonl"
        self.state_path = self.lanes / f"{slot}-watch.json"

    # -- inputs ------------------------------------------------------------------------------

    def launch(self) -> dict[str, Any]:
        active = [row for row in _read_rows(self.journal) if row.get("status") == "active"]
        if len(active) != 1:
            raise LaneWatchError(f"{self.journal} holds no single active launch to watch")
        return active[0]

    def _pending_question(self, role: str) -> bool:
        harness = str(pathlib.Path(__file__).resolve().parent)
        if harness not in sys.path:
            sys.path.insert(0, harness)
        try:
            from lane_dialogue import pending_questions

            return bool(pending_questions(self.root, role))
        except Exception:  # noqa: BLE001 - an unreadable journal must not disarm the watch
            return False

    def _scan_output(self, launch: Mapping[str, Any], state: dict[str, Any]) -> bool:
        log = pathlib.Path(str(launch.get("lane_log", "")))
        raw, offset = _bounded_tail(log, _MAX_LOG_SCAN_BYTES, int(state.get("log_offset", 0)))
        state["log_offset"] = offset
        text = raw.decode("utf-8", errors="replace")
        if launch.get("terminal"):
            # A terminal agent keeps its pane; its output never reaches the log.
            shown = self.tmux("capture-pane", "-p", "-t", str(launch.get("tmux_target", "")),
                              "-S", f"-{_MAX_PANE_LINES}")
            if shown.returncode == 0:
                text += "\n" + shown.stdout
        return _done_in(text)

    def _exits(self, launch: Mapping[str, Any]) -> list[dict[str, Any]]:
        return _read_rows(pathlib.Path(str(launch.get("lane_exits", ""))))

    # -- state -------------------------------------------------------------------------------

    def _write_state(self, state: Mapping[str, Any]) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.state_path)

    def arm(self, *, new_life: bool = False) -> dict[str, Any]:
        """Start this watcher's life, or continue it after the watcher itself restarted.

        ``new_life`` is a resumed lane turn (tmux_lane_message.sh): the earlier life's totals
        carry over, so caps count all of the lane's active time, and only exits after this
        point end the new life."""

        launch = self.launch()
        prior = _load_json(self.state_path)
        current = self.now()
        if prior.get("status") == "watching" and not new_life:
            return prior
        earlier = float(prior.get("active_s", 0.0))
        if prior.get("status") == "watching":
            earlier = float(prior.get("active_s_before", 0.0)) + max(
                0.0, current - float(prior.get("life_started", current))
            )
        state: dict[str, Any] = {
            "schema_version": LANE_WATCH_SCHEMA,
            "slot": self.slot,
            "launch_ts": _epoch(launch["ts"]),
            "life_started": current,
            "active_s_before": earlier,
            "lives": int(prior.get("lives", 0)) + 1,
            "exits_baseline": len(self._exits(launch)),
            "log_offset": int(prior.get("log_offset", 0)),
            "commits": list(prior.get("commits", [])),
            "hold_ts": None,
            "status": "watching",
        }
        self._write_state(state)
        return state

    # -- effects -----------------------------------------------------------------------------

    def _wake(self, launch: Mapping[str, Any], kind: str, detail: str, *, refusal: bool) -> None:
        harness = str(pathlib.Path(__file__).resolve().parent)
        if harness not in sys.path:
            sys.path.insert(0, harness)
        from attention_gate import _append_jsonl, append_refusal_event

        message = f"{self.slot}: {detail}"
        if refusal:
            append_refusal_event(self.root, kind=kind, source="watchdog.py lane",
                                 detail=message, exit_code=70)
        else:
            _append_jsonl(self.root / "events.jsonl", {
                "ts": _iso(self.now()), "kind": kind, "class": "lane",
                "source": "watchdog.py lane", "detail": message[:4096], "wake": True,
            })
        _append_row(self.lanes / "wake.jsonl", {
            "ts": _iso(self.now()), "kind": kind, "slot": self.slot, "detail": detail[:4096],
        })
        self.echo(f"FACTORY_WAKE {kind} {message}")
        session = str(launch.get("tmux_target", "")).split(":", 1)[0]
        if session:
            try:
                self.tmux("display-message", "-t", session, f"FACTORY_WAKE {kind} {message}"[:200])
            except (OSError, subprocess.SubprocessError):
                pass

    def _finish(self, launch: Mapping[str, Any], state: dict[str, Any], status: str,
                active: float, spend: decimal.Decimal | None) -> dict[str, Any]:
        state["status"] = status
        state["ended_ts"] = self.now()
        state["active_s"] = active
        commits = state["commits"]
        first = (min(c["ts"] for c in commits) - state["launch_ts"]) if commits else None
        output: dict[str, Any] = {
            "schema_version": LANE_OUTPUT_SCHEMA,
            "ts": _iso(self.now()),
            "slot": self.slot,
            "lane": launch.get("lane"),
            "role": launch.get("role"),
            "agent": launch.get("agent"),
            "model": launch.get("model"),
            "outcome": status,
            "commits": len(commits),
            "time_to_first_commit_s": first,
            "wall_s": round(active, 1),
            "spend_usd": None if spend is None else str(spend.quantize(decimal.Decimal("0.01"))),
            "spend_basis": "declared-usd-per-hour-x-wall-clock" if spend is not None
            else "no-declared-rate",
            "commits_per_usd": (
                None if not spend else round(len(commits) / float(spend), 3)
            ),
        }
        _append_row(self.lanes / "lane-output.jsonl", output)
        qualify = launch.get("qualify")
        if isinstance(qualify, Mapping):
            reasons = []
            minimum = int(qualify.get("min_commits", 1))
            limit = float(qualify.get("first_commit_s", DEFAULT_READ_ALLOWANCE_S))
            if len(commits) < minimum:
                reasons.append(f"{len(commits)} commit(s), below the {minimum} required")
            if first is None or first > limit:
                reasons.append(
                    "no commit" if first is None
                    else f"first commit after {first:.0f}s, past the {limit:.0f}s limit"
                )
            verdict = "fail" if reasons else "pass"
            ledger = pathlib.Path(str(qualify.get("ledger") or self.lanes / "qualifications.jsonl"))
            _append_row(ledger, {
                "schema_version": QUALIFICATION_SCHEMA, "ts": _iso(self.now()),
                "slot": self.slot, "role": launch.get("role"), "agent": launch.get("agent"),
                "model": launch.get("model"), "verdict": verdict, "reasons": reasons,
                "outcome": status, "commit_count": len(commits),
                "time_to_first_commit_s": first, "wall_s": round(active, 1),
                "spend_usd": output["spend_usd"],
            })
            state["qualification"] = verdict
            output["qualification"] = verdict
        self._write_state(state)
        return output

    # -- the check ---------------------------------------------------------------------------

    def check(self) -> str:
        launch = self.launch()
        state = _load_json(self.state_path)
        if state.get("status") in LANE_FINAL:
            return str(state["status"])
        if state.get("status") != "watching":
            state = self.arm()
        current = self.now()
        active = float(state["active_s_before"]) + max(0.0, current - float(state["life_started"]))
        watch = launch.get("watch") or {}
        caps = launch.get("caps") or {}
        allowance = float(watch.get("read_allowance_s", DEFAULT_READ_ALLOWANCE_S))
        window = float(watch.get("stall_window_s", DEFAULT_STALL_WINDOW_S))

        known = {commit["sha"] for commit in state["commits"]}
        for commit in read_commits(pathlib.Path(str(launch["repository"])), state["launch_ts"]):
            if commit["sha"] not in known:
                state["commits"].append(commit)
                known.add(commit["sha"])
        pending = self._pending_question(str(launch.get("role", "")))
        if pending:
            state["hold_ts"] = current
        done = self._scan_output(launch, state)
        exits = self._exits(launch)[int(state["exits_baseline"]):]

        rate = caps.get("usd_per_hour")
        spend = (
            decimal.Decimal(str(rate)) * decimal.Decimal(str(active)) / decimal.Decimal(3600)
            if rate is not None else None
        )
        summary = (
            f"{len(state['commits'])} commit(s), {active / 60:.1f} min"
            + (f", ~${spend:.2f} spent" if spend is not None else "")
        )
        if done or exits:
            status = "done" if done else "exited"
            rc = f" rc={exits[-1].get('rc')}" if exits else ""
            output = self._finish(launch, state, status, active, spend)
            self._wake(
                launch, f"lane-{status}",
                f"lane {status}{rc}: {summary}"
                + (f"; qualification {output['qualification']}" if "qualification" in output
                   else ""),
                refusal=False,
            )
            return status

        reason = ""
        wall_cap = caps.get("wall_cap_s")
        spend_cap = caps.get("spend_cap_usd")
        if wall_cap is not None and active >= float(wall_cap):
            reason = f"wall-clock cap of {float(wall_cap) / 60:.0f} min reached"
        elif spend is not None and spend_cap is not None and spend >= decimal.Decimal(
            str(spend_cap)
        ):
            reason = f"spend cap of ${spend_cap} reached (declared rate ${rate}/h)"
        if reason:
            stopped = self.stop(str(launch.get("tmux_target", "")))
            output = self._finish(launch, state, "capped", active, spend)
            self._wake(launch, "refusal-lane-cap",
                       f"lane stopped: {reason}; {summary}; {stopped}"
                       + (f"; qualification {output['qualification']}"
                          if "qualification" in output else ""),
                       refusal=True)
            return "capped"

        last_commit = max((float(c["ts"]) for c in state["commits"]), default=0.0)
        clock = max(float(state["life_started"]) + allowance, last_commit,
                    float(state["hold_ts"] or 0.0))
        if not pending and current - clock > window:
            stopped = self.stop(str(launch.get("tmux_target", "")))
            output = self._finish(launch, state, "stalled", active, spend)
            self._wake(launch, "refusal-lane-stall",
                       f"lane stopped: no commit for {(current - clock) / 60:.0f} min past its "
                       f"{allowance / 60:.0f} min read allowance and {window / 60:.0f} min "
                       f"window; {summary}; {stopped}"
                       + (f"; qualification {output['qualification']}"
                          if "qualification" in output else ""),
                       refusal=True)
            return "stalled"
        self._write_state(state)
        return "watching"

    def run(self, sleep: Callable[[float], None] = time.sleep, *, new_life: bool = False,
            launch_wait_s: float = 120.0) -> str:
        # tmux_lane.sh starts this window before it records the launch active; wait for it.
        waited = 0.0
        while True:
            try:
                self.launch()
                break
            except LaneWatchError:
                if waited >= launch_wait_s:
                    raise
                sleep(2.0)
                waited += 2.0
        self.arm(new_life=new_life)
        poll = float((self.launch().get("watch") or {}).get("poll_s", DEFAULT_POLL_S))
        while True:
            verdict = self.check()
            if verdict in LANE_FINAL:
                return verdict
            sleep(poll)


def lane_report(root: pathlib.Path) -> list[dict[str, Any]]:
    """Every finished lane's output row, then each lane still being watched, live."""

    lanes = pathlib.Path(root) / "tmux-lanes"
    rows = _read_rows(lanes / "lane-output.jsonl")
    for state_path in sorted(lanes.glob("*-watch.json")):
        state = _load_json(state_path)
        if state.get("status") == "watching":
            rows.append({
                "slot": state.get("slot"), "outcome": "watching",
                "commits": len(state.get("commits", [])),
            })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="lane progress watchdog")
    commands = parser.add_subparsers(dest="command", required=True)
    lane = commands.add_parser("lane", help="watch one launched lane until it ends")
    lane.add_argument("--root", type=pathlib.Path, required=True)
    lane.add_argument("--slot", required=True)
    lane.add_argument("--new-life", action="store_true",
                      help="a resumed lane turn: carry totals, watch only what follows")
    report = commands.add_parser("report", help="each lane's commits, wall time and spend")
    report.add_argument("--root", type=pathlib.Path, required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "report":
        for row in lane_report(arguments.root):
            print(json.dumps(row, sort_keys=True))
        return 0
    try:
        verdict = LaneWatch(root=arguments.root, slot=arguments.slot).run(
            new_life=arguments.new_life
        )
    except LaneWatchError as error:
        print(f"lane-watchdog: {error}", file=sys.stderr)
        return 70
    print(f"lane-watchdog: {arguments.slot} {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
