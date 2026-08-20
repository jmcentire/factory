from __future__ import annotations

import json
import platform
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

import factory_runtime.snapshot as snapshot_module
from factory_core.manifest import digest_bytes, digest_obj
from factory_runtime.acceptance_obligations import validator_execution_digests
from factory_runtime.adversarial_review import (
    build_review_authority_context,
    build_validator_review_subject,
)
from factory_runtime.candidate_diff import build_candidate_review_context
from factory_runtime.isolation import (
    IsolatedProcessResult,
    IsolationError,
    IsolationQualification,
    MacOSSandbox,
    _interpreter_read_paths,
)
from factory_runtime.lanes import (
    IsolatedBuildLoop,
    LaneError,
    freeze_validator_execution,
    temporary_build_loop_root,
)
from factory_runtime.snapshot import tree_digest

FIXTURES = Path(__file__).parent / "fixtures" / "runtime_agents"


def _git(arguments: Sequence[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=cwd, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _acceptance_catalog(
    tmp_path: Path,
    validator_command: tuple[str, ...],
    validator_trusted_paths: tuple[Path, ...],
) -> Path:
    build_input = json.loads((FIXTURES / "build-input.json").read_text())
    phases = {
        artifact["phase"]: digest_obj(artifact) for artifact in build_input["phase_artifacts"]
    }
    command_digest, configuration_digest, environment_digest = validator_execution_digests(
        validator_command,
        trusted_paths=validator_trusted_paths,
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
        stdin_bytes: bytes | None = None,
    ) -> IsolatedProcessResult:
        del stdin_bytes
        raise AssertionError("an unqualified backend must never launch a lane")


class _RecordingQualifiedBackend:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.validator_script_bytes = b""
        self.validator_environment: dict[str, str] = {}

    def qualify(self, root: str | Path) -> IsolationQualification:
        return IsolationQualification(
            backend="recording-qualified-test-backend",
            read_denied=True,
            write_denied=True,
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
        stdin_bytes: bytes | None = None,
    ) -> IsolatedProcessResult:
        del cwd, readable_paths, writable_paths
        values = dict(environment or {})
        role = values["FACTORY_ROLE"]
        output = Path(values["FACTORY_OUTPUT_DIR"])
        if role == "coder":
            artifact = output / "artifact"
            artifact.mkdir()
            (artifact / "candidate.py").write_text("value = 1\n", encoding="utf-8")
        elif role == "tester":
            tests = output / "tests"
            tests.mkdir()
            (tests / "test_candidate.py").write_text("assert True\n", encoding="utf-8")
        else:
            selected = tuple(str(part) for part in command)
            self.commands.append(selected)
            self.validator_script_bytes = stdin_bytes or b""
            self.validator_environment = values
        return IsolatedProcessResult(
            command=tuple(str(part) for part in command),
            returncode=0,
            stdout="",
            stderr="",
        )


class _MutatingValidatorSnapshotBackend(_RecordingQualifiedBackend):
    def run(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path,
        readable_paths: Sequence[str | Path] = (),
        writable_paths: Sequence[str | Path] = (),
        environment: dict[str, str] | None = None,
        stdin_bytes: bytes | None = None,
    ) -> IsolatedProcessResult:
        if dict(environment or {}).get("FACTORY_ROLE") == "validator":
            source = next(
                Path(path)
                for path in readable_paths
                if Path(path).as_posix().endswith("inputs/input-001/payload")
            )
            source.chmod(0o644)
            source.write_bytes(b"print('substituted validator')\n")
        return super().run(
            command,
            cwd=cwd,
            readable_paths=readable_paths,
            writable_paths=writable_paths,
            environment=environment,
            stdin_bytes=stdin_bytes,
        )


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS Seatbelt integration")
@pytest.mark.isolation_integration
def test_coder_and_tester_are_isolated_and_validator_alone_runs_tests(
    tmp_path: Path,
) -> None:
    root = temporary_build_loop_root(tmp_path)
    validator_command = (sys.executable, str(FIXTURES / "validator.py"))
    validator_trusted_paths = (FIXTURES / "validator.py",)
    acceptance_catalog_path = _acceptance_catalog(
        tmp_path,
        validator_command,
        validator_trusted_paths,
    )
    build_input = json.loads((FIXTURES / "build-input.json").read_text())
    acceptance_catalog = json.loads(acceptance_catalog_path.read_text())
    phases = {
        artifact["phase"]: digest_obj(artifact) for artifact in build_input["phase_artifacts"]
    }
    command_digest, configuration_digest, environment_digest = validator_execution_digests(
        validator_command,
        trusted_paths=validator_trusted_paths,
    )
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    _git(("init", "-q"), cwd=baseline)
    (baseline / "calculator.py").write_text(
        "def add(left, right):\n    return left - right\n", encoding="utf-8"
    )
    _git(("add", "."), cwd=baseline)
    _git(
        (
            "-c",
            "user.name=Factory Test",
            "-c",
            "user.email=factory@example.test",
            "commit",
            "-qm",
            "baseline",
        ),
        cwd=baseline,
    )
    resolved_commit = _git(("rev-parse", "HEAD"), cwd=baseline)
    resolved_tree = _git(("rev-parse", "HEAD^{tree}"), cwd=baseline)
    object_store = tmp_path / "objects.git"
    _git(("clone", "-q", "--bare", str(baseline), str(object_store)))
    verbatim_request = "Implement integer addition and verify positive and negative examples."
    execution_request = {
        "schema_version": "factory-execution-request/1",
        "request_id": "fixture-request",
        "run_id": "fixture-run",
        "repository_id": "fixture-repository",
        "generation": 1,
        "target_manifest_digest": build_input["target_digest"],
        "target_state_digest": acceptance_catalog["target_state_digest"],
        "resolved_commit": resolved_commit,
        "proposed_by": "human:founder",
        "verbatim_request": verbatim_request,
        "verbatim_request_digest": digest_bytes(verbatim_request.encode("utf-8")),
        "requested_outcome": "The calculator returns mathematical integer sums.",
        "surfaces": [
            {
                "surface_id": "calculator",
                "proposed_criticality": "critical",
                "reason": "The public calculator behavior is the requested outcome.",
            }
        ],
        "created_at": 1,
    }
    execution_request_bytes = (
        json.dumps(execution_request, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    checkpoint = {
        "run_id": "fixture-run",
        "checkpoint": "integration",
        "execution_request_digest": digest_obj(execution_request),
    }
    checkpoint_bytes = (
        json.dumps(checkpoint, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    authority_context = build_review_authority_context(
        resume_checkpoint_digest=digest_obj(checkpoint),
        resume_checkpoint_source_digest=digest_bytes(checkpoint_bytes),
        resume_checkpoint_bytes=checkpoint_bytes,
        configuration_sources={"acceptance-catalog": acceptance_catalog_path.read_bytes()},
        expected_configuration_digests={
            "acceptance-catalog": digest_bytes(acceptance_catalog_path.read_bytes())
        },
        changed_existing_tests=(),
        test_change_artifacts={},
        test_change_sources={},
    )

    def review_subject(_coder, _tester, coder_snapshot, tester_snapshot):
        candidate_digest = tree_digest(coder_snapshot.files_directory / "artifact")
        base_source_snapshot, candidate_change_set = build_candidate_review_context(
            target_state={
                "object_store": str(object_store),
                "resolved_commit": resolved_commit,
                "resolved_tree": resolved_tree,
                "subpath": "",
            },
            candidate_root=coder_snapshot.files_directory / "artifact",
            candidate_digest=candidate_digest,
            construction_mode="regenerate",
        )
        return build_validator_review_subject(
            run_id="fixture-run",
            generation=1,
            target_digest=build_input["target_digest"],
            target_state_digest=acceptance_catalog["target_state_digest"],
            resolved_commit=resolved_commit,
            resolved_tree=resolved_tree,
            reviewer_identity="agent:validator",
            base_source_snapshot=base_source_snapshot,
            candidate_change_set=candidate_change_set,
            authority_context=authority_context,
            execution_request_bytes=execution_request_bytes,
            build_input=build_input,
            build_input_digest=digest_obj(build_input),
            pattern_catalog_digest=digest_obj(
                json.loads((FIXTURES / "pattern-catalog.json").read_text())
            ),
            pattern_catalog_source_digest=digest_bytes(
                (FIXTURES / "pattern-catalog.json").read_bytes()
            ),
            build_plan_digest=digest_obj(json.loads((FIXTURES / "build-plan.json").read_text())),
            build_plan_source_digest=digest_bytes((FIXTURES / "build-plan.json").read_bytes()),
            phase_artifact_digests=phases,
            acceptance_obligation_catalog_digest=digest_obj(acceptance_catalog),
            acceptance_obligation_catalog_source_digest=digest_bytes(
                acceptance_catalog_path.read_bytes()
            ),
            candidate_digest=candidate_digest,
            acceptance_tests_digest=tree_digest(tester_snapshot.files_directory / "tests"),
            coder_output_snapshot_digest=coder_snapshot.digest,
            tester_output_snapshot_digest=tester_snapshot.digest,
            command_digest=command_digest,
            configuration_digest=configuration_digest,
            environment_digest=environment_digest,
        )

    result = IsolatedBuildLoop(root).execute(
        build_input_path=FIXTURES / "build-input.json",
        build_plan_path=FIXTURES / "build-plan.json",
        pattern_catalog_path=FIXTURES / "pattern-catalog.json",
        coder_command=(sys.executable, str(FIXTURES / "coder.py")),
        tester_command=(sys.executable, str(FIXTURES / "tester.py")),
        validator_command=validator_command,
        acceptance_catalog_path=acceptance_catalog_path,
        coder_trusted_paths=(FIXTURES / "coder.py",),
        tester_trusted_paths=(FIXTURES / "tester.py",),
        validator_trusted_paths=validator_trusted_paths,
        before_validation=review_subject,
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


def test_validator_launch_uses_frozen_bytes_after_live_path_mutation(tmp_path: Path) -> None:
    root = temporary_build_loop_root(tmp_path)
    validator = tmp_path / "validator.py"
    ratified_bytes = b"print('ratified validator')\n"
    validator.write_bytes(ratified_bytes)
    validator_command = (sys.executable, str(validator))
    validator_trusted_paths = (validator,)
    acceptance_catalog_path = _acceptance_catalog(
        tmp_path,
        validator_command,
        validator_trusted_paths,
    )
    execution_digests = validator_execution_digests(
        validator_command,
        trusted_paths=validator_trusted_paths,
    )
    expected_execution = dict(
        zip(
            ("command_digest", "configuration_digest", "environment_digest"),
            execution_digests,
            strict=True,
        )
    )
    backend = _RecordingQualifiedBackend()

    def review_subject(_coder, _tester, _coder_snapshot, _tester_snapshot):
        validator.write_bytes(b"print('substituted validator')\n")
        return {"validator_execution": expected_execution}

    result = IsolatedBuildLoop(root, sandbox=backend).execute(
        build_input_path=FIXTURES / "build-input.json",
        coder_command=(sys.executable, str(FIXTURES / "coder.py")),
        tester_command=(sys.executable, str(FIXTURES / "tester.py")),
        validator_command=validator_command,
        acceptance_catalog_path=acceptance_catalog_path,
        coder_trusted_paths=(FIXTURES / "coder.py",),
        tester_trusted_paths=(FIXTURES / "tester.py",),
        validator_trusted_paths=validator_trusted_paths,
        before_validation=review_subject,
    )

    assert result.passed is True
    assert len(backend.commands) == 1
    frozen_command = backend.commands[0]
    assert Path(frozen_command[0]).resolve() == Path(sys.executable).resolve()
    assert frozen_command[1] == "-"
    assert backend.validator_script_bytes == ratified_bytes
    assert validator.read_bytes() != ratified_bytes
    assert (
        backend.validator_environment["FACTORY_VALIDATOR_COMMAND_DIGEST"] == (execution_digests[0])
    )
    frozen_manifest = Path(
        backend.validator_environment["FACTORY_VALIDATOR_EXECUTION_MANIFEST_PATH"]
    )
    assert frozen_manifest.is_relative_to(root / "validator-execution")
    assert digest_bytes(frozen_manifest.read_bytes()) == execution_digests[0]


def test_review_callback_runs_only_after_both_snapshots_are_durably_published(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = temporary_build_loop_root(tmp_path)
    validator_command = (sys.executable, str(FIXTURES / "validator.py"))
    validator_trusted_paths = (FIXTURES / "validator.py",)
    acceptance_catalog_path = _acceptance_catalog(
        tmp_path,
        validator_command,
        validator_trusted_paths,
    )
    execution_digests = validator_execution_digests(
        validator_command,
        trusted_paths=validator_trusted_paths,
    )
    expected_execution = dict(
        zip(
            ("command_digest", "configuration_digest", "environment_digest"),
            execution_digests,
            strict=True,
        )
    )
    run_root = tmp_path / "run-1"
    run_root.mkdir()
    review_store = run_root / "evidence" / "review-snapshots"
    real_sync = snapshot_module.fsync_directory_chain
    synced: list[tuple[Path, Path]] = []

    def track_sync(start: str | Path, *, through: str | Path) -> None:
        synced.append((Path(start), Path(through)))
        real_sync(start, through=through)

    monkeypatch.setattr(snapshot_module, "fsync_directory_chain", track_sync)

    def review_subject(_coder, _tester, coder_snapshot, tester_snapshot):
        assert (coder_snapshot.directory, run_root) in synced
        assert (tester_snapshot.directory, run_root) in synced
        return {"validator_execution": expected_execution}

    result = IsolatedBuildLoop(root, sandbox=_RecordingQualifiedBackend()).execute(
        build_input_path=FIXTURES / "build-input.json",
        coder_command=(sys.executable, str(FIXTURES / "coder.py")),
        tester_command=(sys.executable, str(FIXTURES / "tester.py")),
        validator_command=validator_command,
        acceptance_catalog_path=acceptance_catalog_path,
        coder_trusted_paths=(FIXTURES / "coder.py",),
        tester_trusted_paths=(FIXTURES / "tester.py",),
        validator_trusted_paths=validator_trusted_paths,
        review_snapshot_store=review_store,
        review_snapshot_durable_through=run_root,
        before_validation=review_subject,
    )

    assert result.passed is True


def test_external_review_snapshot_store_requires_explicit_durability_root(
    tmp_path: Path,
) -> None:
    root = temporary_build_loop_root(tmp_path)

    with pytest.raises(LaneError, match="requires an explicit durability root"):
        IsolatedBuildLoop(root, sandbox=_RecordingQualifiedBackend()).execute(
            build_input_path=tmp_path / "not-read.json",
            coder_command=("not-run",),
            tester_command=("not-run",),
            validator_command=("not-run",),
            review_snapshot_store=tmp_path / "external-snapshots",
        )

    assert not root.exists()


def test_frozen_validator_launch_has_the_declared_standalone_stdin_abi(
    tmp_path: Path,
) -> None:
    validator = tmp_path / "validator.py"
    validator.write_text(
        "import json, sys\n"
        f"source_dir = {str(tmp_path)!r}\n"
        "print(json.dumps({\n"
        "    'argv': sys.argv,\n"
        "    'file': __file__,\n"
        "    'stdin_eof': sys.stdin.buffer.read() == b'',\n"
        "    'source_dir_on_path': source_dir in sys.path,\n"
        "}))\n",
        encoding="utf-8",
    )
    frozen = freeze_validator_execution(
        tmp_path / "validator-execution",
        (sys.executable, str(validator), "review"),
        (validator,),
    )

    completed = subprocess.run(
        frozen.command,
        input=frozen.source,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    observed = json.loads(completed.stdout)
    assert observed == {
        "argv": ["-", "review"],
        "file": "<stdin>",
        "stdin_eof": True,
        "source_dir_on_path": False,
    }


@pytest.mark.parametrize("variant", ("interpreter-flag", "additional-path-binding"))
def test_validator_launch_refuses_inputs_outside_the_standalone_source_abi(
    tmp_path: Path,
    variant: str,
) -> None:
    validator = tmp_path / "validator.py"
    validator.write_text("raise SystemExit(0)\n", encoding="utf-8")
    extra = tmp_path / "helper.py"
    extra.write_text("VALUE = 1\n", encoding="utf-8")
    trusted: tuple[Path, ...]
    if variant == "interpreter-flag":
        command = (sys.executable, "-I", str(validator))
        trusted = (validator,)
    else:
        command = (sys.executable, str(validator), str(extra))
        trusted = (validator, extra)

    with pytest.raises(
        LaneError,
        match="requires exactly one admitted Python source path",
    ):
        freeze_validator_execution(
            tmp_path / "validator-execution",
            command,
            trusted,
        )


def test_validator_launch_refuses_same_path_mutation_before_snapshot(tmp_path: Path) -> None:
    root = temporary_build_loop_root(tmp_path)
    validator = tmp_path / "validator.py"
    validator.write_text("print('ratified validator')\n", encoding="utf-8")
    validator_command = (sys.executable, str(validator))
    validator_trusted_paths = (validator,)
    acceptance_catalog_path = _acceptance_catalog(
        tmp_path,
        validator_command,
        validator_trusted_paths,
    )
    validator.write_text("print('substituted validator')\n", encoding="utf-8")
    backend = _RecordingQualifiedBackend()

    with pytest.raises(LaneError, match="does not authorize frozen Validator command_digest"):
        IsolatedBuildLoop(root, sandbox=backend).execute(
            build_input_path=FIXTURES / "build-input.json",
            coder_command=(sys.executable, str(FIXTURES / "coder.py")),
            tester_command=(sys.executable, str(FIXTURES / "tester.py")),
            validator_command=validator_command,
            acceptance_catalog_path=acceptance_catalog_path,
            validator_trusted_paths=validator_trusted_paths,
            before_validation=lambda *_: {},
        )

    assert backend.commands == []


def test_validator_launch_refuses_snapshot_mutation_after_prelaunch_verify(
    tmp_path: Path,
) -> None:
    root = temporary_build_loop_root(tmp_path)
    validator = tmp_path / "validator.py"
    ratified_bytes = b"print('ratified validator')\n"
    validator.write_bytes(ratified_bytes)
    validator_command = (sys.executable, str(validator))
    validator_trusted_paths = (validator,)
    acceptance_catalog_path = _acceptance_catalog(
        tmp_path,
        validator_command,
        validator_trusted_paths,
    )
    execution_digests = validator_execution_digests(
        validator_command,
        trusted_paths=validator_trusted_paths,
    )
    expected_execution = dict(
        zip(
            ("command_digest", "configuration_digest", "environment_digest"),
            execution_digests,
            strict=True,
        )
    )
    backend = _MutatingValidatorSnapshotBackend()

    with pytest.raises(LaneError, match="Validator execution snapshot is invalid"):
        IsolatedBuildLoop(root, sandbox=backend).execute(
            build_input_path=FIXTURES / "build-input.json",
            coder_command=(sys.executable, str(FIXTURES / "coder.py")),
            tester_command=(sys.executable, str(FIXTURES / "tester.py")),
            validator_command=validator_command,
            acceptance_catalog_path=acceptance_catalog_path,
            coder_trusted_paths=(FIXTURES / "coder.py",),
            tester_trusted_paths=(FIXTURES / "tester.py",),
            validator_trusted_paths=validator_trusted_paths,
            before_validation=lambda *_: {"validator_execution": expected_execution},
        )

    assert backend.validator_script_bytes == ratified_bytes


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

    marker = (Path(sys.prefix) / "pyvenv.cfg").resolve(strict=True)
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
