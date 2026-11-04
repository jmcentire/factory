"""Normal coordinator launch contracts, using temporary CLI stubs only.

Help text and historical signed startup metadata are not admission authorities.
Test actual arguments/environment and visible failures instead.
"""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from harness import run_record


@pytest.fixture
def launch(tmp_path, monkeypatch):
    repository = tmp_path / "source with spaces"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "-c", "user.name=Fixture", "-c",
         "user.email=fixture@example.invalid", "commit", "--allow-empty", "-qm", "initial"],
        check=True,
    )
    runs = tmp_path / "runs"
    root = run_record.create(runs, "task", repository, b"Keep this exact task.\n")
    binary = tmp_path / "bin"
    binary.mkdir()
    stub = (
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "print(json.dumps({'argv': sys.argv[1:], 'env': dict(os.environ), 'cwd': os.getcwd()}))\n"
    )
    for name in ("codex", "agy"):
        path = binary / name
        path.write_text(stub)
        path.chmod(0o755)
    # A new home and explicit adapter choices keep real user configuration out of fixtures.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", str(binary) + ":/usr/bin:/bin")
    monkeypatch.delenv("FACTORY_CONFIG", raising=False)
    for role in ("VALIDATOR", "ORCHESTRATOR", "CODER", "TESTER"):
        monkeypatch.setenv(f"FACTORY_{role}_AGENT", "agy" if role == "ORCHESTRATOR" else "codex")
        monkeypatch.setenv(f"FACTORY_{role}_MODEL", "")
        monkeypatch.setenv(f"FACTORY_{role}_EFFORT", "")
    monkeypatch.setenv("FACTORY_SEAT_BILLING", "plan")
    real_run = subprocess.run

    def capture(*, model_probe=False, fail_start=False):
        calls = []

        def tmux_only(argv, **kwargs):
            if model_probe and argv[0] == "bash":
                assert Path(argv[4]).name == "model_availability.sh"
                return real_run(argv, **kwargs)
            assert argv[0] == "tmux", "startup must not probe or execute native agents"
            calls.append(argv)
            if fail_start and "new-session" in argv:
                raise subprocess.CalledProcessError(1, argv)
            return subprocess.CompletedProcess(argv, 1 if "has-session" in argv else 0)

        with monkeypatch.context() as isolated:
            isolated.setattr(subprocess, "run", tmux_only)
            run_record.start(runs, "task")
        return calls

    def invocation(calls, role):
        call = next(c for c in calls if "-n" in c and c[c.index("-n") + 1] == role)
        words = shlex.split(call[-1].split(" && exec ", 1)[1])
        words.insert(0, "exec")
        assert words[:3] == ["exec", "env", "-i"]
        environment = {}
        index = 3
        while "=" in words[index] and not words[index].startswith("/"):
            key, value = words[index].split("=", 1)
            environment[key] = value
            index += 1
        assert words[index] == sys.executable
        assert Path(words[index + 1]).name == "lane_agent.py"
        assert "--terminal" in words[index:]
        command = words[words.index("--", index) + 1:]
        return call, environment, command

    def execute(calls, role):
        # Run only the temporary CLI, preserving the exact environment and argv produced
        # for the native wrapper. Wrapper exit/process behavior has separate real-process tests.
        call, environment, command = invocation(calls, role)
        result = real_run(command, env=environment, cwd=call[call.index("-c") + 1],
                          capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    return root, repository, binary, capture, invocation, execute


def test_startup_pins_exact_inputs_and_uses_only_two_coordinators(launch):
    root, repository, _, capture, invocation, _ = launch
    before = (root / "harness.json").read_bytes()
    calls = capture()
    record = json.loads(before)
    assert (root / "TASK.md").read_bytes() == b"Keep this exact task.\n"
    assert (root / "harness.json").read_bytes() == before
    assert [c[c.index("-n") + 1] for c in calls if "-n" in c] == ["validator", "orchestrator"]
    for role in ("validator", "orchestrator"):
        call, environment, command = invocation(calls, role)
        assert call[1:3] == ["-L", record["tmux_socket"]]
        assert call[call.index("-c") + 1] == str(repository)
        assert environment["FACTORY_CONTROL_ROOT"] == str(root)
        assert record["resolved_commit"] in command[-1]
        assert str(root / "TASK.md") in command[-1]
        assert "Kindex" in command[-1]
        assert "Remain in this conversation" in command[-1]
    assert not (root / "run.json").exists()
    assert not (root / "orchestrator/bin").exists()


@pytest.mark.parametrize("role", ["validator", "orchestrator"])
def test_generated_environment_excludes_credentials_and_foreign_run_context(
    launch, monkeypatch, role,
):
    root, repository, _, capture, _, execute = launch
    forbidden = {
        key: "credential-sentinel-" + key for key in
        ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
         "TYPESAFE_API_KEY", "AWS_SECRET_ACCESS_KEY")
    }
    forbidden.update(FACTORY_CONTROL_ROOT="/other/run", HARNESS_RUN_ID="other-run",
                     FACTORY_CONFIG="/other/config.toml", UNRELATED_SECRET="private")
    for key, value in forbidden.items():
        if key != "FACTORY_CONFIG":
            monkeypatch.setenv(key, value)
    result = execute(capture(), role)
    assert result["cwd"] == str(repository)
    assert result["env"]["FACTORY_CONTROL_ROOT"] == str(root)
    for key, value in forbidden.items():
        assert value not in result["env"].values()
        if key != "FACTORY_CONTROL_ROOT":
            assert key not in result["env"]


