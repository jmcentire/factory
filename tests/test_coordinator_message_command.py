"""Normative: the Orchestrator is told the one command that messages the Validator.

Source: the source-free coordinator-message-command contract, revised 2026-11-17 (items
1-5). Background: the Orchestrator's start prompt said only "use run_record.py message"; it
guessed `--text`, then `--run X --root Y`, and each wrong guess was a failed call plus another
human approval in Antigravity. The Validator's prompt stays as it was at 82b7eed, because a
kickoff Validator's retained brief must still match when a failed start is retried after an
upgrade.

Oracles: the exact command is stated in the contract (direct). Whether it works is
established by running it, unchanged except for FILE, through the real
`harness/run_record.py` CLI and reading the Validator's inbox (law: one message sent, one row
received, with that sender and that text). The 82b7eed Validator brief below is a
characterization fixture (TESTING T30): it was captured from a kickoff start that failed on
82b7eed, with machine paths replaced by tokens; the retry check that consumes it is the
behavior under test.

Fidelity: prompts come from the real `run_record.start` through the existing coordinator
startup fixture, which records tmux calls instead of starting sessions (the relative-runs
test repeats that recorder for a start it calls itself). Commands run for real, shell-free,
from the seat's working directory with a declared environment. No doubles.
"""

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from harness import run_record
from tests.test_coordinator_startup import launch as launch

REPO = Path(__file__).resolve().parents[1]
HARNESS = REPO / "harness"
GUIDE = REPO / "prompts" / "orchestrate.md"
TEXT = 'Finding: tests/test_x.py asserts nothing.\nEvidence: "line 12", $(not a command)\n'
MESSAGE_COMMAND = "run_record.py message --runs"

# Characterization: the Validator start prompt as 82b7eed wrote it to validator/BRIEF.md for
# a kickoff Validator, before the builder-attribution suffix (rendered below).
VALIDATOR_BRIEF_82B7EED = (
    "Act as Factory validator. Read <HOME>/prompts/validate.md, <HOME>/docs/SOFTWARE-FACTORY.md "
    "and <ROOT>/TASK.md. Run <RUN>; repository <REPOSITORY>; commit <COMMIT>. Use the harness "
    "at <TOOLS>. Read <ROOT>/validator/inbox.jsonl when checking progress. Both coordinators "
    "review actual code and tests at the initial approach, first useful increment, material "
    "progress and each correction round under Regular quality check-ins. Guide early; drop "
    "unproductive arguments immediately. Escalate an unresolved issue or disputed importance "
    "to the user after three to four meaningful correction rounds; consensus is not required. "
    "Remain in this conversation until the Validator completes the task. At completion run "
    "<PYTHON> <TOOLS>/inbox_wake.py stop --root <ROOT> to stop inbox polling. Use relevant "
    "Kindex history before scans and material decisions. Current user instructions govern. "
    "Preserve assigned agents and models. Do not create a catalog, signatures, waivers or "
    "approval paperwork."
)


def legacy_validator_brief(root: Path, doc: dict) -> bytes:
    text = VALIDATOR_BRIEF_82B7EED
    for token, value in {"<PYTHON>": sys.executable, "<TOOLS>": str(HARNESS),
                         "<HOME>": str(REPO), "<ROOT>": str(root), "<RUN>": root.name,
                         "<REPOSITORY>": doc["repository"],
                         "<COMMIT>": doc["resolved_commit"]}.items():
        text = text.replace(token, value)
    if isinstance(doc.get("factory_version"), dict) and doc["factory_version"].get("name"):
        # The attribution suffix's own format is pinned by test_builder_provenance.
        text += f" Retained builder attribution: {json.dumps(run_record.builder_attribution(doc))}."
    return text.encode()


def start_prompt(root: Path, role: str, command: list[str]) -> str:
    """The prompt a coordinator receives, wherever its adapter carries it."""
    prompt_file = root / role / "initial-prompt.txt"
    return prompt_file.read_text() if prompt_file.exists() else command[-1]


def rows(inbox: Path) -> list[dict]:
    if not inbox.exists():
        return []
    return [json.loads(line) for line in inbox.read_text().splitlines()]


