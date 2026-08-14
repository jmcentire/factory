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
    r = dl(
        tmp_path,
        "append",
        "--scope",
        "run",
        "--text",
        "poll to tend the lanes",
        "--qualifier",
        "tend the lanes, not to produce artifacts",
    )
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
    dl(
        tmp_path,
        "append",
        "--scope",
        "run",
        "--text",
        "poll the lanes",
        "--qualifier",
        "to tend them",
    )
    r = dl(tmp_path, "supersede", "D-0001", "--scope", "run", "--text", "poll faster")
    assert r.returncode != 0
    assert "undispositioned qualifiers" in r.stderr and "to tend them" in r.stderr


def test_supersession_with_dispositions_carries_qualifiers(tmp_path: Path) -> None:
    dl(
        tmp_path,
        "append",
        "--scope",
        "run",
        "--text",
        "poll the lanes",
        "--qualifier",
        "to tend them",
    )
    r = dl(
        tmp_path,
        "supersede",
        "D-0001",
        "--scope",
        "run",
        "--text",
        "poll hourly",
        "--set",
        "to tend them::kept",
    )
    assert r.returncode == 0, r.stderr
    active = dl(tmp_path, "active")
    assert "poll hourly" in active.stdout and "to tend them" in active.stdout
    assert "poll the lanes" not in active.stdout  # superseded parent is dead


def test_provisional_refusal_reclassifies_as_agent_originated(tmp_path: Path) -> None:
    dl(
        tmp_path,
        "provisional",
        "--scope",
        "run",
        "--text",
        "ship it tonight",
        "--cite",
        "transcript.jsonl:42:uuid-1:deadbeef",
    )
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
    r = run(
        ["bash", str(HARNESS / "lane_env.sh"), str(tmp_path / "manifest"), "--", "true"],
        tmp_path,
        env,
    )
    assert r.returncode == 75 and "HALT" in r.stderr


def test_lane_env_refuses_without_grounding(tmp_path: Path) -> None:
    env = lane_env_setup(tmp_path, grounded=False)
    r = run(
        ["bash", str(HARNESS / "lane_env.sh"), str(tmp_path / "manifest"), "--", "true"],
        tmp_path,
        env,
    )
    assert r.returncode == 76 and "not grounded" in r.stderr


def test_lane_env_refuses_missing_secret(tmp_path: Path) -> None:
    env = lane_env_setup(tmp_path)
    (tmp_path / "manifest").write_text("MISSING_SECRET\n")
    r = run(
        ["bash", str(HARNESS / "lane_env.sh"), str(tmp_path / "manifest"), "--", "true"],
        tmp_path,
        env,
    )
    assert r.returncode == 78 and "missing secret" in r.stderr


def test_lane_env_environment_is_the_grant(tmp_path: Path) -> None:
    env = lane_env_setup(tmp_path)
    env["LEAKED_PROFILE_KEY"] = "should-never-cross"
    r = run(
        ["bash", str(HARNESS / "lane_env.sh"), str(tmp_path / "manifest"), "--", "env"],
        tmp_path,
        env,
    )
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
    r = run(
        ["bash", str(HARNESS / "sched_audit.sh")],
        tmp_path,
        {"SCHED_AUDIT_INPUT": str(fixture), "HARNESS_DIR": str(tmp_path / ".harness")},
    )
    assert r.returncode == 3
    assert "UNREGISTERED: com.evil.agent-cron" in r.stdout
    assert "agents do not own timers" in r.stdout


def test_sched_audit_passes_registered_timers(tmp_path: Path) -> None:
    fixture = tmp_path / "timers.txt"
    fixture.write_text("com.approved.backup\n")
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "schedule.registry").write_text("^com\\.approved\\.\n")
    r = run(
        ["bash", str(HARNESS / "sched_audit.sh")],
        tmp_path,
        {"SCHED_AUDIT_INPUT": str(fixture), "HARNESS_DIR": str(tmp_path / ".harness")},
    )
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
        cwd=tmp,
        check=True,
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
    r = inject(tmp_path, "coder", "FAIL test_foo raised AssertionError on line 12", results=True)
    assert r.returncode == 79 and "bare pass/fail only" in r.stderr
    ok = inject(tmp_path, "coder", "FAIL", results=True)
    assert ok.returncode == 0, ok.stderr


def test_dispatch_refuses_without_authority_tuple(tmp_path: Path) -> None:
    (tmp_path / ".harness" / "runs" / "r1").mkdir(parents=True)
    dispatch = tmp_path / "d.md"
    dispatch.write_text("interpretation_confirmed: true\n")
    r = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
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
    r = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r2", "coder", "--dispatch", str(dispatch)],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
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
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-qm",
            "SECRET-CONTEXT: implements f by returning 1",
        ],
        cwd=src,
        check=True,
    )
    return src


def test_coder_projection_excludes_declared_paths_and_history(tmp_path: Path) -> None:
    src = projection_fixture(tmp_path)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True, check=True
    ).stdout.strip()
    conf = tmp_path / "projection.conf"
    conf.write_text("coder-exclude: tests\n")
    dest = tmp_path / "ws-coder"
    r = run(
        ["bash", str(HARNESS / "projection.sh"), "coder", str(src), sha, str(dest)],
        tmp_path,
        {"HARNESS_PROJECTION_CONF": str(conf)},
    )
    assert r.returncode == 0, r.stderr
    assert (dest / "impl.py").exists()
    assert not (dest / "tests").exists()
    log = subprocess.run(
        ["git", "log", "--all", "--format=%s"], cwd=dest, capture_output=True, text=True
    ).stdout
    assert "SECRET-CONTEXT" not in log  # upstream commit messages never cross


def test_tester_projection_refuses_undeclared_view(tmp_path: Path) -> None:
    src = projection_fixture(tmp_path)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True, check=True
    ).stdout.strip()
    r = run(
        [
            "bash",
            str(HARNESS / "projection.sh"),
            "tester",
            str(src),
            sha,
            str(tmp_path / "ws-tester"),
        ],
        tmp_path,
        {"HARNESS_PROJECTION_CONF": str(tmp_path / "nonexistent.conf")},
    )
    assert r.returncode == 66 and "contamination vector" in r.stderr


def test_tester_projection_is_interface_only(tmp_path: Path) -> None:
    src = projection_fixture(tmp_path)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True, check=True
    ).stdout.strip()
    conf = tmp_path / "projection.conf"
    conf.write_text("tester-include: impl.py\n")
    dest = tmp_path / "ws-tester"
    r = run(
        ["bash", str(HARNESS / "projection.sh"), "tester", str(src), sha, str(dest)],
        tmp_path,
        {"HARNESS_PROJECTION_CONF": str(conf)},
    )
    assert r.returncode == 0, r.stderr
    files = {p.name for p in dest.iterdir() if p.name != ".git"}
    assert files == {"impl.py"}
    log = subprocess.run(
        ["git", "log", "--all", "--format=%s"], cwd=dest, capture_output=True, text=True
    ).stdout
    assert "SECRET-CONTEXT" not in log


# --------------------------------------------------------------------------
# Genericity — the target is data; the factory checkout is never the implicit root
# --------------------------------------------------------------------------


