from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from factory_core.manifest import digest_obj
from factory_runtime.attempt_admission import (
    ENVELOPE_KIND,
    SCHEMA_VERSION,
    AttemptAdmissionError,
    admit_attempt_package,
)
from factory_runtime.authority import AuthorityPolicy, Principal
from factory_runtime.cli import _parser
from factory_runtime.snapshot import tree_digest
from factory_runtime.tessera import VerifiedEnvelope


@dataclass
class FakeTessera:
    payload: dict[str, object]
    calls: list[dict[str, object]]

    def verify_json(self, path, **kwargs):
        self.calls.append(dict(kwargs))
        return VerifiedEnvelope(
            kind=ENVELOPE_KIND,
            payload=self.payload,
            payload_digest=digest_obj(self.payload),
            public_key="a" * 64,
            envelope_digest="sha256:" + "b" * 64,
            path=Path(path),
        )


def _policy() -> AuthorityPolicy:
    verifier = Principal("agent:validator", "agent", "a" * 64, frozenset())
    return AuthorityPolicy(
        "repo",
        "policy",
        "c" * 64,
        {verifier.identity: verifier},
        False,
        frozenset(),
        "sha256:" + "d" * 64,
    )


def _payload(tmp_path: Path) -> dict[str, object]:
    runner = tmp_path / "runner.py"
    runner.write_text("pass", encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    profile = {"command": ["python", str(runner)], "trusted_paths": [str(runner)]}
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": "run-1",
        "attempt_id": "attempt-1",
        "identities": {
            "implementer": "agent:coder",
            "tester": "agent:tester",
            "verifier": "agent:validator",
        },
        "build": {},
        "execution_profiles": {"coder": profile, "tester": profile, "validator": profile},
        "target_runtime_profile": {
            "candidate_launch": ["python", "server.py"],
            "runtime_read_paths": [str(runtime)],
            "readiness": {
                "entrypoint": "/health",
                "timeout_seconds": 15,
                "interval_seconds": 1,
                "max_attempts": 15,
            },
            "loopback": {"tcp_ports": [8080], "udp_ports": []},
        },
        "one_attempt_policy": {
            "retry": "forbidden",
            "retention": "retain",
            "terminal_disposition": "record",
        },
        "predecessors": {"required": False, "artifacts": []},
    }


