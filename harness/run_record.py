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
import time
from collections.abc import Callable
from io import BufferedReader
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any, cast

SCHEMA = "factory-harness/3"


def agent_entrypoints(directory: str) -> dict[str, str]:
    path = Path(directory)
    if not path.is_absolute() or os.pathsep in directory:
        raise ValueError(f"FACTORY_AGENT_BIN must name one absolute directory: {directory}")
    if not path.exists():
        raise ValueError(f"agent entrypoint directory (FACTORY_AGENT_BIN) is missing: {directory}")
    if not path.is_dir():
        raise ValueError(
            f"agent entrypoint directory (FACTORY_AGENT_BIN) is not a directory: {directory}")
    import importlib

    # Ollama is a coordinator command and a lane model provider, not an author harness.
    agents = (importlib.import_module("harness.agents") if __package__
              else importlib.import_module("agents"))
    names = {agent.executable for agent in agents.HARNESSES.values()} | {"ollama"}
    entries = {}
    for entry in path.iterdir():
        if entry.name not in names:
            if entry.is_file() and not os.access(entry, os.X_OK):
                continue
            raise ValueError(f"FACTORY_AGENT_BIN contains an unexpected entry: {entry}")
        if not entry.is_file() or not os.access(entry, os.X_OK):
            raise ValueError(f"agent entrypoint is not an executable file: {entry}")
        entries[entry.name] = str(entry.resolve(strict=True))
    return entries


def retained_agent_bin(doc: dict) -> tuple[str, bool]:
    binding = doc.get("agent_bin")
    if binding is None:
        return "", False
    if (not isinstance(binding, dict) or not isinstance(binding.get("directory"), str)
            or not isinstance(binding.get("entries"), dict)):
        raise ValueError("invalid retained agent entrypoints")
    directory, recorded = binding["directory"], binding["entries"]
    entries = agent_entrypoints(directory)
    if entries != recorded:
        # Name each difference: the operator restores entries, not a directory.
        changes = [f"added {name}" for name in sorted(entries.keys() - recorded.keys())]
        changes += [f"removed {name}" for name in sorted(recorded.keys() - entries.keys())]
        changes += [f"retargeted {name}" for name in sorted(entries.keys() & recorded.keys())
                    if entries[name] != recorded[name]]
        raise ValueError(
            f"retained agent entrypoints changed in {directory}: {', '.join(changes)}")
    return directory, "claude" in entries


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


def resource_home() -> Path:
    """Select doctrine only; executable helpers and Python imports stay with this checkout."""
    value = os.environ.get("FACTORY_HOME", str(Path(__file__).resolve().parent.parent))
    if not value:
        raise ValueError("FACTORY_HOME is empty; unset it or select a complete resource directory")
    home = Path(value).resolve()
    required = ("prompts/validate.md", "prompts/orchestrate.md", "prompts/test.md",
                "docs/SOFTWARE-FACTORY.md", "docs/standards/TESTING.md")
    missing = [name for name in required if not (home / name).is_file()]
    if missing:
        raise ValueError(f"FACTORY_HOME {home} is missing required resources: {', '.join(missing)}")
    return home


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

    resource_home()  # Refuse invalid resource selection before creating any run state.
    root = run_root(runs, run)
    repository = repository.resolve(strict=True)
    agent_bin = os.environ.get("FACTORY_AGENT_BIN", "")
    agent_binding = None
    if agent_bin:
        if not Path(agent_bin).is_absolute():
            raise ValueError(f"FACTORY_AGENT_BIN must name one absolute directory: {agent_bin}")
        # Not strict: a missing directory is reported by name below, not as a bare OSError.
        directory = str(Path(agent_bin).resolve())
        entries = agent_entrypoints(directory)
        if not entries:
            raise ValueError(f"FACTORY_AGENT_BIN contains no agent entrypoints: {directory}")
        for candidate in (directory, *entries.values()):
            path = Path(candidate)
            if path.is_relative_to(repository) or path.is_relative_to(root.parent):
                raise ValueError(f"agent entrypoint must be outside the target and runs: {path}")
        agent_binding = {"directory": directory, "entries": entries}
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
    import importlib

    from factory_runtime.version import build_identity

    settings = (importlib.import_module("harness.model_settings") if __package__
                else importlib.import_module("model_settings"))

    # Freeze the namespace before starting a server so cleanup can prove absence
    # even if the caller later changes TMUX_TMPDIR.
    socket_dir = Path(os.environ.get("TMUX_TMPDIR") or "/tmp").resolve()
    record = dict(
        factory_version=build_identity(),
        system_one_profile=settings.system_one_profile(),
        schema_version=SCHEMA,
        run_id=run,
        repository=str(repository),
        resolved_commit=commit,
        resolved_tree=tree,
        task_digest="sha256:" + hashlib.sha256(task).hexdigest(),
        budget_usd=budget,
        log=log,
        tmux_tmpdir=str(socket_dir),
        tmux_socket="factory-" + hashlib.sha256(str(root).encode()).hexdigest()[:20],
        created_at=dt.datetime.now(dt.UTC).isoformat(),
    )
    if agent_binding is not None:
        record["agent_bin"] = agent_binding
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
    if "tmux_tmpdir" in doc:
        directory = doc["tmux_tmpdir"]
        if not isinstance(directory, str) or not Path(directory).is_absolute() or "\0" in directory:
            raise ValueError("invalid retained tmux directory")
    task = root / "TASK.md"
    if (
        task.is_symlink()
        or "sha256:" + hashlib.sha256(task.read_bytes()).hexdigest() != doc["task_digest"]
    ):
        raise ValueError("retained task changed")
    if not Path(doc["repository"]).is_absolute() or not Path(doc["repository"]).is_dir():
        raise ValueError("recorded repository is unavailable")
    return root, doc


