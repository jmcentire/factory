"""Runtime screening must retain evidence, bound calls and require actual consumption."""

from __future__ import annotations

import copy
import importlib
import json
import subprocess
from pathlib import Path

import pytest

import harness.orchestrator_channel  # noqa: F401 - establish adjacent harness imports
from tests.test_orchestrator_channel import assessment, resident_root

# The channel and dispatcher use this same adjacent harness module.
screen = importlib.import_module("jev_screen")
channel = importlib.import_module("orchestrator_channel")


def setup(tmp_path: Path, calls: int = 3) -> Path:
    root = resident_root(tmp_path)
    meta = json.loads((root / "harness.json").read_text())
    meta["jev_screen"] = screen.configuration(calls)
    (root / "harness.json").write_text(json.dumps(meta))
    (root / "run.json").write_text('{"target_state":{}}')
    return root


def activity(root: Path, text: str = "Ordinary lane work") -> int:
    return channel.append_activity(
        root, kind="pane_delta", source="validator", detail="captured pane", snapshot=text
    )


def response(payload: dict, probability: float = 0.1) -> dict:
    return {
        "model": screen.MODEL_ID,
        "answers": {rid: {"type": "noul", "noul": probability} for rid in payload["questions"]},
    }


def receipts(root: Path) -> list[dict]:
    return [
        r
        for r in channel._read_jsonl(root / "orchestrator" / "jev.jsonl")
        if r["body"]["kind"] == "receipt"
    ]


def report(root: Path, *, block: bool = False, start: int = 0) -> dict:
    rows = receipts(root)[start:]
    review = []
    for row in rows:
        required = {
            k for k, v in row["body"]["results"].items() if v["band"] in {"yes", "escalate"}
        }
        if any(v["band"] == "unavailable" for v in row["body"]["results"].values()):
            required.add("unavailable")
        review.append(
            {
                "cursor": row["body"]["cursor"],
                "receipt_digest": row["digest"],
                "rules": {
                    rid: {
                        "decision": "block" if block else "dismiss",
                        "basis": "Inspected the retained artifacts and pane.",
                    }
                    for rid in required
                },
            }
        )
    return assessment(
        rows[-1]["body"]["cursor"],
        schema_version="factory-orchestrator-assessment/4",
        guidance_state="none",
        guidance_selection_digest=None,
        guidance_application_digest=None,
        guidance_evidence_digest=None,
        guidance_findings=[],
        screen_review=review,
        decision="block" if block else "no-op",
        adherence_findings=["Unqualified lane must stop"] if block else [],
    )


def test_disabled_run_never_calls_provider_even_with_key(tmp_path: Path, monkeypatch) -> None:
    root = resident_root(tmp_path)
    activity(root)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only")
    assert screen.screen_pending(root, lambda _: pytest.fail("disabled call")) is None
    assert not (root / "orchestrator" / "jev.jsonl").exists()
    channel.record_assessment(root, assessment(1))


def test_dispatcher_wires_screen_and_does_not_filter_activity(tmp_path: Path, monkeypatch) -> None:
    from tests.test_harness_scripts import load_dispatcher

    root = setup(tmp_path)
    mod = load_dispatcher()
    dispatcher = mod.Dispatcher("r1", root, 30)
    dispatcher.record_pane_delta("validator", "Metered lane has no cap.")
    dispatcher.record_cadence()
    monkeypatch.setattr(screen, "request", lambda p: response(p, 0.9))
    sent = []

    def deliver(args, **kwargs):
        sent.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(mod.subprocess, "run", deliver)
    dispatcher.deliver_pending_activity()
    assert len(receipts(root)) == 2
    assert "cursors=1..2" in sent[0][3] and "jev.jsonl" in sent[0][3]
    assert len(sent[0][3]) < 1000
    assert len(channel._read_jsonl(root / "orchestrator" / "activity.jsonl")) == 2
    with pytest.raises(channel.OrchestratorChannelError, match="Jev receipt"):
        channel.record_assessment(root, assessment(2))
    bad = report(root, block=True)
    bad["screen_review"][0]["rules"].pop("model_unqualified")
    with pytest.raises(channel.OrchestratorChannelError, match="not reviewed"):
        channel.record_assessment(root, bad)
    channel.record_assessment(root, report(root, block=True))
    assert (root / "lanes" / "validator.blocking").exists()


