"""Closed-environment outer model runner with canary and resume qualification.

This boundary deliberately exposes no target/control-root path to the model process.  A role
projection and output schema are copied into a fresh private workspace; target effects are
represented as typed broker requests in the structured handoff and executed elsewhere.
"""

from __future__ import annotations

import hashlib
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
from factory_core.provenance import PhaseArtifact
from factory_runtime.durability import fsync_directory
from factory_runtime.failure_classification import FailureCapsule, classify_terminal_failure
from factory_runtime.instruction_control import validate_directive_readback
from factory_runtime.runner_termination import COMPLETED, SUPERVISOR_ERROR
from factory_runtime.schema import DocumentValidationError, validate_document
from factory_runtime.state_admission import (
    StateAdmissionError,
    profile_digest,
    verify_state_capsule,
)

_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_MAX_SECRET_BYTES = 65_536
_MAX_PROMPT_BYTES = 2_097_152
_PROMPT_SCHEMA_VERSION = "factory-runner-prompt/3"
_PROMPT_ASSEMBLER_VERSION = "factory-runner-prompt-assembler/2"
_MAX_DIAGNOSTIC_STREAM_BYTES = 16_384

# Environment names the dispatch composes itself from host-owned paths and control
# metadata.  A named secret may never shadow one of them: secrets merge into the model
# environment after the host entries, so a secret literally called HOME or CODEX_HOME
# would redirect host filesystem mutation to a path chosen by secret file content.
_RESERVED_ENVIRONMENT_NAMES = frozenset(
    {
        "HOME",
        "CODEX_HOME",
        "PATH",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "FACTORY_ROLE",
        "FACTORY_PROJECTION_DIGEST",
        "FACTORY_STATE_CAPSULE_DIGEST",
    }
)


class RunnerError(ValueError):
    """A model dispatch could not satisfy the hardened execution contract."""

    def __init__(
        self,
        message: str,
        *,
        model_attempts: int = 0,
        refusal_code: str = "PRE_MODEL_REFUSAL",
        dependency_id: str = "",
        invocation_result: RunnerProcessResult | None = None,
    ) -> None:
        super().__init__(message)
        if model_attempts < 0:
            raise ValueError("model_attempts must be non-negative")
        self.model_attempts = model_attempts
        self.refusal_code = refusal_code
        self.dependency_id = dependency_id
        self.invocation_result = invocation_result

    def after_attempt(self, attempt: int) -> RunnerError:
        """Return the same refusal classified with its real model-call count."""

        return RunnerError(
            str(self),
            model_attempts=max(self.model_attempts, attempt),
            refusal_code=self.refusal_code,
            dependency_id=self.dependency_id,
            invocation_result=self.invocation_result,
        )


class RunnerInvocationError(RunnerError):
    """A failed invocation with private diagnostics and a safe disposition."""

    def __init__(
        self,
        message: str,
        *,
        diagnostic_path: Path,
        failure_receipt_path: Path,
        failure_receipt: Mapping[str, Any],
        failure_capsule: FailureCapsule,
        model_attempts: int,
    ) -> None:
        super().__init__(message, model_attempts=model_attempts)
        self.diagnostic_path = diagnostic_path
        self.failure_receipt_path = failure_receipt_path
        self.failure_receipt = dict(failure_receipt)
        self.failure_capsule = failure_capsule


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
    def document(self) -> Mapping[str, Any]:
        return {
            "backend": self.backend,
            "scope_digest": self.scope_digest,
            "forbidden_read_denied": self.forbidden_read_denied,
            "forbidden_write_denied": self.forbidden_write_denied,
            "model_network_available": self.model_network_available,
            "arbitrary_shell_denied": self.arbitrary_shell_denied,
            "process_containment": self.process_containment,
        }

    @property
    def content_digest(self) -> str:
        return digest_obj(self.document)


@dataclass(frozen=True)
class RunnerExecutableSnapshot:
    path: Path
    byte_count: int
    content_digest: str

    def evidence(self, *, workspace: Path) -> Mapping[str, Any]:
        return {
            "relative_path": self.path.relative_to(workspace).as_posix(),
            "byte_count": self.byte_count,
            "content_digest": self.content_digest,
        }


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
        if document.get("schema_version") == "factory-runner-manifest/1":
            raise RunnerError(
                "legacy runner manifest cannot dispatch after state-capsule cutover; "
                "explicitly abandon the legacy run and start a v2 run from a new verified "
                "checkpoint",
                refusal_code="LEGACY_RUNNER_MANIFEST",
            )
        try:
            validate_document("runner-manifest", document)
        except DocumentValidationError as exc:
            raise RunnerError(str(exc)) from exc
        if document["billing_key_name"] not in document["secret_names"]:
            raise RunnerError("runner billing key must be one of the named secrets")
        reserved = _RESERVED_ENVIRONMENT_NAMES.intersection(document["secret_names"])
        if reserved:
            raise RunnerError(
                "runner manifest names secrets that collide with reserved host "
                f"environment variables: {', '.join(sorted(reserved))}"
            )
        children = tuple(document["child_executables"])
        if document["adapter"] == "codex" and children:
            raise RunnerError("direct Codex runner may not declare child executables")
        if document["adapter"] == "ollama-codex" and len(children) != 1:
            raise RunnerError("Ollama-to-Codex runner requires exactly one Codex child")
        if document["state_profile_digest"] != profile_digest("lane-dispatch"):
            raise RunnerError("runner manifest binds a stale state-admission profile")
        # Fail closed at load: every loadable manifest must bind an explicitly
        # declared billing-authentication mechanism; raw-key environment injection
        # is never a fallback for an undeclared adapter/billing-key combination.
        _billing_authentication_mode(
            str(document["adapter"]),
            str(document["billing_key_name"]),
        )
        return cls(dict(document))

    @property
    def content_digest(self) -> str:
        return digest_obj(dict(self.document))

    @property
    def limits(self) -> RunnerLimits:
        return RunnerLimits(**dict(self.document["limits"]))

    @property
    def billing_authentication(self) -> str:
        """The declared billing-authentication mode for this manifest's pairing.

        Re-derived (not stored) so a directly constructed manifest that bypassed
        ``from_dict`` still fails closed on an undeclared pairing.
        """

        return _billing_authentication_mode(
            str(self.document["adapter"]),
            str(self.document["billing_key_name"]),
        )


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
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise RunnerError(f"named secret is not UTF-8: {name}") from exc
            text = text.removesuffix("\n")
            if "\n" in text or "\r" in text:
                # A CRLF-terminated or multi-line secret file would otherwise
                # authenticate with a silently corrupted key; refuse it here so the
                # defect surfaces at resolve time instead of as a provider 401.
                raise RunnerError(
                    f"named secret contains a carriage return or interior newline: {name}"
                )
            values[name] = text
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


