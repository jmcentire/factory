"""Separate probe history from productive seats; retirement never grants qualification."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def read(path: Path, *, lines: bool = False) -> Any:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f"non-regular admission evidence: {path.name}")
        raw = stream.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("admission evidence exceeds its read ceiling")
    if lines:
        return [json.loads(line) for line in raw.splitlines() if line.strip()]
    return json.loads(raw)


def launches(root: Path, run: str, role: str, lane: str = "") -> list[dict[str, Any]]:
    lane = lane or role
    if not re.fullmatch(re.escape(role) + r"(?:-[a-z0-9][a-z0-9-]{0,15})?", lane):
        raise ValueError("invalid lane identity")
    result = []
    for journal in sorted((root / "tmux-lanes").glob(f"{lane}*-launch.jsonl")):
        slot = journal.name.removesuffix("-launch.jsonl")
        if slot != lane and not slot.startswith(lane + "."):
            continue
        rows = read(journal, lines=True)
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError("malformed prior launch history")
        active = [row for row in rows if row.get("status") == "active"]
        if not active:
            continue
        if len(active) != 1:
            raise ValueError("ambiguous prior launch history")
        row = active[0]
        if row.get("role") != role or row.get("run_id") != run:
            raise ValueError("prior launch belongs to another role or run")
        if row.get("mode") == "qualify" and (
            not slot.startswith(lane + ".") or row.get("slot") != slot
        ):
            raise ValueError("qualification slot is missing or mismatched")
        planned = [item for item in rows if item.get("status") == "planned"]
        if len(planned) > 1 or any(
            any(item.get(key) != row.get(key)
                for key in ("run_id", "role", "slot", "agent", "repository"))
            for item in planned
        ):
            raise ValueError("planned launch does not match the active generation")
        row = dict(row)
        if len(planned) == 1:
            row["_planned_ts"] = planned[0]["ts"]
        result.append(row)
    return result


def generation_digest(root: Path, slot: str) -> str:
    rows = read(root / "tmux-lanes" / f"{slot}-launch.jsonl", lines=True)
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("malformed launch generation")
    # Freezing is observation. A reopen changes the process generation and invalidates retirement.
    rows = [row for row in rows if row.get("schema_version") != "factory-tmux-lane-freeze/1"]
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def retired(root: Path, slot: str) -> bool:
    if not re.fullmatch(r"(?:coder|tester)(?:-[a-z0-9][a-z0-9-]{0,15})?"
                        r"(?:\.[A-Za-z0-9][A-Za-z0-9_-]{0,39})?", slot):
        raise ValueError("invalid slot identity")
    path = root / "tmux-lanes" / f"{slot}-retired.json"
    if not path.exists() and not path.is_symlink():
        return False
    row = read(path)
    if (not isinstance(row, dict) or row.get("schema_version") != "factory-probe-retirement/1"
            or row.get("slot") != slot or row.get("qualification") != "unverified"
            or not isinstance(row.get("reason"), str) or not row["reason"].strip()
            or row.get("generation_digest") != generation_digest(root, slot)):
        raise ValueError("probe retirement does not match retained generation")
    return True


def check(root: Path, run: str, role: str, action: str, repository: Path | None,
          slot: str = "", lane: str = "") -> None:
    lane = lane or role
    if action == "message":
        if retired(root, slot):
            raise ValueError("retired unverified probe cannot receive new work")
        addressed = read(root / "tmux-lanes" / f"{slot}-launch.jsonl", lines=True)
        if any(not isinstance(row, dict) for row in addressed):
            raise ValueError("malformed addressed launch history")
        active = [row for row in addressed if row.get("status") == "active"]
        if len(active) != 1:
            raise ValueError("addressed launch is missing or ambiguous")
        if active[0].get("mode") != "qualify":
            return  # A broken unrelated probe must not disable a productive conversation.
    rows = launches(root, run, role, lane)
    productive = any(row.get("mode") != "qualify" for row in rows)
    if action == "message":
        if productive:
            raise ValueError("qualification session cannot address an admitted productive lane")
        return
    if action == "qualify" and productive:
        raise ValueError("qualification cannot replace an admitted productive lane")
    probes = [row for row in rows if row.get("mode") == "qualify"]
    if repository is None:
        raise ValueError("admission requires its repository")
    mine = repository.resolve()
    for probe in probes:
        name = probe["slot"]
        if not retired(root, name):
            state = read(root / "tmux-lanes" / f"{name}-watch.json")
            starts = {datetime.datetime.fromisoformat(stamp).timestamp()
                      for stamp in (probe["ts"], probe.get("_planned_ts", probe["ts"]))}
            terminal = {"done", "exited", "stalled", "capped", "superseded"}
            if (not isinstance(state, dict) or state.get("slot") != name
                    or state.get("schema_version") != "factory-lane-watch/1"
                    or state.get("launch_ts") not in starts or state.get("status") not in terminal):
                raise ValueError(f"qualification {name} has unfinished or unverified observation")
            output = read(root / "tmux-lanes" / "lane-output.jsonl", lines=True)
            if not any(isinstance(row, dict) and row.get("slot") == name
                       and row.get("schema_version") == "factory-lane-output/1"
                       and row.get("role") == role and row.get("outcome") == state["status"]
                       for row in output):
                raise ValueError(f"qualification {name} has no matching terminal output")
        prior = probe.get("repository")
        if not isinstance(prior, str) or not prior:
            raise ValueError(f"qualification {name} has no repository binding")
        theirs = Path(prior).resolve()
        if action == "launch" and os.path.commonpath((mine, theirs)) in {str(mine), str(theirs)}:
            raise ValueError("productive admission requires a fresh clone "
                             "outside probe repositories")


def retire(root: Path, run: str, role: str, lane: str, slot: str, reason: str) -> None:
    """Host disposition of unusable probe evidence, never a successful stop or qualification."""
    if not reason.strip() or len(reason) > 4096:
        raise ValueError("retirement requires a bounded nonempty reason")
    rows = launches(root, run, role, lane)
    selected = [row for row in rows if row.get("slot") == slot and row.get("mode") == "qualify"]
    if len(selected) != 1 or any(row.get("mode") != "qualify" for row in rows):
        raise ValueError("retirement requires one probe and no productive seat in its window")
    if retired(root, slot):
        return
    subprocess.run([sys.executable, str(Path(__file__).with_name("seat_supervisor.py")),
                    "check-validator", "--root", str(root), "--session", run], check=True)
    # A successful exact window listing proves absence. Transport/permission failures are unknown.
    result = subprocess.run(["tmux", "list-windows", "-t", "=" + run,
                             "-F", "#{window_name} #{window_id}"], capture_output=True,
                            text=True, timeout=10)
    if result.returncode:
        raise ValueError("cannot observe exact run windows for retirement")
    targets = [line.split()[1] for line in result.stdout.splitlines()
               if len(line.split()) == 2 and line.split()[0] == lane]
    if len(targets) > 1:
        raise ValueError("ambiguous probe window")
    if targets:
        observed = subprocess.run(["tmux", "display-message", "-p", "-t", targets[0],
                                   "#{pane_dead}"], capture_output=True, text=True, timeout=10)
        if observed.returncode or observed.stdout.strip() != "1":
            raise ValueError("cannot retire a live or unknown probe window")
    module = (importlib.import_module("harness.lane_watchdog") if __package__
              else importlib.import_module("lane_watchdog"))
    table = module.process_snapshot()
    if table is None:
        raise ValueError("process observation unavailable")
    identities: list[dict[str, Any]] = []
    for suffix in ("watch", "process"):
        path = root / "tmux-lanes" / f"{slot}-{suffix}.json"
        if not path.exists() and not path.is_symlink():
            continue
        value = read(path)
        if not isinstance(value, dict):
            raise ValueError("malformed retained process evidence")
        identities += value.get("process_identities", []) if suffix == "watch" else [
            value.get("wrapper", {}), value.get("worker", {})]
    for identity in identities:
        if (not isinstance(identity, dict) or type(identity.get("pid")) is not int
                or not isinstance(identity.get("started"), str)):
            raise ValueError("malformed retained process identity")
        current = table.get(identity["pid"])
        if (current and current["started"] == identity["started"]
                and not current["state"].startswith("Z")):
            raise ValueError("recorded probe process is still alive")
    stamp = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    row = {"schema_version": "factory-probe-retirement/1", "run_id": run,
           "role": role, "slot": slot, "reason": reason, "qualification": "unverified",
           "generation_digest": generation_digest(root, slot), "ts": stamp,
           "window": targets[0] if targets else "missing", "known_processes_absent": identities,
           "limitation": "unobserved detached descendants are not certified absent"}
    event = {"ts": stamp, "kind": "qualification-retired", "class": "lane",
             "source": "tmux_lane.sh", "wake": True,
             "detail": f"{slot}: unverified probe retired without qualification credit: {reason}"}
    attention = (importlib.import_module("harness.attention_gate") if __package__
                 else importlib.import_module("attention_gate"))
    attention._append_jsonl(root / "events.jsonl", event)
    attention._append_jsonl(root / "tmux-lanes" / "wake.jsonl", {
        "ts": stamp, "kind": event["kind"], "slot": slot, "detail": event["detail"],
    })
    directory = root / "tmux-lanes"
    fd, temporary = tempfile.mkstemp(prefix=".retired-", dir=directory)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(row, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, directory / f"{slot}-retired.json")
    finally:
        os.unlink(temporary)
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--role", choices=("coder", "tester"), required=True)
    parser.add_argument("--action", choices=("launch", "qualify", "message", "retire", "baseline"),
                        required=True)
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--slot", default="")
    parser.add_argument("--lane", default="")
    parser.add_argument("--reason", default="")
    args = parser.parse_args()
    try:
        if args.action == "baseline":
            path = args.root / "dialogue" / "journal.jsonl"
            rows = read(path, lines=True) if path.exists() or path.is_symlink() else []
            if any(not isinstance(row, dict) or row.get("sequence") != index
                   for index, row in enumerate(rows, 1)):
                raise ValueError("malformed dialogue baseline")
            print(len(rows))
        elif args.action == "retire":
            retire(args.root, args.run, args.role, args.lane or args.role, args.slot, args.reason)
        else:
            check(args.root, args.run, args.role, args.action, args.repository,
                  args.slot, args.lane)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"lane-admission: {error}")
        return 70
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
