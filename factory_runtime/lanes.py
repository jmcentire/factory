"""Structurally separated Coder and Tester execution with Validator-owned test running."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from factory_core.manifest import digest_bytes
from factory_runtime.isolation import (
    IsolatedProcessResult,
    IsolationQualification,
    MacOSSandbox,
)
from factory_runtime.snapshot import FrozenTree, freeze_tree, verify_frozen_tree


class LaneError(RuntimeError):
    """The separated build loop could not produce trustworthy lane outputs."""


class LaneRole(StrEnum):
    CODER = "coder"
    TESTER = "tester"
    VALIDATOR = "validator"


class IsolationBackend(Protocol):
    def qualify(self, root: str | Path) -> IsolationQualification: ...

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path,
        readable_paths: Sequence[str | Path] = (),
        writable_paths: Sequence[str | Path] = (),
        environment: dict[str, str] | None = None,
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
        build_plan_path: str | Path | None = None,
        pattern_catalog_path: str | Path | None = None,
        review_snapshot_store: str | Path | None = None,
        before_validation: Callable[[LaneExecution, LaneExecution, FrozenTree, FrozenTree], None]
        | None = None,
    ) -> ValidationExecution:
        """Execute one clean-context attempt; lane internals never cross the boundary."""

        if self.root.exists():
            raise LaneError(f"refusing to reuse build-loop directory: {self.root}")
        self.root.mkdir(parents=True)
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

        coder = self._prepare_lane(
            LaneRole.CODER,
            input_bytes,
            plan_bytes=plan_bytes,
            catalog_bytes=catalog_bytes,
        )
        tester = self._prepare_lane(LaneRole.TESTER, input_bytes)
        coder_runners = tuple(Path(path).resolve() for path in coder_trusted_paths)
        tester_runners = tuple(Path(path).resolve() for path in tester_trusted_paths)
        validator_runners = tuple(Path(path).resolve() for path in validator_trusted_paths)
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

        snapshot_store = Path(review_snapshot_store or (self.root / "review-snapshots"))
        coder_snapshot = freeze_tree(coder_result.output_directory, snapshot_store)
        tester_snapshot = freeze_tree(tester_result.output_directory, snapshot_store)
        if before_validation is not None:
            before_validation(
                coder_result,
                tester_result,
                coder_snapshot,
                tester_snapshot,
            )
        validator_result = self._run_validator(
            coder_snapshot,
            tester_snapshot,
            validator_command,
            validator_runners,
            input_bytes,
            expected_input_digest,
            plan_bytes,
            catalog_bytes,
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
        process = self.sandbox.run(
            command,
            cwd=lane / "work",
            readable_paths=readable_paths,
            writable_paths=(lane / "work", output),
            environment=environment,
        )
        return LaneExecution(role=role, process=process, output_directory=output)

    def _run_validator(
        self,
        coder: FrozenTree,
        tester: FrozenTree,
        command: Sequence[str],
        runners: Sequence[Path],
        input_bytes: bytes,
        expected_input_digest: str,
        plan_bytes: bytes | None,
        catalog_bytes: bytes | None,
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
            }
        )
        process = self.sandbox.run(
            command,
            cwd=work,
            readable_paths=(*readable_inputs, implementation, tests, *runners),
            writable_paths=(work, output),
            environment=environment,
        )
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
