"""An interactive lane at a host dialog is held, not stall-stopped, for at most 4 h (PR #124).

On 2026-11-17 a Cursor Tester lane sat at Cursor's command approval ("Run this command? / Not in
allowlist: sha256sum, wc") for 30 minutes and the watchdog stopped it as stalled. Covered here:
the hold and its bound; one wake and one Validator notice per dialog, never the pane's text; a
dialog known by its rows, so a commit gets the lane past it and any other dialog after that is
new; bounded waits on the inbox and event log; a notice delivered exactly once across failed
writes, deaths and other writers; held time kept off the review clock; caps still stopping it.

Black-box (TESTING.md T1, T41): expected values come from the contract given to the Tester. The
real ``LaneWatch`` runs with its real files, a real git repository and an injected clock; tmux is
stubbed at its ``tmux`` seam, locks are held by real second processes, and only the death and
race cases wrap ``run_record`` functions around the real ones. The stall rule: a 900 s read
allowance, then a 1200 s window from the latest of its end, the last commit and the last held check.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from harness import lane_watchdog, run_record, seat_attention

T0 = 1_791_000_000.0
STALL_WINDOW_S = 1200
OLD_STOP = 900 + STALL_WINDOW_S  # with no commit and no dialog: stopped after 2100 s
HOLD_S = 4 * 3600  # the contract's "4 h", not read from the module
LONG_WINDOW_S = 20_000  # only a review, a wrap-up or a cap ends the lane
TARGET = "r1:tester"
INBOX = "validator/inbox.jsonl"
TYPING = {"send-keys", "send", "send-prefix", "paste-buffer", "pasteb"}
CAPTURE_TIMEOUT = subprocess.TimeoutExpired(["tmux", "capture-pane"], 5)
RUN_RECORD_FILE = Path(run_record.__file__).resolve()
PROMPT = "permission_prompt"


def payload(*lines: str) -> str:
    return "\n".join(lines) + "\n"


def names(text: str, seconds: float) -> bool:
    """Whether ``text`` gives the watch clock's ``T0 + seconds`` in ISO-8601 UTC."""
    stamp = datetime.datetime.fromtimestamp(T0 + seconds, datetime.UTC).isoformat()
    return stamp in text or stamp.replace("+00:00", "Z") in text


# Screens. Cursor's three identifying rows are quoted from the 2026-11-17 lane; the rest of every
# dialog is reconstructed for the fixture.
WORK = payload("Reading TASK.md", "Ran 12 tests in 0.41s", "OK")


def cursor_dialog(command: str, allowlist: str = "sha256sum, wc") -> str:
    return payload(f"  $ {command}", " Run this command?", f" Not in allowlist: {allowlist}",
                   " → Run (once) (y)", "   Skip (esc or n)", "   Run Everything (shift+tab)")


def passed_rows(rows: int) -> str:
    return payload(*[f"tests/test_fixture.py::test_case_{n:02d} PASSED" for n in range(rows)])


CURSOR_DIALOG = cursor_dialog("sha256sum dist/factory-0.8.30.tar.gz | wc -c")
CURSOR_APPROVAL = WORK + CURSOR_DIALOG
CURSOR_OTHER_APPROVAL = WORK + cursor_dialog("rm -rf build/tmp && mv out build", "rm, mv")
CURSOR_SECOND_FILE = WORK + cursor_dialog("sha256sum second.tar.gz | wc -c")
CURSOR_FIRST_ROWS_ONLY = WORK + payload(*CURSOR_DIALOG.splitlines()[:3])
HEADLESS_ECHO = WORK + payload('AssertionError: expected "Not in allowlist: sha256sum, wc"') + (
    CURSOR_DIALOG)
HASH_OUTPUT = payload("a3f1c09e2b  dist/factory-0.8.30.tar.gz")
ANSWERED = WORK + HASH_OUTPUT + payload("184320", "Hashes match.")
ANSWERED_STILL_SHOWN = CURSOR_APPROVAL + HASH_OUTPUT
LATER_OUTPUT = payload(*[f"step {n:02d} of the build: ok" for n in range(1, 11)])
#: The answered approval scrolling away: its allowlist row and footer left, or its footer alone.
SCROLLED_PAST = CURSOR_APPROVAL + passed_rows(11)
FOOTER_ONLY = CURSOR_APPROVAL + passed_rows(14)
SPACED_APPROVAL = WORK + payload("") + CURSOR_DIALOG
SPACED_ANSWERED = SPACED_APPROVAL + HASH_OUTPUT
#: The same approval again, a blank row below new output: identical rows (the remaining limit).
SAME_AGAIN = SPACED_ANSWERED + LATER_OUTPUT + payload("") + CURSOR_DIALOG
#: The same approval again directly below new output: that output is the block above it.
SAME_UNDER_NEW_OUTPUT = ANSWERED_STILL_SHOWN + LATER_OUTPUT + CURSOR_DIALOG
#: A folder so deep that two command rows under it share more than their first 200 characters.
LONG_FOLDER = "/srv/fixture/releases/" + "/".join(f"stage-{n:02d}-artifacts" for n in range(1, 13))
LONG_PATH_FIRST = WORK + cursor_dialog(f"sha256sum {LONG_FOLDER}/first.tar.gz | wc -c")
LONG_PATH_SECOND = WORK + cursor_dialog(f"sha256sum {LONG_FOLDER}/second.tar.gz | wc -c")
ANTIGRAVITY_TRUST = WORK + payload(
    "Accessing workspace:", "/srv/fixture/replay/repository",
    "Do you trust the contents of this project?",
    "Antigravity CLI requires permission to read, edit, and execute files here.",
    "> Yes, I trust this folder", "  No, exit", "  ↑/↓ Navigate · enter Confirm")
NEW_BELOW_ANSWERED = ANSWERED_STILL_SHOWN + CURSOR_SECOND_FILE[len(WORK):]
NEW_KIND_BELOW_ANSWERED = ANSWERED_STILL_SHOWN + ANTIGRAVITY_TRUST[len(WORK):]
CLAUDE_BYPASS = WORK + payload("WARNING: Claude Code running in Bypass Permissions mode",
                               " ❯ 1. No, exit", "   2. Yes, I accept")
CLAUDE_HOST_PANEL = WORK + payload(" Teach auto mode about your environment?",
                                   " ❯ 1. Yes, describe it now", "   2. Not now",
                                   " Background update waiting while this panel is open")
LIMIT_LINE = " You've hit your usage limit · resets 3pm (America/New_York)"


def claude_prompt(rows: list[str], tools: str = "sha256sum, sort and wc") -> str:
    """Claude Code's prompt for a Bash command it breaks over ``rows``."""
    return WORK + payload("", " Bash command", "", *rows, "", " Do you want to proceed?",
                          " ❯ 1. Yes", f"   2. Yes, and don't ask again for {tools} commands",
                          "   3. No, and tell Claude what to do differently (esc)")


def script(update_two: str) -> list[str]:
    return ["   python3 <<'PY'", "   if enabled:", "       update_one()", update_two, "   PY"]


CLAUDE_PIPELINE = claude_prompt(["   sha256sum first.tar.gz |", "   sort |", "   wc -c"])
CLAUDE_PIPELINE_SECOND = CLAUDE_PIPELINE.replace("first.tar.gz", "second.tar.gz")
DIFF = ["   diff \\", "     first.txt \\", "     second.txt \\", "     | head"]
CLAUDE_DIFF = claude_prompt(DIFF, "diff and head")
CLAUDE_DIFF_SWAPPED = claude_prompt([DIFF[0], DIFF[2], DIFF[1], DIFF[3]], "diff and head")
CLAUDE_SCRIPT = claude_prompt(script("       update_two()"), "python3")
CLAUDE_SCRIPT_UNINDENTED = claude_prompt(script("   update_two()"), "python3")
CLAUDE_CD = claude_prompt(["   cd /tmp && \\", "   sha256sum first.tar.gz | \\", "   sort | \\",
                           "   wc -c"])
