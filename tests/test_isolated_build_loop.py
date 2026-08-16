from __future__ import annotations

import json
import platform
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from factory_core.manifest import digest_obj
from factory_runtime.acceptance_obligations import validator_execution_digests
from factory_runtime.isolation import (
    IsolatedProcessResult,
    IsolationError,
    IsolationQualification,
    MacOSSandbox,
    _interpreter_read_paths,
)
from factory_runtime.lanes import IsolatedBuildLoop, LaneError, temporary_build_loop_root

FIXTURES = Path(__file__).parent / "fixtures" / "runtime_agents"


def _acceptance_catalog(tmp_path: Path, validator_command: tuple[str, ...]) -> Path:
    build_input = json.loads((FIXTURES / "build-input.json").read_text())
    phases = {
        artifact["phase"]: digest_obj(artifact) for artifact in build_input["phase_artifacts"]
    }
    command_digest, configuration_digest, environment_digest = validator_execution_digests(
        validator_command
    )
    examples = (("AC-1", 2, 3, 5), ("AC-2", -7, 4, -3))
    document = {
        "schema_version": "factory-acceptance-obligation-catalog/1",
        "catalog_id": "fixture-acceptance",
        "version": "1",
        "run_id": "fixture-run",
        "generation": 1,
        "target_state_digest": "sha256:" + ("8" * 64),
        "phase_artifact_digests": phases,
        "human_ratifier": "human:founder",
        "validator_ratifier": "agent:validator",
        "max_review_rounds": 2,
        "triggers": [
            {
                "trigger_id": "validating-to-preview",
                "from_state": "validating",
                "to_state": "preview",
                "command_digest": command_digest,
                "configuration_digest": configuration_digest,
                "environment_digest": environment_digest,
                "obligations": [
                    {
                        "obligation_id": "addition-examples",
                        "criterion": "The exact addition examples pass.",
                        "verifier_id": "validator-test-execution-v1",
                        "intent_backreferences": [
                            {
                                "artifact_id": "synthetic-product-specification",
                                "artifact_digest": phases["product-specification"],
                                "item_id": "product:addition",
                                "intent_digest": "sha256:" + ("7" * 64),
                            }
                        ],
                        "required_evidence_ids": [
                            "candidate",
                            "acceptance-tests",
                            "coder-output-snapshot",
                            "tester-output-snapshot",
                        ],
                        "test_assertions": [
                            {
                                "test_id": test_id,
                                "assertion_digest": digest_obj(
                                    {
                                        "test_id": test_id,
                                        "left": left,
                                        "right": right,
                                        "expected": expected,
                                    }
                                ),
                            }
                            for test_id, left, right, expected in examples
                        ],
                    }
                ],
            }
        ],
    }
    path = tmp_path / "acceptance-obligation-catalog.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


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
    validator_command = (sys.executable, str(FIXTURES / "validator.py"))
    result = IsolatedBuildLoop(root).execute(
        build_input_path=FIXTURES / "build-input.json",
        build_plan_path=FIXTURES / "build-plan.json",
        pattern_catalog_path=FIXTURES / "pattern-catalog.json",
        coder_command=(sys.executable, str(FIXTURES / "coder.py")),
        tester_command=(sys.executable, str(FIXTURES / "tester.py")),
        validator_command=validator_command,
        acceptance_catalog_path=_acceptance_catalog(tmp_path, validator_command),
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