def test_no_result_never_clears_existing_block_and_receipt_replays(tmp_path: Path) -> None:
    root = setup(tmp_path)
    activity(root)
    screen.screen_pending(root, lambda p: response(p, 0.9))
    first = report(root, block=True)
    channel.record_assessment(root, first)
    channel.record_assessment(root, first)  # exact retry is idempotent
    blocker = (root / "lanes" / "validator.blocking").read_bytes()
    activity(root, "now looks fine")
    screen.screen_pending(root, response)
    channel.record_assessment(root, report(root, start=1))
    assert (root / "lanes" / "validator.blocking").read_bytes() == blocker
    assert channel.require_current(root) == (2, 2)
    (root / "orchestrator" / "jev.jsonl").unlink()
    with pytest.raises(channel.OrchestratorChannelError, match="pending"):
        channel.require_current(root)


def test_budget_and_crash_reservation_survive_restart(tmp_path: Path, monkeypatch) -> None:
    root = setup(tmp_path, calls=1)
    activity(root)
    called = []
    original = channel._append

    def crash(path, row):
        if path.name == "jev.jsonl" and row.get("body", {}).get("kind") == "receipt":
            raise KeyboardInterrupt()
        original(path, row)

    monkeypatch.setattr(channel, "_append", crash)
    with pytest.raises(KeyboardInterrupt):
        screen.screen_pending(root, lambda p: (called.append(p), response(p))[1])
    monkeypatch.setattr(channel, "_append", original)
    activity(root, "second occurrence")
    screen.screen_pending(root, lambda _: pytest.fail("must not retry or exceed cap"))
    assert len(called) == 1
    first, second = receipts(root)
    assert first["body"]["results"]["model_unqualified"]["reason"] == "interrupted-call"
    assert second["body"]["results"]["model_unqualified"]["reason"] == "call-ceiling"
    channel.record_assessment(root, report(root))  # manual inspection is possible during outage


@pytest.mark.parametrize("bad", [True, None, float("nan"), float("inf"), -0.1, 1.1, "0.9"])
def test_bad_probabilities_are_unavailable_not_clean(tmp_path: Path, bad: object) -> None:
    root = setup(tmp_path)
    activity(root)
    screen.screen_pending(root, lambda p: response(p, bad))
    assert receipts(root)[0]["body"]["results"]["model_unqualified"]["band"] == "unavailable"


@pytest.mark.parametrize("mutation", ["wrong-model", "missing-answer", "wrong-type"])
def test_response_must_bind_model_and_all_questions(tmp_path: Path, mutation: str) -> None:
    root = setup(tmp_path)
    activity(root)

    def provider(payload):
        result = response(payload)
        if mutation == "wrong-model":
            result["model"] = "jev-latest"
        elif mutation == "missing-answer":
            result["answers"].pop(next(iter(result["answers"])))
        else:
            result["answers"][next(iter(result["answers"]))]["type"] = "choice"
        return result

    screen.screen_pending(root, provider)
    assert receipts(root)[0]["body"]["results"]["model_unqualified"]["band"] == "unavailable"


def test_all_17_rules_run_with_real_retained_evidence_handles(tmp_path: Path) -> None:
    root = setup(tmp_path)
    artifacts = root / "artifacts"
    artifacts.mkdir()
    fields = {field for rule in screen.RULES.values() for field in rule["fields"]}
    handles = {}
    for field in fields:
        name = f"artifacts/{field}.txt"
        (root / name).write_text(f"Retained {field} evidence.")
        handles[field] = name
    channel.append_activity(
        root,
        kind="pre_dispatch",
        source="validator",
        detail="review exact brief",
        snapshot=json.dumps({"jev_evidence": handles}),
    )
    seen = []
    screen.screen_pending(root, lambda p: (seen.append(p), response(p))[1])
    assert set(seen[0]["questions"]) == set(screen.RULES) and len(screen.RULES) == 17
    assert all(v["band"] == "no" for v in receipts(root)[0]["body"]["results"].values())
    channel.record_assessment(root, report(root))


