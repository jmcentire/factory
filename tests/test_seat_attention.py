"""A seat stopped at a host dialog is recorded for the run's roles, and never answered.

On 2026-11-17 a real run's Validator and Orchestrator each sat 14+ minutes at first-launch
dialogs and nothing recorded it: nothing on main watched seat panes. The seat watch runs
beside every run and records such dialogs for the coordinator that can resolve them
(AUTHORITY H9); it never presses a key.

Authority: normative. Every expected value comes from the seat-attention contract given to
the Tester and from the seats' own dialog text captured live with ``tmux capture-pane -pJ``
on 2026-11-17 (TESTING.md T1, T3), never from the implementation. Each test's docstring
names the wrong behavior that turns it red (T31). Where a value is inferred rather than
stated, the test says so.

Fidelity: the recognizer and the timer are pure and run on literal input with an injected
clock. Everything that touches tmux runs a real tmux server on a private ``-L`` socket that
the test kills; inbox notices go through the real ``run_record.message`` and events through
the real events file. The only substitutions are a ``tmux`` on PATH that logs and then execs
the real one (a recorder at the declared seam, T17), a ``tmux`` that is absent, hangs or marks
that it was called (to force the fail-soft and do-not-probe paths), and shortened
``PERSIST_S``/``INTERVAL_S`` for in-process watches. The ``run_record.start`` test runs the
seat watch as its own process with the real 30 s persistence.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import importlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from harness import run_record, seat_attention

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "harness"
TMUX = shutil.which("tmux")
needs_tmux = pytest.mark.skipif(TMUX is None, reason="requires a local tmux")


def payload(*lines: str) -> str:
    return "\n".join(lines) + "\n"


# The seats' own text, captured live on 2026-11-17 with `tmux capture-pane -pJ`. Verbatim,
# except that the operator's home prefix in three paths is replaced (TESTING.md T35: no
# contributor home paths in fixtures). No dialog-identifying line is touched.
READ_OUTSIDE = payload(  # Claude Code, Validator: a read outside the working directories
    " Read(/srv/fixture/factory/docs/SOFTWARE-FACTORY.md)",
    " Auto mode and the sandbox read outside the working directories without asking.",
    " Yes or No answers for this read; the rest is for later reads. Block: the file",
    " tools refuse reads outside the working directories in every project. The",
    " sandbox doesn't enforce it on this machine (off, filesystem rules relaxed,",
    " managed read-path lock on, or working directory name has glob characters).",
    " Auto mode asks before each shell command it can't check. To undo, remove",
    " permissions.blockReadsOutsideWorkingDirectories from your user settings; file",
    " tools follow next session, sandboxed commands at once.",
    " Allow this read outside the working directories?",
    " ❯ 1. Yes, and keep allowing any reads outside the working directories",
    "   2. No, and block reads outside the working directories from now on",
    "   3. No, and ask again next time",
    "   4. Yes, but ask again next time",
    " Esc to cancel · Tab to amend",
)
ADDED_DIRECTORY = payload(  # Claude Code, Validator: the run folder passed with --add-dir
    " Adding a new directory:",
    " /srv/fixture/replay/runs/r",
    " This session hasn't worked here before. Is this a directory you created or one",
    " you trust?",
    " Claude Code'll be able to read, edit, and execute files here. Its skills,",
    " commands, and agents load and can run commands, and so can the git settings of",
    " its repository. Your answer holds for this session.",
    " Security guide",
    " ❯ No, exit",
    "   Yes, add it",
    " Enter to confirm · Esc to cancel",
)
WORKSPACE_TRUST = payload(  # Claude Code, Validator: first launch in a repository
    " Accessing workspace:",
    " /srv/fixture/replay/repository",
    " Quick safety check: Is this a project you created or one you trust? (Like your",
    " own code, a well-known open source project, or work from your team). If not,",
    " take a moment to review what's in this folder first.",
    " Claude Code'll be able to read, edit, and execute files here.",
    " Security guide",
    " ❯ No, exit",
    "   Yes, I trust this folder",
    " Enter to confirm · Esc to cancel",
)
ANTIGRAVITY_TRUST = payload(  # Antigravity, Orchestrator: first launch
    "Accessing workspace:",
    "/srv/fixture/replay/repository",
    "Do you trust the contents of this project?",
    "Antigravity CLI requires permission to read, edit, and execute files here.",
    "> Yes, I trust this folder",
    "  No, exit",
    "  ↑/↓ Navigate · enter Confirm",
)
# Three more hosts' approval dialogs and a Claude Code panel, captured the same way on
# 2026-11-17. Cursor's home prefix is replaced (T35); the 80-column rule, the panel edge and
# Antigravity's status line are built to their exact captured characters and widths.
CURSOR_COMMAND = payload(  # Cursor, Tester lane: a command that is not on its allowlist
    " $  sha256sum /srv/fixture/factory-lanes/run/tester/.git/factory-brief/testing-standard.md"
    " && wc -c",
    "    /srv/fixture/factory-lanes/run/tester/.git/factory-brief/task.txt in .",
    " Run this command?",
    " Not in allowlist: sha256sum, wc",
    "  → Run (once) (y)",
    "    Add Shell(sha256sum), Shell(wc) to allowlist? (tab)",
    "    Run Everything (shift+tab)",
    "    Skip & tell the agent what to do instead (esc or n)",
    " ←/→ to switch approval",
)
RULE = "─" * 80
ANTIGRAVITY_STATUS = "esc to cancel" + " " * 46 + "Gemini 4 Argon · high"
ANTIGRAVITY_TOOL = payload(  # Antigravity, Orchestrator: an MCP tool call
    "● Called 1 MCP tool (kindex/search)",
    "MCP",
    RULE,
    "kindex/search",
    "Allow calling this tool?",
    "> 1. Yes, allow tool call",
    "  2. Yes, and always allow tool 'kindex/search' in this conversation",
    "  3. Yes, and always allow tool 'kindex/search' (Persist to settings.json)",
    "  4. No, deny tool call",
    "  5. No, and always deny tool 'kindex/search' in this conversation",
    "  6. No, and always deny tool 'kindex/search' (Persist to settings.json)",
    "  ↑/↓ Navigate · tab Amend",
    ANTIGRAVITY_STATUS,
)
ANTIGRAVITY_COMMAND = payload(  # Antigravity, Orchestrator: a shell command
    "Command",
    RULE,
    "Requesting permission for:",
    "   /opt/factory/.venv/bin/python /opt/factory/harness/dispatcher.py --run",
    "run --root /srv/runs/run --observe --wait-seconds 30 --after '{}'",
    "Run this command?",
    "> 1. Yes, run command",
    "  2. Yes, and always allow in this conversation for commands that start with",
    "'/opt/factory/.venv/bin/python /opt/factory/harness/dispatche...'",
    "  3. Yes, and always allow for commands that start with",
    "'/opt/factory/.venv/bin/python /opt/factory/harness/dispatche...' "
    "(Persist to settings.json)",
    "  4. No, cancel",
    "  ↑/↓ Navigate · tab Amend · ctrl+g edit/expand command",
    ANTIGRAVITY_STATUS,
)
ANTIGRAVITY_FILE = payload(  # Antigravity, Orchestrator: a read outside its workspace
    "○ Exploring 1 file (inbox.jsonl) (ctrl+o to expand)",
    "File access",
    RULE,
    "Read: /opt/factory/prompts/orchestrate.md",
    "Reason: outside workspace",
    "Allow access to this file?",
    "> 1. Yes, allow access",
    "  2. Yes, and always allow non-workspace access",
    "  3. No, deny access",
    "  ↑/↓ Navigate",
    ANTIGRAVITY_STATUS,
)
ANTIGRAVITY_CREATE = payload(  # Antigravity, Orchestrator: creating a file (no home path)
    "Create file",
    RULE,
    "/srv/runs/run/orchestrator/checkin-1.txt",
    "   1  +  Orchestrator regular quality check-in (initial approach + Coder commits",
    "         fb57e7e/70a5888 + Tester r2b suite):",
    "   2  +",
    "  … 11 more lines (f for full diff)",
    "Allow creation of this file?",
    "> 1. Yes, allow creation",
    "  2. No, deny creation",
    "  ↑/↓ Navigate · f full diff",
    ANTIGRAVITY_STATUS,
)
CLAUDE_PANEL = payload(  # Claude Code, Validator: a panel that holds the seat's input
    "✻ Cooked for 1m 52s · done 4:15 AM",
    "Background update waiting while this panel is open",
    "▔" * 80,
    "   Teach auto mode about your environment?",
    "   Claude Code reads this project, your recent Claude sessions, and",
    "   optionally your shell history and other repositories. Claude analyzes this",
    "   data and customizes auto mode to make better decisions.",
    "     How you use Claude here     Mixed",
    "   ❯ Also scan shell history     on",
    "     Also scan your other repos  off",
    "     Continue",
    "   ←/→ to change · Enter to continue · Esc to cancel",
)
# Ordinary seat output, for the lines around an isolated dialog line.
WORK = payload("Reading TASK.md", "Ran 12 tests in 0.41s", "OK")
ANTIGRAVITY_QUESTION = "Do you trust the contents of this project?"
USAGE_LIMIT = " You've hit your usage limit · resets 3pm (America/New_York)"


# --------------------------------------------------------------------------------------------
# The stated bounds and the one pattern table
# --------------------------------------------------------------------------------------------


def test_the_stated_bounds_and_dialog_kinds():
    """Direct from the contract. Red if persistence, polling or the tail window drift from the
    stated values, or a kind is renamed, dropped or added (each kind names a notice)."""
    assert seat_attention.PERSIST_S == 30.0
    assert seat_attention.INTERVAL_S == 2.0
    assert seat_attention.TAIL_LINES == 15
    assert set(seat_attention.PATTERNS) == {
        "permission_prompt", "folder_trust", "bypass_permissions", "host_panel", "usage_limit"}
    assert all(isinstance(p, re.Pattern) for p in seat_attention.PATTERNS.values())


def test_the_dispatcher_reads_the_seat_watchs_own_patterns():
    """Agreement (T1.5): one decision, two readers. Red if the dispatcher keeps its own copy of
    the dialog patterns, which would drift from the watch's."""
    from harness import dispatcher

    shared = dispatcher.SEAT_ATTENTION_PATTERNS
    # The dispatcher imports the adjacent module by its bare name, so the module object may be
    # loaded twice (as `seat_attention` and `harness.seat_attention`). The promise is that the
    # dispatcher's table *is* a seat_attention module's PATTERNS, read from harness/.
    owners = [module for module in list(sys.modules.values())
              if getattr(module, "PATTERNS", None) is shared]
    assert owners, "the dispatcher's pattern table belongs to no loaded module"
    assert all(Path(module.__file__).resolve() == HARNESS / "seat_attention.py"
               for module in owners)
    assert {kind: (p.pattern, p.flags) for kind, p in shared.items()} == {
        kind: (p.pattern, p.flags) for kind, p in seat_attention.PATTERNS.items()}