def test_factory_refuses_a_non_git_target(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "plain-dir"
    not_a_repo.mkdir()
    r = run(
        ["bash", str(HARNESS / "factory.sh"), "runx", "some task", "--repo", str(not_a_repo)],
        tmp_path,
        {},
    )
    assert r.returncode == 64
    assert "not a git repository" in r.stderr and "target" in r.stderr


def test_factory_refuses_a_missing_target(tmp_path: Path) -> None:
    r = run(
        [
            "bash",
            str(HARNESS / "factory.sh"),
            "runx",
            "some task",
            "--repo",
            str(tmp_path / "nope"),
        ],
        tmp_path,
        {},
    )
    assert r.returncode == 64 and "does not exist" in r.stderr


# --------------------------------------------------------------------------
# Proof-of-done — declared environment, receipted evidence
# --------------------------------------------------------------------------


def test_proof_refuses_without_declared_target(tmp_path: Path) -> None:
    (tmp_path / ".harness" / "runs" / "p1").mkdir(parents=True)
    r = run(
        ["bash", str(HARNESS / "proof.sh"), "p1"],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    assert r.returncode == 64
    assert "declared gap, not a pass" in r.stderr


def test_proof_provisions_probes_and_receipts(tmp_path: Path) -> None:
    h = tmp_path / ".harness"
    (h / "runs" / "p2").mkdir(parents=True)
    (h / "target.conf").write_text(
        'provision: echo up > "$PROOF_DIR/provisioned.txt"\n'
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
    (h / "target.conf").write_text("provision: true\nprobe: ok:: echo fine\nteardown: true\n")
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
    (root / "run.json").write_text(
        json.dumps(
            {
                "run": "r",
                "repo": str(tmp_path),
                "base_sha": "abc",
                "task_digest": "d",
                "budget_usd": None,
                "status": "open",
                "created_at": "2026-08-09T00:00:00+00:00",
            }
        )
    )
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
    art = tmp / ".factory" / "runs" / "r1" / "artifacts"
    art.mkdir(parents=True)
    (art / "product-specification.md").write_text(spec)
    if strat is not None:
        (art / "testing-strategy.md").write_text(strat)
    if contract:
        (art / "oracle-contract.md").write_text("signatures, shapes, marker locations\n")
    return tmp


def p1(tmp: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return run(
        ["bash", str(HARNESS / "phase1_gate.sh"), "r1", "--repo", str(tmp)],
        cwd=tmp,
        env_extra=env or None,
    )


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
    events = tmp / ".factory" / "runs" / "r1" / "events.jsonl"
    rec = read_chain(events)[-1]
    assert rec["gate"] == "phase1" and rec["override"] is True and rec["failures"] == 1


# --------------------------------------------------------------------------
# Projection receipt — reachability, not existence
# --------------------------------------------------------------------------


def mkproj(tmp: Path, *includes: str) -> Path:
    (tmp / ".factory").mkdir(parents=True, exist_ok=True)
    (tmp / ".factory" / "projection.conf").write_text(
        "".join(f"tester-include: {i}\n" for i in includes)
    )
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
        "from pkg import guarded\n\ndef test_g():\n    assert guarded() == 'safe'\n"
    )
    return tmp


def test_mutate_reports_patch_failure_not_survival(tmp_path: Path) -> None:
    """The ad-hoc runner used mid-v8 reported SURVIVED for a patch that had died on
    an IndentationError. That is the false green, inside the instrument."""
    tree = mkpkg(tmp_path / "tree")
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert 'ANCHOR THAT DOES NOT EXIST' in s, 'anchor'\n"
    )
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "m",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
    assert r.returncode == 3, r.stdout + r.stderr
    assert "PATCH-FAILED" in r.stdout and "SURVIVED" not in r.stdout


def test_mutate_kills_a_real_mutation(tmp_path: Path) -> None:
    tree = mkpkg(tmp_path / "tree")
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'safe'\" in s, 'anchor'\n"
        "p.write_text(s.replace(\"return 'safe'\", \"return 'broken'\"))\n"
    )
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "m2",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
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
    root = tmp_path / ".factory" / "runs" / "r1"
    (root / "wakes").mkdir(parents=True)
    (root / "artifacts").mkdir(parents=True)
    (root / "run.json").write_text(
        json.dumps({"run": "r1", "repo": str(tmp_path), "base_sha": "abc"})
    )
    (root / "TASK.md").write_text("task\n")
    # PATH without any agent binary: the invocation cannot succeed.
    r = run(
        ["bash", str(HARNESS / "orchestrator_wake.sh"), "r1", '{"kind":"drill"}'],
        cwd=tmp_path,
        env_extra={"PATH": "/usr/bin:/bin", "ORCH_AGENT": "claude"},
    )
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
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "n",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
    assert r.returncode == 3, r.stdout + r.stderr
    assert "NO-OP PATCH" in r.stdout
    assert "SURVIVED" not in r.stdout


# --------------------------------------------------------------------------
# Oracle receipt (Gate N seam) — mutate.sh machine-derives oracle adequacy
#
# A surface's oracle_adequate claim cites a receipt, not a verdict in prose. mutate.sh
# writes a kind:"oracle" entry to the same tamper-evident chain as the build receipts,
# content-addressed (hash-chained), carrying oracle_adequate = KILLED-by-the-named-oracle.
# The promotion-gate translator reads this to bind a surface's oracle claim. Three
# outcomes, each receipted honestly: a kill BY the named oracle (adequate), a survivor
# (not adequate), and a kill by a DIFFERENT test (not adequate — the named oracle did not
# catch the regression, the batch0 cadence-vs-closed-form shape).
# --------------------------------------------------------------------------


def test_mutate_writes_oracle_receipt_adequate_when_named_oracle_kills(tmp_path: Path) -> None:
    tree = mkpkg(tmp_path / "tree")
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'safe'\" in s, 'anchor'\n"
        "p.write_text(s.replace(\"return 'safe'\", \"return 'broken'\"))\n"
    )
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "m",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
            "--named-test",
            "tests/test_g.py::test_g",
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "KILLED" in r.stdout
    chain = read_chain(tmp_path / ".factory" / "receipts" / "chain.jsonl")
    oracle = [e for e in chain if e.get("kind") == "oracle"]
    assert len(oracle) == 1
    assert oracle[0]["oracle_adequate"] is True
    assert oracle[0]["outcome"] == "KILLED"
    assert oracle[0]["named_test"] == "tests/test_g.py::test_g"
    assert "hash" in oracle[0] and "prev_hash" in oracle[0]  # content-addressed


def test_mutate_writes_oracle_receipt_inadequate_when_survived(tmp_path: Path) -> None:
    tree = mkpkg(tmp_path / "tree")
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'safe'\" in s, 'anchor'\n"
        "p.write_text(s.replace(\"return 'safe'\", \"return 'safe'  # m\"))\n"
    )
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "s",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
            "--named-test",
            "tests/test_g.py::test_g",
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "SURVIVED" in r.stdout
    chain = read_chain(tmp_path / ".factory" / "receipts" / "chain.jsonl")
    oracle = [e for e in chain if e.get("kind") == "oracle"]
    assert len(oracle) == 1
    assert oracle[0]["oracle_adequate"] is False
    assert oracle[0]["outcome"] == "SURVIVED"


def test_mutate_writes_oracle_receipt_inadequate_when_killed_outside(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "src" / "pkg" / "__init__.py").write_text(
        "def guarded():\n    return 'safe'\n\ndef other():\n    return 'ok'\n"
    )
    (tree / "tests").mkdir()
    (tree / "tests" / "test_g.py").write_text(
        "from pkg import guarded, other\n"
        "def test_guarded():\n    assert guarded() == 'safe'\n"
        "def test_other():\n    assert other() == 'ok'\n"
    )
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'safe'\" in s, 'anchor'\n"
        "p.write_text(s.replace(\"return 'safe'\", \"return 'broken'\"))\n"
    )
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "o",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
            "--named-test",
            "tests/test_g.py::test_other",
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
    assert r.returncode == 3, r.stdout + r.stderr
    assert "KILLED-OUTSIDE-ORACLE" in r.stdout
    chain = read_chain(tmp_path / ".factory" / "receipts" / "chain.jsonl")
    oracle = [e for e in chain if e.get("kind") == "oracle"]
    assert len(oracle) == 1
    assert oracle[0]["oracle_adequate"] is False
    assert "KILLED-OUTSIDE-ORACLE" in str(oracle[0].get("verdict_text", ""))


# --------------------------------------------------------------------------
# Flake receipt (Gate N seam) — flake.sh machine-derives determinism
#
# A surface's `deterministic` claim cites a receipt, not a verdict in prose. flake.sh
# runs the suite N times and receipts kind:"flake" {deterministic, flake_count,
# automatic_retry_count} to the same chain. A flaky suite is a FINDING (exit 1), not a
# script failure; a red baseline is INVALID (exit 3) — flake-hunting a red baseline
# manufactures a false flake that is just the pre-existing red.
# --------------------------------------------------------------------------


def _flake_tree(tmp: Path, *, flaky: bool) -> Path:
    """A tree whose suite is deterministic, or one that toggles pass/fail across runs
    via a persistent counter (the only portable, clock-free flake: the N runs share the
    workdir, so the counter file accumulates across runs within one invocation)."""
    tree = tmp / "tree"
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "src" / "pkg" / "__init__.py").write_text("def guarded():\n    return 'safe'\n")
    (tree / "tests").mkdir()
    if flaky:
        (tree / "tests" / "test_flake.py").write_text(
            "import os\n"
            "def test_flaky():\n"
            "    p = os.path.join(os.path.dirname(__file__), '.counter')\n"
            "    n = 0\n"
            "    if os.path.exists(p):\n"
            "        n = int(open(p).read())\n"
            "    open(p, 'w').write(str(n + 1))\n"
            "    assert n % 2 == 0\n"
        )
    else:
        (tree / "tests" / "test_g.py").write_text(
            "from pkg import guarded\n\ndef test_g():\n    assert guarded() == 'safe'\n"
        )
    return tree