def builder_attribution(doc: dict) -> dict:
    """Render only the retained builder identity; never consult the current installation."""
    identity = doc.get("factory_version")
    identity = dict(identity) if isinstance(identity, dict) else {}
    if doc.get("schema_version") == SCHEMA and identity:
        identity.setdefault("name", "Factory")  # Name implicit in older Factory run records.
    name, version = identity.get("name"), identity.get("version")
    known = all(isinstance(value, str)
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._+-]{0,127}", value)
                for value in (name, version))
    builder = f"{name} v{version}" if known else None
    trailers = f"FactoryRun: {doc['run_id']}"
    if builder:
        trailers += f"\nBuilt-By: {builder}"
    return dict(identity=identity, commit_trailers=trailers,
                pr_line=f"PR built by {builder}" if builder else None)


def check_builder(root: Path, doc: dict, repository: Path, commit: str,
                  *, pr: dict | None = None) -> dict:
    """Check actual task commit messages and, when supplied, a fetched PR snapshot.

    Consistency with a retained local record is not cryptographic authorship proof.
    Projection/target bases are excluded; later imported history needs attribution review.
    """
    from factory_runtime.host_git import run_host_git

    exact = run_host_git(repository, ["rev-parse", "--verify", "--end-of-options",
                                     f"{commit}^{{commit}}"]).decode().strip()
    expected = builder_attribution(doc)
    result: dict[str, Any] = dict(status="unknown", expected=expected, commit=exact,
                                  commits=[], findings=[],
                                  repository=str(repository.resolve()), pr=pr,
                                  commit_trailers={}, pr_lines=[], pr_verification="not-checked")
    if not expected["pr_line"]:
        result["findings"].append("Retained builder identity/version is unknown.")
        return result
    bases = [doc["resolved_commit"]]
    if (root / "inputs").is_symlink():
        raise ValueError("input receipts directory is a symlink")
    for path in (root / "inputs").glob("*.json"):
        receipt = read_json(path)
        if receipt.get("source_commit") == doc["resolved_commit"]:
            bases.append(receipt["initial_commit"])
    ancestors = []
    for base in bases:
        if not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{40,64}", base):
            raise ValueError("invalid retained provenance base")
        try:
            run_host_git(repository, ["merge-base", "--is-ancestor", base, exact])
        except RuntimeError:
            continue  # Another author projection need not exist in this object database.
        ancestors.append(base)
    if not ancestors:
        result["findings"].append("Candidate has no retained task/projection base; scope unknown.")
        return result
    commits = run_host_git(repository, ["rev-list", exact, "--not", *ancestors]).decode().split()
    result["commits"] = commits
    for oid in commits:
        body = run_host_git(repository, ["show", "-s", "--format=%B", oid]).decode()
        # Read canonical raw trailers. Repository trailer aliases/config must not rename
        # another field into Built-By, or promote body prose into attribution.
        trailers = body.rstrip().rsplit("\n\n", 1)[-1].splitlines()
        canonical_block = all(re.fullmatch(r"[A-Za-z0-9-]+: .*", line) for line in trailers)
        actual = ([line for line in trailers
                   if line.lower().startswith(("factoryrun:", "built-by:"))]
                  if canonical_block else [])
        result["commit_trailers"][oid] = actual
        if sorted(actual) != sorted(expected["commit_trailers"].splitlines()):
            result["findings"].append(
                f"Commit {oid} needs attribution review: missing or conflicting builder trailers.")
    if pr is not None:
        if pr.get("headRefOid") != exact:
            result["findings"].append("PR head differs from the checked candidate.")
        lines = pr.get("body", "").splitlines()
        stamps = [line for line in lines
                  if line.lower().startswith(("pr built by ", "factoryrun:"))]
        result.update(pr_lines=stamps, pr_verification="checked")
        if sorted(stamps) != sorted([expected["pr_line"], f"FactoryRun: {doc['run_id']}"]):
            result["findings"].append("PR body has missing or conflicting builder attribution.")
    result["status"] = "mismatch" if result["findings"] else "consistent"
    return result


