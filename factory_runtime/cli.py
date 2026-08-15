"""Command-line boundary for the executable Factory runtime."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from factory_core.manifest import digest_bytes, digest_obj
from factory_core.target import load_target_manifest
from factory_runtime.authority import load_genesis
from factory_runtime.schema import SCHEMA_NAMES, validate_document
from factory_runtime.state import RunStore
from factory_runtime.tessera import TesseraCli
from factory_runtime.workflow import FactoryWorkflow


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

    authorize = commands.add_parser(
        "authorize-change",
        help="create intake from a canonical request and exact-subject human receipt",
    )
    authorize.add_argument("--runs", required=True)
    authorize.add_argument("--run-id", required=True)
    authorize.add_argument("--target-digest", required=True)
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

    wrap = commands.add_parser(
        "tessera-wrap",
        help="sign a JSON object in a Factory-bound Tessera envelope",
    )
    wrap.add_argument("--payload", required=True)
    wrap.add_argument("--kind", required=True)
    wrap.add_argument("--key", required=True)
    wrap.add_argument("--output", required=True)
    wrap.add_argument("--tessera-bin", default="tessera")

    # Gate L — the sole advancement path. Renders the pure decide_promotion verdict for a
    # run and writes promotion_verdict.json; the harness (promote.sh) is the sole writer of
    # run.json "closed" and reads this verdict. No genesis/tessera is required: promotion
    # only needs decide_promotion, which is pure; the authority/intake concerns live in the
    # workflow commands that *gather* promotion_inputs.json, not in the verdict renderer.
    promote = commands.add_parser(
        "promote",
        help="render the sole-advancement promotion decision (Gate L) and write "
        "promotion_verdict.json",
    )
    promote.add_argument("--runs", required=True, help="the runs root directory")
    promote.add_argument("--run-id", required=True, help="the run to promote")

    return parser


def _read_object(path: str) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"JSON payload must be an object: {path}")
    return raw


def _emit(value: Any) -> None:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    print(json.dumps(value, sort_keys=True, indent=2))


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
    if arguments.command == "authorize-change":
        workflow = _load_workflow(arguments)
        _emit(
            workflow.authorize_change(
                arguments.run_id,
                target_digest=arguments.target_digest,
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