CLAUDE_CD_DROPPED = claude_prompt(["   sha256sum first.tar.gz | \\", "   sort | \\", "   wc -c"])


def box_row(text: str) -> str:
    return "│ " + text.ljust(60) + " │"


#: Claude Code's prompt drawn in a box: the same empty box row above and below its command.
CLAUDE_BOX = WORK + payload(
    "╭" + "─" * 62 + "╮", box_row("Bash command"), box_row(""), box_row("  make build-fixture"),
    box_row(""), box_row("Do you want to proceed?"), box_row("❯ 1. Yes"),
    box_row("2. No, and tell Claude what to do differently (esc)"), "╰" + "─" * 62 + "╯")
CLAUDE_BOX_SCROLLED = CLAUDE_BOX + LATER_OUTPUT  # its rows now begin at the second empty row


def codex_approval(name: str) -> str:
    """Codex's approval, question first: the command row sits between its matching rows."""
    return WORK + payload("", "  Would you like to run the following command?", "",
                          f"  $ sha256sum {name} | wc -c", "", "› 1. Yes, proceed (y)",
                          "  2. Yes, and don't ask again for sha256sum commands (a)",
                          "  3. No, and tell Codex what to do differently (esc)", "",
                          "  Press enter to confirm or esc to cancel")


def agy_approval(name: str) -> str:
    return WORK + payload(" Requesting permission for:", f"   $ sha256sum {name} | wc -c", "",
                          " > Allow once", "   Allow for this conversation", "   Deny", "",
                          " ctrl+g edit/expand command")


def agent_for(screen: str) -> str:
    """The interactive agent whose dialog ``screen`` shows."""
    return next((agent for words, agent in (
        ("Do you want to proceed?", "claude"), ("Would you like to run", "codex-interactive"),
        ("Requesting permission for:", "agy")) if words in screen), "cursor-agent")


Call = tuple[str, ...]


def is_lookup(args: Call) -> bool:
    return args[0] == "list-windows"


def _targets_lane(args: Call) -> bool:
    target = next(((args[i + 1] if a == "-t" else a[2:]) for i, a in enumerate(args)
                   if a.startswith("-t") and i + (a == "-t") < len(args)), "")
    return target.split(":")[-1] == "@7"


def _prints(args: Call) -> bool:
    return any(a.startswith("-") and "p" in a[1:] for a in args[1:])


class Tmux:
    """The lane's tmux at ``LaneWatch``'s seam, answering as tmux does: the lane's window is @7,
    whose ``capture-pane -p`` prints ``screen``; its pane is dead once ``dead``. ``aimed`` raises
    from a check's n-th ``list-windows``; ``on_capture`` runs once inside the next capture."""

    def __init__(self, screen: str) -> None:
        self.screen = screen
        self.capture_rc = 0
        self.capture_fault: BaseException | None = None
        self.aimed: tuple[int, BaseException] | None = None
        self.on_capture: Callable[[], None] | None = None
        self.fired = 0
        self.dead = False
        self.calls: list[Call] = []
        self.checks: list[list[Call]] = [[]]

    def __call__(self, *args: str) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        self.checks[-1].append(args)
        if self.aimed is not None and is_lookup(args):
            ordinal, error = self.aimed
            if sum(map(is_lookup, self.checks[-1])) == ordinal:
                self.fired += 1
                raise error
        out, rc = ("1\n" if self.dead else "0\n"), 0
        if is_lookup(args):
            out = "tester @7\nwatch-tester @8\n"
        elif args[0] == "capture-pane":
            if self.capture_fault is not None:
                raise self.capture_fault
            if self.on_capture is not None and _targets_lane(args):
                hook, self.on_capture = self.on_capture, None
                hook()
            out = (self.screen if _targets_lane(args) else WORK) if _prints(args) else ""
            rc = self.capture_rc
        elif args[0] in {"display-message", "display"} and "#{pane_dead}" in args:
            out = (out if _targets_lane(args) else "0\n") if _prints(args) else ""
        return subprocess.CompletedProcess(args, rc, out, "")

    def typed(self) -> list[Call]:
        return [call for call in self.calls if call[0] in TYPING]


class Lane:
    """One Tester lane as tmux_lane.sh records it (by default an interactive Cursor session), its
    watcher armed at T0. ``thread=True`` records the resumable thread a review or wrap-up needs."""

    def __init__(self, tmp_path: Path, tmux: Tmux, *, agent: str = "cursor-agent",
                 prompt_via: str | None = "terminal", lane: str | None = "tester",
                 caps: dict[str, object] | None = None, watch: dict[str, object] | None = None,
                 thread: bool = False) -> None:
        self.root, self.tmux, self.now = tmp_path / "run", tmux, T0
        self.lanes = self.root / "tmux-lanes"
        self.lanes.mkdir(parents=True)
        self.repo = tmp_path / "clone"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        (self.lanes / "tester-lane.log").touch()
        row: dict[str, object] = {
            "status": "active", "ts": datetime.datetime.fromtimestamp(T0, datetime.UTC)
            .isoformat(), "run_id": "r1", "role": "tester", "lane": lane, "slot": "tester",
            "agent": agent, "model": "fixture-model", "terminal": False, "prompt_via": prompt_via,
            "repository": str(self.repo), "lane_log": str(self.lanes / "tester-lane.log"),
            "lane_exits": str(self.lanes / "tester-exits.jsonl"), "tmux_target": TARGET,
            "watch": {"read_allowance_s": 900, "stall_window_s": STALL_WINDOW_S, "poll_s": 30,
                      **(watch or {})},
            "caps": caps or {}, "qualify": None}
        (self.lanes / "tester-launch.jsonl").write_text(
            json.dumps({key: value for key, value in row.items()
                        if value is not None or key not in {"lane", "prompt_via"}}) + "\n")
        if thread:
            (self.lanes / "tester-thread-id").write_text("12345678-1234-4234-8234-123456789abc\n")
            (self.lanes / "tester-delivery.env").write_text("PATH=/usr/bin:/bin\n")
            (self.lanes / "tester-delivery.env").chmod(0o600)
        self.stops: list[str] = []
        self.said: list[str] = []
        self.delivered: list[list[str]] = []
        self.dies_at: str | None = None
        self.died: list[str] = []
        self.holders: list[Holder] = []  # other writers a scenario started
        self.restart()
        self.watch.arm()

    def restart(self) -> None:
        """A fresh watcher, not armed again: all it knows is what the run's files hold."""
        self.watch = lane_watchdog.LaneWatch(
            root=self.root, slot="tester", now=lambda: self.now, stop=self._stop,
            tmux=self.tmux, echo=self._echo, deliver=self._deliver, processes=lambda: {})

    def _echo(self, line: str) -> None:
        self.said.append(line)
        if self.dies_at is not None and line.startswith(self.dies_at):
            self.dies_at = None
            self.died.append("died")
            raise SystemExit("fixture: the watcher died")

    def _stop(self, target: str) -> str:
        self.stops.append(target)
        self.tmux.dead = True
        return f"stopped {target}"

    def _deliver(self, argv: list[str]) -> str:
        self.delivered.append(argv)
        return "scheduled"

    def resume(self, seconds: float) -> None:
        """A review pause ends: the pane runs again and the watcher starts a new life."""
        self.now = T0 + seconds
        self.tmux.dead = False
        self.watch.arm(new_life=True)

    def at(self, seconds: float) -> str:
        self.now = T0 + seconds
        self.tmux.checks.append([])
        return self.watch.check()

    def rows(self, name: str) -> list[dict[str, object]]:
        path = self.root / name
        return [json.loads(row) for row in path.read_text().splitlines()] if path.is_file() else []

    def kinds(self) -> list[str]:
        return [str(row["kind"]) for row in self.rows("events.jsonl")]

    def notices(self) -> list[str]:
        return [str(row["text"]) for row in self.rows(INBOX)
                if str(row.get("text", "")).startswith("FACTORY_LANE_AT_DIALOG ")]

    def told(self) -> tuple[int, int, int]:  # lane-at-dialog events and wake rows, notices
        wakes = [row["kind"] for row in self.rows("tmux-lanes/wake.jsonl")]
        return (self.kinds().count("lane-at-dialog"), wakes.count("lane-at-dialog"),
                len(self.notices()))