def test_agy_keeps_interactive_prompt_without_permission_bypass(launch):
    root, _, _, capture, _, execute = launch
    argv = execute(capture(), "orchestrator")["argv"]
    assert argv[argv.index("--add-dir") + 1] == str(root)
    assert argv[argv.index("--prompt-interactive") + 1]
    assert not set(argv) & {"--dangerously-skip-permissions", "--sandbox", "--print", "-p",
                            "--disable-slash-commands"}


def test_help_format_is_not_a_startup_prerequisite(launch):
    _, _, binary, capture, _, execute = launch
    agy = binary / "agy"
    original = agy.read_text()
    # Any help probe would fail the capture boundary. The actual supported invocation works.
    agy.write_text(original.replace("import json, os, sys", "import json, os, sys\n"
                   "assert '--help' not in sys.argv"))
    assert "--prompt-interactive" in execute(capture(), "orchestrator")["argv"]


@pytest.mark.parametrize("terminal,expected", [
    ("dumb", "xterm-256color"), ("", "xterm-256color"), ("tmux-256color", "tmux-256color"),
])
def test_native_coordinator_gets_a_usable_terminal(launch, monkeypatch, terminal, expected):
    _, _, _, capture, _, execute = launch
    monkeypatch.setenv("TERM", terminal)
    assert execute(capture(), "validator")["env"]["TERM"] == expected


@pytest.mark.parametrize("field,value", [
    ("MODEL", "gemini-user-choice"),
    ("MODEL", "private/model $(touch SHOULD_NOT_EXIST); spaces"),
    ("EFFORT", "high"),
])
def test_user_model_and_effort_reach_agy_unchanged(launch, monkeypatch, field, value):
    _, repository, _, capture, _, execute = launch
    monkeypatch.setenv(f"FACTORY_ORCHESTRATOR_{field}", value)
    result = execute(capture(), "orchestrator")
    assert result["argv"][result["argv"].index("--" + field.lower()) + 1] == value
    assert result["env"][f"FACTORY_ORCHESTRATOR_{field}"] == value
    assert not (repository / "SHOULD_NOT_EXIST").exists()


def test_configured_author_assignments_reach_validator(launch, monkeypatch):
    _, _, _, capture, _, execute = launch
    for key, value in {"CODER_AGENT": "claude", "CODER_MODEL": "chosen-coder",
                       "TESTER_AGENT": "agy", "TESTER_MODEL": "chosen-tester"}.items():
        monkeypatch.setenv("FACTORY_" + key, value)
    env = execute(capture(), "validator")["env"]
    for key in ("CODER_AGENT", "CODER_MODEL", "TESTER_AGENT", "TESTER_MODEL"):
        assert env["FACTORY_" + key] == os.environ["FACTORY_" + key]


def test_coordinator_command_pins_python_despite_broken_path_python(launch):
    _, _, binary, capture, invocation, execute = launch
    bad = binary / "python3"
    bad.write_text("#!/bin/sh\necho BAD-PYTHON >&2\nexit 97\n")
    bad.chmod(0o755)
    calls = capture()
    for role in ("validator", "orchestrator"):
        _, env, _ = invocation(calls, role)
        assert env["FACTORY_PYTHON"] == sys.executable
        assert execute(calls, role)["argv"]


