"""Normal-run screening adds bounded evidence, never authority.

The provider is replaced at its declared external seam. These tests exercise the
real run creation, observation, persistence and inbox delivery, not model quality.
"""

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness import run_record


@pytest.fixture(autouse=True)
def no_real_credentials(monkeypatch):
    for name in ("TYPESAFE_API_KEY", "JMC_JEV_API_KEY", "FACTORY_JEV_SCREEN"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True
    )
    (repo / "value.txt").write_text("unchanged\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
    return repo


def create(tmp_path, repository, **kwargs):
    return run_record.create(tmp_path / "runs", "advisory", repository,
                             b"Preserve correct behavior.", **kwargs)


def system_one():
    return importlib.import_module("harness.system_one")


class Provider:
    """Only the remote answers are substituted; the subject records and acts on them."""
    def __init__(self, probability=0.9, error=None):
        self.calls = []
        self.probability = probability
        self.error = error

    def __call__(self, payload):
        self.calls.append(payload)
        if self.error:
            raise self.error
        return {"model": payload["model"], "answers": {
            rule: {"type": "noul", "noul": self.probability}
            for rule in payload["questions"]
        }}


def lane(root, name="coder", text="Read tests/private_oracle.py\n"):
    p = root / "tmux-lanes" / (name + "-lane.log")
    p.parent.mkdir(exist_ok=True)
    p.write_text(text)
    return p


def inbox(root):
    p = root / "orchestrator/inbox.jsonl"
    return [json.loads(row) for row in p.read_text().splitlines()] if p.exists() else []


def durable_text(root):
    return "\n".join(p.read_text() for p in root.rglob("*") if p.is_file()
                      and p.suffix in {".json", ".jsonl"})


@pytest.mark.parametrize("bad", [0, -1, 1001, True, 1.5])
def test_invalid_call_ceiling_does_not_create_a_run(tmp_path, repository, bad):
    with pytest.raises(ValueError):
        create(tmp_path, repository, jev_mode="on", jev_calls=bad)
    assert not (tmp_path / "runs/advisory/harness.json").exists()


def test_unknown_mode_does_not_create_a_run(tmp_path, repository):
    with pytest.raises(ValueError):
        create(tmp_path, repository, jev_mode="silently-ignore", jev_calls=1)
    assert not (tmp_path / "runs/advisory/harness.json").exists()


@pytest.mark.parametrize("credential", ["TYPESAFE_API_KEY", "JMC_JEV_API_KEY"])
def test_auto_mode_with_key_reaches_provider_without_persisting_key(
    tmp_path, repository, monkeypatch, credential
):
    secret = "synthetic-key-never-send"
    monkeypatch.setenv(credential, secret)
    root = create(tmp_path, repository)
    raw = lane(root)
    provider = Provider()
    system_one().tick(root, call=provider)
    assert len(provider.calls) == 1
    assert raw.read_text() == "Read tests/private_oracle.py\n"
    assert secret not in durable_text(root)
    assert inbox(root), "An observed finding must reach the existing Orchestrator inbox"


@pytest.mark.parametrize("mode", ["off", "auto"])
def test_disabled_or_keyless_auto_preserves_observation_without_provider(
    tmp_path, repository, mode
):
    root = create(tmp_path, repository, jev_mode=mode)
    raw = lane(root)
    provider = Provider()
    system_one().tick(root, call=provider)
    assert not provider.calls
    assert raw.read_text() == "Read tests/private_oracle.py\n"


def test_environment_off_overrides_key_and_explicit_on_overrides_environment(
    tmp_path, repository, monkeypatch
):
    monkeypatch.setenv("TYPESAFE_API_KEY", "fake")
    monkeypatch.setenv("FACTORY_JEV_SCREEN", "off")
    root = create(tmp_path, repository)
    lane(root)
    provider = Provider()
    system_one().tick(root, call=provider)
    assert not provider.calls
    root2 = run_record.create(tmp_path / "runs", "enabled", repository, b"Task",
                              jev_mode="on", jev_calls=1)
    lane(root2)
    system_one().tick(root2, call=provider)
    assert len(provider.calls) == 1


def test_existing_unconfigured_run_stays_disabled(tmp_path, repository):
    root = create(tmp_path, repository, jev_mode="off")
    record = json.loads((root / "harness.json").read_text())
    # Simulate a record predating this feature, keeping only established v3 fields.
    old = {key: value for key, value in record.items() if key in {
        "factory_version", "schema_version", "run_id", "repository", "resolved_commit",
        "resolved_tree", "task_digest", "budget_usd", "log", "tmux_socket", "created_at"
    }}
    (root / "harness.json").write_text(json.dumps(old))
    lane(root)
    provider = Provider()
    system_one().tick(root, call=provider)
    assert not provider.calls


def test_each_tick_bounds_provider_work_and_eventual_chunks_are_not_lost(tmp_path, repository):
    root = create(tmp_path, repository, jev_mode="on", jev_calls=10)
    first = lane(root, "coder", "First suspicious activity\n")
    second = lane(root, "tester", "Second suspicious activity\n")
    provider = Provider()
    for _ in range(5):
        before = len(provider.calls)
        system_one().tick(root, call=provider)
        assert len(provider.calls) - before <= 1
    assert len(provider.calls) == 2
    sent = json.dumps(provider.calls)
    assert "First suspicious activity" in sent and "Second suspicious activity" in sent
    assert first.read_text() == "First suspicious activity\n"
    assert second.read_text() == "Second suspicious activity\n"


def test_repeated_ticks_and_new_module_instance_do_not_resend_completed_chunk(tmp_path, repository):
    root = create(tmp_path, repository, jev_mode="on", jev_calls=3)
    raw = lane(root)
    provider = Provider()
    system_one().tick(root, call=provider)
    original_messages = inbox(root)
    assert len(provider.calls) == 1 and original_messages
    importlib.reload(system_one()).tick(root, call=provider)
    system_one().tick(root, call=provider)
    assert len(provider.calls) == 1
    assert inbox(root) == original_messages
    with raw.open("a") as f:
        f.write("New activity after the receipt\n")
    system_one().tick(root, call=provider)
    assert len(provider.calls) == 2


def test_call_ceiling_is_durable_and_exhaustion_is_visible(tmp_path, repository):
    root = create(tmp_path, repository, jev_mode="on", jev_calls=1)
    raw = lane(root)
    provider = Provider()
    system_one().tick(root, call=provider)
    with raw.open("a") as f:
        f.write("A second observation\n")
    importlib.reload(system_one()).tick(root, call=provider)
    assert len(provider.calls) == 1
    messages = "\n".join(row["text"] for row in inbox(root))
    assert "unavailable" in messages.lower()
    assert "ceiling" in messages.lower() or "budget" in messages.lower()


def test_provider_failure_is_not_a_clean_result_and_is_not_retried(tmp_path, repository):
    root = create(tmp_path, repository, jev_mode="on", jev_calls=3)
    lane(root)
    provider = Provider(error=OSError("SENSITIVE_PROVIDER_RESPONSE"))
    system_one().tick(root, call=provider)
    system_one().tick(root, call=provider)
    assert len(provider.calls) == 1
    messages = "\n".join(row["text"] for row in inbox(root))
    assert "unavailable" in messages.lower()
    assert "SENSITIVE_PROVIDER_RESPONSE" not in durable_text(root)


def test_interrupted_request_reservation_is_not_reissued_after_restart(tmp_path, repository):
    class SimulatedExit(BaseException):
        pass

    root = create(tmp_path, repository, jev_mode="on", jev_calls=3)
    lane(root)
    interrupted = Provider(error=SimulatedExit())
    with pytest.raises(SimulatedExit):
        system_one().tick(root, call=interrupted)
    resumed = Provider()
    importlib.reload(system_one()).tick(root, call=resumed)
    assert not resumed.calls
    messages = "\n".join(row["text"] for row in inbox(root))
    assert "unavailable" in messages.lower()
    assert "interrupt" in messages.lower()


def test_worker_does_not_screen_its_own_outputs_or_notify_validator(tmp_path, repository):
    root = create(tmp_path, repository, jev_mode="on", jev_calls=10)
    (root / "orchestrator-lane.log").write_text("Model's screening discussion\n")
    run_record.message(root, "orchestrator", "host", "Prior System One finding")
    initial_inbox = inbox(root)
    provider = Provider()
    system_one().tick(root, call=provider)
    assert not provider.calls
    assert inbox(root) == initial_inbox
    lane(root)
    system_one().tick(root, call=provider)
    for _ in range(3):
        system_one().tick(root, call=provider)
    assert len(provider.calls) == 1
    assert not (root / "validator/inbox.jsonl").exists()


def test_enabled_configuration_is_public_and_secret_free(tmp_path, repository, monkeypatch):
    monkeypatch.setenv("JMC_JEV_API_KEY", "synthetic-confidential-key")
    root = create(tmp_path, repository, jev_calls=17)
    config = json.loads((root / "harness.json").read_text())["system_one"]
    assert config["mode"] == "auto" and config["enabled"] is True
    assert config["max_calls"] == 17
    assert config["model"] and config["rulebook_digest"]
    assert "synthetic-confidential-key" not in json.dumps(config)


def test_missing_key_is_explicit_unavailable_without_network(tmp_path, repository):
    root = create(tmp_path, repository, jev_mode="on", jev_calls=1)
    lane(root)
    result = system_one().tick(root)
    assert result["status"] == "unavailable"
    assert result["reason"] == "missing-key"
    assert inbox(root)


def test_low_confidence_result_is_retained_but_does_not_clear_or_notify(tmp_path, repository):
    root = create(tmp_path, repository, jev_mode="on", jev_calls=1)
    lane(root)
    # Existing independent concern must not be removed by a negative screen.
    run_record.message(root, "orchestrator", "validator", "Existing concrete concern")
    before = inbox(root)
    result = system_one().tick(root, call=Provider(probability=0.1))
    assert result["status"] == "observed"
    assert inbox(root) == before
    receipts = [json.loads(p.read_text()) for p in (root / "system-one").glob("*.json")
                if p.name != "state.json"]
    assert len(receipts) == 1 and receipts[0]["status"] == "observed"
    assert receipts[0]["results"]


def test_malformed_remote_answer_is_unavailable_not_a_model_verdict(tmp_path, repository):
    root = create(tmp_path, repository, jev_mode="on", jev_calls=1)
    lane(root)
    result = system_one().tick(root, call=lambda payload: {"model": "wrong", "answers": {}})
    assert result["status"] == "unavailable"
    assert inbox(root)


def test_missing_key_does_not_spend_the_budget_for_later_real_requests(
    tmp_path, repository, monkeypatch
):
    root = create(tmp_path, repository, jev_mode="on", jev_calls=1)
    raw = lane(root)
    result = system_one().tick(root)
    assert result["status"] == "unavailable" and result["reason"] == "missing-key"
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-later-key")
    with raw.open("a") as f:
        f.write("New work after the operator supplied the key\n")
    provider = Provider()
    result = system_one().tick(root, call=provider)
    assert len(provider.calls) == 1, "No provider request occurred while the key was missing"
    assert result["status"] == "observed"


def test_continuously_appending_lane_cannot_starve_another_ready_source(tmp_path, repository):
    root = create(tmp_path, repository, jev_mode="on", jev_calls=3)
    busy = lane(root, "a-coder", "initial busy work\n")
    waiting = lane(root, "z-tester", "WAITING-SOURCE-MUST-BE-SCREENED\n")
    provider = Provider()
    for n in range(3):
        system_one().tick(root, call=provider)
        with busy.open("a") as f:
            f.write(f"fresh busy work {n}\n")
    assert "WAITING-SOURCE-MUST-BE-SCREENED" in json.dumps(provider.calls)
    assert waiting.read_text() == "WAITING-SOURCE-MUST-BE-SCREENED\n"
    assert len(provider.calls) <= 3


def test_optional_screen_worker_launch_failure_preserves_healthy_coordinators(
    tmp_path, repository, monkeypatch, capsys
):
    root = create(tmp_path, repository, jev_mode="on", jev_calls=1)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name in ("codex", "agy"):
        path = binaries / name
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", str(binaries) + ":/usr/bin:/bin")
    monkeypatch.setenv("FACTORY_SEAT_BILLING", "plan")
    monkeypatch.delenv("FACTORY_CONFIG", raising=False)
    for role in ("VALIDATOR", "ORCHESTRATOR", "CODER", "TESTER"):
        monkeypatch.setenv(f"FACTORY_{role}_AGENT", "agy" if role == "ORCHESTRATOR" else "codex")
        monkeypatch.setenv(f"FACTORY_{role}_MODEL", "")
        monkeypatch.setenv(f"FACTORY_{role}_EFFORT", "")
    calls = []

    def tmux(argv, **kwargs):
        assert argv[0] == "tmux", "No real agent or process may be launched in this test"
        calls.append(argv)
        if "-n" in argv and argv[argv.index("-n") + 1] == "system-one":
            raise subprocess.CalledProcessError(1, argv, stderr="screen worker unavailable")
        return subprocess.CompletedProcess(argv, 1 if "has-session" in argv else 0)

    monkeypatch.setattr(subprocess, "run", tmux)
    run_record.start(root.parent, root.name)
    windows = [args[args.index("-n") + 1] for args in calls if "-n" in args]
    assert "validator" in windows and "orchestrator" in windows and "system-one" in windows
    assert not any("kill-session" in args for args in calls)
    diagnostics = capsys.readouterr()
    assert diagnostics.err or inbox(root), "Optional screening failure must remain visible"


def test_cli_create_accepts_screen_mode_and_call_ceiling(tmp_path, repository):
    task = tmp_path / "task.md"
    task.write_text("Preserve behavior")
    result = subprocess.run(
        [sys.executable, str(Path(run_record.__file__)), "create", "--runs",
         str(tmp_path / "runs"), "--run", "cli-screen", "--repo", str(repository),
         "--task-file", str(task), "--jev-screen", "on", "--jev-screen-calls", "2"],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    assert result.returncode == 0, result.stderr
    config = json.loads((tmp_path / "runs/cli-screen/harness.json").read_text())["system_one"]
    assert config["mode"] == "on" and config["enabled"] is True
    assert config["max_calls"] == 2
