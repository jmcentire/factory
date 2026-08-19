from __future__ import annotations

import json
import os
import platform
import stat
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

import factory_runtime.runner as runtime_runner
import factory_runtime.runner_failure as runtime_runner_failure
from factory_core.manifest import digest_bytes, digest_obj
from factory_runtime.instruction_control import (
    canonical_document_bytes,
    compile_role_contract,
    derive_effective_directive_contract,
)
from factory_runtime.runner import (
    HardenedModelRunner,
    NamedSecretStore,
    RunnerError,
    RunnerInvocationError,
    RunnerLimits,
    RunnerProcessResult,
    RunnerQualification,
)
from factory_runtime.runner_failure import (
    RunnerFailureEvidenceError,
    verify_and_retain_runner_failure,
)
from factory_runtime.runner_isolation import MacOSNetworkedRunner
from factory_runtime.schema import validate_document
from factory_runtime.state_admission import derive_state_capsule, profile_digest


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.qualification = RunnerQualification(
            backend="fake-qualified-v1",
            scope_digest=digest_obj({"scope": "fixture"}),
            forbidden_read_denied=True,
            forbidden_write_denied=True,
            model_network_available=True,
            arbitrary_shell_denied=True,
            process_containment=True,
        )
        self.session_ids = ["session-1", "session-1", "session-1"]
        self.kinds = ["canary", "canary", "handoff"]
        self.returncodes = [0, 0, 0]
        self.termination_reasons = ["completed", "completed", "completed"]
        self.input_tokens = [10, 10, 20]
        self.output_tokens = [5, 5, 10]
        self.stdout = ["", "", ""]
        self.stderr = ["", "", ""]
        self.continuity_nonce = ""
        self.continuity_overrides = ["", "", ""]
        self.state_capsule_overrides = ["", "", ""]

    def qualify(
        self,
        root: str | Path,
        *,
        allowed_executables: Sequence[str | Path],
        forbidden_paths: Sequence[str | Path],
    ) -> RunnerQualification:
        self.qualification_call = {
            "root": str(root),
            "allowed": tuple(map(str, allowed_executables)),
            "forbidden": tuple(map(str, forbidden_paths)),
        }
        return self.qualification

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path,
        readable_paths: Sequence[str | Path],
        writable_paths: Sequence[str | Path],
        environment: Mapping[str, str],
        stdin: bytes,
        limits: RunnerLimits,
    ) -> RunnerProcessResult:
        index = len(self.calls)
        projection_digest = str(environment["FACTORY_PROJECTION_DIGEST"])
        state_capsule_digest = str(environment["FACTORY_STATE_CAPSULE_DIGEST"])
        prompt = json.loads(stdin)
        if index == 0:
            self.continuity_nonce = str(
                prompt["control"]["continuity"]["store_and_echo"]
            )
        output: dict[str, Any] = {
            "kind": self.kinds[index],
            "role": environment["FACTORY_ROLE"],
            "projection_digest": projection_digest,
            "state_capsule_digest": self.state_capsule_overrides[index]
            or state_capsule_digest,
            "sequence": index + 1,
            "continuity_nonce": self.continuity_overrides[index] or self.continuity_nonce,
            "status": "complete" if index == 2 else "qualified",
            "broker_requests": [],
        }
        self.calls.append(
            {
                "command": tuple(command),
                "cwd": str(cwd),
                "readable": tuple(map(str, readable_paths)),
                "writable": tuple(map(str, writable_paths)),
                "environment": dict(environment),
                "stdin": bytes(stdin),
                "limits": limits,
            }
        )
        return RunnerProcessResult(
            command=tuple(command),
            returncode=self.returncodes[index],
            stdout=self.stdout[index],
            stderr=self.stderr[index],
            structured_output=output,
            session_id=self.session_ids[index],
            input_tokens=self.input_tokens[index],
            output_tokens=self.output_tokens[index],
            process_peak=2,
            termination_reason=self.termination_reasons[index],
        )


class RefusingBackend(FakeBackend):
    def __init__(self, *, fail_at: int, reported_attempts: int) -> None:
        super().__init__()
        self.fail_at = fail_at
        self.reported_attempts = reported_attempts

    def run(self, *args: Any, **kwargs: Any) -> RunnerProcessResult:
        if len(self.calls) == self.fail_at:
            raise RunnerError(
                "backend refused",
                model_attempts=self.reported_attempts,
            )
        return super().run(*args, **kwargs)


