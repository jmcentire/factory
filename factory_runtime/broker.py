"""Typed, signed-handle broker operations for the outer model boundary.

The model selects an opaque capability digest and supplies operation data.  It never supplies a
path, executable, argv, option, working directory, or script: those resolve from the host-owned
operation registry after the capability envelope verifies.
"""

from __future__ import annotations

import base64
import binascii
import fcntl
import json
import os
import re
import stat
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from factory_core.manifest import digest_bytes, digest_obj
from factory_runtime.authority import AuthorityPolicy
from factory_runtime.lanes import IsolationBackend
from factory_runtime.schema import DocumentValidationError, validate_document
from factory_runtime.tessera import TesseraCli, TesseraVerificationError

BROKER_CAPABILITY = "factory:activate-broker-capability"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_INPUT_BYTES = 1_048_576
_MAX_OUTPUT_BYTES = 10_485_760


class BrokerError(ValueError):
    """A broker request could not execute without widening its signed capability."""


@dataclass(frozen=True)
class BrokerRegistry:
    """Externally anchored host configuration that is never projected to a model."""

    document: Mapping[str, Any]
    operations: tuple[BrokerOperation, ...]
    capability_envelopes: Mapping[str, Path]

    @property
    def configuration_digest(self) -> str:
        return digest_obj(dict(self.document))


@dataclass(frozen=True)
class BrokerOperation:
    """Host-owned resolution of one operation id; never serialized to the model."""

    operation_id: str
    kind: str
    verifier_kind: str
    resource_root: Path | None = None
    relative_path: str = ""
    command: tuple[str, ...] = ()
    command_readable_paths: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.operation_id):
            raise BrokerError("operation_id is not canonical")
        if self.kind not in {"read-artifact", "publish-artifact", "run-verifier"}:
            raise BrokerError(f"unsupported broker operation kind: {self.kind}")
        if self.kind in {"read-artifact", "publish-artifact"}:
            if self.resource_root is None or not self.relative_path:
                raise BrokerError("file operation requires a resource root and relative path")
            relative = PurePosixPath(self.relative_path)
            if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
                raise BrokerError("broker operation path must be canonical and relative")
            expected = "content-rehash" if self.kind == "read-artifact" else "durable-rehash"
            if self.verifier_kind != expected:
                raise BrokerError(f"{self.kind} requires verifier {expected}")
            if self.command:
                raise BrokerError("file operations may not carry a command")
        else:
            if not self.command or not all(str(part) for part in self.command):
                raise BrokerError("run-verifier requires fixed non-empty argv")
            if self.resource_root is not None or self.relative_path:
                raise BrokerError("run-verifier may not carry a filesystem target")
            if self.verifier_kind != "deterministic-rerun":
                raise BrokerError("run-verifier requires deterministic-rerun verification")

    def body(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind,
            "verifier_kind": self.verifier_kind,
            "resource_root": str(self.resource_root.resolve()) if self.resource_root else "",
            "relative_path": self.relative_path,
            "command": list(self.command),
            "command_readable_paths": [
                str(path.resolve()) for path in self.command_readable_paths
            ],
        }

    @property
    def content_digest(self) -> str:
        return digest_obj(self.body())

    def resolved_path(self) -> Path:
        if self.resource_root is None:
            raise BrokerError("operation has no filesystem target")
        root = self.resource_root.resolve()
        target = (root / self.relative_path).resolve()
        if target == root or not target.is_relative_to(root):
            raise BrokerError("broker operation path escapes its registered resource root")
        return target


