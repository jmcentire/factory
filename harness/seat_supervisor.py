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
The mirror is not the controlled process. A kickoff requires an explicit PID/start/pane binding
to the executing Validator. Missing or changed identity keeps author lanes held and produces
an unacknowledged control result, never a claim that the Validator stopped.

The supervisor also beats Herald's heartbeat, so if it dies, Herald raises the stale alert.
"""

from __future__ import annotations

import argparse
import datetime
import fcntl
import functools
import hashlib
import json
import os
import pathlib
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

_HARNESS_ROOT = str(pathlib.Path(__file__).resolve().parent)
if _HARNESS_ROOT not in sys.path:
    sys.path.insert(0, _HARNESS_ROOT)

import escalate  # noqa: E402
from lane_watchdog import process_snapshot, process_tree, signal_identity  # noqa: E402

SEAT_PREFIXES = ("validator", "coder", "tester")
ACTIONS = ("correct", "stop", "resume")
DOWN = "orchestrator-down"
HELD = "validator-stopped"
UNBOUND = "validator-identity-unavailable"
PAUSE_CONFIRM_SECONDS = 1.0


class SupervisorError(RuntimeError):
    pass


def _read_control(path: pathlib.Path) -> dict[str, Any]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if path.is_symlink():
            raise SupervisorError("symlinked control state") from None
        return {}
    with os.fdopen(fd, encoding="utf-8") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise SupervisorError("control state is not a regular file")
        value = json.load(handle)
    if not isinstance(value, dict):
        raise SupervisorError("control state is not an object")
    return value


def _store(path: pathlib.Path, value: object) -> None:
    fd, name = tempfile.mkstemp(prefix=".seat-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _locked(function):
    @functools.wraps(function)
    def locked(root, *args, **kwargs):
        fd = os.open(_folder(root) / "seat-control.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                     0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            return function(root, *args, **kwargs)
        finally:
            os.close(fd)
    return locked


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
        os.fsync(fd)
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
        "Working child identities from wrapper receipts; never pause a tmux pane root."
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
        except (OSError, subprocess.SubprocessError) as error:
            raise SupervisorError("seat inventory unavailable") from error
        seats = {}
        self.inventory_errors = []
        self.worker_bindings = {}
        table = process_snapshot()
        if table is None:
            raise SupervisorError("seat process observation unavailable")
        for line in out.splitlines():
            parts = line.split()
            if len(parts) != 3 or parts[2] == "1":
                continue
            name, pid = parts[0], parts[1]
            if name.startswith(SEAT_PREFIXES) and not name.startswith("watch-") and pid.isdigit():
                if name == "validator" and kickoff_validator(self.root):
                    continue  # this is the inbox mirror; external() identifies the worker
                pane_pid = int(pid)
                matches = []
                for path in [*self.root.glob("validator-process.json"),
                             *(self.root / "tmux-lanes").glob(f"{name}*-process.json")]:
                    receipt = _read_control(path)
                    wrapper = receipt.get("wrapper", {})
                    worker = receipt.get("worker", {})
                    if (receipt.get("schema") != "factory-worker-process/1"
                            or not isinstance(wrapper, dict) or not isinstance(worker, dict)
                            or wrapper.get("pid") != pane_pid or pane_pid not in table
                            or table[pane_pid]["started"] != wrapper.get("started")):
                        continue
                    if (worker.get("pid") in table and worker["pid"] != pane_pid
                            and table[worker["pid"]]["started"] == worker.get("started")
                            and worker in process_tree(table, [wrapper])):
                        matches.append(worker["pid"])
                if len(matches) == 1:
                    seats[name] = matches[0]
                    self.worker_bindings[name] = {"pid": matches[0],
                                                  "started": table[matches[0]]["started"]}
                else:
                    self.inventory_errors.append(f"{name}: no unique live wrapped worker identity")
        return seats

    def configure(self, root: pathlib.Path) -> None:
        self.root = root
        self.custody_path = _folder(root) / "seat-processes.json"

    def external(self, *, require_live: bool = True,
                 require_running: bool = False) -> dict[str, Any]:
        binding = _read_control(_folder(self.root) / "validator-process.json")
        if (binding.get("schema") != "factory-validator-process/1"
                or binding.get("root") != str(self.root.resolve())
                or binding.get("session") != self.session):
            raise SupervisorError("executing Validator has no matching process binding")
        identity = binding.get("identity", {})
        if (not isinstance(identity, dict)
                or type(identity.get("pid")) is not int or identity["pid"] <= 1
                or not isinstance(identity.get("started"), str) or not identity["started"]):
            raise SupervisorError("malformed Validator process identity")
        if (not isinstance(binding.get("pane"), str) or not binding["pane"].startswith("%")
                or not binding["pane"][1:].isdigit()
                or type(binding.get("pane_pid")) is not int
                or not isinstance(binding.get("pane_started"), str)):
            raise SupervisorError("malformed Validator pane identity")
        if not require_live:
            return identity
        if identity["pid"] == binding["pane_pid"]:
            raise SupervisorError("tmux pane-root is not resumable; bind the shell's worker child")
        table = process_snapshot()
        if table is None:
            raise SupervisorError("executing Validator process observation unavailable")
        row = table.get(identity["pid"])
        if row is None or row["started"] != identity.get("started"):
            raise SupervisorError("executing Validator identity is absent or changed")
        if row["state"].startswith("Z") or (require_running and "T" in row["state"]):
            raise SupervisorError("executing Validator is not running")
        shown = subprocess.run(
            ["tmux", "display-message", "-p", "-t", binding["pane"], "#{pane_pid}"],
            capture_output=True, text=True, check=True, timeout=10)
        pane_row = table.get(binding["pane_pid"])
        if (shown.stdout.strip() != str(binding["pane_pid"]) or pane_row is None
                or pane_row["started"] != binding.get("pane_started")):
            raise SupervisorError("executing Validator pane binding changed")
        return identity

    def transition(self, seat: str, pid: int, stopped: bool) -> dict[str, Any]:
        """Persist intent/attempts before signalling, then acknowledge observed process state.

        Signal delivery alone is never acknowledgement. Failed/uncertain attempts are retained
        without blind retries; new descendants observed under known identities are included.
        """
        records = _read_control(self.custody_path)
        prior = records.get(seat, {})
        desired = "stop" if stopped else "resume"
        try:
            table = process_snapshot()
            if table is None:
                raise SupervisorError("process observation unavailable")
            external = seat == "validator" and kickoff_validator(self.root)
            identity = (self.external(require_live=False) if external else
                        getattr(self, "worker_bindings", {}).get(seat, prior.get("identity")))
            if identity is None and pid in table:
                identity = {"pid": pid, "started": table[pid]["started"]}
            if identity is None:
                raise SupervisorError("seat identity unavailable")
            if identity["pid"] != pid:
                raise SupervisorError("seat PID changed; refusing to adopt another process")
            if prior.get("identity") and prior["identity"] != identity:
                old = process_tree(table, prior.get("identities", []))
                if external or any(i["pid"] in table
                                   and table[i["pid"]]["started"] == i["started"]
                                   and not table[i["pid"]]["state"].startswith("Z") for i in old):
                    raise SupervisorError("seat identity changed before prior workers exited")
                _append(_folder(self.root) / "seat-process-events.jsonl",
                        {"seat": seat, "terminal_previous": prior, "new_worker": identity})
                prior = {}
            if not stopped and not prior:
                raise SupervisorError("resume refused: no recorded stop")
            if not stopped and not any(i["pid"] in table
                                       and table[i["pid"]]["started"] == i["started"]
                                       and not table[i["pid"]]["state"].startswith("Z")
                                       for i in prior.get("identities", [])):
                raise SupervisorError("resume refused: controlled work exited; it is not paused")
            if not stopped and prior.get("requested") == "stop" and not prior.get("acknowledged"):
                raise SupervisorError("resume refused: prior stop is unacknowledged")
            if not prior.get("identity") or prior.get("requested") != desired:
                prior = {"identity": identity, "identities": prior.get("identities", [identity]),
                         "requested": desired, "attempts": {}, "acknowledged": False,
                         "requested_at": _iso(_now())}
            prior["identities"] = process_tree(table, prior["identities"])
            records[seat] = prior
            _store(self.custody_path, records)
            for member in prior["identities"]:
                key = f"{member['pid']}:{member['started']}"
                if key in prior["attempts"]:
                    continue
                # Persist the attempt first: even a crash during kill must not replay a signal.
                prior["attempts"][key] = "pending"
                _store(self.custody_path, records)
                try:
                    prior["attempts"][key] = signal_identity(
                        member, signal.SIGSTOP if stopped else signal.SIGCONT)
                except OSError as error:
                    prior["attempts"][key] = f"failed: {error}"
                _store(self.custody_path, records)
            observed = process_snapshot()
            if observed is None:
                raise SupervisorError("post-signal observation unavailable")
            extended = process_tree(observed, prior["identities"])
            live = [observed[i["pid"]] for i in extended if i["pid"] in observed
                    and observed[i["pid"]]["started"] == i["started"]
                    and not observed[i["pid"]]["state"].startswith("Z")]
            prior["identities"] = extended
            matching = all(("T" in i["state"]) == stopped for i in live)
            prior["acknowledged"] = matching
            if stopped and live:
                # A transient T snapshot is not a sustained pause: tmux may immediately
                # resume a pane root. Require repeated stopped states and unchanged CPU time.
                fingerprint = [[i["pid"], i["started"], i.get("cpu")] for i in live]
                stamp = time.time()
                stable = prior.get("pause_observation", {})
                if (not matching or any(i.get("cpu") is None for i in live)):
                    prior.pop("pause_observation", None)
                    prior["acknowledged"] = False
                elif (stable.get("fingerprint") != fingerprint
                      or stamp < float(stable.get("since", stamp))):
                    prior["pause_observation"] = {"fingerprint": fingerprint, "since": stamp}
                    prior["acknowledged"] = False
                else:
                    prior["acknowledged"] = stamp - stable["since"] >= PAUSE_CONFIRM_SECONDS
            prior["state"] = ("stopped" if stopped else "running") if live else "exited"
            if not prior["acknowledged"]:
                prior["state"] = "pending"
            prior["observed_at"] = _iso(_now())
            prior.pop("error", None)
        except (OSError, ValueError, subprocess.SubprocessError, SupervisorError) as error:
            prior.setdefault("requested", desired)
            prior.update(acknowledged=False, state="pending", error=str(error))
        records[seat] = prior
        _store(self.custody_path, records)
        _append(_folder(self.root) / "seat-process-events.jsonl", {"seat": seat, **prior})
        return dict(prior)

    def deliver(self, seat: str, text: str) -> dict[str, Any]:
        # Into the Validator's terminal directly. A headless lane reads no terminal, so its
        # correction goes to the Validator, which owns delivery into lane sessions.
        target, body = seat, text
        if not seat.startswith("validator"):
            target, body = (
                "validator",
                f"{text} Deliver this correction into {seat}'s own session now.",
            )
        if kickoff_validator(self.root):
            # A macOS canonical tty can discard the newline on a long correction. Writing
            # the existing kickoff inbox directly avoids that transport, and preserves the
            # notice while the executing Validator is stopped. Queued is not read/applied.
            self.external()
            folder = self.root / "validator"
            if folder.is_symlink() or not folder.is_dir():
                raise SupervisorError("Validator inbox directory is unavailable or aliased")
            path = folder / "inbox.jsonl"
            digest = hashlib.sha256(body.encode()).hexdigest()
            _append(path, {"at": _iso(_now()), "text": body, "source": "seat-supervisor",
                           "message_digest": digest})
            return {"state": "durable-queued", "inbox": str(path),
                    "message_digest": digest, "agent_acknowledged": False}
        subprocess.run(
            ["tmux", "send-keys", "-t", f"{self.session}:{target}", "-l", body],
            check=True,
            timeout=10,
        )
        subprocess.run(
            ["tmux", "send-keys", "-t", f"{self.session}:{target}", "Enter"], check=True, timeout=10
        )
        return {"state": "terminal-submitted", "agent_acknowledged": False}


def kickoff_validator(root: pathlib.Path) -> bool:
    "True when the session that kicked off the run is the Validator."
    try:
        metadata = json.loads((root / "harness.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as error:
        raise SupervisorError("Validator launch metadata unavailable or malformed") from error
    if not isinstance(metadata, dict):
        raise SupervisorError("Validator launch metadata is not an object")
    return metadata.get("validator_agent") == "kickoff"


def _targets(root: pathlib.Path, seats: Seats) -> tuple[dict[str, int], str | None]:
    seats.configure(root)
    fault = None
    try:
        panes = seats.panes()
    except SupervisorError as error:
        # Retain controlled identities so a failed tmux listing cannot erase custody.
        records = _read_control(_folder(root) / "seat-processes.json")
        panes = {name: value["identity"]["pid"] for name, value in records.items()
                 if isinstance(value, dict) and isinstance(value.get("identity"), dict)}
        fault = str(error)
    errors = getattr(seats, "inventory_errors", [])
    if errors:
        fault = "; ".join(errors)
    if kickoff_validator(root):
        panes.pop("validator", None)  # never signal the mirror in place of the real Validator
        try:
            panes["validator"] = seats.external(require_live=False)["pid"]
            seats.external()
        except (SupervisorError, OSError, ValueError, subprocess.SubprocessError) as error:
            return panes, str(error)
    return panes, fault


def _identity_hold(state: dict[str, list[str]], panes: Mapping[str, int],
                   fault: str | None) -> None:
    for name in set(panes) | ({"validator"} if fault else set()):
        reasons = state.setdefault(name, [])
        if fault and UNBOUND not in reasons:
            reasons.append(UNBOUND)
        elif not fault and UNBOUND in reasons:
            reasons.remove(UNBOUND)
            if not reasons:
                reasons.append("resume-pending")


def _apply(root: pathlib.Path, seats: Seats, state: dict[str, list[str]],
           panes: Mapping[str, int]) -> dict[str, Any]:
    save_state(root, state)  # hold intent survives crashes and failed signal/delivery
    results = {}
    for name, reasons in list(state.items()):
        if not reasons:
            continue
        if name not in panes:
            results[name] = {"acknowledged": False, "state": "pending",
                             "error": "controlled process identity unavailable"}
            continue
        stopped = any(r != "resume-pending" for r in reasons)
        result = seats.transition(name, panes[name], stopped)
        results[name] = result
        if not stopped and result["acknowledged"]:
            state[name] = []
    save_state(root, state)
    return results


def _validator_stopped(state: Mapping[str, Sequence[str]]) -> bool:
    return any(
        r.startswith("stop:") or r == "resume-pending"
        for name, rs in state.items()
        if name.startswith("validator")
        for r in rs
    )


def _state_path(root: pathlib.Path) -> pathlib.Path:
    return _folder(root) / "seat-state.json"


def _validator_binding(root: pathlib.Path, session: str, pid: int,
                       started: str, pane: str) -> dict:
    if not pane.startswith("%") or not pane[1:].isdigit() or pid <= 1:
        raise SupervisorError("binding needs an exact %pane and non-system PID")
    table = process_snapshot()
    if table is None or pid not in table or table[pid]["started"] != started:
        raise SupervisorError("Validator PID/start identity is not current")
    shown = subprocess.run(["tmux", "display-message", "-p", "-t", pane, "#{pane_pid}"],
                           capture_output=True, text=True, check=True, timeout=10)
    if not shown.stdout.strip().isdigit():
        raise SupervisorError("Validator pane has no process")
    pane_pid = int(shown.stdout.strip())
    if pid == pane_pid:
        raise SupervisorError("tmux pane-root cannot be a resumable Validator; use a shell child")
    if pane_pid not in table:
        raise SupervisorError("Validator pane process is unavailable")
    tree = process_tree(table, [{"pid": pane_pid, "started": table[pane_pid]["started"]}])
    identity = {"pid": pid, "started": started}
    if identity not in tree:
        raise SupervisorError("Validator process is not in the named pane")
    return {"schema": "factory-validator-process/1", "root": str(root.resolve()),
            "session": session, "identity": identity, "pane": pane, "pane_pid": pane_pid,
            "pane_started": table[pane_pid]["started"]}


@_locked
def bind_validator(root: pathlib.Path, session: str, pid: int, started: str, pane: str) -> dict:
    """Initial binding cannot overwrite custody. Maintenance uses explicit rebind instead."""
    binding = _validator_binding(root, session, pid, started, pane)
    path = _folder(root) / "validator-process.json"
    previous = _read_control(path)
    if previous and previous != binding:
        raise SupervisorError("different Validator binding already exists; refusing overwrite")
    _store(path, binding)
    return binding


@_locked
def rebind_validator(root: pathlib.Path, session: str, pid: int, started: str, pane: str,
                     previous_pid: int, previous_started: str, assessment_digest: str | None,
                     maintenance_reason: str | None = None) -> dict:
    """Host-only handoff to a paused incarnation; retain old custody and all stop reasons.

    This neither launches nor resumes a worker, nor clears attention/policy holds. All known
    prior descendants must be gone. The caller separately qualifies model, account and hooks.
    """
    import orchestrator_channel
    from lane_custody import _save_once

    # This is a host-only maintenance operation, not permission to resume a lane.
    # The external engineer's authority is not delegated to the application's AG.
    # Keep the existing reviewed handoff route for in-run callers; never mix modes.
    if maintenance_reason is not None:
        if (assessment_digest is not None or not isinstance(maintenance_reason, str)
                or not maintenance_reason.strip() or len(maintenance_reason.encode()) > 4096):
            raise SupervisorError(
                "maintenance handoff requires one bounded reason, not an assessment")
    binding = _validator_binding(root, session, pid, started, pane)
    old_identity = {"pid": previous_pid, "started": previous_started}
    if old_identity == binding["identity"]:
        raise SupervisorError("rebind requires a different process incarnation")
    subject: dict[str, Any] = {"previous_identity": old_identity, "binding": binding}
    if maintenance_reason is not None:
        subject["maintenance_reason"] = maintenance_reason
    digest = hashlib.sha256(json.dumps(subject, sort_keys=True).encode()).hexdigest()
    folder = _folder(root)
    receipt_path = folder / f"validator-rebind-{digest}.json"
    retained = _read_control(receipt_path)
    current = _read_control(folder / "validator-process.json")
    records = _read_control(folder / "seat-processes.json")
    previous = retained.get("previous_custody", records.get("validator", {}))
    old_binding = retained.get("previous_binding", current)
    if (old_binding.get("identity") != old_identity
            or old_binding.get("session") != session
            or old_binding.get("root") != str(root.resolve())
            or previous.get("identity") != old_identity
            or not isinstance(previous.get("identities"), list)
            or old_identity not in previous["identities"]
            or current not in (old_binding, binding)):
        raise SupervisorError("rebind does not match retained Validator custody")
    if retained and retained.get("subject") != subject:
        raise SupervisorError("rebind receipt does not match requested handoff")
    table = process_snapshot()
    if table is None:
        raise SupervisorError("rebind requires observed prior exit and paused replacement")
    known = process_tree(table, previous["identities"])
    if any(i["pid"] in table and table[i["pid"]]["started"] == i["started"]
           and not table[i["pid"]]["state"].startswith("Z") for i in known):
        raise SupervisorError("prior Validator or tracked descendants remain live")
    new_tree = process_tree(table, [binding["identity"]])
    if not new_tree or any(i["pid"] not in table
                           or table[i["pid"]]["started"] != i["started"]
                           or "T" not in table[i["pid"]]["state"] for i in new_tree):
        raise SupervisorError("replacement Validator tree must already be paused")
    if maintenance_reason is None:
        try:
            orchestrator_channel.require_current(root)
        except orchestrator_channel.OrchestratorChannelError as error:
            raise SupervisorError("rebind supervision is not current") from error
        reports = orchestrator_channel._read_jsonl(root / "orchestrator/reports.jsonl")
        latest = reports[-1] if reports else {}
        if (not assessment_digest or latest.get("assessment_digest") != assessment_digest
                or latest.get("assessment", {}).get("decision") != "no-op"):
            raise SupervisorError("rebind requires the current accepted supervision disposition")
    if not retained:
        retained = {"subject": subject, "previous_binding": old_binding,
                    "previous_custody": previous, "observed_exited": known,
                    "assessment_digest": assessment_digest, "at": _iso(_now())}
        _save_once(receipt_path, retained)
    if current == binding:
        return {"receipt": str(receipt_path), "binding": binding, "replayed": True}
    state = load_state(root)
    reasons = state.setdefault("validator", [])
    if "stop:validator-rebind-validation" not in reasons:
        reasons.append("stop:validator-rebind-validation")
    save_state(root, state)
    # Receipt first, then pending custody, then binding. A crash at any point leaves the
    # replacement paused, and exact retry completes from the immutable prior-custody receipt.
    records["validator"] = {"identity": binding["identity"], "identities": new_tree,
                            "requested": "stop", "attempts": {}, "acknowledged": False,
                            "state": "pending", "requested_at": _iso(_now()),
                            "rebind_receipt": str(receipt_path)}
    _store(folder / "seat-processes.json", records)
    _store(folder / "validator-process.json", binding)
    return {"receipt": str(receipt_path), "binding": binding, "replayed": False}


def load_state(root: pathlib.Path) -> dict[str, list[str]]:
    "Why each seat is paused. A seat runs only when its list is empty."
    value = _read_control(_state_path(root))
    if any(not isinstance(v, list) or any(not isinstance(r, str) for r in v)
           for v in value.values()):
        raise SupervisorError("malformed seat hold state")
    return {k: list(v) for k, v in value.items()}


def save_state(root: pathlib.Path, state: Mapping[str, Sequence[str]]) -> None:
    _store(_state_path(root), {k: sorted(set(v)) for k, v in state.items() if v})


@_locked
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
    panes, fault = _targets(root, seats)
    if seat not in panes and not (seat == "validator" and fault):
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
    _identity_hold(state, panes, fault)
    if action == "stop":
        if f"stop:{rule}" not in reasons:
            reasons.append(f"stop:{rule}")
    elif action == "resume":
        state[seat] = [r for r in reasons if not r.startswith("stop:")]
        if not state[seat]:
            state[seat] = ["resume-pending"]
    if kickoff_validator(root):
        held = _validator_stopped(state)
        for name in panes:
            if name == "validator":
                continue
            rs = state.setdefault(name, [])
            if held and HELD not in rs:
                rs.append(HELD)
            elif not held and HELD in rs:
                rs.remove(HELD)
                if not rs:
                    rs.append("resume-pending")
    row["acknowledgements"] = _apply(root, seats, state, panes)
    if fault:
        row["identity_error"] = fault
    if action in ("correct", "stop"):
        # Typed into the seat's own terminal. A stopped seat holds it in its input until the
        # Orchestrator resumes it, so the correction is the first thing it reads.
        try:
            row["delivery"] = seats.deliver(
                seat, f"FACTORY_CORRECTION from the Orchestrator. Rule: {rule}. "
                f"Evidence: {evidence} Correction: {correction}")
        except (OSError, subprocess.SubprocessError, SupervisorError) as error:
            row["delivery_error"] = str(error)
    save_state(root, state)
    row["acknowledged"] = action != "correct" and not fault and "delivery_error" not in row and all(
        result["acknowledged"] for result in row["acknowledgements"].values())
    # A successful correct operation queues a notice; it cannot attest that an agent read
    # or applied it. Keep command acceptance separate from process-state acknowledgements.
    delivery = row.get("delivery")
    row["accepted"] = (
        not fault and "delivery_error" not in row and isinstance(delivery, dict)
        and delivery.get("state") in {"durable-queued", "terminal-submitted"}
    ) if action == "correct" else row["acknowledged"]
    _append(_folder(root) / "seat-control.jsonl", row)
    _event(root, f"seat-{action}",
           f"{seat}: {rule or action}; acknowledged={row['acknowledged']}")
    return row


@_locked
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
    panes, fault = _targets(root, seats)
    state = load_state(root)
    kickoff = kickoff_validator(root)
    was_down = any(DOWN in reasons for reasons in state.values())
    was_held = any(state.values())
    _identity_hold(state, panes, fault)
    held = kickoff and _validator_stopped(state)
    for seat in panes:
        reasons = state.setdefault(seat, [])
        if seat != "validator":
            if held and HELD not in reasons:
                reasons.append(HELD)
            elif not held and HELD in reasons:
                reasons.remove(HELD)
                if not reasons:
                    reasons.append("resume-pending")
        if lagging and DOWN not in reasons:
            reasons.append(DOWN)
        elif not lagging and DOWN in reasons:
            reasons.remove(DOWN)
            if not reasons:
                reasons.append("resume-pending")
    acknowledgements = _apply(root, seats, state, panes)
    if fault:
        _event(root, "validator-identity-unavailable", fault)
    if lagging and not was_down:
        age = int((now() - oldest).total_seconds() // 60) if oldest else 0
        detail = (
            f"Activity after cursor {through} has gone unassessed for {age} min. "
            f"Pause requested; unacknowledged seats: "
            f"{[name for name, value in acknowledgements.items() if not value['acknowledged']]}. "
            f"Check the Orchestrator and process receipts."
        )
        _event(root, "seats-paused-orchestrator-lagging", detail)
        if kickoff and "validator" in panes:
            try:
                seats.deliver("validator", f"FACTORY_NOTICE from the seat supervisor. {detail}")
            except (OSError, subprocess.SubprocessError, SupervisorError) as error:
                _event(root, "validator-notice-failed", str(error), wake=False)
        try:
            notify(
                root,
                level="critical",
                key=DOWN,
                summary="Factory pause requested: the Orchestrator is not judging",
                detail=detail,
            )
        except escalate.EscalationError as error:
            _event(root, "escalation-failed", f"critical {DOWN}: {error}")
    elif was_held and not lagging:
        resolved = not any(state.values()) and not fault
        _event(
            root,
            ("seats-resumed-orchestrator-current" if resolved
             else "seats-resume-pending-orchestrator-current"),
            f"assessed through cursor {through}; remaining holds: "
            f"{sorted(name for name, reasons in state.items() if reasons)}",
            wake=False,
        )
        try:
            if resolved:
                escalate.resolve(root, key=DOWN)
        except escalate.EscalationError:
            pass
    return {
        "through": through,
        "lagging": lagging,
        "seats": sorted(panes),
        "holds": {k: v for k, v in state.items() if v},
        "paused": {k: v for k, v in state.items() if v
                   and acknowledgements.get(k, {}).get("acknowledged")
                   and acknowledgements[k].get("state") == "stopped"},
        "acknowledgements": acknowledgements,
        "identity_error": fault,
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
    bind = sub.add_parser("bind-validator")
    bind.add_argument("--root", required=True, type=pathlib.Path)
    bind.add_argument("--session", required=True)
    bind.add_argument("--pid", required=True, type=int)
    bind.add_argument("--started", required=True)
    bind.add_argument("--pane", required=True)
    rebind = sub.add_parser("rebind-validator", help="host-only paused process handoff")
    rebind.add_argument("--root", required=True, type=pathlib.Path)
    rebind.add_argument("--session", required=True)
    rebind.add_argument("--pid", required=True, type=int)
    rebind.add_argument("--started", required=True)
    rebind.add_argument("--pane", required=True)
    rebind.add_argument("--previous-pid", required=True, type=int)
    rebind.add_argument("--previous-started", required=True)
    rebind_mode = rebind.add_mutually_exclusive_group(required=True)
    rebind_mode.add_argument("--assessment-digest")
    rebind_mode.add_argument("--maintenance-reason",
                             help="external host repair; retains every hold")
    check = sub.add_parser("check-validator")
    check.add_argument("--root", required=True, type=pathlib.Path)
    check.add_argument("--session", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "rebind-validator":
            print(json.dumps(rebind_validator(
                args.root, args.session, args.pid, args.started, args.pane,
                args.previous_pid, args.previous_started, args.assessment_digest,
                args.maintenance_reason), sort_keys=True))
            return 0
        if args.command == "bind-validator":
            print(json.dumps(bind_validator(args.root, args.session, args.pid, args.started,
                                            args.pane), sort_keys=True))
            return 0
        if args.command == "check-validator":
            if kickoff_validator(args.root):
                seats = Seats(args.session)
                seats.configure(args.root)
                seats.external(require_running=True)
            if load_state(args.root).get("validator"):
                raise SupervisorError("Validator has an unresolved supervision hold")
            return 0
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
            succeeded = row.get("accepted" if args.action == "correct" else "acknowledged")
            return 0 if succeeded is True else 75
        print(json.dumps({"holds": load_state(args.root),
                          "processes": _read_control(_folder(args.root) / "seat-processes.json")},
                         sort_keys=True))
        return 0
    except (SupervisorError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"seat-supervisor: {error}", file=sys.stderr)
        return 64


if __name__ == "__main__":
    raise SystemExit(main())
