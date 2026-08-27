"""Tester-lane oracles for the PR23 repair: authenticated Codex API-key runner invocations.

Authored blind against the Coder lane. Expected values come only from the confirmed
adversarial-review findings (F1-F12), the runner-manifest schema, the public
signatures/docstrings of ``factory_runtime.runner``, ``factory_runtime.runner_failure``,
and the pre-repair convention baseline ``tests/test_runtime_runner.py`` at e778099.
No implementation body of ``factory_runtime/runner.py`` was read.

Each test docstring declares its finding, its guard classification against 809c674
(the subject under judgment), and the named production mutation that reddens it.

Fake-backend rule honored throughout: fakes preserve the real coupling under test.
The Codex contract modeled here is the one the repair itself targets: Codex resolves
its state directory from ``CODEX_HOME`` (default ``$HOME/.codex``), authenticates
API-key mode from ``auth.json`` inside that directory, and ``exec resume`` requires
the session rollout persisted under that same directory by the first invocation.
"""

from __future__ import annotations

import dataclasses
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

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
    RunnerManifest,
    RunnerProcessResult,
    RunnerQualification,
)
from factory_runtime.state_admission import derive_state_capsule, profile_digest

_OPENAI_KEY = "sk-factory-oracle-openai-0a1b2c3d4e5f"
_GENERIC_KEY = "named-secret-value"
_MARKER_NAME = "factory-oracle-rollout-marker.jsonl"


def _default_secret_value(name: str) -> str:
    return _OPENAI_KEY if name.startswith("OPENAI_") else _GENERIC_KEY


def _codex_home(environment: Mapping[str, str]) -> Path | None:
    """Resolve the Codex state directory exactly as Codex itself would."""

    explicit = environment.get("CODEX_HOME", "")
    if explicit:
        return Path(explicit)
    home = environment.get("HOME", "")
    if home:
        return Path(home) / ".codex"
    return None


class FakeBackend:
    """Convention-baseline fake backend (mirrors tests/test_runtime_runner.py@e778099)."""

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
            self.continuity_nonce = str(prompt["control"]["continuity"]["store_and_echo"])
        output: dict[str, Any] = {
            "kind": self.kinds[index],
            "role": environment["FACTORY_ROLE"],
            "projection_digest": projection_digest,
            "state_capsule_digest": state_capsule_digest,
            "sequence": index + 1,
            "continuity_nonce": self.continuity_nonce,
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


class CodexHomeBackend(FakeBackend):
    """Fake backend that preserves the real Codex state coupling.

    Invocation 1 persists a session-rollout marker under the Codex home it is
    given; when ``require_session_persistence`` is set, invocations 2-3 fail
    exactly the way real ``codex exec resume`` fails when its rollout state was
    wiped: the process exits nonzero. It also observes whether ``auth.json``
    was live inside the Codex home during each invocation.
    """

    def __init__(self, *, require_session_persistence: bool) -> None:
        super().__init__()
        self.require_session_persistence = require_session_persistence
        self.marker_written = False
        self.observations: list[dict[str, Any]] = []

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
        invocation = len(self.calls) + 1
        home = _codex_home(environment)
        observation: dict[str, Any] = {
            "invocation": invocation,
            "codex_home": None if home is None else str(home),
            "auth_present": bool(home is not None and (home / "auth.json").is_file()),
            # The parsed live content, not mere presence: codex 0.148.0 authenticates
            # only from exactly {"auth_mode": "apikey", "OPENAI_API_KEY": <key>}, so a
            # present-but-malformed file (placeholder key, wrong field name, empty
            # body) is an authentication failure the presence bit cannot see.
            "auth_content": (
                json.loads((home / "auth.json").read_text(encoding="utf-8"))
                if home is not None and (home / "auth.json").is_file()
                else None
            ),
            "marker_present": None,
        }
        failure = ""
        if home is None:
            failure = "codex has no CODEX_HOME (and no HOME) to resolve state from"
        else:
            marker = home / "sessions" / _MARKER_NAME
            if invocation == 1:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("factory-oracle session rollout\n", encoding="utf-8")
                self.marker_written = True
                observation["marker_present"] = True
            else:
                observation["marker_present"] = marker.is_file()
                if not observation["marker_present"]:
                    failure = "codex: session rollout missing from CODEX_HOME; cannot resume"
        self.observations.append(observation)
        result = super().run(
            command,
            cwd=cwd,
            readable_paths=readable_paths,
            writable_paths=writable_paths,
            environment=environment,
            stdin=stdin,
            limits=limits,
        )
        if failure and self.require_session_persistence:
            return dataclasses.replace(
                result,
                returncode=1,
                stderr=failure,
                structured_output={},
                termination_reason="exit-nonzero",
            )
        return result


def _schema(base: Path) -> Path:
    path = base / "handoff.schema.json"
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


def _build_fixture(
    base: Path,
    *,
    adapter: str = "codex",
    billing_key_name: str = "FACTORY_TEST_API_KEY",
    secrets: Mapping[str, str] | None = None,
    backend: FakeBackend | None = None,
) -> dict[str, Any]:
    base.mkdir(parents=True, exist_ok=True)
    backend = backend or FakeBackend()
    if secrets is None:
        secrets = {billing_key_name: _default_secret_value(billing_key_name) + "\n"}
    secret_root = base / "secrets"
    secret_root.mkdir()
    for name, content in secrets.items():
        secret = secret_root / name
        secret.write_text(content, encoding="utf-8")
        secret.chmod(0o600)
    projection = base / "projection.json"
    projection.write_text('{"scope":"coder-only"}\n', encoding="utf-8")
    schema = _schema(base)
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
        "billing_key_name": billing_key_name,
        "secret_names": sorted(secrets),
        "output_schema_digest": digest_bytes(schema.read_bytes()),
        "network_mode": "unrestricted-outbound",
        "limits": {
            "wall_seconds": 60,
            "idle_seconds": 10,
            "max_processes": 4,
            "max_attempts": 3,
            "max_output_bytes": 65_536,
            "max_tokens": 1_000,
            "max_cost_microusd": 1_000,
        },
        "pricing": {
            "input_microusd_per_million": 1_000_000,
            "output_microusd_per_million": 2_000_000,
        },
        "created_at": 100,
    }
    runner = HardenedModelRunner(
        backend=backend,
        secret_store=NamedSecretStore(secret_root),
        clock=lambda: 100,
        monotonic=lambda: 0.0,
    )
    forbidden = base / "target"
    forbidden.mkdir()
    workspace = base / "runner-workspace"
    return {
        "runner": runner,
        "backend": backend,
        "manifest": manifest,
        "projection": projection,
        "schema": schema,
        "workspace": workspace,
        "forbidden": forbidden,
        "base": base,
        "secret_root": secret_root,
    }


