"""Dummy-only credential composition: actual profile shell, isolated HTTP worker, native env.

Normative source: the authorized bounded profile connection. Only the HTTP worker is replaced
in the shell test; only HTTP transport is replaced in the real-worker test. Neither test reads
the user's HOME/profile or contacts a provider. Noise, consumed payload, changed parent env,
startup-file execution or a credential in the normalized output would turn these checks red.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from harness import jev_screen
from tests.test_coordinator_startup import launch as launch

KEYS = ("TYPESAFE_API_KEY", "JMC_JEV_API_KEY", "VOYAGE_API_KEY")
PAYLOAD = {"model": jev_screen.MODEL_ID, "state": {"literal": "$(not-a-command)\nexact input"},
           "questions": {"q": "Does the builder differ?"}}
ENVELOPE = {"model": jev_screen.MODEL_ID, "answers": {"q": {"type": "noul", "noul": 0.1}}}


@pytest.fixture(autouse=True)
def dummy_home(tmp_path, monkeypatch):
    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in (*KEYS, "FACTORY_SYSTEM_ONE_PROFILE", "BASH_ENV", "ENV"):
        monkeypatch.delenv(name, raising=False)
    return home


def test_profile_source_is_opted_in_silent_and_cannot_consume_payload(
    dummy_home, monkeypatch, capfd,
):
    sourced, unwanted = dummy_home / "sourced", dummy_home / "unapproved-startup"
    profile = dummy_home / ".profile"
    profile.write_text(
        f"touch '{sourced}'\nexport JMC_JEV_API_KEY=dummy-jev-key\n"
        "export VOYAGE_API_KEY=dummy-unrelated-key\n"
        "printf dummy-source-secret\nprintf dummy-source-secret >&2\n"
        "read -r discarded || true\ntrap 'printf dummy-trap-secret' DEBUG\n"
    )
    startup = dummy_home / "startup.sh"
    startup.write_text(f"touch '{unwanted}'\n")
    monkeypatch.setenv("BASH_ENV", str(startup))
    monkeypatch.setenv("ENV", str(startup))
    worker = dummy_home / "recording_http_worker.py"
    worker.write_text(
        "import json, os, sys\n"
        f"assert json.load(sys.stdin) == {PAYLOAD!r}\n"
        "if not os.environ.get('JMC_JEV_API_KEY'): raise SystemExit(78)\n"
        "assert os.environ['JMC_JEV_API_KEY'] == 'dummy-jev-key'\n"
        f"print({json.dumps(ENVELOPE)!r})\n"
    )
    real_popen, children = subprocess.Popen, []

    def shell_with_recording_worker(args, **kwargs):
        assert args[:4] == ["/bin/bash", "--noprofile", "--norc", "-c"]
        assert args[-2] == sys.executable and kwargs["start_new_session"] is True
        assert not {"BASH_ENV", "ENV"} & kwargs["env"].keys()
        child = real_popen([*args[:-1], str(worker)], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(jev_screen.subprocess, "Popen", shell_with_recording_worker)
    with pytest.raises(jev_screen.ScreenError, match="missing-key"):
        jev_screen.request(PAYLOAD)
    assert not sourced.exists() and not children
    monkeypatch.setenv("FACTORY_SYSTEM_ONE_PROFILE", "1")
    parent = dict(os.environ)
    assert jev_screen.request(PAYLOAD) == ENVELOPE
    assert sourced.exists() and not unwanted.exists() and dict(os.environ) == parent
    assert len(children) == 1
    profile.write_text("printf dummy-empty-profile-noise\n")
    with pytest.raises(jev_screen.ScreenError, match="missing-key"):
        jev_screen.request(PAYLOAD)
    profile.unlink()
    with pytest.raises(jev_screen.ScreenError, match="profile-unavailable"):
        jev_screen.request(PAYLOAD)
    # This descendant inherits the wrapper's saved output descriptor. Killing only Bash
    # would leave communicate() waiting for it; it naturally exits after two seconds even
    # on a broken candidate, so the negative control cannot leave a permanent process.
    profile.write_text("sleep 2 &\nwait\n")
    monkeypatch.setattr(jev_screen, "TIMEOUT", 0.1)
    real_killpg, killed = os.killpg, []
    monkeypatch.setattr(os, "killpg", lambda pid, sig:
                        (killed.append(pid), real_killpg(pid, sig))[1])
    started = time.monotonic()
    with pytest.raises(jev_screen.ScreenError, match="deadline"):
        jev_screen.request(PAYLOAD)
    assert time.monotonic() - started < 1.1  # Request deadline plus at most one second cleanup.
    assert killed and set(killed) == {children[-1].pid}
    captured = capfd.readouterr()
    assert "dummy-" not in captured.out + captured.err


def test_real_http_worker_clears_credentials_and_emits_only_validated_envelope(dummy_home):
    harness = Path(__file__).resolve().parents[1] / "harness"
    child_code = f"""
import json, os, sys, urllib.request
sys.path.insert(0, {str(harness)!r})
import jev_screen
class Transport:
    def open(self, request, timeout):
        assert request.get_header('Authorization') == 'Bearer dummy-jev-key'
        assert not set({KEYS!r}) & set(os.environ)
        assert 'dummy-unrelated-key' not in os.environ.values()
        assert json.loads(request.data) == {PAYLOAD!r} and timeout == 5
        return self
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, limit):
        assert limit == jev_screen.MAX_RESPONSE + 1
        return json.dumps({ENVELOPE!r} | {{'extra': 'dummy-jev-key'}}).encode()
urllib.request.build_opener = lambda *args: Transport()
jev_screen._http_request()
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", child_code], input=json.dumps(PAYLOAD).encode(),
        env={"HOME": str(dummy_home), "PATH": "/usr/bin:/bin",
             "JMC_JEV_API_KEY": "dummy-jev-key", "VOYAGE_API_KEY": "dummy-unrelated-key"},
        capture_output=True, timeout=8,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert json.loads(result.stdout) == ENVELOPE and result.stderr == b""
    assert b"dummy-jev-key" not in result.stdout and b"dummy-unrelated-key" not in result.stdout
    missing = subprocess.run(
        [sys.executable, "-I", str(harness / "jev_screen.py"), "--request"],
        input=json.dumps(PAYLOAD).encode(), env={"HOME": str(dummy_home), "PATH": "/usr/bin:/bin"},
        capture_output=True, timeout=8,
    )
    assert missing.returncode == 78 and missing.stdout == b""


def test_coordinators_receive_profile_opt_in_but_never_credentials(launch, monkeypatch):
    _, _, _, capture, _, execute = launch
    monkeypatch.setenv("FACTORY_SYSTEM_ONE_PROFILE", "1")
    for name in KEYS:
        monkeypatch.setenv(name, "dummy-credential-" + name)
    calls = capture()
    for role in ("validator", "orchestrator"):
        environment = execute(calls, role)["env"]
        assert environment["FACTORY_SYSTEM_ONE_PROFILE"] == "1"
        assert not set(KEYS) & environment.keys()
        assert not any(value.startswith("dummy-credential-") for value in environment.values())