def load_broker_registry(
    document: Mapping[str, Any],
    *,
    run_id: str,
    generation: int,
    role: str,
    target_state_digest: str,
    resources: Mapping[str, Mapping[str, Any]],
) -> BrokerRegistry:
    """Resolve an anchored registry and bind every file root to the run resource ledger.

    Commands and paths are accepted only from this host-owned document. Model output can carry
    only an opaque capability digest, an operation kind, and kind-specific data.
    """

    try:
        validate_document("broker-registry", document)
    except DocumentValidationError as exc:
        raise BrokerError(str(exc)) from exc
    expected = {
        "run_id": run_id,
        "generation": generation,
        "role": role,
        "target_state_digest": target_state_digest,
    }
    for field, value in expected.items():
        if document.get(field) != value:
            raise BrokerError(f"broker registry has wrong {field}")

    operations: list[BrokerOperation] = []
    operation_ids: set[str] = set()
    for raw in document["operations"]:
        operation_id = str(raw["operation_id"])
        if operation_id in operation_ids:
            raise BrokerError("broker registry contains duplicate operation ids")
        operation_ids.add(operation_id)
        kind = str(raw["kind"])
        resource_id = str(raw["resource_id"])
        resource_root_text = str(raw["resource_root"])
        relative_path = str(raw["relative_path"])
        command = tuple(str(part) for part in raw["command"])
        readable_text = tuple(str(path) for path in raw["command_readable_paths"])
        if kind in {"read-artifact", "publish-artifact"}:
            if not resource_id or not _ID.fullmatch(resource_id):
                raise BrokerError("file operation requires a canonical resource_id")
            resource = resources.get(resource_id)
            if resource is None:
                raise BrokerError("broker file operation names an unregistered run resource")
            if resource.get("ownership") != "run-owned" or resource.get("status") != "active":
                raise BrokerError("broker file operation requires an active run-owned resource")
            configured_root = Path(resource_root_text)
            if not configured_root.is_absolute():
                raise BrokerError("broker resource root must be absolute")
            try:
                resource_root = configured_root.resolve(strict=True)
                ledger_root = Path(str(resource["identifier"])).resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise BrokerError("broker resource root is unavailable") from exc
            if configured_root != resource_root or resource_root != ledger_root:
                raise BrokerError("broker resource root differs from the run resource ledger")
            if command or readable_text:
                raise BrokerError("file operation may not carry command configuration")
            operation = BrokerOperation(
                operation_id=operation_id,
                kind=kind,
                verifier_kind=str(raw["verifier_kind"]),
                resource_root=resource_root,
                relative_path=relative_path,
            )
        else:
            if resource_id or resource_root_text or relative_path:
                raise BrokerError("verifier operation may not carry a resource path")
            if not command or not Path(command[0]).is_absolute():
                raise BrokerError("verifier command must name an absolute executable")
            executable = Path(command[0])
            try:
                resolved_executable = executable.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise BrokerError("verifier executable is unavailable") from exc
            if executable != resolved_executable or not resolved_executable.is_file():
                raise BrokerError("verifier executable must be a canonical regular file")
            readable: list[Path] = []
            for text in readable_text:
                path = Path(text)
                try:
                    resolved = path.resolve(strict=True)
                except (OSError, RuntimeError) as exc:
                    raise BrokerError("verifier readable path is unavailable") from exc
                if path != resolved:
                    raise BrokerError("verifier readable path must be canonical")
                readable.append(resolved)
            operation = BrokerOperation(
                operation_id=operation_id,
                kind=kind,
                verifier_kind=str(raw["verifier_kind"]),
                command=(str(resolved_executable), *command[1:]),
                command_readable_paths=tuple(readable),
            )
        operations.append(operation)

    capabilities: dict[str, Path] = {}
    for raw in document["capabilities"]:
        capability_digest = str(raw["capability_digest"])
        if capability_digest in capabilities:
            raise BrokerError("broker registry contains duplicate capability digests")
        envelope_path = Path(str(raw["envelope_path"]))
        if (
            not envelope_path.is_absolute()
            or envelope_path.is_symlink()
            or not envelope_path.is_file()
        ):
            raise BrokerError("broker capability envelope must be an absolute regular file")
        resolved_envelope = envelope_path.resolve(strict=True)
        if envelope_path != resolved_envelope:
            raise BrokerError("broker capability envelope path must be canonical")
        capabilities[capability_digest] = resolved_envelope
    return BrokerRegistry(
        document=dict(document),
        operations=tuple(operations),
        capability_envelopes=capabilities,
    )


@dataclass(frozen=True)
class BrokerCapabilityHandle:
    capability_id: str
    capability_digest: str
    run_id: str
    generation: int
    role: str
    target_state_digest: str
    operation_id: str
    operation_kind: str
    operation_definition_digest: str
    configuration_digest: str
    issuer_identity: str
    max_uses: int
    expires_at: int
    nonce: str
    envelope_digest: str