def github_command(argv: list[str]) -> CompletedProcess[str]:
    """Keep gh's transitive Git reads outside candidate repositories and configuration."""
    import subprocess
    import tempfile

    from factory_runtime.kindex_workspace import mark_no_index

    env = {key: os.environ[key] for key in (
        "HOME", "USER", "PATH", "LANG", "LC_ALL", "TMPDIR", "XDG_CONFIG_HOME", "GH_CONFIG_DIR",
        "GH_TOKEN", "GITHUB_TOKEN",
    ) if key in os.environ}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GH_HOST="github.com")
    with tempfile.TemporaryDirectory(prefix="factory-github-") as temporary:
        mark_no_index(Path(temporary))
        return subprocess.run(["gh", *argv], cwd=temporary, env=env, capture_output=True,
                              text=True, timeout=60)


def fetch_pr(url: str) -> dict:
    """Read GitHub's current body/head, without changing the PR or invoking a shell."""
    import subprocess

    if not re.fullmatch(r"https://github\.com/[^/]+/[^/]+/pull/[0-9]+", url):
        raise ValueError("--pr requires a GitHub pull request URL")
    try:
        fetched = github_command(["pr", "view", url, "--json", "url,body,headRefOid"])
        fetched.check_returncode()
    except (subprocess.SubprocessError, OSError) as error:
        raise ValueError("could not read PR metadata; no provenance verdict retained") from error
    pr = json.loads(fetched.stdout)
    if (not isinstance(pr, dict) or pr.get("url") != url
            or not isinstance(pr.get("body"), str) or not isinstance(pr.get("headRefOid"), str)):
        raise ValueError("unexpected PR metadata; no provenance verdict retained")
    return pr


def profile_for_run(doc: dict) -> bool:
    """New runs retain consent; old records keep their explicit invocation behavior."""
    value = doc.get("system_one_profile", os.environ.get("FACTORY_SYSTEM_ONE_PROFILE") == "1")
    if not isinstance(value, bool):
        raise ValueError("invalid retained System One profile permission")
    return value


def system_one_review(report: dict, root: Path | None = None) -> dict:
    import importlib

    screen = (importlib.import_module("harness.jev_screen") if __package__
              else importlib.import_module("jev_screen"))
    if root is None:
        return screen.builder_review(report)
    doc = read_json(root / "harness.json")
    return screen.builder_review(
        report, call=lambda payload: screen.request(payload, profile=profile_for_run(doc)))


def retain_provenance(root: Path, report: dict) -> Path:
    import uuid

    directory = root / "provenance"
    if directory.is_symlink():
        raise ValueError("provenance directory is a symlink")
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / f"{uuid.uuid4().hex}.json"
    report.setdefault("pr_verification", "not-checked")
    report["system_one"] = system_one_review(report, root)
    report = dict(report, observed_at=dt.datetime.now(dt.UTC).isoformat())
    temporary = path.with_suffix(".tmp")
    try:
        write_new(temporary, (json.dumps(report, indent=2) + "\n").encode())
        os.link(temporary, path, follow_symlinks=False)  # Publish complete bytes, never overwrite.
    finally:
        temporary.unlink(missing_ok=True)
    message(root, "orchestrator", "host",
            f"Builder provenance {report['status']} for {report['commit']}: {path}. "
            f"PR verification: {report['pr_verification']}. "
            f"System One: {report['system_one']['status']} "
            f"({report['system_one'].get('reason', 'see retained observation')}). "
            f"Findings: {json.dumps(report['findings'])}. This checks retained identity "
            "consistency, not product acceptance or cryptographic authorship. "
            "Read the report and send the Validator a concrete assessment citing this path, "
            "uncovered claims and the next action (or why no correction is needed), using "
            "run_record.py message. Delivery alone is not a completed review.")
    return path