# --------------------------------------------------------------------------------------------
# dialogs_in: which dialogs a pane shows now
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (READ_OUTSIDE, "permission_prompt"),
        (ADDED_DIRECTORY, "folder_trust"),
        (WORKSPACE_TRUST, "folder_trust"),
        (ANTIGRAVITY_TRUST, "folder_trust"),
        (CURSOR_COMMAND, "permission_prompt"),
        (ANTIGRAVITY_TOOL, "permission_prompt"),
        (ANTIGRAVITY_COMMAND, "permission_prompt"),
        (ANTIGRAVITY_FILE, "permission_prompt"),
        (ANTIGRAVITY_CREATE, "permission_prompt"),
        (CLAUDE_PANEL, "host_panel"),
    ],
    ids=["claude-read-outside", "claude-added-directory", "claude-workspace", "antigravity",
         "cursor-command", "antigravity-tool", "antigravity-command", "antigravity-file",
         "antigravity-create", "claude-panel"],
)
def test_each_live_dialog_is_recognized_as_its_kind(text, kind):
    """The dialogs that stopped real seats, each in full. Red if any is missed (the seat waits
    unseen) or also reported as another kind (a second, wrong notice). Exactly one kind per
    dialog is inferred from the contract naming each a permission, trust or panel dialog."""
    assert set(seat_attention.dialogs_in(text)) == {kind}


