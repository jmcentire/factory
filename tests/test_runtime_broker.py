from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from factory_core.manifest import digest_obj
from factory_runtime.authority import AuthorityPolicy, Principal
from factory_runtime.broker import (
    BrokerError,
    BrokerOperation,
    TypedOperationBroker,
    load_broker_registry,
)
from factory_runtime.isolation import IsolatedProcessResult, IsolationQualification
from tests.test_runtime_workflow import ROOT_KEY, _Tessera

TARGET = "sha256:" + "a" * 64
CONFIG = "sha256:" + "b" * 64


class _Isolation:
    def __init__(self, outputs: tuple[str, ...] = ("ok\n", "ok\n")) -> None:
        self.outputs = iter(outputs)

    def qualify(self, root: str | Path) -> IsolationQualification:
        Path(root).mkdir(parents=True, exist_ok=True)
        return IsolationQualification("fixture", True, True, True, True)

    def run(
        self,
        command: tuple[str, ...],
        *,
        cwd: str | Path,
        readable_paths: tuple[str | Path, ...] = (),
        writable_paths: tuple[str | Path, ...] = (),
        environment: dict[str, str] | None = None,
    ) -> IsolatedProcessResult:
        del cwd, readable_paths, writable_paths, environment
        return IsolatedProcessResult(command, 0, next(self.outputs), "")


def _policy() -> AuthorityPolicy:
    return AuthorityPolicy(
        repository_id="factory",
        policy_id="factory-authority/1",
        root_public_key=ROOT_KEY,
        principals={
            "human:founder": Principal(
                identity="human:founder",
                kind="human",
                public_key=ROOT_KEY,
                capabilities=frozenset({"factory:activate-broker-capability"}),
            )
        },
        bootstrap_enabled=False,
        bootstrap_scope=frozenset(),
        genesis_digest="sha256:" + "c" * 64,
    )


def _capability(
    tmp_path: Path,
    tessera: _Tessera,
    operation: BrokerOperation,
    *,
    max_uses: int = 2,
    configuration_digest: str = CONFIG,
) -> Path:
    payload = {
        "schema_version": "factory-broker-capability/1",
        "capability_id": "capability-1",
        "run_id": "run-1",
        "repository_id": "factory",
        "generation": 1,
        "role": "coder",
        "target_state_digest": TARGET,
        "operation_id": operation.operation_id,
        "operation_kind": operation.kind,
        "operation_definition_digest": operation.content_digest,
        "configuration_digest": configuration_digest,
        "issuer_identity": "human:founder",
        "max_uses": max_uses,
        "issued_at": 100,
        "expires_at": 200,
        "nonce": "capability-nonce-0001",
    }
    return tessera.add(
        tmp_path / "capability.tessera.json",
        payload,
        key=ROOT_KEY,
        kind="factory-broker-capability",
    )


def _request(
    tessera: _Tessera,
    capability: Path,
    *,
    kind: str,
    input_value: dict[str, Any],
    request_id: str = "request-1",
    idempotency_key: str = "effect-1",
) -> dict[str, Any]:
    handle = tessera.envelopes[str(capability)]
    return {
        "schema_version": "factory-broker-request/1",
        "request_id": request_id,
        "run_id": "run-1",
        "generation": 1,
        "role": "coder",
        "capability_digest": handle.payload_digest,
        "operation_kind": kind,
        "idempotency_key": idempotency_key,
        "input": input_value,
        "input_digest": digest_obj(input_value),
        "created_at": 110,
    }


def _broker(
    tmp_path: Path,
    tessera: _Tessera,
    operation: BrokerOperation,
    isolation: _Isolation | None = None,
) -> TypedOperationBroker:
    return TypedOperationBroker(
        run_id="run-1",
        generation=1,
        role="coder",
        target_state_digest=TARGET,
        configuration_digest=CONFIG,
        operations=(operation,),
        evidence_root=tmp_path / "effects",
        policy=_policy(),
        tessera=tessera,  # type: ignore[arg-type]
        isolation=isolation or _Isolation(),
        clock=lambda: 150,
    )


