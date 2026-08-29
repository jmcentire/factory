from __future__ import annotations

import json
import platform
import subprocess
import sys
from collections.abc import Mapping, Sequence
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
    LaneRole,
    freeze_validator_execution,
    temporary_build_loop_root,
)
from factory_runtime.native_test import native_test_execution_digests
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
    def qualify(self, root: str | Path, network_policy=None) -> IsolationQualification:
        return IsolationQualification(
            backend="unqualified-test-backend",
            read_denied=True,
            write_denied=False,
            permitted_use_ok=True,
            denied_ok=True,
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
        network_policy=None,
        reap_process_group: bool = False,
    ) -> IsolatedProcessResult:
        del stdin_bytes
        raise AssertionError("an unqualified backend must never launch a lane")


class _RecordingQualifiedBackend:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.validator_script_bytes = b""
        self.validator_environment: dict[str, str] = {}

    def qualify(self, root: str | Path, network_policy=None) -> IsolationQualification:
        return IsolationQualification(
            backend="recording-qualified-test-backend",
            read_denied=True,
            write_denied=True,
            permitted_use_ok=True,
            denied_ok=True,
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
        network_policy=None,
        reap_process_group: bool = False,
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
        network_policy=None,
        reap_process_group: bool = False,
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


def test_prebuilt_author_artifacts_skip_direct_author_execution(tmp_path: Path) -> None:
    """A qualified networked runner may feed sealed outputs into local validation."""

    root = temporary_build_loop_root(tmp_path)
    validator_command = (sys.executable, str(FIXTURES / "validator.py"))
    validator_trusted_paths = (FIXTURES / "validator.py",)
    acceptance_catalog_path = _acceptance_catalog(
        tmp_path, validator_command, validator_trusted_paths
    )
    execution_digests = validator_execution_digests(
        validator_command, trusted_paths=validator_trusted_paths
    )
    expected_execution = dict(
        zip(
            ("command_digest", "configuration_digest", "environment_digest"),
            execution_digests,
            strict=True,
        )
    )
    coder = tmp_path / "sealed-coder"
    tester = tmp_path / "sealed-tester"
    (coder / "artifact").mkdir(parents=True)
    (tester / "tests").mkdir(parents=True)
    (coder / "artifact" / "candidate.py").write_text("value = 1\n", encoding="utf-8")
    (tester / "tests" / "test_candidate.py").write_text("assert True\n", encoding="utf-8")
    backend = _RecordingQualifiedBackend()

    result = IsolatedBuildLoop(root, sandbox=backend).execute(
        build_input_path=FIXTURES / "build-input.json",
        coder_command=(),
        tester_command=(),
        validator_command=validator_command,
        acceptance_catalog_path=acceptance_catalog_path,
        validator_trusted_paths=validator_trusted_paths,
        prebuilt_author_outputs={
            LaneRole.CODER: coder,
            LaneRole.TESTER: tester,
        },
        before_validation=lambda *_: {"validator_execution": expected_execution},
    )

    assert result.passed is True
    assert result.coder.process.command == ("factory:sealed-author-artifact", "coder")
    assert result.tester.process.command == ("factory:sealed-author-artifact", "tester")
    assert len(backend.commands) == 1
    assert (root / "coder" / "output" / "artifact" / "candidate.py").read_text() == "value = 1\n"


def test_prebuilt_author_artifacts_refuse_direct_commands(tmp_path: Path) -> None:
    root = temporary_build_loop_root(tmp_path)
    validator_command = (sys.executable, str(FIXTURES / "validator.py"))
    validator_trusted_paths = (FIXTURES / "validator.py",)
    acceptance_catalog_path = _acceptance_catalog(
        tmp_path, validator_command, validator_trusted_paths
    )

    with pytest.raises(LaneError, match="may not also admit direct Coder or Tester commands"):
        IsolatedBuildLoop(root, sandbox=_RecordingQualifiedBackend()).execute(
            build_input_path=FIXTURES / "build-input.json",
            coder_command=("must-not-run",),
            tester_command=(),
            validator_command=validator_command,
            acceptance_catalog_path=acceptance_catalog_path,
            validator_trusted_paths=validator_trusted_paths,
            prebuilt_author_outputs={
                LaneRole.CODER: tmp_path,
                LaneRole.TESTER: tmp_path,
            },
        )


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

    with pytest.raises(LaneError, match="does not authorize Validator command_digest"):
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


@pytest.mark.isolation_integration
def test_deny_all_and_declared_loopback_policies_qualify_exactly(tmp_path: Path) -> None:
    """Deny-all proves full network denial; a declared-loopback grant proves exact scoping."""

    from factory_runtime.isolation import DENY_ALL_NETWORK, LoopbackGrant, NetworkPolicy

    sandbox = MacOSSandbox()
    deny = sandbox.qualify(tmp_path / "deny", DENY_ALL_NETWORK)
    assert deny.satisfied and deny.policy_label == "deny-all"
    assert deny.read_denied and deny.write_denied
    assert deny.permitted_use_ok  # no grants -> vacuously true
    assert deny.denied_ok  # bind/connect, tcp/udp, external, unrelated all EPERM

    # An in-lane Validator+candidate grant: TCP signaling and a UDP block, each bind AND connect.
    signaling = (48200,)
    udp_block = tuple(range(48201, 48205))
    policy = NetworkPolicy.declared_loopback(
        [
            LoopbackGrant("tcp", "bind", signaling),
            LoopbackGrant("tcp", "connect", signaling),
            LoopbackGrant("udp", "bind", udp_block),
            LoopbackGrant("udp", "connect", udp_block),
        ]
    )
    q = sandbox.qualify(tmp_path / "declared", policy)
    assert q.satisfied and q.policy_label == "declared-loopback"
    assert q.permitted_use_ok  # every granted (protocol, operation) works
    assert q.denied_ok  # external tcp/udp, unrelated listener, out-of-grant all EPERM
    assert q.read_denied and q.write_denied


@pytest.mark.isolation_integration
def test_declared_loopback_grant_denies_ungranted_operation_and_external(tmp_path: Path) -> None:
    """A bind-only TCP grant may listen in-block but not connect out, bind out-of-block, or reach
    an external address or an unrelated live loopback listener."""

    from factory_runtime.isolation import LoopbackGrant, NetworkPolicy

    sandbox = MacOSSandbox()
    policy = NetworkPolicy.declared_loopback(
        [LoopbackGrant("tcp", "bind", tuple(range(48160, 48168)))]
    )
    q = sandbox.qualify(tmp_path / "bind-only", policy)
    # permitted_use_ok proves the in-block bind works; denied_ok proves connect on the block
    # (ungranted operation), any out-of-block port, UDP, external, and the unrelated live
    # listener are all EPERM refusals.
    assert q.satisfied
    assert q.permitted_use_ok and q.denied_ok
    assert q.read_denied and q.write_denied


@pytest.mark.isolation_integration
def test_loopback_range_profile_denies_a_second_live_listener_outside_range(
    tmp_path: Path,
) -> None:
    """Directly prove a grant-scoped lane cannot reach an unrelated live loopback port."""

    import json
    import socket

    from factory_runtime.isolation import LoopbackGrant, MacOSSandbox, NetworkPolicy

    sandbox = MacOSSandbox()
    policy = NetworkPolicy.declared_loopback(
        [LoopbackGrant("tcp", "connect", tuple(range(48140, 48144)))]
    )
    work = tmp_path / "work"
    work.mkdir()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    unrelated_port = listener.getsockname()[1]
    probe = (
        "import errno, json, socket\n"
        "s=socket.socket(); s.settimeout(3)\n"
        "import os\n"
        "try:\n"
        "    s.connect(('127.0.0.1', int(os.environ['P'])))\n"
        "    print(json.dumps({'denied': False}))\n"
        "except OSError as e:\n"
        "    print(json.dumps({'denied': e.errno in (errno.EPERM, errno.EACCES)}))\n"
    )
    try:
        result = sandbox.run(
            (sys.executable, "-c", probe),
            cwd=work,
            writable_paths=(work,),
            environment={"P": str(unrelated_port)},
            network_policy=policy,
        )
    finally:
        listener.close()
    assert json.loads(result.stdout)["denied"] is True


def test_reserve_loopback_endpoints_allocates_and_proves_no_leak(
    tmp_path: Path, monkeypatch
) -> None:
    """A per-attempt reservation hands out disjoint TCP/UDP ports and asserts no leak on exit."""

    import factory_runtime.loopback_endpoints as endpoints
    from factory_runtime.loopback_endpoints import EndpointSpec, reserve_loopback_endpoints

    monkeypatch.setattr(endpoints, "_REGISTRY_DIR", tmp_path / "reg")
    monkeypatch.setattr(endpoints, "_REGISTRY_FILE", tmp_path / "reg" / "active.json")

    specs = [
        EndpointSpec("tcp", ("bind", "connect"), 1),
        EndpointSpec("udp", ("bind", "connect"), 4),
    ]
    with reserve_loopback_endpoints(specs) as reservation:
        assert len(reservation.tcp_ports) == 1
        assert len(reservation.udp_ports) == 4
        assert not set(reservation.tcp_ports) & set(reservation.udp_ports)
        # No lane bound anything, so the block is clean at exit — no leak assertion fires.


def test_loopback_block_allocation_is_non_overlapping(tmp_path: Path, monkeypatch) -> None:
    """Concurrent attempts never receive an overlapping block."""

    import factory_runtime.loopback_endpoints as endpoints

    monkeypatch.setattr(endpoints, "_REGISTRY_DIR", tmp_path / "reg")
    monkeypatch.setattr(endpoints, "_REGISTRY_FILE", tmp_path / "reg" / "active.json")

    lo1, hi1, r1 = endpoints._allocate_block(8)
    lo2, hi2, r2 = endpoints._allocate_block(8)
    try:
        assert hi1 < lo2 or hi2 < lo1  # disjoint
    finally:
        r1.close()
        r2.close()
        endpoints._revoke(lo1, hi1)
        endpoints._revoke(lo2, hi2)


# --------------------------------------------------------------------------- #
# Generic native-test executor
# --------------------------------------------------------------------------- #

def _native_acceptance_catalog(
    tmp_path: Path,
    *,
    candidate_launch: Sequence[str],
    test_entrypoint: Sequence[str],
    readiness_entrypoint: Sequence[str] = (),
    readiness_timeout_seconds: float = 30.0,
    readiness_interval_seconds: float = 0.5,
    readiness_max_attempts: int = 120,
    test_assertions: Sequence[Mapping[str, str]] | None = None,
) -> Path:
    """An acceptance catalog whose trigger binds a two-profile native-test execution identity.

    ``test_assertions`` lets a test bind an arbitrary number of ratified criteria so the generic
    catalog-input path can be exercised at counts other than one.
    """

    build_input = json.loads((FIXTURES / "build-input.json").read_text())
    phases = {
        artifact["phase"]: digest_obj(artifact) for artifact in build_input["phase_artifacts"]
    }
    if test_assertions is None:
        test_assertions = [
            {"test_id": "native-suite", "assertion_digest": "sha256:" + ("7" * 64)}
        ]
    execution = native_test_execution_digests(
        candidate_launch,
        test_entrypoint,
        readiness_entrypoint=readiness_entrypoint,
        readiness_timeout_seconds=readiness_timeout_seconds,
        readiness_interval_seconds=readiness_interval_seconds,
        readiness_max_attempts=readiness_max_attempts,
    )
    document = {
        "schema_version": "factory-acceptance-obligation-catalog/1",
        "catalog_id": "fixture-native-acceptance",
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
                "command_digest": execution.command_digest,
                "configuration_digest": execution.configuration_digest,
                "environment_digest": execution.environment_digest,
                "obligations": [
                    {
                        "obligation_id": "native-suite",
                        "criterion": "The target's native acceptance suite passes.",
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
                        "test_assertions": list(test_assertions),
                    }
                ],
            }
        ],
    }
    path = tmp_path / "native-acceptance-obligation-catalog.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_native_test_execution_digests_bind_all_argv() -> None:
    """Command/config digests track every exact argv; the environment digest is argv-independent."""

    a = native_test_execution_digests(["python", "server.py"], ["make", "test"])
    b = native_test_execution_digests(["python", "server.py"], ["make", "test"])
    c = native_test_execution_digests(["python", "server.py"], ["make", "acceptance"])
    d = native_test_execution_digests(["python", "other.py"], ["make", "test"])
    e = native_test_execution_digests(
        ["python", "server.py"], ["make", "test"], readiness_entrypoint=["python", "ready.py"]
    )
    assert a.digests == b.digests  # deterministic
    assert a.command_digest != c.command_digest  # test argv-sensitive
    assert a.command_digest != d.command_digest  # candidate argv-sensitive
    assert a.command_digest != e.command_digest  # readiness argv-sensitive
    assert a.configuration_digest != e.configuration_digest
    assert a.environment_digest == c.environment_digest == e.environment_digest  # generic env
    # Both required argvs must be non-empty; readiness is optional but rejects empty parts.
    with pytest.raises(ValueError):
        native_test_execution_digests([], ["make", "test"])
    with pytest.raises(ValueError):
        native_test_execution_digests(["python", "server.py"], [])
    with pytest.raises(ValueError):
        native_test_execution_digests(["python", "server.py"], ["make", ""])
    with pytest.raises(ValueError):
        native_test_execution_digests(
            ["python", "server.py"], ["make", "test"], readiness_entrypoint=["ready", ""]
        )


