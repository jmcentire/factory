"""Bind each author to a policy-only broker outside its process tree.

The host holds a child behind a pipe until its identity, broker and immutable
binding receipt exist. Hooks read that protected receipt; no environment variable
can select a different broker. A new turn retains old denial receipts and cannot
start through an unresolved hold. This does not grant Codex hook trust.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import pathlib
import shlex
import signal
import socket
import subprocess
import sys
import time
import uuid
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import lane_custody as custody  # noqa: E402
import lane_watchdog  # noqa: E402
import policy_custody  # noqa: E402
import signet_hooks  # noqa: E402
import validator_boundary  # noqa: E402

SCHEMA = "factory-author-custody/1"
SOURCES = ("harness/author_custody.py", "harness/lane_custody.py",
           "harness/lane_watchdog.py", "harness/validator_boundary.py")


def select(environment: dict[str, str], role: str, client: str,
           repository: pathlib.Path, boundary: Any,
           root: pathlib.Path | None = None) -> dict | None:
    path = environment.get("FACTORY_AUTHOR_CUSTODY_CONFIG")
    digest = environment.get("FACTORY_AUTHOR_CUSTODY_DIGEST")
    required = (environment.get("FACTORY_REQUIRE_AUTHOR_CUSTODY") == "1"
                or (boundary is not None and (environment.get("FACTORY_REQUIRE_SIGNET") == "1"
                    or environment.get("FACTORY_SIGNET_CONTRACT"))))
    if not path and not digest and not required:
        return None  # legacy direct hooks are never persistent-custody qualification
    if not path or not digest:
        raise ValueError("required author custody binding is missing")
    config = configuration(pathlib.Path(path), digest)
    client = "codex" if client == "codex-interactive" else client
    if (config["role"] != role or config["client"] != client
            or pathlib.Path(config["repository"]).resolve() != repository.resolve()
            or config["boundary"] != boundary
            or (root is not None and pathlib.Path(config["policy"]["root"]).resolve()
                != root.resolve())):
        raise ValueError("launch does not match author custody binding")
    return {"config": path, "digest": digest}


def retained(root: pathlib.Path, slot: str, role: str, client: str,
             repository: pathlib.Path, boundary: Any) -> dict | None:
    if not custody._ID.fullmatch(slot):
        raise ValueError("invalid author slot")
    rows = [json.loads(line) for line in
            (root / "tmux-lanes" / f"{slot}-launch.jsonl").read_text().splitlines()]
    active_rows = [row for row in rows if row.get("status") == "active"]
    if len(active_rows) != 1:
        raise ValueError("retained author launch is missing or ambiguous")
    active = active_rows[0]
    record = active.get("author_custody")
    if record is None:
        if os.environ.get("FACTORY_REQUIRE_AUTHOR_CUSTODY") == "1":
            raise ValueError("required retained author custody is missing")
        return None
    if not isinstance(record, dict) or set(record) != {"config", "digest"}:
        raise ValueError("retained author custody is malformed")
    selected = select({"FACTORY_AUTHOR_CUSTODY_CONFIG": record["config"],
                       "FACTORY_AUTHOR_CUSTODY_DIGEST": record["digest"]},
                      role, client, repository, boundary, root)
    config = configuration(pathlib.Path(record["config"]), record["digest"])
    receipts = pathlib.Path(config["state"]) / "receipts"
    if receipts.exists():
        with policy_custody.locked(receipts):
            if policy_custody.holds(receipts):
                raise ValueError("author policy hold prevents continuation")
    return selected


def configuration(path: pathlib.Path, digest: str, *, host: bool = True) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError("author custody configuration must be host-owned")
    value = json.loads(path.read_text())
    fields = {"schema", "role", "client", "factory_home", "python", "repository",
              "boundary", "policy", "socket", "state", "sources"}
    if (not isinstance(value, dict) or set(value) != fields or value["schema"] != SCHEMA
            or custody._digest(value) != digest or value["role"] not in ("coder", "tester")
            or value["client"] not in signet_hooks.CLIENTS):
        raise ValueError("author custody configuration changed or is incomplete")
    home = pathlib.Path(value["factory_home"])
    if home.resolve() != HERE.parent or home.is_symlink():
        raise ValueError("author custody uses a different Factory source")
    sources = value["sources"]
    if not isinstance(sources, dict) or set(sources) != set(SOURCES):
        raise ValueError("author custody source pins missing")
    for name in SOURCES:
        if hashlib.sha256((home / name).read_bytes()).hexdigest() != sources[name]:
            raise ValueError("author custody source changed")
    if not host:
        # The host already verified the hash-bound boundary. A bounded hook must
        # not traverse its denied peer directories to repeat host admission.
        return value
    repository = pathlib.Path(value["repository"])
    boundary = validator_boundary.validate_lane_record(value["boundary"], value["role"], repository)
    if boundary is None:
        raise ValueError("author custody requires a kernel boundary")
    # validate_lane_record returns the contract, not a new permission grant.
    bound = validator_boundary.Boundary.parse(json.loads(
        pathlib.Path(value["boundary"]["contract"]).read_text()))
    policy_custody.validate(value["policy"], home)
    fixed = signet_hooks.contract(pathlib.Path(value["policy"]["contract"]),
                                 value["policy"]["contract_digest"])
    if (value["policy"]["client"] != value["client"]
            or pathlib.Path(fixed["project"]).resolve() != repository.resolve()):
        raise ValueError("author client or project differs from policy binding")
    state = pathlib.Path(value["state"])
    address = pathlib.Path(value["socket"])
    if (not state.is_absolute() or not address.is_absolute()
            or len(os.fsencode(address)) > 103 or state.is_symlink()
            or address not in bound.allowed_sockets):
        raise ValueError("author custody endpoint is outside the boundary")
    for protected in (path.resolve(), state.resolve(), address.parent.resolve(),
                      home / "harness", home / "factory_core", home / "factory_runtime",
                      pathlib.Path(value["policy"]["contract"]).resolve()):
        if not any(protected == p or protected.is_relative_to(p) for p in bound.protected_writes):
            raise ValueError("author can modify custody controls")
        if any(protected == p or protected.is_relative_to(p) for p in bound.denied_reads):
            raise ValueError("author cannot read its custody binding")
    return value


def read_binding(config: dict, digest: str) -> custody.Plan:
    record = custody._read_receipt(pathlib.Path(config["state"]) / "current.json")
    if (set(record) != {"token", "configuration_digest", "plan_digest", "plan"}
            or record.get("configuration_digest") != digest
            or not isinstance(record.get("plan"), dict)
            or not {"seat", "factory_home", "policy", "operations", "author_role"}
                <= set(record["plan"])):
        raise ValueError("author custody runtime binding changed")
    # Pure checks only: the hook cannot inspect the host's denied run root.
    # The receiving broker parses and validates the complete plan independently.
    plan = custody.Plan(record["plan"])
    custody._identity(plan.document["seat"])
    if (plan.digest != record.get("plan_digest")
            or plan.document.get("author_role") != config["role"]
            or plan.document.get("policy") != config["policy"]
            or plan.document.get("operations") != {}
            or plan.document.get("factory_home") != config["factory_home"]):
        raise ValueError("author custody process/client binding mismatch")
    return plan


def evaluate(path: pathlib.Path, digest: str, client: str, payload: Any,
             contract_path: pathlib.Path, contract_digest: str) -> dict:
    config = configuration(path, digest, host=False)
    if (config["client"] != client or config["policy"]["contract"] != str(contract_path)
            or config["policy"]["contract_digest"] != contract_digest):
        raise ValueError("hook differs from admitted author policy")
    plan = read_binding(config, digest)
    return signet_hooks.evaluate_host(client, payload, config["socket"], plan.digest)


def finish_processes(known: list[dict[str, Any]], children: list[subprocess.Popen]) -> dict:
    """Stop only observed identities, including retained orphans; uncertainty is not success."""
    errors = []
    for process in children:
        if not any(row["pid"] == process.pid for row in known) and process.poll() is None:
            # Startup may fail before capturing a new child's identity. The unreaped
            # Popen child still belongs to us; do not adopt a process-table replacement.
            process.kill()
            process.wait(timeout=3)
    table = lane_watchdog.process_snapshot()
    if table is not None:
        known = lane_watchdog.process_tree(table, known)
    else:
        errors.append("process observation unavailable")
    for number in (signal.SIGTERM, signal.SIGKILL):
        for identity in reversed(known):
            try:
                lane_watchdog.signal_identity(identity, number)
            except OSError:
                errors.append("identity signal unverified")
        for process in children:
            try:
                process.wait(timeout=.2)
            except subprocess.TimeoutExpired:
                pass
        time.sleep(.05)
    table = lane_watchdog.process_snapshot()
    survivors = [] if table is not None else known
    if table is not None:
        survivors = [identity for identity in known if identity["pid"] in table
            and table[identity["pid"]]["started"] == identity["started"]
            and not table[identity["pid"]]["state"].startswith("Z")]
    return {"stop_verified": table is not None and not survivors and not errors,
            "observed_identities": known, "survivors": survivors, "errors": errors}


def run(path: pathlib.Path, digest: str, command: list[str]) -> int:
    config = configuration(path, digest)
    if not command:
        raise ValueError("author command missing")
    state = pathlib.Path(config["state"])
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state.stat().st_mode & 0o077:
        raise ValueError("author custody state must be private")
    lock = os.open(state / "owner.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    child = broker = None
    address_inode = None
    read_fd = write_fd = None
    old_handlers = {}
    known: list[dict[str, Any]] = []
    token = None
    cleanup = None
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        owner = {"role": config["role"], "client": config["client"],
                 "repository": config["repository"], "root": config["policy"]["root"]}
        owner_path = state / "owner.json"
        if owner_path.exists():
            if custody._read_receipt(owner_path) != owner:
                raise ValueError("author custody state belongs to a different role/client/project")
        elif any(state.glob("*.json")) or (state / "receipts").exists():
            raise ValueError("existing custody state has no author ownership")
        else:
            custody._save_once(owner_path, owner)
        current = state / "current.json"
        if current.exists():
            previous = custody._read_receipt(current)
            previous_token = previous.get("token")
            if (not isinstance(previous_token, str) or len(previous_token) != 32
                    or any(c not in "0123456789abcdef" for c in previous_token)):
                raise ValueError("previous author identity is malformed")
            end = custody._read_receipt(state / ("exit-" + previous_token + ".json"))
            if end.get("stop_verified") is not True:
                raise ValueError("previous author exit is unverified")
        receipts = state / "receipts"
        receipts.mkdir(mode=0o700, exist_ok=True)
        with policy_custody.locked(receipts):
            if policy_custody.holds(receipts):
                raise ValueError("author policy hold requires supervised disposition")
        address = pathlib.Path(config["socket"])
        if address.exists() or address.is_symlink():
            raise ValueError("existing author broker requires explicit reconciliation")
        address.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        read_fd, write_fd = os.pipe()
        gate = ("import os,sys; fd=int(sys.argv[1]); token=os.read(fd,1); os.close(fd); "
                "token==b'1' or sys.exit(77); os.execvpe(sys.argv[2],sys.argv[2:],os.environ)")
        child = subprocess.Popen([config["python"], "-I", "-c", gate, str(read_fd), *command],
                                 pass_fds=(read_fd,))
        os.close(read_fd)
        read_fd = None
        table = lane_watchdog.process_snapshot()
        if not table or child.pid not in table:
            raise ValueError("author identity unavailable before admission")
        identity = {"pid": child.pid, "started": table[child.pid]["started"]}
        known = [identity]
        plan = custody.Plan.parse({"schema": custody.SCHEMA, "seat": identity,
            "factory_home": config["factory_home"], "python": config["python"],
            "cwd": config["factory_home"], "operations": {}, "author_role": config["role"],
            "policy": config["policy"]})
        token = uuid.uuid4().hex
        plan_path = state / f"plan-{token}.json"
        custody._save_once(plan_path, plan.document)
        binding = {"token": token, "configuration_digest": digest, "plan_digest": plan.digest,
                   "plan": plan.document}
        retained = state / f"binding-{token}.json"
        custody._save_once(retained, binding)
        temporary = state / f"current-{token}.json"
        custody._save_once(temporary, binding)
        os.replace(temporary, state / "current.json")
        broker = subprocess.Popen([config["python"], str(HERE / "lane_custody.py"), "serve",
            "--plan", str(plan_path), "--digest", plan.digest, "--socket", str(address),
            "--receipts", str(receipts)], cwd=config["factory_home"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        table = lane_watchdog.process_snapshot()
        if table is None or broker.pid not in table:
            raise ValueError("broker identity unavailable before admission")
        known.append({"pid": broker.pid, "started": table[broker.pid]["started"]})
        deadline = time.monotonic() + 5
        while not address.exists() and broker.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        if broker.poll() is not None or not address.exists():
            raise ValueError("author broker did not become ready")
        address_inode = address.lstat().st_ino
        # Connecting as the host is expected to be rejected by peer identity. It still
        # proves accept/respond readiness before releasing the child gate.
        with socket.socket(socket.AF_UNIX) as probe:
            probe.settimeout(2)
            probe.connect(str(address))
            probe.sendall(b'{}\n')
            result = custody._receive(probe)
            if result.get("status") != "refused":
                raise ValueError("author broker unexpectedly admitted host probe")
        def interrupted(number, _frame):
            raise InterruptedError(f"author wrapper interrupted by signal {number}")
        for number in (signal.SIGINT, signal.SIGTERM):
            old_handlers[number] = signal.signal(number, interrupted)
        if child.poll() is not None:
            raise ValueError("author exited before admission")
        os.write(write_fd, b"1")
        os.close(write_fd)
        write_fd = None
        while child.poll() is None:
            table = lane_watchdog.process_snapshot()
            if table is None:
                raise ValueError("author process observation unavailable")
            known = lane_watchdog.process_tree(table, known)
            if broker.poll() is not None:
                raise ValueError("author broker exited during model execution")
            time.sleep(.05)
        return child.returncode
    finally:
        for number, handler in old_handlers.items():
            signal.signal(number, handler)
        for fd in (read_fd, write_fd):
            if fd is not None:
                os.close(fd)
        try:
            if token is not None:
                cleanup = finish_processes(known, [p for p in (child, broker) if p is not None])
                custody._save_once(state / f"exit-{token}.json", cleanup)
            # Before identities can be captured, a retained Popen handle only refers to
            # our unreaped child. No process-table pattern or guessed PID is used.
            else:
                for process in (child, broker):
                    if process is not None and process.poll() is None:
                        process.kill()
                        process.wait(timeout=3)
            if address_inode is not None and cleanup and cleanup["stop_verified"]:
                with contextlib.suppress(FileNotFoundError):
                    if address.lstat().st_ino == address_inode:
                        address.unlink()
        finally:
            os.close(lock)
        if cleanup is not None and not cleanup["stop_verified"]:
            raise ValueError("author shutdown unverified; continuation refused")



def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--shell-command", help="canonical launcher argv, shell-quoted, never eval")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.check:
            configuration(args.config, args.digest)
            return 0
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if args.shell_command is not None:
            if command:
                raise ValueError("ambiguous author command")
            command = shlex.split(args.shell_command)
            if command[:1] == ["exec"]:
                command = command[1:]
        return run(args.config, args.digest, command)
    except (OSError, ValueError, subprocess.SubprocessError):
        print("author custody unavailable or invalid; admission refused", file=sys.stderr)
        return 77


if __name__ == "__main__":
    raise SystemExit(main())
