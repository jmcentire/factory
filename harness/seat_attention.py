"""Seat dialogs: observed and recorded for the run's roles from its start, never answered.

A seat sitting on a host dialog (a permission or folder-trust question, a usage limit) is
otherwise silent, and the run waits on it unseen. ``watch`` runs beside every run from its start,
whatever agents hold the seats: before the Orchestrator observes anything, when a coordinator's
agent cannot be sent messages, and when the Orchestrator is the seat that is stopped. A dialog
that stays is recorded in the run's events and told to the coordinator who owns that seat.
Screen text is never authority to accept a permission or trust decision: nothing here presses
a key or decides a dialog.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_HARNESS_ROOT = str(Path(__file__).resolve().parent)
if _HARNESS_ROOT not in sys.path:
    sys.path.insert(0, _HARNESS_ROOT)

PATTERNS: dict[str, re.Pattern[str]] = {
    # A host permission dialog: Claude Code's (a PreToolUse "ask" hook raises the same one) or
    # Codex's approval prompt. Nobody watches a seat's pane; run e's Validator sat 14 min on one.
    # Claude Code also asks before a file read outside its working directories (seen
    # 2026-11-17 on a Validator reading this checkout's docs/SOFTWARE-FACTORY.md). Claude Code
    # breaks its own lines, which capture-pane -J cannot rejoin, so its permission dialogs are
    # also known by their one-line footer.
    "permission_prompt": re.compile(
        r"Do you want to (?:proceed|make this edit|create|allow)\b|"
        r"tell (?:Claude|Codex) what to do differently|"
        r"Would you like to (?:run the following command|make the following edits)|"
        r"Allow command\?|"
        r"Allow this read outside the working directories\?|"
        r"Esc to cancel \u00b7 Tab to amend"
    ),
    # A folder-trust dialog: Claude Code's and Codex's first-launch prompt for a workdir,
    # Claude Code's question for an added directory it has not worked in (known by its option
    # row, since the question itself wraps), and Antigravity's "Do you trust the contents of
    # this project?" (both seen 2026-11-17 at a run's start).
    "folder_trust": re.compile(
        r"Do you trust the (?:files|contents) (?:in|of) this (?:folder|directory|project)|"
        r"Yes, I trust this folder|allow Codex to work in this folder|"
        r"^[\s\u276f\u203a>]*Yes, add it\s*$"
    ),
    # A fresh Claude Code may show this after the user selected bypass permissions.
    "bypass_permissions": re.compile(r"running in Bypass Permissions mode"),
    # A provider usage or quota limit: the seat looks alive and does nothing until it resets.
    # Anchored on each provider's own dialog line, never on words ordinary output also uses
    # ("rate limit reached", "resets in 5 m"): Claude Code and Codex print "You've hit your usage
    # limit" (run c: "You've hit your usage limit · resets 3pm (America/New_York)"); Claude Code
    # 2.1.299 also prints "You've hit your limit" and "Usage limit reached · <what happens next>"
    # (both read from that release's binary, 2026-10-14; the middle dot is U+00B7); agy prints
    # "Individual quota reached. Resets in 154h." (run e).
    "usage_limit": re.compile(
        r"\bYou(?:'|’)ve hit your (?:usage )?limit\b|"
        r"\bUsage limit reached ·|"
        r"\bIndividual quota reached\.?\s+Resets in \d+\s*[hm]"
    ),
}

#: A dialog is the pane's current state only while it is among the last rows of text.
TAIL_LINES = 15
#: A dialog that stays for less than this is being answered.
PERSIST_S = 30.0
#: How often the watch reads the seats' panes.
INTERVAL_S = 2.0


@dataclass(frozen=True)
class Dialog:
    """One occurrence of a dialog that has stayed on a seat's pane."""

    window: str
    kind: str
    line: str
    seconds: float

    def detail(self) -> str:
        return f"{self.kind}: {self.window} showed this for {self.seconds:.0f}s: {self.line}"


def dialogs_in(tail: str) -> dict[str, str]:
    """Decide which known dialogs a pane shows now: each kind and its first matching line."""
    lines = tail.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()  # capture-pane keeps a fresh pane's empty rows below the text
    shown: dict[str, str] = {}
    for kind, pattern in PATTERNS.items():
        hit = next((line.strip() for line in lines[-TAIL_LINES:] if pattern.search(line)), None)
        if hit is not None:
            shown[kind] = hit[:200]
    return shown


