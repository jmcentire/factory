"""Closed-environment outer model runner with canary and resume qualification.

This boundary deliberately exposes no target/control-root path to the model process.  A role
projection and output schema are copied into a fresh private workspace; target effects are
represented as typed broker requests in the structured handoff and executed elsewhere.
"""

from __future__ import annotations

import json
import os
import re
import secrets as secure_random
import stat
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import jsonschema

from factory_core.manifest import digest_bytes, digest_obj
from factory_runtime.schema import DocumentValidationError, validate_document

_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_MAX_SECRET_BYTES = 65_536
_MAX_PROMPT_BYTES = 2_097_152


class RunnerError(ValueError):
    """A model dispatch could not satisfy the hardened execution contract."""


@dataclass(frozen=True)
class RunnerLimits:
    wall_seconds: int
    idle_seconds: int
    max_processes: int
    max_attempts: int
    max_output_bytes: int
    max_tokens: int
    max_cost_microusd: int


@dataclass(frozen=True)
class RunnerQualification:
    backend: str
    scope_digest: str
    forbidden_read_denied: bool
    forbidden_write_denied: bool
    model_network_available: bool
    arbitrary_shell_denied: bool
    process_containment: bool

    @property
    def satisfied(self) -> bool:
        return all(
            (
                self.forbidden_read_denied,
                self.forbidden_write_denied,
                self.model_network_available,
                self.arbitrary_shell_denied,
                self.process_containment,
            )
        )

    @property
    def content_digest(self) -> str:
        return digest_obj(
            {
                "backend": self.backend,
                "scope_digest": self.scope_digest,
                "forbidden_read_denied": self.forbidden_read_denied,
                "forbidden_write_denied": self.forbidden_write_denied,
                "model_network_available": self.model_network_available,
                "arbitrary_shell_denied": self.arbitrary_shell_denied,
                "process_containment": self.process_containment,
            }
        )


@dataclass(frozen=True)
class RunnerProcessResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    structured_output: Mapping[str, Any]
    session_id: str
    input_tokens: int
    output_tokens: int
    process_peak: int
    termination_reason: str


class NetworkedRunnerBackend(Protocol):
    def qualify(
        self,
        root: str | Path,
        *,
        allowed_executables: Sequence[str | Path],
        forbidden_paths: Sequence[str | Path],
    ) -> RunnerQualification: ...

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
    ) -> RunnerProcessResult: ...


@dataclass(frozen=True)
class RunnerManifest:
    document: Mapping[str, Any]

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> RunnerManifest:
        try:
            validate_document("runner-manifest", document)
        except DocumentValidationError as exc:
            raise RunnerError(str(exc)) from exc
        if document["billing_key_name"] not in document["secret_names"]:
            raise RunnerError("runner billing key must be one of the named secrets")
        children = tuple(document["child_executables"])
        if document["adapter"] == "codex" and children:
            raise RunnerError("direct Codex runner may not declare child executables")
        if document["adapter"] == "ollama-codex" and len(children) != 1:
            raise RunnerError("Ollama-to-Codex runner requires exactly one Codex child")
        return cls(dict(document))

    @property
    def content_digest(self) -> str:
        return digest_obj(dict(self.document))

    @property
    def limits(self) -> RunnerLimits:
        return RunnerLimits(**dict(self.document["limits"]))


@dataclass(frozen=True)
class RunnerReceipt:
    document: Mapping[str, Any]

    @property
    def content_digest(self) -> str:
        return digest_obj(dict(self.document))