@pytest.mark.parametrize(
    ("line", "kind", "reported"),
    [
        (" Allow this read outside the working directories?", "permission_prompt",
         "Allow this read outside the working directories?"),
        (" Esc to cancel · Tab to amend", "permission_prompt", "Esc to cancel · Tab to amend"),
        ("   Yes, add it", "folder_trust", "Yes, add it"),
        (" ❯ Yes, add it", "folder_trust", "❯ Yes, add it"),
        (" › Yes, add it", "folder_trust", "› Yes, add it"),
        (" > Yes, add it", "folder_trust", "> Yes, add it"),
        (ANTIGRAVITY_QUESTION, "folder_trust", ANTIGRAVITY_QUESTION),
        (" Not in allowlist: sha256sum, wc", "permission_prompt",
         "Not in allowlist: sha256sum, wc"),
        # <commands> is whatever Cursor names: a different command list is the same dialog.
        (" Not in allowlist: pytest", "permission_prompt", "Not in allowlist: pytest"),
        ("    Run Everything (shift+tab)", "permission_prompt", "Run Everything (shift+tab)"),
        ("Allow calling this tool?", "permission_prompt", "Allow calling this tool?"),
        ("Requesting permission for:", "permission_prompt", "Requesting permission for:"),
        ("  ↑/↓ Navigate · tab Amend · ctrl+g edit/expand command", "permission_prompt",
         "↑/↓ Navigate · tab Amend · ctrl+g edit/expand command"),
        ("Allow access to this file?", "permission_prompt", "Allow access to this file?"),
        ("Allow creation of this file?", "permission_prompt", "Allow creation of this file?"),
        ("   Teach auto mode about your environment?", "host_panel",
         "Teach auto mode about your environment?"),
        ("Background update waiting while this panel is open", "host_panel",
         "Background update waiting while this panel is open"),
    ],
    ids=["read-question", "permission-footer", "add-it", "add-it-claude-marker",
         "add-it-codex-marker", "add-it-ascii-marker", "antigravity-question",
         "cursor-allowlist", "cursor-allowlist-other-commands", "cursor-run-everything",
         "antigravity-tool", "antigravity-command", "antigravity-command-footer",
         "antigravity-file", "antigravity-create", "claude-panel-question",
         "claude-panel-status"],
)
def test_each_new_recognition_identifies_its_dialog_by_itself(line, kind, reported):
    """Each line this change recognizes, taken verbatim from its live payload, among ordinary
    output. The live payloads also carry older option rows, so this is what shows each new
    recognition exists. Red if one is missing, or if the reported line is not the matching
    line stripped of surrounding whitespace."""
    assert seat_attention.dialogs_in(WORK + line + "\n" + WORK) == {kind: reported}


@pytest.mark.parametrize("command_lines", [7, 20])
def test_an_antigravity_command_of_any_length_is_a_permission_dialog(command_lines):
    """Finding of 2026-11-17, contract amended the same day: "Requesting permission for:" sits
    above the command, so a long command pushes it out of the tail; the footer below the
    command still identifies the dialog. Red if a long command's approval waits unseen."""
    captured = ANTIGRAVITY_COMMAND.splitlines()
    # captured[3:5] is the captured command, hard-wrapped by Antigravity onto two lines.
    command = [captured[3]] + [f"run --root /srv/runs/run --lane lane-{n} --observe"
                               for n in range(command_lines - 1)]
    lines = captured[:3] + command + captured[5:]
    below = len(lines) - lines.index("Requesting permission for:") - 1
    assert below >= seat_attention.TAIL_LINES, "the old identifier must be out of the tail"
    assert set(seat_attention.dialogs_in(payload(*lines))) == {"permission_prompt"}


def test_a_hard_wrapped_claude_question_is_still_a_permission_dialog():
    """Claude Code hard-wraps its own question, so its footer identifies the dialog. Red if
    recognition depends on the question arriving on one line."""
    wrapped = READ_OUTSIDE.replace(
        " Allow this read outside the working directories?\n",
        " Allow this read outside the working\n directories?\n",
    )
    assert wrapped != READ_OUTSIDE
    assert set(seat_attention.dialogs_in(wrapped)) == {"permission_prompt"}


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Bash command\n  TaskStop monitor-1\nDo you want to proceed?\n❯ 1. Yes\n"
         "  2. No, and tell Claude what to do differently (esc)\n", "permission_prompt"),
        ("Would you like to run the following command?\n  $ rm -f x\n"
         "› 1. Yes, proceed\n  2. No, and tell Codex what to do differently\n",
         "permission_prompt"),
        ("Do you trust the files in this folder?\n❯ 1. Yes, proceed\n  2. No, exit\n",
         "folder_trust"),
        ("Do you trust the contents of this directory?\n"
         "› 1. Yes, allow Codex to work in this folder\n", "folder_trust"),
        ("Quick safety check: Is this a project you trust?\n"
         " ❯ No, exit\n Yes, I trust this folder\n", "folder_trust"),
        ("WARNING: Claude Code running in Bypass Permissions mode\n"
         " ❯ 1. No, exit\n 2. Yes, I accept\n", "bypass_permissions"),
        (USAGE_LIMIT + "\n", "usage_limit"),
        ("Individual quota reached. Resets in 154h.\n", "usage_limit"),
        (" Usage limit reached · continuing at 3pm\n", "usage_limit"),
    ],
    ids=["claude-proceed", "codex-run", "claude-trust-files", "codex-trust", "claude-safety",
         "bypass", "claude-usage", "codex-quota", "usage-reached"],
)
def test_pre_existing_dialogs_are_still_recognized(text, kind):
    """The contract keeps every earlier recognition; these are the dispatcher suite's captured
    dialogs. Red if the move into seat_attention dropped one."""
    assert kind in seat_attention.dialogs_in("earlier output\n" + text)


