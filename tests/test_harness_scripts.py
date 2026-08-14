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
    # The tripwire now defaults ON (an unset TRANSCRIPTS silently disabled the only
    # credential check in the harness). These drills must stay hermetic, so point it
    # at an empty sandbox rather than the developer's real transcripts — otherwise
    # ground.sh correctly STOPs on whatever it finds there and the drill measures the
    # machine instead of the script.
    scratch_transcripts = tmp / "transcripts"
    scratch_transcripts.mkdir()
    return {
        "TRANSCRIPTS": str(scratch_transcripts),
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
# Genericity — the target is data; the factory checkout is never the implicit root
# --------------------------------------------------------------------------


def test_factory_refuses_a_non_git_target(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "plain-dir"
    not_a_repo.mkdir()
    r = run(["bash", str(HARNESS / "factory.sh"), "runx", "some task",
             "--repo", str(not_a_repo)], tmp_path, {})
    assert r.returncode == 64
    assert "not a git repository" in r.stderr and "target" in r.stderr


def test_factory_refuses_a_missing_target(tmp_path: Path) -> None:
    r = run(["bash", str(HARNESS / "factory.sh"), "runx", "some task",
             "--repo", str(tmp_path / "nope")], tmp_path, {})
    assert r.returncode == 64 and "does not exist" in r.stderr


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


# --------------------------------------------------------------------------
# Phase-1 adequacy gate — existence is not adequacy
#
# Run v8 launched with all four Phase-A artifacts present and signed, then took
# six amendments authored WHILE the lanes coded, one retracting the one before
# it. dispatch_lane.sh could not have caught that: it only asks whether the files
# are there. These drills watch the adequacy gate fire on each axis it measures.
# --------------------------------------------------------------------------

ADEQUATE_SPEC = """# Product Specification

- **R1.1** Config resolution MUST expose a documented way to bind an explicit
  root without relying on process-start environment.

- **R2.1** Observed weight MUST equal the closed form for every run schedule.
"""

ADEQUATE_STRAT = """# Testing Strategy
R1.1 and R2.1 each get a control. For each, reachability of the code path is
demonstrated and the assertion is shown to discriminate met from unmet.
"""


def mkrun(tmp: Path, spec: str, strat: str | None, contract: bool = True) -> Path:
    art = tmp / ".harness" / "runs" / "r1" / "artifacts"
    art.mkdir(parents=True)
    (art / "product-specification.md").write_text(spec)
    if strat is not None:
        (art / "testing-strategy.md").write_text(strat)
    if contract:
        (art / "oracle-contract.md").write_text("signatures, shapes, marker locations\n")
    return tmp


def p1(tmp: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return run(["bash", str(HARNESS / "phase1_gate.sh"), "r1", "--repo", str(tmp)],
               cwd=tmp, env_extra=env or None)


def test_phase1_gate_passes_on_adequate_artifacts(tmp_path: Path) -> None:
    r = p1(mkrun(tmp_path, ADEQUATE_SPEC, ADEQUATE_STRAT))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "phase1 gate: clean" in r.stdout


def test_phase1_gate_refuses_requirement_that_names_its_oracle(tmp_path: Path) -> None:
    """v8's original R6.1 named a test function inside the signed spec; Amendment 2
    recorded it as a defect in the SPECIFICATION, not in either lane."""
    spec = ADEQUATE_SPEC + (
        "\n- **R6.1** The skipped test `test_r2_5_mcp_store_open_failure` in "
        "tests/test_batch0_degrade.py MUST be unskipped and pass.\n"
    )
    r = p1(mkrun(tmp_path, spec, ADEQUATE_STRAT + "\nR6.1 covered.\n"))
    assert r.returncode == 71
    assert "a requirement names its oracle" in r.stdout


def test_phase1_gate_refuses_requirement_absent_from_strategy(tmp_path: Path) -> None:
    r = p1(mkrun(tmp_path, ADEQUATE_SPEC, "# Testing Strategy\nR1.1 only. Reachability shown.\n"))
    assert r.returncode == 71
    assert "R2.1" in r.stdout and "absent from the testing strategy" in r.stdout


def test_phase1_gate_refuses_strategy_without_non_vacuity_method(tmp_path: Path) -> None:
    """batch0 shipped a vacuous oracle on its headline requirement and every gate
    in that run stayed green."""
    r = p1(mkrun(tmp_path, ADEQUATE_SPEC, "# Testing Strategy\nR1.1 and R2.1 are covered.\n"))
    assert r.returncode == 71
    assert "non-vacuous" in r.stdout


def test_phase1_gate_refuses_missing_oracle_contract(tmp_path: Path) -> None:
    r = p1(mkrun(tmp_path, ADEQUATE_SPEC, ADEQUATE_STRAT, contract=False))
    assert r.returncode == 71
    assert "oracle-contract.md" in r.stdout


def test_phase1_gate_override_is_receipted_not_silent(tmp_path: Path) -> None:
    """An override nobody can see becomes the habit that turns a gate into theater."""
    tmp = mkrun(tmp_path, ADEQUATE_SPEC, ADEQUATE_STRAT, contract=False)
    r = p1(tmp, PHASE1_ALLOW_GAPS="1")
    assert r.returncode == 0
    events = tmp / ".harness" / "runs" / "r1" / "events.jsonl"
    rec = read_chain(events)[-1]
    assert rec["gate"] == "phase1" and rec["override"] is True and rec["failures"] == 1


# --------------------------------------------------------------------------
# Projection receipt — reachability, not existence
# --------------------------------------------------------------------------


def mkproj(tmp: Path, *includes: str) -> Path:
    (tmp / ".harness").mkdir(parents=True, exist_ok=True)
    (tmp / ".harness" / "projection.conf").write_text(
        "".join(f"tester-include: {i}\n" for i in includes))
    return tmp


def pr(tmp: Path, role: str, art: Path) -> subprocess.CompletedProcess[str]:
    return run(["bash", str(HARNESS / "projection_receipt.sh"), role, str(art)], cwd=tmp)


def test_projection_receipt_passes_when_every_path_is_reachable(tmp_path: Path) -> None:
    mkproj(tmp_path, "tests", "pyproject.toml")
    art = tmp_path / "s.md"
    art.write_text("Cases land in tests/test_seam.py; metadata from pyproject.toml.\n")
    r = pr(tmp_path, "tester", art)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "inside the declared view" in r.stdout


def test_projection_receipt_refuses_unreachable_source_paths(tmp_path: Path) -> None:
    mkproj(tmp_path, "tests", "pyproject.toml")
    art = tmp_path / "s.md"
    art.write_text("The oracle imports src/pkg/config.py and compares src/pkg/store.py.\n")
    r = pr(tmp_path, "tester", art)
    assert r.returncode == 67
    assert "src/pkg/config.py" in r.stdout and "src/pkg/store.py" in r.stdout


def test_projection_receipt_does_not_flag_not_yet_written_tests(tmp_path: Path) -> None:
    """The whole point: a test the lane is ABOUT to write does not exist yet, and
    checking existence instead of reachability would refuse every honest dispatch."""
    mkproj(tmp_path, "tests")
    art = tmp_path / "s.md"
    art.write_text("New cases land in tests/test_does_not_exist_yet.py.\n")
    r = pr(tmp_path, "tester", art)
    assert r.returncode == 0, r.stdout + r.stderr


def test_projection_receipt_does_not_gate_the_coder(tmp_path: Path) -> None:
    mkproj(tmp_path, "tests")
    art = tmp_path / "s.md"
    art.write_text("Coder reads src/pkg/config.py.\n")
    r = pr(tmp_path, "coder", art)
    assert r.returncode == 0 and "not include-listed" in r.stdout


# --------------------------------------------------------------------------
# Mutation harness — a runner that cannot tell "did not apply" from "survived"
# manufactures the very false green it exists to detect.
# --------------------------------------------------------------------------


def mkpkg(tmp: Path) -> Path:
    (tmp / "src" / "pkg").mkdir(parents=True)
    (tmp / "src" / "pkg" / "__init__.py").write_text("def guarded():\n    return 'safe'\n")
    (tmp / "tests").mkdir()
    (tmp / "tests" / "test_g.py").write_text(
        "from pkg import guarded\n\ndef test_g():\n    assert guarded() == 'safe'\n")
    return tmp


def test_mutate_reports_patch_failure_not_survival(tmp_path: Path) -> None:
    """The ad-hoc runner used mid-v8 reported SURVIVED for a patch that had died on
    an IndentationError. That is the false green, inside the instrument."""
    tree = mkpkg(tmp_path / "tree")
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert 'ANCHOR THAT DOES NOT EXIST' in s, 'anchor'\n")
    r = run(["bash", str(HARNESS / "mutate.sh"), "m", str(patch),
             "--src", str(tree), "--tests", str(tree)],
            cwd=tmp_path, env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")})
    assert r.returncode == 3, r.stdout + r.stderr
    assert "PATCH-FAILED" in r.stdout and "SURVIVED" not in r.stdout


def test_mutate_kills_a_real_mutation(tmp_path: Path) -> None:
    tree = mkpkg(tmp_path / "tree")
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'safe'\" in s, 'anchor'\n"
        "p.write_text(s.replace(\"return 'safe'\", \"return 'broken'\"))\n")
    r = run(["bash", str(HARNESS / "mutate.sh"), "m2", str(patch),
             "--src", str(tree), "--tests", str(tree)],
            cwd=tmp_path, env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "KILLED" in r.stdout


# --------------------------------------------------------------------------
# Dead-auditor detection — the control that failed through itself
#
# v8 first sent five wakes whose prompt was a stray flag; nothing detected it
# because only emptiness was checked. The repair then failed the SAME way: the
# invocation was wrapped in `|| echo "(orchestrator invocation failed)"`, which
# discarded the exit status and produced a non-empty string matching none of the
# clarify-phrases, so a failed invocation was written out as a normal response.
# Five of sixteen v8 wakes died that way with ZERO dead-wake records, across the
# whole endgame, while the check reported itself healthy.
# --------------------------------------------------------------------------


def test_dead_auditor_is_detected_when_invocation_fails(tmp_path: Path) -> None:
    root = tmp_path / ".harness" / "runs" / "r1"
    (root / "wakes").mkdir(parents=True)
    (root / "artifacts").mkdir(parents=True)
    (root / "run.json").write_text(
        json.dumps({"run": "r1", "repo": str(tmp_path), "base_sha": "abc"}))
    (root / "TASK.md").write_text("task\n")
    # PATH without any agent binary: the invocation cannot succeed.
    r = run(["bash", str(HARNESS / "orchestrator_wake.sh"), "r1", '{"kind":"drill"}'],
            cwd=tmp_path, env_extra={"PATH": "/usr/bin:/bin", "ORCH_AGENT": "claude"})
    receipts = (root / "wakes" / "receipts.jsonl").read_text()
    assert "ORCHESTRATOR_DID_NOT_RUN" in receipts, (
        "a failed invocation must be recorded as a dead wake, not written out as an audit"
    )
    assert "ORCHESTRATOR DID NOT RUN" in r.stderr


def test_mutate_reports_no_op_patch_not_survival(tmp_path: Path) -> None:
    """A patch that applies cleanly and changes nothing is NOT a survivor.

    mutate.sh shipped with this hole: GATE 3 checked the patch's exit code only, so
    a patch that returned 0 without touching a byte came back `*** SURVIVED ***` —
    the exact false green the gate exists to prevent, in the tool built to prevent
    it. The author tested the nonzero-exit variant and stopped, which is the same
    one-variant-treated-as-the-class error this harness exists to catch.
    """
    tree = mkpkg(tmp_path / "tree")
    patch = tmp_path / "noop.py"
    patch.write_text("import sys\nprint('applied, changed nothing')\nsys.exit(0)\n")
    r = run(["bash", str(HARNESS / "mutate.sh"), "n", str(patch),
             "--src", str(tree), "--tests", str(tree)],
            cwd=tmp_path, env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")})
    assert r.returncode == 3, r.stdout + r.stderr
    assert "NO-OP PATCH" in r.stdout
    assert "SURVIVED" not in r.stdout


# --------------------------------------------------------------------------
# Receipt schema — test_count is machine-derived, never agent-supplied
#
# The promotion gate reads test_count > 0 to reject "exit 0 with no tests run."
# If the agent supplied its own count it would be judging its own work, so the
# count is parsed from the command's OWN output. A non-test command yields null,
# not zero — a null is honest where a zero would let a vacuous run pass the gate.
# --------------------------------------------------------------------------


def test_receipt_machine_derives_test_count(tmp_path: Path) -> None:
    env = {"HARNESS_DIR": str(tmp_path / ".harness")}
    r = run(["bash", str(HARNESS / "receipt.sh"), "bash", "-c",
             'echo "3 passed, 1 failed, 2 errors in 0.5s"; exit 0'], tmp_path, env)
    assert r.returncode == 0, r.stderr
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    rec = chain[-1]
    assert rec["test_count"] == 6    # 3 passed + 1 failed + 2 errors
    assert rec["pass_count"] == 3


def test_receipt_null_test_count_for_non_test_command(tmp_path: Path) -> None:
    """exit 0 with no tests is not a green suite; the count is null, not zero."""
    env = {"HARNESS_DIR": str(tmp_path / ".harness")}
    run(["bash", str(HARNESS / "receipt.sh"), "true"], tmp_path, env)
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] is None
    assert chain[-1]["pass_count"] is None


def test_receipt_test_count_hash_chain_stays_intact(tmp_path: Path) -> None:
    """Adding fields to the receipt body must not break hash re-derivation: the
    chain is the tamper-evidence the whole ledger rests on."""
    env = {"HARNESS_DIR": str(tmp_path / ".harness")}
    run(["bash", str(HARNESS / "receipt.sh"), "bash", "-c",
         'echo "2 passed in 0.1s"; exit 0'], tmp_path, env)
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    rec = chain[-1]
    body = {k: v for k, v in rec.items() if k != "hash"}
    derived = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert derived == rec["hash"]


# --------------------------------------------------------------------------
# Gate M (slice 4) — diff-to-surface enumeration: machine-derived changed paths
# and a caller-supplied surface map. The agent cannot author the surface set; the
# receipt derives it from the diff. (Seam half; the core binding is slice 4 step 2.)
# --------------------------------------------------------------------------


def _git_repo_with_base(tmp: Path) -> str:
    """Init a repo, commit a base, return its SHA. receipt.sh diffs against this."""
    run(["git", "init", "-q"], tmp)
    (tmp / ".gitignore").write_text(".harness/\n.factory/\n")
    (tmp / "README").write_text("base\n")
    run(["git", "add", "-A"], tmp)
    run(["git", "-c", "user.name=T", "-c", "user.email=t@t",
         "commit", "-q", "-m", "base"], tmp)
    return run(["git", "rev-parse", "HEAD"], tmp).stdout.strip()


def test_receipt_records_changed_paths_from_diff(tmp_path: Path) -> None:
    """With a base SHA the receipt machine-derives changed_paths from the diff —
    including untracked new files a candidate build creates. The agent cannot
    declare a different set; the receipt records what the diff actually produced."""
    base = _git_repo_with_base(tmp_path)
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "mod.py").write_text("x = 1\n")
    env = {"HARNESS_DIR": str(tmp_path / ".harness"), "HARNESS_BASE_SHA": base}
    run(["bash", str(HARNESS / "receipt.sh"), "true"], tmp_path, env)
    rec = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")[-1]
    assert rec["changed_paths"] == ["src/pkg/mod.py"]
    assert rec["changed_paths_digest"] is not None
    assert rec["disturbed_surface_ids"] is None  # no surface map supplied


def test_receipt_null_changed_paths_when_no_base(tmp_path: Path) -> None:
    """Without a base SHA the receipt is not a candidate-build receipt: changed_paths
    is null, so the promotion gate runs advisory for the migration window (the
    non-breaking cutover the plan's Part 4 caveat b requires)."""
    _git_repo_with_base(tmp_path)
    (tmp_path / "extra.py").write_text("y = 2\n")
    env = {"HARNESS_DIR": str(tmp_path / ".harness")}
    run(["bash", str(HARNESS / "receipt.sh"), "true"], tmp_path, env)
    rec = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")[-1]
    assert rec["changed_paths"] is None
    assert rec["changed_paths_digest"] is None
    assert rec["disturbed_surface_ids"] is None


def test_receipt_maps_paths_to_surfaces_via_supplied_map(tmp_path: Path) -> None:
    """The surface map is caller-supplied data (data-driven, not a code import): the
    generic boundary holds. receipt.sh applies it mechanically and deterministically."""
    base = _git_repo_with_base(tmp_path)
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "mod.py").write_text("x = 1\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("# guide\n")
    surface_map = tmp_path / ".factory" / "surface_map.json"
    surface_map.parent.mkdir(parents=True)
    surface_map.write_text(json.dumps({"src/*": "api", "docs/*": "docs"}))
    env = {"HARNESS_DIR": str(tmp_path / ".harness"),
           "HARNESS_BASE_SHA": base,
           "HARNESS_SURFACE_MAP": str(surface_map)}
    run(["bash", str(HARNESS / "receipt.sh"), "true"], tmp_path, env)
    rec = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")[-1]
    assert rec["disturbed_surface_ids"] == ["api", "docs"]
    assert rec["surface_map_digest"] is not None
    assert rec["unmapped_paths"] is None


def test_receipt_reports_unmapped_paths_not_drops_them(tmp_path: Path) -> None:
    """An unmapped path is reported under unmapped_paths, not silently absorbed into
    the surface set: a path with no surface mapping is a target-config gap for the
    runtime to resolve, not a quiet permission to skip a surface."""
    base = _git_repo_with_base(tmp_path)
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "mod.py").write_text("x = 1\n")
    (tmp_path / "orphan.py").write_text("z = 3\n")
    surface_map = tmp_path / ".factory" / "surface_map.json"
    surface_map.parent.mkdir(parents=True)
    surface_map.write_text(json.dumps({"src/*": "api"}))  # no rule for orphan.py
    env = {"HARNESS_DIR": str(tmp_path / ".harness"),
           "HARNESS_BASE_SHA": base,
           "HARNESS_SURFACE_MAP": str(surface_map)}
    run(["bash", str(HARNESS / "receipt.sh"), "true"], tmp_path, env)
    rec = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")[-1]
    assert rec["disturbed_surface_ids"] == ["api"]
    assert rec["unmapped_paths"] == ["orphan.py"]


def test_receipt_changed_paths_digest_binds_the_set(tmp_path: Path) -> None:
    """changed_paths_digest is the content-address of the exact path set: two builds
    with the same changed paths produce the same digest, a different set a different
    digest. The promotion gate binds the request to this digest, so a swapped set is
    visible by its mismatch."""
    base = _git_repo_with_base(tmp_path)
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "mod.py").write_text("x = 1\n")
    env = {"HARNESS_DIR": str(tmp_path / ".harness"), "HARNESS_BASE_SHA": base}
    run(["bash", str(HARNESS / "receipt.sh"), "true"], tmp_path, env)
    rec1 = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")[-1]
    # same paths, second receipt -> same digest
    run(["bash", str(HARNESS / "receipt.sh"), "true"], tmp_path, env)
    rec2 = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")[-1]
    assert rec1["changed_paths_digest"] == rec2["changed_paths_digest"]
    # add a path -> different digest
    (tmp_path / "src" / "pkg" / "other.py").write_text("y = 2\n")
    run(["bash", str(HARNESS / "receipt.sh"), "true"], tmp_path, env)
    rec3 = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")[-1]
    assert rec3["changed_paths_digest"] != rec1["changed_paths_digest"]


# --------------------------------------------------------------------------
# Named-test mutation gate — a kill by the wrong test is a symptom, not a failure
# --------------------------------------------------------------------------


def mkpkg_two(tmp: Path) -> Path:
    (tmp / "src" / "pkg").mkdir(parents=True)
    (tmp / "src" / "pkg" / "__init__.py").write_text(
        "def guarded():\n    return 'safe'\n\ndef other():\n    return 'ok'\n")
    (tmp / "tests").mkdir()
    (tmp / "tests" / "test_g.py").write_text(
        "from pkg import guarded\n\ndef test_g():\n    assert guarded() == 'safe'\n")
    (tmp / "tests" / "test_o.py").write_text(
        "from pkg import other\n\ndef test_o():\n    assert other() == 'ok'\n")
    return tmp


def _break_guarded(patch: Path) -> None:
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'safe'\" in s, 'anchor'\n"
        "p.write_text(s.replace(\"return 'safe'\", \"return 'broken'\"))\n")


