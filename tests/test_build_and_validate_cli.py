"""Real, non-mocked proof of the `build-and-validate` CLI subcommand (factory_runtime/cli.py).

`FactoryOrchestrator.build_and_validate` was previously reachable only from Python callers
(`tests/test_tessera_cli_integration.py`); this exercises the CLI door added on top of it as an
actual subprocess invocation, with real Ed25519/Tessera-signed artifacts on disk — no mocking of
the orchestrator, Tessera, or the subprocess boundary. It deliberately builds a SIMPLER fixture
than the giant repair-loop test: one clean, non-broken Coder attempt straight to PREVIEW, because
the repair-loop machinery is not what this test is proving. Ground truth for every artifact shape
is `tests/test_tessera_cli_integration.py`.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from factory_core.build_plan import (
    BuildPlan,
    BuildStep,
    OracleLink,
    PatternCatalog,
    PatternDefinition,
)
from factory_core.correction import LANE_CAPABILITY
from factory_core.evidence import EvidenceIntegrity
from factory_core.independence import (
    INDEPENDENCE_STRONGER,
    ROLE_CODER,
    ROLE_TESTER,
    ROLE_VALIDATOR,
    STRUCTURAL_MODE_ISOLATED,
    AgentIdentity,
    IndependenceRecord,
    StructuralModeRecord,
)
from factory_core.manifest import digest_bytes, digest_obj
from factory_core.monitors import (
    MONITOR_AUTHORSHIP_HUMAN,
    MONITOR_DERIVATION_SPECIFICATION,
    Monitor,
)
from factory_core.provenance import PhaseArtifact
from factory_core.target import load_target_manifest
from factory_runtime.acceptance_obligations import (
    AcceptanceObligationCatalog,
    validator_execution_digests,
)
from factory_runtime.authority import load_genesis
from factory_runtime.evidence_plane import DeterminismRecord, SurfaceEvidence
from factory_runtime.resume import derive_resume_checkpoint
from factory_runtime.target_state import normalize_repository_url
from factory_runtime.tessera import TesseraCli
from factory_runtime.workflow import FactoryWorkflow

RUNTIME_FIXTURES = Path(__file__).parent / "fixtures" / "runtime_agents"
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def _binary() -> Path:
    configured = os.environ.get("FACTORY_TESSERA_BIN")
    if not configured:
        pytest.skip("FACTORY_TESSERA_BIN is required for the real Tessera integration proof")
    binary = Path(configured)
    if not binary.is_file():
        pytest.fail(f"configured Tessera binary does not exist: {binary}")
    return binary


def _keypair(binary: Path, path: Path) -> str:
    subprocess.run(
        [str(binary), "keygen", "--output", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    encoded = path.read_text(encoding="utf-8").strip()
    assert len(encoded) == 128
    return encoded[64:]


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(REPOSITORY_ROOT), environment.get("PYTHONPATH", "")))
    )
    return subprocess.run(
        [sys.executable, "-m", "factory_runtime.cli", *args],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _build_fixture(tmp_path: Path, binary: Path):
    """Build one real, signed, ready-to-build run at OPERATIONAL_MATURITY_RATIFIED.

    Mirrors `test_real_runtime_reaches_preview_through_authority_isolation_tests_and_evidence`
    in tests/test_tessera_cli_integration.py through phase ratification, minus the repair-loop
    and deliberate-failure machinery this test does not exercise.
    """

    cli = TesseraCli((str(binary),))
    now = int(time.time())
    root_key = tmp_path / "root.hex"
    validator_key = tmp_path / "validator.hex"
    coder_key = tmp_path / "coder.hex"
    tester_key = tmp_path / "tester.hex"
    root_public_key = _keypair(binary, root_key)
    validator_public_key = _keypair(binary, validator_key)
    coder_public_key = _keypair(binary, coder_key)
    tester_public_key = _keypair(binary, tester_key)

    genesis_payload = {
        "schema_version": "factory-genesis/1",
        "repository_id": "cli-synthetic-factory-target",
        "policy_id": "cli-synthetic-authority/1",
        "root_public_key": root_public_key,
        "principals": [
            {
                "identity": "human:founder",
                "kind": "human",
                "public_key": root_public_key,
                "capabilities": [
                    "factory:authorize-target-resolution",
                    "factory:authorize-change",
                    "factory:ratify-product-specification",
                    "factory:ratify-architecture",
                    "factory:ratify-operational-maturity",
                    "factory:ratify-acceptance-obligation-catalog",
                    "factory:approve-promotion",
                    "factory:activate-policy",
                ],
            },
            {
                "identity": "agent:validator",
                "kind": "agent",
                "public_key": validator_public_key,
                "capabilities": [
                    "factory:ratify-product-specification",
                    "factory:ratify-architecture",
                    "factory:ratify-operational-maturity",
                    "factory:ratify-acceptance-obligation-catalog",
                ],
            },
            {
                "identity": "agent:coder",
                "kind": "agent",
                "public_key": coder_public_key,
                "capabilities": [],
            },
            {
                "identity": "agent:tester",
                "kind": "agent",
                "public_key": tester_public_key,
                "capabilities": [],
            },
        ],
        "bootstrap": {
            "enabled": True,
            "scope": ["authorize-target-resolution", "authorize-change", "activate-policy"],
            "deactivates_when": "the first replacement policy activation is consumed",
        },
        "issued_at": now,
    }
    genesis_path = tmp_path / "genesis.tessera.json"
    cli.wrap_json(
        genesis_payload, kind="factory-genesis", key_path=root_key, output_path=genesis_path
    )
    policy = load_genesis(genesis_path, trusted_root_public_key=root_public_key, tessera=cli)
    workflow = FactoryWorkflow(tmp_path / "runs", authority_policy=policy, tessera=cli)

    pattern = PatternDefinition(
        pattern_id="python-function",
        version="1",
        artifact_digest=digest_obj({"artifact": "python-function-v1"}),
        qualification_evidence_digest=digest_obj({"qualification": "python-function-v1"}),
        mechanism={
            "kind": "source-generator",
            "required_configuration": ["module", "function", "operation"],
        },
    )
    catalog = PatternCatalog(
        catalog_id="cli-synthetic-qualified-patterns", version="1", patterns=(pattern,)
    )
    catalog_path = tmp_path / "pattern-catalog.json"
    catalog_path.write_text(json.dumps(catalog.body()), encoding="utf-8")

    target_path = tmp_path / "target.toml"
    target_path.write_text(
        "\n".join(
            (
                'schema_version = "factory-target-manifest/2"',
                'target_id = "cli-synthetic-runtime"',
                "[repo]",
                'url = "https://example.invalid/cli-synthetic.git"',
                'ref = "main"',
                "[adapters]",
                'repo = "readonly_git"',
                'knowledge = "kin_reader"',
                'compliance = "rules_json"',
                'idp = "oidc"',
                'artifact_sink = "local_fs"',
                "[compliance]",
                'rules_path = "compliance/rules.json"',
                "[build]",
                f'pattern_catalog_digest = "{catalog.content_digest}"',
                "max_attempts = 2",
                'construction_modes = ["regenerate", "brownfield"]',
                "",
                "[build.signal]",
                "signal_pass_deadline = 2",
                "signal_pass_warn = 1",
                "signal_wall_clock_cap_hours = 24",
                "",
            )
        ),
        encoding="utf-8",
    )
    manifest = load_target_manifest(target_path)
    target_digest = manifest.source_digest
    verbatim = "Build the CLI-synthetic authorized Factory change."
    source_digest = digest_bytes(verbatim.encode())

    operator_source = tmp_path / "operator-source"
    subprocess.run(
        ["git", "init", "-b", "main", str(operator_source)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(operator_source), "config", "user.email", "factory@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(operator_source), "config", "user.name", "Factory Test"], check=True
    )
    (operator_source / "README.md").write_text("cli synthetic target\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(operator_source), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(operator_source), "commit", "-m", "cli synthetic target"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(operator_source), "remote", "add", "origin", str(manifest.repo["url"])],
        check=True,
    )

    resolution_request = {
        "schema_version": "factory-target-resolution-request/1",
        "request_id": "cli-synthetic-resolution",
        "run_id": "cli-synthetic-run",
        "repository_id": "cli-synthetic-factory-target",
        "generation": 1,
        "target_manifest_digest": target_digest,
        "normalized_url": normalize_repository_url(str(manifest.repo["url"])),
        "requested_ref": str(manifest.repo["ref"]),
        "subpath": "",
        "allowed_contact_operations": ["git-local-object-read"],
        "lane_execution": False,
        "nonce": "cli-synthetic-resolution-nonce",
        "created_at": now,
        "expires_at": now + 600,
    }
    resolution_request_path = tmp_path / "resolution-request.json"
    resolution_request_path.write_text(json.dumps(resolution_request), encoding="utf-8")
    resolution_receipt_path = tmp_path / "resolution.tessera.json"
    cli.wrap_json(
        {
            "schema_version": "factory-authority-receipt/1",
            "receipt_id": "resolve-cli-synthetic",
            "run_id": "cli-synthetic-run",
            "repository_id": "cli-synthetic-factory-target",
            "action": "authorize-target-resolution",
            "subject_digest": digest_obj(resolution_request),
            "signer_identity": "human:founder",
            "capabilities": ["factory:authorize-target-resolution"],
            "issued_at": now,
            "expires_at": now + 600,
            "nonce": "cli-synthetic-resolution-nonce",
        },
        kind="factory-authority-receipt",
        key_path=root_key,
        output_path=resolution_receipt_path,
    )
    workflow.authorize_target_resolution(
        "cli-synthetic-run",
        manifest_path=target_path,
        request_path=resolution_request_path,
        receipt_path=resolution_receipt_path,
    )
    resolved = workflow.resolve_target("cli-synthetic-run", object_source=operator_source)

    request = {
        "schema_version": "factory-execution-request/1",
        "request_id": "cli-synthetic-request",
        "run_id": "cli-synthetic-run",
        "repository_id": "cli-synthetic-factory-target",
        "generation": resolved.generation,
        "target_manifest_digest": target_digest,
        "target_state_digest": resolved.target_state_digest,
        "resolved_commit": resolved.target_state["resolved_commit"],
        "proposed_by": "human:founder",
        "verbatim_request": verbatim,
        "verbatim_request_digest": source_digest,
        "requested_outcome": "Prove the build-and-validate CLI subcommand end to end.",
        "surfaces": [
            {
                "surface_id": "cli-synthetic-control-plane",
                "proposed_criticality": "critical",
                "reason": "This test exercises authorization.",
            }
        ],
        "created_at": now,
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    authorize_receipt_path = tmp_path / "authorize.tessera.json"
    cli.wrap_json(
        {
            "schema_version": "factory-authority-receipt/1",
            "receipt_id": "authorize-cli-synthetic",
            "run_id": "cli-synthetic-run",
            "repository_id": "cli-synthetic-factory-target",
            "action": "authorize-change",
            "subject_digest": digest_obj(request),
            "signer_identity": "human:founder",
            "capabilities": ["factory:authorize-change"],
            "issued_at": now,
            "expires_at": now + 600,
            "nonce": "authorize-cli-synthetic-nonce",
        },
        kind="factory-authority-receipt",
        key_path=root_key,
        output_path=authorize_receipt_path,
    )
    workflow.authorize_change(
        "cli-synthetic-run", request_path=request_path, receipt_path=authorize_receipt_path
    )

    phases = (
        ("product-specification", "ratify-product-specification"),
        ("architecture", "ratify-architecture"),
        ("operational-maturity", "ratify-operational-maturity"),
    )
    phase_items = {
        "product-specification": (
            "product:addition",
            "The product adds integers and returns their mathematical sum.",
        ),
        "architecture": (
            "architecture:interface",
            "The public Python interface is calculator.py:add(left: int, right: int).",
        ),
        "operational-maturity": (
            "test:addition",
            "Acceptance examples cover positive and negative integer addition.",
        ),
    }
    phase_artifacts: dict[str, PhaseArtifact] = {}
    for index, (phase, action) in enumerate(phases, start=1):
        item_id, statement = phase_items[phase]
        phase_document = {
            "artifact_id": f"cli-synthetic-{phase}",
            "phase": phase,
            "version": "1",
            "source_digest": source_digest,
            "human_ratifier": "human:founder",
            "validator_ratifier": "agent:validator",
            "items": [{"item_id": item_id, "canonical_statement": statement, "supersedes": []}],
        }
        phase_artifact = PhaseArtifact.from_dict(phase_document)
        phase_artifacts[phase] = phase_artifact
        phase_path = tmp_path / f"{phase}.json"
        phase_path.write_text(json.dumps(phase_document), encoding="utf-8")

        receipt_base = {
            "schema_version": "factory-authority-receipt/1",
            "run_id": "cli-synthetic-run",
            "repository_id": "cli-synthetic-factory-target",
            "action": action,
            "subject_digest": phase_artifact.content_digest,
            "capabilities": [f"factory:{action}"],
            "issued_at": now,
            "expires_at": now + 600,
        }
        human_receipt_path = tmp_path / f"{phase}.human.tessera.json"
        cli.wrap_json(
            {
                **receipt_base,
                "receipt_id": f"{phase}-human",
                "signer_identity": "human:founder",
                "nonce": f"{phase}-human-nonce-{index}",
            },
            kind="factory-authority-receipt",
            key_path=root_key,
            output_path=human_receipt_path,
        )
        validator_receipt_path = tmp_path / f"{phase}.validator.tessera.json"
        cli.wrap_json(
            {
                **receipt_base,
                "receipt_id": f"{phase}-validator",
                "signer_identity": "agent:validator",
                "nonce": f"{phase}-validator-nonce-{index}",
            },
            kind="factory-authority-receipt",
            key_path=validator_key,
            output_path=validator_receipt_path,
        )
        workflow.ratify_phase(
            "cli-synthetic-run",
            artifact_path=phase_path,
            human_receipt_path=human_receipt_path,
            validator_receipt_path=validator_receipt_path,
        )

    ratified = workflow.store.load("cli-synthetic-run")
    product = phase_artifacts["product-specification"]
    architecture = phase_artifacts["architecture"]
    operations = phase_artifacts["operational-maturity"]
    product_reference = product.backreference(product.items[0])
    architecture_reference = architecture.backreference(architecture.items[0])
    oracle_reference = operations.backreference(operations.items[0])
    authority = tuple(phase_artifacts[phase] for phase, _ in phases)
    from factory_runtime.generation import build_input_document

    build_input = build_input_document("cli-synthetic-run", target_digest, authority)
    plan = BuildPlan(
        plan_id="cli-synthetic-plan",
        version="1",
        run_id="cli-synthetic-run",
        target_digest=target_digest,
        construction_mode="regenerate",
        max_build_attempts=2,
        build_input_digest=digest_obj(build_input),
        pattern_catalog_digest=catalog.content_digest,
        phase_artifact_digests={artifact.phase: artifact.content_digest for artifact in authority},
        steps=(
            BuildStep(
                step_id="generate-calculator",
                pattern_id=pattern.pattern_id,
                pattern_digest=pattern.content_digest,
                configuration={
                    "module": "calculator.py",
                    "function": "add",
                    "operation": "integer-addition",
                },
                intent_backreferences=(product_reference, architecture_reference),
            ),
        ),
        oracle_links=(
            OracleLink(product_reference, oracle_reference),
            OracleLink(architecture_reference, oracle_reference),
        ),
    )
    plan_path = tmp_path / "build-plan.json"
    plan_path.write_text(json.dumps(plan.body()), encoding="utf-8")

    validator_command = (sys.executable, str(RUNTIME_FIXTURES / "validator.py"))
    command_digest, configuration_digest, environment_digest = validator_execution_digests(
        validator_command, trusted_paths=(RUNTIME_FIXTURES / "validator.py",)
    )
    examples = (("AC-1", 2, 3, 5), ("AC-2", -7, 4, -3))
    acceptance_catalog_document = {
        "schema_version": "factory-acceptance-obligation-catalog/1",
        "catalog_id": "cli-synthetic-acceptance",
        "version": "1",
        "run_id": "cli-synthetic-run",
        "generation": ratified.generation,
        "target_state_digest": ratified.target_state_digest,
        "phase_artifact_digests": dict(ratified.phase_artifact_digests),
        "human_ratifier": "human:founder",
        "validator_ratifier": "agent:validator",
        "max_review_rounds": 2,
        "triggers": [
            {
                "trigger_id": "validating-to-preview",
                "from_state": "validating",
                "to_state": "preview",
                "command_digest": command_digest,
                "configuration_digest": configuration_digest,
                "environment_digest": environment_digest,
                "obligations": [
                    {
                        "obligation_id": "integer-addition-examples",
                        "criterion": (
                            "Every ratified integer-addition example passes against the exact "
                            "candidate and independently authored test snapshot."
                        ),
                        "verifier_id": "validator-test-execution-v1",
                        "intent_backreferences": [
                            product.backreference(product.items[0]).to_dict(),
                            operations.backreference(operations.items[0]).to_dict(),
                        ],
                        "required_evidence_ids": [
                            "candidate",
                            "acceptance-tests",
                            "coder-output-snapshot",
                            "tester-output-snapshot",
                        ],
                        "test_assertions": [
                            {
                                "test_id": test_id,
                                "assertion_digest": digest_obj(
                                    {
                                        "test_id": test_id,
                                        "left": left,
                                        "right": right,
                                        "expected": expected,
                                    }
                                ),
                            }
                            for test_id, left, right, expected in examples
                        ],
                    }
                ],
            }
        ],
    }
    acceptance_catalog = AcceptanceObligationCatalog.from_dict(acceptance_catalog_document)
    resume_config = tmp_path / "resume-config.json"
    resume_config.write_text('{"runner":"synthetic"}\n', encoding="utf-8")
    resume_checkpoint_document = derive_resume_checkpoint(
        workflow.root,
        "cli-synthetic-run",
        checkpoint_id="cli-synthetic-checkpoint-1",
        previous_checkpoint_digest="",
        genesis_path=genesis_path,
        trusted_root_public_key=root_public_key,
        tessera=cli,
        configuration_sources={"runner": resume_config},
        acceptance_obligation_catalog_digest=acceptance_catalog.content_digest,
        retention={
            "policy_id": "cli-synthetic-retention-1",
            "mode": "retain-indefinitely",
            "retain_until": 0,
            "metadata_classes": ["authority-envelopes", "lifecycle-ledger", "resource-ledger"],
            "erasure_authority": "human:founder",
        },
        clock=lambda: now,
    )
    resume_checkpoint = tmp_path / "resume-checkpoint.json"
    resume_checkpoint.write_text(json.dumps(resume_checkpoint_document), encoding="utf-8")
    acceptance_catalog_path = tmp_path / "acceptance-obligation-catalog.json"
    acceptance_catalog_path.write_text(json.dumps(acceptance_catalog_document), encoding="utf-8")
    acceptance_receipt_base = {
        "schema_version": "factory-authority-receipt/1",
        "run_id": "cli-synthetic-run",
        "repository_id": "cli-synthetic-factory-target",
        "action": "ratify-acceptance-obligation-catalog",
        "subject_digest": acceptance_catalog.content_digest,
        "capabilities": ["factory:ratify-acceptance-obligation-catalog"],
        "issued_at": now,
        "expires_at": now + 600,
    }
    acceptance_human_receipt_path = tmp_path / "acceptance.human.tessera.json"
    cli.wrap_json(
        {
            **acceptance_receipt_base,
            "receipt_id": "acceptance-human",
            "signer_identity": "human:founder",
            "nonce": "acceptance-human-nonce",
        },
        kind="factory-authority-receipt",
        key_path=root_key,
        output_path=acceptance_human_receipt_path,
    )
    acceptance_validator_receipt_path = tmp_path / "acceptance.validator.tessera.json"
    cli.wrap_json(
        {
            **acceptance_receipt_base,
            "receipt_id": "acceptance-validator",
            "signer_identity": "agent:validator",
            "nonce": "acceptance-validator-nonce",
        },
        kind="factory-authority-receipt",
        key_path=validator_key,
        output_path=acceptance_validator_receipt_path,
    )

    surface_evidence = (
        SurfaceEvidence(
            surface_id="cli-synthetic-control-plane",
            criticality="critical",
            oracle_adequate=True,
            required_evidence_ids=("acceptance-tests",),
            evidence_digests={},
        ),
    )
    determinism_records = (
        DeterminismRecord(
            surface_id="cli-synthetic-control-plane",
            criticality="critical",
            deterministic=True,
            flake_count=0,
            automatic_retry_count=0,
        ),
    )
    structural_mode = StructuralModeRecord(
        mode=STRUCTURAL_MODE_ISOLATED,
        decision_package_note="The CLI-synthetic lanes ran fully isolated.",
    )
    independence = IndependenceRecord(
        agents=(
            AgentIdentity(
                role=ROLE_CODER,
                model_family="synthetic-a",
                model_version="fixture-1",
                directive_version="coder-fixture-1",
            ),
            AgentIdentity(
                role=ROLE_TESTER,
                model_family="synthetic-b",
                model_version="fixture-1",
                directive_version="tester-fixture-1",
            ),
            AgentIdentity(
                role=ROLE_VALIDATOR,
                model_family="synthetic-c",
                model_version="fixture-1",
                directive_version="validator-fixture-1",
            ),
        ),
        shared_context=False,
        channel_open=False,
        claimed_tier=INDEPENDENCE_STRONGER,
        structural_mode=replace(
            structural_mode,
            mutation_evidence=EvidenceIntegrity(
                body=structural_mode.authority_body(),
                claimed_digest=digest_obj(structural_mode.authority_body()),
            ),
        ),
    )
    monitors = (
        Monitor(
            monitor_id="monitor-cli-synthetic-control-plane",
            surface_id="cli-synthetic-control-plane",
            derivation=MONITOR_DERIVATION_SPECIFICATION,
            authorship=MONITOR_AUTHORSHIP_HUMAN,
            author_identity="human:founder",
            backreference=product.backreference(product.items[0]),
            actionable_conclusion="Page the control-plane owner with the unmet criterion.",
            notifies_human=True,
        ),
    )

    surface_evidence_path = tmp_path / "surface-evidence.json"
    surface_evidence_path.write_text(
        json.dumps([item.to_dict() for item in surface_evidence]), encoding="utf-8"
    )
    determinism_records_path = tmp_path / "determinism-records.json"
    determinism_records_path.write_text(
        json.dumps([item.to_dict() for item in determinism_records]), encoding="utf-8"
    )
    independence_path = tmp_path / "independence.json"
    independence_path.write_text(json.dumps(independence.to_dict()), encoding="utf-8")
    monitors_path = tmp_path / "monitors.json"
    monitors_path.write_text(json.dumps([item.to_dict() for item in monitors]), encoding="utf-8")

    return {
        "workflow": workflow,
        "runs_root": tmp_path / "runs",
        "genesis_path": genesis_path,
        "root_public_key": root_public_key,
        "target_path": target_path,
        "catalog_path": catalog_path,
        "plan_path": plan_path,
        "acceptance_catalog_path": acceptance_catalog_path,
        "acceptance_human_receipt_path": acceptance_human_receipt_path,
        "acceptance_validator_receipt_path": acceptance_validator_receipt_path,
        "resume_checkpoint_path": resume_checkpoint,
        "expected_resume_checkpoint_digest": digest_obj(resume_checkpoint_document),
        "resume_config_path": resume_config,
        "validator_key_path": validator_key,
        "surface_evidence_path": surface_evidence_path,
        "determinism_records_path": determinism_records_path,
        "independence_path": independence_path,
        "monitors_path": monitors_path,
    }


def _cli_args(fixture: dict, *, attempt_id: str, broken: bool) -> list[str]:
    coder_args = [sys.executable, str(RUNTIME_FIXTURES / "coder.py")]
    if broken:
        coder_args.append("--broken")
    args = [
        "build-and-validate",
        "--genesis",
        str(fixture["genesis_path"]),
        "--root-public-key",
        fixture["root_public_key"],
        "--tessera-bin",
        os.environ["FACTORY_TESSERA_BIN"],
        "--runs",
        str(fixture["runs_root"]),
        "--run-id",
        "cli-synthetic-run",
        "--attempt-id",
        attempt_id,
        "--target-manifest",
        str(fixture["target_path"]),
        "--pattern-catalog",
        str(fixture["catalog_path"]),
        "--build-plan",
        str(fixture["plan_path"]),
        "--acceptance-catalog",
        str(fixture["acceptance_catalog_path"]),
        "--acceptance-catalog-human-receipt",
        str(fixture["acceptance_human_receipt_path"]),
        "--acceptance-catalog-validator-receipt",
        str(fixture["acceptance_validator_receipt_path"]),
    ]
    for token in coder_args:
        args += ["--coder-command-arg", token]
    for token in (sys.executable, str(RUNTIME_FIXTURES / "tester.py")):
        args += ["--tester-command-arg", token]
    for token in (sys.executable, str(RUNTIME_FIXTURES / "validator.py")):
        args += ["--validator-command-arg", token]
    args += ["--coder-trusted-path", str(RUNTIME_FIXTURES / "coder.py")]
    args += ["--tester-trusted-path", str(RUNTIME_FIXTURES / "tester.py")]
    args += ["--validator-trusted-path", str(RUNTIME_FIXTURES / "validator.py")]
    args += [
        "--resume-checkpoint",
        str(fixture["resume_checkpoint_path"]),
        "--expected-resume-checkpoint-digest",
        fixture["expected_resume_checkpoint_digest"],
        "--resume-config-source",
        f"runner={fixture['resume_config_path']}",
        "--implementer-identity",
        "agent:coder",
        "--tester-identity",
        "agent:tester",
        "--verifier-identity",
        "agent:validator",
        "--verifier-key",
        str(fixture["validator_key_path"]),
        "--surface-evidence",
        str(fixture["surface_evidence_path"]),
        "--determinism-records",
        str(fixture["determinism_records_path"]),
        "--lane",
        LANE_CAPABILITY,
        "--independence",
        str(fixture["independence_path"]),
        "--monitors",
        str(fixture["monitors_path"]),
        "--monitor-declared-unit-count",
        "75",
    ]
    return args


@pytest.mark.tessera_integration
@pytest.mark.skipif(
    platform.system() != "Darwin", reason="full runtime E2E requires macOS Seatbelt"
)
def test_cli_build_and_validate_reaches_preview(tmp_path: Path) -> None:
    binary = _binary()
    fixture = _build_fixture(tmp_path, binary)

    result = _run_cli(*_cli_args(fixture, attempt_id="attempt-1", broken=False))

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert payload["repair_signal"] == "pass"
    assert payload["run_state"] == "preview"
    for key in (
        "candidate_digest",
        "tests_digest",
        "acceptance_report_digest",
        "adversarial_review_digest",
    ):
        assert payload[key].startswith("sha256:")
        assert len(payload[key]) == len("sha256:") + 64

    assert fixture["workflow"].store.load("cli-synthetic-run").state.value == "preview"


@pytest.mark.tessera_integration
@pytest.mark.skipif(
    platform.system() != "Darwin", reason="full runtime E2E requires macOS Seatbelt"
)
def test_cli_build_and_validate_fails_closed_on_invalid_attempt_id(tmp_path: Path) -> None:
    binary = _binary()
    fixture = _build_fixture(tmp_path, binary)

    result = _run_cli(*_cli_args(fixture, attempt_id="../escape", broken=False))

    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ""
    assert "factory: refused:" in result.stderr
    assert "attempt_id" in result.stderr
    assert (
        fixture["workflow"].store.load("cli-synthetic-run").state.value
        == "operational-maturity-ratified"
    )


def test_cli_preflight_no_leaves_zero_new_files(tmp_path: Path) -> None:
    """Plan §1.1 forcing test: a preflight hard NO at dispatch fires before
    catalog parse, resume verification, retention, and prepare — the refused
    dispatch leaves ZERO new files under the run root (§1.1d's enabler)."""
    binary = _binary()
    fixture = _build_fixture(tmp_path, binary)

    # Keep the schema/2 manifest valid while making its deadline exceed the ratified build
    # attempt ceiling: this reaches the preflight's cross-field hard NO instead of stopping at
    # target-schema parsing.
    target_path = fixture["target_path"]
    text = target_path.read_text(encoding="utf-8")
    target_path.write_text(
        text.replace("signal_pass_deadline = 2", "signal_pass_deadline = 3", 1),
        encoding="utf-8",
    )

    run_root = fixture["runs_root"] / "cli-synthetic-run"
    before = sorted(str(p) for p in run_root.rglob("*"))

    result = _run_cli(*_cli_args(fixture, attempt_id="attempt-1", broken=False))

    assert result.returncode == 2, result.stdout + result.stderr
    assert "preflight refused" in result.stderr
    assert "signal-pass-deadline-exceeds-max-attempts" in result.stderr
    after = sorted(str(p) for p in run_root.rglob("*"))
    assert after == before, "a preflight NO must leave zero new files"
    assert (
        fixture["workflow"].store.load("cli-synthetic-run").state.value
        == "operational-maturity-ratified"
    )