def test_unsupported_native_flag_records_failure_without_model_substitution(launch, monkeypatch):
    root, repository, binary, capture, invocation, _ = launch
    monkeypatch.setenv("FACTORY_ORCHESTRATOR_MODEL", "operator-selected")
    agy = binary / "agy"
    receipt = binary / "invocations.jsonl"
    agy.write_text(
        f"#!{sys.executable}\nimport json, sys\n"
        f"with open({str(receipt)!r}, 'a') as out: out.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "print('unsupported option --model', file=sys.stderr)\nraise SystemExit(64)\n"
    )
    calls = capture()
    call, _, _ = invocation(calls, "orchestrator")
    # Exercise the native wrapper directly; real tmux output plumbing has its own test.
    wrapper = "exec " + call[-1].split(" && exec ", 1)[1]
    completed = subprocess.run(["/bin/bash", "-c", wrapper], cwd=repository,
                               capture_output=True, text=True)
    assert completed.returncode == 64, completed.stderr
    assert "unsupported option --model" in completed.stderr
    invocations = [json.loads(line) for line in receipt.read_text().splitlines()]
    assert len(invocations) == 1
    assert invocations[0][invocations[0].index("--model") + 1] == "operator-selected"
    records = [
        json.loads(line) for line in (root / "orchestrator-exits.jsonl").read_text().splitlines()
    ]
    assert len(records) == 1 and records[0]["rc"] == 64


@pytest.mark.parametrize("available", [False, True])
def test_ollama_coordinator_checks_availability_without_downloading(launch, monkeypatch, available):
    _, _, binary, capture, invocation, _ = launch
    monkeypatch.setenv("FACTORY_VALIDATOR_AGENT", "ollama")
    monkeypatch.setenv("FACTORY_VALIDATOR_MODEL", "chosen-local:latest")
    log = binary / "ollama.calls"
    stub = binary / "ollama"
    stub.write_text(
        f"#!{sys.executable}\nimport json, sys\n"
        f"with open({str(log)!r}, 'a') as out: out.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "assert sys.argv[1:] == ['show', 'chosen-local:latest']\n"
        f"raise SystemExit({0 if available else 1})\n"
    )
    stub.chmod(0o755)
    if not available:
        with pytest.raises(ValueError, match="never downloads models"):
            capture(model_probe=True)
    else:
        _, _, command = invocation(capture(model_probe=True), "validator")
        assert command[:5] == ["ollama", "launch", "codex", "--model", "chosen-local:latest"]
    assert [json.loads(row) for row in log.read_text().splitlines()] == [
        ["show", "chosen-local:latest"]
    ]


def test_ollama_coordinator_requires_named_model_before_launch(launch, monkeypatch):
    _, _, _, capture, _, _ = launch
    monkeypatch.setenv("FACTORY_VALIDATOR_AGENT", "ollama")
    with pytest.raises(ValueError, match="explicit installed model"):
        capture()


def test_kickoff_validator_gets_private_brief_without_duplicate_process(launch, monkeypatch):
    root, _, _, capture, _, _ = launch
    monkeypatch.setenv("FACTORY_VALIDATOR_AGENT", "kickoff")
    calls = capture()
    windows = [c[c.index("-n") + 1] for c in calls if "-n" in c]
    assert windows == ["orchestrator"]
    brief = root / "validator/BRIEF.md"
    assert "inbox.jsonl" in brief.read_text() and str(root / "TASK.md") in brief.read_text()
    assert brief.stat().st_mode & 0o077 == 0
    run_record.message(root, "validator", "orchestrator", "Actual question")
    inbox = root / "validator/inbox.jsonl"
    assert json.loads(inbox.read_text())["text"] == "Actual question"
    assert inbox.stat().st_mode & 0o077 == 0
    assert not (root / "validator-process.json").exists()


@pytest.mark.parametrize("sandbox", ["on", "off"])
def test_codex_startup_preserves_user_selected_native_permission_mode(launch, monkeypatch, sandbox):
    _, _, _, capture, _, execute = launch
    monkeypatch.setenv("FACTORY_LANE_SANDBOX", sandbox)
    result = execute(capture(), "validator")
    argv = result["argv"]
    assert result["env"]["FACTORY_LANE_SANDBOX"] == sandbox
    if sandbox == "on":
        assert argv[argv.index("--sandbox") + 1] == "workspace-write"
        assert argv[argv.index("--ask-for-approval") + 1] == "on-request"
        assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    else:
        assert "--dangerously-bypass-approvals-and-sandbox" in argv
        assert "--sandbox" not in argv and "--ask-for-approval" not in argv


@pytest.mark.parametrize("agent,executable,flag", [
    ("claude", "claude", "--dangerously-skip-permissions"),
    ("agy", "agy", "--dangerously-skip-permissions"),
    ("muse", "muse", "--yolo"),
    ("cursor-agent", "agent", "--yolo"),
])
def test_native_permission_choice_reaches_other_coordinators(
    launch, monkeypatch, agent, executable, flag,
):
    _, _, binary, capture, _, execute = launch
    path = binary / executable
    path.write_text((binary / "codex").read_text())
    path.chmod(0o755)
    monkeypatch.setenv("FACTORY_VALIDATOR_AGENT", agent)
    monkeypatch.setenv("FACTORY_LANE_SANDBOX", "off")
    result = execute(capture(), "validator")
    assert flag in result["argv"]
    assert result["env"]["FACTORY_LANE_SANDBOX"] == "off"
    if agent == "cursor-agent":
        assert result["argv"][result["argv"].index("--sandbox") + 1] == "disabled"


