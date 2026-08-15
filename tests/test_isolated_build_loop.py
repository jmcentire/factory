from __future__ import annotations

import json
import platform
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from factory_runtime.isolation import (
    IsolatedProcessResult,
    IsolationError,
    IsolationQualification,
    MacOSSandbox,
    _interpreter_read_paths,
)
from factory_runtime.lanes import IsolatedBuildLoop, LaneError, temporary_build_loop_root

FIXTURES = Path(__file__).parent / "fixtures" / "runtime_agents"


class _UnqualifiedBackend:
    def qualify(self, root: str | Path) -> IsolationQualification:
        return IsolationQualification(
            backend="unqualified-test-backend",
            read_denied=True,
            write_denied=False,
            network_denied=True,
        )

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path,
        readable_paths: Sequence[str | Path] = (),
        writable_paths: Sequence[str | Path] = (),
        environment: dict[str, str] | None = None,
    ) -> IsolatedProcessResult:
        raise AssertionError("an unqualified backend must never launch a lane")


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS Seatbelt integration")
@pytest.mark.isolation_integration
def test_coder_and_tester_are_isolated_and_validator_alone_runs_tests(
    tmp_path: Path,
) -> None:
    root = temporary_build_loop_root(tmp_path)
    result = IsolatedBuildLoop(root).execute(
        build_input_path=FIXTURES / "build-input.json",
        build_plan_path=FIXTURES / "build-plan.json",
        pattern_catalog_path=FIXTURES / "pattern-catalog.json",
        coder_command=(sys.executable, str(FIXTURES / "coder.py")),
        tester_command=(sys.executable, str(FIXTURES / "tester.py")),
        validator_command=(sys.executable, str(FIXTURES / "validator.py")),
        coder_trusted_paths=(FIXTURES / "coder.py",),
        tester_trusted_paths=(FIXTURES / "tester.py",),
        validator_trusted_paths=(FIXTURES / "validator.py",),
    )

    assert result.qualification.satisfied is True
    assert result.passed is True
    assert result.repair_signal == "pass"
    coder_evidence = json.loads(
        (result.coder.output_directory / "evidence" / "lane-evidence.json").read_text()
    )
    tester_evidence = json.loads(
        (result.tester.output_directory / "evidence" / "lane-evidence.json").read_text()
    )
    verdict = json.loads((result.validator.output_directory / "verdict.json").read_text())
    assert coder_evidence["cross_lane_read_denied"] is True
    assert tester_evidence["cross_lane_read_denied"] is True
    assert tester_evidence["construction_ir_absent"] is True
    assert verdict == {
        "build_input_digest": coder_evidence["build_input_digest"],
        "criteria": ["AC-1", "AC-2"],
        "passed": True,
    }


def test_interpreter_read_paths_cover_the_running_interpreter() -> None:
    granted = _interpreter_read_paths()

    assert granted, "an interpreter with no readable grant cannot start inside the sandbox"
    for required in (Path(sys.prefix), Path(sys.base_prefix)):
        resolved = required.resolve()
        assert any(resolved == path or resolved.is_relative_to(path) for path in granted), (
            f"{required} is not covered by {granted}"
        )
    assert all(path.parent != path for path in granted)


def test_interpreter_read_paths_cover_pyvenv_cfg_under_a_venv() -> None:
    if sys.prefix == sys.base_prefix:
        pytest.skip("not running under a virtualenv")

    marker = Path(sys.prefix) / "pyvenv.cfg"
    assert marker.is_file()
    assert any(marker.is_relative_to(path) for path in _interpreter_read_paths())


def test_macos_backend_fails_closed_off_macos() -> None:
    if platform.system() == "Darwin":
        pytest.skip("negative platform test applies only off macOS")

    with pytest.raises(IsolationError, match="unavailable"):
        MacOSSandbox()


def test_build_loop_refuses_an_unqualified_custom_backend(tmp_path: Path) -> None:
    root = temporary_build_loop_root(tmp_path)

    with pytest.raises(LaneError, match="did not prove"):
        IsolatedBuildLoop(root, sandbox=_UnqualifiedBackend()).execute(
            build_input_path=FIXTURES / "build-input.json",
            coder_command=(sys.executable, str(FIXTURES / "coder.py")),
            tester_command=(sys.executable, str(FIXTURES / "tester.py")),
            validator_command=(sys.executable, str(FIXTURES / "validator.py")),
        )
