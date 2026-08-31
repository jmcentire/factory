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
import shlex
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from factory_core.manifest import LedgerEntry, digest_bytes, digest_obj
from factory_core.target import load_target_manifest
from factory_runtime.resources import ResourceLedger
from factory_runtime.state import RunState, RunStore
from factory_runtime.state_admission import profile_digest
from factory_runtime.target_state import TargetResolver, normalize_repository_url, normalize_subpath

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
    # Harness unit fixtures predate real Tessera envelopes and cannot call a live model. Route
    # those two external boundaries through a test-local evidence-producing shim; all state,
    # projection, resource, digest, and gate commands still reach the real CLI. Dedicated
    # runtime/integration tests exercise real checkpoint, runner, broker, and Seatbelt behavior.
    support = Path(os.environ.get("TMPDIR", "/tmp")) / (
        f"factory-harness-test-{os.getpid()}-{hashlib.sha256(str(cwd).encode()).hexdigest()[:12]}"
    )
    support.mkdir(exist_ok=True)
    checkpoint = support / "checkpoint.json"
    genesis = support / "genesis.json"
    configuration = support / "configuration.txt"
    config_manifest = support / "configuration.manifest"
    for path in (checkpoint, genesis, configuration):
        if not path.exists():
            path.write_text("{}\n", encoding="utf-8")
    directive_ledger = Path(
        env.get(
            "FACTORY_TEST_DIRECTIVE_LEDGER_SOURCE",
            str(support / "directive-ledger.jsonl"),
        )
    )
    directive_ledger.parent.mkdir(parents=True, exist_ok=True)
    directive_ledger.touch(exist_ok=True)
    directive_provisional = directive_ledger.with_name("provisional.jsonl")
    directive_provisional.touch(exist_ok=True)
    role_doctrine = Path(__file__).resolve().parents[1] / "docs" / "SOFTWARE-FACTORY.md"
    config_manifest.write_text(
        "".join(
            (
                f"harness-test={configuration.resolve()}\n",
                f"factory-directive-ledger={directive_ledger.resolve()}\n",
                f"factory-directive-provisional={directive_provisional.resolve()}\n",
                f"factory-role-doctrine={role_doctrine.resolve()}\n",
            )
        ),
        encoding="utf-8",
    )
    shim = support / "factory-cli-shim.py"
    shim.write_text(
        """import hashlib
import json
import os
import pathlib
import signal
import shlex
import sys

verb = sys.argv[1] if len(sys.argv) > 1 else ""

def argument(name):
    return sys.argv[sys.argv.index(name) + 1]

def note(value):
    path = os.environ.get("FACTORY_TEST_BOUNDARY_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(value + "\\n")

if verb == "verify-resume-checkpoint":
    print(json.dumps({"verified": True, "test_fixture": True}))
    raise SystemExit(0)
if verb in {
    "status",
    "verify-target-state",
    "verify-execution-request",
    "record-resource",
    "disposition-resource",
}:
    expected = {
        "--genesis": os.environ["FACTORY_GENESIS"],
        "--root-public-key": os.environ["FACTORY_ROOT_PUBLIC_KEY"],
        "--tessera-bin": os.environ.get("FACTORY_TESSERA_BIN", "tessera"),
    }
    for option, value in expected.items():
        if argument(option) != value:
            raise SystemExit(f"{verb} replay anchor mismatch for {option}")
    passthrough = list(sys.argv[1:])
    for option in expected:
        index = passthrough.index(option)
        del passthrough[index : index + 2]
    command = shlex.split(os.environ["FACTORY_TEST_REAL_CLI"])
    os.execvpe(command[0], [*command, *passthrough], os.environ)
if verb == "bundle-orchestrator-projection":
    output = pathlib.Path(argument("--output"))
    capsule_output = pathlib.Path(argument("--capsule-output"))
    section_specs = [
        sys.argv[index + 1]
        for index, value in enumerate(sys.argv[:-1])
        if value == "--section"
    ]
    expected_caller_sections = {
        "trigger",
        "task",
        "receipt-tail",
        "event-tail",
        "minutes-tail",
        "harness-metadata",
    }
    caller_sections = {spec.split("=", 1)[0] for spec in section_specs}
    if caller_sections != expected_caller_sections:
        raise SystemExit(
            "unexpected caller-owned orchestrator sections: "
            + repr(sorted(caller_sections))
        )
    sections = []
    for spec in section_specs:
        section_id, path = spec.split("=", 1)
        raw = pathlib.Path(path).read_bytes()
        sections.append({
            "section_id": section_id,
            "content": raw.decode("utf-8"),
            "content_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
            "byte_count": len(raw),
            "trust_class": "context",
        })
    for section_id, content, trust_class in (
        ("phase-artifacts", "{}", "context"),
        ("run-projection", "{}", "verified-state"),
    ):
        raw = content.encode("utf-8")
        sections.append({
            "section_id": section_id,
            "content": content,
            "content_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
            "byte_count": len(raw),
            "trust_class": trust_class,
        })
    from factory_runtime.instruction_control import (
        canonical_document_bytes,
        derive_effective_directive_contract,
    )
    effective = derive_effective_directive_contract(
        ledger_bytes=pathlib.Path(argument("--directive-ledger")).read_bytes(),
        provisional_bytes=pathlib.Path(argument("--directive-provisional")).read_bytes(),
        run_id=argument("--run-id"),
        generation=1,
        role="orchestrator",
        evaluated_at=int(__import__("time").time()),
    )
    active = canonical_document_bytes(effective)
    sections.append({
        "section_id": "active-directives",
        "content": active.decode("utf-8"),
        "content_digest": "sha256:" + hashlib.sha256(active).hexdigest(),
        "byte_count": len(active),
        "trust_class": "context",
    })
    capsule = {
        "schema_version": "factory-state-dependency-capsule/1",
        "test_fixture": True,
    }
    projection = {
        "schema_version": "factory-orchestrator-projection/1",
        "test_fixture": True,
        "sections": sections,
        "state_capsule_digest": "sha256:" + hashlib.sha256(
            json.dumps(capsule, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    capsule_output.write_text(
        json.dumps(capsule, sort_keys=True, separators=(",", ":")) + "\\n",
        encoding="utf-8",
    )
    output.write_text(
        json.dumps(projection, sort_keys=True, separators=(",", ":")) + "\\n",
        encoding="utf-8",
    )
    print(json.dumps({"projection_digest": projection["state_capsule_digest"]}))
    raise SystemExit(0)
if verb == "run-model":
    note("run-model")
    if os.environ.get("FACTORY_TEST_RUN_MODEL_FAIL_WITH_RECEIPT") == "1":
        from factory_core.manifest import digest_bytes, digest_obj
        from factory_runtime.runner import RunnerManifest
        from factory_runtime.state_admission import derive_state_capsule, profile_document

        workspace = pathlib.Path(argument("--workspace"))
        input_root = workspace / "input"
        output_root = workspace / "output"
        executable_root = workspace / "executables"
        workspace.mkdir(parents=True)
        for directory in (input_root, output_root, executable_root):
            directory.mkdir()
        role = argument("--role")
        run_id = argument("--run-id")
        generation = int(os.environ["FACTORY_GENERATION"])
        receipt_id = argument("--receipt-id")
        projection_raw = pathlib.Path(argument("--projection")).read_bytes()
        task_raw = pathlib.Path(argument("--task-file")).read_bytes()
        manifest_raw = pathlib.Path(argument("--runner-manifest")).read_bytes()
        broker_raw = pathlib.Path(argument("--broker-registry")).read_bytes()
        manifest_document = json.loads(manifest_raw)
        manifest = RunnerManifest.from_dict(manifest_document)
        executable_raw = pathlib.Path(manifest_document["executable"]).read_bytes()
        executable_path = executable_root / "runner"
        executable_path.write_bytes(executable_raw)
        executable_path.chmod(0o500)
        qualification = {
            "backend": "fixture-qualified-v1",
            "scope_digest": digest_obj({"scope": "fixture"}),
            "forbidden_read_denied": True,
            "forbidden_write_denied": True,
            "model_network_available": True,
            "arbitrary_shell_denied": True,
            "process_containment": True,
        }
        qualification_raw = json.dumps(
            qualification, sort_keys=True, separators=(",", ":")
        ).encode()
        (input_root / "runner-qualification.json").write_bytes(qualification_raw)
        dependencies = {
            item["dependency_id"]: f"fixture:{item['dependency_id']}".encode()
            for item in profile_document("lane-dispatch")["dependencies"]
        }
        dependencies.update({
            "runner-manifest": manifest_raw,
            "runner-projection": projection_raw,
            "frozen-task": task_raw,
            "broker-registry": broker_raw,
        })
        capsule = derive_state_capsule(
            purpose="lane-dispatch",
            run_id=run_id,
            generation=generation,
            role=role,
            target_state_digest=os.environ["FACTORY_TARGET_STATE_DIGEST"],
            run_ledger_head="sha256:" + "1" * 64,
            resume_checkpoint_digest=os.environ["FACTORY_RESUME_CHECKPOINT_DIGEST"],
            dependencies=dependencies,
        )
        capsule_raw = json.dumps(capsule, sort_keys=True, separators=(",", ":")).encode() + b"\\n"
        (input_root / "state-capsule.json").write_bytes(capsule_raw)
        continuity_nonce = "a" * 64
        prompt_document = {
            "schema_version": "factory-runner-prompt/3",
            "kind": "qualification",
            "control": {
                "continuity": {"store_and_echo": continuity_nonce},
            },
        }
        prompt_raw = json.dumps(prompt_document, sort_keys=True, separators=(",", ":")).encode()
        (input_root / "prompt-1.json").write_bytes(prompt_raw)
        diagnostic = {
            "schema_version": "factory-runner-invocation-diagnostic/1",
            "invocation": 1,
            "returncode": 1,
            "termination_reason": "exit-nonzero",
            "process_peak": 1,
            "stdout": "private fixture output",
            "stderr": "fixture failure",
        }
        diagnostic_raw = json.dumps(
            diagnostic, sort_keys=True, separators=(",", ":")
        ).encode() + b"\\n"
        (workspace / "validator-invocation-diagnostic.json").write_bytes(diagnostic_raw)
        capsule_digest = digest_obj(capsule)
        failure_capsule = {
            "schema_version": "factory-failure-capsule/1",
            "owner": "validator-harness",
            "code": "validator-caller-exception",
            "summary": "The Validator caller raised before it could complete the attempt protocol.",
        }
        receipt = {
            "schema_version": "factory-runner-failure-receipt/2",
            "receipt_id": receipt_id,
            "run_id": run_id,
            "generation": generation,
            "role": role,
            "invocation": 1,
            "model_attempts": 1,
            "runner_manifest_digest": manifest.content_digest,
            "runner_manifest_source_digest": digest_bytes(manifest_raw),
            "runner_id": manifest_document["runner_id"],
            "adapter": manifest_document["adapter"],
            "executable_digest": digest_bytes(executable_raw),
            "executable_snapshot": {
                "relative_path": "executables/runner",
                "byte_count": len(executable_raw),
                "content_digest": digest_bytes(executable_raw),
            },
            "child_executable_snapshots": [],
            "runner_version": manifest_document["runner_version"],
            "model": manifest_document["model"],
            "model_version": manifest_document["model_version"],
            "configuration_digest": manifest_document["configuration_digest"],
            "state_profile_digest": manifest_document["state_profile_digest"],
            "state_qualification_digest": manifest_document["state_qualification_digest"],
            "state_capsule_digest": capsule_digest,
            "projection_digest": digest_bytes(projection_raw),
            "task_digest": digest_bytes(task_raw),
            "target_state_digest": os.environ["FACTORY_TARGET_STATE_DIGEST"],
            "run_ledger_head": capsule["run_ledger_head"],
            "resume_checkpoint_digest": os.environ["FACTORY_RESUME_CHECKPOINT_DIGEST"],
            "broker_registry_source_digest": digest_bytes(broker_raw),
            "qualification_digest": digest_obj(qualification),
            "qualification": qualification,
            "continuity_nonce_digest": digest_obj({"continuity_nonce": continuity_nonce}),
            "prompt_schema_version": "factory-runner-prompt/3",
            "prompt_assembler_version": "factory-runner-prompt-assembler/2",
            "prompt_sequence": [{
                "attempt": 1,
                "kind": "qualification",
                "byte_count": len(prompt_raw),
                "content_digest": digest_bytes(prompt_raw),
            }],
            "prompt_bytes_retained": True,
            "diagnostic": {
                "content_digest": digest_bytes(diagnostic_raw),
                "byte_count": len(diagnostic_raw),
                "visibility": "validator-private",
            },
            "termination_reason": "exit-nonzero",
            "returncode": 1,
            "process_peak": 1,
            "failure_capsule": failure_capsule,
            "failed_at": 1,
        }
        (workspace / "runner-failure-receipt.json").write_text(
            json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\\n",
            encoding="utf-8",
        )
        if os.environ.get("FACTORY_TEST_KILL_DISPATCH_AFTER_FAILURE_RECEIPT") == "1":
            for evidence_path in (
                workspace / "runner-failure-receipt.json",
                workspace / "validator-invocation-diagnostic.json",
            ):
                descriptor = os.open(evidence_path, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            directory_descriptor = os.open(workspace, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
            os.kill(os.getppid(), signal.SIGKILL)
        print(json.dumps({"status": "failed", "failure_receipt": receipt}))
        raise SystemExit(70)
    if os.environ.get("FACTORY_TEST_RUN_MODEL_FAIL") == "1":
        raise SystemExit(70)
    workspace = pathlib.Path(argument("--workspace"))
    output = workspace / "output"
    output.mkdir(parents=True)
    role = argument("--role")
    run_id = argument("--run-id")
    receipt_id = argument("--receipt-id")
    projection_raw = pathlib.Path(argument("--projection")).read_bytes()
    projection_digest = "sha256:" + hashlib.sha256(projection_raw).hexdigest()
    continuity_nonce = "a" * 64
    capsule = {"test_fixture": True, "run_id": run_id, "role": role}
    capsule_bytes = json.dumps(capsule, sort_keys=True, separators=(",", ":")).encode()
    capsule_digest = "sha256:" + hashlib.sha256(capsule_bytes).hexdigest()
    input_root = workspace / "input"
    input_root.mkdir(parents=True)
    (input_root / "state-capsule.json").write_bytes(capsule_bytes + b"\\n")
    handoff = {
        "kind": "handoff", "role": role, "projection_digest": projection_digest,
        "state_capsule_digest": capsule_digest,
        "sequence": 3, "status": "complete", "summary": "fixture handoff",
        "questions": [], "broker_requests": [], "continuity_nonce": continuity_nonce,
    }
    handoff_bytes = json.dumps(handoff, sort_keys=True, separators=(",", ":")).encode()
    (output / "handoff.json").write_bytes(handoff_bytes + b"\\n")
    receipt = {
        "schema_version": "factory-runner-receipt/2", "receipt_id": receipt_id,
        "run_id": run_id, "generation": 1, "role": role,
        "runner_manifest_digest": "sha256:" + "1" * 64, "runner_id": "fixture",
        "runner_manifest_source_digest": "sha256:" + "1" * 64,
        "adapter": "codex", "executable_digest": "sha256:" + "2" * 64,
        "runner_version": "fixture", "model": "fixture", "model_version": "fixture",
        "configuration_digest": "sha256:" + "3" * 64,
        "state_profile_digest": "sha256:" + "3" * 64,
        "state_qualification_digest": "sha256:" + "3" * 64,
        "state_capsule_digest": capsule_digest,
        "projection_digest": projection_digest,
        "task_digest": "sha256:" + "3" * 64,
        "resume_checkpoint_digest": "sha256:" + "0" * 64,
        "broker_registry_source_digest": "sha256:" + "3" * 64,
        "billing_key_name": "TEST_TOKEN", "secret_names": ["TEST_TOKEN"],
        "network_mode": "unrestricted-outbound",
        "qualification_digest": "sha256:" + "4" * 64,
        "canary_session_id": "fixture-session", "resumed_session_id": "fixture-session",
        "continuity_nonce_digest": "sha256:" + hashlib.sha256(
            json.dumps(
                {"continuity_nonce": continuity_nonce}, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
        "canary_attempts": 2, "task_attempt": 3, "input_tokens": 1,
        "output_tokens": 1, "cost_microusd": 1, "cost_known": True,
        "meter_semantics": "observed-post-call", "process_peak": 1,
        "termination_reason": "completed",
        "handoff_digest": "sha256:" + hashlib.sha256(handoff_bytes).hexdigest(),
        "started_at": 1, "finished_at": 2,
    }
    (output / "runner-receipt.json").write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\\n",
        encoding="utf-8",
    )
    print(json.dumps({"handoff": handoff, "runner_receipt": receipt}))
    raise SystemExit(0)
if verb == "execute-broker-handoff":
    note("execute-broker-handoff")
    if os.environ.get("FACTORY_TEST_BROKER_FAIL") == "1":
        raise SystemExit(70)
    print(json.dumps({"verified": True, "effects": []}))
    raise SystemExit(0)
command = shlex.split(os.environ["FACTORY_TEST_REAL_CLI"])
os.execvpe(command[0], [*command, *sys.argv[1:]], os.environ)
""",
        encoding="utf-8",
    )
    real_cli = env.get("FACTORY_CLI", "factory")
    # Reject an invalid test command here instead of constructing a subtly different argv.
    if not shlex.split(real_cli):
        raise AssertionError("test FACTORY_CLI must not be empty")
    env.update(
        {
            "FACTORY_TEST_REAL_CLI": real_cli,
            "FACTORY_CLI": f"{sys.executable} {shim}",
            "FACTORY_RESUME_CHECKPOINT": str(checkpoint),
            "FACTORY_RESUME_CHECKPOINT_DIGEST": "sha256:" + "0" * 64,
            "FACTORY_GENESIS": str(genesis),
            "FACTORY_ROOT_PUBLIC_KEY": "0" * 64,
            "FACTORY_RESUME_CONFIG_MANIFEST": str(config_manifest),
        }
    )
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


@pytest.mark.parametrize(
    "arguments",
    [
        ("append", "--scope", "role=codre", "--text", "invalid signed scope"),
        (
            "provisional",
            "--scope",
            "generation=01",
            "--text",
            "invalid provisional scope",
            "--cite",
            "transcript.jsonl:1:uuid:deadbeef",
        ),
    ],
)
def test_directive_writers_reject_invalid_scope_without_mutating_either_chain(
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    directives = tmp_path / "DIRECTIVES"
    ledger = directives / "ledger.jsonl"
    provisional = directives / "provisional.jsonl"
    before = {
        path: path.read_bytes() if path.exists() else None
        for path in (ledger, provisional)
    }

    result = dl(tmp_path, *arguments)

    after = {
        path: path.read_bytes() if path.exists() else None
        for path in (ledger, provisional)
    }
    assert result.returncode != 0
    assert "invalid directive scope" in result.stderr
    assert after == before


def test_directive_supersession_cannot_change_scope_or_mutate_chains(
    tmp_path: Path,
) -> None:
    added = dl(tmp_path, "append", "--scope", "global", "--text", "global rule")
    assert added.returncode == 0, added.stderr
    directives = tmp_path / "DIRECTIVES"
    ledger = directives / "ledger.jsonl"
    provisional = directives / "provisional.jsonl"
    before = {path: path.read_bytes() for path in (ledger, provisional)}

    result = dl(
        tmp_path,
        "supersede",
        "D-0001",
        "--scope",
        "role=coder",
        "--text",
        "narrowed rule",
    )

    assert result.returncode != 0
    assert "must exactly match its parent" in result.stderr
    assert {path: path.read_bytes() for path in (ledger, provisional)} == before


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
    directives = tmp / "DIRECTIVES"
    directives.mkdir()
    (directives / "ledger.jsonl").write_bytes(b"")
    (directives / "provisional.jsonl").write_bytes(b"")
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
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, primer=True)
    dispatch = tmp_path / "d.md"
    dispatch.write_text(
        json.dumps(_lane_dispatch("coder", ambiguity="unresolved")),
        encoding="utf-8",
    )
    r = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        _dispatch_env(stub, root),
    )
    assert r.returncode == 70 and "structured dispatch" in r.stderr
    # Phase 0.1 forcing: the dispatch refusal leaves its signal.
    rows = _refusal_events(root)
    assert [row["kind"] for row in rows] == ["refusal-dispatch"]


def test_dispatch_refuses_omitted_effective_directive_before_model_use(
    tmp_path: Path,
) -> None:
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, primer=True)
    appended = dl(
        tmp_path,
        "append",
        "--scope",
        "run",
        "--text",
        "Do not alter migrations",
    )
    assert appended.returncode == 0, appended.stderr
    environment = _dispatch_env(stub, root)
    environment["FACTORY_TEST_DIRECTIVE_LEDGER_SOURCE"] = str(
        tmp_path / "DIRECTIVES" / "ledger.jsonl"
    )

    result = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        environment,
    )

    assert result.returncode == 70
    assert "structured dispatch" in result.stderr
    assert not (tmp_path / "boundary.log").exists()


