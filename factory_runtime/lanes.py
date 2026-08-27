"""Structurally separated Coder and Tester execution with Validator-owned test running."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from factory_core.manifest import digest_bytes, digest_obj
from factory_runtime.acceptance_obligations import (
    AcceptanceObligationCatalog,
    AcceptanceObligationError,
    ValidatorExecutionCapture,
    capture_validator_execution,
)
from factory_runtime.adversarial_review import canonical_document_bytes
from factory_runtime.isolation import (
    IsolatedProcessResult,
    IsolationQualification,
    MacOSSandbox,
    NetworkPolicy,
)
from factory_runtime.snapshot import (
    FrozenBlob,
    FrozenTree,
    SnapshotError,
    freeze_blob,
    freeze_tree,
    tree_digest,
    verify_frozen_blob,
    verify_frozen_tree,
)


class LaneError(RuntimeError):
    """The separated build loop could not produce trustworthy lane outputs."""


class LaneRole(StrEnum):
    CODER = "coder"
    TESTER = "tester"
    VALIDATOR = "validator"


class IsolationBackend(Protocol):
    def qualify(
        self,
        root: str | Path,
        network_policy: NetworkPolicy = ...,
    ) -> IsolationQualification: ...

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path,
        readable_paths: Sequence[str | Path] = (),
        writable_paths: Sequence[str | Path] = (),
        environment: dict[str, str] | None = None,
        stdin_bytes: bytes | None = None,
        network_policy: NetworkPolicy = ...,
    ) -> IsolatedProcessResult: ...


@dataclass(frozen=True)
class LaneExecution:
    role: LaneRole
    process: IsolatedProcessResult
    output_directory: Path

    @property
    def succeeded(self) -> bool:
        return self.process.returncode == 0


@dataclass(frozen=True)
class ValidationExecution:
    coder: LaneExecution
    tester: LaneExecution
    validator: LaneExecution
    qualification: IsolationQualification
    coder_snapshot: FrozenTree | None = None
    tester_snapshot: FrozenTree | None = None

    @property
    def passed(self) -> bool:
        return self.coder.succeeded and self.tester.succeeded and self.validator.succeeded

    @property
    def repair_signal(self) -> str:
        """The only verdict safe to return to an automated Coder retry."""

        return "pass" if self.passed else "fail"


@dataclass(frozen=True)
class FrozenValidatorExecution:
    """Attempt-local Validator identity plus pathname-independent source bytes.

    The already-running Factory interpreter is the host runtime TCB.  Target-controlled
    Validator source is never executed from a filesystem pathname: exact captured bytes are
    supplied on standard input to a fresh isolated interpreter process.
    """

    capture: ValidatorExecutionCapture
    tree: FrozenTree
    manifest: FrozenBlob
    command: tuple[str, ...]
    readable_paths: tuple[Path, ...]
    source: bytes


def _canonical_json(document: Mapping[str, object]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def freeze_validator_execution(
    root: str | Path,
    command: Sequence[str],
    trusted_paths: Sequence[str | Path] = (),
) -> FrozenValidatorExecution:
    """Capture once, freeze under the attempt, and rewrite argv to only those frozen bytes."""

    destination = Path(root)
    if destination.exists():
        raise LaneError(f"refusing to reuse Validator execution snapshot root: {destination}")
    destination.mkdir(parents=True)
    staging = Path(tempfile.mkdtemp(prefix=".validator-execution-", dir=destination))
    try:
        capture = capture_validator_execution(command, trusted_paths=trusted_paths)
        for item in capture.files:
            target = staging / item.snapshot_path
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                target,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                item.mode,
            )
            try:
                view = memoryview(item.content)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise LaneError("short write while freezing Validator execution input")
                    view = view[written:]
                os.fsync(descriptor)
                os.fchmod(descriptor, item.mode)
            finally:
                os.close(descriptor)
        tree = freeze_tree(
            staging,
            destination / "trees",
            durable_through=destination.parent,
        )
        expected_tree_digest = str(capture.document["snapshot_tree_digest"])
        if tree.digest != expected_tree_digest:
            raise LaneError("Validator execution snapshot differs from its captured identity")
        manifest_bytes = _canonical_json(dict(capture.document))
        manifest = freeze_blob(
            destination / "manifests",
            durable_through=destination.parent,
            label="validator-execution-manifest",
            data=manifest_bytes,
        )
        if manifest.digest != capture.command_digest:
            raise LaneError("Validator command address differs from its frozen manifest")
    except (AcceptanceObligationError, SnapshotError, OSError) as exc:
        raise LaneError(f"Validator execution could not be frozen: {exc}") from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    inputs = {
        str(item["input_id"]): item for item in capture.document["inputs"]
    }
    original_command = [str(part) for part in capture.document["argv"]]
    bindings_by_index = {
        int(binding["argv_index"]): binding for binding in capture.document["path_bindings"]
    }
    if set(bindings_by_index) != {0, 1}:
        raise LaneError(
            "Validator execution requires exactly one admitted Python source path"
        )
    executable_input = inputs[str(bindings_by_index[0]["input_id"])]
    source_input = inputs[str(bindings_by_index[1]["input_id"])]
    if (
        Path(str(executable_input["resolved_path"])) != Path(sys.executable).resolve()
        or str(bindings_by_index[0]["relative_path"]) != "payload"
        or source_input["kind"] != "file"
        or str(bindings_by_index[1]["relative_path"]) != "payload"
        or "trusted-runner-input" not in source_input["roles"]
    ):
        raise LaneError(
            "Validator execution must use the current Factory Python runtime and one admitted "
            "source file"
        )
    source_path = (
        tree.files_directory / str(source_input["snapshot_path"]) / "payload"
    )
    try:
        source_bytes = source_path.read_bytes()
        compile(source_bytes, f"<factory-validator:{capture.command_digest}>", "exec")
    except (OSError, SyntaxError, ValueError) as exc:
        raise LaneError(f"Validator source cannot be executed from captured bytes: {exc}") from exc
    frozen_command = [str(Path(sys.executable).resolve()), "-", *original_command[2:]]
    readable_paths = tuple(
        (
            tree.files_directory / str(item["snapshot_path"]) / "payload"
            if item["kind"] == "file"
            else tree.files_directory / str(item["snapshot_path"])
        )
        for item in capture.document["inputs"]
    )
    frozen = FrozenValidatorExecution(
        capture,
        tree,
        manifest,
        tuple(frozen_command),
        readable_paths,
        source_bytes,
    )
    _verify_frozen_validator_execution(frozen)
    return frozen


def _verify_frozen_validator_execution(
    frozen: FrozenValidatorExecution,
) -> FrozenValidatorExecution:
    try:
        tree = verify_frozen_tree(frozen.tree.directory, expected_digest=frozen.tree.digest)
        manifest = verify_frozen_blob(
            frozen.manifest.directory,
            expected_digest=frozen.manifest.digest,
            label="validator-execution-manifest",
        )
        manifest_bytes = manifest.payload_path.read_bytes()
        if manifest_bytes != _canonical_json(dict(frozen.capture.document)):
            raise LaneError("Validator execution manifest differs from its captured identity")
        if tree.digest != frozen.capture.document["snapshot_tree_digest"]:
            raise LaneError("Validator execution tree differs from its captured identity")
        inputs = {
            str(item["input_id"]): item for item in frozen.capture.document["inputs"]
        }
        source_binding = next(
            (
                binding
                for binding in frozen.capture.document["path_bindings"]
                if int(binding["argv_index"]) == 1
            ),
            None,
        )
        if source_binding is None:
            raise LaneError("Validator execution identity has no source binding")
        source_input = inputs[str(source_binding["input_id"])]
        source_path = tree.files_directory / str(source_input["snapshot_path"]) / "payload"
        if source_path.read_bytes() != frozen.source:
            raise LaneError("Validator source differs from its captured bytes")
    except (SnapshotError, OSError) as exc:
        raise LaneError(f"Validator execution snapshot is invalid: {exc}") from exc
    return frozen


def _copy_regular_tree(source: Path, destination: Path) -> None:
    """Copy lane output without following a symlink out of the sandbox."""

    destination.mkdir(parents=True, exist_ok=False)
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_symlink():
            raise LaneError(f"lane output contains a forbidden symlink: {relative}")
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not path.is_file():
            raise LaneError(f"lane output is not a regular file: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        target.chmod(path.stat().st_mode & 0o777)


class IsolatedBuildLoop:
    """Run Coder and Tester independently, then hand both outputs to the Validator."""

    def __init__(
        self,
        root: str | Path,
        *,
        sandbox: IsolationBackend | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.sandbox = sandbox or MacOSSandbox()

    def execute(
        self,
        *,
        build_input_path: str | Path,
        coder_command: Sequence[str],
        tester_command: Sequence[str],
        validator_command: Sequence[str],
        coder_trusted_paths: Sequence[str | Path] = (),
        tester_trusted_paths: Sequence[str | Path] = (),
        validator_trusted_paths: Sequence[str | Path] = (),
        prebuilt_author_outputs: Mapping[LaneRole, str | Path] | None = None,
        build_plan_path: str | Path | None = None,
        pattern_catalog_path: str | Path | None = None,
        acceptance_catalog_path: str | Path | None = None,
        review_snapshot_store: str | Path | None = None,
        review_snapshot_durable_through: str | Path | None = None,
        repair_brief_bytes: bytes | None = None,
        before_validation: Callable[
            [LaneExecution, LaneExecution, FrozenTree, FrozenTree], Mapping[str, object]
        ]
        | None = None,
    ) -> ValidationExecution:
        """Execute one clean-context attempt; lane internals never cross the boundary."""

        if review_snapshot_store is None:
            snapshot_store = self.root / "review-snapshots"
            snapshot_boundary = Path(review_snapshot_durable_through or self.root)
        else:
            snapshot_store = Path(review_snapshot_store)
            if review_snapshot_durable_through is None:
                raise LaneError(
                    "an external review snapshot store requires an explicit durability root"
                )
            snapshot_boundary = Path(review_snapshot_durable_through)
        if self.root.exists():
            raise LaneError(f"refusing to reuse build-loop directory: {self.root}")
        try:
            self.root.mkdir(parents=True)
        except FileExistsError as exc:
            raise LaneError(f"refusing raced build-loop directory: {self.root}") from exc
        qualification = self.sandbox.qualify(self.root / "qualification")
        if not qualification.satisfied:
            raise LaneError("isolation backend did not prove read, write, and network denial")
        source_input = Path(build_input_path)
        input_bytes = source_input.read_bytes()
        expected_input_digest = digest_bytes(input_bytes)
        if (build_plan_path is None) != (pattern_catalog_path is None):
            raise LaneError("build plan and pattern catalog must be supplied together")
        plan_bytes = Path(build_plan_path).read_bytes() if build_plan_path is not None else None
        catalog_bytes = (
            Path(pattern_catalog_path).read_bytes() if pattern_catalog_path is not None else None
        )
        acceptance_catalog_bytes = (
            Path(acceptance_catalog_path).read_bytes()
            if acceptance_catalog_path is not None
            else None
        )
        if acceptance_catalog_bytes is None:
            raise LaneError("Validator execution requires a ratified acceptance catalog")
        try:
            acceptance_document = json.loads(acceptance_catalog_bytes)
            if not isinstance(acceptance_document, Mapping):
                raise LaneError("acceptance-obligation catalog must be an object")
            acceptance_catalog = AcceptanceObligationCatalog.from_dict(acceptance_document)
            trigger = acceptance_catalog.select("validating", "preview")
            frozen_validator = freeze_validator_execution(
                self.root / "validator-execution",
                validator_command,
                validator_trusted_paths,
            )
        except (json.JSONDecodeError, AcceptanceObligationError) as exc:
            raise LaneError(f"Validator execution contract is invalid: {exc}") from exc
        actual_execution = dict(
            zip(
                ("command_digest", "configuration_digest", "environment_digest"),
                frozen_validator.capture.digests,
                strict=True,
            )
        )
        for field, actual in actual_execution.items():
            if trigger[field] != actual:
                raise LaneError(
                    f"ratified acceptance catalog does not authorize frozen Validator {field}"
                )

        coder = self._prepare_lane(
            LaneRole.CODER,
            input_bytes,
            plan_bytes=plan_bytes,
            catalog_bytes=catalog_bytes,
            repair_brief_bytes=repair_brief_bytes,
        )
        tester = self._prepare_lane(
            LaneRole.TESTER,
            input_bytes,
            acceptance_catalog_bytes=acceptance_catalog_bytes,
        )
        if prebuilt_author_outputs is not None:
            if set(prebuilt_author_outputs) != {LaneRole.CODER, LaneRole.TESTER}:
                raise LaneError(
                    "prebuilt author outputs must contain exactly Coder and Tester artifacts"
                )
            if coder_command or tester_command or coder_trusted_paths or tester_trusted_paths:
                raise LaneError(
                    "prebuilt author outputs may not also admit direct Coder or Tester commands"
                )
            coder_result = self._stage_prebuilt_author_output(
                coder, LaneRole.CODER, Path(prebuilt_author_outputs[LaneRole.CODER])
            )
            tester_result = self._stage_prebuilt_author_output(
                tester, LaneRole.TESTER, Path(prebuilt_author_outputs[LaneRole.TESTER])
            )
        else:
            coder_runners = tuple(Path(path).resolve() for path in coder_trusted_paths)
            tester_runners = tuple(Path(path).resolve() for path in tester_trusted_paths)
            with ThreadPoolExecutor(max_workers=2) as executor:
                coder_future = executor.submit(
                    self._run_author,
                    coder,
                    coder_command,
                    coder_runners,
                    expected_input_digest,
                )
                tester_future = executor.submit(
                    self._run_author,
                    tester,
                    tester_command,
                    tester_runners,
                    expected_input_digest,
                )
                coder_result = coder_future.result()
                tester_result = tester_future.result()
        if not coder_result.succeeded or not tester_result.succeeded:
            validator = LaneExecution(
                role=LaneRole.VALIDATOR,
                process=IsolatedProcessResult(
                    command=tuple(str(part) for part in validator_command),
                    returncode=1,
                    stdout="",
                    stderr="author lane failed; validation was not run",
                ),
                output_directory=self.root / "validator" / "output",
            )
            return ValidationExecution(coder_result, tester_result, validator, qualification)

        coder_snapshot = freeze_tree(
            coder_result.output_directory,
            snapshot_store,
            durable_through=snapshot_boundary,
        )
        tester_snapshot = freeze_tree(
            tester_result.output_directory,
            snapshot_store,
            durable_through=snapshot_boundary,
        )
        review_subject: Mapping[str, object] | None = None
        if before_validation is not None:
            review_subject = before_validation(
                coder_result,
                tester_result,
                coder_snapshot,
                tester_snapshot,
            )
        if review_subject is None:
            raise LaneError("Validator execution requires an immutable adversarial-review subject")
        execution_subject = review_subject.get("validator_execution")
        if execution_subject != actual_execution:
            raise LaneError(
                "Validator adversarial-review subject does not bind the frozen execution identity"
            )
        validator_result = self._run_validator(
            coder_snapshot,
            tester_snapshot,
            frozen_validator,
            input_bytes,
            expected_input_digest,
            plan_bytes,
            catalog_bytes,
            acceptance_catalog_bytes,
            canonical_document_bytes(review_subject),
        )
        return ValidationExecution(
            coder=coder_result,
            tester=tester_result,
            validator=validator_result,
            qualification=qualification,
            coder_snapshot=coder_snapshot,
            tester_snapshot=tester_snapshot,
        )

    def _prepare_lane(
        self,
        role: LaneRole,
        input_bytes: bytes,
        *,
        plan_bytes: bytes | None = None,
        catalog_bytes: bytes | None = None,
        acceptance_catalog_bytes: bytes | None = None,
        repair_brief_bytes: bytes | None = None,
    ) -> Path:
        lane = self.root / role
        (lane / "input").mkdir(parents=True)
        (lane / "output").mkdir()
        (lane / "work").mkdir()
        (lane / "private").mkdir()
        (lane / "input" / "build-input.json").write_bytes(input_bytes)
        if role is LaneRole.CODER and plan_bytes is not None and catalog_bytes is not None:
            (lane / "input" / "build-plan.json").write_bytes(plan_bytes)
            (lane / "input" / "pattern-catalog.json").write_bytes(catalog_bytes)
            if repair_brief_bytes is not None:
                (lane / "input" / "repair-brief.tessera.json").write_bytes(
                    repair_brief_bytes
                )
        if role is LaneRole.TESTER and acceptance_catalog_bytes is not None:
            (lane / "input" / "acceptance-obligation-catalog.json").write_bytes(
                acceptance_catalog_bytes
            )
        (lane / "private" / "sentinel.txt").write_text(
            f"{role}-private",
            encoding="utf-8",
        )
        return lane

    def _run_author(
        self,
        lane: Path,
        command: Sequence[str],
        runners: Sequence[Path],
        expected_input_digest: str,
    ) -> LaneExecution:
        role = LaneRole(lane.name)
        build_input = lane / "input" / "build-input.json"
        output = lane / "output"
        environment = {
            "FACTORY_ROLE": role,
            "FACTORY_BUILD_INPUT_PATH": str(build_input),
            "FACTORY_BUILD_INPUT_DIGEST": expected_input_digest,
            "FACTORY_OUTPUT_DIR": str(output),
        }
        readable_paths: tuple[Path, ...] = (build_input, *runners)
        if role is LaneRole.CODER:
            plan = lane / "input" / "build-plan.json"
            catalog = lane / "input" / "pattern-catalog.json"
            if plan.is_file() and catalog.is_file():
                readable_paths = (build_input, plan, catalog, *runners)
                environment.update(
                    {
                        "FACTORY_BUILD_PLAN_PATH": str(plan),
                        "FACTORY_BUILD_PLAN_SOURCE_DIGEST": digest_bytes(plan.read_bytes()),
                        "FACTORY_PATTERN_CATALOG_PATH": str(catalog),
                        "FACTORY_PATTERN_CATALOG_SOURCE_DIGEST": digest_bytes(catalog.read_bytes()),
                    }
                )
                repair_brief = lane / "input" / "repair-brief.tessera.json"
                if repair_brief.is_file():
                    readable_paths = (*readable_paths, repair_brief)
                    environment.update(
                        {
                            "FACTORY_REPAIR_BRIEF_PATH": str(repair_brief),
                            "FACTORY_REPAIR_BRIEF_ENVELOPE_DIGEST": digest_bytes(
                                repair_brief.read_bytes()
                            ),
                        }
                    )
        elif role is LaneRole.TESTER:
            acceptance_catalog = lane / "input" / "acceptance-obligation-catalog.json"
            if acceptance_catalog.is_file():
                readable_paths = (build_input, acceptance_catalog, *runners)
                environment.update(
                    {
                        "FACTORY_ACCEPTANCE_OBLIGATION_CATALOG_PATH": str(acceptance_catalog),
                        "FACTORY_ACCEPTANCE_OBLIGATION_CATALOG_SOURCE_DIGEST": digest_bytes(
                            acceptance_catalog.read_bytes()
                        ),
                    }
                )
        process = self.sandbox.run(
            command,
            cwd=lane / "work",
            readable_paths=readable_paths,
            writable_paths=(lane / "work", output),
            environment=environment,
        )
        return LaneExecution(role=role, process=process, output_directory=output)

    @staticmethod
    def _stage_prebuilt_author_output(
        lane: Path, role: LaneRole, source: Path
    ) -> LaneExecution:
        """Import a sealed runner artifact as a regular-file lane output.

        A networked model runner is not an author lane: it may publish a result only through
        the broker, and this method is the one-way conversion from that retained result into
        the normal Validator pipeline.  No model command is executed here, and the source is
        copied before the Validator receives it so its path and subsequent mutations cannot
        influence validation.
        """

        if source.is_symlink() or not source.is_dir():
            raise LaneError(f"prebuilt {role} output is missing, symlinked, or not a directory")
        source = source.resolve()
        output = lane / "output"
        if any(output.iterdir()):
            raise LaneError(f"prebuilt {role} output destination is not empty")
        staging = lane / "sealed-output"
        _copy_regular_tree(source, staging)
        output.rmdir()
        staging.replace(output)
        return LaneExecution(
            role=role,
            process=IsolatedProcessResult(
                command=("factory:sealed-author-artifact", str(role)),
                returncode=0,
                stdout="",
                stderr="",
            ),
            output_directory=output,
        )

    def _run_validator(
        self,
        coder: FrozenTree,
        tester: FrozenTree,
        validator_execution: FrozenValidatorExecution,
        input_bytes: bytes,
        expected_input_digest: str,
        plan_bytes: bytes | None,
        catalog_bytes: bytes | None,
        acceptance_catalog_bytes: bytes | None,
        review_subject_bytes: bytes,
    ) -> LaneExecution:
        lane = self.root / LaneRole.VALIDATOR
        input_directory = lane / "input"
        output = lane / "output"
        work = lane / "work"
        input_directory.mkdir(parents=True)
        output.mkdir()
        work.mkdir()
        build_input = input_directory / "build-input.json"
        build_input.write_bytes(input_bytes)
        readable_inputs: list[Path] = [build_input]
        environment = {
            "FACTORY_ROLE": LaneRole.VALIDATOR,
            "FACTORY_BUILD_INPUT_PATH": str(build_input),
            "FACTORY_BUILD_INPUT_DIGEST": expected_input_digest,
            "FACTORY_OUTPUT_DIR": str(output),
        }
        if plan_bytes is not None and catalog_bytes is not None:
            plan = input_directory / "build-plan.json"
            catalog = input_directory / "pattern-catalog.json"
            plan.write_bytes(plan_bytes)
            catalog.write_bytes(catalog_bytes)
            readable_inputs.extend((plan, catalog))
            environment.update(
                {
                    "FACTORY_BUILD_PLAN_PATH": str(plan),
                    "FACTORY_BUILD_PLAN_SOURCE_DIGEST": digest_bytes(plan_bytes),
                    "FACTORY_PATTERN_CATALOG_PATH": str(catalog),
                    "FACTORY_PATTERN_CATALOG_SOURCE_DIGEST": digest_bytes(catalog_bytes),
                }
            )
        if acceptance_catalog_bytes is not None:
            acceptance_catalog = input_directory / "acceptance-obligation-catalog.json"
            acceptance_catalog.write_bytes(acceptance_catalog_bytes)
            readable_inputs.append(acceptance_catalog)
            try:
                acceptance_document = json.loads(acceptance_catalog_bytes)
            except json.JSONDecodeError as exc:
                raise LaneError(f"acceptance-obligation catalog is invalid JSON: {exc}") from exc
            if not isinstance(acceptance_document, dict):
                raise LaneError("acceptance-obligation catalog must be an object")
            environment.update(
                {
                    "FACTORY_ACCEPTANCE_OBLIGATION_CATALOG_PATH": str(acceptance_catalog),
                    "FACTORY_ACCEPTANCE_OBLIGATION_CATALOG_SOURCE_DIGEST": digest_bytes(
                        acceptance_catalog_bytes
                    ),
                    "FACTORY_ACCEPTANCE_OBLIGATION_CATALOG_DIGEST": digest_obj(acceptance_document),
                }
            )
        review_subject = input_directory / "validator-review-subject.json"
        review_subject.write_bytes(review_subject_bytes)
        readable_inputs.append(review_subject)
        environment.update(
            {
                "FACTORY_VALIDATOR_REVIEW_SUBJECT_PATH": str(review_subject),
                "FACTORY_VALIDATOR_REVIEW_SUBJECT_SOURCE_DIGEST": digest_bytes(
                    review_subject_bytes
                ),
            }
        )
        implementation = input_directory / "implementation"
        tests = input_directory / "tests"
        coder = verify_frozen_tree(coder.directory, expected_digest=coder.digest)
        tester = verify_frozen_tree(tester.directory, expected_digest=tester.digest)
        _copy_regular_tree(coder.files_directory, implementation)
        _copy_regular_tree(tester.files_directory, tests)
        environment.update(
            {
                "FACTORY_IMPLEMENTATION_DIR": str(implementation),
                "FACTORY_TEST_DIR": str(tests),
                "FACTORY_CANDIDATE_DIGEST": tree_digest(implementation / "artifact"),
                "FACTORY_ACCEPTANCE_TESTS_DIGEST": tree_digest(tests / "tests"),
                "FACTORY_CODER_OUTPUT_SNAPSHOT_DIGEST": coder.digest,
                "FACTORY_TESTER_OUTPUT_SNAPSHOT_DIGEST": tester.digest,
            }
        )
        validator_execution = _verify_frozen_validator_execution(validator_execution)
        command_digest, configuration_digest, environment_digest = (
            validator_execution.capture.digests
        )
        environment.update(
            {
                "FACTORY_VALIDATOR_COMMAND_DIGEST": command_digest,
                "FACTORY_VALIDATOR_CONFIGURATION_DIGEST": configuration_digest,
                "FACTORY_VALIDATOR_ENVIRONMENT_DIGEST": environment_digest,
                "FACTORY_VALIDATOR_EXECUTION_IDENTITY_DIGEST": (
                    validator_execution.capture.identity_digest
                ),
                "FACTORY_VALIDATOR_EXECUTION_MANIFEST_PATH": str(
                    validator_execution.manifest.payload_path
                ),
                "FACTORY_VALIDATOR_EXECUTION_SNAPSHOT_DIGEST": validator_execution.tree.digest,
            }
        )
        process = self.sandbox.run(
            validator_execution.command,
            cwd=work,
            readable_paths=(
                *readable_inputs,
                implementation,
                tests,
                validator_execution.manifest.payload_path,
                *validator_execution.readable_paths,
            ),
            writable_paths=(work, output),
            environment=environment,
            stdin_bytes=validator_execution.source,
        )
        _verify_frozen_validator_execution(validator_execution)
        return LaneExecution(
            role=LaneRole.VALIDATOR,
            process=process,
            output_directory=output,
        )


def temporary_build_loop_root(parent: str | Path) -> Path:
    """Reserve a unique not-yet-existing root suitable for :class:`IsolatedBuildLoop`."""

    path = Path(tempfile.mkdtemp(prefix="factory-build-loop-", dir=parent))
    path.rmdir()
    return path