@dataclass(frozen=True)
class BrokerEffect:
    schema_version: str
    effect_id: str
    run_id: str
    generation: int
    role: str
    request_digest: str
    capability_digest: str
    operation_id: str
    operation_kind: str
    operation_definition_digest: str
    idempotency_key: str
    verifier_kind: str
    exit_status: int
    stdout_digest: str
    stderr_digest: str
    artifact_digest: str
    verified: bool
    started_at: int
    finished_at: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def content_digest(self) -> str:
        return digest_obj(self.to_dict())


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_once(path: Path, content: bytes) -> None:
    """Atomically publish a complete broker artifact without replacing prior evidence."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".broker-evidence-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise BrokerError("refusing to replace broker evidence") from exc
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _read_effect(path: Path) -> tuple[str, BrokerEffect]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BrokerError(f"broker idempotency receipt is unreadable: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise BrokerError("broker idempotency receipt is not regular")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            encoded = stream.read(_MAX_INPUT_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(encoded) > _MAX_INPUT_BYTES:
        raise BrokerError("broker idempotency receipt exceeds its size ceiling")
    try:
        raw = json.loads(encoded)
        if not isinstance(raw, dict):
            raise TypeError("receipt must be an object")
        request_digest = str(raw.pop("_request_digest"))
        validate_document("broker-effect", raw)
        effect = BrokerEffect(**raw)
    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
        DocumentValidationError,
    ) as exc:
        raise BrokerError(f"broker idempotency receipt is invalid: {exc}") from exc
    if encoded != _canonical_bytes({"_request_digest": request_digest, **effect.to_dict()}) + b"\n":
        raise BrokerError("broker idempotency receipt is not canonical")
    if effect.request_digest != request_digest:
        raise BrokerError("broker idempotency receipt has conflicting request digests")
    if not effect.verified:
        raise BrokerError("broker idempotency receipt records an unverified effect")
    return request_digest, effect


def load_broker_capability(
    envelope_path: str | Path,
    *,
    policy: AuthorityPolicy,
    tessera: TesseraCli,
    clock: Callable[[], int] | None = None,
) -> BrokerCapabilityHandle:
    """Verify one opaque capability against the signed genesis roster."""

    try:
        envelope = tessera.verify_json(
            envelope_path,
            trusted_public_keys=tuple(
                principal.public_key for principal in policy.principals.values()
            ),
            expected_kind="factory-broker-capability",
        )
        validate_document("broker-capability", envelope.payload)
    except (TesseraVerificationError, DocumentValidationError) as exc:
        raise BrokerError(f"broker capability is invalid: {exc}") from exc
    payload = envelope.payload
    issuer = policy.principal(str(payload["issuer_identity"]))
    if issuer is None:
        raise BrokerError("broker capability issuer is not enrolled")
    if issuer.public_key != envelope.public_key:
        raise BrokerError("broker capability issuer does not own the signing key")
    if BROKER_CAPABILITY not in issuer.capabilities:
        raise BrokerError("broker capability issuer lacks activation authority")
    issued_at = int(payload["issued_at"])
    expires_at = int(payload["expires_at"])
    now = (clock or (lambda: int(time.time())))()
    if issued_at > now or expires_at <= issued_at or expires_at < now:
        raise BrokerError("broker capability is not currently valid")
    return BrokerCapabilityHandle(
        capability_id=str(payload["capability_id"]),
        capability_digest=envelope.payload_digest,
        run_id=str(payload["run_id"]),
        generation=int(payload["generation"]),
        role=str(payload["role"]),
        target_state_digest=str(payload["target_state_digest"]),
        operation_id=str(payload["operation_id"]),
        operation_kind=str(payload["operation_kind"]),
        operation_definition_digest=str(payload["operation_definition_digest"]),
        configuration_digest=str(payload["configuration_digest"]),
        issuer_identity=str(payload["issuer_identity"]),
        max_uses=int(payload["max_uses"]),
        expires_at=expires_at,
        nonce=str(payload["nonce"]),
        envelope_digest=envelope.envelope_digest,
    )


class TypedOperationBroker:
    """Resolve and execute closed operations without accepting authority-shaped input."""

    def __init__(
        self,
        *,
        run_id: str,
        generation: int,
        role: str,
        target_state_digest: str,
        configuration_digest: str,
        operations: Sequence[BrokerOperation],
        evidence_root: str | Path,
        policy: AuthorityPolicy,
        tessera: TesseraCli,
        isolation: IsolationBackend,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self.run_id = run_id
        self.generation = generation
        self.role = role
        self.target_state_digest = target_state_digest
        self.configuration_digest = configuration_digest
        self.operations = {operation.operation_id: operation for operation in operations}
        if len(self.operations) != len(tuple(operations)):
            raise BrokerError("broker operation registry contains duplicate ids")
        self.evidence_root = Path(evidence_root)
        if self.evidence_root.is_symlink():
            raise BrokerError("broker evidence root may not be a symlink")
        self.evidence_root.mkdir(parents=True, exist_ok=True)
        if not self.evidence_root.is_dir():
            raise BrokerError("broker evidence root is not a directory")
        self.policy = policy
        self.tessera = tessera
        self.isolation = isolation
        self._clock = clock or (lambda: int(time.time()))

    def execute(
        self,
        request: Mapping[str, Any],
        *,
        capability_envelope_path: str | Path,
    ) -> BrokerEffect:
        try:
            validate_document("broker-request", request)
        except DocumentValidationError as exc:
            raise BrokerError(str(exc)) from exc
        encoded = _canonical_bytes(request)
        if len(encoded) > _MAX_INPUT_BYTES:
            raise BrokerError("broker request exceeds the bounded input size")
        request_digest = digest_obj(dict(request))
        if digest_obj(dict(request["input"])) != request["input_digest"]:
            raise BrokerError("broker request input digest does not re-derive")
        handle = load_broker_capability(
            capability_envelope_path,
            policy=self.policy,
            tessera=self.tessera,
            clock=self._clock,
        )
        self._bind_request(request, handle)
        operation = self.operations.get(handle.operation_id)
        if operation is None:
            raise BrokerError("broker capability names an unknown operation")
        if operation.kind != handle.operation_kind:
            raise BrokerError("broker capability changes the registered operation kind")
        if operation.content_digest != handle.operation_definition_digest:
            raise BrokerError("broker operation definition differs from the signed handle")

        # On the supported local run filesystem, one host lock makes idempotency admission and
        # signed-capability accounting atomic across cooperating broker processes. The model has
        # no direct path to this filesystem or lock. The lock remains held through effect
        # verification and durable receipt publication, so another broker request cannot
        # overspend max_uses or race the same idempotency address.
        lock_path = self.evidence_root / ".broker.lock"
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise BrokerError("broker accounting lock is not regular")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            return self._execute_locked(request, request_digest, handle, operation)
        finally:
            os.close(descriptor)

    def _execute_locked(
        self,
        request: Mapping[str, Any],
        request_digest: str,
        handle: BrokerCapabilityHandle,
        operation: BrokerOperation,
    ) -> BrokerEffect:
        receipt_path = self.evidence_root / f"{request['idempotency_key']}.json"
        if receipt_path.exists() or receipt_path.is_symlink():
            prior_request, effect = _read_effect(receipt_path)
            if prior_request != request_digest:
                raise BrokerError("idempotency key was replayed for a different request")
            expected = {
                "run_id": self.run_id,
                "generation": self.generation,
                "role": self.role,
                "request_digest": request_digest,
                "capability_digest": handle.capability_digest,
                "operation_id": operation.operation_id,
                "operation_kind": operation.kind,
                "operation_definition_digest": operation.content_digest,
                "idempotency_key": str(request["idempotency_key"]),
                "verifier_kind": operation.verifier_kind,
            }
            for field, value in expected.items():
                if getattr(effect, field) != value:
                    raise BrokerError(f"retained broker effect has wrong {field}")
            return effect
        used = self._capability_use_count(handle.capability_digest)
        if used >= handle.max_uses:
            raise BrokerError("broker capability use ceiling is exhausted")

        started_at = self._clock()
        if operation.kind == "read-artifact":
            result = self._read_artifact(operation, request["input"])
        elif operation.kind == "publish-artifact":
            result = self._publish_artifact(operation, request["input"])
        else:
            result = self._run_verifier(operation, request["input"])
        effect = BrokerEffect(
            schema_version="factory-broker-effect/1",
            effect_id=str(request["request_id"]),
            run_id=self.run_id,
            generation=self.generation,
            role=self.role,
            request_digest=request_digest,
            capability_digest=handle.capability_digest,
            operation_id=operation.operation_id,
            operation_kind=operation.kind,
            operation_definition_digest=operation.content_digest,
            idempotency_key=str(request["idempotency_key"]),
            verifier_kind=operation.verifier_kind,
            exit_status=int(result["exit_status"]),
            stdout_digest=str(result["stdout_digest"]),
            stderr_digest=str(result["stderr_digest"]),
            artifact_digest=str(result["artifact_digest"]),
            verified=bool(result["verified"]),
            started_at=started_at,
            finished_at=self._clock(),
        )
        if not effect.verified:
            raise BrokerError("operation-specific verifier rejected the broker effect")
        try:
            validate_document("broker-effect", effect.to_dict())
        except DocumentValidationError as exc:
            raise BrokerError(str(exc)) from exc
        receipt = {"_request_digest": request_digest, **effect.to_dict()}
        _write_once(receipt_path, _canonical_bytes(receipt) + b"\n")
        return effect

    def _bind_request(
        self,
        request: Mapping[str, Any],
        handle: BrokerCapabilityHandle,
    ) -> None:
        expected = {
            "run_id": self.run_id,
            "generation": self.generation,
            "role": self.role,
            "capability_digest": handle.capability_digest,
            "operation_kind": handle.operation_kind,
        }
        for field, value in expected.items():
            if request[field] != value:
                raise BrokerError(f"broker request has wrong {field}")
        if handle.run_id != self.run_id or handle.generation != self.generation:
            raise BrokerError("broker capability belongs to another run generation")
        if handle.role != self.role or handle.target_state_digest != self.target_state_digest:
            raise BrokerError("broker capability belongs to another role or target-state")
        if handle.configuration_digest != self.configuration_digest:
            raise BrokerError("broker capability belongs to another configuration")

    def _capability_use_count(self, capability_digest: str) -> int:
        if not self.evidence_root.exists():
            return 0
        count = 0
        for path in self.evidence_root.glob("*.json"):
            _, effect = _read_effect(path)
            if effect.capability_digest == capability_digest:
                count += 1
        return count

    @staticmethod
    def _read_artifact(operation: BrokerOperation, raw_input: Any) -> Mapping[str, Any]:
        if raw_input != {}:
            raise BrokerError("read-artifact input must be empty")
        path = operation.resolved_path()
        if path.is_symlink() or not path.is_file():
            raise BrokerError("registered read artifact is missing or not regular")
        first = path.read_bytes()
        if len(first) > _MAX_OUTPUT_BYTES:
            raise BrokerError("registered read artifact exceeds the output ceiling")
        second = path.read_bytes()
        first_digest = digest_bytes(first)
        if first_digest != digest_bytes(second):
            raise BrokerError("registered read artifact changed during verification")
        return {
            "exit_status": 0,
            "stdout_digest": first_digest,
            "stderr_digest": digest_bytes(b""),
            "artifact_digest": first_digest,
            "verified": True,
        }

    @staticmethod
    def _publish_artifact(operation: BrokerOperation, raw_input: Any) -> Mapping[str, Any]:
        if not isinstance(raw_input, Mapping) or set(raw_input) != {"content_base64"}:
            raise BrokerError("publish-artifact input requires only content_base64")
        try:
            content = base64.b64decode(str(raw_input["content_base64"]), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise BrokerError("publish-artifact content is not canonical base64") from exc
        if len(content) > _MAX_OUTPUT_BYTES:
            raise BrokerError("publish-artifact content exceeds the output ceiling")
        destination = operation.resolved_path()
        _write_once(destination, content)
        persisted = destination.read_bytes()
        if persisted != content:
            raise BrokerError("published artifact did not survive durable re-open")
        digest = digest_bytes(persisted)
        return {
            "exit_status": 0,
            "stdout_digest": digest_bytes(b""),
            "stderr_digest": digest_bytes(b""),
            "artifact_digest": digest,
            "verified": True,
        }

    def _run_verifier(self, operation: BrokerOperation, raw_input: Any) -> Mapping[str, Any]:
        if not isinstance(raw_input, Mapping) or set(raw_input) != {"stdin"}:
            raise BrokerError("run-verifier input requires only stdin")
        stdin_bytes = _canonical_bytes({"stdin": raw_input["stdin"]})
        qualification_root = Path(
            tempfile.mkdtemp(prefix="qualification-candidate-", dir=self.evidence_root)
        )
        qualification_root.rmdir()
        qualification = self.isolation.qualify(qualification_root)
        if not qualification.satisfied:
            raise BrokerError("broker isolation did not prove read, write, and network denial")
        results = []
        for attempt in (1, 2):
            root = Path(tempfile.mkdtemp(prefix=f"verify-{attempt}-", dir=self.evidence_root))
            input_path = root / "input.json"
            input_path.write_bytes(stdin_bytes)
            result = self.isolation.run(
                operation.command,
                cwd=root,
                readable_paths=(input_path, *operation.command_readable_paths),
                writable_paths=(root,),
                environment={"FACTORY_BROKER_INPUT": str(input_path)},
            )
            results.append(result)
        first, second = results
        stdout = first.stdout.encode("utf-8")
        stderr = first.stderr.encode("utf-8")
        if len(stdout) + len(stderr) > _MAX_OUTPUT_BYTES:
            raise BrokerError("verifier output exceeds the evidence ceiling")
        verified = (
            first.returncode == 0
            and second.returncode == 0
            and first.stdout == second.stdout
            and first.stderr == second.stderr
        )
        return {
            "exit_status": first.returncode,
            "stdout_digest": digest_bytes(stdout),
            "stderr_digest": digest_bytes(stderr),
            "artifact_digest": digest_obj(
                {
                    "stdout": digest_bytes(stdout),
                    "stderr": digest_bytes(stderr),
                }
            ),
            "verified": verified,
        }
