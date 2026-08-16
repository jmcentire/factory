from __future__ import annotations

import json
import os
import platform
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from factory_core.manifest import digest_bytes, digest_obj
from factory_runtime.runner import (
    HardenedModelRunner,
    NamedSecretStore,
    RunnerError,
    RunnerLimits,
    RunnerProcessResult,
    RunnerQualification,
)
from factory_runtime.runner_isolation import MacOSNetworkedRunner


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
        prompt = json.loads(stdin)
        if index == 0:
            self.continuity_nonce = str(
                prompt["control"]["continuity"]["store_and_echo"]
            )
        output: dict[str, Any] = {
            "kind": self.kinds[index],
            "role": environment["FACTORY_ROLE"],
            "projection_digest": projection_digest,
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
                    "sequence",
                    "continuity_nonce",
                    "status",
                    "broker_requests",
                ],
                "properties": {
                    "kind": {"enum": ["canary", "handoff"]},
                    "role": {"enum": ["coder", "tester", "validator"]},
                    "projection_digest": {"pattern": "^sha256:[0-9a-f]{64}$"},
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
        "schema_version": "factory-runner-manifest/1",
        "runner_id": "runner-fixture",
        "role": "coder",
        "adapter": adapter,
        "executable": sys.executable,
        "child_executables": child_executables,
        "runner_version": "fixture-1",
        "model": "fixture-model",
        "model_version": "fixture-model-1",
        "configuration_digest": digest_obj({"configuration": "fixture"}),
        "billing_key_name": "FACTORY_TEST_API_KEY",
        "secret_names": ["FACTORY_TEST_API_KEY"],
        "output_schema_digest": digest_bytes(schema.read_bytes()),
        "network_mode": "model-api-only",
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


def _dispatch(fixture: tuple[Any, ...], *, task: str = "Implement the signed criterion") -> Any:
    runner, _, manifest, projection, schema, workspace, forbidden = fixture
    return runner.dispatch(
        run_id="run-1",
        generation=1,
        receipt_id="runner-receipt-1",
        manifest_document=manifest,
        projection_path=projection,
        output_schema_path=schema,
        task=task,
        workspace_root=workspace,
        forbidden_paths=(forbidden,),
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
    assert receipt.document["meter_semantics"] == "observed-post-call"
    assert (workspace / "output" / "runner-receipt.json").is_file()


def test_task_is_stdin_data_and_cannot_enter_runner_argv(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    attack = "$(touch /tmp/owned); --dangerously-bypass-approvals-and-sandbox"

    _dispatch(fixture, task=attack)

    backend = fixture[1]
    command = backend.calls[-1]["command"]
    assert attack not in command
    assert all("touch /tmp/owned" not in part for part in command)
    assert attack.encode() in backend.calls[-1]["stdin"]


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


@pytest.mark.parametrize(
    ("mutation", "message", "expected_calls"),
    [
        (lambda backend: backend.session_ids.__setitem__(1, "new-session"), "exact canary", 2),
        (lambda backend: backend.kinds.__setitem__(0, "handoff"), "wrong kind", 1),
        (lambda backend: backend.returncodes.__setitem__(0, 1), "failed closed", 1),
        (
            lambda backend: backend.termination_reasons.__setitem__(0, "no-artifact"),
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

    with pytest.raises(RunnerError, match=message):
        _dispatch(fixture)

    assert len(backend.calls) == expected_calls


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