class Watch:
    """Report each dialog once per occurrence, once it has stayed for ``persist_s`` seconds.

    A dialog that clears and comes back is a new occurrence. A shorter stay is someone already
    answering it, so it is not reported.
    """

    def __init__(self, persist_s: float) -> None:
        self.persist_s = persist_s
        self.since: dict[tuple[str, str], float] = {}
        self.reported: set[tuple[str, str]] = set()

    def observe(self, window: str, tail: str, now: float) -> list[Dialog]:
        shown = dialogs_in(tail)
        for key in [key for key in self.since if key[0] == window and key[1] not in shown]:
            del self.since[key]
            self.reported.discard(key)
        due = []
        for kind, line in shown.items():
            key = (window, kind)
            first = self.since.setdefault(key, now)
            if now - first >= self.persist_s and key not in self.reported:
                self.reported.add(key)
                due.append(Dialog(window, kind, line, now - first))
        return due


def watch(root: Path) -> None:
    """Watch the run's seats until its tmux session or its inbox delivery stops."""
    from persistent_lane import read

    config = read(root / "harness.json")
    fd = os.open(root / "seat-watch.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # one watch per run: another already owns this run's seats
        dialogs = Watch(PERSIST_S)
        while running(root, config):
            watch_seats(root, config, dialogs, time.monotonic())
            time.sleep(INTERVAL_S)
    finally:
        os.close(fd)


def running(root: Path, config: dict) -> bool:
    """Decide whether the run is live: its session exists and completion has not stopped it."""
    if (root / "inbox-wake" / "stop.json").exists():
        return False
    alive = subprocess.run(
        ["tmux", "-L", config["tmux_socket"], "has-session", "-t", "=" + config["run_id"]],
        capture_output=True,
    )
    return alive.returncode == 0


def watch_seats(root: Path, config: dict, dialogs: Watch, now: float) -> None:
    """Record each seat dialog that has stayed."""
    for window in seat_windows(config):
        for dialog in dialogs.observe(window, pane(config, window), now):
            report(root, config, dialog)


def seat_windows(config: dict) -> list[str]:
    """Decide the run's seat windows: the coordinators and every author lane.

    Not seats: the Factory's own workers (this watch and the inbox delivery), and the watcher
    tmux_lane.sh opens beside each lane as "watch-<lane>".
    """
    names = tmux(config, "list-windows", "-t", "=" + config["run_id"], "-F", "#{window_name}")
    return [name for name in names.split()
            if name not in {"seat-watch", "inbox-wake"} and not name.startswith("watch-")]


def pane(config: dict, window: str) -> str:
    """The seat's visible text, wrapped rows joined so a split dialog line still matches."""
    return tmux(config, "capture-pane", "-pJ", "-t", f"={config['run_id']}:{window}", "-S", "-60")


def tmux(config: dict, *arguments: str) -> str:
    try:
        return subprocess.run(["tmux", "-L", config["tmux_socket"], *arguments],
                              capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""  # Degraded for one cycle: nothing read shows no dialog; the next cycle reads.


def report(root: Path, config: dict, dialog: Dialog) -> None:
    """Record the dialog with the dispatcher's event kind and tell the coordinator who owns it.

    A coordinator hears first, not the user (AUTHORITY H9): a role that can resolve the blocker
    does; only when none can does it become the user's question. The Validator owns the author
    lanes and the human relationship; each coordinator covers the other.
    """
    import run_record
    from dispatcher import append_jsonl, now

    events = root / "events.jsonl"
    append_jsonl(events, {
        "ts": now(), "kind": "seat_dialog_unanswered", "detail": dialog.detail()[:500],
        "wake": True, "source": "seat-watch",
    })
    owner = "orchestrator" if dialog.window == "validator" else "validator"
    try:
        run_record.message(root, owner, "host", (
            f"The {dialog.window} seat is waiting at a host dialog ({dialog.detail()}). The "
            "Factory never answers a seat's dialog. If no role can resolve it, tell the user it "
            "needs an answer in that window: "
            f"tmux -L {config['tmux_socket']} attach -t {config['run_id']}"
        ))
    except (OSError, ValueError) as error:
        # Degraded: the dialog is recorded above; the watch keeps watching the other seats.
        append_jsonl(events, {
            "ts": now(), "kind": "seat_dialog_notice_undelivered",
            "detail": f"{owner}: {error}"[:500], "wake": False, "source": "seat-watch",
        })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["watch"])
    parser.add_argument("--root", type=Path, required=True)
    watch(parser.parse_args().root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
