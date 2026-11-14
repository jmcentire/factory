"""Host-owned, acknowledged native input for resident Claude and Agy lanes.

A request can address only a retained typed dialogue message, and a successful receipt
requires the native client's own user-input acknowledgement.

Claude Code is a native terminal session (``run_terminal``, ``native_session.py``): the
message is typed into the terminal of the exact process this wrapper started. Its states say
only what was observed (``OUTSTANDING``): ``queued``, ``submitted``, and ``observed`` when the
session's own prompt hook reports it. That is receipt and the end of what can be known: the
operator's hooks run beside the observation hooks and may reject a prompt or continue past
a stop, and a later stop report cannot be tied to a message, so nothing here is called
acceptance or completion. Nothing is inferred from the screen, from silence or from elapsed
time. Agy still uses its stream input (``run``).
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

SCHEMA = "factory-persistent-lane/2"
#: Records a still-running wrapper of an earlier version wrote remain readable.
SCHEMAS = (SCHEMA, "factory-persistent-lane/1")
LIMIT = 4 * 1024 * 1024
#: The transport of a native terminal session, as status and launch records name it.
TERMINAL = "native-terminal"
#: The dialogue journal's transport word for a message such a session reported receiving.
OBSERVED_TRANSPORT = "terminal-observed"


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
        value.get("schema") not in SCHEMAS
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
    if current.get("transport") == TERMINAL:
        # Escape stops a terminal turn, and the program reports nothing back for it: no
        # cancellation receipt and no turn boundary. Typing it would leave this lane unable
        # to tell a stopped turn from a running one, so it is refused, not guessed.
        raise ValueError(
            "a native terminal session has no acknowledged interrupt; wait for its turn to "
            "end, or stop the lane process")
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

    if agent == "claude":
        raise ValueError(
            "Claude Code has no stream transport; it runs as a native terminal session")

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


#: Delivery states of a native terminal session, in order. Each names only what was observed.
#:   queued     accepted and retained; not one byte typed.
#:   submitted  typing began (or finished); nothing from the session confirms it. If the
#:              session is working, it is in the program's own input queue.
#:   observed   the session's own prompt hook reported a prompt carrying this message's
#:              mark: the message reached the session. This is the strongest evidence there
#:              is. It is receipt, not acceptance: hooks run in parallel and another may
#:              still reject the prompt. And nothing after it is tied to this message: a
#:              later stop report may belong to an earlier turn the session was continuing
#:              when it took this prompt from its queue (observed on Claude Code 2.1.323).
#:   unknown    typing began under an earlier wrapper process and no report was seen; what
#:              became of it cannot be observed any more. Never typed again.
#: There is no "acknowledged" or "completed" here, because the session reports neither.
OUTSTANDING = frozenset({"queued", "submitted"})
#: What a caller may treat as the message having reached the session.
RECEIVED = frozenset({"observed", "acknowledged"})  # "acknowledged": Agy's stream transport


INTENT_SCHEMA = "factory-launch-intent/1"


def intent_path(root: pathlib.Path, slot: str) -> pathlib.Path:
    return state_path(root, slot).with_name(f"{slot}-launch-intent.json")


def record_launch_intent(root: pathlib.Path, slot: str, launched: dict) -> None:
    """Record, durably and before the program exists, that this start hands it its first
    message as a launch argument.

    A message given that way reaches the program the instant it is spawned, before any
    session state can be written. So the fact that it is about to be given is written first,
    and it is written once: if a record already exists for this slot, an earlier start
    handed the first message over (or was about to), and what became of it may never have
    been observed. That is an unknown outcome, and an unknown outcome is not permission to
    hand the message over again, so this start is refused. Nothing here ever clears the
    record: a different lane (another slot) is the way to start over, or a person removes
    the file knowing what the earlier session did.
    """

    path = intent_path(root, slot)
    if path.is_symlink():
        raise ValueError(f"launch record {path.name} is a symlink; refusing to start")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        try:
            when = read(path).get("at")
        except (OSError, ValueError):
            when = None
        stamp = (datetime.datetime.fromtimestamp(when, datetime.UTC).isoformat(timespec="seconds")
                 if isinstance(when, (int, float)) else "an unreadable time")
        raise ValueError(
            f"an earlier start of this lane handed its first message to the program as its "
            f"launch argument (recorded {stamp}), and a first message is never handed over "
            f"twice. What that start's session did with it may not have been observed. "
            f"Continue that session if it reported one, start a replacement lane, or remove "
            f"{path} deliberately once you know the message was not taken") from None
    with os.fdopen(descriptor, "w") as stream:
        json.dump({"schema": INTENT_SCHEMA, "slot": slot, "token": launched["token"],
                   "typed_sha256": launched["typed_sha256"], "at": time.time(),
                   "wrapper_pid": os.getpid(),
                   "meaning": "written before the program was spawned; by itself it does not "
                              "say the program took the message, only that it may have"},
                  stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def retained(root: pathlib.Path, slot: str, session_id: str) -> dict:
    """The delivery record an earlier wrapper kept for this conversation, or ``{}``.

    A record that exists is evidence of what was typed. It is validated before anything is
    concluded from it, and it is set aside only on proof that nothing of it can be repeated:

    * unreadable, oversized, symlinked, or not a record of this slot in a known schema with
      a session id and a well-formed delivery map: refuse;
    * the same conversation: it must hold the first message's record (every wrapper writes
      one before it types); it is returned and its receipts stand. An empty ``session_id``
      means this start's conversation is new and its id not yet known (the provider chooses
      it and reports it), so it is the same conversation as nothing;
    * another conversation that never existed (no session id was ever bound) or never
      started (a terminal record with nothing but untyped ``queued`` entries): nothing was
      typed, so this start may supersede it. A record whose first message was handed over
      as a launch argument is not one of these, bound or not: the program may have taken it;
    * anything else is a conversation that received input. Starting a different one in its
      place would repeat its first message, so it is refused: resume it, or remove the
      record deliberately.
    """
    path = state_path(root, slot)
    refusal = "refusing to start a session whose earlier messages could be typed again"
    if path.is_symlink():
        raise ValueError(f"retained delivery record {path.name} is a symlink; {refusal}")
    if not path.exists():
        return {}
    try:
        prior = read(path)
    except (OSError, ValueError) as error:
        raise ValueError(
            f"retained delivery record {path.name} cannot be read ({error}); {refusal}") from None
    deliveries = prior.get("deliveries")
    earlier = prior.get("session_id")
    if (
        prior.get("schema") not in SCHEMAS
        or prior.get("slot") != slot
        or not isinstance(earlier, str)
        or prior.get("transport", "stream") not in (TERMINAL, "stream")
        or not isinstance(deliveries, dict)
        or not all(isinstance(key, str) and isinstance(record, dict)
                   and isinstance(record.get("state"), str)
                   for key, record in deliveries.items())
    ):
        raise ValueError(f"retained delivery record {path.name} is malformed; {refusal}")
    if session_id and earlier == session_id:
        if "initial" not in deliveries:
            raise ValueError(
                f"retained delivery record {path.name} for this conversation has no record "
                f"of its first message; {refusal}")
        return prior
    handed = any(record.get("via") == "launch-argument" and record["state"] != "queued"
                 for record in deliveries.values())
    never_bound = earlier == "" and not handed
    never_typed = (
        prior.get("transport") == TERMINAL
        and prior.get("session_started") is not True
        and all(record["state"] == "queued" for record in deliveries.values())
    )
    if never_bound or never_typed:
        return {}
    if earlier == "":
        raise ValueError(
            f"retained delivery record {path.name} shows a first message handed to the "
            "program at launch by a start whose session never reported; starting again "
            "would hand it over a second time. Remove the record deliberately once you "
            "know the message was not taken")
    raise ValueError(
        f"retained delivery record {path.name} belongs to conversation {earlier}, which "
        "received input; starting a different conversation in its place would repeat its "
        f"first message. Resume {earlier}, or remove the record deliberately")


def run_terminal(
    terminal,
    *,
    root: pathlib.Path,
    slot: str,
    agent: str,
    prompt: str,
    session_file: pathlib.Path,
    session_id: str,
    journal: pathlib.Path,
    journal_offset: int,
    launcher: dict,
    note,
    diagnostic=None,
    launched=None,
    withhold_followups=False,
    bind_by_mark=False,
    queue_command=None,
    queue_env=None,
) -> int:
    """Own one native terminal session until its actual exit.

    ``diagnostic`` receives, once, the first of what the program wrote to its terminal
    (bounded) if it ends without its session ever reporting a start: a program that refuses to start
    says why there and nowhere else. A session that does start has nothing of its screen
    kept. The text is the program's raw output and is not assumed free of anything the
    program chose to show (an account name, a sign-in prompt).

    ``terminal`` is the ``native_session.Terminal`` holding the program. ``note`` receives
    log lines (never the pane). Typed input waits for the session's own start report; a
    dialog the program shows before that (folder trust, sign-in) is the operator's to answer
    and never receives Factory text.

    What the session's hooks can and cannot establish. They are observation hooks added
    beside the operator's own, which run in parallel with them and may reject a prompt or
    make the session continue past a stop. So a prompt report means the prompt reached the
    session, not that it was accepted; a stop report means the session reported ending a
    response, not that it will not continue. ``phase`` therefore names the last report
    (``starting``, ``started``, ``prompt-observed``, ``stop-reported``, ``asking-operator``,
    ``waiting-for-input``, ``exited``), a delivery ends at ``observed``, and no stop report
    is ever attributed to a message.

    When a message is typed. Never because a phase looks idle: no stop report proves that.
    A message is typed when the session has reported starting, none of this wrapper's
    earlier messages is still unaccounted for, and the session's last report is not a
    question to the operator (keys typed then would answer it). If the session is still
    working, the message sits in the program's own input queue, as it would for a person
    typing, and stays ``submitted`` until the session reports it. One unaccounted message at
    a time is this wrapper's custody rule, so each report is attributable; it says nothing
    about the session. ``waiting-for-input`` is the session's own ``idle_prompt`` report.

    Delivery records survive this wrapper: a restart that resumes the same conversation
    keeps them, types nothing that was ever typed, and marks an unfinished one ``unknown``.

    ``withhold_followups`` is set for a program with no observed report of a point at
    which typed input is safe (Muse Code). Then this wrapper types nothing into the session
    at all: its first message went in as the launch argument, and a later message is
    accepted, kept ``queued`` with ``held`` saying why, and never typed. A person can still
    enter it in the session's own window.

    Why nothing short of that. A start report does not show that what is on screen later is
    the chat input. A stop report does not either: it is sent before the program decides
    whether to stop, and the independent tester showed the consequence on Muse Code 1.4.20
    (2026-11-13, /tmp/factory-muse-ef/RESULT.md): with an operator Stop hook still deciding,
    a message typed after the lane's own stop report was taken as part of the continued
    earlier turn, with no prompt report of its own, and stayed ``submitted``. Until the
    program is shown to report the point after which it will not continue, or to offer a
    delivery route of its own, automatic follow-up is withheld rather than guessed at.

    ``bind_by_mark`` is set for a program whose hooks also report sessions this lane did
    not start (Codex reports its own background agents). The lane's conversation is then
    the one whose prompt report carries the launch message's mark, and a start report alone
    binds nothing.

    ``queue_command`` is given when the program has a command of its own for handing a
    message to a running session (Codex: ``codex queue``). A later message is then given by
    running ``queue_command(session_id, text)`` and nothing is typed. The record is
    ``submitted`` (``via: provider-queue``) from before the command runs and ``observed``
    only when the session's own prompt report carries the mark; the command's success is
    not a receipt. A command that fails leaves the record ``unknown`` with what it said,
    and it is not run again.

    ``launched`` is given when the first message was handed to the program as its launch
    argument rather than typed (Muse Code, which reports a session's start only with its
    first prompt): the message's mark and the digests of what was handed over. That message
    is then already ``submitted`` when this begins, is never typed, and becomes ``observed``
    the same way a typed one does, by the session's own prompt report carrying its mark.

    ``session_id`` is empty when the provider chooses its own conversation id (Cursor): the
    first start report after this program was spawned names it, and it is bound then. Where
    a report carries a per-prompt ``generation`` (Cursor), the stop report with the same
    generation is recorded on that message as ``response_stop``: the session's own word
    that its response to that prompt ended, with its status. That is still not acceptance
    of the work, and a session without generations (Claude Code) gets no such field.
    """
    import native_session

    child = terminal.process
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
    if session_file.is_symlink():
        raise ValueError("symlinked session file")
    if session_file.exists() and session_file.read_text().strip() != session_id:
        raise ValueError("retained conversation changed")
    chosen_here = bool(session_id)

    def token_for(identifier):
        return native_session.message_token(state["session_id"], identifier)

    # What an earlier wrapper of this same conversation recorded is kept, not recreated.
    deliveries: dict = {}
    carried = {"stop_reports": 0, "prompt_reports": 0, "reported_active_s": 0.0}
    prior = retained(root, slot, session_id)
    if prior:
        for identifier, record in prior["deliveries"].items():
            # A receipt stands. Only typing that no report ever followed is unknown;
            # "pending" is the earlier stream transport's word for that.
            if record.get("state") in {"submitted", "pending"}:
                record = dict(
                    record,
                    state="unknown",
                    last_state=record.get("state"),
                    detail="typed by an earlier wrapper process; what became of it was not "
                    "observed and it is never typed again",
                )
            deliveries[identifier] = record
        for key in carried:
            if isinstance(prior.get(key), (int, float)):
                carried[key] = prior[key]
    if launched is not None and "initial" not in deliveries:
        # Handed over at launch: from that moment its outcome is uncertain, exactly as for
        # a typed message from its first byte, and it is never given again.
        deliveries["initial"] = {
            "state": "submitted", "message_id": "initial", "token": launched["token"],
            "typed_sha256": launched["typed_sha256"], "at": time.time(),
            "via": "launch-argument"}
    deliveries.setdefault("initial", {"state": "queued", "message_id": "initial"})
    waiting: dict[str, str] = {}  # accepted, not yet typed: message id -> text
    for identifier, record in deliveries.items():
        if record.get("state") != "queued":
            continue
        try:
            waiting[identifier] = (
                prompt if identifier == "initial" else planned(root, slot, identifier)["text"]
            )
        except (OSError, ValueError, KeyError) as error:
            deliveries[identifier] = dict(
                record, state="unknown", last_state="queued",
                detail=f"its retained text is no longer available ({error}); never typed")

    state = {
        "schema": SCHEMA,
        "transport": TERMINAL,
        "root": str(root.resolve()),
        "slot": slot,
        "agent": agent,
        "launcher": launcher,
        "worker": receipt["worker"],
        "wrapper": receipt["wrapper"],
        "session_id": session_id,
        # "chosen": this wrapper named the conversation at start, or resumed a known one.
        # "reported": the provider chose it and its own start report named it.
        "session_id_source": "chosen" if chosen_here else "reported",
        "session_started": False,
        "phase": "starting",
        "last_native_event": None,
        "stop_reports": carried["stop_reports"],
        "prompt_reports": carried["prompt_reports"],
        "reported_active_s": carried["reported_active_s"],
        "turn_started_at": time.time(),
        "stop_reported_at": None,
        "current_message_id": "initial",
        "current_input_id": (launched["typed_sha256"] if launched is not None
                             else native_session.text_digest(prompt)),
        "capabilities": [],
        "review_boundary_pending": False,
        "foreign_inputs": 0,
        "unbound_events": 0,
        "deliveries": deliveries,
    }
    # ``typing`` is the one message between "typing began" and "Enter written":
    # (message id, bytes still to queue or None, monotonic time the next step is due).
    typing: list | None = None
    digests: dict[str, str] = {}  # message id -> digest of exactly what was typed
    bodies: dict[str, str] = {}  # message id -> digest of the message without its mark
    if launched is not None and deliveries["initial"].get("via") == "launch-argument":
        digests["initial"] = launched["typed_sha256"]
        bodies["initial"] = launched["body_sha256"]
    ready_at = None
    events = native_session.Journal(journal, journal_offset)

    def save():
        store(path, state)

    startup = bytearray()  # the first of the program's output before any start report

    def remember(raw):
        # The beginning is what explains a refusal; a flood after it is not kept.
        room = native_session.STARTUP_KEEP - len(startup)
        if room > 0:
            startup.extend(raw[:room])

    def outstanding():
        return [key for key, record in deliveries.items() if record.get("state") in OUTSTANDING]

    def bind(reported):
        # The provider's own name for the conversation this wrapper started.
        if not re.fullmatch(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", reported):
            return False
        if session_file.exists() and session_file.read_text().strip() != reported:
            raise ValueError("retained conversation changed")
        state["session_id"] = reported
        return True

    def stop_row(row):
        # A stop report, named as one. It is not a result: it carries no success or error
        # verdict, and another hook may make the session continue after it. The message is
        # what the session said last, which a reader may search for a claim, never a proof.
        text = row.get("last_assistant_message")
        note((json.dumps({
            "type": "native-stop-report",
            "stop_hook_active": row.get("stop_hook_active") is True,
            "last_assistant_message": text if isinstance(text, str) else "",
            **({"status": row["status"]} if isinstance(row.get("status"), str) else {}),
            # A provider's own remarks about the stop, as given. Not readiness.
            **{name: row[name] for name in ("fully_idle", "continuation_active",
                                            "termination_reason") if name in row},
            "session_id": state["session_id"], "source": "native-stop-hook",
        }) + "\n").encode())

    def observe(row):
        nonlocal ready_at
        kind = row.get("event")
        if not state["session_id"] and bind_by_mark:
            # Not bound yet, and other sessions report through the same hooks: only the
            # prompt report that carries this start's own mark names the conversation.
            carried = row.get("tokens") if isinstance(row.get("tokens"), list) else []
            if (kind != "UserPromptSubmit" or launched is None
                    or launched["token"] not in carried
                    or not bind(str(row.get("session_id") or ""))):
                state["unbound_events"] += 1
                return
            note((json.dumps({"type": "native-session-start",
                              "session_id": state["session_id"],
                              "source": "native-prompt-hook"}) + "\n").encode())
        if not state["session_id"]:
            # Not bound yet: only this program's own start report can name the conversation.
            if kind != "SessionStart" or not bind(str(row.get("session_id") or "")):
                state["unbound_events"] += 1
                return
        session_id = state["session_id"]
        if row.get("session_id") != session_id:
            # Another conversation in this terminal (the operator cleared or switched it) is
            # not this seat's: it neither confirms nor ends anything.
            state["unbound_events"] += 1
            if kind == "SessionStart":
                state["observed_session_id"] = str(row.get("session_id") or "")
            return
        state.pop("observed_session_id", None)
        state["last_native_event"] = {"event": kind, "at": time.time()}
        if kind != "Notification":
            state.pop("asking", None)  # any later report means that question is behind it
        if kind in ("UserPromptSubmit", "Stop") and not state["session_started"]:
            # A resumed session may send no start report (Cursor did not). Its first prompt
            # or stop report is the same evidence that it is running and taking input; until
            # one arrives the session stays "starting" and nothing is typed to it.
            state["session_started"] = True
            state["phase"] = "started"
            ready_at = time.monotonic() + native_session.SETTLE_S
            if not session_file.exists():
                fd = os.open(session_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as stream:
                    stream.write(session_id + "\n")
        if kind == "SessionStart":
            if not state["session_started"]:
                state["session_started"] = True
                if not session_file.exists():
                    fd = os.open(session_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "w") as stream:
                        stream.write(session_id + "\n")
                note((json.dumps({"type": "native-session-start", "session_id": session_id,
                                  "source": "native-session-start-hook"}) + "\n").encode())
            if state["phase"] == "starting":
                state["phase"] = "started"
            ready_at = time.monotonic() + native_session.SETTLE_S
        elif kind == "UserPromptSubmit":
            reported = row.get("prompt_sha256")
            if "invocation" in row and reported and reported == state.get("last_prompt_sha256"):
                # A provider that reports the user's message before every model call
                # (Antigravity) repeats it within a turn. The same message is one prompt.
                return
            state["last_prompt_sha256"] = reported
            state["prompt_reports"] += 1
            tokens = row.get("tokens") if isinstance(row.get("tokens"), list) else []
            submitted = [key for key, record in deliveries.items()
                         if record.get("state") == "submitted"]
            # A report binds to the submitted message by its typed mark, or by being
            # byte-for-byte the message. Anything else is someone else's input.
            #   exact: the reported prompt is exactly what was typed, mark included.
            #   token: it carries the mark, but the program reported the body in another
            #          form (a paste wrapper or placeholder), so only the mark binds.
            #   body:  it is exactly the message, without the mark.
            matched = [(key, "exact" if reported == digests.get(key) else "token")
                       for key in submitted if deliveries[key].get("token") in tokens]
            if not matched:
                matched = [(key, "body") for key in submitted
                           if reported and reported == bodies.get(key)]
            if len(matched) == 1:
                identifier, how = matched[0]
                deliveries[identifier] = dict(
                    deliveries[identifier],
                    state="observed",
                    thread_id=session_id,
                    observed_at=time.time(),
                    text_match=how,
                    evidence="the session's prompt hook reported this message; receipt, "
                    "not acceptance or completion",
                    **({"generation": row["generation"]}
                       if isinstance(row.get("generation"), str) else {}),
                )
                state["current_message_id"] = identifier
                if identifier != "initial" and slot not in {"validator", "orchestrator"}:
                    import lane_dialogue

                    # The journal's "delivered" row names its transport, and this one says
                    # exactly how much was seen.
                    lane_dialogue.record_delivery(
                        root, message_id=identifier, thread_id=session_id,
                        transport=OBSERVED_TRANSPORT)
            else:
                state["foreign_inputs"] += 1  # the operator's own input, or an unmarked prompt
            # ``turn_started_at`` is when this prompt report arrived (the watchdog's clock).
            state.update(phase="prompt-observed", turn_started_at=time.time(),
                         stop_reported_at=None)
        elif kind == "Stop":
            stop_row(row)
            # A stop report is the session's, not any message's: with a queued prompt taken
            # mid-continuation, the next stop can belong to the turn before it. Only a
            # provider's own per-prompt id ties one to a message, and then it is recorded as
            # that and nothing more.
            state["stop_reports"] += 1
            generation = row.get("generation")
            for identifier, record in deliveries.items():
                if (isinstance(generation, str) and record.get("generation") == generation
                        and record.get("state") == "observed"):
                    deliveries[identifier] = dict(record, response_stop={
                        "at": time.time(), "status": row.get("status"),
                        "meaning": "the session reported its response to this prompt ended"})
            if state["phase"] == "prompt-observed":
                # Elapsed time between two reports. Not a completed turn's duration: the
                # session may continue past this stop.
                state["stop_reported_at"] = time.time()
                state["reported_active_s"] += max(
                    0.0, state["stop_reported_at"] - state["turn_started_at"]
                )
            if state["session_started"]:
                state["phase"] = "stop-reported"
        elif kind == "Notification":
            if row.get("notification_type") == native_session.WAITING:
                state["phase"] = "waiting-for-input"
            elif state["session_started"]:
                state["phase"] = "asking-operator"
                state["asking"] = str(row.get("notification_type"))
        elif kind == "SessionEnd":
            state["session_ended"] = str(row.get("reason") or "reported")

    def reopen(row):
        journal_path = path.parent / f"{slot}-launch.jsonl"
        # Invalidate any frozen export before new input can mutate the author tree.
        if not journal_path.exists():
            return
        from lane_dialogue import _append, _read

        launches = [item for item in _read(journal_path) if item.get("status") == "active"]
        if len(launches) != 1:
            raise ValueError("persistent launch is missing or ambiguous")
        _append(
            journal_path,
            {
                "schema_version": "factory-tmux-lane-reopen/1",
                "ts": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
                "run_id": launches[0]["run_id"],
                "role": slot.split("-")[0].split(".")[0],
                "slot": slot,
                "sender": row["sender"],
                "message_kind": row["message_kind"],
                "message_id": row["message_id"],
                "prompt_bytes": len(row["text"].encode()),
                "prompt_limit": None,
                "prompt_delivery": TERMINAL,
            },
        )

    def accept(row):
        identifier = row["message_id"]
        reopen(row)
        waiting[identifier] = row["text"]
        result = {"state": "queued", "message_id": identifier}
        if withhold_followups:
            result["held"] = ("not typed, and it will not be typed automatically: this "
                              "program reports no point at which typed input is safe (a "
                              "stop report comes before it decides whether to continue). "
                              "Enter the message in the session's own window to deliver it")
        deliveries[identifier] = result
        save()  # durable before any byte is typed
        return result

    def may_type():
        # Not a claim that the session is idle. See "When a message is typed" above.
        return (
            state["session_started"]
            and state["phase"] != "asking-operator"
            and not state.get("observed_session_id")
            and not withhold_followups
        )

    def advance():
        """Move the one message being typed a step, or begin the next waiting one."""
        nonlocal typing
        current = time.monotonic()
        if typing is not None:
            identifier, body, due = typing
            if body is not None:
                if current >= due:
                    terminal.type(body)
                    typing = [identifier, None, 0.0]
            elif terminal.typed():
                if due == 0.0:
                    typing[2] = current + native_session.SUBMIT_GAP_S
                elif current >= due:
                    terminal.type(native_session.SUBMIT)
                    typing = None
            return
        if not waiting or ready_at is None or current < ready_at or not may_type():
            return
        identifier = next(iter(waiting))
        if [key for key in outstanding() if key != identifier]:
            return  # one outstanding message at a time, so a report can be attributed
        if inspect(root, slot)["process_state"] != "running":
            return
        text = native_session.typed_text(waiting.pop(identifier), journal.parent, slot)
        marker = token_for(identifier)
        typed = native_session.marked(text, marker)
        digests[identifier] = native_session.text_digest(typed)
        bodies[identifier] = native_session.text_digest(text)
        state["current_input_id"] = digests[identifier]
        # From the first byte on, the outcome is uncertain: say so durably before typing it,
        # so no later process ever types this message again.
        deliveries[identifier] = {
            "state": "submitted", "message_id": identifier, "token": marker,
            "typed_sha256": digests[identifier], "at": time.time(),
            **({"via": "provider-queue"} if queue_command is not None else {})}
        save()
        if queue_command is not None:
            # The program's own queue takes the message; nothing is typed.
            try:
                done = subprocess.run(
                    queue_command(state["session_id"], typed), stdin=subprocess.DEVNULL,
                    capture_output=True, text=True, timeout=30, env=queue_env)
                said = (done.stdout.strip() or done.stderr.strip())[:300]
                failed = done.returncode != 0
            except (OSError, subprocess.SubprocessError) as error:
                said, failed = str(error)[:300], True
            deliveries[identifier] = dict(
                deliveries[identifier], queue_said=said,
                **({"state": "unknown", "last_state": "submitted",
                    "detail": "the program's queue command did not succeed; whether it "
                              "took the message is not known and it is not given again"}
                   if failed else {}))
            save()
            return
        typing = [identifier, native_session.paste(text) + native_session.trailer(marker),
                  current]

    def handle(command):
        if not isinstance(command, dict):
            raise ValueError("persistent request is not an object")
        if (command.get("token") != state["token"] or not state["session_id"]
                or command.get("thread") != state["session_id"]):
            raise ValueError("persistent request binding mismatch")
        if command.get("action", "deliver") != "deliver":
            raise ValueError("a native terminal session accepts deliver requests only")
        identifier = command.get("message_id")
        if not isinstance(identifier, str):
            raise ValueError("persistent message id is not a string")
        row = planned(root, slot, identifier)
        result = deliveries.get(identifier)
        if result is not None:
            return result  # asked again: report, never type twice
        if state.get("observed_session_id"):
            raise ValueError("native conversation changed")
        current = inspect(root, slot)
        # ``defer`` needs nothing held back here: a message typed while the session works
        # waits in the program's own input queue.
        if current["process_state"] != "running":
            return {"state": "busy", "message_id": identifier, "reason": "process stopped"}
        if not state["session_started"]:
            return {"state": "busy", "message_id": identifier,
                    "reason": "the session has not reported starting"}
        if state["phase"] == "asking-operator":
            return {"state": "busy", "message_id": identifier,
                    "reason": "the session is asking the operator a question on screen"}
        if typing is not None or waiting or outstanding():
            return {"state": "busy", "message_id": identifier,
                    "reason": "an earlier message is not yet accounted for"}
        return accept(row)

    with tempfile.TemporaryDirectory(prefix="factory-live-", dir="/tmp") as directory:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(directory + "/lane.sock")
            server.listen(8)
            state.update(socket=directory + "/lane.sock", token=secrets.token_hex(32))
            save()
            try:
                while child.poll() is None:
                    readable, writable, _ = select.select(
                        [server.fileno(), *terminal.readers()], terminal.writers(), [], 0.2)
                    terminal.service(
                        readable, writable, None if state["session_started"] else remember)
                    before = json.dumps(state, sort_keys=True, default=str)
                    for row in events.read():
                        observe(row)
                    advance()
                    if json.dumps(state, sort_keys=True, default=str) != before:
                        save()
                    if server.fileno() in readable:
                        with server.accept()[0] as connection:
                            connection.settimeout(1)
                            try:
                                incoming = bytearray()
                                while not incoming.endswith(b"\n"):
                                    part = connection.recv(4096)
                                    if not part or len(incoming) + len(part) > 8192:
                                        raise ValueError("invalid native input request")
                                    incoming.extend(part)
                                answer = handle(json.loads(incoming))
                                connection.sendall(json.dumps(answer).encode() + b"\n")
                            except (OSError, ValueError, KeyError, RuntimeError) as error:
                                # A client that timed out may have closed this connection.
                                # Keep the resident session; never retry input for it.
                                try:
                                    connection.sendall(
                                        json.dumps({"error": str(error)}).encode() + b"\n"
                                    )
                                except OSError:
                                    pass
                # What it wrote last, as it ended: its reason, if it never started.
                terminal.drain(None if state["session_started"] else remember)
                for row in events.read():
                    observe(row)
                return child.wait()
            finally:
                if not state["session_started"] and diagnostic is not None and startup:
                    try:
                        diagnostic(bytes(startup))
                    except OSError:
                        pass
                state["phase"] = "exited"
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
        # 0 only when the native client itself reported receiving the message.
        return 0 if args.action == "status" or result.get("state") in RECEIVED else 75
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"persistent-lane: {error}", file=__import__("sys").stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