def test_publish_uses_fixed_registered_path_and_durable_rehash(tmp_path: Path) -> None:
    output = tmp_path / "owned"
    output.mkdir()
    operation = BrokerOperation(
        operation_id="publish-candidate",
        kind="publish-artifact",
        verifier_kind="durable-rehash",
        resource_root=output,
        relative_path="candidate.bin",
    )
    tessera = _Tessera()
    capability = _capability(tmp_path, tessera, operation)
    request = _request(
        tessera,
        capability,
        kind=operation.kind,
        input_value={"content_base64": base64.b64encode(b"candidate").decode("ascii")},
    )
    broker = _broker(tmp_path, tessera, operation)

    effect = broker.execute(request, capability_envelope_path=capability)
    assert effect.verified is True
    assert (output / "candidate.bin").read_bytes() == b"candidate"
    assert broker.execute(request, capability_envelope_path=capability) == effect

    replay = dict(request)
    replay["request_id"] = "request-other"
    with pytest.raises(BrokerError, match="different request"):
        broker.execute(replay, capability_envelope_path=capability)


def test_publish_materializes_bounded_text_file_map_host_side(tmp_path: Path) -> None:
    """A sealed lane emits only text; the broker owns every mechanical publish step."""

    output = tmp_path / "owned"
    output.mkdir()
    operation = BrokerOperation(
        operation_id="publish-oracle",
        kind="publish-artifact",
        verifier_kind="durable-rehash",
        resource_root=output,
        relative_path="tester",
    )
    tessera = _Tessera()
    capability = _capability(tmp_path, tessera, operation)
    files = [
        {"path": "tests/run_acceptance.py", "content": "print('ok')\n"},
        {"path": "tests/README.md", "content": "# oracle\n"},
    ]
    request = _request(
        tessera,
        capability,
        kind=operation.kind,
        input_value={"files": files},
    )
    broker = _broker(tmp_path, tessera, operation)

    effect = broker.execute(request, capability_envelope_path=capability)

    assert effect.verified is True
    root = output / "tester"
    assert (root / "tests" / "run_acceptance.py").read_text() == "print('ok')\n"
    assert (root / "tests" / "README.md").read_text() == "# oracle\n"
    assert effect.artifact_digest == digest_obj(
        {
            "files": [
                {
                    "path": item["path"],
                    "content_digest": digest_obj_bytes(item["content"]),
                }
                for item in sorted(files, key=lambda item: item["path"])
            ]
        }
    )
    # Idempotent re-execution returns the retained effect.
    assert broker.execute(request, capability_envelope_path=capability) == effect


def digest_obj_bytes(text: str) -> str:
    from factory_core.manifest import digest_bytes

    return digest_bytes(text.encode("utf-8"))


@pytest.mark.parametrize(
    "path",
    ("../escape.py", "/abs.py", "tests/../../escape.py", "tests//x.py", "", "a\\b"),
)
def test_publish_text_files_refuse_non_canonical_paths(tmp_path: Path, path: str) -> None:
    output = tmp_path / "owned"
    output.mkdir()
    operation = BrokerOperation(
        operation_id="publish-oracle",
        kind="publish-artifact",
        verifier_kind="durable-rehash",
        resource_root=output,
        relative_path="tester",
    )
    tessera = _Tessera()
    capability = _capability(tmp_path, tessera, operation)
    request = _request(
        tessera,
        capability,
        kind=operation.kind,
        input_value={"files": [{"path": path, "content": "x"}]},
    )
    broker = _broker(tmp_path, tessera, operation)

    with pytest.raises(BrokerError, match="canonical and relative|empty"):
        broker.execute(request, capability_envelope_path=capability)
    assert not (tmp_path / "escape.py").exists()


def test_publish_text_files_refuse_duplicates_and_extra_keys(tmp_path: Path) -> None:
    output = tmp_path / "owned"
    output.mkdir()
    operation = BrokerOperation(
        operation_id="publish-oracle",
        kind="publish-artifact",
        verifier_kind="durable-rehash",
        resource_root=output,
        relative_path="tester",
    )
    tessera = _Tessera()
    capability = _capability(tmp_path, tessera, operation)
    broker = _broker(tmp_path, tessera, operation)

    duplicated = _request(
        tessera,
        capability,
        kind=operation.kind,
        input_value={
            "files": [
                {"path": "a.py", "content": "1"},
                {"path": "a.py", "content": "2"},
            ]
        },
    )
    with pytest.raises(BrokerError, match="unique"):
        broker.execute(duplicated, capability_envelope_path=capability)

    widened = _request(
        tessera,
        capability,
        kind=operation.kind,
        input_value={"files": [{"path": "a.py", "content": "1", "mode": "755"}]},
    )
    with pytest.raises(BrokerError, match="exactly path and content"):
        broker.execute(widened, capability_envelope_path=capability)


