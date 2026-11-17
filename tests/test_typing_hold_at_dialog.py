"""A message is never typed into a terminal seat while its pane shows a host dialog.

On 2026-11-17 Claude Code opened its "Teach auto mode about your environment?" panel right
after reporting it was idle. The wrapper typed the Validator's next inbox batch into the
panel, the session never reported that prompt, the delivery stayed ``submitted`` and the
custody rule then held every later message: the Validator's inbox delivered nothing for the
rest of the run.

Authority: normative. Every expected value comes from the contract given to the Tester for
this change, and from ``seat_attention.dialogs_in``, the existing recognizer that contract
names as the definition of "the pane shows a host dialog". None comes from
``persistent_lane``'s implementation, which the Tester did not read (TESTING T1, T41). The
contract, as tested here:

1. Before the first byte of a new message is typed into a terminal session, the session's
   pane is read (``capture-pane -pJ -t <window @id> -S -60``) and judged by ``dialogs_in``.
   A program whose messages go through its own queue command is not checked.
2. ``harness.json`` gives ``tmux_socket`` and ``run_id``. A coordinator is the window named
   exactly as the seat in session ``run_id``. A lane is the window of the ``tmux_target`` of
   its last ``status: active`` launch row, whose session part must be ``run_id``. The name
   resolves to one ``@id`` by exact name in ``list-windows -t =<run_id>``.
3. While a dialog is shown nothing is typed and the delivery stays ``queued``; the pane is
   looked at again about every 2 s; once the dialog is gone the message is typed and
   ``observed``; later messages stay behind it.
4. Every missing or failed piece of that evidence means no hold: typing goes ahead.
5. A message already being typed is finished when a dialog appears.
6. Nothing presses a key to answer or dismiss a dialog.

``persistent_lane.host_dialog_shown(root, slot)`` implements 1, 2 and 4 and is tested
directly; 3, 5 and 6 are tested through the real wrapper.

Fidelity: every pane is a real tmux pane on a private ``-L`` socket that the test creates and
always kills; it runs a small program that shows chosen text and logs every key it receives,
so any key pressed into it is evidence (item 6). The dialog text is the verbatim live capture
kept in ``tests/test_seat_attention.py``. Wrapper tests run the real ``lane_agent.py run``
(``persistent_lane.run_terminal``), PTY, hooks, receipt store, inbox wake and lane dialogue,
with a stand-in program that calls no model and logs every byte it is given. The wrapper
itself runs outside tmux; it finds "its" pane only through ``harness.json`` and the launch
rows, exactly as item 2 says, so the pane the test drives is the one it reads.

Substitutions (TESTING T14, T17): a ``tmux`` on PATH that is absent, not executable, hangs,
or makes one subcommand exit non-zero after running the real one (its output intact); every
other subcommand execs the real tmux. ``FACTORY_NATIVE_SUBMIT_GAP_S``, the native session's
own setting for the pause between a paste and its Enter, is widened in one test so that a
message is observably still being typed when the dialog appears.
"""

from __future__ import annotations

import importlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest

from harness import run_record, seat_attention
from tests.test_seat_attention import ANTIGRAVITY_TOOL, CLAUDE_PANEL, SEAT_SCRIPT, WORK

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "harness"
TMUX = shutil.which("tmux")
pytestmark = pytest.mark.skipif(TMUX is None, reason="requires a local tmux")

#: Ambient settings a test must not inherit: an operator's tmux client, a socket named by
#: environment instead of harness.json, and the native session's typing pauses.
AMBIENT = ("TMUX", "TMUX_PANE", "FACTORY_TMUX_SOCKET", "FACTORY_NATIVE_SETTLE_S",
           "FACTORY_NATIVE_SUBMIT_GAP_S")
#: How long a held message is watched: three of the wrapper's 2 s looks. The positive control
#: in each wrapper test shows an unheld message reaching the program inside this same bound.
HOLD_S = 6.0
#: How long a message may take to be typed once the dialog is gone ("about every 2 s").
RELEASE_S = 10.0
#: Clears the screen and the scrollback, so a pane shows only what it is given next.
CLEAR = "\x1b[H\x1b[2J\x1b[3J"

PROSE = (  # Dialog words in ordinary output, without any dialog's own line.
    "Read docs/HARNESS.md: Claude Code can open a panel asking to teach auto mode about\n"
    "the environment, and a usage limit can stop a seat; the wrapper waits and never\n"
    "answers either. Do you want the summary? It is in NOTES.md.\n"
    "Ran 3 tests in 0.20s\n"
)
SCROLLED = CLAUDE_PANEL + "".join(f"step {n} done\n" for n in range(20))  # dismissed long ago


