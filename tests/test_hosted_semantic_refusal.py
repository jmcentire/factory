"""Semantic guard admission is not satisfied by static kernel capability."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

H = Path(__file__).resolve().parents[1] / "harness"


@pytest.mark.parametrize("agent", ["agy", "muse"])
@pytest.mark.parametrize("entry", ["preflight", "lane", "host"])
@pytest.mark.parametrize(
    "guard,value",
    [
        ("FACTORY_REQUIRE_SIGNET", "false"),
        ("FACTORY_REQUIRE_AUTHOR_CUSTODY", "yes"),
        ("FACTORY_SIGNET_CONTRACT", "/synthetic/missing-signet.json"),
        ("FACTORY_SIGNET_DIGEST", "abc"),
        ("FACTORY_AUTHOR_CUSTODY_CONFIG", "/synthetic/missing-custody.json"),
        ("FACTORY_AUTHOR_CUSTODY_DIGEST", "abc"),
    ],
)
def test_every_public_entry_refuses_nonzero_or_named_semantic_guard(
    tmp_path, agent, entry, guard, value
):
    marker = tmp_path / "provider-spawned"
    binary = tmp_path / agent
    binary.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    binary.chmod(0o755)
    env = {
        k: v
        for k, v in os.environ.items()
        if k in {"PATH", "HOME", "SHELL", "TERM", "LANG", "TMPDIR"}
    }
    env.update({guard: value, "PATH": str(tmp_path) + os.pathsep + env["PATH"]})
    prompt = tmp_path / "prompt"
    prompt.write_text("synthetic no-spawn")
    if entry == "preflight":
        command = [sys.executable, str(H / "lane_agent.py"), "preflight", "--agent", agent]
    elif entry == "host":
        command = [
            sys.executable,
            str(H / "protocol_host.py"),
            "run",
            "--protocol",
            "acp" if agent == "agy" else "msp",
            "--journal",
            str(tmp_path / "journal.jsonl"),
            "--record",
            str(tmp_path / "host-record.json"),
            "--workspace",
            str(tmp_path),
            "--message",
            "synthetic no-spawn",
            "--",
            str(binary),
            "acp" if agent == "agy" else "serve",
        ]
    else:
        command = [
            sys.executable,
            str(H / "lane_agent.py"),
            "run",
            "--slot",
            "coder",
            "--log",
            str(tmp_path / "log"),
            "--exits",
            str(tmp_path / "exits"),
            "--prompt-arg",
            str(prompt),
            "--session-agent",
            agent,
            "--session-file",
            str(tmp_path / "session"),
            "--persistent",
            "--run-root",
            str(tmp_path),
            "--",
            str(binary),
        ]
    result = subprocess.run(
        command, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 77, result.stdout + result.stderr
    assert not marker.exists()