# Two disjoint profiles. The candidate (Profile A) binds the declared loopback port and probes that
# it cannot read the sibling test root; the test (Profile B) connects over loopback and probes that
# it cannot read the sibling candidate root, and derives the ratified assertions from the catalog.
_CANDIDATE_SERVER = '''
import json, os, socket
from pathlib import Path
cand = Path(os.environ["FACTORY_CANDIDATE_DIR"])
out = Path(os.environ["FACTORY_OUTPUT_DIR"]); out.mkdir(parents=True, exist_ok=True)
sibling = cand.parent / "test"
try:
    list(sibling.iterdir()); test_read = "ALLOWED"
except OSError as exc:
    test_read = "denied:%s" % (exc.errno,)
try:
    (sibling / "acceptance-catalog.json").read_text(); catalog_read = "ALLOWED"
except OSError as exc:
    catalog_read = "denied:%s" % (exc.errno,)
(out / "candidate-probe.json").write_text(
    json.dumps({"test_root_read": test_read, "catalog_read": catalog_read})
)
port = int(os.environ["FACTORY_LOOPBACK_TCP_PORTS"].split(",")[0])
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", port)); srv.listen(8)
while True:
    conn, _ = srv.accept()
    try:
        conn.sendall(b"READY\\n"); data = conn.recv(64); conn.sendall(b"ECHO:" + data)
    finally:
        conn.close()
'''