def commit(lane: Lane, at: float) -> None:
    name = f"commit-{at}"
    (lane.repo / name).write_text(name, encoding="utf-8")
    stamp = f"@{int(T0 + at)} +0000"
    env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    for argv in (["add", name], ["commit", "-q", "-m", name]):
        subprocess.run(["git", "-C", str(lane.repo), *argv], check=True, env=env)


def held_until(lane: Lane, stop_after: float, *checks: float) -> None:
    """Watching at each of ``checks`` and 2 s before ``stop_after``; stall-stopped 1 s after."""
    for seconds in (*checks, stop_after - 2):
        assert lane.at(seconds) == "watching", seconds
    assert lane.stops == []
    assert lane.at(stop_after + 1) == "stalled"
    assert lane.stops == [TARGET]


def leaked(lane: Lane, *screens: str) -> list[str]:
    """Rows of ``screens`` (but WORK's and the usage-limit line) found anywhere the watchdog
    writes or says anything: the run's files, its echoed lines, its tmux calls' arguments."""
    rows = {row.strip() for screen in screens for row in screen.splitlines()}
    rows -= {*WORK.splitlines(), LIMIT_LINE.strip()}
    places = [path.read_text(encoding="utf-8", errors="replace")
              for path in lane.root.rglob("*") if path.is_file()]
    places += ["\n".join(lane.said), "\n".join(" ".join(call) for call in lane.tmux.calls)]
    return sorted(row for row in rows if len(row) >= 8 and any(row in text for text in places))


def dialog_read_lookup(folder: Path) -> int:
    """The ordinal of the dialog read's ``list-windows`` in a check: the last before the first
    ``capture-pane`` (observed; the watchdog's other calls let their errors out, as before)."""
    tmux = Tmux(CURSOR_APPROVAL)
    assert Lane(folder, tmux).at(60) == "watching"
    calls = tmux.checks[-1]
    capture = next(i for i, call in enumerate(calls) if call[0] == "capture-pane")
    return sum(map(is_lookup, calls[:capture]))


HOLDER = (
    "import fcntl, os, sys\n"
    "fd = os.open(sys.argv[1], os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX)\n"
    "size, what = os.fstat(fd).st_size, sys.argv[2]\n"
    "os.write(fd, open(what[1:], 'rb').read() if what.startswith('@') else b'x' * int(what))\n"
    "print('held', flush=True)\n"
    "sys.stdin.read()\n"
    "if what != '0': os.ftruncate(fd, size)\n"
)