class NamedSecretStore:
    """Resolve only manifest-named secret files; never consult ambient environment values."""

    def __init__(self, root: str | Path) -> None:
        supplied = Path(root)
        if supplied.is_symlink():
            raise RunnerError("named-secret root may not be a symlink")
        try:
            resolved = supplied.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise RunnerError("named-secret root is unavailable") from exc
        if not resolved.is_dir():
            raise RunnerError("named-secret root is not a directory")
        self.root = resolved

    def resolve(self, names: Sequence[str]) -> dict[str, str]:
        values: dict[str, str] = {}
        for name in names:
            if not _ENV_NAME.fullmatch(name):
                raise RunnerError(f"invalid named secret: {name!r}")
            path = self.root / name
            flags = (
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0)
            )
            try:
                descriptor = os.open(path, flags)
            except OSError as exc:
                raise RunnerError(f"named secret is unavailable: {name}: {exc}") from exc
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise RunnerError(f"named secret is not regular: {name}")
                mode = stat.S_IMODE(metadata.st_mode)
                if mode & 0o077:
                    raise RunnerError(f"named secret must not be group/world accessible: {name}")
                with os.fdopen(descriptor, "rb") as stream:
                    descriptor = -1
                    raw = stream.read(_MAX_SECRET_BYTES + 1)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            if not raw or len(raw) > _MAX_SECRET_BYTES or b"\x00" in raw:
                raise RunnerError(f"named secret has invalid bounded content: {name}")
            try:
                values[name] = raw.decode("utf-8").removesuffix("\n")
            except UnicodeDecodeError as exc:
                raise RunnerError(f"named secret is not UTF-8: {name}") from exc
        return values


class CodexRunnerAdapter:
    """Construct fixed Codex or `ollama launch codex` argv without a shell."""

    def __init__(
        self,
        manifest: RunnerManifest,
        *,
        executable: Path,
        output_schema: Path,
    ) -> None:
        self.manifest = manifest
        self.executable = executable
        self.output_schema = output_schema

    def command(self, *, output: Path, session_id: str = "") -> tuple[str, ...]:
        document = self.manifest.document
        common = (
            "--json",
            "--model",
            str(document["model"]),
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--output-schema",
            str(self.output_schema),
            "--output-last-message",
            str(output),
        )
        if document["adapter"] == "codex":
            if session_id:
                return (
                    str(self.executable),
                    "exec",
                    "resume",
                    *common,
                    session_id,
                    "-",
                )
            return (
                str(self.executable),
                "exec",
                "--sandbox",
                "read-only",
                *common,
                "-",
            )
        codex_args = ("exec", "resume", *common) if session_id else ("exec", *common)
        if not session_id:
            codex_args = (*codex_args, "--sandbox", "read-only")
        if session_id:
            codex_args = (*codex_args, session_id)
        return (
            str(self.executable),
            "launch",
            "codex",
            "--model",
            str(document["model"]),
            "--",
            *codex_args,
            "-",
        )


def _regular_executable(path: str | Path) -> Path:
    resolved = Path(path).resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise RunnerError(f"runner executable is unavailable: {resolved}")
    return resolved


def _regular_bytes(path: str | Path, *, label: str) -> bytes:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise RunnerError(f"{label} is missing, not regular, or a symlink")
    return source.read_bytes()


def _write_once(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _cost(manifest: RunnerManifest, input_tokens: int, output_tokens: int) -> int | None:
    pricing = manifest.document["pricing"]
    input_rate = pricing["input_microusd_per_million"]
    output_rate = pricing["output_microusd_per_million"]
    if input_rate is None or output_rate is None:
        return None
    numerator = input_tokens * int(input_rate) + output_tokens * int(output_rate)
    return (numerator + 999_999) // 1_000_000


def _closed_output_schema(raw: bytes) -> Mapping[str, Any]:
    try:
        schema = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RunnerError("runner output schema is not JSON") from exc
    if not isinstance(schema, Mapping):
        raise RunnerError("runner output schema is not an object")
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise RunnerError(f"runner output schema is invalid: {exc.message}") from exc

    def inspect(value: Any) -> None:
        if isinstance(value, Mapping):
            reference = value.get("$ref")
            if isinstance(reference, str) and not reference.startswith("#"):
                raise RunnerError("runner output schema may not resolve an external reference")
            for child in value.values():
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)

    inspect(schema)
    return schema