# A candidate that delays binding, so a readiness gate must retry before the test can connect.
_CANDIDATE_DELAYED = 'import time\ntime.sleep(2.5)\n' + _CANDIDATE_SERVER

# A candidate that never binds (stays alive) — a readiness gate must time out, not hang forever.
_CANDIDATE_IDLE = '''
import time
while True:
    time.sleep(1)
'''

# A candidate that exits immediately — readiness must detect early candidate exit.
_CANDIDATE_EXIT = 'import sys\nsys.exit(3)\n'

_RUN_TESTS = '''
import errno, json, os, socket, time
from pathlib import Path
tests = Path(os.environ["FACTORY_TEST_DIR"])
out = Path(os.environ["FACTORY_OUTPUT_DIR"]); out.mkdir(parents=True, exist_ok=True)
problems = []
sibling = tests.parent / "candidate"
try:
    list(sibling.iterdir()); problems.append("candidate_root readable from test")
except OSError:
    pass
catalog = json.loads(Path(os.environ["FACTORY_ACCEPTANCE_CATALOG"]).read_text())
digests = {
    str(i["test_id"]): str(i["assertion_digest"])
    for t in catalog["triggers"] for o in t["obligations"] for i in o["test_assertions"]
}
port = int(os.environ["FACTORY_LOOPBACK_TCP_PORTS"].split(",")[0])
deadline = time.time() + 15; hello = b""
while time.time() < deadline:
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=1)
        hello = s.recv(64); s.sendall(b"ping"); s.recv(64); s.close(); break
    except OSError:
        time.sleep(0.2)
if hello != b"READY\\n":
    problems.append("candidate not reachable over loopback")
ext = socket.socket(); ext.settimeout(2)
try:
    ext.connect(("192.0.2.1", 9)); problems.append("external egress not denied")
except OSError as exc:
    if exc.errno not in (errno.EPERM, errno.EACCES):
        problems.append("egress errno %s" % (exc.errno,))
finally:
    ext.close()
(out / "observations.json").write_text(
    json.dumps({"problems": problems, "acceptance_digests": digests})
)
raise SystemExit(1 if problems else 0)
'''

