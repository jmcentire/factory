"""Command-line boundary for the executable Factory runtime."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from factory_core.manifest import digest_bytes, digest_obj
from factory_core.provenance import PhaseArtifact
from factory_core.target import load_target_manifest_bytes
from factory_runtime.authority import load_genesis
from factory_runtime.broker import TypedOperationBroker, load_broker_registry
from factory_runtime.isolation import MacOSSandbox
from factory_runtime.orchestrator_projection import build_orchestrator_projection
from factory_runtime.projection_bundle import bundle_runner_projection
from factory_runtime.resources import ResourceLedger
from factory_runtime.resume import derive_resume_checkpoint, verify_resume_checkpoint
from factory_runtime.runner import (
    HardenedModelRunner,
    NamedSecretStore,
    RunnerError,
    RunnerManifest,
)
from factory_runtime.runner_isolation import MacOSNetworkedRunner
from factory_runtime.schema import SCHEMA_NAMES, validate_document
from factory_runtime.state import RunStore
from factory_runtime.state_admission import (
    StateAdmissionError,
    dependency_rule,
    derive_state_capsule,
    profile_digest,
    read_stable_regular_bytes,
    verify_state_capsule,
)
from factory_runtime.state_qualification import (
    execute_state_qualification_observations,
    qualify_state_observations,
    verify_state_qualification_report,
)
from factory_runtime.target_state import verify_target_state
from factory_runtime.tessera import TesseraCli
from factory_runtime.workflow import FactoryWorkflow

_MAX_INLINE_JSON_BYTES = 65_536
_MAX_BOUNDARY_FILE_BYTES = 5_242_880
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _tessera(path: str) -> TesseraCli:
    return TesseraCli((str(Path(path).expanduser()),))


def _add_authority_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--genesis", required=True, help="signed Tessera genesis envelope")
    parser.add_argument(
        "--root-public-key",
        default=os.environ.get("FACTORY_ROOT_PUBLIC_KEY", ""),
        required="FACTORY_ROOT_PUBLIC_KEY" not in os.environ,
        help="externally pinned founder Ed25519 public key",
    )
    parser.add_argument("--tessera-bin", default="tessera", help="Tessera executable path")


def _load_workflow(arguments: argparse.Namespace) -> FactoryWorkflow:
    tessera = _tessera(arguments.tessera_bin)
    policy = load_genesis(
        arguments.genesis,
        trusted_root_public_key=arguments.root_public_key,
        tessera=tessera,
    )
    return FactoryWorkflow(
        arguments.runs,
        authority_policy=policy,
        tessera=tessera,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="factory",
        description="Run the authorized, evidence-producing Software Factory state machine.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser(
        "validate-document",
        help="validate one runtime JSON artifact against its closed schema",
    )
    validate.add_argument("--schema", required=True, choices=sorted(SCHEMA_NAMES))
    validate.add_argument("--input", required=True)

    digest = commands.add_parser(
        "digest-json",
        help="print the canonical content address of a JSON object",
    )
    digest.add_argument("--input", required=True)

    inspect_target = commands.add_parser(
        "inspect-target",
        help="validate a target manifest and print its content/source addresses and build ABI",
    )
    inspect_target.add_argument("--manifest", required=True)

    status = commands.add_parser("status", help="verify and print an authoritative run projection")
    status.add_argument("--runs", required=True)
    status.add_argument("--run-id", required=True)

    rebuild = commands.add_parser(
        "rebuild-projection",
        help="rebuild run.json only from a verified ledger",
    )
    rebuild.add_argument("--runs", required=True)
    rebuild.add_argument("--run-id", required=True)

    verify_genesis = commands.add_parser(
        "verify-genesis",
        help="verify a signed genesis against an externally pinned root",
    )
    _add_authority_arguments(verify_genesis)

    authorize_resolution = commands.add_parser(
        "authorize-target-resolution",
        help="authorize bounded read-only resolution of one manifest URL and ref",
    )
    authorize_resolution.add_argument("--runs", required=True)
    authorize_resolution.add_argument("--run-id", required=True)
    authorize_resolution.add_argument("--manifest", required=True)
    authorize_resolution.add_argument("--request", required=True)
    authorize_resolution.add_argument("--receipt", required=True)
    _add_authority_arguments(authorize_resolution)

    resolve_target = commands.add_parser(
        "resolve-target",
        help="resolve the retained Stage-R subject into a run-owned exact checkout",
    )
    resolve_target.add_argument("--runs", required=True)
    resolve_target.add_argument("--run-id", required=True)
    resolve_target.add_argument(
        "--object-source",
        help="optional verified read-only local Git object source (never a lane checkout)",
    )
    _add_authority_arguments(resolve_target)

    authorize = commands.add_parser(
        "authorize-change",
        help="create intake from an exact-target execution request and human receipt",
    )
    authorize.add_argument("--runs", required=True)
    authorize.add_argument("--run-id", required=True)
    authorize.add_argument("--request", required=True)
    authorize.add_argument("--receipt", required=True)
    _add_authority_arguments(authorize)

    ratify = commands.add_parser(
        "ratify-phase",
        help="ratify one invariant document with human and Validator receipts",
    )
    ratify.add_argument("--runs", required=True)
    ratify.add_argument("--run-id", required=True)
    ratify.add_argument("--artifact", required=True)
    ratify.add_argument("--human-receipt", required=True)
    ratify.add_argument("--validator-receipt", required=True)
    _add_authority_arguments(ratify)

    verify_target = commands.add_parser(
        "verify-target-state",
        help="re-derive the retained target-state and immutable baseline checkout",
    )
    verify_target.add_argument("--runs", required=True)
    verify_target.add_argument("--run-id", required=True)

    verify_execution = commands.add_parser(
        "verify-execution-request",
        help="re-derive retained Stage-E request bytes against the authoritative intake entry",
    )
    verify_execution.add_argument("--runs", required=True)
    verify_execution.add_argument("--run-id", required=True)
    verify_execution.add_argument(
        "--task-file",
        default="",
        help="also require these exact bytes to equal the signed Stage-E verbatim request",
    )

    verify_resources = commands.add_parser(
        "verify-resources",
        help="verify the run resource ledger, optionally requiring terminal disposition",
    )
    verify_resources.add_argument("--runs", required=True)
    verify_resources.add_argument("--run-id", required=True)
    verify_resources.add_argument("--for-close", action="store_true")
    verify_resources.add_argument(
        "--seal",
        action="store_true",
        help="durably seal the verified closeable head against later resource events",
    )
    verify_resources.add_argument(
        "--actor",
        default="",
        help="required identity label when --seal is used",
    )

    derive_resume = commands.add_parser(
        "derive-resume-checkpoint",
        help="derive a checkpoint for independent custody (derivation does not anchor it)",
    )
    derive_resume.add_argument("--runs", required=True)
    derive_resume.add_argument("--run-id", required=True)
    derive_resume.add_argument("--checkpoint-id", required=True)
    derive_resume.add_argument("--previous-checkpoint-digest", default="")
    derive_resume.add_argument("--acceptance-obligation-catalog-digest", default=None)
    derive_resume.add_argument("--config-source", action="append", default=[], metavar="NAME=PATH")
    derive_resume.add_argument("--retention-policy-id", required=True)
    derive_resume.add_argument(
        "--retention-mode",
        required=True,
        choices=("retain-indefinitely", "retain-until", "erase-on-close"),
    )
    derive_resume.add_argument("--retain-until", type=int, default=0)
    derive_resume.add_argument("--erasure-authority", required=True)
    derive_resume.add_argument("--output", required=True)
    _add_authority_arguments(derive_resume)

    verify_resume = commands.add_parser(
        "verify-resume-checkpoint",
        help="verify an independently pinned checkpoint before grounding or dispatch",
    )
    verify_resume.add_argument("--runs", required=True)
    verify_resume.add_argument("--run-id", required=True)
    verify_resume.add_argument("--checkpoint", required=True)
    verify_resume.add_argument("--checkpoint-digest", required=True)
    verify_resume.add_argument("--acceptance-obligation-catalog-digest", default=None)
    verify_resume.add_argument("--config-source", action="append", default=[], metavar="NAME=PATH")
    verify_resume.add_argument(
        "--accepted-previous-checkpoint-digest",
        action="append",
        default=[],
    )
    _add_authority_arguments(verify_resume)

    qualify_state = commands.add_parser(
        "qualify-state",
        help="execute and retain the code-owned state-admission qualification matrix",
    )
    qualify_state.add_argument("--runner-configuration-digest", required=True)
    qualify_state.add_argument("--qualification-id", required=True)
    qualify_state.add_argument("--observations-output", required=True)
    qualify_state.add_argument("--output", required=True)

    bundle_projection = commands.add_parser(
        "bundle-runner-projection",
        help="freeze one asymmetric lane tree into a bounded path-free model projection",
    )
    bundle_projection.add_argument("--runs", required=True)
    bundle_projection.add_argument("--run-id", required=True)
    bundle_projection.add_argument("--role", required=True, choices=("coder", "tester"))
    bundle_projection.add_argument("--projection-root", required=True)
    bundle_projection.add_argument("--projection-receipt", required=True)
    bundle_projection.add_argument("--output", required=True)

    bundle_orchestrator = commands.add_parser(
        "bundle-orchestrator-projection",
        help="freeze one advisory wake into a bounded path-free structured projection",
    )
    bundle_orchestrator.add_argument("--runs", required=True)
    bundle_orchestrator.add_argument("--run-id", required=True)
    bundle_orchestrator.add_argument("--checkpoint", required=True)
    bundle_orchestrator.add_argument("--checkpoint-digest", required=True)
    bundle_orchestrator.add_argument(
        "--section", action="append", default=[], metavar="NAME=PATH"
    )
    bundle_orchestrator.add_argument("--output", required=True)
    bundle_orchestrator.add_argument("--capsule-output", required=True)
    bundle_orchestrator.add_argument(
        "--config-source", action="append", default=[], metavar="NAME=PATH"
    )
    bundle_orchestrator.add_argument(
        "--accepted-previous-checkpoint-digest", action="append", default=[]
    )
    _add_authority_arguments(bundle_orchestrator)

    run_model = commands.add_parser(
        "run-model",
        help="dispatch a qualified closed-environment model from a path-free projection",
    )
    run_model.add_argument("--runs", required=True)
    run_model.add_argument("--run-id", required=True)
    run_model.add_argument(
        "--role", required=True, choices=("coder", "tester", "validator")
    )
    run_model.add_argument("--receipt-id", required=True)
    run_model.add_argument("--runner-manifest", required=True)
    run_model.add_argument("--runner-manifest-digest", required=True)
    run_model.add_argument("--runner-config-source-name", required=True)
    run_model.add_argument("--projection", required=True)
    run_model.add_argument("--output-schema", required=True)
    run_model.add_argument("--output-schema-digest", required=True)
    run_model.add_argument("--output-schema-config-source-name", required=True)
    run_model.add_argument("--task-file", required=True)
    run_model.add_argument("--task-digest", required=True)
    run_model.add_argument("--role-primer", required=True)
    run_model.add_argument("--broker-registry", required=True)
    run_model.add_argument("--broker-registry-digest", required=True)
    run_model.add_argument("--broker-registry-config-source-name", required=True)
    run_model.add_argument("--state-qualification-observations", required=True)
    run_model.add_argument(
        "--state-qualification-observations-config-source-name", required=True
    )
    run_model.add_argument("--state-qualification-report", required=True)
    run_model.add_argument("--state-qualification-config-source-name", required=True)
    run_model.add_argument("--workspace", required=True)
    run_model.add_argument("--secret-root", required=True)
    run_model.add_argument("--checkpoint", required=True)
    run_model.add_argument("--checkpoint-digest", required=True)
    run_model.add_argument("--config-source", action="append", default=[], metavar="NAME=PATH")
    run_model.add_argument(
        "--accepted-previous-checkpoint-digest",
        action="append",
        default=[],
    )
    _add_authority_arguments(run_model)

    execute_broker = commands.add_parser(
        "execute-broker-handoff",
        help="execute only externally anchored typed operations from a qualified runner handoff",
    )
    execute_broker.add_argument("--runs", required=True)
    execute_broker.add_argument("--run-id", required=True)
    execute_broker.add_argument(
        "--role", required=True, choices=("coder", "tester", "validator")
    )
    execute_broker.add_argument("--receipt-id", required=True)
    execute_broker.add_argument("--runner-receipt", required=True)
    execute_broker.add_argument("--handoff", required=True)
    execute_broker.add_argument("--state-capsule", required=True)
    execute_broker.add_argument("--registry", required=True)
    execute_broker.add_argument("--registry-digest", required=True)
    execute_broker.add_argument("--registry-config-source-name", required=True)
    execute_broker.add_argument("--checkpoint", required=True)
    execute_broker.add_argument("--checkpoint-digest", required=True)
    execute_broker.add_argument(
        "--config-source", action="append", default=[], metavar="NAME=PATH"
    )
    execute_broker.add_argument(
        "--accepted-previous-checkpoint-digest",
        action="append",
        default=[],
    )
    _add_authority_arguments(execute_broker)

    record_resource = commands.add_parser(
        "record-resource",
        help="append one validated run-resource lifecycle event",
    )
    record_resource.add_argument("--runs", required=True)
    record_resource.add_argument("--run-id", required=True)
    record_resource.add_argument("--resource-id", required=True)
    record_resource.add_argument("--resource-type", required=True)
    record_resource.add_argument("--identifier", required=True)
    record_resource.add_argument("--creator-action", required=True)
    record_resource.add_argument(
        "--ownership", required=True, choices=("run-owned", "external-non-owned")
    )
    record_resource.add_argument("--baseline-json", default="{}")
    record_resource.add_argument("--disposition-json", default="{}")
    record_resource.add_argument("--evidence-json", default="{}")
    record_resource.add_argument("--status", required=True)
    record_resource.add_argument("--actor", required=True)

    disposition_resource = commands.add_parser(
        "disposition-resource",
        help="append a terminal disposition carrying forward a resource's immutable identity",
    )
    disposition_resource.add_argument("--runs", required=True)
    disposition_resource.add_argument("--run-id", required=True)
    disposition_resource.add_argument("--resource-id", required=True)
    disposition_resource.add_argument(
        "--status", required=True, choices=("retained", "removed", "disposed", "failed")
    )
    disposition_resource.add_argument("--reason", required=True)
    disposition_resource.add_argument(
        "--residue", required=True, choices=("true", "false")
    )
    disposition_resource.add_argument("--evidence-json", default="{}")
    disposition_resource.add_argument("--actor", required=True)

    wrap = commands.add_parser(
        "tessera-wrap",
        help="sign a JSON object in a Factory-bound Tessera envelope",
    )
    wrap.add_argument("--payload", required=True)
    wrap.add_argument("--kind", required=True)
    wrap.add_argument("--key", required=True)
    wrap.add_argument("--output", required=True)
    wrap.add_argument("--tessera-bin", default="tessera")

    # Gate L — the sole harness-close path. Renders the pure decide_promotion verdict for a
    # run and writes promotion_verdict.json; promote.sh is the sole writer of harness.json
    # status="closed" and reads this verdict. It never mutates authoritative run.json. No
    # genesis/tessera is required: promotion only needs decide_promotion, which is pure; the
    # authority/intake concerns live in the workflow commands that gather promotion_inputs.json.
    promote = commands.add_parser(
        "promote",
        help="render the sole-advancement promotion decision (Gate L) and write "
        "promotion_verdict.json",
    )
    promote.add_argument("--runs", required=True, help="the runs root directory")
    promote.add_argument("--run-id", required=True, help="the run to promote")

    return parser


def _read_regular_bytes(path: str | Path, *, label: str) -> bytes:
    return read_stable_regular_bytes(
        path,
        label=label,
        max_bytes=_MAX_BOUNDARY_FILE_BYTES,
    )


def _object_from_bytes(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        document = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{label} must be a JSON object")
    return document


def _require_semantic_json_digest(
    raw: bytes,
    *,
    expected_digest: str,
    label: str,
) -> dict[str, Any]:
    """Verify a canonical JSON address while retaining the exact source bytes separately."""

    document = _object_from_bytes(raw, label=label)
    if digest_obj(document) != expected_digest:
        raise ValueError(f"{label} changed after external verification")
    return document


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _require_run_projection_unchanged(
    runs: str | Path,
    run_id: str,
    expected: Any,
) -> None:
    current = RunStore(runs).load(run_id)
    if current.to_dict() != expected.to_dict():
        raise ValueError("run projection changed while state dependencies were assembled")


def _ratified_phase_artifacts(
    runs: str | Path,
    run_id: str,
    projection_state: Any,
) -> tuple[dict[str, bytes], dict[str, Mapping[str, Any]]]:
    """Load the exact canonical phase artifacts bound by the checked run projection."""

    required_phases = {
        "product-specification",
        "architecture",
        "operational-maturity",
    }
    if set(projection_state.phase_artifact_digests) != required_phases:
        raise ValueError("dispatch requires the exact three ratified phase digests")
    phase_bytes: dict[str, bytes] = {}
    phase_documents: dict[str, Mapping[str, Any]] = {}
    for phase in sorted(required_phases):
        expected_digest = str(projection_state.phase_artifact_digests[phase])
        if not _DIGEST.fullmatch(expected_digest):
            raise ValueError(f"ratified {phase} artifact digest is not canonical")
        artifact_path = (
            Path(runs)
            / run_id
            / "evidence"
            / phase
            / expected_digest.removeprefix("sha256:")
            / "artifact.json"
        )
        raw = _read_regular_bytes(
            artifact_path,
            label=f"ratified {phase} artifact",
        )
        document = _object_from_bytes(raw, label=f"ratified {phase} artifact")
        validate_document("phase-artifact", document)
        artifact = PhaseArtifact.from_dict(document)
        if artifact.phase != phase or artifact.content_digest != expected_digest:
            raise ValueError(f"ratified {phase} artifact differs from the run ledger")
        phase_bytes[phase] = raw
        phase_documents[phase] = document
    return phase_bytes, phase_documents


def _read_object(path: str) -> dict[str, Any]:
    source = Path(path)
    return _object_from_bytes(
        _read_regular_bytes(source, label="JSON payload"),
        label=f"JSON payload {source}",
    )


def _emit(value: Any) -> None:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    print(json.dumps(value, sort_keys=True, indent=2))


def _parse_inline_object(raw: str, *, label: str) -> dict[str, Any]:
    if len(raw.encode("utf-8")) > _MAX_INLINE_JSON_BYTES:
        raise ValueError(f"{label} exceeds {_MAX_INLINE_JSON_BYTES} bytes")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"{label} is not valid bounded JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _parse_named_paths(values: list[str], *, label: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or not name or not raw_path:
            raise ValueError(f"{label} must use NAME=PATH: {value!r}")
        if name in result:
            raise ValueError(f"duplicate {label} name: {name}")
        result[name] = Path(raw_path)
    if not result:
        raise ValueError(f"at least one {label} is required")
    return result


def _write_json_once(path: str | Path, document: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(destination, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(
                json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
            )
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _retain_state_admission_refusal(
    arguments: argparse.Namespace,
    error: StateAdmissionError,
) -> None:
    """Retain a bounded refusal only for the pre-model run-model admission path."""

    if getattr(arguments, "command", "") != "run-model":
        return
    required = ("runs", "run_id", "receipt_id", "role")
    if any(not getattr(arguments, field, "") for field in required):
        return
    document = {
        "schema_version": "factory-state-admission-refusal/1",
        "receipt_id": str(arguments.receipt_id),
        "run_id": str(arguments.run_id),
        "role": str(arguments.role),
        "purpose": "lane-dispatch",
        "refusal_code": error.code,
        "dependency_id": error.dependency_id,
        "state_profile_digest": profile_digest("lane-dispatch"),
        "model_attempts": 0,
        "broker_effects": 0,
        "created_at": int(time.time()),
    }
    validate_document("state-admission-refusal", document)
    path = (
        Path(arguments.runs)
        / arguments.run_id
        / "evidence"
        / "state-admission"
        / "refusals"
        / f"{arguments.receipt_id}.json"
    )
    try:
        _write_json_once(path, document)
    except FileExistsError as exc:
        raise ValueError(
            "state-admission refusal receipt identity is already retained; "
            "receipt ids are single-use"
        ) from exc


def _execute_unleased(arguments: argparse.Namespace) -> None:
    if arguments.command == "validate-document":
        document = _read_object(arguments.input)
        validate_document(arguments.schema, document)
        _emit(
            {
                "schema": arguments.schema,
                "input": str(Path(arguments.input)),
                "digest": digest_obj(document),
                "valid": True,
            }
        )
        return
    if arguments.command == "digest-json":
        _emit({"digest": digest_obj(_read_object(arguments.input))})
        return
    if arguments.command == "inspect-target":
        manifest_path = Path(arguments.manifest)
        manifest_bytes = _read_regular_bytes(manifest_path, label="target manifest")
        manifest = load_target_manifest_bytes(
            manifest_bytes,
            source_label=str(manifest_path),
        )
        _emit(
            {
                "target_id": manifest.target_id,
                "content_digest": manifest.content_digest,
                "source_digest": digest_bytes(manifest_bytes),
                "repo": dict(manifest.repo),
                "build": dict(manifest.build),
            }
        )
        return
    if arguments.command == "status":
        _emit(RunStore(arguments.runs).load(arguments.run_id))
        return
    if arguments.command == "rebuild-projection":
        _emit(RunStore(arguments.runs).rebuild_projection(arguments.run_id))
        return
    if arguments.command == "verify-genesis":
        tessera = _tessera(arguments.tessera_bin)
        policy = load_genesis(
            arguments.genesis,
            trusted_root_public_key=arguments.root_public_key,
            tessera=tessera,
        )
        _emit(
            {
                "repository_id": policy.repository_id,
                "policy_id": policy.policy_id,
                "genesis_digest": policy.genesis_digest,
                "root_public_key": policy.root_public_key,
                "principals": sorted(policy.principals),
                "bootstrap_enabled": policy.bootstrap_enabled,
                "bootstrap_scope": sorted(policy.bootstrap_scope),
            }
        )
        return
    if arguments.command == "derive-resume-checkpoint":
        checkpoint = derive_resume_checkpoint(
            arguments.runs,
            arguments.run_id,
            checkpoint_id=arguments.checkpoint_id,
            previous_checkpoint_digest=arguments.previous_checkpoint_digest,
            genesis_path=arguments.genesis,
            trusted_root_public_key=arguments.root_public_key,
            tessera=_tessera(arguments.tessera_bin),
            configuration_sources=_parse_named_paths(
                arguments.config_source,
                label="configuration source",
            ),
            acceptance_obligation_catalog_digest=(
                arguments.acceptance_obligation_catalog_digest
            ),
            retention={
                "policy_id": arguments.retention_policy_id,
                "mode": arguments.retention_mode,
                "retain_until": arguments.retain_until,
                "metadata_classes": [
                    "authority-envelopes",
                    "lifecycle-ledger",
                    "resource-ledger",
                    "runner-receipts",
                    "effect-evidence",
                ],
                "erasure_authority": arguments.erasure_authority,
            },
            clock=lambda: int(time.time()),
        )
        _write_json_once(arguments.output, checkpoint)
        _emit(
            {
                "checkpoint_digest": digest_obj(checkpoint),
                "checkpoint_id": checkpoint["checkpoint_id"],
                "output": str(Path(arguments.output)),
                "anchored": False,
            }
        )
        return
    if arguments.command == "verify-resume-checkpoint":
        resume_verification = verify_resume_checkpoint(
            arguments.checkpoint,
            expected_checkpoint_digest=arguments.checkpoint_digest,
            runs_root=arguments.runs,
            run_id=arguments.run_id,
            genesis_path=arguments.genesis,
            trusted_root_public_key=arguments.root_public_key,
            tessera=_tessera(arguments.tessera_bin),
            configuration_sources=_parse_named_paths(
                arguments.config_source,
                label="configuration source",
            ),
            expected_acceptance_obligation_catalog_digest=(
                arguments.acceptance_obligation_catalog_digest
            ),
            accepted_previous_checkpoint_digests=(
                arguments.accepted_previous_checkpoint_digest
            ),
        )
        _emit(resume_verification.to_dict())
        return
    if arguments.command == "bundle-runner-projection":
        projection = RunStore(arguments.runs).load(arguments.run_id)
        if not projection.target_state or not projection.target_state_digest:
            raise ValueError("run has no checked target-state for a runner projection")
        projection_receipt_document = _read_object(arguments.projection_receipt)
        document = bundle_runner_projection(
            arguments.projection_root,
            projection_receipt=projection_receipt_document,
            run_id=arguments.run_id,
            generation=projection.generation,
            role=arguments.role,
            target_state_digest=projection.target_state_digest,
            resolved_commit=str(projection.target_state["resolved_commit"]),
            resolved_tree=str(projection.target_state["resolved_tree"]),
        )
        _write_json_once(arguments.output, document)
        _emit(
            {
                "run_id": arguments.run_id,
                "role": arguments.role,
                "projection_digest": digest_obj(document),
                "projection_manifest_digest": document["projection_manifest_digest"],
                "output": str(Path(arguments.output)),
            }
        )
        return
    if arguments.command == "qualify-state":
        observations = execute_state_qualification_observations(
            arguments.runner_configuration_digest
        )
        report = qualify_state_observations(
            observations,
            qualification_id=arguments.qualification_id,
        )
        _write_json_once(arguments.observations_output, observations)
        _write_json_once(arguments.output, report)
        _emit(report)
        if not report["qualified"]:
            raise ValueError("state-admission configuration is not qualified")
        return
    if arguments.command == "bundle-orchestrator-projection":
        configuration_sources = _parse_named_paths(
            arguments.config_source,
            label="configuration source",
        )
        resume = verify_resume_checkpoint(
            arguments.checkpoint,
            expected_checkpoint_digest=arguments.checkpoint_digest,
            runs_root=arguments.runs,
            run_id=arguments.run_id,
            genesis_path=arguments.genesis,
            trusted_root_public_key=arguments.root_public_key,
            tessera=_tessera(arguments.tessera_bin),
            configuration_sources=configuration_sources,
            accepted_previous_checkpoint_digests=(
                arguments.accepted_previous_checkpoint_digest
            ),
        )
        projection_state = RunStore(arguments.runs).load(arguments.run_id)
        if projection_state.ledger_head != resume.current_run_ledger_head:
            raise ValueError("run ledger changed after external resume verification")
        section_paths = _parse_named_paths(
            arguments.section,
            label="orchestrator section",
        )
        runtime_owned_sections = {"phase-artifacts", "run-projection"}
        supplied_runtime_sections = runtime_owned_sections & set(section_paths)
        if supplied_runtime_sections:
            raise ValueError(
                "orchestrator section is derived by the runtime, not supplied by a caller: "
                + ", ".join(sorted(supplied_runtime_sections))
            )
        sections: dict[str, bytes] = {}
        for section_id, path in section_paths.items():
            rule = dependency_rule("orchestrator-wake", section_id)
            sections[section_id] = read_stable_regular_bytes(
                path,
                label=f"orchestrator section {section_id}",
                max_bytes=rule.max_bytes,
            )
        _, phase_documents = _ratified_phase_artifacts(
            arguments.runs,
            arguments.run_id,
            projection_state,
        )
        sections["phase-artifacts"] = _canonical_bytes(
            {
                phase: dict(document)
                for phase, document in sorted(phase_documents.items())
            }
        )
        sections["run-projection"] = _canonical_bytes(projection_state.to_dict())
        _require_run_projection_unchanged(
            arguments.runs,
            arguments.run_id,
            projection_state,
        )
        capsule = derive_state_capsule(
            purpose="orchestrator-wake",
            run_id=arguments.run_id,
            generation=projection_state.generation,
            role="orchestrator",
            target_state_digest=projection_state.target_state_digest,
            run_ledger_head=projection_state.ledger_head,
            resume_checkpoint_digest=resume.checkpoint_digest,
            dependencies=sections,
        )
        document = build_orchestrator_projection(sections, state_capsule=capsule)
        _write_json_once(arguments.capsule_output, capsule)
        _write_json_once(arguments.output, document)
        _emit(
            {
                "run_id": arguments.run_id,
                "generation": projection_state.generation,
                "projection_digest": digest_obj(document),
                "state_capsule_digest": digest_obj(capsule),
                "output": str(Path(arguments.output)),
                "capsule_output": str(Path(arguments.capsule_output)),
            }
        )
        return
    if arguments.command == "run-model":
        configuration_sources = _parse_named_paths(
            arguments.config_source,
            label="configuration source",
        )
        resume = verify_resume_checkpoint(
            arguments.checkpoint,
            expected_checkpoint_digest=arguments.checkpoint_digest,
            runs_root=arguments.runs,
            run_id=arguments.run_id,
            genesis_path=arguments.genesis,
            trusted_root_public_key=arguments.root_public_key,
            tessera=_tessera(arguments.tessera_bin),
            configuration_sources=configuration_sources,
            accepted_previous_checkpoint_digests=(
                arguments.accepted_previous_checkpoint_digest
            ),
        )
        projection_state = RunStore(arguments.runs).load(arguments.run_id)
        if projection_state.ledger_head != resume.current_run_ledger_head:
            raise ValueError("run ledger changed after external resume verification")
        runner_manifest_bytes = _read_regular_bytes(
            arguments.runner_manifest,
            label="runner manifest",
        )
        runner_manifest = _object_from_bytes(
            runner_manifest_bytes,
            label="runner manifest",
        )
        parsed_manifest = RunnerManifest.from_dict(runner_manifest)
        if resume.configuration_digests.get(arguments.runner_config_source_name) != digest_bytes(
            runner_manifest_bytes
        ):
            raise ValueError("runner manifest is not bound by the external resume checkpoint")
        if digest_obj(runner_manifest) != arguments.runner_manifest_digest:
            raise ValueError("runner manifest differs from its externally expected digest")
        if runner_manifest.get("role") != arguments.role:
            raise ValueError("runner manifest belongs to another role")
        projection_bytes = _read_regular_bytes(
            arguments.projection,
            label="runner projection",
        )
        model_projection = _object_from_bytes(
            projection_bytes,
            label="runner projection",
        )
        validate_document("runner-projection", model_projection)
        expected_projection = {
            "run_id": arguments.run_id,
            "generation": projection_state.generation,
            "role": arguments.role,
            "target_state_digest": projection_state.target_state_digest,
            "resolved_commit": projection_state.target_state.get("resolved_commit"),
            "resolved_tree": projection_state.target_state.get("resolved_tree"),
        }
        for field, expected in expected_projection.items():
            if model_projection.get(field) != expected:
                raise ValueError(f"runner projection has wrong {field}")
        output_schema_bytes = _read_regular_bytes(
            arguments.output_schema,
            label="runner output schema",
        )
        output_schema_digest = digest_bytes(output_schema_bytes)
        if resume.configuration_digests.get(
            arguments.output_schema_config_source_name
        ) != output_schema_digest:
            raise ValueError("runner output schema is not bound by the external resume checkpoint")
        if output_schema_digest != arguments.output_schema_digest:
            raise ValueError("runner output schema differs from its externally expected digest")
        if runner_manifest.get("output_schema_digest") != output_schema_digest:
            raise ValueError("runner manifest binds a different output schema")
        task_bytes = _read_regular_bytes(arguments.task_file, label="runner task")
        if digest_bytes(task_bytes) != arguments.task_digest:
            raise ValueError("runner task differs from its expected digest")
        role_primer_bytes = _read_regular_bytes(
            arguments.role_primer,
            label="role-scoped Kindex primer",
        )
        broker_registry_bytes = _read_regular_bytes(
            arguments.broker_registry,
            label="broker registry",
        )
        broker_registry = _object_from_bytes(
            broker_registry_bytes,
            label="broker registry",
        )
        if digest_obj(broker_registry) != arguments.broker_registry_digest:
            raise ValueError("broker registry differs from its externally expected digest")
        if resume.configuration_digests.get(
            arguments.broker_registry_config_source_name
        ) != digest_bytes(broker_registry_bytes):
            raise ValueError("broker registry is not bound by the external resume checkpoint")
        qualification_observations_bytes = _read_regular_bytes(
            arguments.state_qualification_observations,
            label="state qualification observations",
        )
        qualification_observations = _object_from_bytes(
            qualification_observations_bytes,
            label="state qualification observations",
        )
        if resume.configuration_digests.get(
            arguments.state_qualification_observations_config_source_name
        ) != digest_bytes(qualification_observations_bytes):
            raise ValueError(
                "state qualification observations are not bound by the external resume checkpoint"
            )
        qualification_bytes = _read_regular_bytes(
            arguments.state_qualification_report,
            label="state qualification report",
        )
        qualification_report = _object_from_bytes(
            qualification_bytes,
            label="state qualification report",
        )
        if resume.configuration_digests.get(
            arguments.state_qualification_config_source_name
        ) != digest_bytes(qualification_bytes):
            raise ValueError(
                "state qualification report is not bound by the external resume checkpoint"
            )
        if parsed_manifest.document["state_qualification_digest"] != digest_bytes(
            qualification_bytes
        ):
            raise ValueError("runner manifest binds a different state qualification report")
        verify_state_qualification_report(
            qualification_report,
            observations=qualification_observations,
            expected_profile_digest=str(parsed_manifest.document["state_profile_digest"]),
            expected_runner_configuration_digest=str(
                parsed_manifest.document["configuration_digest"]
            ),
        )
        checkpoint_bytes = _read_regular_bytes(
            arguments.checkpoint,
            label="resume checkpoint",
        )
        _require_semantic_json_digest(
            checkpoint_bytes,
            expected_digest=resume.checkpoint_digest,
            label="resume checkpoint",
        )
        if digest_bytes(checkpoint_bytes) != resume.checkpoint_source_digest:
            raise ValueError("resume checkpoint source bytes changed after external verification")
        target_state_path = (
            Path(arguments.runs)
            / arguments.run_id
            / "evidence"
            / "target-resolution"
            / "target-state.json"
        )
        target_state_bytes = _read_regular_bytes(
            target_state_path,
            label="retained target-state",
        )
        retained_target_state = _object_from_bytes(
            target_state_bytes,
            label="retained target-state",
        )
        verify_target_state(
            retained_target_state,
            expected_digest=projection_state.target_state_digest,
        )
        phase_artifact_bytes, _ = _ratified_phase_artifacts(
            arguments.runs,
            arguments.run_id,
            projection_state,
        )
        resume_verification_document = resume.state_admission_dict()
        state_dependencies = {
            "target-state": target_state_bytes,
            "run-ledger-head": projection_state.ledger_head.encode("ascii"),
            "phase-artifact-digests": _canonical_bytes(
                dict(projection_state.phase_artifact_digests)
            ),
            **{
                f"phase-artifact-{phase}": raw
                for phase, raw in phase_artifact_bytes.items()
            },
            "frozen-task": task_bytes,
            "runner-projection": projection_bytes,
            "role-primer": role_primer_bytes,
            "runner-manifest": runner_manifest_bytes,
            "runner-output-schema": output_schema_bytes,
            "broker-registry": broker_registry_bytes,
            "resume-checkpoint": checkpoint_bytes,
            "resume-verification": _canonical_bytes(resume_verification_document),
            "configuration-set": _canonical_bytes(dict(resume.configuration_digests)),
            "state-qualification-observations": qualification_observations_bytes,
            "state-qualification-report": qualification_bytes,
        }
        _require_run_projection_unchanged(
            arguments.runs,
            arguments.run_id,
            projection_state,
        )
        state_capsule = derive_state_capsule(
            purpose="lane-dispatch",
            run_id=arguments.run_id,
            generation=projection_state.generation,
            role=arguments.role,
            target_state_digest=projection_state.target_state_digest,
            run_ledger_head=projection_state.ledger_head,
            resume_checkpoint_digest=resume.checkpoint_digest,
            dependencies=state_dependencies,
        )
        forbidden: list[Path] = []
        for field in ("control_root", "source_root", "workdir", "object_store"):
            value = projection_state.target_state.get(field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"target-state has no forbidden runner root {field}")
            path = Path(value).resolve(strict=True)
            if path not in forbidden:
                forbidden.append(path)
        workspace = Path(arguments.workspace)
        if not workspace.is_absolute():
            raise ValueError("runner workspace must be an absolute host-owned path")
        arguments._model_attempts = 0
        try:
            handoff, runner_receipt = HardenedModelRunner(
                backend=MacOSNetworkedRunner(),
                secret_store=NamedSecretStore(arguments.secret_root),
            ).dispatch(
                run_id=arguments.run_id,
                generation=projection_state.generation,
                receipt_id=arguments.receipt_id,
                manifest_bytes=runner_manifest_bytes,
                projection_bytes=projection_bytes,
                output_schema_bytes=output_schema_bytes,
                task_bytes=task_bytes,
                state_capsule_document=state_capsule,
                state_dependencies=state_dependencies,
                target_state_digest=projection_state.target_state_digest,
                run_ledger_head=projection_state.ledger_head,
                resume_checkpoint_digest=resume.checkpoint_digest,
                broker_registry_source_digest=digest_bytes(broker_registry_bytes),
                workspace_root=workspace,
                forbidden_paths=forbidden,
                attempt_observer=lambda count: setattr(
                    arguments,
                    "_model_attempts",
                    count,
                ),
            )
        except RunnerError as exc:
            arguments._model_attempts = exc.model_attempts
            raise
        _emit(
            {
                "run_id": arguments.run_id,
                "role": arguments.role,
                "resume_checkpoint_digest": resume.checkpoint_digest,
                "state_capsule_digest": digest_obj(state_capsule),
                "handoff": handoff,
                "runner_receipt": dict(runner_receipt.document),
                "workspace": str(workspace),
            }
        )
        return
    if arguments.command == "execute-broker-handoff":
        configuration_sources = _parse_named_paths(
            arguments.config_source,
            label="configuration source",
        )
        tessera = _tessera(arguments.tessera_bin)
        resume = verify_resume_checkpoint(
            arguments.checkpoint,
            expected_checkpoint_digest=arguments.checkpoint_digest,
            runs_root=arguments.runs,
            run_id=arguments.run_id,
            genesis_path=arguments.genesis,
            trusted_root_public_key=arguments.root_public_key,
            tessera=tessera,
            configuration_sources=configuration_sources,
            accepted_previous_checkpoint_digests=(
                arguments.accepted_previous_checkpoint_digest
            ),
        )
        projection_state = RunStore(arguments.runs).load(arguments.run_id)
        if not projection_state.target_state or not projection_state.target_state_digest:
            raise ValueError("run has no checked target-state for broker execution")
        if projection_state.ledger_head != resume.current_run_ledger_head:
            raise ValueError("run ledger changed after external resume verification")
        retained_runner_receipt = _read_object(arguments.runner_receipt)
        if retained_runner_receipt.get("schema_version") == "factory-runner-receipt/1":
            raise ValueError(
                "legacy runner receipt cannot execute after state-capsule cutover; "
                "explicitly abandon the legacy run and start a v2 run from a new verified "
                "checkpoint"
            )
        validate_document("runner-receipt", retained_runner_receipt)
        expected_receipt = {
            "receipt_id": arguments.receipt_id,
            "run_id": arguments.run_id,
            "generation": projection_state.generation,
            "role": arguments.role,
        }
        for field, expected in expected_receipt.items():
            if retained_runner_receipt.get(field) != expected:
                raise ValueError(f"runner receipt has wrong {field}")
        state_capsule = _read_object(arguments.state_capsule)
        verify_state_capsule(
            state_capsule,
            expected_purpose="lane-dispatch",
            expected_run_id=arguments.run_id,
            expected_generation=projection_state.generation,
            expected_role=arguments.role,
            expected_target_state_digest=projection_state.target_state_digest,
            expected_run_ledger_head=projection_state.ledger_head,
            expected_resume_checkpoint_digest=resume.checkpoint_digest,
        )
        state_capsule_digest = digest_obj(state_capsule)
        receipt_capsule_fields = {
            "state_capsule_digest": state_capsule_digest,
            "state_profile_digest": state_capsule["profile_digest"],
            "resume_checkpoint_digest": resume.checkpoint_digest,
        }
        for field, expected in receipt_capsule_fields.items():
            if retained_runner_receipt.get(field) != expected:
                raise ValueError(f"runner receipt has wrong {field}")
        handoff = _read_object(arguments.handoff)
        validate_document("runner-output", handoff)
        if len(handoff["broker_requests"]) > 64:
            raise ValueError("runner handoff exceeds the broker-request ceiling")
        expected_handoff = {
            "kind": "handoff",
            "role": arguments.role,
            "sequence": 3,
            "state_capsule_digest": state_capsule_digest,
            "projection_digest": retained_runner_receipt["projection_digest"],
        }
        for field, expected in expected_handoff.items():
            if handoff.get(field) != expected:
                raise ValueError(f"runner handoff has wrong {field}")
        if digest_obj(handoff) != retained_runner_receipt["handoff_digest"]:
            raise ValueError("runner handoff differs from the qualified runner receipt")
        if retained_runner_receipt["continuity_nonce_digest"] != digest_obj(
            {"continuity_nonce": handoff["continuity_nonce"]}
        ):
            raise ValueError("runner handoff does not carry the qualified session continuity")
        registry_bytes = _read_regular_bytes(arguments.registry, label="broker registry")
        registry_document = _object_from_bytes(registry_bytes, label="broker registry")
        registry_digest = digest_obj(registry_document)
        if registry_digest != arguments.registry_digest:
            raise ValueError("broker registry differs from its externally expected digest")
        if resume.configuration_digests.get(
            arguments.registry_config_source_name
        ) != digest_bytes(registry_bytes):
            raise ValueError("broker registry is not bound by the external resume checkpoint")
        if retained_runner_receipt["broker_registry_source_digest"] != digest_bytes(
            registry_bytes
        ):
            raise ValueError("broker registry differs from the qualified runner receipt")
        capsule_dependencies = {
            str(item["dependency_id"]): str(item["content_digest"])
            for item in state_capsule["dependencies"]
        }
        if capsule_dependencies.get("broker-registry") != digest_bytes(registry_bytes):
            raise ValueError("broker registry differs from the admitted state capsule")
        resource_ledger = ResourceLedger(
            Path(arguments.runs) / arguments.run_id,
            arguments.run_id,
        )
        registry = load_broker_registry(
            registry_document,
            run_id=arguments.run_id,
            generation=projection_state.generation,
            role=arguments.role,
            target_state_digest=projection_state.target_state_digest,
            resources=resource_ledger.latest(),
        )
        policy = load_genesis(
            arguments.genesis,
            trusted_root_public_key=arguments.root_public_key,
            tessera=tessera,
        )
        broker_root = (
            Path(arguments.runs)
            / arguments.run_id
            / "evidence"
            / "broker"
        )
        broker = TypedOperationBroker(
            run_id=arguments.run_id,
            generation=projection_state.generation,
            role=arguments.role,
            target_state_digest=projection_state.target_state_digest,
            configuration_digest=registry.configuration_digest,
            operations=registry.operations,
            evidence_root=broker_root / "effects",
            policy=policy,
            tessera=tessera,
            isolation=MacOSSandbox(),
        )
        _require_run_projection_unchanged(
            arguments.runs,
            arguments.run_id,
            projection_state,
        )
        effects = []
        for request in handoff["broker_requests"]:
            capability_digest = str(request["capability_digest"])
            capability_envelope = registry.capability_envelopes.get(capability_digest)
            if capability_envelope is None:
                raise ValueError("runner requested a capability absent from the broker registry")
            effects.append(
                broker.execute(
                    request,
                    capability_envelope_path=capability_envelope,
                ).to_dict()
            )
        report = {
            "schema_version": "factory-broker-execution-report/1",
            "run_id": arguments.run_id,
            "generation": projection_state.generation,
            "role": arguments.role,
            "receipt_id": arguments.receipt_id,
            "resume_checkpoint_digest": resume.checkpoint_digest,
            "runner_receipt_digest": digest_obj(retained_runner_receipt),
            "handoff_digest": digest_obj(handoff),
            "state_capsule_digest": state_capsule_digest,
            "registry_digest": registry.configuration_digest,
            "effect_digests": [digest_obj(effect) for effect in effects],
            "verified": True,
        }
        report_path = broker_root / "reports" / f"{arguments.receipt_id}.json"
        if report_path.exists() or report_path.is_symlink():
            if _read_object(str(report_path)) != report:
                raise ValueError("retained broker report differs from the re-derived execution")
        else:
            _write_json_once(report_path, report)
        _emit({**report, "effects": effects, "report": str(report_path)})
        return
    if arguments.command == "authorize-target-resolution":
        workflow = _load_workflow(arguments)
        _emit(
            workflow.authorize_target_resolution(
                arguments.run_id,
                manifest_path=arguments.manifest,
                request_path=arguments.request,
                receipt_path=arguments.receipt,
            )
        )
        return
    if arguments.command == "resolve-target":
        workflow = _load_workflow(arguments)
        _emit(
            workflow.resolve_target(
                arguments.run_id,
                object_source=arguments.object_source,
            )
        )
        return
    if arguments.command == "authorize-change":
        workflow = _load_workflow(arguments)
        _emit(
            workflow.authorize_change(
                arguments.run_id,
                request_path=arguments.request,
                receipt_path=arguments.receipt,
            )
        )
        return
    if arguments.command == "ratify-phase":
        workflow = _load_workflow(arguments)
        result = workflow.ratify_phase(
            arguments.run_id,
            artifact_path=arguments.artifact,
            human_receipt_path=arguments.human_receipt,
            validator_receipt_path=arguments.validator_receipt,
        )
        _emit(
            {
                "artifact_id": result.artifact.artifact_id,
                "artifact_digest": result.artifact_digest,
                "evidence_directory": str(result.directory),
                "run": result.projection.to_dict(),
            }
        )
        return
    if arguments.command == "verify-target-state":
        projection = RunStore(arguments.runs).load(arguments.run_id)
        if not projection.target_state_digest or not projection.target_state:
            raise ValueError("run has no resolved target-state")
        retained_path = (
            Path(arguments.runs)
            / arguments.run_id
            / "evidence"
            / "target-resolution"
            / "target-state.json"
        )
        retained = _read_object(str(retained_path))
        if digest_obj(retained) != projection.target_state_digest:
            raise ValueError("retained target-state differs from the authoritative ledger")
        verify_target_state(retained, expected_digest=projection.target_state_digest)
        _emit(
            {
                "run_id": arguments.run_id,
                "target_state_digest": projection.target_state_digest,
                "resolved_commit": projection.target_state["resolved_commit"],
                "verified": True,
            }
        )
        return
    if arguments.command == "verify-execution-request":
        store = RunStore(arguments.runs)
        projection = store.load(arguments.run_id)
        bindings = store.execution_authority_digests(arguments.run_id)
        request_path = (
            Path(arguments.runs)
            / arguments.run_id
            / "evidence"
            / "intake"
            / "execution-request.json"
        )
        if request_path.is_symlink() or not request_path.is_file():
            raise ValueError("retained Stage-E execution request is missing or a symlink")
        request = _read_object(str(request_path))
        validate_document("execution-request", request)
        request_digest = digest_obj(request)
        if request_digest != bindings["execution-request"]:
            raise ValueError("retained Stage-E request differs from the authoritative intake")
        expected = {
            "run_id": arguments.run_id,
            "generation": projection.generation,
            "target_manifest_digest": projection.target_digest,
            "target_state_digest": projection.target_state_digest,
            "resolved_commit": projection.target_state.get("resolved_commit"),
            "verbatim_request_digest": projection.source_digest,
        }
        for key, value in expected.items():
            if request.get(key) != value:
                raise ValueError(f"retained Stage-E request has wrong {key}")
        verbatim_value = request["verbatim_request"]
        if not isinstance(verbatim_value, str):
            raise ValueError("retained Stage-E verbatim request must be a string")
        verbatim = verbatim_value.encode("utf-8")
        if digest_bytes(verbatim) != projection.source_digest:
            raise ValueError("retained Stage-E verbatim request digest does not re-derive")
        task_digest = ""
        if arguments.task_file:
            task_path = Path(arguments.task_file)
            task_bytes = _read_regular_bytes(task_path, label="task artifact")
            if task_bytes != verbatim:
                raise ValueError("task bytes differ from the retained Stage-E request")
            task_digest = digest_bytes(task_bytes)
        _emit(
            {
                "run_id": arguments.run_id,
                "execution_request_digest": request_digest,
                "execution_receipt_digest": bindings["execution-receipt"],
                "authority_genesis_digest": bindings["authority-genesis"],
                "source_digest": projection.source_digest,
                "task_digest": task_digest or None,
                "verified": True,
            }
        )
        return
    if arguments.command == "verify-resources":
        run_dir = Path(arguments.runs) / arguments.run_id
        ledger = ResourceLedger(run_dir, arguments.run_id)
        seal: Mapping[str, Any] | None = None
        if arguments.seal:
            if not arguments.for_close:
                raise ValueError("--seal requires --for-close")
            if not arguments.actor.strip():
                raise ValueError("--seal requires --actor")
            seal = ledger.seal_for_close(actor=arguments.actor)
            _, records = ledger.verify_sealed_for_close()
            ledger_head = str(seal["ledger_head"])
        elif arguments.for_close:
            ledger_head, records = ledger.close_snapshot()
        else:
            records = ledger.latest()
            ledger_head = ledger.head()
        _emit(
            {
                "run_id": arguments.run_id,
                "ledger_head": ledger_head,
                "resources": records,
                "for_close": bool(arguments.for_close),
                "seal": seal,
                "verified": True,
            }
        )
        return
    if arguments.command == "record-resource":
        projection = RunStore(arguments.runs).load(arguments.run_id)
        baseline = _parse_inline_object(arguments.baseline_json, label="resource baseline")
        disposition = _parse_inline_object(
            arguments.disposition_json,
            label="resource disposition",
        )
        evidence = _parse_inline_object(arguments.evidence_json, label="resource evidence")
        ledger = ResourceLedger(Path(arguments.runs) / arguments.run_id, arguments.run_id)
        entry_hash = ledger.append(
            generation=projection.generation,
            resource_id=arguments.resource_id,
            resource_type=arguments.resource_type,
            identifier=arguments.identifier,
            creator_action=arguments.creator_action,
            ownership=arguments.ownership,
            baseline=baseline,
            disposition=disposition,
            status=arguments.status,
            evidence_digests=evidence,
            actor=arguments.actor,
        )
        _emit(
            {
                "run_id": arguments.run_id,
                "resource_id": arguments.resource_id,
                "status": arguments.status,
                "entry_hash": entry_hash,
                "ledger_head": entry_hash,
            }
        )
        return
    if arguments.command == "disposition-resource":
        RunStore(arguments.runs).load(arguments.run_id)
        ledger = ResourceLedger(Path(arguments.runs) / arguments.run_id, arguments.run_id)
        prior = ledger.latest().get(arguments.resource_id)
        if prior is None:
            raise ValueError(f"unknown run resource: {arguments.resource_id}")
        evidence = _parse_inline_object(arguments.evidence_json, label="resource evidence")
        entry_hash = ledger.append(
            generation=int(prior["generation"]),
            resource_id=arguments.resource_id,
            resource_type=str(prior["resource_type"]),
            identifier=str(prior["identifier"]),
            creator_action=str(prior["creator_action"]),
            ownership=str(prior["ownership"]),
            baseline=dict(prior["baseline"]),
            disposition={
                "reason": arguments.reason,
                "residue": arguments.residue == "true",
            },
            status=arguments.status,
            evidence_digests=evidence,
            actor=arguments.actor,
        )
        _emit(
            {
                "run_id": arguments.run_id,
                "resource_id": arguments.resource_id,
                "status": arguments.status,
                "entry_hash": entry_hash,
                "ledger_head": entry_hash,
            }
        )
        return
    if arguments.command == "tessera-wrap":
        envelope = _tessera(arguments.tessera_bin).wrap_json(
            _read_object(arguments.payload),
            kind=arguments.kind,
            key_path=arguments.key,
            output_path=arguments.output,
        )
        _emit(
            {
                "kind": envelope.kind,
                "payload_digest": envelope.payload_digest,
                "public_key": envelope.public_key,
                "envelope_digest": envelope.envelope_digest,
                "path": str(envelope.path),
            }
        )
        return
    if arguments.command == "promote":
        from factory_runtime.promotion_gate import PromotionGateError, render

        run_root = Path(arguments.runs) / arguments.run_id
        try:
            decision = render(run_root)
        except PromotionGateError as exc:
            # Fail-closed: surface as a refused control (exit 2) so promote.sh closes nothing.
            raise ValueError(str(exc)) from exc
        _emit(decision)
        return
    raise ValueError(f"unsupported command: {arguments.command}")


def _execute(arguments: argparse.Namespace) -> None:
    """Hold the lifecycle/resource lease across every long-running model or broker action."""

    if arguments.command in {
        "bundle-orchestrator-projection",
        "run-model",
        "execute-broker-handoff",
    }:
        ledger = ResourceLedger(
            Path(arguments.runs) / arguments.run_id,
            arguments.run_id,
        )
        with ledger.run_transition_guard():
            try:
                _execute_unleased(arguments)
            except StateAdmissionError:
                raise
            except RunnerError as exc:
                if arguments.command != "run-model":
                    raise
                if max(
                    exc.model_attempts,
                    int(getattr(arguments, "_model_attempts", 0)),
                ) > 0:
                    raise
                raise StateAdmissionError(
                    exc.refusal_code,
                    str(exc),
                    dependency_id=exc.dependency_id,
                ) from exc
            except (OSError, ValueError) as exc:
                if (
                    arguments.command == "run-model"
                    and getattr(arguments, "_model_attempts", 0) == 0
                ):
                    raise StateAdmissionError(
                        "PRE_MODEL_REFUSAL",
                        str(exc),
                    ) from exc
                raise
        return
    _execute_unleased(arguments)


def main(argv: list[str] | None = None) -> int:
    """Run one command; all refused controls exit non-zero with no traceback laundering."""

    arguments: argparse.Namespace | None = None
    try:
        arguments = _parser().parse_args(argv)
        _execute(arguments)
    except StateAdmissionError as exc:
        if arguments is not None:
            try:
                _retain_state_admission_refusal(arguments, exc)
            except (OSError, ValueError) as receipt_error:
                print(
                    "factory: warning: state-admission refusal receipt could not be "
                    f"retained: {receipt_error}",
                    file=sys.stderr,
                )
        print(f"factory: refused: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"factory: refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
