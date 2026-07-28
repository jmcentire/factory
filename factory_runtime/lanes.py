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
        spec_path: str | Path,
        coder_command: Sequence[str],
        tester_command: Sequence[str],
        validator_command: Sequence[str],
        coder_trusted_paths: Sequence[str | Path] = (),
        tester_trusted_paths: Sequence[str | Path] = (),
        validator_trusted_paths: Sequence[str | Path] = (),
        before_validation: Callable[[LaneExecution, LaneExecution], None] | None = None,
    ) -> ValidationExecution:
        """Execute one clean-context attempt; lane internals never cross the boundary."""

        if self.root.exists():
            raise LaneError(f"refusing to reuse build-loop directory: {self.root}")
        self.root.mkdir(parents=True)
        qualification = self.sandbox.qualify(self.root / "qualification")
        source_spec = Path(spec_path)
        spec_bytes = source_spec.read_bytes()
        expected_spec_digest = digest_bytes(spec_bytes)

        coder = self._prepare_lane(LaneRole.CODER, spec_bytes)
        tester = self._prepare_lane(LaneRole.TESTER, spec_bytes)
        coder_runners = tuple(Path(path).resolve() for path in coder_trusted_paths)
        tester_runners = tuple(Path(path).resolve() for path in tester_trusted_paths)
        validator_runners = tuple(Path(path).resolve() for path in validator_trusted_paths)
        with ThreadPoolExecutor(max_workers=2) as executor:
            coder_future = executor.submit(
                self._run_author,
                coder,
                coder_command,
                coder_runners,
                expected_spec_digest,
            )
            tester_future = executor.submit(
                self._run_author,
                tester,
                tester_command,
                tester_runners,
                expected_spec_digest,
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

        if before_validation is not None:
            before_validation(coder_result, tester_result)
        validator_result = self._run_validator(
            coder_result,
            tester_result,
            validator_command,
            validator_runners,
            spec_bytes,
            expected_spec_digest,
        )
        return ValidationExecution(
            coder=coder_result,
            tester=tester_result,
            validator=validator_result,
            qualification=qualification,
        )

    def _prepare_lane(self, role: LaneRole, spec_bytes: bytes) -> Path:
        lane = self.root / role
        (lane / "input").mkdir(parents=True)
        (lane / "output").mkdir()
        (lane / "work").mkdir()
        (lane / "private").mkdir()
        (lane / "input" / "spec.json").write_bytes(spec_bytes)
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
        expected_spec_digest: str,
    ) -> LaneExecution:
        role = LaneRole(lane.name)
        spec = lane / "input" / "spec.json"
        output = lane / "output"
        process = self.sandbox.run(
            command,
            cwd=lane / "work",
            readable_paths=(spec, *runners),
            writable_paths=(lane / "work", output),
            environment={
                "FACTORY_ROLE": role,
                "FACTORY_SPEC_PATH": str(spec),
                "FACTORY_SPEC_DIGEST": expected_spec_digest,
                "FACTORY_OUTPUT_DIR": str(output),
            },
        )
        return LaneExecution(role=role, process=process, output_directory=output)

    def _run_validator(
        self,
        coder: LaneExecution,
        tester: LaneExecution,
        command: Sequence[str],
        runners: Sequence[Path],
        spec_bytes: bytes,
        expected_spec_digest: str,
    ) -> LaneExecution:
        lane = self.root / LaneRole.VALIDATOR
        input_directory = lane / "input"
        output = lane / "output"
        work = lane / "work"
        input_directory.mkdir(parents=True)
        output.mkdir()
        work.mkdir()
        spec = input_directory / "spec.json"
        spec.write_bytes(spec_bytes)
        implementation = input_directory / "implementation"
        tests = input_directory / "tests"
        _copy_regular_tree(coder.output_directory, implementation)
        _copy_regular_tree(tester.output_directory, tests)
        process = self.sandbox.run(
            command,
            cwd=work,
            readable_paths=(input_directory, *runners),
            writable_paths=(work, output),
            environment={
                "FACTORY_ROLE": LaneRole.VALIDATOR,
                "FACTORY_SPEC_PATH": str(spec),
                "FACTORY_SPEC_DIGEST": expected_spec_digest,
                "FACTORY_IMPLEMENTATION_DIR": str(implementation),
                "FACTORY_TEST_DIR": str(tests),
                "FACTORY_OUTPUT_DIR": str(output),
            },
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