def _schema(tmp_path: Path) -> Path:
    path = tmp_path / "handoff.schema.json"
    path.write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "kind",
                    "role",
                    "projection_digest",
                    "state_capsule_digest",
                    "sequence",
                    "continuity_nonce",
                    "status",
                    "broker_requests",
                ],
                "properties": {
                    "kind": {"enum": ["canary", "handoff"]},
                    "role": {"enum": ["coder", "tester", "validator"]},
                    "projection_digest": {"pattern": "^sha256:[0-9a-f]{64}$"},
                    "state_capsule_digest": {"pattern": "^sha256:[0-9a-f]{64}$"},
                    "sequence": {"type": "integer", "minimum": 1, "maximum": 3},
                    "continuity_nonce": {"pattern": "^[0-9a-f]{64}$"},
                    "status": {"enum": ["qualified", "complete", "blocked"]},
                    "broker_requests": {"type": "array", "maxItems": 16},
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _fixture(
    tmp_path: Path,
    *,
    backend: FakeBackend | None = None,
    adapter: str = "codex",
    pricing: tuple[int | None, int | None] = (1_000_000, 2_000_000),
    max_tokens: int = 1_000,
    max_cost_microusd: int = 1_000,
) -> tuple[HardenedModelRunner, FakeBackend, dict[str, Any], Path, Path, Path, Path]:
    backend = backend or FakeBackend()
    secret_root = tmp_path / "secrets"
    secret_root.mkdir()
    secret = secret_root / "FACTORY_TEST_API_KEY"
    secret.write_text("named-secret-value\n", encoding="utf-8")
    secret.chmod(0o600)
    projection = tmp_path / "projection.json"
    projection.write_text('{"scope":"coder-only"}\n', encoding="utf-8")
    schema = _schema(tmp_path)
    child_executables = [] if adapter == "codex" else ["/bin/echo"]
    manifest = {
        "schema_version": "factory-runner-manifest/2",
        "runner_id": "runner-fixture",
        "role": "coder",
        "adapter": adapter,
        "executable": sys.executable,
        "child_executables": child_executables,
        "runner_version": "fixture-1",
        "model": "fixture-model",
        "model_version": "fixture-model-1",
        "configuration_digest": digest_obj({"configuration": "fixture"}),
        "state_profile_digest": profile_digest("lane-dispatch"),
        "state_qualification_digest": digest_obj({"qualification": "fixture"}),
        "billing_key_name": "FACTORY_TEST_API_KEY",
        "secret_names": ["FACTORY_TEST_API_KEY"],
        "output_schema_digest": digest_bytes(schema.read_bytes()),
        "network_mode": "unrestricted-outbound",
        "limits": {
            "wall_seconds": 60,
            "idle_seconds": 10,
            "max_processes": 4,
            "max_attempts": 3,
            "max_output_bytes": 65_536,
            "max_tokens": max_tokens,
            "max_cost_microusd": max_cost_microusd,
        },
        "pricing": {
            "input_microusd_per_million": pricing[0],
            "output_microusd_per_million": pricing[1],
        },
        "created_at": 100,
    }
    runner = HardenedModelRunner(
        backend=backend,
        secret_store=NamedSecretStore(secret_root),
        clock=lambda: 100,
        monotonic=lambda: 0.0,
    )
    forbidden = tmp_path / "target"
    forbidden.mkdir()
    workspace = tmp_path / "runner-workspace"
    return runner, backend, manifest, projection, schema, workspace, forbidden


def _dispatch(
    fixture: tuple[Any, ...],
    *,
    task: str = "Implement the signed criterion",
    capsule_mutation: Callable[[dict[str, Any]], None] | None = None,
    attempt_observer: Callable[[int], None] | None = None,
) -> Any:
    runner, _, manifest, projection, schema, workspace, forbidden = fixture
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    projection_bytes = projection.read_bytes()
    schema_bytes = schema.read_bytes()
    task_bytes = task.encode()
    broker_registry = b'{"operations":[]}'
    resume_digest = digest_obj({"resume": "fixture"})
    directive_ledger = b""
    directive_provisional = b""
    role_doctrine = (
        Path(__file__).resolve().parents[1] / "docs" / "SOFTWARE-FACTORY.md"
    ).read_bytes()
    effective_directives = derive_effective_directive_contract(
        ledger_bytes=directive_ledger,
        provisional_bytes=directive_provisional,
        run_id="run-1",
        generation=1,
        role="coder",
        evaluated_at=100,
    )
    role_contract = compile_role_contract(doctrine_bytes=role_doctrine, role="coder")
    directive_readback = {
        "schema_version": "factory-directive-readback/1",
        "run_id": "run-1",
        "generation": 1,
        "role": "coder",
        "effective_directive_contract_digest": digest_obj(effective_directives),
        "semantic_clearance": False,
        "task_interpretation": {
            "restated_request": "Implement the exact authorized behavior.",
            "operational_consequence": "Return questions instead of inventing authority.",
            "ambiguity": "none",
        },
        "directives": [],
    }
    phase_documents = {
        phase: {
            "artifact_id": f"{phase}-fixture",
            "phase": phase,
            "version": "1",
            "source_digest": "sha256:" + "a" * 64,
            "human_ratifier": "human:fixture",
            "validator_ratifier": "agent:fixture",
            "items": [
                {
                    "item_id": f"{phase}:1",
                    "canonical_statement": f"Ratified {phase} behavior.",
                    "supersedes": [],
                }
            ],
        }
        for phase in (
            "product-specification",
            "architecture",
            "operational-maturity",
        )
    }
    phase_digests = {
        phase: digest_obj(document) for phase, document in phase_documents.items()
    }
    dependencies = {
        "target-state": b'{"target":"fixture"}',
        "run-ledger-head": ("sha256:" + "1" * 64).encode(),
        "phase-artifact-digests": json.dumps(
            phase_digests, sort_keys=True, separators=(",", ":")
        ).encode(),
        **{
            f"phase-artifact-{phase}": json.dumps(
                document, sort_keys=True, separators=(",", ":")
            ).encode()
            for phase, document in phase_documents.items()
        },
        "frozen-task": task_bytes,
        "runner-projection": projection_bytes,
        "role-primer": b"context only",
        "effective-directives": canonical_document_bytes(effective_directives),
        "directive-readback": canonical_document_bytes(directive_readback),
        "role-contract": canonical_document_bytes(role_contract),
        "runner-manifest": manifest_bytes,
        "runner-output-schema": schema_bytes,
        "broker-registry": broker_registry,
        "resume-checkpoint": b'{"checkpoint":"fixture"}',
        "resume-verification": b'{"verified":true}',
        "configuration-set": json.dumps(
            {
                "directive-ledger": digest_bytes(directive_ledger),
                "directive-provisional": digest_bytes(directive_provisional),
                "role-doctrine": digest_bytes(role_doctrine),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
        "state-qualification-observations": b'{"observations":[]}',
        "state-qualification-report": b'{"qualified":true}',
    }
    capsule = derive_state_capsule(
        purpose="lane-dispatch",
        run_id="run-1",
        generation=1,
        role="coder",
        target_state_digest=digest_obj({"target": "fixture"}),
        run_ledger_head="sha256:" + "1" * 64,
        resume_checkpoint_digest=resume_digest,
        dependencies=dependencies,
    )
    if capsule_mutation is not None:
        capsule_mutation(capsule)
    return runner.dispatch(
        run_id="run-1",
        generation=1,
        receipt_id="runner-receipt-1",
        manifest_bytes=manifest_bytes,
        projection_bytes=projection_bytes,
        output_schema_bytes=schema_bytes,
        task_bytes=task_bytes,
        state_capsule_document=capsule,
        state_dependencies=dependencies,
        target_state_digest=digest_obj({"target": "fixture"}),
        run_ledger_head="sha256:" + "1" * 64,
        resume_checkpoint_digest=resume_digest,
        broker_registry_source_digest=digest_bytes(broker_registry),
        workspace_root=workspace,
        forbidden_paths=(forbidden,),
        attempt_observer=attempt_observer,
    )


def test_runner_uses_closed_environment_canaries_resume_and_names_only_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ambient-must-not-leak")
    fixture = _fixture(tmp_path)

    handoff, receipt = _dispatch(fixture)

    _, backend, _, _, _, workspace, _ = fixture
    assert handoff["kind"] == "handoff"
    assert len(backend.calls) == 3
    first, second, third = backend.calls
    assert first["command"][1:4] == ("exec", "--sandbox", "read-only")
    assert second["command"][1:3] == ("exec", "resume")
    assert "--sandbox" not in second["command"]
    assert second["command"][-2:] == ("session-1", "-")
    assert third["command"][-2:] == ("session-1", "-")
    assert "AWS_SECRET_ACCESS_KEY" not in first["environment"]
    assert first["environment"]["FACTORY_TEST_API_KEY"] == "named-secret-value"
    assert first["environment"]["HOME"] == str(workspace / "home")
    encoded_receipt = json.dumps(receipt.document, sort_keys=True)
    assert "named-secret-value" not in encoded_receipt
    assert receipt.document["secret_names"] == ["FACTORY_TEST_API_KEY"]
    assert receipt.document["network_mode"] == "unrestricted-outbound"
    assert receipt.document["meter_semantics"] == "observed-post-call"
    assert (workspace / "output" / "runner-receipt.json").is_file()
    assert receipt.document["schema_version"] == "factory-runner-receipt/3"
    assert receipt.document["prompt_schema_version"] == "factory-runner-prompt/3"
    assert (
        receipt.document["prompt_assembler_version"]
        == "factory-runner-prompt-assembler/2"
    )
    assert receipt.document["prompt_bytes_retained"] is True
    for private_directory in (
        workspace,
        workspace / "input",
        workspace / "output",
        workspace / "home",
        workspace / "tmp",
    ):
        assert stat.S_IMODE(private_directory.stat().st_mode) == 0o700
    for private_input in (
        workspace / "input" / "projection.json",
        workspace / "input" / "output-schema.json",
        workspace / "input" / "state-capsule.json",
    ):
        assert stat.S_IMODE(private_input.stat().st_mode) == 0o600
    assert [item["kind"] for item in receipt.document["prompt_sequence"]] == [
        "qualification",
        "qualification",
        "task",
    ]
    for index, call in enumerate(backend.calls, start=1):
        retained = workspace / "input" / f"prompt-{index}.json"
        assert retained.read_bytes() == call["stdin"]
        prompt_receipt = receipt.document["prompt_sequence"][index - 1]
        assert prompt_receipt["attempt"] == index
        assert prompt_receipt["byte_count"] == len(call["stdin"])
        assert prompt_receipt["content_digest"] == digest_bytes(call["stdin"])
    task_prompt = json.loads(third["stdin"])
    assert "state_capsule" not in task_prompt["data"]
    assert set(task_prompt["data"]["ratified_phase_artifacts"]) == {
        "product-specification",
        "architecture",
        "operational-maturity",
    }
    assert task_prompt["data"]["role_primer"] == "context only"
    assert task_prompt["control"]["role_contract"]["role"] == "coder"
    assert task_prompt["data"]["effective_directives"]["directives"] == []
    assert task_prompt["data"]["directive_readback"]["semantic_clearance"] is False


def test_runner_receipt_v2_keeps_its_historical_prompt_identity(tmp_path: Path) -> None:
    _, receipt = _dispatch(_fixture(tmp_path))
    historical = json.loads(json.dumps(receipt.document))
    historical["schema_version"] = "factory-runner-receipt/2"
    historical["prompt_schema_version"] = "factory-runner-prompt/2"
    historical["prompt_assembler_version"] = "factory-runner-prompt-assembler/1"

    validate_document("runner-receipt", historical)

    historical["prompt_schema_version"] = "factory-runner-prompt/3"
    with pytest.raises(ValueError, match="factory-runner-prompt/2"):
        validate_document("runner-receipt", historical)


def test_runner_freezes_caller_owned_capsule_before_backend_activity(
    tmp_path: Path,
) -> None:
    holder: dict[str, dict[str, Any]] = {}

    class MutatingQualificationBackend(FakeBackend):
        def qualify(
            self,
            root: str | Path,
            *,
            allowed_executables: Sequence[str | Path],
            forbidden_paths: Sequence[str | Path],
        ) -> RunnerQualification:
            holder["capsule"]["role"] = "tester"
            return super().qualify(
                root,
                allowed_executables=allowed_executables,
                forbidden_paths=forbidden_paths,
            )

    fixture = _fixture(tmp_path, backend=MutatingQualificationBackend())
    handoff, receipt = _dispatch(
        fixture,
        capsule_mutation=lambda capsule: holder.__setitem__("capsule", capsule),
    )

    assert holder["capsule"]["role"] == "tester"
    assert handoff["role"] == "coder"
    assert receipt.document["role"] == "coder"


def test_task_is_stdin_data_and_cannot_enter_runner_argv(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    attack = "$(touch /tmp/owned); --dangerously-bypass-approvals-and-sandbox"

    _dispatch(fixture, task=attack)

    backend = fixture[1]
    command = backend.calls[-1]["command"]
    assert attack not in command
    assert all("touch /tmp/owned" not in part for part in command)
    assert attack.encode() in backend.calls[-1]["stdin"]


def test_oversized_assembled_task_prompt_refuses_before_any_model_attempt(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    observed: list[int] = []

    with pytest.raises(RunnerError, match="prompt exceeds") as refused:
        _dispatch(
            fixture,
            task="x" * 2_097_000,
            attempt_observer=observed.append,
        )

    assert refused.value.model_attempts == 0
    assert fixture[1].calls == []
    assert observed == []
    assert not list(fixture[5].glob("input/prompt-*.json"))


def test_ollama_adapter_launches_codex_not_opencode(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, adapter="ollama-codex")

    _dispatch(fixture)

    first, second, _ = fixture[1].calls
    assert first["command"][1:6] == (
        "launch",
        "codex",
        "--model",
        "fixture-model",
        "--",
    )
    assert "opencode" not in first["command"]
    assert "resume" in second["command"]


def test_failed_qualification_stops_before_any_model_attempt(tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.qualification = RunnerQualification(
        backend="fake-unqualified",
        scope_digest=digest_obj({"scope": "bad"}),
        forbidden_read_denied=True,
        forbidden_write_denied=True,
        model_network_available=True,
        arbitrary_shell_denied=False,
        process_containment=True,
    )
    fixture = _fixture(tmp_path, backend=backend)

    with pytest.raises(RunnerError, match="did not satisfy"):
        _dispatch(fixture)

    assert backend.calls == []


def test_invalid_state_capsule_stops_before_qualification_or_model_attempt(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    with pytest.raises(RunnerError, match="state capsule is invalid"):
        _dispatch(
            fixture,
            capsule_mutation=lambda capsule: capsule.__setitem__(
                "resume_checkpoint_digest", "sha256:" + "f" * 64
            ),
        )

    assert fixture[1].calls == []
    assert not hasattr(fixture[1], "qualification_call")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target_state_digest", "sha256:" + "e" * 64),
        ("run_ledger_head", "sha256:" + "d" * 64),
    ],
)
def test_runner_rechecks_capsule_target_and_ledger_scope_before_model(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    fixture = _fixture(tmp_path)

    with pytest.raises(RunnerError, match="state capsule is invalid"):
        _dispatch(
            fixture,
            capsule_mutation=lambda capsule: capsule.__setitem__(field, value),
        )

    assert fixture[1].calls == []


def test_runner_manifest_cannot_claim_unenforced_provider_only_egress(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    fixture[2]["network_mode"] = "model-api-only"

    with pytest.raises(RunnerError, match="network_mode"):
        _dispatch(fixture)

    assert fixture[1].calls == []


def test_legacy_runner_manifest_requires_clean_restart(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture[2]["schema_version"] = "factory-runner-manifest/1"

    with pytest.raises(RunnerError, match="explicitly abandon the legacy run") as error:
        _dispatch(fixture)

    assert error.value.refusal_code == "LEGACY_RUNNER_MANIFEST"
    assert fixture[1].calls == []


def test_canary_must_echo_exact_state_capsule_digest(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture[1].state_capsule_overrides[0] = "sha256:" + "f" * 64

    with pytest.raises(RunnerError, match="state_capsule_digest"):
        _dispatch(fixture)

    assert len(fixture[1].calls) == 1


@pytest.mark.parametrize(
    ("mutation", "message", "expected_calls"),
    [
        (lambda backend: backend.session_ids.__setitem__(1, "new-session"), "exact canary", 2),
        (lambda backend: backend.kinds.__setitem__(0, "handoff"), "wrong kind", 1),
        (lambda backend: backend.returncodes.__setitem__(0, 1), "failed closed", 1),
        (
            lambda backend: backend.termination_reasons.__setitem__(0, "process-escape"),
            "failed closed",
            1,
        ),
    ],
)
def test_canary_or_process_failure_prevents_task_dispatch(
    tmp_path: Path,
    mutation: Any,
    message: str,
    expected_calls: int,
) -> None:
    fixture = _fixture(tmp_path)
    backend = fixture[1]
    mutation(backend)

    with pytest.raises(RunnerError, match=message) as error:
        _dispatch(fixture)

    assert len(backend.calls) == expected_calls
    assert error.value.model_attempts == expected_calls


@pytest.mark.parametrize(
    ("fail_at", "reported_attempts", "expected_attempts", "expected_observations"),
    [
        (0, 0, 0, []),
        (0, 1, 1, [1]),
        (1, 1, 2, [1, 2]),
    ],
)
def test_attempt_observer_distinguishes_prelaunch_refusal_from_started_process(
    tmp_path: Path,
    fail_at: int,
    reported_attempts: int,
    expected_attempts: int,
    expected_observations: list[int],
) -> None:
    backend = RefusingBackend(
        fail_at=fail_at,
        reported_attempts=reported_attempts,
    )
    observations: list[int] = []

    with pytest.raises(RunnerError, match="backend refused") as error:
        _dispatch(
            _fixture(tmp_path, backend=backend),
            attempt_observer=observations.append,
        )

    assert error.value.model_attempts == expected_attempts
    assert observations == expected_observations


@pytest.mark.parametrize("fail_at", [0, 1])
def test_counted_backend_exception_writes_typed_failure_receipt(
    tmp_path: Path,
    fail_at: int,
) -> None:
    backend = RefusingBackend(fail_at=fail_at, reported_attempts=1)
    fixture = _fixture(tmp_path, backend=backend)

    with pytest.raises(RunnerInvocationError) as raised:
        _dispatch(fixture)

    invocation = fail_at + 1
    receipt = raised.value.failure_receipt
    diagnostic = json.loads(raised.value.diagnostic_path.read_text(encoding="utf-8"))
    assert raised.value.model_attempts == invocation
    assert receipt["invocation"] == invocation
    assert receipt["model_attempts"] == invocation
    assert receipt["termination_reason"] == "supervisor-error"
    assert len(receipt["prompt_sequence"]) == invocation
    assert diagnostic["termination_reason"] == "supervisor-error"
    assert "CAPTURE INCOMPLETE" in diagnostic["stderr"]
    for index in range(1, invocation + 1):
        prompt = fixture[5] / "input" / f"prompt-{index}.json"
        assert receipt["prompt_sequence"][index - 1]["content_digest"] == digest_bytes(
            prompt.read_bytes()
        )


def test_no_artifact_result_writes_typed_failure_receipt(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture[1].termination_reasons[0] = "no-artifact"

    with pytest.raises(RunnerInvocationError) as raised:
        _dispatch(fixture)

    assert raised.value.failure_receipt["termination_reason"] == "no-artifact"
    diagnostic = json.loads(raised.value.diagnostic_path.read_text(encoding="utf-8"))
    assert diagnostic["termination_reason"] == "no-artifact"


def test_counted_supervisor_exception_retains_available_partial_capture(
    tmp_path: Path,
) -> None:
    class PartialCaptureBackend(FakeBackend):
        def run(self, *args: Any, **kwargs: Any) -> RunnerProcessResult:
            raise RunnerError(
                "injected supervisor failure",
                model_attempts=1,
                invocation_result=RunnerProcessResult(
                    command=("fixture-runner",),
                    returncode=-1,
                    stdout="captured stdout before supervisor failure",
                    stderr="[CAPTURE INCOMPLETE: injected failure]\ncaptured stderr",
                    structured_output={},
                    session_id="",
                    input_tokens=0,
                    output_tokens=0,
                    process_peak=2,
                    termination_reason="supervisor-error",
                ),
            )

    fixture = _fixture(tmp_path, backend=PartialCaptureBackend())

    with pytest.raises(RunnerInvocationError) as raised:
        _dispatch(fixture)

    diagnostic = json.loads(raised.value.diagnostic_path.read_text(encoding="utf-8"))
    assert diagnostic["stdout"] == "captured stdout before supervisor failure"
    assert diagnostic["stderr"].startswith("[CAPTURE INCOMPLETE: injected failure]")
    assert diagnostic["process_peak"] == 2


def test_failed_invocation_writes_redacted_private_diagnostics_and_safe_capsule(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    backend = fixture[1]
    backend.returncodes[0] = 1
    backend.termination_reasons[0] = "wall-limit"
    backend.stdout[0] = "named-secret-value private model output"
    backend.stderr[0] = "runner timed out"

    with pytest.raises(RunnerInvocationError) as raised:
        _dispatch(fixture)

    error = raised.value
    diagnostic = json.loads(error.diagnostic_path.read_text(encoding="utf-8"))
    failure_receipt = json.loads(error.failure_receipt_path.read_text(encoding="utf-8"))
    validate_document("runner-failure-receipt", failure_receipt)
    assert error.failure_capsule.owner == "validator-harness"
    assert error.failure_capsule.code == "runner-invocation-timeout"
    assert diagnostic["stdout"] == "[REDACTED] private model output"
    assert diagnostic["stderr"] == "runner timed out"
    assert diagnostic["termination_reason"] == "wall-limit"
    assert error.diagnostic_path == fixture[5] / "validator-invocation-diagnostic.json"
    assert error.failure_receipt_path == fixture[5] / "runner-failure-receipt.json"
    assert str(error.diagnostic_path) not in backend.calls[0]["writable"]
    assert str(error.failure_receipt_path) not in backend.calls[0]["writable"]
    assert failure_receipt == error.failure_receipt
    assert failure_receipt["run_id"] == "run-1"
    assert failure_receipt["generation"] == 1
    assert failure_receipt["role"] == "coder"
    assert failure_receipt["receipt_id"] == "runner-receipt-1"
    assert failure_receipt["invocation"] == 1
    assert failure_receipt["model_attempts"] == 1
    assert failure_receipt["prompt_sequence"] == [
        {
            "attempt": 1,
            "kind": "qualification",
            "byte_count": len((fixture[5] / "input" / "prompt-1.json").read_bytes()),
            "content_digest": digest_bytes(
                (fixture[5] / "input" / "prompt-1.json").read_bytes()
            ),
        }
    ]
    assert failure_receipt["prompt_bytes_retained"] is True
    executable_snapshot = fixture[5] / failure_receipt["executable_snapshot"][
        "relative_path"
    ]
    assert failure_receipt["executable_digest"] == digest_bytes(
        executable_snapshot.read_bytes()
    )
    assert failure_receipt["qualification_digest"] == digest_obj(
        failure_receipt["qualification"]
    )
    assert failure_receipt["diagnostic"] == {
        "content_digest": digest_bytes(error.diagnostic_path.read_bytes()),
        "byte_count": len(error.diagnostic_path.read_bytes()),
        "visibility": "validator-private",
    }
    assert failure_receipt["termination_reason"] == "wall-limit"
    assert failure_receipt["failure_capsule"] == error.failure_capsule.document()
    assert "private model output" not in json.dumps(error.failure_capsule.document())
    assert "named-secret-value" not in error.diagnostic_path.read_text(encoding="utf-8")


def test_output_limited_invocation_classifies_without_exposing_output(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    backend = fixture[1]
    backend.returncodes[0] = 124
    backend.termination_reasons[0] = "output-limit"
    backend.stderr[0] = "private oracle details " + ("x" * 20_000)

    with pytest.raises(RunnerInvocationError) as raised:
        _dispatch(fixture)

    error = raised.value
    diagnostic = json.loads(error.diagnostic_path.read_text(encoding="utf-8"))
    assert error.failure_capsule.code == "runner-invocation-output-limit"
    assert diagnostic["stderr"].endswith("[TRUNCATED]")
    assert len(diagnostic["stderr"].encode("utf-8")) <= 16_384
    assert "private oracle details" not in json.dumps(error.failure_capsule.document())


def _failure_boundary_arguments(
    tmp_path: Path,
    fixture: tuple[Any, ...],
    *,
    evidence_name: str = "evidence",
) -> dict[str, Any]:
    manifest_path = tmp_path / "runner-manifest.json"
    manifest_path.write_bytes(
        json.dumps(fixture[2], sort_keys=True, separators=(",", ":")).encode()
    )
    task_path = tmp_path / "task.md"
    task_path.write_bytes(b"Implement the signed criterion")
    evidence_root = tmp_path / evidence_name / "runner" / "coder"
    evidence_root.mkdir(parents=True)
    return {
        "workspace": fixture[5],
        "workspace_root": fixture[5].parent,
        "evidence_root": evidence_root,
        "run_root": tmp_path,
        "projection_path": fixture[3],
        "task_path": task_path,
        "manifest_path": manifest_path,
        "expected_run_id": "run-1",
        "expected_generation": 1,
        "expected_role": "coder",
        "expected_receipt_id": "runner-receipt-1",
        "expected_target_state_digest": digest_obj({"target": "fixture"}),
        "expected_resume_checkpoint_digest": digest_obj({"resume": "fixture"}),
    }


def test_failed_invocation_crosses_lane_boundary_with_exact_retained_state(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    fixture[1].returncodes[0] = 1
    fixture[1].termination_reasons[0] = "exit-nonzero"
    with pytest.raises(RunnerInvocationError):
        _dispatch(fixture)

    arguments = _failure_boundary_arguments(tmp_path, fixture)
    evidence_root = arguments["evidence_root"]

    detail = verify_and_retain_runner_failure(**arguments)

    assert detail["disposition"] == {
        "reason": "qualified runner invocation failed",
        "residue": True,
    }
    assert detail["evidence_digests"]["runner-failure-receipt"] == digest_bytes(
        (evidence_root / "runner-failure-receipt.json").read_bytes()
    )
    assert (evidence_root / "runner-failure-receipt.json").read_bytes() == (
        fixture[5] / "runner-failure-receipt.json"
    ).read_bytes()
    assert (evidence_root / "validator-invocation-diagnostic.json").read_bytes() == (
        fixture[5] / "validator-invocation-diagnostic.json"
    ).read_bytes()
    assert (evidence_root / "failed-state-capsule.json").read_bytes() == (
        fixture[5] / "input" / "state-capsule.json"
    ).read_bytes()
    assert (evidence_root / "failed-prompt-1.json").read_bytes() == (
        fixture[5] / "input" / "prompt-1.json"
    ).read_bytes()
    assert (evidence_root / "runner-qualification.json").read_bytes() == (
        fixture[5] / "input" / "runner-qualification.json"
    ).read_bytes()
    assert digest_bytes((evidence_root / "runner-executable").read_bytes()) == detail[
        "evidence_digests"
    ]["runner-executable"]
    assert os.stat(evidence_root / "runner-executable").st_ino != os.stat(
        fixture[5] / "executables" / "runner"
    ).st_ino

    receipt_path = fixture[5] / "runner-failure-receipt.json"
    original_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt = dict(original_receipt)
    receipt["projection_digest"] = "sha256:" + "f" * 64
    receipt_path.write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RunnerFailureEvidenceError, match="different projection"):
        verify_and_retain_runner_failure(**arguments)

    receipt = dict(original_receipt)
    receipt["failure_capsule"] = {
        "schema_version": "factory-failure-capsule/1",
        "owner": "host-prerequisite",
        "code": "validator-launch-environment-unavailable",
        "summary": "A model-authored but schema-valid ownership reassignment.",
    }
    receipt_path.write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RunnerFailureEvidenceError, match="failure classification"):
        verify_and_retain_runner_failure(**arguments)


def test_later_failure_binds_every_prompt_presented_to_resumed_session(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    fixture[1].returncodes[2] = 1
    fixture[1].termination_reasons[2] = "exit-nonzero"
    with pytest.raises(RunnerInvocationError) as raised:
        _dispatch(fixture)

    assert raised.value.failure_receipt["invocation"] == 3
    assert [
        item["attempt"] for item in raised.value.failure_receipt["prompt_sequence"]
    ] == [1, 2, 3]
    prompt_two = fixture[5] / "input" / "prompt-2.json"
    prompt_two.write_bytes(prompt_two.read_bytes() + b" ")
    arguments = _failure_boundary_arguments(
        tmp_path,
        fixture,
        evidence_name="prompt-mutation-evidence",
    )

    with pytest.raises(RunnerFailureEvidenceError, match="different prompt sequence"):
        verify_and_retain_runner_failure(**arguments)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("qualification_digest", "sha256:" + "f" * 64, "qualification digest"),
        ("model_attempts", 3, "misstates model attempts"),
    ],
)
def test_failure_boundary_rederives_qualification_and_attempt_count(
    tmp_path: Path,
    field: str,
    replacement: object,
    message: str,
) -> None:
    fixture = _fixture(tmp_path)
    fixture[1].returncodes[0] = 1
    with pytest.raises(RunnerInvocationError):
        _dispatch(fixture)
    receipt_path = fixture[5] / "runner-failure-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt[field] = replacement
    receipt_path.write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    arguments = _failure_boundary_arguments(
        tmp_path,
        fixture,
        evidence_name=f"{field}-evidence",
    )

    with pytest.raises(RunnerFailureEvidenceError, match=message):
        verify_and_retain_runner_failure(**arguments)


def test_failure_boundary_refuses_changed_executable_snapshot(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture[1].returncodes[0] = 1
    with pytest.raises(RunnerInvocationError) as raised:
        _dispatch(fixture)
    snapshot = fixture[5] / raised.value.failure_receipt["executable_snapshot"][
        "relative_path"
    ]
    snapshot.chmod(0o700)
    snapshot.write_bytes(snapshot.read_bytes() + b"tampered")
    arguments = _failure_boundary_arguments(
        tmp_path,
        fixture,
        evidence_name="executable-mutation-evidence",
    )

    with pytest.raises(RunnerFailureEvidenceError, match="primary executable digest"):
        verify_and_retain_runner_failure(**arguments)


def test_failure_boundary_refuses_noncanonical_state_capsule_bytes(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture[1].returncodes[0] = 1
    with pytest.raises(RunnerInvocationError):
        _dispatch(fixture)
    capsule_path = fixture[5] / "input" / "state-capsule.json"
    capsule = json.loads(capsule_path.read_text(encoding="utf-8"))
    capsule_path.write_text(json.dumps(capsule, indent=2) + "\n", encoding="utf-8")
    arguments = _failure_boundary_arguments(
        tmp_path,
        fixture,
        evidence_name="capsule-whitespace-evidence",
    )

    with pytest.raises(RunnerFailureEvidenceError, match="not canonical JSON bytes"):
        verify_and_retain_runner_failure(**arguments)


def test_identical_existing_byte_evidence_rejects_pathname_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root = tmp_path / "run"
    evidence_root = run_root / "evidence"
    evidence_root.mkdir(parents=True)
    destination = evidence_root / "failed-prompt-1.json"
    content = b"exact retained prompt"
    destination.write_bytes(content)
    real_lstat = runtime_runner_failure.os.lstat
    swapped = False

    def swap_before_identity_check(path: str | Path) -> os.stat_result:
        nonlocal swapped
        candidate = Path(path)
        if candidate == destination and not swapped:
            replacement = evidence_root / "replacement"
            replacement.write_bytes(b"mutated prompt")
            replacement.replace(destination)
            swapped = True
        return real_lstat(path)

    monkeypatch.setattr(runtime_runner_failure.os, "lstat", swap_before_identity_check)

    with pytest.raises(RunnerFailureEvidenceError, match="changed while being retained"):
        runtime_runner_failure._retain_once(
            destination,
            content,
            run_root=run_root,
        )


def test_identical_existing_executable_evidence_rejects_pathname_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root = tmp_path / "run"
    evidence_root = run_root / "evidence"
    evidence_root.mkdir(parents=True)
    source = tmp_path / "runner-source"
    source.write_bytes(b"exact runner executable")
    destination = evidence_root / "runner-executable"
    destination.write_bytes(source.read_bytes())
    real_lstat = runtime_runner_failure.os.lstat
    swapped = False

    def swap_before_identity_check(path: str | Path) -> os.stat_result:
        nonlocal swapped
        candidate = Path(path)
        if candidate == destination and not swapped:
            replacement = evidence_root / "replacement-executable"
            replacement.write_bytes(b"mutated executable")
            replacement.replace(destination)
            swapped = True
        return real_lstat(path)

    monkeypatch.setattr(runtime_runner_failure.os, "lstat", swap_before_identity_check)

    with pytest.raises(RunnerFailureEvidenceError, match="changed while being verified"):
        runtime_runner_failure._retain_existing_file(
            destination,
            source,
            expected_digest=digest_bytes(source.read_bytes()),
            expected_byte_count=len(source.read_bytes()),
            run_root=run_root,
        )


def test_identical_existing_failure_evidence_is_idempotent_and_exact(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    evidence_root = run_root / "evidence"
    evidence_root.mkdir(parents=True)
    prompt_destination = evidence_root / "failed-prompt-1.json"
    prompt = b"exact retained prompt"
    executable_source = tmp_path / "runner-source"
    executable_source.write_bytes(b"exact runner executable")
    executable_destination = evidence_root / "runner-executable"

    for _ in range(2):
        runtime_runner_failure._retain_once(
            prompt_destination,
            prompt,
            run_root=run_root,
        )
        runtime_runner_failure._retain_existing_file(
            executable_destination,
            executable_source,
            expected_digest=digest_bytes(executable_source.read_bytes()),
            expected_byte_count=len(executable_source.read_bytes()),
            run_root=run_root,
        )

    assert prompt_destination.read_bytes() == prompt
    assert executable_destination.read_bytes() == executable_source.read_bytes()


def test_failure_boundary_refuses_workspace_outside_admitted_root(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture[1].returncodes[0] = 1
    with pytest.raises(RunnerInvocationError):
        _dispatch(fixture)
    unrelated_root = tmp_path / "unrelated-runner-root"
    unrelated_root.mkdir()
    arguments = _failure_boundary_arguments(
        tmp_path,
        fixture,
        evidence_name="workspace-root-evidence",
    )
    arguments["workspace_root"] = unrelated_root

    with pytest.raises(RunnerFailureEvidenceError, match="outside its admitted root"):
        verify_and_retain_runner_failure(**arguments)


def test_ollama_failure_retains_exact_codex_child_snapshot(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, adapter="ollama-codex")
    fixture[1].returncodes[0] = 1
    with pytest.raises(RunnerInvocationError) as raised:
        _dispatch(fixture)
    child = raised.value.failure_receipt["child_executable_snapshots"]
    assert len(child) == 1
    arguments = _failure_boundary_arguments(
        tmp_path,
        fixture,
        evidence_name="ollama-child-evidence",
    )

    detail = verify_and_retain_runner_failure(**arguments)

    retained = arguments["evidence_root"] / "runner-child-executable-1"
    assert digest_bytes(retained.read_bytes()) == child[0]["content_digest"]
    assert detail["evidence_digests"]["runner-child-executable-1"] == child[0][
        "content_digest"
    ]


def test_diagnostic_retention_failure_preserves_real_model_attempt_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    fixture[1].returncodes[0] = 1
    real_write_once = runtime_runner._write_once

    def fail_diagnostic(path: Path, content: bytes) -> None:
        if path.name == "validator-invocation-diagnostic.json":
            raise OSError("injected diagnostic durability failure")
        real_write_once(path, content)

    monkeypatch.setattr(runtime_runner, "_write_once", fail_diagnostic)

    with pytest.raises(
        RunnerError,
        match="private invocation diagnostic could not be retained",
    ) as raised:
        _dispatch(fixture)

    assert raised.value.model_attempts == 1


def test_secret_exfiltration_in_any_model_output_fails_closed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture[1].stderr[0] = "named-secret-value"

    with pytest.raises(RunnerError, match="named secret"):
        _dispatch(fixture)

    assert len(fixture[1].calls) == 1


def test_same_session_id_without_secret_continuity_is_not_a_resume_proof(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    fixture[1].continuity_overrides[1] = "b" * 64

    with pytest.raises(RunnerError, match="same-session continuity"):
        _dispatch(fixture)

    assert len(fixture[1].calls) == 2


def test_cumulative_token_and_cost_limits_stop_before_task(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, max_tokens=20)

    with pytest.raises(RunnerError, match="token ceiling"):
        _dispatch(fixture)

    assert len(fixture[1].calls) == 2


def test_unknown_pricing_cannot_satisfy_a_monetary_ceiling(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, pricing=(None, None), max_cost_microusd=1)

    with pytest.raises(RunnerError, match="unknown pricing"):
        _dispatch(fixture)

    assert fixture[1].calls == []


def test_external_output_schema_references_are_denied(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    schema = fixture[4]
    schema.write_text(
        json.dumps({"$ref": "https://attacker.invalid/schema.json"}), encoding="utf-8"
    )
    fixture[2]["output_schema_digest"] = digest_bytes(schema.read_bytes())

    with pytest.raises(RunnerError, match="external reference"):
        _dispatch(fixture)


def test_runner_manifest_rejects_adapter_exec_chain_confusion(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture[2]["child_executables"] = ["/bin/echo"]

    with pytest.raises(RunnerError, match="may not declare child"):
        _dispatch(fixture)


def test_named_secret_must_be_private_regular_file(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    secret = tmp_path / "secrets" / "FACTORY_TEST_API_KEY"
    secret.chmod(0o644)

    with pytest.raises(RunnerError, match="group/world"):
        _dispatch(fixture)


def test_runner_workspace_is_never_adopted(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture[5].mkdir()

    with pytest.raises(RunnerError, match="fresh and absent"):
        _dispatch(fixture)


def test_receipt_has_no_ambient_environment_value(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    os.environ["FACTORY_AMBIENT_SENTINEL"] = "ambient-sentinel-value"
    try:
        _, receipt = _dispatch(fixture)
    finally:
        os.environ.pop("FACTORY_AMBIENT_SENTINEL", None)
    assert "ambient-sentinel-value" not in json.dumps(receipt.document)


@pytest.mark.isolation_integration
@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS Seatbelt integration")
def test_networked_backend_qualifies_file_exec_network_and_process_boundaries(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    forbidden = tmp_path / "target"
    forbidden.mkdir()
    backend = MacOSNetworkedRunner()

    qualification = backend.qualify(
        workspace / "qualification",
        allowed_executables=(sys.executable,),
        forbidden_paths=(forbidden,),
    )

    assert qualification.satisfied is True
    assert qualification.backend == "macos-seatbelt-networked-v1"


@pytest.mark.isolation_integration
@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS Seatbelt integration")
def test_networked_backend_runs_only_qualified_exec_and_reopens_structured_artifact(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = workspace / "output"
    output.mkdir()
    forbidden = tmp_path / "target"
    forbidden.mkdir()
    interpreter = (
        Path(sys.base_prefix) / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
    ).resolve(strict=True)
    helper = workspace / "runner.py"
    helper.write_text(
        "import json, pathlib, sys\n"
        "out=pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1])\n"
        "out.write_text(json.dumps({'kind':'canary'}), encoding='utf-8')\n"
        "print(json.dumps({'type':'thread.started','thread_id':'session-real'}))\n"
        "print(json.dumps({'type':'turn.completed','usage':"
        "{'input_tokens':3,'output_tokens':2}}))\n",
        encoding="utf-8",
    )
    backend = MacOSNetworkedRunner()
    backend.qualify(
        workspace / "qualification",
        allowed_executables=(interpreter,),
        forbidden_paths=(forbidden,),
    )
    destination = output / "last.json"

    result = backend.run(
        (str(interpreter), str(helper), "--output-last-message", str(destination)),
        cwd=workspace,
        readable_paths=(interpreter, helper),
        writable_paths=(output,),
        environment={
            "HOME": str(workspace),
            "TMPDIR": str(workspace),
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        },
        stdin=b"",
        limits=RunnerLimits(10, 5, 4, 3, 65_536, 100, 0),
    )

    assert result.returncode == 0
    assert result.termination_reason == "completed"
    assert result.structured_output == {"kind": "canary"}
    assert result.session_id == "session-real"
    assert (result.input_tokens, result.output_tokens) == (3, 2)


@pytest.mark.isolation_integration
@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS Seatbelt integration")
def test_networked_backend_wall_limit_covers_a_child_that_never_reads_stdin(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    interpreter = (
        Path(sys.base_prefix) / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
    ).resolve(strict=True)
    helper = workspace / "never-read.py"
    helper.write_text("import time\ntime.sleep(20)\n", encoding="utf-8")
    backend = MacOSNetworkedRunner()
    started = time.monotonic()

    result = backend._supervised(
        (str(interpreter), str(helper)),
        cwd=workspace,
        readable_paths=(interpreter, helper),
        writable_paths=(workspace,),
        environment={
            "HOME": str(workspace),
            "TMPDIR": str(workspace),
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        },
        stdin=b"x" * 2_097_152,
        limits=RunnerLimits(1, 10, 4, 3, 65_536, 100, 0),
        allowed_executables=(interpreter,),
        counts_as_model_attempt=True,
    )

    assert time.monotonic() - started < 5
    assert result.termination_reason == "wall-limit"