def deliver(argv: list[str], inbox: Path, cwd: Path, home: Path) -> dict:
    """Run a message command shell-free; return the one row it added to the inbox."""
    before = rows(inbox)
    result = subprocess.run(argv, cwd=cwd, env={"PATH": "/usr/bin:/bin", "HOME": str(home)},
                            capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    after = rows(inbox)
    assert after[: len(before)] == before and len(after) == len(before) + 1
    return after[-1]


def assert_orchestrator_can_message_validator(prompt: str, root: Path, seat_cwd: Path,
                                              tmp_path: Path) -> None:
    # Item 1: the exact command, with this interpreter, harness, absolute runs folder and run.
    expected = (f"{sys.executable} {HARNESS}/run_record.py message "
                f"--runs {root.parent.resolve()} --run {root.name} "
                "--to validator --from orchestrator --message-file FILE")
    assert expected in prompt
    # Item 3: the command as the prompt gives it works with FILE replaced by a real file.
    found = re.search(r"(\S+ \S*run_record\.py message [^\n`]*?--message-file FILE)",
                      prompt.replace("`", ""))
    assert found, "the prompt gives no complete run_record.py message command"
    finding = tmp_path / "finding.txt"
    finding.write_text(TEXT)
    argv = [str(finding) if word == "FILE" else word for word in shlex.split(found.group(1))]
    home = tmp_path / "seat-home"
    home.mkdir(exist_ok=True)
    row = deliver(argv, root / "validator" / "inbox.jsonl", seat_cwd, home)
    assert row["sender"] == "orchestrator" and row["text"] == TEXT


def test_orchestrator_prompt_names_the_command_that_messages_the_validator(launch, tmp_path):
    """Falsifiers: no command, or a guessable fragment (`use run_record.py message`); a wrong
    flag (`--text`, `--root RUN_ROOT`); the run root instead of the runs folder; `python3`
    or another checkout's harness instead of the running ones; --to/--from swapped; a
    placeholder other than FILE; a command that does not run as printed; or the command
    also added to the Validator's prompt (which breaks kickoff retries, see below)."""
    root, repository, _, capture, invocation, _ = launch
    calls = capture()
    validator = start_prompt(root, "validator", invocation(calls, "validator")[2])
    assert MESSAGE_COMMAND not in validator  # item 2
    orchestrator = start_prompt(root, "orchestrator", invocation(calls, "orchestrator")[2])
    assert_orchestrator_can_message_validator(orchestrator, root, repository, tmp_path)


def test_relative_runs_folder_still_gives_an_absolute_command_that_works(
    launch, tmp_path, monkeypatch,
):
    """Item 5. Falsifier: the prompt repeats the relative runs path the run was started with,
    so the command fails from the seat's working directory (the repository)."""
    root, repository, _, _, invocation, _ = launch
    calls = []

    def tmux_only(argv, **kwargs):  # the startup fixture's recorder, for this start
        if argv == ["ps", "-A", "-o", "pid=,ppid=,lstart=,stat=,time="]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        assert argv[0] == "tmux", "startup must not probe or execute native agents"
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1 if "has-session" in argv else 0)

    started_from = root.parent.parent
    relative = Path(os.path.relpath(root.parent, started_from))
    assert not relative.is_absolute()  # fixture precondition
    monkeypatch.chdir(started_from)
    with monkeypatch.context() as isolated:
        isolated.setattr(subprocess, "run", tmux_only)
        run_record.start(relative, root.name)
    assert repository.resolve() != started_from.resolve()  # the seat runs elsewhere
    orchestrator = start_prompt(root, "orchestrator", invocation(calls, "orchestrator")[2])
    assert_orchestrator_can_message_validator(orchestrator, root, repository, tmp_path)


def test_kickoff_validator_brief_retained_by_82b7eed_still_allows_a_retry(launch, monkeypatch):
    """Item 2. Falsifier: any change to the Validator's start prompt, which makes a kickoff
    run whose start failed before the upgrade refuse every retry with "retained kickoff
    brief changed". The negative control shows the retry really compares the retained
    brief, so passing means it matched."""
    root, _, _, capture, _, _ = launch
    monkeypatch.setenv("FACTORY_VALIDATOR_AGENT", "kickoff")
    _, doc = run_record.load(root.parent, root.name)
    retained = legacy_validator_brief(root, doc)
    brief = root / "validator" / "BRIEF.md"
    brief.parent.mkdir(mode=0o700, exist_ok=True)

    brief.write_bytes(retained + b" Changed since.")
    brief.chmod(0o600)
    with pytest.raises(ValueError, match="retained kickoff brief changed"):
        capture()

    brief.write_bytes(retained)
    capture()
    assert brief.read_bytes() == retained
    assert MESSAGE_COMMAND not in retained.decode()


@pytest.fixture
def plain_run(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo = tmp_path / "product"
    repo.mkdir()
    for args in (["init", "-q"], ["commit", "--allow-empty", "-qm", "initial"]):
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.invalid",
                        "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)
    root = run_record.create(tmp_path / "runs", "task", repo, b"Keep the coordinators talking.\n")
    return root, repo, home


def test_orchestrate_guide_gives_the_same_command_shape_and_it_runs(plain_run, tmp_path):
    """Item 4. Falsifiers: the guide documents a flag the CLI rejects (the guessed `--text`,
    `--root`) or addresses the wrong coordinator. The documented shape is checked by filling
    its placeholders and running it against the real CLI."""
    root, repo, home = plain_run
    found = re.search(r"run_record\.py message [^`\n]*?--message-file FILE", GUIDE.read_text())
    assert found, "prompts/orchestrate.md gives no complete run_record.py message command"
    assert found.group(0) == ("run_record.py message --runs RUNS --run RUN --to validator "
                              "--from orchestrator --message-file FILE")
    finding = tmp_path / "finding.txt"
    finding.write_text(TEXT)
    values = {"RUNS": str(root.parent), "RUN": root.name, "FILE": str(finding)}
    words = shlex.split(found.group(0))
    argv = [sys.executable, str(HARNESS / words[0]), *(values.get(word, word)
                                                       for word in words[1:])]
    row = deliver(argv, root / "validator" / "inbox.jsonl", repo, home)
    assert row["sender"] == "orchestrator" and row["text"] == TEXT
