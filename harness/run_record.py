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
import secrets
import shlex
import stat
import sys
from io import BufferedReader
from pathlib import Path
from typing import cast

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
    from factory_runtime.version import build_identity

    record = dict(
        factory_version=build_identity(),
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
    from factory_runtime.kindex_workspace import mark_no_index

    mark_no_index(root)
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
    sandbox = os.environ.get("FACTORY_LANE_SANDBOX", "on")
    if sandbox not in {"on", "off"}:
        raise ValueError("FACTORY_LANE_SANDBOX must be on or off")
    unrestricted = sandbox == "off"
    model_args = ["--model", model] if model else []
    if agent == "codex":
        effort_args = ["-c", f'model_reasoning_effort="{effort}"'] if effort else []
        permission_args = (
            ["--dangerously-bypass-approvals-and-sandbox"] if unrestricted
            else ["--sandbox", "workspace-write", "--ask-for-approval", "on-request"]
        )
        return [
            "codex",
            *permission_args,
            "--add-dir",
            str(root),
            "--no-alt-screen",
            *model_args,
            *effort_args,
            prompt,
        ]
    if agent in {"claude", "agy"}:
        effort_args = ["--effort", effort] if effort else []
        head = [agent, "--add-dir", str(root),
                *(["--dangerously-skip-permissions"] if unrestricted else []),
                *model_args, *effort_args]
        return [*head, *(["--prompt-interactive"] if agent == "agy" else ["--"]), prompt]
    if agent == "muse":
        return ["muse", *(["--yolo"] if unrestricted else []), *model_args,
                *(["--reasoning-effort", effort] if effort else []), prompt]
    if agent == "cursor-agent":
        return ["agent", *(["--yolo", "--sandbox", "disabled", "--trust"]
                           if unrestricted else []), *model_args, prompt]
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


def capture_output(root: Path, role: str) -> None:
    """Retain tmux's output stream; this endpoint never writes to the terminal."""
    path = root / f"{role}-lane.log"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW
                 | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("terminal output log must be a regular file")
        while data := cast(BufferedReader, sys.stdin.buffer).read1(65536):
            offset = 0
            while offset < len(data):
                count = os.write(fd, data[offset:])
                if count <= 0:
                    raise OSError("terminal log append made no progress")
                offset += count
    finally:
        os.close(fd)


# A seat registers within a second or two; this bounds a seat that hangs before it can.
SEAT_REGISTRATION_SECONDS = 30


class SeatStartFailed(RuntimeError):
    """A coordinator's window opened, but its seat stopped or never registered."""


def sandboxed() -> bool:
    """Whether a macOS sandbox confines this process. The first tmux client forks the run's
    server, so the server and every seat inherit that sandbox: the seats cannot exec setuid
    tools such as ps, and their writes outside the sandbox fail."""
    if sys.platform != "darwin":
        return False
    import ctypes

    try:
        check = ctypes.CDLL("/usr/lib/libSystem.B.dylib").sandbox_check
    except (OSError, AttributeError):
        return False
    check.restype = ctypes.c_int
    check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    return check(os.getpid(), None, 0) != 0


def last_lane_line(log: Path) -> str:
    """The wrapper's last word on a seat, from the tail of its lane log."""
    try:
        with open(log, "rb") as stream:
            stream.seek(max(0, os.fstat(stream.fileno()).st_size - 65536))
            lines = stream.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    # Pane output shares the log and may leave a partial line ahead of the wrapper's.
    said = [line[line.index("lane-agent:"):] for line in lines if "lane-agent:" in line]
    return said[-1] if said else f"see {log}"


# gh reads these before its own keyring; a user signed in only through them keeps that sign-in.
GITHUB_ENV = ("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST")
# Claude Code's own config lock (proper-lockfile: a directory, stale after 10 s unrefreshed).
CLAUDE_LOCK_STALE_SECONDS = 10.0


def trust_claude_folders(home: Path, folders: list[Path], *, wait: float = 5.0) -> None:
    """Record folder trust for the run's authorized folders before a Claude Code seat starts.

    An interactive seat otherwise opens on "trust this folder?" with "No, exit" selected and sits
    idle until someone answers it in the pane. Writes ``projects[<path>].hasTrustDialogAccepted``
    in ``~/.claude.json`` under Claude Code's own lock, keeping every other key; the first change
    keeps the prior file as ``.claude.json.factory-backup``. Raises OSError/ValueError without
    writing when the file cannot be safely updated; the caller reports it and launches anyway.
    """
    import time

    config = home / ".claude.json"
    lock = Path(f"{config}.lock")
    real = config.resolve()  # a dotfile-managed symlink keeps pointing at the updated file
    keys = [str(folder.resolve()) for folder in folders]
    for _ in range(3):
        deadline = time.monotonic() + wait
        while True:
            try:
                lock.mkdir()
                break
            except FileExistsError:
                try:
                    stale = time.time() - lock.stat().st_mtime > CLAUDE_LOCK_STALE_SECONDS
                except FileNotFoundError:
                    continue
                if stale:
                    print(f"factory: removing stale lock {lock}", file=sys.stderr)
                    try:
                        lock.rmdir()
                    except FileNotFoundError:
                        pass
                    continue
                if time.monotonic() > deadline:
                    raise OSError(f"{lock} is held") from None
                time.sleep(0.05)
        try:
            try:
                before = os.stat(real)
                original = real.read_bytes()
                doc = json.loads(original)
            except FileNotFoundError:
                before, original, doc = None, None, {}
            except json.JSONDecodeError as error:
                raise ValueError(f"{real} is not valid JSON") from error
            projects = doc.setdefault("projects", {}) if isinstance(doc, dict) else None
            if not isinstance(projects, dict):
                raise ValueError(f"{real} has an unexpected shape")
            if all(isinstance(projects.get(key), dict)
                   and projects[key].get("hasTrustDialogAccepted") is True for key in keys):
                return
            for key in keys:
                entry = projects.setdefault(key, {})
                if not isinstance(entry, dict):
                    raise ValueError(f"{real} has an unexpected entry for {key}")
                entry["hasTrustDialogAccepted"] = True
            if original is not None:
                try:
                    write_new(Path(f"{real}.factory-backup"), original)
                except FileExistsError:
                    pass
            temporary = real.with_name(f".{real.name}.factory-{secrets.token_hex(6)}")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         stat.S_IMODE(before.st_mode) if before else 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(doc, stream, indent=2, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                # A writer that skipped the lock changed it under us: start over from its bytes.
                try:
                    now = os.stat(real)
                    changed = before is None or (now.st_ino, now.st_size, now.st_mtime_ns) != (
                        before.st_ino, before.st_size, before.st_mtime_ns)
                except FileNotFoundError:
                    changed = before is not None
                if changed:
                    continue
                os.replace(temporary, real)
            finally:
                temporary.unlink(missing_ok=True)
            directory = os.open(real.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return
        finally:
            try:
                lock.rmdir()
            except FileNotFoundError:
                pass
    raise OSError(f"{real} kept changing during the update")


def start(runs: Path, run: str) -> None:
    from factory_runtime.storage import run_storage_lock

    with run_storage_lock(run_root(runs, run)):
        _start_locked(runs, run)


def _start_locked(runs: Path, run: str) -> None:
    import importlib
    import shutil
    import subprocess

    agents = (importlib.import_module("harness.agents") if __package__
              else importlib.import_module("agents"))
    model_settings = (importlib.import_module("harness.model_settings") if __package__
                      else importlib.import_module("model_settings"))

    root, doc = load(runs, run)
    # A warning, not a refusal: stub seats in tests run anywhere. Real agents in a sandbox fail
    # when they save state outside it, so the operator hears it before they do.
    confined = sandboxed()
    if confined:
        print("factory: warning: this shell is inside a macOS sandbox, which the run's tmux "
              "server and every seat inherit; start runs from a shell outside it", file=sys.stderr)
    tools = Path(__file__).resolve().parent
    home = tools.parent
    tmux = ["tmux", "-L", doc["tmux_socket"]]
    retry = shlex.join([sys.executable, str(tools / "run_record.py"), "start",
                        "--runs", str(root.parent), "--run", run])
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
            f"Act as Factory {role}. Read {home}/prompts/{directive}.md, "
            f"{home}/docs/SOFTWARE-FACTORY.md and {root}/TASK.md. "
            f"Run {run}; repository {doc['repository']}; commit {doc['resolved_commit']}. "
            f"Use the harness at {tools}. Read {root}/{role}/inbox.jsonl when checking progress. "
            "Both coordinators review actual code and tests at the initial approach, first useful "
            "increment, material progress and each correction round under Regular quality "
            "check-ins. Guide early; drop unproductive arguments immediately. Escalate an "
            "unresolved issue or disputed importance to the user after three to four meaningful "
            "correction rounds; consensus is not required. "
            "Remain in this conversation until the Validator completes the task. "
            f"At completion run {sys.executable} {tools}/inbox_wake.py stop "
            f"--root {root} to stop inbox polling. "
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
        if agent == "claude":
            try:
                trust_claude_folders(Path(os.environ["HOME"]), [Path(doc["repository"]), root])
            except (KeyError, OSError, ValueError) as error:
                print(f"factory: warning: could not pre-trust the {role}'s folders for Claude "
                      f"Code ({error}); accept its folder-trust prompt in the {role} window",
                      file=sys.stderr)
        native_stream = agent in {"claude", "agy"}
        if native_stream:
            extra = ["--add-dir", str(root)]
            if os.environ.get("FACTORY_LANE_SANDBOX", "on") == "off":
                extra.append("--dangerously-skip-permissions")
            # A flag after --add-dir terminates its variadic arguments in both CLIs.
            built = agents.harness(agent).persistent_turn(
                model=model, effort=effort, cwd=Path(doc['repository']), extra=extra)
            argv = list(built.argv)
            if os.environ.get("FACTORY_LANE_SANDBOX", "on") != "off":
                argv = [arg for arg in argv if arg != "--dangerously-skip-permissions"]
                if "--permission-mode" in argv:
                    argv[argv.index("--permission-mode") + 1] = "default"
            prompt_file = directory / "initial-prompt.txt"
            if not prompt_file.exists():
                write_new(prompt_file, prompt.encode())
            elif prompt_file.read_text() != prompt:
                raise ValueError("retained initial coordinator prompt changed")
        else:
            argv = resident_command(agent, model, effort, prompt, root)
        executable = shutil.which(argv[0])
        if executable is None:
            raise ValueError(f"{role} executable is unavailable: {argv[0]}")
        # A bare name can fall through PATH after ENOENT from a missing shebang
        # interpreter. Launch the selected executable or fail, never a later agent.
        argv[0] = str(Path(executable).absolute())
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
            # TMUX_TMPDIR places the run's tmux socket; a seat without it reaches another server.
            for key in ("HOME", "USER", "PATH", "TMPDIR", "TMUX_TMPDIR", "TERM", "SHELL", "LANG",
                        "CODEX_HOME")
            if key in os.environ
        }
        # The Validator opens authorized pull requests; gh auth held only in the environment
        # reaches it. The Orchestrator and the authors keep no GitHub credential from here.
        if role == "validator":
            env.update({key: os.environ[key] for key in GITHUB_ENV if key in os.environ})
        # Reuse the adapter's credential declarations. Plan mode never inherits API keys.
        adapter = agents.harness("codex" if agent == "ollama" else agent)
        for key in adapter.api_env:
            env.pop(key, None)
        env.update(
            TERM=(env["TERM"] if env.get("TERM") not in {None, "", "dumb"}
                  else "xterm-256color"),
            FACTORY_HOME=str(home),
            PYTHONPATH=str(home),
            FACTORY_PYTHON=sys.executable,
            FACTORY_RUNS_DIR=str(root.parent),
            FACTORY_CONTROL_ROOT=str(root),
            FACTORY_TMUX_SOCKET=doc["tmux_socket"],
            FACTORY_LANE_SANDBOX=os.environ.get("FACTORY_LANE_SANDBOX", "on"),
            FACTORY_LOG=doc["log"],
            CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION="false",
        )
        # Explicit role/model assignments reach the Validator so delegation preserves them.
        for assigned_role, settings in choices.items():
            for field, value in settings.items():
                env[f"FACTORY_{assigned_role.upper()}_{field.upper()}"] = value
        channel = f"factory-ready-{role}-{secrets.token_hex(8)}"
        exits = root / f"{role}-exits.jsonl"
        command = [
            "env",
            "-i",
            *(f"{k}={v}" for k, v in env.items()),
            sys.executable,
            str(tools / "lane_agent.py"),
            "run",
            "--slot",
            role,
            *(["--persistent", "--run-root", str(root), "--session-agent", agent,
               "--session-file", str(root / f"{role}-session-id"),
               "--prompt-arg", str(prompt_file), "--plan-only"]
              if native_stream else ["--terminal"]),
            "--log",
            str(root / f"{role}-lane.log"),
            "--exits",
            str(exits),
            "--ready-channel",
            channel,
            "--",
            *argv,
        ]
        capture = shlex.join([
            sys.executable, str(tools / "run_record.py"), "capture",
            "--runs", str(root.parent), "--run", run, "--to", role,
        ])
        # Install the output pipe from inside this pane before starting the CLI,
        # so even the first model output reaches resident oversight. No input pipe.
        # Use this pane identity even in a detached window. A bare =target expands in zsh.
        pipe = shlex.join([*tmux, "pipe-pane", "-O", "-t"])
        pipe += ' "$TMUX_PANE" ' + shlex.quote(capture)
        commands.append((role, channel, exits.stat().st_size if exits.exists() else 0,
                         pipe + " && exec " + shlex.join(command)))
    def stopped(role: str, before: int) -> str:
        exits = root / f"{role}-exits.jsonl"
        if exits.exists() and exits.stat().st_size > before:
            return (f"the {role} stopped during startup; "
                    f"{last_lane_line(root / f'{role}-lane.log')}")
        return ""

    created = False
    try:
        for role, _, _, shell_command in commands:
            args = (
                ["new-session", "-d", "-s", run]
                if not created
                else ["new-window", "-d", "-t", f"={run}"]
            )
            subprocess.run(
                [*tmux, *args, "-n", role, "-c", doc["repository"], shell_command], check=True
            )
            created = True
        # A window can outlive its seat: the wrapper stops an agent whose process it cannot
        # record. Each seat signals once it registers or ends, and its exits journal says which.
        for role, channel, before, _ in commands:
            try:
                waited = subprocess.run([*tmux, "wait-for", channel], capture_output=True,
                                        text=True, timeout=SEAT_REGISTRATION_SECONDS)
            except subprocess.TimeoutExpired:
                raise SeatStartFailed(
                    f"the {role} did not register within {SEAT_REGISTRATION_SECONDS} seconds; "
                    f"{last_lane_line(root / f'{role}-lane.log')}"
                ) from None
            if seat := stopped(role, before):
                raise SeatStartFailed(seat)
            if waited.returncode:
                raise SeatStartFailed(f"the {role} did not register: {waited.stderr.strip()}")
        if any(agent in {"claude", "agy"} for agent in selected.values()):
            wake = shlex.join([sys.executable, str(tools / "inbox_wake.py"),
                               "run", "--root", str(root)])
            subprocess.run([*tmux, "new-window", "-d", "-t", f"={run}",
                            "-n", "inbox-wake", "-c", doc['repository'], wake], check=True)
        # Native inbox transport only; no supervisor may stop work for review lag.
        print(f"Factory {run}: tmux -L {doc['tmux_socket']} attach -t {shlex.quote(run)}")
        if selected["validator"] == "kickoff":
            print(f"Existing Validator: read {root}/validator/BRIEF.md")
    except BaseException as error:
        if created:
            subprocess.run([*tmux, "kill-session", "-t", f"={run}"], capture_output=True)
        if created and isinstance(error, subprocess.CalledProcessError):
            # A first window whose seat already exited took the session with it, so the next
            # window failed for want of one: report the seat, not tmux's exit status.
            seat = next((s for role, _, before, _ in commands if (s := stopped(role, before))), "")
            if seat:
                error = SeatStartFailed(seat)
        if isinstance(error, (subprocess.CalledProcessError, SeatStartFailed)):
            cause = (f"with exit {error.returncode}"
                     if isinstance(error, subprocess.CalledProcessError) else f"because {error}")
            where = (" from a shell outside the macOS sandbox this one runs in, which the run's "
                     "tmux server and every seat inherited," if confined else "")
            notice = (
                f"Coordinator startup failed {cause}. The run's tmux session is closed and its "
                f"task inputs are preserved; correct the cause, then retry{where} with: {retry}"
            )
            message(root, "validator", "host", notice, log=doc["log"])
            raise ValueError(notice) from None
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "start", "context", "status", "message",
                                           "capture"])
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--task-file", type=Path)
    parser.add_argument("--budget", type=float)
    parser.add_argument("--log", default=os.environ.get("FACTORY_LOG", os.devnull))
    parser.add_argument("--to", choices=["validator", "orchestrator"])
    parser.add_argument("--slot", help="author slot for terminal output capture")
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
            if args.action == "capture":
                if args.slot is not None:
                    if args.to or not re.fullmatch(
                        r"(?:coder|tester)(?:-[a-z0-9][a-z0-9-]{0,15})?"
                        r"(?:\.[A-Za-z0-9][A-Za-z0-9_-]{0,39})?", args.slot
                    ):
                        parser.error("capture requires one valid author slot or coordinator")
                    capture_output(root / "tmux-lanes", args.slot)
                else:
                    if args.to is None:
                        parser.error("capture requires --to or --slot")
                    capture_output(root, args.to)
            elif args.action == "message":
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
