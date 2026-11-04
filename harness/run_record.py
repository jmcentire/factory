"""Local task inputs and results. This is a record, not an approval authority.

The Validator owns task decisions. Agents own their conversations. The host records
exact inputs/results and never turns status text into terminal input.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import sys
from pathlib import Path

SCHEMA = "factory-harness/3"


def read_json(path: Path) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4 * 1024 * 1024:
            raise ValueError(f"not a bounded regular record: {path}")
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"not an object: {path}")
    return value


def write_new(path: Path, value: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def run_root(runs: Path, run: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run):
        raise ValueError("invalid run name")
    root = runs.resolve() / run
    if root.is_symlink():
        raise ValueError("run directory is a symlink")
    return root


def create(
    runs: Path,
    run: str,
    repository: Path,
    task: bytes,
    *,
    budget: float | None = None,
    log: str = os.devnull,
) -> Path:
    from factory_runtime.host_git import run_host_git

    root = run_root(runs, run)
    repository = repository.resolve(strict=True)
    if not task.strip():
        raise ValueError("task is empty")
    task.decode("utf-8")
    if budget is not None and (not math.isfinite(budget) or budget <= 0):
        raise ValueError("budget must be positive and finite")
    if log != os.devnull and not Path(log).is_absolute():
        log = str(Path(log).resolve())
    # Pin inputs without checking out, cleaning, signing or modifying the user's repository.
    commit = run_host_git(repository, ["rev-parse", "--verify", "HEAD^{commit}"]).decode().strip()
    tree = (
        run_host_git(repository, ["rev-parse", "--verify", f"{commit}^{{tree}}"]).decode().strip()
    )
    top = Path(run_host_git(repository, ["rev-parse", "--show-toplevel"]).decode().strip())
    if top.resolve() != repository:
        raise ValueError("--repo must name the repository root")
    record = dict(
        schema_version=SCHEMA,
        run_id=run,
        repository=str(repository),
        resolved_commit=commit,
        resolved_tree=tree,
        task_digest="sha256:" + hashlib.sha256(task).hexdigest(),
        budget_usd=budget,
        log=log,
        tmux_socket="factory-" + hashlib.sha256(str(root).encode()).hexdigest()[:20],
        created_at=dt.datetime.now(dt.UTC).isoformat(),
    )
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(mode=0o700)  # Existing runs, including failed launches, are never overwritten.
    write_new(root / "TASK.md", task)
    write_new(root / "harness.json", (json.dumps(record, indent=2) + "\n").encode())
    return root


def load(runs: Path, run: str) -> tuple[Path, dict]:
    root = run_root(runs, run)
    doc = read_json(root / "harness.json")
    if doc.get("schema_version") != SCHEMA or doc.get("run_id") != run:
        raise ValueError(
            "this command requires a local task record; "
            "historical signed runs are preserved, not adopted"
        )
    if (
        not re.fullmatch(r"factory-[0-9a-f]{20}", doc.get("tmux_socket", ""))
        or doc["tmux_socket"] != "factory-" + hashlib.sha256(str(root).encode()).hexdigest()[:20]
    ):
        raise ValueError("run socket does not match its directory")
    task = root / "TASK.md"
    if (
        task.is_symlink()
        or "sha256:" + hashlib.sha256(task.read_bytes()).hexdigest() != doc["task_digest"]
    ):
        raise ValueError("retained task changed")
    if not Path(doc["repository"]).is_absolute() or not Path(doc["repository"]).is_dir():
        raise ValueError("recorded repository is unavailable")
    return root, doc


def context(runs: Path, run: str) -> None:
    root, doc = load(runs, run)
    # Shell consumers parse NUL-separated values, never eval record contents.
    fields = [
        str(root),
        doc["repository"],
        doc["resolved_commit"],
        doc["resolved_tree"],
        doc["task_digest"],
        doc["tmux_socket"],
        doc["log"],
    ]
    for value in fields:
        if not isinstance(value, str) or "\0" in value:
            raise ValueError("invalid context field")
        sys.stdout.buffer.write(value.encode() + b"\0")


def append_record(path: Path, payload: bytes) -> None:
    """Serialize appends; retain a killed writer's fragment separately from later rows."""
    fd = os.open(
        path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
    )
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"message sink is not a regular file: {path}")
        fcntl.flock(fd, fcntl.LOCK_EX)
        start = os.fstat(fd).st_size
        interrupted = start and os.pread(fd, 1, start - 1) != b"\n"
        if interrupted:
            payload = b"\n" + payload
        try:
            written = 0
            while written < len(payload):
                count = os.write(fd, payload[written:])
                if count < 1:
                    raise OSError("message append made no progress")
                written += count
            os.fsync(fd)
        except OSError:
            # The same inode lock excludes other appenders until rollback completes.
            os.ftruncate(fd, start)
            os.fsync(fd)
            raise
        if interrupted:
            print(f"factory: preserved interrupted message fragment in {path}", file=sys.stderr)
    finally:
        os.close(fd)
    parent = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def message(root: Path, recipient: str, sender: str, text: str, *, log: str = os.devnull) -> None:
    if recipient not in {"validator", "orchestrator"}:
        raise ValueError("author messages use tmux_lane_message.sh to preserve role boundaries")
    if sender not in {
        "validator",
        "orchestrator",
        "coder",
        "tester",
        "host",
        "dispatcher",
        "founder",
        "seat-supervisor",
    }:
        raise ValueError("unknown sender")
    if root.is_symlink():
        raise ValueError("run directory is a symlink")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = root / recipient
    if directory.is_symlink():
        raise ValueError("inbox directory is a symlink")
    directory.mkdir(mode=0o700, exist_ok=True)
    payload = (
        json.dumps(dict(sender=sender, text=text, ts=dt.datetime.now(dt.UTC).isoformat(),
                        message_digest=hashlib.sha256(text.encode()).hexdigest())) + "\n"
    ).encode()
    # Decisions and handoffs always persist, regardless of optional console logging.
    append_record(directory / "inbox.jsonl", payload)
    if log != os.devnull:
        try:
            append_record(Path(log), payload)
        except OSError as error:
            print(f"factory: message queued; optional log failed: {error}", file=sys.stderr)


