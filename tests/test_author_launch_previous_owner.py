"""Public fresh author launch cannot clean a still-owned native workspace."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from tests.test_cursor_ended_native_recovery import PROVIDER
from tests.test_native_claude_contract import FACTORY, execute, launch, read_events, wait_for

pytest_plugins = ["tests.test_native_claude_contract"]


def test_fresh_launch_keeps_previous_native_hooks_until_owner_has_exited(native_case, tmp_path):
    case = native_case
    allowed = {
        "HOME",
        "PATH",
        "SHELL",
        "TMPDIR",
        "TERM",
        "LANG",
        "CLAUDE_CONFIG_DIR",
        "FACTORY_VALIDATOR_AGENT",
        "FACTORY_ORCHESTRATOR_AGENT",
        "FACTORY_PYTHON",
        "FACTORY_CONFIG",
        "FACTORY_KINDEX_LITE_BIN",
    }
    case["env"] = {k: v for k, v in case["env"].items() if k in allowed}
    launch(case)
    release = case["bin_dir"] / "release-old-owner"
    provider = case["bin_dir"] / "agent"
    source = PROVIDER.replace(
        "import hashlib, json, os, pathlib, subprocess, sys, time, tty",
        "import hashlib, json, os, pathlib, signal, subprocess, sys, time, tty",
    )
    source = source.replace(
        "tty.setraw(0)",
        """def slow_exit(signum, frame):
    record(stopping=os.getpid())
    while not (folder/'release-old-owner').exists(): time.sleep(.01)
    raise SystemExit(0)
signal.signal(signal.SIGTERM, slow_exit)
signal.signal(signal.SIGHUP, slow_exit)
tty.setraw(0)""",
    )
    source = source.replace(
        "        if not resumed:\n            report('sessionEnd'); raise SystemExit", ""
    )
    provider.write_text(f"#!{sys.executable}\n" + source)
    provider.chmod(0o755)
    prepared = execute(
        [
            sys.executable,
            str(FACTORY / "harness/role_inputs.py"),
            "prepare",
            "--runs",
            str(case["runs"]),
            "--run",
            "native-acceptance",
            "--role",
            "coder",
            "--repo",
            str(tmp_path / "author"),
        ],
        case["env"],
    )
    assert prepared.returncode == 0, prepared.stderr
    repo = Path(json.loads(prepared.stdout)["repository"])
    brief = tmp_path / "brief"
    brief.write_text("First native author request")
    env = {**case["env"], "FACTORY_LANE_SANDBOX": "off"}
    argv = [
        "bash",
        str(FACTORY / "harness/tmux_lane.sh"),
        "native-acceptance",
        "coder",
        "launch",
        "--runs",
        str(case["runs"]),
        "--repo",
        str(repo),
        "--prompt",
        str(brief),
        "--agent",
        "cursor-agent",
        "--model",
        "synthetic-model",
    ]
    first = execute(argv, env)
    assert first.returncode == 0, first.stdout + first.stderr
    root = case["runs"] / "native-acceptance"
    state_path = root / "tmux-lanes/coder-persistent.json"
    before = wait_for(
        lambda: (
            row
            if state_path.exists()
            and (row := json.loads(state_path.read_text())).get("phase") == "stop-reported"
            else None
        )
    )
    hook = repo / ".cursor/hooks.json"
    original_hooks = hook.read_bytes()
    old_pid = before["wrapper"]["pid"]
    run = json.loads(execute(case["base"] + ["status"] + case["common"], env).stdout)
    log = case["bin_dir"] / "cursor-events.jsonl"
    count = len([e for e in read_events(log) if "argv" in e])
    replacement = argv + ["--round", "r2", "--replace-session", "synthetic old session replacement"]
    child = None
    try:
        execute(
            ["tmux", "-L", run["tmux_socket"], "kill-window", "-t", "native-acceptance:coder"], env
        )
        wait_for(
            lambda: any(e.get("stopping") == before["worker"]["pid"] for e in read_events(log))
        )
        child = subprocess.Popen(
            replacement,
            cwd=FACTORY,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 1.2
        while time.monotonic() < deadline:
            old_start = subprocess.run(
                ["ps", "-p", str(old_pid), "-o", "lstart="],
                capture_output=True,
                text=True,
                env={**os.environ, "TZ": "UTC"},
            ).stdout.strip()
            assert " ".join(old_start.split()) == " ".join(before["wrapper"]["started"].split())
            assert hook.exists() and hook.read_bytes() == original_hooks, "old owner hooks changed"
            assert len([e for e in read_events(log) if "argv" in e]) == count
            if child.poll() is not None:
                stdout, stderr = child.communicate()
                assert child.returncode != 0, stdout + stderr
                break
            time.sleep(0.02)
        release.touch()
        wait_for(
            lambda: (
                str(root)
                not in subprocess.run(
                    ["ps", "-ww", "-p", str(old_pid), "-o", "command="],
                    capture_output=True,
                    text=True,
                ).stdout
            )
        )
        if child.poll() is None:
            stdout, stderr = child.communicate(timeout=30)
            assert child.returncode == 0, stdout + stderr
        else:
            retried = execute(replacement, env)
            assert retried.returncode == 0, retried.stdout + retried.stderr
        wait_for(lambda: len([e for e in read_events(log) if "argv" in e]) == count + 1)
        assert hook.exists(), "old cleanup removed the new installed hooks"
    finally:
        release.touch()
        if child is not None and child.poll() is None:
            child.terminate()
            child.communicate(timeout=10)