# Readiness probe (test-side): reachable candidate -> exit 0. Never reads the candidate tree.
_READY = '''
import os, socket, sys
port = int(os.environ["FACTORY_LOOPBACK_TCP_PORTS"].split(",")[0])
try:
    s = socket.create_connection(("127.0.0.1", port), timeout=1); s.recv(16); s.close()
except OSError:
    sys.exit(1)
sys.exit(0)
'''

_CANDIDATE_LAUNCH = (sys.executable, "candidate_server.py")
_TEST_ENTRY = (sys.executable, "run_tests.py")
_READY_ENTRY = (sys.executable, "check_ready.py")


def _stage_native_target(
    tmp_path: Path,
    *,
    candidate_source: str = _CANDIDATE_SERVER,
    with_readiness: bool = False,
) -> tuple[Path, Path]:
    """Materialize a sealed Coder candidate tree and a sealed Tester test tree (disjoint roots)."""

    coder = tmp_path / "sealed-coder"
    tester = tmp_path / "sealed-tester"
    coder.mkdir(parents=True)
    tester.mkdir(parents=True)
    (coder / "candidate_server.py").write_text(candidate_source, encoding="utf-8")
    (tester / "run_tests.py").write_text(_RUN_TESTS, encoding="utf-8")
    if with_readiness:
        (tester / "check_ready.py").write_text(_READY, encoding="utf-8")
    return coder, tester


