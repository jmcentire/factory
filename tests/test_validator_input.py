"""The Validator input path: what the Validator is owed, delivered once into its inbox.

Written from the contract for #125 (Part A, A1 and A2) without reading the module: only its
signatures, its docstrings and the CLI's ``--help``. ``post`` records a notice as owed in
``<run>/validator/notices.jsonl``; ``deliver`` writes owed notices into
``<run>/validator/inbox.jsonl`` exactly once and never waits past ``wait_s`` in all; ``status``
is ``owed`` or ``delivered``, never accepted. Another writer is a real second process holding
``flock`` on the inbox, as ``run_record.append_record``'s writers do; a crash is a
``BaseException`` raised inside the inbox append, with the retry in a fresh process.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from harness import run_record, validator_input

REPO = Path(__file__).resolve().parent.parent
INBOX = Path("validator") / "inbox.jsonl"
CLOSING = ("Delivered for the Validator's decision. Delivery is not acceptance or approval, "
           "and gives no one a decision they did not already have.")
HOLDER = (
    "import fcntl, os, select, sys\n"
    "fd = os.open(sys.argv[1], os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX)\n"
    "print('locked', flush=True)\n"
    "select.select([sys.stdin], [], [], float(sys.argv[2]))\n"
)
DELIVER = (  # a second deliverer: another process, with nothing in memory
    "import json, sys\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from harness import validator_input\n"
    "print(json.dumps(validator_input.deliver(Path(sys.argv[2]), wait_s=float(sys.argv[3]))))\n"
)


class Killed(BaseException):
    """The deliverer's process dies at this point."""


