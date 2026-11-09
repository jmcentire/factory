"""Command-line boundary for the executable Factory runtime."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from factory_core.manifest import digest_bytes, digest_obj
from factory_core.target import load_target_manifest_bytes
from factory_runtime.schema import SCHEMA_NAMES, validate_document
from factory_runtime.state_admission import (
    read_stable_regular_bytes,
)

_MAX_BOUNDARY_FILE_BYTES = 5_242_880


class _VersionAction(argparse.Action):
    def __call__(self, parser: argparse.ArgumentParser, namespace: argparse.Namespace,
                 values: object, option_string: str | None = None) -> None:
        from factory_runtime.version import format_version
        print(format_version())
        parser.exit()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="factory",
        description="Run Factory tasks and inspect task evidence.",
    )
    parser.add_argument("--version", action=_VersionAction, nargs=0)
    commands = parser.add_subparsers(dest="command", required=True)
    version = commands.add_parser("version", help="identify the installed Factory build")
    version.add_argument("--json", action="store_true")
    cleanup = commands.add_parser("cleanup", help="inspect or archive completed run storage")
    cleanup.add_argument("--runs", type=Path, default=Path(".factory/runs"))
    cleanup.add_argument("--run")
    cleanup.add_argument("--archive", action="store_true")
    cleanup.add_argument("--archive-dir", type=Path)
    cleanup.add_argument("--completed", action="store_true")
    cleanup.add_argument("--apply", action="store_true")
    cleanup.add_argument("--restore", type=Path)
    cleanup.add_argument("--destination", type=Path)
    ordinary = commands.add_parser("run", help="start a task with assigned native agents")
    ordinary.add_argument("run")
    ordinary.add_argument("task")
    ordinary.add_argument("--repo", required=True)
    ordinary.add_argument("--runs", default=".factory/runs")
    ordinary.add_argument("--log")
    ordinary.add_argument("--budget", type=float)


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


    delta = commands.add_parser(
        "context-delta", help="compare retained same-role capsules without granting admission",
    )
    delta.add_argument("--previous", required=True)
    delta.add_argument("--current", required=True)


    preflight = commands.add_parser(
        "preflight",
        help="feasibility preflight: the early NO from declared facts (plan 1.1) — "
        "read-only, writes nothing, exit 2 on a hard NO",
    )
    preflight.add_argument("--coverage", help="coverage-map JSON")
    preflight.add_argument("--profile", help="criticality profile JSON")
    preflight.add_argument("--policy", help="segregation policy JSON")
    preflight.add_argument("--target-manifest", help="target manifest TOML (build/signal knobs)")
    preflight.add_argument("--build-plan", help="build plan JSON (max_build_attempts)")
    preflight.add_argument("--runs", help="runs root for the dead-run liveness probe")
    preflight.add_argument("--run-id", help="run id for the dead-run liveness probe")


    verdict = commands.add_parser(
        "verdict",
        help="compute the mechanically unpersuadable global verdict over a declared "
        "coverage map; with handovers, compose the reserved completion token",
    )
    verdict.add_argument("--coverage", required=True, help="coverage-map JSON")
    verdict.add_argument(
        "--promotion",
        required=True,
        help="the promotion_verdict.json Gate L wrote (the floor the verdict may only narrow)",
    )
    verdict.add_argument(
        "--frame-check",
        default="",
        help="frame-check result JSON; absent means the first line is not demonstrated",
    )
    verdict.add_argument("--receipts", default="", help="characterization-receipt JSON array")
    verdict.add_argument(
        "--deliverables",
        default="",
        help=(
            "JSON array enumerating what the request asked for: "
            "[{deliverable_id, directive_ref, summary}]. Supplying it enables the "
            "fidelity channel; omitting it caps the verdict at INCOMPLETE."
        ),
    )
    verdict.add_argument(
        "--assessments",
        default="",
        help=(
            "JSON array of the lanes' own typed judgment per deliverable: "
            "[{deliverable_id, disposition, divergence, fix_scope}]"
        ),
    )
    verdict.add_argument(
        "--attributions",
        default="",
        help=(
            "JSON array binding built work to what it served: "
            "[{artifact_ref, deliverable_id}]. Work attributed to nothing is scope drift."
        ),
    )
    verdict.add_argument(
        "--utterances",
        default="",
        help=(
            "JSON array of the transcript slice the request came from: "
            "[{utterance_id, speaker, position, line_digest, surfaced, references}]. "
            "Required with --deliverables. The tier is DERIVED from the declared speaker "
            "rather than chosen by the lane, but the digest is NOT yet checked against "
            "any transcript: no extractor exists, so this records a claim, not proof."
        ),
    )
    verdict.add_argument(
        "--fidelity-history",
        default="",
        help="JSON array of prior fidelity dispositions, for the risk-acceptance rate signal",
    )
    verdict.add_argument("--assumptions", default="", help="assumption-record JSON array")
    verdict.add_argument(
        "--handovers",
        default="",
        help="lane-handover JSON array; enables completion-token composition",
    )
    verdict.add_argument("--candidate", required=True, help="candidate digest (sha256:<hex>)")
    verdict.add_argument(
        "--evaluated-position",
        required=True,
        type=int,
        help="this evaluation's ledger position (receipts at or after it are retroactive)",
    )
    verdict.add_argument("--validator", required=True, help="the validator seat identity")


    audit = commands.add_parser(
        "audit",
        help="emit closed-vocabulary codes for defects that survive on a PASS "
        "(non-gating; runs after disposition)",
    )
    audit.add_argument("--run-id", required=True, help="the run this audit describes")
    audit.add_argument("--coverage", required=True, help="coverage-map JSON")
    audit.add_argument("--promotion", required=True, help="the promotion_verdict.json floor")
    audit.add_argument("--frame-check", default="", help="frame-check result JSON")
    audit.add_argument("--receipts", default="", help="characterization-receipt JSON array")
    audit.add_argument("--assumptions", default="", help="assumption-record JSON array")
    audit.add_argument("--handovers", default="", help="lane-handover JSON array")
    audit.add_argument("--candidate", required=True, help="candidate digest (sha256:<hex>)")
    audit.add_argument("--evaluated-position", required=True, type=int)
    audit.add_argument("--validator", required=True, help="the validator seat identity")
    audit.add_argument("--deliverables", default="", help="enumerated request JSON array")
    audit.add_argument("--assessments", default="", help="per-deliverable self-assessment array")
    audit.add_argument("--attributions", default="", help="artifact-to-deliverable array")
    audit.add_argument("--utterances", default="", help="transcript slice behind the request")

    audit_table = commands.add_parser(
        "audit-table",
        help="frequency table over accumulated audit rows, every count carrying its "
        "denominator and grouped by vocabulary digest",
    )
    audit_table.add_argument(
        "--rows", required=True, help="JSON array of audit rows, or a .jsonl file"
    )


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


def _read_object(path: str) -> dict[str, Any]:
    source = Path(path)
    return _object_from_bytes(
        _read_regular_bytes(source, label="JSON payload"),
        label=f"JSON payload {source}",
    )


def _read_array(path: str) -> list[dict[str, Any]]:
    """Load an optional JSON array of objects; an empty path is an empty array."""

    if not path:
        return []
    source = Path(path)
    raw = json.loads(
        _read_regular_bytes(source, label="JSON payload").decode("utf-8")
    )
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise ValueError(f"JSON payload {source} must be an array of objects")
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
        manifest_bytes = _read_regular_bytes(manifest_path, label="target manifest")
        manifest = load_target_manifest_bytes(
            manifest_bytes,
            source_label=str(manifest_path),
        )
        _emit(
            {
                "target_id": manifest.target_id,
                "source_digest": digest_bytes(manifest_bytes),
                "repo": dict(manifest.repo),
                "build": dict(manifest.build),
            }
        )
        return
    if arguments.command == "context-delta":
        from factory_runtime.state_admission import state_dependency_delta

        _emit(state_dependency_delta(
            _read_object(arguments.previous), _read_object(arguments.current),
        ))
        return

    if arguments.command == "preflight":
        from factory_core.build_plan import BuildPlan
        from factory_core.criticality import CriticalityProfile
        from factory_core.target import load_target_manifest
        from factory_core.verdict import CoverageMap
        from factory_runtime.preflight import run_preflight
        from factory_runtime.promotion_gate import _policy_from_dict

        preflight_coverage = (
            CoverageMap.from_dict(_read_object(arguments.coverage))
            if arguments.coverage
            else None
        )
        preflight_profile = (
            CriticalityProfile.from_dict(_read_object(arguments.profile))
            if arguments.profile
            else None
        )
        preflight_policy = (
            _policy_from_dict(_read_object(arguments.policy))
            if arguments.policy
            else None
        )
        preflight_target_build = (
            dict(load_target_manifest(arguments.target_manifest).build)
            if arguments.target_manifest
            else None
        )
        preflight_plan = (
            BuildPlan.from_dict(_read_object(arguments.build_plan))
            if arguments.build_plan
            else None
        )
        from factory_runtime.preflight import (
            LivenessFacts,
            SignalDeadlineFacts,
            probe_liveness,
            probe_signal_deadline,
        )

        preflight_liveness: LivenessFacts | None = (
            probe_liveness(arguments.runs, arguments.run_id)
            if arguments.runs and arguments.run_id
            else None
        )
        preflight_signal: SignalDeadlineFacts | None = (
            probe_signal_deadline(arguments.runs, arguments.run_id)
            if arguments.runs and arguments.run_id
            else None
        )
        preflight_report = run_preflight(
            coverage=preflight_coverage,
            profile=preflight_profile,
            policy=preflight_policy,
            target_build=preflight_target_build,
            plan_max_build_attempts=(
                preflight_plan.max_build_attempts if preflight_plan else None
            ),
            plan=preflight_plan,
            liveness=preflight_liveness,
            signal_deadline=preflight_signal,
        )
        print(json.dumps(preflight_report.to_dict(), indent=2))
        if not preflight_report.go:
            raise SystemExit(2)
        return
    if arguments.command == "verdict":
        from factory_core.directive_authority import (
            STATED,
        )
        from factory_core.directive_authority import (
            receipts as authority_receipts,
        )
        from factory_core.fidelity import (
            risk_acceptance_available,
            risk_acceptance_is_systematic,
        )
        from factory_core.handover import Handover, compose_done, done_attestation_subject
        from factory_core.verdict import (
            AssumptionRecord,
            CharacterizationReceipt,
            CoverageMap,
            FrameCheckResult,
            PromotionFloor,
            compute_verdict,
            render_headline,
            verdict_attestation_subject,
        )

        coverage = CoverageMap.from_dict(_read_object(arguments.coverage))
        promotion = PromotionFloor.from_dict(_read_object(arguments.promotion))
        frame_check = (
            FrameCheckResult.from_dict(_read_object(arguments.frame_check))
            if arguments.frame_check
            else None
        )
        receipts = tuple(
            CharacterizationReceipt.from_dict(item)
            for item in _read_array(arguments.receipts)
        )
        assumptions = tuple(
            AssumptionRecord.from_dict(item) for item in _read_array(arguments.assumptions)
        )
        fidelity, authorities, assessments = _fidelity_channel(arguments)
        computed = compute_verdict(
            coverage,
            promotion,
            frame_check,
            candidate_digest=arguments.candidate,
            evaluated_position=arguments.evaluated_position,
            receipts=receipts,
            assumptions=assumptions,
            validator=arguments.validator,
            fidelity=fidelity,
        )
        payload: dict[str, Any] = {
            "verdict": computed.to_dict(),
            "headline": render_headline(computed),
            "attestation_subject_digest": digest_obj(
                verdict_attestation_subject(computed, coverage)
            ),
        }
        if fidelity is not None:
            payload["fidelity"] = {
                "disposition": fidelity.disposition,
                "reason": fidelity.reason,
                "unserved": list(fidelity.unserved),
                "unasked": list(fidelity.unasked),
                "shortfalls": list(fidelity.shortfalls),
                "uncited": list(fidelity.uncited),
                "risk_acceptance_available": risk_acceptance_available(assessments),
            }
            payload["directive_receipts"] = [
                dict(row) for row in authority_receipts(authorities)
            ]
            payload["fidelity"]["stated_refs"] = sorted(
                a.utterance_id for a in authorities if a.tier == STATED
            )
            history = [str(item) for item in _read_array(arguments.fidelity_history)]
            if history:
                payload["fidelity"]["risk_acceptance_is_systematic"] = (
                    risk_acceptance_is_systematic([*history, fidelity.disposition])
                )
        handover_items = _read_array(arguments.handovers)
        if handover_items:
            handovers = tuple(Handover.from_dict(item) for item in handover_items)
            composition = compose_done(
                coverage, handovers, computed, validator=arguments.validator
            )
            payload["composition"] = composition.to_dict()
            if composition.reachable:
                payload["done_attestation_subject_digest"] = digest_obj(
                    done_attestation_subject(composition, coverage, computed)
                )
        _emit(payload)
        return
    if arguments.command == "audit":
        from factory_core.audit import AuditReport, audit_run, vocabulary_digest
        from factory_core.handover import Handover, compose_done
        from factory_core.verdict import (
            AssumptionRecord,
            CharacterizationReceipt,
            CoverageMap,
            FrameCheckResult,
            PromotionFloor,
            compute_verdict,
        )

        coverage = CoverageMap.from_dict(_read_object(arguments.coverage))
        promotion = PromotionFloor.from_dict(_read_object(arguments.promotion))
        frame_check = (
            FrameCheckResult.from_dict(_read_object(arguments.frame_check))
            if arguments.frame_check
            else None
        )
        audit_fidelity, _audit_authorities, _audit_assessments = _fidelity_channel(
            arguments
        )
        computed = compute_verdict(
            coverage,
            promotion,
            frame_check,
            candidate_digest=arguments.candidate,
            evaluated_position=arguments.evaluated_position,
            fidelity=audit_fidelity,
            receipts=tuple(
                CharacterizationReceipt.from_dict(item)
                for item in _read_array(arguments.receipts)
            ),
            assumptions=tuple(
                AssumptionRecord.from_dict(item)
                for item in _read_array(arguments.assumptions)
            ),
            validator=arguments.validator,
        )
        handovers = tuple(
            Handover.from_dict(item) for item in _read_array(arguments.handovers)
        )
        composition = compose_done(
            coverage, handovers, computed, validator=arguments.validator
        )
        audit_report: AuditReport = audit_run(
            run_id=arguments.run_id,
            coverage=coverage,
            verdict=computed,
            composition=composition,
            handovers=handovers,
        )
        _emit(
            {
                "run_id": audit_report.run_id,
                "codes": list(audit_report.codes),
                "vocab_digest": vocabulary_digest(),
                "rows": list(audit_report.rows()),
                "gating": "none — this audit runs after disposition and narrows nothing",
            }
        )
        return
    if arguments.command == "audit-table":
        from factory_core.audit import AuditError, frequency_table

        try:
            table = frequency_table(_read_array(arguments.rows))
        except AuditError as error:
            raise SystemExit(f"audit-table: {error}") from error
        _emit({"table": list(table)})
        return
    raise ValueError(f"unsupported command: {arguments.command}")


def _fidelity_channel(arguments: Any) -> tuple[Any, tuple[Any, ...], Any]:
    """The fidelity channel for a verdict, plus the authorities behind it.

    Shared by `verdict` and `audit` deliberately. `audit` previously called
    compute_verdict with no fidelity argument, which pinned every audited verdict
    to INCOMPLETE and returned an empty code set — silencing the entire audit
    vocabulary. One helper, two callers, so that cannot recur by omission.

    Returns ``(fidelity, authorities, assessments)``; ``(None, (), ())`` when the
    caller supplied no enumeration.
    """

    from factory_core.directive_authority import (
        ENGAGED,
        DirectiveAuthorityError,
        Utterance,
        tier_rank,
    )
    from factory_core.directive_authority import (
        derive as derive_authority,
    )
    from factory_core.fidelity import (
        Assessment,
        Attribution,
        Deliverable,
        FidelityError,
    )
    from factory_core.fidelity import (
        render as render_fidelity,
    )

    if not getattr(arguments, "deliverables", ""):
        return None, (), ()
    if not getattr(arguments, "utterances", ""):
        raise ValueError(
            "--deliverables requires --utterances: a citation the factory cannot "
            "check against the transcript is a claim, not authority"
        )
    try:
        utterances = tuple(
            Utterance(
                utterance_id=str(item["utterance_id"]),
                speaker=str(item["speaker"]),
                position=int(item["position"]),
                line_digest=str(item["line_digest"]),
                surfaced=bool(item.get("surfaced", True)),
                references=tuple(str(r) for r in item.get("references", ())),
            )
            for item in _read_array(arguments.utterances)
        )
        seen_ids = [u.utterance_id for u in utterances]
        if len(set(seen_ids)) != len(seen_ids):
            # A duplicate id lets one human row authorize every agent row sharing
            # it, and lets a self-reference manufacture ENGAGED.
            raise ValueError("duplicate utterance_id: a citation must address one line")
        authorities = tuple(derive_authority(u, utterances) for u in utterances)
        authorized_refs = frozenset(
            a.utterance_id for a in authorities if tier_rank(a.tier) >= tier_rank(ENGAGED)
        )
    except (DirectiveAuthorityError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"directive authority input refused: {exc}") from exc
    try:
        deliverables = tuple(
            Deliverable(
                deliverable_id=str(item["deliverable_id"]),
                directive_ref=str(item["directive_ref"]),
                summary=str(item.get("summary", "")),
            )
            for item in _read_array(arguments.deliverables)
        )
        assessments = tuple(
            Assessment(
                deliverable_id=str(item["deliverable_id"]),
                disposition=str(item["disposition"]),
                divergence=str(item.get("divergence", "")),
                fix_scope=str(item.get("fix_scope", "")),
            )
            for item in _read_array(arguments.assessments)
        )
        attributions = tuple(
            Attribution(
                artifact_ref=str(item["artifact_ref"]),
                deliverable_id=str(item.get("deliverable_id", "")),
            )
            for item in _read_array(arguments.attributions)
        )
        fidelity = render_fidelity(deliverables, assessments, attributions, authorized_refs)
    except (FidelityError, KeyError, TypeError) as exc:
        raise ValueError(f"fidelity input refused: {exc}") from exc
    return fidelity, authorities, assessments


def main(argv: list[str] | None = None) -> int:
    """Run one command; all refused controls exit non-zero with no traceback laundering."""

    arguments: argparse.Namespace | None = None
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "version":
            from factory_runtime.version import format_version
            print(format_version(as_json=arguments.json))
            return 0
        if arguments.command == "cleanup":
            from factory_runtime.storage import archive_run, inventory, restore_archive
            if arguments.restore is not None:
                if (arguments.destination is None or arguments.run or arguments.archive
                        or arguments.archive_dir):
                    raise ValueError("restore requires --destination and no --run/--archive")
                result = restore_archive(arguments.restore, arguments.destination,
                                         apply=arguments.apply)
            elif arguments.archive:
                if not arguments.run or arguments.destination is not None:
                    raise ValueError("archive requires --run and no --destination")
                result = archive_run(arguments.runs, arguments.run,
                                     completed=arguments.completed, apply=arguments.apply,
                                     archive_dir=arguments.archive_dir)
            else:
                if (arguments.run or arguments.apply or arguments.completed
                        or arguments.destination or arguments.archive_dir):
                    raise ValueError("inventory takes only --runs; use --archive to select a run")
                result = inventory(arguments.runs)
            print(json.dumps(result, sort_keys=True))
            return 0
        if arguments.command == "run":
            import subprocess
            script = Path(__file__).resolve().parents[1] / "harness/factory.sh"
            command = ["bash", str(script), arguments.run, arguments.task,
                       "--repo", arguments.repo, "--runs", arguments.runs]
            for key in ("log", "budget"):
                value = getattr(arguments, key)
                if value is not None:
                    command.extend([f"--{key}", str(value)])
            return subprocess.run(command, check=False,
                                  env={**os.environ, "FACTORY_PYTHON": sys.executable}).returncode
        _execute(arguments)
    except (OSError, ValueError) as exc:
        print(f"factory: refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
