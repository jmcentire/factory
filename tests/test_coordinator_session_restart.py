"""Retained coordinator conversations reach the native resume boundary on restart.

The real start command and adapters run against a disposable tmux executable. It records
the actual launch requests without launching a native model or touching a real tmux server.
"""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from harness import agents, run_record


# AGY is a manual native seat, not an automatically resumed coordinator.
@pytest.mark.parametrize("agent", ["claude", "agy"])
def test_restart_resumes_each_retained_coordinator_without_changing_inputs(tmp_path, agent):
    repository = tmp_path / "source"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "--allow-empty",
                    "-qm", "base"], check=True)
    root = run_record.create(tmp_path / "runs", "restart", repository, b"Retain my context.")
    original = {name: (root / name).read_bytes() for name in ("TASK.md", "harness.json")}
    binary = tmp_path / "bin"
    binary.mkdir()
    calls = tmp_path / "tmux-calls.jsonl"
    tmux = binary / "tmux"
    tmux.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"with open({str(calls)!r}, 'a') as output:\n"
        "    output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "raise SystemExit(1 if 'has-session' in sys.argv else 0)\n"
    )
    tmux.chmod(0o755)
    native = binary / agent
    native.write_text(f"#!{sys.executable}\nraise SystemExit('native models must not run')\n")
    native.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = {key: value for key, value in os.environ.items()
           if key in {"LANG", "TMPDIR", "SYSTEMROOT"}}
    env.update(HOME=str(home), PATH=str(binary) + ":/usr/bin:/bin",
               FACTORY_HERALD="off", FACTORY_SEAT_BILLING="plan", FACTORY_LANE_SANDBOX="on")
    for role in ("VALIDATOR", "ORCHESTRATOR", "CODER", "TESTER"):
        env.update({f"FACTORY_{role}_AGENT": agent, f"FACTORY_{role}_MODEL": "",
                    f"FACTORY_{role}_EFFORT": ""})
    command = [sys.executable, str(Path(run_record.__file__).resolve()), "start",
               "--runs", str(root.parent), "--run", "restart"]

    windows = []

    def start():
        nonlocal windows
        offset = calls.stat().st_size if calls.exists() else 0
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr
        with calls.open() as stream:
            stream.seek(offset)
            rows = [row for row in map(json.loads, stream) if "-n" in row]
            windows = [row[row.index("-n") + 1] for row in rows]
            launches = [row for row in rows
                        if row[row.index("-n") + 1] in {"validator", "orchestrator"}]
        return {row[row.index("-n") + 1]: shlex.split(row[-1].split(" && exec ", 1)[1])
                for row in launches}

    fresh = start()
    resume_flag = "--resume" if agent == "claude" else "--conversation"
    assert set(fresh) == {"validator", "orchestrator"}
    assert all(resume_flag not in argv for argv in fresh.values())
    if agent == "agy":
        # A manual start remains supported, but must not offer a wake daemon or
        # stream transport as if AGY had an unattended resume/delivery path.
        assert windows == ["validator", "orchestrator"]
        assert all("--terminal" in argv and "--persistent" not in argv
                   and "--print" not in argv for argv in fresh.values())
        assert not agents.harness(agent).resumable
        chosen = "12345678-1234-4234-8234-123456789abc"
        native = agents.harness(agent).turn(model="", effort="", cwd=repository, resume=chosen)
        assert native.argv[native.argv.index("--conversation") + 1] == chosen
        assert "--print" not in native.argv
        assert {name: (root / name).read_bytes() for name in original} == original
        return
    sessions = {"validator": "12345678-1234-4234-8234-123456789abc",
                "orchestrator": "99999999-9999-4999-8999-999999999999"}
    for role, session in sessions.items():
        (root / f"{role}-session-id").write_text(session + "\n")
    retained_prompts = {role: (root / role / "initial-prompt.txt").read_bytes()
                        for role in sessions}
    resumed = start()
    for role, session in sessions.items():
        argv = resumed[role]
        native_argv = argv[argv.index("--") + 1:]
        assert native_argv[native_argv.index(resume_flag) + 1] == session
        assert argv[argv.index("--session-file") + 1] == str(root / f"{role}-session-id")
        assert (root / f"{role}-session-id").read_text() == session + "\n"
        assert (root / role / "initial-prompt.txt").read_bytes() == retained_prompts[role]
    assert {name: (root / name).read_bytes() for name in original} == original