def _dispatch(
    fixture: Mapping[str, Any],
    *,
    attempt_observer: Callable[[int], None] | None = None,
) -> Any:
    manifest_bytes = json.dumps(
        fixture["manifest"], sort_keys=True, separators=(",", ":")
    ).encode()
    projection_bytes = fixture["projection"].read_bytes()
    schema_bytes = fixture["schema"].read_bytes()
    task_bytes = b"Implement the signed criterion"
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
    return fixture["runner"].dispatch(
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
        workspace_root=fixture["workspace"],
        forbidden_paths=(fixture["forbidden"],),
        attempt_observer=attempt_observer,
    )


def _auth_residue(root: Path, *, key: str, exclude: Sequence[Path] = ()) -> list[str]:
    """List every retained trace of Codex auth material under ``root``."""

    residue: list[str] = []
    key_bytes = key.encode("utf-8")
    excluded = list(exclude)
    for path in sorted(root.rglob("*")):
        if any(item == path or item in path.parents for item in excluded):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        if path.name == "auth.json":
            residue.append(f"auth file retained: {path}")
            continue
        try:
            data = path.read_bytes()
        except OSError:
            residue.append(f"unreadable retained file: {path}")
            continue
        if key_bytes in data:
            residue.append(f"key material retained: {path}")
    return residue


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("latin-1")
    return str(value)


def _argv_tuple(argv: Any) -> tuple[str, ...]:
    if isinstance(argv, str | bytes | os.PathLike):
        return (_as_text(argv),)
    return tuple(_as_text(item) for item in argv)


def _is_text_mode(kwargs: Mapping[str, Any]) -> bool:
    return bool(
        kwargs.get("text")
        or kwargs.get("universal_newlines")
        or kwargs.get("encoding")
        or kwargs.get("errors")
    )


class _RecordingTextSink(io.StringIO):
    def __init__(self, record: dict[str, Any]) -> None:
        super().__init__()
        self._record = record

    def write(self, s: str) -> int:
        self._record["input"] += _as_text(s)
        return super().write(s)


class _RecordingByteSink(io.BytesIO):
    def __init__(self, record: dict[str, Any]) -> None:
        super().__init__()
        self._record = record

    def write(self, b: Any) -> int:
        self._record["input"] += _as_text(bytes(b))
        return super().write(b)


