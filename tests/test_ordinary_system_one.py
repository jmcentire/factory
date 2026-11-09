"""Independent ordinary-run contracts: retained permission, advisory batches and PR effects.

Real local repositories, retained files and native CLI recorders are used. Provider responses
are supplied at the existing request seam; they do not prove an actual model reviewed work.
No live credentials, profile sourcing, provider calls or remote PR mutations are permitted.
"""

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness import dispatcher, jev_screen, run_record, validate_candidate
from tests.test_builder_provenance import PR_LINE, TRAILERS, commit
from tests.test_builder_provenance import builder_run as builder_run
from tests.test_builder_system_one import response
from tests.test_coordinator_startup import launch as launch

KEYS = ("TYPESAFE_API_KEY", "JMC_JEV_API_KEY", "VOYAGE_API_KEY")


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in (*KEYS, "FACTORY_SYSTEM_ONE_PROFILE", "FACTORY_CONFIG", "BASH_ENV", "ENV"):
        monkeypatch.delenv(name, raising=False)


def test_create_retains_explicit_permission_over_local_config(builder_run, tmp_path, monkeypatch):
    runs, _, repo, _ = builder_run
    config = tmp_path / "config.toml"
    cases = ((None, None, False), (True, None, True), (False, None, False),
             (True, "0", False), (False, "1", True))
    for index, (configured, explicit, expected) in enumerate(cases):
        config.write_text("" if configured is None else
                          f"[system_one]\nprofile = {str(configured).lower()}\n")
        monkeypatch.setenv("FACTORY_CONFIG", str(config))
        if explicit is None:
            monkeypatch.delenv("FACTORY_SYSTEM_ONE_PROFILE", raising=False)
        else:
            monkeypatch.setenv("FACTORY_SYSTEM_ONE_PROFILE", explicit)
        root = run_record.create(runs, f"permission-{index}", repo, b"Retain explicit permission")
        saved = (root / "harness.json").read_bytes()
        assert json.loads(saved)["system_one_profile"] is expected
        monkeypatch.setenv("FACTORY_SYSTEM_ONE_PROFILE", "0" if expected else "1")
        _, loaded = run_record.load(runs, f"permission-{index}")
        assert loaded["system_one_profile"] is expected
        assert (root / "harness.json").read_bytes() == saved


def test_retained_grant_reaches_startup_and_checkpoint_without_leaking_keys(
    launch, builder_run, monkeypatch,
):
    seat_root, _, _, capture, _, execute = launch
    runs, root, repo, _ = builder_run
    for selected in (seat_root, root):
        path = selected / "harness.json"
        doc = json.loads(path.read_text())
        doc["system_one_profile"] = True
        path.write_text(json.dumps(doc))
    for key in KEYS:
        monkeypatch.setenv(key, "dummy-" + key)
    monkeypatch.delenv("FACTORY_SYSTEM_ONE_PROFILE", raising=False)
    calls = capture()
    for role in ("validator", "orchestrator"):
        env = execute(calls, role)["env"]
        assert env["FACTORY_SYSTEM_ONE_PROFILE"] == "1" and not set(KEYS) & env.keys()
    profiles = []

    def missing_key(payload, *, profile=None):
        profiles.append(profile)
        raise jev_screen.ScreenError("missing-key")

    monkeypatch.setattr(jev_screen, "request", missing_key)
    monkeypatch.setenv("FACTORY_SYSTEM_ONE_PROFILE", "0")  # Later ambient state cannot revoke it.
    candidate = commit(repo, "Real candidate\n\n" + TRAILERS)
    code, attempt = validate_candidate.validate(
        runs, "task", repo, candidate,
        [sys.executable, "-c", f"import os; assert not set({KEYS!r}) & os.environ.keys()"],
    )
    result = json.loads((attempt / "result.json").read_text())
    assert code == 0 and profiles == [True]
    assert result["builder_provenance"]["system_one"]["status"] == "unavailable"
    assert result["builder_provenance"]["system_one"]["reason"] == "missing-key"


