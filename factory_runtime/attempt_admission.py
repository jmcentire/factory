"""Signed, fail-closed admission for one typed Factory attempt.

The legacy ``build-and-validate`` API remains an intentionally low-level API.  This
module is its public control-plane companion: it turns a signed, versioned package
into the *same* arguments only after validating every executable and target-runtime
fact.  It never inherits commands, paths, or environment variables from the caller.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory_core.manifest import digest_obj
from factory_runtime.authority import AuthorityPolicy
from factory_runtime.isolation import DENY_ALL_NETWORK, LoopbackGrant, NetworkPolicy
from factory_runtime.lanes import LaneRole
from factory_runtime.native_test import (
    native_execution_identity_digest,
    native_test_execution_digests,
)
from factory_runtime.schema import validate_document
from factory_runtime.snapshot import SnapshotError, tree_digest
from factory_runtime.tessera import TesseraCli, TesseraVerificationError, VerifiedEnvelope

SCHEMA_VERSION = "factory-one-attempt-admission/3"
_LEGACY_SCHEMA_VERSION = "factory-one-attempt-admission/1"
_V2_SCHEMA_VERSION = "factory-one-attempt-admission/2"
ENVELOPE_KIND = "factory-one-attempt-admission"


class AttemptAdmissionError(ValueError):
    """A typed attempt package is not safe to dispatch."""


@dataclass(frozen=True)
class AdmittedAttempt:
    envelope: VerifiedEnvelope
    run_id: str
    attempt_id: str
    build: Mapping[str, Any]
    coder_command: tuple[str, ...]
    tester_command: tuple[str, ...]
    validator_command: tuple[str, ...]
    coder_trusted_paths: tuple[Path, ...]
    tester_trusted_paths: tuple[Path, ...]
    validator_trusted_paths: tuple[Path, ...]
    validator_environment: Mapping[str, str]
    validator_runtime_paths: tuple[Path, ...]
    validator_network_policy: NetworkPolicy
    prebuilt_author_outputs: Mapping[LaneRole, Path] | None
    native_runtime: Mapping[str, object] | None
    receipt: Mapping[str, Any]


def _command(value: object, label: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise AttemptAdmissionError(f"{label} command must be a non-empty string argv array")
    return tuple(value)


def _paths(value: object, label: str) -> tuple[Path, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise AttemptAdmissionError(f"{label} paths must be an array")
    paths: list[Path] = []
    for raw in value:
        if not isinstance(raw, str) or not raw:
            raise AttemptAdmissionError(f"{label} path must be a non-empty absolute path")
        path = Path(raw)
        if not path.is_absolute() or path.is_symlink() or not path.exists():
            raise AttemptAdmissionError(
                f"{label} path is unavailable, relative, or symlinked: {raw}"
            )
        resolved = path.resolve(strict=True)
        if resolved not in paths:
            paths.append(resolved)
    return tuple(paths)


def _profile(
    payload: Mapping[str, Any], role: str, *, sealed: bool
) -> tuple[tuple[str, ...], tuple[Path, ...], Mapping[str, object] | None]:
    profiles = payload.get("execution_profiles")
    if not isinstance(profiles, Mapping):
        raise AttemptAdmissionError("admission package has no execution_profiles object")
    raw = profiles.get(role)
    if not isinstance(raw, Mapping):
        raise AttemptAdmissionError(f"admission package has no {role} execution profile")
    if set(raw) - {"command", "trusted_paths", "qualified_runner_receipt"}:
        raise AttemptAdmissionError(f"{role} execution profile has unsupported fields")
    # A qualified runner receipt is evidence only: it is digest-bound by the
    # enclosing signature and never converted into a shell command.  The command
    # remains an explicit argv array, preserving the existing no-shell boundary.
    receipt = raw.get("qualified_runner_receipt")
    if receipt is not None:
        if not isinstance(receipt, Mapping) or str(receipt.get("role", "")) != role:
            raise AttemptAdmissionError(f"{role} qualified runner receipt has wrong role")
        if sealed:
            if set(receipt) != {"role", "path", "digest"}:
                raise AttemptAdmissionError(
                    f"{role} sealed runner receipt reference has invalid fields"
                )
            receipt_path = receipt.get("path")
            if not isinstance(receipt_path, str):
                raise AttemptAdmissionError(f"{role} sealed runner receipt path is invalid")
            paths = _paths([receipt_path], f"{role} sealed runner receipt")
            if len(paths) != 1 or not paths[0].is_file():
                raise AttemptAdmissionError(f"{role} sealed runner receipt is not a regular file")
            try:
                document = json.loads(paths[0].read_text(encoding="utf-8"))
                validate_document("runner-receipt", document)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                raise AttemptAdmissionError(f"{role} sealed runner receipt is invalid") from exc
            if document.get("role") != role or digest_obj(document) != receipt.get("digest"):
                raise AttemptAdmissionError(f"{role} sealed runner receipt does not bind")
        else:
            claimed = receipt.get("digest")
            body = {str(key): value for key, value in receipt.items() if key != "digest"}
            if not isinstance(claimed, str) or digest_obj(body) != claimed:
                raise AttemptAdmissionError(f"{role} qualified runner receipt digest does not bind")
    if sealed:
        if receipt is None or set(raw) != {"qualified_runner_receipt"}:
            raise AttemptAdmissionError(
                f"{role} sealed profile must contain only its qualified runner receipt"
            )
        return (), (), receipt
    return _command(raw.get("command"), role), _paths(raw.get("trusted_paths", []), role), receipt


def _sealed_author_outputs(payload: Mapping[str, Any]) -> Mapping[LaneRole, Path] | None:
    raw = payload.get("sealed_author_outputs")
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or set(raw) != {"coder", "tester"}:
        raise AttemptAdmissionError("sealed author outputs must name exactly Coder and Tester")
    outputs: dict[LaneRole, Path] = {}
    for role in (LaneRole.CODER, LaneRole.TESTER):
        reference = raw.get(str(role))
        if not isinstance(reference, Mapping) or set(reference) != {"source_path", "tree_digest"}:
            raise AttemptAdmissionError(f"sealed {role} output reference has invalid fields")
        source_path = reference.get("source_path")
        if not isinstance(source_path, str):
            raise AttemptAdmissionError(f"sealed {role} output source is invalid")
        paths = _paths([source_path], f"sealed {role} output")
        if len(paths) != 1 or not paths[0].is_dir():
            raise AttemptAdmissionError(f"sealed {role} output is not a directory")
        try:
            actual_digest = tree_digest(paths[0])
        except SnapshotError as exc:
            raise AttemptAdmissionError(f"sealed {role} output tree is invalid") from exc
        if actual_digest != reference.get("tree_digest"):
            raise AttemptAdmissionError(f"sealed {role} output tree digest does not bind")
        outputs[role] = paths[0]
    coder, tester = outputs[LaneRole.CODER], outputs[LaneRole.TESTER]
    if coder == tester or coder.is_relative_to(tester) or tester.is_relative_to(coder):
        raise AttemptAdmissionError("sealed Coder and Tester outputs must be disjoint")
    return outputs


def _target_profile(
    payload: Mapping[str, Any],
) -> tuple[Mapping[str, str], tuple[Path, ...], NetworkPolicy, Mapping[str, object] | None]:
    raw = payload.get("target_runtime_profile")
    if not isinstance(raw, Mapping):
        raise AttemptAdmissionError("admission package has no target_runtime_profile")
    allowed = {"candidate_launch", "runtime_read_paths", "readiness", "loopback", "mode", "test_entrypoint"}
    if set(raw) - allowed:
        raise AttemptAdmissionError("target runtime profile has unsupported fields")
    launch = _command(raw.get("candidate_launch"), "target candidate_launch")
    if raw.get("mode") == "native-two-profile":
        test = _command(raw.get("test_entrypoint"), "target test_entrypoint")
        readiness = raw.get("readiness")
        if not isinstance(readiness, Mapping) or set(readiness) != {"entrypoint", "timeout_seconds", "interval_seconds", "max_attempts"}:
            raise AttemptAdmissionError("native readiness declaration is invalid")
        ready = _command(readiness.get("entrypoint"), "native readiness")
        bounds = ("timeout_seconds", "interval_seconds", "max_attempts")
        if any(not isinstance(readiness[name], int) or readiness[name] <= 0 for name in bounds):
            raise AttemptAdmissionError("native readiness bounds must be positive integers")
        loopback = raw.get("loopback")
        if not isinstance(loopback, list) or not loopback:
            raise AttemptAdmissionError("native loopback must declare endpoint shapes")
        endpoints: list[Mapping[str, object]] = []
        for endpoint in loopback:
            if not isinstance(endpoint, Mapping) or set(endpoint) != {"protocol", "operations", "count"}:
                raise AttemptAdmissionError("native loopback endpoint shape is invalid")
            if endpoint["protocol"] not in {"tcp", "udp"} or not isinstance(endpoint["operations"], list) or not endpoint["operations"] or set(endpoint["operations"]) - {"bind", "connect"} or not isinstance(endpoint["count"], int) or not 1 <= endpoint["count"] <= 64:
                raise AttemptAdmissionError("native loopback endpoint is unsupported")
            endpoints.append({"protocol": endpoint["protocol"], "operations": sorted(set(endpoint["operations"])), "count": endpoint["count"]})
        execution = native_test_execution_digests(
            launch, test, readiness_entrypoint=ready,
            readiness_timeout_seconds=readiness["timeout_seconds"],
            readiness_interval_seconds=readiness["interval_seconds"],
            readiness_max_attempts=readiness["max_attempts"],
        )
        return {}, _paths(raw.get("runtime_read_paths", []), "target runtime"), DENY_ALL_NETWORK, {
            "candidate_launch": launch, "test_entrypoint": test, "readiness_entrypoint": ready,
            "readiness_timeout_seconds": readiness["timeout_seconds"],
            "readiness_interval_seconds": readiness["interval_seconds"],
            "readiness_max_attempts": readiness["max_attempts"],
            "loopback": tuple(endpoints), "identity": native_execution_identity_digest(execution),
        }
    readiness = raw.get("readiness")
    if not isinstance(readiness, Mapping) or set(readiness) - {
        "entrypoint",
        "timeout_seconds",
        "interval_seconds",
        "max_attempts",
    }:
        raise AttemptAdmissionError("target runtime profile has an invalid readiness declaration")
    entrypoint = readiness.get("entrypoint")
    if not isinstance(entrypoint, str) or not entrypoint:
        raise AttemptAdmissionError("target readiness entrypoint is required")
    for name in ("timeout_seconds", "interval_seconds", "max_attempts"):
        if not isinstance(readiness.get(name), int) or int(readiness[name]) <= 0:
            raise AttemptAdmissionError(f"target readiness {name} must be positive")
    loopback = raw.get("loopback", {})
    if not isinstance(loopback, Mapping) or set(loopback) - {"tcp_ports", "udp_ports"}:
        raise AttemptAdmissionError("target loopback declaration is invalid")
    environment: dict[str, str] = {
        "FACTORY_CANDIDATE_LAUNCH": json.dumps(launch),
        "FACTORY_READINESS_ENTRYPOINT": entrypoint,
        "FACTORY_READINESS_TIMEOUT_SECONDS": str(readiness["timeout_seconds"]),
        "FACTORY_READINESS_INTERVAL_SECONDS": str(readiness["interval_seconds"]),
        "FACTORY_READINESS_MAX_ATTEMPTS": str(readiness["max_attempts"]),
    }
    grants: list[LoopbackGrant] = []
    for protocol, variable in (
        ("tcp_ports", "FACTORY_LOOPBACK_TCP_PORTS"),
        ("udp_ports", "FACTORY_LOOPBACK_UDP_PORTS"),
    ):
        ports = loopback.get(protocol, [])
        if not isinstance(ports, list) or any(
            not isinstance(port, int) or not 1 <= port <= 65535 for port in ports
        ):
            raise AttemptAdmissionError(f"target loopback {protocol} must contain valid ports")
        if ports != sorted(set(ports)):
            raise AttemptAdmissionError(
                f"target loopback {protocol} must be unique and ascending"
            )
        environment[variable] = ",".join(str(port) for port in ports)
        normalized = tuple(ports)
        if normalized:
            # A standalone Validator both launches the target (bind) and drives its
            # declared endpoint (connect). Grant neither direction to other lanes.
            grants.extend(
                (
                    LoopbackGrant(protocol.removesuffix("_ports"), "bind", normalized),
                    LoopbackGrant(protocol.removesuffix("_ports"), "connect", normalized),
                )
            )
    network_policy = NetworkPolicy.declared_loopback(grants) if grants else DENY_ALL_NETWORK
    return environment, _paths(raw.get("runtime_read_paths", []), "target runtime"), network_policy, None


def _verify_predecessors(
    payload: Mapping[str, Any], *, tessera: TesseraCli, verifier_key: str
) -> None:
    raw = payload.get("predecessors", {"required": False, "artifacts": []})
    if not isinstance(raw, Mapping) or set(raw) - {"required", "artifacts"}:
        raise AttemptAdmissionError("predecessor declaration is invalid")
    required = raw.get("required")
    artifacts = raw.get("artifacts", [])
    if not isinstance(required, bool) or not isinstance(artifacts, list):
        raise AttemptAdmissionError("predecessor declaration is invalid")
    if required and not artifacts:
        raise AttemptAdmissionError("required predecessor artifacts are omitted")
    if not required and artifacts:
        raise AttemptAdmissionError(
            "predecessor artifacts supplied although contract declares none"
        )
    for artifact in artifacts:
        if not isinstance(artifact, Mapping) or set(artifact) != {
            "envelope_path",
            "payload_digest",
            "kind",
        }:
            raise AttemptAdmissionError("predecessor artifact has invalid shape")
        path = artifact["envelope_path"]
        if not isinstance(path, str) or not Path(path).is_absolute() or Path(path).is_symlink():
            raise AttemptAdmissionError("predecessor artifact envelope path is unsafe")
        try:
            tessera.verify_json(
                path,
                trusted_public_keys=(verifier_key,),
                expected_kind=str(artifact["kind"]),
                expected_payload_digest=str(artifact["payload_digest"]),
            )
        except TesseraVerificationError as exc:
            raise AttemptAdmissionError(
                f"predecessor artifact is not current signed material: {exc}"
            ) from exc


def admit_attempt_package(
    envelope_path: str | Path, *, policy: AuthorityPolicy, tessera: TesseraCli
) -> AdmittedAttempt:
    """Verify a package and return only explicit, closed execution facts.

    The Validator signs this package because it is the role that owns executable
    acceptance configuration.  The package is rejected before an orchestrator or
    lane is constructed, retaining the no-lane-on-refusal invariant.
    """
    validator_keys = tuple(
        principal.public_key
        for principal in policy.principals.values()
        if principal.kind == "agent" and principal.public_key
    )
    if not validator_keys:
        raise AttemptAdmissionError("policy has no enrolled agent signer")
    try:
        envelope = tessera.verify_json(
            envelope_path,
            trusted_public_keys=validator_keys,
            expected_kind=ENVELOPE_KIND,
        )
    except TesseraVerificationError as exc:
        raise AttemptAdmissionError(f"attempt admission signature is invalid: {exc}") from exc
    payload = envelope.payload
    schema_version = payload.get("schema_version")
    if schema_version not in {SCHEMA_VERSION, _V2_SCHEMA_VERSION, _LEGACY_SCHEMA_VERSION}:
        raise AttemptAdmissionError("attempt admission package version is unsupported")
    required = {
        "schema_version",
        "run_id",
        "attempt_id",
        "identities",
        "build",
        "execution_profiles",
        "target_runtime_profile",
        "one_attempt_policy",
        "predecessors",
    }
    allowed = set(required)
    if schema_version == SCHEMA_VERSION:
        allowed.add("sealed_author_outputs")
    if set(payload) != required and set(payload) != allowed:
        raise AttemptAdmissionError("attempt admission package has missing or unsupported fields")
    identities = payload.get("identities")
    if not isinstance(identities, Mapping) or set(identities) != {
        "implementer",
        "tester",
        "verifier",
    }:
        raise AttemptAdmissionError("attempt admission identities are invalid")
    verifier_identity = str(identities["verifier"])
    verifier = policy.principal(verifier_identity)
    if (
        verifier is None
        or verifier.kind != "agent"
        or verifier.public_key != envelope.public_key
    ):
        raise AttemptAdmissionError(
            "attempt admission signer identity does not match the enrolled Validator"
        )
    build = payload.get("build")
    if not isinstance(build, Mapping):
        raise AttemptAdmissionError("attempt admission build inputs are invalid")
    policy_raw = payload.get("one_attempt_policy")
    if policy_raw != {
        "retry": "forbidden",
        "retention": "retain",
        "terminal_disposition": "record",
    }:
        raise AttemptAdmissionError(
            "attempt policy must explicitly forbid retry and retain terminal evidence"
        )
    prebuilt_author_outputs = _sealed_author_outputs(payload)
    coder_command, coder_paths, _ = _profile(
        payload, "coder", sealed=prebuilt_author_outputs is not None
    )
    tester_command, tester_paths, _ = _profile(
        payload, "tester", sealed=prebuilt_author_outputs is not None
    )
    validator_command, validator_paths, _ = _profile(payload, "validator", sealed=False)
    target_env, runtime_paths, network_policy, native_runtime = _target_profile(payload)
    _verify_predecessors(payload, tessera=tessera, verifier_key=verifier.public_key)
    run_id, attempt_id = str(payload["run_id"]), str(payload["attempt_id"])
    if (
        not run_id
        or not attempt_id
        or build.get("run_id") not in (None, run_id)
        or build.get("attempt_id") not in (None, attempt_id)
    ):
        raise AttemptAdmissionError(
            "attempt admission package has mismatched run or attempt identity"
        )
    receipt = {
        "schema_version": "factory-one-attempt-admission-receipt/1",
        "admission_digest": envelope.payload_digest,
        "admission_envelope_digest": envelope.envelope_digest,
        "run_id": run_id,
        "attempt_id": attempt_id,
        "policy": dict(policy_raw),
        "profile_digests": {
            "coder": digest_obj(dict(payload["execution_profiles"]["coder"])),
            "tester": digest_obj(dict(payload["execution_profiles"]["tester"])),
            "validator": digest_obj(dict(payload["execution_profiles"]["validator"])),
            "target_runtime": digest_obj(dict(payload["target_runtime_profile"])),
        },
        "validator_network_policy": network_policy.identity,
        "sealed_author_outputs": (
            {
                str(role): tree_digest(path)
                for role, path in sorted(
                    prebuilt_author_outputs.items(), key=lambda item: str(item[0])
                )
            }
            if prebuilt_author_outputs is not None
            else None
        ),
    }
    return AdmittedAttempt(
        envelope,
        run_id,
        attempt_id,
        dict(build),
        coder_command,
        tester_command,
        validator_command,
        coder_paths,
        tester_paths,
        validator_paths,
        target_env,
        runtime_paths,
        network_policy,
        prebuilt_author_outputs,
        native_runtime,
        receipt,
    )


def retain_admission_receipt(root: str | Path, admitted: AdmittedAttempt) -> Path:
    """Persist a host-owned receipt before dispatch; never overwrite prior evidence."""
    directory = (
        Path(root)
        / admitted.run_id
        / "evidence"
        / "attempt-admissions"
        / admitted.envelope.payload_digest.removeprefix("sha256:")
    )
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / "receipt.json"
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(admitted.receipt, handle, sort_keys=True, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    return path


def dispatch_admitted_attempt(orchestrator: Any, admitted: AdmittedAttempt) -> Any:
    """Project an admitted package into the existing executor without a second API.

    This is deliberately a library boundary: operators that already construct a
    ``FactoryWorkflow`` can use it without recreating a shell harness.  All required
    low-level authority inputs still pass through ``build_and_validate`` unchanged.
    """
    from factory_core.correction import CorrectionRecord
    from factory_core.independence import IndependenceRecord
    from factory_core.monitors import Monitor
    from factory_runtime.evidence_plane import DeterminismRecord, SurfaceEvidence

    build = admitted.build
    required = {
        "target_manifest_path",
        "pattern_catalog_path",
        "build_plan_path",
        "acceptance_catalog_path",
        "acceptance_catalog_human_receipt_path",
        "acceptance_catalog_validator_receipt_path",
        "resume_checkpoint_path",
        "expected_resume_checkpoint_digest",
        "genesis_path",
        "resume_configuration_sources",
        "implementer_identity",
        "tester_identity",
        "verifier_identity",
        "verifier_key_path",
        "surface_evidence",
        "determinism_records",
        "lane",
        "independence",
    }
    missing = sorted(required - set(build))
    if missing:
        raise AttemptAdmissionError(
            "attempt admission build inputs are incomplete: " + ", ".join(missing)
        )
    if (
        build["implementer_identity"] == build["tester_identity"]
        or build["implementer_identity"] == build["verifier_identity"]
        or build["tester_identity"] == build["verifier_identity"]
    ):
        raise AttemptAdmissionError("attempt admission identities must be separated")
    # Retain only after all package fields have been parsed and checked; an invalid
    # package therefore cannot create a lane or evidence directory.
    retain_admission_receipt(orchestrator.workflow.root, admitted)
    try:
        return orchestrator.build_and_validate(
            admitted.run_id,
            attempt_id=admitted.attempt_id,
            target_manifest_path=build["target_manifest_path"],
            pattern_catalog_path=build["pattern_catalog_path"],
            build_plan_path=build["build_plan_path"],
            acceptance_catalog_path=build["acceptance_catalog_path"],
            acceptance_catalog_human_receipt_path=build["acceptance_catalog_human_receipt_path"],
            acceptance_catalog_validator_receipt_path=build[
                "acceptance_catalog_validator_receipt_path"
            ],
            coder_command=admitted.coder_command,
            tester_command=admitted.tester_command,
            validator_command=admitted.validator_command,
            coder_trusted_paths=admitted.coder_trusted_paths,
            tester_trusted_paths=admitted.tester_trusted_paths,
            validator_trusted_paths=admitted.validator_trusted_paths,
            prebuilt_author_outputs=admitted.prebuilt_author_outputs,
            resume_checkpoint_path=build["resume_checkpoint_path"],
            expected_resume_checkpoint_digest=build["expected_resume_checkpoint_digest"],
            genesis_path=build["genesis_path"],
            resume_configuration_sources=build["resume_configuration_sources"],
            implementer_identity=build["implementer_identity"],
            tester_identity=build["tester_identity"],
            verifier_identity=build["verifier_identity"],
            verifier_key_path=build["verifier_key_path"],
            surface_evidence=tuple(SurfaceEvidence(**item) for item in build["surface_evidence"]),
            determinism_records=tuple(
                DeterminismRecord(**item) for item in build["determinism_records"]
            ),
            lane=build["lane"],
            independence=IndependenceRecord.from_dict(build["independence"]),
            monitors=tuple(Monitor.from_dict(item) for item in build.get("monitors", [])),
            monitor_declared_unit_count=int(build.get("monitor_declared_unit_count", 0)),
            correction=(
                CorrectionRecord.from_dict(build["correction"]) if build.get("correction") else None
            ),
            repair_brief_path=build.get("repair_brief_path"),
            changed_existing_tests=tuple(build.get("changed_existing_tests", [])),
            test_change_authorization_path=build.get("test_change_authorization_path"),
            test_change_human_receipt_path=build.get("test_change_human_receipt_path"),
            test_change_validator_receipt_path=build.get("test_change_validator_receipt_path"),
            validator_profile_environment=admitted.validator_environment,
            validator_runtime_paths=admitted.validator_runtime_paths,
            validator_network_policy=admitted.validator_network_policy,
            candidate_launch=(
                admitted.native_runtime["candidate_launch"]
                if admitted.native_runtime is not None else ()
            ),
            candidate_loopback=(
                admitted.native_runtime["loopback"]
                if admitted.native_runtime is not None else ()
            ),
            native_test_entrypoint=(
                admitted.native_runtime["test_entrypoint"]
                if admitted.native_runtime is not None else ()
            ),
            native_readiness_entrypoint=(
                admitted.native_runtime["readiness_entrypoint"]
                if admitted.native_runtime is not None else ()
            ),
            native_readiness_timeout_seconds=(
                admitted.native_runtime["readiness_timeout_seconds"]
                if admitted.native_runtime is not None else 30.0
            ),
            native_readiness_interval_seconds=(
                admitted.native_runtime["readiness_interval_seconds"]
                if admitted.native_runtime is not None else 0.5
            ),
            native_readiness_max_attempts=(
                admitted.native_runtime["readiness_max_attempts"]
                if admitted.native_runtime is not None else 120
            ),
            native_runtime_read_paths=admitted.validator_runtime_paths,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AttemptAdmissionError(f"attempt admission build inputs are invalid: {exc}") from exc
