"""Forced-negative drills for the harness scripts (docs/HARNESS.md controls 1-9).

Every control here is exercised in BOTH directions: the compliant path succeeds
and the violating path is refused with the declared exit code and signal. A
control that has never been watched firing is a documented intention, not a
control. All drills run against throwaway state under tmp_path; nothing touches
the repository's own .harness/ or DIRECTIVES/.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "harness"


def run(
    args: list[str],
    cwd: Path,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    if env_extra:
        env.update(env_extra)
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)


def read_chain(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# --------------------------------------------------------------------------
# Control 1 / 1a — directive ledger
# --------------------------------------------------------------------------


def dl(tmp: Path, *args: str) -> subprocess.CompletedProcess[str]:
    ledger = tmp / "DIRECTIVES" / "ledger.jsonl"
    return run(
        ["python3", str(HARNESS / "directive.py"), *args],
        cwd=tmp,
        env_extra={"DIRECTIVE_LEDGER": str(ledger)},
    )


def test_directive_append_verify_roundtrip(tmp_path: Path) -> None:
    r = dl(tmp_path, "append", "--scope", "run", "--text", "poll to tend the lanes",
           "--qualifier", "tend the lanes, not to produce artifacts")
    assert r.returncode == 0, r.stderr
    v = dl(tmp_path, "verify")
    assert v.returncode == 0 and "ok: 1 signed" in v.stdout


def test_directive_tamper_is_detected(tmp_path: Path) -> None:
    dl(tmp_path, "append", "--scope", "run", "--text", "two-way doors only")
    ledger = tmp_path / "DIRECTIVES" / "ledger.jsonl"
    ledger.write_text(ledger.read_text().replace("two-way doors", "doors"))
    v = dl(tmp_path, "verify")
    assert v.returncode != 0 and "content altered" in v.stderr


def test_supersession_refuses_silent_qualifier_drop(tmp_path: Path) -> None:
    dl(tmp_path, "append", "--scope", "run", "--text", "poll the lanes",
       "--qualifier", "to tend them")
    r = dl(tmp_path, "supersede", "D-0001", "--scope", "run", "--text", "poll faster")
    assert r.returncode != 0
    assert "undispositioned qualifiers" in r.stderr and "to tend them" in r.stderr


def test_supersession_with_dispositions_carries_qualifiers(tmp_path: Path) -> None:
    dl(tmp_path, "append", "--scope", "run", "--text", "poll the lanes",
       "--qualifier", "to tend them")
    r = dl(tmp_path, "supersede", "D-0001", "--scope", "run", "--text", "poll hourly",
           "--set", "to tend them::kept")
    assert r.returncode == 0, r.stderr
    active = dl(tmp_path, "active")
    assert "poll hourly" in active.stdout and "to tend them" in active.stdout
    assert "poll the lanes" not in active.stdout  # superseded parent is dead


def test_provisional_refusal_reclassifies_as_agent_originated(tmp_path: Path) -> None:
    dl(tmp_path, "provisional", "--scope", "run", "--text", "ship it tonight",
       "--cite", "transcript.jsonl:42:uuid-1:deadbeef")
    r = dl(tmp_path, "ratify", "P-0001", "--refuse")
    assert r.returncode == 0, r.stderr
    assert "[AGENT]-originated" in r.stdout and "keep/revert" in r.stdout
    active = dl(tmp_path, "active")
    assert "PROVISIONAL" not in active.stdout  # settled provisionals leave the list


# --------------------------------------------------------------------------
# Control 3 substrate — receipt chain
# --------------------------------------------------------------------------


def test_receipt_chains_and_propagates_exit(tmp_path: Path) -> None:
    env = {"HARNESS_DIR": str(tmp_path / ".harness")}
    ok = run(["bash", str(HARNESS / "receipt.sh"), "true"], tmp_path, env)
    assert ok.returncode == 0 and ok.stdout.startswith("R-")
    bad = run(["bash", str(HARNESS / "receipt.sh"), "false"], tmp_path, env)
    assert bad.returncode == 1  # a receipt records failure; it never masks it
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert [c["exit"] for c in chain] == [0, 1]
    assert chain[1]["prev_hash"] == chain[0]["hash"]
    body = {k: v for k, v in chain[1].items() if k != "hash"}
    derived = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert derived == chain[1]["hash"]


# --------------------------------------------------------------------------
# Control 5 — tripwire / HALT, and lane_env honoring it
# --------------------------------------------------------------------------


def fake_aws_key() -> str:
    return "AKIA" + "ABCDEFGHIJKLMNOP"  # constructed, never a literal credential


def test_tripwire_halts_on_credential_shape(tmp_path: Path) -> None:
    scan = tmp_path / "transcript.log"
    scan.write_text(f"harmless line\nexport AWS_KEY={fake_aws_key()}\n")
    env = {"HARNESS_DIR": str(tmp_path / ".harness")}
    r = run(["bash", str(HARNESS / "tripwire.sh"), str(scan)], tmp_path, env)
    assert r.returncode == 2
    assert "Credential exposure" in r.stdout and "only item" in r.stdout
    halt = tmp_path / ".harness" / "HALT"
    assert halt.exists() and "INCIDENT" in halt.read_text()


def test_tripwire_clean_paths_pass(tmp_path: Path) -> None:
    scan = tmp_path / "clean.log"
    scan.write_text("nothing to see\n")
    env = {"HARNESS_DIR": str(tmp_path / ".harness")}
    r = run(["bash", str(HARNESS / "tripwire.sh"), str(scan)], tmp_path, env)
    assert r.returncode == 0 and "clean" in r.stdout
    assert not (tmp_path / ".harness" / "HALT").exists()


def lane_env_setup(tmp: Path, grounded: bool = True, halt: bool = False) -> dict[str, str]:
    h = tmp / ".harness"
    h.mkdir(exist_ok=True)
    if grounded:
        (h / "grounded").write_text("2026-08-09T00:00:00Z\n")
    if halt:
        (h / "HALT").write_text("INCIDENT test\n")
    secrets = tmp / "secrets"
    secrets.mkdir(exist_ok=True)
    (secrets / "LANE_TOKEN").write_text("tok-123")
    manifest = tmp / "manifest"
    manifest.write_text("# comment\nLANE_TOKEN\n")
    return {"HARNESS_DIR": str(h), "HARNESS_SECRETS": str(secrets)}


def test_lane_env_refuses_during_halt(tmp_path: Path) -> None:
    env = lane_env_setup(tmp_path, halt=True)
    r = run(["bash", str(HARNESS / "lane_env.sh"), str(tmp_path / "manifest"), "--", "true"],
            tmp_path, env)
    assert r.returncode == 75 and "HALT" in r.stderr


def test_lane_env_refuses_without_grounding(tmp_path: Path) -> None:
    env = lane_env_setup(tmp_path, grounded=False)
    r = run(["bash", str(HARNESS / "lane_env.sh"), str(tmp_path / "manifest"), "--", "true"],
            tmp_path, env)
    assert r.returncode == 76 and "not grounded" in r.stderr


def test_lane_env_refuses_missing_secret(tmp_path: Path) -> None:
    env = lane_env_setup(tmp_path)
    (tmp_path / "manifest").write_text("MISSING_SECRET\n")
    r = run(["bash", str(HARNESS / "lane_env.sh"), str(tmp_path / "manifest"), "--", "true"],
            tmp_path, env)
    assert r.returncode == 78 and "missing secret" in r.stderr


def test_lane_env_environment_is_the_grant(tmp_path: Path) -> None:
    env = lane_env_setup(tmp_path)
    env["LEAKED_PROFILE_KEY"] = "should-never-cross"
    r = run(["bash", str(HARNESS / "lane_env.sh"), str(tmp_path / "manifest"), "--", "env"],
            tmp_path, env)
    assert r.returncode == 0, r.stderr
    keys = {line.split("=", 1)[0] for line in r.stdout.splitlines() if "=" in line}
    assert "LANE_TOKEN" in keys
    assert "LEAKED_PROFILE_KEY" not in keys  # env -i: absence beats instruction
    assert "HARNESS_SECRETS" not in keys


# --------------------------------------------------------------------------
# Control 6 — schedule registry audit
# --------------------------------------------------------------------------


def test_sched_audit_flags_unregistered_timer(tmp_path: Path) -> None:
    fixture = tmp_path / "timers.txt"
    fixture.write_text("com.evil.agent-cron\n")
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "schedule.registry").write_text("^com\\.approved\\.\n")
    r = run(["bash", str(HARNESS / "sched_audit.sh")], tmp_path,
            {"SCHED_AUDIT_INPUT": str(fixture), "HARNESS_DIR": str(tmp_path / ".harness")})
    assert r.returncode == 3
    assert "UNREGISTERED: com.evil.agent-cron" in r.stdout
    assert "agents do not own timers" in r.stdout


def test_sched_audit_passes_registered_timers(tmp_path: Path) -> None:
    fixture = tmp_path / "timers.txt"
    fixture.write_text("com.approved.backup\n")
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "schedule.registry").write_text("^com\\.approved\\.\n")
    r = run(["bash", str(HARNESS / "sched_audit.sh")], tmp_path,
            {"SCHED_AUDIT_INPUT": str(fixture), "HARNESS_DIR": str(tmp_path / ".harness")})
    assert r.returncode == 0 and "cadence clean" in r.stdout


# --------------------------------------------------------------------------
# Controls 7 + 9 — grounding and environment reconciliation
# --------------------------------------------------------------------------


def ground_fixture(tmp: Path) -> dict[str, str]:
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    (tmp / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
        cwd=tmp, check=True,
    )
    empty = tmp / "no-timers.txt"
    empty.write_text("")
    return {
        "DIRECTIVE_LEDGER": str(tmp / "DIRECTIVES" / "ledger.jsonl"),
        "SCHED_AUDIT_INPUT": str(empty),
        "HARNESS_DIR": ".harness",
    }


def test_ground_writes_marker_on_clean_state(tmp_path: Path) -> None:
    env = ground_fixture(tmp_path)
    r = run(["bash", str(HARNESS / "ground.sh")], tmp_path, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / ".harness" / "grounded").exists()
    assert "grounded @" in r.stdout


def test_ground_blocks_on_reconciler_drift(tmp_path: Path) -> None:
    env = ground_fixture(tmp_path)
    rec = tmp_path / ".harness" / "reconcile.d"
    rec.mkdir(parents=True)
    probe = rec / "iam-drift"
    probe.write_text("#!/bin/sh\necho declared != live\nexit 1\n")
    probe.chmod(probe.stat().st_mode | stat.S_IXUSR)
    r = run(["bash", str(HARNESS / "ground.sh")], tmp_path, env)
    assert r.returncode == 5
    assert "declared/live drift" in r.stdout
    assert not (tmp_path / ".harness" / "grounded").exists()  # drift blocks the marker


# --------------------------------------------------------------------------
# tmux layer — injection topology and dispatch gate
# --------------------------------------------------------------------------


def inject(
    tmp: Path, to: str, msg: str, frm: str | None = None, results: bool = False
) -> subprocess.CompletedProcess[str]:
    args = ["bash", str(HARNESS / "inject.sh"), "testrun", to]
    if results:
        args.append("--results")
    args.append(msg)
    env = {"INJECT_DRY_RUN": "1", "HARNESS_DIR": str(tmp / ".harness")}
    if frm:
        env["INJECT_FROM"] = frm
    return run(args, tmp, env)


def test_inject_orchestrator_to_lane_is_refused(tmp_path: Path) -> None:
    r = inject(tmp_path, "coder", "do it differently", frm="orchestrator")
    assert r.returncode == 77 and "topology refusal" in r.stderr


def test_inject_validator_to_lane_is_receipted(tmp_path: Path) -> None:
    r = inject(tmp_path, "coder", "spec question answered: see artifact digest")
    assert r.returncode == 0, r.stderr
    receipts = read_chain(tmp_path / ".harness" / "runs" / "testrun" / "injections.jsonl")
    assert receipts and receipts[0]["to"] == "coder" and receipts[0]["from"] == "validator"


def test_inject_verdict_filter_blocks_test_detail(tmp_path: Path) -> None:
    r = inject(tmp_path, "coder", "FAIL test_foo raised AssertionError on line 12",
               results=True)
    assert r.returncode == 79 and "bare pass/fail only" in r.stderr
    ok = inject(tmp_path, "coder", "FAIL", results=True)
    assert ok.returncode == 0, ok.stderr


def test_dispatch_refuses_without_authority_tuple(tmp_path: Path) -> None:
    (tmp_path / ".harness" / "runs" / "r1").mkdir(parents=True)
    dispatch = tmp_path / "d.md"
    dispatch.write_text("interpretation_confirmed: true\n")
    r = run(["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder",
             "--dispatch", str(dispatch)], tmp_path, {"HARNESS_DIR": str(tmp_path / ".harness")})
    assert r.returncode == 70 and "no oracle yet" in r.stderr


def test_dispatch_refuses_unconfirmed_interpretation(tmp_path: Path) -> None:
    root = tmp_path / ".harness" / "runs" / "r2"
    (root / "artifacts").mkdir(parents=True)
    (root / "run.json").write_text(json.dumps({"repo": str(tmp_path), "base_sha": "x"}))
    for name in ("product-specification.md", "architecture.md"):
        (root / "artifacts" / name).write_text("content\n")
        (root / "artifacts" / f"{name}.digest").write_text("digest\n")
    dispatch = tmp_path / "d.md"
    dispatch.write_text("requirement: build the thing\n")  # restatement gate missing
    r = run(["bash", str(HARNESS / "dispatch_lane.sh"), "r2", "coder",
             "--dispatch", str(dispatch)], tmp_path, {"HARNESS_DIR": str(tmp_path / ".harness")})
    assert r.returncode == 70 and "interpretation_confirmed" in r.stderr


# --------------------------------------------------------------------------
# Projections — asymmetric views, no ancestry
# --------------------------------------------------------------------------


def projection_fixture(tmp: Path) -> Path:
    src = tmp / "src"
    src.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=src, check=True)
    (src / "impl.py").write_text("def f() -> int:\n    return 1\n")
    tests_dir = src / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_impl.py").write_text("def test_f() -> None:\n    assert True\n")
    subprocess.run(["git", "add", "."], cwd=src, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm",
         "SECRET-CONTEXT: implements f by returning 1"],
        cwd=src, check=True,
    )
    return src


def test_coder_projection_excludes_declared_paths_and_history(tmp_path: Path) -> None:
    src = projection_fixture(tmp_path)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, capture_output=True,
                         text=True, check=True).stdout.strip()
    conf = tmp_path / "projection.conf"
    conf.write_text("coder-exclude: tests\n")
    dest = tmp_path / "ws-coder"
    r = run(["bash", str(HARNESS / "projection.sh"), "coder", str(src), sha, str(dest)],
            tmp_path, {"HARNESS_PROJECTION_CONF": str(conf)})
    assert r.returncode == 0, r.stderr
    assert (dest / "impl.py").exists()
    assert not (dest / "tests").exists()
    log = subprocess.run(["git", "log", "--all", "--format=%s"], cwd=dest,
                         capture_output=True, text=True).stdout
    assert "SECRET-CONTEXT" not in log  # upstream commit messages never cross


def test_tester_projection_refuses_undeclared_view(tmp_path: Path) -> None:
    src = projection_fixture(tmp_path)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, capture_output=True,
                         text=True, check=True).stdout.strip()
    r = run(["bash", str(HARNESS / "projection.sh"), "tester", str(src), sha,
             str(tmp_path / "ws-tester")], tmp_path,
            {"HARNESS_PROJECTION_CONF": str(tmp_path / "nonexistent.conf")})
    assert r.returncode == 66 and "contamination vector" in r.stderr


def test_tester_projection_is_interface_only(tmp_path: Path) -> None:
    src = projection_fixture(tmp_path)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, capture_output=True,
                         text=True, check=True).stdout.strip()
    conf = tmp_path / "projection.conf"
    conf.write_text("tester-include: impl.py\n")
    dest = tmp_path / "ws-tester"
    r = run(["bash", str(HARNESS / "projection.sh"), "tester", str(src), sha, str(dest)],
            tmp_path, {"HARNESS_PROJECTION_CONF": str(conf)})
    assert r.returncode == 0, r.stderr
    files = {p.name for p in dest.iterdir() if p.name != ".git"}
    assert files == {"impl.py"}
    log = subprocess.run(["git", "log", "--all", "--format=%s"], cwd=dest,
                         capture_output=True, text=True).stdout
    assert "SECRET-CONTEXT" not in log


# --------------------------------------------------------------------------
# Proof-of-done — declared environment, receipted evidence
# --------------------------------------------------------------------------


def test_proof_refuses_without_declared_target(tmp_path: Path) -> None:
    (tmp_path / ".harness" / "runs" / "p1").mkdir(parents=True)
    r = run(["bash", str(HARNESS / "proof.sh"), "p1"], tmp_path,
            {"HARNESS_DIR": str(tmp_path / ".harness")})
    assert r.returncode == 64
    assert "declared gap, not a pass" in r.stderr


def test_proof_provisions_probes_and_receipts(tmp_path: Path) -> None:
    h = tmp_path / ".harness"
    (h / "runs" / "p2").mkdir(parents=True)
    (h / "target.conf").write_text(
        "provision: echo up > \"$PROOF_DIR/provisioned.txt\"\n"
        "probe: health:: echo healthy\n"
        "probe: broken:: false\n"
        "teardown: echo down\n"
        "access: docs/access.md\n"
    )
    r = run(["bash", str(HARNESS / "proof.sh"), "p2"], tmp_path, {"HARNESS_DIR": str(h)})
    assert r.returncode == 1  # one probe RED -> proof RED, teardown still ran
    proof = h / "runs" / "p2" / "proof"
    assert (proof / "provisioned.txt").read_text().strip() == "up"
    assert (proof / "health.out").read_text().strip() == "healthy"
    summary = json.loads((proof / "summary.json").read_text())
    assert summary["verdict"] == "RED" and summary["access"] == "docs/access.md"
    chain = read_chain(h / "receipts" / "chain.jsonl")
    assert len(chain) == 4  # provision + 2 probes + teardown, all receipted
    assert "teardown" not in r.stderr  # teardown ran cleanly after the failure


def test_proof_green_when_all_probes_pass(tmp_path: Path) -> None:
    h = tmp_path / ".harness"
    (h / "runs" / "p3").mkdir(parents=True)
    (h / "target.conf").write_text(
        "provision: true\nprobe: ok:: echo fine\nteardown: true\n"
    )
    r = run(["bash", str(HARNESS / "proof.sh"), "p3"], tmp_path, {"HARNESS_DIR": str(h)})
    assert r.returncode == 0
    summary = json.loads((h / "runs" / "p3" / "proof" / "summary.json").read_text())
    assert summary["verdict"] == "GREEN" and summary["evidence"] == ["ok.out"]


# --------------------------------------------------------------------------
# Validator failure-mode detectors (deterministic layer of the Orchestrator seat)
# --------------------------------------------------------------------------


def load_dispatcher() -> object:
    import importlib.util

    spec = importlib.util.spec_from_file_location("dispatcher", HARNESS / "dispatcher.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_promise_detection_catches_announced_intent() -> None:
    mod = load_dispatcher()
    text = "Reviewing now. I'll open the PR and merge it next.\nAlso going to update docs."
    promises = mod.detect_promises(text)  # type: ignore[attr-defined]
    assert len(promises) == 2
    assert any("open the PR" in p for p in promises)


def test_promise_detection_ignores_plain_statements() -> None:
    mod = load_dispatcher()
    assert mod.detect_promises("The tests passed. Receipts are chained.") == []  # type: ignore[attr-defined]


def test_authority_claim_detection() -> None:
    mod = load_dispatcher()
    text = "Proceeding because the founder said this was in scope, per [D-0042]."
    claims = mod.detect_authority_claims(text)  # type: ignore[attr-defined]
    assert any("founder said" in c for c in claims)
    assert any("[D-0042]" in c for c in claims)
    assert mod.detect_authority_claims("we chose sqlite for simplicity") == []  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# Postmortem — numbers derive or say so
# --------------------------------------------------------------------------


def test_postmortem_refuses_to_invent(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    (root / "run.json").write_text(json.dumps(
        {"run": "r", "repo": str(tmp_path), "base_sha": "abc", "task_digest": "d",
         "budget_usd": None, "status": "open", "created_at": "2026-08-09T00:00:00+00:00"}))
    r = run(["python3", str(HARNESS / "postmortem.py"), "--root", str(root)], tmp_path)
    assert r.returncode == 0, r.stderr
    text = (root / "postmortem.md").read_text()
    assert "UNDERIVED (endgame never ran)" in text
    assert "UNCOLLECTED" in text  # feedback is collected, never invented


@pytest.mark.parametrize("script", sorted(HARNESS.glob("*.sh")))
def test_scripts_are_executable_and_parse(script: Path) -> None:
    assert os.access(script, os.X_OK), f"not executable: {script.name}"
    r = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert r.returncode == 0, f"{script.name}: {r.stderr}"