def test_observe_cli_batches_new_activity_caches_replay_and_keeps_partial_coverage_visible(
    builder_run, monkeypatch, capsys,
):
    _, root, _, _ = builder_run
    lanes = root / "tmux-lanes"
    lanes.mkdir()
    for role in ("coder", "tester"):
        (lanes / f"{role}-lane.log").write_text(f"{role}: working on the public task\n")
    private = "DUMMY-PRIVATE-CONTEXT-DO-NOT-FORWARD"
    launch = {
        "schema_version": "factory-tmux-lane-launch/1", "slot": "coder", "role": "coder",
        "agent": "codex", "model": "fixture-model", "model_source": "operator",
        "status": "active", "metered": True, "persistent": True,
        "caps": {"spend_cap_usd": 3, "usd_per_hour": 1, "wall_cap_s": 90,
                 "enforcement": "wrapper"}, "env": {"TOKEN": private}, "argv": [private],
    }
    (lanes / "coder-launch.jsonl").write_text(json.dumps(launch) + "\n")
    session_id = "dummy-private-native-session-id"
    (lanes / "coder-persistent.json").write_text(json.dumps({
        "schema": "factory-persistent-lane/1", "slot": "coder", "agent": "codex",
        "phase": "working", "turns": 2, "session_id": session_id,
        "token": private, "pending": private,
    }))
    (lanes / "tester-task.txt").write_text("Check the actual requested behavior independently.")
    (lanes / "tester-prompt.txt").write_text("Retained Tester testing-standard guidance.")
    (lanes / "coder-prompt.txt").write_text(private)
    artifacts = root / "artifacts"
    artifacts.mkdir(exist_ok=True)
    (artifacts / "test.txt").write_text("def test_result(): assert compute(2) == 4")
    (artifacts / "behavior.txt").write_text("compute doubles its input")
    run_record.message(root, "orchestrator", "validator", json.dumps({"jev_evidence": {
        "test_excerpt": "artifacts/test.txt", "behavior_contract": "artifacts/behavior.txt",
    }}))
    original = {path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()}
    calls = []
    # The dispatcher uses the adjacent module, as the existing screen fixtures do.
    screen = importlib.import_module("jev_screen")
    monkeypatch.setattr(screen, "request", lambda payload, **kwargs:
                        (calls.append(payload), response(payload))[1])
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        pytest.fail("observation must not launch a worker or terminal command"))
    pure = dispatcher.observe(root, {})
    assert calls == [] and len(pure["entries"]) >= 2
    assert {path.relative_to(root): path.read_bytes()
            for path in root.rglob("*") if path.is_file()} == original
    argv = ["dispatcher.py", "--run", "task", "--root", str(root), "--observe"]
    monkeypatch.setattr(sys, "argv", argv)
    dispatcher.main()
    first = json.loads(capsys.readouterr().out)
    report = first["system_one"]
    assert len(calls) == 1 and report["status"] == "observed", report
    observations = calls[0]["state"]["observations"]
    coder = next(row for row in observations if row["source"] == "tmux-lanes/coder-lane.log")
    tester = next(row for row in observations if row["source"] == "tmux-lanes/tester-lane.log")
    assert "fixture-model" in coder["role_assignments"]
    assert "spend_cap_usd" in coder["billing_context"] and "3" in coder["billing_context"]
    assert "working" in coder["session_context"] and "sha256" in coder["session_context"]
    assert "Retained Tester testing-standard guidance." in tester["brief"]
    assert "Check the actual requested behavior independently." in tester["brief"]
    assert "brief" not in coder
    serialized = json.dumps(calls[0]["state"])
    assert private not in serialized and session_id not in serialized
    assert '"env"' not in serialized and '"argv"' not in serialized
    assert any(row.get("test_excerpt") == "def test_result(): assert compute(2) == 4"
               and row.get("behavior_contract") == "compute doubles its input"
               for row in observations)
    assert report["coverage"]["weak_test_evidence"]["covered"]
    assert report["coverage"]["test_setup_misses_behavior"]["covered"]
    assert report["coverage"]["code_contract_conflict"]["uncovered"]
    assert any(row["band"] == "unavailable" for row in report["results"].values())
    assert any(row["uncovered"] for row in report["coverage"].values())
    dispatcher.main()
    replay = json.loads(capsys.readouterr().out)
    assert len(calls) == 1 and replay["system_one"]["evidence"] == report["evidence"]
    with (lanes / "coder-lane.log").open("a") as stream:
        stream.write("coder: new independently observable progress\n")
    monkeypatch.setattr(sys, "argv", [*argv, "--after", json.dumps(first["cursors"])])
    dispatcher.main()
    second = json.loads(capsys.readouterr().out)
    assert len(calls) == 2 and second["system_one"]["status"] == "observed"
    monkeypatch.setattr(sys, "argv", [*argv, "--after", json.dumps(second["cursors"])])
    dispatcher.main()
    empty = json.loads(capsys.readouterr().out)
    assert len(calls) == 2 and empty["system_one"]["reason"] == "no-new-activity"
    # A JSONL message may straddle the observer's 16 KiB byte budget.
    run_record.message(root, "orchestrator", "validator", " " * 17000 + json.dumps({
        "jev_evidence": {"test_excerpt": "artifacts/test.txt",
                         "behavior_contract": "artifacts/behavior.txt"},
    }))
    original[Path("orchestrator/inbox.jsonl")] = (root / "orchestrator/inbox.jsonl").read_bytes()
    monkeypatch.setattr(sys, "argv", [*argv, "--after", json.dumps(empty["cursors"])])
    dispatcher.main()
    partial = json.loads(capsys.readouterr().out)
    assert len(calls) == 2  # The incomplete message cannot supply artifact evidence.
    monkeypatch.setattr(sys, "argv", [*argv, "--after", json.dumps(partial["cursors"])])
    dispatcher.main()
    completed = json.loads(capsys.readouterr().out)
    assert len(calls) == 3
    assert completed["system_one"]["coverage"]["weak_test_evidence"]["covered"]
    assert any(row.get("test_excerpt") == "def test_result(): assert compute(2) == 4"
               for row in calls[-1]["state"]["observations"])
    dispatcher.main()
    assert json.loads(capsys.readouterr().out)["system_one"]["evidence"] == (
        completed["system_one"]["evidence"]
    )
    monkeypatch.setattr(sys, "argv", [*argv, "--after", json.dumps(completed["cursors"])])
    dispatcher.main()
    assert json.loads(capsys.readouterr().out)["system_one"]["reason"] == "no-new-activity"
    assert len(calls) == 3
    added = {path.relative_to(root) for path in root.rglob("*") if path.is_file()} - original.keys()
    assert added and all(path.is_relative_to("orchestrator/system-one") for path in added)
    for path, before in original.items():
        expected = before + b"coder: new independently observable progress\n" if (
            path == lanes.relative_to(root) / "coder-lane.log"
        ) else before
        assert (root / path).read_bytes() == expected
    assert not list(root.rglob("*.blocking"))