class Holder:
    """Another process holding ``flock`` LOCK_EX on ``path`` until ``release`` (or for
    ``release_after`` s), first appending ``append`` bytes (or a file's) it truncates on release."""

    def __init__(self, path: Path, append: int | Path = 0,
                 release_after: float | None = None) -> None:
        what = f"@{append}" if isinstance(append, Path) else str(append)
        self.process = subprocess.Popen([sys.executable, "-I", "-c", HOLDER, str(path), what],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        assert self.process.stdout is not None
        assert self.process.stdout.readline().strip() == "held"
        self.timer = threading.Timer(release_after or 0, self.release)
        if release_after is not None:
            self.timer.start()

    def release(self) -> None:
        self.timer.cancel()
        if self.process.poll() is None:
            assert self.process.stdin is not None
            self.process.stdin.close()
            self.process.wait(10)


class Unwritable:
    """The Validator's folder is a file, or a dangling link, until ``release``."""

    def __init__(self, root: Path, link: bool = False) -> None:
        self.path = root / "validator"
        if link:
            self.path.symlink_to(root / "no-such-directory")
        else:
            self.path.write_text("")

    def release(self) -> None:
        if self.path.is_symlink() or self.path.is_file():
            self.path.unlink()


def check_within(lane: Lane, seconds: float, limit: float, holders: list[Holder]) -> str:
    """One check on a thread, which must return within ``limit`` s of wall time. Still running
    10 s past that, it is blocked: the holders let go (so it ends) and the test fails."""
    outcome: list[str] = []

    def run() -> None:
        try:
            outcome.append(lane.at(seconds))
        except BaseException as error:  # reported below, never swallowed
            outcome.append(f"raised {type(error).__name__}: {error}")

    started = time.monotonic()
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(limit + 10)
    took = time.monotonic() - started
    if worker.is_alive():
        for holder in holders:
            holder.release()
        worker.join(30)
        pytest.fail(f"the check at {seconds} s was still running after {took:.1f} s: blocked")
    assert took < limit, f"the check at {seconds} s took {took:.1f} s (limit {limit} s)"
    return outcome[0]


Wrapper = Callable[[Callable[..., object]], Callable[..., object]]


def wrap_run_record(name: str, make: Wrapper, monkeypatch: pytest.MonkeyPatch,
                    folder: Path) -> None:
    """Wrap ``name`` in every loaded copy of run_record, after a warm-up lane has told a dialog
    (so the copy the watchdog writes with is loaded, whichever it is)."""
    warm_up = Lane(folder, Tmux(CURSOR_APPROVAL))
    assert warm_up.at(60) == "watching" and len(warm_up.notices()) == 1
    copies = [module for module in list(sys.modules.values()) if getattr(module, "__file__", None)
              and Path(module.__file__).resolve() == RUN_RECORD_FILE]
    for module in copies:
        monkeypatch.setattr(module, name, make(getattr(module, name)))


def row_as_message_writes_it(folder: Path, text: str) -> bytes:
    run_record.message(folder, "validator", "host", text)
    return (folder / INBOX).read_bytes()


# --- The hold, and what each host's dialog looks like ----------------------------------------


@pytest.mark.parametrize(("after", "commit_at", "checks", "stop_after"), [
    pytest.param(ANSWERED, 3100, (3130,), 3100 + STALL_WINDOW_S, id="answered-then-a-commit"),
    pytest.param(ANSWERED, None, (), 3000 + STALL_WINDOW_S, id="answered-no-commit-control"),
    pytest.param(CURSOR_APPROVAL, None, (9000, 1500 + HOLD_S - 1),
                 1500 + HOLD_S + STALL_WINDOW_S, id="never-answered-the-4-h-bound"),
])
def test_cursors_approval_holds_the_lane_past_its_stall_time_for_at_most_4_h(
    tmp_path: Path, after: str, commit_at: int | None, checks: tuple[int, ...], stop_after: int,
) -> None:
    """The 2026-11-17 case: Cursor's approval from 1500 s holds the lane past 2101 s. Answered,
    the lane gets a window from its last held check, or from a later commit; never answered, the
    hold ends 4 h after the first sighting. Told once; nothing typed into the pane."""
    tmux = Tmux(WORK)
    lane = Lane(tmp_path, tmux)
    assert lane.at(60) == "watching"
    tmux.screen = CURSOR_APPROVAL
    for seconds in (1500, OLD_STOP + 1, 3000):
        assert lane.at(seconds) == "watching", seconds
    tmux.screen = after
    assert lane.at(3030) == "watching"
    if commit_at is not None:
        commit(lane, commit_at)
    held_until(lane, stop_after, *checks)
    assert lane.kinds().count("refusal-lane-stall") == 1
    assert lane.told() == (1, 1, 1) and tmux.typed() == []


@pytest.mark.parametrize(("agent", "screen", "kind"), [
    pytest.param("cursor-agent", CURSOR_APPROVAL, PROMPT, id="cursor-approval"),
    pytest.param("cursor-agent", WORK + payload(
        "Cursor's sha256sum is not in the allowlist here, so hashlib computes the digest.",
        "upstream API: rate limit reached, backing off; resets in 5 m"), None,
        id="cursor-near-miss-dialog-words-in-prose"),
    pytest.param("codex-interactive", codex_approval("a.tar.gz"), PROMPT, id="codex-approval"),
    pytest.param("codex-interactive", codex_approval("a.tar.gz") + passed_rows(16), None,
                 id="codex-near-miss-approval-scrolled-out-of-the-last-15-rows"),
    pytest.param("agy", agy_approval("a.tar.gz"), PROMPT, id="antigravity-approval"),
    pytest.param("agy", ANTIGRAVITY_TRUST, "folder_trust", id="antigravity-folder-trust"),
    pytest.param("agy", ANTIGRAVITY_TRUST + passed_rows(16), None,
                 id="antigravity-near-miss-trust-scrolled-out-of-the-last-15-rows"),
    pytest.param("claude", CLAUDE_PIPELINE, PROMPT, id="claude-permission-prompt"),
    pytest.param("claude", CLAUDE_BYPASS, "bypass_permissions", id="claude-bypass-permissions"),
    pytest.param("claude", CLAUDE_HOST_PANEL, "host_panel", id="claude-host-panel"),
    pytest.param("claude", WORK + payload(LIMIT_LINE), None, id="claude-near-miss-usage-limit"),
    pytest.param("claude", CLAUDE_PIPELINE + payload(LIMIT_LINE), None,
                 id="claude-near-miss-usage-limit-below-a-prompt"),
    pytest.param("cursor-agent", WORK + payload(LIMIT_LINE) + CURSOR_DIALOG, None,
                 id="usage-limit-above-an-approval"),
])
def test_each_hosts_dialog_is_held_and_told_and_its_near_miss_is_not(
    tmp_path: Path, agent: str, screen: str, kind: str | None,
) -> None:
    """Each held kind, with no usage limit on screen, holds the lane past 2101 s and is told once
    by kind, read by a ``-pJ ... -S -60`` capture, no row of it written anywhere. Each near-miss
    (prose, a dialog scrolled out of the last 15 rows, a usage limit) is stopped there, untold."""
    found = seat_attention.dialogs_in(screen)
    if kind is None:
        assert not found or "usage_limit" in found, found
    else:
        assert kind in found and "usage_limit" not in found, found
    tmux = Tmux(WORK)
    lane = Lane(tmp_path, tmux, agent=agent)
    assert lane.at(60) == "watching"
    tmux.screen = screen
    assert lane.at(OLD_STOP - 1) == "watching"
    if kind is None:
        assert lane.at(OLD_STOP + 1) == "stalled" and lane.stops == [TARGET]
        assert lane.told() == (0, 0, 0)
        return
    for seconds in (OLD_STOP + 1, OLD_STOP + 5000):
        assert lane.at(seconds) == "watching", seconds
    assert lane.stops == [] and lane.told() == (1, 1, 1) and kind in lane.notices()[0]
    assert leaked(lane, screen) == [] and tmux.typed() == []
    captures = [call for call in tmux.calls if call[0] == "capture-pane"]
    flags = ["".join(a[1:] for a in call if a.startswith("-") and a[1:].isalpha())
             for call in captures]
    assert captures and all({"p", "J"} <= set(flag) for flag in flags), captures
    assert all(" -S -60" in " ".join(call) for call in captures), captures


@pytest.mark.parametrize("prompt_via", ["stdin", "argv", None])
def test_a_headless_lane_is_never_read_for_a_dialog(tmp_path: Path, prompt_via: str | None) -> None:
    """A lane whose prompt is not typed into a terminal is never captured, whatever it prints:
    stopped at 2101 s, untold."""
    assert PROMPT in seat_attention.dialogs_in(HEADLESS_ECHO)
    tmux = Tmux(HEADLESS_ECHO)
    lane = Lane(tmp_path, tmux, prompt_via=prompt_via)
    assert lane.at(60) == "watching" and lane.at(OLD_STOP - 1) == "watching"
    assert lane.at(OLD_STOP + 1) == "stalled" and lane.stops == [TARGET]
    assert [call for call in tmux.calls if call[0] == "capture-pane"] == []
    assert lane.told() == (0, 0, 0)


def test_dialog_selection_gives_each_kinds_rows_and_whether_they_were_clipped() -> None:
    """Per kind: the matching rows, up to 2 non-blank rows above each and the block above those,
    the rows between the first and last match; whole, indented, in screen order; clipped when
    they begin at the top of a cut 15-row tail. ``dialog_rows`` gives the same rows."""
    work, dialog = WORK.splitlines(), CURSOR_DIALOG.splitlines()
    codex = codex_approval("a.tar.gz").splitlines()
    for screen, rows, clipped in (
        (CURSOR_APPROVAL, [*work, *dialog], False),
        (SPACED_APPROVAL, dialog, False),
        (SCROLLED_PAST, dialog[2:], True),
        (FOOTER_ONLY, dialog[-1:], True),
        (CURSOR_FIRST_ROWS_ONLY, [*work, *dialog[:3]], False),
        (codex_approval("a.tar.gz"), [*work, codex[4], codex[6], *codex[8:11]], False),
        (CLAUDE_SCRIPT_UNINDENTED, [*script("   update_two()"), *CLAUDE_SCRIPT.splitlines()[-4:]],
         False),
    ):
        assert seat_attention.dialog_selection(screen) == {PROMPT: (rows, clipped)}, screen
        assert seat_attention.dialog_rows(screen) == {PROMPT: rows}, screen
    assert seat_attention.dialog_selection(CURSOR_APPROVAL + passed_rows(16)) == {}


# --- What ends a dialog, and when a commit gets the lane past it -----------------------------


@pytest.mark.parametrize(("between", "first_seen"), [
    pytest.param(ANSWERED, 9060, id="a-read-showing-none-ends-it"),
    pytest.param(CURSOR_APPROVAL + payload(LIMIT_LINE), 9060, id="a-usage-limit-ends-it"),
    pytest.param(CURSOR_OTHER_APPROVAL, 1500, id="another-command-without-a-commit-is-the-same"),
    pytest.param(ANTIGRAVITY_TRUST, 1500, id="another-kind-without-a-commit-is-the-same"),
])
def test_only_a_read_showing_no_held_dialog_ends_one(
    tmp_path: Path, between: str, first_seen: int,
) -> None:
    """Cursor's approval from 1500 s to 9000 s, ``between`` at 9030 s, the approval at 9060 s. A
    read showing no held dialog (a usage limit counts as none) ends the dialog: the next is new,
    told again and bounded afresh. Another dialog with no such read and no commit is the same
    dialog: told once, bounded from 1500 s."""
    tmux = Tmux(WORK)
    lane = Lane(tmp_path, tmux)
    assert lane.at(60) == "watching"
    for seconds, screen in ((1500, CURSOR_APPROVAL), (9000, CURSOR_APPROVAL), (9030, between),
                            (9060, CURSOR_APPROVAL)):
        tmux.screen = screen
        assert lane.at(seconds) == "watching", seconds
    told = 1 if first_seen == 1500 else 2
    assert lane.told() == (told, told, told)
    held_until(lane, first_seen + HOLD_S + STALL_WINDOW_S, first_seen + HOLD_S - 1)
    assert lane.told() == (told, told, told) and leaked(lane, between, CURSOR_APPROVAL) == []


@pytest.mark.parametrize(("held", "after"), [
    pytest.param((CURSOR_APPROVAL,), (CURSOR_APPROVAL,), id="still-on-screen"),
    pytest.param((CURSOR_APPROVAL,), (SCROLLED_PAST,), id="scrolled-partly-out-of-the-tail"),
    pytest.param((CURSOR_APPROVAL,), (FOOTER_ONLY,), id="only-its-footer-left"),
    pytest.param((CLAUDE_BOX,), (CLAUDE_BOX_SCROLLED,), id="scrolled-to-a-repeated-row"),
    pytest.param((CURSOR_APPROVAL, CURSOR_OTHER_APPROVAL), (CURSOR_APPROVAL,),
                 id="changed-while-held-its-first-screen"),
    pytest.param((CURSOR_APPROVAL, CURSOR_OTHER_APPROVAL), (CURSOR_OTHER_APPROVAL,),
                 id="changed-while-held-its-last-screen"),
    pytest.param((SPACED_APPROVAL,), (SPACED_ANSWERED, SAME_AGAIN),
                 id="the-remaining-limit-the-same-approval-again-below-a-blank-row"),
])
def test_after_a_commit_the_answered_dialog_holds_nothing(
    tmp_path: Path, held: tuple[str, ...], after: tuple[str, ...],
) -> None:
    """``held`` at 1500 s (and 1530 s), a commit, a failed read, then ``after``: per kind, a held
    screen's rows or their clipped tail. Nothing holds (stopped 1200 s after the commit) and
    nothing is told. The last case pins the remaining limit: identical rows are not told apart."""
    tmux = Tmux(WORK)
    lane = Lane(tmp_path, tmux, agent=agent_for(held[0]))
    assert lane.at(60) == "watching"
    for seconds, screen in zip((1500, 1530), held, strict=False):
        tmux.screen = screen
        assert lane.at(seconds) == "watching", seconds
    commit(lane, 1600)
    tmux.capture_fault = CAPTURE_TIMEOUT
    assert lane.at(1700) == "watching"
    tmux.capture_fault = None
    for seconds, screen in zip((1730, 1760), after, strict=False):
        tmux.screen = screen
        assert lane.at(seconds) == "watching", seconds
    held_until(lane, 1600 + STALL_WINDOW_S)
    assert lane.told() == (1, 1, 1) and leaked(lane, *held, *after) == []


@pytest.mark.parametrize(("first", "then", "kind"), [
    pytest.param(CURSOR_APPROVAL, CURSOR_OTHER_APPROVAL, PROMPT, id="another-commands-approval"),
    pytest.param(CURSOR_APPROVAL, ANTIGRAVITY_TRUST, "folder_trust", id="another-kind"),
    pytest.param(CURSOR_APPROVAL, CURSOR_SECOND_FILE, PROMPT,
                 id="the-same-allowlist-row-for-another-command"),
    pytest.param(CURSOR_APPROVAL, NEW_BELOW_ANSWERED, PROMPT, id="a-new-approval-below-it"),
    pytest.param(CURSOR_APPROVAL, NEW_KIND_BELOW_ANSWERED, "folder_trust",
                 id="a-new-kind-below-it"),
    pytest.param(CURSOR_APPROVAL, SAME_UNDER_NEW_OUTPUT, PROMPT,
                 id="the-same-approval-again-directly-below-new-output"),
    pytest.param(CURSOR_APPROVAL, CURSOR_FIRST_ROWS_ONLY, PROMPT, id="its-first-rows-only"),
    pytest.param(LONG_PATH_FIRST, LONG_PATH_SECOND, PROMPT,
                 id="command-rows-alike-for-their-first-200-characters"),
    pytest.param(codex_approval("first.tar.gz"), codex_approval("second.tar.gz"), PROMPT,
                 id="codex-approval-redrawn-for-another-file"),
    pytest.param(agy_approval("first.tar.gz"), agy_approval("second.tar.gz"), PROMPT,
                 id="antigravity-approval-redrawn-for-another-file"),
    pytest.param(CLAUDE_PIPELINE, CLAUDE_PIPELINE_SECOND, PROMPT,
                 id="claude-pipeline-for-another-file-in-the-block-above"),
    pytest.param(CLAUDE_DIFF, CLAUDE_DIFF_SWAPPED, PROMPT, id="claude-diff-with-two-rows-swapped"),
    pytest.param(CLAUDE_SCRIPT, CLAUDE_SCRIPT_UNINDENTED, PROMPT,
                 id="claude-script-with-a-row-unindented"),
    pytest.param(CLAUDE_CD, CLAUDE_CD_DROPPED, PROMPT, id="claude-command-without-its-first-row"),
])
def test_after_a_commit_any_other_dialog_is_new(
    tmp_path: Path, first: str, then: str, kind: str,
) -> None:
    """``first`` at 1500 s, a commit at 1600 s, ``first`` still showing at 1700 s (no hold, not
    told); at 1760 s, with no clear read between, ``then``: its rows are no held screen's (a row
    it never showed, another order, a gap) nor their clipped tail. It is new: told (``1
    commit(s)``, first seen 1760 s), held past the commit's window and bounded from 1760 s."""
    tmux = Tmux(WORK)
    lane = Lane(tmp_path, tmux, agent=agent_for(first))
    assert lane.at(60) == "watching"
    tmux.screen = first
    assert lane.at(1500) == "watching"
    commit(lane, 1600)
    assert lane.at(1700) == "watching" and lane.told() == (1, 1, 1)
    tmux.screen = then
    assert lane.at(1760) == "watching" and lane.told() == (2, 2, 2)
    second = lane.notices()[1]
    assert kind in second and "1 commit(s)" in second, second
    assert names(second, 1760) and not names(second, 1500), second
    held_until(lane, 1760 + HOLD_S + STALL_WINDOW_S, 1600 + STALL_WINDOW_S + 1, 1760 + HOLD_S - 1)
    assert lane.told() == (2, 2, 2) and leaked(lane, first, then) == []


@pytest.mark.parametrize(("in_the_capture", "sighted", "committed", "stop_after"), [
    pytest.param(True, 1500, 1500, 9100 + STALL_WINDOW_S, id="made-in-its-capture-not-passed"),
    pytest.param(False, 1500.25, 1500.90, 1500 + STALL_WINDOW_S,
                 id="made-after-it-in-the-same-second-passed"),
])
def test_a_commit_passes_a_dialog_only_when_found_after_the_capture_that_first_showed_it(
    tmp_path: Path, in_the_capture: bool, sighted: float, committed: float, stop_after: int,
) -> None:
    """A checkpoint commit made inside the capture that first shows the approval is in its
    baseline (the reflog read after the capture): held on until a later commit (9100 s). One
    made after the capture, in the same second, passes it (commits are counted, not timed)."""
    tmux = Tmux(WORK)
    lane = Lane(tmp_path, tmux)
    assert lane.at(60) == "watching"
    tmux.screen = CURSOR_APPROVAL
    if in_the_capture:
        tmux.on_capture = lambda: commit(lane, committed)
    assert lane.at(sighted) == "watching" and tmux.on_capture is None
    if in_the_capture:
        for seconds in (1530, 1500 + STALL_WINDOW_S + 1, 9000):
            assert lane.at(seconds) == "watching", seconds
        commit(lane, 9100)
    else:
        commit(lane, committed)
    held_until(lane, stop_after, stop_after - STALL_WINDOW_S + 30)
    assert lane.told() == (1, 1, 1)


def test_a_commit_made_while_a_held_dialog_is_read_passes_it_and_the_one_reached_is_new(
    tmp_path: Path,
) -> None:
    """Within the 1530 s check, before its capture returns, the lane commits past the approval
    held since 1500 s and reaches another with the same allowlist row: that one is new, told
    (``1 commit(s)``, first seen 1530 s) and bounded from 1530 s."""
    tmux = Tmux(WORK)
    lane = Lane(tmp_path, tmux)
    assert lane.at(60) == "watching"
    tmux.screen = CURSOR_APPROVAL
    assert lane.at(1500) == "watching"

    def answered_and_reached() -> None:
        commit(lane, 1530)
        tmux.screen = CURSOR_SECOND_FILE

    tmux.on_capture = answered_and_reached
    assert lane.at(1530) == "watching" and tmux.on_capture is None
    assert lane.told() == (2, 2, 2)
    second = lane.notices()[1]
    assert "1 commit(s)" in second and names(second, 1530) and not names(second, 1500), second
    held_until(lane, 1530 + HOLD_S + STALL_WINDOW_S, 1560, 1530 + STALL_WINDOW_S + 1,
               1530 + HOLD_S - 1)
    assert lane.told() == (2, 2, 2) and leaked(lane, CURSOR_APPROVAL, CURSOR_SECOND_FILE) == []


@pytest.mark.parametrize(("attribute", "value"), [
    pytest.param("aimed", None, id="the-reads-window-lookup-times-out"),
    pytest.param("capture_fault", CAPTURE_TIMEOUT, id="capture-times-out"),
    pytest.param("capture_fault", PermissionError(13, "tmux"), id="capture-raises-oserror"),
    pytest.param("capture_rc", 1, id="capture-exits-nonzero"),
    pytest.param("dead", True, id="pane-found-dead"),
])
def test_failed_reads_after_a_sighting_raise_nothing_hold_nothing_and_tell_nothing(
    tmp_path: Path, attribute: str, value: object,
) -> None:
    """The approval is seen at 1500 s, its notice unwritable. Then every read fails or finds the
    pane dead (a review found a tmux timeout in the read's window lookup crashing the watch): no
    check raises, none holds (stopped 1200 s after 1500 s), none sends the owed notice."""
    tmux = Tmux(WORK)
    lane = Lane(tmp_path / "lane", tmux)
    blocked = Unwritable(lane.root)
    assert lane.at(60) == "watching"
    tmux.screen = CURSOR_APPROVAL
    assert lane.at(1500) == "watching" and lane.told() == (1, 1, 0)
    blocked.release()
    if attribute == "aimed":  # the read's own window lookup, every check
        value = (dialog_read_lookup(tmp_path / "calibration"),
                 subprocess.TimeoutExpired(["tmux", "list-windows"], 5))
    setattr(tmux, attribute, value)
    for seconds in (1530, 1500 + STALL_WINDOW_S - 1):
        assert lane.at(seconds) == "watching", seconds
    assert lane.stops == [] and lane.at(1500 + STALL_WINDOW_S + 1) in {"stalled", "stopping"}
    assert lane.stops == [TARGET] and lane.told() == (1, 1, 0)
    assert lane.kinds().count("lane-dialog-notice-undelivered") == 1
    assert tmux.aimed is None or tmux.fired >= 2, tmux.fired


# --- The announcement, and the waits on the inbox and the event log --------------------------


@pytest.mark.parametrize(("lane_name", "named"), [
    pytest.param("tester-b", "tester-b", id="the-launch-rows-lane"),
    pytest.param(None, "tester", id="the-slot-when-the-row-names-no-lane"),
])
def test_a_dialog_is_told_once_by_lane_kind_bound_commits_and_first_sighting_never_its_text(
    tmp_path: Path, lane_name: str | None, named: str,
) -> None:
    """The first check at the approval (97 s) makes one wake and sends the Validator, from host,
    ``FACTORY_LANE_AT_DIALOG <lane>: `` naming the kind, ``4 h``, ``1 commit(s)`` and 97 s in
    ISO-8601 UTC. Later checks add nothing; no row of the pane is written or said anywhere."""
    tmux = Tmux(WORK)
    lane = Lane(tmp_path, tmux, lane=lane_name)
    commit(lane, 30)
    assert lane.at(60) == "watching"
    tmux.screen = CURSOR_APPROVAL
    for seconds in (97, 120, OLD_STOP + 1, 5000):
        assert lane.at(seconds) == "watching", seconds
    assert lane.told() == (1, 1, 1)
    assert [row["wake"] for row in lane.rows("events.jsonl") if row["kind"] == "lane-at-dialog"
            ] == [True]
    assert sum(line.startswith("FACTORY_WAKE lane-at-dialog") for line in lane.said) == 1
    [message] = lane.rows(INBOX)
    text = str(message["text"])
    assert message["sender"] == "host" and text.startswith(f"FACTORY_LANE_AT_DIALOG {named}: ")
    assert all(part in text for part in (PROMPT, "4 h", "1 commit(s)")), text
    assert names(text, 97) and not any(names(text, s) for s in (120, OLD_STOP + 1, 5000)), text
    assert leaked(lane, CURSOR_APPROVAL) == [] and tmux.typed() == []


@pytest.mark.parametrize("first_seen", [60, 3601])
def test_a_lane_at_a_dialog_still_reaches_its_spend_cap_and_one_first_seen_there_is_not_told(
    tmp_path: Path, first_seen: int,
) -> None:
    """A $1 spend cap at $1/h stops the lane at a dialog it has shown since 60 s (the wall cap:
    the wrap-up test). A dialog first shown on the check that reaches the cap is not told."""
    tmux = Tmux(CURSOR_APPROVAL if first_seen == 60 else WORK)
    caps = {"spend_cap_usd": "1", "usd_per_hour": "1"}
    lane = Lane(tmp_path, tmux, caps=caps, watch={"stall_window_s": LONG_WINDOW_S})
    for seconds in (60, 3000):
        assert lane.at(seconds) == "watching", seconds
    tmux.screen = CURSOR_APPROVAL
    assert lane.at(3601) == "capped" and lane.stops == [TARGET]
    assert lane.kinds().count("refusal-lane-cap") == 1
    told = int(first_seen == 60)
    assert lane.told() == (told, told, told) and tmux.typed() == []


Setup = Callable[[Lane, pytest.MonkeyPatch, Path], list]


def on_inbox_end(lane: Lane, monkeypatch: pytest.MonkeyPatch, folder: Path,
                 hook: Callable[[Callable[[], int]], int]) -> None:
    """Wrap ``run_record.inbox_end`` for ``lane``'s Validator inbox, once: ``hook`` is given the
    real call and returns the offset."""
    fired: list[bool] = []

    def make(real: Callable[..., int]) -> Callable[..., int]:
        def inbox_end(root: Path, recipient: str, **options: object) -> int:
            if Path(root) != lane.root or recipient != "validator" or fired:
                return real(root, recipient, **options)
            fired.append(True)
            return hook(lambda: real(root, recipient, **options))
        return inbox_end

    wrap_run_record("inbox_end", make, monkeypatch, folder)


def _locked(*paths: str, release_after: tuple[float | None, ...] = (None, None)) -> Setup:
    def setup(lane: Lane, monkeypatch: pytest.MonkeyPatch, folder: Path) -> list[Holder]:
        (lane.root / "validator").mkdir(mode=0o700, exist_ok=True)
        return [Holder(lane.root / path, release_after=after)
                for path, after in zip(paths, release_after, strict=False)]
    return setup


def _inbox_locked_after_its_offset_is_read(lane: Lane, monkeypatch: pytest.MonkeyPatch,
                                           folder: Path) -> list[Holder]:
    """The offset's read takes 3 s, then another process holds the inbox for 4 s."""
    (lane.root / "validator").mkdir(mode=0o700)
    holders: list[Holder] = []

    def hook(real: Callable[[], int]) -> int:
        offset = real()
        time.sleep(3.0)
        holders.append(Holder(lane.root / INBOX, release_after=4.0))
        return offset

    on_inbox_end(lane, monkeypatch, folder, hook)
    return holders


@pytest.mark.parametrize(("setup", "limit", "woken", "noticed", "undelivered"), [
    pytest.param(lambda lane, *_: [Unwritable(lane.root)], 5.0, True, False, {1},
                 id="validator-folder-is-a-file"),
    pytest.param(lambda lane, *_: [Unwritable(lane.root, link=True)], 5.0, True, False, {1},
                 id="validator-folder-is-a-dangling-link"),
    pytest.param(_locked(INBOX), 7.5, True, False, {1}, id="inbox-locked-one-5-s-wait-in-all"),
    pytest.param(_inbox_locked_after_its_offset_is_read, 6.5, True, False, {1},
                 id="inbox-locked-for-what-the-offset-read-left"),
    pytest.param(_locked("events.jsonl"), 8.5, False, True, {0}, id="event-log-locked"),
    pytest.param(_locked("events.jsonl", INBOX), 13.5, False, False, {0, 1},
                 id="event-log-and-inbox-locked"),
    pytest.param(_locked("events.jsonl", INBOX, release_after=(3.0, 6.5)), 10.0, True, True, {0},
                 id="the-inbox-wait-starts-after-the-wake"),
])
def test_an_unwritable_or_locked_inbox_or_event_log_never_blocks_the_watch_and_all_is_told_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, setup: Setup, limit: float, woken: bool,
    noticed: bool, undelivered: set[int],
) -> None:
    """At the first sighting (90 s) and the next check, each check returns within the waits (5 s
    on the event log; one 5 s on the inbox, shared by the offset's read and the notice, after the
    wake). What was not made then is made once later; a failed notice, one undelivered event."""
    tmux = Tmux(WORK)
    lane = Lane(tmp_path / "lane", tmux)
    assert lane.at(60) == "watching"
    holders = setup(lane, monkeypatch, tmp_path / "warm-up")
    tmux.screen = CURSOR_APPROVAL
    try:
        assert check_within(lane, 90, limit, holders) == "watching"
        assert lane.told() == (int(woken), int(woken), int(noticed))
        assert check_within(lane, 120, limit, holders) == "watching"
    finally:
        for holder in holders:
            holder.release()
    for seconds in (150, 180):
        assert lane.at(seconds) == "watching", seconds
    assert lane.told() == (1, 1, 1) and names(lane.notices()[0], 90), lane.notices()
    assert lane.kinds().count("lane-dialog-notice-undelivered") in undelivered


