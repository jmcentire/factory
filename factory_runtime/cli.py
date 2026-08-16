"""Command-line boundary for the executable Factory runtime."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from factory_core.manifest import digest_bytes, digest_obj
from factory_core.target import load_target_manifest
from factory_runtime.authority import load_genesis
from factory_runtime.resources import ResourceLedger
from factory_runtime.schema import SCHEMA_NAMES, validate_document
from factory_runtime.state import RunStore
from factory_runtime.target_state import verify_target_state
from factory_runtime.tessera import TesseraCli
from factory_runtime.workflow import FactoryWorkflow

_MAX_INLINE_JSON_BYTES = 65_536


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
    source = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise ValueError(f"{label} could not be opened safely: {source}: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"{label} must be a regular file: {source}")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            return handle.read()
    except OSError as exc:
        raise ValueError(f"{label} is unreadable: {source}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_object(path: str) -> dict[str, Any]:
    source = Path(path)
    try:
        raw = json.loads(_read_regular_bytes(source, label="JSON payload"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON payload is unreadable: {source}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"JSON payload must be an object: {path}")
    return raw


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


def _execute(arguments: argparse.Namespace) -> None:
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
        manifest = load_target_manifest(manifest_path)
        _emit(
            {
                "target_id": manifest.target_id,
                "content_digest": manifest.content_digest,
                "source_digest": digest_bytes(manifest_path.read_bytes()),
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


def main(argv: list[str] | None = None) -> int:
    """Run one command; all refused controls exit non-zero with no traceback laundering."""

    try:
        arguments = _parser().parse_args(argv)
        _execute(arguments)
    except (OSError, ValueError) as exc:
        print(f"factory: refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
