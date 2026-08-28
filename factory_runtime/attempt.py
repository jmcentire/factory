"""Checkpoint-bound, Factory-owned execution of immutable attempts.

The campaign layer receives this executor, never a host command.  The typed
invocation is deliberately the same shape as the established orchestrator
entrypoint; no target-specific wrapper has a chance to reinterpret a repair
brief or manufacture terminal state.
"""

from __future__ import annotations

import json
import stat
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from factory_core.correction import CorrectionRecord
from factory_core.independence import IndependenceRecord
from factory_core.manifest import digest_bytes
from factory_core.monitors import Monitor
from factory_runtime.campaign import CampaignAttemptOutcome
from factory_runtime.evidence_plane import DeterminismRecord, SurfaceEvidence
from factory_runtime.lanes import LaneRole
from factory_runtime.orchestrator import FactoryOrchestrator
from factory_runtime.workflow import FactoryWorkflow

_CONFIG_VERSION = "factory-attempt/2"


class AttemptContractError(ValueError):
    """A typed attempt config cannot be resolved from checkpoint sources."""


@dataclass(frozen=True)
class FactoryAttemptConfig:
    """Closed JSON materialization of a :class:`FactoryAttemptInvocation`."""

    source_digest: str
    invocation: FactoryAttemptInvocation

    @classmethod
    def load(
        cls, path: str | Path, *, configuration_sources: Mapping[str, str | Path]
    ) -> FactoryAttemptConfig:
        raw = _read_regular(Path(path), label="attempt config")
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AttemptContractError("attempt config is not valid JSON") from exc
        if not isinstance(document, Mapping):
            raise AttemptContractError("attempt config must be a JSON object")
        required = {
            "schema_version",
            "artifacts",
            "roles",
            "prebuilt_author_outputs",
            "candidate_runtime",
            "candidate_launch",
            "surface_evidence",
            "determinism_records",
            "lane",
            "independence",
            "monitors",
            "monitor_declared_unit_count",
        }
        # A target that exercises a networked candidate declares the loopback endpoint shape it
        # needs (the Validator-only declared-loopback grant); targets without networking omit it.
        # A target may also declare a native acceptance-test argv the Validator runs generically.
        optional = {"candidate_loopback", "test_entrypoint"}
        if document.get("schema_version") == "factory-attempt/1":
            raise AttemptContractError(
                "attempt config factory-attempt/1 predates the sealed candidate runtime/launch "
                "contract; re-author as factory-attempt/2"
            )
        if (set(document) - optional) != required:
            raise AttemptContractError("attempt config fields are incomplete or open")
        if document["schema_version"] != _CONFIG_VERSION:
            raise AttemptContractError("attempt config has an unsupported schema_version")
        sources = {name: Path(value) for name, value in configuration_sources.items()}
        artifacts = _mapping(document["artifacts"], "artifacts")
        if set(artifacts) != {
            "target_manifest",
            "pattern_catalog",
            "build_plan",
            "acceptance_catalog",
            "acceptance_catalog_human_receipt",
            "acceptance_catalog_validator_receipt",
        }:
            raise AttemptContractError("attempt artifacts are incomplete or open")
        roles = _mapping(document["roles"], "roles")
        if set(roles) != {"coder", "tester", "validator"}:
            raise AttemptContractError("attempt roles are incomplete or open")
        coder = _role_command(sources, roles["coder"], "coder")
        tester = _role_command(sources, roles["tester"], "tester")
        validator = _role_command(sources, roles["validator"], "validator")
        prebuilt_author_outputs = _prebuilt_author_outputs(
            sources, document["prebuilt_author_outputs"]
        )
        candidate_runtime_raw = document["candidate_runtime"]
        candidate_runtime_path = (
            None
            if candidate_runtime_raw is None
            else _directory_source(sources, candidate_runtime_raw, "candidate runtime")
        )
        candidate_launch = tuple(
            _string(item, "candidate launch argument")
            for item in _array(document["candidate_launch"], "candidate launch")
        )
        if candidate_launch and not Path(candidate_launch[0]).is_absolute():
            raise AttemptContractError("candidate launch argv[0] must be an absolute path")
        candidate_loopback = _candidate_loopback(document.get("candidate_loopback", []))
        native_test_entrypoint = tuple(
            _string(item, "test entrypoint argument")
            for item in _array(document.get("test_entrypoint", []), "test entrypoint")
        )
        if candidate_loopback and not (candidate_launch or native_test_entrypoint):
            raise AttemptContractError(
                "candidate_loopback declares endpoints but neither candidate_launch nor "
                "test_entrypoint was provided"
            )
        if prebuilt_author_outputs is not None:
            # The runner-backed path has already created sealed Coder and Tester
            # trees.  Passing their runner commands into the deny-network build
            # loop would both be wrong and is explicitly rejected by that loop.
            coder_command: tuple[str, ...] = ()
            tester_command: tuple[str, ...] = ()
            coder_trusted_paths: tuple[Path, ...] = ()
            tester_trusted_paths: tuple[Path, ...] = ()
        else:
            coder_command, coder_trusted_paths, _ = coder
            tester_command, tester_trusted_paths, _ = tester
        invocation = FactoryAttemptInvocation(
            target_manifest_path=_source(sources, artifacts["target_manifest"], "target manifest"),
            pattern_catalog_path=_source(sources, artifacts["pattern_catalog"], "pattern catalog"),
            build_plan_path=_source(sources, artifacts["build_plan"], "build plan"),
            acceptance_catalog_path=_source(
                sources, artifacts["acceptance_catalog"], "acceptance catalog"
            ),
            acceptance_catalog_human_receipt_path=_source(
                sources, artifacts["acceptance_catalog_human_receipt"], "acceptance human receipt"
            ),
            acceptance_catalog_validator_receipt_path=_source(
                sources,
                artifacts["acceptance_catalog_validator_receipt"],
                "acceptance Validator receipt",
            ),
            coder_command=coder_command,
            tester_command=tester_command,
            validator_command=validator[0],
            coder_trusted_paths=coder_trusted_paths,
            tester_trusted_paths=tester_trusted_paths,
            validator_trusted_paths=validator[1],
            resume_checkpoint_path=Path(),
            expected_resume_checkpoint_digest="",
            genesis_path=Path(),
            resume_configuration_sources=sources,
            implementer_identity=coder[2],
            tester_identity=tester[2],
            verifier_identity=validator[2],
            verifier_key_path=Path(),
            surface_evidence=_surface_evidence(document["surface_evidence"]),
            determinism_records=_determinism_records(document["determinism_records"]),
            lane=_string(document["lane"], "lane"),
            independence=IndependenceRecord.from_dict(
                _mapping(document["independence"], "independence")
            ),
            prebuilt_author_outputs=prebuilt_author_outputs,
            monitors=tuple(
                Monitor.from_dict(_mapping(item, "monitor"))
                for item in _array(document["monitors"], "monitors")
            ),
            monitor_declared_unit_count=_nonnegative(
                document["monitor_declared_unit_count"], "monitor_declared_unit_count"
            ),
            candidate_runtime_path=candidate_runtime_path,
            candidate_launch=candidate_launch,
            candidate_loopback=candidate_loopback,
            native_test_entrypoint=native_test_entrypoint,
        )
        return cls(source_digest=digest_bytes(raw), invocation=invocation)

    def invocation_for(
        self,
        *,
        checkpoint: str | Path,
        checkpoint_digest: str,
        genesis: str | Path,
        verifier_key: str | Path,
    ) -> FactoryAttemptInvocation:
        return replace(
            self.invocation,
            resume_checkpoint_path=Path(checkpoint),
            expected_resume_checkpoint_digest=checkpoint_digest,
            genesis_path=Path(genesis),
            verifier_key_path=Path(verifier_key),
        )