@pytest.mark.parametrize(
    "text",
    [
        "pytest: 3 passed; the client retries until the rate limit reached resets\n",
        "upstream API: rate limit reached, backing off; resets in 5 m\n",
        "the quota exceeded test now asserts a 429 with a usage limit header\n",
        "Cache entry resets in 2h; nothing to do\n",
        "Usage limit reached errors are retried by the client with backoff\n",
        # The read dialog's own explanation, without its question, options or footer.
        "".join(READ_OUTSIDE.splitlines(keepends=True)[:9]),
        "Changelog entry drafted. Yes, add it to the release notes as well.\n",
        "The reviewer answered: Yes, add it\n",
        # "Run this command?" alone is too generic to identify Cursor's dialog (stated).
        " Run this command?\n",
        # Cursor's own command and question, without the lines that identify its dialog.
        "".join(CURSOR_COMMAND.splitlines(keepends=True)[:3]),
        "Decided to allow calling this tool only for kindex searches.\n",
        "The coder kept requesting permission for network access, so I stopped it.\n",
        "Remember to allow access to this file in the sandbox profile.\n",
        "Commands not in allowlist are refused by the gate.\n",
        "Release notes: Claude Code can now teach auto mode about your environment.\n",
        # Antigravity's tool- and file-approval footers: only "ctrl+g edit/expand command"
        # makes its footer an identifier (stated), so these lines alone are not dialogs.
        "  ↑/↓ Navigate · tab Amend\n",
        "  ↑/↓ Navigate\n",
        # Antigravity's file-creation approval is identified by its question, not its footer.
        "If the review passes, we allow creation of this file later.\n",
        "  ↑/↓ Navigate · f full diff\n",
    ],
    ids=["retries", "upstream", "quota-test", "cache", "usage-errors", "read-explanation",
         "add-it-in-prose", "add-it-quoted", "bare-run-this-command",
         "cursor-question-without-identifiers", "allow-calling-in-prose",
         "requesting-permission-in-prose", "allow-access-in-prose", "allowlist-in-prose",
         "teach-auto-mode-in-prose", "antigravity-tool-footer", "antigravity-file-footer",
         "allow-creation-in-prose", "antigravity-create-footer"],
)
def test_ordinary_output_that_mentions_dialog_words_is_not_a_dialog(text):
    """Negative control. Red if a seat doing ordinary work is reported as waiting: "Yes, add
    it" counts only on its own line, prose about reads outside the working directories is not
    the question, a bare "Run this command?" identifies no host, and prose using the approval
    or panel phrases is not the dialog."""
    assert seat_attention.dialogs_in("working\n" + text) == {}


@pytest.mark.parametrize(
    ("lines_after", "empty_rows", "seen"),
    [
        (14, 0, True),    # the dialog is the 15th line from the end
        (15, 0, False),   # the 16th: outside the tail
        (14, 40, True),   # a fresh pane's empty rows below the text are not lines
        (15, 40, False),
    ],
)
def test_only_the_last_tail_lines_above_a_panes_empty_rows_are_read(lines_after, empty_rows,
                                                                   seen):
    """Derived from TAIL_LINES = 15 and dropping trailing blank lines. Red on an off-by-one
    tail, on reading the whole scrollback (an answered dialog would stay "shown"), or on
    counting a fresh pane's empty rows (dogfood #49: a dialog near the top went unseen)."""
    assert seat_attention.TAIL_LINES == 15
    text = USAGE_LIMIT + "\n" + "".join(f"step {n}\n" for n in range(lines_after))
    text += "\n" * empty_rows
    assert ("usage_limit" in seat_attention.dialogs_in(text)) is seen


@pytest.mark.parametrize(("lines_after", "seen"), [(14, True), (15, False)])
def test_blank_lines_inside_the_text_still_count_toward_the_tail(lines_after, seen):
    """Only trailing blank lines are dropped. Red if every blank line is dropped, which reads
    further back than the last 15 lines."""
    after = ["step"] * lines_after
    after[3] = after[7] = ""
    text = USAGE_LIMIT + "\n" + "\n".join(after) + "\n"
    assert ("usage_limit" in seat_attention.dialogs_in(text)) is seen


def test_the_reported_line_is_the_first_match():
    """Two folder-trust lines from this change's recognitions, in both orders. Red if the last
    or a fixed line is reported instead of the first."""
    first = WORK + ANTIGRAVITY_QUESTION + "\n" + "  Yes, add it\n"
    second = WORK + "  Yes, add it\n" + ANTIGRAVITY_QUESTION + "\n"
    assert seat_attention.dialogs_in(first) == {"folder_trust": ANTIGRAVITY_QUESTION}
    assert seat_attention.dialogs_in(second) == {"folder_trust": "Yes, add it"}


def test_a_long_dialog_line_is_reported_to_200_characters():
    """A joined (-J) line can be long. Red if the reported line exceeds 200 characters or is
    not that line's own start. Which of these lines still match is left to the patterns (a
    pattern may anchor to whole lines), but at least one must, or the bound is untested."""
    candidates = [
        ANTIGRAVITY_QUESTION + " (" + "/srv/fixture" + "/deep" * 60 + ")",
        USAGE_LIMIT + " · " + "upgrade to a plan with more usage " * 7,
        " Allow this read outside the working directories? " + "y" * 250,
    ]
    reported = {}
    for line in candidates:
        assert len(line.strip()) > 200
        found = seat_attention.dialogs_in(WORK + line + "\n")
        if found:
            (value,) = found.values()
            reported[line] = value
    assert reported, "no long dialog line matched, so the 200-character bound was not exercised"
    for line, value in reported.items():
        assert value == line.strip()[:200]


# --------------------------------------------------------------------------------------------
# Dialog and Watch: report once a dialog has persisted, once per occurrence
# --------------------------------------------------------------------------------------------


def test_a_dialog_describes_itself_with_rounded_seconds():
    """Direct from the stated format. Red if seconds are truncated rather than rounded, or a
    field is dropped from the notice."""
    dialog = seat_attention.Dialog("validator", "usage_limit", USAGE_LIMIT.strip(), 31.6)
    assert dialog.detail() == (
        f"usage_limit: validator showed this for 32s: {USAGE_LIMIT.strip()}")
    with pytest.raises(dataclasses.FrozenInstanceError):
        dialog.seconds = 0.0  # type: ignore[misc]


SHOWN = WORK + ANTIGRAVITY_QUESTION + "\n"
CLEARED = WORK + "Working on the task\n"


def reported(window: str, seconds: float) -> seat_attention.Dialog:
    return seat_attention.Dialog(window, "folder_trust", ANTIGRAVITY_QUESTION, seconds)


def test_a_persisting_dialog_is_reported_exactly_once():
    """Sequence with an injected clock. Red if a dialog is reported before 30 s (a transient
    prompt the seat answers itself), not at 30 s, or again on every later poll."""
    watch = seat_attention.Watch(30.0)
    steps = [(0.0, []), (29.9, []), (30.0, [reported("orchestrator", 30.0)]),
             (31.0, []), (300.0, [])]
    for now, expected in steps:
        assert watch.observe("orchestrator", SHOWN, now) == expected, now


