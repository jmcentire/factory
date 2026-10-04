"""Project start mints every key and the genesis; the human signs nothing.

Authority is what the human said (docs/standards/AUTHORITY.md H1). These tests prove the host
mints the keys, records the human's words as receipts the existing verifiers accept, and hands
each worker only its own key.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from factory_runtime.authority import human_public_keys, verify_receipt
from factory_runtime.cli import main
from factory_runtime.project_init import (
    ProjectInitError,
    init_project,
    load_project,
    record_human_ruling,
    record_human_statement,
)
from factory_runtime.tessera import TesseraCli, TesseraVerificationError

pytestmark = pytest.mark.tessera_integration

REPO = Path(__file__).resolve().parents[1]
SUBJECT = "sha256:" + "a" * 64


def _cli() -> TesseraCli:
    configured = os.environ.get("FACTORY_TESSERA_BIN")
    if not configured:
        pytest.skip("FACTORY_TESSERA_BIN is required for the real Tessera proof")
    return TesseraCli((configured,))


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_init_mints_one_owner_only_key_per_worker_and_a_verifying_genesis(
    tmp_path: Path,
) -> None:
    keys = tmp_path / "keys"
    project = init_project(keys, repository_id="demo", tessera=_cli())

    assert sorted(project.policy.principals) == [
        "agent:coder",
        "agent:orchestrator",
        "agent:tester",
        "agent:validator",
        "human:owner",
    ]
    assert _mode(keys) == 0o700
    assert {path.name: _mode(path) for path in keys.glob("*.key")} == {
        f"{role}.key": 0o600
        for role in ("human", "validator", "orchestrator", "coder", "tester")
    }
    assert (keys / ".gitignore").read_text(encoding="utf-8") == "*\n"
    human = project.policy.principals["human:owner"]
    assert human.public_key == project.root_public_key
    validator = project.policy.principals["agent:validator"]
    assert "factory:ratify-architecture" in validator.capabilities
    assert project.policy.principals["agent:coder"].capabilities == frozenset()


def test_init_is_idempotent_and_refuses_to_mint_over_foreign_files(tmp_path: Path) -> None:
    cli = _cli()
    first = init_project(tmp_path / "keys", repository_id="demo", tessera=cli)
    again = init_project(tmp_path / "keys", repository_id="ignored", tessera=cli)
    assert again.root_public_key == first.root_public_key
    assert again.policy.repository_id == "demo"

    stray = tmp_path / "stray"
    stray.mkdir()
    (stray / "notes.txt").write_text("not a project", encoding="utf-8")
    with pytest.raises(ProjectInitError, match="no genesis"):
        init_project(stray, repository_id="demo", tessera=cli)
    with pytest.raises(ProjectInitError, match="factory init"):
        load_project(tmp_path / "missing", tessera=cli)


def test_the_humans_words_become_a_receipt_the_existing_verifier_accepts(
    tmp_path: Path,
) -> None:
    cli = _cli()
    project = init_project(tmp_path / "keys", repository_id="demo", tessera=cli)
    record_human_statement(
        project,
        run_id="run-1",
        action="ratify-architecture",
        subject_digest=SUBJECT,
        said="Yes. Ship that architecture.",
        said_at="2026-10-04T06:00Z",
        output_path=tmp_path / "receipt.json",
        tessera=cli,
    )
    verified = verify_receipt(
        tmp_path / "receipt.json",
        policy=project.policy,
        expected_action="ratify-architecture",
        expected_subject_digest=SUBJECT,
        expected_run_id="run-1",
        tessera=cli,
    )
    assert verified.signer_identity == "human:owner"
    assert verified.envelope.payload["basis"] == {
        "source": "user-message",
        "said": "Yes. Ship that architecture.",
        "said_at": "2026-10-04T06:00Z",
    }

    with pytest.raises(ProjectInitError, match="human's words"):
        record_human_statement(
            project,
            run_id="run-1",
            action="authorize-change",
            subject_digest=SUBJECT,
            said="  ",
            said_at="now",
            output_path=tmp_path / "empty.json",
            tessera=cli,
        )


def test_the_human_key_never_signs_worker_evidence(tmp_path: Path) -> None:
    cli = _cli()
    project = init_project(tmp_path / "keys", repository_id="demo", tessera=cli)
    with pytest.raises(TesseraVerificationError):
        cli.wrap_json(
            {"evidence": "a worker's bundle"},
            kind="factory-evidence-bundle",
            key_path=project.key_paths["human"],
            output_path=tmp_path / "bundle.json",
            forbidden_signer_public_keys=human_public_keys(project.policy),
        )
    assert not (tmp_path / "bundle.json").exists()


def test_a_repair_ruling_is_the_humans_words_with_the_exact_binding(tmp_path: Path) -> None:
    cli = _cli()
    project = init_project(tmp_path / "keys", repository_id="demo", tessera=cli)
    record_human_ruling(
        project,
        kind="factory-ledger-unlock",
        fields={"run_id": "run-1", "guard": "resources.guard", "reason": "stale lock"},
        said="Unlock it; that process is dead.",
        said_at="2026-10-04T06:05Z",
        output_path=tmp_path / "ruling.json",
        tessera=cli,
    )
    envelope = cli.verify_json(
        tmp_path / "ruling.json",
        trusted_public_keys=tuple(human_public_keys(project.policy)),
        expected_kind="factory-ledger-unlock",
    )
    assert envelope.payload["run_id"] == "run-1"
    assert envelope.payload["basis"]["said"] == "Unlock it; that process is dead."

    with pytest.raises(ProjectInitError, match="needs: guard"):
        record_human_ruling(
            project,
            kind="factory-ledger-unlock",
            fields={"run_id": "run-1", "reason": "x"},
            said="Unlock it.",
            said_at="now",
            output_path=tmp_path / "bad.json",
            tessera=cli,
        )


def test_each_worker_gets_only_its_own_key(tmp_path: Path) -> None:
    project = init_project(tmp_path / "keys", repository_id="demo", tessera=_cli())
    env = project.worker_env("tester")
    assert env["FACTORY_SIGNING_KEY"] == str(project.key_paths["tester"])
    assert env["FACTORY_SIGNER_IDENTITY"] == "agent:tester"
    assert all("human.key" not in value for value in env.values())

    shell = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{REPO}/harness/run_context.sh"; factory_worker_env coder',
        ],
        env={**os.environ, "FACTORY_KEYS_DIR": str(project.keys_dir)},
        check=True,
        capture_output=True,
        text=True,
    )
    assert f"FACTORY_SIGNING_KEY={project.key_paths['coder']}" in shell.stdout
    assert "human.key" not in shell.stdout
    refused = subprocess.run(
        ["bash", "-c", f'source "{REPO}/harness/run_context.sh"; factory_worker_env human'],
        env={**os.environ, "FACTORY_KEYS_DIR": str(project.keys_dir)},
        capture_output=True,
        text=True,
    )
    assert refused.returncode != 0


def test_cli_init_and_record_statement(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    binary = os.environ.get("FACTORY_TESSERA_BIN") or pytest.skip("needs FACTORY_TESSERA_BIN")
    keys = tmp_path / "keys"
    assert main(["init", "--keys-dir", str(keys), "--repository-id", "demo",
                 "--tessera-bin", str(binary)]) == 0
    minted = json.loads(capsys.readouterr().out)
    assert set(minted["workers"]) == {"validator", "orchestrator", "coder", "tester"}

    assert main([
        "record-statement", "--keys-dir", str(keys), "--run-id", "run-1",
        "--action", "authorize-change", "--subject-digest", SUBJECT,
        "--said", "Build it.", "--said-at", "2026-10-04T06:10Z",
        "--output", str(tmp_path / "r.json"), "--tessera-bin", str(binary),
    ]) == 0
    assert (tmp_path / "r.json").is_file()


def test_init_mints_the_chain_root_the_genesis_commits_to(tmp_path: Path) -> None:
    from factory_core.manifest import digest_bytes
    from factory_runtime.durability import load_chain_root_material

    project = init_project(tmp_path / ".factory" / "keys", repository_id="demo", tessera=_cli())
    root_file = tmp_path / ".factory" / ".chain-root.key"
    assert _mode(root_file) == 0o600
    located = load_chain_root_material(tmp_path / ".factory" / "runs" / "run-1" / "ledger.jsonl")
    assert located is not None
    assert project.policy.chain_root_commitment == digest_bytes(located[0])


def test_a_lane_started_with_its_grant_sees_only_its_own_key(tmp_path: Path) -> None:
    project = init_project(tmp_path / "keys", repository_id="demo", tessera=_cli())
    harness_root = tmp_path / "harness"
    harness_root.mkdir()
    (harness_root / "grounded").write_text("now", encoding="utf-8")
    grants = project.keys_dir / "grants"
    lane = subprocess.run(
        [str(REPO / "harness" / "lane_env.sh"), str(grants / "coder.manifest"), "--", "env"],
        env={
            **os.environ,
            "FACTORY_HARNESS_ROOT": str(harness_root),
            "HARNESS_SECRETS": str(grants / "coder"),
        },
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    seen = dict(line.split("=", 1) for line in lane.stdout.splitlines() if "=" in line)
    assert seen["FACTORY_SIGNING_KEY"] == str(project.key_paths["coder"])
    assert seen["FACTORY_SIGNER_IDENTITY"] == "agent:coder"
    assert seen["FACTORY_GENESIS"] == str(project.genesis_path)
    assert not any("human.key" in value or "tester.key" in value for value in seen.values())


def test_keys_chain_root_and_signed_records_stay_out_of_git(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    project = init_project(tmp_path / ".factory" / "keys", repository_id="demo", tessera=_cli())
    record_human_statement(
        project,
        run_id="run-1",
        action="authorize-change",
        subject_digest=SUBJECT,
        said="Build it.",
        said_at="2026-10-04T07:00Z",
        output_path=tmp_path / ".factory" / "intake" / "receipt.tessera.json",
        tessera=_cli(),
    )
    notes = tmp_path / ".factory" / "demo" / "prd.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("# PRD\n", encoding="utf-8")
    status = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--porcelain", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert ".factory/demo/prd.md" in status
    assert ".factory/.gitignore" in status
    for local_only in (".key", ".chain-root.key", ".tessera.json", "grants/"):
        assert local_only not in status