# --- The notice reaches the inbox exactly once -------------------------------------------------


def _dies_after_the_notice_row(lane: Lane, monkeypatch: pytest.MonkeyPatch, folder: Path,
                               happened: list[str] | None = None) -> list[str]:
    happened = [] if happened is None else happened

    def make(real: Callable[..., object]) -> Callable[..., object]:
        def message(root: Path, recipient: str, sender: str, text: str,
                    **options: object) -> object:
            result = real(root, recipient, sender, text, **options)
            if text.startswith("FACTORY_LANE_AT_DIALOG ") and "died" not in happened:
                happened.append("died")
                raise SystemExit("fixture: the watcher died after its notice row")
            return result
        return message

    wrap_run_record("message", make, monkeypatch, folder / "message")
    return happened


def _dies_at_the_wake(lane: Lane, monkeypatch: pytest.MonkeyPatch, folder: Path) -> list[str]:
    lane.dies_at = "FACTORY_WAKE lane-at-dialog"
    return lane.died


def _append_record_fault(killed: bool) -> Setup:
    """``append_record`` writes the notice's row and raises OSError, or a killed writer writes it
    without its newline and the watcher dies."""
    def setup(lane: Lane, monkeypatch: pytest.MonkeyPatch, folder: Path) -> list[str]:
        happened: list[str] = []

        def make(real: Callable[..., object]) -> Callable[..., object]:
            def append(path: Path, payload: bytes, **options: object) -> object:
                if (Path(path).name != "inbox.jsonl" or happened
                        or b"FACTORY_LANE_AT_DIALOG " not in payload):
                    return real(path, payload, **options)
                if killed:
                    with Path(path).open("ab") as handle:
                        handle.write(payload[:-1])
                    happened.append("killed")
                    raise SystemExit("fixture: the writer was killed before its newline")
                real(path, payload, **options)
                happened.append("failed")
                raise OSError(5, "Input/output error after the row was committed", str(path))
            return append
        wrap_run_record("append_record", make, monkeypatch, folder)
        return happened
    return setup