def test_model_cannot_supply_a_path_command_script_or_extra_input(tmp_path: Path) -> None:
    output = tmp_path / "owned"
    output.mkdir()
    operation = BrokerOperation(
        operation_id="publish-candidate",
        kind="publish-artifact",
        verifier_kind="durable-rehash",
        resource_root=output,
        relative_path="candidate.bin",
    )
    tessera = _Tessera()
    capability = _capability(tmp_path, tessera, operation)
    escaped = tmp_path / "escape"
    injected = {
        "content_base64": base64.b64encode(b"candidate").decode("ascii"),
        "path": str(escaped),
        "command": "sh -c whoami",
        "script": "malicious",
    }
    request = _request(
        tessera,
        capability,
        kind=operation.kind,
        input_value=injected,
    )
    with pytest.raises(BrokerError, match="only content_base64"):
        _broker(tmp_path, tessera, operation).execute(
            request,
            capability_envelope_path=capability,
        )
    assert not escaped.exists()


def test_signed_handle_cannot_be_reused_for_changed_configuration_or_definition(
    tmp_path: Path,
) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    original = BrokerOperation(
        operation_id="read-input",
        kind="read-artifact",
        verifier_kind="content-rehash",
        resource_root=root,
        relative_path="one.txt",
    )
    changed = BrokerOperation(
        operation_id="read-input",
        kind="read-artifact",
        verifier_kind="content-rehash",
        resource_root=root,
        relative_path="two.txt",
    )
    (root / "one.txt").write_text("one", encoding="utf-8")
    (root / "two.txt").write_text("two", encoding="utf-8")
    tessera = _Tessera()
    capability = _capability(tmp_path, tessera, original)
    request = _request(tessera, capability, kind="read-artifact", input_value={})
    with pytest.raises(BrokerError, match="definition differs"):
        _broker(tmp_path, tessera, changed).execute(
            request,
            capability_envelope_path=capability,
        )

    other = tmp_path / "other"
    other.mkdir()
    wrong_config = _capability(
        other,
        tessera,
        original,
        configuration_digest="sha256:" + "d" * 64,
    )
    wrong_request = _request(
        tessera,
        wrong_config,
        kind="read-artifact",
        input_value={},
    )
    with pytest.raises(BrokerError, match="another configuration"):
        _broker(tmp_path, tessera, original).execute(
            wrong_request,
            capability_envelope_path=wrong_config,
        )


def test_verifier_effect_requires_two_identical_isolated_runs(tmp_path: Path) -> None:
    operation = BrokerOperation(
        operation_id="run-tests",
        kind="run-verifier",
        verifier_kind="deterministic-rerun",
        command=("fixed-verifier",),
    )
    tessera = _Tessera()
    capability = _capability(tmp_path, tessera, operation)
    request = _request(
        tessera,
        capability,
        kind="run-verifier",
        input_value={"stdin": {"candidate": "sha256:" + "e" * 64}},
    )
    with pytest.raises(BrokerError, match="operation-specific verifier rejected"):
        _broker(
            tmp_path,
            tessera,
            operation,
            _Isolation(("first\n", "second\n")),
        ).execute(request, capability_envelope_path=capability)


def test_capability_use_ceiling_counts_distinct_effects_not_idempotent_replay(
    tmp_path: Path,
) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    (root / "input.txt").write_text("value", encoding="utf-8")
    operation = BrokerOperation(
        operation_id="read-input",
        kind="read-artifact",
        verifier_kind="content-rehash",
        resource_root=root,
        relative_path="input.txt",
    )
    tessera = _Tessera()
    capability = _capability(tmp_path, tessera, operation, max_uses=1)
    broker = _broker(tmp_path, tessera, operation)
    request = _request(tessera, capability, kind="read-artifact", input_value={})
    first = broker.execute(request, capability_envelope_path=capability)
    assert broker.execute(request, capability_envelope_path=capability) == first
    second = _request(
        tessera,
        capability,
        kind="read-artifact",
        input_value={},
        request_id="request-2",
        idempotency_key="effect-2",
    )
    with pytest.raises(BrokerError, match="ceiling is exhausted"):
        broker.execute(second, capability_envelope_path=capability)


def test_capability_use_ceiling_is_atomic_across_concurrent_requests(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    (root / "input.txt").write_text("value", encoding="utf-8")
    operation = BrokerOperation(
        operation_id="read-input",
        kind="read-artifact",
        verifier_kind="content-rehash",
        resource_root=root,
        relative_path="input.txt",
    )
    tessera = _Tessera()
    capability = _capability(tmp_path, tessera, operation, max_uses=1)
    broker = _broker(tmp_path, tessera, operation)
    requests = (
        _request(
            tessera,
            capability,
            kind="read-artifact",
            input_value={},
            request_id=f"request-{number}",
            idempotency_key=f"effect-{number}",
        )
        for number in (1, 2)
    )

    def execute(request: dict[str, Any]) -> str:
        try:
            return broker.execute(
                request, capability_envelope_path=capability
            ).content_digest
        except BrokerError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(execute, requests))

    assert sum(outcome.startswith("sha256:") for outcome in outcomes) == 1
    assert sum("ceiling is exhausted" in outcome for outcome in outcomes) == 1