def eventually(condition: Callable[[], object], seconds: float, what: str):
    deadline = time.monotonic() + seconds
    while True:
        value = condition()
        if value:
            return value
        assert time.monotonic() < deadline, f"timed out after {seconds}s waiting for {what}"
        time.sleep(0.05)


def clean_environment(**extra: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in AMBIENT}
    env.update(extra)
    return env


# --------------------------------------------------------------------------------------------
# A private tmux server whose panes show what the test chooses
# --------------------------------------------------------------------------------------------


class Panes:
    """One private tmux server, never the operator's; every window logs the keys it gets."""

    def __init__(self, folder: Path) -> None:
        assert TMUX is not None
        self.socket = f"factory-hold-{uuid.uuid4().hex[:12]}"
        self.folder = folder
        folder.mkdir()
        self.script = folder / "seat.py"
        self.script.write_text(SEAT_SCRIPT)
        self.started = False
        self.texts = 0
        self.keys: dict[str, Path] = {}

    def tmux(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        assert TMUX is not None
        return subprocess.run([TMUX, "-L", self.socket, *arguments], capture_output=True,
                              text=True, timeout=10)

    def _command(self, text: str, keys: Path) -> str:
        self.texts += 1
        shown = self.folder / f"shown-{self.texts}.txt"
        shown.write_text(CLEAR + text, encoding="utf-8")
        return shlex.join([sys.executable, str(self.script), str(shown), str(keys)])

    def open(self, session: str, name: str, text: str) -> str:
        """Open window ``name`` in ``session`` showing ``text``; return its ``@id``."""
        keys = self.folder / f"keys-{len(self.keys)}.bin"
        tail = ["-P", "-F", "#{window_id}", "-n", name, self._command(text, keys)]
        size = ["-x", "120", "-y", "40"]
        if not self.started:
            self.started = True
            result = self.tmux("-f", os.devnull, "new-session", "-d", "-s", session, *size, *tail)
        elif self.tmux("has-session", "-t", f"={session}").returncode != 0:
            result = self.tmux("new-session", "-d", "-s", session, *size, *tail)
        else:
            result = self.tmux("new-window", "-d", "-t", f"={session}:", *tail)
        assert result.returncode == 0, result.stderr
        window = result.stdout.strip()
        self.keys[window] = keys
        self.await_showing(window, text)
        return window

    def show(self, window: str, text: str) -> None:
        """Make the window show ``text`` instead, and wait until its pane does."""
        command = self._command(text, self.keys[window])
        result = self.tmux("respawn-pane", "-k", "-t", window, command)
        assert result.returncode == 0, result.stderr
        self.await_showing(window, text)

    def capture(self, window: str) -> str:
        """The pane read exactly as the contract reads it."""
        result = self.tmux("capture-pane", "-pJ", "-t", window, "-S", "-60")
        assert result.returncode == 0, result.stderr
        return result.stdout

    def await_showing(self, window: str, text: str) -> None:
        """Readiness: the pane's last line is the text's, and it shows the dialogs ``text``
        shows (none, if none), judged by the contract's own recognizer."""
        last = text.rstrip().splitlines()[-1].strip()
        want = seat_attention.dialogs_in(text)

        def showing() -> bool:
            pane = self.capture(window)
            lines = [line for line in pane.splitlines() if line.strip()]
            return bool(lines) and lines[-1].strip() == last and (
                seat_attention.dialogs_in(pane) == want)

        eventually(showing, 10, f"window {window} to show its text")

    def keys_received(self) -> bytes:
        return b"".join(path.read_bytes() for path in self.keys.values() if path.exists())

    def address(self) -> str:
        """A ``TMUX`` value naming this server: what an operator's own client would carry."""
        result = self.tmux("display-message", "-p", "#{socket_path},#{pid},0")
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def kill(self) -> None:
        """End this server and remove its socket file, which tmux 3.4 leaves behind."""
        if not self.started:
            return
        found = self.tmux("display-message", "-p", "#{socket_path}").stdout.strip()
        self.tmux("kill-server")
        path = Path(found) if found else None
        if path is not None and path.name == self.socket and path.is_socket():
            path.unlink()


@pytest.fixture
def panes(tmp_path, monkeypatch):
    for name in AMBIENT:
        monkeypatch.delenv(name, raising=False)
    server = Panes(tmp_path / "panes")
    try:
        yield server
    finally:
        server.kill()


@pytest.fixture
def lane_module(monkeypatch):
    monkeypatch.syspath_prepend(str(HARNESS))
    return importlib.import_module("persistent_lane")


def new_run(tmp_path: Path, panes: Panes) -> tuple[Path, str]:
    """A run root named as its run id, with a harness.json naming the private server."""
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    root = tmp_path / run_id
    root.mkdir()
    configure(root, run_id=run_id, tmux_socket=panes.socket)
    return root, run_id


def configure(root: Path, **config: str) -> None:
    (root / "harness.json").write_text(json.dumps(config))


def launched(root: Path, slot: str, *rows: dict) -> None:
    folder = root / "tmux-lanes"
    folder.mkdir(exist_ok=True)
    with (folder / f"{slot}-launch.jsonl").open("a") as out:
        for row in rows:
            out.write(json.dumps(row) + "\n")


def active(target: str) -> dict:
    """The two fields of a launch row the contract reads."""
    return {"status": "active", "tmux_target": target}


def lane_launch(run_id: str, slot: str, agent: str) -> dict:
    """An active launch row as tmux_lane.sh records one, for the wrapper's own checks, which
    (before this change too) refuse a lane message whose launch row lacks its run and role."""
    return {"schema_version": "factory-tmux-lane-launch/1", "run_id": run_id, "role": slot,
            "lane": slot, "slot": slot, "mode": "launch", "agent": agent,
            **active(f"{run_id}:{slot}")}


# A ``persistent_lane.host_dialog_shown`` call in its own process, so a tmux on PATH is the one
# it starts whether the module looks tmux up per call or once at import.
PROBE = (
    "import json, pathlib, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import persistent_lane\n"
    "shown = persistent_lane.host_dialog_shown(pathlib.Path(sys.argv[2]), sys.argv[3])\n"
    "print(json.dumps(shown))\n"
)


def shown_with_tmux_from(directory: Path, root: Path, slot: str) -> object:
    result = subprocess.run(
        [sys.executable, "-c", PROBE, str(HARNESS), str(root), slot],
        env=clean_environment(PATH=str(directory)), capture_output=True, text=True,
        timeout=120)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def tmux_stub(directory: Path, body: str, *, executable: bool = True) -> Path:
    directory.mkdir()
    stub = directory / "tmux"
    stub.write_text("#!/bin/sh\n" + body)
    stub.chmod(0o755 if executable else 0o644)
    return stub


def refusing(subcommand: str, log: Path) -> str:
    """A tmux that runs the real command, keeps its output, but exits 1 for ``subcommand``."""
    real = shlex.quote(str(TMUX))
    return (f'echo "$*" >> {shlex.quote(str(log))}\n'
            f'case " $* " in *" {subcommand} "*) {real} "$@"; exit 1;; esac\n'
            f'exec {real} "$@"\n')


# --------------------------------------------------------------------------------------------
# host_dialog_shown: what counts as positive evidence (items 1 and 2)
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("seat", "dialog"), [("validator", CLAUDE_PANEL),
                                              ("orchestrator", ANTIGRAVITY_TOOL)])
