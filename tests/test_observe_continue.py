"""Normative: the resident Orchestrator observes with one fixed, repeatable command.

Source: the source-free `--observe --continue` contract supplied for this change on
2026-11-17 (items 1-6). Background: an Antigravity Orchestrator needed a human approval for
every `dispatcher.py --observe --after CURSORS_JSON` call, because the command line changed
on every call, so its host's "always allow this command" never matched.

Oracles. Agreement: a `--continue` sequence must print what the existing `--after` chain
prints over the same activity (TESTING T1, source 5). Law: paging delivers each byte appended
to each activity source exactly once, in order, and nothing that is not activity. Expected
file states (mode 0600, bytes unchanged after a refusal) come straight from the contract.

Fidelity: the real `harness/dispatcher.py` runs as a script against a real run record from
`run_record.create`, with real files as activity. No doubles. Subprocesses get a declared
environment (synthetic HOME, system PATH, no provider keys), so System One reports itself
unavailable and no provider is called. Mask: the `system_one` block is excluded from the
agreement comparisons; it is an advisory batch with its own replay cache, checked by
test_ordinary_system_one, and it is not part of the cursor contract.

The start-prompt test reuses the existing coordinator startup fixture (which records tmux
calls instead of starting sessions) and then runs the extracted command for real.
"""

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from harness import run_record
from tests.test_coordinator_startup import launch as launch

REPO = Path(__file__).resolve().parents[1]
DISPATCHER = REPO / "harness" / "dispatcher.py"
KEYS = ("TYPESAFE_API_KEY", "JMC_JEV_API_KEY", "VOYAGE_API_KEY")
CODER = "tmux-lanes/coder-lane.log"
TESTER = "tmux-lanes/tester-lane.log"
VALIDATOR = "validator-lane.log"
INBOX = "orchestrator/inbox.jsonl"