def test_mutate_named_test_rejects_symptom_kill(tmp_path: Path) -> None:
    """A mutation that breaks guarded() kills test_g, not test_o. Naming test_o
    as the oracle must reject the kill: the suite reddened, but not on the test
    the requirement names — the batch0 cadence-vs-closed-form shape, where the
    mutation 'survived' the oracle it was aimed at and was killed by a different
    one. Accepting that as KILLED certifies a guard that never watched its behavior.
    """
    tree = mkpkg_two(tmp_path / "tree")
    patch = tmp_path / "p.py"
    _break_guarded(patch)
    r = run(["bash", str(HARNESS / "mutate.sh"), "m", str(patch),
             "--src", str(tree), "--tests", str(tree),
             "--named-test", "tests/test_o.py::test_o"],
            cwd=tmp_path, env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")})
    assert r.returncode == 3, r.stdout + r.stderr
    assert "KILLED-OUTSIDE-ORACLE" in r.stdout


def test_mutate_named_test_accepts_kill_on_named_oracle(tmp_path: Path) -> None:
    """The same mutation, named against the oracle it actually kills, is KILLED."""
    tree = mkpkg_two(tmp_path / "tree")
    patch = tmp_path / "p.py"
    _break_guarded(patch)
    r = run(["bash", str(HARNESS / "mutate.sh"), "m", str(patch),
             "--src", str(tree), "--tests", str(tree),
             "--named-test", "tests/test_g.py::test_g"],
            cwd=tmp_path, env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "KILLED" in r.stdout and "OUTSIDE-ORACLE" not in r.stdout


# --------------------------------------------------------------------------
# Attention without shepherding — blocking events replace pane injection
#
# The orchestrator/dispatcher gets a lane's attention by writing a blocking event
# the lane cannot run past, not by typing prose into its pane mid-reasoning
# (shepherding contaminates; METHODOLOGY.md -22:1 with reset). A pane injection is
# also a surface that stays warm after the seat behind it is dead. These drills
# watch the closed channel stay closed and the blocking event fire instead.
# --------------------------------------------------------------------------


def test_dead_auditor_writes_blocking_event_not_injection(tmp_path: Path) -> None:
    root = tmp_path / ".harness" / "runs" / "r1"
    (root / "wakes").mkdir(parents=True)
    (root / "artifacts").mkdir(parents=True)
    (root / "run.json").write_text(
        json.dumps({"run": "r1", "repo": str(tmp_path), "base_sha": "abc"}))
    (root / "TASK.md").write_text("task\n")
    r = run(["bash", str(HARNESS / "orchestrator_wake.sh"), "r1", '{"kind":"drill"}'],
            cwd=tmp_path, env_extra={"PATH": "/usr/bin:/bin", "ORCH_AGENT": "claude"})
    blocking = root / "lanes" / "validator.blocking"
    assert blocking.exists(), "a dead wake must write a blocking event for attention"
    assert "orchestrator_dead" in blocking.read_text()
    # the shepherd channel is closed: no injection receipt is produced
    inj = root / "injections.jsonl"
    assert not inj.exists(), "orchestrator_wake must not inject into the validator pane"
    assert "ORCHESTRATOR DID NOT RUN" in r.stderr


def test_dispatcher_kills_hung_wake_past_timeout(tmp_path: Path) -> None:
    """Amend 2.5: a hung wake (poll() None forever) left every later trigger
    coalesced as 'a seat is still working' — orchestrator dead but reported
    healthy, for the whole endgame. Past the deadline the seat is hung, not
    working: kill it, record the death, spawn a fresh wake."""
    mod = load_dispatcher()
    root = tmp_path / ".harness" / "runs" / "r1"
    root.mkdir(parents=True)
    (root / "run.json").write_text(json.dumps({"run": "r1", "repo": str(tmp_path)}))
    (root / "events.jsonl").write_text("")
    d = mod.Dispatcher("r1", root, 30)  # type: ignore[attr-defined]
    os.environ["WAKE_TIMEOUT"] = "0"   # deadline already elapsed

    class _Hung:
        killed = False
        def poll(self) -> int | None: return None
        def kill(self) -> None: _Hung.killed = True
        def wait(self) -> int: return -9
    hung = _Hung()
    d._wake_proc = hung  # type: ignore[attr-defined]
    d._wake_start = 0.0  # type: ignore[attr-defined]

    class _FakeProc:
        args: tuple = ()
        def poll(self) -> int | None: return 0
        def kill(self) -> None: pass
        def wait(self) -> int: return 0
        def __enter__(self): return self
        def __exit__(self, *exc: object) -> bool: return False
        def communicate(self, input=None, timeout=None): return (b"", b"")
    orig = mod.subprocess.Popen  # type: ignore[attr-defined]
    mod.subprocess.Popen = lambda *a, **k: _FakeProc()  # type: ignore[assignment]
    try:
        d.wake_orchestrator({"kind": "test"})  # type: ignore[attr-defined]
    finally:
        mod.subprocess.Popen = orig  # type: ignore[assignment]
        del os.environ["WAKE_TIMEOUT"]
    assert _Hung.killed, "a hung wake past its deadline must be killed"
    events = [json.loads(line) for line in
              (root / "events.jsonl").read_text().splitlines() if line.strip()]
    assert any(e["kind"] == "orchestrator_dead" for e in events), (
        "killing a hung wake must record orchestrator_dead, not report it healthy")


def test_lane_env_refuses_past_blocking_event(tmp_path: Path) -> None:
    env = lane_env_setup(tmp_path)
    root = tmp_path / ".harness" / "runs" / "rA"
    (root / "lanes").mkdir(parents=True)
    (root / "lanes" / "validator.blocking").write_text(
        '{"class":"stall","evidence":"validator quiet 30m"}\n')
    env["HARNESS_RUN"] = "rA"
    env["HARNESS_LANE"] = "validator"
    r = run(["bash", str(HARNESS / "lane_env.sh"), str(tmp_path / "manifest"), "--", "true"],
            tmp_path, env)
    assert r.returncode == 81 and "blocking event pending" in r.stderr


def test_lane_env_proceeds_when_blocking_event_absent(tmp_path: Path) -> None:
    """The precondition blocks only when an event is pending; a lane with no
    blocking event starts normally, so the mechanism moves work along rather than
    wedging it."""
    env = lane_env_setup(tmp_path)
    root = tmp_path / ".harness" / "runs" / "rB"
    (root / "lanes").mkdir(parents=True)
    env["HARNESS_RUN"] = "rB"
    env["HARNESS_LANE"] = "validator"
    r = run(["bash", str(HARNESS / "lane_env.sh"), str(tmp_path / "manifest"), "--", "true"],
            tmp_path, env)
    assert r.returncode == 0, r.stderr


# --------------------------------------------------------------------------
# consume_block.sh — the off-ramp that keeps a blocking event from wedging
# --------------------------------------------------------------------------


def test_consume_block_receipts_and_clears(tmp_path: Path) -> None:
    """A blocking event gates dispatch; without a consumer that control is a
    deadlock. consume_block.sh reads each event, receipts it into events.jsonl as
    a blocking_consumed record (so clearing-without-reading is visible by its
    absence), then atomically truncates the file to release the gate."""
    root = tmp_path / ".harness" / "runs" / "rA"
    (root / "lanes").mkdir(parents=True)
    (root / "lanes" / "validator.blocking").write_text(
        '{"class":"stall","evidence":"validator quiet 30m"}\n'
        '{"class":"orchestrator_response","response":"x"}\n')
    r = run(["bash", str(HARNESS / "consume_block.sh"), "rA", "validator"], tmp_path,
            {"HARNESS_DIR": str(tmp_path / ".harness")})
    assert r.returncode == 0, r.stderr
    assert "consumed 2" in r.stdout
    assert (root / "lanes" / "validator.blocking").read_text() == ""
    events = read_chain(root / "events.jsonl")
    assert len(events) == 2 and all(e["kind"] == "blocking_consumed" for e in events)


def test_consume_block_noop_when_empty(tmp_path: Path) -> None:
    root = tmp_path / ".harness" / "runs" / "rB"
    (root / "lanes").mkdir(parents=True)
    r = run(["bash", str(HARNESS / "consume_block.sh"), "rB", "validator"], tmp_path,
            {"HARNESS_DIR": str(tmp_path / ".harness")})
    assert r.returncode == 0
    assert "no blocking event pending" in r.stderr


# --------------------------------------------------------------------------
# Dispatch blocking gate — the precondition fires at dispatch (the per-task
# production path), not just at lane start.
# --------------------------------------------------------------------------


def test_dispatch_refuses_while_blocking_event_pending(tmp_path: Path) -> None:
    """The blocking-event gate is wired into dispatch_lane.sh (the path factory.sh
    and the lanes actually call), not just lane_env.sh. A validator with an
    unconsumed attention event cannot dispatch new lane work until it consumes the
    event (harness/consume_block.sh). This is the production enforcement site."""
    root = tmp_path / ".harness" / "runs" / "r1"
    art = root / "artifacts"
    art.mkdir(parents=True)
    (art / "product-specification.md").write_text(ADEQUATE_SPEC)
    (art / "product-specification.md.digest").write_text("d1\n")
    (art / "architecture.md").write_text("arch\n")
    (art / "architecture.md.digest").write_text("d2\n")
    (art / "testing-strategy.md").write_text(ADEQUATE_STRAT)
    (art / "oracle-contract.md").write_text("contract\n")
    (root / "run.json").write_text(json.dumps({"repo": str(tmp_path), "base_sha": "x"}))
    (root / "lanes").mkdir(parents=True)
    (root / "lanes" / "validator.blocking").write_text('{"class":"orchestrator_response"}\n')
    dispatch = tmp_path / "d.md"
    dispatch.write_text("interpretation_confirmed: true\nrequirement: build it\n")
    r = run(["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder",
             "--dispatch", str(dispatch)], tmp_path, {"HARNESS_DIR": ".harness"})
    assert r.returncode == 81, r.stdout + r.stderr
    assert "blocking event pending" in r.stderr


# --------------------------------------------------------------------------
# Receipt vacuity + anchoring — the two breaks the verification skeptic found
# --------------------------------------------------------------------------


def test_receipt_emits_zero_for_vacuous_test_run(tmp_path: Path) -> None:
    """A pytest run that collected 0 tests prints 'no tests ran', not '0 passed'.
    That is a vacuous run — the exact case test_count>0 exists to reject — so it
    must emit 0, not null (null would let it through as 'not a test runner')."""
    run(["bash", str(HARNESS / "receipt.sh"), "bash", "-c",
         'echo "no tests ran in 0.00s"; exit 0'], tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")})
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 0 and chain[-1]["pass_count"] == 0


def test_receipt_ignores_stray_passed_in_non_summary_output(tmp_path: Path) -> None:
    """An unanchored regex counted '3 passed' inside a build-log line; the anchor
    to a real pytest summary line (start-of-line 'N passed') refuses it, so a
    non-test command is not misread as a 3-test run."""
    run(["bash", str(HARNESS / "receipt.sh"), "bash", "-c",
         'echo "build: 3 passed validation checks"; exit 0'], tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")})
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] is None


# --------------------------------------------------------------------------
# Named-test boundary — the prefix-collision route-around the skeptic found
# --------------------------------------------------------------------------


def test_mutate_named_test_rejects_prefix_collision(tmp_path: Path) -> None:
    """NAMED_TEST 'tests/test_g.py::test_g' must NOT match the unrelated killer
    'tests/test_g.py::test_guard'. The first cut used an unbounded substring grep
    and certified a guard that never fired on the named oracle. The boundary match
    (exact, or a '['-delimited parametrized prefix) refuses the collision."""
    tree = tmp_path / "tree"
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "src" / "pkg" / "__init__.py").write_text(
        "def guarded():\n    return 'safe'\n\ndef guarded_extra():\n    return 'extra'\n")
    (tree / "tests").mkdir()
    (tree / "tests" / "test_g.py").write_text(
        "from pkg import guarded, guarded_extra\n\n"
        "def test_g():\n    assert guarded() == 'safe'\n\n"
        "def test_guard():\n    assert guarded_extra() == 'extra'\n")
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'extra'\" in s, 'anchor'\n"
        "p.write_text(s.replace(\"return 'extra'\", \"return 'broken'\"))\n")
    r = run(["bash", str(HARNESS / "mutate.sh"), "m", str(patch),
             "--src", str(tree), "--tests", str(tree),
             "--named-test", "tests/test_g.py::test_g"],
            cwd=tmp_path, env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")})
    assert r.returncode == 3, r.stdout + r.stderr
    assert "KILLED-OUTSIDE-ORACLE" in r.stdout


def test_mutate_rejects_empty_named_test(tmp_path: Path) -> None:
    """An empty --named-test silently disabled attribution in the first cut (the
    `[ -n ]` guard skipped, so any failure was accepted). It is now rejected at
    parse time."""
    tree = mkpkg(tmp_path / "tree")
    patch = tmp_path / "p.py"
    _break_guarded(patch)
    r = run(["bash", str(HARNESS / "mutate.sh"), "m", str(patch),
             "--src", str(tree), "--tests", str(tree),
             "--named-test", ""],
            cwd=tmp_path, env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")})
    assert r.returncode == 64 and "non-empty" in r.stderr


# --------------------------------------------------------------------------
# Second-round forcing probes — the breaks the verification skeptics found in
# the first-round fixes. Each uses the skeptic's exact reproduction.
# --------------------------------------------------------------------------


def test_receipt_own_line_stray_does_not_shadow_vacuous_run(tmp_path: Path) -> None:
    """The HIGH false-acceptance: an own-line stray 'N passed' (start of line, no
    'in <duration>') matched the summary branch first and — by elif precedence —
    shadowed the vacuous-run marker, reading a vacuous run as test_count>0 and
    passing the very >0 gate it exists to reject. The 'in <digit>' trailer
    refuses the stray, so a vacuous run falls through to 0."""
    run(["bash", str(HARNESS / "receipt.sh"), "bash", "-c",
         'echo "1 passed validation check"; echo "no tests ran in 0.00s"; exit 0'],
        tmp_path, {"HARNESS_DIR": str(tmp_path / ".harness")})
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 0, chain[-1]


def test_receipt_takes_last_summary_match(tmp_path: Path) -> None:
    """pytest prints its summary at the FOOT of the output. A stray own-line
    'N passed in Xs' earlier (a build step that prints a duration) must not shadow
    the real summary later. Take the LAST match: '2 passed in 0.1s' then
    '3 passed, 1 failed in 0.5s' -> 4 tests, 3 passed."""
    run(["bash", str(HARNESS / "receipt.sh"), "bash", "-c",
         'echo "2 passed in 0.1s"; echo "3 passed, 1 failed in 0.5s"; exit 0'],
        tmp_path, {"HARNESS_DIR": str(tmp_path / ".harness")})
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 4, chain[-1]
    assert chain[-1]["pass_count"] == 3, chain[-1]


def test_consume_block_handles_non_json_line(tmp_path: Path) -> None:
    """A process with filesystem access could place a non-JSON line in
    <lane>.blocking. The old printf '%s' embedded it raw into events.jsonl,
    corrupting the ledger for every downstream reader. The python receipt parses
    each line and, on failure, embeds it as an escaped string under event_raw with
    a parse_error flag — events.jsonl stays well-formed (read_chain did not throw)
    and the JSON line after it is still consumed correctly."""
    root = tmp_path / ".harness" / "runs" / "rA"
    (root / "lanes").mkdir(parents=True)
    (root / "lanes" / "validator.blocking").write_text(
        'this is not json\n'
        '{"class":"stall","evidence":"validator quiet 30m"}\n')
    r = run(["bash", str(HARNESS / "consume_block.sh"), "rA", "validator"], tmp_path,
            {"HARNESS_DIR": str(tmp_path / ".harness")})
    assert r.returncode == 0, r.stderr
    events = read_chain(root / "events.jsonl")
    assert len(events) == 2, events
    assert events[0].get("parse_error") is True, events[0]
    assert events[0].get("event_raw") == "this is not json", events[0]
    assert events[1].get("event") == {
        "class": "stall", "evidence": "validator quiet 30m"}, events[1]


def test_postmortem_reports_silent_clears(tmp_path: Path) -> None:
    """The 'clearing-without-reading is visible by its absence' guarantee: a
    blocking_written record with no matching blocking_consumed means the .blocking
    file was rm'd/truncated without consume_block.sh — the attention signal was
    lost, not consumed. postmortem.py cross-references the two and reports it."""
    root = tmp_path / ".harness" / "runs" / "r1"
    root.mkdir(parents=True)
    (root / "run.json").write_text(json.dumps(
        {"run": "r1", "base_sha": "x", "task_digest": "d", "repo": str(tmp_path)}))
    (root / "events.jsonl").write_text(json.dumps(
        {"ts": "t1", "kind": "blocking_written", "lane": "validator",
         "event": {"class": "stall", "evidence": "validator quiet 30m"}}) + "\n")
    (tmp_path / ".harness" / "receipts").mkdir(parents=True)
    (tmp_path / ".harness" / "receipts" / "chain.jsonl").write_text("")
    r = run(["python3", str(HARNESS / "postmortem.py"), "--root", str(root)], tmp_path, {})
    assert r.returncode == 0, r.stderr
    pm = (root / "postmortem.md").read_text()
    assert "SILENT CLEARS" in pm, pm
    assert "validator" in pm


def test_postmortem_clean_when_all_consumed(tmp_path: Path) -> None:
    """When every blocking_written has a matching blocking_consumed, postmortem
    reports no silent clears — the off-ramp was used, the attention signal was
    consumed, not lost."""
    root = tmp_path / ".harness" / "runs" / "r1"
    root.mkdir(parents=True)
    (root / "run.json").write_text(json.dumps(
        {"run": "r1", "base_sha": "x", "task_digest": "d", "repo": str(tmp_path)}))
    evt = {"class": "stall", "evidence": "validator quiet 30m"}
    (root / "events.jsonl").write_text(
        json.dumps({"ts": "t1", "kind": "blocking_written", "lane": "validator",
                    "event": evt}) + "\n"
        + json.dumps({"ts": "t2", "kind": "blocking_consumed", "lane": "validator",
                      "event": evt}) + "\n")
    (tmp_path / ".harness" / "receipts").mkdir(parents=True)
    (tmp_path / ".harness" / "receipts" / "chain.jsonl").write_text("")
    r = run(["python3", str(HARNESS / "postmortem.py"), "--root", str(root)], tmp_path, {})
    assert r.returncode == 0, r.stderr
    pm = (root / "postmortem.md").read_text()
    assert "no silent clears" in pm, pm
    assert "SILENT CLEARS" not in pm


def test_mutate_named_test_attributes_file_level_collection_error(tmp_path: Path) -> None:
    """A mutation that breaks module collection produces 'ERROR tests/test_g.py - ...'
    (a FILE-level row, no '::'), not a 'FAILED <nodeid>' row. The first cut's [^ ]+
    captured the file path and the attribution found no '::' match, rejecting a
    genuine kill of the named oracle as outside-oracle. A file-level ERROR kills
    every test in that file — attribute it to the named oracle when its file matches."""
    tree = tmp_path / "tree"
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "src" / "pkg" / "__init__.py").write_text("def guarded():\n    return 'safe'\n")
    (tree / "tests").mkdir()
    (tree / "tests" / "test_g.py").write_text(
        "from pkg import guarded\n\ndef test_g():\n    assert guarded() == 'safe'\n")
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'safe'\" in s, 'anchor'\n"
        "p.write_text(s + '\\ndef(\\n')\n")  # SyntaxError -> file-level collection ERROR
    r = run(["bash", str(HARNESS / "mutate.sh"), "m", str(patch),
             "--src", str(tree), "--tests", str(tree),
             "--named-test", "tests/test_g.py::test_g"],
            cwd=tmp_path, env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "KILLED by:" in r.stdout, r.stdout


def test_mutate_named_test_preserves_spaces_in_nodeid(tmp_path: Path) -> None:
    """pytest 9 emits literal spaces in parametrize-string IDs:
    'FAILED tests/test_g.py::test_g[with space] - ...'. A [^ ]+ token truncated at
    the first space, dropping the 'space]' tail and mis-attributing the kill. awk
    extracts the full nodeid (between the marker and ' - '), preserving the space."""
    tree = tmp_path / "tree"
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "src" / "pkg" / "__init__.py").write_text(
        "def guarded(x):\n    if x == 'with space':\n        return 'WITH_SPACE'\n    return x\n")
    (tree / "tests").mkdir()
    (tree / "tests" / "test_g.py").write_text(
        "import pytest\nfrom pkg import guarded\n"
        "@pytest.mark.parametrize('x,expected', [('with space','WITH_SPACE'),('plain','plain')],"
        " ids=['with space','plain'])\n"
        "def test_g(x, expected):\n    assert guarded(x) == expected\n")
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'WITH_SPACE'\" in s, 'anchor'\n"
        "p.write_text(s.replace(\"return 'WITH_SPACE'\", \"return 'broken'\"))\n")
    r = run(["bash", str(HARNESS / "mutate.sh"), "m", str(patch),
             "--src", str(tree), "--tests", str(tree),
             "--named-test", "tests/test_g.py::test_g[with space]"],
            cwd=tmp_path, env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "KILLED by:" in r.stdout, r.stdout


def test_mutate_named_test_finds_beyond_head_cap(tmp_path: Path) -> None:
    """The first cut's `head -4` dropped the named oracle when it was the 5th+
    failing test, rejecting a genuine kill as outside-oracle. With no cap, every
    failing row is checked — the named oracle is found however far down it sits."""
    tree = tmp_path / "tree"
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "src" / "pkg" / "__init__.py").write_text("def guarded():\n    return 'safe'\n")
    (tree / "tests").mkdir()
    (tree / "tests" / "test_g.py").write_text(
        "from pkg import guarded\n"
        + "".join(f"def test_g{i}(): assert guarded() == 'safe'\n" for i in range(1, 7)))
    patch = tmp_path / "p.py"
    _break_guarded(patch)
    r = run(["bash", str(HARNESS / "mutate.sh"), "m", str(patch),
             "--src", str(tree), "--tests", str(tree),
             "--named-test", "tests/test_g.py::test_g6"],
            cwd=tmp_path, env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "KILLED by:" in r.stdout, r.stdout


def test_mutate_named_test_works_when_pytest_emits_color(tmp_path: Path) -> None:
    """pytest 9 emits ANSI color under the factory's real dispatch (a tmux pane
    sets TERM; --color=yes forces it deterministically here), so the FAILED line
    arrives as '<ESC>[31mFAILED<ESC>[0m <nodeid>...'. The ^(FAILED|ERROR) anchor
    matches nothing unless the color is stripped first — without the strip, every
    --named-test kill is silently read as outside-oracle because zero killers are
    captured. This is the deepest of the mutate breaks: the three logic fixes
    (file-level ERROR, spaces, head-cap) were all masked by it."""
    tree = mkpkg(tmp_path / "tree")
    patch = tmp_path / "p.py"
    _break_guarded(patch)
    r = run(["bash", str(HARNESS / "mutate.sh"), "m", str(patch),
             "--src", str(tree), "--tests", str(tree),
             "--named-test", "tests/test_g.py::test_g"],
            cwd=tmp_path, env_extra={"MUTATE_WORKDIR": str(tmp_path / "w"),
                                     "PYTEST_ADDOPTS": "--color=yes"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "KILLED by:" in r.stdout, r.stdout


def test_receipt_stray_with_in_phrase_does_not_inflate_count(tmp_path: Path) -> None:
    """The strictly-harder false-acceptance: a stray own-line 'N passed ... in <digit>
    <word>' (e.g. '1 passed validation in 3 checks') HAS the ' in <digit>' phrase the
    first trailer required, so the buggy '\\bin \\d' matched it and read a vacuous run
    as test_count=1 — passing the >0 gate the receipt exists to reject. The fix
    requires the trailing 's' of the pytest duration ('in 0.00s'): 'in 3 checks' has
    no 's' after the digit, so it cannot feed the count and the vacuous marker wins."""
    run(["bash", str(HARNESS / "receipt.sh"), "bash", "-c",
         'echo "1 passed validation in 3 checks"; echo "no tests ran in 0.00s"; exit 0'],
        tmp_path, {"HARNESS_DIR": str(tmp_path / ".harness")})
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 0, chain[-1]


def test_mutate_named_test_preserves_dash_space_in_nodeid(tmp_path: Path) -> None:
    """The strictly-harder nodeid case: a parametrize id containing ' - ' (e.g.
    [a - b]) makes the nodeid 'tests/test_g.py::test_g[a - b]'. The buggy awk
    sub(/ - .*$/,'') stripped at the FIRST ' - ' — INSIDE the id — yielding
    'tests/test_g.py::test_g[a' and rejecting a genuine kill of the named oracle as
    outside-oracle. The bracket-aware extractor reads the full nodeid: the '[a - b]'
    is bracket-delimited, so the ' - ' inside it is not mistaken for the pytest
    separator that follows the closing ']'."""
    tree = tmp_path / "tree"
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "src" / "pkg" / "__init__.py").write_text(
        "def guarded(x):\n    if x == 'a - b':\n        return 'DASH'\n    return x\n")
    (tree / "tests").mkdir()
    (tree / "tests" / "test_g.py").write_text(
        "import pytest\nfrom pkg import guarded\n"
        "@pytest.mark.parametrize('x,expected', [('a - b','DASH'),('plain','plain')],"
        " ids=['a - b','plain'])\n"
        "def test_g(x, expected):\n    assert guarded(x) == expected\n")
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'DASH'\" in s, 'anchor'\n"
        "p.write_text(s.replace(\"return 'DASH'\", \"return 'broken'\"))\n")
    r = run(["bash", str(HARNESS / "mutate.sh"), "m", str(patch),
             "--src", str(tree), "--tests", str(tree),
             "--named-test", "tests/test_g.py::test_g[a - b]"],
            cwd=tmp_path, env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "KILLED by:" in r.stdout, r.stdout
    assert "OUTSIDE-ORACLE" not in r.stdout, r.stdout


def test_mutate_conftest_syntax_error_does_not_survive(tmp_path: Path) -> None:
    """A mutation that breaks collection at the conftest level (a SyntaxError in
    tests/conftest.py) exits non-zero with NO 'N failed/error' summary line — pytest
    prints 'ImportError while loading conftest' and a traceback, then stops (verified:
    exit 4, zero summary lines). The grep gate alone misses it and the run falls
    through to SURVIVED, reading a suite the mutation broke as one that passed every
    test. The exit code cannot be paraphrased: a non-zero exit is a kill (GATE 2
    proved the clean tree exits 0), never a survival."""
    tree = mkpkg(tmp_path / "tree")
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'safe'\" in s, 'anchor'\n"
        "pathlib.Path(sys.argv[1]).joinpath('tests/conftest.py').write_text"
        "('def broken(:\\n    pass\\n')\n")
    r = run(["bash", str(HARNESS / "mutate.sh"), "m", str(patch),
             "--src", str(tree), "--tests", str(tree)],
            cwd=tmp_path, env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "KILLED" in r.stdout, r.stdout
    assert "SURVIVED" not in r.stdout, r.stdout