def _offset_inside_a_rolled_back_row(lane: Lane, monkeypatch: pytest.MonkeyPatch,
                                     folder: Path) -> list[str]:
    """Another writer's short row is in the inbox when the watch reads its end, rolled back
    right after: the notice, committed at the restored end, holds the saved offset."""
    inbox, happened = lane.root / INBOX, []
    handoff = row_as_message_writes_it(folder / "handoff", "FACTORY_HANDOFF x")

    def hook(real: Callable[[], int]) -> int:
        start = inbox.stat().st_size
        with inbox.open("ab") as handle:
            handle.write(handoff)
        offset = real()
        os.truncate(inbox, start)
        happened.append("rolled-back" if offset == start + len(handoff) else str(offset))
        return offset

    on_inbox_end(lane, monkeypatch, folder / "end", hook)
    return _dies_after_the_notice_row(lane, monkeypatch, folder, happened)


def _uncommitted_append_at_the_offset_read(lane: Lane, monkeypatch: pytest.MonkeyPatch,
                                           folder: Path) -> list[str]:
    """Another process holds 8 KiB it has not committed when the watch reads the inbox's end,
    and rolls it back 1 s on."""
    inbox, happened = lane.root / INBOX, []
    committed = inbox.stat().st_size

    def hook(real: Callable[[], int]) -> int:
        happened.append(f"{inbox.stat().st_size - committed}-uncommitted")
        return real()

    on_inbox_end(lane, monkeypatch, folder / "end", hook)
    _dies_after_the_notice_row(lane, monkeypatch, folder, happened)
    lane.holders.append(Holder(inbox, append=8192, release_after=1.0))
    return happened