def _expected_execution(
    candidate_launch: Sequence[str],
    test_entrypoint: Sequence[str],
    readiness_entrypoint: Sequence[str] = (),
    readiness_timeout_seconds: float = 30.0,
    readiness_interval_seconds: float = 0.5,
    readiness_max_attempts: int = 120,
) -> dict[str, str]:
    execution = native_test_execution_digests(
        candidate_launch,
        test_entrypoint,
        readiness_entrypoint=readiness_entrypoint,
        readiness_timeout_seconds=readiness_timeout_seconds,
        readiness_interval_seconds=readiness_interval_seconds,
        readiness_max_attempts=readiness_max_attempts,
    )
    return {
        "command_digest": execution.command_digest,
        "configuration_digest": execution.configuration_digest,
        "environment_digest": execution.environment_digest,
    }


@pytest.mark.isolation_integration
def test_native_two_profile_isolation_and_loopback(tmp_path: Path) -> None:
    """Candidate and test run in disjoint profiles, communicate only over the declared loopback,
    neither can read the other's tree, and the test derives every ratified criterion (no readiness
    declared: the harness self-retries)."""

    root = temporary_build_loop_root(tmp_path)
    coder, tester = _stage_native_target(tmp_path)
    ratified_assertions = [
        {"test_id": f"native-suite-{n}", "assertion_digest": "sha256:" + (str(n) * 64)}
        for n in range(1, 4)
    ]
    expected_digests = {a["test_id"]: a["assertion_digest"] for a in ratified_assertions}
    catalog = _native_acceptance_catalog(
        tmp_path,
        candidate_launch=_CANDIDATE_LAUNCH,
        test_entrypoint=_TEST_ENTRY,
        test_assertions=ratified_assertions,
    )

    result = IsolatedBuildLoop(root).execute(
        build_input_path=FIXTURES / "build-input.json",
        coder_command=(),
        tester_command=(),
        validator_command=_TEST_ENTRY,
        acceptance_catalog_path=catalog,
        prebuilt_author_outputs={LaneRole.CODER: coder, LaneRole.TESTER: tester},
        candidate_loopback=[{"protocol": "tcp", "operations": ["bind", "connect"], "count": 1}],
        candidate_launch=_CANDIDATE_LAUNCH,
        native_test_entrypoint=_TEST_ENTRY,
        before_validation=lambda *_: {
            "validator_execution": _expected_execution(_CANDIDATE_LAUNCH, _TEST_ENTRY)
        },
    )

    observations = json.loads(
        (result.validator.output_directory / "test" / "observations.json").read_text()
    )
    assert observations["problems"] == [], observations["problems"]
    assert observations["acceptance_digests"] == expected_digests
    candidate_probe = json.loads(
        (result.validator.output_directory / "candidate" / "candidate-probe.json").read_text()
    )
    assert candidate_probe["test_root_read"].startswith("denied"), candidate_probe
    assert candidate_probe["catalog_read"].startswith("denied"), candidate_probe
    assert result.validator.succeeded
    assert result.passed is True