def test_admission_projects_only_closed_validator_environment(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    tessera = FakeTessera(payload, [])

    admitted = admit_attempt_package(
        tmp_path / "package.tessera.json", policy=_policy(), tessera=tessera
    )

    assert admitted.coder_command == ("python", str(tmp_path / "runner.py"))
    assert admitted.validator_environment["FACTORY_CANDIDATE_LAUNCH"] == '["python", "server.py"]'
    assert admitted.validator_environment["FACTORY_LOOPBACK_TCP_PORTS"] == "8080"
    assert all(key.startswith("FACTORY_") for key in admitted.validator_environment)
    assert admitted.validator_runtime_paths == ((tmp_path / "runtime").resolve(),)
    assert admitted.validator_network_policy.identity == {
        "label": "declared-loopback",
        "grants": [
            {"protocol": "tcp", "operation": "bind", "ports": [8080]},
            {"protocol": "tcp", "operation": "connect", "ports": [8080]},
        ],
    }
    clauses = admitted.validator_network_policy.clauses()
    assert '(allow network-bind (local tcp "localhost:8080"))' in clauses
    assert '(allow network-outbound (remote tcp "localhost:8080"))' in clauses
    assert "192.0.2.1" not in clauses
    assert (
        admitted.receipt["validator_network_policy"]
        == admitted.validator_network_policy.identity
    )


def test_native_v3_profile_binds_only_declared_executor_facts(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    payload["schema_version"] = SCHEMA_VERSION
    payload["target_runtime_profile"] = {
        "mode": "native-two-profile",
        "candidate_launch": ["/usr/bin/python3", "server.py"],
        "test_entrypoint": ["/usr/bin/python3", "-m", "pytest"],
        "runtime_read_paths": [str(tmp_path / "runtime")],
        "readiness": {
            "entrypoint": ["/usr/bin/python3", "ready.py"],
            "timeout_seconds": 15,
            "interval_seconds": 1,
            "max_attempts": 15,
        },
        "loopback": [{"protocol": "tcp", "operations": ["bind", "connect"], "count": 1}],
        "port_bindings": [{"tcp_slot": 0, "target_input": "PORT"}],
    }
    admitted = admit_attempt_package(
        tmp_path / "package.tessera.json", policy=_policy(), tessera=FakeTessera(payload, [])
    )
    assert admitted.native_runtime is not None
    assert admitted.native_runtime["candidate_launch"] == ("/usr/bin/python3", "server.py")
    assert admitted.native_runtime["identity"].startswith("sha256:")
    assert admitted.native_runtime["port_bindings"] == ((0, "PORT"),)
    assert admitted.validator_network_policy.identity == {"label": "deny-all", "grants": []}


def test_native_port_bindings_support_multiple_declared_tcp_slots(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    payload["target_runtime_profile"] = {
        "mode": "native-two-profile",
        "candidate_launch": ["/usr/bin/python3", "server.py"],
        "test_entrypoint": ["/usr/bin/python3", "-m", "pytest"],
        "runtime_read_paths": [str(tmp_path / "runtime")],
        "readiness": {
            "entrypoint": ["/usr/bin/python3", "ready.py"],
            "timeout_seconds": 15,
            "interval_seconds": 1,
            "max_attempts": 15,
        },
        "loopback": [{"protocol": "tcp", "operations": ["bind", "connect"], "count": 2}],
        "port_bindings": [
            {"tcp_slot": 0, "target_input": "HTTP_PORT"},
            {"tcp_slot": 1, "target_input": "ADMIN_PORT"},
        ],
    }

    admitted = admit_attempt_package(
        tmp_path / "package.tessera.json", policy=_policy(), tessera=FakeTessera(payload, [])
    )

    assert admitted.native_runtime is not None
    assert admitted.native_runtime["port_bindings"] == ((0, "HTTP_PORT"), (1, "ADMIN_PORT"))


@pytest.mark.parametrize(
    "bindings, match",
    [
        (
            [
                {"tcp_slot": 0, "target_input": "PORT"},
                {"tcp_slot": 0, "target_input": "ADMIN_PORT"},
            ],
            "reuses a declared",
        ),
        ([{"tcp_slot": 1, "target_input": "PORT"}], "unsupported"),
        ([{"tcp_slot": 0, "target_input": "FACTORY_OUTPUT_DIR"}], "unsupported"),
        ([], "must bind every declared"),
    ],
)
def test_native_port_bindings_refuse_duplicate_undeclared_and_reserved_inputs(
    tmp_path: Path, bindings, match: str
) -> None:
    payload = _payload(tmp_path)
    payload["target_runtime_profile"] = {
        "mode": "native-two-profile",
        "candidate_launch": ["/usr/bin/python3", "server.py"],
        "test_entrypoint": ["/usr/bin/python3", "-m", "pytest"],
        "runtime_read_paths": [str(tmp_path / "runtime")],
        "readiness": {
            "entrypoint": ["/usr/bin/python3", "ready.py"],
            "timeout_seconds": 15,
            "interval_seconds": 1,
            "max_attempts": 15,
        },
        "loopback": [{"protocol": "tcp", "operations": ["bind", "connect"], "count": 1}],
        "port_bindings": bindings,
    }

    with pytest.raises(AttemptAdmissionError, match=match):
        admit_attempt_package(
            tmp_path / "package.tessera.json", policy=_policy(), tessera=FakeTessera(payload, [])
        )


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (
            lambda payload: payload.__setitem__(
                "schema_version", "factory-one-attempt-admission/999"
            ),
            "unsupported",
        ),
        (
            lambda payload: payload["one_attempt_policy"].__setitem__("retry", "allowed"),
            "forbid retry",
        ),  # type: ignore[index]
        (lambda payload: payload["predecessors"].__setitem__("required", True), "omitted"),  # type: ignore[index]
    ],
)
def test_admission_refuses_before_dispatch(tmp_path: Path, mutate, match: str) -> None:
    payload = _payload(tmp_path)
    mutate(payload)
    tessera = FakeTessera(payload, [])

    with pytest.raises(AttemptAdmissionError, match=match):
        admit_attempt_package(tmp_path / "package.tessera.json", policy=_policy(), tessera=tessera)

    # Validation only queried the admission envelope; no predecessor or lane was touched.
    assert len(tessera.calls) == 1


def test_required_predecessor_must_be_a_signed_exact_envelope(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    predecessor = {
        "envelope_path": str(tmp_path / "previous.tessera.json"),
        "payload_digest": "sha256:" + "e" * 64,
        "kind": "factory-previous",
    }
    payload["predecessors"] = {"required": True, "artifacts": [predecessor]}
    tessera = FakeTessera(payload, [])

    admit_attempt_package(tmp_path / "package.tessera.json", policy=_policy(), tessera=tessera)

    assert tessera.calls[-1]["expected_kind"] == "factory-previous"
    assert tessera.calls[-1]["expected_payload_digest"] == predecessor["payload_digest"]


def test_sealed_author_reference_refuses_tampered_tree_before_dispatch(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    coder = tmp_path / "sealed-coder"
    tester = tmp_path / "sealed-tester"
    coder.mkdir()
    tester.mkdir()
    (coder / "candidate.txt").write_text("candidate", encoding="utf-8")
    (tester / "test.txt").write_text("test", encoding="utf-8")
    payload["sealed_author_outputs"] = {
        "coder": {"source_path": str(coder), "tree_digest": "sha256:" + "1" * 64},
        "tester": {"source_path": str(tester), "tree_digest": "sha256:" + "2" * 64},
    }
    tessera = FakeTessera(payload, [])

    with pytest.raises(AttemptAdmissionError, match="tree digest does not bind"):
        admit_attempt_package(tmp_path / "package.tessera.json", policy=_policy(), tessera=tessera)

    assert len(tessera.calls) == 1


def test_sealed_author_reference_refuses_unavailable_runner_receipt_before_dispatch(
    tmp_path: Path,
) -> None:
    payload = _payload(tmp_path)
    coder = tmp_path / "sealed-coder"
    tester = tmp_path / "sealed-tester"
    coder.mkdir()
    tester.mkdir()
    (coder / "candidate.txt").write_text("candidate", encoding="utf-8")
    (tester / "test.txt").write_text("test", encoding="utf-8")
    payload["sealed_author_outputs"] = {
        "coder": {"source_path": str(coder), "tree_digest": tree_digest(coder)},
        "tester": {"source_path": str(tester), "tree_digest": tree_digest(tester)},
    }
    missing = str(tmp_path / "missing-runner-receipt.json")
    payload["execution_profiles"] = {
        "coder": {"qualified_runner_receipt": {"role": "coder", "path": missing, "digest": "x"}},
        "tester": {"qualified_runner_receipt": {"role": "tester", "path": missing, "digest": "x"}},
        "validator": payload["execution_profiles"]["validator"],  # type: ignore[index]
    }

    with pytest.raises(AttemptAdmissionError, match="receipt path is unavailable"):
        admit_attempt_package(
            tmp_path / "package.tessera.json", policy=_policy(), tessera=FakeTessera(payload, [])
        )


def test_public_cli_has_only_the_signed_typed_submission_inputs() -> None:
    parser = _parser()
    arguments = parser.parse_args(
        [
            "submit-one-attempt",
            "--runs",
            "/runs",
            "--admission-package",
            "/operator/admission.tessera.json",
            "--genesis",
            "/operator/genesis.tessera.json",
            "--root-public-key",
            "a" * 64,
        ]
    )

    assert arguments.command == "submit-one-attempt"
    assert not hasattr(arguments, "coder_command_arg")