def test_flake_receipts_deterministic_when_all_runs_agree(tmp_path: Path) -> None:
    tree = _flake_tree(tmp_path, flaky=False)
    r = run(
        [
            "bash",
            str(HARNESS / "flake.sh"),
            "d",
            "--src",
            str(tree),
            "--tests",
            str(tree),
            "--runs",
            "3",
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "DETERMINISTIC" in r.stdout
    chain = read_chain(tmp_path / ".factory" / "receipts" / "chain.jsonl")
    flake = [e for e in chain if e.get("kind") == "flake"]
    assert len(flake) == 1
    assert flake[0]["deterministic"] is True
    assert flake[0]["flake_count"] == 0
    assert flake[0]["automatic_retry_count"] == 0
    assert flake[0]["runs"] == 3


def test_flake_receipts_flaky_when_runs_disagree(tmp_path: Path) -> None:
    tree = _flake_tree(tmp_path, flaky=True)
    r = run(
        [
            "bash",
            str(HARNESS / "flake.sh"),
            "f",
            "--src",
            str(tree),
            "--tests",
            str(tree),
            "--runs",
            "3",
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "FLAKY" in r.stdout
    chain = read_chain(tmp_path / ".factory" / "receipts" / "chain.jsonl")
    flake = [e for e in chain if e.get("kind") == "flake"]
    assert len(flake) == 1
    assert flake[0]["deterministic"] is False
    assert flake[0]["flake_count"] >= 1
    # run_exits records the mixed outcomes that prove the flake
    exits = flake[0]["run_exits"]
    assert 0 in exits and 1 in exits


def test_flake_refuses_red_baseline(tmp_path: Path) -> None:
    """A red baseline is INVALID, not a flake: flake-hunting a pre-existing red
    manufactures a 'flake' that is the same red recurring. No flake receipt is written
    (the gate exits before the receipt), so the chain carries no kind:"flake" entry."""
    tree = tmp_path / "tree"
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "src" / "pkg" / "__init__.py").write_text("def guarded():\n    return 'safe'\n")
    (tree / "tests").mkdir()
    (tree / "tests" / "test_g.py").write_text(
        "from pkg import guarded\n\ndef test_g():\n    assert guarded() == 'broken'\n"
    )
    r = run(
        [
            "bash",
            str(HARNESS / "flake.sh"),
            "r",
            "--src",
            str(tree),
            "--tests",
            str(tree),
            "--runs",
            "3",
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
    assert r.returncode == 3, r.stdout + r.stderr
    assert "INVALID" in r.stdout and "baseline is not green" in r.stdout
    chain_path = tmp_path / ".factory" / "receipts" / "chain.jsonl"
    if chain_path.exists():
        assert not [e for e in read_chain(chain_path) if e.get("kind") == "flake"]


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
    r = run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            'echo "3 passed, 1 failed, 2 errors in 0.5s"; exit 0',
        ],
        tmp_path,
        env,
    )
    assert r.returncode == 0, r.stderr
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    rec = chain[-1]
    assert rec["test_count"] == 6  # 3 passed + 1 failed + 2 errors
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
    run(
        ["bash", str(HARNESS / "receipt.sh"), "bash", "-c", 'echo "2 passed in 0.1s"; exit 0'],
        tmp_path,
        env,
    )
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
    run(["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-q", "-m", "base"], tmp)
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
    is null. This is the honest shape of a non-candidate command's receipt — the enforcement
    cutover (Gate M/N hard-block + Gate L sole-advancement) is live, so a run that disturbs
    surfaces MUST supply a candidate-build receipt; a receipt with no base is simply not one,
    and decide_promotion fail-closes on the absent binding rather than advising past it."""
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
    env = {
        "HARNESS_DIR": str(tmp_path / ".harness"),
        "HARNESS_BASE_SHA": base,
        "HARNESS_SURFACE_MAP": str(surface_map),
    }
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
    env = {
        "HARNESS_DIR": str(tmp_path / ".harness"),
        "HARNESS_BASE_SHA": base,
        "HARNESS_SURFACE_MAP": str(surface_map),
    }
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
        "def guarded():\n    return 'safe'\n\ndef other():\n    return 'ok'\n"
    )
    (tmp / "tests").mkdir()
    (tmp / "tests" / "test_g.py").write_text(
        "from pkg import guarded\n\ndef test_g():\n    assert guarded() == 'safe'\n"
    )
    (tmp / "tests" / "test_o.py").write_text(
        "from pkg import other\n\ndef test_o():\n    assert other() == 'ok'\n"
    )
    return tmp


def _break_guarded(patch: Path) -> None:
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'safe'\" in s, 'anchor'\n"
        "p.write_text(s.replace(\"return 'safe'\", \"return 'broken'\"))\n"
    )


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
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "m",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
            "--named-test",
            "tests/test_o.py::test_o",
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
    assert r.returncode == 3, r.stdout + r.stderr
    assert "KILLED-OUTSIDE-ORACLE" in r.stdout


def test_mutate_named_test_accepts_kill_on_named_oracle(tmp_path: Path) -> None:
    """The same mutation, named against the oracle it actually kills, is KILLED."""
    tree = mkpkg_two(tmp_path / "tree")
    patch = tmp_path / "p.py"
    _break_guarded(patch)
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "m",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
            "--named-test",
            "tests/test_g.py::test_g",
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
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
    root = tmp_path / ".factory" / "runs" / "r1"
    (root / "wakes").mkdir(parents=True)
    (root / "artifacts").mkdir(parents=True)
    (root / "run.json").write_text(
        json.dumps({"run": "r1", "repo": str(tmp_path), "base_sha": "abc"})
    )
    (root / "TASK.md").write_text("task\n")
    r = run(
        ["bash", str(HARNESS / "orchestrator_wake.sh"), "r1", '{"kind":"drill"}'],
        cwd=tmp_path,
        env_extra={"PATH": "/usr/bin:/bin", "ORCH_AGENT": "claude"},
    )
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
    os.environ["WAKE_TIMEOUT"] = "0"  # deadline already elapsed

    class _Hung:
        killed = False

        def poll(self) -> int | None:
            return None

        def kill(self) -> None:
            _Hung.killed = True

        def wait(self) -> int:
            return -9

    hung = _Hung()
    d._wake_proc = hung  # type: ignore[attr-defined]
    d._wake_start = 0.0  # type: ignore[attr-defined]

    class _FakeProc:
        args: tuple = ()

        def poll(self) -> int | None:
            return 0

        def kill(self) -> None:
            pass

        def wait(self) -> int:
            return 0

        def __enter__(self):
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def communicate(self, input=None, timeout=None):
            return (b"", b"")

    orig = mod.subprocess.Popen  # type: ignore[attr-defined]
    mod.subprocess.Popen = lambda *a, **k: _FakeProc()  # type: ignore[assignment]
    try:
        d.wake_orchestrator({"kind": "test"})  # type: ignore[attr-defined]
    finally:
        mod.subprocess.Popen = orig  # type: ignore[assignment]
        del os.environ["WAKE_TIMEOUT"]
    assert _Hung.killed, "a hung wake past its deadline must be killed"
    events = [
        json.loads(line)
        for line in (root / "events.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert any(e["kind"] == "orchestrator_dead" for e in events), (
        "killing a hung wake must record orchestrator_dead, not report it healthy"
    )


def test_lane_env_refuses_past_blocking_event(tmp_path: Path) -> None:
    env = lane_env_setup(tmp_path)
    root = tmp_path / ".harness" / "runs" / "rA"
    (root / "lanes").mkdir(parents=True)
    (root / "lanes" / "validator.blocking").write_text(
        '{"class":"stall","evidence":"validator quiet 30m"}\n'
    )
    env["HARNESS_RUN"] = "rA"
    env["HARNESS_LANE"] = "validator"
    r = run(
        ["bash", str(HARNESS / "lane_env.sh"), str(tmp_path / "manifest"), "--", "true"],
        tmp_path,
        env,
    )
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
    r = run(
        ["bash", str(HARNESS / "lane_env.sh"), str(tmp_path / "manifest"), "--", "true"],
        tmp_path,
        env,
    )
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
        '{"class":"orchestrator_response","response":"x"}\n'
    )
    r = run(
        ["bash", str(HARNESS / "consume_block.sh"), "rA", "validator"],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    assert r.returncode == 0, r.stderr
    assert "consumed 2" in r.stdout
    assert (root / "lanes" / "validator.blocking").read_text() == ""
    events = read_chain(root / "events.jsonl")
    assert len(events) == 2 and all(e["kind"] == "blocking_consumed" for e in events)


def test_consume_block_noop_when_empty(tmp_path: Path) -> None:
    root = tmp_path / ".harness" / "runs" / "rB"
    (root / "lanes").mkdir(parents=True)
    r = run(
        ["bash", str(HARNESS / "consume_block.sh"), "rB", "validator"],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
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
    r = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        tmp_path,
        {"HARNESS_DIR": ".harness"},
    )
    assert r.returncode == 81, r.stdout + r.stderr
    assert "blocking event pending" in r.stderr


# --------------------------------------------------------------------------
# Gate C (kindex-as-primer) + Gate B (reset-prime-deliver) — the dispatch brief
# is FENCE -> PRIMER -> TASK, and a role-specific kindex primer is a dispatch
# precondition. A gate that has never been watched firing is theater, so each
# drill watches the gate fire (refuse) and pass (deliver + structure).
# --------------------------------------------------------------------------


def dispatch_success_fixture(
    tmp_path: Path, role: str = "coder", primer: bool = True
) -> tuple[Path, Path, Path, Path]:
    """A complete, dispatchable run that passes every precondition through to the
    lane launch. The run lives under the target repo's git root (``<repo>/.factory/
    runs/<run>``) — the real factory layout, where ``dispatch_lane``'s
    ``$HARNESS_DIR``-relative ROOT and ``phase1_gate``'s ``$REPO/$HARNESS_DIR`` ROOT
    agree (both anchor at the repo root when ``HARNESS_DIR`` is the relative
    ``.factory``). Includes a real git repo (so projection works at the real SHA),
    adequate Phase-1 artifacts (so the adequacy gate is clean), no blocking event,
    a role-specific kindex primer (Gate C), and a stub ``tmux`` so the launch is a
    no-op. Returns (repo, root, dispatch_path, stub_bin_dir)."""
    src = projection_fixture(tmp_path)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True, check=True
    ).stdout.strip()
    root = src / ".factory" / "runs" / "r1"
    art = root / "artifacts"
    art.mkdir(parents=True)
    (root / "run.json").write_text(json.dumps({"repo": str(src), "base_sha": sha}))
    for name, body in (
        ("product-specification.md", ADEQUATE_SPEC),
        ("architecture.md", "# Architecture\n"),
        ("testing-strategy.md", ADEQUATE_STRAT),
    ):
        (art / name).write_text(body)
        (art / f"{name}.digest").write_text("d\n")
    (art / "oracle-contract.md").write_text("signatures, shapes, marker locations\n")
    if primer:
        (art / f"primer.{role}.md").write_text(
            "# Phase A0 primer — kindex research for this run\n"
            "constraint: never push to main without a green ship\n"
            "research: vendor doc for the touched surface\n"
        )
    dispatch = tmp_path / "d.md"
    dispatch.write_text("interpretation_confirmed: true\nrequirement: build R1.1\n")
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "tmux").write_text("#!/usr/bin/env bash\nexit 0\n")
    os.chmod(stub / "tmux", 0o755)
    return src, root, dispatch, stub


def _dispatch_env(stub: Path) -> dict[str, str]:
    # HARNESS_DIR is the relative .factory under the repo root (cwd); the stub
    # tmux shadows the real tmux so the lane launch is a no-op.
    return {"HARNESS_DIR": ".factory", "PATH": f"{stub}:{os.environ['PATH']}"}


def test_dispatch_refuses_without_kindex_primer(tmp_path: Path) -> None:
    """Gate C: a dispatch with no role-specific kindex primer is refused — the
    Validator must search kindex and capture research nodes before the lane is
    launched (closes kindex-non-use). This is the reset-prime-deliver PRIMER step
    made a precondition, not a hope."""
    src, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=False)
    r = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        src,
        _dispatch_env(stub),
    )
    assert r.returncode == 70, r.stdout + r.stderr
    assert "no kindex primer" in r.stderr and "Gate C" in r.stderr


def test_dispatch_primer_is_role_specific_not_shared(tmp_path: Path) -> None:
    """The primer is role-specific: a coder lane with only a tester primer present
    is refused. The projection boundary is enforced structurally — the coder does
    not fall back to the tester's primer (which may carry implementation detail)."""
    src, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=False)
    (root / "artifacts" / "primer.tester.md").write_text("tester-only primer\n")
    r = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        src,
        _dispatch_env(stub),
    )
    assert r.returncode == 70 and "no kindex primer" in r.stderr


def test_dispatch_breakglass_primer_gap_is_receipted(tmp_path: Path) -> None:
    """Break-glass (plan §Advocate operational requirements): a missing primer under
    GATE_BC_ALLOW_GAP=1 proceeds with a RECEIPTED gap written to events.jsonl,
    never silently. The break-glass is a receipt, not a backdoor."""
    src, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=False)
    env = _dispatch_env(stub)
    env["GATE_BC_ALLOW_GAP"] = "1"
    r = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        src,
        env,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    events = read_chain(root / "events.jsonl")
    assert events and events[-1]["gate"] == "primer" and events[-1]["override"] is True