def test_a_dialog_cleared_early_is_never_reported_and_its_next_showing_starts_over():
    """Red if the timer survives a cleared dialog: a seat answering prompts quickly would be
    reported as waiting."""
    watch = seat_attention.Watch(30.0)
    steps = [(0.0, SHOWN, []), (20.0, SHOWN, []), (25.0, CLEARED, []),
             (40.0, SHOWN, []), (50.0, SHOWN, []), (69.9, SHOWN, []),
             (70.0, SHOWN, [reported("validator", 30.0)])]
    for now, pane, expected in steps:
        assert watch.observe("validator", pane, now) == expected, now


def test_a_dialog_shown_again_after_it_cleared_is_a_new_occurrence():
    """Red if a window that was reported once is never reported again (#49: a second usage
    limit later in the run went unseen) or is reported early on its second showing."""
    watch = seat_attention.Watch(30.0)
    steps = [(0.0, SHOWN, []), (30.0, SHOWN, [reported("coder", 30.0)]),
             (40.0, CLEARED, []), (50.0, SHOWN, []), (79.0, SHOWN, []),
             (80.0, SHOWN, [reported("coder", 30.0)])]
    for now, pane, expected in steps:
        assert watch.observe("coder", pane, now) == expected, now


def test_windows_are_timed_independently():
    """Red if one window's dialog, or its clearing, moves another window's timer."""
    watch = seat_attention.Watch(30.0)
    assert watch.observe("validator", SHOWN, 0.0) == []
    assert watch.observe("validator", SHOWN, 20.0) == []
    assert watch.observe("orchestrator", SHOWN, 20.0) == []
    assert watch.observe("validator", SHOWN, 30.0) == [reported("validator", 30.0)]
    assert watch.observe("orchestrator", SHOWN, 30.0) == []
    assert watch.observe("validator", CLEARED, 45.0) == []
    assert watch.observe("orchestrator", SHOWN, 45.0) == []
    assert watch.observe("orchestrator", SHOWN, 50.0) == [reported("orchestrator", 30.0)]


def test_zero_persistence_reports_on_first_sight_and_no_dialog_reports_nothing():
    """Limit case of "shown for >= persist_s": 0 >= 0. Red if a first sighting can never be
    reported, or if a pane without a dialog yields a report."""
    watch = seat_attention.Watch(0.0)
    assert watch.observe("tester", CLEARED, 7.0) == []
    assert watch.observe("tester", SHOWN, 7.0) == [reported("tester", 0.0)]


# --------------------------------------------------------------------------------------------
# A private tmux server holding one run's session
# --------------------------------------------------------------------------------------------

# A seat that shows the given text and logs any key it receives, at once (cbreak: no Enter
# needed). Any bytes in the log mean someone answered or typed into the seat.
SEAT_SCRIPT = """\
import os, pathlib, select, sys, tty
sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())
sys.stdout.flush()
keys = pathlib.Path(sys.argv[2])
tty.setcbreak(0)
while True:
    if select.select([0], [], [], 5)[0]:
        try:
            data = os.read(0, 4096)
        except OSError:
            break
        if not data:
            break
        with keys.open("ab") as out:
            out.write(data)
"""


class Server:
    """A run's session on its own private tmux socket; the fixture always kills it."""

    def __init__(self, socket: str, folder: Path) -> None:
        assert TMUX is not None
        self.socket, self.folder, self.started = socket, folder, False
        self.script = folder / "seat.py"
        self.script.write_text(SEAT_SCRIPT)

    def tmux(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        assert TMUX is not None
        return subprocess.run([TMUX, "-L", self.socket, *arguments], capture_output=True,
                              text=True, timeout=10)

    def window(self, session: str, name: str, text: str) -> Path:
        """Open a window showing ``text``; return the file any keys it receives land in."""
        shown = self.folder / f"{session}-{name}.txt"
        shown.write_text(text, encoding="utf-8")
        keys = self.folder / f"{session}-{name}.keys"
        command = shlex.join([sys.executable, str(self.script), str(shown), str(keys)])
        exists = self.tmux("has-session", "-t", f"={session}").returncode == 0
        if not self.started:
            arguments = ["-f", os.devnull, "new-session", "-d", "-s", session, "-x", "80",
                         "-y", "30", "-n", name, command]
            self.started = True
        elif not exists:
            arguments = ["new-session", "-d", "-s", session, "-x", "80", "-y", "30",
                         "-n", name, command]
        else:
            arguments = ["new-window", "-d", "-t", f"={session}:", "-n", name, command]
        result = self.tmux(*arguments)
        assert result.returncode == 0, result.stderr
        last = text.rstrip("\n").splitlines()[-1].strip()
        deadline = time.monotonic() + 10
        while last not in self.tmux("capture-pane", "-pJ", "-t", f"={session}:{name}").stdout:
            assert time.monotonic() < deadline, f"window {name} never showed its text"
            time.sleep(0.05)
        return keys

    def kill(self) -> None:
        if self.started:
            self.tmux("kill-server")


@pytest.fixture
def run(tmp_path, monkeypatch):
    """A real run record (git repository, harness.json, TASK.md) and its private tmux server."""
    if TMUX is None:
        pytest.skip("requires a local tmux")
    for name in ("TMUX", "TMUX_PANE"):
        monkeypatch.delenv(name, raising=False)
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "--allow-empty", "-qm",
                    "base"], check=True)
    root = run_record.create(tmp_path / "runs", "seats", repository, b"Seat attention probe")
    config = json.loads((root / "harness.json").read_text())
    server = Server(config["tmux_socket"], tmp_path)
    try:
        yield root, config, server
    finally:
        server.kill()


def seat_rows(root: Path) -> list[dict]:
    path = root / "events.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [row for row in rows if row.get("kind") == "seat_dialog_unanswered"]


def inbox(root: Path, role: str) -> list[dict]:
    path = root / role / "inbox.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


DETAIL = re.compile(r"(?P<kind>\w+): (?P<window>\S+) showed this for (?P<seconds>\d+)s: "
                    r"(?P<line>.*)", re.S)


def in_background(target: Callable[..., object], *arguments: object):
    errors: list[BaseException] = []

    def body() -> None:
        try:
            target(*arguments)
        except BaseException as error:  # reported by the test, never swallowed
            errors.append(error)

    thread = threading.Thread(target=body, daemon=True)
    thread.start()
    return thread, errors