@dataclass(frozen=True)
class FactoryAttemptInvocation:
    """All typed, checkpoint-verifiable inputs to one immutable attempt."""

    target_manifest_path: Path
    pattern_catalog_path: Path
    build_plan_path: Path
    acceptance_catalog_path: Path
    acceptance_catalog_human_receipt_path: Path
    acceptance_catalog_validator_receipt_path: Path
    coder_command: tuple[str, ...]
    tester_command: tuple[str, ...]
    validator_command: tuple[str, ...]
    coder_trusted_paths: tuple[Path, ...]
    tester_trusted_paths: tuple[Path, ...]
    validator_trusted_paths: tuple[Path, ...]
    resume_checkpoint_path: Path
    expected_resume_checkpoint_digest: str
    genesis_path: Path
    resume_configuration_sources: Mapping[str, Path]
    implementer_identity: str
    tester_identity: str
    verifier_identity: str
    verifier_key_path: Path
    surface_evidence: tuple[SurfaceEvidence, ...]
    determinism_records: tuple[DeterminismRecord, ...]
    lane: str
    independence: IndependenceRecord
    prebuilt_author_outputs: Mapping[LaneRole, Path] | None = None
    monitors: tuple[Monitor, ...] = ()
    monitor_declared_unit_count: int = 0
    correction: CorrectionRecord | None = None
    changed_existing_tests: tuple[str, ...] = ()
    test_change_authorization_path: Path | None = None
    test_change_human_receipt_path: Path | None = None
    test_change_validator_receipt_path: Path | None = None
    candidate_runtime_path: Path | None = None
    candidate_launch: tuple[str, ...] = ()
    candidate_loopback: tuple[Mapping[str, object], ...] = ()
    native_test_entrypoint: tuple[str, ...] = ()