class _FakePopen:
    """Minimal faithful stand-in for one host process the runner tried to spawn."""

    def __init__(self, recorder: _HostRecorder, record: dict[str, Any]) -> None:
        self._recorder = recorder
        self._record = record
        self._timed_out = False
        self.args = list(record["argv"])
        self.returncode: int | None = None
        self.pid = 424242
        if record["text"]:
            self.stdin: Any = _RecordingTextSink(record)
            self.stdout: Any = io.StringIO()
            self.stderr: Any = io.StringIO()
        else:
            self.stdin = _RecordingByteSink(record)
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()

    def _pair(self, out: str, err: str) -> tuple[Any, Any]:
        if self._record["text"]:
            return out, err
        return out.encode("utf-8"), err.encode("utf-8")

    def communicate(self, input: Any = None, timeout: float | None = None) -> tuple[Any, Any]:
        if input is not None:
            self._record["input"] += _as_text(input)
        if timeout is not None:
            self._record["timeout"] = timeout
        verdict = self._recorder.behavior(self._record)
        if verdict == "timeout" and timeout is not None and not self._timed_out:
            self._timed_out = True
            raise subprocess.TimeoutExpired(self.args, timeout)
        if verdict == "fail":
            self.returncode = 1
            return self._pair("", "injected bootstrap failure (oracle)")
        if isinstance(verdict, tuple):
            _, code, out, err = verdict
            self.returncode = int(code)
            return self._pair(out, err)
        self.returncode = -9 if self._timed_out else 0
        return self._pair("", "")

    def wait(self, timeout: float | None = None) -> int:
        if timeout is not None:
            self._record["timeout"] = timeout
        verdict = self._recorder.behavior(self._record)
        if verdict == "timeout" and timeout is not None and not self._timed_out:
            self._timed_out = True
            raise subprocess.TimeoutExpired(self.args, timeout)
        if self.returncode is None:
            if verdict == "fail":
                self.returncode = 1
            elif isinstance(verdict, tuple):
                self.returncode = int(verdict[1])
            else:
                self.returncode = -9 if self._timed_out else 0
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        if self.returncode is None:
            self.returncode = -15

    def kill(self) -> None:
        if self.returncode is None:
            self.returncode = -9

    def send_signal(self, signum: int) -> None:
        return None

    def __enter__(self) -> _FakePopen:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _HostRecorder:
    """Record every host-side process launch attempted outside the backend seam.

    The dispatch backend is already a fake, so any ``subprocess.run``/``Popen``
    reached during dispatch is by construction a bare host process that bypassed
    the qualified backend. ``behavior`` returns "ok", "fail", "timeout", or
    ("result", returncode, stdout, stderr) and is applied with real
    ``subprocess`` semantics (``check=`` raises ``CalledProcessError``,
    a finite ``timeout=`` raises ``TimeoutExpired`` for the "timeout" verdict).
    """

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        behavior: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.behavior = behavior or (lambda record: "ok")
        monkeypatch.setattr(subprocess, "run", self._run)
        monkeypatch.setattr(subprocess, "Popen", self._popen)

    def _record(self, seam: str, argv: Any, kwargs: Mapping[str, Any]) -> dict[str, Any]:
        env = kwargs.get("env")
        record: dict[str, Any] = {
            "seam": seam,
            "argv": _argv_tuple(argv),
            "env": dict(os.environ) if env is None else dict(env),
            "input": _as_text(kwargs.get("input")),
            "timeout": kwargs.get("timeout"),
            "check": bool(kwargs.get("check")),
            "text": _is_text_mode(kwargs),
        }
        self.calls.append(record)
        return record

    def _run(self, argv: Any, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        record = self._record("subprocess.run", argv, kwargs)
        verdict = self.behavior(record)
        code, out, err = 0, "", ""
        if verdict == "fail":
            code, err = 1, "injected bootstrap failure (oracle)"
        elif verdict == "timeout":
            if record["timeout"] is not None:
                raise subprocess.TimeoutExpired(list(record["argv"]), record["timeout"])
        elif isinstance(verdict, tuple):
            _, code, out, err = verdict
            code = int(code)
        stdout: Any
        stderr: Any
        if record["text"]:
            stdout, stderr = out, err
        else:
            stdout, stderr = out.encode("utf-8"), err.encode("utf-8")
        if record["check"] and code != 0:
            raise subprocess.CalledProcessError(
                code, list(record["argv"]), output=stdout, stderr=stderr
            )
        return subprocess.CompletedProcess(list(record["argv"]), code, stdout, stderr)

    def _popen(self, argv: Any, *args: Any, **kwargs: Any) -> _FakePopen:
        record = self._record("subprocess.Popen", argv, kwargs)
        return _FakePopen(self, record)


def _host_call_text(record: Mapping[str, Any]) -> str:
    parts = [" ".join(record["argv"])]
    parts.extend(_as_text(value) for value in record["env"].values())
    parts.append(record["input"])
    return "\n".join(parts)


def test_t1_codex_home_and_session_state_persist_across_dispatch_invocations(
    tmp_path: Path,
) -> None:
    """F1: Codex session state written under CODEX_HOME by invocation 1 must still be
    there for invocations 2-3 (``exec resume`` reads the rollout from the same home),
    and auth must never ride in the model process environment.

    [guard] red-now against 809c674: the per-invocation fresh-home-and-wipe design
    destroys the rollout marker before invocation 2 (the fake then fails exactly as
    real ``codex exec resume`` would), and/or the host-side bootstrap itself fails
    under fixture executables, so the dispatch cannot complete.

    Named mutation that reddens this test after repair: reintroduce per-invocation
    CODEX_HOME teardown/recreation (wipe or replace the Codex home between
    invocations). Separately, injecting OPENAI_API_KEY into the model environment
    reddens the environment assertions.
    """

    backend = CodexHomeBackend(require_session_persistence=True)
    fixture = _build_fixture(
        tmp_path / "t1", billing_key_name="OPENAI_API_KEY", backend=backend
    )

    handoff, _ = _dispatch(fixture)

    assert handoff["kind"] == "handoff"
    assert len(backend.calls) == 3
    assert backend.marker_written is True  # the persistence seam was actually exercised
    later = [item for item in backend.observations if item["invocation"] >= 2]
    assert [item["marker_present"] for item in later] == [True, True], backend.observations
    assert all(item["codex_home"] for item in backend.observations), backend.observations
    for call in backend.calls:
        environment = call["environment"]
        assert "OPENAI_API_KEY" not in environment
        assert _OPENAI_KEY not in "\n".join(map(str, environment.values()))


def test_t2_successful_dispatch_retains_no_codex_auth_material(tmp_path: Path) -> None:
    """F1/cleanup: after a fully successful dispatch no auth.json bytes and no raw
    key material remain anywhere in the retained tree (secret store excluded).

    [guard] judged red-now against 809c674: an OPENAI-billed codex dispatch cannot
    complete there under fixture executables (host-side bootstrap), so the success
    path this test requires does not exist; if 809c674's bootstrap needed no host
    process the per-invocation wipe would make this green-now instead. Either way it
    pins the post-repair cleanup invariant.

    Named mutation that reddens this test after repair: drop or skip the
    end-of-dispatch Codex-home cleanup so auth.json (or key bytes) stays behind;
    or write a present-but-non-authenticating auth.json (placeholder key, wrong
    field name, empty body) — the live-content assertion pins the exact shape
    codex 0.148.0 authenticates from, with the real resolved key value.
    """

    backend = CodexHomeBackend(require_session_persistence=False)
    fixture = _build_fixture(
        tmp_path / "t2", billing_key_name="OPENAI_API_KEY", backend=backend
    )

    handoff, _ = _dispatch(fixture)

    assert handoff["kind"] == "handoff"
    assert len(backend.calls) == 3
    # Reachability: authentication material must have been live during invocations —
    # otherwise this dispatch never exercised the API-key auth path at all.
    assert all(item["auth_present"] for item in backend.observations), backend.observations
    assert all(
        item["auth_content"] == {"auth_mode": "apikey", "OPENAI_API_KEY": _OPENAI_KEY}
        for item in backend.observations
    ), backend.observations
    residue = _auth_residue(
        fixture["base"], key=_OPENAI_KEY, exclude=(fixture["secret_root"],)
    )
    assert residue == []


def test_t3_bootstrap_failure_after_billed_attempt_keeps_honest_accounting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2: a host-side bootstrap failure occurring after a model attempt has been
    billed must surface with model_attempts >= 1, must not be classified as a
    pre-model refusal, and must leave retained failure evidence.

    The sabotage fails the first host process launched after at least one model
    invocation has run (the bootstrap before invocation 2 in a per-invocation
    design). In a once-per-dispatch repair no host process runs after invocation 1,
    the sabotage is structurally unreachable, and the dispatch must complete intact.

    [guard] red-now against 809c674: F2 confirms the bootstrap failure there is
    reported as a pre-model refusal with model_attempts == 0.

    Named mutation that reddens this test after repair: classify a post-attempt
    bootstrap/auth failure as PRE_MODEL_REFUSAL with model_attempts = 0, or skip
    failure-evidence retention for it, or swallow the failure and complete anyway.
    """

    backend = CodexHomeBackend(require_session_persistence=False)
    fired: list[dict[str, Any]] = []

    def fail_after_first_billed_attempt(record: dict[str, Any]) -> str:
        if len(backend.calls) >= 1:
            fired.append(record)
            return "fail"
        return "ok"

    _HostRecorder(monkeypatch, behavior=fail_after_first_billed_attempt)
    fixture = _build_fixture(
        tmp_path / "t3", billing_key_name="OPENAI_API_KEY", backend=backend
    )
    observed: list[int] = []
    error: RunnerError | None = None
    handoff = None
    try:
        handoff, _ = _dispatch(fixture, attempt_observer=observed.append)
    except RunnerError as exc:
        error = exc

    if error is None:
        # Once-per-dispatch design: no bootstrap exists between invocations, so the
        # sabotage must never have fired and the dispatch must be fully intact.
        assert handoff is not None and handoff["kind"] == "handoff"
        assert observed == [1, 2, 3]
        assert fired == []
        return

    assert fired, "dispatch failed before any sabotaged bootstrap call fired"
    assert observed and observed[0] == 1
    assert error.model_attempts >= 1
    if not isinstance(error, RunnerInvocationError):
        assert error.refusal_code != "PRE_MODEL_REFUSAL"
    receipt_path = Path(
        getattr(
            error,
            "failure_receipt_path",
            fixture["workspace"] / "runner-failure-receipt.json",
        )
    )
    assert receipt_path.is_file()


@pytest.mark.parametrize(
    ("adapter", "billing"),
    [
        ("codex", "OPENAI_API_KEY_PROD"),
        ("ollama-codex", "OPENAI_API_KEY"),
        ("codex", "FACTORY_TEST_API_KEY"),
        ("ollama-codex", "FACTORY_TEST_API_KEY"),
    ],
    ids=[
        "codex-openai-prod-name",
        "ollama-codex-openai-name",
        "codex-factory-name",
        "ollama-codex-factory-name",
    ],
)
def test_t4_codex_family_billing_is_bootstrapped_not_env_delivered(
    tmp_path: Path,
    adapter: str,
    billing: str,
) -> None:
    """F5 (founder ruling 2026-08-26, dual-ratified Jeremy McEntire + Validator): a codex-family
    adapter (codex, ollama-codex) authenticates ANY billing key via the CODEX_HOME/auth.json
    bootstrap. The key VALUE is written to auth.json and is NEVER present in the model process
    environment, REGARDLESS of billing_key_name — including OPENAI_API_KEY_PROD (codex) and
    OPENAI_API_KEY (ollama-codex), the two pairings the *dissolved* pre-ruling premise wrongly
    demanded be refused. Env-delivery of a codex billing key is non-functional exposure (codex
    authenticates only from auth.json), so acceptance-with-bootstrap is the security-correct end
    state and name-based refusal is the error the ruling reverses.

    [guard] red-now against the dissolved premise: a build that refuses codex-family + OPENAI_*
    pairings at manifest load (the pre-ruling / original-T4 behavior) reddens the
    accepted-dispatch assertions; a build that env-delivers the billing key to the model process
    reddens the key-absent assertions; a build that skips the codex auth-file bootstrap reddens
    ``auth_present``.

    Named mutation that reddens this test: (a) reintroduce a name-based refusal of codex-family +
    OPENAI_* billing keys at manifest load, or (b) inject the billing key (its name or value)
    into the model process environment instead of writing it to CODEX_HOME/auth.json, or
    (c) write a placeholder/wrong key value or a renamed field into auth.json — the
    live-content assertion requires the resolved billing key under codex's fixed field.
    """

    backend = CodexHomeBackend(require_session_persistence=False)
    fixture = _build_fixture(
        tmp_path / "accepted", adapter=adapter, billing_key_name=billing, backend=backend
    )

    handoff, _ = _dispatch(fixture)

    # Accepted: the pairing is not refused; the full dispatch completes.
    assert handoff["kind"] == "handoff"
    assert len(backend.calls) == 3
    # Never env-delivered to the network-capable (unrestricted-outbound) model process — for
    # either adapter, and for both the billing-key NAME and its VALUE.
    secret_value = _default_secret_value(billing)
    for call in backend.calls:
        environment = call["environment"]
        assert billing not in environment
        assert secret_value not in "\n".join(map(str, environment.values()))
    # Delivered via the auth-file bootstrap: auth.json must be live in the codex home the model
    # process authenticates from, for every invocation — otherwise the API-key auth path was
    # never exercised (reachability). Asserted for the direct-codex adapter, whose CODEX_HOME is
    # the model process's own home (observable here and pinned green by T1/T2); for ollama-codex
    # the auth home belongs to the launched Codex child, so the acceptance + no-env-delivery
    # reversal is what this case pins (bootstrap presence is covered by the codex cases + T2/T5).
    if adapter == "codex":
        assert all(item["auth_present"] for item in backend.observations), backend.observations
        # Content, not presence: the resolved billing key's VALUE must sit under codex's
        # fixed OPENAI_API_KEY auth.json field regardless of the billing_key_name that
        # named the secret — a placeholder value or a renamed field is an invisible
        # authentication break the presence bit alone would admit.
        assert all(
            item["auth_content"]
            == {"auth_mode": "apikey", "OPENAI_API_KEY": secret_value}
            for item in backend.observations
        ), backend.observations


def test_t4_env_delivery_to_nonbootstrapped_network_process_is_refused(
    tmp_path: Path,
) -> None:
    """F5 (founder ruling 2026-08-26, dual-ratified Jeremy McEntire + Validator): the refusal is
    about the DELIVERY MECHANISM, not billing-key NAMES. A network-capable (unrestricted-outbound)
    model process whose adapter has no auth-file bootstrap path may not be handed a billing
    secret; such a configuration is refused at manifest load with a typed RunnerError, before any
    model attempt. Raw-key env injection is never a fallback for an undeclared adapter.

    The runner-manifest schema pins network_mode to ``unrestricted-outbound`` (every loadable
    manifest is network-capable) and its adapter enum is exactly the codex family, so the refusal
    lives in two layers with different reachability:
      * SCHEMA layer (the presently-exercisable protection): a non-family adapter is refused at
        ``from_dict`` schema validation.
      * SEMANTIC delivery-mechanism guard (defense-in-depth for any future enum growth): reached
        only by DELIBERATELY bypassing schema validation via direct ``RunnerManifest``
        construction, where ``billing_authentication`` still fails closed for a non-family adapter
        rather than silently yielding an env-injection mode.
    A codex-family control loads and yields a declared bootstrap mode at the same boundary,
    isolating the refusal to the missing bootstrap path (not to manifest validity in general).

    [guard] red-now against a build that lets a non-bootstrapped, network-capable adapter fall
    through to dispatch (env injection) instead of refusing at load; red-now against a semantic
    guard that returns an env-delivery mode for an undeclared adapter instead of failing closed.

    Named mutation that reddens this test: (a) drop or widen the adapter-enum bound in
    runner-manifest.schema.json so a non-family adapter loads, or (b) make the billing-
    authentication mode resolver default to an env-injection mode for an undeclared/non-family
    adapter instead of raising.
    """

    # Control: a codex-family adapter has a declared auth-file bootstrap path and loads clean.
    control = _build_fixture(tmp_path / "control", adapter="codex")
    control_manifest = RunnerManifest.from_dict(control["manifest"])
    assert control_manifest.billing_authentication  # a declared mode, never fail-closed

    # Offending: a network-capable adapter with no bootstrap path (outside the codex family).
    offending = dict(control["manifest"])
    offending["adapter"] = "bare-network-model"

    # SEMANTIC guard (schema deliberately bypassed via direct construction): the delivery-
    # mechanism resolver fails closed for the non-family adapter instead of yielding an env mode.
    with pytest.raises(RunnerError):
        _ = RunnerManifest(offending).billing_authentication

    # SCHEMA layer (today's exercisable protection): manifest load refuses the non-family adapter
    # with a typed error.
    with pytest.raises(RunnerError):
        RunnerManifest.from_dict(offending)

    # End-to-end: dispatch refuses at load, before any model attempt or backend activity.
    backend = FakeBackend()
    dispatched = _build_fixture(tmp_path / "offending", adapter="codex", backend=backend)
    dispatched["manifest"]["adapter"] = "bare-network-model"
    with pytest.raises(RunnerError) as refused:
        _dispatch(dispatched)
    assert refused.value.model_attempts == 0
    assert backend.calls == []
    assert not hasattr(backend, "qualification_call")


def test_t5_raw_api_key_never_reaches_a_bare_host_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F3: the raw API key must never appear in the argv, stdin, or environment of
    any host-side process. The backend seam is already faked here, so every
    subprocess.run/Popen observed during dispatch is by construction a bare host
    process outside the qualified backend. The assertion targets the forbidden
    action itself, not any artifact a particular fix might produce.

    [guard] red-now against 809c674: the host-side login bootstrap receives the raw
    key (F3), so the leak scan reports it.

    Named mutation that reddens this test after repair: spawn any host process
    (e.g. ``codex login``) with the raw key in its argv, environment, or stdin.
    """

    host = _HostRecorder(monkeypatch)
    backend = FakeBackend()
    fixture = _build_fixture(
        tmp_path / "t5", billing_key_name="OPENAI_API_KEY", backend=backend
    )
    completed = False
    try:
        handoff, _ = _dispatch(fixture)
        completed = handoff["kind"] == "handoff"
    except RunnerError:
        completed = False

    leaks = [
        record for record in host.calls if _OPENAI_KEY in _host_call_text(record)
    ]
    assert leaks == [], [record["argv"] for record in leaks]
    # Reachability: the OPENAI-billed codex dispatch itself must succeed under the
    # repaired design; a refusal here would mean the auth path was never exercised.
    assert completed
    assert len(backend.calls) == 3


def test_t6_any_bootstrap_subprocess_enforces_a_typed_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F4: if a host bootstrap subprocess remains in the repaired design, it must
    carry a finite timeout, and expiry must surface as the typed error contract
    (RunnerError), never a wedge or a naked TimeoutExpired.

    The fake simulates the hang deterministically: a call that presents a finite
    timeout raises subprocess.TimeoutExpired exactly as the real API would; a call
    that presents no timeout is itself the defect and reddens the finite-timeout
    assertion (no real sleeping, so no wedge and no timing flake).

    Structural skip: when the dispatch completes without ever touching the host
    subprocess seam, the repaired design has no bootstrap subprocess and there is
    no timeout seam to test — that is asserted (completed handoff, zero host
    calls), not assumed.

    [guard] red-now against 809c674 if its bootstrap subprocess lacks a timeout
    (F4); structurally skipped only under a subprocess-free repair.

    Named mutation that reddens this test after repair: drop the timeout from the
    bootstrap subprocess call, or let TimeoutExpired escape without mapping it to
    the typed error contract.
    """

    host = _HostRecorder(monkeypatch, behavior=lambda record: "timeout")
    backend = FakeBackend()
    fixture = _build_fixture(
        tmp_path / "t6", billing_key_name="OPENAI_API_KEY", backend=backend
    )
    error: BaseException | None = None
    handoff = None
    try:
        handoff, _ = _dispatch(fixture)
    except Exception as exc:  # noqa: BLE001 - an untyped TimeoutExpired must fail below
        error = exc

    if not host.calls:
        assert error is None
        assert handoff is not None and handoff["kind"] == "handoff"
        pytest.skip(
            "structural: repaired dispatch performs no host bootstrap subprocess; "
            "no timeout seam exists"
        )

    for record in host.calls:
        assert record["timeout"] is not None and float(record["timeout"]) > 0, record
    assert isinstance(error, RunnerError), error


def test_t7_cleanup_failure_is_loud_or_provably_leaves_no_auth_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F8: a failed auth cleanup must never produce a silent green. Either the
    dispatch surfaces a typed error (loud), or the retained tree verifiably holds
    no auth material. The removal primitives (shutil.rmtree; os.unlink/os.remove of
    any file named auth.json) are forced to fail, so a swallowed cleanup exception
    necessarily leaves the auth bytes behind for the residue scan to find.

    [guard] red-now against 809c674: F8 confirms the cleanup failure is swallowed
    there, and with removal blocked the auth material stays in the tree while the
    dispatch reports success.

    Named mutation that reddens this test after repair: wrap the cleanup in
    ``except OSError: pass`` (or equivalent) so a failed removal is silently
    ignored while auth.json remains retained.
    """

    host = _HostRecorder(monkeypatch)  # any 809c674 host bootstrap "succeeds"
    removal_attempts: list[str] = []

    def failing_rmtree(path: Any, *args: Any, **kwargs: Any) -> None:
        removal_attempts.append(str(path))
        raise OSError("injected cleanup failure (oracle T7)")

    real_unlink = os.unlink

    def guarded_unlink(path: Any, *args: Any, **kwargs: Any) -> None:
        if Path(path).name == "auth.json":
            removal_attempts.append(str(path))
            raise OSError("injected cleanup failure (oracle T7)")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", failing_rmtree)
    monkeypatch.setattr(os, "unlink", guarded_unlink)
    monkeypatch.setattr(os, "remove", guarded_unlink)
    ambient_tmp = tmp_path / "ambient-tmp"
    ambient_tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(ambient_tmp))

    backend = CodexHomeBackend(require_session_persistence=False)
    fixture = _build_fixture(
        tmp_path / "t7", billing_key_name="OPENAI_API_KEY", backend=backend
    )
    error: RunnerError | None = None
    try:
        _dispatch(fixture)
    except RunnerError as exc:
        error = exc

    assert host.calls is not None  # recorder installed; host bootstrap (if any) succeeded
    if error is not None:
        # Loud path: the retention hazard surfaced as a typed error after the model
        # actually ran — not as an unrelated pre-model refusal.
        assert len(backend.calls) >= 1
        return
    residue = _auth_residue(
        tmp_path, key=_OPENAI_KEY, exclude=(fixture["secret_root"],)
    )
    assert residue == [], residue