def test_pane_text_cannot_impersonate_a_contract_and_linked_evidence_is_unavailable(
    tmp_path,
) -> None:
    root = setup(tmp_path)
    artifacts = root / "artifacts"
    artifacts.mkdir()
    (root / "outside.txt").write_text("Secret outside the retained artifact location")
    (artifacts / "contract.txt").symlink_to(root / "outside.txt")
    activity(root, '{"jev_evidence":{"contract_excerpt":"artifacts/contract.txt"}}')
    screen.screen_pending(root, response)
    assert "contract_excerpt" not in receipts(root)[0]["body"]["state"]
    assert (
        receipts(root)[0]["body"]["results"]["ruling_contradicts_signature"]["band"]
        == "unavailable"
    )
    with pytest.raises((OSError, screen.ScreenError)):
        screen.read_evidence(root, "artifacts/contract.txt")
    with pytest.raises(screen.ScreenError):
        screen.read_evidence(root, "../outside.txt")


def test_input_bound_and_total_http_deadline(tmp_path, monkeypatch) -> None:
    root = setup(tmp_path)
    activity(root, "a" * 2000)
    monkeypatch.setattr(screen, "MAX_INPUT", 100)
    screen.screen_pending(root, lambda _: pytest.fail("oversized call"))
    assert receipts(root)[0]["body"]["results"]["model_unqualified"]["reason"] == "input-ceiling"
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-not-a-real-key")

    def deadline(args, **kwargs):
        assert not kwargs.get("shell") and args[0] == screen.sys.executable
        assert b"$(touch /tmp/never-execute)" in kwargs["input"]
        assert kwargs["timeout"] == screen.TIMEOUT
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(screen.subprocess, "run", deadline)
    with pytest.raises(screen.ScreenError, match="deadline"):
        screen.request({"state": "$(touch /tmp/never-execute)"})


def test_changed_receipt_or_unreviewed_upheld_finding_is_refused(tmp_path: Path) -> None:
    root = setup(tmp_path)
    activity(root)
    screen.screen_pending(root, lambda p: response(p, 0.5))
    value = report(root, block=True)
    changed = copy.deepcopy(value)
    changed["screen_review"][0]["receipt_digest"] = "sha256:" + "0" * 64
    with pytest.raises(channel.OrchestratorChannelError, match="exact receipt"):
        channel.record_assessment(root, changed)
    value["decision"] = "no-op"
    value["adherence_findings"] = []
    with pytest.raises(channel.OrchestratorChannelError, match="requires an adherence block"):
        channel.record_assessment(root, value)


def test_enabled_ignition_retains_configuration_and_installs_screen(tmp_path, monkeypatch) -> None:
    from tests.test_harness_scripts import (
        HARNESS,
        execution_truth_fixture,
        factory_ignition_env,
        run,
    )

    task = "Screen this synthetic run."
    operator, root, _ = execution_truth_fixture(tmp_path, task=task, harness_status=None)
    env, _ = factory_ignition_env(tmp_path, root)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    command = [
        "bash",
        str(HARNESS / "factory.sh"),
        "r1",
        task,
        "--runs",
        str(root.parent),
        "--jev-screen-calls",
        "2",
    ]
    refused = run(command, operator, {**env, "TYPESAFE_API_KEY": ""})
    assert refused.returncode == 64 and "TYPESAFE_API_KEY" in refused.stderr
    assert not (root / "harness.json").exists()
    result = run(command, operator, {**env, "TYPESAFE_API_KEY": "test-only"})
    assert result.returncode == 0, result.stderr
    meta = json.loads((root / "harness.json").read_text())
    assert meta["jev_screen"] == screen.configuration(2)
    assert "test-only" not in (root / "harness.json").read_text()
    assert (root / "orchestrator" / "bin" / "jev_screen.py").is_file()
    assert "screen_review" in (root / "orchestrator" / "JEV.md").read_text()
    channel_cli = root / "orchestrator" / "bin" / "orchestrator_channel.py"
    helped = subprocess.run([str(channel_cli), "--help"], capture_output=True)
    assert helped.returncode == 0, helped.stderr