def test_pr_mutation_is_structured_then_checked_and_fetch_failure_never_retries_mutation(
    builder_run, tmp_path, monkeypatch,
):
    runs, root, repo, _ = builder_run
    candidate = commit(repo, "Requested change\n\n" + TRAILERS)
    body = tmp_path / "body.md"
    body.write_text(f"Requested change\n\n{PR_LINE}\nFactoryRun: task\n")
    url = "https://github.com/fixture-owner/product/pull/7"
    snapshot = {"url": url, "headRefOid": candidate, "body": body.read_text()}
    binary = tmp_path / "gh-bin"
    binary.mkdir()
    log, fail = tmp_path / "gh.jsonl", tmp_path / "fail-view"
    config_marker = tmp_path / "candidate-config-executed"
    hook = tmp_path / "candidate-fsmonitor"
    hook.write_text(f"#!/bin/sh\n/usr/bin/touch '{config_marker}'\n")
    hook.chmod(0o755)
    subprocess.run(["git", "config", "core.fsmonitor", str(hook)], cwd=repo, check=True)
    subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, check=True)
    assert config_marker.exists()  # Positive control: candidate config really executes here.
    config_marker.unlink()
    gh = binary / "gh"
    gh.write_text(
        f"#!{sys.executable}\nimport json, os, pathlib, subprocess, sys\n"
        "status = subprocess.run(['git', 'status', '--porcelain'], capture_output=True)\n"
        f"with open({str(log)!r}, 'a') as out:\n"
        "    out.write(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(),\n"
        "        'git_status': status.returncode, 'env_keys': list(os.environ),\n"
        "        'gh_host': os.environ.get('GH_HOST')}) + '\\n')\n"
        "if sys.argv[1:3] == ['pr', 'view']:\n"
        f"    if pathlib.Path({str(fail)!r}).exists(): raise SystemExit(9)\n"
        f"    print({json.dumps(snapshot)!r})\n"
        f"else: print({url!r})\n"
    )
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(jev_screen, "request", lambda payload, **kwargs: response(payload))
    for key in KEYS:
        monkeypatch.setenv(key, "dummy-" + key)
    monkeypatch.setenv("GH_HOST", "unrelated.invalid")
    title = "Literal title $(touch SHOULD_NOT_EXIST)"
    created = run_record.publish_pr(
        runs, "task", repo, candidate, "create", body_file=body,
        title=title, head="task-branch", base="main", draft=True,
        github_repo="fixture-owner/product",
    )
    assert created["mutation"] == "completed" and created["verification"] == "consistent"
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert rows[0]["argv"][:2] == ["pr", "create"]
    for option, value in (("--title", title), ("--body-file", str(body)),
                          ("--head", "task-branch"), ("--base", "main"),
                          ("--repo", "github.com/fixture-owner/product")):
        args = rows[0]["argv"]
        assert f"{option}={value}" in args or (
            option in args and args[args.index(option) + 1] == value
        )
    assert "--draft" in rows[0]["argv"]
    assert "--label=Factory" in rows[0]["argv"]
    assert rows[1]["argv"] == ["pr", "view", url, "--json", "url,body,headRefOid"]
    assert not (repo / "SHOULD_NOT_EXIST").exists()
    edited = run_record.publish_pr(runs, "task", repo, candidate, "edit", body_file=body, pr=url)
    assert edited["mutation"] == "completed" and edited["verification"] == "consistent"
    fail.touch()
    failed = run_record.publish_pr(runs, "task", repo, candidate, "edit", body_file=body, pr=url)
    assert failed["mutation"] == "completed" and failed["verification"] == "unavailable"
    assert failed["reason"]
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert [row["argv"][1] for row in rows] == ["create", "view", "edit", "view", "edit", "view"]
    for row in rows:
        assert not Path(row["cwd"]).is_relative_to(repo)
        assert row["git_status"] != 0  # gh's own Git discovery never enters the candidate.
        assert row["gh_host"] == "github.com"
        assert not set(KEYS) & set(row["env_keys"])
    assert not config_marker.exists()
    assert len(list((root / "provenance").glob("*.json"))) == 2
    notices = (root / "orchestrator/inbox.jsonl").read_text()
    assert "unavailable" in notices and url in notices
