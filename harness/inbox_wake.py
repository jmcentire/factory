"""Durable coordinator inbox delivery; transport only, never orchestration.

Native user-input acknowledgment means delivered, not acted upon. Pending writes
retain one batch ID and are never blindly reissued as new native input.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path

ROLES = ("validator", "orchestrator")


def folder(root: Path) -> Path:
    if root.is_symlink():
        raise ValueError("symlinked run")
    path = root / "inbox-wake"
    if path.is_symlink():
        raise ValueError("symlinked inbox transport")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def read(path: Path) -> dict:
    from persistent_lane import read as bounded_read

    return bounded_read(path)


def store(path: Path, value: dict) -> None:
    from persistent_lane import store as atomic_store

    atomic_store(path, value)


def batch_path(root: Path, recipient: str, identifier: str) -> Path:
    import re

    if recipient not in ROLES or not re.fullmatch(r"[a-f0-9]{64}", identifier):
        raise ValueError("invalid inbox batch binding")
    return folder(root) / "batches" / f"{recipient}-{identifier}.json"


def planned(root: Path, recipient: str, identifier: str) -> dict:
    value = read(batch_path(root, recipient, identifier))
    if value.get("recipient") != recipient or value.get("message_id") != identifier:
        raise ValueError("inbox batch binding changed")
    return value


def events(root: Path, recipient: str) -> list[dict]:
    """Read complete retained records, including completions before worker start."""
    import stat

    paths = [root / recipient / "inbox.jsonl", *sorted((root / "tmux-lanes").glob("*-exits.jsonl"))]
    result = []
    for path in paths:
        if any(p.is_symlink() for p in path.parents if p == root or root in p.parents):
            raise ValueError("symlinked event directory")
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            continue
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("event journal is not regular")
            offset = 0
            while raw := stream.readline(1024 * 1024 + 1):
                if len(raw) > 1024 * 1024:
                    raise ValueError("oversized inbox event")
                if not raw.endswith(b"\n"):
                    break
                row = json.loads(raw)
                if not isinstance(row, dict):
                    raise ValueError("malformed event")
                key = (
                    str(path.relative_to(root))
                    + ":"
                    + str(offset)
                    + ":"
                    + hashlib.sha256(raw).hexdigest()
                )
                identifier = hashlib.sha256(key.encode()).hexdigest()
                result.append(
                    {"id": identifier, "source": str(path.relative_to(root)), "record": row}
                )
                offset += len(raw)
    return result


def deliver(root: Path, recipient: str, identifier: str) -> dict:
    """Native adapter checks retained PID/start-time, session and available boundary."""
    import persistent_lane

    path = root / f"{recipient}-persistent.json"
    if not path.exists():
        return {
            "state": "unsupported",
            "detail": "no native coordinator stream; no terminal injection",
        }
    try:
        current = persistent_lane.inspect(root, recipient)
        if not current.get("session_id"):
            return {"state": "busy"}
        return persistent_lane.request(root, recipient, identifier, current["session_id"])
    except (OSError, ValueError, KeyError) as error:
        return {
            "state": "dead" if "exited" in str(error) or "stale" in str(error) else "blocked",
            "detail": str(error),
        }


def status(root: Path) -> dict:
    path = folder(root) / "state.json"
    return read(path) if path.exists() else {"status": "not-started", "recipients": {}}


def stop(root: Path) -> None:
    store(folder(root) / "stop.json", {"stopped": True})


def tick(root: Path, transport=None) -> dict:
    base = folder(root)
    fd = os.open(base / "owner.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "owned"}
        return cycle(root, transport or deliver)
    finally:
        os.close(fd)


def cycle(root: Path, transport) -> dict:
    base = folder(root)
    state = status(root)
    if (base / "stop.json").exists():
        state["status"] = "stopped"
        store(base / "state.json", state)
        return state
    batches = base / "batches"
    if batches.is_symlink():
        raise ValueError("symlinked batch directory")
    batches.mkdir(mode=0o700, exist_ok=True)
    recipients = state.setdefault("recipients", {})
    for recipient in ROLES:
        seat = recipients.setdefault(recipient, {"consumed": [], "pending": None})
        if not seat["pending"]:
            known = set(seat["consumed"])
            pending = []
            size = 0
            for event in events(root, recipient):
                if event["id"] in known:
                    continue
                encoded = len(json.dumps(event, ensure_ascii=False).encode())
                if pending and size + encoded > 2 * 1024 * 1024:
                    break
                pending.append(event)
                size += encoded
                if len(pending) == 32:
                    break
            if not pending:
                seat["delivery"] = "idle"
                continue
            ids = [event["id"] for event in pending]
            identifier = hashlib.sha256(json.dumps([recipient, ids]).encode()).hexdigest()
            text = (
                "Factory retained inbox events. Handle each once; "
                "this receipt is delivery, not task acceptance. "
                "Read retained completion outputs before waiting for future changes.\n"
                + "\n".join(json.dumps(event, ensure_ascii=False) for event in pending)
            )
            batch = {
                "message_id": identifier,
                "recipient": recipient,
                "text": text,
                "event_ids": ids,
                "sender": "host",
                "message_kind": "inbox",
            }
            store(batch_path(root, recipient, identifier), batch)
            seat["pending"] = identifier
            store(base / "state.json", state)  # before any possible native write
        result = transport(root, recipient, seat["pending"])
        seat["delivery"] = result.get("state", "blocked")
        seat["detail"] = result.get("detail", "")
        if seat["delivery"] == "acknowledged":
            batch = planned(root, recipient, seat["pending"])
            seat["consumed"] += batch["event_ids"]
            seat["pending"] = None
        store(base / "state.json", state)
    state["status"] = "running"
    store(base / "state.json", state)
    return state


def run(root: Path) -> None:
    # A separate lifetime lock prevents two pollers, including across restart races.
    base = folder(root)
    fd = os.open(base / "daemon.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            state = tick(root)
            if state.get("status") == "stopped":
                return
            import persistent_lane

            native = [role for role in ROLES if (root / f"{role}-persistent.json").exists()]
            dead = 0
            for role in native:
                try:
                    persistent_lane.inspect(root, role)
                except (OSError, ValueError, KeyError):
                    dead += 1
            if native and dead == len(native):
                state["status"] = "recipients-unavailable"
                store(base / "state.json", state)
                return
            # No respawn: the existing tmux run owns this worker's lifetime.
            import subprocess

            config = read(root / "harness.json")
            alive = subprocess.run(
                ["tmux", "-L", config["tmux_socket"], "has-session", "-t", "=" + config["run_id"]],
                capture_output=True,
            )
            if alive.returncode:
                return
            time.sleep(2)
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["run", "once", "stop", "status"])
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "run":
        run(args.root)
    elif args.action == "stop":
        stop(args.root)
    else:
        print(json.dumps(tick(args.root) if args.action == "once" else status(args.root)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