class FactoryAttemptExecutor:
    """The sole Factory adapter from a campaign retry to orchestration."""

    def __init__(
        self,
        workflow: FactoryWorkflow,
        *,
        invocation: FactoryAttemptInvocation,
        orchestrator: FactoryOrchestrator | None = None,
    ) -> None:
        self.workflow = workflow
        self.invocation = invocation
        self.orchestrator = orchestrator or FactoryOrchestrator(workflow)

    def execute(
        self,
        run_id: str,
        *,
        attempt_id: str,
        repair_brief_path: Path | None,
    ) -> CampaignAttemptOutcome:
        values = self.invocation
        outcome = self.orchestrator.build_and_validate(
            run_id,
            attempt_id=attempt_id,
            target_manifest_path=values.target_manifest_path,
            pattern_catalog_path=values.pattern_catalog_path,
            build_plan_path=values.build_plan_path,
            acceptance_catalog_path=values.acceptance_catalog_path,
            acceptance_catalog_human_receipt_path=(
                values.acceptance_catalog_human_receipt_path
            ),
            acceptance_catalog_validator_receipt_path=(
                values.acceptance_catalog_validator_receipt_path
            ),
            coder_command=values.coder_command,
            tester_command=values.tester_command,
            validator_command=values.validator_command,
            coder_trusted_paths=values.coder_trusted_paths,
            tester_trusted_paths=values.tester_trusted_paths,
            validator_trusted_paths=values.validator_trusted_paths,
            prebuilt_author_outputs=values.prebuilt_author_outputs,
            resume_checkpoint_path=values.resume_checkpoint_path,
            expected_resume_checkpoint_digest=values.expected_resume_checkpoint_digest,
            genesis_path=values.genesis_path,
            resume_configuration_sources=values.resume_configuration_sources,
            implementer_identity=values.implementer_identity,
            tester_identity=values.tester_identity,
            verifier_identity=values.verifier_identity,
            verifier_key_path=values.verifier_key_path,
            surface_evidence=values.surface_evidence,
            determinism_records=values.determinism_records,
            lane=values.lane,
            independence=values.independence,
            monitors=values.monitors,
            monitor_declared_unit_count=values.monitor_declared_unit_count,
            correction=values.correction,
            repair_brief_path=repair_brief_path,
            candidate_runtime_path=values.candidate_runtime_path,
            candidate_launch=values.candidate_launch,
            candidate_loopback=values.candidate_loopback,
            native_test_entrypoint=values.native_test_entrypoint,
            changed_existing_tests=values.changed_existing_tests,
            test_change_authorization_path=values.test_change_authorization_path,
            test_change_human_receipt_path=values.test_change_human_receipt_path,
            test_change_validator_receipt_path=values.test_change_validator_receipt_path,
        )
        return CampaignAttemptOutcome(
            attempt_id=attempt_id,
            candidate_digest=outcome.candidate_digest,
            tests_digest=outcome.tests_digest,
            projection=outcome.projection,
            _passed=outcome.passed,
        )