class HeldLock:
    """Another process holding ``flock(LOCK_EX)`` on a file until released."""

    def __init__(self, path: Path, hold_s: float = 60.0) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.process = subprocess.Popen(
            [sys.executable, "-I", "-c", HOLDER, str(path), str(hold_s)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        assert self.process.stdout is not None
        assert self.process.stdout.readline().strip() == "locked"

    def release(self) -> None:
        if self.process.poll() is None:
            assert self.process.stdin is not None
            self.process.stdin.close()
        self.process.wait(timeout=30)


@pytest.fixture
def hold() -> Any:
    held: list[HeldLock] = []

    def take(path: Path) -> HeldLock:
        held.append(HeldLock(path))
        return held[-1]

    yield take
    for lock in held:
        lock.release()


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "runs" / "r1"
    root.mkdir(parents=True)
    return root


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _inbox(root: Path, needle: str = "") -> list[dict[str, Any]]:
    return [row for row in _rows(root / INBOX) if needle in str(row.get("text", ""))]


def _undelivered(root: Path) -> list[dict[str, Any]]:
    return [row for row in _rows(root / "events.jsonl")
            if row.get("kind") == "validator-input-undelivered"]


def _prefix(kind: str) -> str:
    return "FACTORY_" + kind.upper().replace("-", "_") + " "


def _second_deliverer(root: Path, wait_s: float) -> subprocess.Popen[str]:
    return subprocess.Popen([sys.executable, "-c", DELIVER, str(REPO), str(root), str(wait_s)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _on_inbox_append(monkeypatch: pytest.MonkeyPatch, effect: Any) -> list[str]:
    """Route every loaded ``run_record.append_record`` (under any module name, or imported by
    name elsewhere) through ``effect`` for its first inbox append; returns what fired."""

    fired: list[str] = []
    reals = {id(module.append_record) for name, module in list(sys.modules.items())
             if name in {"run_record", "harness.run_record"}}
    for module in list(sys.modules.values()):
        for name, value in list((getattr(module, "__dict__", None) or {}).items()):
            if id(value) not in reals:
                continue

            def wrapped(path: Any, payload: bytes, *args: Any, _real: Any = value,
                        **kwargs: Any) -> Any:
                if Path(path).name != "inbox.jsonl" or fired:
                    return _real(path, payload, *args, **kwargs)
                fired.append(str(path))
                return effect(_real, path, payload, *args, **kwargs)

            monkeypatch.setattr(module, name, wrapped)
    return fired


# -- post ------------------------------------------------------------------------------------


def test_the_kinds_senders_and_ledger_are_the_contracts(tmp_path: Path) -> None:
    """A1: the five kinds, run_record's allowed senders (each of which may post), and the
    ledger's name."""

    assert validator_input.KINDS == {"lane-turn-ended", "lane-status", "worker-question",
                                     "test-result", "escalation-response"}
    assert run_record.SENDERS == {"validator", "orchestrator", "coder", "tester", "host",
                                  "dispatcher", "founder", "seat-supervisor"}
    assert validator_input.LEDGER == "notices.jsonl"
    assert validator_input.CLOSING == CLOSING
    root = _root(tmp_path)
    for number, sender in enumerate(sorted(run_record.SENDERS)):
        assert validator_input.post(root, f"n{number}", "lane-status", sender, "s") is True
        assert validator_input.status(root, f"n{number}") == "owed"


@pytest.mark.parametrize(("kind", "sender"), [
    ("lane-done", "host"), ("accepted", "host"), ("", "host"), ("LANE-STATUS", "host"),
    ("test-result", "operator"), ("test-result", ""), ("test-result", "Tester"),
])
def test_a_kind_or_sender_outside_the_allowed_sets_is_refused(
    tmp_path: Path, kind: str, sender: str,
) -> None:
    """A1: ``kind`` is one of five slugs and ``sender`` one of run_record's senders; anything
    else is rejected and nothing is owed or delivered."""

    root = _root(tmp_path)
    with pytest.raises(ValueError):
        validator_input.post(root, "n1", kind, sender, "text")
    with pytest.raises(KeyError):
        validator_input.status(root, "n1")
    assert validator_input.deliver(root, wait_s=1) == []
    assert _inbox(root) == []


@pytest.mark.parametrize("again", ["while-owed", "after-delivery"])
def test_an_id_posted_twice_is_recorded_and_delivered_once(tmp_path: Path, again: str) -> None:
    """A1: posting the same ``notice_id`` twice records it once; the first text is told."""

    root = _root(tmp_path)
    assert validator_input.post(root, "n1", "test-result", "tester", "first text") is True
    if again == "after-delivery":
        assert validator_input.deliver(root, wait_s=2) == []
    assert validator_input.post(root, "n1", "test-result", "tester", "second text") is False
    assert validator_input.post(root, "n1", "lane-status", "host", "third text") is False
    assert validator_input.deliver(root, wait_s=2) == []
    assert validator_input.deliver(root, wait_s=2) == []
    [row] = _inbox(root)
    assert "first text" in row["text"] and "second text" not in row["text"]
    assert validator_input.status(root, "n1") == "delivered"


def test_the_ledger_is_owner_only(tmp_path: Path) -> None:
    """A1: the owed notices' own file under ``<run>/validator/`` is 0600."""

    root = _root(tmp_path)
    validator_input.post(root, "n1", "lane-status", "host", "text")
    ledger = root / "validator" / validator_input.LEDGER
    assert ledger.is_file() and not ledger.is_symlink()
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600


@pytest.mark.parametrize("linked", ["validator-folder", "ledger"])
def test_the_ledger_is_never_written_through_a_link(tmp_path: Path, linked: str) -> None:
    """A1: no symlinks followed. A linked ``validator/`` or ledger is refused (an error, or a
    post that records nothing), and the link's target is never written."""

    root = _root(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    target = elsewhere / validator_input.LEDGER
    target.write_text("")
    if linked == "validator-folder":
        (root / "validator").symlink_to(elsewhere, target_is_directory=True)
    else:
        (root / "validator").mkdir(mode=0o700)
        (root / "validator" / validator_input.LEDGER).symlink_to(target)
    try:
        recorded = validator_input.post(root, "n1", "lane-status", "host", "text")
    except (OSError, ValueError):
        recorded = False
    assert recorded is False
    try:
        validator_input.deliver(root, wait_s=1)
    except (OSError, ValueError):
        pass
    assert target.read_text() == ""
    assert sorted(path.name for path in elsewhere.iterdir()) == [validator_input.LEDGER]


# -- status and the delivered text -----------------------------------------------------------


def test_status_goes_from_owed_to_delivered_and_never_to_accepted(tmp_path: Path) -> None:
    """A1: ``owed`` | ``delivered``; no ``accepted`` state, and an unknown id is a KeyError."""

    root = _root(tmp_path)
    with pytest.raises(KeyError):
        validator_input.status(root, "n1")
    validator_input.post(root, "n1", "worker-question", "coder", "Which clock?")
    assert validator_input.status(root, "n1") == "owed"
    with pytest.raises(KeyError):
        validator_input.status(root, "n2")
    assert validator_input.deliver(root, wait_s=2) == []
    for _ in range(2):
        assert validator_input.status(root, "n1") == "delivered"
        assert validator_input.deliver(root, wait_s=2) == []
    for row in _rows(root / "validator" / validator_input.LEDGER):
        assert not {"accepted", "approved"} & {str(value) for value in row.values()}
    assert not [name for name in dir(validator_input) if name.lower().startswith(
        ("accept", "approve"))]


@pytest.mark.parametrize(("kind", "sender"), [
    ("lane-turn-ended", "host"), ("lane-status", "seat-supervisor"),
    ("worker-question", "coder"), ("test-result", "tester"), ("escalation-response", "founder"),
])
def test_a_delivered_row_names_its_kind_and_sender_and_says_delivery_is_not_acceptance(
    tmp_path: Path, kind: str, sender: str,
) -> None:
    """A1: the row's text starts ``FACTORY_<KIND> ``, names its real sender, carries the posted
    text and ends with the closing sentence; the row's sender is that sender."""

    root = _root(tmp_path)
    body = "First line of the body.\nSecond line of the body."
    assert validator_input.post(root, "n1", kind, sender, body) is True
    assert validator_input.deliver(root, wait_s=2) == []
    [row] = _inbox(root)
    text = str(row["text"])
    assert row["sender"] == sender
    assert text.startswith(_prefix(kind))
    assert sender in text and body in text
    assert text.rstrip().endswith(CLOSING)
    assert set(row) >= {"sender", "text", "ts", "message_digest"}


# -- deliver ---------------------------------------------------------------------------------


@pytest.mark.parametrize("failure", ["killed-before-write", "killed-after-write",
                                     "failed-after-write"])
def test_each_owed_notice_reaches_the_inbox_once_across_a_failed_or_killed_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    """A1: exactly once across retries and crashes, including a crash between the inbox write
    and recording ``delivered``; a failure raises nothing; the retry is a fresh process."""

    root = _root(tmp_path)
    validator_input.post(root, "warm", "lane-status", "host", "warm-up")
    assert validator_input.deliver(root, wait_s=2) == []  # the inbox writer is loaded
    validator_input.post(root, "n1", "test-result", "tester", "notice-one")
    validator_input.post(root, "n2", "worker-question", "coder", "notice-two")

    def effect(real: Any, path: Any, payload: bytes, *args: Any, **kwargs: Any) -> Any:
        if failure == "killed-before-write":
            raise Killed
        real(path, payload, *args, **kwargs)
        raise Killed if failure.startswith("killed") else OSError("the disk went away")

    fired = _on_inbox_append(monkeypatch, effect)
    if failure == "failed-after-write":
        owed = validator_input.deliver(root, wait_s=2)
        assert set(owed) <= {"n1", "n2"}
    else:
        try:
            validator_input.deliver(root, wait_s=2)
        except Killed:
            pass
    assert fired, "the inbox append was never reached through run_record"
    monkeypatch.undo()
    retry = _second_deliverer(root, 5)
    out, err = retry.communicate(timeout=60)
    assert retry.returncode == 0, err
    assert json.loads(out) == []
    assert validator_input.deliver(root, wait_s=2) == []
    for needle, notice in (("notice-one", "n1"), ("notice-two", "n2")):
        assert len(_inbox(root, needle)) == 1
        assert validator_input.status(root, notice) == "delivered"


def test_two_deliverers_at_once_write_each_notice_once(tmp_path: Path, hold: Any) -> None:
    """A1: two deliverers (this process and another) racing for the same owed notices."""

    root = _root(tmp_path)
    for number in range(3):
        validator_input.post(root, f"n{number}", "test-result", "tester", f"result-{number}")
    lock = hold(root / INBOX)
    other = _second_deliverer(root, 20)
    mine: list[list[str]] = []
    thread = threading.Thread(
        target=lambda: mine.append(validator_input.deliver(root, wait_s=20)), daemon=True)
    thread.start()
    time.sleep(1.0)  # both are waiting on the held inbox
    lock.release()
    thread.join(timeout=40)
    out, err = other.communicate(timeout=40)
    assert other.returncode == 0, err
    assert mine == [[]] and json.loads(out) == []
    for number in range(3):
        assert len(_inbox(root, f"result-{number}")) == 1
        assert validator_input.status(root, f"n{number}") == "delivered"


@pytest.mark.parametrize("wait_s", [0.5, 2.0])
def test_a_locked_inbox_leaves_the_notices_owed_after_at_most_wait_s(
    tmp_path: Path, hold: Any, wait_s: float,
) -> None:
    """A1: ``deliver`` waits at most ``wait_s`` in all (not per notice), returns every notice
    still owed, and the next ``deliver`` sends them."""

    root = _root(tmp_path)
    ids = [f"n{number}" for number in range(3)]
    for notice in ids:
        validator_input.post(root, notice, "lane-status", "host", f"status-{notice}")
    lock = hold(root / INBOX)
    started = time.monotonic()
    owed = validator_input.deliver(root, wait_s=wait_s)
    elapsed = time.monotonic() - started
    assert sorted(owed) == ids
    assert elapsed < wait_s + 1.0, elapsed
    assert [validator_input.status(root, notice) for notice in ids] == ["owed"] * 3
    lock.release()
    assert validator_input.deliver(root, wait_s=2) == []
    assert all(len(_inbox(root, f"status-{notice}")) == 1 for notice in ids)


def _unwritable(inbox: Path, how: str) -> None:
    inbox.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if how == "folder":
        inbox.mkdir()
    elif how == "read-only":
        inbox.touch(mode=0o400)
        inbox.chmod(0o400)
    else:
        os.mkfifo(inbox, 0o600)


def _writable(inbox: Path, how: str) -> None:
    if how == "folder":
        inbox.rmdir()
    elif how == "read-only":
        inbox.chmod(0o600)
    else:
        inbox.unlink()


@pytest.mark.parametrize("how", ["folder", "read-only", "fifo"])
def test_an_unwritable_inbox_keeps_its_notice_owed_and_wakes_the_operator_once(
    tmp_path: Path, how: str,
) -> None:
    """A1: never raises for an unwritable inbox, never waits past ``wait_s`` (a FIFO with no
    reader included); the notice stays owed for the next ``deliver``. A2: a notice left
    undelivered records one ``validator-input-undelivered`` event with ``wake: true``, not
    one per attempt."""

    root = _root(tmp_path)
    validator_input.post(root, "n1", "escalation-response", "founder", "Use the second one.")
    _unwritable(root / INBOX, how)
    for _ in range(3):
        result: list[list[str]] = []
        attempt = threading.Thread(
            target=lambda out=result: out.append(validator_input.deliver(root, wait_s=1)),
            daemon=True)
        attempt.start()
        attempt.join(timeout=6)
        assert not attempt.is_alive(), "deliver waited past its wait_s"
        assert result == [["n1"]]
        assert validator_input.status(root, "n1") == "owed"
    [event] = _undelivered(root)
    assert event["wake"] is True
    _writable(root / INBOX, how)
    assert validator_input.deliver(root, wait_s=2) == []
    assert len(_inbox(root, "Use the second one.")) == 1
    assert validator_input.status(root, "n1") == "delivered"
    assert len(_undelivered(root)) == 1


# -- the CLI ---------------------------------------------------------------------------------


@pytest.mark.parametrize("case", ["delivered", "locked", "refused-kind", "refused-sender"])
def test_notify_posts_and_delivers_once_and_exits_0_or_75(
    tmp_path: Path, hold: Any, case: str,
) -> None:
    """A1: ``run_record.py notify`` posts and delivers once; it exits 0 when delivered and 75
    when still owed, and a refused kind or sender is neither."""

    repo, runs = tmp_path / "repo", tmp_path / "runs"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    task = tmp_path / "task.txt"
    task.write_text("Report the suite.\n")
    script = str(REPO / "harness" / "run_record.py")
    created = subprocess.run([sys.executable, script, "create", "--runs", str(runs), "--run", "r1",
                              "--repo", str(repo), "--task-file", str(task)],
                             capture_output=True, text=True, timeout=60)
    assert created.returncode == 0, created.stderr
    root = runs / "r1"
    message = tmp_path / "result.txt"
    message.write_text("Suite: 12 passed, 0 failed.\n")
    kind = "approval" if case == "refused-kind" else "test-result"
    sender = "operator" if case == "refused-sender" else "tester"
    argv = [sys.executable, script, "notify", "--runs", str(runs), "--run", "r1", "--kind", kind,
            "--from", sender, "--id", "suite-1", "--message-file", str(message)]
    lock = hold(root / INBOX) if case == "locked" else None
    first = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if case.startswith("refused"):
        assert first.returncode not in {0, 75}
        with pytest.raises(KeyError):
            validator_input.status(root, "suite-1")
        assert _inbox(root, "Suite: 12 passed") == []
        return
    assert first.returncode == (75 if lock else 0), first.stderr
    assert validator_input.status(root, "suite-1") == ("owed" if lock else "delivered")
    if lock:
        assert _inbox(root, "Suite: 12 passed") == []
        lock.release()
    again = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    assert again.returncode == 0, again.stderr
    [row] = _inbox(root, "Suite: 12 passed")
    assert row["sender"] == "tester" and str(row["text"]).startswith("FACTORY_TEST_RESULT ")
    assert validator_input.status(root, "suite-1") == "delivered"