def test_a_coordinators_own_window_showing_a_dialog_is_evidence(
        tmp_path, panes, lane_module, seat, dialog):
    """Items 1-2. Red if a coordinator's dialog is missed (the 2026-11-17 incident), if
    another window's dialog is attributed to this seat, or if a dialog that has closed still
    counts (the seat would never be typed to again)."""
    root, run_id = new_run(tmp_path, panes)
    other = "orchestrator" if seat == "validator" else "validator"
    window = panes.open(run_id, seat, dialog)
    panes.open(run_id, other, WORK)
    panes.open(run_id, "coder", dialog)
    assert lane_module.host_dialog_shown(root, seat) is True
    assert lane_module.host_dialog_shown(root, other) is False
    panes.show(window, WORK)
    assert lane_module.host_dialog_shown(root, seat) is False


@pytest.mark.parametrize("text", [WORK, PROSE, SCROLLED], ids=["work", "prose", "scrolled"])
def test_a_pane_without_a_dialog_now_is_no_evidence(tmp_path, panes, lane_module, text):
    """Item 1: "shown" means dialogs_in finds one in the pane's capture. Red if ordinary
    output, dialog words in prose, or a panel long since dismissed and scrolled above the
    pane's last rows would hold the seat's messages. Positive control: the same window then
    showing the panel is evidence."""
    root, run_id = new_run(tmp_path, panes)
    window = panes.open(run_id, "validator", text)
    assert seat_attention.dialogs_in(panes.capture(window)) == {}  # the fixture is what it says
    assert lane_module.host_dialog_shown(root, "validator") is False
    panes.show(window, CLAUDE_PANEL)
    assert lane_module.host_dialog_shown(root, "validator") is True