def resident_command(agent: str, model: str, effort: str, prompt: str, root: Path) -> list[str]:
    """CLI adapters describe syntax, never an approved-model list."""
    model_args = ["--model", model] if model else []
    if agent == "codex":
        effort_args = ["-c", f'model_reasoning_effort="{effort}"'] if effort else []
        return [
            "codex",
            "--sandbox",
            "workspace-write",
            "--ask-for-approval",
            "on-request",
            "--add-dir",
            str(root),
            "--no-alt-screen",
            *model_args,
            *effort_args,
            prompt,
        ]
    if agent in {"claude", "agy"}:
        effort_args = ["--effort", effort] if effort else []
        head = [agent, "--add-dir", str(root), *model_args, *effort_args]
        return [*head, *(["--prompt-interactive"] if agent == "agy" else []), prompt]
    if agent == "muse":
        return ["muse", *model_args, *(["--reasoning-effort", effort] if effort else []), prompt]
    if agent == "cursor-agent":
        return ["agent", *model_args, prompt]
    if agent == "prime-agent":
        return [
            "prime-agent",
            *model_args,
            *(["--thinking", effort] if effort else []),
            "--",
            prompt,
        ]
    if agent == "ollama":
        if not model:
            raise ValueError("ollama requires an explicit installed model")
        return [
            "ollama",
            "launch",
            "codex",
            "--model",
            model,
            "--",
            "--sandbox",
            "workspace-write",
            prompt,
        ]
    raise ValueError(
        f"no resident CLI adapter for {agent}; add its invocation syntax, not a model approval"
    )


