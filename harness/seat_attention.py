"""Host dialogs on a session's pane: recognised, never answered.

A session waiting at its host's own dialog (a permission or folder-trust question, a panel that
takes its input, a usage limit) shows nothing in its logs. ``dialog_rows`` reads the pane's text
and names the dialog. Screen text is never authority to accept a permission or trust decision:
nothing here presses a key or decides a dialog.
"""

from __future__ import annotations

import re

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
        r"Esc to cancel \u00b7 Tab to amend|"
        # Cursor's per-command approval (seen 2026-11-17 on a Tester lane, which then sat at it
        # for 30 min until the lane watchdog stopped it as stalled).
        r"Not in allowlist: |Run Everything \(shift\+tab\)|"
        # Antigravity's approvals for a tool call, a command and a file outside its workspace
        # (seen 2026-11-17 on an Orchestrator, one at a time, through a run). A command's
        # question line sits above the command, so a long command is known by its footer.
        r"Allow calling this tool\?|Requesting permission for:|Allow access to this file\?|"
        r"ctrl\+g edit/expand command|"
        # ... and its approval for creating a file (seen 2026-11-17 on the same Orchestrator).
        r"Allow creation of this file\?"
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
    # A host panel that takes the seat's input until it is closed: Claude Code's "Teach auto mode
    # about your environment?" held a Validator for 1 h 43 min on 2026-11-17, with its own
    # background updates queued behind it ("Background update waiting while this panel is open").
    "host_panel": re.compile(
        r"Teach auto mode about your environment\?|"
        r"Background update waiting while this panel is open"
    ),
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
#: Non-blank rows above a dialog's matching row taken as the dialog's own, with the rest of the
#: block they belong to: what it asks about sits there (Cursor's approval names its command two
#: rows above its allowlist row; Claude Code breaks a long command over several).
CONTEXT_ROWS = 2


def dialog_rows(tail: str) -> dict[str, list[str]]:
    """Each known dialog kind the pane shows now, with the rows that show it, in screen order:
    every row of the pane's last ``TAIL_LINES`` rows of text that matches the kind; up to
    ``CONTEXT_ROWS`` non-blank rows above each, with the rows above them up to a blank row (a
    command Claude Code breaks over several rows is one block); and every non-blank row between
    its first and last matching rows (Codex's and Antigravity's approvals show what they ask
    about between their question and their footer). Each row is whole, with its indentation:
    two approvals may differ only there. A reader telling one dialog from another compares
    these, not one matching line."""
    lines = tail.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()  # capture-pane keeps a fresh pane's empty rows below the text
    window = lines[-TAIL_LINES:]
    shown: dict[str, list[str]] = {}
    for kind, pattern in PATTERNS.items():
        matches = [index for index, line in enumerate(window) if pattern.search(line)]
        if not matches:
            continue
        taken = set(range(matches[0], matches[-1] + 1))
        for index in matches:
            above = [row for row in range(index) if window[row].strip()]
            context = above[max(0, len(above) - CONTEXT_ROWS):]
            taken.update(context)
            top = context[0] if context else 0
            while top > 0 and window[top - 1].strip():
                top -= 1
                taken.add(top)
        shown[kind] = [window[index].rstrip() for index in sorted(taken) if window[index].strip()]
    return shown