def test_a_lane_is_read_in_the_window_of_its_last_active_launch(tmp_path, panes, lane_module):
    """Item 2. Red if a lane's pane is taken from its slot name, from its first or any active
    launch, or from a later launch row that is not active."""
    root, run_id = new_run(tmp_path, panes)
    panes.open(run_id, "coder", WORK)  # the slot's own name: not how a lane is found
    panes.open(run_id, "coder-a", CLAUDE_PANEL)
    panes.open(run_id, "coder-b", WORK)
    panes.open(run_id, "coder-c", CLAUDE_PANEL)
    launched(root, "coder", active(f"{run_id}:coder-b"), active(f"{run_id}:coder-a"),
             {"status": "exited", "tmux_target": f"{run_id}:coder-b"})
    assert lane_module.host_dialog_shown(root, "coder") is True
    launched(root, "coder", active(f"{run_id}:coder-b"))
    assert lane_module.host_dialog_shown(root, "coder") is False
    launched(root, "coder", {"status": "planned", "tmux_target": f"{run_id}:coder-c"},
             {"status": "exited", "tmux_target": f"{run_id}:coder-a"})
    assert lane_module.host_dialog_shown(root, "coder") is False


def test_a_lane_without_an_active_launch_in_this_run_is_no_evidence(
        tmp_path, panes, lane_module):
    """Items 2 and 4. Red if a lane with no launch record, or none active, is read anyway, or
    if a launch whose target names another session is read there or in this run's session.
    Both sessions' "coder" windows show the panel, so either misreading turns this red.
    Positive control: the same target in this run's session is evidence."""
    root, run_id = new_run(tmp_path, panes)
    elsewhere = f"other-{uuid.uuid4().hex[:6]}"
    panes.open(run_id, "coder", CLAUDE_PANEL)
    panes.open(elsewhere, "coder", CLAUDE_PANEL)
    assert lane_module.host_dialog_shown(root, "coder") is False  # no launch record at all
    launched(root, "coder", {"status": "planned", "tmux_target": f"{run_id}:coder"},
             {"status": "exited", "tmux_target": f"{run_id}:coder"})
    assert lane_module.host_dialog_shown(root, "coder") is False
    launched(root, "coder", active(f"{elsewhere}:coder"))
    assert lane_module.host_dialog_shown(root, "coder") is False
    launched(root, "coder", active(f"{run_id}:coder"))
    assert lane_module.host_dialog_shown(root, "coder") is True


def test_a_window_counts_only_by_exact_name_once_in_the_runs_own_session(
        tmp_path, panes, lane_module):
    """Item 2. Red if the run's session is matched by prefix (tmux's own default for
    ``-t name``), if a window is matched by prefix or in another session, or if one of two
    windows with the seat's name is picked: each wrong reading here finds the panel."""
    root, run_id = new_run(tmp_path, panes)
    panes.open(f"{run_id}-old", "validator", CLAUDE_PANEL)
    assert lane_module.host_dialog_shown(root, "validator") is False  # no session run_id
    panes.open(run_id, "validator-old", CLAUDE_PANEL)
    panes.open(run_id, "orchestrator", WORK)
    assert lane_module.host_dialog_shown(root, "validator") is False  # no exact name
    panes.open(run_id, "validator", CLAUDE_PANEL)
    assert lane_module.host_dialog_shown(root, "validator") is True  # positive control
    panes.open(run_id, "validator", CLAUDE_PANEL)
    assert lane_module.host_dialog_shown(root, "validator") is False  # not unique


def test_a_run_record_that_does_not_name_the_server_and_session_is_no_evidence(
        tmp_path, panes, lane_module, monkeypatch):
    """Items 2 and 4. Red if, without harness.json or one of its two keys, the check falls
    back to the environment's socket or tmux client, the default server, or the run root's
    name: every one of those reaches a live server whose seat shows the panel. Positive
    control: the complete record is evidence."""
    root, run_id = new_run(tmp_path, panes)
    panes.open(run_id, "validator", CLAUDE_PANEL)
    monkeypatch.setenv("FACTORY_TMUX_SOCKET", panes.socket)
    monkeypatch.setenv("TMUX", panes.address())
    (root / "harness.json").unlink()
    assert lane_module.host_dialog_shown(root, "validator") is False
    configure(root, run_id=run_id)
    assert lane_module.host_dialog_shown(root, "validator") is False
    configure(root, tmux_socket=panes.socket)
    assert lane_module.host_dialog_shown(root, "validator") is False
    configure(root, run_id=run_id, tmux_socket=panes.socket)
    assert lane_module.host_dialog_shown(root, "validator") is True