@pytest.mark.parametrize("reserved", ["CODEX_HOME", "HOME"])
def test_t8_reserved_environment_names_are_refused_as_secrets(
    tmp_path: Path,
    reserved: str,
) -> None:
    """F10: a manifest naming CODEX_HOME or HOME as a secret must be refused with a
    typed pre-model error before any filesystem mutation, and no host path derived
    from a secret VALUE may ever be created or deleted (negative control: the
    canary directory the reserved secret points at stays untouched).

    The control dispatch (identical manifest without the reserved name) proves the
    pipeline is otherwise green, isolating the reserved name as the refusal cause.

    [guard] red-now against 809c674: both names pass the schema's env-name pattern
    and are injected as ordinary named secrets, so the dispatch completes and no
    refusal is raised.

    Named mutation that reddens this test after repair: accept reserved
    environment names as named secrets (injecting them into the model environment
    or deriving Codex-home paths from their values).
    """

    canary = tmp_path / "canary"
    canary.mkdir()
    sentinel = canary / "sentinel.txt"
    sentinel.write_text("untouched\n", encoding="utf-8")

    control_backend = FakeBackend()
    control = _build_fixture(tmp_path / "control", backend=control_backend)
    control_handoff, _ = _dispatch(control)
    assert control_handoff["kind"] == "handoff"

    backend = FakeBackend()
    fixture = _build_fixture(
        tmp_path / "offending",
        secrets={
            "FACTORY_TEST_API_KEY": _GENERIC_KEY + "\n",
            reserved: str(canary) + "\n",
        },
        backend=backend,
    )
    refused: RunnerError | None = None
    try:
        _dispatch(fixture)
    except RunnerError as exc:
        refused = exc

    assert canary.is_dir() and not canary.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "untouched\n"
    assert sorted(item.name for item in canary.iterdir()) == ["sentinel.txt"]
    assert refused is not None, "reserved secret name was accepted instead of refused"
    assert refused.model_attempts == 0
    assert backend.calls == []
    assert not hasattr(backend, "qualification_call")
    assert not fixture["workspace"].exists()  # refused before any filesystem mutation