def test_config_source_resolution_uses_verified_vector_not_reread_manifest(
    tmp_path: Path,
) -> None:
    trusted = tmp_path / "trusted-ledger.jsonl"
    forged = tmp_path / "forged-ledger.jsonl"
    manifest = tmp_path / "config.sources"
    trusted.write_bytes(b"")
    forged.write_text("ambient forged directive\n", encoding="utf-8")
    manifest.write_text(
        f"factory-directive-ledger={forged.resolve()}\n",
        encoding="utf-8",
    )
    script = f"""
source {HARNESS / 'run_context.sh'}
FACTORY_RESUME_CONFIG_MANIFEST={manifest}
FACTORY_VERIFIED_RESUME_CONFIG_ARGS=(
  --config-source factory-directive-ledger={trusted.resolve()}
)
factory_config_source_path factory-directive-ledger
"""

    result = run(["bash", "-c", script], tmp_path, {})

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(trusted.resolve())


# --------------------------------------------------------------------------
# Projections — asymmetric views, no ancestry
# --------------------------------------------------------------------------


def projection_fixture(tmp: Path) -> Path:
    src = tmp / "src"
    src.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=src, check=True)
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


def execution_truth_fixture(
    tmp: Path,
    *,
    run_id: str = "r1",
    task: str = "Build the exact authorized behavior.",
    projection_conf: str = "coder-exclude: tests\ntester-include: impl.py\n",
    harness_status: str | None = "open",
    terminal_resources: bool = False,
) -> tuple[Path, Path, dict[str, object]]:
    """Create a real v3 intake run with a verified run-owned target checkout.

    Harness tests must not manufacture the retired ``repo/base_sha`` JSON shape: the scripts
    under test now consume the same ledger-derived target-state as production.
    """

    operator = projection_fixture(tmp)
    config = operator / ".factory" / "projection.conf"
    config.parent.mkdir()
    config.write_text(projection_conf, encoding="utf-8")
    subprocess.run(["git", "add", ".factory/projection.conf"], cwd=operator, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-qm",
            "projection contract",
        ],
        cwd=operator,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "remote",
            "add",
            "origin",
            "https://example.invalid/acme/widget.git",
        ],
        cwd=operator,
        check=True,
    )

    manifest = load_target_manifest(
        Path(__file__).parent / "fixtures" / "synthetic_target" / "target.toml"
    )
    runs = tmp / ".factory" / "runs"
    store = RunStore(runs, clock=lambda: 100)

    def address(label: str) -> str:
        return "sha256:" + hashlib.sha256(f"{run_id}:{label}".encode()).hexdigest()

    resolution_request: dict[str, object] = {
        "schema_version": "factory-target-resolution-request/1",
        "request_id": f"{run_id}-resolution",
        "run_id": run_id,
        "repository_id": "factory-test",
        "generation": 1,
        "target_manifest_digest": manifest.content_digest,
        "target_manifest_source_digest": manifest.source_digest,
        "normalized_url": normalize_repository_url(str(manifest.repo["url"])),
        "requested_ref": str(manifest.repo["ref"]),
        "subpath": normalize_subpath(str(manifest.repo.get("subpath", ""))),
        "allowed_contact_operations": ["git-local-object-read"],
        "lane_execution": False,
        "nonce": f"{run_id}-resolution-nonce",
        "created_at": 50,
        "expires_at": 200,
    }
    store.create(
        run_id,
        target_digest=manifest.content_digest,
        actor="validator",
        artifact_digests={
            "target-manifest-source": manifest.source_digest,
            "target-resolution-request": digest_obj(resolution_request),
            "target-resolution-receipt": address("resolution-receipt"),
            "authority-genesis": address("genesis"),
        },
        payload={"authority_receipt_nonces": [f"{run_id}-resolution-nonce"]},
    )
    resolver = TargetResolver(
        runs / run_id,
        run_id,
        repository_id="factory-test",
        generation=1,
        clock=lambda: 100,
    )
    target_state = resolver.resolve(
        manifest=manifest,
        request=resolution_request,
        object_source=operator,
    )
    target_evidence = runs / run_id / "evidence" / "target-resolution"
    target_evidence.mkdir(parents=True)
    (target_evidence / "target-state.json").write_text(
        json.dumps(target_state, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    store.record_target_state(
        run_id,
        target_state=target_state,
        actor="target-resolver",
        artifact_digests={"resource-ledger": str(target_state["resource_ledger_head"])},
    )
    source_digest = digest_bytes(task.encode())
    execution_request: dict[str, object] = {
        "schema_version": "factory-execution-request/1",
        "request_id": f"{run_id}-execution",
        "run_id": run_id,
        "repository_id": "factory-test",
        "generation": 1,
        "target_manifest_digest": manifest.content_digest,
        "target_state_digest": digest_obj(target_state),
        "resolved_commit": target_state["resolved_commit"],
        "proposed_by": "human:test",
        "verbatim_request": task,
        "verbatim_request_digest": source_digest,
        "requested_outcome": "The authorized behavior works.",
        "surfaces": [
            {
                "surface_id": "fixture",
                "proposed_criticality": "critical",
                "reason": "The fixture exercises the executable boundary.",
            }
        ],
        "created_at": 75,
    }
    intake = runs / run_id / "evidence" / "intake"
    intake.mkdir(parents=True)
    (intake / "execution-request.json").write_text(
        json.dumps(execution_request, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    projection = store.authorize_intake(
        run_id,
        source_digest=source_digest,
        actor="validator",
        artifact_digests={
            "execution-request": digest_obj(execution_request),
            "execution-receipt": address("execution-receipt"),
            "authority-genesis": address("genesis"),
        },
        payload={"authority_receipt_nonces": [f"{run_id}-execution-nonce"]},
        approver_identity="human:test",
    )
    root = runs / run_id
    if terminal_resources:
        ledger = ResourceLedger(root, run_id, clock=lambda: 101)
        for resource_id in ("target-objects", "target-source"):
            prior = ledger.latest()[resource_id]
            ledger.append(
                generation=projection.generation,
                resource_id=resource_id,
                resource_type=str(prior["resource_type"]),
                identifier=str(prior["identifier"]),
                creator_action=str(prior["creator_action"]),
                ownership=str(prior["ownership"]),
                baseline=dict(prior["baseline"]),
                disposition={"reason": "retained fixture evidence", "residue": True},
                status="retained",
                evidence_digests={},
                actor="fixture",
            )
    if harness_status is not None:
        (root / "TASK.md").write_text(task, encoding="utf-8")
        (root / "grounded").write_text("2026-08-15T00:00:00Z\n", encoding="utf-8")
        (root / "harness.json").write_text(
            json.dumps(
                {
                    "schema_version": "factory-harness/2",
                    "run_id": run_id,
                    "status": harness_status,
                    "task_digest": source_digest,
                    "target_state_digest": projection.target_state_digest,
                    "target_manifest_digest": projection.target_digest,
                    "resolved_commit": target_state["resolved_commit"],
                    "checkout_id": target_state["checkout_id"],
                    "budget_usd": None,
                    "budget_enforcement": "UNQUALIFIED_PR2",
                    "audit_interval_min": 45,
                    "promise_window_min": 10,
                    "validator_agent": "codex",
                    "orchestrator_agent": "agy",
                    "interactive_validator_boundary": "operator-owned-tmux",
                    "validator_contract": "docs/VALIDATION-DIRECTIVE.md + /validate",
                    "launcher_qualification": "UNQUALIFIED_PR2",
                    "lane_isolation": "UNQUALIFIED_PR2",
                    "created_at": "2026-08-15T00:00:00+00:00",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return operator, root, target_state


def test_coder_projection_excludes_declared_paths_and_history(tmp_path: Path) -> None:
    src = projection_fixture(tmp_path)
    conf = src / ".factory" / "projection.conf"
    conf.parent.mkdir()
    conf.write_text("coder-exclude: tests\n")
    subprocess.run(["git", "add", ".factory/projection.conf"], cwd=src, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "config"],
        cwd=src,
        check=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True, check=True
    ).stdout.strip()
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
    conf = src / ".factory" / "projection.conf"
    conf.parent.mkdir()
    conf.write_text("tester-include: impl.py\n")
    subprocess.run(["git", "add", ".factory/projection.conf"], cwd=src, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "config"],
        cwd=src,
        check=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True, check=True
    ).stdout.strip()
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


def test_projection_refuses_config_outside_immutable_source(tmp_path: Path) -> None:
    src = projection_fixture(tmp_path)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True, check=True
    ).stdout.strip()
    outside = tmp_path / "outside.conf"
    outside.write_text("coder-exclude: tests\n", encoding="utf-8")

    result = run(
        ["bash", str(HARNESS / "projection.sh"), "coder", str(src), sha, str(tmp_path / "ws")],
        tmp_path,
        {"HARNESS_PROJECTION_CONF": str(outside)},
    )

    assert result.returncode != 0
    assert "escapes the immutable source root" in result.stderr
    assert not (tmp_path / "ws").exists()


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
    assert "--repo is forbidden" in r.stderr and "Stage R/E" in r.stderr


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
    assert r.returncode == 64 and "--repo is forbidden" in r.stderr


def test_factory_refuses_a_git_target_without_an_operational_abi(tmp_path: Path) -> None:
    repo = projection_fixture(tmp_path)
    r = run(
        ["bash", str(HARNESS / "factory.sh"), "runx", "some task", "--repo", str(repo)],
        tmp_path,
        {},
    )
    assert r.returncode == 64
    assert "--repo is forbidden" in r.stderr


def factory_ignition_env(tmp_path: Path, root: Path) -> tuple[dict[str, str], Path]:
    stub = tmp_path / "factory-bin"
    stub.mkdir()
    log = tmp_path / "tmux.log"
    tmux = stub / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = has-session ]; then exit 1; fi\n'
        f'printf \'%s\\n\' "$*" >> "{log!s}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    timers = tmp_path / "timers.txt"
    timers.write_text("", encoding="utf-8")
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    directives = tmp_path / "DIRECTIVES"
    directives.mkdir()
    (directives / "ledger.jsonl").write_bytes(b"")
    (directives / "provisional.jsonl").write_bytes(b"")
    return (
        {
            "PATH": f"{stub}:{os.environ['PATH']}",
            "FACTORY_CLI": f"{sys.executable} -m factory_runtime.cli",
            "FACTORY_RUNS_DIR": str(root.parent),
            "SCHED_AUDIT_INPUT": str(timers),
            "TRANSCRIPTS": str(transcripts),
            "DIRECTIVE_LEDGER": str(tmp_path / "DIRECTIVES" / "ledger.jsonl"),
        },
        log,
    )


def test_factory_ignition_consumes_exact_stage_e_target_and_task(tmp_path: Path) -> None:
    task = "Build the exact authorized behavior."
    operator, root, target = execution_truth_fixture(
        tmp_path,
        task=task,
        harness_status=None,
    )
    env, tmux_log = factory_ignition_env(tmp_path, root)

    result = run(
        [
            "bash",
            str(HARNESS / "factory.sh"),
            "r1",
            task,
            "--runs",
            str(root.parent),
        ],
        operator,
        env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    harness = json.loads((root / "harness.json").read_text())
    runtime = json.loads((root / "run.json").read_text())
    assert harness["target_state_digest"] == runtime["target_state_digest"]
    assert harness["resolved_commit"] == target["resolved_commit"]
    assert harness["validator_agent"] == "codex"
    assert harness["orchestrator_agent"] == "agy"
    assert (root / "TASK.md").read_text() == task
    assert "repo" not in harness and "base_sha" not in harness
    assert str(target["workdir"]) in tmux_log.read_text()
    assert str(operator) not in tmux_log.read_text()
    resources = ResourceLedger(root, "r1").latest()
    assert resources["tmux-session"]["status"] == "active"


def test_factory_task_mismatch_has_zero_tmux_or_harness_mutation(tmp_path: Path) -> None:
    _, root, _ = execution_truth_fixture(
        tmp_path,
        task="Authorized request",
        harness_status=None,
    )
    env, tmux_log = factory_ignition_env(tmp_path, root)

    result = run(
        [
            "bash",
            str(HARNESS / "factory.sh"),
            "r1",
            "Different request",
            "--runs",
            str(root.parent),
        ],
        tmp_path,
        env,
    )

    assert result.returncode != 0
    assert "differ" in result.stderr
    assert not (root / "harness.json").exists()
    assert not (root / "TASK.md").exists()
    assert not tmux_log.exists()
    assert "tmux-session" not in ResourceLedger(root, "r1").latest()


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


def load_attention_gate() -> object:
    import importlib.util

    spec = importlib.util.spec_from_file_location("attention_gate", HARNESS / "attention_gate.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def append_blockers(root: Path, lane: str, *events: Mapping[str, object]) -> Path:
    """Publish test blockers through the production receipt-bearing writer."""

    root.mkdir(parents=True, exist_ok=True)
    mod = load_attention_gate()
    for event in events:
        mod.append_blocking_event(root, lane, event)  # type: ignore[attr-defined]
    return root / "lanes" / f"{lane}.blocking"


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
    root = tmp / ".factory" / "runs" / "r1"
    return run(
        [
            "bash",
            str(HARNESS / "phase1_gate.sh"),
            "r1",
            "--root",
            str(root),
            "--workdir",
            str(tmp),
        ],
        cwd=tmp,
        env_extra=env or None,
    )


def test_phase1_gate_passes_on_adequate_artifacts(tmp_path: Path) -> None:
    r = p1(mkrun(tmp_path, ADEQUATE_SPEC, ADEQUATE_STRAT))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "phase1 gate: clean" in r.stdout
    # GO sibling for the refusal instrumentation: a clean gate emits nothing.
    assert _refusal_events(tmp_path / ".factory" / "runs" / "r1") == []


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
    # Phase 0.1 forcing: exactly one registered refusal event, UTC host ts.
    rows = _refusal_events(tmp_path / ".factory" / "runs" / "r1")
    assert len(rows) == 1
    assert rows[0]["kind"] == "refusal-phase1-gate"
    assert rows[0]["exit_code"] == 71
    assert str(rows[0]["ts"]).endswith("+00:00")


def test_phase1_gate_ignores_ambient_gap_override_and_still_refuses(tmp_path: Path) -> None:
    """An environment variable cannot convert an inadequate phase into authority."""
    tmp = mkrun(tmp_path, ADEQUATE_SPEC, ADEQUATE_STRAT, contract=False)
    r = p1(tmp, PHASE1_ALLOW_GAPS="1")
    assert r.returncode == 71
    # The override changes nothing, and the refusal now leaves its signal
    # (Phase 0.1): exactly one registered refusal event — never a permission.
    rows = _refusal_events(tmp / ".factory" / "runs" / "r1")
    assert [row["kind"] for row in rows] == ["refusal-phase1-gate"]
    assert "Fix and re-ratify" in r.stdout


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


def test_projection_receipt_refuses_unsafe_include_path(tmp_path: Path) -> None:
    mkproj(tmp_path, "../outside")
    art = tmp_path / "s.md"
    art.write_text("Cases land in tests/test_boundary.py.\n")

    r = pr(tmp_path, "tester", art)

    assert r.returncode != 0
    assert "unsafe tester-include path" in r.stderr


def test_projection_receipt_refuses_symlinked_config(tmp_path: Path) -> None:
    outside = tmp_path / "outside.conf"
    outside.write_text("tester-include: tests\n")
    config_dir = tmp_path / ".factory"
    config_dir.mkdir()
    (config_dir / "projection.conf").symlink_to(outside)
    art = tmp_path / "s.md"
    art.write_text("Cases land in tests/test_boundary.py.\n")

    r = pr(tmp_path, "tester", art)

    assert r.returncode == 66
    assert "regular non-symlink" in r.stderr


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
    (root / "harness.json").write_text(
        json.dumps({"status": "open", "orchestrator_agent": "codex"})
    )
    # PATH without any agent binary: the invocation cannot succeed.
    r = run(
        ["bash", str(HARNESS / "orchestrator_wake.sh"), "r1", '{"kind":"drill"}'],
        cwd=tmp_path,
        env_extra={"PATH": "/usr/bin:/bin", "ORCH_AGENT": "codex"},
    )
    receipts = (root / "wakes" / "receipts.jsonl").read_text()
    assert '"status":"did-not-run"' in receipts, (
        "a failed invocation must be recorded as a dead wake, not written out as an audit"
    )
    assert not list((root / "wakes").glob("*.response.md"))
    assert len(list((root / "wakes").glob("*.failure.md"))) == 1
    assert "ORCHESTRATOR DID NOT RUN" in r.stderr


def test_orchestrator_defaults_to_sandboxed_antigravity_with_bounded_projection(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".factory" / "runs" / "r1"
    (root / "wakes").mkdir(parents=True)
    (root / "artifacts").mkdir(parents=True)
    (root / "run.json").write_text(
        json.dumps({"run": "r1", "repo": str(tmp_path), "base_sha": "abc"})
    )
    (root / "TASK.md").write_text("task-" + ("t" * 60_000) + "\n")
    (root / "harness.json").write_text(
        json.dumps({"status": "open", "orchestrator_agent": "agy"})
    )
    (root / "events.jsonl").write_text(
        json.dumps({"padding": "e" * 59_000}, separators=(",", ":")) + "\n"
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    agy = binary / "agy"
    agy.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['FACTORY_TEST_AGY_LOG']).write_text(json.dumps(sys.argv[1:]))\n"
        "pathlib.Path(os.environ['FACTORY_TEST_AGY_INPUT']).write_bytes(sys.stdin.buffer.read())\n"
        "for path in pathlib.Path(os.environ['FACTORY_TEST_WAKE_ROOT']).glob("
        "'*.agy-input.jsonl'):\n"
        "    path.write_text('mutated after supervisor admission\\n')\n"
        "print(json.dumps({'event': 'init', 'init': {'tools': []}}))\n"
        "print(json.dumps({'event': 'result', 'result': {"
        "'status': 'SUCCESS', 'response': 'No process \\\"drift\\\" found.\\nSecond line.\\n'}}))\n"
    )
    agy.chmod(0o755)
    log = tmp_path / "agy.log"
    input_log = tmp_path / "agy-input.jsonl"
    directive = dl(
        tmp_path,
        "append",
        "--scope",
        "run",
        "--text",
        "project this exact directive",
    )
    assert directive.returncode == 0, directive.stderr
    directive_ledger = tmp_path / "DIRECTIVES" / "ledger.jsonl"
    (root / "minutes").mkdir()
    (root / "minutes" / "validator-2026-08-18.log").write_text(
        "one\ntwo\n"
        + "".join(f"minute-{index}-{'m' * 1_450}\n" for index in range(38)),
        encoding="utf-8",
    )

    result = run(
        ["bash", str(HARNESS / "orchestrator_wake.sh"), "r1", '{"kind":"drill"}'],
        cwd=tmp_path,
        env_extra={
            "PATH": f"{binary}:/usr/bin:/bin",
            "FACTORY_TEST_AGY_LOG": str(log),
            "FACTORY_TEST_AGY_INPUT": str(input_log),
            "FACTORY_TEST_WAKE_ROOT": str(root / "wakes"),
            "FACTORY_TEST_DIRECTIVE_LEDGER_SOURCE": str(directive_ledger),
            "DIRECTIVE_LEDGER": str(tmp_path / "ambient-forged-ledger.jsonl"),
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    invocation = json.loads(log.read_text())
    assert invocation[:4] == ["--sandbox", "--disable-slash-commands", "-p", ""]
    assert invocation[4:] == ["--input-format", "stream-json", "--output-format", "stream-json"]
    assert "--mode" not in invocation
    assert "dangerously-skip-permissions" not in invocation
    assert max(map(len, invocation)) < 128
    wire = json.loads(input_log.read_text())
    assert wire["type"] == "user"
    assert wire["message"]["role"] == "user"
    assert "STRUCTURED PROJECTION:" in wire["message"]["content"]
    projections = list((root / "wakes").glob("*.projection.json"))
    capsules = list((root / "wakes").glob("*.state-capsule.json"))
    prompts = list((root / "wakes").glob("*.prompt.txt"))
    assert len(projections) == 1 and len(capsules) == 1 and len(prompts) == 1
    assert "/Users/" not in projections[0].read_text()
    projected = json.loads(projections[0].read_text())
    active_directives = next(
        section for section in projected["sections"]
        if section["section_id"] == "active-directives"
    )
    assert "project this exact directive" in active_directives["content"]
    assert "ambient-forged" not in active_directives["content"]
    minutes = next(
        section for section in projected["sections"]
        if section["section_id"] == "minutes-tail"
    )
    assert "[validator-2026-08-18.log] one" in minutes["content"]
    wake_receipt = json.loads(
        (root / "wakes" / "receipts.jsonl").read_text().splitlines()[0]
    )
    assert wake_receipt["agent"] == "agy"
    assert wake_receipt["schema_version"] == "factory-orchestrator-wake-receipt/4"
    assert wake_receipt["status"] == "projection-prepared"
    assert (
        wake_receipt["sandbox_enforcement"]
        == "cli-declared-not-independently-qualified"
    )
    completed_receipt = json.loads(
        (root / "wakes" / "receipts.jsonl").read_text().splitlines()[1]
    )
    assert completed_receipt["status"] == "completed"
    assert completed_receipt["schema_version"] == "factory-orchestrator-wake-receipt/4"
    assert completed_receipt["exit_code"] == 0
    assert completed_receipt["prompt_schema_version"] == "factory-orchestrator-prompt/1"
    assert (
        completed_receipt["prompt_assembler_version"]
        == "factory-orchestrator-prompt-assembler/2"
    )
    assert completed_receipt["prompt_id"] == prompts[0].name
    assert completed_receipt["prompt_byte_count"] == len(prompts[0].read_bytes())
    assert completed_receipt["prompt_digest"] == digest_bytes(prompts[0].read_bytes())
    assert completed_receipt["prompt_bytes_retained"] is True
    assert completed_receipt["prompt_byte_count"] > 131_072
    client_inputs = list((root / "wakes").glob("*.presented-input"))
    assert len(client_inputs) == 1
    assert completed_receipt["client_input_id"] == client_inputs[0].name
    assert completed_receipt["client_input_transport"] == "agy-stream-json-stdin"
    assert completed_receipt["client_input_descriptor_mode"] == "read-only"
    assert completed_receipt["client_input_digest"] == digest_bytes(client_inputs[0].read_bytes())
    assert completed_receipt["client_input_byte_count"] == len(client_inputs[0].read_bytes())
    assert completed_receipt["client_input_bytes_retained"] is True
    assert client_inputs[0].read_bytes() == input_log.read_bytes()
    assert next((root / "wakes").glob("*.agy-input.jsonl")).read_text() == (
        "mutated after supervisor admission\n"
    )
    assert wire["message"]["content"].encode() == prompts[0].read_bytes()
    raw_outputs = list((root / "wakes").glob("*.client-output"))
    assert len(raw_outputs) == 1
    assert completed_receipt["raw_output_digest"] == digest_bytes(raw_outputs[0].read_bytes())
    assert completed_receipt["raw_output_truncated"] is False
    supervisor_receipts = list((root / "wakes").glob("*.supervisor-receipt.json"))
    assert len(supervisor_receipts) == 1
    supervisor_receipt = json.loads(supervisor_receipts[0].read_text())
    assert supervisor_receipt["input_digest"] == completed_receipt["client_input_digest"]
    assert completed_receipt["supervisor_receipt_digest"] == digest_bytes(
        supervisor_receipts[0].read_bytes()
    )
    responses = list((root / "wakes").glob("*.response.md"))
    assert len(responses) == 1
    assert completed_receipt["output_digest"] == digest_bytes(responses[0].read_bytes())
    assert not list((root / "wakes").glob("*.failure.md"))
    assert '"drift"' in list((root / "wakes").glob("*.response.md"))[0].read_text()
    blocking = read_chain(root / "lanes" / "validator.blocking")
    assert blocking[-1]["trust_class"] == "untrusted-advisory"
    assert blocking[-1]["effect_route"] == "validator-blocking-only"
    for line in (root / "events.jsonl").read_text().splitlines():
        json.loads(line)


def test_orchestrator_rejects_malformed_agy_terminal_stream(tmp_path: Path) -> None:
    root = tmp_path / ".factory" / "runs" / "r1"
    (root / "wakes").mkdir(parents=True)
    (root / "artifacts").mkdir(parents=True)
    (root / "run.json").write_text(
        json.dumps({"run": "r1", "repo": str(tmp_path), "base_sha": "abc"})
    )
    (root / "TASK.md").write_text("task\n")
    (root / "harness.json").write_text(
        json.dumps({"status": "open", "orchestrator_agent": "agy"})
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    agy = binary / "agy"
    agy.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stdin.buffer.read()\n"
        "print('plausible prose without a terminal result')\n"
    )
    agy.chmod(0o755)
    directive = dl(tmp_path, "append", "--scope", "run", "--text", "audit the run")
    assert directive.returncode == 0, directive.stderr

    result = run(
        ["bash", str(HARNESS / "orchestrator_wake.sh"), "r1", '{"kind":"drill"}'],
        cwd=tmp_path,
        env_extra={
            "PATH": f"{binary}:/usr/bin:/bin",
            "DIRECTIVE_LEDGER": str(tmp_path / "DIRECTIVES" / "ledger.jsonl"),
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    receipts = read_chain(root / "wakes" / "receipts.jsonl")
    assert receipts[-1]["status"] == "did-not-run"
    assert receipts[-1]["exit_code"] == 0
    assert not list((root / "wakes").glob("*.response.md"))
    failure = next((root / "wakes").glob("*.failure.md")).read_text()
    assert "client output invalid" in failure
    assert "plausible prose" in failure


def test_orchestrator_receipts_live_supervisor_output_truncation(tmp_path: Path) -> None:
    root = tmp_path / ".factory" / "runs" / "r1"
    (root / "wakes").mkdir(parents=True)
    (root / "artifacts").mkdir(parents=True)
    (root / "run.json").write_text(
        json.dumps({"run": "r1", "repo": str(tmp_path), "base_sha": "abc"})
    )
    (root / "TASK.md").write_text("task\n")
    (root / "harness.json").write_text(
        json.dumps({"status": "open", "orchestrator_agent": "agy"})
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    agy = binary / "agy"
    agy.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "sys.stdin.buffer.read()\n"
        "os.write(sys.stdout.fileno(), b'x' * 131072)\n"
    )
    agy.chmod(0o755)
    directive = dl(tmp_path, "append", "--scope", "run", "--text", "audit the run")
    assert directive.returncode == 0, directive.stderr

    result = run(
        ["bash", str(HARNESS / "orchestrator_wake.sh"), "r1", '{"kind":"drill"}'],
        cwd=tmp_path,
        env_extra={
            "PATH": f"{binary}:/usr/bin:/bin",
            "DIRECTIVE_LEDGER": str(tmp_path / "DIRECTIVES" / "ledger.jsonl"),
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    receipt = read_chain(root / "wakes" / "receipts.jsonl")[-1]
    assert receipt["status"] == "did-not-run"
    assert receipt["exit_code"] == 74
    assert receipt["raw_output_truncated"] is True
    supervisor_receipt = json.loads(
        next((root / "wakes").glob("*.supervisor-receipt.json")).read_text()
    )
    assert supervisor_receipt["combined_output_truncated"] is True
    assert receipt["supervisor_receipt_digest"] == digest_bytes(
        next((root / "wakes").glob("*.supervisor-receipt.json")).read_bytes()
    )


def test_orchestrator_tails_mature_append_only_logs_without_disabling_wake(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".factory" / "runs" / "r1"
    (root / "wakes").mkdir(parents=True)
    (root / "artifacts").mkdir(parents=True)
    (root / "minutes").mkdir(parents=True)
    receipts = tmp_path / ".factory" / "receipts"
    receipts.mkdir(parents=True)
    (root / "run.json").write_text(
        json.dumps({"run": "r1", "repo": str(tmp_path), "base_sha": "abc"})
    )
    (root / "TASK.md").write_text("task\n")
    (root / "harness.json").write_text(
        json.dumps({"status": "open", "orchestrator_agent": "agy"})
    )
    (receipts / "chain.jsonl").write_text(
        "".join(f'{{"receipt":{index}}}\n' for index in range(12_000))
        + '{"receipt":"receipt-final"}\n'
    )
    (root / "events.jsonl").write_text(
        "".join(f'{{"event":{index}}}\n' for index in range(12_000))
        + ("é" * 40_000)
        + "\n"
        + '{"event":"event-final"}\n'
        + '{"event":"unterminated-fragment"'
    )
    (root / "minutes" / "validator.log").write_text(
        "".join(f"minute-{index}\n" for index in range(16_000)) + "minutes-final\n"
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    agy = binary / "agy"
    agy.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "json.loads(sys.stdin.buffer.read())\n"
        "print(json.dumps({'event': 'result', 'result': {"
        "'status': 'SUCCESS', 'response': 'Run remains aligned.\\n'}}))\n"
    )
    agy.chmod(0o755)
    directive = dl(
        tmp_path,
        "append",
        "--scope",
        "run",
        "--text",
        "retain the bounded tail",
    )
    assert directive.returncode == 0, directive.stderr

    result = run(
        ["bash", str(HARNESS / "orchestrator_wake.sh"), "r1", '{"kind":"drill"}'],
        cwd=tmp_path,
        env_extra={
            "PATH": f"{binary}:/usr/bin:/bin",
            "DIRECTIVE_LEDGER": str(tmp_path / "DIRECTIVES" / "ledger.jsonl"),
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    projection = json.loads(next((root / "wakes").glob("*.projection.json")).read_text())
    sections = {section["section_id"]: section["content"] for section in projection["sections"]}
    assert "receipt-final" in sections["receipt-tail"]
    assert "event-final" in sections["event-tail"]
    assert "unterminated-fragment" not in sections["event-tail"]
    assert "omitted earlier, oversized, or invalid record" in sections["event-tail"]
    assert "minutes-final" in sections["minutes-tail"]
    assert all(len(sections[name].encode()) <= 65_536 for name in (
        "receipt-tail",
        "event-tail",
        "minutes-tail",
    ))


def test_advisory_supervisor_kills_noisy_process_at_output_ceiling(tmp_path: Path) -> None:
    noisy = tmp_path / "noisy.py"
    noisy.write_text(
        "import os, sys\n"
        "while True:\n"
        "    os.write(sys.stdout.fileno(), b'x' * 8192)\n"
    )
    prompt = tmp_path / "prompt"
    prompt.write_text("audit\n")
    stdout = tmp_path / "stdout"
    stderr = tmp_path / "stderr"
    input_snapshot = tmp_path / "input-snapshot"
    receipt = tmp_path / "receipt"

    result = subprocess.run(
        [
            sys.executable,
            str(HARNESS / "supervise_advisory.py"),
            "--cwd",
            str(tmp_path),
            "--stdin",
            str(prompt),
            "--stdout",
            str(stdout),
            "--stderr",
            str(stderr),
            "--input-snapshot",
            str(input_snapshot),
            "--receipt",
            str(receipt),
            "--wall-seconds",
            "5",
            "--max-input-bytes",
            "1048576",
            "--max-output-bytes",
            "4096",
            "--",
            sys.executable,
            str(noisy),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 74, result.stdout + result.stderr
    assert len(stdout.read_bytes()) + len(stderr.read_bytes()) <= 4096
    assert json.loads(receipt.read_text())["combined_output_truncated"] is True


def test_advisory_supervisor_drains_bytes_written_before_signal_termination(
    tmp_path: Path,
) -> None:
    payload = b"x" * 61_440
    writer = tmp_path / "writer.py"
    writer.write_text(
        "import os, signal, sys, time\n"
        f"payload = {payload!r}\n"
        "written = 0\n"
        "while written < len(payload):\n"
        "    written += os.write(sys.stdout.fileno(), payload[written:])\n"
        "os.kill(os.getppid(), signal.SIGTERM)\n"
        "time.sleep(60)\n"
    )
    prompt = tmp_path / "prompt"
    prompt.write_text("audit\n")
    stdout = tmp_path / "stdout"
    stderr = tmp_path / "stderr"
    input_snapshot = tmp_path / "input-snapshot"
    receipt = tmp_path / "receipt"

    result = subprocess.run(
        [
            sys.executable,
            str(HARNESS / "supervise_advisory.py"),
            "--cwd",
            str(tmp_path),
            "--stdin",
            str(prompt),
            "--stdout",
            str(stdout),
            "--stderr",
            str(stderr),
            "--input-snapshot",
            str(input_snapshot),
            "--receipt",
            str(receipt),
            "--wall-seconds",
            "5",
            "--max-input-bytes",
            "1048576",
            "--max-output-bytes",
            "65536",
            "--",
            sys.executable,
            str(writer),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 124, result.stdout + result.stderr
    assert stdout.read_bytes() == payload
    supervisor_receipt = json.loads(receipt.read_text())
    assert supervisor_receipt["stdout_byte_count"] == len(payload)
    assert supervisor_receipt["combined_output_truncated"] is False


def test_advisory_supervisor_kills_term_tolerant_child_after_parent_exits(
    tmp_path: Path,
) -> None:
    child_pid = tmp_path / "child.pid"
    child = tmp_path / "child.py"
    child.write_text(
        "import os, pathlib, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"pathlib.Path({str(child_pid)!r}).write_text(str(os.getpid()))\n"
        "os.close(1); os.close(2)\n"
        "while True: time.sleep(1)\n"
    )
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess, sys\n"
        f"subprocess.Popen([sys.executable, {str(child)!r}])\n"
    )
    prompt = tmp_path / "prompt"
    prompt.write_text("audit\n")
    stdout = tmp_path / "stdout"
    stderr = tmp_path / "stderr"
    input_snapshot = tmp_path / "input-snapshot"
    receipt = tmp_path / "receipt"

    result = subprocess.run(
        [
            sys.executable,
            str(HARNESS / "supervise_advisory.py"),
            "--cwd",
            str(tmp_path),
            "--stdin",
            str(prompt),
            "--stdout",
            str(stdout),
            "--stderr",
            str(stderr),
            "--input-snapshot",
            str(input_snapshot),
            "--receipt",
            str(receipt),
            "--wall-seconds",
            "0.5",
            "--max-input-bytes",
            "1048576",
            "--max-output-bytes",
            "4096",
            "--",
            sys.executable,
            str(parent),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 124, result.stdout + result.stderr
    pid = int(child_pid.read_text())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    else:
        os.kill(pid, signal.SIGKILL)
        raise AssertionError(f"TERM-tolerant advisory child {pid} survived supervisor")


def test_advisory_supervisor_reports_rather_than_refuses_when_teardown_is_denied(
    tmp_path: Path,
) -> None:
    """EPERM from killpg must not overwrite an established termination verdict.

    This is the forcing test for the 2026-08-28 flake: `_signal_group` caught only
    ESRCH, so a denied group signal escaped as an OSError, was caught by the refusal
    wrapper, and turned an exit-124 wall-ceiling termination into an exit-70
    "supervisor refused". A cleanup failure was overwriting the verdict that had
    already been decided — a true local statement erasing a true global one.

    EPERM is also not success: it means the group is no longer ours to signal, whose
    likeliest cause is leader-pid reuse. So the run must still report 124 *and* the
    receipt must say the teardown was unverified. Both halves are asserted, because
    silently swallowing the error would pass a test that only checked the exit code.
    """

    import harness.supervise_advisory as supervisor

    denied: list[int] = []
    real_killpg = os.killpg

    def denying_killpg(pgid: int, signum: int) -> None:
        # Deny only the follow-up KILL, which is the real shape of the flake: the
        # leader has already exited, its pid has been reused by another owner, and the
        # group we reach for is no longer ours. The principal is genuinely dead; only
        # the proof of teardown is missing. Denying SIGTERM too would model a different
        # and legitimately fatal case — a principal that truly cannot be killed, which
        # the supervisor is right to refuse over.
        if signum == signal.SIGKILL:
            denied.append(signum)
            raise PermissionError(1, "Operation not permitted")
        return real_killpg(pgid, signum)

    child = tmp_path / "child.py"
    child.write_text("import time\nwhile True: time.sleep(1)\n")
    prompt = tmp_path / "prompt"
    prompt.write_text("audit\n")
    receipt = tmp_path / "receipt"

    original = supervisor.os.killpg
    supervisor.os.killpg = denying_killpg  # type: ignore[assignment]
    try:
        code = supervisor.supervise(
            [sys.executable, str(child)],
            cwd=tmp_path,
            stdin_path=prompt,
            stdout_path=tmp_path / "stdout",
            stderr_path=tmp_path / "stderr",
            input_snapshot_path=tmp_path / "input-snapshot",
            receipt_path=receipt,
            stdin_mode="prompt",
            wall_seconds=0.5,
            max_input_bytes=1048576,
            max_output_bytes=4096,
        )
    finally:
        supervisor.os.killpg = original  # type: ignore[assignment]

    assert denied, "the test did not actually exercise the denied-signal path"
    assert code == 124, "a denied teardown must not erase the wall-ceiling verdict"
    body = json.loads(receipt.read_text())
    assert body["termination_reason"] == "advisory wall-time ceiling exceeded"
    assert body["teardown_verified"] is False
    assert any(c.startswith("group-signal-denied:") for c in body["teardown_conditions"])


def test_advisory_supervisor_receipt_reports_a_verified_teardown_on_the_clean_path(
    tmp_path: Path,
) -> None:
    """The negative half: an ordinary ceiling termination reports teardown_verified.

    Without this, `teardown_verified: False` could be hardcoded and the test above
    would still pass — the disposition has to discriminate to be worth emitting.
    """

    import harness.supervise_advisory as supervisor

    child = tmp_path / "child.py"
    child.write_text("import time\nwhile True: time.sleep(1)\n")
    prompt = tmp_path / "prompt"
    prompt.write_text("audit\n")
    receipt = tmp_path / "receipt"

    code = supervisor.supervise(
        [sys.executable, str(child)],
        cwd=tmp_path,
        stdin_path=prompt,
        stdout_path=tmp_path / "stdout",
        stderr_path=tmp_path / "stderr",
        input_snapshot_path=tmp_path / "input-snapshot",
        receipt_path=receipt,
        stdin_mode="prompt",
        wall_seconds=0.5,
        max_input_bytes=1048576,
        max_output_bytes=4096,
    )
    assert code == 124
    body = json.loads(receipt.read_text())
    assert body["teardown_verified"] is True
    assert body["teardown_conditions"] == []


def test_advisory_supervisor_can_close_stdin_for_argv_prompt_clients(tmp_path: Path) -> None:
    reader = tmp_path / "reader.py"
    reader.write_text("import sys\nprint(len(sys.stdin.buffer.read()))\n")
    prompt = tmp_path / "prompt"
    prompt.write_text("must not be duplicated on stdin")
    stdout = tmp_path / "stdout"
    stderr = tmp_path / "stderr"
    input_snapshot = tmp_path / "input-snapshot"
    receipt = tmp_path / "receipt"

    result = subprocess.run(
        [
            sys.executable,
            str(HARNESS / "supervise_advisory.py"),
            "--cwd",
            str(tmp_path),
            "--stdin",
            str(prompt),
            "--stdin-mode",
            "closed",
            "--stdout",
            str(stdout),
            "--stderr",
            str(stderr),
            "--input-snapshot",
            str(input_snapshot),
            "--receipt",
            str(receipt),
            "--wall-seconds",
            "5",
            "--max-input-bytes",
            "1048576",
            "--max-output-bytes",
            "4096",
            "--",
            sys.executable,
            str(reader),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert stdout.read_text() == "0\n"
    assert stderr.read_bytes() == b""


def test_advisory_supervisor_presents_and_receipts_one_immutable_input_snapshot(
    tmp_path: Path,
) -> None:
    ready = tmp_path / "ready"
    received = tmp_path / "received"
    reader = tmp_path / "delayed_reader.py"
    reader.write_text(
        "import os, pathlib, sys, time\n"
        f"pathlib.Path({str(ready)!r}).write_text('ready')\n"
        "time.sleep(0.3)\n"
        "try:\n"
        "    os.write(sys.stdin.fileno(), b'client mutation')\n"
        "except OSError:\n"
        "    print('stdin-read-only')\n"
        "else:\n"
        "    raise SystemExit('client could write its admitted stdin snapshot')\n"
        f"pathlib.Path({str(received)!r}).write_bytes(sys.stdin.buffer.read())\n"
        "print('done')\n"
    )
    original = b"the exact admitted input\n"
    prompt = tmp_path / "prompt"
    prompt.write_bytes(original)
    stdout = tmp_path / "stdout"
    stderr = tmp_path / "stderr"
    input_snapshot = tmp_path / "input-snapshot"
    receipt = tmp_path / "receipt"
    process = subprocess.Popen(
        [
            sys.executable,
            str(HARNESS / "supervise_advisory.py"),
            "--cwd",
            str(tmp_path),
            "--stdin",
            str(prompt),
            "--stdout",
            str(stdout),
            "--stderr",
            str(stderr),
            "--input-snapshot",
            str(input_snapshot),
            "--receipt",
            str(receipt),
            "--wall-seconds",
            "5",
            "--max-input-bytes",
            "1048576",
            "--max-output-bytes",
            "4096",
            "--",
            sys.executable,
            str(reader),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    for _ in range(100):
        if ready.exists():
            break
        time.sleep(0.01)
    assert ready.exists(), "client did not start after the supervisor admitted stdin"
    prompt.write_text("mutated after client launch\n")
    process_stdout, process_stderr = process.communicate(timeout=10)

    assert process.returncode == 0, process_stdout + process_stderr
    assert "stdin-read-only" in stdout.read_text()
    assert received.read_bytes() == original
    supervisor_receipt = json.loads(receipt.read_text())
    assert supervisor_receipt["input_digest"] == digest_bytes(original)
    assert supervisor_receipt["input_byte_count"] == len(original)
    assert supervisor_receipt["input_admitted"] is True
    assert supervisor_receipt["input_descriptor_mode"] == "read-only"
    assert input_snapshot.read_bytes() == original


def test_orchestrator_refuses_unbounded_minutes_file_enumeration(tmp_path: Path) -> None:
    root = tmp_path / ".factory" / "runs" / "r1"
    (root / "wakes").mkdir(parents=True)
    (root / "artifacts").mkdir(parents=True)
    minutes = root / "minutes"
    minutes.mkdir(parents=True)
    (root / "run.json").write_text(
        json.dumps({"run": "r1", "repo": str(tmp_path), "base_sha": "abc"})
    )
    (root / "TASK.md").write_text("task\n")
    (root / "harness.json").write_text(
        json.dumps({"status": "open", "orchestrator_agent": "agy"})
    )
    for index in range(65):
        (minutes / f"{index:02d}.log").write_text("minute\n")

    result = run(
        ["bash", str(HARNESS / "orchestrator_wake.sh"), "r1", '{"kind":"drill"}'],
        cwd=tmp_path,
    )

    assert result.returncode != 0
    assert "too many orchestrator minutes inputs" in result.stderr
    assert not list((root / "wakes").glob("*.projection.json"))


def test_advisory_supervisor_kills_descendants_at_wall_ceiling(tmp_path: Path) -> None:
    child_pid = tmp_path / "child.pid"
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"pathlib.Path({str(child_pid)!r}).write_text(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    prompt = tmp_path / "prompt"
    prompt.write_text("audit\n")
    stdout = tmp_path / "stdout"
    stderr = tmp_path / "stderr"
    input_snapshot = tmp_path / "input-snapshot"
    receipt = tmp_path / "receipt"

    result = subprocess.run(
        [
            sys.executable,
            str(HARNESS / "supervise_advisory.py"),
            "--cwd",
            str(tmp_path),
            "--stdin",
            str(prompt),
            "--stdout",
            str(stdout),
            "--stderr",
            str(stderr),
            "--input-snapshot",
            str(input_snapshot),
            "--receipt",
            str(receipt),
            "--wall-seconds",
            "0.3",
            "--max-input-bytes",
            "1048576",
            "--max-output-bytes",
            "4096",
            "--",
            sys.executable,
            str(parent),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 124, result.stdout + result.stderr
    pid = int(child_pid.read_text())
    for _ in range(20):
        status = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "stat="],
            capture_output=True,
            text=True,
        ).stdout.strip()
        if not status or status.startswith("Z"):
            break
        time.sleep(0.05)
    assert not status or status.startswith("Z")


def test_orchestrator_refuses_ambient_agent_substitution(tmp_path: Path) -> None:
    root = tmp_path / ".factory" / "runs" / "r1"
    (root / "wakes").mkdir(parents=True)
    (root / "harness.json").write_text(
        json.dumps({"status": "open", "orchestrator_agent": "agy"})
    )

    result = run(
        ["bash", str(HARNESS / "orchestrator_wake.sh"), "r1", '{"kind":"drill"}'],
        cwd=tmp_path,
        env_extra={"ORCH_AGENT": "claude"},
    )

    assert result.returncode == 72
    assert "differs from bound metadata" in result.stderr


def test_orchestrator_refuses_unsandboxed_claude_adapter(tmp_path: Path) -> None:
    root = tmp_path / ".factory" / "runs" / "r1"
    root.mkdir(parents=True)
    (root / "harness.json").write_text(
        json.dumps({"status": "open", "orchestrator_agent": "claude"})
    )

    result = run(
        ["bash", str(HARNESS / "orchestrator_wake.sh"), "r1", '{"kind":"drill"}'],
        cwd=tmp_path,
        env_extra={"ORCH_AGENT": "claude"},
    )

    assert result.returncode == 72
    assert "no valid bound orchestrator" in result.stderr


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
    (root / "harness.json").write_text(
        json.dumps({"status": "open", "orchestrator_agent": "codex"})
    )
    r = run(
        ["bash", str(HARNESS / "orchestrator_wake.sh"), "r1", '{"kind":"drill"}'],
        cwd=tmp_path,
        env_extra={"PATH": "/usr/bin:/bin", "ORCH_AGENT": "codex"},
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

    invocation: dict[str, object] = {}
    orig = mod.subprocess.Popen  # type: ignore[attr-defined]

    def fake_popen(*args: object, **kwargs: object) -> _FakeProc:
        invocation["args"] = args
        invocation["kwargs"] = kwargs
        return _FakeProc()

    mod.subprocess.Popen = fake_popen  # type: ignore[assignment]
    try:
        d.wake_orchestrator({"kind": "test"})  # type: ignore[attr-defined]
    finally:
        mod.subprocess.Popen = orig  # type: ignore[assignment]
        del os.environ["WAKE_TIMEOUT"]
    assert _Hung.killed, "a hung wake past its deadline must be killed"
    assert invocation["kwargs"]["start_new_session"] is True  # type: ignore[index]
    events = [
        json.loads(line)
        for line in (root / "events.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert any(e["kind"] == "orchestrator_dead" for e in events), (
        "killing a hung wake must record orchestrator_dead, not report it healthy"
    )
    assert any("scope=wrapper-only-fallback" in str(e["detail"]) for e in events)


def test_dispatcher_gives_supervisor_term_grace_before_group_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = load_dispatcher()
    delivered: list[tuple[int, signal.Signals]] = []
    sleeps: list[float] = []

    class _Proc:
        pid = 4242
        waited = False

        def poll(self) -> int | None:
            return None

        def wait(self) -> int:
            self.waited = True
            return -9

    proc = _Proc()
    monkeypatch.setattr(mod.os, "killpg", lambda pid, sig: delivered.append((pid, sig)))
    monkeypatch.setattr(mod.time, "sleep", sleeps.append)

    scope = mod.terminate_wake_group(proc)  # type: ignore[arg-type]

    assert scope == "process-group-term-kill"
    assert delivered == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]
    assert sleeps == [1.0]
    assert proc.waited is True


def test_dispatcher_distinguishes_unavailable_verifier_from_target_divergence(
    tmp_path: Path,
) -> None:
    mod = load_dispatcher()
    root = tmp_path / ".harness" / "runs" / "r1"
    root.mkdir(parents=True)
    (root / "run.json").write_text(json.dumps({"target_state": {}}), encoding="utf-8")
    (root / "harness.json").write_text("{}", encoding="utf-8")
    dispatcher = mod.Dispatcher("r1", root, 30)  # type: ignore[attr-defined]
    dispatcher.factory_cli = [str(tmp_path / "missing-factory")]
    dispatcher.wake_orchestrator = lambda _: None

    dispatcher.check_validator_failure_modes("")

    events = read_chain(root / "events.jsonl")
    assert events[0]["kind"] == "target_state_verifier_unavailable"
    blocking = read_chain(root / "lanes" / "validator.blocking")
    assert blocking[0]["class"] == "target_state_verifier_unavailable"


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


def test_legacy_lane_admission_and_event_production_share_one_ordering_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = load_attention_gate()
    root = tmp_path / "run"
    root.mkdir()
    entered = threading.Event()
    release = threading.Event()
    original_check = mod._file_has_bytes  # type: ignore[attr-defined]

    def paused_check(path: Path) -> bool:
        result = original_check(path)
        entered.set()
        assert release.wait(timeout=5)
        return result

    monkeypatch.setattr(mod, "_file_has_bytes", paused_check)
    event = {
        "ts": "2026-08-19T12:00:00+00:00",
        "class": "stall",
        "evidence": "validator quiet 30m",
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        admitted = pool.submit(mod.check_lane_admission, root, "validator")
        assert entered.wait(timeout=5)
        appended = pool.submit(mod.append_blocking_event, root, "validator", event)
        time.sleep(0.05)
        assert not appended.done(), "producer must wait behind legacy admission"
        release.set()
        admitted.result(timeout=5)
        appended.result(timeout=5)

    with pytest.raises(mod.BlockingEventPending):  # type: ignore[attr-defined]
        mod.check_lane_admission(root, "validator")


# --------------------------------------------------------------------------
# consume_block.sh — the off-ramp that keeps a blocking event from wedging
# --------------------------------------------------------------------------


def test_consume_block_receipts_and_clears(tmp_path: Path) -> None:
    """A blocking event gates dispatch; without a consumer that control is a
    deadlock. consume_block.sh reads each event, receipts it into events.jsonl as
    a blocking_consumed record (so clearing-without-reading is visible by its
    absence), then atomically truncates the file to release the gate."""
    root = tmp_path / ".harness" / "runs" / "rA"
    blocking = append_blockers(
        root,
        "validator",
        {
            "ts": "2026-08-19T12:00:00+00:00",
            "class": "stall",
            "evidence": "validator quiet 30m",
        },
        {
            "ts": "2026-08-19T12:00:01+00:00",
            "class": "orchestrator_response",
            "response": "wakes/wake-1.response.md",
            "wake": "wake-1",
            "trust_class": "untrusted-advisory",
            "effect_route": "validator-blocking-only",
        },
    )
    evidence = root / "disposition-proof.txt"
    evidence.write_text("The narrowed task excludes the disputed surface.\n", encoding="utf-8")
    subject_digest = "sha256:" + hashlib.sha256(blocking.read_bytes()).hexdigest()
    evidence_digest = "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest()
    r = run(
        [
            "bash",
            str(HARNESS / "consume_block.sh"),
            "rA",
            "validator",
            "--disposition",
            "narrow",
            "--reason",
            "The next dispatch excludes the disputed surface.",
            "--subject-digest",
            subject_digest,
            "--evidence-file",
            str(evidence),
            "--evidence-digest",
            evidence_digest,
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    assert r.returncode == 0, r.stderr
    assert "dispositioned 2" in r.stdout
    assert (root / "lanes" / "validator.blocking").read_text() == ""
    events = read_chain(root / "events.jsonl")
    consumed = [event for event in events if event["kind"] == "blocking_consumed"]
    assert len(consumed) == 2
    assert all(e["disposition"] == "narrow" for e in consumed)
    assert all(e["blocking_subject_digest"] == subject_digest for e in consumed)
    assert all(e["disposition_evidence_digest"] == evidence_digest for e in consumed)
    retained = root / consumed[0]["disposition_evidence_id"]
    assert retained.read_bytes() == evidence.read_bytes()
    assert all(
        e["disposition_evidence_byte_count"] == len(evidence.read_bytes()) for e in consumed
    )


def test_consume_block_durably_receipts_before_clearing_gate(tmp_path: Path) -> None:
    root = tmp_path / ".harness" / "runs" / "rA-crash"
    event = {
        "ts": "2026-08-19T12:00:00+00:00",
        "class": "stall",
        "evidence": "validator quiet 30m",
    }
    blocking = append_blockers(root, "validator", event)
    evidence = root / "disposition-proof.txt"
    evidence.write_text("The stalled lane was stopped.\n", encoding="utf-8")
    arguments = [
        "bash",
        str(HARNESS / "consume_block.sh"),
        "rA-crash",
        "validator",
        "--disposition",
        "stop",
        "--reason",
        "The stalled lane was stopped before another dispatch.",
        "--subject-digest",
        "sha256:" + hashlib.sha256(blocking.read_bytes()).hexdigest(),
        "--evidence-file",
        str(evidence),
        "--evidence-digest",
        "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest(),
    ]

    interrupted = run(
        arguments,
        tmp_path,
        {
            "HARNESS_DIR": str(tmp_path / ".harness"),
            "FACTORY_TEST_CONSUME_CRASH_AFTER_RECEIPT_SYNC": "1",
        },
    )

    assert interrupted.returncode != 0
    assert "after durable receipt" in interrupted.stderr
    assert blocking.stat().st_size > 0
    assert [item["kind"] for item in read_chain(root / "events.jsonl")] == [
        "blocking_written",
        "blocking_consumed",
    ]

    recovered = run(
        arguments,
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    assert recovered.returncode == 0, recovered.stderr
    assert blocking.read_bytes() == b""
    assert [item["kind"] for item in read_chain(root / "events.jsonl")] == [
        "blocking_written",
        "blocking_consumed",
    ]


def test_consume_block_refuses_orphan_without_write_receipt(tmp_path: Path) -> None:
    root = tmp_path / ".harness" / "runs" / "rA-orphan"
    (root / "lanes").mkdir(parents=True)
    blocking = root / "lanes" / "validator.blocking"
    event = {
        "ts": "2026-08-19T12:00:00+00:00",
        "class": "stall",
        "evidence": "validator quiet 30m",
    }
    blocking.write_text(
        json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    evidence = root / "disposition-proof.txt"
    evidence.write_text("The event has no durable publication receipt.\n", encoding="utf-8")

    result = run(
        [
            "bash",
            str(HARNESS / "consume_block.sh"),
            "rA-orphan",
            "validator",
            "--disposition",
            "stop",
            "--reason",
            "An orphan cannot be consumed until its producer recovers the receipt.",
            "--subject-digest",
            "sha256:" + hashlib.sha256(blocking.read_bytes()).hexdigest(),
            "--evidence-file",
            str(evidence),
            "--evidence-digest",
            "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest(),
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )

    assert result.returncode != 0
    assert "lacks exactly one durable write receipt" in result.stderr
    assert blocking.stat().st_size > 0
    assert not (root / "evidence" / "dispositions").exists()


def test_consume_block_ignores_bounded_malformed_legacy_event_rows(tmp_path: Path) -> None:
    root = tmp_path / ".harness" / "runs" / "rA-legacy"
    root.mkdir(parents=True)
    (root / "events.jsonl").write_bytes(b"interrupted legacy tail")
    event = {
        "ts": "2026-08-19T12:00:00+00:00",
        "class": "stall",
        "evidence": "validator quiet 30m",
    }
    blocking = append_blockers(root, "validator", event)
    evidence = root / "disposition-proof.txt"
    evidence.write_text("The valid event remains independently receipted.\n", encoding="utf-8")

    result = run(
        [
            "bash",
            str(HARNESS / "consume_block.sh"),
            "rA-legacy",
            "validator",
            "--disposition",
            "resolve",
            "--reason",
            "The valid event is resolved without trusting the legacy fragment.",
            "--subject-digest",
            "sha256:" + hashlib.sha256(blocking.read_bytes()).hexdigest(),
            "--evidence-file",
            str(evidence),
            "--evidence-digest",
            "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest(),
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )

    assert result.returncode == 0, result.stderr
    assert blocking.read_bytes() == b""
    assert any(
        row.get("kind") == "blocking_consumed"
        for row in (
            json.loads(line)
            for line in (root / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.startswith("{")
        )
    )


def test_consume_block_separates_interrupted_events_tail_before_receipt(tmp_path: Path) -> None:
    root = tmp_path / ".harness" / "runs" / "rA-torn-consume"
    event = {
        "ts": "2026-08-19T12:00:00+00:00",
        "class": "stall",
        "evidence": "validator quiet 30m",
    }
    blocking = append_blockers(root, "validator", event)
    events_path = root / "events.jsonl"
    with events_path.open("ab") as stream:
        stream.write(b'{"kind":"blocking_consumed"')
    evidence = root / "disposition-proof.txt"
    evidence.write_text("The valid event remains independently receipted.\n", encoding="utf-8")

    result = run(
        [
            "bash",
            str(HARNESS / "consume_block.sh"),
            "rA-torn-consume",
            "validator",
            "--disposition",
            "resolve",
            "--reason",
            "The valid event is resolved after isolating the interrupted tail.",
            "--subject-digest",
            "sha256:" + hashlib.sha256(blocking.read_bytes()).hexdigest(),
            "--evidence-file",
            str(evidence),
            "--evidence-digest",
            "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest(),
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )

    assert result.returncode == 0, result.stderr
    assert blocking.read_bytes() == b""
    rows = []
    malformed = 0
    for line in events_path.read_bytes().splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            malformed += 1
    assert malformed == 1
    assert [row["kind"] for row in rows] == ["blocking_written", "blocking_consumed"]


def test_consume_block_rolls_back_partial_receipt_before_retry(tmp_path: Path) -> None:
    root = tmp_path / ".harness" / "runs" / "rA-partial-consume"
    event = {
        "ts": "2026-08-19T12:00:00+00:00",
        "class": "stall",
        "evidence": "validator quiet 30m",
    }
    blocking = append_blockers(root, "validator", event)
    evidence = root / "disposition-proof.txt"
    evidence.write_text("The stopped lane cannot dispatch again.\n", encoding="utf-8")
    arguments = [
        "bash",
        str(HARNESS / "consume_block.sh"),
        "rA-partial-consume",
        "validator",
        "--disposition",
        "stop",
        "--reason",
        "The stalled lane was stopped before another dispatch.",
        "--subject-digest",
        "sha256:" + hashlib.sha256(blocking.read_bytes()).hexdigest(),
        "--evidence-file",
        str(evidence),
        "--evidence-digest",
        "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest(),
    ]
    events_path = root / "events.jsonl"
    prior = events_path.read_bytes()

    interrupted = run(
        arguments,
        tmp_path,
        {
            "HARNESS_DIR": str(tmp_path / ".harness"),
            "FACTORY_TEST_CONSUME_FAIL_AFTER_PARTIAL_RECEIPT_WRITE": "1",
        },
    )

    assert interrupted.returncode != 0
    assert "injected partial blocking disposition append" in interrupted.stderr
    assert blocking.stat().st_size > 0
    assert events_path.read_bytes() == prior

    recovered = run(arguments, tmp_path, {"HARNESS_DIR": str(tmp_path / ".harness")})
    assert recovered.returncode == 0, recovered.stderr
    assert blocking.read_bytes() == b""
    assert [row["kind"] for row in read_chain(events_path)] == [
        "blocking_written",
        "blocking_consumed",
    ]


def test_consume_block_noop_when_empty(tmp_path: Path) -> None:
    root = tmp_path / ".harness" / "runs" / "rB"
    (root / "lanes").mkdir(parents=True)
    evidence = root / "disposition-proof.txt"
    evidence.write_text("No pending event remains.\n", encoding="utf-8")
    r = run(
        [
            "bash",
            str(HARNESS / "consume_block.sh"),
            "rB",
            "validator",
            "--disposition",
            "resolve",
            "--reason",
            "No pending event remains.",
            "--subject-digest",
            "sha256:" + hashlib.sha256(b"").hexdigest(),
            "--evidence-file",
            str(evidence),
            "--evidence-digest",
            "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest(),
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    assert r.returncode == 0
    assert "no blocking event pending" in r.stderr


def test_consume_block_cannot_clear_without_a_disposition(tmp_path: Path) -> None:
    root = tmp_path / ".harness" / "runs" / "rC"
    (root / "lanes").mkdir(parents=True)
    blocking = root / "lanes" / "validator.blocking"
    blocking.write_text('{"class":"orchestrator_response"}\n', encoding="utf-8")

    result = run(
        ["bash", str(HARNESS / "consume_block.sh"), "rC", "validator"],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )

    assert result.returncode == 64
    assert "valid disposition" in result.stderr
    assert blocking.read_text(encoding="utf-8") != ""


def test_consume_block_refuses_non_event_bytes_without_clearing(tmp_path: Path) -> None:
    root = tmp_path / ".harness" / "runs" / "rC-empty"
    (root / "lanes").mkdir(parents=True)
    blocking = root / "lanes" / "validator.blocking"
    blocking.write_text(" \n", encoding="utf-8")
    evidence = root / "disposition-proof.txt"
    evidence.write_text("No valid event exists.\n", encoding="utf-8")

    result = run(
        [
            "bash",
            str(HARNESS / "consume_block.sh"),
            "rC-empty",
            "validator",
            "--disposition",
            "refute",
            "--reason",
            "Malformed control bytes cannot be dispositioned as an event.",
            "--subject-digest",
            "sha256:" + hashlib.sha256(blocking.read_bytes()).hexdigest(),
            "--evidence-file",
            str(evidence),
            "--evidence-digest",
            "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest(),
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )

    assert result.returncode != 0
    assert "contains no events" in result.stderr
    assert blocking.read_text(encoding="utf-8") == " \n"


def test_consume_block_refuses_unknown_object_without_clearing(tmp_path: Path) -> None:
    root = tmp_path / ".harness" / "runs" / "rC-object"
    (root / "lanes").mkdir(parents=True)
    blocking = root / "lanes" / "validator.blocking"
    blocking.write_text("{}\n", encoding="utf-8")
    evidence = root / "disposition-proof.txt"
    evidence.write_text("An unknown object is not producer evidence.\n", encoding="utf-8")

    result = run(
        [
            "bash",
            str(HARNESS / "consume_block.sh"),
            "rC-object",
            "validator",
            "--disposition",
            "refute",
            "--reason",
            "Unknown objects cannot clear the attention gate.",
            "--subject-digest",
            "sha256:" + hashlib.sha256(blocking.read_bytes()).hexdigest(),
            "--evidence-file",
            str(evidence),
            "--evidence-digest",
            "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest(),
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )

    assert result.returncode != 0
    assert "invalid class" in result.stderr
    assert blocking.read_text(encoding="utf-8") == "{}\n"
    assert not (root / "events.jsonl").exists()


def test_consume_block_refuses_stale_subject_without_clearing(tmp_path: Path) -> None:
    root = tmp_path / ".harness" / "runs" / "rD"
    (root / "lanes").mkdir(parents=True)
    blocking = root / "lanes" / "validator.blocking"
    blocking.write_text('{"class":"orchestrator_response"}\n', encoding="utf-8")
    evidence = root / "disposition-proof.txt"
    evidence.write_text("Response was independently refuted.\n", encoding="utf-8")

    result = run(
        [
            "bash",
            str(HARNESS / "consume_block.sh"),
            "rD",
            "validator",
            "--disposition",
            "refute",
            "--reason",
            "The advisory premise differs from the retained proof.",
            "--subject-digest",
            "sha256:" + "d" * 64,
            "--evidence-file",
            str(evidence),
            "--evidence-digest",
            "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest(),
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )

    assert result.returncode != 0
    assert "subject changed" in result.stderr
    assert blocking.read_text(encoding="utf-8") != ""
    assert not (root / "events.jsonl").exists()


# --------------------------------------------------------------------------
# Dispatch blocking gate — the precondition fires at dispatch (the per-task
# production path), not just at lane start.
# --------------------------------------------------------------------------


def test_dispatch_refuses_while_blocking_event_pending(tmp_path: Path) -> None:
    """The blocking-event gate is wired into dispatch_lane.sh (the path factory.sh
    and the lanes actually call), not just lane_env.sh. A validator with an
    unconsumed attention event cannot dispatch new lane work until it consumes the
    event (harness/consume_block.sh). This is the production enforcement site."""
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, primer=True)
    (root / "lanes").mkdir(parents=True)
    (root / "lanes" / "validator.blocking").write_text('{"class":"orchestrator_response"}\n')
    r = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        _dispatch_env(stub, root),
    )
    assert r.returncode == 81, r.stdout + r.stderr
    assert "blocking event pending" in r.stderr
    assert not (root / "dispatch-inputs").exists()
    assert not (root / "instruction-inputs").exists()


def test_disposition_releases_same_unpublished_dispatch(tmp_path: Path) -> None:
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, primer=True)
    blocking = append_blockers(
        root,
        "validator",
        {
            "ts": "2026-08-19T12:00:00+00:00",
            "class": "orchestrator_response",
            "response": "wakes/wake-1.response.md",
            "wake": "wake-1",
            "trust_class": "untrusted-advisory",
            "effect_route": "validator-blocking-only",
        },
    )
    environment = _dispatch_env(stub, root)

    refused = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        environment,
    )
    assert refused.returncode == 81, refused.stdout + refused.stderr
    assert not (root / "dispatch-inputs").exists()

    evidence = root / "disposition-proof.txt"
    evidence.write_text("The retained task already excludes the disputed surface.\n")
    disposition = run(
        [
            "bash",
            str(HARNESS / "consume_block.sh"),
            "r1",
            "validator",
            "--disposition",
            "narrow",
            "--reason",
            "Proceed only with the exact already-bounded task.",
            "--subject-digest",
            "sha256:" + hashlib.sha256(blocking.read_bytes()).hexdigest(),
            "--evidence-file",
            str(evidence),
            "--evidence-digest",
            "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest(),
        ],
        cwd,
        {"HARNESS_RUN_ROOT": str(root)},
    )
    assert disposition.returncode == 0, disposition.stderr

    admitted = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        environment,
    )
    assert admitted.returncode == 0, admitted.stdout + admitted.stderr


def test_blocking_append_after_admission_marker_applies_to_next_dispatch(
    tmp_path: Path,
) -> None:
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, primer=True)
    environment = _dispatch_env(stub, root)
    delegate = tmp_path / "factory-with-post-admission-block"
    delegate.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "if [ \"${1:-}\" = prepare-lane-dispatch ] && "
        "[ ! -e \"$HARNESS_RUN_ROOT/post-admission-block-injected\" ]; then\n"
        "  mkdir -p \"$HARNESS_RUN_ROOT/lanes\"\n"
        "  printf '%s\\n' '{\"class\":\"orchestrator_response\",\"response\":\"next\"}' "
        ">> \"$HARNESS_RUN_ROOT/lanes/validator.blocking\"\n"
        "  : > \"$HARNESS_RUN_ROOT/post-admission-block-injected\"\n"
        "fi\n"
        f"exec {sys.executable} -m factory_runtime.cli \"$@\"\n",
        encoding="utf-8",
    )
    delegate.chmod(0o755)
    environment["FACTORY_CLI"] = str(delegate)

    admitted = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        environment,
    )
    assert admitted.returncode == 0, admitted.stdout + admitted.stderr
    assert (root / "lanes" / "validator.blocking").stat().st_size > 0

    next_dispatch = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        environment,
    )
    assert next_dispatch.returncode == 81
    assert "blocking event pending" in next_dispatch.stderr


def test_attention_lock_orders_overlapping_producer_after_dispatch_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = load_attention_gate()
    root = tmp_path / "run"
    root.mkdir()
    entered = threading.Event()
    release = threading.Event()
    original_open = mod._open_dispatch_lock  # type: ignore[attr-defined]

    def paused_open(root_path: Path, role: str) -> int:
        entered.set()
        assert release.wait(timeout=5)
        return original_open(root_path, role)

    monkeypatch.setattr(mod, "_open_dispatch_lock", paused_open)
    event = {
        "ts": "2026-08-19T12:00:00+00:00",
        "class": "stall",
        "evidence": "coder quiet 30m",
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        admitted = pool.submit(mod.acquire_dispatch_lock, root, "coder")
        assert entered.wait(timeout=5)
        appended = pool.submit(mod.append_blocking_event, root, "coder", event)
        time.sleep(0.05)
        assert not appended.done(), "producer must wait behind the admission ordering point"
        release.set()
        dispatch_fd = admitted.result(timeout=5)
        appended.result(timeout=5)

    os.close(dispatch_fd)
    assert (root / "lanes" / "coder.blocking").stat().st_size > 0
    with pytest.raises(mod.BlockingEventPending):  # type: ignore[attr-defined]
        mod.acquire_dispatch_lock(root, "coder")


@pytest.mark.parametrize("receipt_was_written", [False, True])
def test_blocking_event_exact_retry_recovers_receipt_crash_without_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    receipt_was_written: bool,
) -> None:
    mod = load_attention_gate()
    root = tmp_path / "run"
    root.mkdir()
    event = {
        "ts": "2026-08-19T12:00:00+00:00",
        "class": "stall",
        "evidence": "coder quiet 30m",
    }
    original_append = mod._append_jsonl  # type: ignore[attr-defined]
    calls = 0

    def fail_receipt(path: Path, body: Mapping[str, object]) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            if receipt_was_written:
                original_append(path, body)
            raise OSError("injected receipt crash")
        original_append(path, body)

    monkeypatch.setattr(mod, "_append_jsonl", fail_receipt)
    with pytest.raises(OSError, match="injected receipt crash"):
        mod.append_blocking_event(root, "coder", event)
    monkeypatch.setattr(mod, "_append_jsonl", original_append)

    mod.append_blocking_event(root, "coder", event)

    blocking = read_chain(root / "lanes" / "coder.blocking")
    written = [
        row
        for row in read_chain(root / "events.jsonl")
        if row.get("kind") == "blocking_written" and row.get("lane") == "coder"
    ]
    assert blocking == [event]
    assert len(written) == 1


def test_blocking_event_partial_append_rolls_back_and_exact_retry_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = load_attention_gate()
    root = tmp_path / "run"
    root.mkdir()
    event = {
        "ts": "2026-08-19T12:00:00+00:00",
        "class": "stall",
        "evidence": "coder quiet 30m",
    }
    original_write = mod.os.write  # type: ignore[attr-defined]
    calls = 0

    def interrupted_write(descriptor: int, payload: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(descriptor, payload[:7])
        if calls == 2:
            raise OSError("injected partial event append")
        return original_write(descriptor, payload)

    monkeypatch.setattr(mod.os, "write", interrupted_write)  # type: ignore[attr-defined]
    with pytest.raises(OSError, match="injected partial event append"):
        mod.append_blocking_event(root, "coder", event)
    monkeypatch.setattr(mod.os, "write", original_write)  # type: ignore[attr-defined]

    blocker = root / "lanes" / "coder.blocking"
    assert blocker.read_bytes() == b""

    mod.append_blocking_event(root, "coder", event)

    assert read_chain(blocker) == [event]
    assert len(
        [
            row
            for row in read_chain(root / "events.jsonl")
            if row.get("kind") == "blocking_written" and row.get("lane") == "coder"
        ]
    ) == 1


def test_attention_append_rollback_preserves_preexisting_durable_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = load_attention_gate()
    root = tmp_path / "run"
    root.mkdir()
    events = root / "events.jsonl"
    prior = b'{"kind":"legacy-dispatcher-event"}\n'
    events.write_bytes(prior)
    original_write = mod.os.write  # type: ignore[attr-defined]
    calls = 0

    def interrupted_write(descriptor: int, payload: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(descriptor, payload[:7])
        raise OSError("injected attention receipt failure")

    monkeypatch.setattr(mod.os, "write", interrupted_write)  # type: ignore[attr-defined]
    with pytest.raises(OSError, match="injected attention receipt failure"):
        mod._append_jsonl(  # type: ignore[attr-defined]
            events,
            {
                "ts": "2026-08-19T12:00:00+00:00",
                "kind": "blocking_written",
                "lane": "coder",
                "event": {
                    "ts": "2026-08-19T12:00:00+00:00",
                    "class": "stall",
                    "evidence": "coder quiet 30m",
                },
            },
        )

    assert events.read_bytes() == prior


def test_blocking_event_exact_retry_repairs_killed_unterminated_tail(tmp_path: Path) -> None:
    mod = load_attention_gate()
    root = tmp_path / "run"
    (root / "lanes").mkdir(parents=True)
    blocker = root / "lanes" / "coder.blocking"
    blocker.write_bytes(b'{"class')
    event = {
        "ts": "2026-08-19T12:00:00+00:00",
        "class": "stall",
        "evidence": "coder quiet 30m",
    }

    mod.append_blocking_event(root, "coder", event)  # type: ignore[attr-defined]

    assert read_chain(blocker) == [event]
    assert [row["kind"] for row in read_chain(root / "events.jsonl")] == [
        "blocking_written"
    ]


def test_blocking_event_exact_retry_fsyncs_complete_unreceipted_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A kill after a full write but before fsync leaves valid-looking bytes.

    The retry must sync that existing inode before it creates the durable write
    receipt; accepting the row by shape alone would let the receipt outlive the
    blocker after power loss.
    """

    mod = load_attention_gate()
    root = tmp_path / "run"
    lanes = root / "lanes"
    lanes.mkdir(parents=True)
    blocker = lanes / "coder.blocking"
    event = {
        "ts": "2026-08-19T12:00:00+00:00",
        "class": "stall",
        "evidence": "coder quiet 30m",
    }
    blocker.write_text(
        json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    blocker_identity = (blocker.stat().st_dev, blocker.stat().st_ino)
    synced: list[tuple[int, int]] = []
    original_fsync = mod.os.fsync  # type: ignore[attr-defined]

    def record_fsync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        synced.append((metadata.st_dev, metadata.st_ino))
        original_fsync(descriptor)

    monkeypatch.setattr(mod.os, "fsync", record_fsync)  # type: ignore[attr-defined]

    mod.append_blocking_event(root, "coder", event)  # type: ignore[attr-defined]

    assert blocker_identity in synced
    assert read_chain(blocker) == [event]
    assert [row["kind"] for row in read_chain(root / "events.jsonl")] == [
        "blocking_written"
    ]


def test_inherited_dispatch_descriptor_repeats_blocker_admission(tmp_path: Path) -> None:
    mod = load_attention_gate()
    root = tmp_path / "run"
    lanes = root / "lanes"
    lanes.mkdir(parents=True)
    lock_path = lanes / ".dispatch-coder.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    (lanes / "validator.blocking").write_text("pending\n", encoding="utf-8")
    try:
        with pytest.raises(mod.BlockingEventPending):  # type: ignore[attr-defined]
            mod.verify_dispatch_lock(root, "coder", descriptor)  # type: ignore[attr-defined]
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("retained_publications", [1, 2, 3, 4])
def test_dispatch_exact_retry_recovers_after_partial_instruction_publication(
    tmp_path: Path,
    retained_publications: int,
) -> None:
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, primer=True)
    environment = _dispatch_env(stub, root)
    marker = tmp_path / f"partial-{retained_publications}.marker"
    delegate = tmp_path / f"partial-{retained_publications}.py"
    delegate.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, subprocess, sys\n"
        "def argument(name): return pathlib.Path(sys.argv[sys.argv.index(name) + 1])\n"
        "marker = pathlib.Path(os.environ['FACTORY_TEST_PARTIAL_MARKER'])\n"
        "if sys.argv[1:2] == ['prepare-lane-dispatch'] and not marker.exists():\n"
        "    result = subprocess.run(\n"
        "        [sys.executable, '-m', 'factory_runtime.cli', *sys.argv[1:]]\n"
        "    )\n"
        "    if result.returncode != 0: raise SystemExit(result.returncode)\n"
        "    outputs = [argument('--effective-directives-output'), "
        "argument('--role-contract-output'), argument('--readback-output'), "
        "argument('--task-output')]\n"
        "    retain = int(os.environ['FACTORY_TEST_PARTIAL_RETAIN_COUNT'])\n"
        "    for path in outputs[retain:]: path.unlink()\n"
        "    marker.write_text('injected after publication\\n', encoding='utf-8')\n"
        "    raise SystemExit(70)\n"
        "os.execv(sys.executable, [sys.executable, '-m', 'factory_runtime.cli', *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    delegate.chmod(0o755)
    environment.update(
        {
            "FACTORY_CLI": str(delegate),
            "FACTORY_TEST_PARTIAL_MARKER": str(marker),
            "FACTORY_TEST_PARTIAL_RETAIN_COUNT": str(retained_publications),
        }
    )
    command = [
        "bash",
        str(HARNESS / "dispatch_lane.sh"),
        "r1",
        "coder",
        "--dispatch",
        str(dispatch),
    ]

    interrupted = run(command, cwd, environment)

    assert interrupted.returncode == 70
    retained_dispatch = root / "dispatch-inputs" / "coder.json"
    assert retained_dispatch.read_bytes() == dispatch.read_bytes()
    instruction_root = root / "instruction-inputs" / "coder-g1"
    publications = [
        instruction_root / "effective-directives.json",
        instruction_root / "role-contract.json",
        instruction_root / "directive-readback.json",
        instruction_root / "task.txt",
    ]
    assert [path.exists() for path in publications] == [
        index < retained_publications for index in range(4)
    ]

    recovered = run(command, cwd, environment)

    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert retained_dispatch.read_bytes() == dispatch.read_bytes()
    assert all(path.is_file() for path in publications)


def test_dispatch_sigkill_releases_role_lock_and_exact_retry_recovers(
    tmp_path: Path,
) -> None:
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, primer=True)
    environment = _dispatch_env(stub, root)
    marker = tmp_path / "killed-after-instruction-publication"
    delegate = tmp_path / "kill-dispatch-parent.py"
    delegate.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, signal, subprocess, sys\n"
        "marker = pathlib.Path(os.environ['FACTORY_TEST_KILL_MARKER'])\n"
        "if sys.argv[1:2] == ['prepare-lane-dispatch'] and not marker.exists():\n"
        "    completed = subprocess.run(\n"
        "        [sys.executable, '-m', 'factory_runtime.cli', *sys.argv[1:]]\n"
        "    )\n"
        "    if completed.returncode != 0: raise SystemExit(completed.returncode)\n"
        "    marker.write_text('published before SIGKILL\\n', encoding='utf-8')\n"
        "    os.kill(os.getppid(), signal.SIGKILL)\n"
        "    raise SystemExit(70)\n"
        "os.execv(sys.executable, [sys.executable, '-m', 'factory_runtime.cli', *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    delegate.chmod(0o755)
    environment.update(
        {
            "FACTORY_CLI": str(delegate),
            "FACTORY_TEST_KILL_MARKER": str(marker),
        }
    )
    command = [
        "bash",
        str(HARNESS / "dispatch_lane.sh"),
        "r1",
        "coder",
        "--dispatch",
        str(dispatch),
    ]

    killed = run(command, cwd, environment)

    assert killed.returncode != 0
    assert marker.is_file()
    retained_dispatch = root / "dispatch-inputs" / "coder.json"
    assert retained_dispatch.read_bytes() == dispatch.read_bytes()

    recovered = run(command, cwd, environment)

    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert retained_dispatch.read_bytes() == dispatch.read_bytes()


# --------------------------------------------------------------------------
# Gate C (kindex-as-primer) + Gate B (reset-prime-deliver) — the dispatch brief
# is FENCE -> PRIMER -> TASK, and a role-specific kindex primer is a dispatch
# precondition. A gate that has never been watched firing is theater, so each
# drill watches the gate fire (refuse) and pass (deliver + structure).
# --------------------------------------------------------------------------


def _lane_dispatch(
    role: str,
    *,
    task: str = "requirement: build R1.1",
    ambiguity: str = "none",
) -> dict[str, object]:
    return {
        "schema_version": "factory-lane-dispatch/1",
        "run_id": "r1",
        "generation": 1,
        "role": role,
        "semantic_clearance": False,
        "interpretation": {
            "restated_request": "Build the exact ratified R1.1 behavior.",
            "operational_consequence": (
                "Return a question rather than inventing missing intent or authority."
            ),
            "ambiguity": ambiguity,
        },
        "directive_readback": [],
        "task": task,
    }


def dispatch_success_fixture(
    tmp_path: Path, role: str = "coder", primer: bool = True
) -> tuple[Path, Path, Path, Path]:
    """A complete v4 execution-truth run, not a hand-authored repo/SHA record."""

    _, root, target_state = execution_truth_fixture(tmp_path)
    harness_metadata_path = root / "harness.json"
    harness_metadata = json.loads(harness_metadata_path.read_text(encoding="utf-8"))
    harness_metadata["budget_usd"] = 1
    harness_metadata["budget_enforcement"] = "reserved-runner-ceilings"
    harness_metadata_path.write_text(json.dumps(harness_metadata), encoding="utf-8")
    workdir = Path(str(target_state["workdir"]))
    art = root / "artifacts"
    art.mkdir(parents=True)
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
    dispatch = tmp_path / "d.json"
    dispatch.write_text(json.dumps(_lane_dispatch(role)) + "\n", encoding="utf-8")
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "tmux").write_text("#!/usr/bin/env bash\nexit 0\n")
    os.chmod(stub / "tmux", 0o755)
    return workdir, root, dispatch, stub


def _dispatch_env(stub: Path, root: Path) -> dict[str, str]:
    fixture_root = root.parents[2]
    runner_config = fixture_root / "runner-config"
    manifests = runner_config / "manifests"
    registries = runner_config / "registries"
    secrets = runner_config / "secrets"
    qualifications = runner_config / "qualifications"
    for directory in (manifests, registries, secrets, qualifications):
        directory.mkdir(parents=True, exist_ok=True)
    output_schema = runner_config / "runner-output.schema.json"
    output_schema.write_text(
        (
            Path(__file__).resolve().parents[1]
            / "factory_runtime"
            / "schemas"
            / "runner-output.schema.json"
        ).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    for role in ("coder", "tester"):
        (manifests / f"{role}.json").write_text(
            json.dumps(
                {
                    "schema_version": "factory-runner-manifest/2",
                    "runner_id": f"fixture-{role}",
                    "role": role,
                    "adapter": "codex",
                    "executable": sys.executable,
                    "child_executables": [],
                    "runner_version": "fixture-1",
                    "model": "fixture-model",
                    "model_version": "fixture-model-1",
                    "configuration_digest": digest_obj({"fixture": role}),
                    "state_profile_digest": profile_digest("lane-dispatch"),
                    "state_qualification_digest": digest_obj({"qualified": role}),
                    "billing_key_name": "TEST_TOKEN",
                    "secret_names": ["TEST_TOKEN"],
                    "output_schema_digest": digest_bytes(output_schema.read_bytes()),
                    "network_mode": "unrestricted-outbound",
                    "limits": {
                        "wall_seconds": 60,
                        "idle_seconds": 10,
                        "max_processes": 4,
                        "max_attempts": 3,
                        "max_output_bytes": 65536,
                        "max_tokens": 1000,
                        "max_cost_microusd": 1000,
                    },
                    "pricing": {
                        "input_microusd_per_million": 1,
                        "output_microusd_per_million": 1,
                    },
                    "created_at": 1,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (registries / f"{role}.json").write_text(
            json.dumps({"role": role, "operations": [], "capabilities": []}) + "\n",
            encoding="utf-8",
        )
        (qualifications / f"{role}.json").write_text(
            json.dumps({"qualified": True, "test_fixture": True}) + "\n",
            encoding="utf-8",
        )
        (qualifications / f"{role}.observations.json").write_text(
            json.dumps({"observations": [], "test_fixture": True}) + "\n",
            encoding="utf-8",
        )
    return {
        "HARNESS_DIR": str(root.parent.parent),
        "FACTORY_RUNS_DIR": str(root.parent),
        "HARNESS_RUN_ROOT": str(root),
        "PATH": f"{stub}:{os.environ['PATH']}",
        "FACTORY_CLI": f"{sys.executable} -m factory_runtime.cli",
        "FACTORY_RUNNER_MANIFEST_DIR": str(manifests),
        "FACTORY_RUNNER_OUTPUT_SCHEMA": str(output_schema),
        "FACTORY_RUNNER_SECRET_ROOT": str(secrets),
        "FACTORY_RUNNER_WORKSPACE_ROOT": str(fixture_root / "runner-workspaces"),
        "FACTORY_BROKER_REGISTRY_DIR": str(registries),
        "FACTORY_STATE_QUALIFICATION_DIR": str(qualifications),
        "FACTORY_TEST_BOUNDARY_LOG": str(fixture_root / "boundary.log"),
    }


def test_dispatch_refuses_without_kindex_primer(tmp_path: Path) -> None:
    """Gate C: a dispatch with no role-specific kindex primer is refused — the
    Validator must search kindex and capture research nodes before the lane is
    launched (closes kindex-non-use). This is the reset-prime-deliver PRIMER step
    made a precondition, not a hope."""
    src, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=False)
    r = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        src,
        _dispatch_env(stub, root),
    )
    assert r.returncode == 70, r.stdout + r.stderr
    assert "no kindex primer" in r.stderr and "Gate C" in r.stderr


def test_legacy_harness_requires_explicit_unqualified_abandonment(tmp_path: Path) -> None:
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=True)
    metadata_path = root / "harness.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["schema_version"] = "factory-harness/1"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    environment = _dispatch_env(stub, root)

    refused = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        environment,
    )
    assert refused.returncode == 70
    assert "harness metadata is stale or unbound" in refused.stderr

    format_control = run(
        [
            "bash",
            str(HARNESS / "abandon_legacy.sh"),
            "r1",
            "--actor",
            "human:operator",
            "--reason",
            "misleading \u202etxt.exe",
            "--acknowledge-unqualified-restart",
            "--runs",
            str(root.parent),
        ],
        cwd,
        environment,
    )
    assert format_control.returncode != 0
    assert "control-free" in format_control.stderr
    assert not (root / "legacy-harness-abandonment.json").exists()

    abandoned = run(
        [
            "bash",
            str(HARNESS / "abandon_legacy.sh"),
            "r1",
            "--actor",
            "human:operator",
            "--reason",
            "restart under the qualified v2 boundary",
            "--acknowledge-unqualified-restart",
            "--runs",
            str(root.parent),
        ],
        cwd,
        environment,
    )
    assert abandoned.returncode == 0, abandoned.stdout + abandoned.stderr
    receipt = json.loads((root / "legacy-harness-abandonment.json").read_text())
    assert receipt["disposition"] == "abandoned-unqualified"
    assert receipt["replacement_schema_version"] == "factory-harness/2"

    bidi_receipt = root / "bidi-abandonment.json"
    bidi_document = dict(receipt)
    bidi_document["reason"] = "misleading \u202etxt.exe"
    bidi_receipt.write_text(json.dumps(bidi_document), encoding="utf-8")
    bidi_verification = run(
        [
            sys.executable,
            str(HARNESS / "legacy_abandonment.py"),
            "--harness",
            str(metadata_path),
            "--receipt",
            str(bidi_receipt),
            "--run",
            "r1",
        ],
        cwd,
        environment,
    )
    assert bidi_verification.returncode != 0
    assert "invalid reason" in bidi_verification.stderr

    mod = load_dispatcher()
    dispatcher = mod.Dispatcher("r1", root, 30)  # type: ignore[attr-defined]
    dispatcher.wake_orchestrator = lambda _: None
    dispatcher.run_loop()
    assert read_chain(root / "events.jsonl")[-1]["detail"] == (
        "verified legacy harness abandonment"
    )


def test_dispatcher_refuses_unverified_legacy_abandonment_marker(tmp_path: Path) -> None:
    mod = load_dispatcher()
    root = tmp_path / ".factory" / "runs" / "r1"
    root.mkdir(parents=True)
    target_digest = "sha256:" + "a" * 64
    (root / "run.json").write_text(
        json.dumps({"target_state": {}}),
        encoding="utf-8",
    )
    (root / "harness.json").write_text(
        json.dumps(
            {
                "schema_version": "factory-harness/1",
                "run_id": "r1",
                "target_state_digest": target_digest,
                "status": "open",
            }
        ),
        encoding="utf-8",
    )
    (root / "legacy-harness-abandonment.json").write_text("{}", encoding="utf-8")
    dispatcher = mod.Dispatcher("r1", root, 30)  # type: ignore[attr-defined]
    dispatcher.wake_orchestrator = lambda _: None

    dispatcher.run_loop()

    blocking = read_chain(root / "lanes" / "validator.blocking")
    assert blocking[0]["class"] == "invalid_legacy_abandonment"
    events = read_chain(root / "events.jsonl")
    assert events[-1]["detail"] == "invalid legacy abandonment marker refused"


@pytest.mark.parametrize("schema_version", [None, "factory-harness/1"])
def test_dispatcher_refuses_legacy_harness_before_monitoring(
    tmp_path: Path,
    schema_version: str | None,
) -> None:
    mod = load_dispatcher()
    root = tmp_path / ".factory" / "runs" / "r1"
    root.mkdir(parents=True)
    (root / "run.json").write_text(
        json.dumps({"target_state": {}}),
        encoding="utf-8",
    )
    metadata: dict[str, object] = {"status": "open"}
    if schema_version is not None:
        metadata["schema_version"] = schema_version
    (root / "harness.json").write_text(json.dumps(metadata), encoding="utf-8")
    dispatcher = mod.Dispatcher("r1", root, 30)  # type: ignore[attr-defined]
    dispatcher.wake_orchestrator = lambda _: None

    dispatcher.run_loop()

    blocking = read_chain(root / "lanes" / "validator.blocking")
    assert blocking[0]["class"] == "legacy_harness"
    events = read_chain(root / "events.jsonl")
    assert events[-1]["kind"] == "dispatcher_stop"
    assert "legacy or unversioned" in events[-1]["detail"]


def test_current_harness_cannot_use_legacy_abandonment(tmp_path: Path) -> None:
    cwd, root, _, stub = dispatch_success_fixture(tmp_path, role="coder", primer=True)
    result = run(
        [
            "bash",
            str(HARNESS / "abandon_legacy.sh"),
            "r1",
            "--actor",
            "human:operator",
            "--reason",
            "not applicable",
            "--acknowledge-unqualified-restart",
            "--runs",
            str(root.parent),
        ],
        cwd,
        _dispatch_env(stub, root),
    )
    assert result.returncode != 0
    assert "only factory-harness/1" in result.stderr


def test_dispatch_refuses_a_target_manifest_changed_after_ignition(tmp_path: Path) -> None:
    src, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=True)
    target = root / "evidence" / "target-resolution" / "target-state.json"
    document = json.loads(target.read_text())
    document["resolved_tree"] = "f" * 40
    target.write_text(json.dumps(document))

    r = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        src,
        _dispatch_env(stub, root),
    )

    assert r.returncode == 70
    assert "retained target-state differs" in r.stderr


def test_dispatch_refuses_retained_stage_e_request_substitution(tmp_path: Path) -> None:
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=True)
    request_path = root / "evidence" / "intake" / "execution-request.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["requested_outcome"] = "A substituted outcome not authorized at Stage E."
    request_path.write_text(json.dumps(request), encoding="utf-8")

    result = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        _dispatch_env(stub, root),
    )

    assert result.returncode == 70
    assert "retained Stage-E request differs" in result.stderr
    assert "lane-workspace-coder" not in ResourceLedger(root, "r1").latest()


def test_dispatch_refuses_task_artifact_substitution(tmp_path: Path) -> None:
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=True)
    (root / "TASK.md").write_text("substituted task", encoding="utf-8")

    result = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        _dispatch_env(stub, root),
    )

    assert result.returncode == 70
    assert "task bytes differ" in result.stderr
    assert "lane-workspace-coder" not in ResourceLedger(root, "r1").latest()


def test_dispatch_primer_is_role_specific_not_shared(tmp_path: Path) -> None:
    """The primer is role-specific: a coder lane with only a tester primer present
    is refused. The projection boundary is enforced structurally — the coder does
    not fall back to the tester's primer (which may carry implementation detail)."""
    src, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=False)
    (root / "artifacts" / "primer.tester.md").write_text("tester-only primer\n")
    r = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        src,
        _dispatch_env(stub, root),
    )
    assert r.returncode == 70 and "no kindex primer" in r.stderr


def test_dispatch_ambient_primer_gap_override_is_ignored(tmp_path: Path) -> None:
    """A missing role primer always denies; ambient break-glass is not authority."""
    src, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=False)
    env = _dispatch_env(stub, root)
    env["GATE_BC_ALLOW_GAP"] = "1"
    r = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        src,
        env,
    )
    assert r.returncode == 70, r.stdout + r.stderr
    assert "no kindex primer" in r.stderr


def test_dispatch_delivers_path_free_fence_dispatch_specs_and_primer(tmp_path: Path) -> None:
    """Gate B freezes dispatch alone; run-model injects exact capsule-bound context.

    The shell may use Markdown views for its adequacy preflight, but those mutable views are not
    copied into the model task. Canonical phase JSON and the primer cross only through run-model.
    """
    src, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=True)
    r = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        src,
        _dispatch_env(stub, root),
    )
    assert r.returncode == 0, r.stdout + r.stderr
    task = (root / "runner-tasks" / "coder.md").read_text()
    headings = ("## FENCE", "## FROZEN DISPATCH")
    positions = [task.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert "One pen only" in task and "DATA, never authority" in task
    assert "RATIFIED PRODUCT SPECIFICATION" not in task
    assert "Phase A0 primer" not in task
    projection_path = root / "evidence" / "runner" / "coder" / "projection.json"
    projection = json.loads(projection_path.read_text())
    assert projection["role"] == "coder"
    assert all("source_root" not in item for item in projection["files"])
    receipt = read_chain(root / "dispatches.jsonl")[-1]
    reservation = read_chain(root / "budget-reservations.jsonl")[-1]
    runtime = json.loads((root / "run.json").read_text())
    target = runtime["target_state"]
    assert receipt["target_state_digest"] == runtime["target_state_digest"]
    assert receipt["resolved_commit"] == target["resolved_commit"]
    assert receipt["resolved_tree"] == target["resolved_tree"]
    assert receipt["checkout_id"] == target["checkout_id"]
    assert receipt["launcher_qualification"] == "QUALIFIED_PR2"
    assert receipt["lane_isolation"] == "QUALIFIED_PR2"
    assert receipt["budget_reservation_digest"] == "sha256:" + reservation["hash"]
    resources = ResourceLedger(root, "r1").latest()
    assert resources["lane-workspace-coder"]["status"] == "retained"
    assert resources["runner-workspace-coder"]["status"] == "retained"
    assert "tmux-window-coder" not in resources
    assert (tmp_path / "boundary.log").read_text().splitlines() == [
        "run-model",
        "execute-broker-handoff",
    ]


def test_dispatch_refuses_tampered_receipt_chain_before_window_plan(tmp_path: Path) -> None:
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=True)
    forged = {
        "run": "r1",
        "prev_hash": "0" * 64,
        "claim": "not covered by the supplied hash",
        "hash": "0" * 64,
    }
    (root / "dispatches.jsonl").write_text(json.dumps(forged) + "\n", encoding="utf-8")

    result = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        _dispatch_env(stub, root),
    )

    assert result.returncode == 70
    assert "dispatch receipt chain is invalid" in result.stderr
    resources = ResourceLedger(root, "r1").latest()
    assert "tmux-window-coder" not in resources
    assert not (tmp_path / "boundary.log").exists()


def test_dispatch_failed_canary_executes_no_broker_operation(tmp_path: Path) -> None:
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=True)
    environment = _dispatch_env(stub, root)
    environment["FACTORY_TEST_RUN_MODEL_FAIL"] = "1"

    result = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        environment,
    )

    assert result.returncode == 70
    assert "no broker operation was executed" in result.stderr
    assert (tmp_path / "boundary.log").read_text().splitlines() == ["run-model"]
    assert len(read_chain(root / "budget-reservations.jsonl")) == 1
    resources = ResourceLedger(root, "r1").latest()
    assert resources["runner-workspace-coder"]["status"] in {"abandoned", "retained"}
    assert "tmux-window-coder" not in resources


def test_dispatch_validates_and_freezes_runner_failure_receipt(tmp_path: Path) -> None:
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=True)
    environment = _dispatch_env(stub, root)
    environment["FACTORY_TEST_RUN_MODEL_FAIL_WITH_RECEIPT"] = "1"

    result = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        environment,
    )

    assert result.returncode == 70
    assert "no broker operation was executed" in result.stderr
    assert (tmp_path / "boundary.log").read_text().splitlines() == ["run-model"]
    retained = root / "evidence" / "runner" / "coder"
    receipt_path = retained / "runner-failure-receipt.json"
    diagnostic_path = retained / "validator-invocation-diagnostic.json"
    state_capsule_path = retained / "failed-state-capsule.json"
    prompt_path = retained / "failed-prompt-1.json"
    qualification_path = retained / "runner-qualification.json"
    executable_path = retained / "runner-executable"
    assert all(
        path.is_file()
        for path in (
            receipt_path,
            diagnostic_path,
            state_capsule_path,
            prompt_path,
            qualification_path,
            executable_path,
        )
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["run_id"] == "r1"
    assert receipt["role"] == "coder"
    assert receipt["failure_capsule"]["owner"] == "validator-harness"
    assert receipt["diagnostic"]["content_digest"] == digest_bytes(
        diagnostic_path.read_bytes()
    )
    assert receipt["state_capsule_digest"] == digest_obj(
        json.loads(state_capsule_path.read_text(encoding="utf-8"))
    )
    assert receipt["prompt_sequence"][0]["content_digest"] == digest_bytes(
        prompt_path.read_bytes()
    )
    assert receipt["qualification_digest"] == digest_bytes(qualification_path.read_bytes())
    assert receipt["executable_digest"] == digest_bytes(executable_path.read_bytes())
    resources = ResourceLedger(root, "r1").latest()
    assert resources["runner-workspace-coder"]["status"] == "retained"
    failure_event = next(
        record
        for record in ResourceLedger(root, "r1").records()
        if record["resource_id"] == "runner-workspace-coder"
        and record["status"] == "failed"
    )
    assert failure_event["evidence_digests"]["runner-failure-receipt"] == digest_bytes(
        receipt_path.read_bytes()
    )
    assert failure_event["evidence_digests"][
        "validator-invocation-diagnostic"
    ] == digest_bytes(diagnostic_path.read_bytes())
    assert failure_event["evidence_digests"]["failed-prompt-1"] == digest_bytes(
        prompt_path.read_bytes()
    )
    assert failure_event["evidence_digests"]["runner-executable"] == digest_bytes(
        executable_path.read_bytes()
    )
    assert "tmux-window-coder" not in resources


def test_dispatch_exact_retry_recovers_complete_orphaned_runner_failure_once(
    tmp_path: Path,
) -> None:
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=True)
    environment = _dispatch_env(stub, root)
    environment["FACTORY_TEST_RUN_MODEL_FAIL_WITH_RECEIPT"] = "1"
    environment["FACTORY_TEST_KILL_DISPATCH_AFTER_FAILURE_RECEIPT"] = "1"

    interrupted = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        environment,
    )

    assert interrupted.returncode == -signal.SIGKILL
    assert (tmp_path / "boundary.log").read_text().splitlines() == ["run-model"]
    retained = root / "evidence" / "runner" / "coder"
    assert not (retained / "runner-failure-receipt.json").exists()

    environment.pop("FACTORY_TEST_KILL_DISPATCH_AFTER_FAILURE_RECEIPT")
    recovered = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        environment,
    )

    assert recovered.returncode == 70, recovered.stdout + recovered.stderr
    assert "recovered exact runner failure" in recovered.stderr
    assert "no model or broker ran" in recovered.stderr
    assert (tmp_path / "boundary.log").read_text().splitlines() == ["run-model"]
    assert (retained / "runner-failure-receipt.json").is_file()
    assert (retained / "validator-invocation-diagnostic.json").is_file()
    resources = ResourceLedger(root, "r1").latest()
    assert resources["runner-workspace-coder"]["status"] == "retained"
    assert resources["lane-workspace-coder"]["status"] == "retained"

    record_count = len(ResourceLedger(root, "r1").records())
    repeated = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        environment,
    )
    assert repeated.returncode == 70, repeated.stdout + repeated.stderr
    assert "recovered exact runner failure" in repeated.stderr
    assert (tmp_path / "boundary.log").read_text().splitlines() == ["run-model"]
    assert len(ResourceLedger(root, "r1").records()) == record_count


@pytest.mark.parametrize("damage", ("partial-runner-evidence", "mismatched-lane"))
def test_dispatch_crash_retry_refuses_unsafe_or_mismatched_orphan_without_model_call(
    tmp_path: Path,
    damage: str,
) -> None:
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=True)
    environment = _dispatch_env(stub, root)
    environment["FACTORY_TEST_RUN_MODEL_FAIL_WITH_RECEIPT"] = "1"
    environment["FACTORY_TEST_KILL_DISPATCH_AFTER_FAILURE_RECEIPT"] = "1"

    interrupted = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        environment,
    )
    assert interrupted.returncode == -signal.SIGKILL

    runner_workspace = Path(environment["FACTORY_RUNNER_WORKSPACE_ROOT"]) / "r1" / "lane-coder-g1"
    lane_workspace = root / "workspaces" / "coder"
    if damage == "partial-runner-evidence":
        (runner_workspace / "validator-invocation-diagnostic.json").unlink()
    else:
        candidate = next(
            path
            for path in sorted(lane_workspace.rglob("*"))
            if path.is_file() and ".git" not in path.parts
        )
        candidate.write_bytes(candidate.read_bytes() + b"\npost-crash substitution\n")

    environment.pop("FACTORY_TEST_KILL_DISPATCH_AFTER_FAILURE_RECEIPT")
    refused = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        environment,
    )

    assert refused.returncode == 70, refused.stdout + refused.stderr
    assert (tmp_path / "boundary.log").read_text().splitlines() == ["run-model"]
    retained = root / "evidence" / "runner" / "coder"
    assert not (retained / "runner-failure-receipt.json").exists()
    resources = ResourceLedger(root, "r1").latest()
    assert resources["runner-workspace-coder"]["status"] == "planned"
    assert resources["lane-workspace-coder"]["status"] == "active"


def test_dispatch_refuses_missing_pr2_configuration_before_model_call(tmp_path: Path) -> None:
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=True)
    environment = _dispatch_env(stub, root)
    del environment["FACTORY_BROKER_REGISTRY_DIR"]

    result = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        environment,
    )

    assert result.returncode == 70
    assert "PR2 runner configuration is incomplete" in result.stderr
    assert not (tmp_path / "boundary.log").exists()


def test_dispatch_refuses_runner_reservation_above_objective_budget(tmp_path: Path) -> None:
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=True)
    environment = _dispatch_env(stub, root)
    metadata_path = root / "harness.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["budget_usd"] = 0.0005
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    result = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        environment,
    )

    assert result.returncode == 70
    assert "objective budget reservation was refused" in result.stderr
    assert not (tmp_path / "boundary.log").exists()


def test_parallel_lane_reservations_cannot_oversubscribe_objective_budget(
    tmp_path: Path,
) -> None:
    cwd, root, _, stub = dispatch_success_fixture(tmp_path, role="coder", primer=True)
    (root / "artifacts" / "primer.tester.md").write_text(
        "# Tester primer\nconstraint: preserve exact test authority\n",
        encoding="utf-8",
    )
    metadata_path = root / "harness.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["budget_usd"] = 0.001
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    environment = _dispatch_env(stub, root)

    def run_dispatch(role: str) -> subprocess.CompletedProcess[str]:
        role_dispatch = tmp_path / f"dispatch-{role}.json"
        role_dispatch.write_text(
            json.dumps(_lane_dispatch(role)) + "\n",
            encoding="utf-8",
        )
        return run(
            [
                "bash",
                str(HARNESS / "dispatch_lane.sh"),
                "r1",
                role,
                "--dispatch",
                str(role_dispatch),
            ],
            cwd,
            environment,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run_dispatch, ("coder", "tester")))

    assert sorted(result.returncode for result in results) == [0, 70]
    refused = next(result for result in results if result.returncode == 70)
    assert "objective budget reservation was refused" in refused.stderr
    reservations = read_chain(root / "budget-reservations.jsonl")
    assert len(reservations) == 1
    assert reservations[0]["reserved_max_cost_microusd"] == 1000


def test_dispatch_refuses_model_use_without_an_explicit_objective_budget(
    tmp_path: Path,
) -> None:
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=True)
    metadata_path = root / "harness.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["budget_usd"] = None
    metadata["budget_enforcement"] = "not-requested"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    result = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        _dispatch_env(stub, root),
    )

    assert result.returncode == 70
    assert "objective budget reservation was refused" in result.stderr
    assert not (tmp_path / "boundary.log").exists()


def test_dispatch_rejects_caller_sha_before_workspace_creation(tmp_path: Path) -> None:
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, primer=True)
    result = run(
        [
            "bash",
            str(HARNESS / "dispatch_lane.sh"),
            "r1",
            "coder",
            "--dispatch",
            str(dispatch),
            "--sha",
            "0" * 40,
        ],
        cwd,
        _dispatch_env(stub, root),
    )
    assert result.returncode == 64
    assert "checked target-state is the only selector" in result.stderr
    assert not (root / "workspaces" / "coder").exists()


@pytest.mark.parametrize("schema_version", ("factory-run/1", "factory-run/2"))
def test_dispatch_refuses_legacy_run_schema(tmp_path: Path, schema_version: str) -> None:
    runs = tmp_path / ".factory" / "runs"
    store = RunStore(runs)
    artifacts: dict[str, object] = {
        "target": "sha256:" + ("1" * 64),
        "source": "sha256:" + ("2" * 64),
        "phase_artifacts": {},
    }
    if schema_version == "factory-run/2":
        artifacts["generation_artifacts"] = {}
    store._ledger("legacy").append(
        LedgerEntry(
            capability_id="legacy",
            from_state="",
            to_state=RunState.INTAKE,
            artifact_digests=artifacts,
            payload={"run_schema_version": schema_version},
            actor="validator",
            created_at="100",
        )
    )
    store.rebuild_projection("legacy")
    dispatch = tmp_path / "dispatch.md"
    dispatch.write_text("interpretation_confirmed: true\n", encoding="utf-8")

    result = run(
        [
            "bash",
            str(HARNESS / "dispatch_lane.sh"),
            "legacy",
            "coder",
            "--dispatch",
            str(dispatch),
            "--runs",
            str(runs),
        ],
        tmp_path,
        {"FACTORY_CLI": f"{sys.executable} -m factory_runtime.cli"},
    )

    assert result.returncode == 70
    assert "legacy run schemas cannot dispatch" in result.stderr
    assert not (runs / "legacy" / "workspaces").exists()


def test_dispatch_ignores_operator_checkout_dirt(tmp_path: Path) -> None:
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, primer=True)
    operator = tmp_path / "src"
    (operator / "operator-only-dirt.txt").write_text("unrelated\n", encoding="utf-8")

    result = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        _dispatch_env(stub, root),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (root / "workspaces" / "coder" / "operator-only-dirt.txt").exists()


def test_dispatch_refuses_run_owned_source_divergence_before_resource_plan(
    tmp_path: Path,
) -> None:
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, primer=True)
    target = json.loads((root / "run.json").read_text())["target_state"]
    (Path(target["source_root"]) / "diverged.txt").write_text("changed\n", encoding="utf-8")

    result = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        cwd,
        _dispatch_env(stub, root),
    )

    assert result.returncode == 70
    assert "target-state-diverged" in result.stderr
    assert "lane-workspace-coder" not in ResourceLedger(root, "r1").latest()


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


def test_consume_block_refuses_non_json_line_without_clearing(tmp_path: Path) -> None:
    """Malformed control input is neither a valid event nor safe to disposition."""
    root = tmp_path / ".harness" / "runs" / "rA"
    (root / "lanes").mkdir(parents=True)
    (root / "lanes" / "validator.blocking").write_text(
        'this is not json\n{"class":"stall","evidence":"validator quiet 30m"}\n'
    )
    blocking = root / "lanes" / "validator.blocking"
    evidence = root / "disposition-proof.txt"
    evidence.write_text("Malformed input is not admissible.\n", encoding="utf-8")
    r = run(
        [
            "bash",
            str(HARNESS / "consume_block.sh"),
            "rA",
            "validator",
            "--disposition",
            "refute",
            "--reason",
            "The malformed event cannot be authenticated.",
            "--subject-digest",
            "sha256:" + hashlib.sha256(blocking.read_bytes()).hexdigest(),
            "--evidence-file",
            str(evidence),
            "--evidence-digest",
            "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest(),
        ],
        tmp_path,
        {"HARNESS_DIR": str(tmp_path / ".harness")},
    )
    assert r.returncode != 0
    assert "not JSON" in r.stderr
    assert not (root / "events.jsonl").exists()
    assert (root / "lanes" / "validator.blocking").read_text() != ""


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
# Gate L — promote.sh is the SOLE writer of harness.json "closed", reached only
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
    _, root, _ = execution_truth_fixture(
        tmp,
        run_id=run_id,
        harness_status=status,
        terminal_resources=True,
    )
    return root


def _run_status(root: Path) -> str:
    return json.loads((root / "harness.json").read_text())["status"]


def test_promote_writes_closed_when_verdict_allows(tmp_path: Path) -> None:
    """The happy path: a run with gathered promoting evidence closes through decide_promotion.
    This is the sole harness-close path — promote.sh writes 'closed' iff the verdict allows."""
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
    assert "status" not in json.loads((root / "run.json").read_text())
    harness = json.loads((root / "harness.json").read_text())
    assert harness["promotion_verdict"] == "promotion_verdict.json"
    assert harness["promotion_verdict_digest"] == digest_bytes(
        (root / "promotion_verdict.json").read_bytes()
    )
    assert harness["closed_at"].endswith("+00:00")
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
    # Phase 0.1 forcing: the silent-death close now leaves its signal.
    rows = _refusal_events(root)
    assert [row["kind"] for row in rows] == ["refusal-promote"]


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
    # Execution truth fails before destructive freshness handling; the forged file remains
    # inert and harness.json stays open.
    assert (root / "promotion_verdict.json").exists()


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
    assert "Factory run does not exist" in r.stderr


def test_promote_is_sole_writer_of_closed() -> None:
    """The sole harness-close invariant: no shell script other than promote.sh writes the
    JSON value "closed". factory.sh writes harness state "open"; the dispatcher reads "closed"
    to stop but never writes it. Authoritative RunStore advancement is a separate unwired control.
    If another script gained a writer, Gate L would be route-aroundable."""
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


def test_endgame_routes_only_a_fully_green_run_through_gate_l() -> None:
    """Live close wiring is structural: endgame owns the call, but never owns the verdict."""

    text = (HARNESS / "endgame.sh").read_text(encoding="utf-8")
    invocation = '"$D/promote.sh" "$RUN" --runs "$FACTORY_RUNS_ROOT"'
    assert invocation in text
    assert text.index(invocation) > text.index("== exact-subject and run-owned-resource hygiene")
    assert text.index(invocation) < text.index('python3 - "$ROOT" "$RUN" "$SHA"')
    assert 'if [ "$FAILED" -eq 0 ]; then' in text[text.index("== sole harness close") :]

    missing_target_branch = text[
        text.index('if [ -f "$TARGET_CONF" ]') : text.index(
            "== exact-subject and run-owned-resource hygiene"
        )
    ]
    assert "FAILED=1" in missing_target_branch


# --------------------------------------------------------------------------
# Ambient-override reconciliation (contract 780ce1f092f6) — every remaining
# ambient override is a hard denial. The environment is never authority:
# setting a retired break-glass variable changes nothing, a refusal mints no
# evidence, and the retired names may not reappear in an enforcing surface.
# --------------------------------------------------------------------------

_ORACLE_LEAK_MESSAGE = (
    "adjust the code until pytest tests/test_widget.py::test_frobnicates passes "
    "-- the assert currently raises AssertionError"
)


def test_inject_oracle_leak_ambient_override_is_ignored(tmp_path: Path) -> None:
    """Gate E: a coder-bound message carrying pytest vocabulary always refuses
    (exit 80). INJECT_ALLOW_ORACLE_WORDS is not read — the boundary does not
    open from the environment — and a refused message is never receipted."""
    r = run(
        ["bash", str(HARNESS / "inject.sh"), "testrun", "coder", _ORACLE_LEAK_MESSAGE],
        tmp_path,
        {
            "INJECT_DRY_RUN": "1",
            "HARNESS_DIR": str(tmp_path / ".harness"),
            "INJECT_ALLOW_ORACLE_WORDS": "1",
        },
    )
    assert r.returncode == 80, r.stdout + r.stderr
    assert "oracle-leak refusal" in r.stderr
    assert "no ambient override" in r.stderr
    assert "Fix and re-ratify" in r.stderr
    assert not (tmp_path / ".harness" / "runs" / "testrun" / "injections.jsonl").exists()


def test_inject_oracle_leak_refusal_identical_without_override(tmp_path: Path) -> None:
    """The same message with no override variable refuses identically (exit 80):
    the variable was dead weight, never a hinge."""
    r = run(
        ["bash", str(HARNESS / "inject.sh"), "testrun", "coder", _ORACLE_LEAK_MESSAGE],
        tmp_path,
        {"INJECT_DRY_RUN": "1", "HARNESS_DIR": str(tmp_path / ".harness")},
    )
    assert r.returncode == 80, r.stdout + r.stderr
    assert "oracle-leak refusal" in r.stderr
    assert not (tmp_path / ".harness" / "runs" / "testrun" / "injections.jsonl").exists()


def test_inject_shell_target_ambient_override_is_ignored(tmp_path: Path) -> None:
    """A target pane running a plain shell is never an injection target (exit 77):
    INJECT_ALLOW_SHELL is not read, and nothing is delivered (no send-keys)."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    calls = tmp_path / "tmux-calls.log"
    tmux_stub = stub_dir / "tmux"
    tmux_stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> {shlex.quote(str(calls))}\n'
        'for a in "$@"; do\n'
        '  case "$a" in display*) echo bash; exit 0;; esac\n'
        "done\n"
        "exit 0\n"
    )
    os.chmod(tmux_stub, 0o755)
    r = run(
        ["bash", str(HARNESS / "inject.sh"), "testrun", "validator", "short note"],
        tmp_path,
        {
            "HARNESS_DIR": str(tmp_path / ".harness"),
            "INJECT_ALLOW_SHELL": "1",
            "PATH": f"{stub_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        },
    )
    assert r.returncode == 77, r.stdout + r.stderr
    assert "not an agent" in r.stderr
    assert "no ambient override" in r.stderr
    delivered = calls.read_text(encoding="utf-8") if calls.exists() else ""
    assert "send-keys" not in delivered


def test_dispatch_ambient_gap_override_family_mints_no_evidence(tmp_path: Path) -> None:
    """The whole retired gap-override family set at once still denies a
    primer-gap dispatch: exit 70, no workspace resource, no dispatch receipt."""
    src, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="coder", primer=False)
    receipts = root / "dispatches.jsonl"
    before = receipts.read_bytes() if receipts.exists() else b""
    env = _dispatch_env(stub, root)
    env.update({"GATE_BC_ALLOW_GAP": "1", "PHASE1_ALLOW_GAPS": "1", "FACTORY_ALLOW_GAP": "1"})
    r = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "coder", "--dispatch", str(dispatch)],
        src,
        env,
    )
    assert r.returncode == 70, r.stdout + r.stderr
    assert "no kindex primer" in r.stderr
    assert "lane-workspace-coder" not in ResourceLedger(root, "r1").latest()
    after = receipts.read_bytes() if receipts.exists() else b""
    assert after == before


def test_no_harness_reader_of_retired_ambient_overrides() -> None:
    """Structural sweep: the retired override names may not appear anywhere in
    the enforcing surfaces (harness/*.sh, harness/*.py, scripts/*.py) — not even
    in a comment. Reintroducing a reader turns this red. tests/ and docs may
    still name them when describing the refusals."""
    import re

    repo = Path(__file__).resolve().parents[1]
    surfaces = sorted(
        [
            *(repo / "harness").glob("*.sh"),
            *(repo / "harness").glob("*.py"),
            *(repo / "scripts").glob("*.py"),
        ]
    )
    names = {p.name for p in surfaces}
    assert {"inject.sh", "lane_env.sh", "dispatch_lane.sh", "phase1_gate.sh"} <= names
    assert "check_denial_probes.py" in names
    patterns = (re.compile(r"ALLOW_GAPS?\b"), re.compile(r"INJECT_ALLOW_[A-Z_]+"))
    offenders = [
        f"{path.name}: {match.group(0)}"
        for path in surfaces
        for pattern in patterns
        for match in pattern.finditer(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"retired ambient-override names found: {offenders}"


def test_lane_env_ground_min_extension_is_clamped(tmp_path: Path) -> None:
    """HARNESS_MAX_GROUND_MIN tightens only: a 400-minute-old grounding marker
    is stale at the 360-minute ceiling no matter how large the variable is, and
    the attempted extension is called out on stderr."""
    env = lane_env_setup(tmp_path)
    marker = tmp_path / ".harness" / "grounded"
    stale = time.time() - 400 * 60
    os.utime(marker, (stale, stale))
    env["HARNESS_MAX_GROUND_MIN"] = "100000"
    r = run(
        ["bash", str(HARNESS / "lane_env.sh"), str(tmp_path / "manifest"), "--", "true"],
        tmp_path,
        env,
    )
    assert r.returncode == 76, r.stdout + r.stderr
    assert "not grounded" in r.stderr
    assert "grounding-staleness extension refused" in r.stderr


def test_lane_env_ground_min_tightening_still_works(tmp_path: Path) -> None:
    """Lowering the ceiling is honored: a 120-minute-old marker is fresh at the
    360-minute default but stale under HARNESS_MAX_GROUND_MIN=60 — and no
    extension-refusal note fires, because tightening is legitimate."""
    env = lane_env_setup(tmp_path)
    marker = tmp_path / ".harness" / "grounded"
    aged = time.time() - 120 * 60
    os.utime(marker, (aged, aged))
    env["HARNESS_MAX_GROUND_MIN"] = "60"
    r = run(
        ["bash", str(HARNESS / "lane_env.sh"), str(tmp_path / "manifest"), "--", "true"],
        tmp_path,
        env,
    )
    assert r.returncode == 76, r.stdout + r.stderr
    assert "grounding-staleness extension refused" not in r.stderr


def test_lane_env_ground_min_invalid_value_refuses(tmp_path: Path) -> None:
    """A non-integer knob value is refused outright (exit 76), never coerced."""
    env = lane_env_setup(tmp_path)
    env["HARNESS_MAX_GROUND_MIN"] = "never"
    r = run(
        ["bash", str(HARNESS / "lane_env.sh"), str(tmp_path / "manifest"), "--", "true"],
        tmp_path,
        env,
    )
    assert r.returncode == 76, r.stdout + r.stderr
    assert "invalid HARNESS_MAX_GROUND_MIN" in r.stderr


# --------------------------------------------------------------------------
# Phase 0.1 (remediation plan) — refusal-path event instrumentation.
# Every refusal exit writes exactly one events.jsonl row with a kind from the
# committed closed registry, so the earliest deaths leave a derivable signal.
# The registry gates naming only; a registry gap can never swallow the death.
# --------------------------------------------------------------------------


def _refusal_events(root: Path) -> list[dict[str, object]]:
    path = root / "events.jsonl"
    if not path.exists():
        return []
    return [row for row in read_chain(path) if row.get("class") == "refusal"]


def test_refusal_kind_registry_is_closed_over_harness_usage() -> None:
    """Axis-2 additions are data-file diffs visible in git, never runtime-invented
    kinds: every --kind a harness script passes must be in the committed registry."""
    registry = json.loads(
        (HARNESS / "refusal_event_kinds.json").read_text(encoding="utf-8")
    )["kinds"]
    used: set[str] = set()
    for script in sorted(HARNESS.glob("*.sh")):
        # Join backslash continuations so an invocation split across lines scans
        # as one; only refusal-event invocations feed this registry —
        # record_no.sh's --kind flag has its own closed set (terminal_no_kinds.json).
        text = script.read_text(encoding="utf-8").replace("\\\n", " ")
        for line in text.splitlines():
            if "refusal-event" not in line:
                continue
            tokens = line.split()
            used.update(
                tokens[i + 1]
                for i, token in enumerate(tokens[:-1])
                if token == "--kind"
            )
    assert used, "no harness script passes --kind — instrumentation is unwired"
    assert used <= set(registry), f"unregistered kinds in harness scripts: {used - set(registry)}"
    assert "refusal-unregistered" in registry


def test_refusal_event_unregistered_kind_degrades_never_swallows(tmp_path: Path) -> None:
    """An unregistered kind maps to refusal-unregistered with the attempted kind
    preserved — the registry gates naming, never whether the death is recorded."""
    root = tmp_path / "runroot"
    root.mkdir()
    r = run(
        [
            "python3",
            str(HARNESS / "attention_gate.py"),
            "refusal-event",
            "--root",
            str(root),
            "--kind",
            "refusal-not-a-registered-kind",
            "--source",
            "test",
            "--detail",
            "boom",
        ],
        tmp_path,
    )
    assert r.returncode == 0, r.stderr
    rows = _refusal_events(root)
    assert len(rows) == 1
    assert rows[0]["kind"] == "refusal-unregistered"
    assert "refusal-not-a-registered-kind" in str(rows[0]["detail"])
    assert "boom" in str(rows[0]["detail"])


def test_endgame_refusal_leaves_derivable_signal(tmp_path: Path) -> None:
    """An endgame that cannot even resolve its candidate resource still leaves
    exactly one registered refusal event before dying."""
    root = _make_run(tmp_path)
    r = run(
        [
            "bash",
            str(HARNESS / "endgame.sh"),
            "r1",
            "a" * 40,
            "--candidate-resource",
            "no-such-resource",
        ],
        tmp_path,
        _factory_cli_env(),
    )
    assert r.returncode != 0
    rows = _refusal_events(root)
    assert [row["kind"] for row in rows] == ["refusal-endgame"]


def _unattributed_refusal_exits(
    script_text: str,
    exit_pattern: str,
    call_names: tuple[str, ...] = ("refusal_event",),
    exempt_exits: tuple[str, ...] = (),
) -> list[str]:
    """Advisory lexical layer of the refusal-exit guarantee (rounds 3-5).

    Round 5 demoted this scanner: EXECUTION is the guarantee (the runtime
    row-count tests drive every site); this layer stays as cheap early
    feedback and now (a) FAILS TOWARD FLAGGING on exit forms it cannot
    classify — any `exit` whose argument is not a registered literal code,
    the exact quoted "$rc", or an explicitly exempt form is reported rather
    than ignored (round-5 F-2: `exit $(( 66 ))` was invisible); (b) refuses
    substitution look-alikes at the match site — `refusal_event_hushed=` and
    trailing-comment mentions do not attribute (F-1); (c) recognizes quoted
    and lowercase heredoc tags (F-3). `false && call` and other never-executing
    placements remain beyond a lexical layer — by design, the runtime tests
    own that class.
    """
    import re as _re

    exit_re = _re.compile(exit_pattern)
    any_exit_re = _re.compile(r"(?:^|[;{|&(]\s*)exit\b\s*([^;}&|)]*)")
    call_re = _re.compile(
        "|".join(_re.escape(name) + r"(?![\w=])" for name in call_names)
    )
    boundary_re = _re.compile(r"^(\}|fi|done|else|esac)\b")
    exempt = set(exempt_exits)

    raw = script_text.replace("\\\n", " ")
    lines = raw.splitlines()

    marked: list[str] = []
    heredoc_tag: str | None = None
    in_helper = False
    for line in lines:
        stripped = line.strip()
        if heredoc_tag is not None:
            marked.append("###BOUNDARY###")
            if stripped == heredoc_tag:
                heredoc_tag = None
            continue
        if in_helper:
            marked.append("###BOUNDARY###")
            if stripped == "}":
                in_helper = False
            continue
        if any(stripped.startswith(f"{name}()") for name in call_names):
            in_helper = True
            marked.append("###BOUNDARY###")
            continue
        match = _re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
        if match:
            heredoc_tag = match.group(1)
            marked.append(line)
            continue
        marked.append(line)

    def _effective(line: str) -> str:
        # Cut a trailing comment so '# refusal_event' cannot attribute; a '#'
        # inside quotes is rare in these scripts and cutting early only makes
        # the scanner MORE likely to flag — fail toward flagging.
        position = line.find("#")
        return line if position < 0 else line[:position]

    unattributed: list[str] = []
    for i, line in enumerate(marked):
        stripped = line.strip()
        if stripped.startswith("#") or line == "###BOUNDARY###":
            continue
        effective = _effective(stripped)
        exit_match = exit_re.search(effective)
        if not exit_match:
            for generic in any_exit_re.finditer(effective):
                argument = generic.group(1).strip()
                if argument in exempt:
                    continue
                unattributed.append(
                    f"{i + 1}: UNCLASSIFIABLE exit form {argument!r}: {stripped}"
                )
            continue
        call_match = call_re.search(effective)
        if call_match and call_match.start() < exit_match.start():
            continue
        attributed = False
        for j in range(i - 1, max(-1, i - 11), -1):
            back_line = marked[j]
            back = _effective(back_line.strip())
            if back_line == "###BOUNDARY###":
                break
            if not back or back_line.strip().startswith("#"):
                continue
            if exit_re.search(back) or any_exit_re.search(back) or boundary_re.match(back):
                break
            if call_re.search(back):
                attributed = True
                break
            if back.endswith(("}", "then", "do", "{")):
                break
        if not attributed:
            unattributed.append(f"{i + 1}: {stripped}")
    return unattributed


_PROMOTE_EXIT_RE = r'(?:^|[;{|&]\s*)exit (?:(?:2|64|66|70)(?!\d)|"\$rc")'
_ENDGAME_EXIT_RE = r'(?:^|[;{|&]\s*)exit (?:70(?!\d)|"\$rc")'


def test_promote_refusal_exits_all_route_through_refusal_event() -> None:
    """Every fail-closed exit in promote.sh after the helper definition is
    attributed to a refusal_event call in its own basic block (round-3 D1:
    block-structural, exit "$rc" in scope). Exit 1 (BLOCKED) is exempt — it
    renders a verdict; pre-helper exits are the stated pre-root boundary."""
    text = (HARNESS / "promote.sh").read_text(encoding="utf-8")
    body = text[text.index("refusal_event()") :]
    missing = _unattributed_refusal_exits(
        body, _PROMOTE_EXIT_RE, exempt_exits=("1", "$?")
    )
    assert not missing, "fail-closed exits without attribution:\n" + "\n".join(missing)


def test_endgame_refusal_exits_all_route_through_refusal_event() -> None:
    """Same attribution for endgame.sh; the RED/GREEN verdict exit is exempt."""
    text = (HARNESS / "endgame.sh").read_text(encoding="utf-8")
    body = text[text.index("refusal_event()") :]
    missing = _unattributed_refusal_exits(
        body, _ENDGAME_EXIT_RE, exempt_exits=('"$FAILED"', "64")
    )
    assert not missing, "refusal exits without attribution:\n" + "\n".join(missing)


def test_phase1_refusal_exit_is_attributed() -> None:
    """Round-3 D3: phase1_gate.sh's gate-G exit 71 carries its attribution;
    its pre-root exit-64 usage checks are the stated boundary."""
    text = (HARNESS / "phase1_gate.sh").read_text(encoding="utf-8")
    missing = _unattributed_refusal_exits(
        text,
        r'(?:^|[;{|&]\s*)exit 71\b',
        ("refusal_event", "refusal-event"),
        exempt_exits=("64",),
    )
    assert not missing, "\n".join(missing)


def test_dispatch_refusals_are_funneled_through_fail() -> None:
    """Round-5 F-4 rewrite: the funnel must contain an EXECUTING refusal-event
    invocation (a python3 line calling the hyphenated subcommand dispatch
    actually uses — the old refusal_event disjunct was dead text), and ANY exit
    after the funnel that is not a registered non-refusal form is flagged, so
    a refusal path replaced by echo+exit N turns this red."""

    text = (HARNESS / "dispatch_lane.sh").read_text(encoding="utf-8")
    lines = text.splitlines()
    start_index = next(i for i, line in enumerate(lines) if line.startswith("fail() {"))
    end_index = next(i for i in range(start_index + 1, len(lines)) if lines[i] == "}")
    fail_body_lines = [line.strip() for line in lines[start_index:end_index]]
    assert any(
        line.startswith("python3") and "refusal-event" in line
        for line in fail_body_lines
    ), "fail() carries no executing refusal-event invocation"

    offenders = _dispatch_exits_outside_funnel(lines, end_index)
    assert not offenders, f"exit outside the fail() funnel: {offenders}"


def _dispatch_exits_outside_funnel(lines: list, end_index: int) -> list:
    import re as _re

    joined_tail = "\n".join(lines[end_index + 1 :]).replace("\\\n", " ")
    any_exit_re = _re.compile(r"(?:^|[;{|&(]\s*)exit\b\s*([^;}&|)]*)")
    offenders = []
    for line in joined_tail.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        effective = stripped.split("#", 1)[0]
        for match in any_exit_re.finditer(effective):
            argument = match.group(1).strip()
            if argument in {"64", "0", '"$rc"', "$?"}:
                continue
            offenders.append(stripped)
    return offenders


_MASKING_MUTATIONS = [
    # Verification round 3 found these seven sites silently de-instrumentable
    # under the window-grep. Each mutation removes one attribution; the
    # scanner must go red on every one.
    ("promote.sh", _PROMOTE_EXIT_RE, '  refusal_event "no checked harness.json" 64\n'),
    (
        "promote.sh", _PROMOTE_EXIT_RE,
        'rc=$?; refusal_event "target-state verification refused" "$rc"; ',
    ),
    (
        "promote.sh", _PROMOTE_EXIT_RE,
        '  refusal_event "run-owned resources lack a terminal disposition"\n',
    ),
    (
        "endgame.sh", _ENDGAME_EXIT_RE,
        'rc=$?; refusal_event "target-state verification refused" "$rc"; ',
    ),
    (
        "endgame.sh", _ENDGAME_EXIT_RE,
        'rc=$?; refusal_event "resource verification refused" "$rc"; ',
    ),
    (
        "endgame.sh", _ENDGAME_EXIT_RE,
        '  refusal_event "final SHA is not a commit in the candidate resource"\n',
    ),
    ("endgame.sh", _ENDGAME_EXIT_RE, 'refusal_event "final SHA did not resolve exactly"; '),
]


_SUBSTITUTION_MUTATIONS = [
    # Round-5 F-1/F-2: token present but never executes — the pure-removal
    # fixtures proved the scanner only against deletion; these prove it
    # against substitution (the verifier's end-to-end reproductions).
    (
        "promote.sh",
        "identifier-assignment",
        'refusal_event "factory CLI exited 0 but wrote no verdict file"; ',
        'refusal_event_hushed="factory CLI exited 0 but wrote no verdict file"; ',
    ),
    (
        "promote.sh",
        "arithmetic-exit",
        'refusal_event "harness metadata check refused" 66; exit 66;',
        'exit $(( 66 ));',
    ),
]


@pytest.mark.parametrize("script,label,old,new", _SUBSTITUTION_MUTATIONS)
def test_substitution_mutations_turn_the_scanner_red(
    script: str, label: str, old: str, new: str
) -> None:
    text = (HARNESS / script).read_text(encoding="utf-8")
    assert old in text, f"substitution anchor drifted in {script}: {label}"
    mutated = text.replace(old, new, 1)
    body = mutated[mutated.index("refusal_event()") :]
    assert _unattributed_refusal_exits(
        body, _PROMOTE_EXIT_RE, exempt_exits=("1", "$?")
    ), f"scanner failed to catch substitution {label} in {script}"


def test_dispatch_echo_substitution_turns_the_funnel_scan_red() -> None:
    """Round-5 F-4 end-to-end reproduction: a post-root fail() call replaced by
    echo+exit must be flagged by the funnel scan."""
    text = (HARNESS / "dispatch_lane.sh").read_text(encoding="utf-8")
    anchor = 'fail "run has not been ignited through harness/factory.sh"'
    assert anchor in text, "dispatch mutation anchor drifted"
    mutated = text.replace(anchor, 'echo "run has no grounding marker" >&2; exit 65', 1)
    lines = mutated.splitlines()
    start_index = next(i for i, line in enumerate(lines) if line.startswith("fail() {"))
    end_index = next(i for i in range(start_index + 1, len(lines)) if lines[i] == "}")
    assert _dispatch_exits_outside_funnel(lines, end_index), (
        "the funnel scan must flag the substituted bare exit"
    )


@pytest.mark.parametrize("script,pattern,removal", _MASKING_MUTATIONS)
def test_masking_mutations_turn_the_attribution_scanner_red(
    script: str, pattern: str, removal: str
) -> None:
    text = (HARNESS / script).read_text(encoding="utf-8")
    assert removal in text, f"mutation anchor drifted in {script}: {removal!r}"
    mutated = text.replace(removal, "", 1)
    body = mutated[mutated.index("refusal_event()") :]
    assert _unattributed_refusal_exits(body, pattern), (
        f"scanner failed to catch de-instrumentation of {removal!r} in {script}"
    )


def test_promote_blocked_decision_emits_no_refusal_event(tmp_path: Path) -> None:
    """BLOCKED is a rendered verdict, not a silent death — the discriminating
    sibling: no refusal event on exit 1."""
    root = _make_run(tmp_path)
    (root / "promotion_inputs.json").write_text(
        json.dumps({"request": {}, "policy": {}, "profile": {}}, indent=2),
        encoding="utf-8",
    )
    r = run(["bash", str(HARNESS / "promote.sh"), "r1"], tmp_path, _factory_cli_env())
    assert r.returncode == 1
    assert (root / "promotion_verdict.json").exists()
    assert _refusal_events(root) == []


@pytest.mark.skipif(sys.platform != "darwin", reason="chflags uchg is the darwin write-block")
def test_promote_close_write_failure_leaves_refusal_event(tmp_path: Path) -> None:
    """The :243 silent-death class from verification round 2: a green verdict
    whose harness.json close write fails must leave a refusal event (exit 70)."""
    from tests.conftest import promoting_promotion_inputs, write_promoting_chain

    root = _make_run(tmp_path)
    (root / "promotion_inputs.json").write_text(
        json.dumps(promoting_promotion_inputs(), indent=2), encoding="utf-8"
    )
    write_promoting_chain(root)
    subprocess.run(["chflags", "uchg", str(root / "harness.json")], check=True)
    try:
        r = run(["bash", str(HARNESS / "promote.sh"), "r1"], tmp_path, _factory_cli_env())
    finally:
        subprocess.run(["chflags", "nouchg", str(root / "harness.json")], check=False)
    assert r.returncode == 70, (r.returncode, r.stderr)
    assert _run_status(root) == "open"
    rows = _refusal_events(root)
    assert [row["kind"] for row in rows] == ["refusal-promote"]
    assert rows[0]["exit_code"] == 70


# --------------------------------------------------------------------------
# Phase 0.2 (remediation plan) — host-written terminal-NO record.
# The pass rule reads "harness terminal in {closed-green, closed-red,
# host-recorded-NO}" — never "verdict.json present". record_no.sh is the sole
# writer of the "no" disposition; kinds are committed closed data with a
# signal/bound class so deadline expiry can never masquerade as an early NO.
# --------------------------------------------------------------------------


def _harness_doc(root: Path) -> dict[str, object]:
    return json.loads((root / "harness.json").read_text(encoding="utf-8"))


def test_record_no_writes_host_terminal_disposition(tmp_path: Path) -> None:
    root = _make_run(tmp_path)
    r = run(
        ["bash", str(HARNESS / "record_no.sh"), "r1", "--kind", "operator",
         "--reason", "spec unsatisfiable, human decision"],
        tmp_path,
        _factory_cli_env(),
    )
    assert r.returncode == 0, r.stderr
    doc = _harness_doc(root)
    assert doc["status"] == "no"
    assert doc["no_kind"] == "operator"
    assert doc["no_class"] == "signal"
    assert doc["no_reason"] == "spec unsatisfiable, human decision"
    assert str(doc["no_recorded_at"]).endswith("+00:00")


def test_record_no_deadline_kind_is_a_bound_not_a_signal(tmp_path: Path) -> None:
    """0.2 enforcement: deadline expiry is excluded from the rewarded NO-relevant
    class — the deadline knob cannot manufacture the terminal the instrument pays."""
    root = _make_run(tmp_path)
    r = run(
        ["bash", str(HARNESS / "record_no.sh"), "r1", "--kind", "watchdog-deadline",
         "--reason", "signal deadline expired with residual blockers"],
        tmp_path,
        _factory_cli_env(),
    )
    assert r.returncode == 0, r.stderr
    assert _harness_doc(root)["no_class"] == "bound"


def test_record_no_refuses_unknown_kind(tmp_path: Path) -> None:
    """A deliberate host action with an unknown kind is a caller bug, not a death
    signal to preserve — refused, never minted mislabeled."""
    root = _make_run(tmp_path)
    r = run(
        ["bash", str(HARNESS / "record_no.sh"), "r1", "--kind", "vibes",
         "--reason", "x"],
        tmp_path,
        _factory_cli_env(),
    )
    assert r.returncode == 65
    assert _harness_doc(root)["status"] == "open"


def test_record_no_refuses_closed_run_and_conflicting_rewrite(tmp_path: Path) -> None:
    root = _make_run(tmp_path)
    args = ["bash", str(HARNESS / "record_no.sh"), "r1", "--kind", "operator",
            "--reason", "first"]
    assert run(args, tmp_path, _factory_cli_env()).returncode == 0
    # Identical retry is idempotent (crash-retry safety, promote.sh precedent).
    assert run(args, tmp_path, _factory_cli_env()).returncode == 0
    # A different NO must not overwrite the recorded one.
    conflicting = run(
        ["bash", str(HARNESS / "record_no.sh"), "r1", "--kind", "operator",
         "--reason", "second"],
        tmp_path,
        _factory_cli_env(),
    )
    assert conflicting.returncode == 2
    assert _harness_doc(root)["no_reason"] == "first"


def test_promote_refuses_a_no_run_terminal_is_terminal(tmp_path: Path) -> None:
    """A host-recorded NO is terminal: the close path fail-closes on it (and
    leaves its own refusal event), never advancing a dead run to closed."""
    from tests.conftest import promoting_promotion_inputs, write_promoting_chain

    root = _make_run(tmp_path)
    (root / "promotion_inputs.json").write_text(
        json.dumps(promoting_promotion_inputs(), indent=2), encoding="utf-8"
    )
    write_promoting_chain(root)
    assert run(
        ["bash", str(HARNESS / "record_no.sh"), "r1", "--kind", "operator",
         "--reason", "dead end"],
        tmp_path,
        _factory_cli_env(),
    ).returncode == 0
    r = run(["bash", str(HARNESS / "promote.sh"), "r1"], tmp_path, _factory_cli_env())
    assert r.returncode != 0
    assert _harness_doc(root)["status"] == "no"
    assert [row["kind"] for row in _refusal_events(root)] == ["refusal-promote"]


def test_dispatcher_stops_babysitting_a_no_run(tmp_path: Path) -> None:
    """Round-3 carryover: a host-recorded terminal NO is terminal — the
    dispatcher stops on status 'no' exactly as it stops on 'closed'."""
    mod = load_dispatcher()
    root = tmp_path / ".harness" / "runs" / "r1"
    root.mkdir(parents=True)
    (root / "run.json").write_text(json.dumps({"run": "r1", "repo": str(tmp_path)}))
    (root / "events.jsonl").write_text("")
    (root / "harness.json").write_text(
        json.dumps({"schema_version": "factory-harness/2", "status": "no"})
    )
    d = mod.Dispatcher("r1", root, 1)  # type: ignore[attr-defined]
    d.run_loop()
    events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
    stops = [e for e in events if e.get("kind") == "dispatcher_stop"]
    assert stops and "run no" in stops[0]["detail"]


# --------------------------------------------------------------------------
# Round-5 F-1/F-2: runtime row-count coverage for every promote.sh refusal
# site. The lexical scanner is advisory; EXECUTION is the guarantee — each
# path is driven end-to-end and must leave exactly one registered refusal
# row. A delegating stub CLI overrides one subcommand and hands everything
# else to the real factory CLI, so run-context verification stays real.
# --------------------------------------------------------------------------


def _delegating_stub(tmp_path: Path, case: str) -> dict[str, str]:
    env = _factory_cli_env()
    real = env["FACTORY_CLI"]
    stub = tmp_path / "stub-cli.sh"
    body = {
        "promote-refuses": 'if [ "$1" = "promote" ]; then echo "stub refusal" >&2; exit 2; fi',
        "promote-silent-zero": 'if [ "$1" = "promote" ]; then exit 0; fi',
        "promote-forked-output": (
            'if [ "$1" = "promote" ]; then'
            ' root="$(dirname "$0")/root-marker"; root="$(cat "$root")";'
            ' printf \'{"allowed": true}\' > "$root/promotion_verdict.json";'
            ' printf \'{"allowed": false}\'; exit 0; fi'
        ),
        "promote-unreadable-verdict": (
            'if [ "$1" = "promote" ]; then'
            ' root="$(dirname "$0")/root-marker"; root="$(cat "$root")";'
            ' printf "not json" > "$root/promotion_verdict.json";'
            ' printf "not json"; exit 0; fi'
        ),
        "seal-refused": (
            'if [ "$1" = "verify-resources" ]; then'
            '  for a in "$@"; do'
            '    [ "$a" = "--seal" ] && { echo "stub seal refusal" >&2; exit 1; };'
            '  done;'
            'fi'
        ),
    }[case]
    stub.write_text(
        "#!/bin/bash\n" + body + f'\nexec {real} "$@"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env["FACTORY_CLI"] = str(stub)
    return env


_PROMOTE_RUNTIME_SITES = [
    # (case, needs_promoting_inputs, expected_exit, detail_fragment)
    ("promote-refuses", True, 2, "no verdict rendered"),
    ("promote-silent-zero", True, 2, "wrote no verdict file"),
    ("promote-forked-output", True, 2, "stale/forged verdict refused"),
    ("promote-unreadable-verdict", True, 2, "verdict unreadable"),
    ("seal-refused", True, 2, "terminal resource seal refused"),
]


@pytest.mark.parametrize("case,inputs,expected_exit,fragment", _PROMOTE_RUNTIME_SITES)
def test_promote_refusal_sites_leave_exactly_one_row_at_runtime(
    tmp_path: Path, case: str, inputs: bool, expected_exit: int, fragment: str
) -> None:
    from tests.conftest import promoting_promotion_inputs, write_promoting_chain

    root = _make_run(tmp_path)
    if inputs:
        (root / "promotion_inputs.json").write_text(
            json.dumps(promoting_promotion_inputs(), indent=2), encoding="utf-8"
        )
        write_promoting_chain(root)
    env = _delegating_stub(tmp_path, case)
    (tmp_path / "root-marker").write_text(str(root), encoding="utf-8")
    r = run(["bash", str(HARNESS / "promote.sh"), "r1"], tmp_path, env)
    assert r.returncode == expected_exit, (case, r.returncode, r.stderr)
    rows = _refusal_events(root)
    assert [row["kind"] for row in rows] == ["refusal-promote"], (case, rows)
    assert fragment in str(rows[0]["detail"]), (case, rows[0])


def test_promote_metadata_site_leaves_exactly_one_row_at_runtime(tmp_path: Path) -> None:
    """The :55 metadata-66 site (round-5 scanner-only list)."""
    root = _make_run(tmp_path)
    doc = json.loads((root / "harness.json").read_text(encoding="utf-8"))
    doc["unexpected_field"] = True
    (root / "harness.json").write_text(json.dumps(doc), encoding="utf-8")
    r = run(["bash", str(HARNESS / "promote.sh"), "r1"], tmp_path, _factory_cli_env())
    assert r.returncode == 66
    rows = _refusal_events(root)
    assert [row["kind"] for row in rows] == ["refusal-promote"]
    assert rows[0]["exit_code"] == 66