# --------------------------------------------------------------------------------------------
# host_dialog_shown: tmux that fails is no evidence (item 4)
# --------------------------------------------------------------------------------------------


def test_a_tmux_command_that_fails_is_no_evidence_whatever_it_printed(
        tmp_path, panes, lane_module, monkeypatch):
    """Item 4. Red if a non-zero ``list-windows`` or ``capture-pane`` is read anyway: each
    stub runs the real command, so its output names the seat's window or shows the panel,
    and only its exit status says it failed. Also red if a server that is not running is
    replaced by the environment's tmux client. Positive control: a pass-through tmux on the
    same PATH arrangement finds the panel."""
    root, run_id = new_run(tmp_path, panes)
    panes.open(run_id, "validator", CLAUDE_PANEL)

    through = tmux_stub(tmp_path / "through", f"exec {shlex.quote(str(TMUX))} \"$@\"\n")
    assert shown_with_tmux_from(through.parent, root, "validator") is True

    listing_log = tmp_path / "listing.log"
    listing = tmux_stub(tmp_path / "listing", refusing("list-windows", listing_log))
    assert shown_with_tmux_from(listing.parent, root, "validator") is False
    assert "list-windows" in listing_log.read_text()  # the failure was the one it met

    capture_log = tmp_path / "capture.log"
    capture = tmux_stub(tmp_path / "capture", refusing("capture-pane", capture_log))
    assert shown_with_tmux_from(capture.parent, root, "validator") is False
    assert "capture-pane" in capture_log.read_text()  # it got as far as reading the pane

    monkeypatch.setenv("TMUX", panes.address())
    configure(root, run_id=run_id, tmux_socket=panes.socket + "-absent")
    assert lane_module.host_dialog_shown(root, "validator") is False


def test_tmux_that_is_missing_unusable_or_hung_is_no_evidence(tmp_path, panes):
    """Item 4. Red if the check raises (the wrapper would fail where it should type) or waits
    for ever when tmux is not on PATH, cannot be executed, or never answers. The contract
    names no timeout; the hung case waits under a generous bound and reports how long."""
    root, run_id = new_run(tmp_path, panes)
    panes.open(run_id, "validator", CLAUDE_PANEL)

    empty = tmp_path / "no-tmux"
    empty.mkdir()
    assert shown_with_tmux_from(empty, root, "validator") is False

    unusable = tmux_stub(tmp_path / "unusable", "exit 0\n", executable=False)
    assert shown_with_tmux_from(unusable.parent, root, "validator") is False

    pids = tmp_path / "hung.pids"
    sleep = shutil.which("sleep")
    assert sleep is not None
    hung = tmux_stub(tmp_path / "hung",
                     f"echo $$ >> {shlex.quote(str(pids))}\nexec {shlex.quote(sleep)} 600\n")
    began = time.monotonic()
    try:
        assert shown_with_tmux_from(hung.parent, root, "validator") is False
    finally:
        for pid in pids.read_text().split() if pids.exists() else []:
            try:
                os.kill(int(pid), signal.SIGKILL)  # only the stub's own sleep, if left
            except ProcessLookupError:
                pass
    assert pids.exists(), "the hung tmux was never started"
    print(f"host_dialog_shown returned from a hung tmux after {time.monotonic() - began:.1f}s")


# --------------------------------------------------------------------------------------------
# Through the real wrapper (items 3, 5 and 6)
# --------------------------------------------------------------------------------------------