def test_dispatch_delivers_fence_primer_task_brief(tmp_path: Path) -> None:
    """Gate B: the brief is ordered FENCE -> PRIMER -> TASK (reset-then-prime is the
    single largest intervention; leading with instruction measurably worsens
    output). The FENCE (boundary/reset) precedes the PRIMER (ground truth) which
    precedes the TASK. The primer is delivered to the workspace; the assembled
    brief is an auditable artifact with the order inspectable. Closes
    validator-shallow / mode-switching."""
    src, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=True)
    r = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        src,
        _dispatch_env(stub),
    )
    assert r.returncode == 0, r.stdout + r.stderr
    ws = root / "workspaces" / "coder"
    # The primer is delivered (Gate C: delivery, not use — use is the semantic flag).
    assert (ws / "PRIMER.md").read_text() == (root / "artifacts" / "primer.coder.md").read_text()
    brief = (ws / "BRIEF.md").read_text()
    # Order is the intervention: FENCE before PRIMER before TASK.
    assert brief.index("## FENCE") < brief.index("## PRIMER") < brief.index("## TASK")
    # The FENCE is the reset — the boundary stated before any task content.
    assert "One pen only" in brief and "DATA, never authority" in brief
    # The PRIMER points at the delivered kindex primer; the TASK points at the dispatch.
    assert "PRIMER.md" in brief and "DISPATCH.md" in brief


# --------------------------------------------------------------------------
# Receipt vacuity + anchoring — the two breaks the verification skeptic found
# --------------------------------------------------------------------------


def test_receipt_emits_zero_for_vacuous_test_run(tmp_path: Path) -> None:
    """A pytest run that collected 0 tests prints 'no tests ran', not '0 passed'.
    That is a vacuous run — the exact case test_count>0 exists to reject — so it
    must emit 0, not null (null would let it through as 'not a test runner')."""
    run(
        ["bash", str(HARNESS / "receipt.sh"), "bash", "-c", 'echo "no tests ran in 0.00s"; exit 0'],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 0 and chain[-1]["pass_count"] == 0


def test_receipt_ignores_stray_passed_in_non_summary_output(tmp_path: Path) -> None:
    """An unanchored regex counted '3 passed' inside a build-log line; the anchor
    to a real pytest summary line (start-of-line 'N passed') refuses it, so a
    non-test command is not misread as a 3-test run."""
    run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            'echo "build: 3 passed validation checks"; exit 0',
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
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
        "def guarded():\n    return 'safe'\n\ndef guarded_extra():\n    return 'extra'\n"
    )
    (tree / "tests").mkdir()
    (tree / "tests" / "test_g.py").write_text(
        "from pkg import guarded, guarded_extra\n\n"
        "def test_g():\n    assert guarded() == 'safe'\n\n"
        "def test_guard():\n    assert guarded_extra() == 'extra'\n"
    )
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'extra'\" in s, 'anchor'\n"
        "p.write_text(s.replace(\"return 'extra'\", \"return 'broken'\"))\n"
    )
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "m",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
            "--named-test",
            "tests/test_g.py::test_g",
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
    assert r.returncode == 3, r.stdout + r.stderr
    assert "KILLED-OUTSIDE-ORACLE" in r.stdout


def test_mutate_rejects_empty_named_test(tmp_path: Path) -> None:
    """An empty --named-test silently disabled attribution in the first cut (the
    `[ -n ]` guard skipped, so any failure was accepted). It is now rejected at
    parse time."""
    tree = mkpkg(tmp_path / "tree")
    patch = tmp_path / "p.py"
    _break_guarded(patch)
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "m",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
            "--named-test",
            "",
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
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
    run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            'echo "1 passed validation check"; echo "no tests ran in 0.00s"; exit 0',
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 0, chain[-1]


def test_receipt_takes_last_summary_match(tmp_path: Path) -> None:
    """pytest prints its summary at the FOOT of the output. A stray own-line
    'N passed in Xs' earlier (a build step that prints a duration) must not shadow
    the real summary later. Take the LAST match: '2 passed in 0.1s' then
    '3 passed, 1 failed in 0.5s' -> 4 tests, 3 passed."""
    run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            'echo "2 passed in 0.1s"; echo "3 passed, 1 failed in 0.5s"; exit 0',
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
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
        'this is not json\n{"class":"stall","evidence":"validator quiet 30m"}\n'
    )
    r = run(
        ["bash", str(HARNESS / "consume_block.sh"), "rA", "validator"],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    assert r.returncode == 0, r.stderr
    events = read_chain(root / "events.jsonl")
    assert len(events) == 2, events
    assert events[0].get("parse_error") is True, events[0]
    assert events[0].get("event_raw") == "this is not json", events[0]
    assert events[1].get("event") == {"class": "stall", "evidence": "validator quiet 30m"}, events[
        1
    ]


def test_postmortem_reports_silent_clears(tmp_path: Path) -> None:
    """The 'clearing-without-reading is visible by its absence' guarantee: a
    blocking_written record with no matching blocking_consumed means the .blocking
    file was rm'd/truncated without consume_block.sh — the attention signal was
    lost, not consumed. postmortem.py cross-references the two and reports it."""
    root = tmp_path / ".harness" / "runs" / "r1"
    root.mkdir(parents=True)
    (root / "run.json").write_text(
        json.dumps({"run": "r1", "base_sha": "x", "task_digest": "d", "repo": str(tmp_path)})
    )
    (root / "events.jsonl").write_text(
        json.dumps(
            {
                "ts": "t1",
                "kind": "blocking_written",
                "lane": "validator",
                "event": {"class": "stall", "evidence": "validator quiet 30m"},
            }
        )
        + "\n"
    )
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
    (root / "run.json").write_text(
        json.dumps({"run": "r1", "base_sha": "x", "task_digest": "d", "repo": str(tmp_path)})
    )
    evt = {"class": "stall", "evidence": "validator quiet 30m"}
    (root / "events.jsonl").write_text(
        json.dumps({"ts": "t1", "kind": "blocking_written", "lane": "validator", "event": evt})
        + "\n"
        + json.dumps({"ts": "t2", "kind": "blocking_consumed", "lane": "validator", "event": evt})
        + "\n"
    )
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
        "from pkg import guarded\n\ndef test_g():\n    assert guarded() == 'safe'\n"
    )
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'safe'\" in s, 'anchor'\n"
        "p.write_text(s + '\\ndef(\\n')\n"
    )  # SyntaxError -> file-level collection ERROR
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "m",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
            "--named-test",
            "tests/test_g.py::test_g",
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
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
        "def guarded(x):\n    if x == 'with space':\n        return 'WITH_SPACE'\n    return x\n"
    )
    (tree / "tests").mkdir()
    (tree / "tests" / "test_g.py").write_text(
        "import pytest\nfrom pkg import guarded\n"
        "@pytest.mark.parametrize('x,expected', [('with space','WITH_SPACE'),('plain','plain')],"
        " ids=['with space','plain'])\n"
        "def test_g(x, expected):\n    assert guarded(x) == expected\n"
    )
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'WITH_SPACE'\" in s, 'anchor'\n"
        "p.write_text(s.replace(\"return 'WITH_SPACE'\", \"return 'broken'\"))\n"
    )
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "m",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
            "--named-test",
            "tests/test_g.py::test_g[with space]",
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
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
        + "".join(f"def test_g{i}(): assert guarded() == 'safe'\n" for i in range(1, 7))
    )
    patch = tmp_path / "p.py"
    _break_guarded(patch)
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "m",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
            "--named-test",
            "tests/test_g.py::test_g6",
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
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
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "m",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
            "--named-test",
            "tests/test_g.py::test_g",
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w"), "PYTEST_ADDOPTS": "--color=yes"},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "KILLED by:" in r.stdout, r.stdout


def test_receipt_stray_with_in_phrase_does_not_inflate_count(tmp_path: Path) -> None:
    """The strictly-harder false-acceptance: a stray own-line 'N passed ... in <digit>
    <word>' (e.g. '1 passed validation in 3 checks') HAS the ' in <digit>' phrase the
    first trailer required, so the buggy '\\bin \\d' matched it and read a vacuous run
    as test_count=1 — passing the >0 gate the receipt exists to reject. The fix
    requires the trailing 's' of the pytest duration ('in 0.00s'): 'in 3 checks' has
    no 's' after the digit, so it cannot feed the count and the vacuous marker wins."""
    run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            'echo "1 passed validation in 3 checks"; echo "no tests ran in 0.00s"; exit 0',
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
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
        "def guarded(x):\n    if x == 'a - b':\n        return 'DASH'\n    return x\n"
    )
    (tree / "tests").mkdir()
    (tree / "tests" / "test_g.py").write_text(
        "import pytest\nfrom pkg import guarded\n"
        "@pytest.mark.parametrize('x,expected', [('a - b','DASH'),('plain','plain')],"
        " ids=['a - b','plain'])\n"
        "def test_g(x, expected):\n    assert guarded(x) == expected\n"
    )
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'DASH'\" in s, 'anchor'\n"
        "p.write_text(s.replace(\"return 'DASH'\", \"return 'broken'\"))\n"
    )
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "m",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
            "--named-test",
            "tests/test_g.py::test_g[a - b]",
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
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
        "('def broken(:\\n    pass\\n')\n"
    )
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "m",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "KILLED" in r.stdout, r.stdout
    assert "SURVIVED" not in r.stdout, r.stdout


# --------------------------------------------------------------------------
# Third-round forcing probes — REAL pytest, not synthetic echoes.
#
# The second-round probes above were forcing for the REGEX but not for the
# command: every receipt probe used `echo "N passed in Xs"`, a synthetic bare
# line. Real pytest prints "===== N passed in Xs =====" (with '=' padding) and,
# under a tmux pane, ANSI color — both of which the bare-line probes never
# exercised, so `make ship` was green against the wrong shape. An adversarial
# pass found the receipt's test_count was None for every real pytest run. These
# probes run the REAL command through the script: the check must guard the
# prohibited action, not the fix's artifact.
# --------------------------------------------------------------------------