class HardenedModelRunner:
    """Run two canaries, prove same-session resume, then dispatch the real projection."""

    def __init__(
        self,
        *,
        backend: NetworkedRunnerBackend,
        secret_store: NamedSecretStore,
        clock: Callable[[], int] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.backend = backend
        self.secret_store = secret_store
        self._clock = clock or (lambda: int(time.time()))
        self._monotonic = monotonic or time.monotonic

    def dispatch(
        self,
        *,
        run_id: str,
        generation: int,
        receipt_id: str,
        manifest_document: Mapping[str, Any],
        projection_path: str | Path,
        output_schema_path: str | Path,
        task: str,
        workspace_root: str | Path,
        forbidden_paths: Sequence[str | Path],
    ) -> tuple[Mapping[str, Any], RunnerReceipt]:
        manifest = RunnerManifest.from_dict(manifest_document)
        limits = manifest.limits
        if limits.max_attempts < 3:
            raise RunnerError("runner requires two canaries plus one task attempt")
        output_schema_bytes = _regular_bytes(output_schema_path, label="runner output schema")
        if digest_bytes(output_schema_bytes) != manifest.document["output_schema_digest"]:
            raise RunnerError("runner output schema differs from the manifest")
        output_schema_document = _closed_output_schema(output_schema_bytes)
        projection_bytes = _regular_bytes(projection_path, label="runner projection")
        projection_digest = digest_bytes(projection_bytes)
        try:
            projection_document = json.loads(projection_bytes)
        except json.JSONDecodeError as exc:
            raise RunnerError("runner projection is not JSON") from exc
        if not isinstance(projection_document, Mapping):
            raise RunnerError("runner projection must be a JSON object")
        projection_payload = json.dumps(
            projection_document,
            sort_keys=True,
            separators=(",", ":"),
        )
        executable = _regular_executable(str(manifest.document["executable"]))
        child_executables = tuple(
            _regular_executable(path) for path in manifest.document["child_executables"]
        )
        workspace = Path(workspace_root)
        if workspace.exists() or workspace.is_symlink():
            raise RunnerError("runner workspace must be fresh and absent")
        input_root = workspace / "input"
        output_root = workspace / "output"
        home = workspace / "home"
        temporary = workspace / "tmp"
        for directory in (input_root, output_root, home, temporary):
            directory.mkdir(parents=True, exist_ok=False)
        projection = input_root / "projection.json"
        output_schema = input_root / "output-schema.json"
        projection.write_bytes(projection_bytes)
        output_schema.write_bytes(output_schema_bytes)

        secrets = self.secret_store.resolve(tuple(manifest.document["secret_names"]))
        path_entries = {str(executable.parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin"}
        environment = {
            "HOME": str(home),
            "CODEX_HOME": str(home / "codex"),
            "TMPDIR": str(temporary),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": ":".join(sorted(path_entries)),
            "FACTORY_ROLE": str(manifest.document["role"]),
            "FACTORY_PROJECTION_DIGEST": projection_digest,
            **secrets,
        }
        qualification = self.backend.qualify(
            workspace / "qualification",
            allowed_executables=(executable, *child_executables),
            forbidden_paths=forbidden_paths,
        )
        if not qualification.satisfied:
            raise RunnerError("networked runner backend did not satisfy its qualification")
        max_cost = limits.max_cost_microusd
        if max_cost and _cost(manifest, 0, 0) is None:
            raise RunnerError("runner cost ceiling cannot use unknown pricing as zero")

        adapter = CodexRunnerAdapter(
            manifest,
            executable=executable,
            output_schema=output_schema,
        )
        started_at = self._clock()
        objective_started = self._monotonic()
        results: list[RunnerProcessResult] = []
        session_id = ""
        continuity_nonce = secure_random.token_hex(32)
        prompts = (
            self._canary_prompt(manifest, projection_digest, 1, continuity_nonce),
            self._canary_prompt(manifest, projection_digest, 2, ""),
            self._task_prompt(manifest, projection_digest, projection_payload, task),
        )
        for index, prompt in enumerate(prompts, start=1):
            if len(prompt.encode("utf-8")) > _MAX_PROMPT_BYTES:
                raise RunnerError("runner prompt exceeds the bounded input size")
            elapsed = self._monotonic() - objective_started
            remaining_wall = limits.wall_seconds - int(elapsed)
            if remaining_wall <= 0:
                raise RunnerError("runner objective wall-time ceiling was exceeded")
            attempt_limits = RunnerLimits(
                wall_seconds=remaining_wall,
                idle_seconds=min(limits.idle_seconds, remaining_wall),
                max_processes=limits.max_processes,
                max_attempts=limits.max_attempts,
                max_output_bytes=limits.max_output_bytes,
                max_tokens=limits.max_tokens,
                max_cost_microusd=limits.max_cost_microusd,
            )
            output = output_root / f"attempt-{index}.json"
            command = adapter.command(output=output, session_id=session_id if index > 1 else "")
            result = self.backend.run(
                command,
                cwd=workspace,
                readable_paths=(output_schema, executable, *child_executables),
                writable_paths=(output_root, home, temporary),
                environment=environment,
                stdin=prompt.encode("utf-8"),
                limits=attempt_limits,
            )
            self._require_process_success(result, limits)
            self._require_no_secret_leak(result, tuple(secrets.values()))
            if index == 1:
                session_id = result.session_id
                if not session_id:
                    raise RunnerError("runner canary produced no resumable session id")
            elif result.session_id != session_id:
                raise RunnerError("runner did not resume the exact canary session")
            self._require_output(
                result.structured_output,
                manifest=manifest,
                projection_digest=projection_digest,
                sequence=index,
                continuity_nonce=continuity_nonce,
                output_schema=output_schema_document,
            )
            results.append(result)
            self._enforce_meter(manifest, results)

        handoff = dict(results[-1].structured_output)
        handoff_bytes = json.dumps(
            handoff,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        _write_once(output_root / "handoff.json", handoff_bytes + b"\n")
        input_tokens = sum(result.input_tokens for result in results)
        output_tokens = sum(result.output_tokens for result in results)
        cost = _cost(manifest, input_tokens, output_tokens)
        document = {
            "schema_version": "factory-runner-receipt/1",
            "receipt_id": receipt_id,
            "run_id": run_id,
            "generation": generation,
            "role": manifest.document["role"],
            "runner_manifest_digest": manifest.content_digest,
            "runner_id": manifest.document["runner_id"],
            "adapter": manifest.document["adapter"],
            "executable_digest": digest_bytes(executable.read_bytes()),
            "runner_version": manifest.document["runner_version"],
            "model": manifest.document["model"],
            "model_version": manifest.document["model_version"],
            "configuration_digest": manifest.document["configuration_digest"],
            "billing_key_name": manifest.document["billing_key_name"],
            "secret_names": list(manifest.document["secret_names"]),
            "qualification_digest": qualification.content_digest,
            "canary_session_id": session_id,
            "resumed_session_id": results[-1].session_id,
            "continuity_nonce_digest": digest_obj(
                {"continuity_nonce": continuity_nonce}
            ),
            "canary_attempts": 2,
            "task_attempt": 3,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_microusd": cost,
            "cost_known": cost is not None,
            "meter_semantics": "observed-post-call",
            "process_peak": max(result.process_peak for result in results),
            "termination_reason": "completed",
            "handoff_digest": digest_bytes(handoff_bytes),
            "started_at": started_at,
            "finished_at": self._clock(),
        }
        try:
            validate_document("runner-receipt", document)
        except DocumentValidationError as exc:
            raise RunnerError(str(exc)) from exc
        receipt = RunnerReceipt(document)
        _write_once(
            output_root / "runner-receipt.json",
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n",
        )
        return handoff, receipt

    @staticmethod
    def _canary_prompt(
        manifest: RunnerManifest,
        projection_digest: str,
        sequence: int,
        continuity_nonce: str,
    ) -> str:
        return json.dumps(
            {
                "schema_version": "factory-runner-prompt/1",
                "kind": "qualification",
                "control": {
                    "response": "configured-json-only",
                    "expected_kind": "canary",
                    "role": manifest.document["role"],
                    "projection_digest": projection_digest,
                    "sequence": sequence,
                    "continuity": (
                        {"store_and_echo": continuity_nonce}
                        if sequence == 1
                        else {"recall_and_echo_from_prior_turn": True}
                    ),
                    "target_effects": "forbidden",
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _task_prompt(
        manifest: RunnerManifest,
        projection_digest: str,
        projection_payload: str,
        task: str,
    ) -> str:
        return json.dumps(
            {
                "schema_version": "factory-runner-prompt/1",
                "kind": "task",
                "control": {
                    "response": "configured-json-only",
                    "expected_kind": "handoff",
                    "role": manifest.document["role"],
                    "projection_digest": projection_digest,
                    "sequence": 3,
                    "continuity": {"recall_and_echo_from_first_turn": True},
                    "effect_boundary": "typed-broker-requests-only",
                    "authority_shaped_model_fields": "forbidden",
                },
                "data": {
                    "projection": json.loads(projection_payload),
                    "task": task,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _require_process_success(result: RunnerProcessResult, limits: RunnerLimits) -> None:
        if result.returncode != 0 or result.termination_reason != "completed":
            raise RunnerError(
                f"runner process failed closed: {result.termination_reason} / {result.returncode}"
            )
        if result.process_peak > limits.max_processes:
            raise RunnerError("runner process-tree ceiling was exceeded")
        if len(result.stdout.encode("utf-8")) + len(result.stderr.encode("utf-8")) > (
            limits.max_output_bytes
        ):
            raise RunnerError("runner output ceiling was exceeded")
        if not result.structured_output:
            raise RunnerError("runner produced no structured evidence artifact")

    @staticmethod
    def _require_no_secret_leak(
        result: RunnerProcessResult,
        secret_values: Sequence[str],
    ) -> None:
        serialized = json.dumps(result.structured_output, sort_keys=True)
        visible = "\n".join((result.stdout, result.stderr, serialized))
        for value in secret_values:
            if value and value in visible:
                raise RunnerError("runner output contained a named secret value")

    @staticmethod
    def _require_output(
        output: Mapping[str, Any],
        *,
        manifest: RunnerManifest,
        projection_digest: str,
        sequence: int,
        continuity_nonce: str,
        output_schema: Mapping[str, Any],
    ) -> None:
        expected = {
            "kind": "handoff" if sequence == 3 else "canary",
            "role": manifest.document["role"],
            "projection_digest": projection_digest,
            "sequence": sequence,
        }
        for field, value in expected.items():
            if output.get(field) != value:
                raise RunnerError(f"runner structured output has wrong {field}")
        if output.get("continuity_nonce") != continuity_nonce:
            raise RunnerError("runner did not prove same-session continuity")
        try:
            validate_document("runner-output", output)
        except DocumentValidationError as exc:
            raise RunnerError(f"runner output violates the code-owned schema: {exc}") from exc
        errors = sorted(
            jsonschema.Draft202012Validator(output_schema).iter_errors(dict(output)),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if errors:
            first = errors[0]
            location = "/".join(str(part) for part in first.absolute_path) or "<root>"
            raise RunnerError(
                f"runner structured output violates its signed schema at {location}: "
                f"{first.message}"
            )

    @staticmethod
    def _enforce_meter(
        manifest: RunnerManifest,
        results: Sequence[RunnerProcessResult],
    ) -> None:
        limits = manifest.limits
        input_tokens = sum(result.input_tokens for result in results)
        output_tokens = sum(result.output_tokens for result in results)
        if input_tokens + output_tokens > limits.max_tokens:
            raise RunnerError("runner token ceiling was exceeded")
        cost = _cost(manifest, input_tokens, output_tokens)
        if limits.max_cost_microusd and (
            cost is None or cost > limits.max_cost_microusd
        ):
            raise RunnerError("runner monetary ceiling was exceeded or unprovable")
