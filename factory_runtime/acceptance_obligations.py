"""Ratified product acceptance obligations and point-for-point effect receipts.

The code-owned transition catalog in :mod:`factory_runtime.transition_obligations` protects the
Factory's own mechanics.  This module protects the target product's meaning.  A target catalog
does not become authority because an agent generated it: a distinct enrolled human and Validator
ratify the exact catalog, every obligation resolves to the current three phase artifacts, and the
runtime selects a trigger by an exact state pair.  Unknown or ambiguous selectors deny.

Validator observations are not accepted as proof merely because they say ``passed``.  The host
re-derives their subject, exact test membership, command/configuration/environment bindings and
every cited evidence digest from independently trusted values before retaining a report.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory_core.manifest import digest_bytes, digest_obj
from factory_core.provenance import (
    CLAIM_TEST_ASSERTION,
    IntentBackreference,
    PhaseArtifact,
    ProvenanceBundle,
    ProvenanceClaim,
)
from factory_runtime.authority import (
    AuthorityPolicy,
    AuthorityVerificationError,
    VerifiedReceipt,
    verify_receipt,
)
from factory_runtime.durability import DurabilityError, fsync_directory_chain
from factory_runtime.schema import DocumentValidationError, validate_document
from factory_runtime.snapshot import (
    SnapshotError,
    verify_frozen_blob,
    verify_frozen_tree,
)
from factory_runtime.state import RunState, RunStore
from factory_runtime.tessera import TesseraCli

CATALOG_ARTIFACT_KEY = "acceptance-obligation-catalog"
CATALOG_HUMAN_RECEIPT_KEY = f"{CATALOG_ARTIFACT_KEY}:human-receipt"
CATALOG_VALIDATOR_RECEIPT_KEY = f"{CATALOG_ARTIFACT_KEY}:validator-receipt"
REPORT_ARTIFACT_KEY = "acceptance-obligation-report"
RATIFY_ACTION = "ratify-acceptance-obligation-catalog"
REQUIRED_TRIGGER = ("validating", "preview")
TRUSTED_EVIDENCE_IDS = frozenset(
    {
        "candidate",
        "acceptance-tests",
        "coder-output-snapshot",
        "tester-output-snapshot",
    }
)
_VALIDATOR_LAUNCH_CONTRACT = {
    "schema_version": "factory-validator-launch/1",
    "launch_mode": "python-source-stdin/1",
    "runtime_tcb": "current-factory-python/1",
    "validator_abi": "standalone-python-source/1",
    "argv_0": "-",
    "file": "<stdin>",
    "stdin_after_source": "eof",
    "script_directory_on_sys_path": False,
    "interpreter_flags": "forbidden",
    "additional_path_bindings": "forbidden",
}
_VALIDATOR_ENVIRONMENT_CONTRACT = {
    "schema_version": "factory-validator-environment/5",
    "ambient_environment": "closed",
    # Generic Validator-only declared-loopback grant. When a target declares a candidate the
    # Validator must exercise, the Validator lane runs under a grant of exactly the per-attempt
    # loopback ports the orchestrator allocated for that target's declared shape (TCP and/or UDP,
    # bind and/or connect). The target launches the candidate in-lane using those ports. Every
    # undeclared loopback endpoint and every external address, TCP or UDP, stays denied; Coder,
    # Tester-authoring, and broker lanes remain network-denied. The Factory names no transport.
    # Declaring this honestly is why the contract — and every acceptance catalog's
    # environment_digest — advances.
    "network": "validator-only-declared-loopback",
    "port_allocation": "orchestrator-per-attempt/1",
    "candidate_launch": "in-lane-target-declared/1",
    "launch_contract": _VALIDATOR_LAUNCH_CONTRACT,
    "read_scope": [
        "build-input",
        "build-plan",
        "pattern-catalog",
        "acceptance-obligation-catalog",
        "coder-output-snapshot",
        "tester-output-snapshot",
        "validator-execution-snapshot",
    ],
    "write_scope": ["validator-work", "validator-output"],
}
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ATTEMPT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_VALIDATOR_EXECUTION_FILES = 4096
_MAX_VALIDATOR_EXECUTION_BYTES = 128 * 1024 * 1024


class AcceptanceObligationError(ValueError):
    """A target acceptance obligation could not be authorized or proved."""


@dataclass(frozen=True)
class ValidatorExecutionFile:
    """One exact file captured for a deterministic Validator execution snapshot."""

    snapshot_path: str
    content: bytes
    mode: int


@dataclass(frozen=True)
class ValidatorExecutionCapture:
    """Exact Validator executable/input bytes plus their path-and-byte-bound manifest."""

    document: Mapping[str, Any]
    files: tuple[ValidatorExecutionFile, ...]

    @property
    def identity_digest(self) -> str:
        return digest_obj(dict(self.document))

    @property
    def command_digest(self) -> str:
        # The command address is the canonical manifest address itself. Catalogs, review
        # subjects, and attempt evidence therefore share one directly recoverable identity.
        return self.identity_digest

    @property
    def configuration_digest(self) -> str:
        return digest_obj(
            {
                "schema_version": "factory-validator-configuration/3",
                "runner": "isolated-build-loop/3",
                "launch_contract": _VALIDATOR_LAUNCH_CONTRACT,
                "command_digest": self.command_digest,
                "execution_identity_digest": self.identity_digest,
                "snapshot_tree_digest": self.document["snapshot_tree_digest"],
            }
        )

    @property
    def environment_digest(self) -> str:
        return digest_obj(_VALIDATOR_ENVIRONMENT_CONTRACT)

    @property
    def digests(self) -> tuple[str, str, str]:
        return self.command_digest, self.configuration_digest, self.environment_digest


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _capture_regular_file(path: Path, *, label: str) -> tuple[bytes, int]:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise AcceptanceObligationError(f"{label} is not a regular file")
        chunks: list[bytes] = []
        byte_count = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            byte_count += len(chunk)
            if byte_count > _MAX_VALIDATOR_EXECUTION_BYTES:
                raise AcceptanceObligationError("Validator execution inputs exceed the byte limit")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        installed = os.lstat(path)
        if (
            _file_identity(before) != _file_identity(after)
            or stat.S_ISLNK(installed.st_mode)
            or (installed.st_dev, installed.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise AcceptanceObligationError(f"{label} changed while it was captured")
        return b"".join(chunks), stat.S_IMODE(after.st_mode)
    except OSError as exc:
        raise AcceptanceObligationError(f"{label} is unavailable: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _resolve_executable(value: str) -> tuple[str, Path]:
    candidate = Path(value)
    selected = value
    if not candidate.is_absolute() and candidate.parent == Path("."):
        selected = shutil.which(value) or ""
        if not selected:
            raise AcceptanceObligationError(f"Validator executable is unavailable: {value}")
        candidate = Path(selected)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise AcceptanceObligationError(
            f"Validator executable is unavailable: {value}: {exc}"
        ) from exc
    return selected, resolved


def _capture_input(
    *,
    input_id: str,
    declared_path: str,
    source: Path,
    roles: Sequence[str],
) -> tuple[dict[str, Any], list[ValidatorExecutionFile]]:
    snapshot_root = f"inputs/{input_id}"
    files: list[ValidatorExecutionFile] = []
    rows: list[dict[str, Any]] = []
    input_byte_count = 0
    if source.is_symlink():
        raise AcceptanceObligationError(f"Validator execution input is a symlink: {declared_path}")
    if source.is_file():
        content, mode = _capture_regular_file(source, label=f"Validator input {declared_path}")
        local_path = "payload"
        files.append(ValidatorExecutionFile(f"{snapshot_root}/{local_path}", content, mode))
        input_byte_count = len(content)
        rows.append(
            {
                "path": local_path,
                "mode": mode,
                "byte_count": len(content),
                "content_digest": digest_bytes(content),
            }
        )
        kind = "file"
    elif source.is_dir():
        root_before = os.lstat(source)
        if not stat.S_ISDIR(root_before.st_mode):
            raise AcceptanceObligationError(
                f"Validator execution input is not a real directory: {declared_path}"
            )
        kind = "directory"
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source).as_posix()
            if path.is_symlink():
                raise AcceptanceObligationError(
                    f"Validator execution input contains a symlink: {declared_path}/{relative}"
                )
            if path.is_dir():
                continue
            content, mode = _capture_regular_file(
                path,
                label=f"Validator input {declared_path}/{relative}",
            )
            files.append(
                ValidatorExecutionFile(f"{snapshot_root}/{relative}", content, mode)
            )
            input_byte_count += len(content)
            if input_byte_count > _MAX_VALIDATOR_EXECUTION_BYTES:
                raise AcceptanceObligationError("Validator execution inputs exceed the byte limit")
            rows.append(
                {
                    "path": relative,
                    "mode": mode,
                    "byte_count": len(content),
                    "content_digest": digest_bytes(content),
                }
            )
            if len(files) > _MAX_VALIDATOR_EXECUTION_FILES:
                raise AcceptanceObligationError("Validator execution inputs exceed the file limit")
        root_after = os.lstat(source)
        if _file_identity(root_before) != _file_identity(root_after):
            raise AcceptanceObligationError(
                f"Validator execution input changed while it was captured: {declared_path}"
            )
        if not rows:
            raise AcceptanceObligationError(
                f"Validator execution input directory is empty: {declared_path}"
            )
    else:
        raise AcceptanceObligationError(
            f"Validator execution input is not a regular file or directory: {declared_path}"
        )
    identity_rows = [
        {
            "path": row["path"],
            "mode": row["mode"],
            "byte_count": row["byte_count"],
            "content_digest": row["content_digest"],
        }
        for row in rows
    ]
    return (
        {
            "input_id": input_id,
            "roles": sorted(set(roles)),
            "declared_path": declared_path,
            "resolved_path": str(source),
            "kind": kind,
            "snapshot_path": snapshot_root,
            "tree_digest": digest_obj({"files": identity_rows}),
            "files": identity_rows,
        },
        files,
    )


def capture_validator_execution(
    command: Sequence[str],
    *,
    trusted_paths: Sequence[str | Path] = (),
) -> ValidatorExecutionCapture:
    """Capture the exact executable and trusted inputs behind one Validator argv.

    The returned bytes are the sole source used to construct an attempt-local frozen snapshot.
    Reopening the mutable source path for launch would break this contract.
    """

    argv = [str(part) for part in command]
    if not argv or any(not part for part in argv):
        raise AcceptanceObligationError("Validator command cannot be empty")
    _, executable = _resolve_executable(argv[0])
    declared: list[tuple[str, Path, set[str]]] = [
        (argv[0], executable, {"primary-executable"})
    ]
    seen = {executable}
    for raw_path in trusted_paths:
        declared_path = os.fspath(raw_path)
        try:
            resolved = Path(raw_path).resolve(strict=True)
        except OSError as exc:
            raise AcceptanceObligationError(
                f"Validator trusted input is unavailable: {declared_path}: {exc}"
            ) from exc
        if resolved in seen:
            for _, existing, roles in declared:
                if existing == resolved:
                    roles.add("trusted-runner-input")
                    break
            continue
        seen.add(resolved)
        declared.append((declared_path, resolved, {"trusted-runner-input"}))
    declared[1:] = sorted(declared[1:], key=lambda item: (str(item[1]), item[0]))

    inputs: list[dict[str, Any]] = []
    captured_files: list[ValidatorExecutionFile] = []
    source_to_input: dict[Path, dict[str, Any]] = {}
    captured_byte_count = 0
    for index, (declared_path, source, roles) in enumerate(declared):
        input_document, input_files = _capture_input(
            input_id=f"input-{index:03d}",
            declared_path=declared_path,
            source=source,
            roles=tuple(roles),
        )
        inputs.append(input_document)
        captured_byte_count += sum(len(item.content) for item in input_files)
        if captured_byte_count > _MAX_VALIDATOR_EXECUTION_BYTES:
            raise AcceptanceObligationError("Validator execution inputs exceed the byte limit")
        if len(captured_files) + len(input_files) > _MAX_VALIDATOR_EXECUTION_FILES:
            raise AcceptanceObligationError("Validator execution inputs exceed the file limit")
        captured_files.extend(input_files)
        source_to_input[source] = input_document
    bindings: list[dict[str, Any]] = [
        {
            "argv_index": 0,
            "input_id": inputs[0]["input_id"],
            "relative_path": "payload",
        }
    ]
    for index, value in enumerate(argv[1:], start=1):
        candidate = Path(value)
        if not candidate.is_absolute() and not candidate.exists():
            continue
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise AcceptanceObligationError(
                f"Validator command path is unavailable: {value}: {exc}"
            ) from exc
        bound_input = source_to_input.get(resolved)
        if bound_input is None:
            raise AcceptanceObligationError(
                f"Validator command path is not an admitted trusted input: {value}"
            )
        bindings.append(
            {
                "argv_index": index,
                "input_id": bound_input["input_id"],
                "relative_path": "payload" if bound_input["kind"] == "file" else "",
            }
        )

    snapshot_rows = sorted(
        (
            {
                "path": item.snapshot_path,
                "mode": item.mode,
                "digest": digest_bytes(item.content),
            }
            for item in captured_files
        ),
        key=lambda row: str(row["path"]),
    )
    document = {
        "schema_version": "factory-validator-execution-identity/1",
        "argv": argv,
        "path_bindings": bindings,
        "trusted_input_ids": [
            item["input_id"] for item in inputs if "trusted-runner-input" in item["roles"]
        ],
        "inputs": inputs,
        "snapshot_tree_digest": digest_obj({"files": snapshot_rows}),
    }
    return ValidatorExecutionCapture(document, tuple(captured_files))


def validator_execution_digests(
    command: Sequence[str],
    *,
    trusted_paths: Sequence[str | Path] = (),
) -> tuple[str, str, str]:
    """Return byte-bound command, runner-configuration, and environment addresses.

    These values are ratified in the catalog before authoring.  The caller cannot describe a
    friendlier environment than the runtime actually supplies: the environment contract is
    code-owned, while exact executable and trusted-input bytes remain an explicit human decision.
    """

    return capture_validator_execution(command, trusted_paths=trusted_paths).digests


def verify_retained_validator_execution(
    run_dir: str | Path,
    *,
    attempt_id: str,
    command_digest: str,
    configuration_digest: str,
    environment_digest: str,
    expected_snapshot_digest: str | None = None,
) -> str:
    """Reopen the attempt-local Validator manifest and every frozen executable/input byte.

    The ratified command address is the manifest address.  Its embedded tree address is not a
    sufficient replay proof by itself: both content-addressed snapshots must still exist and
    re-derive before a VALIDATING or PREVIEW ledger entry may depend on them.
    """

    if not _ATTEMPT_ID.fullmatch(attempt_id):
        raise AcceptanceObligationError("Validator execution has an invalid attempt id")
    for label, value in (
        ("Validator command digest", command_digest),
        ("Validator configuration digest", configuration_digest),
        ("Validator environment digest", environment_digest),
    ):
        if not isinstance(value, str) or not _DIGEST.fullmatch(value):
            raise AcceptanceObligationError(f"{label} is not a canonical content address")
    root = Path(run_dir) / "evidence" / "build-attempts" / attempt_id / "validator-execution"
    manifest_dir = (
        root
        / "manifests"
        / "validator-execution-manifest"
        / command_digest.removeprefix("sha256:")
    )
    try:
        manifest = verify_frozen_blob(
            manifest_dir,
            expected_digest=command_digest,
            label="validator-execution-manifest",
        )
        manifest_bytes = manifest.payload_path.read_bytes()
        document = json.loads(manifest_bytes.decode("utf-8"))
    except (SnapshotError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcceptanceObligationError(
            f"retained Validator execution manifest is invalid: {exc}"
        ) from exc
    if not isinstance(document, Mapping):
        raise AcceptanceObligationError("retained Validator execution manifest must be an object")
    document = dict(document)
    if manifest_bytes != json.dumps(document, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    ):
        raise AcceptanceObligationError(
            "retained Validator execution manifest is not canonical JSON"
        )
    if set(document) != {
        "schema_version",
        "argv",
        "path_bindings",
        "trusted_input_ids",
        "inputs",
        "snapshot_tree_digest",
    } or document.get("schema_version") != "factory-validator-execution-identity/1":
        raise AcceptanceObligationError("retained Validator execution manifest is malformed")
    snapshot_digest = str(document.get("snapshot_tree_digest", ""))
    if not _DIGEST.fullmatch(snapshot_digest):
        raise AcceptanceObligationError(
            "retained Validator execution snapshot address is malformed"
        )
    if expected_snapshot_digest is not None and snapshot_digest != expected_snapshot_digest:
        raise AcceptanceObligationError(
            "retained Validator execution snapshot differs from its ledger address"
        )
    try:
        verify_frozen_tree(
            root / "trees" / snapshot_digest.removeprefix("sha256:"),
            expected_digest=snapshot_digest,
        )
    except SnapshotError as exc:
        raise AcceptanceObligationError(
            f"retained Validator execution snapshot is invalid: {exc}"
        ) from exc
    capture = ValidatorExecutionCapture(document, ())
    if capture.digests != (command_digest, configuration_digest, environment_digest):
        raise AcceptanceObligationError(
            "retained Validator execution identity differs from its ratified digest tuple"
        )
    return snapshot_digest


def _canonical_bytes(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _read_regular_bytes(path: str | Path, *, label: str) -> bytes:
    source = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise AcceptanceObligationError(f"{label} is unreadable: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise AcceptanceObligationError(f"{label} is not regular")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_object(path: str | Path, *, label: str) -> dict[str, Any]:
    raw_bytes = _read_regular_bytes(path, label=label)
    try:
        raw = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise AcceptanceObligationError(f"{label} is unreadable: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise AcceptanceObligationError(f"{label} must be a JSON object")
    return dict(raw)


def _read_canonical_object(path: str | Path, *, label: str) -> dict[str, Any]:
    raw_bytes = _read_regular_bytes(path, label=label)
    try:
        raw = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise AcceptanceObligationError(f"{label} is unreadable: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise AcceptanceObligationError(f"{label} must be a JSON object")
    document = dict(raw)
    if raw_bytes != _canonical_bytes(document):
        raise AcceptanceObligationError(f"{label} is not in canonical retained form")
    return document


def _sync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise AcceptanceObligationError(f"acceptance evidence path is not a directory: {path}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sync_evidence_directories(path: Path) -> None:
    _sync_directory(path.parent)
    _sync_directory(path.parent.parent)


def _existing_file_is_identical(path: Path, content: bytes) -> bool:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise AcceptanceObligationError(
            f"acceptance-obligation evidence became unreadable: {exc}"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise AcceptanceObligationError(
                "acceptance-obligation evidence destination is not regular"
            )
        chunks: list[bytes] = []
        remaining = len(content) + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        stable = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) == (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if not stable:
            raise AcceptanceObligationError(
                "acceptance-obligation evidence changed during comparison"
            )
        if b"".join(chunks) != content:
            return False
        os.fsync(descriptor)
        installed = os.lstat(path)
        if stat.S_ISLNK(installed.st_mode) or (
            installed.st_dev,
            installed.st_ino,
        ) != (after.st_dev, after.st_ino):
            raise AcceptanceObligationError("acceptance-obligation evidence changed during fsync")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _sync_evidence_directories(path)
    return True


def _write_once_or_identical(path: Path, content: bytes) -> None:
    """Publish one complete immutable file with an atomic no-replace hard link."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".acceptance-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            if not _existing_file_is_identical(path, content):
                raise AcceptanceObligationError(
                    "acceptance-obligation evidence address contains different bytes"
                ) from exc
            return
        _sync_evidence_directories(path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class AcceptanceObligationCatalog:
    """Closed, exact-selector catalog whose content address is the ratified subject."""

    document: Mapping[str, Any]

    @property
    def content_digest(self) -> str:
        return digest_obj(dict(self.document))

    def select(self, source: str, destination: str) -> Mapping[str, Any]:
        matches = [
            trigger
            for trigger in self.document["triggers"]
            if trigger["from_state"] == source and trigger["to_state"] == destination
        ]
        if len(matches) != 1:
            qualifier = "unknown" if not matches else "ambiguous"
            raise AcceptanceObligationError(
                f"{qualifier} acceptance-obligation selector: {source} -> {destination}"
            )
        return matches[0]

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> AcceptanceObligationCatalog:
        try:
            validate_document("acceptance-obligation-catalog", document)
        except DocumentValidationError as exc:
            raise AcceptanceObligationError(str(exc)) from exc
        triggers = list(document["triggers"])
        trigger_ids = [str(trigger["trigger_id"]) for trigger in triggers]
        pairs = [(str(trigger["from_state"]), str(trigger["to_state"])) for trigger in triggers]
        if len(trigger_ids) != len(set(trigger_ids)):
            raise AcceptanceObligationError("acceptance-obligation trigger ids must be unique")
        if len(pairs) != len(set(pairs)):
            raise AcceptanceObligationError("acceptance-obligation state selectors must be unique")
        if REQUIRED_TRIGGER not in pairs:
            raise AcceptanceObligationError(
                "acceptance-obligation catalog must define validating -> preview"
            )
        obligation_ids: list[str] = []
        for trigger in triggers:
            local_ids = [str(item["obligation_id"]) for item in trigger["obligations"]]
            if len(local_ids) != len(set(local_ids)):
                raise AcceptanceObligationError(
                    f"trigger {trigger['trigger_id']} contains duplicate obligation ids"
                )
            obligation_ids.extend(local_ids)
            for obligation in trigger["obligations"]:
                unknown_evidence = sorted(
                    set(obligation["required_evidence_ids"]) - TRUSTED_EVIDENCE_IDS
                )
                if unknown_evidence:
                    raise AcceptanceObligationError(
                        f"obligation {obligation['obligation_id']} requests unsupported "
                        f"evidence ids: {', '.join(unknown_evidence)}"
                    )
                references = [
                    json.dumps(reference, sort_keys=True, separators=(",", ":"))
                    for reference in obligation["intent_backreferences"]
                ]
                if len(references) != len(set(references)):
                    raise AcceptanceObligationError(
                        f"obligation {obligation['obligation_id']} repeats an intent backreference"
                    )
                test_pairs = [
                    (str(item["test_id"]), str(item["assertion_digest"]))
                    for item in obligation["test_assertions"]
                ]
                if len(test_pairs) != len(set(test_pairs)):
                    raise AcceptanceObligationError(
                        f"obligation {obligation['obligation_id']} repeats a test assertion"
                    )
                if obligation["verifier_id"] == "validator-test-execution-v1" and not test_pairs:
                    raise AcceptanceObligationError(
                        f"obligation {obligation['obligation_id']} test verifier has no exact tests"
                    )
                if obligation["verifier_id"] != "validator-test-execution-v1" and test_pairs:
                    raise AcceptanceObligationError(
                        f"obligation {obligation['obligation_id']} assigns tests to a "
                        "non-test verifier"
                    )
        if len(obligation_ids) != len(set(obligation_ids)):
            raise AcceptanceObligationError(
                "acceptance-obligation ids must be unique across the catalog"
            )
        return cls(dict(document))


@dataclass(frozen=True)
class StoredAcceptanceCatalog:
    catalog: AcceptanceObligationCatalog
    human_receipt: VerifiedReceipt
    validator_receipt: VerifiedReceipt
    directory: Path
    consumes_new_nonces: bool = True

    @property
    def artifact_digests(self) -> Mapping[str, str]:
        return {
            CATALOG_ARTIFACT_KEY: self.catalog.content_digest,
            CATALOG_HUMAN_RECEIPT_KEY: self.human_receipt.envelope.envelope_digest,
            CATALOG_VALIDATOR_RECEIPT_KEY: self.validator_receipt.envelope.envelope_digest,
        }


def _phase_artifacts(runs_root: Path, run_id: str) -> tuple[PhaseArtifact, ...]:
    projection = RunStore(runs_root).load(run_id)
    artifacts: list[PhaseArtifact] = []
    for phase, expected_digest in projection.phase_artifact_digests.items():
        path = (
            runs_root
            / run_id
            / "evidence"
            / phase
            / expected_digest.removeprefix("sha256:")
            / "artifact.json"
        )
        document = _read_object(path, label=f"retained {phase} artifact")
        try:
            validate_document("phase-artifact", document)
        except DocumentValidationError as exc:
            raise AcceptanceObligationError(str(exc)) from exc
        artifact = PhaseArtifact.from_dict(document)
        if artifact.phase != phase or artifact.content_digest != expected_digest:
            raise AcceptanceObligationError(
                f"retained {phase} artifact differs from the run ledger"
            )
        artifacts.append(artifact)
    return tuple(artifacts)


def _verify_catalog_provenance(
    catalog: AcceptanceObligationCatalog,
    artifacts: Sequence[PhaseArtifact],
) -> None:
    trusted = {artifact.artifact_id: artifact.content_digest for artifact in artifacts}
    claims: list[ProvenanceClaim] = []
    for trigger in catalog.document["triggers"]:
        for obligation in trigger["obligations"]:
            for index, reference in enumerate(obligation["intent_backreferences"], start=1):
                claims.append(
                    ProvenanceClaim(
                        claim_id=f"{obligation['obligation_id']}.{index}",
                        kind=CLAIM_TEST_ASSERTION,
                        backreference=IntentBackreference.from_dict(reference),
                    )
                )
    report = ProvenanceBundle(
        artifacts=tuple(artifacts),
        claims=tuple(claims),
        trusted_artifact_digests=trusted,
    ).verify()
    if not report.satisfied:
        raise AcceptanceObligationError(
            "acceptance-obligation intent provenance is invalid: " + ", ".join(report.issues)
        )


def verify_and_retain_acceptance_catalog(
    runs_root: str | Path,
    run_id: str,
    *,
    catalog_path: str | Path,
    human_receipt_path: str | Path | None,
    validator_receipt_path: str | Path | None,
    policy: AuthorityPolicy,
    tessera: TesseraCli,
    clock: Callable[[], int] | None = None,
) -> StoredAcceptanceCatalog:
    """Verify independent ratification and retain exact catalog/receipt bytes before build."""

    root = Path(runs_root)
    projection = RunStore(root).load(run_id)
    if projection.state != RunState.OPERATIONAL_MATURITY_RATIFIED:
        raise AcceptanceObligationError(
            "a new acceptance-obligation catalog requires operational-maturity ratification"
        )
    document = _read_object(catalog_path, label="acceptance-obligation catalog")
    catalog = AcceptanceObligationCatalog.from_dict(document)
    expected = {
        "run_id": run_id,
        "generation": projection.generation,
        "target_state_digest": projection.target_state_digest,
        "phase_artifact_digests": dict(projection.phase_artifact_digests),
    }
    for field, value in expected.items():
        if catalog.document[field] != value:
            raise AcceptanceObligationError(f"acceptance-obligation catalog has wrong {field}")
    human_identity = str(catalog.document["human_ratifier"])
    validator_identity = str(catalog.document["validator_ratifier"])
    if human_identity == validator_identity:
        raise AcceptanceObligationError(
            "acceptance-obligation human and Validator ratifiers must be distinct"
        )
    _verify_catalog_provenance(catalog, _phase_artifacts(root, run_id))
    phase_derived = catalog.document.get("authority_basis") == {
        "mode": "phase-ratification",
        "phase": "operational-maturity",
    }
    if phase_derived:
        phase_digest = projection.phase_artifact_digests["operational-maturity"]
        receipt_root = (
            root
            / run_id
            / "evidence"
            / "operational-maturity"
            / phase_digest.removeprefix("sha256:")
        )
        human_receipt_path = receipt_root / "human-receipt.tessera.json"
        validator_receipt_path = receipt_root / "validator-receipt.tessera.json"
        expected_action = "ratify-operational-maturity"
        expected_subject_digest = phase_digest
    else:
        if human_receipt_path is None or validator_receipt_path is None:
            raise AcceptanceObligationError(
                "independently ratified acceptance-obligation catalog requires both receipts"
            )
        expected_action = RATIFY_ACTION
        expected_subject_digest = catalog.content_digest
    human_envelope_bytes = _read_regular_bytes(
        human_receipt_path,
        label="acceptance-obligation human receipt",
    )
    validator_envelope_bytes = _read_regular_bytes(
        validator_receipt_path,
        label="acceptance-obligation Validator receipt",
    )
    consumed = () if phase_derived else RunStore(root).consumed_authority_nonces(run_id)
    try:
        human_receipt = verify_receipt(
            human_receipt_path,
            policy=policy,
            expected_action=expected_action,
            expected_subject_digest=expected_subject_digest,
            expected_run_id=run_id,
            expected_signer_identity=human_identity,
            tessera=tessera,
            clock=clock,
            consumed_nonces=tuple(consumed),
        )
        human = policy.principal(human_identity)
        if human is None or human.kind != "human":
            raise AuthorityVerificationError(
                "acceptance-obligation human ratifier is not an enrolled human"
            )
        validator_receipt = verify_receipt(
            validator_receipt_path,
            policy=policy,
            expected_action=expected_action,
            expected_subject_digest=expected_subject_digest,
            expected_run_id=run_id,
            expected_signer_identity=validator_identity,
            tessera=tessera,
            clock=clock,
            consumed_nonces=tuple((*consumed, human_receipt.nonce)),
        )
        validator = policy.principal(validator_identity)
        if validator is None or validator.kind != "agent":
            raise AuthorityVerificationError(
                "acceptance-obligation Validator ratifier is not an enrolled agent"
            )
        if digest_bytes(human_envelope_bytes) != human_receipt.envelope.envelope_digest:
            raise AuthorityVerificationError(
                "acceptance-obligation human receipt changed while it was verified"
            )
        if digest_bytes(validator_envelope_bytes) != validator_receipt.envelope.envelope_digest:
            raise AuthorityVerificationError(
                "acceptance-obligation Validator receipt changed while it was verified"
            )
    except AuthorityVerificationError as exc:
        raise AcceptanceObligationError(str(exc)) from exc

    directory = (
        root
        / run_id
        / "evidence"
        / "acceptance-obligation-catalogs"
        / catalog.content_digest.removeprefix("sha256:")
    )
    _write_once_or_identical(directory / "catalog.json", _canonical_bytes(catalog.document))
    _write_once_or_identical(
        directory / "human-receipt.tessera.json",
        human_envelope_bytes,
    )
    _write_once_or_identical(
        directory / "validator-receipt.tessera.json",
        validator_envelope_bytes,
    )
    try:
        fsync_directory_chain(directory, through=root / run_id)
    except DurabilityError as exc:
        raise AcceptanceObligationError(str(exc)) from exc
    return StoredAcceptanceCatalog(
        catalog, human_receipt, validator_receipt, directory, consumes_new_nonces=not phase_derived
    )


def load_retained_acceptance_catalog(
    runs_root: str | Path,
    run_id: str,
    *,
    expected_digest: str | None = None,
) -> AcceptanceObligationCatalog:
    root = Path(runs_root)
    projection = RunStore(root).load(run_id)
    digest = expected_digest or projection.acceptance_obligation_catalog_digest
    if not digest:
        raise AcceptanceObligationError("run has no ratified acceptance-obligation catalog")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise AcceptanceObligationError(
            "acceptance-obligation catalog digest is not a canonical content address"
        )
    path = (
        root
        / run_id
        / "evidence"
        / "acceptance-obligation-catalogs"
        / digest.removeprefix("sha256:")
        / "catalog.json"
    )
    catalog = AcceptanceObligationCatalog.from_dict(
        _read_object(path, label="retained acceptance-obligation catalog")
    )
    if catalog.content_digest != digest:
        raise AcceptanceObligationError(
            "retained acceptance-obligation catalog differs from its ledger address"
        )
    if catalog.document["target_state_digest"] != projection.target_state_digest:
        raise AcceptanceObligationError(
            "retained acceptance-obligation catalog targets another subject"
        )
    if catalog.document["generation"] != projection.generation:
        raise AcceptanceObligationError(
            "retained acceptance-obligation catalog targets another generation"
        )
    if catalog.document["phase_artifact_digests"] != dict(projection.phase_artifact_digests):
        raise AcceptanceObligationError(
            "retained acceptance-obligation catalog has stale phase versions"
        )
    _verify_catalog_provenance(catalog, _phase_artifacts(root, run_id))
    return catalog


def derive_acceptance_obligation_report(
    catalog: AcceptanceObligationCatalog,
    *,
    observations: Mapping[str, Any],
    run_id: str,
    generation: int,
    source: str,
    destination: str,
    target_state_digest: str,
    resolved_commit: str,
    resolved_tree: str,
    phase_artifact_digests: Mapping[str, str],
    candidate_digest: str,
    acceptance_tests_digest: str,
    command_digest: str,
    configuration_digest: str,
    environment_digest: str,
    trusted_evidence_digests: Mapping[str, str],
) -> dict[str, Any]:
    """Re-derive one exact trigger report from Validator observations and trusted evidence."""

    try:
        validate_document("acceptance-obligation-observations", observations)
    except DocumentValidationError as exc:
        raise AcceptanceObligationError(str(exc)) from exc
    trigger = catalog.select(source, destination)
    catalog_subject = {
        "run_id": run_id,
        "generation": generation,
        "target_state_digest": target_state_digest,
        "phase_artifact_digests": dict(phase_artifact_digests),
    }
    for field, expected in catalog_subject.items():
        if catalog.document[field] != expected:
            raise AcceptanceObligationError(
                f"acceptance-obligation catalog has stale or substituted {field}"
            )
    execution_contract = {
        "command_digest": command_digest,
        "configuration_digest": configuration_digest,
        "environment_digest": environment_digest,
    }
    for field, expected in execution_contract.items():
        if trigger[field] != expected:
            raise AcceptanceObligationError(
                f"acceptance-obligation trigger does not authorize the exact {field}"
            )
    exact = {
        "run_id": run_id,
        "generation": generation,
        "catalog_digest": catalog.content_digest,
        "trigger_id": trigger["trigger_id"],
        "candidate_digest": candidate_digest,
        "acceptance_tests_digest": acceptance_tests_digest,
        "command_digest": command_digest,
        "configuration_digest": configuration_digest,
        "environment_digest": environment_digest,
    }
    for field, expected in exact.items():
        if observations[field] != expected:
            raise AcceptanceObligationError(
                f"acceptance-obligation observations have wrong {field}"
            )
    if int(observations["finished_at"]) < int(observations["started_at"]):
        raise AcceptanceObligationError("acceptance-obligation observation time runs backwards")
    expected_obligations = list(trigger["obligations"])
    observed_results = list(observations["results"])
    expected_ids = [str(item["obligation_id"]) for item in expected_obligations]
    observed_ids = [str(item["obligation_id"]) for item in observed_results]
    if observed_ids != expected_ids:
        raise AcceptanceObligationError(
            "acceptance-obligation results must match the ratified order and exact membership"
        )

    report_results: list[dict[str, Any]] = []
    for obligation, result in zip(expected_obligations, observed_results, strict=True):
        obligation_id = str(obligation["obligation_id"])
        if result["verifier_id"] != obligation["verifier_id"]:
            raise AcceptanceObligationError(
                f"obligation {obligation_id} changed its code-owned verifier"
            )
        evidence = {str(key): str(value) for key, value in result["evidence_digests"].items()}
        required_evidence = list(obligation["required_evidence_ids"])
        if set(evidence) != set(required_evidence):
            raise AcceptanceObligationError(
                f"obligation {obligation_id} evidence membership differs from its ratified set"
            )
        for evidence_id, claimed_digest in evidence.items():
            if trusted_evidence_digests.get(evidence_id) != claimed_digest:
                raise AcceptanceObligationError(
                    f"obligation {obligation_id} cites untrusted evidence {evidence_id}"
                )
        expected_tests = [
            (str(item["test_id"]), str(item["assertion_digest"]))
            for item in obligation["test_assertions"]
        ]
        observed_tests = [
            (str(item["test_id"]), str(item["assertion_digest"])) for item in result["test_results"]
        ]
        if observed_tests != expected_tests:
            raise AcceptanceObligationError(
                f"obligation {obligation_id} did not execute the exact ratified test selection"
            )
        if obligation["verifier_id"] == "validator-test-execution-v1" and not observed_tests:
            raise AcceptanceObligationError(
                f"obligation {obligation_id} has a vacuous test execution"
            )
        for test_result in result["test_results"]:
            expected_output_digest = digest_obj(
                {
                    "test_id": test_result["test_id"],
                    "assertion_digest": test_result["assertion_digest"],
                    "exit_status": 0,
                    "candidate_digest": candidate_digest,
                    "acceptance_tests_digest": acceptance_tests_digest,
                    "command_digest": command_digest,
                }
            )
            if test_result["output_digest"] != expected_output_digest:
                raise AcceptanceObligationError(
                    f"obligation {obligation_id} test output receipt does not re-derive"
                )
        effect_body = {
            "obligation_id": obligation_id,
            "verifier_id": obligation["verifier_id"],
            "candidate_digest": candidate_digest,
            "acceptance_tests_digest": acceptance_tests_digest,
            "command_digest": command_digest,
            "configuration_digest": configuration_digest,
            "environment_digest": environment_digest,
            "started_at": observations["started_at"],
            "finished_at": observations["finished_at"],
            "evidence_digests": evidence,
            "test_results": list(result["test_results"]),
        }
        if result["effect_digest"] != digest_obj(effect_body):
            raise AcceptanceObligationError(
                f"obligation {obligation_id} effect digest does not re-derive"
            )
        report_results.append(
            {
                "obligation_id": obligation_id,
                "criterion": obligation["criterion"],
                "verifier_id": obligation["verifier_id"],
                "intent_backreferences": list(obligation["intent_backreferences"]),
                "required_evidence_ids": required_evidence,
                "test_assertions": list(obligation["test_assertions"]),
                "evidence_digests": evidence,
                "test_results": list(result["test_results"]),
                "effect_digest": result["effect_digest"],
                "passed": True,
            }
        )
    # ``satisfied`` means every ratified assertion has a matching deterministic observation with
    # exit status zero and re-derived evidence digests. Semantic adequacy comes from the human and
    # Validator-ratified obligation/test membership, not from this boolean by itself.
    document = {
        "schema_version": "factory-acceptance-obligation-report/1",
        "run_id": run_id,
        "generation": generation,
        "catalog_digest": catalog.content_digest,
        "trigger_id": trigger["trigger_id"],
        "from_state": source,
        "to_state": destination,
        "target_state_digest": target_state_digest,
        "resolved_commit": resolved_commit,
        "resolved_tree": resolved_tree,
        "phase_artifact_digests": dict(phase_artifact_digests),
        "candidate_digest": candidate_digest,
        "acceptance_tests_digest": acceptance_tests_digest,
        "observations": dict(observations),
        "observations_digest": digest_obj(dict(observations)),
        "command_digest": command_digest,
        "configuration_digest": configuration_digest,
        "environment_digest": environment_digest,
        "started_at": observations["started_at"],
        "finished_at": observations["finished_at"],
        "idempotency_key": digest_obj(
            {
                "catalog_digest": catalog.content_digest,
                "trigger_id": trigger["trigger_id"],
                "candidate_digest": candidate_digest,
                "acceptance_tests_digest": acceptance_tests_digest,
                "observations_digest": digest_obj(dict(observations)),
            }
        ),
        "results": report_results,
        "satisfied": True,
    }
    try:
        validate_document("acceptance-obligation-report", document)
    except DocumentValidationError as exc:
        raise AcceptanceObligationError(str(exc)) from exc
    return document


def verify_acceptance_obligation_report(
    catalog: AcceptanceObligationCatalog,
    report: Mapping[str, Any],
    *,
    run_id: str,
    generation: int,
    source: str,
    destination: str,
    target_state_digest: str,
    resolved_commit: str,
    resolved_tree: str,
    phase_artifact_digests: Mapping[str, str],
    candidate_digest: str,
    acceptance_tests_digest: str,
    command_digest: str,
    configuration_digest: str,
    environment_digest: str,
    trusted_evidence_digests: Mapping[str, str],
) -> None:
    """Re-derive a retained report from its raw observations and exact runtime subject."""

    try:
        validate_document("acceptance-obligation-report", report)
    except DocumentValidationError as exc:
        raise AcceptanceObligationError(str(exc)) from exc
    observations = report.get("observations")
    if not isinstance(observations, Mapping):
        raise AcceptanceObligationError("acceptance-obligation report has no observations")
    expected = derive_acceptance_obligation_report(
        catalog,
        observations=observations,
        run_id=run_id,
        generation=generation,
        source=source,
        destination=destination,
        target_state_digest=target_state_digest,
        resolved_commit=resolved_commit,
        resolved_tree=resolved_tree,
        phase_artifact_digests=phase_artifact_digests,
        candidate_digest=candidate_digest,
        acceptance_tests_digest=acceptance_tests_digest,
        command_digest=command_digest,
        configuration_digest=configuration_digest,
        environment_digest=environment_digest,
        trusted_evidence_digests=trusted_evidence_digests,
    )
    if digest_obj(dict(report)) != digest_obj(expected):
        raise AcceptanceObligationError(
            "acceptance-obligation report differs from fresh derivation"
        )


def retain_acceptance_obligation_report(
    runs_root: str | Path,
    run_id: str,
    report: Mapping[str, Any],
) -> str:
    digest = digest_obj(dict(report))
    root = (
        Path(runs_root)
        / run_id
        / "evidence"
        / "acceptance-obligation-reports"
        / str(report["catalog_digest"]).removeprefix("sha256:")
    )
    _write_once_or_identical(
        root / f"{digest.removeprefix('sha256:')}.json", _canonical_bytes(report)
    )
    try:
        fsync_directory_chain(root, through=Path(runs_root) / run_id)
    except DurabilityError as exc:
        raise AcceptanceObligationError(str(exc)) from exc
    return digest


def verify_retained_acceptance_obligation_report(
    run_dir: str | Path,
    *,
    catalog_digest: str,
    report_digest: str,
    run_id: str,
    generation: int,
    source: str,
    destination: str,
    target_state_digest: str,
    resolved_commit: str,
    resolved_tree: str,
    phase_artifact_digests: Mapping[str, str],
    candidate_digest: str,
    acceptance_tests_digest: str,
    trusted_evidence_digests: Mapping[str, str],
) -> Mapping[str, Any]:
    """Reopen and re-derive the ratified catalog and report behind a ledger transition."""

    root = Path(run_dir)
    catalog_path = (
        root
        / "evidence"
        / "acceptance-obligation-catalogs"
        / catalog_digest.removeprefix("sha256:")
        / "catalog.json"
    )
    catalog_document = _read_canonical_object(
        catalog_path, label="retained acceptance-obligation catalog"
    )
    catalog = AcceptanceObligationCatalog.from_dict(catalog_document)
    if catalog.content_digest != catalog_digest:
        raise AcceptanceObligationError(
            "retained acceptance-obligation catalog differs from its content address"
        )
    report_path = (
        root
        / "evidence"
        / "acceptance-obligation-reports"
        / catalog_digest.removeprefix("sha256:")
        / f"{report_digest.removeprefix('sha256:')}.json"
    )
    report = _read_canonical_object(report_path, label="retained acceptance-obligation report")
    if digest_obj(report) != report_digest:
        raise AcceptanceObligationError(
            "retained acceptance-obligation report differs from its content address"
        )
    # The execution contract comes from the independently ratified catalog.  A retained report
    # is evidence, never an authority source for the command/configuration/environment it claims.
    trigger = catalog.select(source, destination)
    verify_acceptance_obligation_report(
        catalog,
        report,
        run_id=run_id,
        generation=generation,
        source=source,
        destination=destination,
        target_state_digest=target_state_digest,
        resolved_commit=resolved_commit,
        resolved_tree=resolved_tree,
        phase_artifact_digests=phase_artifact_digests,
        candidate_digest=candidate_digest,
        acceptance_tests_digest=acceptance_tests_digest,
        command_digest=str(trigger["command_digest"]),
        configuration_digest=str(trigger["configuration_digest"]),
        environment_digest=str(trigger["environment_digest"]),
        trusted_evidence_digests=trusted_evidence_digests,
    )
    return report


__all__ = [
    "AcceptanceObligationCatalog",
    "AcceptanceObligationError",
    "CATALOG_ARTIFACT_KEY",
    "CATALOG_HUMAN_RECEIPT_KEY",
    "CATALOG_VALIDATOR_RECEIPT_KEY",
    "RATIFY_ACTION",
    "REPORT_ARTIFACT_KEY",
    "StoredAcceptanceCatalog",
    "ValidatorExecutionCapture",
    "ValidatorExecutionFile",
    "capture_validator_execution",
    "derive_acceptance_obligation_report",
    "load_retained_acceptance_catalog",
    "retain_acceptance_obligation_report",
    "validator_execution_digests",
    "verify_acceptance_obligation_report",
    "verify_retained_acceptance_obligation_report",
    "verify_retained_validator_execution",
    "verify_and_retain_acceptance_catalog",
]