@pytest.mark.isolation_integration
def test_native_readiness_gate_success(tmp_path: Path) -> None:
    """A declared readiness argv retries a slow-binding candidate, then the test runs and passes."""

    root = temporary_build_loop_root(tmp_path)
    coder, tester = _stage_native_target(
        tmp_path, candidate_source=_CANDIDATE_DELAYED, with_readiness=True
    )
    catalog = _native_acceptance_catalog(
        tmp_path,
        candidate_launch=_CANDIDATE_LAUNCH,
        test_entrypoint=_TEST_ENTRY,
        readiness_entrypoint=_READY_ENTRY,
    )
    result = IsolatedBuildLoop(root).execute(
        build_input_path=FIXTURES / "build-input.json",
        coder_command=(),
        tester_command=(),
        validator_command=_TEST_ENTRY,
        acceptance_catalog_path=catalog,
        prebuilt_author_outputs={LaneRole.CODER: coder, LaneRole.TESTER: tester},
        candidate_loopback=[{"protocol": "tcp", "operations": ["bind", "connect"], "count": 1}],
        candidate_launch=_CANDIDATE_LAUNCH,
        native_test_entrypoint=_TEST_ENTRY,
        native_readiness_entrypoint=_READY_ENTRY,
        before_validation=lambda *_: {
            "validator_execution": _expected_execution(
                _CANDIDATE_LAUNCH, _TEST_ENTRY, _READY_ENTRY
            )
        },
    )
    evidence = json.loads(
        (result.validator.output_directory / "test" / "native-readiness.json").read_text()
    )
    assert evidence["outcome"] == "ready", evidence
    assert result.passed is True


@pytest.mark.isolation_integration
def test_native_readiness_timeout_is_evidenced(tmp_path: Path) -> None:
    """A candidate that never serves makes readiness time out; the test is skipped and evidenced."""

    root = temporary_build_loop_root(tmp_path)
    coder, tester = _stage_native_target(
        tmp_path, candidate_source=_CANDIDATE_IDLE, with_readiness=True
    )
    catalog = _native_acceptance_catalog(
        tmp_path,
        candidate_launch=_CANDIDATE_LAUNCH,
        test_entrypoint=_TEST_ENTRY,
        readiness_entrypoint=_READY_ENTRY,
        readiness_timeout_seconds=2.0,
        readiness_interval_seconds=0.3,
        readiness_max_attempts=6,
    )
    result = IsolatedBuildLoop(root).execute(
        build_input_path=FIXTURES / "build-input.json",
        coder_command=(),
        tester_command=(),
        validator_command=_TEST_ENTRY,
        acceptance_catalog_path=catalog,
        prebuilt_author_outputs={LaneRole.CODER: coder, LaneRole.TESTER: tester},
        candidate_loopback=[{"protocol": "tcp", "operations": ["bind", "connect"], "count": 1}],
        candidate_launch=_CANDIDATE_LAUNCH,
        native_test_entrypoint=_TEST_ENTRY,
        native_readiness_entrypoint=_READY_ENTRY,
        native_readiness_timeout_seconds=2.0,
        native_readiness_interval_seconds=0.3,
        native_readiness_max_attempts=6,
        before_validation=lambda *_: {
            "validator_execution": _expected_execution(
                _CANDIDATE_LAUNCH, _TEST_ENTRY, _READY_ENTRY,
                readiness_timeout_seconds=2.0,
                readiness_interval_seconds=0.3,
                readiness_max_attempts=6,
            )
        },
    )
    evidence = json.loads(
        (result.validator.output_directory / "test" / "native-readiness.json").read_text()
    )
    assert evidence["outcome"] == "readiness-timeout", evidence
    # The test entrypoint never ran, so it left no observations.
    assert not (result.validator.output_directory / "test" / "observations.json").exists()
    assert result.validator.succeeded is False
    assert result.passed is False