def _registry_document(root: Path, envelope: Path) -> dict[str, Any]:
    return {
        "schema_version": "factory-broker-registry/1",
        "registry_id": "coder-registry",
        "run_id": "run-1",
        "generation": 1,
        "role": "coder",
        "target_state_digest": TARGET,
        "operations": [
            {
                "operation_id": "publish-candidate",
                "kind": "publish-artifact",
                "verifier_kind": "durable-rehash",
                "resource_id": "candidate-output",
                "resource_root": str(root),
                "relative_path": "candidate.bin",
                "command": [],
                "command_readable_paths": [],
            }
        ],
        "capabilities": [
            {
                "capability_digest": "sha256:" + "d" * 64,
                "envelope_path": str(envelope),
            }
        ],
        "created_at": 100,
    }


def test_registry_configuration_digest_is_computable_before_capability_issuance(
    tmp_path: Path,
) -> None:
    """A capability embeds the configuration digest and the registry lists the capability.

    The binding is satisfiable only if the digest is independent of the capability
    handles themselves; otherwise no issuable capability can ever pass the
    execute-time configuration check.
    """

    root = (tmp_path / "owned").resolve()
    root.mkdir()
    envelope = (tmp_path / "capability.json").resolve()
    envelope.write_text("{}\n", encoding="utf-8")
    resources = {
        "candidate-output": {
            "ownership": "run-owned",
            "status": "active",
            "identifier": str(root),
        }
    }
    document = _registry_document(root, envelope)
    pre_issuance = dict(document)
    pre_issuance["capabilities"] = []

    registry = load_broker_registry(
        document,
        run_id="run-1",
        generation=1,
        role="coder",
        target_state_digest=TARGET,
        resources=resources,
    )

    assert registry.configuration_digest == digest_obj(pre_issuance)


def test_registry_resolves_only_active_run_owned_resource_roots(tmp_path: Path) -> None:
    root = (tmp_path / "owned").resolve()
    root.mkdir()
    envelope = (tmp_path / "capability.json").resolve()
    envelope.write_text("{}\n", encoding="utf-8")
    resources = {
        "candidate-output": {
            "ownership": "run-owned",
            "status": "active",
            "identifier": str(root),
        }
    }

    registry = load_broker_registry(
        _registry_document(root, envelope),
        run_id="run-1",
        generation=1,
        role="coder",
        target_state_digest=TARGET,
        resources=resources,
    )

    assert registry.operations[0].resolved_path() == root / "candidate.bin"
    assert registry.capability_envelopes["sha256:" + "d" * 64] == envelope


@pytest.mark.parametrize(
    ("ownership", "status"),
    (("external-non-owned", "active"), ("run-owned", "retained")),
)
def test_registry_refuses_non_owned_or_non_active_file_effect_roots(
    tmp_path: Path,
    ownership: str,
    status: str,
) -> None:
    root = (tmp_path / "owned").resolve()
    root.mkdir()
    envelope = (tmp_path / "capability.json").resolve()
    envelope.write_text("{}\n", encoding="utf-8")

    with pytest.raises(BrokerError, match="active run-owned"):
        load_broker_registry(
            _registry_document(root, envelope),
            run_id="run-1",
            generation=1,
            role="coder",
            target_state_digest=TARGET,
            resources={
                "candidate-output": {
                    "ownership": ownership,
                    "status": status,
                    "identifier": str(root),
                }
            },
        )


def test_registry_refuses_root_substitution_even_with_same_resource_id(tmp_path: Path) -> None:
    root = (tmp_path / "owned").resolve()
    root.mkdir()
    substituted = (tmp_path / "substituted").resolve()
    substituted.mkdir()
    envelope = (tmp_path / "capability.json").resolve()
    envelope.write_text("{}\n", encoding="utf-8")

    with pytest.raises(BrokerError, match="differs from the run resource ledger"):
        load_broker_registry(
            _registry_document(substituted, envelope),
            run_id="run-1",
            generation=1,
            role="coder",
            target_state_digest=TARGET,
            resources={
                "candidate-output": {
                    "ownership": "run-owned",
                    "status": "active",
                    "identifier": str(root),
                }
            },
        )