# A stand-in for Claude Code's interactive terminal: it reports its session through the hooks
# the wrapper gave it, logs every byte it is given, notes when the first byte of each input and
# its Enter arrive, and reports each submitted prompt. It calls no model.
STAND_IN = r"""
import json, os, pathlib, subprocess, sys, time, tty
args = sys.argv[1:]
folder = pathlib.Path(__file__).parent
settings = json.loads(pathlib.Path(args[args.index('--settings') + 1]).read_text())
flag = '--resume' if '--resume' in args else '--session-id'
session = args[args.index(flag) + 1]
def hook(event, **fields):
    payload = dict(hook_event_name=event, session_id=session, cwd=os.getcwd(), **fields)
    for group in settings['hooks'][event]:
        for entry in group['hooks']:
            subprocess.run(entry['command'], shell=True, input=json.dumps(payload),
                           text=True, check=True, timeout=5)
def note(name, row):
    with (folder / name).open('a') as out:
        out.write(json.dumps(row) + '\n')
tty.setraw(0)
hook('SessionStart')
typed = (folder / 'typed.bin').open('ab', buffering=0)
buffer, first_at = b'', None
while True:
    try:
        byte = os.read(0, 1)
    except OSError:
        break
    if not byte:
        break
    typed.write(byte)
    if not buffer:
        first_at = time.monotonic()
        note('starts.jsonl', dict(at=first_at))
    buffer += byte
    if not buffer.endswith(b'\r'):
        continue
    text = buffer[:-1].decode().replace('\x1b[200~', '').replace('\x1b[201~', '')
    buffer = b''
    note('received.jsonl', dict(text=text, first_at=first_at, enter_at=time.monotonic()))
    hook('UserPromptSubmit', prompt=text)
    hook('Stop', stop_hook_active=False)
"""


class Hosted:
    """One wrapper hosting the stand-in for ``slot``, its seat window on the private server."""

    def __init__(self, root: Path, slot: str, window: str, panes: Panes,
                 child: subprocess.Popen, output) -> None:
        self.root, self.slot, self.window, self.panes = root, slot, window, panes
        self.folder = root if slot in ("validator", "orchestrator") else root / "tmux-lanes"
        self.child, self.output = child, output

    def raw(self) -> dict:
        path = self.folder / f"{self.slot}-persistent.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def rows(self, name: str) -> list[dict]:
        path = self.folder / name
        return [json.loads(row) for row in path.read_text().splitlines()] if path.exists() else []

    def received(self) -> list[str]:
        return [row["text"] for row in self.rows("received.jsonl")]

    def typed(self) -> bytes:
        path = self.folder / "typed.bin"
        return path.read_bytes() if path.exists() else b""

    def plan(self, text: str) -> str:
        dialogue = importlib.import_module("lane_dialogue")
        return dialogue.plan_message(self.root, sender="validator", lane=self.slot,
                                     message_kind="status-probe", text=text,
                                     basis="typing hold regression",
                                     authority="runtime-protocol")["message_id"]

    def request(self, message: str) -> str:
        driver = importlib.import_module("persistent_lane")
        return driver.request(self.root, self.slot, message, self.raw()["session_id"])["state"]

    def stop(self) -> None:
        if self.child.poll() is None:
            self.child.terminate()  # only our own wrapper, which ends the stand-in it hosts
        try:
            self.child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.child.kill()
            self.child.wait(timeout=5)
        self.output.close()


@pytest.fixture
def host(tmp_path, panes, monkeypatch):
    """Start the real wrapper for a seat whose window shows ordinary output."""
    monkeypatch.syspath_prepend(str(HARNESS))
    started: list[Hosted] = []

    def start(slot: str, **settings: str) -> Hosted:
        root, run_id = new_run(tmp_path, panes)
        folder = root if slot in ("validator", "orchestrator") else root / "tmux-lanes"
        folder.mkdir(exist_ok=True)
        window = panes.open(run_id, slot, WORK)
        if folder != root:
            launched(root, slot, lane_launch(run_id, slot, "claude"))
        executable = folder / "cl"
        executable.write_text(f"#!{sys.executable}\n" + STAND_IN)
        executable.chmod(0o755)
        (root / "prompt").write_text("initial")
        output = (root / "wrapper-output").open("w")
        child = subprocess.Popen(
            [sys.executable, str(HARNESS / "lane_agent.py"), "run", "--slot", slot,
             "--log", str(folder / f"{slot}-log"),
             "--exits", str(folder / f"{slot}-exits.jsonl"),
             "--prompt-arg", str(root / "prompt"), "--session-agent", "claude",
             "--session-file", str(folder / f"{slot}-thread-id"), "--persistent",
             "--run-root", str(root), "--", str(executable)],
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            env=clean_environment(**settings))
        hosted = Hosted(root, slot, window, panes, child, output)
        started.append(hosted)
        eventually(lambda: hosted.raw().get("phase") == "stop-reported" or child.poll() is not None,
                   30, "the stand-in's first prompt to be reported")
        assert child.poll() is None, (root / "wrapper-output").read_text()
        return hosted

    try:
        yield start
    finally:
        for hosted in started:
            hosted.stop()