def _pytest_tree(tmp: Path, n: int = 2) -> Path:
    """A real collectable pytest tree with `n` passing tests (no -q; default
    verbosity, so the foot line is the padded '===== N passed in Xs =====')."""
    tree = tmp / "ptree"
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "src" / "pkg" / "__init__.py").write_text("def val():\n    return 42\n")
    (tree / "tests").mkdir()
    body = (
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))\n"
        "from pkg import val\n\n"
    )
    for i in range(n):
        body += f"def test_{i}():\n    assert val() == 42\n\n"
    (tree / "tests" / "test_x.py").write_text(body)
    return tree


def test_receipt_parses_real_pytest_padded_foot(tmp_path: Path) -> None:
    """A REAL `python3 -m pytest` run (default verbosity) prints a foot padded
    with '=': '===== N passed in Xs ====='. The bare-line anchor '(?:^|\\n)\\s*(\\d+)'
    rejects it (\\s* does not consume '='), so test_count was None — the load-bearing
    >0 gate inert against the very command it wraps. The '[ =]*' anchor tolerates the
    padding; test_count must be the real N, not None."""
    tree = _pytest_tree(tmp_path, 2)
    run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            f"cd {tree} && python3 -m pytest tests/test_x.py --color=no",
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 2, chain[-1]
    assert chain[-1]["pass_count"] == 2, chain[-1]


def test_receipt_parses_real_pytest_with_ansi_color(tmp_path: Path) -> None:
    """pytest 9 emits ANSI color on the summary line even when stdout is a pipe
    (it keys off TERM). The escapes sit before the digit, so the anchor saw 0x1b
    not a digit and test_count was None. The receipt must strip ANSI (as mutate.sh
    does for its own extraction) before deriving the count."""
    tree = _pytest_tree(tmp_path, 1)
    run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            f"cd {tree} && python3 -m pytest tests/test_x.py --color=yes",
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness"), "TERM": "xterm-256color"},
    )
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 1, chain[-1]


def test_receipt_real_pytest_vacuous_run_is_zero(tmp_path: Path) -> None:
    """A real pytest run that collects 0 tests prints 'no tests ran' (padded).
    Vacuous-first must classify it 0 — the >0 gate rejects it — not None."""
    tree = tmp_path / "empty"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "test_none.py").write_text("# no tests here\n")
    run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            f"cd {tree} && python3 -m pytest tests/ --color=no",
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 0, chain[-1]


def test_receipt_vacuous_marker_wins_over_stray_before_it(tmp_path: Path) -> None:
    """The HIGH false-acceptance the skeptics found: 'last match wins' does NOT
    protect a vacuous run, because a vacuous run has no real 'N passed' foot, so a
    stray 'N passed in Xs' BEFORE the vacuous marker is the only match and wins
    regardless of position — inflating a vacuous run to test_count>0 and passing
    the >0 gate. Vacuous-first (marker present anywhere -> 0) closes it: the stray
    is refused because the vacuous marker is authoritative."""
    run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            'echo "1 passed in 0.1s"; echo "no tests ran in 0.00s"; exit 0',
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 0, chain[-1]


def test_mutate_named_test_preserves_space_in_file_path(tmp_path: Path) -> None:
    """A pytest nodeid whose FILE path contains a space (tests/test_thing bar.py —
    legal, pytest 9.0.3 collects it) was dropped entirely by the space-forbidding
    regex token, so a real kill was mis-attributed <unnamed>, or with --named-test
    falsely rejected as KILLED-OUTSIDE-ORACLE even when the EXACT named oracle
    failed. The bracket-depth scan admits spaces in the path; the named oracle's
    kill must be attributed to it."""
    tree = tmp_path / "tree"
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "src" / "pkg" / "__init__.py").write_text("def guarded():\n    return 'safe'\n")
    (tree / "tests").mkdir()
    (tree / "tests" / "test_thing bar.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))\n"
        "from pkg import guarded\n\ndef test_oracle():\n    assert guarded() == 'safe'\n"
    )
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'safe'\" in s, 'anchor'\n"
        "p.write_text(s.replace(\"return 'safe'\", \"return 'broken'\"))\n"
    )
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "sp",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
            "--named-test",
            "tests/test_thing bar.py::test_oracle",
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "KILLED by: tests/test_thing bar.py::test_oracle" in r.stdout, r.stdout
    assert "OUTSIDE-ORACLE" not in r.stdout, r.stdout


def test_mutate_gate2_rejects_broken_clean_baseline(tmp_path: Path) -> None:
    """GATE 2 must see pytest's OWN exit code, not tail's. The first cut piped to
    `tail -3`, so $? was tail's (always 0): a clean baseline with a pre-existing
    conftest SyntaxError (exit 4, no 'N failed/error' summary) was accepted as
    green, and the mutation was falsely reported KILLED — the exact v8 false-red
    GATE 2 exists to prevent. With clean_rc captured before the tail pipe, GATE 2
    returns INVALID (baseline not green)."""
    tree = mkpkg(tmp_path / "tree")
    # A pre-existing broken conftest in the CLEAN baseline (not introduced by the patch).
    (tree / "tests" / "conftest.py").write_text("def broken(:\n    pass\n")
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'safe'\" in s, 'anchor'\n"
        "p.write_text(s.replace(\"return 'safe'\", \"return 'broken'\"))\n"
    )
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "g2",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
    assert r.returncode == 3, r.stdout + r.stderr
    assert "INVALID" in r.stdout and "baseline is not green" in r.stdout, r.stdout
    assert "KILLED" not in r.stdout, r.stdout


def test_mutate_named_test_conftest_crash_is_unattributed(tmp_path: Path) -> None:
    """With --named-test, a mutation that crashes suite-wide conftest collection
    (by breaking a symbol conftest imports) yields NO FAILED/ERROR rows, so
    killers is empty. The first cut fell through to KILLED-OUTSIDE-ORACLE with a
    literally-false 'a test failed' message (no test ran — collection crashed).
    The empty-killers path now emits KILLED-UNATTRIBUTED: the break is real
    (test_rc != 0) but not demonstrated by the named oracle, which never ran."""
    tree = tmp_path / "tree"
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "src" / "pkg" / "__init__.py").write_text("def add(a, b):\n    return a + b\n")
    (tree / "tests").mkdir()
    (tree / "tests" / "conftest.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))\n"
        "from pkg import add\n"
    )
    (tree / "tests" / "test_add.py").write_text(
        "from pkg import add\n\ndef test_add_basic():\n    assert add(1, 1) == 2\n"
    )
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert 'def add(a, b):' in s, 'anchor'\n"
        "p.write_text(s.replace('def add(a, b):', 'def add_(a, b):').replace('a + b', 'a - b'))\n"
    )
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "cc",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
            "--named-test",
            "tests/test_add.py::test_add_basic",
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
    assert r.returncode == 3, r.stdout + r.stderr
    assert "KILLED-UNATTRIBUTED" in r.stdout, r.stdout
    assert "OUTSIDE-ORACLE" not in r.stdout, r.stdout


# --- r4 fixes: real-pytest probes for the six false-rejection breaks ----------------


def test_receipt_real_pytest_failed_first_order_is_counted(tmp_path: Path) -> None:
    """r4 HIGH false-rejection: the foot anchor required `passed` at the line start,
    but pytest 9 orders failures FIRST ('1 failed, 2 passed in 0.03s'), so any run
    with a failure yielded test_count=None — every failing run misread as 'not a test
    runner' and the load-bearing >0 gate inert against it. Match the foot by keyword +
    'in Ns' and extract each count independently, so failed-first parses."""
    tree = tmp_path / "ftree"
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "src" / "pkg" / "__init__.py").write_text("def val():\n    return 42\n")
    (tree / "tests").mkdir()
    (tree / "tests" / "test_x.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))\n"
        "from pkg import val\n\n"
        "def test_pass_a():\n    assert val() == 42\n\n"
        "def test_pass_b():\n    assert val() == 42\n\n"
        "def test_fail():\n    assert val() == 99\n"
    )
    run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            f"cd {tree} && python3 -m pytest tests/test_x.py --color=yes",
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness"), "TERM": "xterm-256color"},
    )
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 3, chain[-1]
    assert chain[-1]["pass_count"] == 2, chain[-1]


def test_receipt_vacuous_phrase_in_test_stdout_under_s_is_not_zero(
    tmp_path: Path,
) -> None:
    """r4 HIGH false-rejection: the unanchored vacuous search matched the phrase
    'no tests ran' / 'collected 0 items' inside a test's OWN stdout (under -s),
    forcing test_count=0 on a real PASSING run — the >0 gate rejecting a green build,
    the opposite of the false-acceptance vacuous-first was added to close. Anchor the
    marker to pytest's terminal signal (timing-suffixed foot / end-of-line collection):
    'no tests ran today' lacks 'in Xs' and must NOT trigger vacuous."""
    tree = tmp_path / "stree"
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "src" / "pkg" / "__init__.py").write_text("def val():\n    return 42\n")
    (tree / "tests").mkdir()
    (tree / "tests" / "test_x.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))\n"
        "from pkg import val\n\n"
        "def test_prints_vacuous_phrase():\n"
        "    print('no tests ran today, all good')\n"
        "    print('collected 0 items from cache')\n"
        "    assert val() == 42\n\n"
        "def test_real_pass():\n    assert val() == 42\n"
    )
    run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            f"cd {tree} && python3 -m pytest tests/test_x.py -s --color=no",
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 2, chain[-1]
    assert chain[-1]["pass_count"] == 2, chain[-1]


def test_receipt_vacuous_phrase_in_captured_stdout_does_not_mask_failure(
    tmp_path: Path,
) -> None:
    """r4 MEDIUM: in default capture, a failing test's captured stdout (containing
    'no tests ran in submodule') is printed in the FAILURES section; the unanchored
    vacuous search matched it and forced test_count=0, hiding a real '1 failed, 1
    passed' behind a vacuous classification (the ledger lying even though exit=1
    still rejects). The anchored marker ('no tests ran in <N>s') does not match
    'in submodule', so the real summary parses."""
    tree = tmp_path / "ctree"
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "src" / "pkg" / "__init__.py").write_text("def val():\n    return 42\n")
    (tree / "tests").mkdir()
    (tree / "tests" / "test_x.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))\n"
        "from pkg import val\n\n"
        "def test_fails_and_prints_vacuous():\n"
        "    print('no tests ran in submodule')\n    assert val() == 99\n\n"
        "def test_real_pass():\n    assert val() == 42\n"
    )
    run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            f"cd {tree} && python3 -m pytest tests/test_x.py --color=no",
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 2, chain[-1]
    assert chain[-1]["pass_count"] == 1, chain[-1]