def test_t9_secret_resolution_rejects_control_characters_and_multiline_content(
    tmp_path: Path,
) -> None:
    """F12: NamedSecretStore.resolve must reject a secret file containing a carriage
    return ("sk-x\\r\\n") and one containing multiple lines ("line1\\nline2\\n") with a
    typed error — never silently truncate them. The clean control pins the resolve
    contract from the convention baseline (single trailing newline stripped) and
    proves the store and names are otherwise resolvable, so each rejection is
    attributable to the file content alone.

    [guard] red-now against 809c674: both files resolve silently (truncated) and no
    typed error is raised.

    Named mutation that reddens this test after repair: strip or truncate CR/LF or
    multiline secret content in resolve instead of rejecting it.
    """

    root = tmp_path / "secrets"
    root.mkdir()

    def write(name: str, content: bytes) -> None:
        path = root / name
        path.write_bytes(content)
        path.chmod(0o600)

    write("CLEAN_KEY", b"sk-clean\n")
    write("CRLF_KEY", b"sk-x\r\n")
    write("MULTILINE_KEY", b"line1\nline2\n")
    store = NamedSecretStore(root)

    assert store.resolve(["CLEAN_KEY"]) == {"CLEAN_KEY": "sk-clean"}
    with pytest.raises(RunnerError):
        store.resolve(["CRLF_KEY"])
    with pytest.raises(RunnerError):
        store.resolve(["MULTILINE_KEY"])