def hold(still_held: Callable[[], None], seconds: float = HOLD_S) -> None:
    """Check, again and again for ``seconds``, that the message is still held."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        still_held()
        time.sleep(0.25)
    still_held()


def test_an_inbox_batch_waits_while_the_validator_shows_the_auto_mode_panel(host, panes):
    """Items 3 and 6, the 2026-11-17 incident itself, through the inbox wake. Red if the batch
    is typed into the panel (any byte reaching the program), reported anything but queued
    while the panel shows, never typed once it closes, typed twice, overtaken by a later
    inbox message, or if anything presses a key in the seat's pane. Positive control: with no
    dialog shown, a batch reaches the program within the same bound the hold is watched for."""
    seat = host("validator")
    wake = importlib.import_module("inbox_wake")

    def delivery() -> str:
        return wake.tick(seat.root)["recipients"]["validator"]["delivery"]

    def received(word: str) -> list[str]:
        return [text for text in seat.received() if word in text]

    run_record.message(seat.root, "validator", "founder", "CONTROL-7f3 nothing on screen")
    eventually(lambda: (delivery(), received("CONTROL-7f3"))[1], HOLD_S,
               "the control batch to be typed")
    eventually(lambda: delivery() == "observed", RELEASE_S, "the control batch observed")

    panes.show(seat.window, CLAUDE_PANEL)
    before = seat.typed()
    run_record.message(seat.root, "validator", "founder", "HELD-2c9 asked while the panel shows")
    assert delivery() == "queued"

    def still_held() -> None:
        assert delivery() == "queued"
        assert seat.typed() == before, "bytes reached the program while the panel showed"

    hold(still_held, HOLD_S / 2)
    run_record.message(seat.root, "validator", "founder", "BEHIND-5d1 sent after it")
    hold(still_held, HOLD_S / 2)
    assert panes.keys_received() == b""

    panes.show(seat.window, WORK)
    # Typed by the wrapper on its own once the panel closes: nothing asks again meanwhile.
    eventually(lambda: received("HELD-2c9"), RELEASE_S, "the held batch to be typed")
    eventually(lambda: delivery() == "observed", RELEASE_S, "the held batch observed")
    eventually(lambda: (delivery(), received("BEHIND-5d1"))[1], RELEASE_S,
               "the later batch to be typed after it")
    texts = seat.received()
    held = [i for i, text in enumerate(texts) if "HELD-2c9" in text]
    behind = [i for i, text in enumerate(texts) if "BEHIND-5d1" in text]
    assert len(held) == 1 and len(behind) == 1, texts
    assert held[0] < behind[0], texts
    assert panes.keys_received() == b""


def test_a_lane_message_waits_while_the_lane_shows_a_dialog(host, panes):
    """Items 3 and 6 for an author lane, found by its active launch row. Red if the message
    is typed (any byte) or leaves queued while the dialog shows, is not typed and observed
    once it closes, is typed twice, or is overtaken by a message requested after it.
    Positive control: with no dialog shown, a message is typed within the hold's bound."""
    lane = host("coder")

    control = lane.plan("CONTROL-a41 nothing on screen")
    lane.request(control)
    eventually(lambda: any("CONTROL-a41" in text for text in lane.received()), HOLD_S,
               "the control message to be typed")
    eventually(lambda: lane.request(control) == "observed", RELEASE_S, "the control observed")

    panes.show(lane.window, CLAUDE_PANEL)
    before = lane.typed()
    held = lane.plan("HELD-b72 asked while the dialog shows")
    assert lane.request(held) == "queued"
    behind = lane.plan("BEHIND-c03 asked after it")

    def still_held() -> None:
        assert lane.request(held) == "queued"
        assert lane.request(behind) not in ("submitted", "observed", "unknown")
        assert lane.typed() == before, "bytes reached the program while the dialog showed"

    hold(still_held)
    assert panes.keys_received() == b""

    panes.show(lane.window, WORK)
    eventually(lambda: lane.request(held) == "observed", RELEASE_S, "the held message observed")
    eventually(lambda: lane.request(behind) == "observed", RELEASE_S, "the later one observed")
    texts = lane.received()
    first = [i for i, text in enumerate(texts) if "HELD-b72" in text]
    second = [i for i, text in enumerate(texts) if "BEHIND-c03" in text]
    assert len(first) == 1 and len(second) == 1, texts
    assert first[0] < second[0], texts
    assert panes.keys_received() == b""