def test_mutate_gate2_not_fooled_by_terminal_summary_hook(tmp_path: Path) -> None:
    """r4 LOW: GATE 2's grep `[0-9]+ (failed|error)` matched a
    pytest_terminal_summary hook line ('1 failed to archive coverage artifacts')
    printed just before the foot in a GREEN run, returning INVALID despite clean_rc=0
    — blocking mutation testing on a tree with such a hook. Anchor the grep to
    summary syntax (a comma or ' in ' after the keyword) so a non-failure hook line
    does not match; the clean baseline passes and the mutation is correctly KILLED."""
    tree = mkpkg(tmp_path / "tree")
    (tree / "tests" / "conftest.py").write_text(
        "def pytest_terminal_summary(terminalreporter, exitstatus, config):\n"
        "    terminalreporter.write_line('1 failed to archive coverage artifacts')\n"
    )
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'safe'\" in s, 'anchor'\n"
        "p.write_text(s.replace(\"return 'safe'\", \"return 'broken'\"))\n"
    )
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "hk",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "KILLED" in r.stdout, r.stdout
    assert "INVALID" not in r.stdout, r.stdout


def test_mutate_named_test_open_bracket_in_param_id(tmp_path: Path) -> None:
    """r4 HIGH: a parametrize string id with a literal '[' (pytest 9 does NOT escape
    it) left bracket depth >0 at the real ' - ' separator, so the depth-scan appended
    the reason to the nodeid and --named-test rejected the EXACT oracle that failed as
    KILLED-OUTSIDE-ORACLE. Prefix-matching the known named-test against the raw FAILED
    line sidesteps the unparseable nodeid: the oracle's kill is attributed to it."""
    tree = tmp_path / "tree"
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "src" / "pkg" / "__init__.py").write_text("def guarded():\n    return 'safe'\n")
    (tree / "tests").mkdir()
    (tree / "tests" / "test_x.py").write_text(
        "import sys, pathlib, pytest\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))\n"
        "from pkg import guarded\n\n"
        "@pytest.mark.parametrize('x', ['bracket[open'])\n"
        "def test_g(x):\n    assert guarded() == 'safe'\n"
    )
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'safe'\" in s, 'anchor'\n"
        "p.write_text(s.replace(\"return 'safe'\", \"return 'broken'\"))\n"
    )
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "ob",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
            "--named-test",
            "tests/test_x.py::test_g[bracket[open]",
        ],
        cwd=tmp_path,
        env_extra={
            "MUTATE_WORKDIR": str(tmp_path / "w"),
            "PYTEST_ADDOPTS": "--color=yes",
            "TERM": "xterm-256color",
        },
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "KILLED" in r.stdout, r.stdout
    assert "OUTSIDE-ORACLE" not in r.stdout, r.stdout


def test_mutate_named_test_close_bracket_and_dash_in_param_id(tmp_path: Path) -> None:
    """r4 HIGH: a parametrize id with a literal ']' followed by ' - ' ('a]b - c')
    closed bracket depth prematurely to 0, so the depth-scan mistook the ' - ' INSIDE
    the id for the pytest separator and truncated the nodeid; --named-test rejected
    the exact oracle that failed as KILLED-OUTSIDE-ORACLE. Prefix-matching the known
    named-test against the raw FAILED line (the known string includes the id's ' - ')
    attributes the kill correctly."""
    tree = tmp_path / "tree"
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "src" / "pkg" / "__init__.py").write_text("def guarded():\n    return 'safe'\n")
    (tree / "tests").mkdir()
    (tree / "tests" / "test_x.py").write_text(
        "import sys, pathlib, pytest\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))\n"
        "from pkg import guarded\n\n"
        "@pytest.mark.parametrize('x', ['a]b - c'])\n"
        "def test_g(x):\n    assert guarded() == 'safe'\n"
    )
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'safe'\" in s, 'anchor'\n"
        "p.write_text(s.replace(\"return 'safe'\", \"return 'broken'\"))\n"
    )
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "cb",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
            "--named-test",
            "tests/test_x.py::test_g[a]b - c]",
        ],
        cwd=tmp_path,
        env_extra={
            "MUTATE_WORKDIR": str(tmp_path / "w"),
            "PYTEST_ADDOPTS": "--color=yes",
            "TERM": "xterm-256color",
        },
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "KILLED" in r.stdout, r.stdout
    assert "OUTSIDE-ORACLE" not in r.stdout, r.stdout


# --- r5 fixes: real-pytest probes for the three breaks that survived r4 ----------------


def test_receipt_all_deselected_vacuous_is_zero_not_none(tmp_path: Path) -> None:
    """r5 MEDIUM false-acceptance: a run that deselects EVERY test (-k NoSuchName /
    -m NoSuchMarker / --deselect all) prints 'collected N items / N deselected / 0
    selected' + 'N deselected in Xs' — neither the r4 vacuous anchor ('collected 0
    items' / 'no tests ran in Xs') nor the keyword summary regex matched, so
    test_count stayed None. With the exit code masked to 0 (|| true), the >0 gate
    skipped it as 'not a test runner' and ACCEPTED a 0-test build — the exact
    false-acceptance the vacuous branch exists to close. The '0 selected' token
    (only present when zero tests will run) and the 'N deselected in Xs' keyword-less
    foot now anchor it as test_count=0, which the >0 gate rejects."""
    tree = tmp_path / "dtree"
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "src" / "pkg" / "__init__.py").write_text("def val():\n    return 42\n")
    (tree / "tests").mkdir()
    (tree / "tests" / "test_x.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))\n"
        "from pkg import val\n\n"
        "def test_a1():\n    assert val() == 42\n\n"
        "def test_a2():\n    assert val() == 42\n"
    )
    # -k NoSuchName deselects all; || true masks pytest's exit 5 to 0 (the dangerous
    # case: a 0-test build that looks green to an exit-only check).
    run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            f"cd {tree} && python3 -m pytest tests/test_x.py -k NoSuchName --color=yes || true",
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness"), "TERM": "xterm-256color"},
    )
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 0, chain[-1]
    assert chain[-1]["pass_count"] == 0, chain[-1]


def test_receipt_skip_only_with_fake_summary_stdout_is_zero_not_five(
    tmp_path: Path,
) -> None:
    """r5 HIGH false-acceptance: a skip-only run's real foot ('1 skipped in 0.02s') has
    no passed/failed/error keyword, so 'take the last regex match ANYWHERE' fell back to
    a test's OWN stdout line '5 passed in 0.1s' (printed under -s before pytest.skip()),
    fabricating test_count=5 for a run that executed ZERO tests — defeating the
    load-bearing >0 gate. Anchoring the foot to its POSITION (the last non-empty line)
    structurally excludes test stdout (it prints during the run, before the terminal
    phase), and the keyword-less skip foot is classified vacuous (test_count=0), not
    None, so the >0 gate rejects the unverified build instead of skipping it."""
    tree = tmp_path / "stree"
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "src" / "pkg" / "__init__.py").write_text("def val():\n    return 42\n")
    (tree / "tests").mkdir()
    (tree / "tests" / "test_x.py").write_text(
        "import sys, pathlib, pytest\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))\n"
        "from pkg import val\n\n"
        "def test_dyn_skip():\n"
        "    print('5 passed in 0.1s')\n"
        "    pytest.skip('dynamic skip')\n"
    )
    # The fake '5 passed in 0.1s' is streamed under -s; the real foot is '1 skipped'.
    run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            f"cd {tree} && python3 -m pytest tests/test_x.py -q -s --color=yes",
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness"), "TERM": "xterm-256color"},
    )
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 0, chain[-1]
    assert chain[-1]["pass_count"] == 0, chain[-1]


def test_mutate_gate2_not_fooled_by_error_in_configuration_hook(tmp_path: Path) -> None:
    """r5 MEDIUM false-rejection: the r4 GATE 2 grep anchor '(,| in )' matched a
    pytest_terminal_summary hook line '1 error in configuration loading' via the
    ' in ' branch, INVALID-ing a genuinely GREEN rc=0 baseline ('1 passed in 0.02s')
    and blocking mutation testing on any tree with such a hook. The grep was fully
    redundant with the exit-code guard (in -q mode pytest prints no lowercase
    'N failed/error in Xs' timing line; the short-summary is uppercase, which the
    case-sensitive grep never matched), so its only independent effect was
    false-rejection. Relying on clean_rc alone, the green baseline proceeds and the
    killing mutation is correctly KILLED."""
    tree = mkpkg(tmp_path / "tree")
    (tree / "tests" / "conftest.py").write_text(
        "def pytest_terminal_summary(terminalreporter, exitstatus, config):\n"
        "    terminalreporter.write_line(\n"
        "        '1 error in configuration loading (deprecation: legacy adapter)')\n"
    )
    patch = tmp_path / "p.py"
    patch.write_text(
        "import sys,pathlib\n"
        "p=pathlib.Path(sys.argv[1])/'src/pkg/__init__.py'; s=p.read_text()\n"
        "assert \"return 'safe'\" in s, 'anchor'\n"
        "p.write_text(s.replace(\"return 'safe'\", \"return 'broken'\"))\n"
    )
    r = run(
        [
            "bash",
            str(HARNESS / "mutate.sh"),
            "ec",
            str(patch),
            "--src",
            str(tree),
            "--tests",
            str(tree),
        ],
        cwd=tmp_path,
        env_extra={"MUTATE_WORKDIR": str(tmp_path / "w")},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "KILLED" in r.stdout, r.stdout
    assert "INVALID" not in r.stdout, r.stdout


# --- r6 fix: the scan-backward foot anchor (post-foot plugin output) ------------------


def test_receipt_post_foot_coverage_line_does_not_mask_real_foot(tmp_path: Path) -> None:
    """r6 HIGH false-rejection (a regression the r5 last-line anchor introduced): a
    coverage/telemetry plugin prints a non-summary line AFTER the real pytest foot
    (pytest_unconfigure fires at session teardown, after summary_stats). The r5
    'foot = last non-empty line' anchor read the 'Coverage: 100%' line as the foot,
    missed the real '2 passed in 0.02s' foot, and recorded test_count=None for a real
    passing run — misclassifying a test runner as 'not a test runner'. Scan BACKWARD for
    the last line matching a foot pattern (keyword-bearing OR vacuous foot): the coverage
    line matches no foot pattern, so the real foot is found and test_count=2."""
    tree = tmp_path / "covtree"
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "src" / "pkg" / "__init__.py").write_text("def val():\n    return 42\n")
    (tree / "tests").mkdir()
    (tree / "tests" / "conftest.py").write_text(
        "def pytest_unconfigure(config):\n    print('Coverage: 100% (0 missing)')\n"
    )
    (tree / "tests" / "test_x.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))\n"
        "from pkg import val\n\n"
        "def test_a():\n    assert val() == 42\n\n"
        "def test_b():\n    assert val() == 42\n"
    )
    run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            f"cd {tree} && python3 -m pytest tests/test_x.py -q --color=no",
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 2, chain[-1]
    assert chain[-1]["pass_count"] == 2, chain[-1]