def _unsynced_copy_after_the_offset_read(lane: Lane, monkeypatch: pytest.MonkeyPatch,
                                         folder: Path) -> list[str]:
    """Just after the watch reads the inbox's end, another process appends the very notice the
    check owes (told by a lane run the same way), unsynced, and rolls it back 1 s on."""
    owed_tmux = Tmux(WORK)
    owed = Lane(folder / "owed", owed_tmux)
    assert owed.at(60) == "watching"
    owed_tmux.screen = CURSOR_APPROVAL
    assert owed.at(90) == "watching"
    row = folder / "row.jsonl"
    row.write_bytes(row_as_message_writes_it(folder / "copy", owed.notices()[0]))
    happened: list[str] = []

    def hook(real: Callable[[], int]) -> int:
        offset = real()
        lane.holders.append(Holder(lane.root / INBOX, append=row, release_after=1.0))
        happened.append("appended")
        return offset

    on_inbox_end(lane, monkeypatch, folder / "end", hook)
    return happened


@pytest.mark.parametrize(("setup", "earlier", "filler", "expected"), [
    pytest.param(_append_record_fault(killed=False), True, 88, ["failed", "1 at 90 s"],
                 id="write-failed-after-its-row-then-88-kib-of-other-rows"),
    pytest.param(_append_record_fault(killed=True), True, 0, ["killed", "0 at 90 s"],
                 id="writer-killed-before-its-newline"),
    pytest.param(_dies_at_the_wake, True, 0, ["died", "0 at 90 s"], id="watcher-died-at-the-wake"),
    pytest.param(_dies_after_the_notice_row, True, 0, ["died", "1 at 90 s"],
                 id="watcher-died-after-the-notice-row"),
    pytest.param(_offset_inside_a_rolled_back_row, True, 0, ["rolled-back", "died", "1 at 90 s"],
                 id="offset-inside-another-writers-rolled-back-row"),
    pytest.param(_offset_inside_a_rolled_back_row, False, 0, ["rolled-back", "died", "1 at 90 s"],
                 id="offset-inside-a-rolled-back-first-row"),
    pytest.param(_uncommitted_append_at_the_offset_read, True, 10,
                 ["8192-uncommitted", "died", "1 at 90 s"],
                 id="offset-read-under-the-lock-past-an-uncommitted-append"),
    pytest.param(_unsynced_copy_after_the_offset_read, True, 0, ["appended", "1 at 90 s"],
                 id="another-writers-unsynced-copy-rolled-back"),
])
def test_the_notice_reaches_the_inbox_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, setup: Setup, earlier: bool, filler: int,
    expected: list[str],
) -> None:
    """The 90 s check that first sees the dialog meets ``setup``'s fault while telling it;
    ``filler`` KiB of rows may follow; a fresh watcher checks at 120 s and 150 s. The inbox then
    holds the earlier row and one notice, newline-terminated, naming 90 s."""
    tmux = Tmux(WORK)
    lane = Lane(tmp_path / "lane", tmux)
    inbox = lane.root / INBOX
    assert lane.at(60) == "watching"
    if earlier:
        run_record.message(lane.root, "validator", "dispatcher", "FACTORY_FIXTURE earlier row")
    else:
        inbox.parent.mkdir(mode=0o700)
        inbox.touch(mode=0o600)
    happened = setup(lane, monkeypatch, tmp_path / "fixture")
    tmux.screen = CURSOR_APPROVAL
    with contextlib.suppress(SystemExit):
        lane.at(90)
    for holder in lane.holders:
        holder.release()
    told = sum(row.endswith(b"\n") and b"FACTORY_LANE_AT_DIALOG " in row
               for row in inbox.read_bytes().splitlines(keepends=True))
    assert [*happened, f"{told} at 90 s"] == expected
    for row in range(filler):
        run_record.message(lane.root, "validator", "dispatcher", f"FILLER {row} " + "x" * 1000)
    lane.restart()
    for seconds in (120, 150):
        assert lane.at(seconds) == "watching", seconds
    data = inbox.read_bytes()
    texts = [str(json.loads(row)["text"]) for row in data.splitlines()]
    notices = [text for text in texts if text.startswith("FACTORY_LANE_AT_DIALOG ")]
    assert data.endswith(b"\n") and len(notices) == 1 and names(notices[0], 90), texts
    assert [text for text in texts if not text.startswith("FILLER ")] == [
        *(["FACTORY_FIXTURE earlier row"] if earlier else []), notices[0]]
    assert leaked(lane, CURSOR_APPROVAL) == []