# CLI-specific billing-authentication knowledge lives here, adjacent to
# CodexRunnerAdapter.  The mechanism is keyed on adapter-FAMILY membership, not on the
# billing_key_name.  Every codex-family adapter (the codex CLI directly, and
# `ollama launch codex`) authenticates by writing the billing key's VALUE into
# CODEX_HOME/auth.json (see CodexApiKeyBootstrap); codex reads the key from a fixed
# auth.json field named ``OPENAI_API_KEY``, so the manifest's billing_key_name only
# *names the secret to resolve* and is decoupled from that field — hence unconstrained.
# The key is never placed in the model process environment for these adapters.
#
# Founder ruling 2026-08-26 (F5 amendment, signed intent): the iter-1 guard admitted
# only the single pairing ("codex","OPENAI_API_KEY") and so wrongly refused
# codex+FACTORY_TEST_API_KEY and every ollama-codex pairing, all of which bootstrap
# safely.  Widen the supported set to the whole family.  Membership is the schema's
# adapter enum; the frozenset below is the single source of truth for the family and
# the map is derived from it.  Any adapter OUTSIDE the family has no auth-file
# bootstrap path, so a declared billing_key_name there would require delivering a
# billing secret into a network-capable model process environment; that pairing is
# refused fail-closed at load rather than silently env-injected.
_CODEX_AUTH_FILE = "codex-auth-file"
_CODEX_ADAPTER_FAMILY = frozenset({"codex", "ollama-codex"})
_BILLING_AUTHENTICATION_BY_ADAPTER: Mapping[str, str] = {
    adapter: _CODEX_AUTH_FILE for adapter in _CODEX_ADAPTER_FAMILY
}


def _billing_authentication_mode(adapter: str, billing_key_name: str) -> str:
    """Resolve the billing-authentication mechanism for an adapter/billing-key pairing.

    Codex-family adapters bootstrap the billing key into CODEX_HOME/auth.json for ANY
    billing_key_name (the name is decoupled from codex's fixed ``OPENAI_API_KEY``
    auth.json field), so the name is unconstrained here.  An adapter outside the family
    has no auth-file bootstrap path, so declaring a billing_key_name would require
    delivering a billing secret into a network-capable model process environment —
    refused fail-closed as an unsafe channel / spec defect, never a raw-key
    environment-injection fallback.
    """

    mode = _BILLING_AUTHENTICATION_BY_ADAPTER.get(adapter)
    if mode is None:
        raise RunnerError(
            "runner manifest binds a billing_key_name "
            f"({billing_key_name}) to a non-codex-family adapter ({adapter}) with no "
            "auth-file bootstrap path; delivering a billing secret into the model "
            "process environment is refused (raw-key environment injection is never a "
            "fallback)"
        )
    return mode