class Run:
    """A real run record and the observer command line pointed at it."""

    def __init__(self, root: Path, env: dict[str, str]):
        self.root = root
        self.env = env
        self.cursor_file = root / "orchestrator" / "observe-cursors.json"

    def argv(self, *flags: str, wait: str = "0") -> list[str]:
        return [sys.executable, str(DISPATCHER), "--run", self.root.name,
                "--root", str(self.root), "--observe", "--wait-seconds", wait, *flags]

    def call(self, *flags: str, wait: str = "0", stdout=subprocess.PIPE, timeout: float = 60,
             **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run(self.argv(*flags, wait=wait), env=self.env, stdout=stdout,
                              stderr=subprocess.PIPE, text=True, timeout=timeout, check=False,
                              **kwargs)

    def observe(self, *flags: str, wait: str = "0", **kwargs) -> dict:
        result = self.call(*flags, wait=wait, **kwargs)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def append(self, source: str, data: bytes) -> None:
        path = self.root / source
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as stream:
            stream.write(data)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture",
                    "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                    *args], check=True, capture_output=True)


@pytest.fixture
def run(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in (*KEYS, "FACTORY_SYSTEM_ONE_PROFILE", "FACTORY_CONFIG", "BASH_ENV", "ENV"):
        monkeypatch.delenv(name, raising=False)
    repo = tmp_path / "product"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "commit", "--allow-empty", "-qm", "initial")
    root = run_record.create(tmp_path / "runs", "task", repo, b"Observe every lane.\n")
    return Run(root, {"PATH": "/usr/bin:/bin", "HOME": str(home)})


def observation(doc: dict) -> dict:
    """Everything the observer printed except the advisory System One batch (see module doc)."""
    return {key: value for key, value in doc.items() if key != "system_one"}


def delivered(doc: dict) -> dict[str, str]:
    texts: dict[str, str] = {}
    for row in doc["entries"]:
        texts[row["source"]] = texts.get(row["source"], "") + row["text"]
    return texts


def kept(path: Path) -> tuple:
    """What a reader can tell about a path without following it."""
    status = os.lstat(path)
    if stat.S_ISLNK(status.st_mode):
        return "symlink", os.readlink(path)
    if stat.S_ISDIR(status.st_mode):
        return "directory", sorted(os.listdir(path))
    if stat.S_ISREG(status.st_mode):
        return "file", path.read_bytes(), status.st_ino, status.st_mtime_ns
    return "other", stat.S_IFMT(status.st_mode), status.st_ino


def tree(path: Path) -> dict[str, bytes | None]:
    return {str(item.relative_to(path)): item.read_bytes() if item.is_file() else None
            for item in path.rglob("*")}


def page_round(run: Run, after: dict) -> tuple[dict, dict[str, str]]:
    """Read one round of activity both ways until neither form has anything new.

    Before each pair of calls nothing changes, so both read the same state; `--after` runs
    first so it can never benefit from a file `--continue` has just written.
    """
    texts: dict[str, str] = {}
    for _ in range(20):
        by_after = run.observe("--after", json.dumps(after))
        by_continue = run.observe("--continue")
        assert observation(by_continue) == observation(by_after)
        assert json.loads(run.cursor_file.read_bytes()) == by_continue["cursors"]
        after = by_after["cursors"]
        if not by_after["entries"]:
            return after, texts
        for source, text in delivered(by_after).items():
            texts[source] = texts.get(source, "") + text
    pytest.fail("the observation never caught up within 20 pages")


def inbox_delta(run: Run, text: str) -> str:
    inbox = run.root / INBOX
    before = inbox.read_bytes() if inbox.exists() else b""
    run_record.message(run.root, "orchestrator", "validator", text)
    after = inbox.read_bytes()
    assert after.startswith(before)
    return after[len(before):].decode()


def test_repeated_continue_pages_through_activity_exactly_like_the_after_chain(run, tmp_path):
    """Falsifiers: --continue ignores or never saves its cursors (re-delivers everything),
    saves the starting instead of the returned cursors (repeats a page), skips a page, or
    reports its own cursor file as activity. Each makes a round disagree with the --after
    chain or with the bytes actually appended."""
    after: dict = {}

    # Round 1: several sources, including the inbox that shares orchestrator/ with the file.
    run.append(CODER, b"coder: reading the task\n")
    run.append(TESTER, b"tester: deriving cases\n")
    run.append(VALIDATOR, b"validator: brief sent\n")
    question = inbox_delta(run, "first question for the orchestrator")
    after, got = page_round(run, after)
    assert got == {CODER: "coder: reading the task\n", TESTER: "tester: deriving cases\n",
                   VALIDATOR: "validator: brief sent\n", INBOX: question}

    # Round 2: appends between calls are delivered once, and only they are.
    run.append(CODER, b"coder: wrote the change\n")
    run.append(TESTER, b"tester: first test red\n")
    answer = inbox_delta(run, "second question for the orchestrator")
    after, got = page_round(run, after)
    assert got == {CODER: "coder: wrote the change\n", TESTER: "tester: first test red\n",
                   INBOX: answer}

    # Round 3: nothing new is nothing delivered (and the file's own rewrite is not activity).
    after, got = page_round(run, after)
    assert got == {}

    # Round 4: one log replaced by a new, shorter file; another truncated and rewritten in place.
    replacement = tmp_path / "replacement.log"
    replacement.write_bytes(b"validator: restarted\n")
    os.replace(replacement, run.root / VALIDATOR)
    (run.root / CODER).write_bytes(b"coder: new session\n")
    after, got = page_round(run, after)
    assert got == {VALIDATOR: "validator: restarted\n", CODER: "coder: new session\n"}

    # Round 5: more than one observation's worth of output arrives in several pages.
    lines = "".join(f"tester: line {index:05d}\n" for index in range(2400))
    run.append(TESTER, lines.encode())
    after, got = page_round(run, after)
    assert got == {TESTER: lines}


def test_the_identical_command_line_returns_only_the_new_bytes(run):
    """Falsifier: the repeated command restarts from {} or replays the previous page, which
    is what made the Orchestrator's host ask again (it needed a different command line)."""
    run.append(CODER, b"coder: first progress\n")
    argv = run.argv("--continue")

    def once() -> dict:
        result = subprocess.run(argv, env=run.env, capture_output=True, text=True,
                                timeout=60, check=False)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    first = once()
    assert delivered(first) == {CODER: "coder: first progress\n"}  # positive control
    run.append(CODER, b"coder: second progress\n")
    second = once()
    assert delivered(second) == {CODER: "coder: second progress\n"}
    assert [row["start"] for row in second["entries"]] == [len(b"coder: first progress\n")]
    assert once()["entries"] == []


def test_first_continue_creates_orchestrator_and_an_owner_only_cursor_file(run):
    """Falsifiers: the file inherits the umask (world-readable), or a run whose orchestrator/
    folder does not exist yet cannot be observed with --continue."""
    assert not (run.root / "orchestrator").exists()  # fixture precondition
    result = run.observe("--continue", umask=0)
    assert result["entries"] == [] and result["cursors"] == {}
    assert set(os.listdir(run.root / "orchestrator")) - {"system-one"} == {
        "observe-cursors.json"}
    status = os.lstat(run.cursor_file)
    assert stat.S_ISREG(status.st_mode) and stat.S_IMODE(status.st_mode) == 0o600
    assert json.loads(run.cursor_file.read_bytes()) == {}


def test_cursor_file_is_replaced_whole_owner_only_and_leaves_no_temporary_files(run):
    """Falsifiers: rewriting the file in place (a reader, or a crash, sees a partial file),
    keeping a wider mode from an existing file, or leaving a temporary file behind."""
    run.append(CODER, b"coder: progress\n")
    orchestrator = run.root / "orchestrator"
    orchestrator.mkdir(mode=0o700)
    run.cursor_file.write_bytes(b"{}")
    run.cursor_file.chmod(0o644)
    run.observe("--after", "{}")  # whatever an ordinary observation leaves in orchestrator/
    before = set(os.listdir(orchestrator))
    reader = os.open(run.cursor_file, os.O_RDONLY)
    try:
        result = run.observe("--continue", umask=0)
        seen_by_reader = os.pread(reader, 65536, 0)
    finally:
        os.close(reader)
    assert seen_by_reader == b"{}"  # the old file was replaced, not rewritten under its reader
    assert set(os.listdir(orchestrator)) == before
    status = os.lstat(run.cursor_file)
    assert stat.S_ISREG(status.st_mode) and stat.S_IMODE(status.st_mode) == 0o600
    assert result["cursors"] != {}
    assert json.loads(run.cursor_file.read_bytes()) == result["cursors"]


def test_failed_observation_keeps_the_cursor_file_and_loses_no_activity(run, tmp_path):
    """Falsifier: cursors are saved before (or although) the observation fails, so the
    activity appended before the failure is silently skipped by the next call."""
    run.append(CODER, b"coder: first\n")
    run.observe("--continue")
    before = kept(run.cursor_file)
    run.append(CODER, b"coder: second\n")
    elsewhere = tmp_path / "elsewhere.log"
    elsewhere.write_bytes(b"unrelated\n")
    (run.root / VALIDATOR).symlink_to(elsewhere)
    failed = run.call("--continue")
    assert failed.returncode != 0
    assert kept(run.cursor_file) == before
    (run.root / VALIDATOR).unlink()
    assert delivered(run.observe("--continue")) == {CODER: "coder: second\n"}  # positive control


def test_cursors_do_not_advance_when_the_observation_cannot_be_delivered(run):
    """Falsifier: the cursor file is written before the printed observation reaches its
    reader (for example, before stdout is flushed), so output nobody read is skipped."""
    run.append(CODER, b"coder: first\n")
    run.observe("--continue")
    before = kept(run.cursor_file)
    run.append(CODER, b"coder: unread\n")
    read_end, write_end = os.pipe()
    os.close(read_end)  # the reader is gone, so printing fails
    try:
        run.call("--continue", stdout=write_end)
    finally:
        os.close(write_end)
    assert kept(run.cursor_file) == before
    assert delivered(run.observe("--continue")) == {CODER: "coder: unread\n"}  # positive control


def valid_link(path: Path, outside: Path) -> None:
    target = outside / "observe-cursors.json"
    target.write_bytes(b"{}")
    path.symlink_to(target)


def dangling_link(path: Path, outside: Path) -> None:
    path.symlink_to("/srv/fixture/observe-cursors.json")


def directory(path: Path, outside: Path) -> None:
    path.mkdir()
    (path / "kept.txt").write_bytes(b"kept\n")


def fifo(path: Path, outside: Path) -> None:
    os.mkfifo(path, 0o600)


def document(data: bytes):
    def write(path: Path, outside: Path) -> None:
        path.write_bytes(data)
        path.chmod(0o600)
    return write


REFUSED = "dispatcher.py --observe refused: --continue: "
NOT_KEPT = REFUSED + "observation printed, cursors not kept"
CAUSES = ("not a regular file", "not JSON", "not an object")


def assert_named_refusal(result: subprocess.CompletedProcess) -> str:
    """Contract 6 and 7: exit 70 and one prefixed stderr line, never a traceback."""
    assert result.returncode == 70, result.stderr
    assert "Traceback" not in result.stderr
    lines = result.stderr.splitlines()
    assert len(lines) == 1 and lines[0].startswith(REFUSED), result.stderr
    return lines[0]


@pytest.mark.parametrize("plant,cause", [
    (valid_link, "observe-cursors.json"),
    (dangling_link, "observe-cursors.json"),
    (directory, "not a regular file"),
    (fifo, "not a regular file"),
    (document(b""), "not JSON"),
    (document(b"{bad"), "not JSON"),
    (document(b'{"tmux-lanes/coder-lane.log": '), "not JSON"),  # a torn write
    (document(b"[]"), "not an object"),
    (document(b"3"), "not an object"),
    (document(b'"x"'), "not an object"),
    (document(b"null"), "not an object"),  # derived: null is JSON and not an object
], ids=["symlink-to-valid-cursors", "dangling-symlink", "directory", "fifo", "empty",
        "bad-json", "truncated-json", "json-list", "json-number", "json-string", "json-null"])
def test_unusable_cursor_file_is_a_named_refusal_that_leaves_it_alone(
    run, tmp_path, plant, cause,
):
    """Falsifiers: following a link (reading the target, or treating a dangling link as
    absent and replacing it), blocking on a FIFO, treating unusable content as {} and then
    overwriting it with fresh cursors, printing an observation from cursors it could not
    read, or failing with a traceback (or a generic message) that does not say why."""
    run.append(CODER, b"coder: progress\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (run.root / "orchestrator").mkdir(mode=0o700)
    plant(run.cursor_file, outside)
    before = kept(run.cursor_file)
    outside_before = tree(outside)
    listing = set(os.listdir(run.root / "orchestrator")) - {"system-one"}
    try:
        refused = run.call("--continue", timeout=20)
    except subprocess.TimeoutExpired:  # a normal call takes well under a second
        pytest.fail("--continue blocked on the planted cursor file instead of refusing it")
    line = assert_named_refusal(refused)
    assert refused.stdout == ""
    reason = line[len(REFUSED):]
    assert cause in reason, line
    if cause in CAUSES:  # the line names this cause, not a list of every possible one
        assert not [other for other in CAUSES if other != cause and other in reason], line
    assert kept(run.cursor_file) == before
    assert tree(outside) == outside_before
    assert set(os.listdir(run.root / "orchestrator")) - {"system-one"} == listing
    # Positive control: the same command works once the file is an ordinary cursor file.
    if stat.S_ISDIR(os.lstat(run.cursor_file).st_mode):
        shutil.rmtree(run.cursor_file)
    else:
        run.cursor_file.unlink()
    run.cursor_file.write_bytes(b"{}")
    assert delivered(run.observe("--continue")) == {CODER: "coder: progress\n"}


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write into a folder without write "
                    "permission, so the cursors would be kept")
def test_printed_observation_whose_cursors_cannot_be_kept_is_a_named_refusal(run):
    """Falsifiers: exiting 0 although the next identical call will repeat this page (the
    Orchestrator cannot tell the cursors did not move), withholding the observation that
    was already read, or changing the kept cursors."""
    run.append(CODER, b"coder: first\n")
    run.observe("--after", "{}")  # an ordinary observation; creates orchestrator/ as usual
    orchestrator = run.root / "orchestrator"

    def with_orchestrator_read_only() -> subprocess.CompletedProcess:
        orchestrator.chmod(0o500)
        try:
            return run.call("--continue")
        finally:
            orchestrator.chmod(0o700)

    def assert_printed_not_kept(result: subprocess.CompletedProcess, expected: dict) -> None:
        assert assert_named_refusal(result).startswith(NOT_KEPT), result.stderr
        assert delivered(json.loads(result.stdout)) == expected

    # No kept cursors yet: none appear.
    assert_printed_not_kept(with_orchestrator_read_only(), {CODER: "coder: first\n"})
    assert not run.cursor_file.exists()
    # Positive control: writable again, the same page is delivered again and kept.
    assert delivered(run.observe("--continue")) == {CODER: "coder: first\n"}

    # Kept cursors exist: they stay exactly as they were.
    before = kept(run.cursor_file)
    run.append(CODER, b"coder: second\n")
    assert_printed_not_kept(with_orchestrator_read_only(), {CODER: "coder: second\n"})
    assert kept(run.cursor_file) == before
    assert delivered(run.observe("--continue")) == {CODER: "coder: second\n"}


def test_symlinked_orchestrator_folder_is_refused_without_writing_through_it(run, tmp_path):
    """Falsifier: cursors read from or written into the folder the link points at.

    Only failure and no write-through are asserted. Reading the activity already refuses an
    orchestrator/ whose path goes through a link (as it does for --after), so nothing is
    printed and contract 7's printed-but-not-kept outcome is not reached on this path."""
    run.append(CODER, b"coder: progress\n")
    outside = tmp_path / "outside-orchestrator"
    outside.mkdir()
    (outside / "observe-cursors.json").write_bytes(b"{}")
    (run.root / "orchestrator").symlink_to(outside)
    before = tree(outside)
    refused = run.call("--continue")
    assert refused.returncode != 0
    assert tree(outside) == before
    assert (run.root / "orchestrator").is_symlink()
    (run.root / "orchestrator").unlink()
    assert delivered(run.observe("--continue")) == {CODER: "coder: progress\n"}  # positive control


@pytest.mark.parametrize("flags", [("--continue", "--after", "{}"),
                                   ("--after", "{}", "--continue")])
def test_continue_with_after_is_a_usage_error_that_changes_nothing(run, flags):
    """Falsifier: one of the two silently wins, so a caller believes it paged from cursors it
    did not use, or the stored cursors move."""
    run.append(CODER, b"coder: progress\n")
    for attempt in ("no file yet", "file kept"):
        before = kept(run.cursor_file) if run.cursor_file.exists() else None
        refused = run.call(*flags)
        assert refused.returncode == 2, attempt
        assert refused.stdout == ""
        message = refused.stderr.strip().splitlines()[-1]
        assert "--continue" in message and "cursors" in message, refused.stderr
        assert (kept(run.cursor_file) if run.cursor_file.exists() else None) == before
        run.observe("--continue")  # positive control, and gives the second attempt a file


def test_after_and_plain_observe_neither_read_nor_write_the_cursor_file(run):
    """Falsifiers: --after or a flagless observation creating, rewriting or consulting the
    stored cursors (which would make them skip activity or fail on another reader's file)."""
    run.append(CODER, b"coder: progress\n")
    everything = {CODER: "coder: progress\n"}
    assert delivered(run.observe()) == everything
    assert delivered(run.observe("--after", "{}")) == everything
    assert not run.cursor_file.exists()

    run.observe("--continue")  # cursors now stored at the end of the log
    before = kept(run.cursor_file)
    assert delivered(run.observe()) == everything  # still starts from {}
    assert delivered(run.observe("--after", "{}")) == everything
    assert kept(run.cursor_file) == before

    run.cursor_file.unlink()
    run.cursor_file.write_bytes(b"[]")
    before = kept(run.cursor_file)
    assert delivered(run.observe()) == everything
    assert delivered(run.observe("--after", "{}")) == everything
    assert kept(run.cursor_file) == before
    control = run.call("--continue")  # control: the planted file is one --continue refuses
    assert "not an object" in assert_named_refusal(control)


def test_wait_seconds_bounds_are_unchanged_with_continue(run):
    """Falsifier: --continue bypassing the 0-60 second bound. Inferred: the bound is
    inclusive, and an out-of-range call fails before anything is stored."""
    run.append(CODER, b"coder: progress\n")
    for seconds in ("-1", "61"):
        refused = run.call("--continue", wait=seconds)
        assert refused.returncode != 0, seconds
        assert not run.cursor_file.exists()
    # Positive control at the limit: activity is waiting, so this returns at once.
    assert delivered(run.observe("--continue", wait="60")) == {CODER: "coder: progress\n"}


def test_continue_still_waits_for_activity_when_nothing_is_new(run):
    """Falsifier: --continue dropping the wait, so an idle Orchestrator spins through calls.
    Inferred: with nothing new, the call waits out --wait-seconds before returning empty."""
    run.append(CODER, b"coder: progress\n")
    first = run.observe("--continue")
    started = time.monotonic()
    quiet = run.observe("--continue", wait="1")
    assert time.monotonic() - started >= 1.0
    assert quiet["entries"] == [] and quiet["cursors"] == first["cursors"]
    assert json.loads(run.cursor_file.read_bytes()) == first["cursors"]


def observe_command(prompt: str) -> list[str]:
    """The dispatcher command the prompt tells the Orchestrator to run, as argv."""
    text = prompt.replace("`", "")
    end_of_script = text.index("dispatcher.py") + len("dispatcher.py")
    script = text[: end_of_script].rsplit(None, 1)[-1]
    rest = text[end_of_script:]
    sentence_end = re.search(r"\.(?:\s|$)|\n", rest)
    return [script, *shlex.split(rest[: sentence_end.start()] if sentence_end else rest)]


def test_orchestrator_start_prompt_names_one_fixed_command_that_pages(launch, tmp_path):
    """Falsifiers: the prompt still hands the Orchestrator a per-call value to fill in
    (--after CURSORS_JSON), so every observation needs a new approval; or the command it
    names does not run, or does not continue from where the previous call ended."""
    root, _, _, capture, invocation, _ = launch
    _, _, command = invocation(capture(), "orchestrator")
    prompt_file = root / "orchestrator" / "initial-prompt.txt"
    prompt = prompt_file.read_text() if prompt_file.exists() else command[-1]
    assert "--after" not in prompt and "CURSORS_JSON" not in prompt
    argv = observe_command(prompt)
    assert Path(argv[0]).resolve() == DISPATCHER.resolve()  # the harness that started the run
    assert {"--observe", "--continue"} <= set(argv)
    assert argv[argv.index("--run") + 1] == root.name
    assert Path(argv[argv.index("--root") + 1]) == root
    home = tmp_path / "observer-home"
    home.mkdir()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}

    def once() -> dict:
        result = subprocess.run([sys.executable, *argv], env=env, capture_output=True,
                                text=True, timeout=90, check=False)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    log = root / CODER
    log.parent.mkdir(exist_ok=True)
    log.write_bytes(b"coder: started\n")
    assert delivered(once()).get(CODER) == "coder: started\n"  # positive control
    with log.open("ab") as stream:
        stream.write(b"coder: continued\n")
    assert delivered(once()) == {CODER: "coder: continued\n"}