@pytest.mark.isolation_integration
def test_native_readiness_detects_early_candidate_exit(tmp_path: Path) -> None:
    """If the candidate exits before serving, readiness reports candidate-early-exit and stops."""

    root = temporary_build_loop_root(tmp_path)
    coder, tester = _stage_native_target(
        tmp_path, candidate_source=_CANDIDATE_EXIT, with_readiness=True
    )
    catalog = _native_acceptance_catalog(
        tmp_path,
        candidate_launch=_CANDIDATE_LAUNCH,
        test_entrypoint=_TEST_ENTRY,
        readiness_entrypoint=_READY_ENTRY,
        readiness_timeout_seconds=5.0,
        readiness_interval_seconds=0.3,
        readiness_max_attempts=20,
    )
    result = IsolatedBuildLoop(root).execute(
        build_input_path=FIXTURES / "build-input.json",
        coder_command=(),
        tester_command=(),
        validator_command=_TEST_ENTRY,
        acceptance_catalog_path=catalog,
        prebuilt_author_outputs={LaneRole.CODER: coder, LaneRole.TESTER: tester},
        candidate_loopback=[{"protocol": "tcp", "operations": ["bind", "connect"], "count": 1}],
        candidate_launch=_CANDIDATE_LAUNCH,
        native_test_entrypoint=_TEST_ENTRY,
        native_readiness_entrypoint=_READY_ENTRY,
        native_readiness_timeout_seconds=5.0,
        native_readiness_interval_seconds=0.3,
        native_readiness_max_attempts=20,
        before_validation=lambda *_: {
            "validator_execution": _expected_execution(
                _CANDIDATE_LAUNCH, _TEST_ENTRY, _READY_ENTRY,
                readiness_timeout_seconds=5.0,
                readiness_interval_seconds=0.3,
                readiness_max_attempts=20,
            )
        },
    )
    evidence = json.loads(
        (result.validator.output_directory / "test" / "native-readiness.json").read_text()
    )
    assert evidence["outcome"] == "candidate-early-exit", evidence
    assert result.passed is False


@pytest.mark.isolation_integration
def test_native_test_executor_refuses_unratified_argv(tmp_path: Path) -> None:
    """The catalog must authorize the exact native argvs; a test-entry mismatch fails closed."""

    root = temporary_build_loop_root(tmp_path)
    coder, tester = _stage_native_target(tmp_path)
    # The catalog binds the ratified test entrypoint; the run declares a different one.
    catalog = _native_acceptance_catalog(
        tmp_path, candidate_launch=_CANDIDATE_LAUNCH, test_entrypoint=_TEST_ENTRY
    )
    with pytest.raises(LaneError, match="does not authorize Validator"):
        IsolatedBuildLoop(root).execute(
            build_input_path=FIXTURES / "build-input.json",
            coder_command=(),
            tester_command=(),
            validator_command=_TEST_ENTRY,
            acceptance_catalog_path=catalog,
            prebuilt_author_outputs={LaneRole.CODER: coder, LaneRole.TESTER: tester},
            candidate_loopback=[{"protocol": "tcp", "operations": ["bind", "connect"], "count": 1}],
            candidate_launch=_CANDIDATE_LAUNCH,
            native_test_entrypoint=(sys.executable, "other_entry.py"),  # not what the catalog bound
            before_validation=lambda *_: {"validator_execution": {}},
        )
