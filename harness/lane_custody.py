#!/usr/bin/env python3
"""Host-owned, fixed-operation custody for a kernel-bounded Factory seat.

Execution requests send an operation ID and a unique request ID, never argv or shell text.
An optional host-bound policy endpoint accepts tool metadata only for evaluation, never execution.
The host freezes each operation's canonical entry point and arguments before admission.
The kernel supplies the connecting PID; only the bound seat or its current descendants may
request work. The service must live outside that seat's process tree. A persisted started
request is never automatically replayed after a crash: uncertain effects require inspection.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import platform
import re
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any

_HARNESS = str(pathlib.Path(__file__).resolve().parent)
if _HARNESS not in sys.path:
    sys.path.insert(0, _HARNESS)
import lane_watchdog  # noqa: E402

SCHEMA = "factory-lane-custody/1"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")
_MAX_MESSAGE = 65536
_ENTRYPOINTS = {
    "lane": ("harness/tmux_lane.sh",),
    "message": ("harness/tmux_lane_message.sh",),
    "factory-start": ("harness/factory.sh",),
    "assessment": ("harness/orchestrator_channel.py",),
    "seat-control": ("harness/seat_supervisor.py",),
    "agent-info": ("harness/lane_agent.py",),
    "factory-cli": ("factory_runtime/cli.py",),
}


class CustodyError(ValueError):
    """A request cannot be attributed to its admitted seat and fixed operation."""


def _digest(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(body).hexdigest()


def _identity(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != {"pid", "started"}
            or type(value["pid"]) is not int or value["pid"] <= 1
            or not isinstance(value["started"], str) or not value["started"].strip()):
        raise CustodyError("invalid seat process identity")
    return value


def _absolute(value: Any) -> pathlib.Path:
    if not isinstance(value, str) or not pathlib.Path(value).is_absolute():
        raise CustodyError("host paths must be absolute")
    return pathlib.Path(value).resolve(strict=True)


@dataclass(frozen=True)
class Plan:
    document: dict[str, Any]

    @classmethod
    def parse(cls, value: Any) -> Plan:
        fields = {"schema", "seat", "factory_home", "python", "cwd", "operations"}
        if (not isinstance(value, dict) or not fields <= set(value)
                or set(value) - fields - {"policy"}
                or value["schema"] != SCHEMA):
            raise CustodyError("invalid custody plan")
        _identity(value["seat"])
        home = _absolute(value["factory_home"])
        python = _absolute(value["python"])
        cwd = _absolute(value["cwd"])
        if not home.is_dir() or not cwd.is_dir() or not os.access(python, os.X_OK):
            raise CustodyError("custody runtime is unavailable")
        if cwd != home:
            raise CustodyError("custody must execute from the protected Factory checkout")
        operations = value["operations"]
        if not isinstance(operations, dict) or not operations or len(operations) > 256:
            raise CustodyError("host must supply a bounded nonempty operation registry")
        for name, operation in operations.items():
            if not isinstance(name, str) or not _ID.fullmatch(name):
                raise CustodyError("invalid operation ID")
            if (not isinstance(operation, dict) or set(operation) != {
                    "entrypoint", "args", "timeout", "source_sha256"}
                    or not isinstance(operation["entrypoint"], str)
                    or operation["entrypoint"] not in _ENTRYPOINTS
                    or type(operation["timeout"]) is not int
                    or not 1 <= operation["timeout"] <= 300
                    or not isinstance(operation["args"], list)
                    or len(operation["args"]) > 128
                    or any(not isinstance(a, str) or "\0" in a for a in operation["args"])):
                raise CustodyError("invalid fixed operation")
            script = home / _ENTRYPOINTS[operation["entrypoint"]][0]
            if not script.is_file() or script.is_symlink():
                raise CustodyError("canonical entry point is absent or changed")
            if hashlib.sha256(script.read_bytes()).hexdigest() != operation["source_sha256"]:
                raise CustodyError("canonical entry point is absent or changed")
        if "policy" in value:
            import policy_custody
            policy_custody.validate(value["policy"], home)
        return cls(value)

    @property
    def digest(self) -> str:
        return _digest(self.document)

    def command(self, operation: str) -> list[str]:
        spec = self.document["operations"].get(operation)
        if spec is None:
            raise CustodyError("operation was not admitted by the host")
        # Recheck immediately before execution; neither the client nor a stale plan chooses code.
        Plan.parse(self.document)
        script = pathlib.Path(self.document["factory_home"]) / _ENTRYPOINTS[spec["entrypoint"]][0]
        if spec["entrypoint"] == "factory-cli":
            # -m normally searches cwd before PYTHONPATH. Never import a seat-created
            # factory_runtime package. Isolated Python gets only the host-pinned code root.
            return [self.document["python"], "-I", "-c",
                    "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
                    "runpy.run_module('factory_runtime.cli',run_name='__main__')",
                    self.document["factory_home"], *spec["args"]]
        interpreter = self.document["python"] if script.suffix == ".py" else "/bin/bash"
        return [interpreter, str(script), *spec["args"]]


def peer_pid(connection: socket.socket) -> int:
    if platform.system() == "Darwin":
        # Darwin SDK sys/un.h: SOL_LOCAL=0, LOCAL_PEERPID=2. Never accept a caller-supplied PID.
        return struct.unpack("i", connection.getsockopt(0, 2, struct.calcsize("i")))[0]
    if platform.system() == "Linux":
        peercred = getattr(socket, "SO_PEERCRED", None)
        if peercred is None:
            raise CustodyError("kernel peer credentials are unavailable")
        identity = connection.getsockopt(socket.SOL_SOCKET, peercred, 12)
        return struct.unpack("3i", identity)[0]
    raise CustodyError("kernel peer identity is unavailable on this platform")


def authorized_peer(plan: Plan, pid: int, server_pid: int) -> None:
    table = lane_watchdog.process_snapshot()
    if table is None:
        raise CustodyError("cannot observe seat process identity")
    seat = plan.document["seat"]
    root = table.get(seat["pid"])
    if (root is None or root["started"] != seat["started"] or root["state"].startswith("Z")
            or "T" in root["state"]):
        raise CustodyError("seat identity ended or changed")
    tree = lane_watchdog.process_tree(table, [seat])
    members = {item["pid"] for item in tree}
    if server_pid in members:
        raise CustodyError("custody must be outside the seat process tree")
    if pid not in members or pid not in table or table[pid]["state"].startswith("Z"):
        raise CustodyError("request did not originate in the admitted seat")


def _receive(connection: socket.socket, timeout: float = 5,
             maximum: int = _MAX_MESSAGE) -> Any:
    connection.settimeout(timeout)
    body = bytearray()
    while b"\n" not in body:
        block = connection.recv(min(4096, maximum + 1 - len(body)))
        if not block:
            raise CustodyError("incomplete request")
        body.extend(block)
        if len(body) > maximum:
            raise CustodyError("request too large")
    line, trailing = bytes(body).split(b"\n", 1)
    if trailing.strip():
        raise CustodyError("one request per connection is required")
    return json.loads(line)


def _save_once(path: pathlib.Path, value: Any) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_receipt(path: pathlib.Path) -> dict[str, Any]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_MESSAGE:
            raise CustodyError("invalid custody receipt")
        value = json.load(handle)
    if not isinstance(value, dict):
        raise CustodyError("invalid custody receipt")
    return value


def execute(plan: Plan, request: Any, receipts: pathlib.Path) -> dict[str, Any]:
    if (not isinstance(request, dict) or set(request) != {"request_id", "operation", "plan_digest"}
            or not isinstance(request["request_id"], str)
            or not _ID.fullmatch(request["request_id"])
            or request["request_id"].startswith("policy-")
            or not isinstance(request["operation"], str)
            or request["plan_digest"] != plan.digest):
        raise CustodyError("request does not match the admitted plan")
    if "policy" in plan.document:
        import policy_custody
        with policy_custody.locked(receipts):
            if policy_custody.holds(receipts):
                raise CustodyError(
                    "policy hold prevents execution; supervised disposition required")
    command = plan.command(request["operation"])
    stem = receipts / request["request_id"]
    started = stem.with_suffix(stem.suffix + ".started.json")
    terminal = stem.with_suffix(stem.suffix + ".terminal.json")
    if terminal.exists() or terminal.is_symlink():
        prior = _read_receipt(terminal)
        if prior.get("request") != request:
            raise CustodyError("request ID was reused for different work")
        return {**prior, "replayed": True}
    if started.exists() or started.is_symlink():
        raise CustodyError("prior request outcome is uncertain; inspect before retrying")
    # A new request ID does not resolve an earlier uncertain effect of the same operation.
    # This is checked before creating a new started receipt, not after executing again.
    for index, pending in enumerate(receipts.glob("*.started.json")):
        if "policy" in plan.document and pending.name.startswith("policy-"):
            continue  # validated policy outcomes/holds above, not execution receipts
        if index >= 4096:
            raise CustodyError("custody receipt bound exceeded; inspect before further work")
        previous = _read_receipt(pending).get("request")
        if not isinstance(previous, dict):
            raise CustodyError("prior outcome is uncertain or malformed")
        if previous.get("operation") != request["operation"]:
            continue
        finished = pending.with_name(pending.name.removesuffix(".started.json") + ".terminal.json")
        completion = _read_receipt(finished) if finished.exists() else {}
        if completion.get("request") != previous or completion.get("status") != "completed":
            raise CustodyError("operation has an uncertain prior request; inspect before retrying")
    _save_once(started, {"request": request, "at": datetime.datetime.now(datetime.UTC).isoformat()})
    # Logs remain host-owned. Their contents are not relayed automatically across role boundaries.
    log = stem.with_suffix(stem.suffix + ".log")
    spec = plan.document["operations"][request["operation"]]
    with log.open("xb") as output:
        child = subprocess.Popen(command, cwd=plan.document["cwd"], stdin=subprocess.DEVNULL,
                                 stdout=output, stderr=subprocess.STDOUT,
                                 env={**os.environ, "PYTHONPATH": plan.document["factory_home"],
                                      "FACTORY_HOME": plan.document["factory_home"]})
        snapshot = lane_watchdog.process_snapshot()
        identity = ({"pid": child.pid, "started": snapshot[child.pid]["started"]}
                    if snapshot and child.pid in snapshot else None)
        timed_out = False
        try:
            rc = child.wait(timeout=spec["timeout"])
        except subprocess.TimeoutExpired:
            timed_out = True
            if identity is None:
                raise CustodyError("operation timed out without an observable identity") from None
            table = lane_watchdog.process_snapshot()
            if table is None:
                raise CustodyError("operation timeout; process state unknown") from None
            identities = lane_watchdog.process_tree(table, [identity])
            for item in reversed(identities):
                lane_watchdog.signal_identity(item, signal.SIGTERM)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                table = lane_watchdog.process_snapshot()
                if table is not None and not any(
                    i["pid"] in table and table[i["pid"]]["started"] == i["started"]
                    and not table[i["pid"]]["state"].startswith("Z") for i in identities
                ):
                    break
                time.sleep(.1)
            else:
                raise CustodyError("operation stop not acknowledged; retained as uncertain")
            rc = child.wait(timeout=1)
    result = {"request": request, "status": "timed-out" if timed_out else "completed",
              "returncode": rc, "identity": identity, "replayed": False}
    _save_once(terminal, result)
    return result


def serve(plan: Plan, address: pathlib.Path, receipts: pathlib.Path, *, once: bool = False) -> None:
    if address.exists() or address.is_symlink():
        raise CustodyError("refusing to replace an existing custody endpoint")
    receipts.mkdir(mode=0o700, parents=True, exist_ok=True)
    if receipts.is_symlink() or receipts.stat().st_mode & 0o077:
        raise CustodyError("custody receipts must be in an owner-only real directory")
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(address))
        os.chmod(address, 0o600)
        inode = address.lstat().st_ino
        server.listen(4)
        try:
            while True:
                connection, _ = server.accept()
                with connection:
                    try:
                        authorized_peer(plan, peer_pid(connection), os.getpid())
                        request = _receive(connection, maximum=2 * 1024 * 1024)
                        if isinstance(request, dict) and request.get("kind") == "policy":
                            import policy_custody
                            result = policy_custody.evaluate(plan, request, receipts)
                        else:
                            result = execute(plan, request, receipts)
                    except (CustodyError, OSError, ValueError, TypeError,
                            subprocess.SubprocessError) as error:
                        result = {"status": "refused", "reason": str(error)}
                    try:
                        connection.sendall(json.dumps(result).encode() + b"\n")
                    except (BrokenPipeError, ConnectionResetError):
                        pass  # terminal/uncertain receipts, not the connection, own the outcome
                if once:
                    return
        finally:
            if address.exists() and address.lstat().st_ino == inode:
                address.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    host = sub.add_parser("serve")
    host.add_argument("--plan", required=True, type=pathlib.Path)
    host.add_argument("--digest", required=True)
    host.add_argument("--socket", required=True, type=pathlib.Path)
    host.add_argument("--receipts", required=True, type=pathlib.Path)
    release = sub.add_parser("release-policy", help="host-only supervised hold disposition")
    release.add_argument("--plan", required=True, type=pathlib.Path)
    release.add_argument("--digest", required=True)
    release.add_argument("--receipts", required=True, type=pathlib.Path)
    release.add_argument("--denial-id", required=True)
    release.add_argument("--assessment-digest", required=True)
    client = sub.add_parser("request")
    client.add_argument("--socket", required=True, type=pathlib.Path)
    client.add_argument("--digest", required=True)
    client.add_argument("--operation", required=True)
    client.add_argument("--request-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action in {"serve", "release-policy"}:
            plan = Plan.parse(json.loads(args.plan.read_text()))
            if plan.digest != args.digest:
                raise CustodyError("host custody plan changed")
            if args.action == "serve":
                serve(plan, args.socket, args.receipts)
            else:
                import policy_custody
                print(json.dumps(policy_custody.release(plan, args.receipts, args.denial_id,
                                                       args.assessment_digest)))
        else:
            with socket.socket(socket.AF_UNIX) as connection:
                connection.connect(str(args.socket))
                connection.sendall(json.dumps({"request_id": args.request_id,
                    "operation": args.operation, "plan_digest": args.digest}).encode() + b"\n")
                result = _receive(connection, timeout=310)
                print(json.dumps(result))
                return 0 if result.get("status") == "completed" \
                    and result.get("returncode") == 0 else 77
    except (CustodyError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"lane-custody: {error}", file=sys.stderr)
        return 77
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