def test_receipt_xfail_only_with_fake_stdout_under_s_is_one_not_five(
    tmp_path: Path,
) -> None:
    """r7 HIGH false-acceptance (an over-correction the r6 scan-backward anchor
    introduced): an xfail-only run's real foot '1 xfailed in 0.02s' matched NONE of the
    r6 foot patterns (passed|failed|error | skipped|deselected | no tests ran), so
    scan-backward skipped the real foot and fell back to the test's OWN mid-run stdout
    '5 passed in 0.1s' (printed under -s) — recording test_count=5 for a run that was
    really 1 xfailed, 0 passed. This re-admitted the exact test-stdout the anchor exists
    to exclude, and it needs no plugin/conftest (so it is NOT the disclaimed forgery
    residual). Completing the keyword set with xfailed|xpassed makes the real xfail foot
    the last keyword-bearing line, so scan-backward anchors on it; xfailed counts as an
    EXECUTED test (test_count=1), pass_count stays 0 (xfail is not a pass)."""
    tree = tmp_path / "xftree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "test_xfail_print.py").write_text(
        "import pytest\n"
        "@pytest.mark.xfail(reason='expected to fail')\n"
        "def test_xfail_prints_fake():\n"
        "    print('5 passed in 0.1s')\n"
        "    assert False\n"
    )
    run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            f"cd {tree} && python3 -m pytest tests/test_xfail_print.py -s --color=no",
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 1, chain[-1]
    assert chain[-1]["pass_count"] == 0, chain[-1]


def test_receipt_xfail_only_no_print_is_one_not_none(tmp_path: Path) -> None:
    """r7 MEDIUM false-rejection: a pure xfail-only run (no printing) read test_count=None
    because the real foot '1 xfailed in 0.02s' matched none of the r6 foot patterns, so the
    foot stayed empty and the receipt misclassified a real test runner as 'genuinely not a
    test runner'. An xfail-only run executed a test — it is NOT 'no tests ran' — so the >0
    gate must see test_count=1, not None (skip) and not 0 (reject). xfailed counts toward
    test_count; pass_count stays 0."""
    tree = tmp_path / "xfotree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "test_xfail_only.py").write_text(
        "import pytest\n"
        "@pytest.mark.xfail(reason='expected to fail')\n"
        "def test_xfail_only():\n"
        "    assert False\n"
    )
    run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            f"cd {tree} && python3 -m pytest tests/test_xfail_only.py -q --color=no",
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 1, chain[-1]
    assert chain[-1]["pass_count"] == 0, chain[-1]


def test_receipt_xpass_only_is_one_not_none(tmp_path: Path) -> None:
    """r7 symmetric: an xpass-only run (xfail marker on a test that unexpectedly passes)
    has the real foot '1 xpassed in 0.02s', which the r6 set also missed. xpassed is an
    EXECUTED test -> test_count=1. pass_count stays 0: xpassed is a pass only under
    non-strict xfail (strict mode treats it as a failure), and the receipt does not
    adjudicate strict-vs-non-strict — that is the promotion gate's oracle-adequacy call."""
    tree = tmp_path / "xptree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "test_xpass_only.py").write_text(
        "import pytest\n"
        "@pytest.mark.xfail(reason='expected to fail')\n"
        "def test_xpass_only():\n"
        "    assert True\n"
    )
    run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            f"cd {tree} && python3 -m pytest tests/test_xpass_only.py -q --color=no",
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 1, chain[-1]
    assert chain[-1]["pass_count"] == 0, chain[-1]


def test_receipt_mixed_skipped_deselected_vacuous_is_zero_not_none(
    tmp_path: Path,
) -> None:
    """r8 MEDIUM false-acceptance (pre-existing, surfaced by testing the r7 keyword-set
    completeness claim): a vacuous 0-test run whose foot MIXES skipped and deselected
    ('2 skipped, 1 deselected in 0.00s' — 0 executed, exit 0) read test_count=None because
    vacuous_skip_re required ' in ' directly after the FIRST keyword and the mixed foot has
    ', 1 deselected in' after 'skipped'. None lets the >0 gate skip it as 'not a test runner'
    and accept a vacuous build — the exact false-acceptance the gate exists to reject, and a
    violation of the receipt's own 'vacuous -> 0, NOT null' contract. The fix allows a
    comma-separated skipped/deselected tail before ' in Xs'. A foot that STARTS with an
    executed keyword ('1 passed, 1 skipped') is NOT vacuous (start-anchored), so this stays
    a count, not a 0."""
    tree = tmp_path / "mixtree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "test_mix.py").write_text(
        "import pytest\n"
        "@pytest.mark.skip(reason='s1')\n"
        "def test_s1():\n    assert False\n"
        "@pytest.mark.skip(reason='s2')\n"
        "def test_s2():\n    assert False\n"
        "def test_unmarked():\n    assert True\n"
    )
    run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            f"cd {tree} && python3 -m pytest tests/test_mix.py -q -k 's1 or s2' --color=no",
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 0, chain[-1]
    assert chain[-1]["pass_count"] == 0, chain[-1]


def test_receipt_warnings_only_foot_is_zero_not_none(tmp_path: Path) -> None:
    """r9 HIGH false-acceptance (the gap that ended mix-enumeration): a run that collected ZERO
    tests but emitted a warning prints the foot '1 warning in 0.00s' INSTEAD OF 'no tests ran
    in 0.00s'. Under the enumerated vacuous patterns this foot matched NONE of keyword_re
    (no executed keyword), vacuous_foot_re ('no tests ran'), or vacuous_skip_re
    (skipped/deselected only) -> foot not found -> vacuous_coll did not fire (no 'collected 0
    items' / '0 selected' under -q) -> test_count=None -> the >0 gate skipped it as 'not a test
    runner' and accepted a 0-test build. The exact false-acceptance the gate exists to reject.
    The structural fix classifies by the executed-keyword PROPERTY: a foot carrying a pytest
    keyword (warning) but NONE of passed/failed/error/xfailed/xpassed is vacuous -> 0, however
    the non-executed counts combine. A 0-test run is now REJECTED, not skipped."""
    tree = tmp_path / "warntree"
    (tree / "tests").mkdir(parents=True)
    # conftest emits a warning at import; no test file is collected -> 0 tests, 1 warning.
    (tree / "tests" / "conftest.py").write_text(
        "import warnings\nwarnings.warn('from conftest', UserWarning)\n"
    )
    (tree / "tests" / "not_a_test.py").write_text("x = 1\n")
    run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            f"cd {tree} && python3 -m pytest tests/ -q --color=no",
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 0, chain[-1]
    assert chain[-1]["pass_count"] == 0, chain[-1]


def test_receipt_mixed_skipped_and_warning_vacuous_is_zero_not_none(
    tmp_path: Path,
) -> None:
    """r9 latent false-acceptance (the mix that proved enumeration is bottomless): a run whose
    foot MIXES a non-executed count with a warning ('2 skipped, 1 warning in 0.00s' — 0
    executed, exit 0). The r8 vacuous_skip_re tail absorbed only a comma-separated
    skipped/deselected mix; '1 warning' in the tail broke the match, so the foot matched
    nothing and read test_count=None -> the >0 gate skipped it and accepted a vacuous build.
    Each round of mix-enumeration exposed the next non-executed keyword combo; the structural
    fix ends the loop by classifying the property (no executed keyword -> vacuous) rather than
    enumerating each combination. A foot that carries an executed keyword ('1 passed, 1
    warning') is NOT vacuous and stays a count (see test_receipt_pass_warn_keeps_count)."""
    tree = tmp_path / "skipwarntree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "conftest.py").write_text(
        "import warnings\nwarnings.warn('from conftest', UserWarning)\n"
    )
    (tree / "tests" / "test_s.py").write_text(
        "import pytest\n"
        "@pytest.mark.skip(reason='s1')\n"
        "def test_s1():\n    assert False\n"
        "@pytest.mark.skip(reason='s2')\n"
        "def test_s2():\n    assert False\n"
    )
    run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            f"cd {tree} && python3 -m pytest tests/test_s.py -q --color=no",
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 0, chain[-1]
    assert chain[-1]["pass_count"] == 0, chain[-1]