def test_kickoff_retry_preserves_brief_and_queued_failure(launch, monkeypatch):
    root, _, _, capture, _, _ = launch
    monkeypatch.setenv("FACTORY_VALIDATOR_AGENT", "kickoff")
    with pytest.raises(ValueError, match="startup failed"):
        capture(fail_start=True)
    brief = (root / "validator/BRIEF.md").read_bytes()
    calls = capture()
    assert (root / "validator/BRIEF.md").read_bytes() == brief
    assert sum("new-session" in call for call in calls) == 1
    assert "startup failed" in (root / "validator/inbox.jsonl").read_text()


@pytest.mark.parametrize("replacement", ["changed", "symlink"])
def test_kickoff_retry_does_not_overwrite_replaced_brief(launch, monkeypatch, replacement):
    root, _, _, capture, _, _ = launch
    monkeypatch.setenv("FACTORY_VALIDATOR_AGENT", "kickoff")
    with pytest.raises(ValueError, match="startup failed"):
        capture(fail_start=True)
    brief = root / "validator/BRIEF.md"
    if replacement == "changed":
        brief.write_text("operator annotation")
    else:
        saved = root / "saved-brief"
        brief.rename(saved)
        brief.symlink_to(saved)
    before = brief.read_bytes()
    with pytest.raises((ValueError, OSError)):
        capture()
    assert brief.read_bytes() == before


def test_existing_task_cannot_be_overwritten_or_relaunched(launch):
    root, repository, binary, _, _, _ = launch
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    tmux = binary / "tmux"
    marker = binary / "unexpected-tmux"
    tmux.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\nexit 97\n")
    tmux.chmod(0o755)
    environment = dict(os.environ, FACTORY_PYTHON=sys.executable)
    result = subprocess.run(
        ["bash", str(Path(run_record.__file__).with_name("factory.sh")), "task",
         "Different user request", "--repo", str(repository), "--runs", str(root.parent)],
        capture_output=True, text=True, env=environment,
    )
    assert result.returncode != 0 and "File exists" in result.stderr
    assert not marker.exists()
    assert {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()} == before


def test_concurrent_task_creation_preserves_exactly_one_request(launch):
    from concurrent.futures import ThreadPoolExecutor

    root, repository, _, _, _, _ = launch

    def create(task):
        try:
            return run_record.create(root.parent, "concurrent", repository, task)
        except FileExistsError:
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(create, [b"request one", b"request two"]))
    assert sum(result is not None for result in results) == 1
    winner, _ = run_record.load(root.parent, "concurrent")
    assert (winner / "TASK.md").read_bytes() in {b"request one", b"request two"}


def test_question_is_durable_without_an_external_channel(launch, monkeypatch):
    root, _, _, _, _, _ = launch

    def no_delivery(*args, **kwargs):
        pytest.fail("durable inbox must not spawn an external sender or terminal command")

    monkeypatch.setattr(subprocess, "run", no_delivery)
    run_record.message(root, "validator", "orchestrator", "Decision needed: resolve blocker")
    rows = [json.loads(row) for row in (root / "validator/inbox.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["text"] == "Decision needed: resolve blocker"
    assert rows[0]["sender"] == "orchestrator"
    assert not (root / "notifications-sent.jsonl").exists()


@pytest.mark.parametrize("option,value", [
    ("--engagement", "autonomous"), ("--engagement", "semi"), ("--jev-screen-calls", "2"),
])
def test_retired_startup_options_fail_explicitly_without_mutation(launch, option, value):
    root, repository, binary, _, _, _ = launch
    marker = binary / "unexpected-tmux"
    tmux = binary / "tmux"
    tmux.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\nexit 97\n")
    tmux.chmod(0o755)
    result = subprocess.run(
        ["bash", str(Path(run_record.__file__).with_name("factory.sh")), "new-task", "task",
         "--repo", str(repository), "--runs", str(root.parent), option, value],
        env=dict(os.environ, FACTORY_PYTHON=sys.executable), capture_output=True, text=True,
    )
    assert result.returncode == 64 and f"unknown argument: {option}" in result.stderr
    assert not (root.parent / "new-task").exists()
    assert not marker.exists()