def await_condition(condition: Callable[[], object], seconds: float, what: str) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.05)


# --------------------------------------------------------------------------------------------
# tmux, seat_windows, pane: reading the run's own server
# --------------------------------------------------------------------------------------------


@needs_tmux
def test_tmux_speaks_only_to_the_runs_private_server(run):
    """Boundary. Red if the reader reaches a default or another run's server."""
    root, config, server = run
    server.window(config["run_id"], "validator", WORK)
    listed = seat_attention.tmux(config, "list-sessions", "-F", "#{session_name}")
    assert listed.split() == [config["run_id"]]
    elsewhere = dict(config, tmux_socket=config["tmux_socket"] + "-absent")
    assert seat_attention.tmux(elsewhere, "list-sessions", "-F", "#{session_name}") == ""


def test_tmux_fails_soft_when_tmux_is_absent(tmp_path, monkeypatch):
    """Forcing: an OSError from starting tmux. Red if the watch would crash (and stop
    watching) on a host where tmux is momentarily unavailable."""
    empty = tmp_path / "no-tools"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    config = {"run_id": "r", "tmux_socket": "factory-absent"}
    assert seat_attention.tmux(config, "list-windows", "-t", "=r") == ""


def test_tmux_fails_soft_when_tmux_hangs(tmp_path, monkeypatch):
    """Forcing: a tmux that never answers (a wedged server). Red if the watch waits forever.
    The contract names no timeout, so this waits for the call to return under a generous
    bound and reports how long it took."""
    stub = tmp_path / "hung"
    stub.mkdir()
    (stub / "tmux").write_text("#!/bin/sh\nexec sleep 600\n")
    (stub / "tmux").chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub}{os.pathsep}/usr/bin{os.pathsep}/bin")
    config = {"run_id": "r", "tmux_socket": "factory-hung"}
    result: list[str] = []
    began = time.monotonic()
    thread, errors = in_background(lambda: result.append(seat_attention.tmux(config, "ls")))
    thread.join(timeout=120)
    assert not thread.is_alive(), "tmux() did not return from a hung tmux within 120 s"
    assert errors == [] and result == [""], (errors, result)
    print(f"tmux() returned from a hung tmux after {time.monotonic() - began:.1f}s")


@needs_tmux
def test_seat_windows_lists_the_runs_seats_in_order_without_factory_workers(run):
    """Red if the watch reads its own window, the wake worker, a lane's watcher, or another
    session's windows, or loses the session's window order."""
    root, config, server = run
    session = config["run_id"]
    for name in ("validator", "orchestrator", "seat-watch", "inbox-wake", "coder",
                 "watch-coder", "tester", "watch-tester"):
        server.window(session, name, WORK)
    server.window("other", "intruder", WORK)
    assert seat_attention.seat_windows(config) == ["validator", "orchestrator", "coder", "tester"]


@needs_tmux
def test_seat_windows_of_a_session_that_is_gone_is_empty(run):
    root, config, server = run
    server.window("other", "validator", WORK)
    assert seat_attention.seat_windows(config) == []


@needs_tmux
def test_pane_reads_that_seats_text_with_wrapped_rows_joined(run):
    """Red if a pane is read from another session's same-named window, or without joining
    wrapped rows: an 80-column wrap splits a dialog line and its pattern no longer matches."""
    root, config, server = run
    long_line = "L" + "0123456789" * 30
    server.window(config["run_id"], "validator", payload("first", long_line, "last"))
    server.window("other", "validator", payload("OTHER SESSION"))
    text = seat_attention.pane(config, "validator")
    assert long_line in text.splitlines()
    assert "OTHER SESSION" not in text


# --------------------------------------------------------------------------------------------
# watch_seats: the seam with the events file and the coordinators' inboxes
# --------------------------------------------------------------------------------------------