def start(runs: Path, run: str) -> None:
    import importlib
    import shlex
    import shutil
    import subprocess

    agents = (importlib.import_module("harness.agents") if __package__
              else importlib.import_module("agents"))
    model_settings = (importlib.import_module("harness.model_settings") if __package__
                      else importlib.import_module("model_settings"))

    root, doc = load(runs, run)
    tools = Path(__file__).resolve().parent
    home = tools.parent
    tmux = ["tmux", "-L", doc["tmux_socket"]]
    if subprocess.run([*tmux, "has-session", "-t", f"={run}"], capture_output=True).returncode == 0:
        raise ValueError("run session already exists; existing sessions are never replaced")
    billing = os.environ.get("FACTORY_SEAT_BILLING", "plan")
    if billing not in {"plan", "metered"}:
        raise ValueError("FACTORY_SEAT_BILLING must be plan or metered")
    if billing == "metered":
        raise ValueError(
            "resident coordination uses the signed-in plan; "
            "metered authors require explicit lane spend caps"
        )
    choices = {role: model_settings.resolve(role) for role in model_settings.ROLES}
    selected = {role: choices[role]["agent"] for role in ("validator", "orchestrator")}
    commands = []
    for role, agent in selected.items():
        directory = root / role
        if directory.is_symlink():
            raise ValueError(f"{role} inbox directory is a symlink")
        directory.mkdir(mode=0o700, exist_ok=True)
        model, effort = choices[role]["model"], choices[role]["effort"]
        directive = "validate" if role == "validator" else "orchestrate"
        prompt = (
            f"Act as Factory {role}. Read {home}/prompts/{directive}.md and {root}/TASK.md. "
            f"Run {run}; repository {doc['repository']}; commit {doc['resolved_commit']}. "
            f"Use the harness at {tools}. Read {root}/{role}/inbox.jsonl when checking progress. "
            "Remain in this conversation until the Validator completes the task. "
            "Use relevant Kindex history before scans and material decisions. "
            "Current user instructions govern. Preserve assigned agents and models. "
            "Do not create a catalog, signatures, waivers or approval paperwork."
        )
        if role == "orchestrator":
            prompt += (
                f" Continuously read all lane activity using {tools}/dispatcher.py "
                f"--run {run} --root {root} --observe --wait-seconds 30 --after CURSORS_JSON. "
                "Begin with {}; retain and pass the returned cursors to the next call. "
                "Keep observing and acting on concrete findings until task completion."
            )
        if agent == "kickoff" and role == "validator":
            brief = root / role / "BRIEF.md"
            try:
                write_new(brief, prompt.encode())
            except FileExistsError:
                # A failed coordinator start is retried with the same retained inputs.
                # Do not overwrite a changed brief or follow a substituted file.
                fd = os.open(brief, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, "rb") as stream:
                    if (not stat.S_ISREG(os.fstat(stream.fileno()).st_mode)
                            or stream.read(len(prompt.encode()) + 1) != prompt.encode()):
                        raise ValueError("retained kickoff brief changed") from None
            continue
        argv = resident_command(agent, model, effort, prompt, root)
        if shutil.which(argv[0]) is None:
            raise ValueError(f"{role} executable is unavailable: {argv[0]}")
        if agent == "ollama":
            # Reuse the authors' availability guard: `ollama launch` can otherwise
            # download a missing model. Naming a model is not permission to install it.
            available = subprocess.run(
                ["bash", "-c", 'source "$1"; factory_require_ollama_model factory "$2" "$3"',
                 "factory-model-availability", str(tools / "model_availability.sh"),
                 f"FACTORY_{role.upper()}_MODEL", model],
                capture_output=True, text=True,
            )
            if available.returncode:
                raise ValueError(available.stderr.strip() or "Ollama model is unavailable")
        env = {
            key: os.environ[key]
            for key in ("HOME", "USER", "PATH", "TMPDIR", "TERM", "SHELL", "LANG", "CODEX_HOME")
            if key in os.environ
        }
        # Reuse the adapter's credential declarations. Plan mode never inherits API keys.
        adapter = agents.harness("codex" if agent == "ollama" else agent)
        for key in adapter.api_env:
            env.pop(key, None)
        env.update(
            FACTORY_HOME=str(home),
            PYTHONPATH=str(home),
            FACTORY_PYTHON=sys.executable,
            FACTORY_RUNS_DIR=str(root.parent),
            FACTORY_CONTROL_ROOT=str(root),
            FACTORY_TMUX_SOCKET=doc["tmux_socket"],
            FACTORY_LOG=doc["log"],
            CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION="false",
        )
        # Explicit role/model assignments reach the Validator so delegation preserves them.
        for assigned_role, settings in choices.items():
            for field, value in settings.items():
                env[f"FACTORY_{assigned_role.upper()}_{field.upper()}"] = value
        command = [
            "env",
            "-i",
            *(f"{k}={v}" for k, v in env.items()),
            sys.executable,
            str(tools / "lane_agent.py"),
            "run",
            "--slot",
            role,
            "--terminal",
            "--log",
            str(root / f"{role}-lane.log"),
            "--exits",
            str(root / f"{role}-exits.jsonl"),
            "--",
            *argv,
        ]
        commands.append((role, "exec " + shlex.join(command)))
    created = False
    try:
        for role, shell_command in commands:
            args = (
                ["new-session", "-d", "-s", run]
                if not created
                else ["new-window", "-d", "-t", f"={run}"]
            )
            subprocess.run(
                [*tmux, *args, "-n", role, "-c", doc["repository"], shell_command], check=True
            )
            created = True
        # No dispatcher typing prose, no supervisor that can stop work for review lag.
        print(f"Factory {run}: tmux -L {doc['tmux_socket']} attach -t {shlex.quote(run)}")
        if selected["validator"] == "kickoff":
            print(f"Existing Validator: read {root}/validator/BRIEF.md")
    except BaseException as error:
        if created:
            subprocess.run([*tmux, "kill-session", "-t", f"={run}"], capture_output=True)
        if isinstance(error, subprocess.CalledProcessError):
            notice = (
                f"Coordinator startup failed with exit {error.returncode}. "
                "Task inputs are preserved; correct the launch error and retry start for this run."
            )
            message(root, "validator", "host", notice, log=doc["log"])
            raise ValueError(notice) from None
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "start", "context", "status", "message"])
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--task-file", type=Path)
    parser.add_argument("--budget", type=float)
    parser.add_argument("--log", default=os.environ.get("FACTORY_LOG", os.devnull))
    parser.add_argument("--to", choices=["validator", "orchestrator"])
    parser.add_argument("--from", dest="sender", default="host")
    parser.add_argument("--message-file", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "create":
            if args.repo is None or args.task_file is None:
                parser.error("create requires --repo and --task-file")
            print(
                create(
                    args.runs,
                    args.run,
                    args.repo,
                    args.task_file.read_bytes(),
                    budget=args.budget,
                    log=args.log,
                )
            )
        elif args.action == "start":
            start(args.runs, args.run)
        elif args.action == "context":
            context(args.runs, args.run)
        else:
            root, doc = load(args.runs, args.run)
            if args.action == "message":
                if args.to is None or args.message_file is None:
                    parser.error("message requires --to and --message-file")
                message(root, args.to, args.sender, args.message_file.read_text(), log=doc["log"])
            else:
                doc["validation"] = [
                    read_json(p) for p in sorted(root.glob("validation/*/result.json"))
                ]
                print(json.dumps(doc, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        print(f"factory: {error}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