def test_a_message_being_typed_is_finished_when_a_dialog_appears(host, panes):
    """Item 5. The dialog appears after the program has the message's first byte and before
    its Enter (the native session's paste-to-Enter pause is widened to 4 s so this is
    observable, and the test proves it happened). Red if the rest of the message, its mark or
    its Enter is withheld, so the half-typed message is never observed while the dialog
    stays; or if a key is pressed in the pane."""
    lane = host("coder", FACTORY_NATIVE_SUBMIT_GAP_S="4")
    text = "MIDWAY-e58 being typed when the panel opens"
    message = lane.plan(text)
    starts = len(lane.rows("starts.jsonl"))
    lane.request(message)
    eventually(lambda: len(lane.rows("starts.jsonl")) > starts, HOLD_S,
               "the message's first byte to reach the program")
    panes.show(lane.window, CLAUDE_PANEL)
    shown_at = time.monotonic()
    eventually(lambda: lane.request(message) == "observed", 15,
               "the message to be finished and observed while the panel shows")
    [row] = [row for row in lane.rows("received.jsonl") if "MIDWAY-e58" in row["text"]]
    assert row["first_at"] < shown_at < row["enter_at"], (
        "the panel did not appear while the message was being typed", row, shown_at)
    assert text in row["text"]
    assert panes.keys_received() == b""


# A wrapper for a Codex lane, whose later messages go through ``codex queue`` and are never
# typed. Same arguments as tests/test_native_codex_queue_contract.py, without its fault seam.
CODEX_WRAPPER = r"""
import pathlib, sys
sys.path.insert(0, sys.argv[1])
import lane_agent
root = pathlib.Path(sys.argv[2])
folder = root / 'tmux-lanes'
raise SystemExit(lane_agent.run_lane(
    [sys.argv[3], '--no-daemon', '--no-alt-screen'], slot='coder',
    log=folder/'coder-log', exits=folder/'coder-exits.jsonl',
    prompt=root/'prompt', session_agent='codex-interactive',
    session_file=folder/'coder-thread-id', persistent=True, run_root=root))
"""


def test_a_queue_command_program_is_not_held_by_a_dialog(tmp_path, panes, lane_module):
    """Item 1: a program whose messages go through its own queue command is not checked.
    Red if a Codex lane's message waits for its pane's dialog to close instead of going to
    ``codex queue`` at once. The pane's dialog is shown to be evidence first, so a check, had
    it been applied, would have held the message."""
    from tests.test_native_codex_queue_contract import CODEX

    root, run_id = new_run(tmp_path, panes)
    folder = root / "tmux-lanes"
    folder.mkdir()
    panes.open(run_id, "coder", CLAUDE_PANEL)
    launched(root, "coder", lane_launch(run_id, "coder", "codex"))
    assert lane_module.host_dialog_shown(root, "coder") is True
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    executable = folder / "codex"
    executable.write_text(f"#!{sys.executable}\n" + CODEX)
    executable.chmod(0o755)
    (root / "prompt").write_text("initial literal native message")
    home = tmp_path / "operator"
    home.mkdir()
    env = clean_environment(HOME=str(home), CODEX_HOME=str(home / ".codex"),
                            PATH=str(folder) + os.pathsep + os.environ["PATH"])
    output = (root / "wrapper-output").open("w")
    child = subprocess.Popen(
        [sys.executable, "-c", CODEX_WRAPPER, str(HARNESS), str(root), str(executable)],
        cwd=project, env=env, stdin=subprocess.DEVNULL, stdout=output,
        stderr=subprocess.STDOUT)
    lane = Hosted(root, "coder", "", panes, child, output)
    try:
        eventually(lambda: lane.raw().get("phase") == "stop-reported" or child.poll() is not None,
                   30, "the Codex stand-in to report its first turn")
        assert child.poll() is None, (root / "wrapper-output").read_text()
        message = lane.plan("QUEUED-91d while the dialog shows")
        lane.request(message)
        eventually(lambda: any("QUEUED-91d" in row["message"] for row in lane.rows("queues.jsonl")),
                   HOLD_S, "the message to go to codex queue")
        assert lane.request(message) == "submitted"
        assert not (folder / "typed.bin").exists()
        assert panes.keys_received() == b""
    finally:
        (folder / "exit").touch()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            lane.stop()
        else:
            output.close()