def recording_tmux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put only a logging tmux on PATH: it records each call and execs the real tmux. With
    nothing else on PATH, a process the watch started by name would fail."""
    assert TMUX is not None
    folder = tmp_path / "recorder"
    folder.mkdir()
    log = tmp_path / "tmux-calls.log"
    (folder / "tmux").write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> {shlex.quote(str(log))}\n'
        f'exec {shlex.quote(TMUX)} "$@"\n')
    (folder / "tmux").chmod(0o755)
    monkeypatch.setenv("PATH", str(folder))
    return log


@needs_tmux
def test_watch_seats_records_each_dialog_for_the_coordinator_that_can_resolve_it(
        run, tmp_path, monkeypatch):
    """Seam through the real units. A Validator's dialog goes to the Orchestrator; the
    Orchestrator's and a lane's go to the Validator (H9). Red if a dialog is unrecorded,
    recorded twice, routed to the waiting seat itself, missing its wake/source, or if the watch
    reads its own worker windows, starts any process but tmux reads, or types into a seat."""
    root, config, server = run
    session, socket = config["run_id"], config["tmux_socket"]
    shown = {"validator": ADDED_DIRECTORY, "orchestrator": ANTIGRAVITY_TRUST,
             "seat-watch": READ_OUTSIDE, "inbox-wake": WORKSPACE_TRUST, "coder": READ_OUTSIDE,
             "watch-coder": ANTIGRAVITY_TRUST, "tester": WORK}
    keys = {name: server.window(session, name, text) for name, text in shown.items()}
    calls = recording_tmux(tmp_path, monkeypatch)
    dialogs = seat_attention.Watch(5.0)

    began = dt.datetime.now(dt.UTC)
    seat_attention.watch_seats(root, config, dialogs, 100.0)
    assert seat_rows(root) == []
    assert inbox(root, "validator") == [] and inbox(root, "orchestrator") == []
    seat_attention.watch_seats(root, config, dialogs, 105.0)
    ended = dt.datetime.now(dt.UTC)

    rows = seat_rows(root)
    expected = {"validator": "folder_trust", "orchestrator": "folder_trust",
                "coder": "permission_prompt"}
    found = {}
    for row in rows:
        assert row["wake"] is True and row["source"] == "seat-watch"
        stamp = dt.datetime.fromisoformat(row["ts"])  # inferred: ISO-8601, recording time
        assert stamp.tzinfo is not None
        assert began - dt.timedelta(seconds=2) <= stamp <= ended + dt.timedelta(seconds=2)
        parts = DETAIL.fullmatch(row["detail"])
        assert parts, row["detail"]
        window = parts["window"]
        assert window not in found, f"{window} reported twice"
        assert parts["seconds"] == "5"
        # The reported line is one of the seat's own lines (which one: the unit tests above).
        assert parts["line"] in {line.strip() for line in shown[window].splitlines()}
        found[window] = (parts["kind"], row["detail"])
    assert {window: kind for window, (kind, _) in found.items()} == expected

    routes = {"orchestrator": ["validator"], "validator": ["orchestrator", "coder"]}
    attach = f"tmux -L {socket} attach -t {session}"
    for owner, windows in routes.items():
        notices = inbox(root, owner)
        assert sorted(n["text"].split(" seat ")[0] for n in notices) == sorted(
            f"The {w}" for w in windows)
        for notice in notices:
            window = notice["text"].removeprefix("The ").split(" ", 1)[0]
            detail = found[window][1]
            assert notice["sender"] == "host"
            assert notice["text"].startswith(
                f"The {window} seat is waiting at a host dialog ({detail})")
            assert attach in notice["text"]

    # Exactly once: the same occurrence, polled again much later, adds nothing.
    seat_attention.watch_seats(root, config, dialogs, 500.0)
    assert len(seat_rows(root)) == 3
    assert len(inbox(root, "validator")) == 2 and len(inbox(root, "orchestrator")) == 1

    assert all(not path.exists() or path.read_bytes() == b"" for path in keys.values()), \
        "a key reached a seat"
    # Positive control: a key sent to a seat does land in its log.
    assert subprocess.run([TMUX, "-L", socket, "send-keys", "-t", f"={session}:tester", "1"],
                          capture_output=True).returncode == 0
    await_condition(lambda: keys["tester"].exists() and keys["tester"].read_bytes() == b"1", 10,
                    "the key-log positive control")
    reads = {"list-windows", "capture-pane", "list-panes", "display-message", "has-session",
             "list-sessions"}
    used = calls.read_text().splitlines()
    assert used and all(line.startswith(f"-L {socket} ") for line in used), used
    assert {line.split()[2] for line in used} <= reads, used


@needs_tmux
def test_a_dialog_is_recorded_even_when_its_notice_cannot_be_delivered(run, tmp_path):
    """Order: the event is written before the inbox notice. Forced by an Orchestrator inbox
    folder that is a symlink, which run_record.message refuses. Red if a failed notice also
    loses the durable record. Whether watch_seats then raises is left open by the contract."""
    root, config, server = run
    server.window(config["run_id"], "validator", ADDED_DIRECTORY)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "orchestrator").symlink_to(elsewhere, target_is_directory=True)
    dialogs = seat_attention.Watch(1.0)
    seat_attention.watch_seats(root, config, dialogs, 0.0)
    with contextlib.suppress(OSError, ValueError):
        seat_attention.watch_seats(root, config, dialogs, 1.0)
    (row,) = seat_rows(root)
    assert row["detail"].startswith("folder_trust: validator showed this for 1s: ")
    assert list(elsewhere.iterdir()) == []


# --------------------------------------------------------------------------------------------
# watch: the loop beside every run
# --------------------------------------------------------------------------------------------


def shorten(monkeypatch: pytest.MonkeyPatch, persist: float, interval: float) -> None:
    monkeypatch.setattr(seat_attention, "PERSIST_S", persist)
    monkeypatch.setattr(seat_attention, "INTERVAL_S", interval)


@needs_tmux
def test_watch_records_a_persisting_dialog_and_returns_when_the_session_ends(run, monkeypatch):
    """Real tmux, in process, timings shortened. Red if the loop never records, records the
    same occurrence again on later cycles, or outlives its run's session."""
    root, config, server = run
    server.window(config["run_id"], "validator", READ_OUTSIDE)
    server.window(config["run_id"], "orchestrator", WORK)
    shorten(monkeypatch, persist=1.0, interval=0.1)
    thread, errors = in_background(seat_attention.watch, root)
    await_condition(lambda: seat_rows(root) or not thread.is_alive(), 20, "a recorded dialog")
    assert errors == []
    assert server.tmux("kill-session", "-t", f"={config['run_id']}").returncode == 0
    thread.join(timeout=10)
    assert not thread.is_alive(), "the watch outlived its run's session"
    assert errors == []
    (row,) = seat_rows(root)
    assert row["detail"].startswith("permission_prompt: validator showed this for ")
    (notice,) = inbox(root, "orchestrator")
    assert notice["text"].startswith("The validator seat is waiting at a host dialog (")
    assert inbox(root, "validator") == []


def forbid_tmux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "forbidden"
    folder.mkdir()
    marker = tmp_path / "tmux-was-called"
    (folder / "tmux").write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\nexit 99\n")
    (folder / "tmux").chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}/usr/bin{os.pathsep}/bin")
    return marker


def test_a_completed_run_is_not_probed(tmp_path, monkeypatch):
    """Negative control. Completion writes inbox-wake/stop.json (the real inbox_wake.stop).
    Red if the watch of a finished run touches tmux, records anything, or keeps running."""
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "--allow-empty", "-qm",
                    "base"], check=True)
    root = run_record.create(tmp_path / "runs", "done", repository, b"Finished run")
    importlib.import_module("harness.inbox_wake").stop(root)
    assert (root / "inbox-wake/stop.json").exists()
    marker = forbid_tmux(tmp_path, monkeypatch)
    thread, errors = in_background(seat_attention.watch, root)
    thread.join(timeout=10)
    assert not thread.is_alive(), "the watch of a completed run kept running"
    assert errors == []
    assert not marker.exists(), "a completed run was probed"
    assert seat_rows(root) == [] and inbox(root, "validator") == []
    # Positive control: the module's own tmux reader does reach the marking stub.
    seat_attention.tmux(json.loads((root / "harness.json").read_text()), "list-sessions")
    assert marker.exists()


@needs_tmux
def test_completion_stops_a_running_watch_while_the_session_lives(run, monkeypatch):
    """Red if a watch keeps reading seats after the Validator completed the run because the
    session happens to stay open."""
    root, config, server = run
    server.window(config["run_id"], "validator", WORK)
    shorten(monkeypatch, persist=1000.0, interval=0.1)
    thread, errors = in_background(seat_attention.watch, root)
    await_condition(lambda: (root / "seat-watch.lock").exists() or not thread.is_alive(), 10,
                    "the watch to take its lock")
    assert thread.is_alive() and errors == []
    importlib.import_module("harness.inbox_wake").stop(root)
    thread.join(timeout=10)
    assert not thread.is_alive(), "completion did not stop the watch"
    assert errors == []
    assert server.tmux("has-session", "-t", f"={config['run_id']}").returncode == 0


