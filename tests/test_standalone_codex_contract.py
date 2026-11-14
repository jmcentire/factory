"""Standalone public contract discovery follows native default, without model calls."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NATIVE = {"--no-daemon", "--no-alt-screen", "--thread", "--message", "--add-dir", "--model"}
EXEC = {"--json", "--thread", "--message", "--add-dir", "--model"}


def check(tmp_path, agent, flags):
    binary = tmp_path / "codex"
    log = tmp_path / "invocations.jsonl"
    binary.write_text(f"""#!{sys.executable}
import json, sys
with open({str(log)!r}, 'a') as out: out.write(json.dumps(sys.argv[1:])+'\\n')
if sys.argv[1:] == ['--version']:
    print('codex-cli 0.167.0-synthetic')
elif '--help' in sys.argv[1:]:
    print({" ".join(sorted(flags))!r})
else:
    raise SystemExit('Only contract discovery is allowed')
""")
    binary.chmod(0o755)
    env = {
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "HOME": str(tmp_path),
        "FACTORY_PYTHON": sys.executable,
    }
    result = subprocess.run(
        ["bash", str(ROOT / "harness/agent_contract.sh"), agent],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    return result, calls


@pytest.mark.parametrize("agent", ["codex", "codex-interactive"])
def test_standalone_codex_uses_native_contract_without_exec_requirement(tmp_path, agent):
    result, calls = check(tmp_path, agent, NATIVE)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["codex-native-terminal-v1", "codex-cli 0.167.0-synthetic"]
    assert not any("exec" in call for call in calls)
    assert any("queue" in call and "--help" in call for call in calls)


@pytest.mark.parametrize("agent", ["codex", "codex-interactive"])
@pytest.mark.parametrize("missing", ["--no-daemon", "--no-alt-screen", "--thread", "--message"])
def test_standalone_native_codex_fails_closed_without_required_capability(tmp_path, agent, missing):
    result, _ = check(tmp_path, agent, (NATIVE | EXEC) - {missing})
    assert result.returncode != 0
    assert missing in result.stderr
    assert "codex-native-terminal-v1" not in result.stdout


def test_explicit_ollama_still_checks_exec_json_transport(tmp_path):
    result, calls = check(tmp_path, "codex-ollama", EXEC)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "codex-exec-json-plus-queue-resume-v1",
        "codex-cli 0.167.0-synthetic",
    ]
    assert any("exec" in call and "--help" in call for call in calls)


def test_explicit_ollama_fails_without_exec_json_capability(tmp_path):
    result, _ = check(tmp_path, "codex-ollama", EXEC - {"--json"})
    assert result.returncode != 0 and "--json" in result.stderr