def publish_pr(runs: Path, run: str, repository: Path, commit: str, operation: str, *,
               body_file: Path, title: str | None = None, pr: str | None = None,
               head: str | None = None, base: str | None = None, draft: bool = False,
               github_repo: str | None = None) -> dict:
    """Perform one authorized PR mutation, then inspect GitHub's actual result.

    Publication and verification are separate outcomes. Never retry a mutation because its
    subsequent read failed; the retained inbox tells the Orchestrator what remains unknown.
    """
    import subprocess

    from factory_runtime.host_git import run_host_git

    root, doc = load(runs, run)
    actual = run_host_git(repository, ["rev-parse", "--verify", "HEAD^{commit}"]).decode().strip()
    if commit != actual:
        raise ValueError("PR operation requires the exact current candidate commit")
    if operation not in {"create", "edit"}:
        raise ValueError("PR operation must be create or edit")
    argv = ["pr", operation]
    if operation == "create":
        if (not head or not title or not github_repo
                or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", github_repo)):
            raise ValueError("PR create requires --head, --title and --github-repo OWNER/REPO")
        argv += [f"--head={head}", f"--repo=github.com/{github_repo}", "--label=Factory"]
        if base:
            argv += [f"--base={base}"]
        if draft:
            argv.append("--draft")
    else:
        if head or base or draft:
            raise ValueError("--head, --base and --draft apply only to PR create")
        if not pr or not re.fullmatch(r"https://github\.com/[^/]+/[^/]+/pull/[0-9]+", pr):
            raise ValueError("PR edit requires a GitHub PR URL")
        argv.append(pr)
    if title is not None:
        argv += [f"--title={title}"]
    argv += [f"--body-file={body_file.resolve(strict=True)}"]
    outcome = dict(operation=operation, mutation="uncertain", verification="unavailable", pr=pr)
    try:
        completed = github_command(argv)
        if completed.returncode:
            outcome["reason"] = "GitHub command failed; inspect remote state before retrying."
        else:
            outcome["mutation"] = "completed"
            url = pr if operation == "edit" else completed.stdout.strip()
            outcome["pr"] = url
            snapshot = fetch_pr(url or "")
            report = check_builder(root, doc, repository, commit, pr=snapshot)
            evidence = retain_provenance(root, report)
            return dict(outcome, verification=report["status"], evidence=str(evidence))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        outcome["reason"] = (
            "PR mutation completed, but verification is unavailable; recheck the PR, "
            "do not repeat the mutation." if outcome["mutation"] == "completed" else
            "PR mutation outcome uncertain; inspect GitHub before retrying.")
    message(root, "orchestrator", "host", json.dumps(outcome) +
            " Review the actual remote state and report the remaining gap to the Validator.")
    return outcome


def context(runs: Path, run: str, *, with_agent_bin: bool = False) -> None:
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
        doc.get("tmux_tmpdir", ""),
    ]
    if with_agent_bin:
        agent_bin, custom_claude = retained_agent_bin(doc)
        fields.extend((agent_bin, "claude" if custom_claude else ""))
    for value in fields:
        if not isinstance(value, str) or "\0" in value:
            raise ValueError("invalid context field")
        sys.stdout.buffer.write(value.encode() + b"\0")


#: How long a supervisor (a lane watchdog) waits for another writer's lock on an inbox; then it
#: gives up and retries at its next check rather than block its supervision.
SUPERVISOR_WAIT_S = 5.0


def append_record(path: Path, payload: bytes, *, wait_s: float | None = None,
                  once: Callable[[bytes], bool] | None = None, since: int | None = None) -> bool:
    """Serialize appends; retain a killed writer's fragment separately from later rows.

    With ``wait_s``, a writer that cannot take the lock within that long raises instead of
    waiting on whoever holds it: a supervisor must not block on another writer.

    With ``once``, a writer that may already have written this row (a retry) decides under the
    lock, so two retrying at once write it once: ``once`` says whether rows read from ``since``
    (or the last 64 KiB) hold it. When the complete rows do, nothing is written. When the
    unterminated tail is it whole (a writer killed before its newline), only the newline is
    written, which completes that row. Returns whether anything was written.
    """
    fd = os.open(
        path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
    )
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"message sink is not a regular file: {path}")
        _flock(fd, fcntl.LOCK_EX, wait_s, "message sink", path)
        start = os.fstat(fd).st_size
        interrupted = start and os.pread(fd, 1, start - 1) != b"\n"
        if once is not None:
            begin = (_row_start(fd, since) if since is not None and 0 <= since <= start
                     else max(0, start - 65536))
            complete, _, tail = os.pread(fd, start - begin, begin).rpartition(b"\n")
            if once(complete):
                return False
            if interrupted and once(tail):
                payload, interrupted = b"\n", False
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
    return True