def test_t10_rejected_bootstrap_diagnostic_is_bounded_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F7: when the auth bootstrap is rejected, the typed error must carry a bounded
    diagnostic containing an excerpt of the rejection stderr but never the key
    material. The fake host process rejects with a stderr that includes a unique
    marker, the raw key, and ~100KB of padding; the surfaced error text (message,
    validator diagnostic, failure receipt) must include the marker, exclude the
    key, and stay bounded.

    Structural skip: when the dispatch completes without touching the host
    subprocess seam, no bootstrap rejection path exists — asserted, not assumed.

    [guard] red-now against 809c674 per F7 (the rejection either drops the stderr
    excerpt or surfaces it unredacted/unbounded).

    Named mutation that reddens this test after repair: surface the raw bootstrap
    stderr (key included) in the typed error, or discard the stderr excerpt from
    the diagnostic entirely, or drop the size bound.
    """

    marker = "E401-ORACLE-REJECTION-MARKER"
    stderr_text = (
        f"codex login rejected: {marker} bad api key {_OPENAI_KEY} " + "x" * 100_000
    )
    host = _HostRecorder(
        monkeypatch, behavior=lambda record: ("result", 1, "", stderr_text)
    )
    backend = FakeBackend()
    fixture = _build_fixture(
        tmp_path / "t10", billing_key_name="OPENAI_API_KEY", backend=backend
    )
    error: RunnerError | None = None
    handoff = None
    try:
        handoff, _ = _dispatch(fixture)
    except RunnerError as exc:
        error = exc

    if not host.calls:
        assert error is None
        assert handoff is not None and handoff["kind"] == "handoff"
        pytest.skip(
            "structural: repaired dispatch performs no host bootstrap subprocess; "
            "no rejection path exists"
        )

    assert error is not None, "rejected bootstrap did not surface a typed error"
    surfaced = [str(error)]
    diagnostic_path = getattr(error, "diagnostic_path", None)
    if diagnostic_path is not None and Path(diagnostic_path).is_file():
        surfaced.append(Path(diagnostic_path).read_text(encoding="utf-8"))
    failure_receipt = getattr(error, "failure_receipt", None)
    if failure_receipt:
        surfaced.append(json.dumps(failure_receipt, sort_keys=True))
    combined = "\n".join(surfaced)
    assert marker in combined
    assert _OPENAI_KEY not in combined
    assert len(combined.encode("utf-8")) <= 65_536
