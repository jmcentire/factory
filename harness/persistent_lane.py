"""Host-owned, acknowledged native input for resident Claude and Agy lanes.

A request can address only a retained typed dialogue message, and a successful receipt
requires the native client's own user-input acknowledgement.

Claude Code is a native terminal session (``run_terminal``, ``native_session.py``): the
message is typed into the terminal of the exact process this wrapper started. Its states say
only what was observed (``OUTSTANDING``): ``queued``, ``submitted``, ``observed`` when the
session's own prompt hook reports it, and ``acknowledged`` only when a stop report follows
that prompt. The operator's hooks run beside the observation hooks and may reject a prompt
or continue past a stop, so neither report alone is called delivery or completion. Nothing
is inferred from the screen, from silence or from elapsed time. Agy still uses its stream
input (``run``).
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
#:   queued        accepted and retained; not one byte typed.
#:   submitted     typing began (or finished); nothing from the session confirms it.
#:   observed      the session's own prompt hook reported a prompt carrying this message's
#:                 token. Hooks run in parallel and another one may still reject the prompt.
#:   acknowledged  a stop report followed that prompt with no other prompt between: the
#:                 session took the message and reported ending a response to it.
#:   unknown       typed, and what became of it can no longer be observed (the wrapper
#:                 restarted, or another prompt arrived first). Never typed again.
OUTSTANDING = frozenset({"queued", "submitted", "observed"})
#: Phases at which the session's last report leaves its input free for one more message.
INPUT_BOUNDARY = frozenset({"started", "stop-reported"})


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
) -> int:
    """Own one native terminal session until its actual exit.

    ``terminal`` is the ``native_session.Terminal`` holding the program. ``note`` receives
    log lines (never the pane). Typed input waits for the session's own start report; a
    dialog the program shows before that (folder trust, sign-in) is the operator's to answer
    and never receives Factory text.

    What the session's hooks can and cannot establish. They are observation hooks added
    beside the operator's own, which run in parallel with them and may reject a prompt or
    make the session continue past a stop. So a prompt report means the prompt reached the
    session, not that it was accepted; a stop report means the session reported ending a
    response, not that it will not continue. ``phase`` therefore names the last report
    (``starting``, ``started``, ``prompt-observed``, ``stop-reported``, ``exited``), and a
    delivery is ``acknowledged`` only on the evidence described at ``OUTSTANDING``. One
    message is outstanding at a time, so a report is attributable to it.

    Delivery records survive this wrapper: a restart that resumes the same conversation
    keeps them, types nothing that was ever typed, and marks an unfinished one ``unknown``.
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

    def token_for(identifier):
        return native_session.message_token(session_id, identifier)

    # What an earlier wrapper of this same conversation recorded is kept, not recreated.
    deliveries: dict = {}
    carried = {"stop_reports": 0, "prompt_reports": 0, "reported_active_s": 0.0}
    try:
        prior = read(path) if path.exists() and not path.is_symlink() else {}
    except (OSError, ValueError):
        prior = {}
    if (
        prior.get("schema") == SCHEMA
        and prior.get("transport") == TERMINAL
        and prior.get("slot") == slot
        and prior.get("session_id") == session_id
        and isinstance(prior.get("deliveries"), dict)
    ):
        for identifier, record in prior["deliveries"].items():
            if not isinstance(record, dict):
                continue
            if record.get("state") in {"submitted", "observed"}:
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
        "session_started": False,
        "phase": "starting",
        "last_native_event": None,
        "stop_reports": carried["stop_reports"],
        "prompt_reports": carried["prompt_reports"],
        "reported_active_s": carried["reported_active_s"],
        "turn_started_at": time.time(),
        "stop_reported_at": None,
        "current_message_id": "initial",
        "current_input_id": native_session.text_digest(prompt),
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
    observed = None  # the message whose prompt report is waiting for a stop report
    deferred = None
    ready_at = None
    events = native_session.Journal(journal, journal_offset)

    def save():
        store(path, state)

    def outstanding():
        return [key for key, record in deliveries.items() if record.get("state") in OUTSTANDING]

    def close_unknown(identifier, detail):
        record = deliveries[identifier]
        deliveries[identifier] = dict(
            record, state="unknown", last_state=record.get("state"), detail=detail)

    def stop_row(row):
        # A stop report, named as one. It is not a result: it carries no success or error
        # verdict, and another hook may make the session continue after it. The message is
        # what the session said last, which a reader may search for a claim, never a proof.
        text = row.get("last_assistant_message")
        note((json.dumps({
            "type": "native-stop-report",
            "last_assistant_message": text if isinstance(text, str) else "",
            "session_id": session_id, "source": "native-stop-hook",
        }) + "\n").encode())

    def observe(row):
        nonlocal ready_at, observed
        kind = row.get("event")
        if row.get("session_id") != session_id:
            # Another conversation in this terminal (the operator cleared or switched it) is
            # not this seat's: it neither confirms nor ends anything.
            state["unbound_events"] += 1
            if kind == "SessionStart":
                state["observed_session_id"] = str(row.get("session_id") or "")
            return
        state.pop("observed_session_id", None)
        state["last_native_event"] = {"event": kind, "at": time.time()}
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
            state["prompt_reports"] += 1
            if observed is not None:
                # No stop report came between: the earlier prompt cannot be shown accepted.
                close_unknown(observed, "another prompt was reported before any stop report "
                              "followed this one; a hook may have rejected it")
                observed = None
            tokens = row.get("tokens") if isinstance(row.get("tokens"), list) else []
            reported = row.get("prompt_sha256")
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
                )
                observed = identifier
                state["current_message_id"] = identifier
            else:
                state["foreign_inputs"] += 1  # the operator's own input, or an unmarked prompt
            # ``turn_started_at`` is when this prompt report arrived (the watchdog's clock).
            state.update(phase="prompt-observed", turn_started_at=time.time(),
                         stop_reported_at=None)
        elif kind == "Stop":
            stop_row(row)
            state["stop_reports"] += 1
            if observed is not None:
                deliveries[observed] = dict(
                    deliveries[observed], state="acknowledged", at=time.time(),
                    evidence="a stop report followed the observed prompt")
                if observed != "initial" and slot not in {"validator", "orchestrator"}:
                    import lane_dialogue

                    lane_dialogue.record_delivery(
                        root, message_id=observed, thread_id=session_id, transport="persistent"
                    )
                observed = None
            if state["phase"] == "prompt-observed":
                # Elapsed time between two reports. Not a completed turn's duration: the
                # session may continue past this stop.
                state["stop_reported_at"] = time.time()
                state["reported_active_s"] += max(
                    0.0, state["stop_reported_at"] - state["turn_started_at"]
                )
            if state["session_started"]:
                state["phase"] = "stop-reported"
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

    def accept(row, boundary=None):
        identifier = row["message_id"]
        if boundary is None:
            reopen(row)
            waiting[identifier] = row["text"]
        result = {"state": "queued", "message_id": identifier}
        if boundary:
            result["boundary"] = boundary
        deliveries[identifier] = result
        save()  # durable before any byte is typed
        return result

    def at_boundary():
        return (
            state["session_started"]
            and state["phase"] in INPUT_BOUNDARY
            and not state.get("observed_session_id")
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
        if not waiting or ready_at is None or current < ready_at or not at_boundary():
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
            "typed_sha256": digests[identifier], "at": time.time()}
        save()
        typing = [identifier, native_session.paste(text) + native_session.trailer(marker),
                  current]

    def handle(command):
        nonlocal deferred
        if not isinstance(command, dict):
            raise ValueError("persistent request is not an object")
        if command.get("token") != state["token"] or command.get("thread") != session_id:
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
        may_defer = (
            command.get("defer") is True
            and row["message_kind"] == "progress-review"
            and row["text"].startswith("FACTORY_REVIEW verdict=ON_TRACK\n")
            and state["phase"] == "prompt-observed"
            and deferred is None
        )
        if may_defer and current["process_state"] == "running":
            deferred = row
            return accept(row, boundary="next-native-turn")
        if (current["process_state"] != "running" or not at_boundary()
                or typing is not None or waiting or outstanding()):
            return {"state": "busy", "message_id": identifier}
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
                    terminal.service(readable, writable)
                    before = json.dumps(state, sort_keys=True, default=str)
                    for row in events.read():
                        observe(row)
                    if deferred is not None and at_boundary() and typing is None \
                            and not waiting and outstanding() == [deferred["message_id"]]:
                        reopen(deferred)
                        waiting[deferred["message_id"]] = deferred["text"]
                        deferred = None
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
                terminal.drain()
                for row in events.read():
                    observe(row)
                return child.wait()
            finally:
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
        return 0 if args.action == "status" or result.get("state") == "acknowledged" else 75
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"persistent-lane: {error}", file=__import__("sys").stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