def _flock(fd: int, operation: int, wait_s: float | None, what: str, path: object) -> None:
    """Take ``operation`` (``LOCK_EX`` or ``LOCK_SH``) on ``fd``, waiting on whoever holds the
    lock; with ``wait_s``, at most that long, then raising OSError."""
    if wait_s is None:
        fcntl.flock(fd, operation)
        return
    deadline = time.monotonic() + wait_s
    while True:
        try:
            fcntl.flock(fd, operation | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise OSError(f"{what} stayed locked for {wait_s:g} s: {path}") from None
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


#: Who may write to an inbox.
SENDERS = frozenset({"validator", "orchestrator", "coder", "tester", "host", "dispatcher",
                     "founder", "seat-supervisor"})


def message(root: Path, recipient: str, sender: str, text: str, *, log: str = os.devnull,
            wait_s: float | None = None, once_since: int | None = None) -> None:
    """Append ``text`` to the recipient's inbox (and, optionally, ``log``).

    With ``once_since`` (an ``inbox_end`` taken before a first attempt), a retry: the message is
    written only if the inbox does not already hold it from there, decided under the inbox's
    lock (``append_record``), and the log gets nothing when it is not written.
    """
    if recipient not in {"validator", "orchestrator"}:
        raise ValueError("author messages use tmux_lane_message.sh to preserve role boundaries")
    if sender not in SENDERS:
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
    if not append_record(
            directory / "inbox.jsonl", payload, wait_s=wait_s, since=once_since,
            once=None if once_since is None else (lambda rows: _holds(rows, sender, text))):
        return  # already in the inbox: nothing was written, so nothing to log
    if log != os.devnull:
        try:
            append_record(Path(log), payload, wait_s=wait_s)
        except OSError as error:
            print(f"factory: message queued; optional log failed: {error}", file=sys.stderr)


def _inbox_directory(root: Path, recipient: str) -> int:
    """The recipient's directory, opened only where ``message`` writes: not through a linked
    run directory or a linked recipient directory. Raises OSError otherwise."""
    if root.is_symlink():
        raise OSError(f"{root} is a symlink")
    return os.open(root / recipient, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def inbox_end(root: Path, recipient: str, *, wait_s: float | None = None) -> int:
    """The recipient's inbox size, read under the inbox's lock: a message written after this
    is at or past it.

    A writer holds the lock from its append to its sync or its rollback (``append_record``), so
    a size read without it could count a row that is then rolled back, and a message written
    next would start before the offset, or wholly before it. Under the lock the size is where
    committed rows end (or a killed writer's unterminated row). With ``wait_s``, a reader that
    cannot take the lock within that long raises OSError, as a writer does.

    An inbox that is not a regular file in a real directory (missing, a link, a folder, or
    under a linked directory) is 0: a message can be written only once it is one, and then
    from its start.
    """
    try:
        directory = _inbox_directory(root, recipient)
    except OSError:
        return 0
    try:
        fd = os.open("inbox.jsonl", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory)
    except OSError:
        return 0
    finally:
        os.close(directory)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return 0
        _flock(fd, fcntl.LOCK_SH, wait_s, "inbox", root / recipient / "inbox.jsonl")
        return os.fstat(fd).st_size
    finally:
        os.close(fd)


def _row_start(fd: int, offset: int) -> int:
    """Where the row holding byte ``offset`` starts: ``offset`` itself at a row's start.

    ``inbox_end`` takes an offset under the inbox's lock: a row's start, or the end of a row a
    killed writer left unterminated. One saved before it did (a watcher's state from an earlier
    version) can count another writer's append that was then rolled back, so the row written
    next starts before it. A read from the offset starts at that row."""
    position = offset
    while position > 0:
        size = min(4096, position)
        newline = os.pread(fd, size, position - size).rfind(b"\n")
        if newline >= 0:
            return position - size + newline + 1
        position -= size
    return 0


def _holds(rows: bytes, sender: str, text: str, digest: str | None = None) -> bool:
    """Whether ``rows`` hold this message from ``sender``."""
    digest = digest or hashlib.sha256(text.encode()).hexdigest()
    for line in rows.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if (isinstance(row, dict) and row.get("message_digest") == digest
                and row.get("sender") == sender and row.get("text") == text):
            return True
    return False


#: The Kindex tools a coordinator calls (observed on Antigravity seats in two runs, each asking
#: a person every time): reads and its own session tag. Other writes still ask.
AGY_SEAT_KINDEX_TOOLS = ("search", "show", "context", "list_nodes", "watch_list",
                         "tag_start", "tag_update", "tag_resume")
#: The harness programs a coordinator runs (prompts/orchestrate.md, the dispatcher loop and
#: inbox_wake stop in the start prompt, status.sh in the field runs).
AGY_SEAT_SCRIPTS = ("dispatcher.py", "run_record.py", "inbox_wake.py", "tmux_lane_message.sh",
                    "status.sh")


def agy_seat_settings(repository: str, root: Path, tools: Path, python: str) -> str:
    """Antigravity's own per-session settings (`--settings`, never saved) for a coordinator
    seat, so its dispatcher loop (which is how it reads its inbox) runs with nobody at the pane.

    Its terminal sandbox cannot start this runtime's Python ("operation not permitted"), and
    Auto mode runs an allowed command inside it, so the seat runs in accept-edits mode with the
    sandbox off (resident_command): exactly the commands listed here run without asking, as a
    person approved them in the field runs; every other command asks. It trusts the run's
    folders and reads outside the workspace (Factory's prompts, the lane clones it reviews)."""

    allow = [f"mcp(kindex/{tool})" for tool in AGY_SEAT_KINDEX_TOOLS] + ["command(ls)"]
    for script in AGY_SEAT_SCRIPTS:
        path = tools / script
        allow += ([f"command(bash {path})", f"command({path})"] if script.endswith(".sh")
                  else [f"command({python} {path})"])
    return json.dumps({"trustedWorkspaces": [repository, str(root)],
                       "allowNonWorkspaceAccess": True, "enableTerminalSandbox": False,
                       "permissions": {"allow": allow}})


def resident_command(agent: str, model: str, effort: str, prompt: str, root: Path,
                     settings: str = "") -> list[str]:
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
                *(["--mode", "accept-edits", "--settings", settings]
                  if agent == "agy" and settings else []),
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
# Certificate-trust paths, not secrets; harness/run_context.sh lists the same for lanes.
CA_ENV = ("NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
          "CURL_CA_BUNDLE", "NIX_SSL_CERT_FILE")
def seat_status(root: Path, role: str) -> dict[str, Any]:
    """What a coordinator seat's native session has itself reported, and nothing inferred.

    ``phase`` names the session's last report: ``starting`` until it reports that it started
    (it may be showing the operator a folder-trust or sign-in question), then ``started``,
    ``prompt-observed`` and ``stop-reported``. The operator's own hooks may reject a prompt or
    continue past a stop, so neither report is called acceptance or completion. A delivery is
    ``queued``, ``submitted``, ``observed`` or ``unknown`` (``persistent_lane.OUTSTANDING``);
    ``observed`` means the session reported receiving it, and nothing stronger is known.
    """
    import importlib

    if not (root / f"{role}-persistent.json").exists():
        return {"native": False,
                "detail": "this seat has no resident native session record"}
    lane = (importlib.import_module("harness.persistent_lane") if __package__
            else importlib.import_module("persistent_lane"))
    try:
        seen = lane.inspect(root, role)
    except (OSError, ValueError, KeyError) as error:
        return {"native": True, "process_state": "unavailable", "detail": str(error)}
    return {
        "native": True,
        "transport": seen.get("transport", "stream"),
        "launcher": seen.get("launcher"),
        "session_id": seen.get("session_id"),
        "session_started": seen.get("session_started"),
        "phase": seen.get("phase"),
        "last_native_event": seen.get("last_native_event"),
        "prompt_reports": seen.get("prompt_reports"),
        "stop_reports": seen.get("stop_reports", seen.get("turns")),
        "process_state": seen.get("process_state"),
        "worker": seen.get("worker"),
        "deliveries": {key: value.get("state")
                       for key, value in (seen.get("deliveries") or {}).items()},
    }


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
    resources = resource_home()
    agent_bin, custom_claude = retained_agent_bin(doc)
    agent_path = (
        os.pathsep.join((agent_bin, os.environ.get("PATH", os.defpath))) if agent_bin else None
    )
    selector = os.environ.get("FACTORY_TMUX") or "tmux"
    executable = shutil.which(selector)
    if executable is None:
        raise ValueError(f"FACTORY_TMUX executable is unavailable: {selector}")
    # Panes change cwd to the target repository; keep the executable selected here.
    tmux = [str(Path(executable).absolute()), "-L", doc["tmux_socket"]]
    socket_directory = doc.get("tmux_tmpdir")
    tmux_env = {**os.environ, "TMUX_TMPDIR": socket_directory} if socket_directory else None
    socket_env = ["env", f"TMUX_TMPDIR={socket_directory}"] if socket_directory else []
    retry = shlex.join([sys.executable, str(tools / "run_record.py"), "start",
                        "--runs", str(root.parent), "--run", run])
    if subprocess.run([*tmux, "has-session", "-t", f"={run}"],
                      capture_output=True, env=tmux_env).returncode == 0:
        raise ValueError("run session already exists; existing sessions are never replaced")
    # A seat's earlier life may still be stopping although its tmux session is gone: a wrapper
    # that was signalled waits for its program and writes its exit record last. Below, a seat
    # is said to have stopped during startup when its exits journal grows, and that journal is
    # one file for every life of the seat, its rows naming no process. So the earlier life is
    # waited for first, by the exact processes its receipt names (PID and start time), and
    # for a bounded time; if it is still stopping, nothing is started. It is never signalled.
    lane_watchdog = (importlib.import_module("harness.lane_watchdog") if __package__
                     else importlib.import_module("lane_watchdog"))
    for role in ("validator", "orchestrator"):
        receipt = root / f"{role}-process.json"
        earlier = lane_watchdog.await_previous_owner(receipt)
        if earlier is None:
            raise ValueError(
                f"cannot tell whether the previous {role} has stopped ({receipt.name} or the "
                f"process table could not be read); nothing was started. Retry with: {retry}")
        if earlier:
            named = ", ".join(f"{row['role']} pid {row['pid']}" for row in earlier)
            raise ValueError(
                f"the previous {role} is still stopping ({named}) after "
                f"{lane_watchdog.PREVIOUS_OWNER_WAIT_S:g}s; nothing was started and it was not "
                f"signalled. Retry once it has ended, with: {retry}")
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
            f"Act as Factory {role}. Read {resources}/prompts/{directive}.md, "
            f"{resources}/docs/SOFTWARE-FACTORY.md and {root}/TASK.md. "
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
        # Old runs retain their exact initial prompt; upgrading must not break resume.
        if isinstance(doc.get("factory_version"), dict) and doc["factory_version"].get("name"):
            prompt += f" Retained builder attribution: {json.dumps(builder_attribution(doc))}."
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
        # Claude Code's own files are never read or written here. A folder it has not seen asks
        # its trust question in the seat's window, for the operator to answer; until the session
        # reports that it started, the seat's status is "starting" and nothing is typed into it.
        # Only Claude Code is hosted for a seat by the lane wrapper. An Antigravity seat is
        # its own interactive session in its window. Nothing is typed into it: it reads its
        # inbox through its own dispatcher loop, which its per-session settings allow.
        native_stream = agent == "claude"
        if agent == "agy":
            print(f"Factory {run}: the {role} seat is Antigravity's own interactive session. "
                  "It reads its inbox through its own dispatcher loop; its harness commands "
                  "and read-only Kindex tools are allowed for this session only.")
        if native_stream:
            extra = ["--add-dir", str(root)]
            if os.environ.get("FACTORY_LANE_SANDBOX", "on") == "off":
                # FACTORY_LANE_SANDBOX=off selects the permissive mode, as the native flag.
                extra.append("--dangerously-skip-permissions")
            # A flag after --add-dir terminates its variadic arguments in both CLIs.
            # A retained session id means this start is a retry: resume that conversation, or the
            # lane refuses the new one as "native conversation changed".
            session = root / f"{role}-session-id"
            resume = session.read_text().strip() if session.is_file() else ""
            built = agents.harness(agent).persistent_turn(
                model=model, effort=effort, cwd=Path(doc['repository']), resume=resume,
                extra=extra)
            argv = list(built.argv)
            # Claude Code gets no permission mode from here: neither "default" forced over
            # the configured one, nor a bypass. FACTORY_LANE_SANDBOX=off is the configuration
            # that adds its native permissive flag (above).
            prompt_file = directory / "initial-prompt.txt"
            if not prompt_file.exists():
                write_new(prompt_file, prompt.encode())
            elif prompt_file.read_text() != prompt:
                raise ValueError("retained initial coordinator prompt changed")
        else:
            argv = resident_command(agent, model, effort, prompt, root, settings=(
                agy_seat_settings(doc["repository"], root, tools, sys.executable)
                if agent == "agy" else ""))
        if agent_bin and argv[0] in doc["agent_bin"]["entries"]:
            executable = str(Path(agent_bin) / argv[0])
        else:
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
                env={**os.environ, "PATH": agent_path} if agent_path is not None else None,
            )
            if available.returncode:
                raise ValueError(available.stderr.strip() or "Ollama model is unavailable")
        env = {
            key: os.environ[key]
            # TMUX_TMPDIR places the run's tmux socket; a seat without it reaches another server.
            # CLAUDE_CONFIG_DIR and CODEX_HOME are the operator's agent profiles, and
            # FACTORY_CLAUDE_LAUNCHER the command they start Claude Code with.
            for key in ("HOME", "USER", "PATH", "TMPDIR", "TMUX_TMPDIR", "TERM", "SHELL", "LANG",
                        "CODEX_HOME", "CLAUDE_CONFIG_DIR", "FACTORY_CLAUDE_LAUNCHER")
            if key in os.environ
        }
        # Certificate-trust paths (e.g. a TLS-inspecting network's CA bundle), opt-in only.
        if os.environ.get("FACTORY_PASS_CA_CERTS") == "1":
            env["FACTORY_PASS_CA_CERTS"] = "1"
            env.update({key: os.environ[key] for key in CA_ENV if os.environ.get(key)})
        if agent_path is not None:
            env["PATH"] = agent_path
        if custom_claude:
            env["FACTORY_CLAUDE_LAUNCHER"] = "claude"
        if socket_directory:
            env["TMUX_TMPDIR"] = socket_directory
        # The Validator opens authorized pull requests; gh auth held only in the environment
        # reaches it. The Orchestrator and the authors keep no GitHub credential from here.
        if role == "validator":
            env.update({key: os.environ[key] for key in GITHUB_ENV if key in os.environ})
        # Permission to source the profile reaches the trusted HTTP child; no key reaches seats.
        if profile_for_run(doc):
            env["FACTORY_SYSTEM_ONE_PROFILE"] = "1"
        # Reuse the adapter's credential declarations. Plan mode never inherits API keys.
        adapter = agents.harness("codex" if agent == "ollama" else agent)
        for key in adapter.api_env:
            env.pop(key, None)
        env.update(
            TERM=(env["TERM"] if env.get("TERM") not in {None, "", "dumb"} else "xterm-256color"),
            FACTORY_HOME=str(resources),
            FACTORY_TMUX=tmux[0],
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
        pipe = shlex.join([*socket_env, *tmux, "pipe-pane", "-O", "-t"])
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
                [*tmux, *args, "-n", role, "-c", doc["repository"], shell_command],
                check=True, env=tmux_env
            )
            created = True
        # A window can outlive its seat: the wrapper stops an agent whose process it cannot
        # record. Each seat signals once it registers or ends, and its exits journal says which.
        for role, channel, before, _ in commands:
            try:
                waited = subprocess.run([*tmux, "wait-for", channel], capture_output=True,
                                        text=True, timeout=SEAT_REGISTRATION_SECONDS, env=tmux_env)
            except subprocess.TimeoutExpired:
                raise SeatStartFailed(
                    f"the {role} did not register within {SEAT_REGISTRATION_SECONDS} seconds; "
                    f"{last_lane_line(root / f'{role}-lane.log')}"
                ) from None
            if seat := stopped(role, before):
                raise SeatStartFailed(seat)
            if waited.returncode:
                raise SeatStartFailed(f"the {role} did not register: {waited.stderr.strip()}")
        if any(agent == "claude" for agent in selected.values()):
            wake = shlex.join([*(socket_env or ["env"]), f"FACTORY_TMUX={tmux[0]}",
                               sys.executable, str(tools / "inbox_wake.py"),
                               "run", "--root", str(root)])
            subprocess.run([*tmux, "new-window", "-d", "-t", f"={run}",
                            "-n", "inbox-wake", "-c", doc['repository'], wake],
                           check=True, env=tmux_env)
        # Native inbox transport only; no supervisor may stop work for review lag.
        attach = shlex.join([*socket_env, *tmux, "attach", "-t", run])
        print(f"Factory {run}: {attach}")
        if selected["validator"] == "kickoff":
            print(f"Existing Validator: read {root}/validator/BRIEF.md")
    except BaseException as error:
        if created:
            subprocess.run([*tmux, "kill-session", "-t", f"={run}"],
                           capture_output=True, env=tmux_env)
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
                                           "notify", "capture", "provenance", "pr"])
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--agent-entrypoints", action="store_true")
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--task-file", type=Path)
    parser.add_argument("--commit", help="exact candidate or revision to check")
    parser.add_argument("--pr", help="GitHub PR URL to inspect, or edit with pr --operation edit")
    parser.add_argument("--operation", choices=["create", "edit"])
    parser.add_argument("--body-file", type=Path)
    parser.add_argument("--title")
    parser.add_argument("--head")
    parser.add_argument("--github-repo", help="explicit OWNER/REPO for authorized PR creation")
    parser.add_argument("--base")
    parser.add_argument("--draft", action="store_true")
    parser.add_argument("--budget", type=float)
    parser.add_argument("--log", default=os.environ.get("FACTORY_LOG", os.devnull))
    parser.add_argument("--to", choices=["validator", "orchestrator"])
    parser.add_argument("--slot", help="author slot for terminal output capture")
    parser.add_argument("--from", dest="sender", default="host")
    parser.add_argument("--message-file", type=Path)
    parser.add_argument("--kind", help=(
        "notify: the notice's kind (validator_input.KINDS). Exits 0 delivered, 75 recorded and "
        "still owed, 74 recorded but delivery unknown, 65 id already used for another notice "
        "(not sent), 70 not recorded (a record stayed locked or failed; retry)"))
    parser.add_argument("--id", dest="notice_id", help="notify: the notice's id, told once")
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
            context(args.runs, args.run, with_agent_bin=args.agent_entrypoints)
        else:
            root, doc = load(args.runs, args.run)
            if args.action == "pr":
                if not all((args.repo, args.commit, args.operation, args.body_file)):
                    parser.error("pr requires --repo, --commit, --operation and --body-file")
                outcome = publish_pr(args.runs, args.run, args.repo, args.commit, args.operation,
                                     body_file=args.body_file, title=args.title, pr=args.pr,
                                     head=args.head, base=args.base, draft=args.draft,
                                     github_repo=args.github_repo)
                print(json.dumps(outcome, indent=2))
                return 0 if outcome["mutation"] == "completed" else 1
            elif args.action == "provenance":
                if not args.commit:
                    if args.pr:
                        parser.error("--pr requires --commit")
                    print(json.dumps(builder_attribution(doc), indent=2))
                else:
                    pr = fetch_pr(args.pr) if args.pr else None
                    report = check_builder(root, doc, args.repo or Path(doc["repository"]),
                                           args.commit, pr=pr)
                    path = retain_provenance(root, report)
                    print(json.dumps(dict(report, evidence=str(path)), indent=2))
                    return 0 if report["status"] == "consistent" else 1
            elif args.action == "capture":
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
            elif args.action == "notify":
                if not (args.kind and args.notice_id and args.message_file):
                    parser.error("notify requires --kind, --id and --message-file")
                import validator_input

                try:
                    told = validator_input.notify(root, args.notice_id, args.kind, args.sender,
                                                  args.message_file.read_text(),
                                                  wait_s=SUPERVISOR_WAIT_S)
                except validator_input.LedgerUnreadable as error:
                    print(f"factory: {args.notice_id} is recorded, but whether it was delivered "
                          f"is unknown: {error}", file=sys.stderr)
                    return 74
                except validator_input.NoticeIdReused as error:
                    print(f"factory: {error}", file=sys.stderr)
                    return 65
                if not told:
                    print(f"factory: {args.notice_id} is recorded and still owed to the "
                          "Validator; the next delivery retries it", file=sys.stderr)
                    return 75
                print(f"factory: {args.notice_id} delivered to the Validator's inbox")
            else:
                doc["validation"] = [
                    read_json(p) for p in sorted(root.glob("validation/*/result.json"))
                ]
                doc["seats"] = {role: seat_status(root, role)
                                for role in ("validator", "orchestrator")}
                print(json.dumps(doc, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        print(f"factory: {error}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
