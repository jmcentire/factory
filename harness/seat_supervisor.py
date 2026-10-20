#!/usr/bin/env python3
"""The seats work only while the Orchestrator is judging them.

A small deterministic process beside the run. It enforces no rule itself; the rules are the
Orchestrator's judgment (and its System One screen). It enforces two things code can:

1. No judgment, no work. When activity has gone unassessed for longer than the lease (the
   Orchestrator is absent, stalled, or blind), every working seat (the Validator, the Coder, the
   Tester) is paused with SIGSTOP, and the founder's dash gets one critical item. When the
   Orchestrator catches up, the seats resume with SIGCONT. Pausing keeps each long-lived
   session and its context; nothing restarts.
2. The Orchestrator's hand on the brake. `control` lets the Orchestrator correct a seat (the
   correction is delivered into the seat's live session), stop a seat that is breaking a rule
   (paused until the Orchestrator resumes it), or resume it. Every control row names the rule,
   the evidence and the correction; a stop without a correction is refused, because stopping
   corrects nothing.

When the session that kicked off the run is the Validator (harness.json validator_agent
"kickoff"), its window is validator_mirror.py: what is typed there reaches the Validator's inbox.
That session is the founder's conversation, so it is never frozen. Stopping it holds every lane
instead, so it has nothing to drive until the Orchestrator resumes it, and the correction is in
its inbox.

The supervisor also beats Herald's heartbeat, so if it dies, Herald raises the stale alert.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

_HARNESS_ROOT = str(pathlib.Path(__file__).resolve().parent)
if _HARNESS_ROOT not in sys.path:
    sys.path.insert(0, _HARNESS_ROOT)

import escalate  # noqa: E402

SEAT_PREFIXES = ("validator", "coder", "tester")
ACTIONS = ("correct", "stop", "resume")
DOWN = "orchestrator-down"
HELD = "validator-stopped"


class SupervisorError(RuntimeError):
    pass


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _iso(moment: datetime.datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _parse(value: object) -> datetime.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=datetime.UTC)


def _folder(root: pathlib.Path) -> pathlib.Path:
    path = root / "orchestrator"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _append(path: pathlib.Path, row: Mapping[str, object]) -> None:
    line = json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def _event(root: pathlib.Path, kind: str, detail: str, *, wake: bool = True) -> None:
    _append(
        root / "events.jsonl",
        {
            "ts": _iso(_now()),
            "kind": kind,
            "class": "seat",
            "source": "seat_supervisor.py",
            "wake": wake,
            "detail": detail,
        },
    )


class Journal:
    "Incremental reader: cursor timestamps from activity.jsonl, assessed cursor from reports.jsonl."

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self.offset = 0
        self.stamps: dict[int, datetime.datetime] = {}

    def _scan_activity(self) -> None:
        path = _folder(self.root) / "activity.jsonl"
        try:
            with path.open("rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read()
        except FileNotFoundError:
            return
        end = chunk.rfind(b"\n")
        if end < 0:
            return
        self.offset += end + 1
        for line in chunk[: end + 1].splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            cursor, stamp = row.get("cursor"), _parse(row.get("ts"))
            if isinstance(cursor, int) and stamp is not None:
                self.stamps[cursor] = stamp

    def assessed(self) -> int:
        through = 0
        try:
            text = (_folder(self.root) / "reports.jsonl").read_text(encoding="utf-8")
        except FileNotFoundError:
            return 0
        for line in text.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            value = row.get("assessment")
            cursor = value.get("through_cursor") if isinstance(value, dict) else None
            if isinstance(cursor, int) and not isinstance(cursor, bool):
                through = max(through, cursor)
        return through

    def oldest_unassessed(self) -> tuple[int, datetime.datetime | None]:
        "The first cursor the Orchestrator has not assessed, and when it was observed."
        self._scan_activity()
        through = self.assessed()
        pending = sorted(c for c in self.stamps if c > through)
        if not pending:
            return through, None
        return through, self.stamps[pending[0]]


class Seats:
    "tmux and signals, behind one seam so tests drive it without a terminal or real processes."

    def __init__(self, session: str) -> None:
        self.session = session

    def panes(self) -> dict[str, int]:
        "Working seats by window name and their pane's process id; the Orchestrator is never one."
        try:
            out = subprocess.run(
                [
                    "tmux",
                    "list-panes",
                    "-s",
                    "-t",
                    self.session,
                    "-F",
                    "#{window_name} #{pane_pid} #{pane_dead}",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return {}
        seats = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) != 3 or parts[2] == "1":
                continue
            name, pid = parts[0], parts[1]
            if name.startswith(SEAT_PREFIXES) and not name.startswith("watch-") and pid.isdigit():
                seats[name] = int(pid)
        return seats

    def _tree(self, pid: int) -> list[int]:
        try:
            out = subprocess.run(
                ["ps", "-A", "-o", "pid=,ppid="],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return [pid]
        children: dict[int, list[int]] = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                children.setdefault(int(parts[1]), []).append(int(parts[0]))
        tree, stack = [], [pid]
        while stack:
            current = stack.pop()
            tree.append(current)
            stack.extend(children.get(current, []))
        return tree

    def signal(self, pid: int, signum: int) -> None:
        "Signal the seat's whole process tree, so anything it launched pauses with it."
        for member in self._tree(pid):
            try:
                os.kill(member, signum)
            except ProcessLookupError:
                pass

    def deliver(self, seat: str, text: str) -> None:
        # Into the Validator's terminal directly. A headless lane reads no terminal, so its
        # correction goes to the Validator, which owns delivery into lane sessions.
        target, body = seat, text
        if not seat.startswith("validator"):
            target, body = (
                "validator",
                f"{text} Deliver this correction into {seat}'s own session now.",
            )
        subprocess.run(
            ["tmux", "send-keys", "-t", f"{self.session}:{target}", "-l", body],
            check=True,
            timeout=10,
        )
        subprocess.run(
            ["tmux", "send-keys", "-t", f"{self.session}:{target}", "Enter"], check=True, timeout=10
        )


def kickoff_validator(root: pathlib.Path) -> bool:
    "True when the session that kicked off the run is the Validator."
    try:
        metadata = json.loads((root / "harness.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(metadata, dict) and metadata.get("validator_agent") == "kickoff"


def _hold_lanes(state: dict[str, list[str]], panes: Mapping[str, int], seats: Seats) -> None:
    "Pause every lane while the kickoff Validator is stopped, including lanes launched since."
    for name, pid in panes.items():
        if name.startswith("validator"):
            continue
        reasons = state.setdefault(name, [])
        if HELD not in reasons:
            reasons.append(HELD)
            seats.signal(pid, signal.SIGSTOP)


def _release_lanes(state: dict[str, list[str]], panes: Mapping[str, int], seats: Seats) -> None:
    for name, pid in panes.items():
        reasons = state.get(name, [])
        if HELD in reasons:
            reasons.remove(HELD)
            if not reasons:
                seats.signal(pid, signal.SIGCONT)


def _validator_stopped(state: Mapping[str, Sequence[str]]) -> bool:
    return any(
        r.startswith("stop:")
        for name, rs in state.items()
        if name.startswith("validator")
        for r in rs
    )


def _state_path(root: pathlib.Path) -> pathlib.Path:
    return _folder(root) / "seat-state.json"


def load_state(root: pathlib.Path) -> dict[str, list[str]]:
    "Why each seat is paused. A seat runs only when its list is empty."
    try:
        value = json.loads(_state_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return (
        {k: list(v) for k, v in value.items() if isinstance(v, list)}
        if isinstance(value, dict)
        else {}
    )


def save_state(root: pathlib.Path, state: Mapping[str, Sequence[str]]) -> None:
    path = _state_path(root)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps({k: sorted(set(v)) for k, v in state.items() if v}, sort_keys=True),
        encoding="utf-8",
    )
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def control(
    root: pathlib.Path,
    seats: Seats,
    *,
    seat: str,
    action: str,
    rule: str,
    evidence: str,
    correction: str,
    by: str = "orchestrator",
) -> dict[str, Any]:
    "The Orchestrator's correct/stop/resume. Every action carries its rule and its correction."
    if action not in ACTIONS:
        raise SupervisorError(f"action must be one of {', '.join(ACTIONS)}")
    if not seat.startswith(SEAT_PREFIXES):
        raise SupervisorError("only the Validator, the Coder and the Tester are controlled seats")
    rule, evidence, correction = rule.strip(), evidence.strip(), correction.strip()
    if action != "resume" and not (rule and evidence and correction):
        raise SupervisorError(
            "correct and stop name the rule, the evidence and the exact correction"
        )
    panes = seats.panes()
    if seat not in panes:
        raise SupervisorError(f"no live seat named {seat!r} in this run")
    state = load_state(root)
    reasons = state.setdefault(seat, [])
    row: dict[str, Any] = {
        "at": _iso(_now()),
        "seat": seat,
        "action": action,
        "rule": rule,
        "evidence": evidence[:2000],
        "correction": correction[:2000],
        "by": by,
    }
    kickoff = seat.startswith("validator") and kickoff_validator(root)
    if action == "stop":
        if f"stop:{rule}" not in reasons:
            reasons.append(f"stop:{rule}")
        if kickoff:
            _hold_lanes(state, panes, seats)
        else:
            seats.signal(panes[seat], signal.SIGSTOP)
    elif action == "resume":
        state[seat] = [r for r in reasons if not r.startswith("stop:")]
        if kickoff:
            _release_lanes(state, panes, seats)
        elif not state[seat]:
            seats.signal(panes[seat], signal.SIGCONT)
    if action in ("correct", "stop"):
        # Typed into the seat's own terminal. A stopped seat holds it in its input until the
        # Orchestrator resumes it, so the correction is the first thing it reads.
        seats.deliver(
            seat,
            f"FACTORY_CORRECTION from the Orchestrator. Rule: {rule}. "
            f"Evidence: {evidence} Correction: {correction}",
        )
    save_state(root, state)
    _append(_folder(root) / "seat-control.jsonl", row)
    _event(root, f"seat-{action}", f"{seat}: {rule or 'resumed'}")
    return row


def tick(
    root: pathlib.Path,
    seats: Seats,
    journal: Journal,
    *,
    ttl: datetime.timedelta,
    now: Callable[[], datetime.datetime] = _now,
    notify: Callable[..., object] = escalate.escalate,
) -> dict[str, Any]:
    "One pass: pause every seat when judgment lags past the lease; resume when it is current."
    through, oldest = journal.oldest_unassessed()
    lagging = oldest is not None and now() - oldest > ttl
    panes = seats.panes()
    state = load_state(root)
    kickoff = kickoff_validator(root)
    was_down = any(DOWN in reasons for reasons in state.values())
    if kickoff and _validator_stopped(state):
        _hold_lanes(state, panes, seats)
    for seat, pid in panes.items():
        if kickoff and seat.startswith("validator"):
            continue  # the mirror of the founder's own session; its lanes are what pause
        reasons = state.setdefault(seat, [])
        if lagging and DOWN not in reasons:
            reasons.append(DOWN)
            seats.signal(pid, signal.SIGSTOP)
        elif not lagging and DOWN in reasons:
            reasons.remove(DOWN)
            if not reasons:
                seats.signal(pid, signal.SIGCONT)
    save_state(root, state)
    if lagging and not was_down:
        age = int((now() - oldest).total_seconds() // 60) if oldest else 0
        detail = (
            f"Activity after cursor {through} has gone unassessed for {age} min. Every seat is "
            f"paused until the Orchestrator assesses it. Check the orchestrator window."
        )
        _event(root, "seats-paused-orchestrator-lagging", detail)
        if kickoff and "validator" in panes:
            try:
                seats.deliver("validator", f"FACTORY_NOTICE from the seat supervisor. {detail}")
            except (OSError, subprocess.SubprocessError) as error:
                _event(root, "validator-notice-failed", str(error), wake=False)
        try:
            notify(
                root,
                level="critical",
                key=DOWN,
                summary="Factory paused: the Orchestrator is not judging",
                detail=detail,
            )
        except escalate.EscalationError as error:
            _event(root, "escalation-failed", f"critical {DOWN}: {error}")
    elif was_down and not lagging:
        _event(
            root,
            "seats-resumed-orchestrator-current",
            f"assessed through cursor {through}",
            wake=False,
        )
        try:
            escalate.resolve(root, key=DOWN)
        except escalate.EscalationError:
            pass
    return {
        "through": through,
        "lagging": lagging,
        "seats": sorted(panes),
        "paused": {k: v for k, v in state.items() if v},
    }


def run(
    root: pathlib.Path, session: str, *, ttl_minutes: float, poll_seconds: float, beat_seconds: int
) -> int:
    seats, journal = Seats(session), Journal(root)
    ttl = datetime.timedelta(minutes=ttl_minutes)
    last_beat = 0.0
    _event(
        root, "seat-supervisor-started", f"lease {ttl_minutes} min, session {session}", wake=False
    )
    while True:
        try:
            tick(root, seats, journal, ttl=ttl)
        except Exception as error:  # the supervisor never dies on one bad pass
            _event(root, "seat-supervisor-error", f"{error.__class__.__name__}: {error}")
        if time.monotonic() - last_beat >= beat_seconds:
            last_beat = time.monotonic()
            try:
                escalate.heartbeat(root, interval=max(60, beat_seconds * 2))
            except escalate.EscalationError as error:
                _event(root, "heartbeat-failed", str(error), wake=False)
        if (
            not subprocess.run(
                ["tmux", "has-session", "-t", session], capture_output=True
            ).returncode
            == 0
        ):
            _event(root, "seat-supervisor-stopped", "the run's tmux session ended", wake=False)
            try:
                escalate.heartbeat(root, interval=max(60, beat_seconds * 2), done=True)
            except escalate.EscalationError as error:
                _event(root, "heartbeat-failed", str(error), wake=False)
            return 0
        time.sleep(poll_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    loop = sub.add_parser("run")
    loop.add_argument("--root", required=True, type=pathlib.Path)
    loop.add_argument("--session", required=True)
    loop.add_argument("--lease-minutes", type=float, default=15.0)
    loop.add_argument("--poll-seconds", type=float, default=30.0)
    loop.add_argument("--heartbeat-seconds", type=int, default=300)
    act = sub.add_parser("control")
    act.add_argument("--root", required=True, type=pathlib.Path)
    act.add_argument("--session", required=True)
    act.add_argument("--seat", required=True)
    act.add_argument("--action", required=True, choices=ACTIONS)
    act.add_argument("--rule", default="")
    act.add_argument("--evidence", default="")
    act.add_argument("--correction", default="")
    show = sub.add_parser("status")
    show.add_argument("--root", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return run(
                args.root,
                args.session,
                ttl_minutes=args.lease_minutes,
                poll_seconds=args.poll_seconds,
                beat_seconds=args.heartbeat_seconds,
            )
        if args.command == "control":
            row = control(
                args.root,
                Seats(args.session),
                seat=args.seat,
                action=args.action,
                rule=args.rule,
                evidence=args.evidence,
                correction=args.correction,
            )
            print(json.dumps(row, sort_keys=True))
            return 0
        print(json.dumps(load_state(args.root), sort_keys=True))
        return 0
    except SupervisorError as error:
        print(f"seat-supervisor: {error}", file=sys.stderr)
        return 64


if __name__ == "__main__":
    raise SystemExit(main())