def test_receipt_pass_warn_keeps_count_not_vacuous(tmp_path: Path) -> None:
    """r9 negative control for the structural fix: a foot that MIXES an executed keyword with a
    warning ('1 passed, 1 warning in 0.00s') must stay a COUNT (test_count=1), not be swept into
    the vacuous-0 branch. The structural classification keys on the PRESENCE of an executed
    keyword (passed/failed/error/xfailed/xpassed); '1 passed' is executed, so the foot counts
    regardless of the trailing warning. This is the discrimination that makes 'vacuous iff no
    executed keyword' safe: it rejects the 0-test warnings-only foot without rejecting a real
    passing run that merely emitted a warning. (A test whose body warns is itself an executed
    passing test, so this is the common 'a passing test raised a deprecation' shape.)"""
    tree = tmp_path / "passwarntree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "test_pw.py").write_text(
        "import warnings\n"
        "def test_p():\n    assert True\n"
        "def test_w():\n    warnings.warn('x', UserWarning)\n"
    )
    run(
        [
            "bash",
            str(HARNESS / "receipt.sh"),
            "bash",
            "-c",
            f"cd {tree} && python3 -m pytest tests/test_pw.py -q --color=no",
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    chain = read_chain(tmp_path / ".harness" / "receipts" / "chain.jsonl")
    assert chain[-1]["test_count"] == 2, chain[-1]
    assert chain[-1]["pass_count"] == 2, chain[-1]


# --------------------------------------------------------------------------
# Gate L — promote.sh is the SOLE writer of run.json "closed", reached only
# through decide_promotion (the factory CLI). A run with no gathered evidence,
# a blocked decision, or an unreachable CLI closes nothing (fail-closed).
# --------------------------------------------------------------------------

VENV_PY = HARNESS.parent / ".venv" / "bin" / "python"


def _factory_cli_env() -> dict[str, str]:
    if not VENV_PY.exists():
        pytest.skip("factory venv not built — `make dev` (promote.sh needs the factory CLI)")
    # The venv has the factory installed editable, so `import factory_runtime` resolves. PYTHONPATH
    # is belt-and-suspenders for a venv built without the editable install.
    return {
        "FACTORY_CLI": f"{VENV_PY} -m factory_runtime.cli",
        "PYTHONPATH": str(HARNESS.parent),
    }


def _make_run(tmp: Path, *, run_id: str = "r1", status: str = "open") -> Path:
    root = tmp / ".factory" / "runs" / run_id
    root.mkdir(parents=True)
    (root / "run.json").write_text(
        json.dumps(
            {
                "run": run_id,
                "repo": str(tmp),
                "base_sha": "0" * 40,
                "task_digest": "x" * 64,
                "status": status,
                "created_at": "2026-08-14T00:00:00+00:00",
            },
            indent=2,
        )
    )
    return root


def _run_status(root: Path) -> str:
    return json.loads((root / "run.json").read_text())["status"]


def test_promote_writes_closed_when_verdict_allows(tmp_path: Path) -> None:
    """The happy path: a run with gathered promoting evidence closes through decide_promotion.
    This is the sole advancement path — promote.sh writes 'closed' iff the verdict allows."""
    from tests.conftest import promoting_promotion_inputs, write_promoting_chain

    root = _make_run(tmp_path)
    (root / "promotion_inputs.json").write_text(
        json.dumps(promoting_promotion_inputs(), indent=2), encoding="utf-8"
    )
    # F3: the seam grounds each cited envelope in the real receipt chain. A real run's
    # evidence-production pipeline writes these chain entries via receipt.sh/mutate.sh/flake.sh;
    # here the harness-dir layout (run_root = <H>/runs/<run>, chain at <H>/receipts/chain.jsonl)
    # is the same, so write_promoting_chain grounds the fixture's R/M/F-default receipts.
    write_promoting_chain(root)
    r = run(
        ["bash", str(HARNESS / "promote.sh"), "r1"],
        tmp_path,
        _factory_cli_env(),
    )
    assert r.returncode == 0, r.stderr
    assert _run_status(root) == "closed"
    # The audited verdict file is written for the postmortem.
    assert (root / "promotion_verdict.json").exists()
    verdict = json.loads((root / "promotion_verdict.json").read_text())
    assert verdict["allowed"] is True


def test_promote_fail_closes_when_decision_blocks(tmp_path: Path) -> None:
    """A blocked decision (allowed=False) is a finding, not a failure of promote.sh: the cage
    refused to advance a run the evidence does not support. run.json stays open."""
    root = _make_run(tmp_path)
    # An empty request default-denies: candidate-digest-missing, no surfaces -> BLOCK.
    (root / "promotion_inputs.json").write_text(
        json.dumps({"request": {}, "policy": {}, "profile": {}}, indent=2),
        encoding="utf-8",
    )
    r = run(
        ["bash", str(HARNESS / "promote.sh"), "r1"],
        tmp_path,
        _factory_cli_env(),
    )
    assert r.returncode != 0, "a blocked decision must not close the run"
    assert _run_status(root) == "open"
    assert "BLOCKED" in r.stderr


def test_promote_fail_closes_when_inputs_missing(tmp_path: Path) -> None:
    """A run that has not gathered promotion_inputs.json cannot close — the close-path refuses
    rather than advancing on no evidence. This is the cage doing its job (fail-closed)."""
    root = _make_run(tmp_path)
    r = run(
        ["bash", str(HARNESS / "promote.sh"), "r1"],
        tmp_path,
        _factory_cli_env(),
    )
    assert r.returncode != 0
    assert _run_status(root) == "open"
    # No verdict is rendered for a run with no evidence.
    assert not (root / "promotion_verdict.json").exists()


def test_promote_fail_closes_when_cli_unreachable(tmp_path: Path) -> None:
    """If the factory CLI (the trust anchor) is unreachable, promote.sh fail-closes rather than
    guessing a verdict. A broken factory install can never be the route-around."""
    from tests.conftest import promoting_promotion_inputs

    root = _make_run(tmp_path)
    (root / "promotion_inputs.json").write_text(
        json.dumps(promoting_promotion_inputs(), indent=2), encoding="utf-8"
    )
    r = run(
        ["bash", str(HARNESS / "promote.sh"), "r1"],
        tmp_path,
        {"FACTORY_CLI": "/no/such/factory-binary", "PYTHONPATH": str(HARNESS.parent)},
    )
    assert r.returncode != 0
    assert _run_status(root) == "open"


def test_promote_refuses_stale_or_forged_verdict(tmp_path: Path) -> None:
    """A stale or hand-written promotion_verdict.json must NOT close a run (Opus F2).

    Before the freshness fix, promote.sh checked only ``[ -f promotion_verdict.json ]``, so a
    pre-existing forged verdict (``{"allowed": true}``) plus a no-op FACTORY_CLI (``true``,
    which exits 0 and writes nothing) closed the run WITHOUT decide_promotion ever running.
    The fix removes the verdict file before the CLI call and binds the verdict to this
    invocation (the file must match the CLI's captured stdout), so a no-op CLI writes no
    verdict and the close fail-closes. This is the red-now test for that route-around: it
    MUST fail against the unfixed script and pass against the fixed one.
    """
    root = _make_run(tmp_path)
    # A forged verdict planted before the run — the route-around.
    (root / "promotion_verdict.json").write_text(
        json.dumps({"allowed": True, "disposition": "promote"}), encoding="utf-8"
    )
    r = run(
        ["bash", str(HARNESS / "promote.sh"), "r1"],
        tmp_path,
        # `true` is a no-op CLI: exits 0, writes no verdict. The forged file must not satisfy
        # the close — the freshness removal + stdout binding defeat it.
        {"FACTORY_CLI": "true", "PYTHONPATH": str(HARNESS.parent)},
    )
    assert r.returncode != 0, "a forged verdict must not close the run"
    assert _run_status(root) == "open"
    # The forged verdict is removed; no fresh verdict was rendered by the no-op CLI.
    assert not (root / "promotion_verdict.json").exists()


def test_promote_refuses_verdict_that_differs_from_cli_stdout(tmp_path: Path) -> None:
    """If the verdict file does not match the CLI's stdout, promote.sh refuses it (Opus F2
    binding). A CLI that writes one verdict to the file and prints a different one to stdout
    is not a verdict this invocation can ground a close on — fail-closed."""
    root = _make_run(tmp_path)
    # A shim CLI: writes a FORGED allowed=true verdict file, but prints a BLOCKED decision
    # to stdout. The binding check (diff file vs stdout) catches the mismatch and refuses.
    shim = root / "fake_cli.py"
    shim.write_text(
        "import json, sys, pathlib\n"
        "argv = sys.argv\n"
        "runs = argv[argv.index('--runs') + 1]\n"
        "rid = argv[argv.index('--run-id') + 1]\n"
        "root = pathlib.Path(runs) / rid\n"
        "(root / 'promotion_verdict.json').write_text(\n"
        "    json.dumps({'allowed': True}))\n"
        "print(json.dumps({\n"
        "    'allowed': False, 'disposition': 'block',\n"
        "    'reasons': ['forged-file']}))\n"
    )
    py = str(VENV_PY) if VENV_PY.exists() else "python3"
    r = run(
        ["bash", str(HARNESS / "promote.sh"), "r1"],
        tmp_path,
        {"FACTORY_CLI": f"{py} {shim}", "PYTHONPATH": str(HARNESS.parent)},
    )
    assert r.returncode != 0, "a verdict file that differs from CLI stdout must not close"
    assert _run_status(root) == "open"


def test_promote_refuses_run_with_no_run_json(tmp_path: Path) -> None:
    """promote.sh refuses a run that has no run.json — it cannot close a run that does not exist."""
    (tmp_path / ".factory" / "runs").mkdir(parents=True)
    r = run(
        ["bash", str(HARNESS / "promote.sh"), "nope"],
        tmp_path,
        _factory_cli_env(),
    )
    assert r.returncode == 64
    assert "no run.json" in r.stderr


def test_promote_is_sole_writer_of_closed() -> None:
    """The sole-advancement invariant: NO harness shell script other than promote.sh writes the
    JSON value "closed". factory.sh writes "open"; the dispatcher READS "closed" to stop but
    never writes it. If another script gained a "closed" writer, advancement would have a second
    path and Gate L would be route-aroundable — this test fails closed the moment that happens."""
    import subprocess

    writers = subprocess.run(
        ["grep", "-rl", '"closed"', *[str(p) for p in HARNESS.glob("*.sh")]],
        capture_output=True,
        text=True,
    ).stdout.split()
    # promote.sh is the only writer; normalize to basenames for a stable assertion.
    writer_names = sorted(Path(w).name for w in writers if w)
    assert writer_names == ["promote.sh"], (
        f'only promote.sh may write "closed"; found: {writer_names}'
    )