DRIVER = """\
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from harness import seat_attention
seat_attention.PERSIST_S = float(sys.argv[3])
seat_attention.INTERVAL_S = float(sys.argv[4])
seat_attention.watch(Path(sys.argv[2]))
"""


@needs_tmux
def test_a_second_watch_on_a_run_returns_at_once_and_records_nothing(run):
    """Stated (2026-11-17): a watch that finds the run's lock already held returns at once, its
    process exits 0 with nothing on stderr, and it records nothing; only one watch runs per
    run. Each watch is a real process with shortened timings, so this holds whatever the lock
    mechanism. The owner has recorded before the second starts, so it holds the lock. Red if
    the second records (every notice doubled), waits for the lock (it would take over later,
    unseen), or ends with an error or a traceback."""
    root, config, server = run
    session = config["run_id"]
    server.window(session, "orchestrator", ANTIGRAVITY_TRUST)

    def start_watch() -> subprocess.Popen[str]:
        return subprocess.Popen([sys.executable, "-c", DRIVER, str(ROOT), str(root), "0.5",
                                 "0.1"], cwd=ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)

    owner, second = start_watch(), None
    try:
        await_condition(lambda: seat_rows(root) or owner.poll() is not None, 20,
                        "the first watch to record the dialog")
        assert owner.poll() is None, owner.communicate()
        second = start_watch()
        _, second_errors = second.communicate(timeout=10)
        assert second.returncode == 0, second_errors
        assert second_errors == ""
        # It returned while its run was still live and owned: it did not wait for the lock.
        assert server.tmux("has-session", "-t", f"={session}").returncode == 0
        assert owner.poll() is None
        assert len(seat_rows(root)) == 1
        assert len(inbox(root, "validator")) == 1
        assert server.tmux("kill-session", "-t", f"={session}").returncode == 0
        _, owner_errors = owner.communicate(timeout=15)
        assert owner.returncode == 0, owner_errors
    finally:
        for process in (owner, second):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
    assert len(seat_rows(root)) == 1


def test_the_watch_command_runs_as_a_script(tmp_path, monkeypatch):
    """The command run_record.start gives the seat-watch window, run as tmux would run it.
    Red if the script cannot start outside a package import (the window would die at once)."""
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "--allow-empty", "-qm",
                    "base"], check=True)
    root = run_record.create(tmp_path / "runs", "script", repository, b"Script probe")
    importlib.import_module("harness.inbox_wake").stop(root)
    marker = forbid_tmux(tmp_path, monkeypatch)
    result = subprocess.run([sys.executable, str(HARNESS / "seat_attention.py"), "watch",
                             "--root", str(root)], cwd=repository, capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert not marker.exists()


# --------------------------------------------------------------------------------------------
# run_record.start: every run gets a seat watch, whatever its agents
# --------------------------------------------------------------------------------------------


@needs_tmux
def test_every_run_starts_a_seat_watch_that_records_a_seat_at_a_dialog(tmp_path, monkeypatch):
    """Live probe of the fix with non-Claude seats (no inbox-wake). The Validator stub stops at
    Codex's trust dialog; the seat-watch window, a separate process with the real 30 s
    persistence, must record it for the Orchestrator. Red if the window is missing, its
    command dies, or it reports before 30 s. Takes about 35 s."""
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "--allow-empty", "-qm",
                    "initial"], check=True)
    release = tmp_path / "release"
    binary = tmp_path / "bin"
    binary.mkdir()
    stub = binary / "codex"
    stub.write_text(f"#!{sys.executable}\n" + f"""import pathlib, sys, time
if any("Act as Factory validator" in argument for argument in sys.argv):
    print("Do you trust the contents of this directory?", flush=True)
    print("› 1. Yes, allow Codex to work in this folder", flush=True)
    print("  2. No, quit", flush=True)
else:
    print("Reading the task", flush=True)
while not pathlib.Path({str(release)!r}).exists():
    time.sleep(0.2)
""")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary}:/opt/homebrew/bin:/usr/bin:/bin")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in ("FACTORY_CONFIG", "TMUX", "TMUX_PANE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FACTORY_SEAT_BILLING", "plan")
    for role in ("VALIDATOR", "ORCHESTRATOR", "CODER", "TESTER"):
        monkeypatch.setenv(f"FACTORY_{role}_AGENT", "codex")
        monkeypatch.setenv(f"FACTORY_{role}_MODEL", "fixture")
        monkeypatch.setenv(f"FACTORY_{role}_EFFORT", "")
    runs = tmp_path / "runs"
    root = run_record.create(runs, "watched", repository, b"Disposable seat-watch probe")
    doc = json.loads((root / "harness.json").read_text())
    tmux = ["tmux", "-L", doc["tmux_socket"]]
    try:
        began = time.monotonic()
        run_record.start(runs, "watched")
        windows = subprocess.run([*tmux, "list-windows", "-t", "=watched", "-F",
                                  "#{window_name}"], capture_output=True, text=True,
                                 check=True).stdout.split()
        assert sorted(windows) == ["orchestrator", "seat-watch", "validator"]
        await_condition(lambda: seat_rows(root), 90, "the seat watch to record the Validator")
        (row,) = seat_rows(root)
        parts = DETAIL.fullmatch(row["detail"])
        assert parts and parts["kind"] == "folder_trust" and parts["window"] == "validator"
        assert int(parts["seconds"]) >= 30, row["detail"]
        assert time.monotonic() - began >= 30
        assert row["wake"] is True and row["source"] == "seat-watch"
        (notice,) = inbox(root, "orchestrator")
        assert notice["sender"] == "host"
        assert notice["text"].startswith(
            f"The validator seat is waiting at a host dialog ({row['detail']})")
        assert f"tmux -L {doc['tmux_socket']} attach -t watched" in notice["text"]
        assert "seat-watch" in subprocess.run(
            [*tmux, "list-windows", "-t", "=watched", "-F", "#{window_name}"],
            capture_output=True, text=True).stdout.split()
    finally:
        release.touch()
        subprocess.run([*tmux, "kill-server"], capture_output=True, timeout=10)