@dataclass(frozen=True)
class CodexApiKeyBootstrap:
    """Provision Codex API-key authentication once per dispatch, host-side, exec-free.

    Codex reads ``CODEX_HOME/auth.json`` for API-key authentication, and the same
    ``CODEX_HOME`` holds the session rollouts that ``codex exec resume`` requires, so
    the home must be bootstrapped once before the invocation loop and preserved across
    every invocation of the dispatch.  The auth file is written directly by the host —
    no process (least of all the snapshotted model executable) ever runs with the raw
    key, so no timeout or sandbox question arises for a login subprocess that does not
    exist.

    File-format evidence: on codex-cli 0.148.0 (the pinned executable family),
    ``codex login --with-api-key`` writes ``CODEX_HOME/auth.json`` with mode 0600
    containing exactly ``{"auth_mode": "apikey", "OPENAI_API_KEY": "<key>"}``, and
    ``codex login status`` accepts a directly written file of that shape (verified
    empirically 2026-08-26 against a throwaway CODEX_HOME with a fake key).

    Enforced containment (exactly this, no more): ``auth.json`` must live inside the
    model-writable home because Codex requires it there, so its absence from retained
    material is proven rather than assumed — ``conclude`` removes the file exactly once
    after the dispatch loop (success or failure, after failure evidence is retained),
    verifies the removal, and then scans every retained regular file under the
    workspace for the raw key bytes, failing the dispatch on any appearance.  Model
    stdout/stderr/structured output are separately gated by ``_require_no_secret_leak``
    and retained diagnostics pass through ``_redact_diagnostic_stream`` with the key in
    the redaction set.
    """

    codex_home: Path
    billing_key_name: str
    api_key: str

    @classmethod
    def for_dispatch(
        cls,
        manifest: RunnerManifest,
        *,
        codex_home: Path,
        secrets: Mapping[str, str],
    ) -> CodexApiKeyBootstrap | None:
        """Build the declared bootstrap for this manifest, or None when no file bootstrap applies.

        ``codex_home`` is a host-owned path derived from the private workspace, never
        read back out of an environment mapping a secret value could have shadowed.
        """

        if manifest.billing_authentication != _CODEX_AUTH_FILE:
            return None
        billing_key_name = str(manifest.document["billing_key_name"])
        api_key = secrets.get(billing_key_name, "")
        if not api_key:
            raise RunnerError(
                "Codex API-key authentication requires a non-empty "
                f"{billing_key_name} named secret"
            )
        return cls(
            codex_home=codex_home,
            billing_key_name=billing_key_name,
            api_key=api_key,
        )

    @property
    def auth_path(self) -> Path:
        return self.codex_home / "auth.json"

    def provision(self) -> None:
        """Create the Codex home and write auth.json before the first invocation."""

        try:
            self.codex_home.mkdir(mode=0o700, parents=True, exist_ok=False)
            self.codex_home.chmod(0o700)
        except OSError as exc:
            raise RunnerError(
                f"Codex authentication home could not be provisioned: {exc}"
            ) from exc
        content = json.dumps(
            {"auth_mode": "apikey", "OPENAI_API_KEY": self.api_key},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            _write_once(self.auth_path, content)
        except (OSError, RunnerError) as exc:
            removal_hazard = ""
            try:
                self.auth_path.unlink(missing_ok=True)
            except OSError as unlink_exc:
                removal_hazard = (
                    "; retention hazard: partial auth material could not be removed: "
                    f"{unlink_exc}"
                )
            raise RunnerError(
                f"Codex auth file could not be provisioned: {exc}{removal_hazard}"
            ) from exc

    def conclude(self, *, workspace: Path, model_attempts: int) -> None:
        """Remove the auth material exactly once and prove the retained tree is key-free.

        Runs after the dispatch loop — and, on failure, after invocation evidence has
        been retained — so Codex-side session rollouts and logs survive as retained
        diagnostics while the credential does not.  Any cleanup failure is a typed
        retention hazard carrying the true model-attempt count; it is never silent.
        """

        try:
            self.auth_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise RunnerError(
                "retention hazard: Codex auth material could not be removed from the "
                f"retained workspace: {exc}",
                model_attempts=model_attempts,
            ) from exc
        if self.auth_path.exists() or self.auth_path.is_symlink():
            raise RunnerError(
                "retention hazard: Codex auth material persists in the retained "
                "workspace after removal",
                model_attempts=model_attempts,
            )
        needle = self.api_key.encode("utf-8")
        try:
            for root, _directories, files in os.walk(workspace, followlinks=False):
                for file_name in sorted(files):
                    candidate = Path(root) / file_name
                    if candidate.is_symlink() or not candidate.is_file():
                        continue
                    if self._file_contains(candidate, needle):
                        raise RunnerError(
                            "retention hazard: Codex billing key material appears in "
                            "retained runner bytes: "
                            f"{candidate.relative_to(workspace).as_posix()}",
                            model_attempts=model_attempts,
                        )
        except OSError as exc:
            raise RunnerError(
                "retention hazard: retained runner bytes could not be verified "
                f"key-free: {exc}",
                model_attempts=model_attempts,
            ) from exc

    @staticmethod
    def _file_contains(path: Path, needle: bytes) -> bool:
        overlap = max(len(needle) - 1, 0)
        tail = b""
        with open(path, "rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    return False
                if needle in tail + chunk:
                    return True
                tail = chunk[-overlap:] if overlap else b""


def _regular_executable(path: str | Path) -> Path:
    resolved = Path(path).resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise RunnerError(f"runner executable is unavailable: {resolved}")
    return resolved


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _snapshot_executable(source: Path, destination: Path) -> RunnerExecutableSnapshot:
    """Copy one stable executable inode into the private runner workspace.

    Qualification and every invocation use the snapshot rather than reopening a
    mutable installation path.  The retained snapshot is therefore the exact byte
    identity the failure boundary can verify after the process exits.
    """

    source_fd = -1
    destination_fd = -1
    try:
        source_fd = os.open(
            source,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise RunnerError(f"runner executable source is not regular: {source}")
        destination_fd = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o500,
        )
        hasher = hashlib.sha256()
        byte_count = 0
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
            offset = 0
            while offset < len(chunk):
                written = os.write(destination_fd, chunk[offset:])
                if written < 1:
                    raise OSError("runner executable snapshot made no progress")
                offset += written
            byte_count += len(chunk)
        after = os.fstat(source_fd)
        if _file_identity(before) != _file_identity(after) or byte_count != before.st_size:
            raise RunnerError("runner executable changed while being snapshotted")
        os.fchmod(destination_fd, 0o500)
        os.fsync(destination_fd)
    except OSError as exc:
        raise RunnerError(f"runner executable snapshot failed: {source}: {exc}") from exc
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
        if source_fd >= 0:
            os.close(source_fd)
    fsync_directory(destination.parent)
    return RunnerExecutableSnapshot(
        path=destination,
        byte_count=byte_count,
        content_digest="sha256:" + hasher.hexdigest(),
    )


def _write_once(path: Path, content: bytes) -> None:
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise RunnerError("runner evidence parent must be a regular directory")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    fsync_directory(path.parent)


def _redact_diagnostic_stream(value: str, secret_values: Sequence[str]) -> str:
    """Bound and redact a private stream before writing Validator evidence."""

    redacted = value
    for secret in secret_values:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    marker = "[TRUNCATED]"
    raw = redacted.encode("utf-8")
    if len(raw) <= _MAX_DIAGNOSTIC_STREAM_BYTES:
        return redacted
    clipped = raw[: _MAX_DIAGNOSTIC_STREAM_BYTES - len(marker)].decode(
        "utf-8", errors="ignore"
    )
    return f"{clipped}{marker}"


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
        manifest_bytes: bytes,
        projection_bytes: bytes,
        output_schema_bytes: bytes,
        task_bytes: bytes,
        state_capsule_document: Mapping[str, Any],
        state_dependencies: Mapping[str, bytes],
        target_state_digest: str,
        run_ledger_head: str,
        resume_checkpoint_digest: str,
        broker_registry_source_digest: str,
        workspace_root: str | Path,
        forbidden_paths: Sequence[str | Path],
        attempt_observer: Callable[[int], None] | None = None,
    ) -> tuple[Mapping[str, Any], RunnerReceipt]:
        try:
            state_capsule_snapshot = json.loads(
                json.dumps(
                    state_capsule_document,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        except (TypeError, ValueError, RecursionError, json.JSONDecodeError) as exc:
            raise RunnerError("runner state capsule is not canonical JSON") from exc
        if not isinstance(state_capsule_snapshot, Mapping):
            raise RunnerError("runner state capsule must be a JSON object")
        state_dependencies_snapshot: dict[str, bytes] = {}
        for dependency_id, raw in state_dependencies.items():
            if not isinstance(dependency_id, str) or not isinstance(raw, bytes):
                raise RunnerError("runner state dependencies must map strings to bytes")
            state_dependencies_snapshot[dependency_id] = bytes(raw)
        try:
            manifest_document = json.loads(manifest_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RunnerError("runner manifest is not valid UTF-8 JSON") from exc
        if not isinstance(manifest_document, Mapping):
            raise RunnerError("runner manifest must be a JSON object")
        manifest = RunnerManifest.from_dict(manifest_document)
        limits = manifest.limits
        if limits.max_attempts < 3:
            raise RunnerError("runner requires two canaries plus one task attempt")
        if digest_bytes(output_schema_bytes) != manifest.document["output_schema_digest"]:
            raise RunnerError("runner output schema differs from the manifest")
        output_schema_document = _closed_output_schema(output_schema_bytes)
        projection_digest = digest_bytes(projection_bytes)
        try:
            projection_document = json.loads(projection_bytes)
        except json.JSONDecodeError as exc:
            raise RunnerError("runner projection is not JSON") from exc
        if not isinstance(projection_document, Mapping):
            raise RunnerError("runner projection must be a JSON object")
        try:
            task = task_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RunnerError("runner task must be UTF-8") from exc
        task_digest = digest_bytes(task_bytes)
        state_capsule_digest = digest_obj(state_capsule_snapshot)
        try:
            verify_state_capsule(
                state_capsule_snapshot,
                expected_purpose="lane-dispatch",
                expected_run_id=run_id,
                expected_generation=generation,
                expected_role=str(manifest.document["role"]),
                expected_target_state_digest=target_state_digest,
                expected_run_ledger_head=run_ledger_head,
                expected_resume_checkpoint_digest=resume_checkpoint_digest,
                expected_dependencies=state_dependencies_snapshot,
            )
        except StateAdmissionError as exc:
            raise RunnerError(
                f"runner state capsule is invalid: {exc}",
                refusal_code=exc.code,
                dependency_id=exc.dependency_id,
            ) from exc
        if state_capsule_snapshot["profile_digest"] != manifest.document[
            "state_profile_digest"
        ]:
            raise RunnerError("runner manifest and state capsule bind different profiles")
        dependency_map = {
            str(item["dependency_id"]): str(item["content_digest"])
            for item in state_capsule_snapshot["dependencies"]
        }
        expected_dependency_digests = {
            "runner-manifest": digest_bytes(manifest_bytes),
            "runner-projection": projection_digest,
            "runner-output-schema": digest_bytes(output_schema_bytes),
            "frozen-task": task_digest,
            "broker-registry": broker_registry_source_digest,
            "effective-directives": digest_bytes(
                state_dependencies_snapshot["effective-directives"]
            ),
            "directive-readback": digest_bytes(
                state_dependencies_snapshot["directive-readback"]
            ),
            "role-contract": digest_bytes(state_dependencies_snapshot["role-contract"]),
        }
        for dependency_id, expected_digest in expected_dependency_digests.items():
            if dependency_map.get(dependency_id) != expected_digest:
                raise RunnerError(
                    f"runner state capsule binds different {dependency_id} bytes"
                )
        try:
            phase_digest_document = json.loads(
                state_dependencies_snapshot["phase-artifact-digests"]
            )
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RunnerError("ratified phase digest set is not valid JSON") from exc
        if not isinstance(phase_digest_document, Mapping):
            raise RunnerError("ratified phase digest set must be an object")
        required_phases = {
            "product-specification",
            "architecture",
            "operational-maturity",
        }
        if set(phase_digest_document) != required_phases:
            raise RunnerError("runner requires the exact three ratified phase artifacts")
        phase_artifacts: dict[str, Mapping[str, Any]] = {}
        for phase in sorted(required_phases):
            dependency_id = f"phase-artifact-{phase}"
            try:
                document = json.loads(state_dependencies_snapshot[dependency_id])
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RunnerError(f"ratified {phase} artifact is not valid JSON") from exc
            if not isinstance(document, Mapping):
                raise RunnerError(f"ratified {phase} artifact must be an object")
            try:
                validate_document("phase-artifact", document)
            except DocumentValidationError as exc:
                raise RunnerError(str(exc)) from exc
            artifact = PhaseArtifact.from_dict(document)
            if (
                artifact.phase != phase
                or artifact.content_digest != phase_digest_document[phase]
            ):
                raise RunnerError(f"ratified {phase} artifact differs from its authority digest")
            phase_artifacts[phase] = dict(document)
        try:
            role_primer = state_dependencies_snapshot["role-primer"].decode("utf-8")
        except (KeyError, UnicodeDecodeError) as exc:
            raise RunnerError("role-scoped primer must be admitted UTF-8 context") from exc
        try:
            effective_directives = json.loads(
                state_dependencies_snapshot["effective-directives"]
            )
            role_contract = json.loads(state_dependencies_snapshot["role-contract"])
            directive_readback = json.loads(
                state_dependencies_snapshot["directive-readback"]
            )
            configuration_set = json.loads(
                state_dependencies_snapshot["configuration-set"]
            )
        except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
            raise RunnerError("runner instruction dependencies are not valid JSON") from exc
        for label, document, schema_name in (
            ("effective directive contract", effective_directives, "effective-directive-contract"),
            ("role contract", role_contract, "role-contract"),
        ):
            if not isinstance(document, Mapping):
                raise RunnerError(f"{label} must be a JSON object")
            try:
                validate_document(schema_name, document)
            except DocumentValidationError as exc:
                raise RunnerError(str(exc)) from exc
        if not isinstance(directive_readback, Mapping):
            raise RunnerError("directive readback must be a JSON object")
        if not isinstance(configuration_set, Mapping) or not all(
            isinstance(value, str) for value in configuration_set.values()
        ):
            raise RunnerError("runner configuration set is invalid")
        configuration_digests = set(configuration_set.values())
        source_digests = {
            str(effective_directives["ledger"]["source_digest"]),
            str(effective_directives["provisional"]["source_digest"]),
            str(role_contract["source_digest"]),
        }
        if not source_digests <= configuration_digests:
            raise RunnerError("instruction sources are not in the checkpoint configuration set")
        if role_contract["role"] != manifest.document["role"]:
            raise RunnerError("role contract belongs to another lane")
        expected_effective_scope = {
            "run_id": run_id,
            "generation": generation,
            "role": manifest.document["role"],
        }
        if any(
            effective_directives.get(field) != expected
            for field, expected in expected_effective_scope.items()
        ):
            raise RunnerError("effective directive contract belongs to another lane invocation")
        instructions = str(role_contract["instructions"])
        if digest_bytes(instructions.encode("utf-8")) != role_contract["instructions_digest"]:
            raise RunnerError("role contract instructions digest differs")
        try:
            validate_directive_readback(
                directive_readback,
                contract=effective_directives,
                expected_run_id=run_id,
                expected_generation=generation,
                expected_role=str(manifest.document["role"]),
            )
        except ValueError as exc:
            raise RunnerError(str(exc)) from exc
        projection_payload = json.dumps(
            projection_document,
            sort_keys=True,
            separators=(",", ":"),
        )
        executable_source = _regular_executable(str(manifest.document["executable"]))
        child_executable_sources = tuple(
            _regular_executable(path) for path in manifest.document["child_executables"]
        )
        workspace = Path(workspace_root)
        if workspace.exists() or workspace.is_symlink():
            raise RunnerError("runner workspace must be fresh and absent")
        input_root = workspace / "input"
        output_root = workspace / "output"
        home = workspace / "home"
        temporary = workspace / "tmp"
        executable_root = workspace / "executables"
        workspace.mkdir(mode=0o700, parents=True, exist_ok=False)
        workspace.chmod(0o700)
        for directory in (input_root, output_root, home, temporary, executable_root):
            directory.mkdir(mode=0o700, exist_ok=False)
            directory.chmod(0o700)
        executable_snapshot = _snapshot_executable(
            executable_source,
            executable_root / "runner",
        )
        child_executable_snapshots = tuple(
            _snapshot_executable(source, executable_root / "codex")
            for source in child_executable_sources
        )
        executable = executable_snapshot.path
        child_executables = tuple(item.path for item in child_executable_snapshots)
        projection = input_root / "projection.json"
        output_schema = input_root / "output-schema.json"
        state_capsule = input_root / "state-capsule.json"
        _write_once(projection, projection_bytes)
        _write_once(output_schema, output_schema_bytes)
        _write_once(
            state_capsule,
            (
                json.dumps(
                    state_capsule_snapshot,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8"),
        )

        secrets = self.secret_store.resolve(tuple(manifest.document["secret_names"]))
        if _RESERVED_ENVIRONMENT_NAMES.intersection(secrets):
            # from_dict already refuses these; keep the merge point fail-closed for
            # any manifest object that reached dispatch without that gate.
            raise RunnerError(
                "resolved secrets collide with reserved host environment variables"
            )
        # Host-owned path: every bootstrap filesystem effect derives from this
        # variable, never from the (secret-merged) environment mapping.
        codex_home = home / "codex"
        path_entries = {str(executable.parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin"}
        environment = {
            "HOME": str(home),
            "CODEX_HOME": str(codex_home),
            "TMPDIR": str(temporary),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": ":".join(sorted(path_entries)),
            "FACTORY_ROLE": str(manifest.document["role"]),
            "FACTORY_PROJECTION_DIGEST": projection_digest,
            "FACTORY_STATE_CAPSULE_DIGEST": state_capsule_digest,
            **secrets,
        }
        qualification = self.backend.qualify(
            workspace / "qualification",
            allowed_executables=(executable, *child_executables),
            forbidden_paths=forbidden_paths,
        )
        if not qualification.satisfied:
            raise RunnerError("networked runner backend did not satisfy its qualification")
        qualification_bytes = json.dumps(
            qualification.document,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        _write_once(input_root / "runner-qualification.json", qualification_bytes)
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
            self._canary_prompt(
                manifest, projection_digest, state_capsule_digest, 1, continuity_nonce
            ),
            self._canary_prompt(manifest, projection_digest, state_capsule_digest, 2, ""),
            self._task_prompt(
                manifest,
                projection_digest,
                state_capsule_digest,
                projection_payload,
                phase_artifacts,
                role_contract,
                effective_directives,
                directive_readback,
                role_primer,
                task,
            ),
        )
        prompt_kinds = ("qualification", "qualification", "task")
        prompt_bytes = tuple(prompt.encode("utf-8") for prompt in prompts)
        if any(len(raw) > _MAX_PROMPT_BYTES for raw in prompt_bytes):
            raise RunnerError("runner prompt exceeds the bounded input size")
        prompt_sequence = [
            {
                "attempt": index,
                "kind": kind,
                "byte_count": len(raw),
                "content_digest": digest_bytes(raw),
            }
            for index, (kind, raw) in enumerate(
                zip(prompt_kinds, prompt_bytes, strict=True), start=1
            )
        ]
        # Bootstrap the declared billing authentication exactly once per dispatch,
        # before the invocation loop, so Codex session rollouts survive for the
        # mandatory `codex exec resume` invocations; the billing key is withheld
        # from the model environment for the auth-file mode.  A provision failure
        # here precedes every model attempt, so its default zero-attempt
        # pre-model classification is truthful.
        model_environment = dict(environment)
        bootstrap = CodexApiKeyBootstrap.for_dispatch(
            manifest,
            codex_home=codex_home,
            secrets=secrets,
        )
        if bootstrap is not None:
            model_environment.pop(bootstrap.billing_key_name, None)
            bootstrap.provision()
        try:
            for index, raw_prompt in enumerate(prompt_bytes, start=1):
                _write_once(input_root / f"prompt-{index}.json", raw_prompt)
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
                try:
                    result = self.backend.run(
                        command,
                        cwd=workspace,
                        readable_paths=(output_schema, executable, *child_executables),
                        writable_paths=(output_root, home, temporary),
                        environment=model_environment,
                        stdin=raw_prompt,
                        limits=attempt_limits,
                    )
                except RunnerError as exc:
                    current_attempts = min(exc.model_attempts, 1)
                    attempts = (index - 1) + current_attempts
                    if attempt_observer is not None and attempts:
                        attempt_observer(attempts)
                    if current_attempts:
                        result = exc.invocation_result or RunnerProcessResult(
                            command=tuple(map(str, command)),
                            returncode=-1,
                            stdout="",
                            stderr=(
                                "[CAPTURE INCOMPLETE: runner backend failed after the model "
                                "attempt began]"
                            ),
                            structured_output={},
                            session_id="",
                            input_tokens=0,
                            output_tokens=0,
                            process_peak=1,
                            termination_reason=SUPERVISOR_ERROR,
                        )
                        raise self._invocation_error(
                            workspace=workspace,
                            run_id=run_id,
                            generation=generation,
                            receipt_id=receipt_id,
                            invocation=index,
                            result=result,
                            secret_values=tuple(secrets.values()),
                            message=str(exc),
                            model_attempts=attempts,
                            manifest=manifest,
                            manifest_bytes=manifest_bytes,
                            projection_digest=projection_digest,
                            task_digest=task_digest,
                            state_capsule_digest=state_capsule_digest,
                            target_state_digest=target_state_digest,
                            run_ledger_head=run_ledger_head,
                            resume_checkpoint_digest=resume_checkpoint_digest,
                            broker_registry_source_digest=broker_registry_source_digest,
                            qualification=qualification,
                            continuity_nonce_digest=digest_obj(
                                {"continuity_nonce": continuity_nonce}
                            ),
                            prompt_sequence=prompt_sequence[:index],
                            executable_snapshot=executable_snapshot.evidence(
                                workspace=workspace
                            ),
                            child_executable_snapshots=[
                                item.evidence(workspace=workspace)
                                for item in child_executable_snapshots
                            ],
                            failed_at=self._clock(),
                        ) from exc
                    raise RunnerError(str(exc), model_attempts=attempts) from exc
                except (OSError, ValueError) as exc:
                    if attempt_observer is not None:
                        attempt_observer(index)
                    result = RunnerProcessResult(
                        command=tuple(map(str, command)),
                        returncode=-1,
                        stdout="",
                        stderr=(
                            "[CAPTURE INCOMPLETE: runner backend raised after invocation "
                            "admission]"
                        ),
                        structured_output={},
                        session_id="",
                        input_tokens=0,
                        output_tokens=0,
                        process_peak=1,
                        termination_reason=SUPERVISOR_ERROR,
                    )
                    raise self._invocation_error(
                        workspace=workspace,
                        run_id=run_id,
                        generation=generation,
                        receipt_id=receipt_id,
                        invocation=index,
                        result=result,
                        secret_values=tuple(secrets.values()),
                        message=str(exc),
                        model_attempts=index,
                        manifest=manifest,
                        manifest_bytes=manifest_bytes,
                        projection_digest=projection_digest,
                        task_digest=task_digest,
                        state_capsule_digest=state_capsule_digest,
                        target_state_digest=target_state_digest,
                        run_ledger_head=run_ledger_head,
                        resume_checkpoint_digest=resume_checkpoint_digest,
                        broker_registry_source_digest=broker_registry_source_digest,
                        qualification=qualification,
                        continuity_nonce_digest=digest_obj(
                            {"continuity_nonce": continuity_nonce}
                        ),
                        prompt_sequence=prompt_sequence[:index],
                        executable_snapshot=executable_snapshot.evidence(workspace=workspace),
                        child_executable_snapshots=[
                            item.evidence(workspace=workspace)
                            for item in child_executable_snapshots
                        ],
                        failed_at=self._clock(),
                    ) from exc
                if attempt_observer is not None:
                    attempt_observer(index)
                try:
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
                        state_capsule_digest=state_capsule_digest,
                        sequence=index,
                        continuity_nonce=continuity_nonce,
                        output_schema=output_schema_document,
                    )
                    results.append(result)
                    self._enforce_meter(manifest, results)
                except RunnerError as exc:
                    raise self._invocation_error(
                        workspace=workspace,
                        run_id=run_id,
                        generation=generation,
                        receipt_id=receipt_id,
                        invocation=index,
                        result=result,
                        secret_values=tuple(secrets.values()),
                        message=str(exc),
                        model_attempts=max(exc.model_attempts, index),
                        manifest=manifest,
                        manifest_bytes=manifest_bytes,
                        projection_digest=projection_digest,
                        task_digest=task_digest,
                        state_capsule_digest=state_capsule_digest,
                        target_state_digest=target_state_digest,
                        run_ledger_head=run_ledger_head,
                        resume_checkpoint_digest=resume_checkpoint_digest,
                        broker_registry_source_digest=broker_registry_source_digest,
                        qualification=qualification,
                        continuity_nonce_digest=digest_obj(
                            {"continuity_nonce": continuity_nonce}
                        ),
                        prompt_sequence=prompt_sequence[:index],
                        executable_snapshot=executable_snapshot.evidence(workspace=workspace),
                        child_executable_snapshots=[
                            item.evidence(workspace=workspace)
                            for item in child_executable_snapshots
                        ],
                        failed_at=self._clock(),
                    ) from exc
        except BaseException as exc:
            if bootstrap is not None:
                # Every invocation error retained its diagnostic and failure receipt
                # before it was raised, so cleanup here runs strictly after failure
                # evidence retention: the retained workspace keeps the Codex-side
                # sessions and logs while the auth material is removed and the
                # retained bytes are proven key-free.  A cleanup failure surfaces as
                # a typed retention hazard carrying the true attempt count (the
                # original failure remains chained as its context and its receipt
                # bytes are already durably on disk).
                bootstrap.conclude(
                    workspace=workspace,
                    model_attempts=max(len(results), getattr(exc, "model_attempts", 0)),
                )
            raise
        if bootstrap is not None:
            bootstrap.conclude(workspace=workspace, model_attempts=len(results))

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
            "schema_version": "factory-runner-receipt/3",
            "receipt_id": receipt_id,
            "run_id": run_id,
            "generation": generation,
            "role": manifest.document["role"],
            "runner_manifest_digest": digest_bytes(manifest_bytes),
            "runner_id": manifest.document["runner_id"],
            "adapter": manifest.document["adapter"],
            "executable_digest": executable_snapshot.content_digest,
            "runner_version": manifest.document["runner_version"],
            "model": manifest.document["model"],
            "model_version": manifest.document["model_version"],
            "configuration_digest": manifest.document["configuration_digest"],
            "state_profile_digest": manifest.document["state_profile_digest"],
            "state_qualification_digest": manifest.document[
                "state_qualification_digest"
            ],
            "state_capsule_digest": state_capsule_digest,
            "projection_digest": projection_digest,
            "task_digest": task_digest,
            "prompt_schema_version": _PROMPT_SCHEMA_VERSION,
            "prompt_assembler_version": _PROMPT_ASSEMBLER_VERSION,
            "prompt_sequence": prompt_sequence,
            "prompt_bytes_retained": True,
            "resume_checkpoint_digest": resume_checkpoint_digest,
            "broker_registry_source_digest": broker_registry_source_digest,
            "billing_key_name": manifest.document["billing_key_name"],
            "secret_names": list(manifest.document["secret_names"]),
            "network_mode": manifest.document["network_mode"],
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
            "termination_reason": COMPLETED,
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
    def _invocation_error(
        *,
        workspace: Path,
        run_id: str,
        generation: int,
        receipt_id: str,
        invocation: int,
        result: RunnerProcessResult,
        secret_values: Sequence[str],
        message: str,
        model_attempts: int,
        manifest: RunnerManifest,
        manifest_bytes: bytes,
        projection_digest: str,
        task_digest: str,
        state_capsule_digest: str,
        target_state_digest: str,
        run_ledger_head: str,
        resume_checkpoint_digest: str,
        broker_registry_source_digest: str,
        qualification: RunnerQualification,
        continuity_nonce_digest: str,
        prompt_sequence: Sequence[Mapping[str, Any]],
        executable_snapshot: Mapping[str, Any],
        child_executable_snapshots: Sequence[Mapping[str, Any]],
        failed_at: int,
    ) -> RunnerInvocationError:
        """Write only bounded, redacted output outside the model's grants."""

        diagnostic = {
            "schema_version": "factory-runner-invocation-diagnostic/1",
            "invocation": invocation,
            "returncode": result.returncode,
            "termination_reason": result.termination_reason,
            "process_peak": result.process_peak,
            "stdout": _redact_diagnostic_stream(result.stdout, secret_values),
            "stderr": _redact_diagnostic_stream(result.stderr, secret_values),
        }
        path = workspace / "validator-invocation-diagnostic.json"
        diagnostic_bytes = (
            json.dumps(diagnostic, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        try:
            validate_document("runner-invocation-diagnostic", diagnostic)
            _write_once(path, diagnostic_bytes)
        except (DocumentValidationError, OSError, RunnerError) as exc:
            # A model process already ran. Never let a secondary evidence-retention
            # failure erase that fact and become a zero-attempt admission refusal.
            raise RunnerError(
                "private invocation diagnostic could not be retained",
                model_attempts=model_attempts,
            ) from exc
        capsule = classify_terminal_failure(
            final={"status": "runtime-exception"},
            caller_returncode=result.returncode,
            caller_stdout=result.stdout,
            caller_stderr=result.stderr,
            validator_result_present=False,
            coder_receipt_present=False,
            tester_receipt_present=False,
            invocation_termination_reason=result.termination_reason,
        )
        failure_receipt = {
            "schema_version": "factory-runner-failure-receipt/2",
            "receipt_id": receipt_id,
            "run_id": run_id,
            "generation": generation,
            "role": manifest.document["role"],
            "invocation": invocation,
            "model_attempts": model_attempts,
            "runner_manifest_digest": digest_bytes(manifest_bytes),
            "runner_id": manifest.document["runner_id"],
            "adapter": manifest.document["adapter"],
            "executable_digest": executable_snapshot["content_digest"],
            "executable_snapshot": dict(executable_snapshot),
            "child_executable_snapshots": [
                dict(item) for item in child_executable_snapshots
            ],
            "runner_version": manifest.document["runner_version"],
            "model": manifest.document["model"],
            "model_version": manifest.document["model_version"],
            "configuration_digest": manifest.document["configuration_digest"],
            "state_profile_digest": manifest.document["state_profile_digest"],
            "state_qualification_digest": manifest.document[
                "state_qualification_digest"
            ],
            "state_capsule_digest": state_capsule_digest,
            "projection_digest": projection_digest,
            "task_digest": task_digest,
            "target_state_digest": target_state_digest,
            "run_ledger_head": run_ledger_head,
            "resume_checkpoint_digest": resume_checkpoint_digest,
            "broker_registry_source_digest": broker_registry_source_digest,
            "qualification_digest": qualification.content_digest,
            "qualification": dict(qualification.document),
            "continuity_nonce_digest": continuity_nonce_digest,
            "prompt_schema_version": _PROMPT_SCHEMA_VERSION,
            "prompt_assembler_version": _PROMPT_ASSEMBLER_VERSION,
            "prompt_sequence": [dict(item) for item in prompt_sequence],
            "prompt_bytes_retained": True,
            "diagnostic": {
                "content_digest": digest_bytes(diagnostic_bytes),
                "byte_count": len(diagnostic_bytes),
                "visibility": "validator-private",
            },
            "termination_reason": result.termination_reason,
            "returncode": result.returncode,
            "process_peak": result.process_peak,
            "failure_capsule": capsule.document(),
            "failed_at": failed_at,
        }
        failure_receipt_path = workspace / "runner-failure-receipt.json"
        try:
            validate_document("runner-failure-receipt", failure_receipt)
            _write_once(
                failure_receipt_path,
                json.dumps(
                    failure_receipt,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n",
            )
        except (DocumentValidationError, OSError, RunnerError) as exc:
            raise RunnerError(
                "private invocation failure receipt could not be retained",
                model_attempts=model_attempts,
            ) from exc
        return RunnerInvocationError(
            message,
            diagnostic_path=path,
            failure_receipt_path=failure_receipt_path,
            failure_receipt=failure_receipt,
            failure_capsule=capsule,
            model_attempts=model_attempts,
        )

    @staticmethod
    def _canary_prompt(
        manifest: RunnerManifest,
        projection_digest: str,
        state_capsule_digest: str,
        sequence: int,
        continuity_nonce: str,
    ) -> str:
        return json.dumps(
            {
                "schema_version": _PROMPT_SCHEMA_VERSION,
                "kind": "qualification",
                "control": {
                    "response": "configured-json-only",
                    "expected_kind": "canary",
                    "role": manifest.document["role"],
                    "projection_digest": projection_digest,
                    "state_capsule_digest": state_capsule_digest,
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
        state_capsule_digest: str,
        projection_payload: str,
        phase_artifacts: Mapping[str, Mapping[str, Any]],
        role_contract: Mapping[str, Any],
        effective_directives: Mapping[str, Any],
        directive_readback: Mapping[str, Any],
        role_primer: str,
        task: str,
    ) -> str:
        return json.dumps(
            {
                "schema_version": _PROMPT_SCHEMA_VERSION,
                "kind": "task",
                "control": {
                    "response": "configured-json-only",
                    "expected_kind": "handoff",
                    "role": manifest.document["role"],
                    "projection_digest": projection_digest,
                    "state_capsule_digest": state_capsule_digest,
                    "sequence": 3,
                    "continuity": {"recall_and_echo_from_first_turn": True},
                    "effect_boundary": "typed-broker-requests-only",
                    "authority_shaped_model_fields": "forbidden",
                    "role_contract": dict(role_contract),
                },
                "data": {
                    "projection": json.loads(projection_payload),
                    "ratified_phase_artifacts": {
                        phase: dict(artifact)
                        for phase, artifact in sorted(phase_artifacts.items())
                    },
                    "effective_directives": dict(effective_directives),
                    "directive_readback": dict(directive_readback),
                    "role_primer": role_primer,
                    "task": task,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _require_process_success(result: RunnerProcessResult, limits: RunnerLimits) -> None:
        if result.returncode != 0 or result.termination_reason != COMPLETED:
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
        state_capsule_digest: str,
        sequence: int,
        continuity_nonce: str,
        output_schema: Mapping[str, Any],
    ) -> None:
        expected = {
            "kind": "handoff" if sequence == 3 else "canary",
            "role": manifest.document["role"],
            "projection_digest": projection_digest,
            "state_capsule_digest": state_capsule_digest,
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