def _read_regular(path: Path, *, label: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise AttemptContractError(f"{label} is unreadable") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 131_072:
        raise AttemptContractError(f"{label} must be a bounded regular file")
    return path.read_bytes()


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise AttemptContractError(f"{label} must be a JSON object")
    return value


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise AttemptContractError(f"{label} must be an array")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AttemptContractError(f"{label} must be a non-empty string")
    return value


def _nonnegative(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise AttemptContractError(f"{label} must be a non-negative integer")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise AttemptContractError(f"{label} must be a boolean")
    return value


def _candidate_loopback(value: object) -> tuple[Mapping[str, object], ...]:
    """Validate the target-declared loopback endpoint shape at admission (fail fast)."""

    specs: list[Mapping[str, object]] = []
    for entry in _array(value, "candidate loopback"):
        spec = _mapping(entry, "candidate loopback endpoint")
        if set(spec) != {"protocol", "operations", "count"}:
            raise AttemptContractError(
                "candidate loopback endpoint fields are incomplete or open"
            )
        protocol = _string(spec["protocol"], "loopback protocol")
        if protocol not in ("tcp", "udp"):
            raise AttemptContractError(f"unsupported loopback protocol: {protocol}")
        operations = tuple(
            _string(op, "loopback operation")
            for op in _array(spec["operations"], "loopback operations")
        )
        if not operations or any(op not in ("bind", "connect") for op in operations):
            raise AttemptContractError(
                "loopback operations must be a non-empty subset of {bind, connect}"
            )
        count = _nonnegative(spec["count"], "loopback count")
        if not (1 <= count <= 64):
            raise AttemptContractError("loopback count must be between 1 and 64")
        specs.append({"protocol": protocol, "operations": list(operations), "count": count})
    return tuple(specs)


def _source(sources: Mapping[str, Path], value: object, label: str) -> Path:
    name = _string(value, label)
    path = sources.get(name)
    if path is None:
        raise AttemptContractError(f"{label} does not name a supplied configuration source")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise AttemptContractError(f"{label} source is unreadable") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise AttemptContractError(f"{label} source is not a regular file")
    return path.resolve(strict=True)


def _role_command(
    sources: Mapping[str, Path], value: object, role: str
) -> tuple[tuple[str, ...], tuple[Path, ...], str]:
    raw = _mapping(value, f"{role} role")
    if set(raw) != {"identity", "executable_source", "arguments", "trusted_path_sources"}:
        raise AttemptContractError(f"{role} role fields are incomplete or open")
    executable = _source(sources, raw["executable_source"], f"{role} executable")
    arguments = tuple(
        _string(item, f"{role} argument")
        for item in _array(raw["arguments"], f"{role} arguments")
    )
    trusted = tuple(
        _source(sources, item, f"{role} trusted path")
        for item in _array(raw["trusted_path_sources"], f"{role} trusted path sources")
    )
    if not trusted:
        raise AttemptContractError(f"{role} trusted path sources cannot be empty")
    return ((str(executable), *arguments), trusted, _string(raw["identity"], f"{role} identity"))


def _prebuilt_author_outputs(
    sources: Mapping[str, Path], value: object
) -> Mapping[LaneRole, Path] | None:
    if value is None:
        return None
    raw = _mapping(value, "prebuilt author outputs")
    if set(raw) != {"coder", "tester"}:
        raise AttemptContractError(
            "prebuilt author outputs must name exactly coder and tester"
        )
    return {
        LaneRole.CODER: _directory_source(sources, raw["coder"], "Coder author output"),
        LaneRole.TESTER: _directory_source(
            sources, raw["tester"], "Tester author output"
        ),
    }


def _directory_source(sources: Mapping[str, Path], value: object, label: str) -> Path:
    name = _string(value, label)
    path = sources.get(name)
    if path is None:
        raise AttemptContractError(f"{label} does not name a supplied configuration source")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise AttemptContractError(f"{label} source is unreadable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise AttemptContractError(f"{label} source is not a directory")
    return path.resolve(strict=True)


def _surface_evidence(value: object) -> tuple[SurfaceEvidence, ...]:
    result: list[SurfaceEvidence] = []
    for item in _array(value, "surface_evidence"):
        raw = _mapping(item, "surface evidence")
        if set(raw) != {
            "surface_id",
            "criticality",
            "oracle_adequate",
            "required_evidence_ids",
            "evidence_digests",
        }:
            raise AttemptContractError("surface evidence fields are incomplete or open")
        required = tuple(
            _string(item, "required evidence id")
            for item in _array(raw["required_evidence_ids"], "required evidence ids")
        )
        digests = _mapping(raw["evidence_digests"], "evidence digests")
        result.append(
            SurfaceEvidence(
                surface_id=_string(raw["surface_id"], "surface id"),
                criticality=_string(raw["criticality"], "surface criticality"),
                oracle_adequate=_boolean(raw["oracle_adequate"], "oracle_adequate"),
                required_evidence_ids=required,
                evidence_digests={
                    _string(key, "evidence digest key"): _string(item, "evidence digest")
                    for key, item in digests.items()
                },
            )
        )
    return tuple(result)


def _determinism_records(value: object) -> tuple[DeterminismRecord, ...]:
    result: list[DeterminismRecord] = []
    for item in _array(value, "determinism_records"):
        raw = _mapping(item, "determinism record")
        if set(raw) != {
            "surface_id",
            "criticality",
            "deterministic",
            "flake_count",
            "automatic_retry_count",
        }:
            raise AttemptContractError("determinism record fields are incomplete or open")
        result.append(
            DeterminismRecord(
                surface_id=_string(raw["surface_id"], "surface id"),
                criticality=_string(raw["criticality"], "criticality"),
                deterministic=_boolean(raw["deterministic"], "deterministic"),
                flake_count=_nonnegative(raw["flake_count"], "flake_count"),
                automatic_retry_count=_nonnegative(
                    raw["automatic_retry_count"], "automatic_retry_count"
                ),
            )
        )
    return tuple(result)
