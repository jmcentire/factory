"""GUI application instances are not schedules; actual unknown jobs stay visible."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "harness/sched_audit.sh"


@pytest.mark.parametrize("label", [
    "application.com.google.Chrome.123.456",
    "application.com.apple.Music.781.998",
])
def test_gui_relaunch_does_not_create_an_unregistered_timer(tmp_path, label):
    result = audit(tmp_path, launch_labels=[label, "org.fixture.backup"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "cadence clean" in result.stdout


@pytest.mark.parametrize("label", [
    "org.unknown.timer",
    "application.org.unknown.timer",
    "application.org.unknown.123.456.extra",
])
def test_similar_or_unknown_job_labels_are_not_hidden(tmp_path, label):
    result = audit(tmp_path, launch_labels=["application.com.google.Chrome.1.2", label])
    assert result.returncode == 3
    assert f"UNREGISTERED: {label}" in result.stdout
    assert "UNREGISTERED: application.com.google.Chrome.1.2" not in result.stdout


def test_gui_label_exception_does_not_apply_to_cron(tmp_path):
    cron = "0 * * * * application.com.example.12.34"
    result = audit(tmp_path, launch_labels=[], cron=cron)
    assert result.returncode == 3
    assert f"UNREGISTERED: {cron}" in result.stdout


def audit(tmp_path, *, launch_labels, cron=""):
    binary = tmp_path / "bin"
    binary.mkdir()
    scripts = {
        "launchctl": 'printf "%s\\n" "$LAUNCH_FIXTURE"',
        "crontab": 'printf "%s\\n" "$CRON_FIXTURE"',
        "atq": "exit 0",
        "systemctl": "exit 0",
    }
    for name, body in scripts.items():
        path = binary / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "schedule.registry").write_text("^org\\.fixture\\.backup$\n")
    env = {**os.environ, "PATH": f"{binary}:/usr/bin:/bin", "HARNESS_DIR": str(registry),
           "LAUNCH_FIXTURE": "PID Status Label\n" + "\n".join(
               f"123 0 {label}" for label in launch_labels), "CRON_FIXTURE": cron}
    env.pop("SCHED_AUDIT_INPUT", None)
    return subprocess.run(["/bin/bash", str(SCRIPT)], env=env, cwd=tmp_path,
                          capture_output=True, text=True, timeout=5)
