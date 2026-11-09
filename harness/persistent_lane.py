"""Host-owned, acknowledged native stream input for resident Claude and Agy lanes.

No terminal input is used. A request can address only a retained typed dialogue message,
and a successful receipt requires the native client's user-input acknowledgement.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import secrets
import select
import selectors
import socket
import stat
import subprocess
import tempfile
import time
import uuid

import agents

SCHEMA = "factory-persistent-lane/1"
LIMIT = 4 * 1024 * 1024


def read(path: pathlib.Path) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
            raise ValueError("persistent control record is not a bounded regular file")
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("persistent control record is not an object")
    return value


def store(path: pathlib.Path, value: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".persistent-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def state_path(root: pathlib.Path, slot: str) -> pathlib.Path:
    if slot in {"validator", "orchestrator"}:
        if root.is_symlink():
            raise ValueError("symlinked coordinator root")
        return root / f"{slot}-persistent.json"
    if not re.fullmatch(r"(coder|tester)(-[a-z0-9][a-z0-9-]{0,15})?(\.[A-Za-z0-9_-]{1,40})?", slot):
        raise ValueError("invalid persistent slot")
    if root.is_symlink() or (root / "tmux-lanes").is_symlink():
        raise ValueError("symlinked persistent control directory")
    return root / "tmux-lanes" / f"{slot}-persistent.json"


def inspect(root: pathlib.Path, slot: str) -> dict:
    from lane_watchdog import process_snapshot

    value = read(state_path(root, slot))
    if (
        value.get("schema") != SCHEMA
        or value.get("root") != str(root.resolve())
        or value.get("slot") != slot
    ):
        raise ValueError("persistent lane binding mismatch")
    receipt = read(state_path(root, slot).parent / f"{slot}-process.json")
    table = process_snapshot()
    if (
        table is None
        or receipt.get("status") != "running"
        or receipt.get("schema") != "factory-worker-process/1"
        or receipt.get("slot") != slot
    ):
        raise ValueError("persistent process identity unavailable")
    for key in ("worker", "wrapper"):
        identity = value.get(key)
        if not isinstance(identity, dict) or identity != receipt.get(key):
            raise ValueError("persistent process receipt changed")
        if type(identity.get("pid")) is not int:
            raise ValueError("persistent process PID malformed")
        actual = table.get(identity["pid"], {})
        if actual.get("started") != identity.get("started") or actual.get("state", "Z").startswith(
            "Z"
        ):
            raise ValueError("persistent process exited or identity is stale")
    if table[value["worker"]["pid"]]["ppid"] != value["wrapper"]["pid"]:
        raise ValueError("persistent worker is no longer owned by its wrapper")
    value["process_state"] = (
        "stopped" if "T" in table[value["worker"]["pid"]]["state"] else "running"
    )
    return value


def planned(root: pathlib.Path, slot: str, identifier: str) -> dict:
    if slot in {"validator", "orchestrator"}:
        import inbox_wake
        return inbox_wake.planned(root, slot, identifier)
    import lane_dialogue

    with lane_dialogue._locked(root) as directory:
        rows = lane_dialogue._read(directory / "journal.jsonl")
        lane_dialogue._validate(rows)
    matches = [
        row
        for row in rows
        if row.get("record_type") == "message-planned" and row.get("message_id") == identifier
    ]
    if len(matches) != 1 or matches[0]["lane"] != slot.split(".")[0]:
        raise ValueError("message does not bind this lane")
    row = matches[0]
    if row["text_sha256"] != "sha256:" + hashlib.sha256(row["text"].encode()).hexdigest():
        raise ValueError("message digest mismatch")
    return row


def unheld(root: pathlib.Path, slot: str) -> None:
    # Process/session validation belongs to the transport, not review-state files.
    # Individual delivery paths report a stopped process as busy without submitting input.
    inspect(root, slot)


def verify_interruption(value: dict, watch: dict | None = None) -> None:
    """A continuation needs the native cancellation receipt and terminal result.

    No supervisor or review marker can provide a missing native acknowledgment.
    """
    if not value.get("review_boundary_pending"):
        return
    receipt = value.get("interruption", {})
    outcome = value.get("last_turn_outcome")
    if (
        value.get("phase") != "idle"
        or outcome not in {"interrupted", "completed"}
        or receipt.get("state") != outcome
        or receipt.get("native_message_id") != value.get("current_input_id")
    ):
        raise ValueError("native interruption boundary remains unverified")


def interrupt(root: pathlib.Path, slot: str) -> dict:
    current = inspect(root, slot)
    return request(
        root,
        slot,
        "",
        current["session_id"],
        action="interrupt",
        input_id=current.get("current_input_id"),
    )


def request(
    root: pathlib.Path,
    slot: str,
    identifier: str,
    thread: str,
    *,
    defer=False,
    action="deliver",
    input_id=None,
) -> dict:
    current = inspect(root, slot)
    if current.get("session_id") != thread:
        raise ValueError("persistent conversation mismatch")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect(current["socket"])
        client.sendall(
            json.dumps(
                {
                    "token": current["token"],
                    "message_id": identifier,
                    "thread": thread,
                    "defer": defer,
                    "action": action,
                    "input_id": input_id,
                }
            ).encode()
            + b"\n"
        )
        raw = bytearray()
        while not raw.endswith(b"\n"):
            chunk = client.recv(4096)
            if not chunk or len(raw) + len(chunk) > LIMIT:
                raise ValueError("incomplete persistent response")
            raw.extend(chunk)
    result = json.loads(raw)
    if result.get("error"):
        raise ValueError(result["error"])
    return result


def run(
    child: subprocess.Popen,
    *,
    root: pathlib.Path,
    slot: str,
    agent: str,
    prompt: str,
    session_file: pathlib.Path,
    emit,
) -> int:
    """Own one native process until its actual exit; idle is a live input boundary."""
    path = state_path(root, slot)
    receipt = read(path.parent / f"{slot}-process.json")
    if (
        receipt.get("schema") != "factory-worker-process/1"
        or receipt.get("slot") != slot
        or receipt.get("status") != "running"
        or not isinstance(receipt.get("worker"), dict)
        or not isinstance(receipt.get("wrapper"), dict)
        or receipt.get("worker", {}).get("pid") != child.pid
        or receipt.get("wrapper", {}).get("pid") != os.getpid()
    ):
        raise ValueError("persistent run root does not own this native child and wrapper")
    assert child.stdin is not None and child.stdout is not None

    thread = session_file.read_text().strip() if session_file.exists() else ""
    state = {
        "schema": SCHEMA,
        "root": str(root.resolve()),
        "slot": slot,
        "agent": agent,
        "worker": receipt["worker"],
        "wrapper": receipt["wrapper"],
        "session_id": thread,
        "phase": "working",
        "turns": 0,
        "completed_work_s": 0.0,
        "turn_started_at": time.time(),
        "turn_completed_at": None,
        "current_message_id": "initial",
        "deliveries": {},
    }
    native_id = str(uuid.uuid4())
    state.update(current_input_id=native_id, capabilities=[], review_boundary_pending=False)
    pending = "initial"
    accepted = False
    pending_text = prompt
    last_user_step = -1
    buffer = b""
    deferred = None

    def save():
        store(path, state)

    def bind(value):
        if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9-]{36}", value):
            raise ValueError("native conversation id missing or malformed")
        if state["session_id"] and state["session_id"] != value:
            raise ValueError("native conversation changed")
        state["session_id"] = value
        if session_file.is_symlink():
            raise ValueError("symlinked session file")
        if session_file.exists() and session_file.read_text().strip() != value:
            raise ValueError("retained conversation changed")
        if not session_file.exists():
            fd = os.open(session_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(value + "\n")

    def send(text):
        write_frame(agents.persistent_input(agent, text, native_id))

    def write_frame(frame):
        payload = json.dumps(frame).encode() + b"\n"
        descriptor = child.stdin.fileno()
        os.set_blocking(descriptor, False)
        deadline = time.monotonic() + 2
        try:
            while payload:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([], [descriptor], [], remaining)[1]:
                    raise ValueError("native input write outcome incomplete; retained pending")
                try:
                    payload = payload[os.write(descriptor, payload) :]
                except BlockingIOError:
                    continue
        finally:
            os.set_blocking(descriptor, True)

    def interrupt_turn(root, input_id):
        if input_id != native_id:
            raise ValueError("interrupt targets a different native input")
        previous = state.get("interruption", {})
        if previous.get("native_message_id") == native_id:
            return previous  # Unknown write outcomes are never replayed.
        unheld(root, slot)
        required = {"interrupt_receipt_v1", "interrupt_cancel_queued_v1"}
        if (
            agent != "claude"
            or not required.issubset(state["capabilities"])
            or state["phase"] != "working"
            or not accepted
            or inspect(root, slot)["process_state"] != "running"
        ):
            raise ValueError("native lane lacks an acknowledged interruptible working turn")
        receipt = {
            "state": "requested",
            "request_id": str(uuid.uuid4()),
            "native_message_id": native_id,
            "requested_at": time.time(),
        }
        state.update(interruption=receipt, review_boundary_pending=True)
        save()
        write_frame(
            {
                "type": "control_request",
                "request_id": receipt["request_id"],
                "request": {"subtype": "interrupt", "cancel_queued": True},
            }
        )
        return receipt

    def start_message(root, row):
        nonlocal pending, native_id, accepted, pending_text
        unheld(root, slot)
        if inspect(root, slot)["process_state"] != "running" or state["phase"] != "idle":
            raise ValueError("native lane is not at an available input boundary")
        verify_interruption(state)
        pending = row["message_id"]
        native_id = str(uuid.uuid5(uuid.NAMESPACE_URL, state["session_id"] + pending))
        accepted = False
        pending_text = row["text"]
        result = {"state": "pending", "message_id": pending}
        state["deliveries"][pending] = result
        journal = path.parent / f"{slot}-launch.jsonl"
        # Invalidate any frozen export before new input can mutate the author tree.
        if journal.exists():
            from lane_dialogue import _append, _read

            launches = [row for row in _read(journal) if row.get("status") == "active"]
            if len(launches) != 1:
                raise ValueError("persistent launch is missing or ambiguous")
            _append(
                journal,
                {
                    "schema_version": "factory-tmux-lane-reopen/1",
                    "ts": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
                    "run_id": launches[0]["run_id"],
                    "role": slot.split("-")[0].split(".")[0],
                    "slot": slot,
                    "sender": row["sender"],
                    "message_kind": row["message_kind"],
                    "message_id": pending,
                    "prompt_bytes": len(row["text"].encode()),
                    "prompt_limit": None,
                    "prompt_delivery": "stream-json",
                },
            )
        state.update(
            phase="working",
            current_message_id=pending,
            current_input_id=native_id,
            review_boundary_pending=False,
            turn_started_at=time.time(),
            turn_completed_at=None,
        )
        save()  # Unknown write outcomes remain pending and must never be replayed blindly.
        send(row["text"])
        return result

    def initial(root):
        unheld(root, slot)
        if inspect(root, slot)["process_state"] != "running":
            raise ValueError("native lane was stopped before initial input")
        send(prompt)

    def event(raw):
        nonlocal accepted, last_user_step
        try:
            row = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            emit(raw)
            return
        if not isinstance(row, dict):
            emit(raw)
            return
        ack = done = failed = False
        if agent == "claude":
            if row.get("type") == "system" and row.get("subtype") == "init":
                capabilities = row.get("capabilities", [])
                if isinstance(capabilities, list) and all(isinstance(c, str) for c in capabilities):
                    state["capabilities"] = capabilities
            interruption = state.get("interruption", {})
            response = row.get("response", {})
            if (
                row.get("type") == "control_response"
                and isinstance(response, dict)
                and response.get("request_id") == interruption.get("request_id")
                and interruption.get("native_message_id") == native_id
                and interruption.get("state") == "requested"
            ):
                detail = response.get("response", {})
                good = (
                    response.get("subtype") == "success"
                    and isinstance(detail, dict)
                    and detail.get("still_queued") == []
                    and isinstance(detail.get("cancelled"), list)
                    and all(isinstance(i, str) for i in detail["cancelled"])
                )
                interruption["state"] = "acknowledged" if good else "failed"
                if (
                    good
                    and state["phase"] == "idle"
                    and state.get("last_turn_outcome") == "completed"
                ):
                    interruption["state"] = "completed"
            if row.get("type") in {"system", "user", "assistant", "result"} and row.get(
                "session_id"
            ):
                bind(row["session_id"])
            ack = (
                row.get("type") == "user"
                and row.get("uuid") == native_id
                and row.get("parent_tool_use_id") is None
                and row.get("message") == {"role": "user", "content": pending_text}
            )
            done = row.get("type") == "result"
            failed = bool(row.get("is_error"))
        else:
            kind = row.get("event")
            payload = row if kind == "init" else row.get(kind, {}) if isinstance(kind, str) else {}
            if kind in {"init", "step_update", "result"} and isinstance(payload, dict):
                if payload.get("conversation_id"):
                    bind(payload["conversation_id"])
                ack = (
                    kind == "step_update"
                    and payload.get("step_type") == "user_input"
                    and payload.get("state") == "DONE"
                    and type(payload.get("step_index")) is int
                    and payload["step_index"] > last_user_step
                )
                if ack:
                    last_user_step = payload["step_index"]
                done = kind == "result"
                failed = payload.get("status") == "ERROR"
        # Session checks precede the legacy output observer, which otherwise writes first id.
        emit(raw)
        if ack and pending and not accepted and state["session_id"]:
            accepted = True
            state["deliveries"][pending] = {
                "state": "acknowledged",
                "message_id": pending,
                "thread_id": state["session_id"],
                "at": time.time(),
            }
            if pending != "initial" and slot not in {"validator", "orchestrator"}:
                import lane_dialogue

                lane_dialogue.record_delivery(
                    root, message_id=pending, thread_id=state["session_id"], transport="persistent"
                )
        if done and state["session_id"] and state["phase"] == "idle":
            # A turn the agent started itself (Claude Code wakes when a background task ends)
            # finished while the lane was idle; it answers no delivered message, so stay idle.
            done = False
        if done:
            if not state["session_id"] or not pending or state["phase"] != "working":
                raise ValueError("unbound native turn result")
            interruption = state.get("interruption", {})
            if (
                interruption.get("native_message_id") == native_id
                and row.get("user_message_uuid") != native_id
            ):
                raise ValueError("interrupt terminal result does not bind the current native input")
            interrupted = (
                agent == "claude"
                and failed
                and row.get("terminal_reason") == "aborted_tools"
                and row.get("user_message_uuid") == native_id
                and interruption.get("native_message_id") == native_id
                and interruption.get("state") == "acknowledged"
            )
            outcome = "interrupted" if interrupted else "failed" if failed else "completed"
            if interruption.get("native_message_id") == native_id and (
                outcome != "completed" or interruption.get("state") == "acknowledged"
            ):
                interruption["state"] = outcome
            state["last_turn_outcome"] = outcome
            state["turns"] += 1
            state["phase"] = "failed" if outcome == "failed" else "idle"
            state["turn_completed_at"] = time.time()
            state["completed_work_s"] += max(
                0.0, state["turn_completed_at"] - state["turn_started_at"]
            )
        save()

    with tempfile.TemporaryDirectory(prefix="factory-live-", dir="/tmp") as directory:
        with (
            socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server,
            selectors.DefaultSelector() as poll,
        ):
            server.bind(directory + "/lane.sock")
            server.listen(8)
            state.update(socket=directory + "/lane.sock", token=secrets.token_hex(32))
            save()
            poll.register(server, selectors.EVENT_READ)
            poll.register(child.stdout, selectors.EVENT_READ)
            initial(root)
            try:
                while child.poll() is None:
                    if deferred is not None and state["phase"] == "idle":
                        try:
                            unheld(root, slot)
                            if inspect(root, slot)["process_state"] == "running":
                                start_message(root, deferred)
                                deferred = None
                        except (ValueError, RuntimeError):
                            pass  # A later governance hold retains pending work without delivery.
                    for key, _ in poll.select(0.2):
                        if key.fileobj is child.stdout:
                            chunk = os.read(child.stdout.fileno(), 65536)
                            if not chunk:
                                poll.unregister(child.stdout)
                                continue
                            buffer += chunk
                            if len(buffer) > LIMIT:
                                raise ValueError("native stream frame exceeds ceiling")
                            while b"\n" in buffer:
                                line, buffer = buffer.split(b"\n", 1)
                                event(line + b"\n")
                        else:
                            with server.accept()[0] as connection:
                                connection.settimeout(1)
                                try:
                                    incoming = bytearray()
                                    while not incoming.endswith(b"\n"):
                                        part = connection.recv(4096)
                                        if not part or len(incoming) + len(part) > 8192:
                                            raise ValueError("invalid native input request")
                                        incoming.extend(part)
                                    command = json.loads(incoming)
                                    if not isinstance(command, dict):
                                        raise ValueError("persistent request is not an object")
                                    if (
                                        command.get("token") != state["token"]
                                        or command.get("thread") != state["session_id"]
                                    ):
                                        raise ValueError("persistent request binding mismatch")
                                    action = command.get("action", "deliver")
                                    if action == "interrupt":
                                        result = interrupt_turn(root, command.get("input_id"))
                                        connection.sendall(json.dumps(result).encode() + b"\n")
                                        continue
                                    if action != "deliver":
                                        raise ValueError("unknown persistent action")
                                    identifier = command.get("message_id")
                                    if not isinstance(identifier, str):
                                        raise ValueError("persistent message id is not a string")
                                    row = planned(root, slot, identifier)
                                    result = state["deliveries"].get(identifier)
                                    if result is None:
                                        current = inspect(root, slot)
                                        unheld(root, slot)
                                        may_defer = (
                                            command.get("defer") is True
                                            and row["message_kind"] == "progress-review"
                                            and row["text"].startswith(
                                                "FACTORY_REVIEW verdict=ON_TRACK\n"
                                            )
                                            and state["phase"] == "working"
                                            and deferred is None
                                            and current["process_state"] == "running"
                                        )
                                        if may_defer:
                                            deferred = row
                                            result = {
                                                "state": "pending",
                                                "message_id": identifier,
                                                "boundary": "next-native-turn",
                                            }
                                            state["deliveries"][identifier] = result
                                            save()
                                        elif (
                                            current["process_state"] != "running"
                                            or state["phase"] != "idle"
                                        ):
                                            result = {"state": "busy", "message_id": identifier}
                                        else:
                                            result = start_message(root, row)
                                    connection.sendall(json.dumps(result).encode() + b"\n")
                                except (OSError, ValueError, KeyError, RuntimeError) as error:
                                    # Cancellation or a client timeout can close this connection
                                    # after stdin was written. Preserve that pending outcome and
                                    # the resident native child; never retry input or make a lost
                                    # error response fatal to the driver.
                                    try:
                                        connection.sendall(
                                            json.dumps({"error": str(error)}).encode() + b"\n"
                                        )
                                    except OSError:
                                        pass
                rest = child.stdout.read()
                for line in (buffer + rest).splitlines(keepends=True):
                    event(line)
                return child.wait()
            finally:
                state["phase"] = "failed"
                save()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "deliver", "interrupt"))
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--slot", required=True)
    parser.add_argument("--message-id")
    parser.add_argument("--thread")
    parser.add_argument("--defer", action="store_true")
    args = parser.parse_args()
    try:
        result = (
            inspect(args.root, args.slot)
            if args.action == "status"
            else interrupt(args.root, args.slot)
            if args.action == "interrupt"
            else request(args.root, args.slot, args.message_id, args.thread, defer=args.defer)
        )
        # Socket authentication remains private; status exposes process evidence only.
        print(json.dumps({k: v for k, v in result.items() if k not in {"token", "socket"}}))
        return 0 if args.action == "status" or result.get("state") == "acknowledged" else 75
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"persistent-lane: {error}", file=__import__("sys").stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
