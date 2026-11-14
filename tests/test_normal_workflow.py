"""Ordinary task behavior without a catalog, signatures, model approval or live agents."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from harness import run_record
from harness.validate_candidate import validate

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "repo with spaces"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True
    )
    (repo / "value.txt").write_text("correct\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
    return repo


def test_task_to_exact_candidate_result_without_paperwork(tmp_path, repository):
    runs = tmp_path / "runs"
    root = run_record.create(runs, "ordinary", repository, b"Keep the correct value.\n")
    _, doc = run_record.load(runs, "ordinary")
    assert doc["log"] == os.devnull
    assert sorted(p.name for p in root.iterdir()) == [".kindex-no-index", "TASK.md", "harness.json"]
    # A dirty operator checkout must not change the subject or be overwritten.
    (repository / "value.txt").write_text("uncommitted user work\n")
    code, result_root = validate(
        runs,
        "ordinary",
        repository,
        doc["resolved_commit"],
        [
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('value.txt').read_text() == 'correct\\n'",
        ],
    )
    assert code == 0
    result = json.loads((result_root / "result.json").read_text())
    assert result["candidate"]["commit"] == doc["resolved_commit"]
    assert result["status"] == "tests-passed"
    assert (repository / "value.txt").read_text() == "uncommitted user work\n"
    assert not (root / "grounded").exists()


def test_failure_and_missing_command_remain_real_results(tmp_path, repository):
    runs = tmp_path / "runs"
    run_record.create(runs, "task", repository, b"Do a task")
    _, doc = run_record.load(runs, "task")
    for command, expected in (
        ([sys.executable, "-c", "raise SystemExit(7)"], 7),
        (["/does/not/exist"], 127),
    ):
        code, result = validate(runs, "task", repository, doc["resolved_commit"], command)
        assert code == expected
        assert json.loads((result / "result.json").read_text())["status"] == "tests-failed"


def test_hostile_git_configuration_cannot_change_candidate_or_execute(tmp_path, repository):
    runs = tmp_path / "runs"
    run_record.create(runs, "task", repository, b"Read the exact candidate")
    _, doc = run_record.load(runs, "task")
    marker = tmp_path / "executed"
    subprocess.run(
        ["git", "-C", str(repository), "config", "core.fsmonitor", f"touch {marker}"], check=True
    )
    (repository / ".git/info/attributes").write_text(
        "value.txt export-ignore\n* filter=plant\n"
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "filter.plant.smudge", f"touch {marker}"],
        check=True,
    )
    code, result = validate(
        runs,
        "task",
        repository,
        doc["resolved_commit"],
        [sys.executable, "-c", "from pathlib import Path; assert Path('value.txt').exists()"],
    )
    assert code == 0 and not marker.exists()
    assert (result / "candidate/value.txt").read_text() == "correct\n"


@pytest.mark.parametrize(
    "model", ["private/new-provider:v9", "not-approved", "$(touch SHOULD_NOT_EXIST) model"]
)
def test_model_selection_is_literal_and_has_no_approval(model, tmp_path):
    for agent in ("codex", "claude", "agy", "muse", "cursor-agent"):
        argv = run_record.resident_command(agent, model, "", "task", tmp_path)
        assert argv[argv.index("--model") + 1] == model
        assert not any(
            "bypass" in x or x in {"--force", "--yolo", "--dangerously-skip-permissions"}
            for x in argv
        )


def test_start_has_only_assigned_residents_and_private_socket(tmp_path, repository, monkeypatch):
    runs = tmp_path / "runs"
    root = run_record.create(runs, "task", repository, b"Do a task")
    monkeypatch.setenv("FACTORY_VALIDATOR_AGENT", "claude")
    monkeypatch.setenv("FACTORY_VALIDATOR_MODEL", "private/model")
    monkeypatch.setenv("FACTORY_ORCHESTRATOR_AGENT", "codex")
    monkeypatch.setenv("FACTORY_TESTER_MODEL", "assigned/tester")
    monkeypatch.setenv("OPENAI_API_KEY", "test-value-must-not-be-inherited")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1 if "has-session" in argv else 0)

    with (
        patch("subprocess.run", side_effect=fake_run),
        patch("shutil.which", return_value="/stub/agent"),
    ):
        run_record.start(runs, "task")
    launches = [call for call in calls if "new-session" in call or "new-window" in call]
    assert len(launches) == 3
    assert {call[call.index("-n") + 1] for call in launches} == {
        "validator", "orchestrator", "inbox-wake"}
    _, doc = run_record.load(runs, "task")
    assert all(call[:3] == ["tmux", "-L", doc["tmux_socket"]] for call in calls)
    assert "private/model" in launches[0][-1] and "assigned/tester" in launches[0][-1]
    assert all("OPENAI_API_KEY" not in call[-1] for call in launches)
    assert not (root / "orchestrator/seat-control.json").exists()


def test_messages_survive_exited_agent_without_any_terminal_call(tmp_path, monkeypatch):
    root = tmp_path / "task"
    text = "VALIDATOR: ready\n$(touch BAD); `echo BAD`\nCODER: handoff"
    with patch("subprocess.run", side_effect=AssertionError("must not contact tmux or a shell")):
        run_record.message(root, "validator", "coder", text)
        log = tmp_path / "optional.log"
        run_record.message(root, "validator", "coder", text, log=str(log))
    inbox = [json.loads(x) for x in (root / "validator/inbox.jsonl").read_text().splitlines()]
    assert len(inbox) == 2 and all(row["text"] == text for row in inbox)
    assert json.loads(log.read_text())["text"] == text
    assert not (tmp_path / "BAD").exists()


def test_existing_run_and_symlink_targets_are_not_overwritten(tmp_path, repository):
    runs = tmp_path / "runs"
    root = run_record.create(runs, "task", repository, b"Original")
    with pytest.raises(FileExistsError):
        run_record.create(runs, "task", repository, b"Replacement")
    assert (root / "TASK.md").read_bytes() == b"Original"
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    (root / "validator").mkdir()
    (root / "validator/inbox.jsonl").symlink_to(outside)
    with pytest.raises(OSError):
        run_record.message(root, "validator", "coder", "notice")
    assert outside.read_text() == "untouched"


def test_fifo_inbox_does_not_block_or_receive_status(tmp_path):
    root = tmp_path / "run"
    (root / "validator").mkdir(parents=True)
    os.mkfifo(root / "validator/inbox.jsonl")
    with pytest.raises(OSError):
        run_record.message(root, "validator", "coder", "notice")


@pytest.mark.parametrize("log_enabled", [False, True])
def test_inject_script_never_addresses_a_pane(tmp_path, log_enabled):
    stub = tmp_path / "bin"
    stub.mkdir()
    called = tmp_path / "tmux-was-called"
    tmux = stub / "tmux"
    tmux.write_text(f"#!/bin/sh\ntouch '{called}'\nexit 99\n")
    tmux.chmod(0o755)
    root = tmp_path / "record"
    log = tmp_path / "status.log"
    history = tmp_path / "zsh_history"
    history.write_text("existing user history\n")
    text = f"CODER: ready\n$(touch {tmp_path}/executed); `false`\nVALIDATOR: decision"
    env = {
        "PATH": str(stub) + os.pathsep + os.environ["PATH"],
        "HOME": str(tmp_path),
        "HISTFILE": str(history),
        "HARNESS_RUN_ROOT": str(root),
        "FACTORY_PYTHON": sys.executable,
        "INJECT_FROM": "coder",
    }
    if log_enabled:
        env["FACTORY_LOG"] = str(log)
    result = subprocess.run(
        ["bash", str(ROOT / "harness/inject.sh"), "task", "validator", text],
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads((root / "validator/inbox.jsonl").read_text())["text"] == text
    assert log.exists() == log_enabled
    assert not called.exists() and not (tmp_path / "executed").exists()
    assert history.read_text() == "existing user history\n"


@pytest.mark.parametrize("shell_exited", [False, True])
def test_status_does_not_enter_real_zsh_history(tmp_path, shell_exited):
    """Reproduce the shell-after-agent scenario on an explicitly private test server."""
    tmux_bin, zsh = shutil.which("tmux"), shutil.which("zsh")
    if not tmux_bin or not zsh:
        pytest.skip("requires local tmux and zsh")
    with tempfile.TemporaryDirectory(prefix="fac-history-", dir="/private/tmp") as short:
        socket = str(Path(short) / "tmux.sock")
        tmux = [tmux_bin, "-S", socket]
        history = tmp_path / "zsh_history"
        env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "TERM": "xterm",
               "HISTFILE": str(history), "HISTSIZE": "100", "SAVEHIST": "100"}
        owner = None
        try:
            subprocess.run([*tmux, "-f", "/dev/null", "new-session", "-d", "-s", "task",
                            "-n", "validator",
                            "exec " + shlex.quote(zsh) + " -f"], env=env, check=True)
            owner = subprocess.check_output([*tmux, "display-message", "-p", "#{pid}"],
                                            text=True).strip()
            command = "setopt INC_APPEND_HISTORY; fc -AI; print FACTORY_HISTORY_FIXTURE_READY"
            subprocess.run([*tmux, "send-keys", "-t", "task:validator", "-l", command], check=True)
            subprocess.run([*tmux, "send-keys", "-t", "task:validator", "Enter"], check=True)
            for _ in range(100):
                pane = subprocess.check_output([*tmux, "capture-pane", "-pt", "task:validator"],
                                               text=True)
                if "FACTORY_HISTORY_FIXTURE_READY" in pane.splitlines():
                    break
                time.sleep(.02)
            else:
                pytest.fail("private zsh fixture did not start")
            if shell_exited:
                subprocess.run([*tmux, "send-keys", "-t", "task:validator", "exit", "Enter"],
                               check=True)
                for _ in range(100):
                    alive = subprocess.run([*tmux, "has-session", "-t", "task"],
                                           capture_output=True)
                    if alive.returncode:
                        break
                    time.sleep(.02)
                assert alive.returncode != 0
            before = history.read_bytes()
            stub = tmp_path / "bin"
            stub.mkdir()
            marker = tmp_path / "terminal-contact"
            wrapper = stub / "tmux"
            wrapper.write_text("#!/bin/sh\n" + f"touch {shlex.quote(str(marker))}\nexit 99\n")
            wrapper.chmod(0o700)
            # Even a regression cannot accidentally reach a user's default server.
            env.update(PATH=str(stub) + os.pathsep + env["PATH"],
                       FACTORY_PYTHON=sys.executable, HARNESS_RUN_ROOT=str(tmp_path / "run"),
                       FACTORY_TMUX_SOCKET=socket, INJECT_FROM="coder")
            notice = "CODER: FEEDBACK ready\nVALIDATOR: review complete\n$(touch NEVER_EXECUTE)"
            result = subprocess.run(["bash", str(ROOT / "harness/inject.sh"), "task", "validator",
                                     notice], env=env, capture_output=True, text=True)
            assert result.returncode == 0, result.stderr
            assert history.read_bytes() == before
            assert not marker.exists()
            inbox = json.loads((tmp_path / "run/validator/inbox.jsonl").read_text())
            assert inbox["text"] == notice
        finally:
            probe = subprocess.run([*tmux, "display-message", "-p", "#{pid}"],
                                   capture_output=True, text=True)
            if probe.returncode == 0:
                assert owner == probe.stdout.strip(), "test server ownership changed"
                subprocess.run([*tmux, "kill-server"], check=True)


def test_partial_start_failure_stops_only_its_session_and_retains_failure(tmp_path, repository,
                                                                       monkeypatch):
    runs = tmp_path / "runs"
    root = run_record.create(runs, "task", repository, b"Keep exact task")
    original = (root / "harness.json").read_bytes()
    calls = []
    fail = True

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if fail and "new-window" in argv:
            raise subprocess.CalledProcessError(7, argv)
        return subprocess.CompletedProcess(argv, 1 if "has-session" in argv else 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(shutil, "which", lambda _: "/fixture/agent")
    with pytest.raises(ValueError, match="startup failed with exit 7"):
        run_record.start(runs, "task")
    _, record = run_record.load(runs, "task")
    cleanup = [call for call in calls if "kill-session" in call]
    assert cleanup == [["tmux", "-L", record["tmux_socket"], "kill-session", "-t", "=task"]]
    assert not any("kill-server" in call for call in calls)
    notice = json.loads((root / "validator/inbox.jsonl").read_text())
    assert notice["sender"] == "host" and "exit 7" in notice["text"]
    assert (root / "harness.json").read_bytes() == original
    assert (root / "TASK.md").read_bytes() == b"Keep exact task"

    fail = False
    calls.clear()
    run_record.start(runs, "task")
    assert sum("new-session" in call for call in calls) == 1
    assert {call[call.index("-n") + 1] for call in calls if "new-window" in call} == {
        "orchestrator"}
    assert not any("kill-session" in call for call in calls)
    assert json.loads((root / "validator/inbox.jsonl").read_text()) == notice
    assert (root / "harness.json").read_bytes() == original


def test_invalid_candidate_retains_failure_without_running_checks(tmp_path, repository):
    runs = tmp_path / "runs"
    root = run_record.create(runs, "task", repository, b"Validate exact committed content")
    marker = tmp_path / "command-was-run"
    code, attempt = validate(runs, "task", repository, "f" * 40,
                            [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"])
    assert code == 70 and not marker.exists()
    result = json.loads((attempt / "result.json").read_text())
    assert result["status"] == "validation-failed" and result["error"]
    assert result["requested_commit"] == "f" * 40 and "candidate" not in result
    assert attempt.parent == root / "validation"


def test_cannot_report_pass_when_result_cannot_be_retained(
    tmp_path, repository, monkeypatch, capsys,
):
    from harness import validate_candidate

    runs = tmp_path / "runs"
    run_record.create(runs, "task", repository, b"Retain validation evidence")
    _, record = run_record.load(runs, "task")

    def fail_write(*args):
        raise OSError("result storage unavailable")

    monkeypatch.setattr(validate_candidate, "write_new", fail_write)
    monkeypatch.setattr(sys, "argv", ["validate_candidate.py", "task", record["resolved_commit"],
                                     "--runs", str(runs), "--repo", str(repository), "--",
                                     sys.executable, "-c", "raise SystemExit(0)"])
    assert validate_candidate.main() == 70
    output = capsys.readouterr()
    assert "PASS" not in output.out and "result storage unavailable" in output.err