# --- Reviews, new lives, wrap-ups ------------------------------------------------------------

REVIEW_900 = {"runs_dir": "/runs", "review_every_s": 900, "stall_window_s": LONG_WINDOW_S}
WRAP = {"runs_dir": "/runs", "wrap_s": 300, "stall_window_s": LONG_WINDOW_S}


def every_30_s(lane: Lane, start: int, stop: int) -> None:
    for seconds in range(start, stop + 1, 30):
        assert lane.at(seconds) == "watching", seconds


def paused_at(lane: Lane, seconds: int) -> None:
    assert lane.at(seconds) in {"paused", "stopping"}, seconds
    assert lane.stops[-1:] == [TARGET] and lane.at(seconds + 30) == "paused", lane.stops


def test_held_time_is_not_work_a_review_is_owed_for_and_carries_into_a_new_life(
    tmp_path: Path,
) -> None:
    """Reviews every 900 s, a check every 30 s. The review falls due at the 900 s check, which
    first sees the approval; kept off while it shows, it starts at the read showing it gone
    (1530 s). Held time (900-1530 s) is not work and carries into the new life from 5000 s:
    the next review falls due 900 s of work later, at 5900 s."""
    tmux = Tmux(WORK)
    lane = Lane(tmp_path, tmux, agent="claude", watch=REVIEW_900, thread=True)
    every_30_s(lane, 30, 870)
    tmux.screen = CURSOR_APPROVAL
    every_30_s(lane, 900, 1500)
    assert lane.stops == [] and lane.delivered == [] and "lane-review-due" not in lane.kinds()
    tmux.screen = ANSWERED
    paused_at(lane, 1530)
    tmux.screen = WORK
    lane.resume(5000)
    every_30_s(lane, 5030, 5870)
    paused_at(lane, 5900)
    assert lane.kinds().count("lane-review-due") == 2 and lane.told() == (1, 1, 1)


def test_a_dialog_still_showing_across_a_new_life_keeps_its_bound_and_is_told_once(
    tmp_path: Path,
) -> None:
    """Cursor's approval from 60 s, never answered; a new life at 4000 s keeps the dialog: not
    told again, and bounded from 60 s."""
    tmux = Tmux(CURSOR_APPROVAL)
    lane = Lane(tmp_path, tmux, agent="claude")
    for seconds in (60, 3000):
        assert lane.at(seconds) == "watching", seconds
    lane.resume(4000)
    held_until(lane, 60 + HOLD_S + STALL_WINDOW_S, 4030, 9000, 60 + HOLD_S - 1)
    assert lane.told() == (1, 1, 1)


@pytest.mark.parametrize(("cap_s", "commit_at", "before", "wrap_at"), [
    pytest.param(3600, None, (3000,), None, id="held-no-wrap-up-and-its-cap-still-stops-it"),
    pytest.param(3600, 600, (630,), 3300, id="committed-past-it-wrap-up-as-usual"),
    pytest.param(18_000, None, (60 + HOLD_S - 1,), 17_700, id="past-its-bound-wrap-up-as-usual"),
])
def test_a_wrap_up_never_interrupts_a_held_lane_and_comes_as_usual_once_it_no_longer_holds(
    tmp_path: Path, cap_s: int, commit_at: int | None, before: tuple[int, ...],
    wrap_at: int | None,
) -> None:
    """A resumable lane with a wall cap is at Cursor's approval from 60 s; its wrap-up turn is
    due 300 s before the cap. Held (its read failing at the wrap-up time), it gets none and the
    cap stops it; committed past the dialog, or past the bound, it gets it as usual."""
    tmux = Tmux(CURSOR_APPROVAL)
    lane = Lane(tmp_path, tmux, agent="claude", caps={"wall_cap_s": cap_s}, watch=WRAP,
                thread=True)
    assert lane.at(60) == "watching"
    if commit_at is not None:
        commit(lane, commit_at)
    for seconds in before:
        assert lane.at(seconds) == "watching", seconds
    if wrap_at is None:
        tmux.capture_fault = CAPTURE_TIMEOUT
        assert lane.at(cap_s - 300) == "watching"
        tmux.capture_fault = None
        assert lane.at(cap_s - 270) == "watching" and lane.at(cap_s + 1) == "capped"
        assert lane.delivered == [] and "lane-wrap-up" not in lane.kinds()
    else:
        assert lane.at(wrap_at) in {"wrapping", "stopping"} and lane.at(wrap_at + 1) == "wrapping"
        assert len(lane.delivered) == 1 and "lane-wrap-up" in lane.kinds()
    assert lane.stops == [TARGET] and lane.told() == (1, 1, 1) and tmux.typed() == []
